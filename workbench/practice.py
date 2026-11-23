"""The practice data the guided tour works on.

A small real assay ships with the program in `workbench/practice/`:

    photos/   eight photographs: two treatments, two timepoints, two plates
    run/      what a quantification of them produced, already saved

The tour has the person build the plate layout and the experiment for those
photographs, and when they press Run it opens `run/` instead of measuring for
a minute or two. Both are COPIED into the project before they are used
(`Practice/Photos`, and a result set called "Practice run"), so nothing the
person does to them touches the shipped files, and starting the tour again
starts from clean copies.

Everything the tour's "Do it for me" buttons do is here too, one function per
step, and `workbench/practice_build.py` builds `run/` by calling the very same
functions -- so the shipped results are the results of the experiment the
tour teaches, by construction.

Nothing here imports tkinter or the measurement engine.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import settings

#: The shipped, read-only files.
BUNDLE = Path(__file__).resolve().parent / "practice"
BUNDLE_PHOTOS = BUNDLE / "photos"
BUNDLE_RUN = BUNDLE / "run"

#: Stands for the photo folder in the shipped results, which were made on one
#: machine and are opened on another. Swapped for the real folder on install.
PLACEHOLDER = "{{PRACTICE_PHOTOS}}"
#: Marks a result folder as an installed practice copy, safe to replace.
MARKER = ".practice"

NAME = "Practice run"
#: How bright the result sheets open. Their spots are drawn on the measured
#: 0-255 scale, where even dense growth is a dark grey.
SHEET_BRIGHTNESS = 3.0
PLATE_NAME = "Practice plate"

#: The strain in each sample slot. Slot 4 is a second wild type, spotted as
#: a check on the first.
STRAINS = ("GLR1", "SCH9", "PRX1", "WT 2", "WT", "ZWF1", "RHO5", "Tat-D")
#: The slot every spot is normalized to, and the strain the rest are compared with.
REFERENCE_SLOT = 5
REFERENCE = STRAINS[REFERENCE_SLOT - 1]
CATEGORY = "Carbon source"

ROWS, COLS, LEVELS = 6, 8, 3


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

def available() -> bool:
    """Whether the practice data shipped with this copy of the program."""
    return (BUNDLE_RUN / "timecourse_candidates.csv").is_file() and any(
        BUNDLE_PHOTOS.rglob("*.JPG"))


def photos_dir() -> Path:
    return settings.ROOT / "Practice" / "Photos"


def results_dir() -> Path:
    from results_review import discovery
    return discovery.TIMECOURSE_RESULTS / NAME


# ---------------------------------------------------------------------------
# Installing the copies
# ---------------------------------------------------------------------------

def install_photos() -> Path:
    """Put the practice photographs in the project; returns their folder.

    Files already there and the same size are left alone, so this is cheap
    to call before every step that needs them.
    """
    target = photos_dir()
    for source in BUNDLE_PHOTOS.rglob("*"):
        if not source.is_file():
            continue
        copy = target / source.relative_to(BUNDLE_PHOTOS)
        if copy.is_file() and copy.stat().st_size == source.stat().st_size:
            continue
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)
    return target


def install_results() -> Path:
    """A clean copy of the shipped results, as a result set of the project.

    An earlier practice copy is replaced. A folder of that name that is NOT
    a practice copy is somebody's own results and is left alone, as is one
    that cannot be removed: the copy goes beside it under the next free name.
    """
    photos = install_photos()
    target = results_dir()
    index = 2
    while target.exists() and not _clear(target):
        target = target.with_name(f"{NAME} {index}")
        index += 1
    shutil.copytree(BUNDLE_RUN, target)
    (target / MARKER).write_text(
        "A practice copy made by the guided tour. Safe to delete.\n",
        encoding="utf-8")
    for path in target.rglob("*"):
        if path.suffix.lower() in (".json", ".csv"):
            _point_at(path, photos)
    return target


def _clear(folder: Path) -> bool:
    """Remove an earlier practice copy. False, with nothing touched, if
    `folder` is not one or is held open (a result set showing in a tab)."""
    import uuid

    if not (folder / MARKER).exists():
        return False
    # Renamed away first: that either happens whole or not at all, where a
    # delete stopped half way would leave a broken result set behind.
    doomed = folder.with_name(f".practice-old-{uuid.uuid4().hex[:8]}")
    try:
        folder.rename(doomed)
    except OSError:
        return False
    shutil.rmtree(doomed, ignore_errors=True)
    return True


def _point_at(path: Path, photos: Path) -> None:
    """Swap the placeholder in one shipped file for the real photo folder."""
    text = path.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        return
    real = str(photos)
    if path.suffix.lower() == ".json":
        real = json.dumps(real)[1:-1]            # backslashes, as JSON spells them
    path.write_text(text.replace(PLACEHOLDER, real), encoding="utf-8")


def remove_installed() -> None:
    """Take the practice copies out of the project again."""
    target = results_dir()
    if (target / MARKER).exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(photos_dir().parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# The plate layout, a stage at a time
# ---------------------------------------------------------------------------
# Eight sample slots across, three dilutions down, twice per plate; plate 1
# holds replicates 1 and 2 and plate 2 holds 3 and 4. Each function takes the
# plate designer's `EditorController` and does what one tour step asks for.

def _top_row(replicate: int) -> int:
    return (replicate - 1) * LEVELS


def place_samples(controller, replicate: int = 1) -> None:
    from plate_template.autofill import RunFillSpec, plan_run_fill

    template = controller.template
    if (template.rows, template.cols) != (ROWS, COLS):
        controller.resize(ROWS, COLS)
    plate = controller.template.plates[0].id
    controller.apply(plate, plan_run_fill(
        controller.template, plate,
        RunFillSpec(start=(_top_row(replicate), 0), direction="right",
                    count=COLS, first_slot=1, replicate=replicate, dilution=0)),
        "Place samples")


def add_dilutions(controller, replicate: int = 1) -> None:
    from plate_template.autofill import SeriesFillSpec, plan_series_fill

    if not samples_placed(controller.template, replicate):
        place_samples(controller, replicate)
    plate = controller.template.plates[0].id
    controller.apply(plate, plan_series_fill(
        controller.template, plate,
        SeriesFillSpec(sources=[(_top_row(replicate), c) for c in range(COLS)],
                       direction="down", spacing=1, levels=LEVELS)),
        "Add dilutions")


def second_replicate(controller) -> None:
    for replicate in (1, 2):
        if not dilutions_added(controller.template, replicate):
            add_dilutions(controller, replicate)


def duplicate_plate(controller) -> None:
    second_replicate(controller)
    if len(controller.template.plates) < 2:
        controller.duplicate_plate(controller.template.plates[0].id, "2", 2)


def _rows(template, plate_index: int, first: int, count: int) -> list:
    if len(template.plates) <= plate_index or template.cols != COLS:
        return []
    plate = template.plates[plate_index]
    if plate.rows < first + count:
        return []
    return [[plate.get(r, c).token for c in range(COLS)]
            for r in range(first, first + count)]


def _expected(replicate: int, levels: int) -> list:
    return [[f"s{slot}r{replicate}d{level}" for slot in range(1, COLS + 1)]
            for level in range(levels)]


def samples_placed(template, replicate: int = 1) -> bool:
    return _rows(template, 0, _top_row(replicate), 1) == _expected(replicate, 1)


def dilutions_added(template, replicate: int = 1) -> bool:
    return (_rows(template, 0, _top_row(replicate), LEVELS)
            == _expected(replicate, LEVELS))


def plate_duplicated(template) -> bool:
    return (len(template.plates) == 2
            and all(dilutions_added(template, r) for r in (1, 2))
            and _rows(template, 1, 0, ROWS)
            == _expected(3, LEVELS) + _expected(4, LEVELS))


def layout():
    """The finished practice layout, as a plate template."""
    from plate_template.gui.controller import EditorController
    from plate_template.presets import blank

    controller = EditorController(blank(ROWS, COLS, LEVELS, PLATE_NAME))
    duplicate_plate(controller)
    return controller.template


# ---------------------------------------------------------------------------
# The experiment, a step at a time
# ---------------------------------------------------------------------------
# Each takes the experiment designer's `ExperimentController`.

def use_layout(controller) -> None:
    if controller.template is None or not plate_duplicated(controller.template):
        controller.use_layout(layout())
    if controller.experiment.name != NAME:
        controller.set_name(NAME)


def name_strains(controller) -> None:
    use_layout(controller)
    for slot, name in enumerate(STRAINS, start=1):
        if (controller.experiment.strain(slot) or "") != (name or ""):
            controller.set_strain(slot, name or "")


def strains_named(experiment) -> bool:
    def spelt(name) -> str:
        return " ".join(str(name or "").split()).casefold()
    names = list(experiment.strains) + [None] * len(STRAINS)
    return all(spelt(names[i]) == spelt(STRAINS[i]) for i in range(len(STRAINS)))


def link_photos(controller) -> None:
    controller.set_photo_root(install_photos(), adopt_conditions=True)


def photos_linked(experiment) -> bool:
    if not experiment.photo_root:
        return False
    try:
        return Path(experiment.photo_root).resolve() == photos_dir().resolve()
    except OSError:
        return False


def fill_categories(controller) -> None:
    from experiments import categories

    e = controller.experiment
    _names, found, _assumed = categories.guess_all(
        [(c.code, c.display()) for c in e.conditions])
    controller.fill_categories(
        {code: {CATEGORY: values.get(categories.CARBON, "")}
         for code, values in found.items()})


def categories_set(experiment) -> bool:
    return bool(experiment.conditions) and all(
        any(c.categories.values()) for c in experiment.conditions)


def set_references(controller) -> None:
    name_strains(controller)
    controller.set_final_analysis({
        "normalization": {"*": REFERENCE_SLOT},
        "references": {"*": [REFERENCE]},
    })


def references_set(experiment) -> bool:
    from experiments import analysis

    try:
        analysis.check_panel(
            analysis.for_experiment(experiment), experiment.strains,
            experiment.condition_codes(),
            {c.code: c.exclude for c in experiment.conditions})
    except ValueError:
        return False
    return bool(experiment.condition_codes())


def finish_experiment(controller) -> None:
    """Every step above, for an experiment made in one go."""
    name_strains(controller)
    if not photos_linked(controller.experiment):
        link_photos(controller)
    if not categories_set(controller.experiment):
        fill_categories(controller)
    if not references_set(controller.experiment):
        set_references(controller)
