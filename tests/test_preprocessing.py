import numpy as np
import pytest

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.config import PreprocessingConfig, SensorConfig
from ppg_resp.errors import ConfigError, DataFormatError, InsufficientDataError
from ppg_resp.preprocessing import hampel_filter, preprocess, zero_phase_filter
from ppg_resp.synthetic import synthetic_ppg


def _recording(**kwargs):
    sim = synthetic_ppg(**kwargs)
    return recording_from_arrays(sim.values, sim.fs_hz), sim


def test_clean_signal_passes_untouched_flags():
    rec, _ = _recording(duration_s=60)
    pre = preprocess(rec)
    rep = pre.report
    assert rep.fs_hz == pytest.approx(100.0)
    assert rep.n_missing == rep.n_out_of_range == rep.n_gap_samples == 0
    assert not rep.resampled and not rep.fs_mismatch
    assert rep.bad_fraction < 0.01
    assert pre.pulse.shape == pre.lowpassed.shape == pre.time_s.shape


def test_raw_recording_is_not_modified():
    rec, _ = _recording(duration_s=30)
    rec.raw[100] = np.nan
    before = rec.raw.copy()
    preprocess(rec)
    np.testing.assert_array_equal(rec.raw, before)


def test_zero_phase_filter_does_not_shift_a_sine():
    fs = 100.0
    t = np.arange(0, 20, 1 / fs)
    x = np.sin(2 * np.pi * 1.2 * t)
    y = zero_phase_filter(x, fs, low_hz=0.5, high_hz=4.0, order=3)
    core = slice(300, -300)
    lag = np.argmax(np.correlate(y[core], x[core], mode="full")) - (x[core].size - 1)
    assert lag == 0
    assert np.max(np.abs(y[core] - x[core])) < 0.02


def test_filter_rejects_cutoff_above_nyquist():
    with pytest.raises(ConfigError, match="incompatible"):
        zero_phase_filter(np.zeros(500), fs_hz=10.0, high_hz=8.0)
    with pytest.raises(ConfigError):
        zero_phase_filter(np.zeros(500), fs_hz=100.0, low_hz=4.0, high_hz=0.5)


def test_low_sampling_rate_lowers_cutoffs_instead_of_failing():
    rec, _ = _recording(duration_s=60, fs_hz=12.0, noise_sd=0.0)
    # An 8 Hz low-pass is illegal at fs = 12 Hz (Nyquist 6 Hz).
    pre = preprocess(rec, cfg=PreprocessingConfig(min_fs_hz=10.0))
    assert any("cut-offs lowered" in w for w in pre.report.warnings)


def test_sampling_rate_exactly_at_minimum_is_accepted():
    rec, _ = _recording(duration_s=60, fs_hz=20.0)
    assert preprocess(rec).fs_hz == pytest.approx(20.0)


def test_sampling_rate_too_low_is_refused():
    rec, _ = _recording(duration_s=60, fs_hz=8.0)
    with pytest.raises(InsufficientDataError, match="sampling frequency"):
        preprocess(rec)


def test_too_short_recording_is_refused():
    rec, _ = _recording(duration_s=3)
    with pytest.raises(InsufficientDataError, match="at least"):
        preprocess(rec)


def test_declared_fs_mismatch_is_reported():
    sim = synthetic_ppg(duration_s=30)
    rec = recording_from_arrays(sim.values, sim.fs_hz, time_s=sim.time_s)
    rec.meta["declared_fs_hz"] = 50.0
    pre = preprocess(rec)
    assert pre.report.fs_mismatch
    assert pre.fs_hz == pytest.approx(100.0)


def test_isolated_missing_values_are_interpolated_not_flagged():
    rec, _ = _recording(duration_s=30, noise_sd=0.0)
    truth = rec.raw.copy()
    rec.raw[[500, 900, 1500]] = np.nan
    pre = preprocess(rec)
    assert pre.report.n_missing == 3
    assert pre.report.n_gap_samples == 0
    assert abs(pre.clean[900] - truth[900]) < 0.02


