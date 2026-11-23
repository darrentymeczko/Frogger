"""The dilution drag derives direction, level count and source run from the
gesture alone, so that arithmetic is worth pinning down.

Importing plate_template.app pulls in tkinter but never creates a root window,
so these run without a display.
"""

import pytest

from plate_template.app import DesignerApp
from plate_template.autofill import SeriesFillSpec, plan_series_fill
from plate_template.model import EMPTY, Cell, DilutionSpec, Template


def plate_with(row_spots) -> Template:
    """One 6x8 plate; row_spots maps (row, col) -> (slot, rep, dilution)."""
    t = Template(name="t", rows=6, cols=8, dilution=DilutionSpec(levels=4))
    plate = t.add_plate("1", control_slot=1)
    for (r, c), (slot, rep, dil) in row_spots.items():
        plate.set(r, c, Cell.spot(slot, rep, dil))
    return t


# ---------------------------------------------------------------------------
# Direction and span come from the drag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "anchor,current,direction,spanned",
    [
        ((0, 0), (2, 0), "down", 3),
        ((2, 0), (0, 0), "up", 3),
        ((0, 0), (0, 4), "right", 5),
        ((0, 4), (0, 0), "left", 5),
        ((0, 0), (0, 0), "down", 1),      # no movement
        ((0, 0), (3, 1), "down", 4),       # dominant axis wins, never diagonal
        ((0, 0), (1, 5), "right", 6),
    ],
)
def test_line_reads_direction_and_span(anchor, current, direction, spanned):
    assert DesignerApp._line(anchor, current) == (direction, spanned)


# ---------------------------------------------------------------------------
# The source block is found, not clicked
# ---------------------------------------------------------------------------


def block(plate, anchor):
    return DesignerApp._source_block(plate, anchor, plate.get(*anchor).placement)


def test_source_block_picks_up_the_whole_row():
    plate = plate_with({(0, c): (c + 1, 1, 0) for c in range(8)}).plate("1")
    assert block(plate, (0, 3)) == [(0, c) for c in range(8)]


def test_source_block_stops_at_a_gap():
    spots = {(0, c): (c + 1, 1, 0) for c in range(8)}
    del spots[(0, 5)]
    plate = plate_with(spots).plate("1")
    plate.set(0, 5, EMPTY)
    assert block(plate, (0, 3)) == [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]


def test_source_block_spans_mixed_replicates():
    spots = {(0, c): (c + 1, 1, 0) for c in range(4)}
    spots.update({(0, c): (c + 1, 2, 0) for c in range(4, 8)})
    plate = plate_with(spots).plate("1")
    assert block(plate, (0, 1)) == [(0, c) for c in range(8)]


def test_source_block_stops_at_a_different_dilution():
    spots = {(0, c): (c + 1, 1, 0) for c in range(8)}
    spots[(0, 6)] = (7, 1, 1)
    plate = plate_with(spots).plate("1")
    assert block(plate, (0, 2)) == [(0, c) for c in range(6)]


def test_source_block_of_a_lone_spot_is_just_itself():
    plate = plate_with({(2, 2): (1, 1, 0)}).plate("1")
    assert block(plate, (2, 2)) == [(2, 2)]


# The layout that prompted block drags: strain per column, three replicates
# down each column, strains 2-4 spilling into the next column as reps 4-6.
SCREENSHOT = {
    (r, c): (slot, rep_base + r, 0)
    for c, (slot, rep_base) in enumerate(
        [(1, 1), (2, 1), (2, 4), (3, 1), (3, 4), (4, 1), (4, 4), (1, 4)]
    )
    for r in range(3)
}


def test_source_block_takes_the_whole_3x8_block():
    plate = plate_with(SCREENSHOT).plate("1")
    assert block(plate, (1, 4)) == sorted(SCREENSHOT)


# ---------------------------------------------------------------------------
# Direction, step and levels come from where the drag ends
# ---------------------------------------------------------------------------


ROW = [(0, c) for c in range(8)]
BLOCK = sorted(SCREENSHOT)


@pytest.mark.parametrize(
    "cells,current,spacing,expected",
    [
        (ROW, (2, 3), 1, ("down", 1, 3)),      # plain three-cell drag
        (ROW, (0, 3), 1, None),                # still on the source
        (ROW, (4, 3), 2, ("down", 2, 3)),      # drag ends on the LAST level
        (ROW, (3, 3), 2, ("down", 2, 2)),
        (ROW, (6, 0), 3, ("down", 3, 3)),
        ([(2, 0)], (0, 0), 1, ("up", 1, 3)),
        ([(0, 0)], (0, 4), 1, ("right", 1, 5)),
        ([(0, 0)], (3, 1), 1, ("down", 1, 4)),  # dominant axis wins
        (BLOCK, (3, 0), 1, ("down", 3, 2)),    # first row of the next tile
        (BLOCK, (5, 7), 1, ("down", 3, 2)),    # anywhere in that tile
        (BLOCK, (6, 2), 1, ("down", 3, 3)),    # into a third tile
        (BLOCK, (1, 5), 1, None),              # inside the block
        ([(0, 0), (1, 0)], (5, 0), 2, ("down", 2, 3)),  # interleaved reps
    ],
)
def test_block_drag(cells, current, spacing, expected):
    assert DesignerApp._block_drag(cells, current, spacing) == expected


