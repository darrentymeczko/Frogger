"""Native guide entry points, with isolated settings and generated photos only."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import json

from test_action_shortcuts import host

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "scratch/validation/task12"


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def invoke_label(menu, label):
    for i in range((menu.index("end") or 0) + 1):
        if menu.type(i) != "separator" and menu.entrycget(i, "label") == label:
            menu.invoke(i)
            return
    raise AssertionError(f"Missing menu item {label}")


def verify_view(win):
    from uikit.data_organization import TITLE, GUIDE
    win.update()
    viewers = [w for w in descendants(win) if isinstance(w, tk.Toplevel) and w.title() == TITLE]
    assert len(viewers) == 1
    viewer = viewers[0]
    assert viewer.winfo_viewable()
    text = next(w for w in descendants(viewer) if isinstance(w, tk.Text))
    assert text.get("1.0", "end-1c") == GUIDE.read_text(encoding="utf-8")
    assert str(text.cget("state")) == "disabled"
    text.yview_moveto(1)
    win.update()
    assert text.yview()[1] == 1.0
    next(w for w in descendants(viewer) if isinstance(w, ttk.Button) and w.cget("text") == "Close").invoke()
    win.update()
    assert not viewer.winfo_exists()


def test_native_guide_links_and_manual_rule_setup(host, tmp_path):
    from experiments.model import Experiment, Condition, TIMECOURSE
    from experiments import intake
    from experiments.names import HOURS, PLATE
    from PIL import Image
    win, shell, build = host
    checks = []
    if shell:
        invoke_label(shell.help_menu, "Photograph and folder organization")
        verify_view(win)
        checks.append("workbench Home Help")
    photo = tmp_path / "next day/Glucose/R1/IMG_9.JPG"
    photo.parent.mkdir(parents=True)
    Image.new("RGB", (64, 48), (60, 90, 120)).save(photo)
    e = Experiment(name="Help acceptance", mode=TIMECOURSE, photo_root=str(tmp_path),
                   conditions=[Condition("GLU", "Glucose")])
    app = build("experiment", e)
    app.controller.rescan(infer=True)
    app.notebook.select(app.data)
    app.refresh()
    win.update()
    button = next(w for w in descendants(app.data) if isinstance(w, ttk.Button) and w.cget("text") == "Photo organization guide")
    assert button.winfo_viewable()
    before = app.controller.snapshot()
    button.invoke()
    verify_view(win)
    checks.append("Data button")
    if shell:
        menu = shell._doc_menu(app.host, "Help")
    else:
        menubar = app.host._menubar
        menu = next(win.nametowidget(menubar.entrycget(i, "menu"))
                    for i in range(menubar.index("end") + 1)
                    if menubar.type(i) == "cascade" and menubar.entrycget(i, "label") == "Help")
    invoke_label(menu, "Photograph and folder organization")
    verify_view(win)
    checks.append("experiment Help")
    assert app.controller.snapshot() == before
    # Exercise the exact documented advanced-editor workaround through widgets.
    app.data._toggle_advanced()
    for facet, pattern in (("timepoint", HOURS), ("plate", PLATE)):
        source, depth, entry = app.data.rules[facet]
        source.set("Whole relative path")
        entry.set(pattern)
    app.data._apply()
    assert {"plate", "timepoint"} <= app.controller.experiment.profile.facets()
    path = "next day/Glucose/R1/IMG_9.JPG"
    app.controller.override_many([path], "plate", 1)
    app.controller.override_many([path], "timepoint", 48)
    row = next(r for r in app.controller.resolution.rows if r.relpath == path)
    assert (row.plate, row.timepoint, row.condition, row.status) == (1, 48, "GLU", "ok")
    checks.append("advanced whole-path rules plus manual overrides")
    label = "workbench" if shell else "standalone"
    (ARTIFACTS / f"native-{label}.json").write_text(json.dumps({"checks": checks, "row": row.__dict__}, indent=2), encoding="utf-8")


def test_guide_missing_file_reports_error(tk_root, monkeypatch, tmp_path):
    from uikit import data_organization
    errors = []
    monkeypatch.setattr(data_organization, "GUIDE", tmp_path / "missing.md")
    monkeypatch.setattr(data_organization.messagebox, "showerror", lambda *a, **k: errors.append(a))
    assert data_organization.show_data_organization(tk_root) is None
    assert len(errors) == 1 and "missing.md" in errors[0][1]