def test_long_gap_is_masked_not_invented():
    rec, _ = _recording(duration_s=60)
    rec.raw[2000:2300] = np.nan  # 3 s gap
    pre = preprocess(rec)
    assert pre.bad_mask[2000:2300].all()
    assert not pre.bad_mask[500:1500].any()
    assert any("gaps" in w for w in pre.report.warnings)


def test_dropped_samples_give_irregular_timestamps_and_resampling():
    sim = synthetic_ppg(duration_s=60)
    keep = np.ones(sim.values.size, dtype=bool)
    keep[np.random.default_rng(1).choice(sim.values.size, 300, replace=False)] = False
    keep[3000:3200] = False  # plus a 2 s hole
    rec = recording_from_arrays(sim.values[keep], sim.fs_hz, time_s=sim.time_s[keep])
    pre = preprocess(rec)
    assert pre.report.resampled
    assert np.allclose(np.diff(pre.time_s), 0.01)
    hole = (pre.time_s > 30.1) & (pre.time_s < 31.9)
    assert pre.bad_mask[hole].all()


def test_non_increasing_timestamps_are_rejected():
    sim = synthetic_ppg(duration_s=10)
    t = sim.time_s.copy()
    t[50] = t[40]
    with pytest.raises(DataFormatError, match="increasing"):
        preprocess(recording_from_arrays(sim.values, sim.fs_hz, time_s=t))


def test_spikes_are_removed_but_peaks_survive():
    rec, _ = _recording(duration_s=30, noise_sd=0.0)
    clean = preprocess(rec).clean
    spiked = rec.raw.copy()
    idx = np.array([333, 1111, 2222])
    spiked[idx] += 5.0
    pre = preprocess(recording_from_arrays(spiked, 100.0))
    assert pre.report.n_spikes >= 3
    assert np.max(np.abs(pre.clean - clean)) < 0.05
    # No systolic peak of the clean signal was clipped.
    assert preprocess(rec).report.n_spikes == 0


def test_hampel_on_constant_signal_flags_nothing():
    _, mask = hampel_filter(np.full(100, 3.0), window=7, k=6.0)
    assert not mask.any()


def test_saturation_and_inversion_for_max30102_like_counts():
    sim = synthetic_ppg(duration_s=30, sensor_like=True)
    values = sim.values.copy()
    values[1000:1005] = 262143  # saturated ADC
    sensor = SensorConfig(name="MAX30102", invert=True, valid_min=1, valid_max=262142)
    pre = preprocess(recording_from_arrays(values, sim.fs_hz), sensor=sensor)
    assert pre.report.n_out_of_range == 5
    reference = preprocess(recording_from_arrays(sim.values, sim.fs_hz), sensor=sensor)
    # Inverted: systolic maxima of the model are maxima again (correlation with -raw).
    assert np.corrcoef(reference.pulse, -(sim.values - sim.values.mean()))[0, 1] > 0.9


def test_motion_artifact_is_flagged():
    rec, _ = _recording(duration_s=60)
    rec.raw[3000:3300] += 8.0 * np.sin(2 * np.pi * 1.7 * np.arange(300) / 100.0)
    pre = preprocess(rec)
    assert pre.bad_mask[3050:3250].all()
    assert pre.bad_mask.mean() < 0.2


def test_flat_signal_is_entirely_bad():
    pre = preprocess(recording_from_arrays(np.full(3000, 5.0), 100.0))
    assert pre.bad_mask.all()


def test_mostly_invalid_recording_is_refused():
    values = np.full(3000, np.nan)
    values[:500] = 1.0
    with pytest.raises(InsufficientDataError, match="valid"):
        preprocess(recording_from_arrays(values, 100.0), cfg=PreprocessingConfig())
