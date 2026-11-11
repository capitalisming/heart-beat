"""End-to-end pipeline: raw recording -> windowed respiratory-rate estimates.

    PPGRecording -> preprocess -> detect_pulses -> extract_amplitudes
                 -> build_respiratory_signal -> per-window estimation + quality

Every window ends in exactly one status:

* ``OK``                - an estimate is reported
* ``LOW_QUALITY``       - an estimate could be computed but is withheld
* ``INSUFFICIENT_DATA`` - there is not enough usable signal to estimate at all

A withheld estimate is still stored as ``rr_candidate_bpm`` so that the
quality gate itself can be evaluated against ground truth; it must never be
presented as a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .acquisition import PPGRecording
from .amplitude_extraction import BeatFeatures, extract_amplitudes
from .config import FEATURES, Settings
from .errors import InsufficientDataError
from .preprocessing import PreprocessedPPG, preprocess
from .pulse_detection import PulseDetection, detect_pulses
from .respiratory_estimation import estimate_all, frequency_resolution_bpm
from .respiratory_signal import RespiratorySignal, build_respiratory_signal
from .signal_quality import (
    REPEAT_ADVICE,
    STATUS_INSUFFICIENT,
    STATUS_LOW_QUALITY,
    STATUS_OK,
    beat_coverage,
    compute_sqi,
    main_lobe_half_width_hz,
    spectral_quality,
)


@dataclass
class WindowResult:
    """Result of one analysis window."""

    t_start_s: float
    t_end_s: float
    status: str
    reasons: List[str] = field(default_factory=list)
    rr_bpm: Optional[float] = None
    rr_candidate_bpm: Optional[float] = None
    methods: Dict[str, Optional[float]] = field(default_factory=dict)
    sqi: Optional[float] = None
    components: Dict[str, float] = field(default_factory=dict)
    indicators: Dict[str, Any] = field(default_factory=dict)
    spectrum: Optional[Tuple[np.ndarray, np.ndarray]] = None


@dataclass
class PipelineResult:
    """Everything the pipeline produced, including all intermediate stages."""

    recording: PPGRecording
    settings: Settings
    feature: str
    status: str
    reasons: List[str] = field(default_factory=list)
    pre: Optional[PreprocessedPPG] = None
    pulses: Optional[PulseDetection] = None
    features: Optional[BeatFeatures] = None
    resp: Optional[RespiratorySignal] = None
    windows: List[WindowResult] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        """Recording-level summary; ``rr_bpm`` is the median over accepted windows."""
        accepted = [w.rr_bpm for w in self.windows if w.status == STATUS_OK]
        heart = [w.indicators.get("hr_bpm") for w in self.windows if w.indicators.get("hr_bpm")]
        sqis = [w.sqi for w in self.windows if w.sqi is not None]
        out: Dict[str, Any] = {
            "status": self.status,
            "reasons": "; ".join(self.reasons),
            "feature": self.feature,
            "method": self.settings.respiratory.primary_method,
            "duration_s": self.recording.duration_s,
            "n_windows": len(self.windows),
            "n_ok": len(accepted),
            "ok_fraction": len(accepted) / len(self.windows) if self.windows else 0.0,
            "rr_bpm": float(np.median(accepted)) if accepted else None,
            "rr_min_bpm": float(np.min(accepted)) if accepted else None,
            "rr_max_bpm": float(np.max(accepted)) if accepted else None,
            "hr_bpm": float(np.median(heart)) if heart else None,
            "sqi_median": float(np.median(sqis)) if sqis else None,
            "n_beats": self.features.n_beats if self.features else 0,
        }
        if self.status != STATUS_OK:
            out["advice"] = REPEAT_ADVICE
        return out

    def to_frame(self) -> pd.DataFrame:
        """One row per analysis window (the file written by ``run_estimation.py``)."""
        rows = []
        for w in self.windows:
            row: Dict[str, Any] = {
                "t_start_s": w.t_start_s,
                "t_end_s": w.t_end_s,
                "status": w.status,
                "rr_bpm": w.rr_bpm,
                "rr_candidate_bpm": w.rr_candidate_bpm,
                "sqi": w.sqi,
            }
            row.update({f"rr_{name}_bpm": value for name, value in w.methods.items()})
            row.update({f"q_{name}": value for name, value in w.components.items()})
            row.update({k: v for k, v in w.indicators.items() if not isinstance(v, (tuple, list))})
            row["reasons"] = "; ".join(w.reasons)
            rows.append(row)
        return pd.DataFrame(rows)


def analyze_window(
    t0: float,
    t1: float,
    resp: RespiratorySignal,
    feats: BeatFeatures,
    settings: Settings,
    feature: str,
) -> WindowResult:
    """Estimate the respiratory rate and its quality in the window [t0, t1)."""
    rc, qc = settings.respiratory, settings.quality
    window_s = t1 - t0
    eps = 0.5 / resp.fs_hz
    sel = (resp.time_s >= t0 - eps) & (resp.time_s < t1 - eps)
    y = resp.filtered[sel]

    detected = (feats.time_s >= t0) & (feats.time_s < t1)
    used_t, _ = feats.series(feature)
    used = used_t[(used_t >= t0) & (used_t < t1)]
    n_detected, n_used = int(detected.sum()), int(used.size)
    with_interval = detected & np.isfinite(feats.ibi_s)
    good_ibi = feats.ibi_s[with_interval & feats.ibi_ok]

    components = {
        "coverage": beat_coverage(used, t0, t1, rc.max_beat_gap_s),
        "regularity": float(feats.ibi_ok[with_interval].mean()) if with_interval.any() else 0.0,
        "beat_validity": n_used / n_detected if n_detected else 0.0,
    }
    hr_hz = 1.0 / float(np.median(good_ibi)) if good_ibi.size else None
    indicators: Dict[str, Any] = {
        "n_beats": n_used,
        "hr_bpm": 60.0 * hr_hz if hr_hz else None,
        "resolution_bpm": frequency_resolution_bpm(window_s),
    }
    result = WindowResult(t_start_s=t0, t_end_s=t1, status=STATUS_INSUFFICIENT, components=components, indicators=indicators)

    # ---- hard requirements: without these no estimate is attempted ------
    reasons: List[str] = []
    if n_used < qc.min_beats:
        reasons.append(f"only {n_used} usable pulses in the window (minimum {qc.min_beats})")
    if components["coverage"] < qc.min_coverage:
        reasons.append(
            f"usable pulses cover {100 * components['coverage']:.0f} % of the window (minimum {100 * qc.min_coverage:.0f} %)"
        )
    if y.size < int(round(window_s * resp.fs_hz)) or not np.all(np.isfinite(y)):
        reasons.append("the respiratory signal has a gap inside the window")
    band: Optional[Tuple[float, float]] = None
    if hr_hz is None:
        reasons.append("no plausible inter-beat intervals, heart rate unknown")
    else:
        # One amplitude sample per beat: breathing above HR/2 cannot be represented.
        band = (rc.band_hz[0], min(rc.band_hz[1], hr_hz / 2.0))
        min_width = max(qc.min_band_width_hz, 2.0 * main_lobe_half_width_hz(window_s))
        if band[1] - band[0] < min_width:
            reasons.append(
                f"heart rate {60 * hr_hz:.0f} bpm limits the observable breathing band to "
                f"{60 * band[0]:.0f}-{60 * band[1]:.0f} breaths/min, too narrow to analyse"
            )
    if reasons:
        result.reasons = reasons
        return result
    assert band is not None

    # ---- estimation with every method -----------------------------------
    estimates = estimate_all(y, resp.fs_hz, band)
    result.methods = {name: res.rr_bpm for name, res in estimates.items()}
    primary = estimates[rc.primary_method]
    result.rr_candidate_bpm = primary.rr_bpm

    fft_detail = estimates["fft"].detail
    if "power" in fft_detail:
        freq, power = fft_detail["freq_hz"], fft_detail["power"]
        components["concentration"], components["dominance"] = spectral_quality(freq, power, band, window_s)
        keep = freq <= 1.0
        result.spectrum = (freq[keep], power[keep])
    else:
        components["concentration"] = components["dominance"] = 0.0
    result.sqi = compute_sqi(components)

    available = [v for v in result.methods.values() if v is not None]
    spread = float(max(available) - min(available)) if len(available) >= 2 else None
    level = float(np.nanmean(resp.interpolated[sel]))
    indicators.update(
        {
            "band_low_bpm": 60.0 * band[0],
            "band_high_bpm": 60.0 * band[1],
            "method_spread_bpm": spread,
            "methods_agree": (spread <= qc.agreement_tol_bpm) if spread is not None else None,
            # Peak relative modulation of the feature, e.g. 0.1 = +/-10 %.
            "modulation_depth": float(np.sqrt(2.0) * np.std(y) / abs(level)) if level != 0 else None,
        }
    )

    # ---- quality gate ------------------------------------------------------
    low: List[str] = []
    if primary.rr_bpm is None:
        low.append(f"no respiratory peak found ({primary.detail.get('reason', 'unknown reason')})")
    elif primary.detail.get("edge_peak"):
        low.append("the strongest component lies at the edge of the breathing band (true rate may be outside it)")
    if components["regularity"] < qc.min_regularity:
        low.append(
            f"irregular pulse intervals ({100 * components['regularity']:.0f} % plausible): "
            "motion, poor contact or no real pulse"
        )
    if result.sqi < qc.sqi_threshold:
        low.append(f"SQI {result.sqi:.0f} below threshold {qc.sqi_threshold:.0f}: no clear single breathing rhythm")
    if low:
        result.status = STATUS_LOW_QUALITY
        result.reasons = low
    else:
        result.status = STATUS_OK
        result.rr_bpm = primary.rr_bpm
    return result


def run_pipeline(
    recording: PPGRecording, settings: Optional[Settings] = None, feature: Optional[str] = None
) -> PipelineResult:
    """Run the complete analysis on one recording.

    Args:
        recording: Raw recording (left unmodified).
        settings: Configuration; generic defaults when omitted.
        feature: Modulation to analyse; defaults to ``settings.respiratory.feature``
            (``"am"``, the peak-to-peak amplitude).

    Unusable recordings do not raise: the result carries the status
    ``INSUFFICIENT_DATA`` and the reason. Malformed input (e.g. non-increasing
    timestamps) raises :class:`~ppg_resp.errors.DataFormatError`.
    """
    settings = settings or Settings()
    feature = feature or settings.respiratory.feature
    if feature not in FEATURES:
        raise ValueError(f"feature must be one of {FEATURES}, got {feature!r}")
    result = PipelineResult(recording=recording, settings=settings, feature=feature, status=STATUS_INSUFFICIENT)

    try:
        result.pre = preprocess(recording, settings.sensor, settings.preprocessing)
    except InsufficientDataError as exc:
        result.reasons = [str(exc)]
        return result

    result.pulses = detect_pulses(result.pre, settings.pulse)
    result.features = extract_amplitudes(result.pulses, settings.pulse)
    beat_t, beat_v = result.features.series(feature)
    try:
        result.resp = build_respiratory_signal(beat_t, beat_v, settings.respiratory, feature)
    except InsufficientDataError as exc:
        result.reasons = [f"no usable pulse sequence: {exc}"]
        return result

    rc = settings.respiratory
    resp = result.resp
    span = resp.time_s[-1] - resp.time_s[0] + 1.0 / resp.fs_hz
    if span < rc.window_s:
        result.reasons = [
            f"usable signal lasts {span:.0f} s, shorter than one analysis window ({rc.window_s:.0f} s)"
        ]
        return result

    starts = np.arange(resp.time_s[0], resp.time_s[0] + span - rc.window_s + 1e-9, rc.step_s)
    result.windows = [
        analyze_window(float(t0), float(t0 + rc.window_s), resp, result.features, settings, feature) for t0 in starts
    ]

    statuses = {w.status for w in result.windows}
    if STATUS_OK in statuses:
        result.status = STATUS_OK
    elif STATUS_LOW_QUALITY in statuses:
        result.status = STATUS_LOW_QUALITY
    counts: Dict[str, int] = {}
    if result.status != STATUS_OK:
        for w in result.windows:
            for reason in w.reasons:
                counts[reason.split(" (")[0].split(":")[0]] = counts.get(reason.split(" (")[0].split(":")[0], 0) + 1
        result.reasons = [f"{reason} [{n} window(s)]" for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    return result
