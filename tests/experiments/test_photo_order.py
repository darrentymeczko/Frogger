"""Plates and strain groups read from each photo's position in its folder.

The bench this exists for spots three strain sets at once and files every photo
of one medium/time/treatment in one folder: set A plates 1 and 2 (replicates
1+2, 3+4), then set B's, then set C's. Nothing in the names says which is which.
"""

from pathlib import Path

import pytest

from experiments import intake, schema
from experiments.gui.controller import ExperimentController
from experiments.model import (
    SCHEMA_VERSION, Condition, Experiment, OrderSlot, StrainGroup,
)
from experiments.profiles import HOURS_PATTERNS, FacetRule, NamingProfile
from experiments.schema import ExperimentError
from experiments.validate import validate
from plate_template.schema import load

SETS = ("A", "B", "C")
FOLDERS = [f"YPD/30C/{h} Hours/{t}" for h in (24, 48) for t in ("Glucose", "Glucose + H2O2")]
# IMG_9 sorts before IMG_10 the way Explorer lists them, not as plain text does.
NAMES = [f"IMG_{i}.JPG" for i in range(9, 15)]
TEMPLATE = Path("plate_template/templates/lab_standard_8x6.json")


def write(root: Path, folder: str, names) -> None:
    (root / folder).mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / folder / name).write_bytes(b"x")


def bench_profile() -> NamingProfile:
    return NamingProfile("Bench", (
        FacetRule("timepoint", "segment", -3, HOURS_PATTERNS, "hours"),
        FacetRule("condition", "segment", -2, (r"^(.+)$",), "code"),
    ))


@pytest.fixture
def bench(tmp_path):
    for folder in FOLDERS:
        write(tmp_path, folder, NAMES)
    template = load(TEMPLATE)
    e = Experiment(
        name="Three sets", photo_root=str(tmp_path), template_id=template.id,
        conditions=[Condition("GLU", "Glucose"), Condition("GLU-H2O2", "Glucose + H2O2")],
        profile=bench_profile(),
        strain_groups={k: StrainGroup([f"{k}{i}" for i in range(8)], 1) for k in SETS})
    ctl = ExperimentController(e)
    ctl.template = template
    ctl.files, _ = intake.scan_images(tmp_path)
    ctl.reresolve()
    return ctl


def placed(ctl, folder, names=NAMES):
    rows = {r.relpath: r for r in ctl.resolution.rows}
    return [(rows[f"{folder}/{n}"].set_key, rows[f"{folder}/{n}"].plate) for n in names]


def test_without_an_order_nothing_is_guessed(bench):
    assert all({"plate", "set"} <= set(r.missing) for r in bench.resolution.rows)


def test_each_position_names_a_strain_group_and_plate(bench):
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    for folder in FOLDERS:
        assert placed(bench, folder) == [("A", 1), ("A", 2), ("B", 1), ("B", 2), ("C", 1), ("C", 2)]
    assert all(r.is_ok for r in bench.resolution.rows)
    assert not [i for i in bench.issues() if i.is_error]
    # Every group has a complete sitting at every time and treatment.
    for key in SETS:
        groups = intake.group_resolution(bench.resolution, key).by_group()
        assert len(groups) == 4 and all(set(p) == {1, 2} for p in groups.values())


def test_plate_one_of_every_group_first(bench):
    bench.set_photo_order(intake.build_order(SETS, (1, 2), groups_first=False))
    assert placed(bench, FOLDERS[0]) == [("A", 1), ("B", 1), ("C", 1), ("A", 2), ("B", 2), ("C", 2)]


def test_a_folder_that_does_not_fit_is_left_alone_until_an_extra_is_ignored(bench, tmp_path):
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    odd = FOLDERS[3]
    write(tmp_path, odd, ["IMG_15.JPG"])
    bench.files, _ = intake.scan_images(tmp_path)
    bench.reresolve()
    rows = [r for r in bench.resolution.rows if r.relpath.startswith(odd + "/")]
    assert len(rows) == 7
    assert all(r.status == "unresolved" and r.plate is None for r in rows)
    assert all("7 photos" in r.note and "lists 6" in r.note for r in rows)
    assert bench.resolution.order_mismatches == {odd: 7}
    codes = {i.code for i in bench.issues()}
    assert {"photo_order_mismatch", "unassigned_strain_group"} <= codes
    assert all(r.is_ok for r in bench.resolution.rows if not r.relpath.startswith(odd + "/"))

    # Leaving out the blurred first shot makes the rest fit, in order.
    bench.set_ignored([f"{odd}/IMG_9.JPG"], True)
    rows = {r.relpath: r for r in bench.resolution.rows}
    assert rows[f"{odd}/IMG_9.JPG"].status == "ignored"
    assert [(rows[f"{odd}/IMG_{i}.JPG"].set_key, rows[f"{odd}/IMG_{i}.JPG"].plate)
            for i in range(10, 16)] == [("A", 1), ("A", 2), ("B", 1), ("B", 2), ("C", 1), ("C", 2)]
    assert not [i for i in bench.issues() if i.is_error]


