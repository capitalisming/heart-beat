"""Figures for every stage of the pipeline and for the evaluation.

Plotting only: no analysis is performed here. Each function takes results
computed elsewhere and returns a Matplotlib figure, so figures always show
exactly what the algorithm used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from .evaluation import bland_altman  # noqa: E402
from .ground_truth import BreathReference  # noqa: E402
from .pipeline import PipelineResult  # noqa: E402
from .signal_quality import STATUS_INSUFFICIENT, STATUS_LOW_QUALITY, STATUS_OK  # noqa: E402

BLUE, ORANGE, GREEN, RED, GREY = "#1f5fa8", "#d9730d", "#2a8a4a", "#c0392b", "#7a7a7a"
STATUS_COLOR = {STATUS_OK: GREEN, STATUS_LOW_QUALITY: ORANGE, STATUS_INSUFFICIENT: GREY}
FEATURE_LABEL = {
    "am": "Pulse amplitude A = P − T",
    "am_detrended": "Baseline-corrected pulse amplitude",
    "bw": "Pulse baseline (P + T) / 2",
    "fm": "Inter-beat interval (s)",
}
ZOOM_S = 15.0


def _new(title: str, height: float = 3.4) -> Tuple[Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(10, height), constrained_layout=True)
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    ax.grid(alpha=0.25)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return fig, ax


def _units(result: PipelineResult) -> str:
    return "ADC counts" if result.settings.sensor.name.upper().startswith("MAX") else "a.u."


def _zoom(result: PipelineResult) -> Tuple[float, float]:
    """A 15 s span from the middle of the recording, where beats are visible individually."""
    t = result.pre.time_s
    mid = 0.5 * (t[0] + t[-1])
    return max(t[0], mid - ZOOM_S / 2), min(t[-1], mid + ZOOM_S / 2)


def plot_raw(result: PipelineResult) -> Figure:
    """1. Raw PPG exactly as recorded."""
    rec = result.recording
    fig, ax = _new(f"1. Raw PPG signal ({result.settings.sensor.name}, fs = {rec.fs_hz:.1f} Hz)")
    ax.plot(rec.time_s, rec.raw, color=BLUE, lw=0.6)
    ax.set(xlabel="Time (s)", ylabel=f"Raw PPG ({_units(result)})")
    return fig


def plot_filtered(result: PipelineResult) -> Figure:
    """2. Preprocessed signal: measurement (low-pass) and detection (band-pass) versions."""
    pre, cfg = result.pre, result.settings.preprocessing
    t0, t1 = _zoom(result)
    sel = (pre.time_s >= t0) & (pre.time_s <= t1)
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True, constrained_layout=True)
    axes[0].plot(pre.time_s[sel], pre.lowpassed[sel], color=BLUE, lw=1)
    axes[0].set_title(
        f"2. Filtered PPG — zero-phase low-pass {cfg.lowpass_hz:g} Hz (amplitude measurement)",
        loc="left", fontsize=11, fontweight="bold",
    )
    axes[0].set_ylabel(f"PPG, systole up ({_units(result)})")
    axes[1].plot(pre.time_s[sel], pre.pulse[sel], color=ORANGE, lw=1)
    axes[1].set_title(
        f"Zero-phase band-pass {cfg.pulse_band_hz[0]:g}–{cfg.pulse_band_hz[1]:g} Hz (beat detection only)",
        loc="left", fontsize=10,
    )
    axes[1].set(xlabel="Time (s)", ylabel=f"Pulse band ({_units(result)})")
    for ax in axes:
        ax.grid(alpha=0.25)
        bad = pre.bad_mask & sel
        if bad.any():
            ax.fill_between(pre.time_s, 0, 1, where=bad, transform=ax.get_xaxis_transform(), color=RED, alpha=0.15)
    return fig


def plot_peaks(result: PipelineResult) -> Figure:
    """3. Detected systolic peaks and their paired troughs (zoomed)."""
    pre, pulses = result.pre, result.pulses
    t0, t1 = _zoom(result)
    sel = (pre.time_s >= t0) & (pre.time_s <= t1)
    fig, ax = _new("3. Detected pulse peaks (▲) and paired onsets (▼)")
    ax.plot(pre.time_s[sel], pre.lowpassed[sel], color=BLUE, lw=1, label="Filtered PPG")
    inside = (pulses.peak_time_s >= t0) & (pulses.peak_time_s <= t1)
    ax.plot(pulses.peak_time_s[inside], pulses.peak_value[inside], "^", color=RED, ms=6, label="Systolic peak P")
    ax.plot(pulses.trough_time_s[inside], pulses.trough_value[inside], "v", color=GREEN, ms=6, label="Onset (trough) T")
    ax.vlines(pulses.peak_time_s[inside], pulses.trough_value[inside], pulses.peak_value[inside],
              color=GREY, lw=0.8, ls=":", label="A = P − T")
    ax.set(xlabel="Time (s)", ylabel=f"PPG, systole up ({_units(result)})")
    ax.legend(loc="upper right", ncol=4, fontsize=8, frameon=False)
    return fig


def plot_amplitudes(result: PipelineResult) -> Figure:
    """4. Pulse-to-pulse amplitude series, one point per beat."""
    feats = result.features
    fig, ax = _new("4. Pulse-to-pulse amplitude A(t) — one value per heartbeat")
    ax.plot(feats.time_s[feats.valid], feats.amplitude[feats.valid], "o-", color=BLUE, ms=3, lw=0.7, label="Accepted beats")
    if (~feats.valid).any():
        ax.plot(feats.time_s[~feats.valid], feats.amplitude[~feats.valid], "x", color=RED, ms=6, label="Rejected beats")
    ax.set(xlabel="Time (s)", ylabel=f"Amplitude ({_units(result)})")
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    return fig


def plot_resp_signal(result: PipelineResult) -> Figure:
    """5. Respiratory modulation signal: beat series interpolated to a uniform grid."""
    resp = result.resp
    fig, ax = _new(f"5. Respiratory modulation signal — resampled to {resp.fs_hz:g} Hz ({result.settings.respiratory.interpolation})")
    ax.plot(resp.beat_time_s, resp.beat_value, "o", color=GREY, ms=3, label="Per-beat values")
    ax.plot(resp.time_s, resp.interpolated, color=BLUE, lw=1, label="Uniformly resampled")
    ax.set(xlabel="Time (s)", ylabel=FEATURE_LABEL.get(resp.feature, resp.feature))
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    return fig


def plot_resp_filtered(result: PipelineResult, reference: Optional[BreathReference] = None) -> Figure:
    """6. Respiratory-band component, with reference breath events if available."""
    resp = result.resp
    low, high = resp.band_hz
    fig, ax = _new(f"6. Respiratory band-pass {low:g}–{high:g} Hz ({60 * low:g}–{60 * high:g} breaths/min)")
    ax.plot(resp.time_s, resp.filtered, color=BLUE, lw=1.1, label="Respiratory component of the PPG amplitude")
    if reference is not None and reference.event_times_s.size:
        events = reference.event_times_s
        events = events[(events >= resp.time_s[0]) & (events <= resp.time_s[-1])]
        for k, t in enumerate(events):
            ax.axvline(t, color=ORANGE, lw=0.8, alpha=0.7, label=f"Reference breaths ({reference.source})" if k == 0 else None)
    ax.set(xlabel="Time (s)", ylabel="Modulation (units of the feature)")
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    return fig


def plot_rr_over_time(result: PipelineResult, reference: Optional[BreathReference] = None) -> Figure:
    """7. Estimated respiratory rate per window, withheld windows shown separately."""
    rc = result.settings.respiratory
    fig, ax = _new(
        f"7. Estimated respiratory rate over time ({rc.window_s:g} s windows, step {rc.step_s:g} s, method: {rc.primary_method})"
    )
    for status, marker in ((STATUS_OK, "o"), (STATUS_LOW_QUALITY, "x")):
        rows = [w for w in result.windows if w.status == status and w.rr_candidate_bpm is not None]
        if rows:
            centres = [0.5 * (w.t_start_s + w.t_end_s) for w in rows]
            label = "Accepted estimate" if status == STATUS_OK else "Withheld (low quality)"
            ax.plot(centres, [w.rr_candidate_bpm for w in rows], marker, color=STATUS_COLOR[status], ms=6, label=label)
    insufficient = [w for w in result.windows if w.status == STATUS_INSUFFICIENT]
    for k, w in enumerate(insufficient):
        ax.axvspan(w.t_start_s, w.t_end_s, color=GREY, alpha=0.08, label="Insufficient data" if k == 0 else None)
    if reference is not None and reference.kind == "events":
        centres, rates = [], []
        for w in result.windows:
            rate = reference.rate(w.t_start_s, w.t_end_s)
            if rate is not None:
                centres.append(0.5 * (w.t_start_s + w.t_end_s))
                rates.append(rate)
        if rates:
            ax.plot(centres, rates, "-", color=ORANGE, lw=1.5, label=f"Reference ({reference.source})")
    ax.set(xlabel="Window centre (s)", ylabel="Respiratory rate (breaths/min)",
           ylim=(60 * rc.band_hz[0] - 1, 60 * rc.band_hz[1] + 1))
    ax.legend(loc="upper right", fontsize=8, frameon=False, ncol=2)
    return fig


def plot_spectrum(result: PipelineResult) -> Figure:
    """Supplement: spectrum of the middle analysis window with the searched band."""
    usable = [w for w in result.windows if w.spectrum is not None]
    fig, ax = _new("Spectrum of the respiratory signal (middle window)")
    if usable:
        w = usable[len(usable) // 2]
        freq, power = w.spectrum
        ax.plot(60 * freq, power / power.max(), color=BLUE, lw=1.2)
        ax.axvspan(w.indicators["band_low_bpm"], w.indicators["band_high_bpm"], color=GREEN, alpha=0.08, label="Search band")
        if w.rr_candidate_bpm is not None:
            ax.axvline(w.rr_candidate_bpm, color=RED, lw=1, ls="--",
                       label=f"Dominant peak {w.rr_candidate_bpm:.1f} /min (SQI {w.sqi:.0f}, {w.status})")
        ax.legend(loc="upper right", fontsize=8, frameon=False)
    ax.set(xlabel="Frequency (breaths/min)", ylabel="Normalised power", xlim=(0, 45))
    return fig


def save_recording_figures(
    result: PipelineResult, out_dir: Union[str, Path], reference: Optional[BreathReference] = None, prefix: str = ""
) -> List[Path]:
    """Save all per-recording figures that the available stages allow."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    makers = [("01_raw_ppg", lambda: plot_raw(result))]
    if result.pre is not None:
        makers.append(("02_filtered_ppg", lambda: plot_filtered(result)))
    if result.pulses is not None and result.pulses.n_beats:
        makers.append(("03_pulse_peaks", lambda: plot_peaks(result)))
        makers.append(("04_pulse_amplitude", lambda: plot_amplitudes(result)))
    if result.resp is not None:
        makers.append(("05_respiratory_modulation", lambda: plot_resp_signal(result)))
        makers.append(("06_respiratory_band", lambda: plot_resp_filtered(result, reference)))
    if result.windows:
        makers.append(("07_rr_over_time", lambda: plot_rr_over_time(result, reference)))
        makers.append(("07b_spectrum", lambda: plot_spectrum(result)))
    paths = []
    for name, make in makers:
        fig = make()
        path = out_dir / f"{prefix}{name}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(path)
    return paths


