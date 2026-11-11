"""Synthetic PPG-like test signals with a known breathing rate.

FOR SOFTWARE VERIFICATION ONLY. These signals follow a simple parametric
model; passing tests on them shows that the code does what the maths says,
not that the method works on people.

Model (t in seconds, f_c = heart rate, f_r = breathing rate, both in Hz)::

    r(t)   = sin(2*pi*f_r*t)                       breathing waveform
    phi(t) = integral of f_c*(1 + m_fm*r(t)) dt    cardiac phase  (FM)
    x(t)   = (1 + m_am*r(t)) * g(phi mod 1)        pulse train    (AM)
             + m_bw*r(t) + drift(t) + noise(t)     baseline       (BW)

``g`` is a two-Gaussian pulse (systolic + diastolic wave) with unit height.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class SyntheticPPG:
    """A simulated recording together with the truth used to create it."""

    time_s: np.ndarray
    values: np.ndarray
    fs_hz: float
    rr_bpm: float
    hr_bpm: float
    breath_times_s: np.ndarray  # start of each simulated breath cycle


def _pulse_shape(phase: np.ndarray) -> np.ndarray:
    """Unit-height pulse as a function of cardiac phase in [0, 1), periodic."""
    out = np.zeros_like(phase)
    for shift in (-1.0, 0.0, 1.0):  # wrap so the template is continuous
        p = phase + shift
        out += np.exp(-0.5 * ((p - 0.25) / 0.08) ** 2)
        out += 0.4 * np.exp(-0.5 * ((p - 0.55) / 0.12) ** 2)
    return out / out.max() if out.max() > 0 else out


def synthetic_ppg(
    duration_s: float = 120.0,
    fs_hz: float = 100.0,
    hr_bpm: float = 70.0,
    rr_bpm: float = 15.0,
    am_depth: float = 0.15,
    bw_depth: float = 0.0,
    fm_depth: float = 0.0,
    noise_sd: float = 0.01,
    drift_depth: float = 0.0,
    seed: Optional[int] = 0,
    sensor_like: bool = False,
) -> SyntheticPPG:
    """Generate a synthetic PPG with selectable respiratory modulations.

    Args:
        am_depth: Relative amplitude modulation (0.15 = +/-15 % pulse height).
        bw_depth: Baseline modulation in units of pulse height.
        fm_depth: Relative heart-rate modulation (respiratory sinus arrhythmia).
        noise_sd: White-noise SD in units of pulse height.
        drift_depth: Slow non-respiratory baseline drift (0.02 Hz) amplitude.
        sensor_like: If True, map to MAX30102-like raw counts: a large DC level
            with an *inverted* small AC component (reflective mode).
    """
    if duration_s <= 0 or fs_hz <= 0 or hr_bpm <= 0 or rr_bpm <= 0:
        raise ValueError("duration_s, fs_hz, hr_bpm and rr_bpm must be positive")
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * fs_hz))
    t = np.arange(n) / fs_hz
    f_r = rr_bpm / 60.0
    f_c = hr_bpm / 60.0

    breathing = np.sin(2 * np.pi * f_r * t)
    inst_hr = f_c * (1.0 + fm_depth * breathing)
    phase = np.cumsum(inst_hr) / fs_hz
    x = (1.0 + am_depth * breathing) * _pulse_shape(np.mod(phase, 1.0))
    x = x + bw_depth * breathing
    x = x + drift_depth * np.sin(2 * np.pi * 0.02 * t + 1.0)
    x = x + noise_sd * rng.standard_normal(n)

    if sensor_like:
        x = 120_000.0 - 1_500.0 * x

    breath_times = np.arange(0.0, duration_s, 1.0 / f_r)
    return SyntheticPPG(
        time_s=t, values=x, fs_hz=fs_hz, rr_bpm=rr_bpm, hr_bpm=hr_bpm, breath_times_s=breath_times
    )
