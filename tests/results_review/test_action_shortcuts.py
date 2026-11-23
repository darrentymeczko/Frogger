"""Task 10: real Tk events in both hosts, using generated data only."""
from copy import deepcopy
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

import pytest

from test_saved_runs import generated, reopen, tree_hashes


def wait(window, predicate, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "native Tk state did not become ready"


def key(window, widget, sequence):
    widget.focus_force()
    window.update()
    assert window.focus_get() is widget
    widget.event_generate(sequence)
    window.update()


def type_name(window, editor, name, sequence="<Return>"):
    editor.entry.delete(0, "end")
    editor.entry.insert(0, name)
    key(window, editor.entry, sequence)
    assert editor.closed


@pytest.fixture(params=["standalone", "workbench"])
def host(request, tk_root, tmp_path, monkeypatch):
    from results_review import links
    from workbench.shell import Shell
    from workbench.settings import Settings
    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "SEARCH_HINTS", [])
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
    monkeypatch.setattr("experiments.app.default_experiment_dir", lambda: tmp_path)
    monkeypatch.setattr("plate_template.app.default_template_dir", lambda: tmp_path)
    monkeypatch.setattr("tkinter.messagebox.askyesnocancel", lambda *a, **k: False)
    win = tk.Toplevel(tk_root)
    win.geometry("1300x900")
    shell = Shell(win, Settings(tmp_path / "settings.json"), restore=False) if request.param == "workbench" else None
    apps = []
    def build(kind, payload=None, path=None):
        if shell:
            doc = shell.open_document(kind, path, payload)
            assert doc is not None
            app = doc.app
        else:
            if kind == "experiment":
                from experiments.app import ExperimentApp
                app = ExperimentApp(win, payload)
            elif kind == "plate":
                from plate_template.app import DesignerApp
                app = DesignerApp(win, payload)
            else:
                from results_review.app import ReviewApp
                from results_review.discovery import load_set
                app = ReviewApp(win, load_set(path))
        apps.append(app)
        win.deiconify()
        win.update()
        return app
    yield win, shell, build
    if shell:
        shell.exit(force=True)
    else:
        for app in apps:
            dispose = getattr(app, "_dispose", None)
            if dispose:
                dispose()
        win.destroy()


def test_condition_inline_identity_undo_save_and_guards(host, tmp_path, monkeypatch):
    from experiments.model import Experiment, Condition
    from experiments.schema import load
    win, shell, build = host
    experiment = Experiment(name="Original", conditions=[Condition("A", "Alpha"), Condition("B", "Beta")],
        overrides={"photo.png": {"condition": "A", "plate": 1}},
        picks={"A": {"1": "photo.png"}})
    app = build("experiment", experiment)
    app.notebook.select(app.conditions)
    tree = app.conditions.tree
    win.update()
    tree.selection_set("A")
    before = app.controller.snapshot()
    assignments = deepcopy(experiment.overrides)
    chosen = deepcopy(experiment.picks)
    calls = []
    original = app.controller.set_condition_name
    monkeypatch.setattr(app.controller, "set_condition_name", lambda *a: calls.append(a) or original(*a))
    key(win, tree, "<F2>")
    editor = app.conditions._inline
    assert editor.entry.get() == "Alpha"
    type_name(win, editor, "Cancelled", "<Escape>")
    assert app.controller.snapshot() == before and not calls
    key(win, tree, "<F2>")
    type_name(win, app.conditions._inline, "Renamed")
    assert calls == [("A", "Renamed")]
    assert app.controller.experiment.condition_codes() == ["A", "B"]
    assert app.controller.experiment.overrides == assignments
    assert app.controller.experiment.picks == chosen
    assert tree.selection() == ("A",) and tree.focus() == "A"
    key(win, tree, "<Control-z>")
    assert app.controller.experiment.condition("A").display() == "Alpha"
    key(win, tree, "<Control-y>")
    assert app.controller.experiment.condition("A").display() == "Renamed"
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: errors.append(a))
    stable = app.controller.snapshot()
    key(win, tree, "<F2>")
    type_name(win, app.conditions._inline, " beta ")
    assert errors and app.controller.snapshot() == stable
    # A resolver failure rolls the label and aliases back before history/save.
    resolve = app.controller.reresolve
    def fail_resolve():
        raise ValueError("simulated resolver failure")
    monkeypatch.setattr(app.controller, "reresolve", fail_resolve)
    key(win, tree, "<F2>")
    type_name(win, app.conditions._inline, "Failed name")
    assert app.controller.snapshot() == stable
    monkeypatch.setattr(app.controller, "reresolve", resolve)
    entry = ttk.Entry(app.conditions)
    entry.place(x=0, y=0)
    key(win, entry, "<F2>")
    assert app.conditions._inline.closed
    # Save As works even while typing and dispatches only once.
    target = tmp_path / "renamed.spotexp.json"
    dialogs = []
    monkeypatch.setattr("experiments.app.filedialog.asksaveasfilename", lambda **k: dialogs.append(k) or str(target))
    key(win, entry, "<Control-Shift-S>")
    assert len(dialogs) == 1 and app.path == target
    saved = load(target)
    assert saved.id == experiment.id and saved.condition("A").display() == "Renamed"
    assert saved.overrides == experiment.overrides and saved.picks == experiment.picks
    entry.destroy()
    from experiments.gui.layout import LayoutEditor
    from plate_template.presets import lab_standard_8x6
    from plate_template.schema import load as load_template
    accepted = []
    draft = LayoutEditor(win, lab_standard_8x6(), accepted.append)
    reusable = tmp_path / "reusable.json"
    monkeypatch.setattr("experiments.gui.layout.filedialog.asksaveasfilename", lambda **k: str(reusable))
    key(draft.root, draft.notebook, "<Control-Shift-S>")
    assert load_template(reusable).id == draft.template.id
    assert draft.root.winfo_exists() and not accepted
    draft.root.destroy()
    # Elsewhere F2 is the same experiment rename action as the header button.
    app.notebook.select(app.data)
    monkeypatch.setattr("experiments.app.simpledialog.askstring", lambda *a, **k: "New experiment")
    key(win, app.notebook, "<F2>")
    assert app.controller.experiment.name == "New experiment"
    key(win, app.notebook, "<Control-z>")
    assert app.controller.experiment.name == "Original"
    key(win, app.notebook, "<Control-y>")
    key(win, app.notebook, "<Control-s>")
    assert load(target).name == "New experiment"


