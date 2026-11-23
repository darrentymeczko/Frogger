"""Final analysis decisions. Headless, shared by setup, review and exports.

Slots identify normalization samples; exact strain names identify comparison
references. ``*`` supplies a group default, with condition overrides. Matching
only proposes references and never changes a scientific decision implicitly.
"""
from __future__ import annotations

from copy import deepcopy
import re


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("final_analysis must be an object")
    normalization = raw.get("normalization", {})
    references = raw.get("references", {})
    matching = raw.get("matching", {})
    if not all(isinstance(x, dict) for x in (normalization, references, matching)):
        raise ValueError("normalization, references and matching must be objects")
    for key, slot in normalization.items():
        if not isinstance(key, str) or (slot is not None and
                (type(slot) is not int or slot < 1)):
            raise ValueError("normalization requires condition names and positive slots or null")
    for key, refs in references.items():
        if not isinstance(key, str) or not isinstance(refs, list) or any(
                not isinstance(r, str) or not r.strip() for r in refs):
            raise ValueError("references requires condition names and lists of exact strain names")
    tokens = matching.get("tokens", ["WT", "EV"])
    if not isinstance(tokens, list) or any(not isinstance(t, str) or
            not re.fullmatch(r"[^\W_]+", t, re.UNICODE) for t in tokens):
        raise ValueError("matching tokens must be whole words (letters/numbers, no separators)")
    case = matching.get("case_sensitive", False)
    pairs = raw.get("all_pairs", False)
    if type(case) is not bool or type(pairs) is not bool:
        raise ValueError("case_sensitive and all_pairs must be booleans")
    return {"normalization": dict(normalization),
            "references": {k: list(dict.fromkeys(v)) for k, v in references.items()},
            "matching": {"tokens": list(dict.fromkeys(tokens)), "case_sensitive": case},
            "all_pairs": pairs}


def normalization(config: dict, condition: str) -> int | None:
    slots = config.get("normalization", {})
    return slots.get(condition, slots.get("*"))


def references(config: dict, condition: str) -> list[str]:
    refs = config.get("references", {})
    return list(refs.get(condition, refs.get("*", [])))


def suggest(strains, matching=None) -> list[str]:
    rules = validate({"matching": matching or {}})["matching"]
    fold = (lambda x: x) if rules["case_sensitive"] else str.casefold
    tokens = {fold(t) for t in rules["tokens"]}
    return list(dict.fromkeys(s for s in strains if s and
                tokens.intersection(fold(t) for t in re.findall(r"[^\W_]+", s))))


def pairs(names, refs, all_pairs=False) -> list[tuple[str, str]]:
    names = list(dict.fromkeys(n for n in names if n))
    selected = names if all_pairs else [r for r in dict.fromkeys(refs) if r in names]
    seen, result = set(), []
    for ref in selected:
        for name in names:
            key = frozenset((ref, name))
            if name != ref and key not in seen:
                seen.add(key)
                result.append((ref, name))
    return result


def from_legacy(strains, default, overrides=None, extra=(), all_pairs=False) -> dict:
    slots = {"*": default, **(overrides or {})}
    refs = {k: list(dict.fromkeys(([strains[v - 1]] if v and 0 < v <= len(strains)
                                and strains[v - 1] else []) + list(extra)))
            for k, v in slots.items()}
    return validate({"normalization": slots, "references": refs, "all_pairs": all_pairs})


def default_references(strains) -> list[str]:
    """Exact WT/EV names start selected; retain the panel's spelling and order."""
    return list(dict.fromkeys(s for s in strains if s and s.casefold() in {"wt", "ev"}))


def for_experiment(e, *, use_defaults=False) -> dict:
    """Resolve saved choices; optionally seed new editor choices with WT/EV."""
    return (validate(e.final_analysis) if e.final_analysis else
            from_legacy(e.strains, e.control_slot,
                        {c.code: c.control_slot for c in e.conditions if c.control_slot is not None},
                        ([*e.statistics.extra_references, *default_references(e.strains)]
                         if use_defaults else e.statistics.extra_references),
                        e.statistics.all_pairs))


def from_pipeline(cfg, extra=(), all_pairs=False) -> dict:
    return (validate(cfg["final_analysis"]) if cfg.get("final_analysis") else
            from_legacy(cfg.get("strains", []), cfg.get("control_col"),
                        {m: v["control_col"] for m, v in cfg.get("media", {}).items()
                         if v.get("control_col") is not None}, extra, all_pairs))


def apply(cfg: dict, config: dict) -> dict:
    """Copy only; never mutate the recorded run or its measurement settings."""
    out = deepcopy(cfg)
    if config:
        config = validate(config)
        out["final_analysis"] = config
        out["control_col"] = normalization(config, "*")
        for code in set(out.get("media", {})) | (set(config["normalization"]) - {"*"}):
            out.setdefault("media", {}).setdefault(code, {})["control_col"] = normalization(config, code)
    return out


def statistics(config: dict) -> dict:
    if not config:
        return {}
    return {"comparison_references": tuple((k, tuple(v)) for k, v in
                                          sorted(config["references"].items())),
            "all_pairs": config.get("all_pairs", False)}


def check_panel(config, strains, conditions, exclusions=None):
    """Validate committed choices before calculations; no implicit slot 1."""
    names = {s for s in strains if s}
    for code in conditions:
        slot = normalization(config, code)
        if slot is None or slot > len(strains) or not strains[slot - 1]:
            raise ValueError(f"{code}: choose a normalization reference in final Analysis / comparisons")
        if slot in (exclusions or {}).get(code, ()):
            raise ValueError(f"{code}: normalization reference is excluded")
        missing = set(references(config, code)) - names
        if missing:
            raise ValueError(f"{code}: unknown comparison references: {', '.join(sorted(missing))}")
