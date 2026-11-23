"""Task 11 native Tk acceptance; all photos and saves are generated scratch."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import pytest
from test_action_shortcuts import host, key, wait

ARTIFACTS = Path(__file__).resolve().parents[2] / "validation/task11"


def click(win, tree, code, modifier=""):
    tree.see(code)
    win.update()
    x, y, w, h = tree.bbox(code, "label")
    tree.event_generate(f"<{modifier}Button-1>", x=x + 15, y=y + h // 2)
    tree.event_generate(f"<{modifier}ButtonRelease-1>", x=x + 15, y=y + h // 2)
    win.update()


def fixture_experiment(tmp_path):
    from PIL import Image
    from experiments.model import Experiment, Condition, StrainGroup
    from experiments.profiles import NamingProfile
    e = Experiment(name="Task 11", conditions=[Condition(c, "Treatment " + c) for c in "ABCD"],
                   photo_root=str(tmp_path), profile=NamingProfile(name="Manual"))
    for group in ("G1", "G2"):
        e.strain_groups[group] = StrainGroup(["WT", "EV"], conditions={c.code: deepcopy(c) for c in e.conditions},
            final_analysis={"normalization": {c: 1 for c in "ABCD"}, "references": {c: ["WT"] for c in "ABCD"}})
        for code in "ABCD":
            path = f"{group}-{code}.png"
            Image.new("RGB", (80, 60), (20 + ord(code), 80, 140)).save(tmp_path / path)
            e.overrides[path] = {"condition": code, "set": group, "plate": 1}
            e.strain_groups[group].picks[code] = {"1": path}
    return e


def hashes(tmp_path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.png")}


def test_native_exact_selection_guard_undo_groups_and_reopen(host, tmp_path, monkeypatch):
    from experiments.schema import save, load
    from experiments.gui.controller import ExperimentController
    win, shell, build = host
    app = build("experiment", fixture_experiment(tmp_path))
    ctl = app.controller
    ctl.rescan(infer=False)
    app.notebook.select(app.conditions)
    app.refresh()
    win.update()
    panel, tree = app.conditions, app.conditions.tree
    original = ctl.snapshot()
    originals = hashes(tmp_path)
    click(win, tree, "A")
    click(win, tree, "C", "Control-")
    assert tree.selection() == ("A", "C")
    app.refresh()
    win.update()
    assert tree.selection() == ("A", "C")
    assert panel.selected_code() == "" and not panel._exclude_vars
    panel._rename()  # no ambiguous single-row action
    assert not getattr(panel, "_inline", None)
    click(win, tree, "C", "Control-")
    assert tree.selection() == ("A",)
    click(win, tree, "C", "Shift-")
    assert tree.selection() == ("A", "B", "C")
    click(win, tree, "B", "Control-")
    assert tree.selection() == ("A", "C")
    ctl.select_group("G2")
    app.refresh()
    win.update()
    assert tree.selection() == ("A", "C")
    prompts = []
    monkeypatch.setattr("experiments.gui.conditions.messagebox.askokcancel", lambda *a, **k: prompts.append(a) or False)
    panel._remove()
    assert ctl.snapshot() == original
    assert "4 linked photo" in prompts[-1][1] and "Original photo files" in prompts[-1][1]
    assert "Treatment B\n" not in prompts[-1][1]
    monkeypatch.setattr("experiments.gui.conditions.messagebox.askokcancel", lambda *a, **k: True)
    panel._remove()
    assert ctl.experiment.condition_codes() == ["B", "D"]
    assert not ctl.adopt_found_conditions()  # deleted assignments stay cleared
    for group in ctl.experiment.strain_groups.values():
        assert set(group.conditions) == set(group.picks) == {"B", "D"}
        assert set(group.final_analysis["normalization"]) == {"B", "D"}
        assert set(group.final_analysis["references"]) == {"B", "D"}
    for row in ctl.resolution.rows:
        assert row.condition is None if row.relpath[-5] in "AC" else row.condition in "BD"
    deleted = ctl.snapshot()
    target = tmp_path / "deleted.spotexp.json"
    save(ctl.experiment, target, bump_revision=False)
    reopened = ExperimentController(load(target))
    reopened.rescan(infer=False)
    assert reopened.snapshot() == deleted
    assert [(r.relpath, r.condition, r.status) for r in reopened.resolution.rows] == [
        (r.relpath, r.condition, r.status) for r in ctl.resolution.rows]
    key(win, tree, "<Control-z>")
    assert ctl.snapshot() == original
    key(win, tree, "<Control-y>")
    assert ctl.snapshot() == deleted
    # Resolver failure rolls everything back without publishing history.
    before_history = len(ctl.undo_stack._undo)
    def fail():
        raise ValueError("fixture resolver failure")
    monkeypatch.setattr(ctl, "reresolve", fail)
    with pytest.raises(ValueError, match="fixture resolver"):
        ctl.remove_conditions(["B", "D"])
    assert ctl.snapshot() == deleted and len(ctl.undo_stack._undo) == before_history
    assert hashes(tmp_path) == originals


@pytest.mark.parametrize("width,height", [(850, 650), (1300, 900), (1800, 1100)])
def test_preview_native_slot_labels_and_coordinates(host, width, height, tmp_path):
    from plate_template.presets import lab_standard_8x6
    from experiments.model import Experiment
    # Use the production embedded-layout path.
    win, shell, build = host
    app = build("experiment", Experiment())
    template = lab_standard_8x6()
    app.controller.use_layout(template)
    app.refresh()
    win.geometry(f"{width}x{height}")
    app.notebook.select(app.panel)
    win.update()
    canvas = app.panel.template_preview
    app.panel._draw_preview()
    win.update()
    labels = canvas.find_withtag("slot-label")
    placements = [(pi, r, c, cell.placement) for pi, p in enumerate(template.plates)
                  for r, row in enumerate(p.cells) for c, cell in enumerate(row) if cell.placement]
    assert len(labels) == len(placements)
    boxes = []
    for item, (pi, r, c, placement) in zip(labels, placements):
        assert canvas.itemcget(item, "text") == str(placement.sample_slot)
        tags = canvas.gettags(item)
        assert f"plate-{pi}" in tags and f"slot-{placement.sample_slot}" in tags
        font = tkfont.Font(root=canvas, font=canvas.itemcget(item, "font"))
        assert font.metrics("linespace") >= 14
        oval = item - 1
        x0, y0, x1, y1 = canvas.coords(oval)
        x, y = canvas.coords(item)
        assert x == pytest.approx((x0 + x1) / 2) and y == pytest.approx((y0 + y1) / 2)
        box = canvas.bbox(item)
        assert x0 <= box[0] < box[2] <= x1 and y0 <= box[1] < box[3] <= y1
        assert canvas.itemcget(item, "fill") in ("#000000", "#ffffff")
        boxes.append({"slot": placement.sample_slot, "bbox": box, "center": [x, y]})
    (tmp_path / "label-coordinates.json").write_text(json.dumps(boxes, indent=2))
    # Exact HWND capture is independent of desktop/monitor coordinates.
    capture_window(win, tmp_path / "preview-window.png")


def capture_window(win, path):
    """Capture this HWND, not a DPI-dependent rectangle of the desktop."""
    import ctypes as c
    from ctypes import wintypes as w
    from PIL import Image
    user, gdi = c.windll.user32, c.windll.gdi32
    user.GetAncestor.argtypes = [w.HWND, w.UINT]
    user.GetAncestor.restype = w.HWND
    user.GetWindowDC.argtypes = [w.HWND]
    user.GetWindowDC.restype = w.HDC
    gdi.CreateCompatibleDC.argtypes = [w.HDC]
    gdi.CreateCompatibleDC.restype = w.HDC
    gdi.CreateCompatibleBitmap.argtypes = [w.HDC, c.c_int, c.c_int]
    gdi.CreateCompatibleBitmap.restype = w.HBITMAP
    gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
    gdi.SelectObject.restype = w.HANDLE
    user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
    user.ReleaseDC.argtypes = [w.HWND, w.HDC]
    gdi.DeleteObject.argtypes = [w.HANDLE]
    gdi.DeleteDC.argtypes = [w.HDC]
    win.deiconify()
    win.lift()
    win.update()
    hwnd = user.GetAncestor(win.winfo_id(), 2)
    rect = w.RECT()
    user.GetWindowRect(hwnd, c.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = user.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(dc)
    bmp = gdi.CreateCompatibleBitmap(dc, width, height)
    old = gdi.SelectObject(mem, bmp)
    try:
        assert user.PrintWindow(hwnd, mem, 2), "PrintWindow unavailable"
        class Header(c.Structure):
            _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                        ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                        ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                        ("used", w.DWORD), ("important", w.DWORD)]
        header = Header(c.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = c.create_string_buffer(width * height * 4)
        gdi.SelectObject(mem, old)
        gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.c_void_p, w.UINT]
        assert gdi.GetDIBits(mem, bmp, 0, height, data, c.byref(header), 0) == height
        Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1).save(path)
    finally:
        gdi.SelectObject(mem, old)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mem)
        user.ReleaseDC(hwnd, dc)


@pytest.mark.parametrize("size", ["minimum", "maximum"])
def test_preview_native_supported_spot_sizes(host, size, tmp_path):
    from plate_template.model import Template, Cell
    from experiments.model import Experiment
    win, shell, build = host
    app = build("experiment", Experiment())
    template = Template(name="Readable slots 1, 12, 24", rows=2, cols=3)
    plate = template.add_plate("1")
    for r in range(2):
        for c, slot in enumerate((1, 12, 24)):
            plate.set(r, c, Cell.spot(slot, r + 1, 0))
    app.controller.use_layout(template)
    app.refresh()
    win.geometry("1100x800+0+0")
    app.notebook.select(app.panel)
    win.update()
    canvas = app.panel.template_preview
    # Exercise the preview itself at its documented readable floor and DPI
    # aware zoom cap, in a compact fixture that fits the physical desktop.
    if size == "minimum":
        # The actual floor is reached by many columns in a narrow viewport.
        template.cols = 12
        from plate_template.model import UNASSIGNED
        plate.cells = [row + [UNASSIGNED] * 9 for row in plate.cells]
        app.controller.use_layout(template)
        app.refresh()
        app.panel.panes.sashpos(0, app.panel.panes.winfo_width() - 230)
    app.panel._draw_preview()
    win.update()
    labels = canvas.find_withtag("slot-label")
    assert len(labels) == 6
    evidence = []
    for label in labels:
        oval = label - 1
        x0, y0, x1, y1 = canvas.coords(oval)
        pitch = (x1 - x0) / .8
        expected = 28 if size == "minimum" else 72 * max(1, canvas.winfo_fpixels("1i") / 96)
        assert pitch == pytest.approx(expected)
        box = canvas.bbox(label)
        assert x0 <= box[0] < box[2] <= x1 and y0 <= box[1] < box[3] <= y1
        font = tkfont.Font(root=canvas, font=canvas.itemcget(label, "font"))
        assert font.metrics("linespace") >= 14
        evidence.append({"slot": canvas.itemcget(label, "text"), "pitch": pitch, "bbox": box,
                         "font": font.actual(), "ink": canvas.itemcget(label, "fill")})
    (tmp_path / "supported-size-coordinates.json").write_text(json.dumps(evidence, indent=2))
    capture_window(win, tmp_path / f"plate-{size}.png")


def test_deleting_inferred_links_cannot_assign_to_sole_survivor(tmp_path):
    from experiments.gui.controller import ExperimentController
    from experiments.model import Experiment, Condition
    from experiments.profiles import capture_tree
    from experiments.schema import save, load
    from PIL import Image
    e = Experiment(name="Inferred", conditions=[Condition("GLU", "Glucose"), Condition("GAL", "Galactose")],
                   photo_root=str(tmp_path), profile=capture_tree())
    for medium in ("Glucose", "Galactose"):
        folder = tmp_path / "16 Hours" / medium / "Plate 1"
        folder.mkdir(parents=True)
        Image.new("RGB", (30, 20), "gray").save(folder / "photo.png")
    ctl = ExperimentController(e)
    ctl.rescan(infer=False)
    before = ctl.snapshot()
    assert ctl.condition_photo_links(["GLU"]) == {"16 Hours/Glucose/Plate 1/photo.png"}
    ctl.remove_conditions(["GLU", "not-a-condition"])
    assert ctl.experiment.condition_codes() == ["GAL"]
    assert [r.condition for r in ctl.resolution.rows] == ["GAL", None]
    path = tmp_path / "sole.spotexp.json"
    save(ctl.experiment, path, bump_revision=False)
    reopened = ExperimentController(load(path))
    reopened.rescan(infer=False)
    assert [r.condition for r in reopened.resolution.rows] == ["GAL", None]
    ctl.undo()
    assert ctl.snapshot() == before


def test_job_native_widgets_and_queued_reader_thread_delivery(tk_root, tmp_path):
    from uikit.host import JobSpec
    from workbench.jobs import Job, JobRunner
    from workbench.jobs_ui import _Row, JobLog
    window = tk.Toplevel(tk_root)
    window.geometry("900x700")
    runner = JobRunner(lambda *_: None, log_dir=tmp_path)
    job = Job(runner, JobSpec("Generated job", ["unused"]))
    row = _Row(window, job, lambda *_: None)
    row.pack(fill="x")
    class Host:
        frame = window
        def set_title(self, *a, **k): pass
    class Shell:
        def open_results(self, *_): pass
    log = JobLog(Host(), job, Shell())
    try:
        def check(state, mode, phrase, value=None):
            job.state = state
            row.update_row()
            log.update_log()
            window.update()
            for view in (row, log):
                assert phrase in view.detail.cget("text")
                assert str(view.bar.cget("mode")) == mode
                assert view.bar.winfo_ismapped()
                if value is not None:
                    # As a fraction of the bar: its scale is an animation detail.
                    filled = (float(view.bar.cget("value"))
                              / float(view.bar.cget("maximum")))
                    assert filled == pytest.approx(value)
        check(Job.QUEUED, "determinate", "Queued", 0)
        check(Job.RUNNING, "indeterminate", "Starting")
        job._take("line", "Measuring plates")
        job._take("live", "  2/8")
        check(Job.RUNNING, "determinate", "Measuring plates", .25)
        job._take("line", "  [3/12] more plates discovered")
        check(Job.RUNNING, "determinate", "3/12", .25)
        job._take("line", "Exporting figures")
        check(Job.RUNNING, "indeterminate", "Exporting figures")
        job._take("live", "Rendering without a newline")
        check(Job.RUNNING, "indeterminate", "Rendering without a newline")
        job._take("live", "  1/0")
        check(Job.RUNNING, "indeterminate", "Running")
        job._take("live", "  3/2")
        assert job.progress is None
        job._take("line", "version 1/2 is descriptive text")
        assert job.progress is None
        job._take("live", "  1/4")
        job.returncode = 9
        check(Job.FAILED, "determinate", "Failed", .25)
        check(Job.STOPPED, "determinate", "Cancelled", .25)
        job.progress = None
        check(Job.DONE, "determinate", "Completed", 1)
        # Real subprocess reader produces events, never calls widgets itself.
        ui_thread = threading.get_ident()
        threads = []
        def changed(current, kinds):
            threads.append(threading.get_ident())
            row.job = log.job = current
            row.update_row()
            log.update_log(full=True)
        runner.on_change = changed
        child = runner.start(JobSpec("scratch", [sys.executable, "-u", "-c",
            "import time; print('Measuring'); print('  1/2'); time.sleep(.2); print('Rendering'); time.sleep(.2)"], cwd=str(tmp_path)), owner=window)
        wait(window, lambda: not runner._events.empty())
        assert not child.lines
        end = time.monotonic() + 15
        while not child.finished and time.monotonic() < end:
            runner.pump()
            window.update()
            time.sleep(.01)
        assert child.state == Job.DONE and child.owner is window
        assert child.phase == "Rendering" and child.progress is None
        assert set(threads) == {ui_thread}
        assert "Completed" in row.detail.cget("text") and float(log.bar.cget("value")) == float(log.bar.cget("maximum"))
        # Real queue, cancellation, start and failure transitions use the same
        # callback path as the shell. Child processes only write scratch output.
        slow = runner.start(JobSpec("slow", [sys.executable, "-u", "-c", "import time; time.sleep(20)"], kind="experiment-run"))
        queued = runner.start(JobSpec("queued", [sys.executable, "-c", "raise SystemExit(7)"], kind="experiment-run"))
        assert queued.state == Job.QUEUED and "Queued" in log.detail.cget("text")
        queued.stop()
        assert queued.state == Job.STOPPED and "Cancelled" in log.detail.cget("text")
        following = runner.start(JobSpec("following", [sys.executable, "-c", "raise SystemExit(7)"], kind="experiment-run"))
        slow.stop()
        end = time.monotonic() + 15
        while (not slow.finished or not following.finished) and time.monotonic() < end:
            runner.pump()
            window.update()
            time.sleep(.01)
        assert slow.state == Job.STOPPED and following.state == Job.FAILED
        assert "Failed" in row.detail.cget("text") and "7" in log.detail.cget("text")
        assert str(row.bar.cget("mode")) == "determinate"
        with pytest.raises(OSError):
            runner.start(JobSpec("missing", [str(tmp_path / "missing.exe")]))
        assert runner.jobs[-1].state == Job.FAILED and "could not start" in row.detail.cget("text")
        assert set(threads) == {ui_thread}
    finally:
        for current in runner.running():
            current.stop()
        window.destroy()
