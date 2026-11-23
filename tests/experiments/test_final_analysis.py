"""Final reference choices are persisted decisions, never inferred divisors."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tkinter as tk

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from experiments import analysis, schema, run
from experiments.model import Experiment, Condition, StrainGroup, Statistics
from experiments.gui.controller import ExperimentController


def select_condition(editor, label):
    if not editor.different_comparisons.get():
        editor.different_comparisons_button.invoke()
    editor.condition.set(label)
    editor._condition_changed()


def config(slot=1, refs=("WT",)):
    return analysis.validate({"normalization": {"*": slot}, "references": {"*": list(refs)}})


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_old_controls_migrate_with_group_version_and_condition_overrides(version):
    raw = {"schema_version": version, "mode": "timecourse", "strains": ["WT", "EV", "mut"],
           "control_slot": 1, "conditions": [{"code": "A"}, {"code": "B", "control_slot": 2}],
           "statistics": {"extra_references": ["mut"]},
           "strain_groups": {"G": {"strains": ["other", "manual"], "control_slot": 2,
                                    "conditions": [{"code": "B", "control_slot": 1}]}}}
    e = schema.from_dict(raw)
    assert analysis.normalization(e.final_analysis, "A") == 1
    assert analysis.normalization(e.final_analysis, "B") == 2
    assert analysis.references(e.final_analysis, "B") == ["EV", "mut"]
    g = e.for_group("G")
    assert analysis.normalization(g.final_analysis, "A") == 2
    assert analysis.normalization(g.final_analysis, "B") == 1
    assert schema.to_dict(schema.from_dict(schema.to_dict(e))) == schema.to_dict(e)


@pytest.mark.parametrize("explicit", [False, True])
def test_current_empty_final_configuration_roundtrips_exactly_and_undo(explicit):
    e = Experiment(strains=["WT", "mut"], conditions=[Condition("A")],
                   strain_groups={"G": StrainGroup(["a", "b"])})
    raw = schema.to_dict(e)
    if explicit:
        raw["final_analysis"] = {}
    loaded = schema.from_dict(raw)
    assert loaded.final_analysis == loaded.strain_groups["G"].final_analysis == {}
    ctl = ExperimentController(loaded)
    before = ctl.snapshot()
    ctl.set_strain(1, "changed")
    assert ctl.undo()
    assert ctl.snapshot() == before
    assert ctl.redo()
    assert ctl.panel_experiment.strain(1) == "changed"


def test_legacy_group_migration_preserves_shared_condition_inheritance():
    raw = {"schema_version": 4, "mode": "timecourse",
           "conditions": [{"code": "A", "control_slot": 2},
                          {"code": "B", "control_slot": 2}],
           "strain_groups": {"G": {"strains": ["parent", "vector"], "control_slot": 1,
                                    "conditions": [{"code": "B"}]}}}
    e = schema.from_dict(raw).for_group("G")
    assert e.control_for("A") == 2  # absent group override inherits shared condition
    assert e.control_for("B") == 1  # explicit null uses group default
    assert analysis.references(e.final_analysis, "A") == ["vector"]
    assert analysis.references(e.final_analysis, "B") == ["parent"]


def test_matching_is_transparent_editable_and_never_substring():
    names = ["WT", "WT BY", "EV-BY", "a_EV", "wt", "SWT", "DEV1", "WT2", "EVIL", "WT BY"]
    assert analysis.suggest(names) == ["WT", "WT BY", "EV-BY", "a_EV", "wt"]
    assert analysis.suggest(names, {"tokens": ["EV"], "case_sensitive": True}) == ["EV-BY", "a_EV"]
    assert analysis.suggest(["parent", "vector"], {"tokens": ["parent", "vector"]}) == ["parent", "vector"]
    proposed = analysis.pairs(names, ["WT", "WT BY", "WT", "missing"])
    assert all(a != b for a, b in proposed)
    assert len({frozenset(p) for p in proposed}) == len(proposed)
    assert not any("missing" in p for p in proposed)


@pytest.mark.parametrize("bad", [
    {"normalization": {"*": True}}, {"normalization": {"*": 0}},
    {"references": {"*": "WT"}}, {"references": {"*": [1]}},
    {"matching": {"tokens": ["WT.*"]}}, {"matching": {"case_sensitive": "no"}},
])
def test_invalid_final_schema_is_rejected(bad):
    with pytest.raises(ValueError):
        analysis.validate(bad)
    with pytest.raises(schema.ExperimentError, match="final_analysis"):
        schema.from_dict({"schema_version": 5, "mode": "timecourse", "final_analysis": bad})


@pytest.mark.parametrize("mode", ["timecourse", "quantify"])
def test_both_modes_refuse_missing_final_normalization_before_runner(mode, tmp_path, monkeypatch):
    e = Experiment(mode=mode, strains=["manual", "other"], conditions=[Condition("A")])
    calls = []
    monkeypatch.setattr(run, "_run_timecourse", lambda *a, **k: calls.append("measured"))
    monkeypatch.setattr(run, "_run_quantify", lambda *a, **k: calls.append("measured"))
    with pytest.raises(ValueError, match="normalization"):
        run.run(e, res=SimpleNamespace(), outdir=tmp_path)
    assert not calls
    ctl = ExperimentController(e)
    assert ctl.set_final_analysis(config(2, ["manual"]))
    assert run.to_pipeline_config(e)["media"]["A"]["control_col"] == 2
    run.run(e, res=SimpleNamespace(), outdir=tmp_path)
    assert calls == ["measured"]


def test_group_manual_choices_and_no_default_roundtrip_undo(tmp_path):
    e = Experiment(conditions=[Condition("A"), Condition("B")], strain_groups={
        "WT group": StrainGroup(["WT", "EV", "mut"]),
        "manual group": StrainGroup(["parent", "vector", "mut"])})
    ctl = ExperimentController(e)
    ctl.select_group("manual group")
    before = ctl.snapshot()
    final = config(None, ["parent"])
    final["normalization"].update(A=2, B=1)
    ctl.set_final_analysis(final)
    assert e.for_group("manual group").control_for("A") == 2
    assert e.for_group("WT group").control_for("A") is None
    after = ctl.snapshot()
    ctl.undo(); assert ctl.snapshot() == before
    ctl.redo(); assert ctl.snapshot() == after
    schema.save(ctl.experiment, tmp_path / "experiment.json")
    reopened = schema.load(tmp_path / "experiment.json")
    assert reopened.for_group("manual group").final_analysis == final
    assert not reopened.for_group("WT group").final_analysis


def test_native_final_editor_manual_references_without_pair_list(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT BY", "EV BY", "SWT", "DEV1"], ["A", "B"], config(1, []))
    editor.pack(); parent.update()
    editor.reference_checks["WT BY"].invoke()
    editor.reference_checks["EV BY"].invoke()
    chosen = editor.result()
    assert analysis.references(chosen, "A") == ["WT BY", "EV BY"]
    assert analysis.references(chosen, "B") == ["WT BY", "EV BY"]
    assert len(analysis.pairs(editor.names, analysis.references(chosen, "A"))) == 5
    assert not hasattr(editor, "preview")
    assert all(a != b for a, b in analysis.pairs(editor.names, analysis.references(chosen, "A")))
    select_condition(editor, "B")
    editor.normalization.set("2: EV BY"); editor._changed()
    for var in editor.reference_vars.values():
        var.set(False)
    editor.reference_checks["SWT"].invoke()
    chosen = editor.result()
    assert analysis.normalization(chosen, "A") == 1
    assert analysis.normalization(chosen, "B") == 2
    assert analysis.references(chosen, "B") == ["SWT"]
    editor.reference_checks["SWT"].invoke()
    editor.reference_checks["DEV1"].invoke()
    assert analysis.references(editor.result(), "B") == ["DEV1"]
    parent.destroy()


def test_review_app_apply_invalidates_frames_graphs_and_reopens(tk_root, tmp_path, monkeypatch):
    from results_review import background, links, discovery, review as rv
    from results_review.app import ReviewApp
    from results_review.model import Candidate
    from experiments.gui import analysis as gui
    monkeypatch.setattr(background, "warm", lambda: None)
    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "resolve", lambda *a, **k: tmp_path)
    monkeypatch.setattr(discovery, "TIMECOURSE_RESULTS", tmp_path)
    calls = []
    monkeypatch.setattr(ReviewApp, "_rebuild_current", lambda self, redraw=False: calls.append(redraw))
    cand = Candidate("A", "A", "24 Hours", 24, "a.png", "b.png", "least")
    info = discovery.RunInfo(pipeline_config={"strains": ["WT", "EV", "mut"], "control_col": 1})
    recorded = deepcopy(info.pipeline_config)
    result = discovery.SetRun(tmp_path, "test", [cand], experiment=info)
    parent = tk.Toplevel(tk_root)
    app = ReviewApp(parent, result); parent.update()
    monkeypatch.setattr(gui, "ask_analysis", lambda *a: config(2, ["mut"]))
    app.ctl._frames["A"] = object()
    app.ctl._preview_frames[cand.key] = object()
    app.btn_comparisons.invoke()
    assert calls[-1] is True
    assert not app.ctl._frames and not app.ctl._preview_frames
    assert "A" in app._stale_media and not app._redrawn_sheets
    assert app.ctl.cfg["control_col"] == 2
    assert info.pipeline_config == recorded
    app.ctl.save()
    assert rv.load_for(result).final_analysis == config(2, ["mut"])
    app._undo(); assert app.ctl.cfg["control_col"] == 1
    app._redo(); assert app.ctl.cfg["control_col"] == 2
    app._dispose(); parent.destroy()


def test_condition_choices_and_all_pairs_edits_preserve_other_conditions(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV", "mut"], ["A", "B"], config())
    select_condition(editor, "A")
    editor.reference_checks["WT"].invoke()
    editor.reference_checks["EV"].invoke()
    editor.all_pairs.set(True); editor._changed()
    assert editor.config["normalization"] == {"*": 1}
    assert tuple(editor.condition_picker.cget("values")) == ("A", "B")
    select_condition(editor, "B")
    editor.normalization.set("2: EV"); editor._changed(field="normalization")
    assert analysis.normalization(editor.result(), "A") == 1
    assert analysis.normalization(editor.result(), "B") == 2
    assert analysis.references(editor.result(), "A") == ["EV"]
    assert analysis.references(editor.result(), "B") == ["WT"]
    parent.destroy()


def test_normalization_event_does_not_freeze_inherited_reference_order(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV", "mut"], ["A"], config(refs=["EV", "WT"]))
    select_condition(editor, "A")
    editor.normalization.set("2: EV")
    editor.normalization_picker.event_generate("<<ComboboxSelected>>")
    parent.update()
    assert editor.config["normalization"] == {"*": 1, "A": 2}
    assert editor.config["references"] == {"*": ["EV", "WT"]}
    parent.destroy()


def test_reference_checkboxes_and_dialog_scroll_reach_all_choices(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    parent.geometry("520x360")
    names = ["WT", *[f"mutant {i}" for i in range(30)]]
    editor = AnalysisEditor(parent, names, ["A", "B"], config())
    editor.pack(fill="both", expand=True)
    parent.update()
    assert tuple(editor.condition_picker.cget("values")) == ("A", "B")
    assert len(editor.reference_checks) == len(names)
    editor.reference_checks[names[-1]].invoke()
    assert analysis.references(editor.result(), "A") == ["WT", names[-1]]
    assert analysis.references(editor.result(), "B") == ["WT", names[-1]]
    assert editor.canvas.yview()[1] < 1
    editor.canvas.yview_moveto(1)
    parent.update()
    assert editor.canvas.yview()[1] == 1
    note_y = editor.note.winfo_rooty() - editor.canvas.winfo_rooty()
    assert 0 <= note_y < editor.canvas.winfo_height()
    parent.destroy()


def test_wt_ev_defaults_are_exact_and_saved_unchecks_survive_reopening(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    names = ["WT", "EV", "SWT", "DEV1", "WT BY", "EV-BY", "mut"]
    e = Experiment(strains=names, control_slot=1, conditions=[Condition("A"), Condition("B")])
    initial = analysis.for_experiment(e, use_defaults=True)
    editor = AnalysisEditor(parent, names, ["A", "B"], initial)
    assert [name for name, var in editor.reference_vars.items() if var.get()] == ["WT", "EV"]
    assert analysis.references(editor.result(), "B") == ["WT", "EV"]
    editor.reference_checks["WT"].invoke()
    editor.reference_checks["EV"].invoke()
    saved = editor.result()
    e.final_analysis = saved
    reopened = AnalysisEditor(parent, names, ["A", "B"], analysis.for_experiment(e, use_defaults=True))
    assert not any(var.get() for var in reopened.reference_vars.values())
    select_condition(reopened, "B")
    assert not any(var.get() for var in reopened.reference_vars.values())
    assert not hasattr(editor, "tokens") and not hasattr(editor, "suggest_button")
    parent.destroy()


def test_empty_editor_starts_with_wt_ev_references_without_selecting_normalization(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["wt", "EV", "mut"], ["A"], {})
    assert analysis.references(editor.config, "A") == ["wt", "EV"]
    assert analysis.normalization(editor.config, "A") is None
    parent.destroy()


def test_condition_picker_shows_display_names_and_saves_under_correct_codes(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV", "mut"], ["GLU", "K-OAc"], config(),
                            condition_labels={"GLU": "Glucose", "K-OAc": "Potassium Acetate"})
    assert tuple(editor.condition_picker.cget("values")) == ("Glucose", "Potassium Acetate")
    assert editor.condition.get() == ""
    select_condition(editor, "Potassium Acetate")
    editor.normalization.set("2: EV"); editor._changed(field="normalization")
    editor.reference_checks["EV"].invoke()
    chosen = editor.result()
    assert analysis.normalization(chosen, "GLU") == 1
    assert analysis.normalization(chosen, "K-OAc") == 2
    assert analysis.references(chosen, "K-OAc") == ["WT", "EV"]
    assert "Potassium Acetate" not in chosen["normalization"]
    parent.destroy()


def test_duplicate_condition_names_can_still_be_selected_independently(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV"], ["A", "B"], config(),
                            condition_labels={"A": "Treatment", "B": "Treatment"})
    assert tuple(editor.condition_picker.cget("values")) == ("Treatment", "Treatment (2)")
    select_condition(editor, "Treatment (2)")
    assert editor.current == "B"
    parent.destroy()


def test_shared_comparisons_with_explicit_treatment_override_and_reset(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV", "mut"], ["A", "B", "C"], config())
    editor.reference_checks["EV"].invoke()
    editor.reference_checks["mut"].invoke()
    assert editor.config["references"] == {"*": ["WT", "EV", "mut"]}
    select_condition(editor, "B")
    assert "B" not in editor.config["references"]
    editor.reference_checks["WT"].invoke()
    editor.reference_checks["mut"].invoke()
    assert editor.config["references"] == {"*": ["WT", "EV", "mut"], "B": ["EV"]}
    saved = editor.result()
    assert analysis.references(saved, "A") == analysis.references(saved, "C") == ["WT", "EV", "mut"]
    assert analysis.references(saved, "B") == ["EV"]
    reopened = AnalysisEditor(parent, editor.strains, editor.conditions, saved)
    select_condition(reopened, "B")
    assert reopened.config["references"]["B"] == ["EV"]
    assert reopened.reference_vars["EV"].get() and not reopened.reference_vars["WT"].get()
    reopened._inherit("references")
    assert "B" not in reopened.config["references"]
    assert analysis.references(reopened.result(), "B") == ["WT", "EV", "mut"]
    parent.destroy()


def test_treatment_dropdown_only_appears_after_enabling_different_comparisons(tk_root):
    from experiments.gui.analysis import AnalysisEditor
    parent = tk.Toplevel(tk_root)
    editor = AnalysisEditor(parent, ["WT", "EV", "mut"], ["A", "B"], config())
    editor.pack(fill="both", expand=True)
    parent.update()
    assert not editor.condition_picker.winfo_ismapped()
    assert not hasattr(editor, "custom_comparisons_check")
    editor.reference_checks["EV"].invoke()
    assert analysis.references(editor.result(), "A") == analysis.references(editor.result(), "B") == ["WT", "EV"]
    editor.different_comparisons_button.invoke()
    parent.update()
    assert editor.condition_picker.winfo_ismapped()
    select_condition(editor, "B")
    editor.reference_checks["WT"].invoke()
    assert analysis.references(editor.result(), "B") == ["EV"]
    saved = editor.result()
    reopened = AnalysisEditor(parent, editor.strains, editor.conditions, saved)
    reopened.pack()
    parent.update()
    assert reopened.different_comparisons.get()
    assert reopened.condition_picker.winfo_ismapped()
    editor.different_comparisons_button.invoke()
    parent.update()
    assert not editor.condition_picker.winfo_ismapped()
    assert editor.current == "*"
    assert analysis.references(editor.result(), "A") == analysis.references(editor.result(), "B") == ["WT", "EV"]
    parent.destroy()
