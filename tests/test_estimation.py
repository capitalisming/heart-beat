"""Respiratory signal reconstruction, the four estimators, SQI and the full pipeline.

All signals here are SYNTHETIC. These tests verify the implementation against
a known model; they say nothing about accuracy on people.
"""

import numpy as np
import pytest

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.config import RespiratoryConfig, Settings, settings_from_dict
from ppg_resp.errors import DataFormatError, InsufficientDataError
from ppg_resp.pipeline import run_pipeline
from ppg_resp.respiratory_estimation import (
    band_peak,
    estimate_all,
    estimate_autocorr,
    estimate_fft,
    estimate_peak_count,
    estimate_welch,
    frequency_resolution_bpm,
)
from ppg_resp.respiratory_signal import build_respiratory_signal, contiguous_runs
from ppg_resp.signal_quality import (
    STATUS_INSUFFICIENT,
    STATUS_LOW_QUALITY,
    STATUS_OK,
    beat_coverage,
    compute_sqi,
    spectral_quality,
)
from ppg_resp.synthetic import synthetic_ppg

BAND = (0.1, 0.5)
FS = 4.0


def _sine(rr_bpm, seconds=32.0, noise=0.0, seed=0):
    t = np.arange(0, seconds, 1 / FS)
    rng = np.random.default_rng(seed)
    return np.sin(2 * np.pi * rr_bpm / 60 * t + 0.4) + noise * rng.standard_normal(t.size)


def _run(sim, feature=None, settings=None):
    return run_pipeline(recording_from_arrays(sim.values, sim.fs_hz), settings, feature)


# ---------------------------------------------------------------- resampling


def test_contiguous_runs():
    mask = np.array([0, 1, 1, 0, 0, 1, 1, 1, 0, 1], dtype=bool)
    assert contiguous_runs(mask) == [(1, 3), (5, 8), (9, 10)]
    assert contiguous_runs(np.zeros(4, dtype=bool)) == []


def test_irregular_beats_are_resampled_without_extrapolation():
    rng = np.random.default_rng(0)
    beats = np.cumsum(0.8 + 0.05 * rng.standard_normal(150)) + 3.3
    values = 1.0 + 0.2 * np.sin(2 * np.pi * 0.25 * beats)
    resp = build_respiratory_signal(beats, values)
    assert np.allclose(np.diff(resp.time_s), 0.25)
    assert resp.time_s[0] >= beats[0] and resp.time_s[-1] <= beats[-1]
    assert resp.valid.all()
    truth = 0.2 * np.sin(2 * np.pi * 0.25 * resp.time_s)
    core = slice(20, -20)
    assert np.corrcoef(resp.filtered[core], truth[core])[0, 1] > 0.98
    # Linear interpolation never leaves the range of the measured beats.
    assert resp.interpolated.max() <= values.max() and resp.interpolated.min() >= values.min()


def test_long_beat_gap_is_not_bridged():
    beats = np.concatenate([np.arange(0, 60, 0.8), np.arange(70, 130, 0.8)])
    values = 1.0 + 0.2 * np.sin(2 * np.pi * 0.25 * beats)
    resp = build_respiratory_signal(beats, values)
    inside = (resp.time_s > 60) & (resp.time_s < 69.5)
    assert not resp.valid[inside].any()
    assert np.isnan(resp.filtered[inside]).all() and np.isnan(resp.interpolated[inside]).all()
    assert resp.valid[(resp.time_s > 5) & (resp.time_s < 55)].all()


def test_too_few_beats():
    with pytest.raises(InsufficientDataError):
        build_respiratory_signal(np.array([0.0, 1.0, 2.0]), np.array([1.0, 1.1, 1.0]))


def test_pchip_option_runs():
    beats = np.arange(0, 60, 0.8)
    values = 1.0 + 0.2 * np.sin(2 * np.pi * 0.2 * beats)
    resp = build_respiratory_signal(beats, values, RespiratoryConfig(interpolation="pchip"))
    assert np.isfinite(resp.filtered).all()


# ---------------------------------------------------------------- estimators


