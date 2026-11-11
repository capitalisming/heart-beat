"""Signal preprocessing: validation, cleaning, zero-phase filtering, artifact mask.

Two filtered versions of the signal are produced on purpose:

* ``pulse`` - band-passed to the cardiac band, used only to *locate* beats.
* ``lowpassed`` - noise-suppressed but with baseline and amplitude intact,
  used to *measure* peak and trough values.

Measuring amplitudes on a high-passed signal would let the filter reshape the
very modulation this project studies, so detection and measurement are kept
apart. All filters are applied forward-backward (zero phase), so beat times
are not shifted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import maximum_filter1d, minimum_filter1d
from scipy.signal import butter, sosfiltfilt

from .acquisition import PPGRecording
from .config import PreprocessingConfig, SensorConfig
from .errors import ConfigError, DataFormatError, InsufficientDataError

MIN_DURATION_S = 5.0
MIN_VALID_FRACTION = 0.5


@dataclass
class PreprocessingReport:
    """What preprocessing found and changed; nothing is fixed silently."""

    n_samples: int
    duration_s: float
    fs_hz: float
    declared_fs_hz: Optional[float]
    fs_mismatch: bool
    resampled: bool
    n_missing: int
    n_out_of_range: int
    n_spikes: int
    n_gap_samples: int
    bad_fraction: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class PreprocessedPPG:
    """Preprocessed signal on a uniform time grid.

    Attributes:
        time_s: Uniform time axis (same origin as the raw recording).
        clean: Oriented (systole up), gap-filled, de-spiked signal.
        lowpassed: ``clean`` after the zero-phase noise low-pass.
        pulse: ``clean`` band-passed to the cardiac band (for detection).
        bad_mask: True where the signal must not be used (gaps, artifacts).
    """

    time_s: np.ndarray
    fs_hz: float
    clean: np.ndarray
    lowpassed: np.ndarray
    pulse: np.ndarray
    bad_mask: np.ndarray
    report: PreprocessingReport


def zero_phase_filter(
    x: np.ndarray,
    fs_hz: float,
    low_hz: Optional[float] = None,
    high_hz: Optional[float] = None,
    order: int = 3,
) -> np.ndarray:
    """Zero-phase Butterworth filter (low-, high- or band-pass).

    Forward-backward filtering squares the magnitude response (effective order
    ``2*order``) and cancels the phase delay.

    Raises:
        ConfigError: Cut-offs are not inside (0, fs/2) or are out of order.
        InsufficientDataError: The signal is too short for the filter's edge padding.
    """
    nyquist = fs_hz / 2.0
    for name, cut in (("low_hz", low_hz), ("high_hz", high_hz)):
        if cut is not None and not 0 < cut < nyquist:
            raise ConfigError(
                f"filter {name}={cut} Hz is incompatible with fs={fs_hz:.3g} Hz (Nyquist {nyquist:.3g} Hz)"
            )
    if low_hz is not None and high_hz is not None:
        if low_hz >= high_hz:
            raise ConfigError(f"band-pass needs low < high, got [{low_hz}, {high_hz}]")
        sos = butter(order, [low_hz, high_hz], btype="bandpass", fs=fs_hz, output="sos")
    elif low_hz is not None:
        sos = butter(order, low_hz, btype="highpass", fs=fs_hz, output="sos")
    elif high_hz is not None:
        sos = butter(order, high_hz, btype="lowpass", fs=fs_hz, output="sos")
    else:
        raise ConfigError("zero_phase_filter needs low_hz and/or high_hz")

    x = np.asarray(x, dtype=float)
    slowest = low_hz if low_hz is not None else high_hz
    # Pad by about two periods of the slowest cut-off to settle edge transients.
    wanted = max(3 * (2 * sos.shape[0] + 1), int(round(2.0 * fs_hz / slowest)))
    padlen = min(wanted, x.size - 1)
    if padlen < 3 * (2 * sos.shape[0] + 1):
        raise InsufficientDataError(f"signal of {x.size} samples is too short to filter")
    return sosfiltfilt(sos, x, padlen=padlen)


def hampel_filter(x: np.ndarray, window: int, k: float) -> tuple:
    """Replace isolated spikes by the local median.

    A sample is a spike when it differs from the median of its ``window``
    neighbours by more than ``k`` robust SDs. The robust SD has a floor (the
    typical sample-to-sample step) so that flat or quantised stretches do not
    turn every tiny step into a "spike". The window is deliberately a few
    samples only: systolic peaks are smooth at that scale and are not clipped.

    Returns:
        (cleaned signal, boolean spike mask)
    """
    if x.size < window:
        return x.copy(), np.zeros(x.size, dtype=bool)
    half = window // 2
    windows = sliding_window_view(np.pad(x, half, mode="edge"), window)
    median = np.median(windows, axis=1)
    mad = np.median(np.abs(windows - median[:, None]), axis=1)
    step = np.abs(np.diff(x))
    floor = float(np.median(step)) if step.size else 0.0
    floor = max(floor, np.finfo(float).eps * max(1.0, float(np.max(np.abs(x)))))
    mask = np.abs(x - median) > k * 1.4826 * np.maximum(mad, floor)
    cleaned = x.copy()
    cleaned[mask] = median[mask]
    return cleaned, mask


def _odd(n: int) -> int:
    n = max(3, int(n))
    return n if n % 2 else n + 1


def preprocess(
    recording: PPGRecording,
    sensor: Optional[SensorConfig] = None,
    cfg: Optional[PreprocessingConfig] = None,
) -> PreprocessedPPG:
    """Validate and clean a raw recording.

    Steps: timestamp checks -> invalid/missing detection -> uniform grid
    (only short gaps are bridged) -> orientation -> spike removal ->
    zero-phase filters -> artifact mask.

    Raises:
        DataFormatError: Timestamps are not increasing or sizes disagree.
        InsufficientDataError: Sampling rate too low, recording too short or
            mostly invalid.
    """
    sensor = sensor or SensorConfig()
    cfg = cfg or PreprocessingConfig()
    t = np.asarray(recording.time_s, dtype=float)
    raw = np.asarray(recording.raw, dtype=float)
    warnings: List[str] = []

    if t.shape != raw.shape or t.ndim != 1:
        raise DataFormatError("time and value arrays must be 1-D and of equal length")
    if t.size < 2:
        raise InsufficientDataError("recording has fewer than 2 samples")
    if not np.all(np.isfinite(t)):
        raise DataFormatError("timestamps contain NaN or infinite values")
    steps = np.diff(t)
    if np.any(steps <= 0):
        raise DataFormatError(
            f"timestamps must be strictly increasing ({int(np.sum(steps <= 0))} violations)"
        )

    step = float(np.median(steps))
    fs = 1.0 / step
    duration = float(t[-1] - t[0])
    if fs < cfg.min_fs_hz * (1 - 1e-6):
        raise InsufficientDataError(
            f"sampling frequency {fs:.2f} Hz is below the minimum {cfg.min_fs_hz} Hz "
            "needed to resolve pulse peaks"
        )
    if duration < MIN_DURATION_S:
        raise InsufficientDataError(f"recording lasts {duration:.1f} s; at least {MIN_DURATION_S} s needed")

    declared = recording.meta.get("declared_fs_hz")
    fs_mismatch = False
    if declared and abs(fs - declared) / declared > cfg.fs_mismatch_tol:
        fs_mismatch = True
        warnings.append(
            f"declared fs {declared:.2f} Hz differs from timestamp-derived {fs:.2f} Hz; "
            "the timestamp-derived value is used"
        )

    # --- missing and invalid values -------------------------------------
    missing = ~np.isfinite(raw)
    out_of_range = np.zeros(raw.size, dtype=bool)
    if sensor.valid_min is not None:
        out_of_range |= ~missing & (raw < sensor.valid_min)
    if sensor.valid_max is not None:
        out_of_range |= ~missing & (raw > sensor.valid_max)
    valid = ~(missing | out_of_range)
    if valid.mean() < MIN_VALID_FRACTION:
        raise InsufficientDataError(
            f"only {100 * valid.mean():.0f} % of samples are valid "
            f"({int(missing.sum())} missing, {int(out_of_range.sum())} out of range)"
        )
    if out_of_range.any():
        warnings.append(f"{int(out_of_range.sum())} samples outside the sensor's valid range (saturation/no data)")

    # --- uniform time grid; bridge only short gaps ----------------------
    irregular = bool(np.max(np.abs(steps - step)) > cfg.timestamp_jitter_tol * step)
    if irregular:
        grid = t[0] + np.arange(int(np.floor(duration / step)) + 1) * step
        warnings.append("irregular timestamps: signal resampled to a uniform grid by linear interpolation")
    else:
        grid = t
    tv, xv = t[valid], raw[valid]
    x = np.interp(grid, tv, xv)
    right = np.clip(np.searchsorted(tv, grid, side="left"), 1, tv.size - 1)
    bracket = tv[right] - tv[right - 1]
    gap_limit = max(cfg.max_interp_gap_s, 1.5 * step)
    gap_mask = (bracket > gap_limit) | (grid < tv[0]) | (grid > tv[-1])
    if gap_mask.any():
        warnings.append(
            f"{gap_mask.sum() * step:.1f} s of signal lies in gaps longer than {gap_limit:.2f} s and is excluded"
        )

    if sensor.invert:
        x = -x

    x, spikes = hampel_filter(x, cfg.hampel_window, cfg.hampel_k)

    # --- zero-phase filters, cut-offs checked against the real fs --------
    nyquist_guard = 0.45 * fs
    lowpass_hz = min(cfg.lowpass_hz, nyquist_guard)
    band_low, band_high = cfg.pulse_band_hz[0], min(cfg.pulse_band_hz[1], nyquist_guard)
    if band_high < cfg.pulse_band_hz[1] or lowpass_hz < cfg.lowpass_hz:
        warnings.append(f"filter cut-offs lowered to {nyquist_guard:.2f} Hz because fs is only {fs:.1f} Hz")
    lowpassed = zero_phase_filter(x, fs, high_hz=lowpass_hz, order=cfg.filter_order)
    pulse = zero_phase_filter(x, fs, low_hz=band_low, high_hz=band_high, order=cfg.filter_order)

    # --- artifact mask from the local pulse range ------------------------
    window = _odd(round(cfg.artifact_window_s * fs))
    local_range = maximum_filter1d(pulse, window, mode="nearest") - minimum_filter1d(pulse, window, mode="nearest")
    usable = ~gap_mask
    reference = float(np.median(local_range[usable])) if usable.any() else 0.0
    # Below this the "pulse" is only numerical residue of filtering a constant.
    has_pulse = reference > 1e-9 * max(float(np.max(np.abs(x))), np.finfo(float).tiny)
    if has_pulse:
        artifact = (local_range > cfg.artifact_high_ratio * reference) | (
            local_range < cfg.artifact_low_ratio * reference
        )
    else:
        artifact = np.ones(x.size, dtype=bool)  # flat line: nothing usable
        warnings.append("signal is flat: no pulsatile component")
    bad_mask = gap_mask | artifact
    if artifact.any() and has_pulse:
        warnings.append(f"{100 * artifact.mean():.1f} % of the signal flagged as artifact or dropout")

    report = PreprocessingReport(
        n_samples=int(grid.size),
        duration_s=duration,
        fs_hz=fs,
        declared_fs_hz=float(declared) if declared else None,
        fs_mismatch=fs_mismatch,
        resampled=irregular,
        n_missing=int(missing.sum()),
        n_out_of_range=int(out_of_range.sum()),
        n_spikes=int(spikes.sum()),
        n_gap_samples=int(gap_mask.sum()),
        bad_fraction=float(bad_mask.mean()),
        warnings=warnings,
    )
    return PreprocessedPPG(
        time_s=grid, fs_hz=fs, clean=x, lowpassed=lowpassed, pulse=pulse, bad_mask=bad_mask, report=report
    )
