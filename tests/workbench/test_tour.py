"""The guided tour.

  * a first launch starts it, a second does not
  * each tool's chapter is offered the first time that tool is shown
  * it follows the person round an experiment's tabs
  * Help starts it again
  * it never starts by itself unless the shell was asked to (so no other
    test, and no self-test, ever has a callout over the window)
"""

from __future__ import annotations

import tkinter as tk

import pytest

from workbench.settings import Settings
from workbench.tour import (CHAPTERS, PRACTICE, SEEN_KEY, TOOL_CHAPTERS,
                            WORKBENCH, place_callout)


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    from results_review import links

    monkeypatch.setattr(links, "LINKS_FILE", tmp_path / "links.json")
    monkeypatch.setattr(links, "SEARCH_HINTS", [])
    return tmp_path


@pytest.fixture
def practice_project(hermetic, monkeypatch):
    """A project folder of its own for the practice copies to land in."""
    from results_review import discovery
    from workbench import practice, settings

    if not practice.available():
        pytest.skip("the practice data is not in this checkout")
    monkeypatch.setattr(settings, "ROOT", hermetic)
    monkeypatch.setattr(discovery, "TIMECOURSE_RESULTS",
                        hermetic / "Results" / "Timecourse")
    return hermetic


@pytest.fixture
def make_shell(tk_root, hermetic):
    from workbench.shell import Shell

    made = []

    def make(tour=True):
        window = tk.Toplevel(tk_root)
        window.geometry("1200x800")
        sh = Shell(window, Settings(hermetic / "workbench.json"),
                   restore=False, tour=tour)
        window.update()
        made.append(sh)
        return sh

    yield make
    for sh in made:
        try:
            sh.exit(force=True)
        except tk.TclError:
            pass


def pump(shell, until, seconds=60.0, what="the event loop"):
    """Run the event loop until `until()` is true (background work lands on it)."""
    import time

    end = time.monotonic() + seconds
    while not until():
        assert time.monotonic() < end, f"timed out: {what}"
        shell.root.update()
        time.sleep(0.01)


def drawn(shell):
    """Wait for the callout to be drawn for the step on screen: it is put
    in place a moment after the step is shown, once the page has settled."""
    tour = shell.tour
    pump(shell, lambda: tour._placed is not None and tour._placed[4] == tour.index
         and tour._seen is None, 10, "the callout")


def help_labels(shell) -> list[str]:
    menu = shell._doc_menu(shell.active, "Help") or shell.help_menu
    return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"]


def test_the_first_launch_starts_the_tour_and_the_second_does_not(make_shell):
    first = make_shell()
    assert first.tour.running and first.tour.chapter.name == WORKBENCH
    while first.tour.running:
        first.tour.next()
    assert WORKBENCH in first.settings.get(SEEN_KEY)
    first.exit(force=True)

    second = make_shell()
    assert not second.tour.running


def test_it_never_starts_unasked(make_shell):
    shell = make_shell(tour=False)
    shell.open_document("experiment")
    shell.root.update()
    assert not shell.tour.running


def test_every_step_points_at_something_on_screen(make_shell):
    shell = make_shell()
    tour = shell.tour
    for _ in range(len(tour._steps)):
        drawn(shell)
        step = tour._steps[tour.index]
        if step.target is not None:
            assert tour._target_box() is not None, step.title
        assert tour._callout.winfo_ismapped()
        if tour.index < len(tour._steps) - 1:
            tour.next()


@pytest.mark.parametrize("kind", ["experiment", "plate"])
def test_a_tool_gets_its_own_chapter_the_first_time(make_shell, kind):
    shell = make_shell()
    doc = shell.open_document(kind)
    shell.root.update()
    tour = shell.tour
    assert tour.chapter.name == kind and tour.doc is doc
    assert WORKBENCH in tour.seen()          # the chapter it took over from
    for _ in range(len(tour._steps)):
        shell.root.update()
        step = tour._steps[tour.index]
        assert tour._target_box() is not None, step.title
        tour.next()
    assert not tour.running

    shell.open_document(kind)
    assert not tour.running                  # offered once


