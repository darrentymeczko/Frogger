"""The guided tour: a first-time walk through the program, step by step.

A callout sits beside the real thing each step is about, with a ring drawn
round it, and Back / Next underneath. Nothing is modal -- the program stays
usable under the tour, and the tour follows what the person does:

  * the first launch starts the WORKBENCH chapter, on Home, which ends by
    offering the PRACTICE run;
  * the practice run is the whole job done once on photographs that ship
    with the program (`workbench.practice`): design the plate, describe the
    experiment, run it, review the results. Its steps are
    tasks: each waits until the person has done it, or does it for them;
  * the first time a plate template, an experiment or a result set is shown
    outside the practice, that tool's own chapter starts. Those explain every
    control, and are what Help > Tour of this tab shows again;
  * inside a chapter, moving about the tool yourself (clicking an experiment's
    "2. Data" tab, say) moves the tour to the step about where you now are;
  * switching to another tab puts a chapter away until you come back.

Which chapters have been seen is remembered in the settings, so each is
offered once. Help > Take the guided tour forgets that and starts again.

The steps are plain data (`CHAPTERS`), so the wording can be edited without
touching the machinery below it.
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

from uikit import icons
from uikit import tokens as t
from uikit.dpi import px
from uikit.theme import FONT_HEADING, FONT_SMALL
from uikit.widgets import LinkLabel

from . import practice

if TYPE_CHECKING:                                  # pragma: no cover
    from .docs import DocumentHost
    from .shell import Shell

#: The settings key holding the chapters already shown.
SEEN_KEY = "tour_seen"
#: How often a running chapter looks at where its target is, in milliseconds.
TICK_MS = 250
#: How soon an arrangement is looked at again before it is drawn.
SETTLE_MS = 40
#: How long (s) a step waits for its target to come on screen.
TARGET_WAIT = 0.6

WORKBENCH = "workbench"


@dataclass(frozen=True)
class Step:
    title: str
    #: The text, or (shell, doc) -> the text.
    body: object
    #: (shell, doc) -> the widget this step is about, or None for no ring.
    target: Callable | None = None
    #: (shell, doc) -> None; puts the program where the step can be seen.
    prepare: Callable | None = None
    #: (shell, doc) -> bool; a step that does not apply is passed over.
    when: Callable | None = None
    #: Matched against the chapter's `follow`, to keep up with the person.
    key: str = ""
    #: On the last step: (button label, (shell) -> None), done after finishing.
    then: tuple | None = None
    #: In a lesson, where the step happens: "home" or a document kind.
    on: str = ""
    #: (shell, doc) -> bool. Makes the step a task: it moves on by itself
    #: once this turns true.
    done: Callable | None = None
    #: (shell, doc) -> None; does the task, for the "Do it for me" button.
    do: Callable | None = None
    #: Which side of the target the callout tries first: "right", "below",
    #: "left" or "above".
    place: str = ""


@dataclass(frozen=True)
class Chapter:
    name: str
    steps: tuple
    #: (shell, doc) -> the `key` of the step about where the person is now.
    follow: Callable | None = None
    #: A lesson moves between documents, which it opens as it goes.
    lesson: bool = False


# -- the workbench ---------------------------------------------------------------

def _home(card: str | None = None, *, clear: bool = False) -> Callable:
    """Bring Home forward; select `card`, or no card at all with `clear`."""
    def prepare(shell, _doc) -> None:
        if shell.active is not None:
            shell.activate(None)
        if clear or (card is not None and card in shell.home.cards):
            shell.home.select(card)
    return prepare


def _explorer_shown(shell, _doc) -> bool:
    return bool(shell._show_explorer.get())


_WORKBENCH = Chapter(WORKBENCH, (
    Step("Welcome to Frogger",
         "This short tour shows where everything is. It takes about a "
         "minute, and the program stays usable while it runs.\n\n"
         "It ends with a practice run on real photographs.",
         prepare=_home(clear=True)),
    Step("The work, left to right",
         "Every assay goes through these steps in order: lay out the plate, "
         "describe the experiment and measure its photographs, then review "
         "what was measured.\n\nClick a card to choose the step you are on. None is "
         "chosen until you do.",
         target=lambda s, d: next(iter(s.home.cards.values())).master,
         prepare=_home(clear=True)),
    Step("How to start",
         "The selected step's actions are listed here. Experiment is selected "
         "now as an example. Most work starts with "
         "New experiment: it can create its plate layout as it goes, so a "
         "separate plate template is only needed for a layout you will reuse.",
         target=lambda s, d: s.home.actions, prepare=_home("experiment")),
    Step("Carry on where you left off",
         "Whatever you opened or changed most recently for the selected step "
         "is listed here. One click reopens it.",
         target=lambda s, d: s.home.recent, prepare=_home("experiment")),
    Step("Everything in the project",
         "The Explorer lists every plate template, experiment and result set "
         "in the project folder. Double-click one to open it.\n\nWhile a "
         "quantification runs, its progress appears underneath, in Jobs.",
         target=lambda s, d: s.explorer, prepare=_home(),
         when=_explorer_shown),
    Step("One tab per document",
         "Each thing you open gets a tab here, and the menus at the top "
         "follow the tab you are on. Home is always the first tab.",
         target=lambda s, d: s.tabs, prepare=_home()),
    Step("Now try it",
         "The practice run takes you through one whole assay on photographs "
         "that came with the program, from the plate layout to reviewed "
         "results. It takes about ten minutes.\n\nTo see any of this "
         "again, choose Help > Take the guided tour.",
         target=lambda s, d: next(iter(s.home.cards.values())).master,
         prepare=_home(clear=True),
         then=("Start the practice run", lambda s: s.tour.start_practice())),
))


# -- reaching into a tool ----------------------------------------------------------

def _find(parent, text: str):
    """The widget under `parent` whose label starts with `text`. Most of the
    tools' buttons are made inline and kept nowhere, so they are found by
    what they say -- which is also how the person finds them."""
    for child in parent.winfo_children():
        try:
            if str(child.cget("text")).startswith(text):
                return child
        except tk.TclError:
            pass
        found = _find(child, text)
        if found is not None:
            return found
    return None


def _tab(name: str) -> Callable:
    """Show the experiment's notebook page called `name`."""
    def prepare(_shell, doc) -> None:
        app = doc.app
        app.notebook.select(getattr(app, name))
    return prepare


# -- an experiment ---------------------------------------------------------------

def _plates_shown(_shell, doc) -> bool:
    app = doc.app
    try:
        return app.notebook.tab(app.plates, "state") != "hidden"
    except tk.TclError:
        return False


_EXPERIMENT_PAGES = ("panel", "data", "conditions", "plates", "run_tab")


