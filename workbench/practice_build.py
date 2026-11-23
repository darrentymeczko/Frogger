"""Build the practice data the guided tour ships with (`workbench/practice/`).

    py -m workbench.practice_build --source "<a capture tree>"

A maintenance tool, run by hand when the pipeline's output format changes or
different photographs are wanted; the program itself never runs it. It

  1. copies a few photographs out of a real capture tree, at half size, into
     `practice/photos/`;
  2. builds the practice experiment with the same functions the tour's steps
     call (`workbench.practice`), and runs the real quantification on it;
  3. saves the result as a result run, so it owns every candidate's
     measurements and opens without measuring anything;
  4. replaces the photo folder's path in what was written with a placeholder,
     and stores the result in `practice/run/`.

The source tree must be laid out `<N> Hours/<treatment>/Plate <n> .../<photo>`
and spotted in the practice layout (`practice.layout()`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import practice

REPO = Path(__file__).resolve().parents[1]

#: (timepoint folder, treatment folder) taken from the source tree.
#: The latest timepoints of each treatment: the densest growth, which is
#: both the easiest to see and the most alike between replicates.
DEFAULT_KEEP = (("18 Hours", "Glucose"), ("22 Hours", "Glucose"),
                ("42 Hours", "Glycerol"), ("46 Hours", "Glycerol"))
#: Long edge of the shipped photographs. Half the camera's: a spot is still
#: ~100 px across, and eight photographs come to 6 MB instead of 55.
LONG_EDGE = 3000
JPEG_QUALITY = 88


def copy_photos(source: Path, target: Path, keep) -> int:
    from PIL import Image

    count = 0
    for hours, treatment in keep:
        folder = source / hours / treatment
        if not folder.is_dir():
            raise SystemExit(f"not in the source tree: {folder}")
        for plate in sorted(p for p in folder.iterdir() if p.is_dir()):
            photos = sorted(plate.glob("*.JPG"))
            if not photos:
                continue
            # One photograph per plate keeps the practice small and the
            # candidate list short enough to read.
            photo = photos[0]
            out = target / hours / treatment / plate.name / photo.name
            out.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(photo) as image:
                exif = image.info.get("exif")
                scale = LONG_EDGE / max(image.size)
                if scale < 1:
                    image = image.resize(
                        (round(image.width * scale), round(image.height * scale)),
                        Image.LANCZOS)
                image.save(out, "JPEG", quality=JPEG_QUALITY,
                           **({"exif": exif} if exif else {}))
            count += 1
    return count


def build_experiment(photos: Path):
    """The practice experiment, made the way the tour makes it."""
    from experiments.gui.controller import ExperimentController
    from experiments.model import Experiment

    controller = ExperimentController(Experiment())
    practice.use_layout(controller)
    practice.name_strains(controller)
    controller.set_photo_root(photos, adopt_conditions=True)
    practice.fill_categories(controller)
    practice.set_references(controller)
    issues = [i for i in controller.issues() if i.is_error]
    if issues:
        raise SystemExit("the practice experiment has errors:\n  "
                         + "\n  ".join(i.message for i in issues))
    return controller.experiment


def run_pipeline(experiment_file: Path, outdir: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(REPO / "spotting_app.py"), "experiment-cli",
         "run", str(experiment_file), "--out", str(outdir)], cwd=str(REPO))
    if done.returncode != 0:
        raise SystemExit(f"the quantification failed (exit {done.returncode})")


def save_as_run(outdir: Path) -> None:
    """Make the results own their measurements, as Results > Save does."""
    for path in (REPO, REPO / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from results_review import discovery
    from results_review import review as rv
    from results_review.gui.controller import ReviewController

    run = discovery.load_set(outdir)
    from results_review import presentation

    controller = ReviewController(run, rv.load_for(run))
    shown = presentation.load(controller.review.presentation)
    shown["sheet"] = {**shown["sheet"], "brightness": practice.SHEET_BRIGHTNESS}
    # Shown by its name alone, without the run id a saved run carries.
    shown["names"] = {**shown.get("names", {}), "result": practice.NAME}
    controller.set_presentation(shown)
    controller.save()
    missing = controller.run.manifest.get(
        "measurement_archive", {}).get("unavailable", [])
    if missing:
        raise SystemExit(f"{len(missing)} candidate(s) could not be archived: "
                         f"{missing[:2]}")


def shrink_sheets(outdir: Path) -> None:
    """Re-save the sheets with full PNG compression (same pixels, a fifth of
    the size), keeping the text the review reads the spot positions from."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    for sheet in outdir.rglob("*.png"):
        with Image.open(sheet) as image:
            image.load()
            info = PngInfo()
            for key, value in image.info.items():
                if isinstance(value, str):
                    info.add_text(key, value)
            image.save(sheet, optimize=True, pnginfo=info)


