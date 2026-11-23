"""Linking a table row to its spot on the sheet, and its ROI to the picture.

The sheet records where every spot of its montage sits (`spotting_sheet.
view_spots`), and each table row carries the ROI it was measured with, in the
photograph's pixels: a centred circle (`roi_x/roi_y/roi_width`). The montage is the photograph
resampled through an affine fit of the grid -- tilt removed, possibly turned to
the experiment's orientation -- and laid out in blocks with gaps between them.

So no single map carries photo pixels onto the montage. Each spot's own
neighbours give it one: the other spots on the same plate are known in both
the photograph and the montage, and the closest of them, on the same block,
fix the local scale and turn. Neighbours across a block gap or a label lane
are farther apart in the montage than in the photo, so only those at the
smallest montage-to-photo ratio are used.

Pure Python, no Tk and no numpy: the review window imports it on any machine.
"""

from __future__ import annotations

import math

#: Neighbours considered when fitting a spot's local map.
NEIGHBOURS = 8
#: A neighbour is on the same block when its montage/photo distance ratio is
#: within this factor of the smallest one seen.
SAME_BLOCK = 1.15
#: Points on the drawn ROI circle.
CIRCLE_POINTS = 48


def rep_no(replicate) -> int:
    digits = "".join(ch for ch in str(replicate) if ch.isdigit())
    return int(digits) if digits else 0


def row_key(row: dict) -> tuple:
    """(sample slot, replicate number): what a row and a spot share."""
    return int(row["strain_col"]), rep_no(row["replicate"])


def spot_key(spot: dict) -> tuple:
    return int(spot["slot"]), int(spot["rep"])


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def photo_centre(row: dict):
    """The ROI's centre in the photograph, or None if it was not recorded."""
    x, y = _num(row.get("roi_x")), _num(row.get("roi_y"))
    w, h = _num(row.get("roi_width")), _num(row.get("roi_height"))
    if None in (x, y, w, h):
        return None
    return x + w / 2, y + h / 2


def spot_at(spots: list, x: float, y: float):
    """The spot whose cell contains (x, y), in the spots' own pixels."""
    best, best_d = None, None
    for s in spots:
        d = math.hypot(s["x"] - x, s["y"] - y)
        if d <= s["r"] and (best_d is None or d < best_d):
            best, best_d = s, d
    return best


def _turn(v: tuple, degrees: int) -> tuple:
    """`v` (image coordinates, y down) turned anticlockwise on screen."""
    dx, dy = v
    return {0: (dx, dy), 90: (dy, -dx), 180: (-dx, -dy),
            270: (-dy, dx)}[int(degrees) % 360]


def local_map(row: dict, rows: list, spots: dict):
    """2x2 matrix taking photo offsets to montage-map offsets near `row`.

    `spots` maps row_key -> spot. None when the plate has too few spots known
    in both to tell scale and turn.
    """
    here = photo_centre(row)
    spot = spots.get(row_key(row))
    if here is None or spot is None:
        return None
    pairs = []
    for other in rows:
        if other is row or other.get("plate") != row.get("plate"):
            continue
        p, s = photo_centre(other), spots.get(row_key(other))
        if p is None or s is None:
            continue
        d = (p[0] - here[0], p[1] - here[1])
        e = (s["mx"] - spot["mx"], s["my"] - spot["my"])
        dn = math.hypot(*d)
        if dn > 1e-6:
            pairs.append((dn, d, e, math.hypot(*e) / dn))
    pairs = sorted(pairs)[:NEIGHBOURS]
    if not pairs:
        return None
    base = min(ratio for *_, ratio in pairs)
    kept = [(d, e) for _, d, e, ratio in pairs if ratio <= SAME_BLOCK * base]

    # Least squares e = M d over the kept neighbours, when they span the plane.
    sxx = sum(d[0] * d[0] for d, _ in kept)
    sxy = sum(d[0] * d[1] for d, _ in kept)
    syy = sum(d[1] * d[1] for d, _ in kept)
    det = sxx * syy - sxy * sxy
    if det > 1e-9 * max(sxx * syy, 1e-12):
        inv = ((syy / det, -sxy / det), (-sxy / det, sxx / det))
        cols = []
        for axis in (0, 1):
            bx = sum(d[0] * e[axis] for d, e in kept)
            by = sum(d[1] * e[axis] for d, e in kept)
            cols.append((inv[0][0] * bx + inv[0][1] * by,
                         inv[1][0] * bx + inv[1][1] * by))
        return ((cols[0][0], cols[0][1]), (cols[1][0], cols[1][1]))
    # One direction only: a similarity from the nearest kept neighbour (the
    # montage turns a plate, it never mirrors one).
    d, e = kept[0]
    scale = math.hypot(*e) / math.hypot(*d)
    angle = math.atan2(e[1], e[0]) - math.atan2(d[1], d[0])
    c, s_ = math.cos(angle) * scale, math.sin(angle) * scale
    return ((c, -s_), (s_, c))


def outline(row: dict, rows: list, spots: list) -> "dict | None":
    """What to draw for `row`, in the view's pixels.

    {"centre": (x, y), "r": half the spot pitch, "roi": [(x, y), ...] or []}.
    None when the row's spot is not on the sheet. The ROI is left empty when
    the row carries none, or the plate gives too little to map it with.
    """
    by_key = {spot_key(s): s for s in spots}
    spot = by_key.get(row_key(row))
    if spot is None:
        return None
    out = {"centre": (spot["x"], spot["y"]), "r": spot["r"], "roi": []}
    m = local_map(row, rows, by_key)
    here = photo_centre(row)
    if m is None or here is None:
        return out

    def place(px: float, py: float) -> tuple:
        d = (px - here[0], py - here[1])
        e = (m[0][0] * d[0] + m[0][1] * d[1], m[1][0] * d[0] + m[1][1] * d[1])
        tx, ty = _turn(e, spot.get("turn", 0))
        return spot["x"] + tx * spot["k"], spot["y"] + ty * spot["k"]

    radius = (_num(row.get("roi_width")) or 0) / 2
    if radius > 0:
        out["roi"] = [place(here[0] + radius * math.cos(a),
                            here[1] + radius * math.sin(a))
                      for a in (2 * math.pi * i / CIRCLE_POINTS
                                for i in range(CIRCLE_POINTS))]
    return out