def test_hand_corrections_beat_the_order(bench):
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    path = f"{FOLDERS[0]}/IMG_9.JPG"
    bench.override(path, "set", "C")
    row = next(r for r in bench.resolution.rows if r.relpath == path)
    assert (row.set_key, row.plate) == ("C", 1)


def test_the_order_replaces_a_name_based_plate_reading(bench, tmp_path):
    bench.experiment.profile.rules += (FacetRule("plate", "stem", -1, (r"IMG_(\d+)",), "int"),)
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    assert {r.plate for r in bench.resolution.rows} == {1, 2}
    # A folder the order does not fit must not fall back to the filename.
    write(tmp_path, FOLDERS[0], ["IMG_15.JPG"])
    bench.files, _ = intake.scan_images(tmp_path)
    bench.reresolve()
    assert {r.plate for r in bench.resolution.rows if r.relpath.startswith(FOLDERS[0] + "/")} == {None}


def test_one_panel_with_unlabelled_replicate_photos(tmp_path):
    """The R1+2 / R3+4 case: one strain panel, two plates, nothing in the names."""
    for folder in ("24 Hours/Glucose", "48 Hours/Glucose"):
        write(tmp_path, folder, ["DSC_0001.JPG", "DSC_0002.JPG"])
    e = Experiment(name="One set", photo_root=str(tmp_path), strains=[f"S{i}" for i in range(8)],
                   control_slot=1, conditions=[Condition("GLU", "Glucose")],
                   profile=bench_profile(), photo_order=(OrderSlot(1), OrderSlot(2)))
    files, _ = intake.scan_images(tmp_path)
    res = intake.resolve(e, files)
    assert [(r.relpath.rsplit("/", 1)[1], r.plate, r.set_key) for r in res.rows][:2] == [
        ("DSC_0001.JPG", 1, None), ("DSC_0002.JPG", 2, None)]
    assert all(r.is_ok for r in res.rows)
    template = load(TEMPLATE)
    e.template_id = template.id
    errors = [i for i in validate(e, template) + intake.check_resolution(e, res, template) if i.is_error]
    assert not errors, errors  # in particular, no "cannot read plate" profile error


def test_round_trip_and_newer_file_version(bench):
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    d = schema.to_dict(bench.experiment)
    assert d["photos"]["order"][:2] == [{"plate": 1, "group": "A"}, {"plate": 2, "group": "A"}]
    assert d["schema_version"] == SCHEMA_VERSION >= 4
    again = schema.loads_experiment(schema.dumps_experiment(bench.experiment))
    assert again.photo_order == bench.experiment.photo_order
    assert "order" not in schema.to_dict(Experiment())["photos"]
    one = schema.to_dict(Experiment(photo_order=(OrderSlot(2),)))
    assert one["photos"]["order"] == [{"plate": 2}]


@pytest.mark.parametrize("order, message", [
    ("A1", "expected list"),
    ([{"group": "A"}], "missing required key 'plate'"),
    ([{"plate": 0}], "start at 1"),
    ([{"plate": True}], "start at 1"),
    ([{"plate": 1, "group": 2}], "expected str"),
])
def test_malformed_orders_are_refused_with_their_path(order, message):
    d = schema.to_dict(Experiment())
    d["photos"]["order"] = order
    with pytest.raises(ExperimentError, match=message) as exc:
        schema.from_dict(d)
    assert "$.photos.order" in str(exc.value)