def scrub(outdir: Path, photos: Path) -> None:
    """Replace this machine's photo folder with the placeholder."""
    raw = str(photos)
    spellings = {
        ".json": [json.dumps(raw)[1:-1], raw.replace("\\", "/")],
        ".csv": [raw, raw.replace("\\", "/")],
    }
    for path in outdir.rglob("*"):
        wanted = spellings.get(path.suffix.lower())
        if not wanted or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for spelling in wanted:
            new = new.replace(spelling, practice.PLACEHOLDER)
        if new != text:
            path.write_text(new, encoding="utf-8")


def leftovers(outdir: Path, needles) -> list[str]:
    """Files still naming this machine after the scrub."""
    found = []
    for path in outdir.rglob("*"):
        if path.suffix.lower() not in (".json", ".csv") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if any(n and n in text for n in needles):
            found.append(str(path.relative_to(outdir)))
    return found


def replace_tree(source: Path, target: Path) -> None:
    """Make `target` a copy of `source`. A folder a sync client is holding
    cannot always be removed, so files are replaced in place and whatever is
    no longer wanted is removed one by one."""
    wanted = {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    if target.exists():
        for old in sorted(target.rglob("*"), reverse=True):
            try:
                if old.is_file() and old.relative_to(target) not in wanted:
                    old.unlink()
                elif old.is_dir() and not any(old.iterdir()):
                    old.rmdir()
            except OSError:
                pass
    shutil.copytree(source, target, dirs_exist_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path,
                        help="the capture tree to take photographs from")
    parser.add_argument("--keep-work", action="store_true",
                        help="leave the working folder behind, to look at")
    args = parser.parse_args(argv)

    from experiments.schema import save

    work = Path(tempfile.mkdtemp(prefix="spotting-practice-"))
    photos, outdir = work / "Photos", work / practice.NAME
    print(f"working in {work}")
    count = copy_photos(args.source, photos, DEFAULT_KEEP)
    print(f"  {count} photograph(s) copied")

    experiment = build_experiment(photos)
    experiment_file = work / f"{practice.NAME}.spotexp.json"
    save(experiment, experiment_file)
    run_pipeline(experiment_file, outdir)
    save_as_run(outdir)
    shrink_sheets(outdir)
    scrub(outdir, photos)
    left = leftovers(outdir, [str(work), str(work).replace("\\", "\\\\"),
                              str(Path.home()),
                              str(Path.home()).replace("\\", "\\\\")])
    if left:
        print("  ! these still name this machine:\n    " + "\n    ".join(left))

    for source, target in ((photos, practice.BUNDLE_PHOTOS),
                           (outdir, practice.BUNDLE_RUN)):
        replace_tree(source, target)
    size = sum(p.stat().st_size for p in practice.BUNDLE.rglob("*") if p.is_file())
    print(f"wrote {practice.BUNDLE}  ({size / 1e6:.1f} MB)")
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if left else 0


if __name__ == "__main__":
    raise SystemExit(main())
