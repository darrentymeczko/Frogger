"""Keyword rules: "photos whose name contains X are Y".

The middle tier between detecting names and correcting photos one by one. The
cases here are the ones a new user hits first: a folder called `Gluc` that the
detector turned into a treatment nobody declared, camera numbers that happen to
say which plate, and a tree with no hours anywhere in it.
"""

from pathlib import Path

import pytest

from experiments import intake, schema
from experiments.gui.controller import ExperimentController
from experiments.intake import ImageFile, resolve
from experiments.model import (
    SCHEMA_VERSION, TIMECOURSE, Condition, Experiment, OrderSlot, StrainGroup,
)
from experiments.profiles import (
    FacetRule, KeywordRule, NamingProfile, ProfileError, apply_keywords, keyword_from_dict,
)
from experiments.schema import ExperimentError
from experiments.validate import validate


def files(*relpaths):
    return [ImageFile(relpath=r, parts=tuple(r.split("/"))) for r in sorted(relpaths)]


def parts(relpath):
    return tuple(relpath.split("/"))


TREE = files(
    "24 Hours/Gluc/IMG_9.JPG", "24 Hours/Gluc/IMG_10.JPG",
    "24 Hours/Gluc + H2O2/IMG_9.JPG", "24 Hours/Gluc + H2O2/IMG_10.JPG",
    "48 Hours/Gluc/IMG_9.JPG", "48 Hours/Gluc/IMG_10.JPG",
    "48 Hours/Gluc + H2O2/IMG_9.JPG", "48 Hours/Gluc + H2O2/IMG_10.JPG",
)
HOURS_ONLY = NamingProfile("Hours", (
    FacetRule("timepoint", "segment", -3, (r"(\d+)\s*h",), "hours"),))


def experiment(**kw):
    kw = {"name": "K", "mode": TIMECOURSE, "photo_root": "/root", "profile": HOURS_ONLY,
          "conditions": [Condition("GLU", "Glucose"), Condition("GLU-H2O2", "Glucose + H2O2")],
          **kw}
    return Experiment(**kw)


def rows(e, found=TREE):
    return {r.relpath: r for r in resolve(e, found).rows}


# --- matching ---------------------------------------------------------------


def test_matching_is_a_case_insensitive_substring_test_in_the_chosen_place():
    p = parts("24 Hours/Gluc + H2O2/IMG_9.JPG")
    assert KeywordRule("gluc", "condition", "GLU").matches(p)
    assert KeywordRule("h2o2", "condition", "GLU", "folder").matches(p)
    assert not KeywordRule("h2o2", "condition", "GLU", "filename").matches(p)
    assert KeywordRule("img_9", "plate", 1, "filename").matches(p)
    assert not KeywordRule("img_9", "plate", 1, "folder").matches(p)
    assert not KeywordRule("Glycerol", "condition", "GLY").matches(p)


def test_the_longest_matching_keyword_wins_for_a_facet():
    rules = (KeywordRule("Gluc", "condition", "GLU"),
             KeywordRule("Gluc + H2O2", "condition", "GLU-H2O2"),
             KeywordRule("IMG_9", "plate", 1, "filename"))
    assert apply_keywords(parts("24 Hours/Gluc + H2O2/IMG_9.JPG"), rules) == {
        "condition": "GLU-H2O2", "plate": 1}
    assert apply_keywords(parts("24 Hours/Gluc/IMG_10.JPG"), rules) == {"condition": "GLU"}
    # Listing the general one last changes nothing: length decides, not order.
    assert apply_keywords(parts("24 Hours/Gluc + H2O2/IMG_9.JPG"), rules[::-1])["condition"] == "GLU-H2O2"


def test_hours_keywords_carry_the_label_the_pipeline_names_folders_after():
    assert apply_keywords(parts("day2/x.jpg"), (KeywordRule("day2", "timepoint", 48.0),)) == {
        "timepoint": 48.0, "timepoint_label": "48 Hours"}