def test_it_follows_the_person_round_an_experiment(make_shell):
    shell = make_shell()
    doc = shell.open_document("experiment")
    shell.root.update()
    tour = shell.tour
    doc.app.notebook.select(doc.app.conditions)
    shell.root.update()
    tour._tick()
    assert tour._steps[tour.index].key == "conditions"


def test_a_chapter_waits_while_its_tab_is_away_and_ends_with_it(make_shell):
    shell = make_shell()
    doc = shell.open_document("experiment")
    drawn(shell)
    tour = shell.tour
    shell.activate(None)
    tour._tick()
    shell.root.update()
    assert tour.running and not tour._callout.winfo_ismapped()
    shell.activate(doc)
    tour._tick()
    drawn(shell)
    assert tour._callout.winfo_ismapped()
    shell.close_document(doc)
    tour._tick()
    assert not tour.running


def test_skipping_stops_every_chapter(make_shell):
    shell = make_shell()
    shell.tour.skip_all()
    assert set(shell.settings.get(SEEN_KEY)) == set(CHAPTERS)
    shell.open_document("experiment")
    assert not shell.tour.running


def test_help_starts_it_again_from_any_tab(make_shell):
    shell = make_shell()
    shell.tour.skip_all()
    assert "Take the guided tour" in help_labels(shell)

    doc = shell.open_document("experiment")
    labels = help_labels(shell)
    assert labels[:2] == ["Tour of this tab", "Take the guided tour"]
    shell.tour.start_for(doc)
    assert shell.tour.chapter.name == "experiment"

    shell.tour.restart()
    assert shell.active is None and shell.tour.chapter.name == WORKBENCH
    assert shell.settings.get(SEEN_KEY) == []


def test_the_last_step_offers_the_practice_run(make_shell, practice_project):
    shell = make_shell()
    tour = shell.tour
    while tour.index < len(tour._steps) - 1:
        tour.next()
    tour.finish_then()
    assert tour.chapter.name == PRACTICE and shell.active is None


def test_the_callout_stays_inside_the_window():
    window, callout, gap = (1000, 700), (300, 200), 12
    for box in (None, (0, 0, 200, 600), (800, 100, 190, 100),
                (100, 600, 800, 90), (0, 0, 1000, 700), (990, 690, 5, 5)):
        x, y = place_callout(box, callout, window, gap)
        assert gap <= x <= window[0] - callout[0] - gap
        assert gap <= y <= window[1] - callout[1] - gap
    # Beside a narrow target, not over it.
    x, _ = place_callout((0, 0, 200, 600), callout, window, gap)
    assert x >= 200


def test_home_opens_with_no_step_selected(make_shell):
    shell = make_shell(tour=False)
    assert shell.home.current is None
    assert not any(card.selected for card in shell.home.cards.values())
    shell.home.select("plate")
    assert shell.home.cards["plate"].selected
    assert "home_card" not in shell.settings.data      # never remembered
    shell.home.select(None)
    assert not any(card.selected for card in shell.home.cards.values())


def test_every_review_step_points_at_something(make_shell, hermetic, monkeypatch):
    from results_review import discovery

    monkeypatch.setattr(discovery, "TIMECOURSE_RESULTS", hermetic)
    folder = hermetic / "ZZ Tour Test"
    folder.mkdir()
    (folder / discovery.CANDIDATES_CSV).write_text(
        "medium,medium_label,timepoint,hours,plate1,plate2,dilution,control_n,"
        "median_CV,control_mean,control_CV,n_strains,n_significant,rank_score,"
        "ranked_by,best_set_score\n"
        "GLU,Glucose,16 Hours,16.0,_9.JPG,_9_1.JPG,least,4,0.05,10.7,0.34,7,7,"
        "1.75,combined,4.72\n", encoding="utf-8")
    shell = make_shell()
    doc = shell.open_document("review", folder)
    shell.root.update()
    tour = shell.tour
    assert tour.chapter.name == "review" and tour.doc is doc
    for _ in range(len(tour._steps)):
        shell.root.update()
        assert tour._target_box() is not None, tour._steps[tour.index].title
        tour.next()


