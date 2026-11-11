"""Turn the per-beat series into a uniformly sampled respiratory signal.

The amplitude series A(t_i) has one sample per heartbeat, i.e. it is
*irregularly* sampled at roughly the heart rate. Spectral methods need a
uniform grid, so the series is interpolated and then band-passed to the
respiratory band.

Design decisions
----------------
* **Linear interpolation by default.** It cannot overshoot, so it never
  creates extrema that were not measured (cubic splines can, especially next
  to an outlier beat). Its cost is a mild low-pass effect near the cardiac
  Nyquist frequency. Shape-preserving PCHIP is available as an alternative.
* **No extrapolation.** The grid spans first to last beat only.
* **No bridging of long gaps.** Where consecutive trusted beats are further
  apart than ``max_beat_gap_s`` the signal is marked invalid (NaN) instead of
  being filled with a straight line that would look like "no breathing".
* **Sampling limit.** Because the series is sampled once per beat, breathing
  faster than half the heart rate cannot be represented (cardiac Nyquist
  limit). The estimator narrows the search band accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy.interpolate import PchipInterpolator

from .config import RespiratoryConfig
from .errors import InsufficientDataError
from .preprocessing import zero_phase_filter

MIN_BEATS = 4


@dataclass
class RespiratorySignal:
    """Respiratory modulation signal on a uniform grid.

    ``interpolated`` is the resampled beat series in its original units;
    ``filtered`` is its respiratory-band component. Both are NaN where
    ``valid`` is False.
    """

    feature: str
    fs_hz: float
    band_hz: Tuple[float, float]
    time_s: np.ndarray
    interpolated: np.ndarray
    filtered: np.ndarray
    valid: np.ndarray
    beat_time_s: np.ndarray
    beat_value: np.ndarray


def contiguous_runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """Return [start, stop) index pairs of consecutive True values."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask.astype(bool), [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def build_respiratory_signal(
    beat_time_s: np.ndarray,
    beat_value: np.ndarray,
    cfg: Optional[RespiratoryConfig] = None,
    feature: Optional[str] = None,
) -> RespiratorySignal:
    """Interpolate a per-beat series to ``cfg.resample_hz`` and band-pass it.

    Raises:
        InsufficientDataError: Fewer than 4 beats, or they span less than one
            sample of the output grid.
    """
    cfg = cfg or RespiratoryConfig()
    t = np.asarray(beat_time_s, dtype=float)
    v = np.asarray(beat_value, dtype=float)
    if t.shape != v.shape:
        raise ValueError("beat_time_s and beat_value must have the same length")
    if t.size < MIN_BEATS:
        raise InsufficientDataError(f"only {t.size} usable beats; at least {MIN_BEATS} are needed")
    if np.any(np.diff(t) <= 0):
        raise ValueError("beat times must be strictly increasing")

    fs = cfg.resample_hz
    first = np.ceil(t[0] * fs - 1e-9)
    last = np.floor(t[-1] * fs + 1e-9)
    if last - first < 1:
        raise InsufficientDataError("beats span less than one sample of the respiratory grid")
    grid = np.arange(first, last + 1) / fs

    if cfg.interpolation == "pchip":
        values = PchipInterpolator(t, v)(grid)
    else:
        values = np.interp(grid, t, v)

    right = np.clip(np.searchsorted(t, grid, side="left"), 1, t.size - 1)
    valid = (t[right] - t[right - 1]) <= cfg.max_beat_gap_s
    interpolated = np.where(valid, values, np.nan)

    filtered = np.full(grid.size, np.nan)
    low, high = cfg.band_hz
    min_run = int(np.ceil(2.0 * fs / low))  # two periods of the slowest breathing
    for start, stop in contiguous_runs(valid):
        if stop - start < min_run:
            valid[start:stop] = False
            interpolated[start:stop] = np.nan
            continue
        segment = interpolated[start:stop]
        filtered[start:stop] = zero_phase_filter(
            segment - segment.mean(), fs, low_hz=low, high_hz=high, order=cfg.filter_order
        )

    return RespiratorySignal(
        feature=feature or cfg.feature,
        fs_hz=fs,
        band_hz=(low, high),
        time_s=grid,
        interpolated=interpolated,
        filtered=filtered,
        valid=valid,
        beat_time_s=t,
        beat_value=v,
    )
