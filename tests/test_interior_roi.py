"""Scientific regression checks: rings, geometry, cache and display isolation."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import spotting_quant as sq
import spotting_batch as sb
from spotting_roi import ROISettings, detect_boundaries, configure_interior, LEGACY_METHOD


def ring_plate():
    centers = np.array([[(85.25 + 95*r, 85.75 + 95*c) for c in range(8)] for r in range(6)])
    yy, xx = np.mgrid[:660, :850]
    image = np.full(yy.shape, 30., dtype=float)
    for r, c in np.ndindex(6, 8):
        cy, cx = centers[r, c]
        radius = np.hypot(yy+.5-cy, xx+.5-cx)
        image[radius < 31] = 80 + r + 3*c
        image[(radius >= 27) & (radius < 31)] = 230
    grid = sq.Grid(centers, 31, 31, 62, (330, 425), 600, plate_known=False)
    return image.astype(np.uint8), grid


def detection(image, grid):
    grid.boundary_radii, grid.boundary_quality = detect_boundaries(image, grid.centers, grid.measure_radius)
    return dict(img8=image, grid=grid, bg_centers=[(40, 40), (40, 140), (40, 240), (40, 340), (40, 440)],
                ball_radius=82, small=None, ds=1)


def test_bright_ring_excluded_centered_and_background_same_size():
    image, grid = ring_plate()
    original = image.copy()
    detection(image, grid)
    old = sq.measure_plate(image, grid, [(40,40)])
    configure_interior(grid, ROISettings(inward_margin=.2), (1,2,3,4,5,6))
    got = sq.measure_plate(image, grid, [(40,40)])
    expected = np.array([[80+r+3*c for c in range(8)] for r in range(6)])
    np.testing.assert_allclose(got.raw, expected)
    assert np.all(old.raw > expected + 20), "Fixture must expose peripheral ring bias"
    assert not got.rim_flag.any()
    np.testing.assert_array_equal(image, original)
    np.testing.assert_allclose(got.roi_boxes[..., :2] + got.roi_boxes[..., 2:]/2,
                               grid.centers[..., ::-1], atol=1e-12)
    assert got.bg_mean == 30
    assert max(abs(got.n_px.ravel() - got.bg_n_px[0])) < .01 * got.bg_n_px[0]
    # Same continuous geometry; subpixel centres can differ by a few sampled pixels.
    np.testing.assert_allclose(got.roi_boxes[..., 2:], 2*grid.measure_radius)


def test_small_irregular_unresolved_and_minimum_area_flagged():
    image, grid = ring_plate()
    detection(image, grid)
    grid.boundary_radii[0,0] = 3
    grid.boundary_radii[0,1] = np.linspace(10,31,72)
    grid.boundary_quality[0,1] = "irregular_boundary"
    grid.boundary_radii[0,2] = np.nan
    grid.boundary_quality[0,2] = "unresolved_boundary"
    configure_interior(grid, ROISettings(), (1,2))
    got = sq.measure_plate(image, grid, [(40,40)])
    assert got.quality[0,0] == "insufficient_interior"
    # Untraceable spots are sized from the top level, so carry no note.
    assert list(got.quality[0,1:3]) == ["irregular_boundary", ""]
    # Boundary notes do not drop a spot: blank (no-growth) spots have no edge.
    assert not got.rim_flag.any()
    assert np.isfinite(got.raw).all(), "Keep diagnostic measurements"
    configure_interior(grid, ROISettings(inward_margin=.999), (1,2))
    got = sq.measure_plate(image, grid, [(40,40)])
    assert (got.quality == "insufficient_interior").all()
    assert got.rim_flag.all(), "Too few pixels to average is unmeasurable"


def test_dilute_levels_sized_from_the_concentrated_rows():
    image, grid = ring_plate()
    detection(image, grid)
    base = grid.measure_radius
    # Level 2 (rows 2, 5): traced edges hug a small dense core.
    grid.boundary_radii[[1, 4]] = 15
    # Level 3 (rows 3, 6): no growth, so no edge anywhere.
    grid.boundary_radii[[2, 5]] = np.nan
    grid.boundary_quality[[2, 5]] = "unresolved_boundary"
    sized = {}
    for rows in [(1, 4), (2, 5), (3, 6)]:
        g = replace(grid, measure_radius=base)
        configure_interior(g, ROISettings(inward_margin=0), rows)
        sized[rows] = g.measure_radius
    assert sized[(1, 4)] == pytest.approx(31, abs=1)
    assert sized[(2, 5)] == pytest.approx(.75 * sized[(1, 4)])
    assert sized[(3, 6)] == sized[(1, 4)]
    # Sized from level 1, an untraceable faint spot is not a quality issue.
    assert (g.interior_quality[[2, 5]] == "").all()
    # One slightly smaller spot on a level still sizes that level, so the
    # circle stays inside it rather than copying level 1.
    grid.boundary_radii[[1, 4]] = np.nan
    grid.boundary_quality[[1, 4]] = "unresolved_boundary"
    grid.boundary_radii[1, :4] = 31
    grid.boundary_radii[1, 3] = 26
    grid.boundary_quality[1, :4] = ""
    g = replace(grid, measure_radius=base)
    configure_interior(g, ROISettings(inward_margin=0), (2, 5))
    assert g.measure_radius == pytest.approx(26)
    assert g.interior_quality[1, 3] == ""


def test_contaminant_beside_a_spot_does_not_pull_its_centre():
    yy, xx = np.mgrid[:400, :400]
    image = np.full(yy.shape, 100, np.uint8)
    centers = np.array([[(100. + 200*r, 100. + 200*c) for c in range(2)] for r in range(2)])
    for cy, cx in centers.reshape(-1, 2):
        image[np.hypot(yy-cy, xx-cx) < 50] = 130
    # A small, much brighter colony overlapping the first spot's edge.
    image[np.hypot(yy-140, xx-145) < 18] = 220
    got = sq.refine_centers_fullres(image, centers + 6, 50)
    np.testing.assert_allclose(got[0, 0], centers[0, 0], atol=2)


def test_real_boundary_search_rejects_speckles_but_flags_notches_and_blank_spots():
    image, grid = ring_plate()
    rng = np.random.default_rng(13)
    image = np.clip(image.astype(float) + rng.normal(0, .7, image.shape), 0, 255)
    # Bright disconnected outer fragments cannot become a coherent boundary.
    for cy,cx in grid.centers.reshape(-1,2):
        image[int(cy+37),int(cx+4)] = 245
        image[int(cy-8),int(cx+38)] = 245
    yy,xx = np.mgrid[:image.shape[0],:image.shape[1]]
    cy,cx=grid.centers[0,0]
    image[(yy<cy)&(xx>cx)&(np.hypot(yy-cy,xx-cx)<35)] = 30
    cy,cx=grid.centers[0,1]
    image[np.hypot(yy-cy,xx-cx)<35] = 30
    radii,quality=detect_boundaries(image,grid.centers,31)
    assert quality[0,0] in ("irregular_boundary","unresolved_boundary")
    assert quality[0,1] == "unresolved_boundary"
    assert (quality[1:] == "").all()
    assert np.max(radii[1:]) < 34, "Distant isolated speckles must not move the edge"


@pytest.mark.parametrize("margin", [-.1, 1., float("nan")])
def test_invalid_margin_rejected(margin):
    with pytest.raises(ValueError):
        ROISettings(inward_margin=margin)


def test_historical_imagej_calibration_keeps_its_original_oval_method():
    import calibrate
    opts=calibrate.build_opts({"measure_diameter_px":"52"})
    assert opts.roi_settings.method == LEGACY_METHOD
    assert opts.roi_settings.inward_margin == 0
    assert opts.measure_diameter == 52


def test_cache_roundtrip_display_recalculation_and_statistics(tmp_path, monkeypatch):
    from results_review.gui import imaging
    image, grid = ring_plate()
    path = tmp_path / "source.png"
    Image.fromarray(image).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # Exercise real source loading + boundary detection each recalculation;
    # the expensive lattice locator is separately exercised on real photographs.
    monkeypatch.setattr(sq, "detect_grid", lambda *a, **kw: replace(grid))
    monkeypatch.setattr(sq, "background_gap_centers", lambda g: [(40,40), (40,140), (40,240), (40,340), (40,440)])
    opts = sq.MeasureOptions(bg_mode="python", quant_rows=(1,4), inward_margin=.2)
    ref = sb.PhotoRef(path, 1, 1, "ring")
    first = sb.measure(ref, opts, tmp_path/"cache1", row_sets=[(1,4)])
    saved = sb.measure(ref, opts, tmp_path/"cache1", row_sets=[(1,4)])
    monkeypatch.setattr(imaging.ImageTk, "PhotoImage", lambda im: im.copy())
    cache = imaging.ImageCache()
    plain = cache.get(path, (850,660), 1,1)
    adjusted = cache.get(path, (850,660), 1.9,.4)
    assert not np.array_equal(plain, adjusted)
    second = sb.measure(ref, opts, tmp_path/"cache2", row_sets=[(1,4)])
    for other in (saved, second):
        np.testing.assert_array_equal(first.centers, other.centers)
        np.testing.assert_array_equal(first.net, other.net)
        np.testing.assert_array_equal(first.bg_samples, other.bg_samples)
        for key in ("raw", "n_px", "roi_boxes", "boundary_radii", "quality"):
            np.testing.assert_equal(first.provenance[key], other.provenance[key])
    frames = [sb.build_tidy("1|ring", [p], ["WT"]+[f"mut{i}" for i in range(7)], 1,
                            {"mode":"combo", "choice":"least"}) for p in (first,saved,second)]
    for frame in frames[1:]:
        pd.testing.assert_frame_equal(frames[0], frame)
        pd.testing.assert_frame_equal(sq._paired_ttests(frames[0],1,"ring"), sq._paired_ttests(frame,1,"ring"))
    frames[0].to_csv(tmp_path/"saved-results.csv", index=False)
    reopened = pd.read_csv(tmp_path/"saved-results.csv")
    assert set(reopened.measurement_method) == {"centered-interior-v1"}
    np.testing.assert_allclose(reopened.relative_growth, frames[0].relative_growth, equal_nan=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert sb._cache_key(path, opts) != sb._cache_key(path, replace(opts, inward_margin=.3))
    assert sb._cache_key(path, opts) != sb._cache_key(path, replace(opts, roi_method=LEGACY_METHOD))


def test_generated_images_and_preview_arrays_cannot_be_analysis(tmp_path):
    from PIL.PngImagePlugin import PngInfo
    for key in ("spotting-sheet", "spotting-spots", "spotting-display"):
        info = PngInfo(); info.add_text(key, "generated")
        path = tmp_path / f"{key}.png"
        Image.new("L", (100,100)).save(path, pnginfo=info)
        with pytest.raises(ValueError, match="original photograph"):
            sq.load_gray8(path)
    image, grid = ring_plate()
    det = detection(image, grid)
    with pytest.raises(ValueError, match="previews"):
        sq.analyze_image_multi(Path("unused"), sq.MeasureOptions(), [(1,4)],
                               detection=det, proc=image[::2,::2])
