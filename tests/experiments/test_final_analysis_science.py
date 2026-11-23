"""Production measurement -> selection -> review -> statistics -> export/reopen.

Only the known synthetic lattice and agar sample locations are supplied. No
measurement, normalization, statistics, CSV, graph or slide renderer is mocked.
"""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import json
import sys

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import spotting_quant as sq
import spotting_timecourse as tc
import spotting_batch as sb
import spotting_plots as sp
from spotting_roi import ROISettings
from experiments import analysis, schema, run as erun, intake
from experiments.model import Experiment, Condition, Statistics
from plate_template.presets import lab_standard_8x6
import results_review
from results_review import discovery, review as rv, rebuild as rb, export as ex
from results_review.gui.controller import ReviewController


def scientific_workflow(out, monkeypatch):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    photos = out / "photos"; photos.mkdir(exist_ok=True)
    cache = out / ".spotting_cache"
    monkeypatch.setattr(results_review, "PROJECT_ROOT", out)
    monkeypatch.setattr(sb, "PROJECT_ROOT", out)
    monkeypatch.setattr(tc, "PROJECT_ROOT", out, raising=False)
    centers = np.array([[(85.25 + 95*r, 85.75 + 95*c) for c in range(8)] for r in range(6)])
    grid = sq.Grid(centers, 31, 31, 62, (330, 425), 600, plate_known=False)
    monkeypatch.setattr(sq, "detect_grid", lambda *a, **k: replace(grid))
    monkeypatch.setattr(sq, "background_gap_centers", lambda *a: [(40,40),(40,140),(40,240),(40,340),(40,440)])
    yy, xx = np.mgrid[:660, :850]
    names = ["WT BY", "EV BY", "mutant", "low", "WT-MATa", "EV_other", "SWT", "DEV1"]
    shots = []
    for medium in ("A", "B"):
        for plate in (1, 2):
            pixels = np.full(yy.shape, 30, dtype=np.uint8)
            for r, c in np.ndindex(6, 8):
                cy, cx = centers[r, c]
                rad = np.hypot(yy+.5-cy, xx+.5-cx)
                values = [85 + r + plate, 135 + 2*r - plate, 106 + 3*r + plate,
                          30, 92 + r, 122 + r, 102 + r + plate, 115 + r]
                pixels[rad < 31] = values[c] + (3 if medium == "B" and c != 3 else 0)
                pixels[(rad >= 27) & (rad < 31)] = 230
            path = photos / f"{medium}-{plate}.png"
            Image.fromarray(pixels).save(path)
            shots.append(tc.Shot(path, 24., "24 Hours", medium, f"Treatment {medium}", plate))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in photos.glob("*.png")}
    final = analysis.validate({"normalization": {"*": 1},
                               "references": {"*": ["WT BY"], "B": ["EV BY"]}})
    e = Experiment(name="Task03 synthetic", strains=names, conditions=[
        Condition(c, f"Treatment {c}", dilution={"mode": "condition", "choice": "least"}) for c in ("A", "B")],
        photo_root=str(photos), final_analysis=final, statistics=Statistics(p_adjust="holm"))
    cfg = erun.to_pipeline_config(e)
    assert cfg["control_col"] == 1 and e.control_slot is None
    cfg["resolved_photos"] = [dict(condition=s.medium, condition_label=s.medium_label,
        timepoint=24., timepoint_label=s.tp_label, plate=s.plate,
        relpath=s.path.relative_to(photos).as_posix()) for s in shots]
    dest = out / "timecourse"; dest.mkdir(exist_ok=True)
    (dest / "experiment.json").write_text(json.dumps(dict(experiment=schema.to_dict(e),
        photo_root=str(photos), pipeline_config=cfg, strains=names)), encoding="utf-8")
    args = SimpleNamespace(cache_dir=cache, workers=1, timing=False, figures="none",
                           estimate=False, rank_by="combined", out=dest)
    assert tc.run_one(tc.Tree(photos, e.name), args, cfg, shots=shots,
                      statistics=e.statistics_kwargs()) == 0
    erun.seed_review_statistics(e, dest)
    result = discovery.load_set(dest)
    assert len(result.candidates) == 6 and set(result.media) == {"A", "B"}
    review = rv.with_defaults(result, rv.load_for(result))
    review.capture_root = str(photos)
    for medium in result.media:
        review.set_pick(medium, next(c for c in result.for_medium(medium) if c.dilution == "least"))
    ctl = ReviewController(result, review)
    before = ctl.frame("A")
    assert before is not None, ctl.error("A")
    assert not before.tidy.artifact.any()
    assert before.tidy.measurement_method.eq("centered-interior-v1").all()
    low = before.tidy[before.tidy.strain == "low"]
    assert (low.raw_growth < 0).all() and low.growth_quality.eq("low_growth").all()
    assert np.isfinite(low.relative_growth).all()
    options = ex.ExportOptions(data=True, summary=True, figures=True, statistics=True, montages=False)
    first = ex.export_review(result, ctl.review, result.media, photos, cfg,
                             options=options, outdir=dest / "first")
    assert first.ok and not first.warnings, first.warnings
    initial_tests = pd.read_csv(first.statistics)
    assert np.isfinite(initial_tests.p).sum() > 0
    tests_a = initial_tests[initial_tests.treatment.str.contains("Treatment A")]
    tests_b = initial_tests[initial_tests.treatment.str.contains("Treatment B")]
    assert set(tests_a.group1) == {"WT BY"}
    assert set(tests_b.group1) == {"EV BY"}, "Condition overrides survive display-name changes"
    p_wt = tests_a.loc[tests_a.group2 == "mutant", "p"].iloc[0]
    candidate_a = next(c for c in result.for_medium("A") if c.dilution == "least")
    assert candidate_a.n_significant == int((tests_a.p_adj <= .05).sum())

    changed = deepcopy(final)
    changed["normalization"]["*"] = 2
    changed["references"] = {"*": ["WT BY"]}
    ctl.set_final_analysis(changed)
    assert not ctl._frames and not ctl._preview_frames
    after = ctl.frame("A")
    pd.testing.assert_series_equal(before.tidy.raw_growth, after.tidy.raw_growth)
    provenance = ["measurement_method", "source_sha256", "source_path", "roi_pixels",
                  "roi_x", "roi_y", "roi_width", "roi_height", "boundary_radii", "roi_quality"]
    pd.testing.assert_frame_equal(before.tidy[provenance], after.tidy[provenance])
    assert not np.allclose(before.tidy.relative_growth, after.tidy.relative_growth)
    # Independent arithmetic from measured net values on each physical plate.
    for plate, rows in after.tidy.groupby("plate"):
        expected = rows[rows.strain == "EV BY"].raw_growth.mean()
        np.testing.assert_allclose(rows.control_raw, expected)
        np.testing.assert_allclose(rows.relative_growth, rows.raw_growth / expected)
    after.tidy.to_csv(out / "normalization-EV.csv", index=False)
    before.tidy.to_csv(out / "normalization-WT.csv", index=False)
    ctl.undo(); pd.testing.assert_frame_equal(ctl.frame("A").tidy, before.tidy)
    ctl.redo(); pd.testing.assert_frame_equal(ctl.frame("A").tidy, after.tidy)

    # Change comparisons while keeping normalization fixed at WT.
    comparison = deepcopy(final); comparison["references"] = {"*": ["EV BY"]}
    ctl.set_final_analysis(comparison)
    same_ratios = ctl.frame("A")
    pd.testing.assert_series_equal(before.tidy.relative_growth, same_ratios.tidy.relative_growth)
    second = ex.export_review(result, ctl.review, result.media, photos, cfg,
                              options=options, outdir=dest / "comparison-EV")
    assert second.ok and not second.warnings, second.warnings
    changed_tests = pd.read_csv(second.statistics)
    assert set(changed_tests.group1) == {"EV BY"}
    p_ev = changed_tests.loc[changed_tests.treatment.str.contains("Treatment A") &
                              (changed_tests.group2 == "mutant"), "p"].iloc[0]
    assert np.isfinite(p_ev) and np.isfinite(p_wt) and abs(p_ev - p_wt) > 1e-8
    from scipy.stats import ttest_1samp
    raw = before.tidy.pivot(index="replicate", columns="strain", values="raw_growth")
    expected_p = ttest_1samp(np.log(raw["mutant"] / raw["EV BY"]), 0).pvalue
    np.testing.assert_allclose(p_ev, expected_p, rtol=1e-10)

    # Persist both changed choices, plus raw low growth and a manual exclusion.
    chosen = deepcopy(comparison); chosen["normalization"]["*"] = 2
    ctl.set_final_analysis(chosen)
    ctl.set_medium("A"); ctl.set_excluded("rep1", 7, "SWT", True)
    ctl.frame("A"); ctl.frame("B"); ctl.save()
    manifest = json.loads((dest / "review_measurements/manifest.json").read_text())
    assert manifest["review"]["final_analysis"] == chosen
    assert set(manifest["current"]) == {"A", "B"}
    for medium, entry in manifest["current"].items():
        saved = pd.read_csv(dest / "review_measurements" / entry["file"])
        saved_provenance = pd.read_csv(dest / "review_measurements" / entry["file"],
                                      usecols=provenance, keep_default_na=False)
        pd.testing.assert_frame_equal(saved_provenance[provenance], ctl.frame(medium).tidy[provenance],
                                      check_dtype=False)
        np.testing.assert_allclose(saved.relative_growth, ctl.frame(medium).tidy.relative_growth, equal_nan=True)
    fresh = ReviewController(discovery.load_set(dest), rv.load_for(result))
    assert fresh.review.final_analysis == chosen and fresh.cfg["measurement"] == cfg["measurement"]
    pd.testing.assert_frame_equal(ctl.frame("A").tidy, fresh.frame("A").tidy)
    # Direct callers passing OLD frames must also be recalculated before export.
    direct = ex.export(result, fresh.review, [before], photos, cfg, options=options,
                       outdir=dest / "stale-frame-recalculated", cache_dir=cache)
    assert not direct.warnings, direct.warnings
    csv = pd.read_csv(direct.csv_path)
    metadata = json.loads((direct.output_dir / "final_analysis.json").read_text())
    assert metadata["final_analysis"] == chosen
    summary = pd.read_csv(direct.summary_path)
    assert summary.best_set_score.isna().all()
    assert summary.candidate_metrics_status.str.contains("invalidated").all()
    np.testing.assert_allclose(csv.relative_growth, fresh.frame("A").tidy.relative_growth, equal_nan=True)
    assert csv.measurement_method.eq("centered-interior-v1").all()
    csv_low = csv[csv.strain == "low"]
    assert (csv_low.raw_growth < 0).all() and np.isfinite(csv_low.relative_growth).all()
    third = ex.export_review(result, fresh.review, result.media, photos, cfg,
                             options=options, outdir=dest / "reopened")
    assert not third.warnings, third.warnings
    reopened_csv = pd.read_csv(third.csv_path)
    for medium in result.media:
        frame = fresh.frame(medium)
        rows = reopened_csv[reopened_csv.analysis_condition == medium]
        np.testing.assert_allclose(rows.relative_growth, frame.tidy.relative_growth, equal_nan=True)
        np.testing.assert_allclose(rows.raw_growth, frame.tidy.raw_growth)
    reopened_stats = pd.read_csv(third.statistics)
    assert set(reopened_stats.group1) == {"EV BY"}
    assert np.isfinite(reopened_stats.p).sum() > 0
    # Save after invalidation before revisiting a condition cannot advertise an
    # old measurement CSV as belonging to the new decision snapshot.
    temporary = deepcopy(chosen); temporary["references"] = {"*": ["mutant"]}
    fresh.set_final_analysis(temporary); fresh.save()
    invalidated = json.loads((dest / "review_measurements/manifest.json").read_text())
    assert invalidated["current"] == {}
    assert invalidated["review"]["final_analysis"] == temporary
    fresh.undo(); fresh.frame("A"); fresh.frame("B"); fresh.save()

    # Actual handpicked runner, same scientific sources and final configuration.
    hand = deepcopy(e); hand.mode = "quantify"; hand.final_analysis = chosen
    hand.picks = {m: {str(p): f"{m}-{p}.png" for p in (1, 2)} for m in ("A", "B")}
    res = intake.Resolution([])
    handdir = out / "handpicked"
    assert erun.run(hand, res=res, template=lab_standard_8x6(), outdir=handdir) == 0
    hand_csv = pd.read_csv(handdir / "spotting_results_normalized.csv")
    assert hand_csv.measurement_method.eq("centered-interior-v1").all()
    hand_frame = hand_csv[hand_csv.analysis_condition == "B"]
    np.testing.assert_allclose(hand_frame.raw_growth, fresh.frame("B").tidy.raw_growth)
    np.testing.assert_allclose(hand_frame.relative_growth, fresh.frame("B").tidy.relative_growth)
    hand_stats = pd.read_csv(handdir / "figures/spotting_paired_ttests.csv")
    assert set(hand_stats.group1) == {"EV BY"} and np.isfinite(hand_stats.p).sum() > 0
    assert rv.load(handdir / "review.json").final_analysis == chosen
    saved_experiment = schema.from_dict(json.loads((handdir / "experiment.json").read_text())["experiment"])
    assert saved_experiment.final_analysis == chosen
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in photos.glob("*.png")}
    report = dict(candidates=6, conditions=2, source_sha256=hashes,
        p_mutant_vs_WT=float(p_wt), p_mutant_vs_EV=float(p_ev), independent_p=float(expected_p),
        mean_mutant_normalized_WT=float(before.tidy[before.tidy.strain == "mutant"].relative_growth.mean()),
        mean_mutant_normalized_EV=float(after.tidy[after.tidy.strain == "mutant"].relative_growth.mean()),
        tests_reopened=len(reopened_stats), finite_tests=int(np.isfinite(reopened_stats.p).sum()),
        handpicked_rows=len(hand_csv), low_growth_raw=csv_low.raw_growth.tolist(),
        final_analysis=chosen, checks="ratios, independent t-test, overrides, undo/redo, raw low growth, ROI provenance, CSV/PNG/PDF, stale export, save/reopen, both modes")
    (out / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def test_real_scientific_workflow(tmp_path, monkeypatch):
    scientific_workflow(tmp_path, monkeypatch)


def test_multistep_comparison_refs_are_independent_and_jointly_corrected(tmp_path):
    import spotting_multistep as multi
    from experiments.model import MultiStepAnalysis
    from scipy.stats import ttest_1samp
    from statsmodels.stats.multitest import multipletests
    rows = []
    for bio in range(4):
        for tech in range(3):
            values = [40 + bio + tech, 70 + 3*bio + tech, 55 + 2*bio - tech]
            for slot, (strain, value) in enumerate(zip(["WT", "EV", "mut"], values), 1):
                rows.append(dict(condition="A", strain=strain, strain_col=slot, replicate=f"rep{bio}",
                    physical_plate=f"{bio}-{tech}", hours=24., raw_growth=value,
                    detection_limit=.5, is_control=slot == 1, qc_reason=""))
    raw = pd.DataFrame(rows)
    settings = MultiStepAnalysis(enabled=True, scope="technical", method="two_step", dilution=0, hours=(24.,))
    selection = analysis.validate({"normalization": {"*": 1}, "references": {"*": ["EV"]}})
    result = multi.analyze_references(raw, settings, selection)
    pd.testing.assert_frame_equal(result.observations, raw)
    assert set(result.contrasts.control_strain) == {"EV"}
    assert set(result.tests.comparison_reference) == {"EV"}
    assert set(result.tests.strain) == {"WT", "mut"}
    pivot = raw.pivot(index=["replicate", "physical_plate"], columns="strain", values="raw_growth")
    independent = ttest_1samp(np.log(pivot["mut"] / pivot["EV"]).groupby("replicate").mean(), 0).pvalue
    assert np.isclose(result.tests.loc[result.tests.strain == "mut", "p"].iloc[0], independent)
    np.testing.assert_allclose(result.tests.p_holm, multipletests(result.tests.p, method="holm")[1])
    destination = multi.write_results(result, tmp_path / "multi", settings)
    assert list(destination.glob("reference-*/*-comparison.png"))
    saved = pd.read_csv(destination / "tests.csv")
    np.testing.assert_allclose(saved.p, result.tests.p)
    selection["references"]["*"] = ["WT", "EV"]
    combined = multi.analyze_references(raw, settings, selection)
    assert len(combined.tests) == 3  # WT/EV, WT/mut, EV/mut; no reverse/self pairs
    assert combined.tests.family_size.eq(3).all()
    np.testing.assert_allclose(combined.tests.p_holm, multipletests(combined.tests.p, method="holm")[1])
    combined_destination = multi.write_results(combined, tmp_path / "multi-multiple", settings)
    exported = pd.read_csv(combined_destination / "tests.csv")
    np.testing.assert_allclose(exported.p_holm, combined.tests.p_holm)
    (tmp_path / "multi-acceptance.json").write_text(json.dumps({
        "single_reference": "EV", "single_reference_tests": len(result.tests),
        "mutant_vs_EV_p": float(independent), "multiple_reference_tests": len(combined.tests),
        "holm_family_size": 3, "raw_observations_unchanged": True,
        "root_normalization_strain": "WT", "figures_by_reference": True}, indent=2))
