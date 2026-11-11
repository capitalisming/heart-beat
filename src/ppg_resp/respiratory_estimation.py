"""Respiratory-rate estimators for one analysis window.

Four independent methods operate on the same band-passed respiratory signal:

* ``fft``        - dominant peak of the Hann-windowed periodogram (primary)
* ``welch``      - dominant peak of Welch's averaged PSD
* ``autocorr``   - first major peak of the autocorrelation function
* ``peak_count`` - mean interval between breath maxima in the time domain

All return breaths per minute via ``RR = 60 * f_resp``. Why the periodogram
is primary: for a fixed window it has the finest frequency resolution of the
spectral options, and unlike peak counting a single noise wiggle cannot add or
remove a whole "breath". Its assumption - a roughly constant rate within the
window - is the price, and is listed among the limitations.

Resolution: a window of T seconds cannot separate rates closer than about
``60 / T`` breaths/min (1.9 for T = 32 s). Zero-padding and parabolic peak
interpolation refine the *location* of a single peak; they do not improve the
ability to separate two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.signal import correlate, detrend, find_peaks, welch
from scipy.signal.windows import hann

MIN_NFFT = 4096
PEAK_PROMINENCE_FRACTION = 0.25
ACF_PEAK_TOLERANCE = 0.9


@dataclass
class MethodResult:
    """Outcome of one estimator: ``rr_bpm`` is None when no estimate is possible."""

    rr_bpm: Optional[float]
    detail: Dict[str, Any] = field(default_factory=dict)


def frequency_resolution_bpm(window_s: float) -> float:
    """Smallest rate difference a window of this length can resolve (breaths/min)."""
    return 60.0 / window_s


def _nfft(n: int) -> int:
    return max(MIN_NFFT, int(2 ** np.ceil(np.log2(max(n, 2)))))


def _check(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if y.ndim != 1:
        raise ValueError("signal must be 1-D")
    if not 0 < band_hz[0] < band_hz[1] <= fs_hz / 2:
        raise ValueError(f"band {band_hz} Hz is not valid for fs={fs_hz} Hz")
    return y


def periodogram(y: np.ndarray, fs_hz: float) -> Tuple[np.ndarray, np.ndarray]:
    """Hann-windowed, linearly detrended, zero-padded power spectrum."""
    y = detrend(np.asarray(y, dtype=float), type="linear")
    window = hann(y.size, sym=False)
    spectrum = np.fft.rfft(y * window, n=_nfft(y.size))
    return np.fft.rfftfreq(_nfft(y.size), d=1.0 / fs_hz), np.abs(spectrum) ** 2


def band_peak(
    freq: np.ndarray, power: np.ndarray, band_hz: Tuple[float, float]
) -> Tuple[Optional[float], bool]:
    """Locate the dominant spectral peak inside a band.

    Returns:
        (frequency in Hz or None, at_edge). ``at_edge`` is True when the
        in-band maximum is not a local maximum of the full spectrum, i.e. the
        true peak probably lies outside the band and the value is unreliable.
    """
    inside = np.flatnonzero((freq >= band_hz[0]) & (freq <= band_hz[1]))
    if inside.size < 3 or not np.any(power[inside] > 0):
        return None, False
    k = int(inside[np.argmax(power[inside])])
    if k == 0 or k == power.size - 1:
        return float(freq[k]), True
    left, centre, right = power[k - 1], power[k], power[k + 1]
    if left > centre or right > centre:
        return float(freq[k]), True
    curvature = left - 2 * centre + right
    shift = 0.5 * (left - right) / curvature if curvature != 0 else 0.0
    return float(freq[k] + shift * (freq[1] - freq[0])), False


def estimate_fft(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> MethodResult:
    """Method B: dominant frequency of the periodogram within the band."""
    y = _check(y, fs_hz, band_hz)
    if y.size < 8 or np.allclose(y, y[0]):
        return MethodResult(None, {"reason": "signal empty or constant"})
    freq, power = periodogram(y, fs_hz)
    peak, at_edge = band_peak(freq, power, band_hz)
    return MethodResult(
        60.0 * peak if peak is not None else None,
        {"freq_hz": freq, "power": power, "edge_peak": at_edge},
    )


def estimate_welch(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> MethodResult:
    """Method C: dominant frequency of Welch's PSD (half-window segments, 50 % overlap).

    Averaging lowers the variance of the spectrum but each segment is half
    as long, so the resolution is twice as coarse as the periodogram's.
    """
    y = _check(y, fs_hz, band_hz)
    nperseg = y.size // 2
    if nperseg < 8 or np.allclose(y, y[0]):
        return MethodResult(None, {"reason": "window too short for Welch segments"})
    freq, power = welch(
        y, fs=fs_hz, window="hann", nperseg=nperseg, noverlap=nperseg // 2, nfft=_nfft(y.size), detrend="linear"
    )
    peak, at_edge = band_peak(freq, power, band_hz)
    return MethodResult(60.0 * peak if peak is not None else None, {"edge_peak": at_edge})


def estimate_autocorr(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> MethodResult:
    """Method D: breathing period from the autocorrelation function.

    The unbiased ACF is searched between the shortest and longest admissible
    breathing period (and never beyond half the window). The first peak that
    reaches 90 % of the highest one is taken, which avoids locking onto a
    multiple of the true period.
    """
    y = _check(y, fs_hz, band_hz)
    n = y.size
    y = y - y.mean()
    energy = float(np.dot(y, y))
    if n < 8 or energy <= 0:
        return MethodResult(None, {"reason": "signal empty or constant"})
    acf = correlate(y, y, mode="full", method="fft")[n - 1 :]
    acf = acf / (n - np.arange(n)) / (energy / n)
    min_lag = max(1, int(np.ceil(fs_hz / band_hz[1])))
    max_lag = min(int(np.floor(fs_hz / band_hz[0])), n // 2)
    if max_lag - min_lag < 2:
        return MethodResult(None, {"reason": "window too short for the breathing band"})
    peaks, _ = find_peaks(acf[: max_lag + 2])
    peaks = peaks[(peaks >= min_lag) & (peaks <= max_lag)]
    if peaks.size == 0:
        return MethodResult(None, {"reason": "no autocorrelation peak in the breathing band"})
    best = float(np.max(acf[peaks]))
    if best <= 0:
        return MethodResult(None, {"reason": "no positive autocorrelation peak"})
    k = int(peaks[np.argmax(acf[peaks] >= ACF_PEAK_TOLERANCE * best)])
    left, centre, right = acf[k - 1], acf[k], acf[k + 1]
    curvature = left - 2 * centre + right
    lag = k + (0.5 * (left - right) / curvature if curvature != 0 else 0.0)
    return MethodResult(60.0 * fs_hz / lag, {"acf_peak": float(centre)})


def estimate_peak_count(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> MethodResult:
    """Method A: count breath maxima in the time domain.

    Maxima must stand out by 25 % of the signal's 5-95 % range and be at least
    0.7 of the shortest admissible period apart. The rate is
    ``60 * (n_peaks - 1) / (t_last - t_first)``: whole cycles between the
    first and last maximum, so partial cycles at the window edges do not bias
    the count.
    """
    y = _check(y, fs_hz, band_hz)
    if y.size < 8:
        return MethodResult(None, {"reason": "window too short"})
    spread = float(np.percentile(y, 95) - np.percentile(y, 5))
    if spread <= 0:
        return MethodResult(None, {"reason": "signal is constant"})
    distance = max(1, int(0.7 * fs_hz / band_hz[1]))
    peaks, _ = find_peaks(y, distance=distance, prominence=PEAK_PROMINENCE_FRACTION * spread)
    if peaks.size < 3:
        return MethodResult(None, {"reason": "fewer than 3 breath peaks", "n_peaks": int(peaks.size)})
    rate = 60.0 * (peaks.size - 1) * fs_hz / float(peaks[-1] - peaks[0])
    return MethodResult(rate, {"n_peaks": int(peaks.size), "peak_idx": peaks})


ESTIMATORS = {
    "fft": estimate_fft,
    "welch": estimate_welch,
    "autocorr": estimate_autocorr,
    "peak_count": estimate_peak_count,
}


def estimate_all(y: np.ndarray, fs_hz: float, band_hz: Tuple[float, float]) -> Dict[str, MethodResult]:
    """Run every estimator on one window."""
    return {name: func(y, fs_hz, band_hz) for name, func in ESTIMATORS.items()}