@pytest.mark.parametrize("rr", [7.0, 10.0, 13.5, 18.0, 24.0, 28.0])
@pytest.mark.parametrize("estimator", [estimate_fft, estimate_autocorr, estimate_peak_count])
def test_estimators_recover_a_clean_rate(estimator, rr):
    assert estimator(_sine(rr), FS, BAND).rr_bpm == pytest.approx(rr, abs=0.5)


@pytest.mark.parametrize("rr", [10.0, 18.0, 24.0])
def test_welch_recovers_rate_within_its_coarser_resolution(rr):
    assert estimate_welch(_sine(rr), FS, BAND).rr_bpm == pytest.approx(rr, abs=1.0)


def test_all_methods_agree_under_moderate_noise():
    results = estimate_all(_sine(15.0, noise=0.3, seed=2), FS, BAND)
    values = [r.rr_bpm for r in results.values()]
    assert all(v is not None for v in values)
    assert max(values) - min(values) < 2.0


def test_constant_signal_gives_no_estimate():
    flat = np.zeros(128)
    for result in estimate_all(flat, FS, BAND).values():
        assert result.rr_bpm is None


def test_rate_outside_band_is_flagged_as_edge_peak():
    freq = np.linspace(0, 2, 2001)
    power = np.exp(-0.5 * ((freq - 0.6) / 0.05) ** 2)  # true peak above the band
    peak, at_edge = band_peak(freq, power, BAND)
    assert at_edge and peak == pytest.approx(0.5, abs=1e-3)


def test_invalid_band_is_rejected():
    with pytest.raises(ValueError):
        estimate_fft(_sine(12), FS, (0.1, 3.0))


def test_resolution_statement():
    assert frequency_resolution_bpm(32.0) == pytest.approx(1.875)


def test_autocorr_does_not_lock_onto_a_period_multiple():
    assert estimate_autocorr(_sine(24.0, seconds=64), FS, BAND).rr_bpm == pytest.approx(24.0, abs=0.5)


# ---------------------------------------------------------------- quality


def test_beat_coverage():
    beats = np.arange(0.5, 32, 0.8)
    assert beat_coverage(beats, 0, 32, 3.0) == pytest.approx(1.0)
    holed = beats[(beats < 10) | (beats > 20)]
    assert beat_coverage(holed, 0, 32, 3.0) == pytest.approx(1 - 10.4 / 32, abs=0.03)
    assert beat_coverage(np.empty(0), 0, 32, 3.0) == 0.0


def test_spectral_quality_separates_tone_noise_and_two_tones():
    def quality(y):
        d = estimate_fft(y, FS, BAND).detail
        return spectral_quality(d["freq_hz"], d["power"], BAND, 32.0)

    tone_c, tone_d = quality(_sine(15.0))
    # A single noise realisation can look peaky by chance, so judge the median.
    noise = [quality(np.random.default_rng(seed).standard_normal(128)) for seed in range(40)]
    noise_c, noise_d = np.median([c for c, _ in noise]), np.median([d for _, d in noise])
    two_c, two_d = quality(_sine(10.0) + 0.9 * _sine(22.0))
    assert tone_c > 0.9 and tone_d > 0.9
    assert noise_c < 0.5 and noise_d < 0.8
    assert two_d < 0.3  # a competing rhythm destroys dominance
    assert two_c < tone_c


def test_sqi_formula():
    perfect = dict(coverage=1, regularity=1, beat_validity=1, concentration=1, dominance=1)
    assert compute_sqi(perfect) == pytest.approx(100.0)
    assert compute_sqi({**perfect, "coverage": 0.5}) == pytest.approx(50.0)
    assert compute_sqi({**perfect, "concentration": 0.25}) == pytest.approx(50.0)
    assert compute_sqi({**perfect, "dominance": 0.0}) == 0.0


# ---------------------------------------------------------------- full pipeline


@pytest.mark.parametrize("rr,hr", [(7.0, 62), (10.0, 80), (15.0, 70), (20.0, 95), (26.0, 110)])
def test_pipeline_recovers_synthetic_rate(rr, hr):
    sim = synthetic_ppg(duration_s=120, hr_bpm=hr, rr_bpm=rr, am_depth=0.15, noise_sd=0.02, seed=int(rr))
    result = _run(sim)
    summary = result.summary()
    assert result.status == STATUS_OK
    assert summary["ok_fraction"] > 0.9
    assert summary["rr_bpm"] == pytest.approx(rr, abs=0.5)
    assert summary["hr_bpm"] == pytest.approx(hr, rel=0.03)
    assert all(w.rr_bpm is None for w in result.windows if w.status != STATUS_OK)