def _experiment_follow(_shell, doc) -> str:
    app = doc.app
    try:
        selected = app.notebook.select()
    except tk.TclError:
        return ""
    return next((name for name in _EXPERIMENT_PAGES
                 if str(getattr(app, name, None)) == selected), "")


_PANEL, _DATA = _tab("panel"), _tab("data")
_CONDITIONS, _RUN = _tab("conditions"), _tab("run_tab")

_EXPERIMENT = Chapter("experiment", (
    Step("An experiment, tab by tab",
         "An experiment says what was on the plate and where its photographs "
         "are. Work through the numbered tabs in order.\n\nThis bar names "
         "the experiment. Rename changes the name, Choose folder links the "
         "photographs, and Re-read looks at the folder again after files "
         "were added or renamed.",
         target=lambda s, d: d.app.header),

    # 1. Panel
    Step("1. Panel: the plate layout",
         "Start with the layout. Create layout draws the grid of spots for "
         "this experiment. Copy from template reuses one saved in the plate "
         "designer.\n\nThe experiment keeps its own copy, so later changes "
         "to a template never alter it.",
         target=lambda s, d: d.app.panel.preview_column, prepare=_PANEL,
         key="panel"),
    Step("Name the strains",
         "Each sample slot of the layout gets a row here. Type the strain "
         "that was spotted in it. Leave a slot blank if nothing was spotted "
         "there.",
         target=lambda s, d: d.app.panel.strain_column, prepare=_PANEL),
    Step("More than one set of strains?",
         "If the same layout was spotted with different strains, press Add "
         "strain group. Choose a group in this box to fill in its own "
         "strains.",
         target=lambda s, d: d.app.panel.group_selector, prepare=_PANEL),

    # 2. Data
    Step("2. Data: link the photographs",
         "Choose data folder links the folder holding this experiment's "
         "photographs.\n\nPhoto organization guide shows the folder and file "
         "naming layouts the program can read by itself.",
         target=lambda s, d: _find(d.app.data, "Choose data folder").master,
         prepare=_DATA, key="data"),
    Step("First, automatic reading",
         "Each photo needs a treatment, a plate number and the hours since "
         "spotting. The program first tries to read these from the folder "
         "and file names, and says here what it found.\n\nDetect again "
         "repeats that after you rename files or folders.",
         target=lambda s, d: d.app.data.how, prepare=_DATA),
    Step("If automatic reading gets it wrong: keyword rules",
         "Keyword rules is the next thing to try. You say that a word in a "
         "name means something, for example that \"glu\" means the Glucose "
         "treatment or \"R2\" means plate 2.\n\nPhoto order in each folder is "
         "for photos whose names say nothing but are always taken in the "
         "same order. It assigns strain group and plate by position.",
         target=lambda s, d: _find(d.app.data, "Keyword rules...").master,
         prepare=_DATA),
    Step("Still wrong for a few photos: fix them by hand",
         "Select the photos in this list. Ctrl-click and Shift-click select "
         "several. Buttons then appear above the list to set their "
         "Condition, Plate, Hours or Strain group together, or to Ignore "
         "photos that should be left out.\n\nDouble-click a single value to "
         "correct just that one. Assignments made by hand win over "
         "everything else.",
         target=lambda s, d: d.app.data.table, prepare=_DATA),
    Step("Find what still needs fixing",
         "Needs attention lists only the photos still missing something, "
         "and the Review column says what. Manually edited and Ignored show "
         "what you have changed. Find searches the names.",
         target=lambda s, d: d.app.data.review_bar, prepare=_DATA),
    Step("For unusual naming",
         "If your naming follows a pattern the program did not recognise, "
         "this opens a form where you choose, for each detail, which folder "
         "level or part of the file name it is written in. Examples from "
         "your own photos appear under each choice.",
         target=lambda s, d: d.app.data.organization_button, prepare=_DATA),

    # 3. Conditions
    Step("3. Conditions: the treatments",
         "The treatments found in the photographs are listed on this tab. "
         "Add, Rename and Remove correct the list. Add the ones found in the "
         "photos brings in any that are still missing.",
         target=lambda s, d: _find(d.app.conditions, "Add...").master,
         prepare=_CONDITIONS, key="conditions"),
    Step("Categories",
         "Categories say how treatments differ, such as carbon source or "
         "temperature.\n\nFill from names guesses each treatment's values from its "
         "name.",
         target=lambda s, d: _find(d.app.conditions, "Categories..."),
         prepare=_CONDITIONS),
    Step("One treatment's details",
         "Select a treatment to set its category values here, and to "
         "exclude any slot that was not spotted on that treatment.",
         target=lambda s, d: d.app.conditions.inspector, prepare=_CONDITIONS),

    # 4. Plates (handpicked only)
    Step("4. Plates: one photograph per plate",
         "For handpicked quantification only. Select a plate's row, flip "
         "through the photographs, and press Use this photograph. Then set "
         "the dilution row to score for that plate while you can see it.",
         target=lambda s, d: d.app.plates, prepare=_tab("plates"),
         when=_plates_shown, key="plates"),

    # Analysis / comparisons
    Step("Analysis: the type of quantification",
         "Choose how the photographs are measured.\n\nTime course scores "
         "every timepoint, photo pairing and dilution, then proposes the "
         "best one per treatment.\n\nHandpicked quantification measures only "
         "the photographs you choose. Selecting it adds a Plates tab where "
         "you pick them.",
         target=lambda s, d: _find(d.app.run_tab, "Time course"),
         prepare=_RUN, key="run_tab"),
    Step("Photo orientation",
         "Say which edge of each photograph is the top of the plate. A plate "
         "photographed sideways or upside down can then use the same layout.",
         target=lambda s, d: d.app.photo_top_picker.master, prepare=_RUN),
    Step("Output",
         "Full writes graphs, charts and statistics. Data only skips those "
         "and writes just the per-spot table of raw and normalized values.",
         target=lambda s, d: _find(d.app.run_tab, "Full").master,
         prepare=_RUN),
    Step("Normalization and comparison references",
         "This opens the choice of which strain every spot is normalized to, "
         "and which strains the others are compared against, for each "
         "treatment and strain group.\n\nIt must be set before a run. WT and "
         "EV start checked when those strains are present.",
         target=lambda s, d: d.app.analysis_button, prepare=_RUN),
    Step("The statistical test",
         "Test chooses between ratio paired t-tests and a one-way ANOVA.\n\n"
         "The second box follows the test. For t-tests it is the "
         "multiple-comparison correction. For an ANOVA it is the post-hoc "
         "test.\n\np cutoff is the significance threshold. Review results "
         "starts from these choices and can still change them.",
         target=lambda s, d: d.app.stats_frame, prepare=_RUN),
    Step("Check before you run",
         "The summary above this button says what is ready and what is "
         "still missing. How long would this take? estimates the run "
         "without starting it.",
         target=lambda s, d: d.app.estimate_button, prepare=_RUN),
    Step("Run quantification",
         "This saves the experiment, lists anything worth a look, and "
         "measures in the background. Progress appears in Jobs, under the "
         "Explorer.\n\nWhen it finishes, a notice offers to open the results "
         "in Review.",
         target=lambda s, d: d.app.run_button, prepare=_RUN),
), follow=_experiment_follow)


