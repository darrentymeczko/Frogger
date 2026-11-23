"""Bounded display caches and two Pillow workers for review sheets.

Decoded sources, fitted/tone-adjusted Pillow images and Tk images have separate
byte budgets as well as entry limits. Only the foreground image and its two
nearest neighbors are requested. Workers never construct or destroy Tk images;
SheetView polls a shared Future and publishes it only for the current request.

Pillow is used when it is there (it is in `requirements.txt`) because it resizes
smoothly; Tk's own PhotoImage can only `subsample` by whole numbers, which makes
spot edges crawl. Both paths are kept, since browsing results is exactly the
situation where somebody may not have the science stack installed.

Tk images must be referenced from Python or they are garbage collected and the
widget silently goes blank, which is why the cache holds them rather than the
widgets doing it ad hoc.
"""

from __future__ import annotations

import tkinter as tk
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from pathlib import Path

try:                                            # pragma: no cover - env dependent
    from PIL import Image, ImageEnhance, ImageTk
    HAVE_PIL = True
except Exception:                               # pragma: no cover - env dependent
    Image = ImageEnhance = ImageTk = None
    HAVE_PIL = False


def fit_sheet(im, box: tuple[int, int], mode: str = "fit",
              rotate: bool = False, with_map: bool = False):
    """`im` shown as `mode`, graph turned if `rotate`, and laid out to fill
    `box`, never enlarged.

    Raises ValueError, with a reason fit to show, when the sheet cannot be
    shown as `mode`. `with_map` returns (image, spots, plates): where each
    quantified spot landed and which boxes hold plate, or [] and None
    without the engine.
    """
    try:
        from ..export import fit_sheet as fit
    except Exception:                           # pragma: no cover - env dependent
        fit = None
    if fit is not None:
        return fit(im, box, mode, rotate, with_map=with_map)
    if mode != "fit" or rotate:
        raise ValueError("this view needs the measurement engine (src/)")
    scale = min(box[0] / im.width, box[1] / im.height, 1.0)
    if scale < 1.0:
        im = im.resize((max(1, int(im.width * scale)),
                        max(1, int(im.height * scale))), Image.LANCZOS)
    im = im.convert("RGB")
    return (im, [], None) if with_map else im


#: A pixel whose channels differ by more than this is an overlay drawn on the
#: plate (the amber outline), never the plate itself, which is grey. The same
#: test as spotting_sheet.OVERLAY_SAT.
OVERLAY_SAT = 30


def _adjust(im, brightness: float, contrast: float):
    if brightness != 1.:
        im = ImageEnhance.Brightness(im).enhance(brightness)
    if contrast != 1.:
        im = ImageEnhance.Contrast(im).enhance(contrast)
    return im


def _overlay_mask(im):
    """255 where `im` is coloured (an overlay), 0 where it is grey."""
    from PIL import ImageChops

    r, g, b = im.convert("RGB").split()
    spread = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b),
                                 ImageChops.darker(ImageChops.darker(r, g), b))
    return spread.point(lambda v: 255 if v > OVERLAY_SAT else 0)