@pytest.mark.parametrize("rule, message", [
    (KeywordRule("", "condition", "GLU"), "needs some text"),
    (KeywordRule("x", "colour", "GLU"), "unknown facet"),
    (KeywordRule("x", "plate", 0), "start at 1"),
    (KeywordRule("x", "plate", "1"), "start at 1"),
    (KeywordRule("x", "timepoint", -1.0), "nonnegative"),
    (KeywordRule("x", "condition", "GLU", "everywhere"), "unknown place"),
    (KeywordRule("x", "condition", ""), "no value"),
])
def test_malformed_rules_say_what_is_wrong(rule, message):
    with pytest.raises(ProfileError, match=message):
        rule.check()


# --- resolving --------------------------------------------------------------


def test_keywords_fix_a_treatment_the_detector_misread():
    e = experiment(profile=NamingProfile("Tree", (
        *HOURS_ONLY.rules, FacetRule("condition", "segment", -2, (r"^(.+)$",), "code"))))
    before = rows(e)
    assert before["24 Hours/Gluc/IMG_9.JPG"].condition == "GLUC"      # undeclared
    e.keywords = (KeywordRule("Gluc", "condition", "GLU"),
                  KeywordRule("Gluc + H2O2", "condition", "GLU-H2O2"))
    after = rows(e)
    assert after["24 Hours/Gluc/IMG_9.JPG"].condition == "GLU"
    assert after["48 Hours/Gluc + H2O2/IMG_10.JPG"].condition == "GLU-H2O2"
    assert after["24 Hours/Gluc/IMG_9.JPG"].condition_label == "GLU"


def test_keywords_supply_plates_from_camera_numbers_and_complete_the_rows():
    e = experiment(keywords=(
        KeywordRule("Gluc", "condition", "GLU"),
        KeywordRule("Gluc + H2O2", "condition", "GLU-H2O2"),
        KeywordRule("IMG_9", "plate", 1, "filename"),
        KeywordRule("IMG_10", "plate", 2, "filename")))
    got = rows(e)
    assert all(r.status == "ok" for r in got.values()), [r.missing for r in got.values()]
    assert (got["48 Hours/Gluc/IMG_10.JPG"].plate, got["48 Hours/Gluc/IMG_10.JPG"].timepoint) == (2, 48.0)


def test_precedence_hand_corrections_beat_keywords_which_beat_the_order_and_profile():
    e = experiment(
        profile=NamingProfile("Tree", (
            *HOURS_ONLY.rules,
            FacetRule("condition", "segment", -2, (r"^(.+)$",), "code"),
            FacetRule("plate", "stem", -1, (r"IMG_(\d+)",), "int"))),
        photo_order=(OrderSlot(1), OrderSlot(2)),
        keywords=(KeywordRule("Gluc", "condition", "GLU"),
                  KeywordRule("IMG_10", "plate", 7, "filename")),
        overrides={"24 Hours/Gluc/IMG_10.JPG": {"plate": 3}})
    got = rows(e, files("24 Hours/Gluc/IMG_9.JPG", "24 Hours/Gluc/IMG_10.JPG"))
    assert got["24 Hours/Gluc/IMG_9.JPG"].plate == 1          # order, not the 9 in the name
    assert got["24 Hours/Gluc/IMG_10.JPG"].plate == 3         # hand correction over keyword 7
    e.overrides.clear()
    assert rows(e, files("24 Hours/Gluc/IMG_10.JPG"))["24 Hours/Gluc/IMG_10.JPG"].plate == 7


def test_strain_group_keywords_identify_panels():
    e = experiment(strain_groups={k: StrainGroup([f"{k}{i}" for i in range(8)], 1) for k in "AB"},
                   keywords=(KeywordRule("setA", "set", "A"), KeywordRule("setB", "set", "B"),
                             KeywordRule("Gluc", "condition", "GLU"),
                             KeywordRule("_9", "plate", 1), KeywordRule("_10", "plate", 2)))
    got = rows(e, files("24 Hours/Gluc/setA_9.JPG", "24 Hours/Gluc/setB_10.JPG"))
    assert got["24 Hours/Gluc/setA_9.JPG"].set_key == "A"
    assert got["24 Hours/Gluc/setB_10.JPG"].set_key == "B"
    assert all(r.status == "ok" for r in got.values())


# --- validation -------------------------------------------------------------


def codes(issues):
    return {i.code for i in issues}


