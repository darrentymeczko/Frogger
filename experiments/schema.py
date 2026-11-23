"""Reading and writing experiment JSON.

The file records who was spotted, on what, and where the photos are -- the
answers a photograph cannot supply. Like a plate template it is meant to be read
by a human and checked against the bench, because the critical failure mode is
still a mislabelled sample; unlike a template it has no grid to mirror, so plain
indented JSON is enough.

`ensure_ascii=False` throughout is not cosmetic: the strain names in this lab are
written with real Greek deltas ("ΔATX1") and escaping them would make the file
unreadable to the person who has to confirm it.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from .model import (
    DILUTION_MODES,
    KIND,
    MODES,
    OUTPUTS,
    P_ADJUST_METHODS,
    PHOTO_TOPS,
    POSTHOC_METHODS,
    SCHEMA_VERSION,
    STATISTICAL_TESTS,
    Condition,
    Experiment,
    OrderSlot,
    Statistics,
    StrainGroup,
)
from .profiles import (
    ProfileError, keyword_from_dict, keyword_to_dict, profile_from_dict, profile_to_dict,
)
from . import multistep, analysis


class ExperimentError(ValueError):
    """An experiment file could not be read. Messages always name the JSON path."""


_MISSING = object()


def _get(d: dict, key: str, path: str, types: type | tuple[type, ...], *, default=_MISSING):
    if key not in d or d[key] is None:
        if default is _MISSING:
            raise ExperimentError(f"{path}: missing required key {key!r}")
        return default
    value = d[key]
    if not isinstance(value, types):
        wanted = types if isinstance(types, tuple) else (types,)
        names = " or ".join(t.__name__ for t in wanted)
        raise ExperimentError(
            f"{path}.{key}: expected {names}, got {type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# dict <-> Experiment
# ---------------------------------------------------------------------------


def condition_to_dict(c: Condition) -> dict:
    d: dict = {"code": c.code, "label": c.label}
    if c.control_slot is not None:
        d["control_slot"] = c.control_slot
    if c.exclude:
        d["exclude"] = list(c.exclude)
    if c.dilution:
        d["dilution"] = dict(c.dilution)
    if c.categories:
        d["categories"] = dict(c.categories)
    return d


def condition_from_dict(raw: dict, path: str) -> Condition:
    if not isinstance(raw, dict):
        raise ExperimentError(f"{path}: expected an object, got {type(raw).__name__}")
    code = _get(raw, "code", path, str)
    if not code.strip():
        raise ExperimentError(f"{path}.code: must not be blank")

    dilution = _get(raw, "dilution", path, dict, default=None)
    if dilution is not None:
        mode = dilution.get("mode")
        if mode not in DILUTION_MODES:
            raise ExperimentError(
                f"{path}.dilution.mode: expected one of "
                f"{', '.join(DILUTION_MODES)}, got {mode!r}"
            )

    exclude = _get(raw, "exclude", path, list, default=[])
    for i, slot in enumerate(exclude):
        if not isinstance(slot, int):
            raise ExperimentError(
                f"{path}.exclude[{i}]: expected a sample slot number, "
                f"got {type(slot).__name__}"
            )

    categories = _get(raw, "categories", path, dict, default={})
    for name, value in categories.items():
        if not isinstance(value, str):
            raise ExperimentError(
                f"{path}.categories[{name!r}]: expected text, got {type(value).__name__}"
            )

    return Condition(
        code=code,
        label=_get(raw, "label", path, str, default=""),
        categories={str(k): v.strip() for k, v in categories.items() if v.strip()},
        control_slot=_get(raw, "control_slot", path, int, default=None),
        exclude=tuple(exclude),
        dilution=dilution,
    )


def statistics_to_dict(s: Statistics) -> dict:
    return s.review_dict()


def statistics_from_dict(raw: dict, path: str) -> Statistics:
    def choice(key: str, allowed: tuple[str, ...], default: str) -> str:
        value = _get(raw, key, path, str, default=default)
        if value not in allowed:
            raise ExperimentError(
                f"{path}.{key}: expected one of {', '.join(allowed)}, "
                f"got {value!r}"
            )
        return value

    test = choice("test", STATISTICAL_TESTS, "t_test")
    p_adjust = choice("p_adjust", P_ADJUST_METHODS, "none")
    posthoc = choice("posthoc", POSTHOC_METHODS, "dunnett")
    alpha = _get(raw, "alpha", path, (int, float), default=0.05)
    if isinstance(alpha, bool) or not 0 < float(alpha) < 1:
        raise ExperimentError(f"{path}.alpha: must be between 0 and 1")
    all_pairs = _get(raw, "all_pairs", path, bool, default=False)
    refs = _get(raw, "extra_references", path, list, default=[])
    for i, ref in enumerate(refs):
        if not isinstance(ref, str):
            raise ExperimentError(
                f"{path}.extra_references[{i}]: expected a strain name, "
                f"got {type(ref).__name__}"
            )
    if all_pairs and posthoc == "dunnett":
        # Dunnett only compares with a reference; mirrors Review.from_dict.
        posthoc = "tukey"
    return Statistics(
        test=test,
        p_adjust=p_adjust,
        posthoc=posthoc,
        alpha=float(alpha),
        all_pairs=all_pairs,
        extra_references=tuple(dict.fromkeys(r for r in refs if r.strip())),
    )


def keywords_from_list(raw: list, path: str) -> tuple:
    rules = []
    for i, item in enumerate(raw):
        try:
            rules.append(keyword_from_dict(item, f"{path}[{i}]"))
        except ProfileError as exc:
            raise ExperimentError(str(exc)) from exc
    return tuple(rules)


def order_to_list(order) -> list[dict]:
    return [{"plate": s.plate, **({"group": s.group} if s.group is not None else {})}
            for s in order]


def order_from_list(raw: list, path: str) -> tuple[OrderSlot, ...]:
    slots = []
    for i, item in enumerate(raw):
        where = f"{path}[{i}]"
        if not isinstance(item, dict):
            raise ExperimentError(f"{where}: expected an object, got {type(item).__name__}")
        plate = _get(item, "plate", where, int)
        if isinstance(plate, bool) or plate < 1:
            raise ExperimentError(f"{where}.plate: plate numbers start at 1")
        group = _get(item, "group", where, str, default=None)
        slots.append(OrderSlot(plate, group))
    return tuple(slots)


def to_dict(e: Experiment) -> dict:
    d: dict = {
        "schema_version": max(e.schema_version, SCHEMA_VERSION) if (e.strain_groups or e.multi_step.enabled or e.photo_order or e.keywords or e.categories) else e.schema_version,
        "kind": KIND,
        "id": e.id,
        "revision": e.revision,
        "name": e.name,
    }
    if e.description:
        d["description"] = e.description
    if e.created:
        d["created"] = e.created

    d["template"] = {"id": e.template_id, "path": e.template_path}
    if e.template_snapshot is not None:
        from copy import deepcopy
        d["template"]["snapshot"] = deepcopy(e.template_snapshot)
        d["schema_version"] = SCHEMA_VERSION
    d["mode"] = e.mode
    d["strains"] = list(e.strains)
    d["control_slot"] = e.control_slot
    if e.final_analysis:
        d["final_analysis"] = analysis.validate(e.final_analysis)
        d["schema_version"] = SCHEMA_VERSION
    if e.categories:
        d["categories"] = list(e.categories)
    d["conditions"] = [condition_to_dict(c) for c in e.conditions]
    d["statistics"] = statistics_to_dict(e.statistics)
    d["multi_step"] = multistep.to_dict(e.multi_step)
    d["output"] = e.output
    if e.strain_groups:
        d["strain_groups"] = {
            key: {"strains": list(g.strains), "control_slot": g.control_slot,
                  "final_analysis": analysis.validate(g.final_analysis) if g.final_analysis else {},
                  "picks": {c: dict(p) for c, p in g.picks.items()},
                  # Categories are shared; a group's copy never overrides them.
                  "conditions": [condition_to_dict(replace(c, categories={}))
                                 for c in g.conditions.values()]}
            for key, g in e.strain_groups.items()
        }

    photos: dict = {
        "root": e.photo_root,
        "experiment_top": e.photo_top,
        "profile": profile_to_dict(e.profile),
    }
    if e.set_key is not None:
        photos["set_key"] = e.set_key
    if e.photo_order:
        photos["order"] = order_to_list(e.photo_order)
    if e.keywords:
        photos["keywords"] = [keyword_to_dict(k) for k in e.keywords]
    if e.picks:
        photos["picks"] = {
            code: dict(sorted(slots.items()))
            for code, slots in sorted(e.picks.items())
        }
    if e.overrides:
        photos["overrides"] = {k: dict(v) for k, v in sorted(e.overrides.items())}
    if e.ignored:
        photos["ignored"] = sorted(e.ignored)
    d["photos"] = photos
    return d


def _migrate(d: dict) -> dict:
    version = d.get("schema_version")
    if version is None:
        raise ExperimentError("$: missing required key 'schema_version'")
    if not isinstance(version, int):
        raise ExperimentError("$.schema_version: expected an integer")
    if version > SCHEMA_VERSION:
        raise ExperimentError(
            f"$.schema_version: {version} is newer than this tool understands "
            f"(highest supported is {SCHEMA_VERSION}); update the Experiment Designer"
        )
    return d


def from_dict(d: dict) -> Experiment:
    if not isinstance(d, dict):
        raise ExperimentError(f"$: expected a JSON object, got {type(d).__name__}")
    d = _migrate(d)

    mode = _get(d, "mode", "$", str)
    if mode not in MODES:
        raise ExperimentError(
            f"$.mode: expected one of {', '.join(MODES)}, got {mode!r}"
        )

    output = _get(d, "output", "$", str, default="full")
    if output not in OUTPUTS:
        raise ExperimentError(
            f"$.output: expected one of {', '.join(OUTPUTS)}, got {output!r}"
        )

    template = _get(d, "template", "$", dict, default={})
    snapshot = _get(template, "snapshot", "$.template", dict, default=None)
    if snapshot is not None:
        from plate_template.schema import TemplateError, from_dict as layout_from_dict
        from plate_template.schema import to_dict as layout_to_dict
        try:
            snapshot = layout_to_dict(layout_from_dict(snapshot))
        except TemplateError as exc:
            raise ExperimentError(f"$.template.snapshot: {exc}") from exc
    strains_raw = _get(d, "strains", "$", list, default=[])
    strains: list[str | None] = []
    for i, s in enumerate(strains_raw):
        if s is None:
            strains.append(None)
        elif isinstance(s, str):
            strains.append(s.strip() or None)
        else:
            raise ExperimentError(
                f"$.strains[{i}]: expected a name or null, got {type(s).__name__}"
            )

    conditions = [
        condition_from_dict(raw, f"$.conditions[{i}]")
        for i, raw in enumerate(_get(d, "conditions", "$", list, default=[]))
    ]
    categories = _get(d, "categories", "$", list, default=[])
    for i, name in enumerate(categories):
        if not isinstance(name, str) or not name.strip():
            raise ExperimentError(f"$.categories[{i}]: expected a category name")
    categories = list(dict.fromkeys(n.strip() for n in categories))
    for i, c in enumerate(conditions):
        unknown = set(c.categories) - set(categories)
        if unknown:
            raise ExperimentError(
                f"$.conditions[{i}].categories: {sorted(unknown)[0]!r} is not one of "
                f"the experiment's categories")
    seen: set[str] = set()
    for c in conditions:
        if c.code in seen:
            raise ExperimentError(
                f"$.conditions: {c.code!r} appears more than once; condition "
                f"codes name result files and must be unique"
            )
        seen.add(c.code)

    photos = _get(d, "photos", "$", dict, default={})
    photo_top = _get(
        photos, "experiment_top", "$.photos", str, default="top"
    ).strip().lower()
    if photo_top not in PHOTO_TOPS:
        raise ExperimentError(
            "$.photos.experiment_top: expected one of "
            f"{', '.join(PHOTO_TOPS)}, got {photo_top!r}"
        )
    try:
        profile = profile_from_dict(
            _get(photos, "profile", "$.photos", dict, default={}), "$.photos.profile"
        )
    except ProfileError as exc:
        raise ExperimentError(str(exc)) from exc

    picks_raw = _get(photos, "picks", "$.photos", dict, default={})
    picks: dict[str, dict[str, str]] = {}
    for code, slots in picks_raw.items():
        if not isinstance(slots, dict):
            raise ExperimentError(
                f"$.photos.picks[{code!r}]: expected an object of plate -> file, "
                f"got {type(slots).__name__}"
            )
        for plate, relpath in slots.items():
            if not isinstance(relpath, str):
                raise ExperimentError(
                    f"$.photos.picks[{code!r}][{plate!r}]: expected a file path, "
                    f"got {type(relpath).__name__}"
                )
            picks.setdefault(str(code), {})[str(plate)] = relpath

    overrides_raw = _get(photos, "overrides", "$.photos", dict, default={})
    overrides: dict[str, dict] = {}
    for key, value in overrides_raw.items():
        if not isinstance(value, dict):
            raise ExperimentError(
                f"$.photos.overrides[{key!r}]: expected an object, "
                f"got {type(value).__name__}"
            )
        overrides[str(key)] = dict(value)

    groups = {}
    for key, raw in _get(d, "strain_groups", "$", dict, default={}).items():
        path = f"$.strain_groups[{key!r}]"
        if not isinstance(key, str) or not key.strip() or key != key.strip() or "|" in key or any(ord(c) < 32 for c in key):
            raise ExperimentError(f"{path}: use a nonblank group name without | or control characters")
        if not isinstance(raw, dict):
            raise ExperimentError(f"{path}: expected an object")
        # Reuse the same strict panel/pick/condition validation as a legacy file.
        try:
            panel = from_dict({"schema_version": d["schema_version"], "mode": mode,
                               "strains": raw.get("strains", []),
                               "control_slot": raw.get("control_slot"),
                               "final_analysis": raw.get("final_analysis", {}),
                               "statistics": d.get("statistics", {}),
                               "conditions": raw.get("conditions", []),
                               "photos": {"picks": raw.get("picks", {})}})
        except ExperimentError as exc:
            raise ExperimentError(f"{path}: {exc}") from exc
        groups[key] = StrainGroup(panel.strains, panel.control_slot, panel.picks,
                                 {c.code: c for c in panel.conditions},
                                 final_analysis=panel.final_analysis)
        if d["schema_version"] < 5 and not raw.get("final_analysis"):
            # An absent group condition inherited the entire shared condition;
            # an explicit group condition replaced it (including a null control).
            # Preserve that distinction while migrating only scientific choices.
            effective = [groups[key].conditions.get(c.code, c) for c in conditions]
            groups[key].final_analysis = analysis.from_legacy(
                panel.strains, panel.control_slot,
                {c.code: c.control_slot for c in effective if c.control_slot is not None},
                panel.statistics.extra_references, panel.statistics.all_pairs)

    try:
        multi_step = multistep.from_dict(d.get("multi_step", {}))
    except (ValueError, TypeError) as exc:
        raise ExperimentError(f"$.multi_step: {exc}") from exc

    experiment = Experiment(
        name=_get(d, "name", "$", str, default="Untitled"),
        template_id=_get(template, "id", "$.template", str, default=""),
        template_path=_get(template, "path", "$.template", str, default=""),
        template_snapshot=snapshot,
        strains=strains,
        control_slot=_get(d, "control_slot", "$", int, default=None),
        conditions=conditions,
        categories=categories,
        mode=mode,
        photo_top=photo_top,
        photo_root=_get(photos, "root", "$.photos", str, default=""),
        set_key=_get(photos, "set_key", "$.photos", str, default=None),
        strain_groups=groups,
        profile=profile,
        photo_order=order_from_list(
            _get(photos, "order", "$.photos", list, default=[]), "$.photos.order"),
        keywords=keywords_from_list(
            _get(photos, "keywords", "$.photos", list, default=[]), "$.photos.keywords"),
        picks=picks,
        overrides=overrides,
        ignored=[str(p) for p in _get(photos, "ignored", "$.photos", list, default=[])],
        statistics=statistics_from_dict(
            _get(d, "statistics", "$", dict, default={}), "$.statistics"
        ),
        output=output,
        multi_step=multi_step,
        id=_get(d, "id", "$", str, default=""),
        revision=_get(d, "revision", "$", int, default=1),
        description=_get(d, "description", "$", str, default=""),
        created=_get(d, "created", "$", str, default=""),
        schema_version=SCHEMA_VERSION,
    )

    try:
        experiment.final_analysis = (analysis.validate(d["final_analysis"])
            if d.get("final_analysis") else
            analysis.for_experiment(experiment) if d["schema_version"] < 5 else {})
    except ValueError as exc:
        raise ExperimentError(f"$.final_analysis: {exc}") from exc
    return experiment


# ---------------------------------------------------------------------------
# Text and files
# ---------------------------------------------------------------------------


def dumps_experiment(e: Experiment) -> str:
    return json.dumps(to_dict(e), indent=2, ensure_ascii=False) + "\n"


def loads_experiment(text: str) -> Experiment:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"not valid JSON: {exc}") from exc
    return from_dict(raw)


def load(path: Path) -> Experiment:
    # utf-8-sig so a file hand-edited in Notepad or written by PowerShell, which
    # add a UTF-8 BOM, still loads. Writing stays BOM-free.
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ExperimentError(f"could not read {path}: {exc}") from exc
    try:
        experiment = loads_experiment(text)
        _pin_legacy_layout(experiment, Path(path).parent)
        return experiment
    except ExperimentError as exc:
        raise ExperimentError(f"{path}: {exc}") from exc


def save(e: Experiment, path: Path, *, bump_revision: bool = True) -> None:
    """Write atomically -- these files live on OneDrive."""
    _pin_legacy_layout(e, Path(path).parent)
    if bump_revision:
        e.revision += 1
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps_experiment(e), encoding="utf-8")
    os.replace(tmp, path)


def _pin_legacy_layout(e: Experiment, base: Path) -> None:
    from plate_template.schema import TemplateError
    from .layout import pin
    # Incomplete/old experiments remain editable and saveable when their old
    # source is missing. The controller reports that problem. Never substitute
    # an external file for an already embedded snapshot.
    if e.template_snapshot is None:
        try:
            pin(e, base)
        except TemplateError:
            pass