class ImageCache:
    """Display-only LRUs keyed by source identity/stat, view, size and tone.

    Cache budgets exclude the one visible image and at most two active worker
    results/temporaries. Oversized entries are displayed without caching. Source
    decoding is guarded at 90 megapixels. No scientific frame/array is stored.
    """

    def __init__(self, limit: int = 48, *, decoded_bytes=96*1024**2,
                 rendered_bytes=32*1024**2, tk_bytes=32*1024**2) -> None:
        self.limit = limit
        self._items: "OrderedDict[tuple, tk.PhotoImage]" = OrderedDict()
        #: Why the last `get` returned None, when there is a reason worth
        #: showing (a view this sheet cannot provide); "" otherwise.
        self.last_error = ""
        self._lock = threading.RLock()
        self._decoded = OrderedDict()
        self._rendered = OrderedDict()
        self._budgets = (decoded_bytes, rendered_bytes, tk_bytes)
        self._pending = {}
        #: Where the spots and the plate are in a laid-out sheet, per layout
        #: (tone moves neither). Tiny, so kept for more layouts than images.
        self._maps = OrderedDict()
        self._epoch = 0
        self._versions = {}
        self._closed = False
        self._pool = None
        self._counts = dict(hits=0, decodes=0, renders=0, deduplicated=0,
                            evictions=0, submitted=0, rejected=0, layouts=0)

    @staticmethod
    def _bytes(im):
        if isinstance(im, tk.PhotoImage) or hasattr(im, 'tk'):
            return im.width() * im.height() * 4
        # Pillow RGB storage uses four bytes per pixel internally. Include PNG
        # text (embedded native sheet panels) as well as the pixel allocation.
        return im.width * im.height * max(4, len(im.getbands())) + sum(
            len(v.encode('utf-8')) if isinstance(v, str) else len(v)
            for v in im.info.values() if isinstance(v, (str, bytes)))

    @property
    def stats(self):
        with self._lock:
            return {**self._counts, 'decoded_bytes':sum(map(self._bytes,self._decoded.values())),
                    'rendered_bytes':sum(map(self._bytes,self._rendered.values())),
                    'tk_bytes':sum(map(self._bytes,self._items.values())),
                    'entries':len(self._items), 'in_flight':len(self._pending),
                    'budgets':self._budgets, 'max_in_flight':2}

    def _store(self, cache, key, im, budget):
        if self._bytes(im) > budget:
            return im
        cache[key] = im
        cache.move_to_end(key)
        while len(cache) > self.limit or sum(map(self._bytes,cache.values())) > budget:
            cache.popitem(last=False)
            self._counts['evictions'] += 1
        return im

    def key(self, path, box, brightness=1., contrast=1., mode='fit', rotate=False):
        path = Path(path).resolve()
        st = path.stat()
        with self._lock:
            stamp = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
                     self._epoch, self._versions.get(str(path), 0))
        return (str(path), stamp, max(1,int(box[0])), max(1,int(box[1])),
                round(max(.01,float(brightness)),2), round(max(.01,float(contrast)),2),
                mode, bool(rotate))

    def current(self, key):
        try:
            return self.key(key[0], key[2:4], *key[4:]) == key
        except OSError:
            return False

    def _prepare(self, key):
        """Worker-only Pillow work. No Tk objects or callbacks leave this lane."""
        from src.spotting_pixels import open_pixels
        path, stamp, w, h, brightness, contrast, mode, rotate = key
        base_key = (path,stamp,w,h,1.,1.,mode,rotate)
        with self._lock:
            base = self._rendered.get(base_key)
            if base is not None:
                self._rendered.move_to_end(base_key)
        if base is not None:
            return self._tone(base,brightness,contrast,self._plates(base_key))
        source_key = (path, stamp)
        with self._lock:
            im = self._decoded.get(source_key)
            if im is not None:
                self._decoded.move_to_end(source_key)
        if im is None:
            # Keep full stored pixels/metadata; JPEG draft would change previews.
            from src.spotting_pixels import pixel_size
            width, height = pixel_size(path)
            if width * height > 90_000_000:
                raise ValueError('Preview exceeds the 90 megapixel display limit')
            im = open_pixels(path)
            with self._lock:
                self._counts['decodes'] += 1
                if not self._closed and self.current(key):
                    self._store(self._decoded,source_key,im,self._budgets[0])
        # fit_sheet's operations return new images, without editing im's pixels.
        result, spots, plates = fit_sheet(im,(w,h),mode,rotate,with_map=True)
        with self._lock:
            self._maps[base_key] = (spots, plates)
            self._maps.move_to_end(base_key)
            while len(self._maps) > self.limit * 4:
                self._maps.popitem(last=False)
            self._counts['layouts'] += 1
            if not self._closed and self.current(key):
                self._store(self._rendered,base_key,result,self._budgets[1])
        return self._tone(result,brightness,contrast,plates)

    def _plates(self, base_key):
        with self._lock:
            found = self._maps.get(base_key)
        return found[1] if found else None

    def _tone(self, result, brightness, contrast, plates=None):
        """Brightness and contrast for the photographed plate only.

        `plates` are the boxes holding plate; the graph and the white around
        it are left exactly as drawn, and so are coloured overlays inside a
        plate box (the amber "quantified" outline): those pixels are put back
        after the adjustment. Without boxes -- an older sheet, an ordinary
        picture -- the whole image is adjusted, as it always was.
        """
        if brightness == 1. and contrast == 1.:
            pass
        elif plates is None:
            result = _adjust(result, brightness, contrast)
        else:
            result = result.copy()
            for box in plates:
                x0, y0, x1, y1 = (int(v) for v in box)
                x0, y0 = max(0, x0), max(0, y0)
                x1, y1 = min(result.width, x1), min(result.height, y1)
                if x1 <= x0 or y1 <= y0:
                    continue
                plate = result.crop((x0, y0, x1, y1))
                toned = _adjust(plate, brightness, contrast)
                toned.paste(plate, mask=_overlay_mask(plate))
                result.paste(toned, (x0, y0))
        result.info['spotting-display'] = 'preview only; never measurement input'
        with self._lock:
            self._counts['renders'] += 1
        return result

    def request(self, key):
        """Return a shared Future, or None for backpressure. At most two jobs.

        Foreground callers retry from Tk's timer. Prefetch only uses spare
        capacity; no unbounded executor queue or retained callback list.
        """
        from concurrent.futures import Future
        with self._lock:
            if self._closed:
                raise RuntimeError('Image loader is closed')
            if key in self._rendered:
                self._counts['hits'] += 1
                self._rendered.move_to_end(key)
                done = Future(); done.set_result(self._rendered[key]); return done
            if key in self._pending:
                self._counts['deduplicated'] += 1
                return self._pending[key]
            if len(self._pending) >= 2:
                self._counts['rejected'] += 1
                return None
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='review-image')
            future = self._pool.submit(self._prepare,key)
            self._pending[key] = future
            self._counts['submitted'] += 1
            def finished(f):
                with self._lock:
                    self._pending.pop(key,None)
                    if not f.cancelled() and f.exception() is None and not self._closed and self.current(key):
                        self._store(self._rendered,key,f.result(),self._budgets[1])
            future.add_done_callback(finished)
            return future

    def spots(self, key) -> list:
        """Where the spots of a laid-out sheet are, in its pixels; [] if unknown.

        `key` is a display key from `key()`; brightness and contrast are
        ignored, since they never move anything.
        """
        if key is None:
            return []
        path, stamp, w, h, _b, _c, mode, rotate = key
        with self._lock:
            found = self._maps.get((path,stamp,w,h,1.,1.,mode,rotate))
        return list(found[0]) if found else []

    def photo(self, key, prepared=None):
        """Tk-thread only. A prepared Pillow image becomes a Tk image here."""
        if key in self._items:
            self._counts['hits'] += 1
            self._items.move_to_end(key)
            return self._items[key]
        if prepared is None:
            return None
        return self._store(self._items,key,ImageTk.PhotoImage(prepared),self._budgets[2])

    def clear(self) -> None:
        with self._lock:
            self._epoch += 1
            self._items.clear()
            self._decoded.clear()
            self._rendered.clear()
            self._maps.clear()
            self._versions.clear()
            for future in list(self._pending.values()):
                future.cancel()

    def close(self):
        with self._lock:
            self._closed = True
            self.clear()
            pool, self._pool = self._pool, None
        if pool:
            pool.shutdown(wait=False,cancel_futures=True)

    def invalidate(self, path: Path) -> None:
        """Forget every scaled copy of one file.

        A redrawn graph is written back to the same filename, so without this
        the cache would keep serving the picture from before the edit -- the
        one case where a stale image is not merely untidy but wrong.
        """
        path = str(Path(path).resolve())
        with self._lock:
            if len(self._versions) >= self.limit * 2:
                self.clear()
            self._versions[path] = self._versions.get(path,0) + 1
            for cache in (self._items,self._decoded,self._rendered,self._maps):
                for k in [k for k in cache if k[0] == path]:
                    del cache[k]

    def get(self, path: Path, box: tuple[int, int], brightness: float = 1.0,
            contrast: float = 1.0, mode: str = "fit",
            rotate: bool = False) -> "tk.PhotoImage | None":
        """An adjusted image for `path`, shown as `mode` (graph turned if
        `rotate`), scaled to fit `box`.

        Aspect ratio is preserved and the image is never scaled UP: a sheet
        blown past its own resolution looks like a mistake, and the detail the
        person is checking -- whether a spot is actually quantifiable -- is not
        there to recover. Brightness and contrast affect this preview only;
        neither the source image nor an exported figure is changed.
        """
        # Synchronous compatibility API for noninteractive callers. SheetView
        # uses request/poll, and never waits for a worker on the Tk thread.
        self.last_error = ""
        if self._closed:
            self.last_error = 'Image loader is closed'
            return None
        if (mode != "fit" or rotate) and not HAVE_PIL:
            self.last_error = "this view needs Pillow (pip install pillow)"
            return None
        try:
            key = self.key(path,box,brightness,contrast,mode,rotate)
            cached = self.photo(key)
            if cached is not None:
                return cached
            if not HAVE_PIL:
                return self._store(self._items,key,self._with_tk(path,*box),self._budgets[2])
            with self._lock:
                prepared = self._rendered.get(key)
            if prepared is None:
                prepared = self._prepare(key)
                with self._lock:
                    self._store(self._rendered,key,prepared,self._budgets[1])
            return self.photo(key,prepared) if self.current(key) else None
        except Exception as e:
            self.last_error = str(e)
            return None

    # -- backends ------------------------------------------------------------

    @staticmethod
    def _with_pil(path: Path, w: int, h: int, brightness: float,
                  contrast: float, mode: str = "fit", rotate: bool = False):
        from src.spotting_pixels import open_pixels

        with open_pixels(path) as im:
            # A sheet that records its pieces is rearranged for this box and
            # view rather than shrunk whole into it. Anything else is scaled
            # as before.
            im = fit_sheet(im, (w, h), mode, rotate)
            if brightness != 1.0:
                im = ImageEnhance.Brightness(im).enhance(brightness)
            if contrast != 1.0:
                im = ImageEnhance.Contrast(im).enhance(contrast)
            im.info["spotting-display"] = "preview only; never measurement input"
            return ImageTk.PhotoImage(im)

    @staticmethod
    def _with_tk(path: Path, w: int, h: int):
        img = tk.PhotoImage(file=str(path))
        # subsample only divides by whole numbers, so pick the smallest factor
        # that fits rather than the one that fits best.
        factor = 1
        while (img.width() // factor > w or img.height() // factor > h) \
                and factor < 16:
            factor += 1
        return img.subsample(factor, factor) if factor > 1 else img


def size_of(path: Path) -> "tuple[int, int] | None":
    """Pixel size without decoding the whole image, when Pillow is available."""
    if not HAVE_PIL:
        return None
    try:
        from src.spotting_pixels import pixel_size

        return pixel_size(path)
    except Exception:
        return None
