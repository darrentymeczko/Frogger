"""Stored photo pixels, the coordinate system used by detection and saved ROIs.

EXIF orientation is deliberately not a second transform of an experiment's
grid. Interpret a camera turn with the experiment's explicit Photo orientation
setting. Changing this convention would invalidate existing ROI/cache geometry.
Pillow auto-orients TIFF (but not JPEG), so read TIFF through tifffile, exactly
as the measurement loader does. Never write back to a source photograph.
"""

from pathlib import Path


def pixel_size(path):
    """Stored (width, height), without decoding the photograph."""
    from PIL import Image

    with Image.open(path) as im:
        if im.format == "TIFF":
            return int(im.tag_v2[256]), int(im.tag_v2[257])
        return im.size


def open_pixels(path, draft=None):
    """A detached Pillow image in measurement coordinates, without EXIF turns.

    Optional JPEG draft decoding is for previews only. PNG sheet text metadata
    and filename survive so embedded native panels can still be recovered.
    Orientation-bearing EXIF/XMP is not propagated to display/export images.
    """
    from PIL import Image

    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import numpy as np
        import tifffile

        arr = tifffile.imread(path)
        # Pillow cannot represent 16-bit RGB; use the same high-byte reduction
        # as the measurement loader. Grayscale bit depth stays intact here.
        if arr.ndim == 3 and arr.dtype == np.uint16:
            arr = (arr >> 8).astype(np.uint8)
        out = Image.fromarray(arr)
    else:
        with Image.open(path) as im:
            if draft is not None:
                im.draft("RGB", draft)
            im.load()
            out = im.copy()
            out.info = {k: v for k, v in im.info.items()
                        if k not in ("exif", "xmp", "XML:com.adobe.xmp")}
    out.filename = str(path)
    return out