def test_keyword_rules_satisfy_the_facets_the_profile_cannot_read():
    e = experiment()
    assert "profile_incomplete" in codes(validate(e))
    e.keywords = (KeywordRule("Gluc", "condition", "GLU"), KeywordRule("IMG_9", "plate", 1))
    assert "profile_incomplete" not in codes(validate(e))


def test_photos_assigned_entirely_by_hand_are_enough_to_run():
    """The documented 'current validation limitation' is gone: a table every
    row of which was corrected by hand no longer fails on the profile."""
    e = experiment(profile=NamingProfile())
    issue = next(i for i in validate(e) if i.code == "profile_incomplete")
    assert "treatment, plate number, hours" in issue.message and "keyword rule" in issue.message
    e.overrides = {"a.jpg": {"condition": "GLU", "plate": 1, "timepoint": 24.0}}
    assert "profile_incomplete" not in codes(validate(e))


def test_a_broken_keyword_rule_is_reported_not_raised():
    e = experiment(keywords=(KeywordRule("x", "plate", 0),))
    assert "keyword_invalid" in codes(validate(e))


# --- schema -----------------------------------------------------------------


def test_round_trip_and_version():
    e = experiment(keywords=(KeywordRule("Gluc", "condition", "GLU"),
                             KeywordRule("IMG_9", "plate", 1, "filename"),
                             KeywordRule("day2", "timepoint", 48.0, "folder")))
    d = schema.to_dict(e)
    assert d["schema_version"] == SCHEMA_VERSION >= 7
    assert d["photos"]["keywords"][1] == {"text": "IMG_9", "facet": "plate", "value": 1, "where": "filename"}
    assert schema.from_dict(d).keywords == e.keywords
    assert "keywords" not in schema.to_dict(experiment())["photos"]


def test_numbers_are_coerced_and_defaults_filled_when_read():
    assert keyword_from_dict({"text": "p1", "facet": "plate", "value": 1.0}, "$") == KeywordRule("p1", "plate", 1)
    assert keyword_from_dict({"text": "d", "facet": "timepoint", "value": 24}, "$").value == 24.0


@pytest.mark.parametrize("item, message", [
    ("Gluc", r"\$\.photos\.keywords\[0\]: expected an object"),
    ({"facet": "plate", "value": 1}, r"missing required key 'text'"),
    ({"text": "x", "facet": "plate", "value": "one"}, "start at 1"),
])
def test_malformed_keywords_are_refused_with_their_path(item, message):
    d = schema.to_dict(experiment())
    d["photos"]["keywords"] = [item]
    with pytest.raises(ExperimentError, match=message):
        schema.from_dict(d)


# --- controller -------------------------------------------------------------


@pytest.fixture
def bench(tmp_path):
    for f in TREE:
        (tmp_path / f.relpath).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f.relpath).write_bytes(b"x")
    e = experiment(photo_root=str(tmp_path))
    ctl = ExperimentController(e)
    ctl.files, _ = intake.scan_images(tmp_path)
    ctl.reresolve()
    return ctl


def test_controller_adds_checks_edits_and_undoes_rules(bench):
    e = bench.experiment
    assert bench.add_keyword("Gluc", "condition", "Glucose")
    assert e.keywords == (KeywordRule("Gluc", "condition", "GLU"),)
    assert bench.keyword_matches(e.keywords[0]) == 8
    # A new treatment name is declared on the way, like a per-photo correction.
    bench.add_keyword("H2O2", "condition", "Peroxide")
    assert e.has_condition(e.keywords[1].value) and e.condition(e.keywords[1].value).label == "Peroxide"
    # Re-adding the same keyword for the same detail edits it in place.
    bench.add_keyword("gluc", "condition", "Glucose + H2O2")
    assert [k for k in e.keywords if k.text.casefold() == "gluc"] == [
        KeywordRule("gluc", "condition", "GLU-H2O2")]
    bench.add_keyword("IMG_9", "plate", " 1 ", "filename")
    bench.add_keyword("IMG_10", "plate", "2", "filename")
    assert all(r.status == "ok" for r in bench.resolution.rows)
    with pytest.raises(ValueError, match="start at 1"):
        bench.add_keyword("x", "plate", "0")
    with pytest.raises(ValueError, match="number"):
        bench.add_keyword("x", "timepoint", "two days")
    with pytest.raises(ValueError, match="Panel first"):
        bench.add_keyword("x", "set", "A")
    with pytest.raises(ValueError, match="text to look for"):
        bench.add_keyword("  ", "plate", "1")
    n = len(e.keywords)
    assert bench.remove_keywords([e.keywords[-1]]) and len(e.keywords) == n - 1
    # Undo swaps in a restored experiment, so look it up again.
    assert bench.undo() == "Remove 1 keyword rule(s)" and len(bench.experiment.keywords) == n


