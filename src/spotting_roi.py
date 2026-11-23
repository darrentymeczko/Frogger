"""Interior ROI geometry in stored-photo coordinates, independent of display.

The boundary is a radial intensity edge relative to nearby agar. It is not a
segmentation of individual colonies. Unresolved, clipped, or irregular outlines
are explicitly reported; no extrapolated outline is certified as an interior.
"""
from dataclasses import dataclass, asdict
import math

LEGACY_METHOD = "fixed-oval-v23"
INTERIOR_METHOD = "centered-interior-v1"
ANGLES = tuple(k * (2 * math.pi / 72) for k in range(72))


@dataclass(frozen=True)
class ROISettings:
    method: str = INTERIOR_METHOD
    inward_margin: float = 0.15  # fraction of detected safe radius
    min_pixels: int = 25

    def __post_init__(self):
        if self.method not in (LEGACY_METHOD, INTERIOR_METHOD):
            raise ValueError(f"Unknown measurement method: {self.method}")
        if not math.isfinite(self.inward_margin) or not 0 <= self.inward_margin < 1:
            raise ValueError("ROI inward margin must be a fraction in [0, 1)")
        if self.min_pixels < 1 or int(self.min_pixels) != self.min_pixels:
            raise ValueError("Minimum interior pixels must be a positive integer")

    def to_dict(self):
        return asdict(self)


def settings_from_config(cfg, *, legacy_if_missing=False):
    value = cfg.get("measurement", {})
    return ROISettings(**value) if value else ROISettings(
        method=LEGACY_METHOD if legacy_if_missing else INTERIOR_METHOD,
        inward_margin=0.0 if legacy_if_missing else 0.15)


