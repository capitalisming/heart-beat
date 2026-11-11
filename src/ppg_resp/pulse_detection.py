"""Pulse detection: systolic peaks and their preceding onsets (troughs).

Peaks are located on the band-passed signal after dividing by a running
estimate of its own SD, so the prominence threshold is relative to the local
pulse size and does not depend on sensor gain, LED current or skin contact.
Peak and trough *values* are then read from the low-passed signal, whose
amplitude and baseline are intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

from .config import PulseConfig
from .preprocessing import PreprocessedPPG

MIN_RISE_S = 0.04  # a trough closer than this to its peak is not a pulse onset


@dataclass
class PulseDetection:
    """Detected beats; arrays are aligned, one entry per beat, in time order.

    Each beat owns exactly one trough: the minimum of the low-passed signal
    between the previous peak and its own peak. ``ibi_s[i]`` is the interval
    from beat ``i-1`` to beat ``i`` (NaN for the first beat); ``ibi_ok`` marks
    intervals that are physiologically plausible and consistent with their
    neighbours (a False flags a probable missed or false detection).
    """

    peak_idx: np.ndarray
    trough_idx: np.ndarray
    peak_time_s: np.ndarray
    trough_time_s: np.ndarray
    peak_value: np.ndarray
    trough_value: np.ndarray
    ibi_s: np.ndarray
    ibi_ok: np.ndarray
    n_candidates: int

    @property
    def n_beats(self) -> int:
        return int(self.peak_idx.size)


def _empty(n_candidates: int = 0) -> PulseDetection:
    ints = np.empty(0, dtype=int)
    floats = np.empty(0, dtype=float)
    return PulseDetection(ints, ints, floats, floats, floats, floats, floats, np.empty(0, dtype=bool), n_candidates)


def _odd(n: float) -> int:
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def check_intervals(peak_time_s: np.ndarray, cfg: PulseConfig) -> tuple:
    """Inter-beat intervals and a plausibility flag for each.

    An interval is accepted when it corresponds to a heart rate inside
    [min_hr, max_hr] and lies within ``ibi_tolerance`` of the running median
    of the neighbouring intervals.
    """
    n = peak_time_s.size
    ibi = np.full(n, np.nan)
    ok = np.zeros(n, dtype=bool)
    if n < 2:
        return ibi, ok
    ibi[1:] = np.diff(peak_time_s)
    intervals = ibi[1:]
    in_range = (intervals >= 60.0 / cfg.max_hr_bpm) & (intervals <= 60.0 / cfg.min_hr_bpm)
    local = median_filter(intervals, size=min(_odd(cfg.local_beats), _odd(intervals.size)), mode="nearest")
    consistent = np.abs(intervals - local) <= cfg.ibi_tolerance * local
    ok[1:] = in_range & consistent
    return ibi, ok


def detect_pulses(pre: PreprocessedPPG, cfg: Optional[PulseConfig] = None) -> PulseDetection:
    """Detect systolic peaks and pair each with its own preceding trough.

    Returns an empty :class:`PulseDetection` (never raises) when no pulses can
    be found; the quality stage turns that into an INSUFFICIENT DATA status.
    """
    cfg = cfg or PulseConfig()
    fs = pre.fs_hz
    x = pre.pulse
    if x.size < 3 or pre.bad_mask.all():
        return _empty()

    # Running SD of the (zero-mean) band-passed signal as amplitude reference.
    envelope = np.sqrt(uniform_filter1d(x * x, _odd(cfg.envelope_window_s * fs), mode="nearest"))
    usable = ~pre.bad_mask
    typical = float(np.median(envelope[usable]))
    if not typical > 0:
        return _empty()
    # The floor stops weak segments (sensor lifted) from being amplified into "pulses".
    envelope = np.maximum(envelope, 0.2 * typical)

    min_distance = max(1, int(np.floor(fs * 60.0 / cfg.max_hr_bpm)))
    candidates, _ = find_peaks(x / envelope, distance=min_distance, prominence=cfg.min_prominence)
    n_candidates = int(candidates.size)
    candidates = candidates[usable[candidates]]
    if candidates.size == 0:
        return _empty(n_candidates)

    # Refine each location on the low-passed (measurement) signal.
    y = pre.lowpassed
    half = max(1, int(round(cfg.refine_window_s * fs)))
    refined = np.empty_like(candidates)
    for k, p in enumerate(candidates):
        lo, hi = max(0, p - half), min(y.size, p + half + 1)
        refined[k] = lo + int(np.argmax(y[lo:hi]))
    peaks = np.unique(refined)

    # One trough per beat, searched strictly between the previous peak and this one.
    if peaks.size > 1:
        typical_ibi = int(np.median(np.diff(peaks)))
    else:
        typical_ibi = int(round(fs * 60.0 / cfg.min_hr_bpm))
    min_rise = max(1, int(round(MIN_RISE_S * fs)))
    keep_peaks, keep_troughs = [], []
    previous = -1
    for p in peaks:
        start = max(previous + 1, p - typical_ibi, 0)
        previous = p
        if p - start < min_rise:
            continue  # no room for an onset (start of record)
        trough = start + int(np.argmin(y[start:p]))
        if p - trough < min_rise or pre.bad_mask[trough : p + 1].any():
            continue
        keep_peaks.append(p)
        keep_troughs.append(trough)

    if not keep_peaks:
        return _empty(n_candidates)
    peak_idx = np.asarray(keep_peaks, dtype=int)
    trough_idx = np.asarray(keep_troughs, dtype=int)
    peak_time = pre.time_s[peak_idx]
    ibi, ibi_ok = check_intervals(peak_time, cfg)
    return PulseDetection(
        peak_idx=peak_idx,
        trough_idx=trough_idx,
        peak_time_s=peak_time,
        trough_time_s=pre.time_s[trough_idx],
        peak_value=y[peak_idx],
        trough_value=y[trough_idx],
        ibi_s=ibi,
        ibi_ok=ibi_ok,
        n_candidates=n_candidates,
    )