# -- a plate template ------------------------------------------------------------

_PLATE = Chapter("plate", (
    Step("A plate template",
         "A template is the shape of the assay: the grid, and which cell "
         "holds which sample slot, replicate and dilution. It names no "
         "strains, so one template serves every experiment spotted the same "
         "way.\n\nDouble-click any cell to edit it exactly.",
         target=lambda s, d: d.app.notebook),
    Step("The tools",
         "Hover any button to see what it does. In order of use:\n\n"
         "Grid size sets the rows and columns.\n"
         "Place samples: drag across cells to number sample slots.\n"
         "Add dilutions: drag outwards from a placed spot to where the last "
         "dilution lands.\n"
         "Add plate and Duplicate make a second plate.\n"
         "T shows the text that will be saved, to check against the bench.",
         target=lambda s, d: d.app.toolbar),
    Step("The selected tool's settings",
         "This bar shows only what the current tool reads, such as the "
         "first slot number, the replicate, and the spacing between "
         "dilutions.",
         target=lambda s, d: d.app.context),
    Step("Findings, as you work",
         "Anything wrong with the layout is listed here as you go. Errors "
         "mean the design cannot be analysed. Warnings are worth a look. "
         "Click a line to jump to the cells it is about.",
         target=lambda s, d: d.app.validation),
    Step("Use it",
         "File > Save keeps the template for reuse. Or press this to carry "
         "the layout straight into a new experiment without saving a file.",
         target=lambda s, d: d.app.use_layout_button),
))


# -- a result set ----------------------------------------------------------------

_REVIEW = Chapter("review", (
    Step("Review results",
         "This is what a run measured. The bar names the result set and "
         "steps between sets. Double-click the name to rename it.",
         target=lambda s, d: d.app.header),
    Step("One treatment at a time",
         "Each treatment has a button here. Everything below shows the "
         "selected treatment.",
         target=lambda s, d: d.app._tabs),
    Step("Change the statistics",
         "The test, its correction or post-hoc test, and the p cutoff start "
         "from what the experiment chose and can be changed here. "
         "Comparisons chooses which strains are compared.\n\nAfter a change, "
         "a Recompute button appears to bring the figures up to date.",
         target=lambda s, d: d.app.stats),
    Step("What area of each spot is measured",
         "Measurement ROI sets the region of each spot that is measured. "
         "Review ROI overlays shows that region drawn on the photographs.",
         target=lambda s, d: d.app.btn_roi),
    Step("Leave a treatment out",
         "Untick this to keep the selected treatment out of exports.",
         target=lambda s, d: d.app.btn_include),
    Step("The candidates, ranked",
         "A time course scores many candidates per treatment: every "
         "timepoint, photo pairing and dilution. They are ranked here, best "
         "first. Select one to preview it.",
         target=lambda s, d: d.app.candidates),
    Step("The candidate, as a figure",
         "The selected candidate's plates and graphs. Look at the winner "
         "before reporting it. The ranking is a place to start, not a "
         "verdict.",
         target=lambda s, d: d.app.sheet),
    Step("Every spot's number",
         "Each spot's measurement is listed here. Correct a value, mark an "
         "outlier or add a note, and the statistics are worked out again.",
         target=lambda s, d: d.app.table),
    Step("Keep one",
         "Use this candidate keeps the one you are looking at instead of "
         "the pipeline's pick. Reset to pipeline pick, beside it, goes back.",
         target=lambda s, d: d.app.btn_use),
    Step("Export",
         "Export writes the chosen candidates' figures and tables.",
         target=lambda s, d: d.app.btn_export),
))


# -- the practice run ----------------------------------------------------------
# One job, start to finish, on the photographs in `workbench.practice`. Every
# `done` and `do` below is a function of that module, or one line over it, so
# what the steps ask for and what "Do it for me" does cannot drift apart.

PRACTICE = "practice"


def _has(kind: str) -> Callable:
    """Whether the practice has a document of this kind yet."""
    return lambda shell, _doc: kind in shell.tour.docs


def _plate_target(shell, _doc):
    home = shell.home
    return home.actions if home.current == "plate" else home.cards["plate"]


def _template(done: Callable, *args) -> Callable:
    return lambda _shell, doc: done(doc.app.template, *args)


def _layout(do: Callable, *args) -> Callable:
    return lambda _shell, doc: do(doc.app.controller, *args)


def _assign_tool(_shell, doc) -> None:
    doc.app._select_tool("assign")


def _experiment(done: Callable) -> Callable:
    return lambda _shell, doc: done(doc.app.controller.experiment)


def _controller(do: Callable) -> Callable:
    def run(_shell, doc) -> None:
        do(doc.app.controller)
        doc.app.refresh()
    return run


def _data_tab(shell, doc) -> None:
    practice.install_photos()
    _DATA(shell, doc)


def _photos_text(_shell, _doc) -> str:
    return ("The practice photographs are in the program's own folder:\n\n"
            f"{practice.photos_dir()}\n\n"
            "Press Choose data folder and select that Photos folder. Its "
            "subfolders are named for the hours, the treatment and the "
            "plate, which is all the program needs to read them.")


def _remember(name: str, read: Callable) -> Callable:
    """Note how something stands when a step starts, for its `done` to
    compare against."""
    def prepare(shell, doc) -> None:
        shell.tour.memo[name] = read(doc.app)
    return prepare


def _moved(name: str, read: Callable) -> Callable:
    def done(shell, doc) -> bool:
        memo = shell.tour.memo
        return name in memo and read(doc.app) != memo[name]
    return done


def _view(app) -> tuple:
    return app.sheet.view, app.sheet.rotated


def _medium(app) -> str:
    return app.ctl.medium


def _other_medium(_shell, doc) -> None:
    app = doc.app
    media = app.ctl.run.media
    app._step_medium(1 if media.index(app.ctl.medium) < len(media) - 1 else -1)


def _flagged(_shell, doc) -> bool:
    return any(edit.outlier is not None
               for edits in doc.app.ctl.review.edits.values()
               for edit in edits.values())