def detect_boundaries(image, centers, base_radius):
    """72 ray distances about each unchanged detected centre, plus reasons.

    Along each smoothed ray, find the dominant contiguous interval of growth,
    in brightness and in texture, and keep the further of the two;
    this retains a peripheral deposit as the boundary, rather than mistaking
    its dim centre for missing growth. A missing ray or an edge at the search
    limit is unresolved. A >35% radial spread is marked irregular.
    """
    import numpy as np
    angles = np.asarray(ANGLES)
    centers = np.asarray(centers)
    flat = centers.reshape(-1, 2)
    distances = np.linalg.norm(flat[:, None] - flat[None, :], axis=2)
    distances[distances == 0] = np.inf
    pitch = float(np.min(distances)) if len(flat) > 1 else 4 * base_radius
    limit = min(1.7 * base_radius, 0.47 * pitch)
    rs = np.arange(0, limit + 0.5, 0.5)
    boundaries = np.full((*centers.shape[:2], len(ANGLES)), np.nan)
    reasons = np.full(centers.shape[:2], "", dtype="U64")
    for index in np.ndindex(centers.shape[:2]):
        cy, cx = centers[index]
        reach = int(np.ceil(limit)) + 3
        x0, x1 = int(cx) - reach, int(cx) + reach + 1
        y0, y1 = int(cy) - reach, int(cy) + reach + 1
        if x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
            reasons[index] = "clipped_boundary"
            continue
        patch = np.asarray(image[y0:y1, x0:x1], dtype=float)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        rr = np.hypot(yy - cy, xx - cx)
        agar = (rr > limit * 0.92) & (rr <= limit)
        # Brightness finds dense growth, but on a dilute spot it stops at the
        # dense middle, and a faint spot of tiny colonies has no brightness
        # edge at all. Both are plainly textured out to the droplet's edge, so
        # each ray takes whichever signal reaches further.
        # The texture window straddling an edge reads half a window outside it.
        found = []
        # A dilute spot's microcolonies sit in a ring at the droplet's edge,
        # apart from its dense middle, so texture takes the outermost interval.
        for signal, floor, shift, outermost in (
                (patch, 2.0, 0, False),
                (_texture(patch), 0.5, TEXTURE_WINDOW // 2, True)):
            got = _ray_edges(signal, agar, yy-cy, xx-cx, cy-y0, cx-x0,
                             angles, rs, base_radius, floor, outermost)
            if got is not None:
                found.append(got - shift)
        if not found:
            reasons[index] = "unresolved_boundary"
            continue
        edges = boundaries[index]
        edges[:] = np.fmax.reduce(found)
        # A pinned spot is a disc, but single rays stop early at gaps between
        # microcolonies or jump to a stray speckle, which made jagged outlines.
        # The boundary is the circle the rays agree on; rays that disagree are
        # outliers, and too many of them means the edge is not a clean disc.
        fitted, inliers = _fit_circle(angles, edges)
        if fitted is None:
            edges[:] = np.nan
            reasons[index] = "unresolved_boundary"
            continue
        boundaries[index] = fitted
        if inliers < 0.8 * len(angles) or (fitted.max()-fitted.min()) / np.median(fitted) > 0.35:
            reasons[index] = "irregular_boundary"
    return boundaries, reasons


TEXTURE_WINDOW = 7      # px; local standard deviation window for texture


def _texture(patch):
    """Local standard deviation: high over a field of colonies, low on agar."""
    import numpy as np
    from scipy.ndimage import uniform_filter
    mean = uniform_filter(patch, TEXTURE_WINDOW)
    return np.sqrt(np.maximum(uniform_filter(patch*patch, TEXTURE_WINDOW) - mean*mean, 0))


def _ray_edges(signal, agar, dy, dx, cy, cx, angles, rs, base_radius, floor,
               outermost=False):
    """Edge distance along each ray where `signal` stops exceeding local agar,
    or None if the spot does not stand out. `floor` is the smallest excess, in
    the signal's own units, that counts as growth. Each ray ends at its
    strongest interval, or with `outermost` at its furthest one."""
    import numpy as np
    from scipy.ndimage import map_coordinates, gaussian_filter1d
    # Fit a local agar plane at the outside of the search disc to reject
    # lighting slopes.
    A = np.column_stack((np.ones(agar.sum()), dy[agar], dx[agar]))
    coef, *_ = np.linalg.lstsq(A, signal[agar], rcond=None)
    residual = signal - (coef[0] + coef[1]*dy + coef[2]*dx)
    noise = 1.4826 * np.median(np.abs(residual[agar] - np.median(residual[agar])))
    profiles = map_coordinates(residual,
        [cy + np.sin(angles[:, None])*rs,
         cx + np.cos(angles[:, None])*rs], order=1, mode="nearest")
    # A colony edge is supported across neighbouring rays and a sustained
    # radial interval. Isolated bright agar pixels outside that interval
    # must not move the edge to the last above-threshold sample.
    profiles = gaussian_filter1d(profiles, 1.0, axis=0, mode="wrap")
    profiles = gaussian_filter1d(profiles, max(1.5, .025*base_radius/.5), axis=1)
    peak = float(np.percentile(profiles.max(axis=1), 50))
    threshold = max(floor, 3 * noise, 0.15 * peak)
    if peak <= threshold:
        return None
    edges = np.full(len(angles), np.nan)
    for k, profile in enumerate(profiles):
        above = profile > threshold
        starts = np.flatnonzero(np.diff(np.r_[False,above].astype(int)) == 1)
        ends = np.flatnonzero(np.diff(np.r_[above,False].astype(int)) == -1)
        # Bridge short internal gaps between microcolonies, but never
        # extrapolate the end of a run. Require substantial positive
        # support, so scattered agar speckles cannot chain into a colony.
        merged = []
        for a,b in zip(starts,ends):
            if merged and a-merged[-1][1]-1 <= .12*base_radius/.5:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a,b))
        runs = [(a,b) for a,b in merged
                if above[a:b+1].mean() >= .60
                if b-a+1 >= max(4, .08*base_radius/.5)]
        if not runs:
            continue
        # Integrated excess distinguishes the supported colony/ring from
        # distant noise. A hollow bright ring remains a valid interval.
        a,j = runs[-1] if outermost else max(
            runs, key=lambda ab: np.sum(profile[ab[0]:ab[1]+1]-threshold))
        if j >= len(rs)-2:
            continue
        edges[k] = rs[j] + 0.5 * (profile[j]-threshold) / (profile[j]-profile[j+1])
    return edges


def _fit_circle(angles, edges):
    """Robust circle through the ray edges, as distances along each ray.

    Returns (radii along the 72 rays to the fitted circle, inlier ray count),
    or (None, 0) when fewer than half the rays found an edge, or the circle
    does not contain the lattice centre the rays start from.
    """
    import numpy as np
    ok = np.isfinite(edges)
    if ok.sum() < len(angles) / 2:
        return None, 0
    x, y = edges * np.cos(angles), edges * np.sin(angles)
    keep = ok.copy()
    for _ in range(4):
        # Algebraic (Kasa) fit: x^2 + y^2 + Dx + Ey + F = 0.
        A = np.column_stack((x[keep], y[keep], np.ones(keep.sum())))
        (D, E, F), *_ = np.linalg.lstsq(A, -(x[keep]**2 + y[keep]**2), rcond=None)
        cx, cy = -D / 2, -E / 2
        R2 = cx*cx + cy*cy - F
        if R2 <= 0:
            return None, 0
        R = np.sqrt(R2)
        resid = np.abs(np.hypot(x - cx, y - cy) - R)
        spread = 1.4826 * np.median(resid[keep])
        new = ok & (resid <= max(2.5 * spread, 0.05 * R))
        if new.sum() < len(angles) / 2:
            return None, 0
        if (new == keep).all():
            break
        keep = new
    if np.hypot(cx, cy) >= R:
        return None, 0
    # Distance from the lattice centre to the circle along each ray.
    ux, uy = np.cos(angles), np.sin(angles)
    b = ux*cx + uy*cy
    return b + np.sqrt(b*b - (cx*cx + cy*cy) + R2), int(keep.sum())


