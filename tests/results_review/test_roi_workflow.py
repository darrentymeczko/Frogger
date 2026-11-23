"""User-facing method changes are explicit, persisted, and undoable."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tkinter as tk
from tkinter import ttk
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"src"))
from results_review.model import Review, Candidate
from results_review.discovery import SetRun
from results_review.gui import controller, roi_dialog
from results_review import review as rv
from src.spotting_roi import settings_from_config, LEGACY_METHOD


def test_review_settings_require_explicit_change_and_survive_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.links,"resolve",lambda *a,**k:tmp_path)
    cfg={"strains":["WT","mut"],"control_col":1}
    info=SimpleNamespace(pipeline_config=cfg)
    run=SetRun(results_dir=tmp_path,label="synthetic",candidates=[],experiment=info)
    ctl=controller.ReviewController(run,Review())
    assert settings_from_config(ctl.cfg,legacy_if_missing=True).method==LEGACY_METHOD
    assert not ctl.review.measurement
    ctl._frames["old"]=object(); ctl._preview_frames[("old",)]=object()
    assert ctl.set_measurement(.23,49)
    assert not ctl._frames and not ctl._preview_frames
    assert "measurement" not in cfg, "Do not mutate original run configuration"
    ctl.save()
    saved=rv.load_for(run)
    fresh=controller.ReviewController(run,saved)
    assert fresh.cfg["measurement"]=={"method":"centered-interior-v1","inward_margin":.23,"min_pixels":49}
    ctl.undo()
    assert settings_from_config(ctl.cfg,legacy_if_missing=True).method==LEGACY_METHOD
    ctl.redo()
    assert ctl.cfg["measurement"]["inward_margin"]==.23


def test_settings_dialog_commits_only_recalculate_button(tk_root):
    parent=tk.Toplevel(tk_root); parent.deiconify(); parent.update()
    def interact():
        windows=[w for w in parent.winfo_children() if isinstance(w,tk.Toplevel)]
        if not windows: parent.after(10,interact); return
        body=windows[0].winfo_children()[0]
        spins=[w for w in body.winfo_children() if isinstance(w,ttk.Spinbox)]
        assert spins[0].get() == "15", "Legacy migration offers the new inward-margin default"
        spins[0].set("22"); spins[1].set("36")
        button=next(w for w in body.winfo_children() if isinstance(w,ttk.Button)
                    and str(w.cget("text")).startswith("Recalculate"))
        button.invoke()
    parent.after(20,interact)
    result=roi_dialog.ask_settings(parent,{})
    assert result.inward_margin==.22 and result.min_pixels==36
    parent.destroy()


def test_overlay_window_draws_boundary_and_exact_roi_on_source(tmp_path,tk_root):
    from PIL import Image
    from data_review.gui.plate import PlateView, BOUNDARY
    from results_review.rebuild import Frame
    from spotting_batch import PlateData, PhotoRef
    path=tmp_path/"source.png"; Image.new("L",(400,400),80).save(path)
    centers=np.array([[[200.25,199.75]]])
    p=PlateData(PhotoRef(path,1,1,"GLU"),np.array([[50.]]),np.array([[False]]),
        30.,0.,centers,30.,np.array([30.]*5),(),(200,200),180,
        {"settings":{"method":"centered-interior-v1","inward_margin":.2},
         "boundary_radii":np.full((1,1,72),40.),"quality":np.full((1,1),"")})
    parent=tk.Toplevel(tk_root); parent.deiconify()
    frame=Frame("GLU",None,"synthetic",pd.DataFrame(),1,plates=[p])
    roi_dialog.show_overlays(parent,frame); parent.update()
    win=next(w for w in parent.winfo_children() if isinstance(w,tk.Toplevel))
    canvas=next(w for w in win.winfo_children() if isinstance(w,PlateView))
    assert len([i for i in canvas.find_all() if canvas.type(i)=="line"
                and canvas.itemcget(i,"fill")==BOUNDARY])==72
    oval=next(i for i in canvas.find_withtag("overlay") if canvas.type(i)=="oval")
    x0,y0,x1,y1=canvas.coords(oval)
    scale,left,top,*_=canvas._fit
    assert abs((x1-x0)/scale-60)<1e-8
    assert abs((x1+x0)/2-left-199.75*scale)<1e-8
    assert abs((y1+y0)/2-top-200.25*scale)<1e-8
    win.destroy(); parent.destroy()


def test_app_reopen_marks_historical_graph_stale_and_action_updates_method(tmp_path,tk_root,monkeypatch):
    from results_review import background, links, discovery
    from results_review.app import ReviewApp
    from src.spotting_roi import ROISettings
    monkeypatch.setattr(background,"warm",lambda:None)
    monkeypatch.setattr(links,"LINKS_FILE",tmp_path/"links.json")
    monkeypatch.setattr(links,"resolve",lambda *a,**k:tmp_path)
    monkeypatch.setattr(discovery,"TIMECOURSE_RESULTS",tmp_path)
    calls=[]
    monkeypatch.setattr(ReviewApp,"_rebuild_current",lambda self,redraw=False:calls.append(redraw))
    cand=Candidate("GLU","Glucose","20 Hours",20,"a.png","b.png","least")
    info=discovery.RunInfo(pipeline_config={"strains":["WT","mut"],"control_col":1})
    run=SetRun(tmp_path,"ROI workflow",[cand],experiment=info)
    rv.save_for(run,Review(measurement=ROISettings(inward_margin=.23).to_dict()))
    parent=tk.Toplevel(tk_root)
    app=ReviewApp(parent,run); parent.update()
    assert "GLU" in app._stale_media
    assert "out of date" in app.pick_note.cget("text")
    assert "23%" in app.roi_method_label.cget("text")
    monkeypatch.setattr(roi_dialog,"ask_settings",lambda *a:ROISettings(inward_margin=.2))
    app.btn_roi.invoke()
    assert calls[-1] is True, "Explicit recalculation also requests a fresh graph"
    assert "20%" in app.roi_method_label.cget("text")
    app._undo()
    assert "23%" in app.roi_method_label.cget("text")
    app._dispose(); parent.destroy()


def test_rebuild_legacy_then_explicit_interior_save_reopen_and_display(tmp_path,monkeypatch):
    # Use real pipeline/background/normalisation/statistics and review rebuild;
    # fix only lattice positions, whose accuracy has separate real-photo checks.
    import spotting_quant as sq
    import spotting_timecourse as tc
    import spotting_batch as sb
    from results_review import rebuild
    from results_review.gui import imaging
    from PIL import Image
    from dataclasses import replace
    yy,xx=np.mgrid[:660,:850]
    centers=np.array([[(85.25+95*r,85.75+95*c) for c in range(8)] for r in range(6)])
    im=np.full(yy.shape,30,dtype=np.uint8)
    for r,c in np.ndindex(6,8):
        cy,cx=centers[r,c]; rad=np.hypot(yy+.5-cy,xx+.5-cx)
        im[rad<31]=80+r+3*c
        im[(rad>=27)&(rad<31)]=230
    for name in ("a.png","b.png"): Image.fromarray(im).save(tmp_path/name)
    grid=sq.Grid(centers,31,31,62,(330,425),600,plate_known=False)
    monkeypatch.setattr(sq,"detect_grid",lambda *a,**k:replace(grid))
    monkeypatch.setattr(sq,"background_gap_centers",lambda g:[(40,40),(40,140),(40,240),(40,340),(40,440)])
    cfg={"strains":["WT"]+[f"mut{i}" for i in range(7)],"control_col":1,
         "resolved_photos":[{"condition":"GLU","timepoint":20,"timepoint_label":"20 Hours",
                              "relpath":name,"plate":i} for i,name in enumerate(("a.png","b.png"),1)]}
    cand=Candidate("GLU","Glucose","20 Hours",20,"a.png","b.png","least")
    old=rebuild.rebuild(tmp_path,"ring",cfg,cand,cache_dir=tmp_path/"legacy")
    assert set(old.tidy.measurement_method)=={LEGACY_METHOD}
    review=Review(measurement={"method":"centered-interior-v1","inward_margin":.2,"min_pixels":25})
    current={**cfg,"measurement":review.measurement}
    first=rebuild.rebuild(tmp_path,"ring",current,cand,cache_dir=tmp_path/"new")
    assert np.isfinite(first.tidy.relative_growth).all()
    assert not first.tidy.artifact.any()
    assert not np.allclose(old.tidy.raw_growth,first.tidy.raw_growth)
    monkeypatch.setattr(imaging.ImageTk,"PhotoImage",lambda im:im.copy())
    cache=imaging.ImageCache(); cache.get(tmp_path/"a.png",(600,400),2,.3)
    rv.save(tmp_path/"review.json",review)
    loaded=rv.load(tmp_path/"review.json")
    tc._cached_measure.cache_clear()
    reopened=rebuild.rebuild(tmp_path,"ring",{**cfg,"measurement":loaded.measurement},cand,cache_dir=tmp_path/"new")
    recalculated=rebuild.rebuild(tmp_path,"ring",{**cfg,"measurement":loaded.measurement},cand,cache_dir=tmp_path/"fresh")
    for frame in (reopened,recalculated):
        pd.testing.assert_frame_equal(first.tidy,frame.tidy)
        assert rebuild.summarize(first)==rebuild.summarize(frame)
        for a,b in zip(first.plates,frame.plates):
            np.testing.assert_equal(a.centers,b.centers)
            np.testing.assert_equal(a.provenance['boundary_radii'],b.provenance['boundary_radii'])
            np.testing.assert_equal(a.bg_samples,b.bg_samples)
