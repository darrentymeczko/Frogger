"""Low growth and unavailable measurements are data, not discovery filters."""
from pathlib import Path
from types import SimpleNamespace
import math
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import numpy as np
import pandas as pd
import pytest
import spotting_quant as sq
import spotting_batch as sb
import spotting_timecourse as tc
from results_review.model import Candidate, Review
from results_review.rebuild import Frame, summarize
from results_review import discovery, review as rv
from results_review.gui.controller import ReviewController
from results_review.gui.export_dialog import ExportDialog


def table():
    return pd.DataFrame([dict(experiment="test", treatment="test", plate=p,
        replicate=f"rep{p}", strain_col=c, strain="WT" if c == 1 else "low",
        raw_growth=v, artifact=False, excluded=False, is_control=c == 1)
        for p in (1,2) for c,v in ((1,10.),(2,-.1))])


def test_missing_and_nonfinite_denominators_are_unavailable_not_zero():
    t = table()
    t.loc[(t.plate == 1) & (t.strain_col == 1), "artifact"] = True
    t.loc[(t.plate == 2) & (t.strain_col == 1), "raw_growth"] = np.inf
    got = sq.add_relative_growth(t, group_keys=["experiment"])
    assert not got.control_ok.any()
    assert got.relative_growth.isna().all()
    assert set(got.normalization_reason) == {"no_usable_control", "unsuitable_measurement_roi"}
    np.testing.assert_equal(got.raw_growth.to_numpy(), t.raw_growth.to_numpy())


def test_valid_negative_low_growth_is_preserved_and_not_an_artifact():
    t = table()
    got = sq.add_relative_growth(t, group_keys=["experiment"])
    low = got[got.strain_col == 2]
    np.testing.assert_allclose(low.relative_growth, -.01)
    assert low.control_ok.all() and not low.artifact.any()
    assert low.growth_quality.eq("low_growth").all()
    assert low.normalization_reason.eq("").all()


def test_failed_plate_does_not_discard_successful_raw_measurements(monkeypatch):
    shots = [tc.Shot(Path(f"p{p}.png"),24,"24 Hours","LOW","Low",p) for p in (1,2)]
    candidate = tc.candidates(shots)[0]
    def measure(path, plate, *a, **k):
        if plate == 2:
            raise ValueError("unreadable synthetic plate")
        return SimpleNamespace(ref=sb.PhotoRef(path,0,plate,"LOW"),
            net=np.full((6,8),.1), rim=np.zeros((6,8),bool), bg_samples=np.zeros(5))
    monkeypatch.setattr(tc,"_cached_measure",measure)
    level = sb.classic_layout().levels[0]
    scored = tc.score_candidate(candidate,level,Path("unused"),["WT","low"],1)
    assert scored is not None and math.isnan(scored["median_CV"])
    assert math.isnan(scored["n_significant"])
    assert "low_growth" in scored["quality_flags"]
    tidy = tc.build_tidy_for_candidate(candidate,level,dict(strains=["WT","low"],control_col=1),
                                       Path("unused"),"test")
    assert len(tidy) == 8
    np.testing.assert_allclose(tidy.loc[tidy.plate == 1,"raw_growth"],.1)
    assert tidy.loc[tidy.plate == 2,"raw_growth"].isna().all()
    assert tidy.loc[tidy.plate == 2,"roi_quality"].eq("measurement_failed").all()
    assert tidy.relative_growth.isna().all()


def test_unavailable_summary_has_zero_observations_and_null_statistics():
    t = sq.unavailable_growth(table(), "no_usable_control")
    t["outlier"] = False
    rows = summarize(Frame("LOW",None,"test",t,1))
    assert len(rows) == 2
    assert all(r["n"] == 0 and math.isnan(r["mean"]) and math.isnan(r["sd"])
               and r["p_value"] is None for r in rows)


def test_inclusion_survives_controller_save_undo_and_export_dialog(tmp_path, tk_root, monkeypatch):
    from results_review.gui import controller
    monkeypatch.setattr(controller.links,"resolve",lambda *a,**k:None)
    c = Candidate("LOW","Undergrown","24 Hours",24,"a.png","b.png","most",
                  quality_flags="low_growth;no_usable_control")
    run = discovery.SetRun(tmp_path,"retention",[c])
    ctl = ReviewController(run, Review())
    assert ctl.review.is_included("LOW")
    assert ctl.set_included("LOW",False)
    ctl.save()
    fresh = rv.load_for(run)
    assert not fresh.is_included("LOW")
    assert len(run.candidates) == 1 and rv.chosen_candidate(run,fresh,"LOW") == c
    ctl.undo(); assert ctl.review.is_included("LOW")
    ctl.redo(); assert not ctl.review.is_included("LOW")
    dialog = ExportDialog(tk_root,run,fresh)
    assert len(dialog.treatments.get(0,"end")) == 1
    assert not dialog.treatments.curselection()
    dialog.destroy()


def test_discovery_preserves_null_scores_and_quality(tmp_path):
    csv = tmp_path / "timecourse_candidates.csv"
    csv.write_text("medium,timepoint,dilution,plate1,plate2,n_significant,quality_flags\n"
                   "LOW,24 Hours,most,a.png,b.png,,low_growth;no_usable_control\n")
    run = discovery.load_set(tmp_path)
    assert run.media == ["LOW"]
    c = run.pipeline_best("LOW")
    assert math.isnan(c.n_significant) and math.isnan(c.best_set_score)
    assert "low growth" in c.quality_text


def test_review_app_keeps_excluded_low_condition_visible_and_can_reinclude(tmp_path, tk_root, monkeypatch):
    import tkinter as tk
    from results_review import background, links
    from results_review.app import ReviewApp
    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(background, "warm", lambda: None)
    monkeypatch.setattr(links, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(ReviewApp, "_rebuild_current", lambda *a, **k: None)
    c = Candidate("LOW", "Undergrown", "24 Hours", 24, "a.png", "b.png", "most",
                  quality_flags="low_growth;control_at_or_below_noise_floor")
    run = discovery.SetRun(tmp_path, "retention", [c])
    r = Review(); r.set_included("LOW", False); rv.save_for(run, r)
    parent = tk.Toplevel(tk_root)
    app = ReviewApp(parent, run); parent.update()
    assert not app._include_var.get()
    assert len(app.candidates.tree.get_children()) == 1
    assert "low growth" in app.pick_note.cget("text")
    assert "excluded from exports" in app.pick_note.cget("text")
    app.btn_include.invoke(); parent.update()
    assert app.ctl.review.is_included("LOW") and app.ctl.dirty
    app.ctl.save()
    assert rv.load_for(run).is_included("LOW")
    app._dispose(); parent.destroy()