# ------------------------------------------------------------------ evaluation


def plot_estimate_vs_reference(table: pd.DataFrame, level: str = "recording", note: str = "") -> Figure:
    """8. PPG estimate against the reference, with the line of identity."""
    fig, ax = plt.subplots(figsize=(5.6, 5.4), constrained_layout=True)
    conditions = sorted(table["condition"].astype(str).unique()) if "condition" in table else ["all"]
    cmap = plt.get_cmap("tab10")
    for k, condition in enumerate(conditions):
        rows = table[table["condition"].astype(str) == condition] if "condition" in table else table
        ax.plot(rows["rr_ref"], rows["rr_est"], "o", color=cmap(k % 10), ms=5, alpha=0.8, label=condition)
    values = pd.concat([table["rr_ref"], table["rr_est"]]).dropna()
    if len(values):
        low, high = values.min() - 2, values.max() + 2
        ax.plot([low, high], [low, high], color=GREY, lw=1, ls="--", label="Identity (perfect agreement)")
        ax.set(xlim=(low, high), ylim=(low, high))
    ax.set_title(f"8. PPG estimate vs ground truth ({level} level){note}", loc="left", fontsize=11, fontweight="bold")
    ax.set(xlabel="Reference respiratory rate (breaths/min)", ylabel="PPG-derived respiratory rate (breaths/min)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.25)
    if len(values):
        ax.legend(fontsize=7, frameon=False, loc="upper left")
    return fig


def plot_error_distribution(table: pd.DataFrame, level: str = "recording", note: str = "") -> Figure:
    """9. Histogram of errors (estimate − reference)."""
    error = (table["rr_est"] - table["rr_ref"]).dropna()
    fig, ax = _new(f"9. Error distribution ({level} level, n = {len(error)}){note}", height=3.8)
    if len(error):
        ax.hist(error, bins=min(30, max(5, int(np.sqrt(len(error)) * 2))), color=BLUE, alpha=0.85, edgecolor="white")
        ax.axvline(0, color=GREY, lw=1)
        ax.axvline(error.mean(), color=RED, lw=1.2, ls="--", label=f"Bias {error.mean():+.2f}")
        ax.legend(fontsize=8, frameon=False)
    ax.set(xlabel="Error = estimate − reference (breaths/min)", ylabel="Count")
    return fig


def plot_bland_altman(table: pd.DataFrame, level: str = "recording", note: str = "") -> Figure:
    """10. Bland–Altman plot with bias and 95 % limits of agreement."""
    ba = bland_altman(table["rr_ref"], table["rr_est"])
    fig, ax = _new(f"10. Bland–Altman plot ({level} level, n = {ba['n']}){note}", height=4.2)
    ax.plot(ba["mean"], ba["diff"], "o", color=BLUE, ms=5, alpha=0.8)
    ax.axhline(0, color=GREY, lw=0.8)
    if ba["bias"] is not None:
        for value, label, style in (
            (ba["bias"], f"Bias {ba['bias']:+.2f}", "-"),
            (ba["loa_high"], f"+1.96 SD = {ba['loa_high']:+.2f}", "--"),
            (ba["loa_low"], f"−1.96 SD = {ba['loa_low']:+.2f}", "--"),
        ):
            ax.axhline(value, color=RED, lw=1.1, ls=style, label=label)
        ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.set(xlabel="Mean of PPG estimate and reference (breaths/min)",
           ylabel="Difference: estimate − reference (breaths/min)")
    return fig


def save_evaluation_figures(
    recording_table: pd.DataFrame, out_dir: Union[str, Path], level: str = "recording", note: str = ""
) -> Dict[str, Path]:
    """Save figures 8-10 for a table with ``rr_ref`` and ``rr_est`` columns."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, maker in (
        ("08_estimate_vs_reference", plot_estimate_vs_reference),
        ("09_error_distribution", plot_error_distribution),
        ("10_bland_altman", plot_bland_altman),
    ):
        fig = maker(recording_table, level, note)
        out[name] = out_dir / f"{name}.png"
        fig.savefig(out[name], dpi=150)
        plt.close(fig)
    return out