def test_validation_names_bad_groups_duplicates_and_plates(bench):
    e = bench.experiment
    e.photo_order = (OrderSlot(1, "A"), OrderSlot(1, "A"), OrderSlot(3, "B"), OrderSlot(1, "Z"))
    found = {i.code: i for i in validate(e, bench.template)}
    assert found["photo_order_duplicate"].is_error
    assert found["photo_order_plate"].is_error and "3" in found["photo_order_plate"].message
    assert found["photo_order_groups"].is_error and "'Z'" in found["photo_order_groups"].message
    # Reported once for the experiment, not once per strain group.
    assert sum(i.code == "photo_order_duplicate" for i in validate(e, bench.template)) == 1

    e.photo_order = intake.build_order(("A", "B"), (1, 2))
    missing_c = [i for i in validate(e, bench.template) if i.code == "photo_order_groups"]
    assert missing_c and not missing_c[0].is_error and "'C'" in missing_c[0].message

    e.photo_order = (OrderSlot(1, "A"), OrderSlot(2))
    assert any(i.code == "photo_order_groups" and i.is_error for i in validate(e, bench.template))


def test_controller_refuses_bad_orders_and_undoes_good_ones(bench):
    before = bench.snapshot()
    with pytest.raises(ValueError, match="only once"):
        bench.set_photo_order((OrderSlot(1, "A"), OrderSlot(1, "A")))
    with pytest.raises(ValueError, match="Z"):
        bench.set_photo_order((OrderSlot(1, "Z"),))
    assert bench.snapshot() == before

    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    assert all(r.is_ok for r in bench.resolution.rows)
    bench.undo()
    assert bench.experiment.photo_order == ()
    assert not any(r.is_ok for r in bench.resolution.rows)
    bench.redo()
    assert all(r.is_ok for r in bench.resolution.rows)


def test_new_data_folder_keeps_the_order(bench, tmp_path_factory):
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    other = tmp_path_factory.mktemp("next_week")
    write(other, "YPD/30C/72 Hours/Glucose", NAMES)
    bench.set_photo_root(other, infer=False)
    assert bench.experiment.photo_order == intake.build_order(SETS, (1, 2))
    assert placed(bench, "YPD/30C/72 Hours/Glucose") == [
        ("A", 1), ("A", 2), ("B", 1), ("B", 2), ("C", 1), ("C", 2)]


def test_timecourse_run_gives_each_group_only_its_positions(bench, tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from experiments import run
    from results_review import discovery
    from tests.results_review.test_run_info import CANDIDATES
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    _, tc = run._pipeline()
    seen = {}

    def score(tree, args, cfg, **kwargs):
        seen[tree.set_hint] = sorted((s.path.name, s.plate) for s in kwargs["shots"])
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / discovery.CANDIDATES_CSV).write_text(CANDIDATES, encoding="utf-8")
        return 0

    monkeypatch.setattr(tc, "run_one", score)
    monkeypatch.setattr(run, "_configure_timecourse_workers", lambda *args: None)
    assert run.run(bench.experiment, outdir=tmp_path / "results", res=bench.resolution,
                   template=bench.template) == 0
    # Four folders (two times x two treatments), one photo per plate in each.
    assert seen == {
        "A": sorted([("IMG_9.JPG", 1), ("IMG_10.JPG", 2)] * 4),
        "B": sorted([("IMG_11.JPG", 1), ("IMG_12.JPG", 2)] * 4),
        "C": sorted([("IMG_13.JPG", 1), ("IMG_14.JPG", 2)] * 4),
    }


def test_review_rebuilds_from_the_positions_not_the_names(bench, tmp_path, monkeypatch):
    from experiments import run
    from results_review import discovery, rebuild
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    output = tmp_path / "results"
    run.write_handoff(bench.experiment.for_group("B"),
                      intake.group_resolution(bench.resolution, "B"), output)
    (output / discovery.CANDIDATES_CSV).write_text(
        "medium,medium_label,timepoint,hours,plate1,plate2,dilution,control_n,"
        "median_CV,control_mean,control_CV,n_strains,n_significant,rank_score,"
        "ranked_by,best_set_score\n"
        "GLU,Glucose,24 Hours,24.0,IMG_11.JPG,IMG_12.JPG,least,4,0.05,10.7,0.34,7,7,"
        "1.75,combined,4.72\n", encoding="utf-8")
    review = discovery.load_set(output)
    monkeypatch.setattr(rebuild, "tree_candidates", lambda *_a, **_kw: pytest.fail("must not re-scan names"))
    got = rebuild.pipeline_candidate(tmp_path, review.candidates[0], review.experiment.pipeline_config)
    assert [s.path.relative_to(tmp_path).as_posix() for s in got["plates"]] == [
        f"{FOLDERS[0]}/IMG_11.JPG", f"{FOLDERS[0]}/IMG_12.JPG"]


