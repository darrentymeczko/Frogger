"""Real Tk/workbench flow and the experiment's ownership of layout geometry."""
from copy import deepcopy
import hashlib
import json
import tkinter as tk
from tkinter import ttk

import pytest
from PIL import Image, ImageDraw

from experiments import analysis, schema
from experiments.gui.controller import ExperimentController
from experiments.layout import resolve
from experiments.model import Experiment
from plate_template import presets, schema as layouts


@pytest.fixture
def shell(tk_root, tmp_path, monkeypatch):
    from workbench.shell import Shell
    from workbench.settings import Settings
    from results_review import links
    from experiments import app

    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "SEARCH_HINTS", [])
    monkeypatch.setattr(app, "default_experiment_dir", lambda: tmp_path / "designs")
    win = tk.Toplevel(tk_root)
    sh = Shell(win, Settings(tmp_path / "workbench.json"), restore=False)
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **kw: errors.append(a))
    win.geometry("1400x950")
    win.update()
    yield sh
    sh.exit(force=True)
    assert not errors, errors


def draw_layout(editor):
    """Real canvas gestures -> app planning -> EditorController -> refresh."""
    editor.root.update()
    canvas = next(iter(editor.canvases.values()))
    gesture_time = 1000

    def drag(start, stop):
        nonlocal gesture_time
        gesture_time += 2000  # Distinct gestures, never the double-click cell dialog.
        def center(cell):
            x0, y0, x1, y1 = canvas.cell_rect(*cell)
            return dict(x=round((x0 + x1) / 2), y=round((y0 + y1) / 2))
        canvas.event_generate("<ButtonPress-1>", time=gesture_time, **center(start))
        canvas.event_generate("<B1-Motion>", time=gesture_time + 100, **center(stop))
        canvas.event_generate("<ButtonRelease-1>", time=gesture_time + 200, **center(stop))
        editor.root.update()

    drag((0, 0), (0, 1))
    assert editor.template.sample_slots() == 2
    editor.tool.set("series")
    editor._on_tool_change()
    drag((0, 0), (0, 0))  # a click selects the row's block of sources
    drag((0, 0), (2, 0))
    assert editor.template.plate(canvas.plate_id).get(2, 1).placement.dilution == 2


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def choose_analysis(app):
    """Drive the final dialog through its actual modal Apply callback."""
    from experiments.gui.analysis import AnalysisEditor

    errors = []
    def edit():
        try:
            editor = next(w for w in widgets(app.root) if isinstance(w, AnalysisEditor))
            editor.normalization.set("1: WT")
            editor.normalization_picker.event_generate("<<ComboboxSelected>>")
            editor.refs.selection_clear(0, "end")
            editor.refs.selection_set(1)  # different comparison reference: EV
            editor.refs.event_generate("<<ListboxSelect>>")
            editor.tokens.set("WT, EV")
            next(w for w in widgets(editor.winfo_toplevel()) if isinstance(w, ttk.Button)
                 and w.cget("text") == "Apply and recalculate").invoke()
        except Exception as exc:
            errors.append(exc)
            for child in app.root.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()
    app.root.after(100, edit)
    app.analysis_button.invoke()
    assert not errors
    assert analysis.normalization(app.controller.experiment.final_analysis, "GLU") == 1
    assert analysis.references(app.controller.experiment.final_analysis, "GLU") == ["EV"]