def _flag_one(_shell, doc, tries: int = 120) -> None:
    table = doc.app.table
    rows = table.tree.get_children() if table.tree.winfo_ismapped() else ()
    if rows:
        table.tree.selection_set(rows[0])
        table.tree.focus(rows[0])
        table._toggle_outlier()
    elif tries:
        # The numbers are still being put together for this treatment.
        table.after(250, lambda: _flag_one(_shell, doc, tries - 1))


def _stale(app) -> bool:
    return app.ctl.medium in app._stale_media


_ON_PLATE = dict(on="plate")
_ON_EXPERIMENT = dict(on="experiment")
_ON_REVIEW = dict(on="review")

_PRACTICE = Chapter(PRACTICE, (
    # Home
    Step("A practice run",
         "Now the whole job, once, on real photographs that came with the "
         "program: design the plate, describe the experiment, run it, review "
         "the numbers. It takes about ten minutes.\n\n"
         "Most steps ask you to do something and move on when you have. If "
         "you get stuck, Do it for me does the step for you.",
         prepare=_home(clear=True), on="home"),
    Step("Start with the plate",
         "Click the Plate template card, then New blank template.",
         target=_plate_target, prepare=_home(), on="home",
         done=_has("plate"), do=lambda s, d: s.open_document("plate")),

    # The plate designer
    Step("One plate, seen from above",
         "Each cell of this grid is a place a spot can go. It is 6 rows by "
         "8 columns already, the size these plates were spotted in. Grid "
         "size (⊞) in the toolbar changes it.\n\n"
         "The practice plates hold 8 samples across and 3 dilutions down, "
         "and that block is spotted twice on each plate.",
         target=lambda s, d: d.app.notebook, **_ON_PLATE),
    Step("Place the samples",
         "The Place samples tool (●) is already selected. Drag across the "
         "whole top row, from its first cell to its last.\n\n"
         "The cells are numbered 1 to 8. These are sample slots: positions, "
         "not strains. The strains are named later, in the experiment, so "
         "one layout serves every experiment spotted this way.",
         target=lambda s, d: d.app.notebook, prepare=_assign_tool,
         done=_template(practice.samples_placed),
         do=_layout(practice.place_samples), place="below", **_ON_PLATE),
    Step("Add the dilutions",
         "Choose Add dilutions (⇩), ringed here. Click any spot in the top "
         "row, which selects the whole row. Then drag from that row down to "
         "the third row.\n\nEach sample gets two more spots under it, one "
         "dilution further on each time.",
         target=lambda s, d: _find(d.app.toolbar, "⇩"),
         done=_template(practice.dilutions_added),
         do=_layout(practice.add_dilutions), **_ON_PLATE),
    Step("The second replicate",
         "Rows 4 to 6 hold the same samples again, as replicate 2.\n\n"
         "Go back to Place samples (●). In this bar, set Replicate to 2 and "
         "First slot to 1. Drag across row 4, then add its dilutions down "
         "to row 6 as before.",
         target=lambda s, d: d.app.context,
         done=_template(practice.dilutions_added, 2),
         do=_layout(practice.second_replicate), **_ON_PLATE),
    Step("The second plate",
         "This assay used two plates laid out the same way. Plate 2 holds "
         "replicates 3 and 4.\n\nPress Duplicate this plate (⧉), ringed "
         "here, and accept the suggested 2. That is how much is added to "
         "each replicate number on the copy.",
         target=lambda s, d: _find(d.app.toolbar, "⧉"),
         done=_template(practice.plate_duplicated),
         do=_layout(practice.duplicate_plate), **_ON_PLATE),
    Step("Checked as you work",
         "Anything wrong with a layout is listed here while you draw it. "
         "Errors mean it cannot be analysed. Click a line to see the cells "
         "it is about.\n\nCtrl+Z undoes a step, and double-clicking a cell "
         "sets that one cell exactly.",
         target=lambda s, d: d.app.validation, **_ON_PLATE),
    Step("Use the layout",
         "A layout you will use again can be saved as a template with "
         "File > Save.\n\nFor now, press Use layout in experiment. It "
         "carries the layout straight into a new experiment.",
         target=lambda s, d: d.app.use_layout_button,
         done=_has("experiment"),
         do=lambda s, d: d.app.use_in_experiment(), **_ON_PLATE),

    # The experiment
    Step("The experiment",
         "An experiment says which strains were on the plates and where the "
         "photographs are. This one is named Practice run, and it already "
         "holds the layout you drew.\n\nIts tabs are worked through from "
         "left to right.",
         target=lambda s, d: d.app.header, prepare=_PANEL, **_ON_EXPERIMENT),
    Step("Name the strains",
         "Each sample slot has a row here. Type these names, pressing Enter "
         "after each one:\n\n"
         "1:  GLR1\n2:  SCH9\n3:  PRX1\n4:  WT 2\n5:  WT\n6:  ZWF1\n"
         "7:  RHO5\n8:  Tat-D\n\n"
         "Slot 4 is a second wild type, spotted as a check on the first.",
         target=lambda s, d: d.app.panel.strain_column, prepare=_PANEL,
         done=_experiment(practice.strains_named),
         do=_controller(practice.name_strains), **_ON_EXPERIMENT),
    Step("Link the photographs", _photos_text,
         target=lambda s, d: _find(d.app.data, "Choose data folder"),
         prepare=_data_tab, done=_experiment(practice.photos_linked),
         do=_controller(practice.link_photos), **_ON_EXPERIMENT),
    Step("Read automatically",
         "Each photograph needs a treatment, a plate number and the hours "
         "since spotting. All eight were read from their folder names, such "
         "as 18 Hours > Glucose > Plate 1.\n\n"
         "When your own naming is not understood, Keyword rules, Photo "
         "order and correcting photos by hand are the tools for it. "
         "Help > Tour of this tab explains each of them.",
         target=lambda s, d: d.app.data.table, prepare=_DATA,
         **_ON_EXPERIMENT),
    Step("Add a category",
         "The two treatments found in the photographs, Glucose and "
         "Glycerol, are listed on this tab.\n\n"
         "Categories say how treatments differ, such as carbon source or "
         "temperature. Press Fill from names, ringed here, and accept what "
         "it read: a category called Carbon source.\n\n"
         "Categories… beside it adds one by hand. Its values are then set "
         "per treatment on the right.",
         target=lambda s, d: _find(d.app.conditions, "Fill from names"),
         prepare=_CONDITIONS, done=_experiment(practice.categories_set),
         do=_controller(practice.fill_categories), **_ON_EXPERIMENT),
    Step("Choose the reference strain",
         "Growth is reported relative to a reference strain on the same "
         "plate.\n\nPress this button. Choose WT as the normalization "
         "reference, tick WT as the strain the others are compared with, "
         "and apply.",
         target=lambda s, d: d.app.analysis_button, prepare=_RUN,
         done=_experiment(practice.references_set),
         do=_controller(practice.set_references), **_ON_EXPERIMENT),
    Step("The statistical test",
         "The test, its correction or post-hoc test, and the p cutoff are "
         "chosen here. The practice keeps what is set. Review results can "
         "still change them after the run.",
         target=lambda s, d: d.app.stats_frame, prepare=_RUN,
         **_ON_EXPERIMENT),
    Step("Run the quantification",
         "Press Run quantification.\n\n"
         "A real run measures every photograph. That takes a minute or two "
         "for a set this size, and its progress shows in Jobs under the "
         "Explorer. For the practice, the results were measured ahead of "
         "time and open at once.",
         target=lambda s, d: d.app.run_button, prepare=_RUN,
         done=_has("review"), do=lambda s, d: s.tour.practice_run(),
         **_ON_EXPERIMENT),

    # Review results
    Step("The results",
         "The run scored every timepoint and dilution of each treatment "
         "and ranked them. These are the candidates for the treatment "
         "shown, best first. The chosen one is what gets reported.\n\n"
         "The ranking is a place to start, not a verdict.",
         target=lambda s, d: d.app.candidates, **_ON_REVIEW),
    Step("Look at another candidate",
         "Click a different row, or press the Down arrow. The sheet and "
         "the numbers beside it follow, without changing which candidate "
         "is chosen.",
         target=lambda s, d: d.app.candidates,
         done=lambda s, d: d.app._preview is not None,
         do=lambda s, d: d.app._step_candidate(1), **_ON_REVIEW),
    Step("Change the view",
         "The sheet shows the plates beside their graph. Click Aligned, or "
         "press V, to step through the ways of showing it. The arrow "
         "button, or R, turns the graph on its side.\n\n"
         "The mouse wheel zooms and dragging pans.",
         target=lambda s, d: _find(d.app.sheet, "View (V):").master,
         prepare=_remember("view", _view), done=_moved("view", _view),
         do=lambda s, d: d.app.sheet.cycle_view(), place="below",
         **_ON_REVIEW),
    Step("Switch treatment",
         "Each treatment has a button here. Click the other one, or press "
         "the Right or Left arrow.",
         target=lambda s, d: d.app._tabs,
         prepare=_remember("medium", _medium),
         done=_moved("medium", _medium), do=_other_medium, **_ON_REVIEW),
    Step("Flag an outlier",
         "Every spot's measurement is in this table. A replicate that is "
         "clearly wrong, such as a spot that failed to grow, can be left "
         "out of the statistics.\n\nClick a row and press O to flag it as "
         "an outlier. O again takes the flag off.\n\n"
         "The program flags the most extreme spots itself. They say so in "
         "the Flags column.",
         target=lambda s, d: d.app.table, done=_flagged, do=_flag_one,
         place="above", **_ON_REVIEW),
    Step("Bring the graph up to date",
         "The means and p-values in the table changed at once. The graph "
         "was drawn before your change, and this button now says it is out "
         "of date.\n\nPress it to redraw the graph without the flagged "
         "spot.",
         target=lambda s, d: d.app.btn_redraw,
         done=lambda s, d: not _stale(d.app),
         do=lambda s, d: d.app._redraw_graph(), **_ON_REVIEW),
    Step("Keep a choice",
         "Use this candidate keeps the candidate you are looking at in "
         "place of the program's pick. Reset to pipeline pick goes back.\n\n"
         "Ctrl+S saves your choices and corrections with the results.",
         target=lambda s, d: d.app.btn_use, **_ON_REVIEW),
    Step("That is the whole job",
         "You designed a plate, described an experiment, ran it and "
         "reviewed the results. Your own data goes through the same "
         "steps.\n\n"
         "The practice files stay in the project: the Practice folder and "
         "the result set named Practice run. Delete them whenever you "
         "like.\n\n"
         "Help > Practice run repeats this. Help > Tour of this tab "
         "explains every control of the tool you are in.",
         **_ON_REVIEW),
), lesson=True)


