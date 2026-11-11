"""Signal quality assessment, kept separate from the rate estimate.

Three different things are reported and must not be confused:

1. the respiratory-rate estimate (or its absence),
2. the Signal Quality Index (SQI, 0-100) with its components,
3. reliability indicators (agreement between methods, modulation depth, ...).

SQI definition
--------------
Five components, each in [0, 1]:

* ``coverage``      - share of the window covered by trusted beats without long gaps
* ``regularity``    - share of inter-beat intervals that pass the plausibility check
* ``beat_validity`` - share of detected beats whose amplitude was accepted
* ``concentration`` - share of in-band spectral power inside the main lobe of
  the dominant peak, rescaled so that a flat (noise) spectrum scores 0
* ``dominance``     - 1 - (second-highest in-band peak / highest peak)

    SQI = 100 * coverage * regularity * beat_validity * sqrt(concentration * dominance)

The three pulse-level terms multiply because each is a necessary condition:
a window is unusable if any one of them fails. The two spectral terms describe
the same property (one clear breathing rhythm) from two angles, so their
geometric mean enters once.

The SQI is a heuristic ordering of windows from "clear single rhythm on clean
pulses" to "noise". It is NOT a probability that the estimate is correct, and
its acceptance threshold has to be calibrated on real recordings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

STATUS_OK = "OK"
STATUS_LOW_QUALITY = "LOW_QUALITY"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
REPEAT_ADVICE = "Repeat the measurement: keep the finger still, with light steady pressure on the sensor."


@dataclass
class QualityReport:
    """Quality verdict for one window."""

    status: str
    reasons: List[str] = field(default_factory=list)
    sqi: Optional[float] = None
    components: Dict[str, float] = field(default_factory=dict)


def beat_coverage(beat_time_s: np.ndarray, t0: float, t1: float, max_gap_s: float) -> float:
    """Fraction of [t0, t1] covered by consecutive beats no further apart than ``max_gap_s``."""
    if t1 <= t0 or beat_time_s.size == 0:
        return 0.0
    # Window edges count as anchors so a beat just outside is not required.
    points = np.concatenate([[t0], beat_time_s[(beat_time_s > t0) & (beat_time_s < t1)], [t1]])
    gaps = np.diff(points)
    return float(gaps[gaps <= max_gap_s].sum() / (t1 - t0))


def main_lobe_half_width_hz(window_s: float) -> float:
    """Half-width of the Hann window's main lobe (two frequency bins of 1/T)."""
    return 2.0 / window_s


def spectral_quality(
    freq: np.ndarray, power: np.ndarray, band_hz: Tuple[float, float], window_s: float
) -> Tuple[float, float]:
    """Return (concentration, dominance) of the dominant in-band spectral peak."""
    inside = (freq >= band_hz[0]) & (freq <= band_hz[1])
    total = float(power[inside].sum())
    if inside.sum() < 3 or total <= 0:
        return 0.0, 0.0
    f_in, p_in = freq[inside], power[inside]
    k = int(np.argmax(p_in))
    half_width = main_lobe_half_width_hz(window_s)
    lobe = np.abs(f_in - f_in[k]) <= half_width

    # A flat spectrum puts this share of its power in the lobe by chance.
    chance = min(float(lobe.mean()), 0.99)
    concentration = float(np.clip((p_in[lobe].sum() / total - chance) / (1.0 - chance), 0.0, 1.0))

    if lobe.all():
        return concentration, 0.0  # band too narrow to see a competitor
    dominance = float(np.clip(1.0 - p_in[~lobe].max() / p_in[k], 0.0, 1.0))
    return concentration, dominance


def compute_sqi(components: Dict[str, float]) -> float:
    """Combine the five components into the 0-100 index (see module docstring)."""
    pulse = components["coverage"] * components["regularity"] * components["beat_validity"]
    spectral = float(np.sqrt(components["concentration"] * components["dominance"]))
    return float(100.0 * np.clip(pulse * spectral, 0.0, 1.0))
