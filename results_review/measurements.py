"""Portable, non-pickle raw measurements and original-coordinate geometry.

Archives precede review edits and normalization. Changing references uses the
same raw data; changing measurement method creates a separate archive.
"""
from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path

from .runs import atomic_json


def _encode(value):
    import numpy as np
    if isinstance(value, np.ndarray):
        return {"$array": _encode(value.tolist()), "dtype": str(value.dtype)}
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": str(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value):
    import numpy as np
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        if "$array" in value:
            return np.asarray(_decode(value["$array"]), dtype=value["dtype"])
        if "$float" in value:
            return float(value["$float"])
        return {k: _decode(v) for k, v in value.items()}
    return value


def path_for(cfg, cand):
    from spotting_roi import settings_from_config
    folder = cfg.get("_run_dir")
    if not folder:
        return None
    signature = {"candidate": cand.key, "strains": cfg.get("strains"),
                 "layout": cfg.get("dilution_layout"),
                 "measurement": settings_from_config(cfg, legacy_if_missing=True).to_dict()}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return Path(folder) / "measurements" / f"{key}.json"


def save(cfg, cand, tidy, plates):
    path = path_for(cfg, cand)
    if path is None or path.exists() or cfg.get("_run_read_only"):
        return
    plate_data = []
    for plate in plates:
        item = {f.name: getattr(plate, f.name) for f in fields(plate) if f.name != "ref"}
        item["ref"] = {f.name: getattr(plate.ref, f.name) for f in fields(plate.ref)}
        root = cfg.get("_run_root")
        if root:
            item["source_relpath"] = Path(plate.ref.path).resolve().relative_to(Path(root).resolve()).as_posix()
            matches = [s for s in cfg.get("_run_sources", []) if s["relpath"] == item["source_relpath"]]
            measured_hash = (plate.provenance or {}).get("source_sha256")
            if matches and measured_hash and matches[0].get("sha256") != measured_hash:
                from .rebuild import RebuildError
                raise RebuildError("Measured source hash differs from the saved original identity")
        plate_data.append(item)
    atomic_json(path, _encode({"version": 1, "candidate": cand.key,
        "columns": list(tidy.columns), "dtypes": {c: str(d) for c, d in tidy.dtypes.items()},
        "rows": list(tidy.itertuples(index=False, name=None)), "plates": plate_data}))


def load(cfg, cand, root):
    import pandas as pd
    from spotting_batch import PlateData, PhotoRef
    path = path_for(cfg, cand)
    if path is None or not path.exists():
        return None
    raw = _decode(json.loads(path.read_text(encoding="utf-8")))
    if raw["version"] != 1:
        raise ValueError("Unsupported raw measurement archive")
    tidy = pd.DataFrame(raw["rows"], columns=raw["columns"]).astype(raw["dtypes"])
    plates = []
    for item in raw["plates"]:
        ref = item.pop("ref")
        rel = item.pop("source_relpath", None)
        original = Path(ref["path"])
        manifest = cfg.get("_run_sources", [])
        matches = [s for s in manifest if s.get("original_path") == str(original)]
        if rel is not None:
            from .runs import source_path
            ref["path"] = source_path(root, rel)
        elif len(matches) == 1:
            ref["path"] = Path(root) / matches[0]["relpath"]
        else:
            ref["path"] = original
        item["quant_rows"] = tuple(item["quant_rows"])
        item["plate_center"] = tuple(item["plate_center"])
        plates.append(PlateData(ref=PhotoRef(**ref), **item))
    return tidy, plates
