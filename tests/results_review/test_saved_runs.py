"""Task 5 acceptance: real measurement, saved ownership, and native Tk actions."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pandas as pd
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import spotting_quant as sq
import spotting_timecourse as tc
import spotting_batch as sb
from experiments import analysis, layout, schema, run as erun
from experiments.model import Experiment, Condition
from plate_template.presets import lab_standard_8x6
from plate_template.schema import to_dict as layout_dict
from data_review import flags as flagfile
from data_review.flags import DataFlags
import results_review
from results_review import discovery, links, runs, review as rv, rebuild as rb
from results_review.gui.controller import ReviewController


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): runs.sha256(p)
            for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def generated(tmp_path, monkeypatch):
    """Supply lattice locations only; use production measurement/scoring."""
    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "SEARCH_HINTS", [])
    monkeypatch.setattr(results_review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(discovery, "TIMECOURSE_RESULTS", tmp_path / "results")
    root = tmp_path / "originals"; root.mkdir()
    centers = np.array([[(85.25+95*r, 85.75+95*c) for c in range(8)] for r in range(6)])
    grid = sq.Grid(centers, 31, 31, 62, (330, 425), 600, plate_known=False)
    monkeypatch.setattr(sq, "detect_grid", lambda *a, **k: replace(grid))
    monkeypatch.setattr(sq, "background_gap_centers", lambda *a: [(40,40),(40,140),(40,240),(40,340),(40,440)])
    yy, xx = np.mgrid[:660, :850]
    shots = []
    for condition in ("A", "B"):
        for plate in (1, 2):
            pixels = np.full(yy.shape, 30, dtype=np.uint8)
            for r, c in np.ndindex(6, 8):
                cy, cx = centers[r, c]
                rad = np.hypot(yy+.5-cy, xx+.5-cx)
                value = 30 if condition == "B" or c == 3 else 85+4*c+r+plate
                pixels[rad < 31] = value
                pixels[(rad >= 27) & (rad < 31)] = 230
            path = root / f"{condition}-{plate}.png"
            Image.fromarray(pixels).save(path)
            shots.append(tc.Shot(path, 24., "24 Hours", condition, condition, plate))
    names = ["WT", "EV", "mut", "low", "s5", "s6", "s7", "s8"]
    e = Experiment(name="Task05 generated", strains=names, conditions=[Condition(c, c) for c in ("A", "B")],
        photo_root=str(root), template_snapshot=layout_dict(lab_standard_8x6()),
        final_analysis=analysis.validate({"normalization": {"*": 1}, "references": {"*": ["WT"]}}))
    cfg = erun.to_pipeline_config(e)
    cfg["resolved_photos"] = [dict(relpath=s.path.name, condition=s.medium,
        timepoint=24., timepoint_label="24 Hours", plate=s.plate) for s in shots]
    folder = tmp_path / "results" / "generated"; folder.mkdir(parents=True)
    flags = DataFlags()
    flag_path = tmp_path / "live.datareview.json"
    flagfile.save(flag_path, flags)
    snapshot = dict(experiment=schema.to_dict(e), pipeline_config=cfg,
        photo_root=str(root), strains=names, data_review={"file": str(flag_path), "flags": flags.to_dict()})
    runs.atomic_json(folder / "experiment.json", snapshot)
    args = SimpleNamespace(cache_dir=tmp_path / ".spotting_cache", workers=1, timing=False,
        figures="none", estimate=False, rank_by="combined", out=folder)
    assert tc.run_one(tc.Tree(root, e.name), args, cfg, shots=shots, statistics=e.statistics_kwargs()) == 0
    erun.seed_review_statistics(e, folder)
    run = discovery.load_set(folder)
    assert len(run.candidates) == 6
    ctl = ReviewController(run, rv.load_for(run))
    for medium in run.media:
        ctl.choose(next(c for c in run.for_medium(medium) if c.dilution == "least"))
    return SimpleNamespace(ctl=ctl, folder=folder, root=root, experiment=e, flags=flag_path,
                           hashes=tree_hashes(root), tmp=tmp_path)


def reopen(path):
    run = discovery.load_set(path)
    return ReviewController(run, rv.load_for(run))


def test_two_owned_runs_roundtrip_without_cross_mutation(generated, monkeypatch):
    g = generated
    ctl = g.ctl
    raw_a = ctl.frame("A").tidy.copy()
    raw_b = ctl.frame("B").tidy.copy()
    assert raw_a[raw_a.strain == "low"].growth_quality.eq("low_growth").all()
    assert raw_b.relative_growth.isna().all()
    ctl.save()
    id_a = ctl.run.manifest["id"]
    assert len({s["id"] for s in ctl.run.manifest["sources"]}) == 4
    assert not ctl.run.manifest["measurement_archive"]["unavailable"]
    original_archives = tree_hashes(g.folder / "measurements")
    assert len(original_archives) == 6
    frozen_a = tree_hashes(g.folder)
    second = g.folder.parent / "independent B"
    ctl.save_as(second)
    assert tree_hashes(g.folder) == frozen_a
    assert ctl.run.manifest["id"] != id_a
    assert tree_hashes(second / "measurements") == original_archives
    a, b = reopen(g.folder), reopen(second)
    # Destroy mutable upstream state; both runs retain their snapshots.
    runs.atomic_json(g.flags, {"version": 1, "plates": {"A-1.png": {"reason": "later"}}})
    g.experiment.template_snapshot["name"] = "changed after run"
    assert a.run.experiment.data_review_file == b.run.experiment.data_review_file == ""
    assert a.data_flags().is_empty and b.data_flags().is_empty
    assert a.run.experiment.pipeline_config == b.run.experiment.pipeline_config
    # Prove reopening/reanalysis uses owned data with no shared cache/measurement calls.
    rb.release_memory()
    original_builder = tc.build_tidy_for_candidate
    def no_measurement(*a, **k):
        raise AssertionError("Saved run remeasured an original for analysis-only change")
    monkeypatch.setattr(tc, "build_tidy_for_candidate", no_measurement)
    pd.testing.assert_frame_equal(a.frame("A").tidy, raw_a)
    pd.testing.assert_frame_equal(a.frame("B").tidy, raw_b)
    final = deepcopy(b.review.final_analysis)
    final["normalization"]["*"] = 2
    final["references"] = {"*": ["EV"]}
    b.set_final_analysis(final)
    b.set_excluded("rep1", 3, "mut", True)
    b.set_note("rep2", 4, "low", "retain valid low growth")
    b.set_included("B", False)
    b.set_statistics("t_test", "holm", .01)
    presentation = {"version": 1, "figure": {"title": "Independent B", "future": {"x": [1,2]}},
        "montage": {"order": ["crop-a"], "crops": [{"id": "crop-a", "source_id": b.run.manifest["sources"][0]["id"],
            "original_region": {"kind": "rectangle", "xywh": [50,60,90,100]}, "strain": "EV",
            "strain_slot": 2, "replicate": "rep1", "dilution_index": 0, "future_crop": ["opaque"]}]}}
    b.set_presentation(presentation)
    changed = b.frame("A")
    pd.testing.assert_series_equal(changed.tidy.raw_growth, raw_a.raw_growth)
    assert not np.allclose(changed.tidy.relative_growth, raw_a.relative_growth, equal_nan=True)
    assert changed.tidy.loc[changed.tidy.strain_col.eq(2), "is_control"].all()
    b.save()
    assert tree_hashes(g.folder) == frozen_a
    frozen_b = tree_hashes(second)
    a.set_note("rep1", 1, "WT", "A only")
    a.set_included("A", False)
    a.set_statistics("anova", "none", .02, "tukey")
    a.set_presentation({"version": 1, "figure": {"title": "Independent A"}})
    a.save()
    assert tree_hashes(second) == frozen_b
    a, b = reopen(g.folder), reopen(second)
    assert not a.review.is_included("A") and b.review.is_included("A")
    assert a.review.is_included("B") and not b.review.is_included("B")
    assert a.review.statistical_test == "anova" and b.review.p_adjust == "holm"
    assert b.review.presentation == presentation
    assert b.review.final_analysis["normalization"]["*"] == 2
    assert a.review.final_analysis["normalization"]["*"] == 1
    pd.testing.assert_frame_equal(b.frame("A").tidy, changed.tidy)
    assert tree_hashes(g.folder / "measurements") == original_archives
    assert tree_hashes(second / "measurements") == original_archives
    # A scientific ROI change in B creates a separate raw archive; A and B's
    # original-method artifacts must stay byte-identical.
    monkeypatch.setattr(tc, "build_tidy_for_candidate", original_builder)
    a_bytes = tree_hashes(g.folder)
    b.set_measurement(.23, 49)
    interior = b.frame("A")
    assert interior is not None, b.error("A")
    assert interior.tidy.inward_margin.eq(.23).all()
    assert not np.allclose(interior.tidy.roi_width, raw_a.roi_width)
    b.save()
    assert tree_hashes(g.folder) == a_bytes
    new_archives = tree_hashes(second / "measurements")
    assert len(new_archives) == 12
    assert all(new_archives[name] == digest for name, digest in original_archives.items())
    b = reopen(second)
    assert b.review.measurement["inward_margin"] == .23
    assert not reopen(g.folder).review.measurement
    pd.testing.assert_frame_equal(b.frame("A").tidy, interior.tidy)
    assert tree_hashes(g.root) == g.hashes
    snapshot = json.loads((second / "experiment.json").read_text())
    assert snapshot["experiment"]["template"]["snapshot"] != g.experiment.template_snapshot
    runs.atomic_json(g.tmp / "acceptance.json", {"runs": [id_a, b.run.manifest["id"]],
        "candidates_per_run": 6, "raw_rows_per_candidate": len(raw_a), "source_hashes": g.hashes,
        "raw_archive_hashes": original_archives, "null_derived_rows": int(raw_b.relative_growth.isna().sum()),
        "low_growth_rows": int(raw_a.growth_quality.eq("low_growth").sum()),
        "cross_run_mutations": 0, "upstream_flags_detached": True,
        "B_archives_after_ROI_change": len(new_archives), "B_inward_margin": .23})


def test_missing_relinked_and_changed_originals(generated):
    from results_review.cli import run_one
    g = generated; g.ctl.save()
    moved = g.tmp / "moved originals"
    g.root.rename(moved)
    fresh = reopen(g.folder)
    assert len(fresh.source_problems()) == 4
    # Numeric review remains possible from preserved raw data.
    assert fresh.frame("A") is not None
    before_cli = tree_hashes(g.folder)
    assert run_one(g.folder, dry_run=True) == 0
    assert tree_hashes(g.folder) == before_cli
    fake = g.tmp / "display exports"; shutil.copytree(moved, fake)
    Image.new("L", (80,80), 200).save(fake / "A-1.png")
    before = fresh.snapshot()
    ok, why = fresh.link_photos(fake)
    assert not ok and "SHA256 mismatch" in why
    assert run_one(g.folder, dry_run=True, photos=fake) == 1
    assert tree_hashes(g.folder) == before_cli
    assert fresh.snapshot() == before
    assert fresh.link_photos(moved)[0]
    assert run_one(g.folder, dry_run=True, photos=moved) == 0
    assert tree_hashes(g.folder) == before_cli
    assert not fresh.source_problems()
    fresh.save()
    again = reopen(g.folder)
    assert again.capture_root == moved.resolve()
    assert not again.source_problems()
    assert tree_hashes(moved) == g.hashes
    # Photo paths used by montage/export now resolve to originals, not exports.
    cand = again.chosen("A")
    found = rb.pipeline_candidate(moved, cand, again.cfg)
    assert all(s.path.parent == moved for s in found["plates"])
    bad = deepcopy(again.cfg); bad["resolved_photos"][0]["relpath"] = "../escape.png"
    with pytest.raises(rb.RebuildError, match="leaves the linked folder"):
        rb.pipeline_candidate(moved, cand, bad)
    runs.atomic_json(g.tmp / "sources-acceptance.json", {"missing_reported": 4,
        "display_export_rejected": True, "relinked_hashes": tree_hashes(moved)})


def test_legacy_read_only_and_unavailable_sources(generated):
    g = generated
    # Simulate a pre-provenance legacy CSV without source hashes.
    for path in (g.folder / "best").glob("*.csv"):
        table = pd.read_csv(path)
        if "source_sha256" in table:
            table.drop(columns=["source_sha256"]).to_csv(path, index=False)
    before = tree_hashes(g.folder)
    fresh = reopen(g.folder)
    assert not fresh.run.manifest and tree_hashes(g.folder) == before
    g.root.rename(g.tmp / "restore originals")
    fresh.save()
    assert fresh.run.manifest["measurement_archive"]["unavailable"]
    assert all(s["sha256"] is None for s in fresh.run.manifest["sources"])
    assert len(fresh.source_problems()) == 4
    assert fresh.link_photos(g.tmp / "restore originals")[0]
    fresh.save()
    assert not reopen(g.folder).source_problems()
    assert len(list((g.folder / "measurements").glob("*.json"))) == 6


def test_first_save_keeps_recorded_hash_if_original_changed(generated):
    g = generated
    best_before = tree_hashes(g.folder / "best")
    Image.new("L", (100,100), 155).save(g.root / "A-1.png")
    g.ctl.save()
    source = next(s for s in g.ctl.run.manifest["sources"] if s["relpath"] == "A-1.png")
    assert source["sha256"] == g.hashes["A-1.png"]
    assert "SHA256 mismatch" in "; ".join(g.ctl.source_problems())
    assert g.ctl.run.manifest["measurement_archive"]["unavailable"]
    assert tree_hashes(g.folder / "best") == best_before


def test_missing_legacy_layout_never_becomes_a_live_reference(generated):
    g = generated
    raw = json.loads((g.folder / "experiment.json").read_text())
    missing = g.tmp / "legacy-missing-template.json"
    raw["experiment"]["template"].pop("snapshot")
    raw["experiment"]["template"]["path"] = str(missing)
    runs.atomic_json(g.folder / "experiment.json", raw)
    ctl = reopen(g.folder)
    ctl.save()
    saved = json.loads((g.folder / "experiment.json").read_text())
    assert saved["experiment"]["template"]["path"] == ""
    assert saved["experiment"]["template"]["source_path"] == str(missing)
    assert ctl.run.manifest["snapshot_warnings"]
    runs.atomic_json(missing, layout_dict(lab_standard_8x6()))
    assert layout.resolve(schema.from_dict(saved["experiment"])) is None
    assert ctl.frame("A") is not None


def test_save_as_rejects_existing_nested_and_rolls_back(generated, monkeypatch):
    g = generated; g.ctl.save()
    before = tree_hashes(g.folder)
    for bad in (g.folder, g.folder / "nested", g.folder.parent):
        with pytest.raises(ValueError):
            g.ctl.save_as(bad)
    def fail(*a, **kw):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(runs.shutil, "copytree", fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        g.ctl.save_as(g.tmp / "failed copy")
    assert g.ctl.run.results_dir == g.folder.resolve()
    assert tree_hashes(g.folder) == before
    assert not (g.tmp / "failed copy").exists()
    assert not list(g.tmp.glob(".result-copy-*"))


@pytest.mark.parametrize("hosted", [False, True], ids=["standalone", "workbench"])
def test_native_tk_results_actions(generated, tk_root, monkeypatch, hosted):
    from results_review import app as appmod, background
    from results_review.app import ReviewApp
    from workbench.shell import Shell
    from workbench.settings import Settings
    g = generated
    monkeypatch.setattr(background, "warm", lambda: None)
    monkeypatch.setattr(ReviewApp, "_rebuild_current", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(appmod.messagebox, "showerror", lambda *a, **k: errors.append(a))
    window = tk.Toplevel(tk_root)
    shell = None
    if hosted:
        shell = Shell(window, Settings(None), restore=False, log_dir=None)
        monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(g.folder))
        i = next(i for i in range(shell.file_menu.index("end")+1)
                 if shell.file_menu.type(i) == "command" and shell.file_menu.entrycget(i,"label") == "Open Results…")
        shell.file_menu.invoke(i)
        app = shell.active.app
        assert not shell.runner.jobs
    else:
        app = ReviewApp(window, discovery.load_set(g.folder))
    window.update()
    menu = app.results_menu
    labels = [menu.entrycget(i,"label") for i in range(menu.index("end")+1)]
    assert labels == ["Open Results…", "Save Results", "Save Results As…", "Relink Original Images…", "Rename selected treatment...", "Rename result..."]
    menu.invoke(1)
    # The unchosen dilutions are measured off the window's thread first, then it saves.
    deadline = time.monotonic()+120
    while (app._busy or app.ctl.dirty) and time.monotonic() < deadline:
        window.update(); time.sleep(.02)
    assert not app.ctl.dirty and not app.ctl.run.manifest["measurement_archive"]["unavailable"]
    first_id = app.ctl.run.manifest["id"]
    first_files = tree_hashes(g.folder)
    second = g.folder.parent / "GUI independent"
    monkeypatch.setattr(appmod.filedialog, "asksaveasfilename", lambda **k: str(second))
    menu.invoke(2)
    window.update()
    assert not errors, errors
    assert app.ctl.run.results_dir == second.resolve(), app.status.label.cget("text") if hasattr(app.status, "label") else app._busy
    assert app.ctl.run.manifest["id"] != first_id
    assert tree_hashes(g.folder) == first_files
    # Real tone/view controls change persisted presentation, never science config.
    cfg = deepcopy(app.ctl.cfg)
    app.sheet.set_view("aligned")
    app.sheet.toggle_rotate()
    app.sheet._brightness_var.set(1.3); app.sheet._adjusted()
    app.ctl.set_included("B", False)
    menu.invoke(1)
    assert cfg == app.ctl.cfg
    monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(second))
    menu.invoke(0)
    window.update()
    opened = shell.active.app if shell else app
    assert opened.ctl.review.presentation["sheet"]["brightness"] == 1.3
    assert opened.sheet.view == "aligned" and opened.sheet.rotated
    assert not opened.ctl.review.is_included("B")
    assert not errors
    relocated = g.tmp / "GUI relinked originals"
    original_root = app.ctl.capture_root
    app._busy = True
    monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: pytest.fail("Relink dialog opened during rebuild"))
    menu.invoke(3)
    assert app.ctl.capture_root == original_root
    app._busy = False
    g.root.rename(relocated)
    monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(relocated))
    menu.invoke(3)
    assert app.ctl.capture_root == relocated.resolve()
    menu.invoke(1)
    # Close and truly reopen the saved B tab in workbench (no dedupe shortcut).
    if hosted:
        monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(g.folder))
        menu.invoke(0)
        a_app = shell.active.app
        assert a_app is not app
        assert shell.close_document(app.host)
        monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(second))
        a_app.results_menu.invoke(0)
        opened = shell.active.app
        assert opened is not app and opened.ctl.capture_root == relocated.resolve()
        assert opened.sheet.view == "aligned" and opened.sheet.rotated
    assert tree_hashes(relocated) == g.hashes
    assert not errors
    runs.atomic_json(g.tmp / "gui-acceptance.json", {"host": "workbench" if hosted else "standalone",
        "menus_invoked": labels, "first_id": first_id, "second_id": opened.ctl.run.manifest["id"],
        "jobs_required": False, "presentation_reopened": opened.sheet.presentation()})
    if shell:
        shell.exit(force=True)
    else:
        app._dispose(); window.destroy()


def test_forward_compatible_presentation_and_unknown_review_fields():
    value = {"version": 1, "presentation": {"version": 17, "unknown": {"nested": [1,2]}},
             "future_review": {"x": [3]}, "extensions": {"another-editor": {"opaque": True}}}
    loaded = rv.Review.from_dict(value)
    data = loaded.to_dict()
    data["presentation"]["unknown"]["nested"].append(9)
    assert loaded.to_dict()["presentation"] == value["presentation"]
    assert loaded.to_dict()["future_review"] == value["future_review"]


def test_legacy_artifacts_save_as_without_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "SEARCH_HINTS", [])
    folder = tmp_path / "Legacy scientific label"; folder.mkdir()
    (folder / "timecourse_candidates.csv").write_text(
        "medium,timepoint,hours,plate1,plate2,dilution\nA,24 Hours,24,a.png,b.png,least\n")
    (folder / "best").mkdir()
    (folder / "best" / "raw.csv").write_text("raw_growth,relative_growth\n0,\n")
    ctl = reopen(folder)
    before = tree_hashes(folder)
    fork = tmp_path / "Legacy independent"
    ctl.save_as(fork)
    assert tree_hashes(folder) == before
    assert ctl.run.manifest["scientific_label"] == folder.name
    assert ctl.run.manifest["measurement_archive"]["unavailable"]
    assert (fork / "best" / "raw.csv").read_bytes() == (folder / "best" / "raw.csv").read_bytes()
    assert not (fork / "best" / "raw.csv").samefile(folder / "best" / "raw.csv")
    assert not ctl.link_photos(tmp_path)[0], "Do not guess sources when no original assignments survived"


def test_saved_run_is_not_overwritten_by_experiment_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "TIMECOURSE_RESULTS", tmp_path)
    experiment = Experiment(name="Protected run")
    saved = erun.default_results_dir(experiment)
    saved.mkdir()
    (saved / "run.json").write_text("{}")
    assert erun.default_results_dir(experiment) == tmp_path / "Protected run - Run 2"
    before = tree_hashes(saved)
    with pytest.raises(erun.RunError, match="independently saved"):
        erun.run(experiment, outdir=saved, res=SimpleNamespace())
    assert tree_hashes(saved) == before


def test_presentation_undo_keeps_scientific_frames_and_future_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "resolve", lambda *a: None)
    run = discovery.SetRun(tmp_path, "presentation-only")
    ctl = ReviewController(run)
    sentinel = object()
    ctl._frames["A"] = sentinel
    value = {"version": 1, "figure": {"future": [1]}, "sheet": {"brightness": 1.3}}
    ctl.set_presentation(value)
    ctl.undo()
    assert ctl._frames["A"] is sentinel
    ctl.redo()
    assert ctl.review.presentation == value
    assert ctl._frames["A"] is sentinel


def test_invalid_manifest_version_is_explicit_and_does_not_rewrite(tmp_path):
    path = tmp_path / "run.json"
    path.write_text('{"kind":"spotting_result_run","version":19,"future":true}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported result run version"):
        runs.read(tmp_path)
    assert path.read_bytes() == before