CHAPTERS: dict[str, Chapter] = {c.name: c for c in
                                (_WORKBENCH, _EXPERIMENT, _PLATE, _REVIEW,
                                 _PRACTICE)}
#: The chapters that belong to one kind of document each.
TOOL_CHAPTERS = ("experiment", "plate", "review")


# ---------------------------------------------------------------------------
# The callout and the ring
# ---------------------------------------------------------------------------

class _Callout(tk.Frame):
    def __init__(self, master, tour: "Tour") -> None:
        super().__init__(master, background=t.SURFACE, highlightthickness=2,
                         highlightbackground=t.ACCENT, highlightcolor=t.ACCENT)
        pad = px(self, 14)
        head = tk.Frame(self, background=t.SURFACE)
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        self.count = tk.Label(head, font=FONT_SMALL, background=t.SURFACE,
                              foreground=t.TEXT_MUTED)
        self.count.pack(side="left")
        glyph, font = icons.get(self, "close", 7)
        close = tk.Label(head, text=glyph, font=font, cursor="hand2",
                         background=t.SURFACE, foreground=t.TEXT_MUTED)
        close.pack(side="right")
        close.bind("<ButtonRelease-1>", lambda _e: tour.finish())

        wrap = px(self, 340)
        self.title = tk.Label(self, font=FONT_HEADING, background=t.SURFACE,
                              foreground=t.TEXT, anchor="w", justify="left",
                              wraplength=wrap)
        self.title.pack(fill="x", padx=pad, pady=(px(self, 4), 0))
        self.body = tk.Label(self, background=t.SURFACE, foreground=t.TEXT,
                             anchor="w", justify="left", wraplength=wrap)
        self.body.pack(fill="x", padx=pad, pady=(px(self, 6), 0))

        foot = tk.Frame(self, background=t.SURFACE)
        foot.pack(fill="x", padx=pad, pady=(px(self, 12), pad))
        self.skip = LinkLabel(foot, "Skip all tours", tour.skip_all,
                              background=t.SURFACE, foreground=t.TEXT_MUTED)
        self.skip.pack(side="left", padx=(0, px(self, 24)))
        self.next = ttk.Button(foot, text="Next", style="Accent.TButton",
                               command=tour.next)
        self.next.pack(side="right")
        self.back = ttk.Button(foot, text="Back", command=tour.back)
        self.back.pack(side="right", padx=(0, px(self, 6)))
        self.then = ttk.Button(foot, command=tour.finish_then)
        self._tour = tour

    def show(self, step: Step, body: str, index: int, total: int, first: bool,
             last: bool, task: str = "") -> None:
        """`task` is "" for a step that only explains, "todo" for a task
        still to do, "done" for one already done, and "stuck" for one that
        Do it for me could not finish."""
        self.count.configure(text=f"STEP {index} OF {total}"
                             + ("  ·  DONE" if task == "done" else ""))
        self.title.configure(text=step.title)
        self.body.configure(text=body)
        self.back.configure(state="disabled" if first else "normal")
        self.back.pack(side="right", padx=(0, px(self, 6)))
        self.then.pack_forget()
        self.next.configure(command=self._tour.next)
        if task == "todo":
            self.next.configure(text="Do it for me", style="Accent.TButton",
                                command=self._tour.do_step)
        elif last and step.then is not None:
            # The step's own next move is the main button; Finish steps aside.
            self.next.configure(text="Finish", style="TButton")
            self.then.configure(text=step.then[0], style="Accent.TButton")
            self.then.pack(side="right", padx=(px(self, 6), 0),
                           before=self.next)
        else:
            self.next.configure(text="Finish" if last else
                                "Skip this step" if task == "stuck" else "Next",
                                style="Accent.TButton")

    def show_waiting(self, where: str, go: Callable) -> None:
        """The lesson's place is not on screen: say where it carries on."""
        self.count.configure(text="PRACTICE RUN")
        self.title.configure(text="The practice is waiting")
        self.body.configure(text=f"It carries on in {where}.")
        self.back.pack_forget()
        self.then.pack_forget()
        self.next.configure(text="Go there", style="Accent.TButton", command=go)