def test_pipeline_on_max30102_like_counts_with_repo_settings():
    sim = synthetic_ppg(duration_s=120, rr_bpm=13.0, sensor_like=True, noise_sd=0.02)
    settings = settings_from_dict(
        {"sensor": {"name": "MAX30102", "invert": True, "valid_min": 1, "valid_max": 262142}}
    )
    assert _run(sim, settings=settings).summary()["rr_bpm"] == pytest.approx(13.0, abs=0.5)


def test_without_inversion_the_reflective_signal_still_yields_the_rate_but_other_pulses():
    """Guards the orientation flag: wrong polarity changes which extrema are "peaks"."""
    sim = synthetic_ppg(duration_s=120, rr_bpm=13.0, sensor_like=True, noise_sd=0.0)
    upright = _run(sim, settings=settings_from_dict({"sensor": {"invert": True}}))
    flipped = _run(sim, settings=settings_from_dict({"sensor": {"invert": False}}))
    assert not np.array_equal(upright.pulses.peak_idx, flipped.pulses.peak_idx)


def test_window_table_has_expected_columns():
    frame = _run(synthetic_ppg(duration_s=90)).to_frame()
    expected = {"t_start_s", "t_end_s", "status", "rr_bpm", "rr_candidate_bpm", "sqi", "rr_fft_bpm",
                "rr_welch_bpm", "rr_autocorr_bpm", "rr_peak_count_bpm", "q_coverage", "q_concentration",
                "hr_bpm", "method_spread_bpm", "methods_agree", "modulation_depth", "reasons"}
    assert expected <= set(frame.columns)
    assert np.allclose(frame["t_end_s"] - frame["t_start_s"], 32.0)


def test_modulation_depth_indicator_tracks_the_simulated_depth():
    sim = synthetic_ppg(duration_s=120, rr_bpm=12, am_depth=0.2, noise_sd=0.0)
    depths = [w.indicators["modulation_depth"] for w in _run(sim).windows]
    assert np.median(depths) == pytest.approx(0.2, rel=0.15)


def test_no_respiratory_modulation_is_mostly_withheld():
    accepted = total = 0
    for seed in range(6):
        sim = synthetic_ppg(duration_s=120, hr_bpm=60 + 5 * seed, am_depth=0.0, noise_sd=0.03, seed=seed)
        result = _run(sim)
        total += len(result.windows)
        accepted += sum(w.status == STATUS_OK for w in result.windows)
    assert accepted / total < 0.15
    assert total > 50


def test_pure_noise_is_low_quality_with_a_reason():
    noise = np.random.default_rng(0).standard_normal(12000)
    result = run_pipeline(recording_from_arrays(noise, 100.0))
    assert result.status == STATUS_LOW_QUALITY
    assert result.summary()["rr_bpm"] is None
    assert "advice" in result.summary()
    assert any("irregular" in r or "SQI" in r for r in result.reasons)


def test_flat_signal_is_insufficient_data():
    result = run_pipeline(recording_from_arrays(np.full(6000, 2.0), 100.0))
    assert result.status == STATUS_INSUFFICIENT and not result.windows
    assert "pulse" in result.reasons[0]


def test_short_recording_is_insufficient_data():
    result = _run(synthetic_ppg(duration_s=20))
    assert result.status == STATUS_INSUFFICIENT
    assert "shorter than one analysis window" in result.reasons[0]
    very_short = _run(synthetic_ppg(duration_s=3))
    assert very_short.status == STATUS_INSUFFICIENT and very_short.pre is None


def test_low_sampling_rate_is_insufficient_data_not_a_crash():
    result = _run(synthetic_ppg(duration_s=60, fs_hz=8.0))
    assert result.status == STATUS_INSUFFICIENT and "sampling frequency" in result.reasons[0]


