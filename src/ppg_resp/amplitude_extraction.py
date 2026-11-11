"""Peak-to-peak (pulse) amplitude extraction - the core measurement.

For beat ``i`` with systolic peak value ``P_i`` and onset (trough) value
``T_i``::

    A_i = P_i - T_i

time-stamped at the systolic peak. The sequence ``A(t_i)`` is the amplitude-
modulation (AM) series from which breathing is estimated.

Caveat that matters for interpretation: the trough precedes the peak by the
pulse rise time (roughly 0.1-0.3 s), so a moving baseline adds
``slope * rise_time`` to ``A_i``. Plain peak-minus-trough amplitude therefore
contains some baseline wander, not only true amplitude modulation. To
quantify that, a baseline-corrected amplitude is also computed
(``am_detrended``): the peak height above the straight line joining the
beat's own trough and the next one, which cancels a locally linear baseline.

Further per-beat series, for *comparison only*:

* baseline wander (BW): ``(P_i + T_i) / 2``
* frequency modulation (FM): the inter-beat interval ``t_i - t_(i-1)``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy.ndimage import median_filter

from .config import FEATURES, PulseConfig
from .errors import PPGError
from .pulse_detection import PulseDetection


@dataclass
class BeatFeatures:
    """Per-beat measurements. ``valid`` marks beats whose amplitude is trusted."""

    time_s: np.ndarray
    amplitude: np.ndarray
    amplitude_detrended: np.ndarray  # NaN where the next trough is unavailable
    baseline: np.ndarray
    ibi_s: np.ndarray
    ibi_ok: np.ndarray
    valid: np.ndarray

    @property
    def n_beats(self) -> int:
        return int(self.time_s.size)

    @property
    def n_valid(self) -> int:
        return int(self.valid.sum())

    def series(self, feature: str = "am") -> Tuple[np.ndarray, np.ndarray]:
        """Return (times, values) of the trusted beats for one modulation type."""
        if feature == "am":
            keep = self.valid
            return self.time_s[keep], self.amplitude[keep]
        if feature == "am_detrended":
            keep = self.valid & np.isfinite(self.amplitude_detrended)
            return self.time_s[keep], self.amplitude_detrended[keep]
        if feature == "bw":
            keep = self.valid
            return self.time_s[keep], self.baseline[keep]
        if feature == "fm":
            keep = self.ibi_ok
            return self.time_s[keep], self.ibi_s[keep]
        raise ValueError(f"feature must be one of {FEATURES}, got {feature!r}")


def verify_pairing(pulses: PulseDetection) -> None:
    """Assert that every peak has its own trough, strictly ordered in time.

    Order required: T_1 < P_1 < T_2 < P_2 < ... A trough shared by two beats,
    or one lying after its peak, would corrupt the amplitude series silently,
    so a violation is treated as a programming error.
    """
    peaks, troughs = pulses.peak_idx, pulses.trough_idx
    if peaks.shape != troughs.shape:
        raise PPGError("peak/trough arrays differ in length")
    if peaks.size == 0:
        return
    if np.any(troughs >= peaks):
        raise PPGError("a trough does not precede its peak")
    if np.any(troughs[1:] <= peaks[:-1]):
        raise PPGError("a trough is not located after the previous peak (shared or misassigned trough)")


def extract_amplitudes(pulses: PulseDetection, cfg: Optional[PulseConfig] = None) -> BeatFeatures:
    """Compute A_i = P_i - T_i for every beat and flag implausible values.

    A beat is rejected when its amplitude is non-positive or lies outside
    [amp_ratio_low, amp_ratio_high] times the running median of neighbouring
    beats. Respiratory modulation is typically tens of percent at most, so
    these wide limits remove artifacts without touching the modulation itself.
    """
    cfg = cfg or PulseConfig()
    verify_pairing(pulses)
    amplitude = pulses.peak_value - pulses.trough_value
    baseline = 0.5 * (pulses.peak_value + pulses.trough_value)
    valid = amplitude > 0
    if valid.sum() >= 3:
        size = min(cfg.local_beats, int(valid.sum()))
        size = size if size % 2 else size - 1
        local = np.full(amplitude.size, np.nan)
        local[valid] = median_filter(amplitude[valid], size=max(size, 1), mode="nearest")
        ratio = np.divide(amplitude, local, out=np.zeros_like(amplitude), where=valid)
        valid &= (ratio >= cfg.amp_ratio_low) & (ratio <= cfg.amp_ratio_high)
    # Baseline-corrected variant: height above the line through this beat's
    # trough and the next beat's trough (only for consecutive beats).
    detrended = np.full(amplitude.size, np.nan)
    if amplitude.size > 1:
        consecutive = pulses.ibi_ok[1:]
        t_trough, v_trough = pulses.trough_time_s, pulses.trough_value
        span = t_trough[1:] - t_trough[:-1]
        frac = (pulses.peak_time_s[:-1] - t_trough[:-1]) / span
        line = v_trough[:-1] + (v_trough[1:] - v_trough[:-1]) * frac
        detrended[:-1] = np.where(consecutive, pulses.peak_value[:-1] - line, np.nan)

    return BeatFeatures(
        time_s=pulses.peak_time_s.copy(),
        amplitude=amplitude,
        amplitude_detrended=detrended,
        baseline=baseline,
        ibi_s=pulses.ibi_s.copy(),
        ibi_ok=pulses.ibi_ok.copy(),
        valid=valid,
    )