def test_dialog_builds_checks_and_applies_the_order(app_tk_root, bench):
    import tkinter as tk
    from experiments.app import ExperimentApp
    from experiments.gui.data import _BY_ORDER
    from experiments.gui.order import PhotoOrderDialog
    win = tk.Toplevel(app_tk_root)
    win.withdraw()
    try:
        app = ExperimentApp(win, bench.experiment)
        app.controller.template = bench.template
        app.controller.files = bench.files
        app.controller.reresolve()
        app.refresh()
        assert "Photo order in each folder" in app.data.simple_rules["plate"]["example"].cget("text")

        dialog = PhotoOrderDialog(app.data, app.controller, app.refresh)
        assert dialog.order() == intake.build_order(SETS, (1, 2))
        assert dialog.plate_box.get(0) == "Plate 1 (reps 1+2)"
        assert "All 4 folders hold 6 photos" in dialog.fit.cget("text")
        assert dialog.table.item(dialog.table.get_children()[0])["values"][1:] == [
            "A", "Plate 1 (reps 1+2)", "IMG_9.JPG"]
        dialog.group_box.selection_set(1)
        dialog._move(dialog.group_box, -1)
        dialog.groups_first.set(False)
        dialog._changed()
        assert dialog.order()[:3] == (OrderSlot(1, "B"), OrderSlot(1, "A"), OrderSlot(1, "C"))
        dialog._apply()
        assert app.controller.experiment.photo_order[:3] == (
            OrderSlot(1, "B"), OrderSlot(1, "A"), OrderSlot(1, "C"))
        for facet in ("plate", "set"):
            fields = app.data.simple_rules[facet]
            assert fields["selected"].get() == _BY_ORDER
            assert str(fields["choice_box"].cget("state")) == "disabled"
        assert "By position in each folder: B plate 1, A plate 1" in \
            app.data.simple_rules["plate"]["example"].cget("text")

        # Re-opening recognises the saved order instead of resetting it.
        dialog = PhotoOrderDialog(app.data, app.controller, app.refresh)
        assert dialog.custom is None and dialog.groups_first.get() is False
        assert dialog.group_order == ["B", "A", "C"]
        dialog._clear()
        assert app.controller.experiment.photo_order == ()
        assert str(app.data.simple_rules["plate"]["choice_box"].cget("state")) == "readonly"
    finally:
        win.destroy()


def test_data_table_explains_a_folder_that_does_not_fit(app_tk_root, bench, tmp_path):
    import tkinter as tk
    from experiments.app import ExperimentApp
    write(tmp_path, FOLDERS[0], ["IMG_15.JPG"])
    bench.set_photo_order(intake.build_order(SETS, (1, 2)))
    win = tk.Toplevel(app_tk_root)
    win.withdraw()
    try:
        app = ExperimentApp(win, bench.experiment)
        app.controller.template = bench.template
        app.controller.files, _ = intake.scan_images(tmp_path)
        app.controller.reresolve()
        app.refresh()
        status = app.data.table.set(f"{FOLDERS[0]}/IMG_9.JPG", "status")
        assert "needs plate" in status and "its folder has 7 photos" in status
        assert "1 folder holds a different number" in \
            app.data.simple_rules["plate"]["example"].cget("text")
    finally:
        win.destroy()


def test_adding_strain_groups_from_the_dialog(app_tk_root, tmp_path, monkeypatch):
    import tkinter as tk
    from experiments.app import ExperimentApp
    from experiments.gui import groups
    from experiments.gui.order import PhotoOrderDialog
    write(tmp_path, FOLDERS[0], NAMES[:4])
    e = Experiment(photo_root=str(tmp_path), strains=["WT", "Mutant"], control_slot=1,
                   profile=bench_profile())
    win = tk.Toplevel(app_tk_root)
    win.withdraw()
    try:
        app = ExperimentApp(win, e)
        dialog = PhotoOrderDialog(app.data, app.controller, app.refresh)
        assert dialog.group_order == [None]
        assert dialog.order() == (OrderSlot(1), OrderSlot(2))
        assert "This folder has 4 photos but the order lists 2" in dialog.fit.cget("text")
        replies = iter(["Set A", "Set B"])
        monkeypatch.setattr(groups, "_ask_text", lambda *a, **kw: next(replies))
        dialog._add_group()
        assert dialog.group_order == ["Set A", "Set B"]
        assert "All 1 folder holds 4 photos" in dialog.fit.cget("text")
        dialog._apply()
        assert placed(app.controller, FOLDERS[0], NAMES[:4]) == [
            ("Set A", 1), ("Set A", 2), ("Set B", 1), ("Set B", 2)]
    finally:
        win.destroy()
