import numpy as np
import pandas as pd
import pytest

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.errors import DataFormatError
from ppg_resp.evaluation import (
    agreement_metrics,
    bland_altman,
    bland_altman_repeated,
    cluster_bootstrap_ci,
    evaluate,
    format_report,
    window_pairs,
)
from ppg_resp.ground_truth import (
    load_reference,
    reference_from_count,
    reference_from_events,
    reference_from_markers,
    reference_from_waveform,
)
from ppg_resp.pipeline import run_pipeline
from ppg_resp.synthetic import synthetic_ppg

# ------------------------------------------------------------ ground truth


def test_count_reference_is_n_over_minutes():
    assert reference_from_count(30, 120.0).rate() == pytest.approx(15.0)
    assert reference_from_count(30, 120.0).rate(0, 60) is None  # a total cannot be windowed


def test_interval_rule_ignores_partial_cycles():
    events = np.arange(1.3, 120, 4.0)  # exactly 15 breaths/min
    ref = reference_from_events(events, "observer")
    assert ref.rate(10, 42) == pytest.approx(15.0)
    assert ref.rate() == pytest.approx(15.0)


def test_count_rule_is_quantised():
    ref = reference_from_events(np.arange(1.3, 120, 4.0), "observer")
    rates = {round(ref.rate(t0, t0 + 30, rule="count"), 2) for t0 in np.arange(0, 60, 0.5)}
    assert rates <= {14.0, 16.0}  # 7 or 8 events in 30 s: +/- one breath = 2 /min


def test_too_few_events_or_missed_marker_gives_no_reference():
    assert reference_from_events(np.array([1.0, 5.0]), "observer").rate(0, 30) is None
    events = np.delete(np.arange(0, 60, 4.0), [6, 7])  # two consecutive markers missed
    ref = reference_from_events(events, "observer")
    assert ref.rate(0, 60) is None
    assert ref.rate(0, 60, max_interval_ratio=None) is not None


def test_ppg_derived_reference_is_refused():
    with pytest.raises(ValueError, match="circular"):
        reference_from_events(np.array([1.0, 2.0, 3.0]), "PPG baseline wander")


def test_markers_from_recording():
    rec = recording_from_arrays(np.zeros(1000), 100.0, markers_s=np.array([1.0, 5.0, 9.0]))
    assert reference_from_markers(rec).rate() == pytest.approx(15.0)


def test_belt_waveform_is_converted_to_breaths():
    t = np.arange(0, 120, 0.04)
    belt = np.sin(2 * np.pi * 0.2 * t) + 0.1 * np.random.default_rng(0).standard_normal(t.size)
    ref = reference_from_waveform(t, belt, "respiratory belt")
    assert ref.rate() == pytest.approx(12.0, abs=0.2)
    shifted = reference_from_waveform(t, belt, "respiratory belt", time_offset_s=2.5)
    np.testing.assert_allclose(shifted.event_times_s, ref.event_times_s + 2.5)


def test_load_reference_files(tmp_path):
    events = tmp_path / "events.csv"
    events.write_text("breath_time_s\n1.0\n5.0\n9.0\n13.0\n")
    assert load_reference(events).rate() == pytest.approx(15.0)
    bare = tmp_path / "bare.txt"
    bare.write_text("1.0\n5.0\n9.0\n")
    assert load_reference(bare).event_times_s.size == 3
    t = np.arange(0, 60, 0.05)
    wave = tmp_path / "belt.csv"
    pd.DataFrame({"time_s": t, "resp": np.sin(2 * np.pi * 0.25 * t)}).to_csv(wave, index=False)
    assert load_reference(wave, kind="waveform").rate() == pytest.approx(15.0, abs=0.3)
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    with pytest.raises(DataFormatError):
        load_reference(bad)


# ------------------------------------------------------------ metrics


def test_metrics_match_hand_calculation():
    ref = np.array([10.0, 12.0, 14.0, 16.0])
    est = np.array([11.0, 12.0, 13.0, 18.0])
    m = agreement_metrics(ref, est)  # errors: +1, 0, -1, +2
    assert m["n"] == 4
    assert m["mae"] == pytest.approx(1.0)
    assert m["rmse"] == pytest.approx(np.sqrt(6 / 4))
    assert m["bias"] == pytest.approx(0.5)
    assert m["sd_error"] == pytest.approx(np.std([1, 0, -1, 2], ddof=1))
    assert m["loa_high"] - m["loa_low"] == pytest.approx(2 * 1.96 * m["sd_error"])


def test_perfect_correlation_does_not_mean_agreement():
    ref = np.array([8.0, 12.0, 16.0, 20.0, 24.0])
    est = 1.5 * ref + 3.0
    m = agreement_metrics(ref, est)
    assert m["pearson_r"] == pytest.approx(1.0) and m["spearman_rho"] == pytest.approx(1.0)
    assert m["mae"] > 5.0 and m["bias"] > 5.0
    assert bland_altman(ref, est)["slope"] == pytest.approx(0.4)  # proportional bias detected


