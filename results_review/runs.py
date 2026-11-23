"""Owned analysis runs, additive to the existing timecourse folder format.

Only originals are referenced; display exports are never source candidates.
Copying uses ordinary files, never links. Opening never writes anything.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

MANIFEST = "run.json"


def read(folder):
    path = Path(folder) / MANIFEST
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("kind") != "spotting_result_run":
        raise ValueError(f"Invalid result run manifest: {path}")
    if value.get("version") != 1:
        raise ValueError(f"Unsupported result run version: {value.get('version')}")
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_path(root, relpath):
    root = Path(root).resolve()
    rel = Path(relpath)
    path = (root / rel).resolve()
    if rel.is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"Source path leaves original-image folder: {relpath}")
    return path


def preflight_export_paths(paths, manifest, root=None, *, extra_sources=()):
    """Reject the entire output batch before writing any destination file.

    Protect recorded original names even after a relink, current root-relative
    paths (including missing originals), resolved aliases, and existing hard
    links. Byte hashes identify sources; they never authorize overwriting one.
    """
    originals = [Path(p).expanduser().resolve() for p in extra_sources]
    for source in manifest.get("sources", []):
        if source.get("original_path"):
            originals.append(Path(source["original_path"]).expanduser().resolve())
        if root is not None and source.get("relpath"):
            originals.append(source_path(root, source["relpath"]))
    originals = list(dict.fromkeys(originals))
    for path in paths:
        target = Path(path).expanduser().resolve()
        for original in originals:
            same = target == original
            if not same and target.exists() and original.exists():
                same = target.samefile(original)
            if same:
                raise ValueError(f"Export would overwrite a protected original source: {target}. Choose a different filename/folder.")


def source_problems(manifest, root):
    bad = ["Original assignments unavailable: " + str(item.get("reason", ""))
           for item in manifest.get("unresolved_source_assignments", [])]
    for source in manifest.get("sources", []):
        try:
            path = source_path(root, source["relpath"])
            if not path.is_file():
                bad.append(f"Missing original: {source['relpath']}")
            elif source.get("sha256") and sha256(path) != source["sha256"]:
                bad.append(f"Changed original (SHA256 mismatch): {source['relpath']}")
            elif not source.get("sha256"):
                bad.append(f"Unverified original: {source['relpath']}; explicitly relink to establish identity")
        except (OSError, ValueError) as exc:
            bad.append(str(exc))
    return bad


def recorded_source_hashes(folder):
    """Prefer scientific provenance already written by the measurement engine."""
    recorded = {}
    candidates = list(Path(folder).glob("*.csv"))
    for sub in ("best", "chosen", "review_measurements"):
        candidates.extend((Path(folder) / sub).glob("*.csv"))
    for path in candidates:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                original, digest = row.get("source_path"), str(row.get("source_sha256") or "").lower()
                if not original or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                    continue
                key = str(Path(original).resolve())
                if key in recorded and recorded[key] != digest:
                    raise ValueError(f"Conflicting recorded original-image hashes: {original}")
                recorded[key] = digest
    return recorded


def pin(controller, *, new_identity=False):
    """Freeze config/layout/flags before writing owned review/derived data."""
    from . import datareview, discovery
    from .rebuild import RebuildError, pipeline_candidate
    run = controller.run
    old = read(run.results_dir)
    if old and not new_identity:
        return old
    cfg = deepcopy(controller.recorded_config if controller.cfg else {})
    for key in list(cfg):
        if key.startswith("_run_"):
            cfg.pop(key)
    root = controller.capture_root
    raw_path = run.results_dir / "experiment.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    recorded_root = raw.get("photo_root")
    unresolved = deepcopy(old.get("unresolved_source_assignments", []))
    snapshot_warnings = deepcopy(old.get("snapshot_warnings", []))
    # Legacy capture trees get exact assignments rather than a future rescan.
    if "resolved_photos" not in cfg and root and cfg:
        records = {}
        for cand in run.candidates:
            try:
                found = pipeline_candidate(root, cand, cfg)
            except RebuildError as exc:
                unresolved.append({"candidate": list(cand.key), "reason": str(exc)})
                continue
            for shot in found.get("plates", ()):
                rel = Path(shot.path).resolve().relative_to(Path(root).resolve()).as_posix()
                records[(cand.medium, cand.timepoint, shot.plate, rel)] = dict(
                    relpath=rel, condition=cand.medium, condition_label=cand.medium_label,
                    timepoint=cand.hours, timepoint_label=cand.timepoint, plate=shot.plate)
        cfg["resolved_photos"] = list(records.values())
    flags = datareview.load_flags(run)
    raw["data_review"] = {**(raw.get("data_review") or {}), "file": "",
                          "flags": flags.to_dict() if flags is not None else None}
    # Resolve legacy template references once, then retain only owned geometry.
    if raw.get("experiment") and "mode" in raw["experiment"]:
        from experiments import schema, layout
        experiment = schema.from_dict(raw["experiment"])
        layout.pin(experiment)
        if experiment.template_snapshot is not None and not raw["experiment"].get("template", {}).get("snapshot"):
            raw["experiment"].setdefault("template", {})["snapshot"] = deepcopy(experiment.template_snapshot)
    template = (raw.get("experiment") or {}).get("template")
    if isinstance(template, dict) and not template.get("snapshot") and template.get("path"):
        # A lost legacy template cannot become a live layout later merely
        # because somebody creates a different file at the old path.
        template["source_path"] = template["path"]
        template["path"] = ""
        warning = "Original plate layout unavailable; recorded pipeline geometry and result artifacts are retained"
        if warning not in snapshot_warnings:
            snapshot_warnings.append(warning)
    raw["pipeline_config"] = cfg
    raw.setdefault("strains", cfg.get("strains", []))
    raw["photo_root"] = str(root or raw.get("photo_root") or "")
    sources = deepcopy(old.get("sources", []))
    if not sources:
        historical_hashes = recorded_source_hashes(run.results_dir)
        for rel in sorted({r["relpath"] for r in cfg.get("resolved_photos", [])}):
            path = source_path(root or raw.get("photo_root") or Path("."), rel)
            exists = root is not None and path.is_file()
            actual_digest = sha256(path) if exists else None
            digest = historical_hashes.get(str(path))
            if digest is None and recorded_root:
                digest = historical_hashes.get(str(source_path(recorded_root, rel)))
            digest = digest or actual_digest
            sources.append({"id": "source:" + str(uuid.uuid4()),
                            "relpath": rel, "original_path": str(path), "sha256": digest,
                            "size": path.stat().st_size if exists and actual_digest == digest else None})
    now = datetime.now(timezone.utc).isoformat()
    manifest = {**old, "kind": "spotting_result_run", "version": 1,
                "id": str(uuid.uuid4()), "created": now,
                "name": run.results_dir.name, "sources": sources,
                "unresolved_source_assignments": unresolved,
                "snapshot_warnings": snapshot_warnings,
                "scientific_label": old.get("scientific_label") or
                    (run.experiment.name if run.experiment and run.experiment.name else run.label),
                "parent_id": old.get("id"), "original_photo_root": raw["photo_root"],
                "measurement_archive": {"version": 1, "folder": "measurements"}}
    atomic_json(raw_path, raw)
    atomic_json(run.results_dir / MANIFEST, manifest)
    run.experiment = discovery.load_run_info(run.results_dir)
    run.manifest = manifest
    run.label = f"{manifest['name']} [{manifest['id'][:8]}]"
    return manifest


def copy_run(controller, destination):
    """Stage a detached fork. Switch controller only after successful promotion."""
    from . import discovery
    from .gui.controller import ReviewController
    src, dst = controller.run.results_dir.resolve(), Path(destination).resolve()
    if dst == src or dst.is_relative_to(src) or src.is_relative_to(dst):
        raise ValueError("Save Results As requires a separate folder outside the current run.")
    if dst.exists():
        raise ValueError("Choose a new result folder; existing folders are never overwritten.")
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in src.rglob("*")):
        raise ValueError("Result folders containing links cannot be copied safely.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=dst.parent, prefix=".result-copy-"))
    try:
        shutil.copytree(src, stage, dirs_exist_ok=True, copy_function=shutil.copy2)
        run = discovery.load_set(stage)
        # The staging folder is transport, never scientific experiment identity.
        run.label = controller.run.label
        fork = ReviewController(run, deepcopy(controller.review), rebuilder=controller.rebuilder)
        fork.capture_root = controller.capture_root
        fork.cfg = deepcopy(controller.cfg)
        fork.recorded_config = deepcopy(getattr(controller, "recorded_config", {}))
        manifest = pin(fork, new_identity=True)
        manifest["name"] = dst.name
        atomic_json(stage / MANIFEST, manifest)
        fork._load_cfg()
        fork.save()
        stage.rename(dst)
    finally:
        if stage.exists():
            if stage.resolve().parent != dst.parent or not stage.name.startswith(".result-copy-"):
                raise OSError("Copy staging path changed unexpectedly; automatic cleanup refused")
            shutil.rmtree(stage)
    controller.run = discovery.load_set(dst)
    controller._load_cfg()
    controller._frames.clear()
    controller._errors.clear()
    controller._drop_previews()
    controller._data_flags = None
    controller._flags_stamp = "unread"
    controller.undo_stack.clear()
    controller._saved = controller.review.to_dict()
    controller._notify()
    return dst
