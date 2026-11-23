"""Task 9: actual Tk delivery, bounded work and deliberately late completions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import threading
import time
import shutil
import tkinter as tk

from PIL import Image, ImageTk
import pytest

from results_review.gui.imaging import ImageCache
from results_review.gui.browser import SheetView
from results_review.app import ReviewApp
from test_sheetview import CAND, OTHER
from test_navigation import keyboard_app
from test_saved_runs import generated, tree_hashes

REBUILD_CURRENT = ReviewApp._rebuild_current


@pytest.fixture(autouse=True)
def discard_generated_edits(monkeypatch):
    # These state-race tests intentionally dirty an unsaved generated run.
    monkeypatch.setattr('results_review.app.messagebox.askyesnocancel',lambda *a,**k:False)


def spin(widget, predicate, timeout=8):
    end = time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        widget.update()
        time.sleep(.003)
    assert predicate(), 'Tk completion timed out'


def picture(path, color='red', size=(700,500)):
    Image.new('RGB',size,color).save(path)
    return path


def test_bytes_eviction_settings_and_pixel_equivalence(tk_root,tmp_path):
    path=picture(tmp_path/'photo.png')
    before=path.read_bytes()
    cache=ImageCache(limit=3,decoded_bytes=2_000_000,rendered_bytes=600_000,tk_bytes=500_000)
    try:
        for n in range(15):
            photo=cache.get(path,(300+n,200),1+n/100,1.2)
            assert photo is not None
            stats=cache.stats
            assert stats['decoded_bytes']<=stats['budgets'][0]
            assert stats['rendered_bytes']<=stats['budgets'][1]
            assert stats['tk_bytes']<=stats['budgets'][2]
            assert stats['entries']<=3
        assert cache.stats['decodes']==1
        assert cache.stats['evictions']>0
        legacy=ImageCache._with_pil(path,320,210,1.1,.8)
        current=cache.get(path,(320,210),1.1,.8)
        assert ImageTk.getimage(legacy).tobytes()==ImageTk.getimage(current).tobytes()
        assert path.read_bytes()==before
        key=cache.key(path,(320,210))
        picture(path,'blue')
        assert not cache.current(key)
        assert ImageTk.getimage(cache.get(path,(320,210))).getpixel((10,10))[:3]==(0,0,255)
        cache.invalidate(path)
        assert not cache._decoded and not cache._rendered and not cache._items
    finally: cache.close()


def test_tone_reuses_fitted_pixels(tk_root,tmp_path):
    path=picture(tmp_path/'tone.png')
    cache=ImageCache()
    try:
        cache.get(path,(300,200),1.2,.8)
        cache.get(path,(300,200),1.4,1.6)
        assert cache.stats['decodes']==cache.stats['layouts']==1
        assert cache.stats['renders']==2
    finally:cache.close()


def test_dedup_backpressure_clear_and_shutdown(tmp_path):
    path=picture(tmp_path/'photo.png')
    cache=ImageCache()
    entered=threading.Event();release=threading.Event()
    original=cache._prepare
    def blocked(key):
        entered.set();assert release.wait(8)
        return original(key)
    cache._prepare=blocked
    key=cache.key(path,(300,200))
    first=cache.request(key)
    assert entered.wait(2)
    assert cache.request(key) is first
    second=cache.request(cache.key(path,(301,200)))
    assert cache.request(cache.key(path,(302,200))) is None
    assert cache.stats['in_flight']==2
    cache.clear()
    assert not cache.current(key)
    start=time.monotonic();cache.close()
    assert time.monotonic()-start<.2
    release.set()
    first.result(8);second.result(8)
    end=time.monotonic()+2
    while cache.stats['in_flight'] and time.monotonic()<end:time.sleep(.003)
    assert not cache._rendered and not cache._decoded
    assert cache.stats['deduplicated']==1
    with pytest.raises(RuntimeError,match='closed'):cache.request(key)
    assert cache.get(path,(300,200)) is None
    assert 'closed' in cache.last_error


def test_worker_failure_is_visible_and_retryable(tk_root,tmp_path,monkeypatch):
    path=picture(tmp_path/'error.png')
    view=SheetView(tk_root);view.pack()
    original=view.cache._prepare
    monkeypatch.setattr(view.cache,'_prepare',lambda key:(_ for _ in ()).throw(OSError('deliberate decoder failure')))
    view.show(CAND,path)
    spin(view,lambda:any('deliberate decoder failure' in view.canvas.itemcget(i,'text')
                        for i in view.canvas.find_all()))
    assert view._image is None
    monkeypatch.setattr(view.cache,'_prepare',original)
    view._draw();spin(view,lambda:view._image is not None)
    view.destroy()


def test_late_image_cannot_replace_selection_tone_or_relinked_source(tk_root,tmp_path):
    a=picture(tmp_path/'a.png','red');b=picture(tmp_path/'b.png','blue')
    view=SheetView(tk_root);view.pack();view.update_idletasks()
    original=view.cache._prepare;entered=threading.Event();release=threading.Event()
    def blocked(key):
        if key[0]==str(a.resolve()):
            entered.set();assert release.wait(8)
        return original(key)
    view.cache._prepare=blocked
    try:
        view.show(CAND,a);assert entered.wait(2)
        view.show(OTHER,b)
        view._brightness_var.set(.5);view._draw()
        ticks=[]
        view.after(1,lambda:ticks.append(True))
        spin(view,lambda:view._image is not None)
        assert ticks and view._cand==OTHER
        current=view._display_key
        assert current[0]==str(b.resolve()) and current[4]==.5
        release.set();spin(view,lambda:view.cache.stats['in_flight']==0)
        view.update()
        assert view._display_key==current
        assert ImageTk.getimage(view._image).getpixel((2,2))[:3]==(0,0,127)
        # A same-name regenerated/relinked image must replace the cached pixels.
        picture(b,'green');view.cache.clear();view.show(CAND,b)
        spin(view,lambda:view._image is not None)
        assert ImageTk.getimage(view._image).getpixel((2,2))[:3]==(0,64,0)
    finally:release.set();view.destroy()


def test_destroy_during_load_has_no_tk_worker_callback(tk_root,tmp_path):
    path=picture(tmp_path/'close.png')
    view=SheetView(tk_root);view.pack()
    entered=threading.Event();release=threading.Event();original=view.cache._prepare
    def work(key):
        entered.set();assert release.wait(8);return original(key)
    view.cache._prepare=work
    view.show(CAND,path);assert entered.wait(2)
    future=next(iter(view.cache._pending.values()))
    view.destroy();release.set();future.result(8)
    tk_root.update()
    assert view._disposed and not view.cache._items and not view.cache._rendered


@pytest.mark.parametrize('change',['presentation','roi','relink','run','source'])
def test_late_graph_never_resurrects_old_state(keyboard_app,monkeypatch,change,tmp_path):
    app=keyboard_app
    if change=='source':
        source=app.ctl.run.sheet(app.ctl.chosen())
        source.parent.mkdir(parents=True,exist_ok=True)
        picture(source)
    token=app._graph_token();calls=[]
    monkeypatch.setattr(app,'_redraw_finished',calls.append)
    if change=='presentation':app.ctl.set_presentation({'version':1,'sheet':{'brightness':1.4}})
    elif change=='roi':app.ctl.set_measurement(.23)
    elif change=='relink':app.ctl.capture_root=tmp_path/'moved'
    elif change=='source':picture(source,'blue')
    else:
        original=app.ctl.run
        app.ctl.run=replace(original,candidates=list(original.candidates))
        assert app.ctl.run is not original and app.ctl.run==original
    app._redraw_if_current(token,('old','old graph'))
    assert calls==[]
    app._redraw_if_current(app._graph_token(),'current')
    assert calls==['current']


@pytest.mark.parametrize('fails',[False,True])
@pytest.mark.parametrize('change',['settings','equal_run','root'])
def test_late_scientific_frame_does_not_repopulate_invalidated_cache(keyboard_app,monkeypatch,fails,change):
    ctl=keyboard_app.ctl
    entered=threading.Event();release=threading.Event()
    monkeypatch.setattr(type(ctl),'can_edit',property(lambda self:True))
    def rebuild(*args):
        entered.set();assert release.wait(8)
        if fails:raise ValueError('obsolete failure')
        return object()
    monkeypatch.setattr(ctl,'_rebuild',rebuild)
    # Prevent the first flags read being mistaken for a user change.
    ctl.data_flags()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(ctl.frame)
        assert entered.wait(2)
        if change=='settings':ctl._drop_previews()
        elif change=='equal_run':
            original=ctl.run
            ctl.run=replace(original,candidates=list(original.candidates))
            assert ctl.run==original and ctl.run is not original
        else:ctl.capture_root=Path('different-source-root')
        ctl._frames.clear();ctl._errors.clear()
        release.set();assert future.result(8) is None
    assert not ctl._frames and not ctl._errors


def test_rebuild_completion_requeues_distinct_equal_run(keyboard_app,monkeypatch):
    app=keyboard_app
    monkeypatch.setattr(type(app.ctl),'can_edit',property(lambda self:True))
    jobs=[];requeued=[]
    monkeypatch.setattr(app,'_run_async',lambda work,done,text:jobs.append(done))
    monkeypatch.setattr(app,'_refresh_table',lambda:None)
    monkeypatch.setattr(app,'_rebuild_current',lambda **kw:requeued.append(kw))
    REBUILD_CURRENT(app)
    assert len(jobs)==1
    original=app.ctl.run
    app.ctl.run=replace(original,candidates=list(original.candidates))
    assert app.ctl.run==original and app.ctl.run is not original
    jobs[0](object())
    assert requeued==[{'redraw':False}]


def test_actual_relink_and_open_run_while_image_is_in_flight(generated,tk_root,monkeypatch):
    from results_review import background,discovery,runs
    g=generated
    g.ctl.save()
    run_a=discovery.load_set(g.folder)
    candidate=g.ctl.chosen()
    sheet=run_a.sheet(candidate)
    sheet.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(g.root/'A-1.png',sheet)
    fork=g.folder.parent/'independent'
    g.ctl.save_as(fork)
    frozen_a=tree_hashes(g.folder);frozen_b=tree_hashes(fork)
    moved=g.tmp/'moved originals';shutil.copytree(g.root,moved)
    monkeypatch.setattr(background,'warm',lambda:None)
    monkeypatch.setattr(ReviewApp,'_rebuild_current',lambda self:None)
    win=tk.Toplevel(tk_root);app=ReviewApp(win,run_a)
    release=threading.Event();entered=threading.Event()
    prepare=app.sheet.cache._prepare
    # Only the pre-relink epoch waits. Relinked and new-run requests can
    # complete on worker two while the obsolete request is still pending.
    epoch=app.sheet.cache._epoch
    def delayed(key):
        if key[1][-2]==epoch:
            entered.set();assert release.wait(12)
        return prepare(key)
    try:
        # Drain any construction request before starting the deliberate race.
        spin(app,lambda:app.sheet._image is not None)
        app.sheet.cache.clear();epoch=app.sheet.cache._epoch
        app.sheet.cache._prepare=delayed
        app._show_sheet(app.ctl.chosen());assert entered.wait(2)
        monkeypatch.setattr('results_review.app.filedialog.askdirectory',lambda **kw:str(moved))
        app._locate_photos()
        assert app.ctl.capture_root==moved
        assert not app.ctl.source_problems()
        spin(app,lambda:app.sheet._image is not None)
        assert app.sheet._display_key[1][-2]!=epoch
        # Start another obsolete request in A and open B through the actual
        # app action; do not simulate the transition by assigning ctl.run.
        release.set();spin(app,lambda:app.sheet.cache.stats['in_flight']==0)
        release.clear();entered.clear()
        app.sheet.cache.clear();epoch=app.sheet.cache._epoch
        app._show_sheet(app.ctl.chosen());assert entered.wait(2)
        monkeypatch.setattr(app,'_confirm_discard',lambda:True)
        assert app._load_set(fork)
        spin(app,lambda:app.sheet._image is not None)
        key=app.sheet._display_key
        assert app.ctl.run.results_dir==fork
        assert Path(key[0]).is_relative_to(fork)
        release.set();spin(app,lambda:app.sheet.cache.stats['in_flight']==0)
        app.update();assert app.sheet._display_key==key
        assert tree_hashes(g.root)==g.hashes
        assert tree_hashes(moved)==g.hashes
        assert tree_hashes(g.folder)==frozen_a and tree_hashes(fork)==frozen_b
    finally:
        release.set();app._dispose();win.destroy()
