"""Version 1 display-only extension shared by the figure and montage editors.

See docs/result-runs.md for coordinates, identity and forward compatibility.
Never pass this object to a measurement function.
"""
from copy import deepcopy


def defaults():
    return {"version": 1, "sheet": {"view": "fit", "rotate": False,
            "brightness": 1.0, "contrast": 1.0}, "figure": {},
            "montage": {"crops": [], "order": [], "match_graph_order": False}}


def load(value):
    result = defaults()
    if isinstance(value, dict):
        result.update(deepcopy(value))
    return result


def sheet(value):
    """Future versions remain opaque; show neutral supported display settings."""
    result = load(value)
    return result.get("sheet", {}) if result.get("version") == 1 else defaults()["sheet"]


def result_label(run, review):
    return review.presentation.get("names", {}).get("result", run.label)


def medium_label(run, review, medium):
    return review.presentation.get("names", {}).get("conditions", {}).get(medium, run.medium_label(medium))