def test_renaming_or_removing_a_condition_keeps_its_keywords_in_step(bench):
    e = bench.experiment
    bench.add_keyword("Gluc", "condition", "Glucose")
    bench.add_keyword("H2O2", "condition", "Glucose + H2O2")
    bench.rename_condition("GLU", "GLUCOSE", "Glucose")
    assert e.keywords[0].value == "GLUCOSE"
    bench.remove_conditions(["GLU-H2O2"])
    assert [k.value for k in e.keywords] == ["GLUCOSE"]


def test_a_new_data_folder_keeps_the_rules_but_not_the_photo_corrections(bench, tmp_path_factory):
    bench.add_keyword("Gluc", "condition", "Glucose")
    bench.override_many(["24 Hours/Gluc/IMG_9.JPG"], "plate", 1)
    other = tmp_path_factory.mktemp("other")
    (other / "Gluc").mkdir()
    (other / "Gluc" / "a.jpg").write_bytes(b"x")
    bench.set_photo_root(other)
    assert bench.experiment.keywords and not bench.experiment.overrides
    assert bench.resolution.rows[0].condition == "GLU"


# --- the dialog -------------------------------------------------------------


def test_dialog_previews_matches_and_adds_the_rule(app_tk_root, bench):
    import tkinter as tk
    from experiments.app import ExperimentApp
    from experiments.gui.keywords import KeywordDialog, KeywordRulesDialog
    win = tk.Toplevel(app_tk_root)
    win.withdraw()
    try:
        app = ExperimentApp(win, bench.experiment)
        app.controller.files = bench.files
        app.controller.reresolve()
        app.refresh()
        assert app.data.keyword_summary.cget("text") == "none yet"

        # Opening the rules window with no rules goes straight to adding one,
        # seeded from the search box.
        app.data.search.set("h2o2")
        rules = KeywordRulesDialog(app.data, app.controller, app.refresh, text=app.data.search.get())
        dialog = next(w for w in rules.winfo_children() if isinstance(w, KeywordDialog))
        assert dialog.rule() == KeywordRule("h2o2", "condition", None, "anywhere")
        assert "Matches 4 of 8 photos" in dialog.preview.cget("text")
        dialog.where.set("file name")
        dialog._preview()
        assert "No photo's file name contains" in dialog.preview.cget("text")
        dialog.where.set("folder names")
        dialog.value.set("Glucose + H2O2")
        dialog._apply()
        assert app.controller.experiment.keywords == (
            KeywordRule("h2o2", "condition", "GLU-H2O2", "folder"),)
        assert rules.table.item(rules.table.get_children()[0])["values"] == [
            "h2o2", "folder names", "Treatment: Glucose + H2O2", 4]
        assert app.data.keyword_summary.cget("text") == "1 rule, 4 photos matched"

        # Editing a saved rule opens it filled in.
        rules.table.selection_set(rules.table.get_children()[0])
        dialog = KeywordDialog(rules, app.controller, rules._changed, initial=rules.selected()[0])
        assert (dialog.text.get(), dialog.facet.get(), dialog.value.get(), dialog.where.get()) == (
            "h2o2", "Treatment", "Glucose + H2O2", "folder names")
        dialog.facet.set("Plate number")
        dialog._facet_changed()
        assert "template's plate number" in dialog.value_hint.cget("text")
        dialog.destroy()

        rules._remove()
        assert app.controller.experiment.keywords == ()
        assert app.data.keyword_summary.cget("text") == "none yet"
        rules.destroy()
    finally:
        win.destroy()