def test_plate_actions_native_equivalence_and_history(host, tmp_path, monkeypatch):
    from plate_template.presets import lab_standard_8x6
    from plate_template.schema import load
    win, shell, build = host
    app = build("plate", lab_standard_8x6())
    canvas = app.canvases[app.current_plate_id()] if hasattr(app, "canvases") else app.notebook
    before = app.controller.snapshot()
    for name, letter in [("first_slot", "s"), ("replicate", "r"), ("dilution", "d")]:
        variable = getattr(app.context, name)
        spin = app.context.spins[str(variable)]
        variable.set(1)
        spin.event_generate("<<Increment>>")
        expected = variable.get()
        variable.set(1)
        key(win, canvas, f"<Control-Alt-{letter}>")
        assert variable.get() == expected == 2
    assert app.controller.snapshot() == before
    key(win, canvas, "<Control-Alt-Key-3>")
    assert app.tool.get() == "empty"
    entry = ttk.Entry(app.host.frame)
    entry.place(x=0, y=0)
    state = app.context.replicate.get()
    key(win, entry, "<Control-Alt-r>")
    assert app.context.replicate.get() == state
    count = len(app.template.plates)
    buttons = [w for w in app.toolbar.winfo_children() if isinstance(w, ttk.Button)]
    buttons[3].invoke()  # Add plate toolbar button.
    button_result = app.controller.snapshot()
    app.undo()
    canvas = app.canvases[app.current_plate_id()]
    key(win, canvas, "<Control-Alt-n>")
    assert len(app.template.plates) == count + 1
    assert app.controller.snapshot() == button_result
    canvas = app.canvases[app.current_plate_id()]
    assert win.focus_get() is canvas
    key(win, canvas, "<Control-z>")
    assert app.controller.snapshot() == before
    canvas = app.canvases[app.current_plate_id()]
    key(win, canvas, "<Control-y>")
    assert len(app.template.plates) == count + 1
    monkeypatch.setattr("plate_template.app.simpledialog.askstring", lambda *a, **k: "New plate")
    plate_id = app.current_plate_id()
    canvas = app.canvases[plate_id]
    key(win, canvas, "<F2>")
    assert app.template.plate(plate_id).label == "New plate"
    target = tmp_path / "design.json"
    dialogs = []
    monkeypatch.setattr("plate_template.app.filedialog.asksaveasfilename", lambda **k: dialogs.append(k) or str(target))
    key(win, entry, "<Control-Shift-S>")
    assert len(dialogs) == 1 and load(target).plate(plate_id).label == "New plate"
    previous = app.path
    monkeypatch.setattr("plate_template.app.filedialog.asksaveasfilename", lambda **k: "")
    key(win, entry, "<Control-Shift-S>")
    assert app.path == previous
    monkeypatch.setattr("plate_template.app.filedialog.asksaveasfilename", lambda **k: str(tmp_path / "failed.json"))
    monkeypatch.setattr("plate_template.app.save", lambda *a: (_ for _ in ()).throw(OSError("simulated write failure")))
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: errors.append(a))
    key(win, entry, "<Control-Shift-S>")
    assert errors and app.path == previous and not (tmp_path / "failed.json").exists()


