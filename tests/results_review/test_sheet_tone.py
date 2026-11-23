"""Brightness and contrast change the photographed plate, and nothing else.

The sliders are there to make faint spots readable. They used to adjust the
whole sheet, so the graph beside the plate washed out and the amber outline
marking the quantified row changed colour with them.
"""

import pytest

Image = pytest.importorskip("PIL.Image")

from results_review import export  # noqa: E402,F401  (puts the engine on the path)
from results_review.gui.browser import BRIGHTNESS_RANGE, CONTRAST_RANGE  # noqa: E402
from results_review.gui.imaging import ImageCache  # noqa: E402

import spotting_sheet as ss  # noqa: E402

PLATE_GREY = (60, 60, 60)
AMBER = (255, 170, 0)
GRAPH = (30, 90, 200)


def sheet(tmp_path):
    """A montage of grey plate with an amber outline in it, beside a graph."""
    montage = Image.new("RGB", (600, 400), (10, 10, 10))
    spots = []
    for i in range(3):
        x = 100 + 200 * i
        montage.paste(PLATE_GREY, (x - 40, 60, x + 40, 140))
        spots.append({"x": x, "y": 100, "r": 40, "strain": "ABC"[i],
                      "slot": i + 1, "rep": 1, "level": 0})
    montage.paste(AMBER, (20, 200, 580, 206))           # the "quantified" box
    graph = Image.new("RGB", (900, 600), "white")
    graph.paste(GRAPH, (100, 100, 800, 500))
    extra = {"level": 0, "montage_map": {"size": [600, 400], "spots": spots}}
    pieces = {"montage": montage, "graph": graph}
    im, lay = ss.arrange(pieces, ss.SHEET_BOX)
    return ss.save(im, lay, tmp_path / "tone.png", extra=extra, panels=pieces)


def shown(cache, path, brightness, contrast):
    from PIL import ImageTk
    return ImageTk.getimage(cache.get(path, (1500, 700), brightness, contrast)).convert("RGB")


def test_only_the_plate_is_brightened(tk_root, tmp_path):
    path = sheet(tmp_path)
    cache = ImageCache()
    try:
        plain = shown(cache, path, 1.0, 1.0)
        bright = shown(cache, path, 3.0, 2.0)
        spots = cache.spots(cache.key(path, (1500, 700), 3.0, 2.0))
    finally:
        cache.close()
    assert plain.size == bright.size

    def where(colour):
        return [(x, y) for x in range(0, plain.width, 3)
                for y in range(0, plain.height, 3)
                if all(abs(a - b) <= 6 for a, b in zip(plain.getpixel((x, y)), colour))]

    spot = (round(spots[0]["x"]), round(spots[0]["y"]))
    assert sum(bright.getpixel(spot)) > 1.5 * sum(plain.getpixel(spot))
    graph, amber = where(GRAPH), where(AMBER)
    assert graph and amber
    for xy in graph + amber:
        assert bright.getpixel(xy) == plain.getpixel(xy), xy


def test_a_picture_without_recorded_pieces_is_adjusted_whole(tk_root, tmp_path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (200, 100), (60, 60, 60)).save(path)
    cache = ImageCache()
    try:
        assert shown(cache, path, 2.0, 1.0).getpixel((150, 50))[0] > 100
    finally:
        cache.close()


def test_the_sliders_reach_five_times():
    assert BRIGHTNESS_RANGE[1] == CONTRAST_RANGE[1] == 5.0
