"""Owned plate layouts, with read-only fallback for older file references."""

from pathlib import Path

from plate_template.schema import from_dict, load, to_dict

from . import BUNDLE, REPO


def resolve(experiment, base: Path | None = None):
    """Return a fresh layout. An embedded snapshot always wins, even if broken.

    Do not fall back to an external file for a damaged snapshot: that would
    silently substitute different scientific geometry. Schema errors propagate.
    """
    if experiment.template_snapshot is not None:
        return from_dict(experiment.template_snapshot)
    raw = experiment.template_path
    if not raw:
        return None
    candidates = ([Path(base) / raw] if base is not None else [])
    candidates.extend((Path(raw), REPO / raw, BUNDLE / raw))
    for path in candidates:
        if path.is_file():
            return load(path)
    return None


def pin(experiment, base: Path | None = None):
    """Freeze a legacy reference on first successful read; never write its file."""
    template = resolve(experiment, base)
    if template is not None and experiment.template_snapshot is None:
        experiment.template_snapshot = to_dict(template)
    return template
