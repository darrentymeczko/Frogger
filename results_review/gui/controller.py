"""Owns the review and every change to it.

Undo is a stack of whole-`Review` snapshots rather than inverse commands, for
the same reason `plate_template.gui.controller` does it: a review is a handful
of small dicts, so a snapshot costs nothing, while correct inverses for "set a
raw value", "clear an exclusion back to the pipeline's opinion" and "choose a
different candidate" would be three more chances to get an edge case subtly
wrong. Snapshots go through `to_dict`/`from_dict`, so the serialiser is
exercised on every single edit and a round-trip bug shows up in seconds of use
rather than on the next file load.

Deliberately free of tkinter. Everything here is testable without a display,
and rebuilding -- the one slow part -- is left to the caller to put on a thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .. import datareview, links, runs, review as rv
from ..discovery import SetRun
from ..model import Candidate, Review, SpotEdit
from ..rebuild import Frame, RebuildError, rebuild


class UndoStack:
    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self._undo: list[tuple[dict, str]] = []
        self._redo: list[tuple[dict, str]] = []

    def push(self, snapshot: dict, label: str) -> None:
        self._undo.append((snapshot, label))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, current: dict) -> "tuple[dict, str] | None":
        if not self._undo:
            return None
        snapshot, label = self._undo.pop()
        self._redo.append((current, label))
        return snapshot, label

    def redo(self, current: dict) -> "tuple[dict, str] | None":
        if not self._redo:
            return None
        snapshot, label = self._redo.pop()
        self._undo.append((current, label))
        return snapshot, label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][1] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][1] if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()


class ReviewController:
    def __init__(self, run: SetRun, review: "Review | None" = None,
                 on_change: "Callable[[], None] | None" = None,
                 rebuilder: "Callable[..., Frame] | None" = None) -> None:
        self.run = run
        self.review = rv.with_defaults(run, review or Review())
        self.on_change = on_change
        #: What does the rebuilding: `rebuild` in this process unless told
        #: otherwise. The window hands it `background.rebuild`, the same
        #: function in a worker process.
        self.rebuilder = rebuilder
        self.undo_stack = UndoStack()
        self._saved = self.review.to_dict()
        self.last_change = ""
        self.changing = ""
        self._frames: dict[str, Frame] = {}
        self._generation = 0
        self._errors: dict[str, str] = {}
        # Candidates looked at but not chosen. Keyed by candidate, so flicking
        # back and forth between two graphs rebuilds neither twice.
        self._preview_frames: dict[tuple, Frame] = {}
        self._preview_errors: dict[tuple, str] = {}
        # Candidates a background measuring pass could not measure, and why.
        self._unmeasurable: dict[tuple, str] = {}
        self.medium: str =(run.media[0] if run.media else "")

        manifest = getattr(run, "manifest", {})
        self.capture_root: "Path | None" = (Path(self.review.capture_root or
            manifest["original_photo_root"]) if manifest and
            (self.review.capture_root or manifest.get("original_photo_root")) else
            None if manifest else links.resolve(run.label, self.review.capture_root, run.results_dir))
        self.cfg: "dict | None" = None
        self.config_error: str = ""
        self._load_cfg()
        self._saved = self.review.to_dict()
        #: The experiment's data review, and the file stamp it was read at.
        self._data_flags = None
        self._flags_stamp: object = "unread"

    # -- the data review -----------------------------------------------------

    def reload_data_flags(self) -> bool:
        """Re-read the data review if its file changed. True when it had.

        The data review may be open in another tab and saved while this one
        is open. When its file changes, the rebuilt frames are dropped, since
        each carries the flags as they stood when it was built -- so a True
        here means the current medium needs rebuilding.
        """
        info = getattr(self.run, "experiment", None)
        path = Path(info.data_review_file) if info and info.data_review_file else None
        try:
            stamp = path.stat().st_mtime_ns if path is not None else None
        except OSError:
            stamp = None
        if stamp == self._flags_stamp:
            return False
        first = self._flags_stamp == "unread"
        self._data_flags = datareview.load_flags(self.run)
        self._flags_stamp = stamp
        if first:
            return False
        self._frames.clear()
        self._errors.clear()
        self._drop_previews()
        return True

    def data_flags(self):
        """The data review's flags (`DataFlags`), or None."""
        self.reload_data_flags()
        return self._data_flags

    def candidate_flags(self, cand: Candidate) -> tuple[int, int]:
        """(flagged plates, flagged spots) a candidate scores."""
        return datareview.candidate_flags(self.run, cand, self.data_flags())

    # -- plumbing ------------------------------------------------------------

    def snapshot(self) -> dict:
        return self.review.to_dict()

    def _commit(self, before: dict, label: str, *, invalidate: str = "") -> bool:
        """Record the change if it actually changed anything."""
        if self.review.to_dict() == before:
            return False
        self.undo_stack.push(before, label)
        if invalidate:
            self._frames.pop(invalidate, None)
            self._errors.pop(invalidate, None)
            self._drop_previews(invalidate)
        self.last_change = label
        # Listeners may ask what is being told (``changing``); only while telling it.
        self.changing = label
        try:
            self._notify()
        finally:
            self.changing = ""
        return True

    def _drop_previews(self, medium: str = "") -> None:
        """Forget previewed frames (all, or one medium's): their edits moved."""
        self._generation += 1
        for cache in (self._preview_frames, self._preview_errors):
            for key in [k for k in cache if not medium or k[0] == medium]:
                del cache[key]

    def _notify(self) -> None:
        if self.on_change:
            self.on_change()

    @property
    def dirty(self) -> bool:
        return self.review.to_dict() != self._saved

    # -- the capture tree ----------------------------------------------------

    def _load_cfg(self) -> None:
        """The strain panel, from the run that produced these results if it said.

        A folder produced through the experiment layer records the exact config
        the run used, and its photo folder need not contain a
        `timecourse_config.json` at all -- that file is written by the console
        pipeline, which is only one of the ways these results can be made. The
        recorded config is preferred whenever it is there, so the review
        normalises against the same control the run did.
        """
        self._generation += 1
        self.cfg, self.config_error = None, ""
        info = getattr(self.run, "experiment", None)
        if info is not None and info.pipeline_config:
            from copy import deepcopy
            self.cfg = deepcopy(info.pipeline_config)
            self.recorded_config = self.cfg
            self._measurement_config()
            return
        if getattr(self.run, "manifest", {}):
            self.config_error = "This run has no recorded scientific configuration; existing result artifacts remain readable"
            return
        if self.capture_root is None:
            self.config_error = "no capture folder linked"
            return
        try:
            self.cfg = links.load_config(self.capture_root)
            self.recorded_config = self.cfg
            self._measurement_config()
        except Exception as e:
            self.config_error = str(e)

    def _measurement_config(self):
        from experiments import analysis
        if self.cfg is not None and getattr(self.run, "manifest", {}):
            self.cfg["_run_dir"] = str(self.run.results_dir)
            self.cfg["_run_root"] = str(self.capture_root or "")
            self.cfg["_run_label"] = self.run.manifest.get("scientific_label", self.run.label)
            from copy import deepcopy
            self.cfg["_run_sources"] = deepcopy(self.run.manifest.get("sources", []))
            for source in self.cfg["_run_sources"]:
                if not source.get("sha256"):
                    source["sha256"] = self.review.extensions.get("source_identities", {}).get(source["id"])
        if self.cfg is not None:
            if not self.review.final_analysis:
                self.review.final_analysis = analysis.from_pipeline(
                    self.cfg, self.review.extra_references, self.review.all_pairs)
            self.cfg = analysis.apply(self.cfg, self.review.final_analysis)
            self.review.all_pairs = self.review.final_analysis["all_pairs"]
            if self.review.all_pairs and self.review.posthoc == "dunnett":
                self.review.posthoc = "tukey"
        if self.cfg is not None and "measurement" not in self.cfg:
            import json
            record = self.run.results_dir / "measurement.json"
            if record.exists():
                from src.spotting_roi import ROISettings
                settings = ROISettings(**json.loads(record.read_text(encoding="utf-8")))
                self.cfg = {**self.cfg, "measurement": settings.to_dict()}
        if self.cfg is not None and self.review.measurement:
            self.cfg = {**self.cfg, "measurement": dict(self.review.measurement)}

    def set_final_analysis(self, config: dict) -> bool:
        from experiments import analysis
        config = analysis.validate(config)
        analysis.check_panel(config, (self.cfg or {}).get("strains", []), self.run.media,
                             {m: v.get("exclude", []) for m, v in (self.cfg or {}).get("media", {}).items()})
        before = self.snapshot()
        self.review.final_analysis = config
        self.review.all_pairs = config["all_pairs"]
        if self.review.all_pairs and self.review.posthoc == "dunnett":
            self.review.posthoc = "tukey"
        self._load_cfg()
        self._frames.clear()
        self._errors.clear()
        self._drop_previews()
        return self._commit(before, "Recalculate final normalization and comparisons")

    def set_measurement(self, inward_margin: float, min_pixels: int = 25) -> bool:
        """Explicit scientific recalculation; opening a review never calls this."""
        from src.spotting_roi import ROISettings
        settings = ROISettings(inward_margin=inward_margin, min_pixels=min_pixels)
        before = self.snapshot()
        self.review.measurement = settings.to_dict()
        self._measurement_config()
        self._frames.clear()
        self._errors.clear()
        self._drop_previews()
        return self._commit(before, f"Recalculate interior ROIs ({inward_margin:.0%} inward margin)")

    def link_photos(self, root: Path) -> tuple[bool, str]:
        """Point this set at a capture tree. Returns (ok, message)."""
        from ..rebuild import clear_cache

        root = Path(root)
        if getattr(self.run, "manifest", {}):
            if not root.is_dir():
                return False, f"Original-image folder does not exist: {root}"
            if not self.run.manifest.get("sources") or self.run.manifest.get("unresolved_source_assignments"):
                return False, "Original-image assignments are unavailable; cannot safely infer measurement originals from exported images"
            problems = runs.source_problems(self.run.manifest, root)
            # Unknown legacy identities may only be established by this explicit action.
            blocking = [p for p in problems if not p.startswith("Unverified original:")]
            if blocking:
                return False, "; ".join(blocking)
            before = self.snapshot()
            self.capture_root = root.resolve()
            self.review.capture_root = str(self.capture_root)
            identities = self.review.extensions.setdefault("source_identities", {})
            for source in self.run.manifest.get("sources", []):
                if not source.get("sha256"):
                    identities[source["id"]] = runs.sha256(runs.source_path(root, source["relpath"]))
            self._load_cfg()
            clear_cache()
            self._unmeasurable.clear()
            self._frames.clear()
            self._errors.clear()
            self._drop_previews()
            self._commit(before, "Relink original photographs")
            unverified = sum(1 for s in self.run.manifest.get("sources", []) if not s.get("sha256"))
            return True, (f"Linked originals to {root}" +
                          (f"; established {unverified} previously unverified identities from the selected folder"
                           if unverified else "; SHA256 identities verified"))
        recorded = self.cfg is not None and "resolved_photos" in self.cfg
        if not root.is_dir() or (not recorded and not links.is_capture_tree(root)):
            return False, (f"{root.name} does not look like a capture tree "
                           f"(expected timepoint / medium / plate folders).")
        clear_cache()
        self._unmeasurable.clear()
        self._frames.clear()
        self._errors.clear()
        self._drop_previews()
        self.capture_root = root
        self.review.capture_root = str(root)
        self._load_cfg()
        if self.config_error:
            return False, self.config_error
        links.remember(self.run.label, root)
        self._notify()
        return True, f"linked to {root}"

    @property
    def can_edit(self) -> bool:
        return self.capture_root is not None and self.cfg is not None

    def source_problems(self) -> list[str]:
        if getattr(self.run, "manifest", {}):
            return runs.source_problems({**self.run.manifest,
                                         "sources": (self.cfg or {}).get("_run_sources", self.run.manifest.get("sources", []))},
                                        self.capture_root or Path("."))
        return []

    @property
    def display_label(self):
        return self.review.presentation.get("names", {}).get("result", self.run.label)

    def medium_label(self, medium):
        return self.review.presentation.get("names", {}).get("conditions", {}).get(medium, self.run.medium_label(medium))

    def rename_display(self, name, medium=None):
        """Names are undoable display metadata; scientific/file identities stay fixed."""
        from copy import deepcopy
        name = name.strip()
        if not name:
            raise ValueError("Enter a name.")
        value = deepcopy(self.review.presentation)
        if value.get("version", 1) != 1:
            raise ValueError("This run uses a newer presentation version.")
        if medium is not None:
            if medium not in self.run.media:
                raise ValueError("Select a condition to rename.")
            if any(m != medium and " ".join(self.medium_label(m).casefold().split()) == " ".join(name.casefold().split()) for m in self.run.media):
                raise ValueError("Another treatment already has that name.")
            value.setdefault("names", {}).setdefault("conditions", {})[medium] = name
        else:
            value.setdefault("names", {})["result"] = name
        return self.set_presentation(value)

    def set_presentation(self, value: dict, label: str = "Change presentation") -> bool:
        from copy import deepcopy
        before = self.snapshot()
        self.review.presentation = deepcopy(value)
        return self._commit(before, label)

    # -- picks ---------------------------------------------------------------

    def candidates(self, medium: str = "") -> list[Candidate]:
        return self.run.for_medium(medium or self.medium)

    def chosen(self, medium: str = "") -> "Candidate | None":
        return rv.chosen_candidate(self.run, self.review, medium or self.medium)

    def is_default(self, medium: str = "") -> bool:
        return rv.is_default(self.run, self.review, medium or self.medium)

    def set_medium(self, medium: str) -> None:
        if medium != self.medium:
            self.medium = medium
            self._notify()

    def choose(self, cand: Candidate, reason: str = "") -> bool:
        """Pick a candidate for its medium.

        Edits are kept, not discarded: they are keyed on (replicate, column),
        which means the same spot in a different photo pairing, and silently
        dropping "column 8 replicate 3 is unusable" because the timepoint
        changed would throw away a judgement the person still holds. Anything
        that no longer matches is reported by `rebuild` instead.
        """
        before = self.snapshot()
        self.review.set_pick(cand.medium, cand, reason)
        return self._commit(before, f"Choose {self.medium_label(cand.medium)} "
                                    f"{cand.label}",
                            invalidate=cand.medium)

    def reset_pick(self, medium: str = "") -> bool:
        medium = medium or self.medium
        best = self.run.pipeline_best(medium)
        if best is None:
            return False
        before = self.snapshot()
        self.review.set_pick(medium, best)
        return self._commit(before, f"Reset {self.medium_label(medium)} "
                                    f"to the pipeline's pick",
                            invalidate=medium)

    def set_included(self, medium: str, included: bool) -> bool:
        before = self.snapshot()
        self.review.set_included(medium, included)
        return self._commit(before, ("Include " if included else "Exclude ")
                            + self.medium_label(medium) + " in exports")

    # -- edits ---------------------------------------------------------------

    def _amend(self, replicate: str, strain_col: int, label: str,
               **changes) -> bool:
        medium = self.medium
        before = self.snapshot()
        self.review.amend(medium, replicate, int(strain_col), **changes)
        return self._commit(before, label, invalidate=medium)

    def set_raw(self, replicate: str, strain_col: int, strain: str,
                value: "float | None") -> bool:
        """Substitute a hand-measured grey value, or clear back to the measured one."""
        if value is None:
            return self._amend(replicate, strain_col,
                               f"Restore measured value for {strain} {replicate}",
                               raw_growth=None)
        return self._amend(replicate, strain_col,
                           f"Set {strain} {replicate} to {value:g}",
                           raw_growth=float(value))

    def set_excluded(self, replicate: str, strain_col: int, strain: str,
                     value: "bool | None") -> bool:
        """Drop or keep a spot. None means "defer to the pipeline again"."""
        if value is None:
            word = "Clear exclusion decision for"
        else:
            word = "Exclude" if value else "Keep"
        return self._amend(replicate, strain_col,
                           f"{word} {strain} {replicate}", excluded=value)

    def set_outlier(self, replicate: str, strain_col: int, strain: str,
                    value: "bool | None") -> bool:
        if value is None:
            word = "Clear outlier decision for"
        else:
            word = "Flag" if value else "Unflag"
        return self._amend(replicate, strain_col,
                           f"{word} {strain} {replicate}", outlier=value)

    def set_note(self, replicate: str, strain_col: int, strain: str,
                 note: str) -> bool:
        return self._amend(replicate, strain_col,
                           f"Note on {strain} {replicate}", note=note.strip())

    def set_statistics(self, statistical_test: str, p_adjust: str,
                       alpha: "float | None" = None,
                       posthoc: "str | None" = None) -> bool:
        """Choose the test used by review previews and exports.

        `p_adjust` is the t-tests' correction and `posthoc` the ANOVA's
        follow-up test; each is kept while the other test is selected, so
        switching back and forth does not lose either choice.
        """
        from ..model import POSTHOC_METHODS

        if statistical_test not in {"t_test", "anova"}:
            raise ValueError("statistical_test must be 't_test' or 'anova'")
        if p_adjust not in {"none", "holm", "bonferroni", "sidak"}:
            raise ValueError("unknown multiple-testing correction")
        posthoc = self.review.posthoc if posthoc is None else posthoc
        if posthoc not in POSTHOC_METHODS:
            raise ValueError("unknown post-hoc test")
        if posthoc == "dunnett" and self.review.all_pairs:
            raise ValueError("Dunnett's test compares strains with a "
                             "reference; use Tukey HSD for every pair")
        alpha = self.review.alpha if alpha is None else float(alpha)
        if not 0 < alpha < 1:
            raise ValueError("alpha must be between 0 and 1")
        before = self.snapshot()
        self.review.statistical_test = statistical_test
        self.review.p_adjust = p_adjust
        self.review.posthoc = posthoc
        self.review.alpha = alpha
        if statistical_test == "t_test":
            detail = (f"ratio paired t-tests with {p_adjust} correction"
                      if p_adjust != "none"
                      else "ratio paired t-tests without correction")
        else:
            detail = ("one-way ANOVA, omnibus only" if posthoc == "none"
                      else f"one-way ANOVA with {posthoc} post-hoc")
        return self._commit(before, f"Use {detail}, α={alpha:g}")

    def strain_names(self) -> list[str]:
        """The strain panel, in slot order, for choosing references from.

        From the run's config when it is loaded, otherwise from whatever
        medium has already been rebuilt; empty when neither is available.
        """
        names = list((self.cfg or {}).get("strains") or [])
        if not names:
            for frame in self._frames.values():
                t = frame.tidy.sort_values("strain_col")
                names = t["strain"].drop_duplicates().tolist()
                break
        return list(dict.fromkeys(str(n) for n in names if n))

    def control_name(self, medium: str = "") -> str:
        """The current medium's control strain, when it can be told."""
        frame = self._frames.get(medium or self.medium)
        if frame is not None:
            rows = frame.tidy[frame.tidy["strain_col"].astype(int)
                              == frame.control_col]
            if not rows.empty:
                return str(rows["strain"].iloc[0])
        return ""

    def set_comparisons(self, extra_references, all_pairs: bool) -> bool:
        """Choose which pairs of strains are compared.

        Each medium's control is always a reference. An extra that happens to
        be a medium's control is simply a no-op there -- the control differs
        by medium, so it is not filtered out here. Asking for every pair while
        Dunnett is the post-hoc test
        switches it to Tukey HSD -- Dunnett only compares with a reference --
        and the change says so.
        """
        before = self.snapshot()
        self.review.extra_references = list(dict.fromkeys(
            str(r) for r in extra_references if str(r)))
        self.review.all_pairs = bool(all_pairs)
        if self.review.final_analysis:
            from experiments import analysis
            config = self.review.final_analysis
            config["all_pairs"] = bool(all_pairs)
            config["references"] = analysis.from_legacy(
                (self.cfg or {}).get("strains", []),
                config["normalization"].get("*"),
                {k: v for k, v in config["normalization"].items() if k != "*"},
                self.review.extra_references)["references"]
        label = f"Compare {self.review.comparisons_text()}"
        if self.review.all_pairs and self.review.posthoc == "dunnett":
            self.review.posthoc = "tukey"
            label += " (post-hoc test now Tukey HSD)"
        return self._commit(before, label)

    def revert_spot(self, replicate: str, strain_col: int, strain: str) -> bool:
        """Forget every correction to one spot."""
        medium = self.medium
        before = self.snapshot()
        self.review.set_edit(medium, SpotEdit(replicate, int(strain_col)))
        return self._commit(before, f"Revert {strain} {replicate}",
                            invalidate=medium)

    def revert_medium(self, medium: str = "") -> bool:
        medium = medium or self.medium
        before = self.snapshot()
        self.review.clear_medium(medium)
        return self._commit(before, f"Revert every edit on "
                                    f"{self.medium_label(medium)}",
                            invalidate=medium)

    def edit_for(self, replicate: str, strain_col: int) -> "SpotEdit | None":
        return self.review.edit(self.medium, (str(replicate), int(strain_col)))

    # -- rebuilt data --------------------------------------------------------

    def _frame_token(self):
        # Keep the run alive while a request exists, so Python cannot recycle
        # its id. Dataclass value equality must never equate two open runs.
        return self._generation,id(self.run),self.capture_root,self.run

    def _rebuild(self, cand: Candidate, edits: dict) -> Frame:
        return (self.rebuilder or rebuild)(
            self.capture_root, self.run.label, self.cfg, cand, edits,
            data_flags=self._data_flags)

    def _previewed(self, cand: "Candidate | None") -> bool:
        """True when `cand` is a candidate other than the chosen one."""
        if cand is None:
            return False
        chosen = self.chosen(cand.medium)
        return chosen is None or chosen.key != cand.key

    def frame(self, medium: str = "", force: bool = False,
              cand: "Candidate | None" = None) -> "Frame | None":
        """The rebuilt per-spot frame for a medium, or None if it cannot be built.

        Cached per medium and dropped whenever that medium's pick or edits
        change, so the table can never show numbers from a superseded decision.
        Slow the first time (it walks the capture tree); call it off the UI
        thread.

        `cand` is a candidate being previewed rather than chosen: its numbers
        are what the graph on screen shows, so they are what the table must
        show. Those are cached per candidate and dropped with the medium's.
        """
        self.data_flags()                # drops stale frames if it changed
        if self._previewed(cand):
            return self._preview_frame(cand)
        medium = medium or self.medium
        if force:
            self._frames.pop(medium, None)
            self._errors.pop(medium, None)
        if medium in self._frames:
            return self._frames[medium]
        if medium in self._errors or not self.can_edit:
            return None
        cand = self.chosen(medium)
        if cand is None:
            self._errors[medium] = (
                f"the candidate recorded for {self.medium_label(medium)} "
                f"is not in this run's "
                f"results any more; choose one from the list")
            return None
        try:
            token = self._frame_token()
            f = self._rebuild(cand, self.review.edits_for(medium))
        except RebuildError as e:
            if token != self._frame_token():
                return None
            self._errors[medium] = str(e)
            return None
        except Exception as e:                    # pragma: no cover - defensive
            if token != self._frame_token():
                return None
            self._errors[medium] = f"{type(e).__name__}: {e}"
            return None
        if token != self._frame_token() or self.chosen(medium).key != cand.key:
            return None
        self._frames[medium] = f
        return f

    def _preview_frame(self, cand: Candidate) -> "Frame | None":
        key = cand.key
        if key in self._preview_frames:
            return self._preview_frames[key]
        if key in self._preview_errors or not self.can_edit:
            return None
        try:
            token = self._frame_token()
            f = self._rebuild(cand, self.review.edits_for(cand.medium))
        except RebuildError as e:
            if token != self._frame_token():
                return None
            self._preview_errors[key] = str(e)
            return None
        except Exception as e:                    # pragma: no cover - defensive
            if token != self._frame_token():
                return None
            self._preview_errors[key] = f"{type(e).__name__}: {e}"
            return None
        if token != self._frame_token():
            return None
        self._preview_frames[key] = f
        return f

    def cached_frame(self, medium: str = "",
                     cand: "Candidate | None" = None) -> "Frame | None":
        """The rebuilt frame ONLY if it is already in hand.

        Used by every redraw, so that painting the window never blocks on
        reading a photograph. `frame()` is the one that does real work, and it
        belongs on a worker thread.
        """
        if self._previewed(cand):
            return self._preview_frames.get(cand.key)
        return self._frames.get(medium or self.medium)

    def is_pending(self, medium: str = "",
                   cand: "Candidate | None" = None) -> bool:
        """Rebuildable, but not rebuilt yet -- i.e. worth showing a wait for."""
        if self._previewed(cand):
            return (self.can_edit and cand.key not in self._preview_frames
                    and cand.key not in self._preview_errors)
        medium = medium or self.medium
        return (self.can_edit and medium not in self._frames
                and medium not in self._errors)

    def error(self, medium: str = "",
              cand: "Candidate | None" = None) -> str:
        if not self.can_edit:
            return self.config_error or "no capture folder linked"
        if self._previewed(cand):
            return self._preview_errors.get(cand.key, "")
        medium = medium or self.medium
        return self._errors.get(medium, "")

    def frames(self) -> tuple[list[Frame], list[str]]:
        """Every medium's frame, for export. Returns (frames, problems)."""
        out, bad = [], []
        for medium in self.run.media:
            f = self.frame(medium)
            if f is None:
                bad.append(f"{self.medium_label(medium)}: "
                           f"{self.error(medium)}")
            else:
                out.append(f)
        return out, bad

    # -- saving --------------------------------------------------------------

    def pin_sources(self) -> None:
        """Fix the run's photos (stable source ids) without saving anything else:
        all that cutting crops from the originals needs."""
        if self.run.candidates_csv.exists():
            runs.pin(self)
            identities = self.review.extensions.get("source_identities", {})
            for source in self.run.manifest.get("sources", []):
                if not source.get("sha256") and source["id"] in identities:
                    source["sha256"] = identities[source["id"]]
            self._load_cfg()

    def measuring(self):
        """``(count, work)`` for the candidates a save would still have to measure,
        or None if none. ``work(progress)`` only waits on the rebuilder (worker
        processes in the window), two at a time, so it belongs on a thread; the
        save after it finds them archived instead of measuring for minutes on
        the window's own thread (a result never saved has every unchosen
        dilution to measure)."""
        from copy import deepcopy
        from .. import measurements
        self.pin_sources()
        if not (self.run.candidates_csv.exists() and self.cfg and self.capture_root is not None):
            return None
        missing = [c for c in self.run.candidates
                   if c.key not in self._unmeasurable and not measurements.path_for(self.cfg, c).exists()]
        if not missing:
            return None
        build = self.rebuilder or rebuild
        root, label, cfg, flags = self.capture_root, self.run.label, deepcopy(self.cfg), deepcopy(self.data_flags())

        def work(progress=None):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            def one(cand):
                try:
                    build(root, label, cfg, cand, data_flags=flags)
                except Exception as exc:
                    return cand.key, str(exc)
            failed = {}
            with ThreadPoolExecutor(2) as pool:
                for k, job in enumerate(as_completed([pool.submit(one, c) for c in missing]), 1):
                    if job.result():
                        failed.update([job.result()])
                    if progress:
                        progress(k, len(missing))
            return failed
        return len(missing), work

    def measured(self, failed) -> None:
        """What ``measuring``'s work could not measure: the save records it as
        unavailable rather than trying it again on the window's thread."""
        self._unmeasurable.update(failed or {})

    def save(self) -> Path:
        if self.run.candidates_csv.exists():
            self.pin_sources()
            self._archive_measurements()
        if self.capture_root is not None:
            self.review.capture_root = str(self.capture_root)
        path = rv.save_for(self.run, self.review)
        if self.review.measurement or self.review.final_analysis:
            # A review owns its recalculated measurements as well as its
            # decisions. Geometry/quality/provenance columns are retained even
            # if the disposable acceleration cache is later removed.
            import hashlib
            import json
            folder = path.parent / "review_measurements"
            folder.mkdir(exist_ok=True)
            current = {}
            for frame in self._frames.values():
                key = hashlib.sha256(frame.medium.encode()).hexdigest()[:12]
                target = folder / f"{key}.csv"
                tmp = target.with_suffix(".csv.tmp")
                frame.tidy.to_csv(tmp, index=False)
                tmp.replace(target)
                current[frame.medium] = {"file": target.name, "candidate": frame.candidate.key}
            # Only entries in this manifest belong to the saved decisions.
            # Earlier generated CSVs are retained as historical artifacts.
            manifest = folder / "manifest.json"
            tmp = manifest.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"review": self.review.to_dict(), "current": current,
                "unlisted_files": "historical; rebuild before use"}, indent=2), encoding="utf-8")
            tmp.replace(manifest)
        self._saved = self.review.to_dict()
        self._notify()
        return path

    def _archive_measurements(self):
        """Own every candidate, including low-growth and unchosen dilutions."""
        from .. import measurements
        missing = []
        if self.cfg and self.capture_root is not None:
            for cand in self.run.candidates:
                path = measurements.path_for(self.cfg, cand)
                if path.exists():
                    continue
                if cand.key in self._unmeasurable:
                    missing.append({"candidate": list(cand.key), "reason": self._unmeasurable[cand.key]})
                    continue
                try:
                    rebuild(self.capture_root, self.run.label, self.cfg, cand,
                            data_flags=self.data_flags())
                except (RebuildError, OSError) as exc:
                    missing.append({"candidate": list(cand.key), "reason": str(exc)})
        elif self.run.candidates:
            missing.append({"reason": "Originals/configuration unavailable; existing artifacts retained"})
        manifest = getattr(self.run, "manifest", {})
        if manifest:
            manifest["measurement_archive"]["unavailable"] = missing
            runs.atomic_json(self.run.results_dir / runs.MANIFEST, manifest)

    def save_as(self, destination: Path) -> Path:
        return runs.copy_run(self, destination)

    # -- history -------------------------------------------------------------

    def _restore(self, snapshot: dict) -> None:
        before = self.snapshot()
        self.review = Review.from_dict(snapshot)
        if getattr(self.run, "manifest", {}):
            root = self.review.capture_root or self.run.manifest.get("original_photo_root")
            self.capture_root = Path(root) if root else None
        self._load_cfg()
        # Display undo/redo has no scientific consequence.
        before.pop("presentation", None)
        after = self.snapshot()
        after.pop("presentation", None)
        if before != after:
            self._frames.clear()
            self._errors.clear()
            self._drop_previews()
        self._notify()

    def undo(self) -> "str | None":
        got = self.undo_stack.undo(self.snapshot())
        if got is None:
            return None
        snapshot, label = got
        self._restore(snapshot)
        return label

    def redo(self) -> "str | None":
        got = self.undo_stack.redo(self.snapshot())
        if got is None:
            return None
        snapshot, label = got
        self._restore(snapshot)
        return label