class Tour:
    """Runs one chapter at a time over the shell's window."""

    def __init__(self, shell: "Shell", *, auto: bool = False) -> None:
        self.shell = shell
        #: Whether chapters start by themselves (first launch, first open).
        self.auto = auto
        self.chapter: Chapter | None = None
        self.doc: "DocumentHost | None" = None
        self.index = 0
        self._steps: list[Step] = []
        self._callout: _Callout | None = None
        self._ring: list[tk.Frame] = []
        self._placed: tuple | None = None
        #: The arrangement last looked at, when, and the look-again timer.
        self._seen: tuple | None = None
        self._seen_at = 0.0
        self._settle_id: str | None = None
        #: What the callout on screen says: (step, task state, in view).
        self._content: tuple | None = None
        self._followed = ""
        self._tick_id: str | None = None
        #: A lesson's documents by kind, taken up as the person opens them.
        self.docs: dict[str, "DocumentHost"] = {}
        #: What was open before the lesson, and so is not part of it.
        self._before: list = []
        #: Whatever a step noted when it started, for its `done` to compare.
        self.memo: dict = {}
        #: Whether the step on screen was already done when it was shown.
        self._was_done = False
        #: Ticks left for "Do it for me" to take effect before the step
        #: offers to be skipped; None when it has not been pressed.
        self._doing: int | None = None

    # -- what has been seen --------------------------------------------------------

    def seen(self) -> set[str]:
        raw = self.shell.settings.get(SEEN_KEY, [])
        return {n for n in raw if isinstance(n, str)} if isinstance(raw, list) else set()

    def _mark(self, *names: str) -> None:
        self.shell.settings.set(SEEN_KEY, sorted(self.seen() | set(names)))
        self.shell.settings.save()

    # -- starting ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.chapter is not None

    @property
    def in_lesson(self) -> bool:
        return self.chapter is not None and self.chapter.lesson

    def first_run(self) -> None:
        """Called once the window is up and the session is back."""
        if not self.auto:
            return
        if WORKBENCH not in self.seen():
            self.start(WORKBENCH)
        elif self.shell.active is not None:
            self.document_shown(self.shell.active)

    def document_shown(self, doc: "DocumentHost | None") -> None:
        """A document came to the front: its tool's chapter, the first time."""
        if (not self.auto or doc is None or doc.kind not in TOOL_CHAPTERS
                or doc.kind in self.seen() or doc.app is None):
            return
        if self.in_lesson or (self.running and self.doc is doc):
            return
        self.start(doc.kind, doc)

    def restart(self) -> None:
        """Help > Take the guided tour: everything, from the beginning."""
        self.auto = True
        self._end(mark=False)
        self.shell.settings.set(SEEN_KEY, [])
        self.start(WORKBENCH)

    def start_practice(self) -> bool:
        """Help > Practice run. False when the practice data is not here."""
        if not practice.available():
            messagebox.showinfo(
                "Practice run",
                "The practice photographs did not come with this copy of "
                f"the program. They belong in:\n\n{practice.BUNDLE}",
                parent=self.shell.root)
            return False
        self.start(PRACTICE)
        return True

    def start_for(self, doc: "DocumentHost | None") -> bool:
        """Help > Tour of this tab. False when this tab has no chapter."""
        if doc is None or doc.kind not in TOOL_CHAPTERS or doc.app is None:
            return False
        self.start(doc.kind, doc)
        return True

    def start(self, name: str, doc: "DocumentHost | None" = None) -> None:
        if self.running:
            self._end(mark=True)
        self.chapter = CHAPTERS[name]
        self.doc = doc
        self.docs, self.memo = {}, {}
        self._before = list(self.shell.documents)
        self._steps = [s for s in self.chapter.steps
                       if s.when is None or self._safe(s.when, True)]
        self._followed = self._follow()
        self._show(0)
        self._tick_id = self.shell.root.after(TICK_MS, self._tick)

    # -- moving ----------------------------------------------------------------------

    def next(self) -> None:
        if not self.running:
            return
        if self.index >= len(self._steps) - 1:
            self.finish(completed=True)
        else:
            self._show(self.index + 1)

    def back(self) -> None:
        if self.running and self.index > 0:
            self._show(self.index - 1)

    def do_step(self) -> None:
        """Do it for me: do the task on screen. The step moves on when the
        task shows as done, which for some (a window opening) is a moment
        later; one that never does offers to be skipped."""
        if not self.running:
            return
        step = self._steps[self.index]
        if step.do is None:
            return self.next()
        try:
            step.do(self.shell, self._step_doc(step))
        except Exception as error:
            self.shell._log_error(error)
            self._doing = 0
        else:
            self._doing = 40
        self._check_task()

    def finish(self, completed: bool = False) -> None:
        """This chapter is done (or closed); the others are still offered."""
        if self.running:
            self._end(mark=True, completed=completed)

    def finish_then(self) -> None:
        step = self._steps[self.index] if self.running else None
        self.finish(completed=True)
        if step is not None and step.then is not None:
            step.then[1](self.shell)

    def skip_all(self) -> None:
        """No more tours until one is asked for from Help."""
        self._end(mark=False)
        self._mark(*CHAPTERS)

    def close(self) -> None:
        """The window is going: stop without recording anything."""
        self._end(mark=False)

    def _end(self, *, mark: bool, completed: bool = False) -> None:
        chapter, self.chapter, self.doc = self.chapter, None, None
        if self._tick_id is not None:
            try:
                self.shell.root.after_cancel(self._tick_id)
            except tk.TclError:                      # pragma: no cover
                pass
            self._tick_id = None
        self._drop_callout()
        experiment = self.docs.get("experiment")
        if experiment is not None and experiment.app is not None:
            experiment.app.run_override = None
        self.docs, self.memo, self._before = {}, {}, []
        if mark and chapter is not None:
            # Whoever finished the practice has been through every tool.
            self._mark(chapter.name, *(TOOL_CHAPTERS if chapter.lesson
                                       and completed else ()))

    def _drop_callout(self) -> None:
        if self._settle_id is not None:
            try:
                self.shell.root.after_cancel(self._settle_id)
            except tk.TclError:                      # pragma: no cover
                pass
            self._settle_id = None
        if self._callout is not None:
            try:
                self._callout.destroy()
            except tk.TclError:                      # pragma: no cover
                pass
        self._hide_ring(destroy=True)
        self._callout, self._placed = None, None
        self._seen, self._content = None, None

    # -- a lesson's documents ----------------------------------------------------------

    def _alive(self, doc) -> bool:
        return (doc is not None and not doc.closed
                and doc in self.shell.documents)

    def _adopt(self) -> None:
        """Take up the documents the person has opened since the lesson began."""
        for doc in self.shell.documents:
            if doc in self._before or doc.app is None or doc in self.docs.values():
                continue
            if doc.kind == "plate" and "plate" not in self.docs:
                self.docs["plate"] = doc
                self._guard(lambda: doc.app.controller.set_name(practice.PLATE_NAME))
            elif doc.kind == "experiment" and "experiment" not in self.docs:
                self.docs["experiment"] = doc
                # Whatever was drawn, the practice photographs are of the
                # practice layout; and Run opens what was measured earlier.
                self._guard(lambda: practice.use_layout(doc.app.controller))
                self._guard(doc.app.refresh)
                doc.app.run_override = self.practice_run
            elif (doc.kind == "review" and "review" not in self.docs
                  and (doc.path / practice.MARKER).exists()):
                self.docs["review"] = doc

    def _guard(self, action: Callable) -> None:
        try:
            action()
        except Exception as error:
            self.shell._log_error(error)

    def practice_run(self) -> bool:
        """What Run quantification does during the practice: open the
        results measured ahead of time. True, as it has dealt with the run."""
        try:
            folder = practice.install_results()
        except OSError as error:
            messagebox.showerror(
                "Practice run", f"The practice results could not be copied "
                                f"into the project.\n\n{error}",
                parent=self.shell.root)
            return True
        doc = self.shell.open_document("review", folder)
        if doc is not None:
            self.docs["review"] = doc
        return True

    def _step_doc(self, step: Step):
        """The document a step happens in (None for Home)."""
        if not step.on:
            return self.doc
        if step.on == "home":
            return None
        return self.docs.get(step.on)

    def _surface(self, step: Step) -> tuple:
        """(visible, where it is, how to get there) for a step's place."""
        shell = self.shell
        if not step.on:
            # A tool's chapter belongs to its tab; the workbench's to Home.
            return (shell.active is self.doc, "", None)
        if step.on == "home":
            return (shell.active is None, "the Home tab",
                    lambda: shell.activate(None))
        doc = self._step_doc(step)
        if doc is None:
            return (False, "the tab before this one", self.back)
        return (shell.active is doc, f"the {doc.tab_label} tab",
                lambda: shell.activate(doc))

    # -- showing a step ----------------------------------------------------------------

    def _safe(self, fn: Callable, default=None):
        """A step reaches into a tool's widgets; a tool that has changed
        shape costs the step its ring, never the program an error."""
        try:
            step = self._steps[self.index] if self._steps else None
            return fn(self.shell, self._step_doc(step) if step else self.doc)
        except Exception:
            return default

    def _follow(self) -> str:
        if self.chapter is None or self.chapter.follow is None:
            return ""
        return self._safe(self.chapter.follow, "") or ""

    def _show(self, index: int) -> None:
        self.index = index
        step = self._steps[index]
        self._doing = None
        # The ring goes at once: what it was round is no longer the point.
        self._hide_ring()
        if self.in_lesson:
            self._adopt()
            # Back and Next go to the step's own tab; only the person
            # wandering off leaves the lesson waiting.
            doc = self._step_doc(step)
            if step.on == "home" and self.shell.active is not None:
                self.shell.activate(None)
            elif self._alive(doc) and self.shell.active is not doc:
                self.shell.activate(doc)
        if step.prepare is not None and (
                (not step.on and self.doc is None) or self._surface(step)[0]):
            self._safe(step.prepare)
            # Not at idle: the page just raised has to be laid out before
            # its place on screen means anything.
            self.shell.root.update_idletasks()
        self._followed = self._follow()
        self._was_done = bool(step.done is not None and self._safe(step.done))
        self._placed = None
        self._place()

    def _task(self, step: Step) -> str:
        if step.done is None:
            return ""
        if self._was_done:
            return "done"
        return "stuck" if self._doing == 0 else "todo"

    def _check_task(self) -> None:
        """Move on once the task on screen has been done."""
        step = self._steps[self.index]
        if step.done is None or self._was_done:
            return
        if self._safe(step.done):
            self.next()
        elif self._doing:
            self._doing -= 1
            if self._doing == 0:
                self._placed = None
                self._place()

    def _tick(self) -> None:
        self._tick_id = None
        if not self.running:
            return
        if self.in_lesson:
            self._adopt()
            if any(not self._alive(d) for d in self.docs.values()):
                self._end(mark=True)                 # a practice tab was closed
                return
            if ("review" in self.docs
                    and self._steps[self.index].on in ("home", "plate", "experiment")):
                # Run was pressed ahead of its step: the results are open.
                self._show(next(i for i, s in enumerate(self._steps)
                                if s.on == "review"))
            if self.running:
                # Wherever the person is: a task often ends by opening the
                # next step's tab over this one's.
                self._check_task()
        elif self.doc is not None and not self._alive(self.doc):
            self._end(mark=True)
            return
        elif self._surface(self._steps[self.index])[0]:
            now = self._follow()
            if now != self._followed:
                self._followed = now
                match = next((i for i, s in enumerate(self._steps)
                              if s.key and s.key == now), None)
                if match is not None and match != self.index:
                    self._show(match)
        if not self.running:
            return
        self._place()
        self._tick_id = self.shell.root.after(TICK_MS, self._tick)

    # -- geometry ------------------------------------------------------------------------

    def _target(self):
        """The step's target widget, if there is one on screen."""
        step = self._steps[self.index]
        if step.target is None:
            return None
        widget = self._safe(step.target)
        try:
            return widget if widget is not None and widget.winfo_viewable() else None
        except tk.TclError:
            return None

    def _window(self, step: Step, widget):
        """The window the callout goes in: the one holding the target."""
        if widget is not None:
            return widget.winfo_toplevel()
        return self.shell.root

    def _target_box(self) -> tuple | None:
        """The step's target as (x, y, w, h) inside its window, if on screen."""
        widget = self._target()
        if widget is None:
            return None
        try:
            window = widget.winfo_toplevel()
            box = (widget.winfo_rootx() - window.winfo_rootx(),
                   widget.winfo_rooty() - window.winfo_rooty(),
                   widget.winfo_width(), widget.winfo_height())
        except tk.TclError:
            return None
        return box if box[2] > 1 and box[3] > 1 else None

    def _ring_in(self, window) -> None:
        """The ring's four edges, made in `window` (again, if they were in
        another, or that one has gone and taken them with it)."""
        try:
            usable = bool(self._ring) and all(
                e.winfo_exists() and e.master is window for e in self._ring)
        except tk.TclError:
            usable = False
        if usable:
            return
        self._hide_ring(destroy=True)
        edge = px(window, 2)
        self._ring = [tk.Frame(window, background=t.ACCENT,
                               width=edge, height=edge) for _ in range(4)]

    def _hide_ring(self, destroy: bool = False) -> None:
        for edge in self._ring:
            try:
                edge.destroy() if destroy else edge.place_forget()
            except tk.TclError:
                pass
        if destroy:
            self._ring = []

    def _settle(self) -> None:
        """Look again in a moment: see `_place`."""
        if self._settle_id is None:
            self._settle_id = self.shell.root.after(SETTLE_MS, self._settled)

    def _settled(self) -> None:
        self._settle_id = None
        if self.running:
            self._place()

    def _place(self) -> None:
        """Put the callout and the ring where the step on screen wants them.

        A page that has just been raised is laid out over several turns of
        the event loop, and its widgets move as it is. Chasing them shows
        as the callout jumping about, so nothing is drawn until the same
        arrangement has been seen twice running, and a step whose target
        is not on screen yet is given a moment before it settles for the
        middle of the window. What changes then changes in one go: a new
        callout is made ready out of sight and takes the old one's place.
        """
        step = self._steps[self.index]
        visible, where, go = self._surface(step)
        widget = self._target() if visible else None
        root = self.shell.root
        window = self._window(step, widget) if visible else root
        box = self._target_box() if widget is not None else None
        task = self._task(step)
        try:
            size = (window.winfo_width(), window.winfo_height())
            exists = self._callout is not None and self._callout.winfo_exists()
        except tk.TclError:                          # pragma: no cover
            return
        state = (visible, str(window), box, size, self.index, task, exists)
        if state == self._placed:
            self._seen = None
            return

        if not visible and not self.in_lesson:
            # A tool's chapter waits out of sight until its tab is back.
            if exists:
                self._callout.place_forget()
            self._hide_ring()
            self._placed, self._seen = state, None
            return

        now = time.monotonic()
        if state != self._seen:
            self._seen, self._seen_at = state, now
            return self._settle()
        if (visible and step.target is not None and box is None
                and now - self._seen_at < TARGET_WAIT):
            return self._settle()
        self._seen = None

        old = self._callout if exists else None
        content = (self.index, task, visible)
        if (old is not None and old.master is window
                and content == self._content):
            callout = old                            # only its place changed
        else:
            callout = _Callout(window, self)
            total = len(self._steps)
            if visible:
                body = step.body if isinstance(step.body, str) else (
                    self._safe(step.body, "") or "")
                callout.show(step, body, self.index + 1, total,
                             self.index == 0, self.index == total - 1, task)
            else:
                callout.show_waiting(where, go)
            # Not on screen yet, so this only works out how big it is.
            callout.update_idletasks()
        gap = px(window, 12)
        need = (callout.winfo_reqwidth(), callout.winfo_reqheight())
        if visible:
            x, y = place_callout(box, need, size, gap, step.place)
        else:
            x, y = size[0] - need[0] - gap, size[1] - need[1] - 3 * gap
        callout.place(x=max(gap, x), y=max(gap, y))
        if box is None:
            self._hide_ring()
        else:
            self._ring_in(window)
            self._draw_ring(window, box, size)
        callout.lift()
        if old is not None and old is not callout:
            try:
                old.destroy()
            except tk.TclError:                      # pragma: no cover
                pass
        self._callout, self._content = callout, content
        self._placed = (*state[:-1], True)

    def _draw_ring(self, window, box: tuple, size: tuple) -> None:
        e, gap = px(window, 2), px(window, 3)
        x0 = max(0, box[0] - gap - e)
        y0 = max(0, box[1] - gap - e)
        x1 = min(size[0], box[0] + box[2] + gap + e)
        y1 = min(size[1], box[1] + box[3] + gap + e)
        w, h = x1 - x0, y1 - y0
        top, bottom, left, right = self._ring
        top.place(x=x0, y=y0, width=w, height=e)
        bottom.place(x=x0, y=y1 - e, width=w, height=e)
        left.place(x=x0, y=y0, width=e, height=h)
        right.place(x=x1 - e, y=y0, width=e, height=h)
        for edge in self._ring:
            edge.lift()


def place_callout(box: tuple | None, callout: tuple, window: tuple,
                  gap: int, prefer: str = "") -> tuple[int, int]:
    """Where the callout's top-left goes: beside the target when there is
    room (right, below, left, above, in that order, `prefer` first),
    otherwise tucked into the target's bottom-right corner. With no target,
    the window's centre."""
    cw, ch = callout
    ww, wh = window

    def clamp(x: int, y: int) -> tuple[int, int]:
        return (max(gap, min(x, ww - cw - gap)), max(gap, min(y, wh - ch - gap)))

    if box is None:
        return clamp((ww - cw) // 2, (wh - ch) // 3)
    x, y, w, h = box
    sides = {
        "right": (x + w + gap + cw + gap <= ww, (x + w + gap, y)),
        "below": (y + h + gap + ch + gap <= wh, (x, y + h + gap)),
        "left": (x - gap - cw >= gap, (x - gap - cw, y)),
        "above": (y - gap - ch >= gap, (x, y - gap - ch)),
    }
    order = ([prefer] if prefer in sides else []) + list(sides)
    for side in order:
        fits, at = sides[side]
        if fits:
            return clamp(*at)
    return clamp(x + w - cw - 2 * gap, y + h - ch - 2 * gap)
