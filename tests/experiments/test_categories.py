"""Treatment categories: named ways treatments differ.

"Carbon source" and "Temperature" are the user's words for how Glucose + 37C
relates to Glycerol + 30C. They are stated once in the experiment designer,
never guessed from the treatment names.
"""

import json

import pytest

from experiments import schema
from experiments.gui.controller import ExperimentController
from experiments.model import Condition, Experiment, StrainGroup


def _experiment(**kw):
    return Experiment(
        name="Cats", id="exp-1", strains=["WT", "ΔA"], control_slot=1,
        conditions=[Condition("GLU30", "Glucose"), Condition("GLU37", "Glucose + 37C"),
                    Condition("GLY30", "Glycerol"), Condition("GLY37", "Glycerol + 37C")],
        **kw)


def test_categories_round_trip_and_bump_the_schema():
    e = _experiment(categories=["Carbon source", "Temperature"])
    e.conditions[0].categories = {"Carbon source": "Glucose", "Temperature": "30°C"}
    d = schema.to_dict(e)
    assert d["schema_version"] >= 8 and d["categories"] == ["Carbon source", "Temperature"]
    back = schema.from_dict(json.loads(json.dumps(d)))
    assert back.categories == ["Carbon source", "Temperature"]
    assert back.condition("GLU30").categories == {"Carbon source": "Glucose", "Temperature": "30°C"}
    assert back.condition("GLY30").categories == {}


def test_a_value_for_an_unknown_category_is_refused():
    d = schema.to_dict(_experiment(categories=["Temperature"]))
    d["conditions"][0]["categories"] = {"Carbon source": "Glucose"}
    with pytest.raises(schema.ExperimentError, match="Carbon source"):
        schema.from_dict(d)


def test_categories_are_shared_by_strain_groups():
    e = _experiment(categories=["Temperature"])
    e.condition("GLU37").categories = {"Temperature": "37°C"}
    e.strain_groups = {"A": StrainGroup(["WT", "ΔA"], 1,
                                        conditions={"GLU37": Condition("GLU37", "x", exclude=(2,),
                                                                       categories={"Temperature": "stale"})})}
    panel = e.for_group("A")
    assert panel.condition("GLU37").categories == {"Temperature": "37°C"}
    assert panel.condition("GLU37").exclude == (2,)
    d = schema.to_dict(e)
    assert "categories" not in d["strain_groups"]["A"]["conditions"][0]
    assert schema.from_dict(d).for_group("A").condition("GLU37").category("Temperature") == "37°C"


def test_controller_edits_categories_with_undo():
    ctl = ExperimentController(_experiment())
    ctl.add_category("Carbon source")
    ctl.add_category("temperature")
    with pytest.raises(ValueError):
        ctl.add_category("Temperature ")  # Same name, other spelling.

    assert ctl.set_category_value(["GLU30", "GLU37"], "Carbon source", "Glucose")
    # A second spelling of a value in use joins it rather than splitting the group.
    ctl.set_category_value(["GLY30"], "Carbon source", "glucose")
    assert ctl.experiment.condition("GLY30").category("Carbon source") == "Glucose"
    ctl.set_category_value(["GLY30"], "Carbon source", "")
    assert ctl.experiment.condition("GLY30").categories == {}

    ctl.set_category_value(["GLU37", "GLY37"], "temperature", "37°C")
    assert ctl.rename_category("temperature", "Temperature")
    assert ctl.experiment.condition("GLY37").categories == {"Temperature": "37°C"}
    assert ctl.move_category("Temperature", -1)
    assert ctl.experiment.categories == ["Temperature", "Carbon source"]

    assert ctl.remove_category("Carbon source")
    assert ctl.experiment.condition("GLU30").categories == {}
    ctl.undo()
    assert ctl.experiment.condition("GLU30").category("Carbon source") == "Glucose"


