import numpy as np
import pytest

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.amplitude_extraction import extract_amplitudes, verify_pairing
from ppg_resp.errors import PPGError
from ppg_resp.preprocessing import preprocess
from ppg_resp.pulse_detection import PulseDetection, detect_pulses
from ppg_resp.synthetic import synthetic_ppg


def _features(sim):
    pulses = detect_pulses(preprocess(recording_from_arrays(sim.values, sim.fs_hz)))
    return extract_amplitudes(pulses)


def test_amplitude_matches_the_simulated_modulation():
    depth, rr = 0.2, 12.0
    sim = synthetic_ppg(duration_s=120, hr_bpm=75, rr_bpm=rr, am_depth=depth, noise_sd=0.0)
    feats = _features(sim)
    t, a = feats.series("am")
    assert feats.valid.all()
    relative = a / a.mean()
    # A sinusoid fitted at the true breathing frequency recovers the AM depth.
    basis = np.column_stack([np.sin(2 * np.pi * rr / 60 * t), np.cos(2 * np.pi * rr / 60 * t), np.ones_like(t)])
    coef, *_ = np.linalg.lstsq(basis, relative, rcond=None)
    assert np.hypot(coef[0], coef[1]) == pytest.approx(depth, rel=0.1)
    residual = relative - basis @ coef
    assert residual.std() < 0.02


def test_unmodulated_signal_has_constant_amplitude():
    sim = synthetic_ppg(duration_s=60, am_depth=0.0, noise_sd=0.0)
    _, a = _features(sim).series("am")
    assert a.std() / a.mean() < 0.01


def test_baseline_wander_leak_is_bounded_and_reduced_by_detrending():
    """BW is a separate mechanism; this quantifies how much of it enters A_i.

    A_i = P_i - T_i picks up (baseline slope x rise time). For a baseline
    swinging by a full pulse height at 15 breaths/min that is a real, sizeable
    effect and is documented as a limitation. The baseline-corrected amplitude
    reduces it but cannot remove it: a tilted baseline also moves the trough
    itself along the flat diastolic segment.
    """
    quiet = synthetic_ppg(duration_s=120, am_depth=0.0, bw_depth=0.0, noise_sd=0.0)
    wander = synthetic_ppg(duration_s=120, am_depth=0.0, bw_depth=0.5, noise_sd=0.0)
    _, a_quiet = _features(quiet).series("am")
    feats = _features(wander)
    _, a_plain = feats.series("am")
    _, a_corrected = feats.series("am_detrended")
    _, baseline = feats.series("bw")
    cv_plain = a_plain.std() / a_plain.mean()
    cv_corrected = a_corrected.std() / a_corrected.mean()
    assert 0.05 < cv_plain < 0.25
    assert cv_corrected < 0.6 * cv_plain
    assert baseline.std() > 2 * a_plain.std()
    assert a_plain.mean() == pytest.approx(a_quiet.mean(), rel=0.05)


def test_detrended_amplitude_still_carries_true_am():
    sim = synthetic_ppg(duration_s=120, hr_bpm=75, rr_bpm=12, am_depth=0.2, noise_sd=0.0)
    _, a = _features(sim).series("am_detrended")
    assert (a.std() / a.mean()) == pytest.approx(0.2 / np.sqrt(2), rel=0.15)


def test_fm_series_tracks_heart_rate_modulation():
    sim = synthetic_ppg(duration_s=120, hr_bpm=60, rr_bpm=12, am_depth=0.0, fm_depth=0.1, noise_sd=0.0)
    _, ibi = _features(sim).series("fm")
    assert ibi.mean() == pytest.approx(1.0, rel=0.02)
    assert 0.05 < ibi.std() < 0.09  # +/-10 % sinusoidal modulation has SD ~0.07 s


def test_amplitude_timestamps_are_peak_times_and_increase():
    sim = synthetic_ppg(duration_s=60)
    feats = _features(sim)
    assert np.all(np.diff(feats.time_s) > 0)
    assert feats.time_s[0] >= 0 and feats.time_s[-1] <= sim.time_s[-1]


def test_artifact_amplitudes_are_rejected():
    sim = synthetic_ppg(duration_s=60, hr_bpm=60, noise_sd=0.0)
    pulses = detect_pulses(preprocess(recording_from_arrays(sim.values, sim.fs_hz)))
    pulses.peak_value[20] += 10 * (pulses.peak_value[20] - pulses.trough_value[20])
    pulses.peak_value[30] = pulses.trough_value[30] - 1.0
    feats = extract_amplitudes(pulses)
    assert not feats.valid[20] and not feats.valid[30]
    assert feats.n_valid == feats.n_beats - 2


def _fake(peaks, troughs):
    peaks, troughs = np.asarray(peaks), np.asarray(troughs)
    zeros = np.zeros(peaks.size)
    return PulseDetection(peaks, troughs, zeros, zeros, zeros + 1, zeros, zeros, zeros.astype(bool), peaks.size)


def test_shared_or_misordered_troughs_are_refused():
    verify_pairing(_fake([10, 30, 50], [5, 20, 40]))
    with pytest.raises(PPGError, match="shared or misassigned"):
        verify_pairing(_fake([10, 30, 50], [5, 5, 40]))
    with pytest.raises(PPGError, match="precede"):
        verify_pairing(_fake([10, 30], [12, 20]))


def test_unknown_feature_name():
    with pytest.raises(ValueError):
        _features(synthetic_ppg(duration_s=30)).series("xyz")
