import numpy as np
import pytest

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.amplitude_extraction import verify_pairing
from ppg_resp.preprocessing import preprocess
from ppg_resp.pulse_detection import detect_pulses
from ppg_resp.synthetic import synthetic_ppg


def _detect(values, fs=100.0):
    return detect_pulses(preprocess(recording_from_arrays(values, fs)))


@pytest.mark.parametrize("hr_bpm", [45, 60, 75, 100, 140, 180])
def test_heart_rate_range(hr_bpm):
    sim = synthetic_ppg(duration_s=60, hr_bpm=hr_bpm, rr_bpm=12, noise_sd=0.02, seed=hr_bpm)
    pulses = _detect(sim.values)
    expected = hr_bpm  # beats in 60 s
    assert abs(pulses.n_beats - expected) <= 2
    assert 60.0 / np.nanmedian(pulses.ibi_s) == pytest.approx(hr_bpm, rel=0.02)
    assert pulses.ibi_ok[1:].mean() > 0.97
    verify_pairing(pulses)


@pytest.mark.parametrize("gain", [1e-3, 1.0, 5e4])
def test_detection_is_independent_of_signal_scale(gain):
    sim = synthetic_ppg(duration_s=60, hr_bpm=72, noise_sd=0.01)
    reference = _detect(sim.values)
    scaled = _detect(sim.values * gain + 7.0 * gain)
    np.testing.assert_array_equal(scaled.peak_idx, reference.peak_idx)


def test_slowly_changing_amplitude_is_followed():
    sim = synthetic_ppg(duration_s=120, hr_bpm=70, am_depth=0.0, noise_sd=0.005)
    gain = np.linspace(1.0, 0.25, sim.values.size)  # e.g. finger cooling
    pulses = _detect(sim.values * gain)
    assert abs(pulses.n_beats - 140) <= 2
    assert pulses.ibi_ok[1:].mean() > 0.97


def test_dicrotic_wave_is_not_counted_as_a_beat():
    sim = synthetic_ppg(duration_s=60, hr_bpm=50, noise_sd=0.0)
    pulses = _detect(sim.values)
    assert abs(pulses.n_beats - 50) <= 1
    assert np.nanmin(pulses.ibi_s) > 1.0


def test_respiratory_modulations_do_not_break_detection():
    sim = synthetic_ppg(duration_s=120, hr_bpm=66, rr_bpm=14, am_depth=0.3, bw_depth=0.5, fm_depth=0.08)
    pulses = _detect(sim.values)
    assert abs(pulses.n_beats - 132) <= 2
    verify_pairing(pulses)


def test_peaks_and_troughs_alternate_and_are_unique():
    sim = synthetic_ppg(duration_s=90, noise_sd=0.05, bw_depth=0.4, seed=3)
    pulses = _detect(sim.values)
    assert np.all(pulses.trough_idx < pulses.peak_idx)
    assert np.all(pulses.trough_idx[1:] > pulses.peak_idx[:-1])
    assert np.unique(pulses.trough_idx).size == pulses.trough_idx.size
    assert np.all(pulses.peak_value > pulses.trough_value)


def test_beats_inside_gaps_and_artifacts_are_dropped():
    sim = synthetic_ppg(duration_s=60, hr_bpm=60)
    values = sim.values.copy()
    values[2000:2500] = np.nan
    pulses = _detect(values)
    inside = (pulses.peak_time_s > 20.0) & (pulses.peak_time_s < 25.0)
    assert not inside.any()
    # The interval that spans the gap is flagged, not silently accepted.
    after_gap = np.searchsorted(pulses.peak_time_s, 25.0)
    assert not pulses.ibi_ok[after_gap]


def test_missed_beat_is_flagged_by_interval_check():
    sim = synthetic_ppg(duration_s=60, hr_bpm=60, noise_sd=0.0)
    values = sim.values.copy()
    values[3000:3100] = values[2990]  # erase one pulse (flat for 1 s)
    pulses = _detect(values)
    assert (~pulses.ibi_ok[1:]).sum() >= 1


def test_flat_signal_yields_no_beats():
    assert _detect(np.full(3000, 4.2)).n_beats == 0


def test_pure_noise_does_not_look_like_a_regular_pulse():
    noise = np.random.default_rng(0).standard_normal(6000)
    pulses = _detect(noise)
    # Something may be "detected", but its rhythm must fail the regularity check.
    assert pulses.n_beats == 0 or pulses.ibi_ok[1:].mean() < 0.7