@pytest.mark.parametrize("entry", ["experiment", "plate"])
def test_unsaved_layout_through_host_to_final_analysis_and_reopen(shell, tmp_path, monkeypatch, entry):
    """No template save/open dialog is permitted until final optional export."""
    from experiments import app as experiment_app
    from experiments.run import load_template, to_layout

    def forbidden(*args, **kwargs):
        pytest.fail("An intermediate template file dialog was requested")
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", forbidden)
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", forbidden)
    if entry == "experiment":
        doc = shell.open_document("experiment")
        app = doc.app
        app.panel.edit_layout_button.invoke()
        editor = app._layout_editor
    else:
        source = shell.open_document("plate")
        editor = source.app
    draw_layout(editor)
    owned = layouts.to_dict(editor.template)
    editor.use_layout_button.invoke()
    if entry == "plate":
        doc = shell.active
        assert doc.kind == "experiment" and doc is not source
        app = doc.app
    else:
        assert app._layout_editor is None and shell.active is doc
    assert app.controller.experiment.template_snapshot == owned
    assert doc.dirty  # Closing must protect the handed-off/applied drawing.
    assert app.controller.experiment.template_path == ""
    assert not list(tmp_path.rglob("*.json"))

    # Use the live strain entries and their production Return commit handlers.
    for slot, name in enumerate(("WT", "EV"), 1):
        field = app.panel._entries[slot]
        field.focus_force()
        shell.root.update()
        field.delete(0, "end")
        field.insert(0, name)
        field.event_generate("<Return>")
    assert app.controller.experiment.strains == ["WT", "EV"]
    app.mode.set("quantify")
    app._mode_changed()

    photos = tmp_path / "photos"
    photos.mkdir()
    photograph = photos / "generated-plate.png"
    image = Image.new("RGB", (480, 360), "#d0c6b0")
    draw = ImageDraw.Draw(image)
    for row in range(3):
        for col in range(2):
            x, y = 80 + col * 100, 65 + row * 90
            draw.ellipse((x - 20, y - 20, x + 20, y + 20), fill=(70 + row * 35,) * 3)
    image.save(photograph)
    source_hash = hashlib.sha256(photograph.read_bytes()).hexdigest()
    monkeypatch.setattr("tkinter.filedialog.askdirectory", lambda **kw: str(photos))
    app.choose_photos()
    assert len(app.controller.files) == 1
    # Add/assign the treatment through the existing conditions/intake actions.
    monkeypatch.setattr("experiments.gui.conditions._ask_text", lambda *a, **kw: "Glucose")
    app.conditions._add()
    code = app.controller.experiment.conditions[-1].code
    monkeypatch.setattr("experiments.gui.data._ask_text", lambda *a, **kw: "Glucose")
    app.data.table.selection_set(app.controller.files[0].relpath)
    app.data._correct("condition")
    monkeypatch.setattr("experiments.gui.data._ask_text", lambda *a, **kw: "1")
    app.data.table.selection_set(app.controller.files[0].relpath)
    app.data._correct("plate")
    app.notebook.select(app.plates)
    shell.root.update()
    iid = next(i for i in app.plates.slots.get_children()
               if i.startswith(code + "\x1f"))
    app.plates.slots.selection_set(iid)
    app.plates.slots.event_generate("<<TreeviewSelect>>")
    app.plates.candidates.selection_set(0)
    app.plates.candidates.event_generate("<<ListboxSelect>>")
    app.plates._assign()
    assert app.controller.experiment.pick(code, "1") == "generated-plate.png"
    app.controller.set_plate_dilution(code, "1", 0)
    app.notebook.select(app.run_tab)
    shell.root.update()
    assert "Analysis / comparisons" in app.notebook.tab(app.run_tab, "text")
    assert app.save_template_button.winfo_ismapped()
    choose_analysis(app)
    app.controller.set_statistics(p_adjust="holm", alpha=0.01)
    app.controller.set_photo_top("right")
    expected = deepcopy(app.controller.experiment.final_analysis)

    target = tmp_path / "experiment.spotexp.json"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kw: str(target))
    assert doc.commands["save"]()
    assert not doc.dirty
    assert layouts.to_dict(load_template(schema.load(target))) == owned
    before = schema.to_dict(app.controller.experiment)

    # Optional reusable export, followed by real standalone template edits/save.
    reusable = tmp_path / "reusable.json"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kw: str(reusable))
    app.save_template_button.invoke()
    assert reusable.is_file()
    assert schema.to_dict(app.controller.experiment) == before
    external = shell.open_document("plate", reusable)
    external.app.controller.resize(4, 4)
    assert external.commands["save"]()
    assert external.app.template.rows == 4
    assert app.controller.template.rows == owned["grid"]["rows"]
    if entry == "plate":
        source.app.controller.resize(5, 5)
        assert layouts.to_dict(load_template(app.controller.experiment)) == owned
    assert shell.close_document(doc)
    reopened = shell.open_document("experiment", target).app
    assert layouts.to_dict(reopened.controller.template) == owned
    e = reopened.controller.experiment
    assert e.final_analysis == expected
    assert e.statistics.p_adjust == "holm" and e.statistics.alpha == 0.01
    assert e.photo_top == "right" and e.pick(code, "1") == "generated-plate.png"
    assert to_layout(reopened.controller.template, e.photo_top) is not None
    from data_review.controller import DataReviewController
    from data_review.flags import sidecar_for
    from experiments.cli import _load_template
    review = DataReviewController(sidecar_for(target))
    review.load()
    assert not review.error and len(review.photos) == 1
    assert layouts.to_dict(review.template) == owned
    assert layouts.to_dict(_load_template(e, target.parent)) == owned
    assert hashlib.sha256(photograph.read_bytes()).hexdigest() == source_hash
    (tmp_path / "acceptance.json").write_text(json.dumps({
        "entry": entry, "template_saved_before_analysis": False,
        "layout": owned, "final_analysis": expected, "experiment": str(target),
        "reusable_template": str(reusable), "photo_sha256": source_hash,
        "save_reopen": True, "external_edits_isolated": True}, indent=2), encoding="utf-8")