def test_run_info_reads_categories_live_from_the_experiment(tmp_path):
    from results_review.discovery import load_run_info

    e = _experiment(categories=["Temperature"])
    exp_path = tmp_path / "Cats.spotexp.json"
    review_path = tmp_path / "Cats.datareview.json"
    out = tmp_path / "results"
    out.mkdir()
    handoff = {"experiment": schema.to_dict(e), "data_review": {"file": str(review_path)}}
    (out / "experiment.json").write_text(json.dumps(handoff), encoding="utf-8")

    # No experiment file beside the review: the snapshot (no values yet) is used.
    info = load_run_info(out)
    assert info.categories == ["Temperature"] and info.condition_categories == {}

    # Values given after the run reach the results.
    e.condition("GLU37").categories = {"Temperature": "37°C"}
    schema.save(e, exp_path)
    info = load_run_info(out)
    assert info.condition_categories == {"GLU37": {"Temperature": "37°C"}}

    # A different experiment that happens to sit there is not believed.
    e.id = "someone-else"
    schema.save(e, exp_path)
    assert load_run_info(out).condition_categories == {}


def test_names_are_read_into_categories():
    from experiments.categories import ADDITIVE, CARBON, TEMPERATURE, guess, guess_all

    assert guess("Glucose + 37C") == {CARBON: "Glucose", TEMPERATURE: "37°C"}
    assert guess("Glycerol 37 °C") == {CARBON: "Glycerol", TEMPERATURE: "37°C"}
    assert guess("Glucose 37") == {CARBON: "Glucose", TEMPERATURE: "37°C"}
    assert guess("Glucose + NaAsO2") == {CARBON: "Glucose", ADDITIVE: "NaAsO2"}
    assert guess("K-OAc") == {CARBON: "K-OAc"}
    assert guess("Mystery medium") == {}
    # A truncated code still names the carbon source, but nothing more.
    assert guess("YPD", "GLUCOSE3") == {CARBON: "Glucose"}

    names, values, assumed = guess_all([("A", "Glucose"), ("B", "Glucose + 37C"),
                                        ("C", "Glycerol + H2O2"), ("D", "Odd")])
    assert names == [CARBON, TEMPERATURE, ADDITIVE]
    assert values["A"] == {CARBON: "Glucose", TEMPERATURE: "30°C", ADDITIVE: "None"}
    assert ("A", TEMPERATURE) in assumed and ("B", TEMPERATURE) not in assumed
    assert values["D"] == {}  # Nothing read, so nothing assumed either.


def test_guessed_categories_map_onto_the_experiments_own_names():
    from experiments.categories import ADDITIVE, TEMPERATURE, existing_name

    assert existing_name(TEMPERATURE, ["Carbon", "Temp"]) == "Temp"
    assert existing_name(ADDITIVE, ["Stressor"]) == "Stressor"
    assert existing_name(ADDITIVE, ["Carbon source"]) is None


def test_fill_categories_is_one_step_and_keeps_what_was_set():
    ctl = ExperimentController(_experiment(categories=["Temp"]))
    ctl.set_category_value(["GLU37"], "Temp", "hot")
    assert ctl.fill_categories({
        "GLU30": {"Carbon source": "Glucose", "temp": "30°C"},
        "GLU37": {"Carbon source": "glucose", "Temp": "37°C"}})
    e = ctl.experiment
    assert e.categories == ["Temp", "Carbon source"]
    assert e.condition("GLU37").categories == {"Temp": "hot", "Carbon source": "Glucose"}
    assert e.condition("GLU30").category("Temp") == "30°C"
    ctl.fill_categories({"GLU37": {"Temp": "37°C"}}, keep_existing=False)
    assert e.condition("GLU37").category("Temp") == "37°C"
    ctl.undo(); ctl.undo()
    assert ctl.experiment.categories == ["Temp"]


def test_conditions_order_by_category():
    from experiments.categories import order

    values = {"GLU30": {"C": "Glucose", "T": "30"}, "GLY30": {"C": "Glycerol", "T": "30"},
              "GLU37": {"C": "Glucose", "T": "37"}, "GLY37": {"C": "Glycerol", "T": "37"}}
    codes = ["GLY37", "GLU37", "GLY30", "GLU30", "X"]
    assert order(codes, values, ["C", "T"]) == ["GLU30", "GLU37", "GLY30", "GLY37", "X"]
    assert order(codes, values, ["C", "T"], first="T") == ["GLU30", "GLY30", "GLU37", "GLY37", "X"]
    assert order(codes, values, []) == codes
