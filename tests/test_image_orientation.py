"""Asymmetric pixels through intake, ROI review, montage, sheet and PPTX.

Stored pixels are the intentional coordinate contract, including on old runs.
Different corner tones and an off-centre mark detect reflections that round
spots or centre-only assertions cannot detect. All inputs are synthetic.
"""

from io import BytesIO
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw
from pptx import Presentation
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import spotting_batch as sb
import spotting_montage as sm
import spotting_pptx as sp
import spotting_quant as sq
import spotting_sheet as ss
from data_review.gui.plate import open_full, open_preview
from experiments import geometry
from experiments.gui import imaging as intake
from results_review.gui import imaging as review


def asymmetric(size=(120, 80)):
    im = Image.new("RGB", size, (15, 15, 15))
    w, h = size
    for box, tone in [((0, 0, w//2, h//2), 45),
                      ((w//2, 0, w, h//2), 105),
                      ((0, h//2, w//2, h), 165),
                      ((w//2, h//2, w, h), 225)]:
        im.paste((tone,) * 3, box)
    ImageDraw.Draw(im).rectangle((8, 12, 15, 30), fill=(250,) * 3)
    return im


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("ext", ["jpg", "png", "tif"])
def test_exif_never_changes_measurement_review_or_deck_coordinates(
        tmp_path, monkeypatch, orientation, ext):
    path = tmp_path / f"asymmetric-exif-{orientation}.{ext}"
    original = asymmetric()
    if ext == "tif":
        tifffile.imwrite(path, np.array(original), photometric="rgb",
                         extratags=[(274, "H", 1, orientation, False)])
        expected = np.array(original)
    else:
        exif = Image.Exif()
        exif[274] = orientation
        original.save(path, exif=exif, quality=100, subsampling=0)
        # Decode independently, with no EXIF transpose, allowing JPEG losses.
        with Image.open(path) as im:
            expected = np.array(im)
    before = path.read_bytes()
    np.testing.assert_array_equal(sq.load_gray8(path), expected[..., 0])
    pixels = open_preview(path)
    assert pixels.size == original.size
    np.testing.assert_array_equal(np.asarray(pixels.preview), expected)
    np.testing.assert_array_equal(np.asarray(open_full(path)), expected)

    # Exercise both real preview backends without needing a desktop window.
    monkeypatch.setattr(intake.ImageTk, "PhotoImage", lambda im: im.copy())
    monkeypatch.setattr(review.ImageTk, "PhotoImage", lambda im: im.copy())
    shown = intake.ImageCache._with_pil(path, 1000, 1000)
    inspected = review.ImageCache._with_pil(path, 1000, 1000, 1, 1)
    np.testing.assert_array_equal(np.asarray(shown), expected)
    np.testing.assert_array_equal(np.asarray(inspected), expected)
    cache = review.ImageCache()
    prepared = cache._prepare(cache.key(path, (1000, 1000)))
    np.testing.assert_array_equal(np.asarray(prepared), expected)
    cache.close()
    assert review.size_of(path) == original.size

    # Both public PPTX entry points must embed identical, untagged pixels.
    decks = [sp.build_slides([([path], "stored pixels")], tmp_path / "review.pptx"),
             sp.build([(("1", "T"), path, path)], tmp_path / "batch.pptx")]
    for deck in decks:
        pres = Presentation(deck)
        for shape in pres.slides[0].shapes:
            with Image.open(BytesIO(shape.image.blob)) as embedded:
                assert embedded.getexif().get(274) in (None, 1)
                np.testing.assert_array_equal(np.asarray(embedded), expected)
            assert shape.rotation == 0
            xf = shape._element.spPr.xfrm
            assert not xf.flipH and not xf.flipV
            assert shape.width / shape.height == pytest.approx(1.5, rel=1e-5)
    assert path.read_bytes() == before


@pytest.mark.parametrize("top,turn", [("top", 0), ("right", 3),
                                        ("bottom", 2), ("left", 1)])
def test_explicit_photo_orientation_maps_labels_without_mirroring(top, turn):
    from experiments.run import to_layout
    from plate_template.model import Template, Cell, DilutionSpec

    # Unique identities on a non-square plate expose transposes and reversals.
    ids = np.arange(6).reshape(2, 3)
    want = np.rot90(ids, turn)
    got = np.full(want.shape, -1)
    for r, c in np.ndindex(ids.shape):
        rr, cc = geometry.oriented_cell(r, c, *ids.shape, top)
        got[rr, cc] = ids[r, c]
    np.testing.assert_array_equal(got, want)
    template = Template(name="asymmetric", rows=2, cols=3,
                        dilution=DilutionSpec(levels=1))
    plate = template.add_plate("1", control_slot=1)
    for r, c in np.ndindex(ids.shape):
        plate.cells[r][c] = Cell.spot(c+1, r+1, 0)
    layout = to_layout(template, top)
    assert (layout.n_rows, layout.n_cols) == want.shape
    for cell in layout.levels[0].cells(1):
        assert (cell.replicate-1)*3 + cell.slot-1 == want[cell.row, cell.col]


def plate_fixture(tmp_path):
    """Known source centres and unequal, internally asymmetric spot patches."""
    from types import SimpleNamespace

    yy, xx = np.mgrid[:180, :240]
    source = np.zeros((180, 240), dtype=np.uint8)
    centers = np.empty((2, 3, 2), float)
    for r, c in np.ndindex(2, 3):
        y, x = 50 + r * 70, 50 + c * 70
        centers[r, c] = y, x
        # Different centre tone per cell, plus off-centre brighter/darker marks.
        source[(abs(yy-y) < 20) & (abs(xx-x) < 20)] = 50 + 30*(r*3+c)
        source[(abs(yy-(y-12)) < 5) & (abs(xx-(x-12)) < 5)] = 240
        source[(abs(yy-(y+12)) < 5) & (abs(xx-(x+12)) < 5)] = 25
    path = tmp_path / "source.png"
    Image.fromarray(source).save(path)
    plate = sb.PlateData(ref=SimpleNamespace(path=path, plate=1),
                         net=np.arange(6).reshape(2, 3),
                         rim=np.zeros((2, 3), bool), bg_mean=0, spread=0,
                         centers=centers, radius=20, bg_samples=np.zeros(5),
                         quant_rows=(1, 2), plate_center=(90, 120), plate_radius=500)
    cells = tuple(sb.LevelCell(r, c, c+1, r+1) for r, c in np.ndindex(2, 3))
    design = sb.DilutionLayout((sb.DilutionLevel(0, "neat", ((1, cells),)),), 2, 3)
    return plate, source, design


@pytest.mark.parametrize("turned", [False, True])
def test_montage_coordinates_and_asymmetric_marks_ignore_plot_style(
        tmp_path, turned):
    import matplotlib as mpl

    plate, source, design = plate_fixture(tmp_path)
    if turned:
        design = sm.rotate_layout(design, [plate])
        plate = sm.rotate_plate(plate)
    # Deliberately hostile global style: montage must declare image origin.
    with mpl.rc_context({"image.origin": "lower"}):
        out = sm.build_montage("1|T", [plate], ["A", "B", "C"],
                               sq.MeasureOptions(), tmp_path / "montage.png",
                               layout=design, proc=[source], whole_plate=True,
                               dpi=100)
    with Image.open(out) as im:
        record = ss.png_record(im, sm.SPOT_KEY)
        gray = im.convert("L")
        assert len(record["spots"]) == 6
        for s in record["spots"]:
            # Identity follows the explicitly turned layout, never a flip.
            expect = 50 + 30*((s["rep"]-1)*3 + s["slot"]-1)
            x, y = round(s["x"]), round(s["y"])
            assert gray.getpixel((x, y)) == pytest.approx(expect, abs=3)
            delta = round(s["r"] * 2 * 12 / 70)
            # Top-left mark turns to bottom-left under an explicit CCW turn.
            bright = (x-delta, y+delta if turned else y-delta)
            dark = (x+delta, y-delta if turned else y+delta)
            assert gray.getpixel(bright) > 220
            assert gray.getpixel(dark) < 40


@pytest.mark.parametrize("reverse", [False, True])
def test_aligned_user_turn_is_rotation_not_reflection(reverse):
    montage = Image.new("RGB", (240, 80), (15, 15, 15))
    tile = asymmetric((40, 40))
    group = []
    for rep, x in enumerate(([180, 100, 20] if reverse else [20, 100, 180]), 1):
        montage.paste(tile, (x-20, 20))
        group.append(dict(x=x, y=40, r=40, rep=rep))
    strip, centres = ss._strain_strip(group, montage, 1)
    # Compare an interior tile against an independently specified rigid turn.
    expected = tile.transpose(Image.Transpose.ROTATE_90 if reverse
                              else Image.Transpose.ROTATE_270)
    centre = round(centres[1])
    cut = strip.crop((strip.width//2-20, centre-20,
                      strip.width//2+20, centre+20))
    np.testing.assert_array_equal(np.asarray(cut), np.asarray(expected))


@pytest.mark.parametrize("mode", ["fit", "aligned"])
@pytest.mark.parametrize("rotate", [False, True])
def test_review_export_and_deck_share_asymmetric_orientation(
        tmp_path, monkeypatch, mode, rotate):
    from results_review.export import slide_picture, SLIDE_PX

    montage = asymmetric((1200, 800))
    graph = Image.new("RGB", (2400, 1300), "white")
    graph_h = Image.new("RGB", (1300, 2400), "white")
    pieces = dict(montage=montage, graph=graph, graph_h=graph_h,
                  montage_rot=montage.transpose(Image.Transpose.ROTATE_90))
    spots = [dict(x=300+600*c, y=200+400*r, r=120,
                  strain=n, slot=c+1, rep=r+1, level=0)
             for c, n in enumerate(("A", "B")) for r in range(2)]
    extra = dict(level=0, montage_map=dict(size=[1200, 800], spots=spots),
                 graph_map=dict(size=list(graph.size), pitch=1200,
                                ticks=[dict(name=n, x=600+1200*c)
                                       for c, n in enumerate(("A", "B"))]),
                 graph_h_map=dict(size=list(graph_h.size), pitch=1200,
                                  ticks=[dict(name=n, y=600+1200*c)
                                         for c, n in enumerate(("A", "B"))]))
    sheet, lay = ss.arrange({k: pieces[k] for k in ("montage", "graph")}, ss.SHEET_BOX)
    path = ss.save(sheet, lay, tmp_path / "sheet.png", extra=extra, panels=pieces)
    graph_path = tmp_path / "graph.png"
    from PIL.PngImagePlugin import PngInfo
    import json
    import spotting_plots as plots

    for p, image, gmap in [(graph_path, graph, extra["graph_map"]),
                            (plots.horizontal_of(graph_path), graph_h,
                             extra["graph_h_map"])]:
        info = PngInfo()
        info.add_text(plots.GRAPH_KEY, json.dumps(gmap))
        p.parent.mkdir(parents=True, exist_ok=True)
        image.save(p, pnginfo=info)
    before = path.read_bytes()
    monkeypatch.setattr(review.ImageTk, "PhotoImage", lambda im: im.copy())
    shown = review.ImageCache._with_pil(path, *SLIDE_PX, 1, 1, mode, rotate)
    cache = review.ImageCache()
    prepared = cache._prepare(cache.key(path, SLIDE_PX, 1, 1, mode, rotate))
    np.testing.assert_array_equal(np.asarray(prepared), np.asarray(shown))
    cache.close()
    exported, note = slide_picture(path, graph_path, tmp_path / "slides", mode, rotate)
    assert not note
    with Image.open(exported) as im:
        np.testing.assert_array_equal(np.asarray(shown), np.asarray(im))
    deck = sp.build_slides([([exported], f"{mode}; rotate={rotate}")],
                           tmp_path / "orientation.pptx")
    shape = Presentation(deck).slides[0].shapes[0]
    with Image.open(BytesIO(shape.image.blob)) as embedded:
        np.testing.assert_array_equal(np.asarray(embedded), np.asarray(shown))
    if mode == "fit":
        # Native montage corners stay ordered at radically different boxes.
        for box in [(2400, 1000), (4000, 700), (1000, 2400)]:
            chosen = ss.best_montage(pieces, box)
            assert chosen["montage"] is montage
            assert chosen["montage"].getpixel((20, 20)) == (45,)*3
            with Image.open(path) as sheet:
                resized = review.fit_sheet(sheet, box, mode, rotate)
            gsize = (graph_h if rotate else graph).size
            lay = ss.layout(dict(montage=montage.size, graph=gsize), box)
            x0, y0, x1, y1 = lay.boxes["montage"]
            for fx, fy, tone in [(0.25, 0.25, 45), (0.75, 0.25, 105),
                                  (0.25, 0.75, 165), (0.75, 0.75, 225)]:
                at = (round(x0+(x1-x0)*fx), round(y0+(y1-y0)*fy))
                assert resized.getpixel(at) == (tone,)*3
    assert path.read_bytes() == before