def test_edit_apply_cancel_undo_and_group_science_are_independent(shell, monkeypatch):
    ctl = shell.open_document("experiment").app.controller
    ctl.use_layout(presets.lab_standard_8x6())
    ctl.set_strain(1, "WT")
    ctl.add_condition("GLU")
    ctl.add_strain_group("B", first_key="A")
    ctl.set_strain(1, "parent")
    ctl.set_final_analysis({"normalization": {"*": 1}, "references": {"*": ["parent"]}})
    # A persisted assignment whose source is now absent must survive layout edits.
    ctl.panel_experiment.set_pick("GLU", "1", "missing-for-test.png")
    app = shell.active.app
    before = ctl.snapshot()
    app.panel.edit_layout_button.invoke()
    draft = app._layout_editor
    draft.controller.resize(7, 8)
    assert ctl.snapshot() == before
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
    draft.host.close()
    assert app._layout_editor is None and ctl.snapshot() == before
    app.panel.edit_layout_button.invoke()
    draft = app._layout_editor
    draft.controller.resize(7, 8)
    draft.use_layout_button.invoke()
    after = ctl.snapshot()
    assert after != before and ctl.template.rows == 7
    assert after["strain_groups"] == before["strain_groups"]
    ctl.undo()
    assert ctl.snapshot() == before and ctl.template.rows == 6
    ctl.redo()
    assert ctl.snapshot() == after and ctl.template.rows == 7


def test_legacy_template_reference_pins_on_open_and_never_follows_file(tmp_path):
    template = presets.lab_standard_8x6()
    layouts.save(template, tmp_path / "source.json", bump_revision=False)
    path = tmp_path / "legacy.spotexp.json"
    raw = schema.to_dict(Experiment(template_path="source.json", template_id=template.id))
    raw["schema_version"] = 5
    path.write_text(json.dumps(raw), encoding="utf-8")
    e = schema.load(path)
    pinned = deepcopy(e.template_snapshot)
    template.resize(4, 4)
    layouts.save(template, tmp_path / "source.json")
    assert layouts.to_dict(resolve(e)) == pinned
    schema.save(e, tmp_path / "owned.spotexp.json")
    (tmp_path / "source.json").unlink()
    assert schema.load(tmp_path / "owned.spotexp.json").template_snapshot == pinned
    assert json.loads(path.read_text(encoding="utf-8")) == raw  # read-only migration


def test_snapshot_schema_has_no_aliases_and_rejects_corruption():
    ctl = ExperimentController(Experiment())
    source = presets.lab_standard_8x6()
    ctl.use_layout(source)
    expected = ctl.snapshot()
    source.resize(4, 4)
    ctl.template.resize(5, 5)
    assert ctl.snapshot() == expected
    raw = ctl.snapshot()
    restored = schema.from_dict(raw)
    raw["template"]["snapshot"]["grid"]["rows"] = 99
    assert restored.template_snapshot == expected["template"]["snapshot"]
    bad = deepcopy(expected)
    bad["template"]["snapshot"]["grid"]["rows"] = "bad"
    with pytest.raises(schema.ExperimentError, match="template.snapshot"):
        schema.from_dict(bad)


def test_bulk_setup_uses_current_embedded_layout_without_source_file(shell, tmp_path):
    from experiments.gui.bulk import BulkCreateDialog

    app = shell.open_document("experiment").app
    app.controller.use_layout(presets.lab_standard_8x6(), source_path="missing-template.json")
    app.controller.add_condition("GLU")
    folder = tmp_path / "generated-bulk-photos"
    folder.mkdir()
    Image.new("RGB", (60, 40), "gray").save(folder / "generated.png")
    expected = deepcopy(app.controller.experiment.template_snapshot)
    errors = []
    def create():
        dialog = next(w for w in shell.root.winfo_children() if isinstance(w, BulkCreateDialog))
        try:
            assert layouts.to_dict(dialog.template) == expected
            dialog.folders = [folder]
            dialog._refresh()
            dialog._cells[1, 1].set("WT")
            dialog._cells[2, 1].set("EV")
            dialog.create_button.invoke()
            assert len(dialog.created) == 1
        except Exception as exc:
            errors.append(exc)
            dialog.destroy()
    shell.root.after(100, create)
    app.bulk_create()
    assert not errors
    created = shell.active.app
    assert created is not app and created.path.is_file()
    assert created.controller.experiment.template_snapshot == expected
