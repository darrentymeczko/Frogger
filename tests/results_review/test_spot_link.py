"""A table row and its spot on the sheet point at each other.

Picking a grey value in the table draws the ROI that spot was measured through
on the montage, carried over from the photograph; clicking a spot on
the montage picks its row. `spotmap` does the arithmetic, which is what is
pinned down here, then the two widgets are checked to be wired to it.
"""

import math
from types import SimpleNamespace

import pytest

from results_review import spotmap

#: The photograph's grid: 2 rows x 3 columns, 200 px apart, tilted 10 degrees.
PITCH, TILT = 200.0, math.radians(10)


def photo_xy(i, j):
    dx, dy = j * PITCH, i * PITCH
    return (1000 + dx * math.cos(TILT) - dy * math.sin(TILT),
            800 + dx * math.sin(TILT) + dy * math.cos(TILT))


def make(turn_montage=False, gap_after_col=None):
    """Rows (photo ROIs) and montage spots for one plate.

    The montage removes the tilt and halves the scale (100 px pitch); with
    `turn_montage` it also turns the plate a quarter anticlockwise, as
    rotate_plate does; `gap_after_col` opens a label lane between columns.
    """
    rows, spots = [], []
    for i in range(2):
        for j in range(3):
            slot, rep = j + 1, i + 1
            cx, cy = photo_xy(i, j)
            rows.append({"strain_col": slot, "replicate": f"rep{rep}", "plate": 1,
                         "roi_x": cx - 30, "roi_y": cy - 30, "roi_width": 60,
                         "roi_height": 60})
            lane = 60 if gap_after_col is not None and j > gap_after_col else 0
            mx, my = 100 + j * 100 + lane, 100 + i * 100
            if turn_montage:
                mx, my = 100 + i * 100, 400 - j * 100
            spots.append({"slot": slot, "rep": rep, "level": 0, "x": 2 * mx,
                          "y": 2 * my, "r": 100.0, "k": 2.0, "turn": 0,
                          "mx": mx, "my": my})
    return rows, spots


def radius_of(points, centre):
    return [math.hypot(x - centre[0], y - centre[1]) for x, y in points]


def test_the_roi_circle_is_carried_onto_the_spot_at_the_montage_scale():
    rows, spots = make()
    out = spotmap.outline(rows[4], rows, spots)
    assert out["centre"] == (spots[4]["x"], spots[4]["y"])
    # Photo 200 px pitch -> map 100 px -> view x2: a 30 px radius stays 30.
    assert radius_of(out["roi"], out["centre"]) == pytest.approx([30] * 48, abs=1e-6)


def test_the_roi_turns_with_the_plate():
    rows, spots = make(turn_montage=True)
    out = spotmap.outline(rows[0], rows, spots)
    cx, cy = out["centre"]
    # The circle's first point lies along the photo's +x; once the plate is
    # turned anticlockwise it is up the screen (the photo is tilted 10
    # degrees, so not quite straight up). Its size is unchanged.
    dx, dy = out["roi"][0][0] - cx, out["roi"][0][1] - cy
    assert dy < 0 and abs(dx) < abs(dy) * 0.3
    assert radius_of(out["roi"], out["centre"]) == pytest.approx([30] * 48, abs=1e-6)


def test_neighbours_across_a_label_lane_do_not_stretch_the_roi():
    rows, spots = make(gap_after_col=0)
    out = spotmap.outline(rows[1], rows, spots)      # beside the lane
    assert radius_of(out["roi"], out["centre"]) == pytest.approx([30] * 48, abs=0.5)


def test_a_row_without_an_roi_has_nothing_to_draw():
    rows, spots = make()
    bare = {k: v for k, v in rows[2].items() if not k.startswith("roi")}
    out = spotmap.outline(bare, rows, spots)
    assert out["centre"] == (spots[2]["x"], spots[2]["y"])
    assert out["roi"] == []


def test_a_row_whose_spot_is_not_on_the_sheet_has_no_outline():
    rows, spots = make()
    assert spotmap.outline(rows[0], rows, spots[1:]) is None


def test_a_click_finds_the_spot_whose_cell_it_is_in():
    _, spots = make()
    s = spots[3]
    assert spotmap.spot_at(spots, s["x"] + 60, s["y"] - 50) is s
    assert spotmap.spot_at(spots, -500, -500) is None


# --- the widgets ---------------------------------------------------------------


@pytest.fixture
def sheet(tk_root):
    from results_review.gui.browser import SheetView
    clicked = []
    view = SheetView(tk_root, on_spot=lambda *a: clicked.append(a))
    view.clicked = clicked
    rows, spots = make()
    view._image = SimpleNamespace(width=lambda: 1000, height=lambda: 800)
    view._spots, view._origin = spots, (10.0, 20.0)
    view.rows = rows
    yield view
    view._image = None
    view.destroy()


def test_clicking_a_spot_reports_it_and_a_drag_does_not(sheet):
    s = sheet._spots[4]
    at = SimpleNamespace(x=10 + s["x"], y=20 + s["y"])
    sheet._pan_start(at)
    sheet._pan_end(at)
    assert sheet.clicked == [(s["slot"], s["rep"])]
    sheet._pan_start(at)
    sheet._pan_end(SimpleNamespace(x=at.x + 40, y=at.y))   # a pan, not a click
    assert len(sheet.clicked) == 1


def test_highlighting_a_row_draws_only_its_roi(sheet):
    sheet.highlight(sheet.rows[4], sheet.rows)
    assert len(sheet.canvas.find_withtag("spot")) == 1
    sheet.highlight(None)
    assert sheet.canvas.find_withtag("spot") == ()


def test_the_table_selects_a_clicked_spot_and_reports_its_rows(tk_root):
    from results_review.gui.table import SpotTable
    picked = []
    table = SpotTable(tk_root, on_raw=print, on_outlier=print, on_note=print,
                      on_revert=print, on_select=picked.append)
    rows, _ = make()
    for r in rows:
        r.update(strain=f"s{r['strain_col']}", raw_growth=1.0,
                 relative_growth=1.0)
    table.show(rows, [])
    assert picked[-1] is not None
    assert table.select_spot(3, 2)
    tk_root.update()
    assert spotmap.row_key(picked[-1]) == (3, 2)
    assert not table.select_spot(9, 9)
    table.destroy()