def configure_interior(grid, settings, rows=None):
    """Set one shared centred radius for spots AND background on this plate.

    Robust sizing excludes extreme small outliers from sizing the whole plate,
    but explicitly flags every spot the resulting circle does not fit inside.
    Values are mean grey, never integrated intensity or area-scaled ratios.
    """
    import numpy as np
    grid.roi_settings = settings
    if settings.method == LEGACY_METHOD:
        return
    boundary = grid.boundary_radii
    reasons = grid.boundary_quality.copy()
    safe = np.min(boundary, axis=-1)
    valid = np.isfinite(safe) & (reasons == "")
    selected = np.zeros(grid.shape, bool)
    selected[[r-1 for r in rows if 1 <= r <= grid.shape[0]] if rows else slice(None)] = True
    sizes = safe[valid & selected]
    radius = grid.measure_radius
    if sizes.size:
        radius = min(radius, _robust_min(sizes))
    # Every spot is the same pinned droplet, so dilute spots share the
    # concentrated spots' footprint; they only have fewer colonies, whose
    # ragged edges let one spot shrink a whole level (16h GLU P1 L2: 73->44px).
    # The cleanly traced concentrated rows bound the size: each level still
    # fits its own smallest clean spot, so one slightly smaller spot is read
    # inside its edge, but no level may shrink far below the concentrated
    # one. A level with no clean edge takes that size as is. Traced edges
    # then replace the coarser texture size, so that an empty level and the
    # concentrated level get the same circle.
    anchor = _anchor_radius(safe, valid, rows)
    if anchor is not None:
        radius = _bounded(sizes.min() if sizes.size else anchor, anchor)
        # The circle no longer depends on this spot's own edge, so a faint
        # spot too sparse to trace is measured as placed, not a quality issue.
        reasons[reasons == "unresolved_boundary"] = ""
    grid.measure_radius = radius * (1-settings.inward_margin)
    insufficient = valid & (safe*(1-settings.inward_margin) + 1e-8 < grid.measure_radius)
    reasons[insufficient] = "insufficient_interior"
    if np.pi * grid.measure_radius**2 < settings.min_pixels:
        reasons[:] = "insufficient_interior"
    grid.interior_quality = reasons


ANCHOR_TRIM = 0.25      # largest shrink of the anchor size any level may make;
                        # a slightly smaller spot (16-23% on Set01) still sizes
                        # its level, a collapse onto a dense core (~45%) cannot
ANCHOR_MIN_CLEAN = 3    # cleanly traced spots the top rows need to set the anchor


def _robust_min(sizes):
    """Smallest size, ignoring extreme small outliers (3 MADs below median)."""
    import numpy as np
    median = np.median(sizes)
    mad = max(1.4826 * np.median(np.abs(sizes-median)), 0.05 * median)
    return float(sizes[sizes >= median - 3*mad].min())


def _anchor_radius(safe, valid, rows):
    """The size the most concentrated level gets on its own, or None.

    That level is the top row of each replicate block the scored rows sit in:
    rows (3, 6) are anchored by (1, 4). The fullest growth has the clearest
    edges, and the replicates of one level can differ by 25% in spot size
    (46h K-OAc P1), so they are taken together rather than by size.
    """
    if not rows:
        return None
    top = [r - min(rows) for r in rows if 0 <= r - min(rows) < safe.shape[0]]
    sizes = safe[top][valid[top]]
    if sizes.size < ANCHOR_MIN_CLEAN:
        return None
    # Bounded by its own typical spot, exactly as it bounds the other levels.
    return _bounded(float(sizes.min()), _robust_min(sizes))


def _bounded(size, anchor):
    """`size`, kept within ANCHOR_TRIM below `anchor` and never above it."""
    return min(max(float(size), (1 - ANCHOR_TRIM) * anchor), anchor)


def boundary_xy(center, radii):
    import numpy as np
    cy, cx = center
    return np.column_stack((cx + radii*np.cos(ANGLES), cy + radii*np.sin(ANGLES)))