def test_block_drag_copies_the_screenshot_layout_one_dilution_down():
    t = plate_with(SCREENSHOT)
    plate = t.plate("1")
    sources = block(plate, (0, 0))
    direction, step, levels = DesignerApp._block_drag(sources, (4, 0), 1)
    plan = plan_series_fill(
        t, "1", SeriesFillSpec(sources, direction, spacing=step, levels=levels)
    )
    assert not plan.issues
    got = {(r, c): cell.placement for r, c, cell in plan.writes}
    assert sorted(got) == [(r + 3, c) for r, c in BLOCK]
    for (r, c), (slot, rep, dil) in SCREENSHOT.items():
        p = got[(r + 3, c)]
        assert (p.sample_slot, p.replicate, p.dilution) == (slot, rep, dil + 1)


# ---------------------------------------------------------------------------
# Selecting the sources before dragging them
# ---------------------------------------------------------------------------


def test_click_on_a_spot_selects_its_whole_block():
    plate = plate_with(SCREENSHOT).plate("1")
    assert DesignerApp._pick_sources(plate, (2, 6), (2, 6)) == set(SCREENSHOT)


def test_click_on_a_blank_selects_nothing():
    plate = plate_with(SCREENSHOT).plate("1")
    assert DesignerApp._pick_sources(plate, (4, 0), (4, 0)) == set()


def test_drag_selects_only_the_spots_in_its_rectangle():
    plate = plate_with(SCREENSHOT).plate("1")
    # Columns 2-3, rows 1 to 4: rows 3-4 are blank so only rows 1-2 count.
    assert DesignerApp._pick_sources(plate, (4, 2), (1, 1)) == {
        (1, 1), (1, 2), (2, 1), (2, 2)
    }


def test_partial_selection_tiles_by_its_own_height():
    t = plate_with(SCREENSHOT)
    plate = t.plate("1")
    sources = sorted(DesignerApp._pick_sources(plate, (0, 0), (1, 1)))
    direction, step, levels = DesignerApp._block_drag(sources, (2, 0), 1)
    # Rows 0-1 are two high, so they step two rows: row 0 onto row 2, which
    # is occupied and skipped with a warning, row 1 onto the free row 3.
    assert (direction, step, levels) == ("down", 2, 2)
    plan = plan_series_fill(
        t, "1", SeriesFillSpec(sources, direction, spacing=step, levels=levels)
    )
    assert sorted(plan.cells) == [(3, 0), (3, 1)]
    assert {i.code for i in plan.issues} == {"series_target_occupied"}


class _Event:
    def __init__(self, cell):
        self.x, self.y = cell


@pytest.fixture
def canvas(tk_root):
    from types import SimpleNamespace

    from plate_template.gui.grid_canvas import GridCanvas

    t = plate_with(SCREENSHOT)
    c = GridCanvas(tk_root, SimpleNamespace(template=t), "1")
    c.cell_at = lambda x, y: (x, y)  # events carry cells directly
    c.select_provider = lambda a, b: DesignerApp._pick_sources(t.plate("1"), a, b)
    c.edits = []
    c.plan_provider = lambda a, b: ("plan", a, b)
    c.commit_handler = c.edits.append
    yield c
    c.destroy()


def gesture(canvas, *cells):
    canvas._on_press(_Event(cells[0]))
    for cell in cells[1:]:
        canvas._on_drag(_Event(cell))
    canvas._on_release(_Event(cells[-1]))


def test_a_drag_outside_the_selection_selects_rather_than_edits(canvas):
    gesture(canvas, (0, 0), (1, 1))
    assert canvas.selection == {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert canvas.edits == []


def test_a_drag_from_inside_the_selection_edits_and_spends_it(canvas):
    gesture(canvas, (0, 0), (1, 1))
    gesture(canvas, (1, 1), (3, 1))
    assert canvas.edits == [("plan", (1, 1), (3, 1))]
    assert canvas.selection == set()


def test_clicking_a_blank_or_escape_clears_the_selection(canvas):
    gesture(canvas, (0, 0))
    assert canvas.selection == set(SCREENSHOT)
    gesture(canvas, (5, 5))
    assert canvas.selection == set()
    gesture(canvas, (0, 0))
    canvas.clear_selection()
    assert canvas.selection == set()


# ---------------------------------------------------------------------------
# The slot counter walks itself on
# ---------------------------------------------------------------------------


def test_next_free_slot_starts_at_one_on_an_empty_plate():
    t = plate_with({})
    assert DesignerApp._next_free_slot(t, 1, 0) == 1


def test_next_free_slot_advances_past_a_placed_run():
    t = plate_with({(0, c): (c + 1, 1, 0) for c in range(8)})
    assert DesignerApp._next_free_slot(t, 1, 0) == 9


def test_next_free_slot_resets_for_a_new_replicate():
    # Replicate 2 uses the same samples again, so the counter must drop back.
    t = plate_with({(0, c): (c + 1, 1, 0) for c in range(8)})
    assert DesignerApp._next_free_slot(t, 2, 0) == 1


def test_next_free_slot_is_scoped_per_dilution():
    # Placing the undiluted row must not make the counter skip for dilution 1.
    t = plate_with({(0, c): (c + 1, 1, 0) for c in range(8)})
    assert DesignerApp._next_free_slot(t, 1, 1) == 1


def test_next_free_slot_fills_an_erased_gap():
    spots = {(0, c): (c + 1, 1, 0) for c in range(8)}
    del spots[(0, 2)]  # slot 3 never placed
    t = plate_with(spots)
    assert DesignerApp._next_free_slot(t, 1, 0) == 3


def test_next_free_slot_is_bounded():
    t = plate_with({})
    plate = t.plate("1")
    for c in range(8):
        plate.set(0, c, Cell.spot(c + 1, 1, 0))
    # Sanity: never loops forever even if every slot up to the cap is taken.
    assert 1 <= DesignerApp._next_free_slot(t, 1, 0) <= 99