def test_malformed_timestamps_raise():
    sim = synthetic_ppg(duration_s=40)
    t = sim.time_s.copy()
    t[10] = t[5]
    with pytest.raises(DataFormatError):
        run_pipeline(recording_from_arrays(sim.values, sim.fs_hz, time_s=t))


def test_gap_only_invalidates_the_windows_it_touches():
    sim = synthetic_ppg(duration_s=180, rr_bpm=14, noise_sd=0.02)
    values = sim.values.copy()
    values[8000:9000] = np.nan  # 10 s without signal at t = 80-90 s
    result = run_pipeline(recording_from_arrays(values, sim.fs_hz))
    for w in result.windows:
        touches = w.t_start_s < 91 and w.t_end_s > 79
        if touches:
            assert w.status == STATUS_INSUFFICIENT and w.rr_bpm is None
        else:
            assert w.status == STATUS_OK and w.rr_bpm == pytest.approx(14, abs=0.6)
    assert any(w.status == STATUS_OK for w in result.windows)
    assert any(w.status == STATUS_INSUFFICIENT for w in result.windows)


def test_strong_motion_artifact_does_not_produce_a_wrong_accepted_rate():
    sim = synthetic_ppg(duration_s=150, rr_bpm=16, noise_sd=0.02)
    values = sim.values.copy()
    rng = np.random.default_rng(5)
    values[6000:7500] += np.cumsum(rng.standard_normal(1500)) * 0.4  # 15 s of motion
    result = run_pipeline(recording_from_arrays(values, sim.fs_hz))
    accepted = [w.rr_bpm for w in result.windows if w.status == STATUS_OK]
    assert accepted and all(abs(v - 16) < 1.5 for v in accepted)


def test_missing_samples_and_irregular_timestamps_are_tolerated():
    sim = synthetic_ppg(duration_s=120, rr_bpm=11, noise_sd=0.02)
    keep = np.ones(sim.values.size, dtype=bool)
    keep[np.random.default_rng(3).choice(sim.values.size, 600, replace=False)] = False
    rec = recording_from_arrays(sim.values[keep], sim.fs_hz, time_s=sim.time_s[keep])
    result = run_pipeline(rec)
    assert result.pre.report.resampled
    assert result.summary()["rr_bpm"] == pytest.approx(11, abs=0.5)


def test_cardiac_nyquist_limit_narrows_the_band():
    sim = synthetic_ppg(duration_s=120, hr_bpm=48, rr_bpm=12, noise_sd=0.01)
    window = _run(sim).windows[0]
    assert window.indicators["band_high_bpm"] == pytest.approx(24.0, abs=0.7)
    assert window.rr_bpm == pytest.approx(12, abs=0.5)


def test_known_limitation_breathing_above_half_heart_rate_aliases():
    """Documented failure mode, not a feature.

    One amplitude sample per beat cannot represent breathing faster than
    HR/2. A 28 /min rhythm at HR 45 appears as a clean 45 - 28 = 17 /min
    rhythm, and nothing in the amplitude series can reveal the error.
    """
    sim = synthetic_ppg(duration_s=120, hr_bpm=45, rr_bpm=28, am_depth=0.15, noise_sd=0.01)
    assert _run(sim).summary()["rr_bpm"] == pytest.approx(17.0, abs=0.7)


@pytest.mark.parametrize("feature", ["am_detrended", "bw", "fm"])
def test_comparison_features_run_through_the_same_pipeline(feature):
    sim = synthetic_ppg(duration_s=120, rr_bpm=12, am_depth=0.15, bw_depth=0.3, fm_depth=0.05, noise_sd=0.01)
    result = _run(sim, feature=feature)
    assert result.feature == feature
    assert result.summary()["rr_bpm"] == pytest.approx(12, abs=0.5)


def test_unknown_feature_is_rejected():
    with pytest.raises(ValueError):
        _run(synthetic_ppg(duration_s=40), feature="xyz")


def test_default_settings_object_is_reusable():
    settings = Settings()
    first = _run(synthetic_ppg(duration_s=60, seed=1), settings=settings)
    second = _run(synthetic_ppg(duration_s=60, seed=1), settings=settings)
    assert first.summary()["rr_bpm"] == second.summary()["rr_bpm"]