def test_handpicked_mode_adds_the_plates_step(make_shell):
    from experiments.model import QUANTIFY, Experiment

    shell = make_shell()
    shell.open_document("experiment", None, Experiment(mode=QUANTIFY))
    shell.root.update()
    assert "plates" in [s.key for s in shell.tour._steps]


# -- the practice run ---------------------------------------------------------------

def test_the_practice_layout_is_the_one_its_steps_build():
    from plate_template.gui.controller import EditorController
    from plate_template.presets import blank, lab_standard_8x6
    from workbench import practice

    controller = EditorController(blank())
    assert not practice.samples_placed(controller.template)
    practice.place_samples(controller)
    assert practice.samples_placed(controller.template)
    practice.add_dilutions(controller)
    assert practice.dilutions_added(controller.template)
    assert not practice.dilutions_added(controller.template, 2)
    practice.second_replicate(controller)
    assert not practice.plate_duplicated(controller.template)
    practice.duplicate_plate(controller)
    assert practice.plate_duplicated(controller.template)
    standard = lab_standard_8x6()
    assert ([[c.token for c in row] for p in controller.template.plates for row in p.cells]
            == [[c.token for c in row] for p in standard.plates for row in p.cells])


def test_the_practice_results_install_as_a_clean_copy(practice_project):
    from workbench import practice

    first = practice.install_results()
    assert first.name == practice.NAME and (first / practice.MARKER).exists()
    text = (first / "review.json").read_text(encoding="utf-8")
    assert practice.PLACEHOLDER not in text
    assert practice.photos_dir().is_dir()
    (first / "scribble.txt").write_text("x")
    assert not (practice.install_results() / "scribble.txt").exists()

    # Somebody's own results of the same name are never replaced.
    (first / practice.MARKER).unlink()
    assert practice.install_results().name == f"{practice.NAME} 2"


def test_the_practice_run_goes_from_a_blank_plate_to_reviewed_results(
        make_shell, practice_project):
    from workbench import practice

    shell = make_shell()
    tour = shell.tour
    assert tour.start_practice()
    surfaces = []
    for _ in range(len(tour._steps) + 5):
        if not tour.running:
            break
        shell.root.update()
        step, index = tour._steps[tour.index], tour.index
        surfaces.append(step.on)
        assert tour._surface(step)[0], step.title
        if step.target is not None:
            pump(shell, lambda: tour._target_box() is not None, 20, step.title)
        drawn(shell)
        assert tour._callout.winfo_ismapped(), step.title
        if step.done is not None:
            assert tour._callout.next.cget("text") == "Do it for me", step.title
            tour._callout.next.invoke()
            pump(shell, lambda: not tour.running or tour.index != index,
                 what=step.title)
        else:
            tour.next()
    assert not tour.running
    assert [s for i, s in enumerate(surfaces) if i == 0 or s != surfaces[i - 1]] == [
        "home", "plate", "experiment", "review"]
    kinds = [d.kind for d in shell.documents]
    assert kinds == ["plate", "experiment", "review"]
    experiment = shell.documents[1].app.controller.experiment
    assert practice.strains_named(experiment) and practice.references_set(experiment)
    assert experiment.categories == [practice.CATEGORY]
    assert set(TOOL_CHAPTERS) <= set(shell.settings.get(SEEN_KEY))


def test_a_practice_step_moves_on_when_the_person_does_it(make_shell, practice_project):
    from workbench import practice

    shell = make_shell()
    tour = shell.tour
    tour.start_practice()
    tour.next()
    shell.open_document("plate")              # the person, not the button
    pump(shell, lambda: tour._steps[tour.index].on == "plate", 5)
    tour.next()
    index = tour.index
    practice.place_samples(tour.docs["plate"].app.controller)
    pump(shell, lambda: tour.index == index + 1, 5)

    # Wandering off leaves the practice waiting, with a way back.
    shell.activate(None)
    pump(shell, lambda: tour._callout.next.cget("text") == "Go there", 5)
    tour._callout.next.invoke()
    assert shell.active is tour.docs["plate"]


def test_no_tool_chapter_starts_during_the_practice(make_shell, practice_project):
    shell = make_shell()
    shell.tour.start_practice()
    shell.open_document("experiment")
    shell.root.update()
    assert shell.tour.chapter.name == PRACTICE