def test_small_or_degenerate_samples_give_nan_not_numbers():
    assert np.isnan(agreement_metrics([], [])["mae"])
    two = agreement_metrics([10.0, 12.0], [11.0, 12.0])
    assert np.isnan(two["pearson_r"]) and np.isfinite(two["mae"])
    constant = agreement_metrics([12.0, 12.0, 12.0], [11.0, 12.0, 13.0])
    assert np.isnan(constant["pearson_r"])
    dropped = agreement_metrics([10.0, np.nan, 14.0], [10.0, 12.0, np.nan])
    assert dropped["n"] == 1


def test_bland_altman_limits_and_intervals():
    rng = np.random.default_rng(0)
    ref = rng.uniform(8, 24, 200)
    est = ref + 1.0 + rng.normal(0, 2.0, 200)
    ba = bland_altman(ref, est)
    assert ba["bias"] == pytest.approx(1.0, abs=0.4)
    assert ba["loa_high"] - ba["loa_low"] == pytest.approx(4 * 1.96, rel=0.15)
    assert ba["bias_ci"][0] < ba["bias"] < ba["bias_ci"][1]
    assert ba["loa_low_ci"][0] < ba["loa_low"] < ba["loa_low_ci"][1]
    assert bland_altman([1.0], [2.0])["bias"] is None


def test_repeated_measures_limits_are_wider_than_naive_when_participants_differ():
    rng = np.random.default_rng(1)
    offsets = rng.normal(0, 2.0, 12)  # each participant has their own bias
    groups = np.repeat(np.arange(12), 20)
    diff = offsets[groups] + rng.normal(0, 0.5, groups.size)
    out = bland_altman_repeated(diff, groups)
    assert out["sd_between"] == pytest.approx(2.0, rel=0.5)
    assert out["sd_within"] == pytest.approx(0.5, rel=0.15)
    assert out["sd"] == pytest.approx(np.hypot(out["sd_within"], out["sd_between"]))
    assert bland_altman_repeated(diff[:20], groups[:20])["bias"] is None  # one participant only


def test_cluster_bootstrap_needs_enough_participants():
    frame = pd.DataFrame({"participant_id": np.repeat(list("ABC"), 5), "error": np.arange(15.0)})
    assert cluster_bootstrap_ci(frame, "participant_id", lambda d: d["error"].mean()) is None
    rng = np.random.default_rng(2)
    big = pd.DataFrame({"participant_id": np.repeat(np.arange(10), 4), "error": rng.normal(1.0, 1.0, 40)})
    low, high = cluster_bootstrap_ci(big, "participant_id", lambda d: d["error"].mean(), n_boot=500)
    assert low < big["error"].mean() < high


# ------------------------------------------------------------ end to end (synthetic)


def _pairs(n_participants=3, rates=(10.0, 16.0, 22.0)):
    frames = []
    for p in range(n_participants):
        for rr in rates:
            sim = synthetic_ppg(duration_s=100, hr_bpm=65 + 6 * p, rr_bpm=rr, noise_sd=0.02, seed=10 * p + int(rr))
            result = run_pipeline(recording_from_arrays(sim.values, sim.fs_hz))
            ref = reference_from_events(sim.breath_times_s, "synthetic truth")
            frames.append(window_pairs(result, ref, f"P{p}_rr{rr:.0f}", f"P{p}", "S1", f"paced {rr:.0f}/min"))
    return pd.concat(frames, ignore_index=True)


def test_evaluation_end_to_end_on_synthetic_recordings():
    pairs = _pairs()
    report = evaluate(pairs, n_boot=200)
    assert report.coverage["n_recordings"] == 9 and report.coverage["n_participants"] == 3
    assert report.coverage["accepted_fraction"] > 0.9
    assert report.recording_metrics["n"] == 9
    assert report.recording_metrics["mae"] < 0.5
    assert report.recording_ci["mae"] is None  # 3 participants: no interval
    assert any("no confidence intervals" in note for note in report.notes)
    assert len(report.condition_table) == 3 and len(report.participant_table) == 3
    assert np.isnan(report.window_metrics["pearson_p"])  # never a p-value for dependent windows
    text = format_report(report, "Synthetic check")
    assert "Recording level" in text and "Bland-Altman" in text


def test_evaluation_with_no_accepted_windows_does_not_invent_results():
    pairs = _pairs(n_participants=1, rates=(12.0,))
    pairs["status"] = "LOW_QUALITY"
    pairs["rr_est"] = np.nan
    report = evaluate(pairs)
    assert report.coverage["n_accepted"] == 0
    assert report.recording_metrics["n"] == 0 and np.isnan(report.recording_metrics["mae"])
    assert report.candidate_metrics["n"] > 0  # the gate's effect stays visible
    assert "n/a" in format_report(report)


def test_evaluate_rejects_incomplete_table():
    with pytest.raises(ValueError, match="lacks columns"):
        evaluate(pd.DataFrame({"rr_ref": [1.0]}))