def test_historical_photo_alias_conflict_is_atomic():
    from experiments.gui.controller import ExperimentController
    from experiments.model import Experiment, Condition
    experiment = Experiment(conditions=[Condition("A", "Alpha"), Condition("B", "Beta")])
    ctl = ExperimentController(experiment)
    ctl.set_condition_name("B", "Gamma")
    before = ctl.snapshot()
    with pytest.raises(ValueError, match="photo alias"):
        ctl.set_condition_name("A", "Beta")
    assert ctl.snapshot() == before
    assert ctl.experiment.profile.aliases["beta"] == "B"


def test_results_native_rename_roundtrip_exports_and_save_as(host, generated, monkeypatch):
    from results_review import export
    win, shell, build = host
    g = generated
    # The measurement fixture intentionally requests no figures. Supply actual
    # generated photo previews at the production candidate paths for native I/O.
    from PIL import Image
    for candidate in g.ctl.run.candidates:
        path = g.ctl.run.sheet(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(g.root / candidate.plate1) as image:
            image.thumbnail((600, 400))
            image.save(path)
    g.ctl.save()
    app = build("review", path=g.folder)
    wait(win, lambda: app.sheet._image is not None and len(app.table.tree.get_children()) > 0)
    scientific = [c.key for c in app.ctl.run.candidates]
    sources = deepcopy(app.ctl.run.manifest["sources"])
    picks = deepcopy(app.ctl.review.picks)
    archives = tree_hashes(g.folder / "measurements")
    stable = app.ctl.snapshot()
    key(win, app.candidates.tree, "<F2>")
    type_name(win, app._name_edit, "Cancelled", "<Escape>")
    assert app.ctl.snapshot() == stable
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: errors.append(a))
    key(win, app.candidates.tree, "<F2>")
    type_name(win, app._name_edit, "B")
    assert errors and app.ctl.snapshot() == stable
    key(win, app.candidates.tree, "<F2>")
    type_name(win, app._name_edit, "New treatment")
    assert app.ctl.medium_label(app.ctl.medium) == "New treatment"
    assert "New treatment" in app.sheet.caption.cget("text")
    key(win, app.candidates.tree, "<Control-z>")
    assert app.ctl.medium_label(app.ctl.medium) == "A"
    assert "New treatment" not in app.sheet.caption.cget("text")
    key(win, app.candidates.tree, "<Control-y>")
    assert "New treatment" in app.sheet.caption.cget("text")
    key(win, app.header.set_label, "<F2>")
    type_name(win, app._name_edit, "New result")
    assert app.header.set_label.cget("text") == "New result"
    key(win, app.candidates.tree, "<Control-z>")
    assert app.header.set_label.cget("text") != "New result"
    key(win, app.candidates.tree, "<Control-y>")
    assert app.header.set_label.cget("text") == "New result"
    key(win, app.candidates.tree, "<Control-s>")
    reopened = reopen(g.folder)
    assert reopened.display_label == "New result"
    assert reopened.medium_label(app.ctl.medium) == "New treatment"
    frames = export.named_frames(reopened.run, [reopened.frame(app.ctl.medium)], reopened.review)
    assert frames[0].experiment.startswith("New result New treatment")
    assert frames[0].tidy.analysis_condition.eq(app.ctl.medium).all()
    import pandas as pd
    csv = export.write_csv(frames, g.tmp / "renamed-export")
    assert pd.read_csv(csv).experiment.str.startswith("New result New treatment").all()
    assert [c.key for c in reopened.run.candidates] == scientific
    assert reopened.review.picks == picks and reopened.run.manifest["sources"] == sources
    monkeypatch.setattr("results_review.app._ask_folder", lambda *a, **k: g.folder)
    key(win, app.candidates.tree, "<Control-o>")
    wait(win, lambda: app.sheet._image is not None and len(app.table.tree.get_children()) > 0)
    assert app.header.set_label.cget("text") == "New result"
    assert "New treatment" in app.sheet.caption.cget("text")
    target = g.folder.parent / "new copy"
    dialogs = []
    monkeypatch.setattr("results_review.app.filedialog.asksaveasfilename", lambda **k: dialogs.append(k) or str(target))
    frozen = tree_hashes(g.folder)
    key(win, app.alpha_entry, "<Control-Shift-S>")
    wait(win, lambda: app.sheet._image is not None and len(app.table.tree.get_children()) > 0)
    assert len(dialogs) == 1 and app.ctl.run.results_dir == target
    assert reopen(target).display_label == "New result"
    assert tree_hashes(g.folder) == frozen and tree_hashes(target / "measurements") == archives
    assert tree_hashes(g.root) == g.hashes
    # Conflicting and cancelled Save As never switch identity or mutate files.
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: errors.append(a))
    unchanged = tree_hashes(target)
    key(win, app.candidates.tree, "<Control-Shift-S>")
    assert errors and app.ctl.run.results_dir == target and tree_hashes(target) == unchanged
    monkeypatch.setattr("results_review.app.filedialog.asksaveasfilename", lambda **k: "")
    key(win, app.candidates.tree, "<Control-Shift-S>")
    assert tree_hashes(target) == unchanged
