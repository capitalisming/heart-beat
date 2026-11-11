"""Statistical comparison of PPG-derived and reference respiratory rates.

Conventions: ``error = estimate - reference`` (breaths/min), so a positive
bias means the PPG method reads high.

Statistical cautions built into this module
-------------------------------------------
* Overlapping windows of one recording are not independent observations.
  Window-level numbers are descriptive; no p-values are computed for them.
* The unit of inference is the recording, clustered by participant. Confidence
  intervals come from a cluster bootstrap over participants and are only
  produced when there are enough participants.
* Correlation measures association, not agreement: a method can correlate
  perfectly and still be wrong by a constant or proportional error. Agreement
  is judged by MAE/RMSE, bias and Bland-Altman limits.
* Only accepted (status OK) windows have estimates. Accuracy is therefore
  always reported together with coverage, and the metrics of all computable
  candidates are given alongside to expose what the quality gate hides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from .ground_truth import BreathReference
from .pipeline import PipelineResult
from .signal_quality import STATUS_OK

PAIR_COLUMNS = [
    "recording_id", "participant_id", "session_id", "condition", "t_start_s", "t_end_s",
    "status", "sqi", "methods_agree", "rr_ref", "rr_est", "rr_candidate",
]
MIN_CLUSTERS_FOR_CI = 6


def window_pairs(
    result: PipelineResult,
    reference: BreathReference,
    recording_id: str,
    participant_id: str = "unknown",
    session_id: str = "unknown",
    condition: str = "unspecified",
    rule: str = "interval",
) -> pd.DataFrame:
    """Pair every analysis window with the reference rate of the same time span.

    A ``count`` reference (N breaths in T seconds) has no time resolution: its
    single whole-recording rate is paired with every window, which assumes the
    breathing rate was steady. Only recording-level results are meaningful then.
    """
    whole = reference.rate() if reference.kind == "count" else None
    rows = []
    for w in result.windows:
        rows.append(
            {
                "recording_id": recording_id,
                "participant_id": participant_id,
                "session_id": session_id,
                "condition": condition,
                "t_start_s": w.t_start_s,
                "t_end_s": w.t_end_s,
                "status": w.status,
                "sqi": w.sqi,
                "methods_agree": w.indicators.get("methods_agree"),
                "rr_ref": whole if reference.kind == "count" else reference.rate(w.t_start_s, w.t_end_s, rule=rule),
                "rr_est": w.rr_bpm,
                "rr_candidate": w.rr_candidate_bpm,
            }
        )
    frame = pd.DataFrame(rows, columns=PAIR_COLUMNS)
    for column in ("sqi", "rr_ref", "rr_est", "rr_candidate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def agreement_metrics(reference: np.ndarray, estimate: np.ndarray, p_values: bool = True) -> Dict[str, float]:
    """MAE, RMSE, bias, SD of error, Pearson r, Spearman rho and 95 % limits of agreement.

    Pairs with a missing value are dropped. Statistics that need more data
    than available are NaN rather than misleading numbers.
    """
    ref = np.asarray(reference, dtype=float)
    est = np.asarray(estimate, dtype=float)
    keep = np.isfinite(ref) & np.isfinite(est)
    ref, est = ref[keep], est[keep]
    n = int(ref.size)
    out = {k: float("nan") for k in (
        "mae", "rmse", "bias", "sd_error", "loa_low", "loa_high",
        "pearson_r", "pearson_p", "spearman_rho", "spearman_p",
    )}
    out["n"] = n
    if n == 0:
        return out
    error = est - ref
    out["mae"] = float(np.mean(np.abs(error)))
    out["rmse"] = float(np.sqrt(np.mean(error**2)))
    out["bias"] = float(np.mean(error))
    if n >= 2:
        out["sd_error"] = float(np.std(error, ddof=1))
        out["loa_low"] = out["bias"] - 1.96 * out["sd_error"]
        out["loa_high"] = out["bias"] + 1.96 * out["sd_error"]
    if n >= 3 and np.ptp(ref) > 0 and np.ptp(est) > 0:
        pearson = stats.pearsonr(ref, est)
        spearman = stats.spearmanr(ref, est)
        out["pearson_r"], out["spearman_rho"] = float(pearson[0]), float(spearman[0])
        if p_values:
            out["pearson_p"], out["spearman_p"] = float(pearson[1]), float(spearman[1])
    return out


def bland_altman(reference: np.ndarray, estimate: np.ndarray) -> Dict[str, Any]:
    """Bland-Altman statistics for independent pairs (Bland & Altman 1986).

    Returns the per-pair means and differences for plotting, the bias, the
    95 % limits of agreement, approximate 95 % CIs of bias and limits
    (SE of a limit ~ sqrt(3 s^2 / n)), and the slope of difference against
    mean as a check for proportional bias.
    """
    ref = np.asarray(reference, dtype=float)
    est = np.asarray(estimate, dtype=float)
    keep = np.isfinite(ref) & np.isfinite(est)
    ref, est = ref[keep], est[keep]
    diff, mean = est - ref, (est + ref) / 2.0
    n = int(diff.size)
    out: Dict[str, Any] = {"n": n, "mean": mean, "diff": diff}
    for key in ("bias", "sd", "loa_low", "loa_high", "bias_ci", "loa_low_ci", "loa_high_ci", "slope", "slope_p"):
        out[key] = None
    if n < 2:
        return out
    bias, sd = float(diff.mean()), float(diff.std(ddof=1))
    out.update(bias=bias, sd=sd, loa_low=bias - 1.96 * sd, loa_high=bias + 1.96 * sd)
    if n >= 3:
        t_crit = float(stats.t.ppf(0.975, n - 1))
        se_bias, se_loa = sd / np.sqrt(n), np.sqrt(3.0 * sd**2 / n)
        out["bias_ci"] = (bias - t_crit * se_bias, bias + t_crit * se_bias)
        out["loa_low_ci"] = (out["loa_low"] - t_crit * se_loa, out["loa_low"] + t_crit * se_loa)
        out["loa_high_ci"] = (out["loa_high"] - t_crit * se_loa, out["loa_high"] + t_crit * se_loa)
        if np.ptp(mean) > 0:
            fit = stats.linregress(mean, diff)
            out["slope"], out["slope_p"] = float(fit.slope), float(fit.pvalue)
    return out


def bland_altman_repeated(diff: np.ndarray, groups: np.ndarray) -> Dict[str, Any]:
    """Limits of agreement with several observations per participant.

    Follows Bland & Altman (2007) for the case where the true value varies
    within a participant: one-way ANOVA of the differences by participant
    splits the variance into within- and between-participant parts, and the
    limits use their sum. Treating all windows as independent instead would
    make the limits look more certain than they are.
    """
    frame = pd.DataFrame({"d": np.asarray(diff, dtype=float), "g": np.asarray(groups)}).dropna()
    sizes = frame.groupby("g")["d"].size()
    k, n = int(sizes.size), int(len(frame))
    out: Dict[str, Any] = {"n": n, "n_groups": k, "bias": None, "sd": None, "loa_low": None, "loa_high": None,
                           "sd_within": None, "sd_between": None}
    if k < 2 or n <= k:
        return out
    means = frame.groupby("g")["d"].mean()
    grand = float(frame["d"].mean())
    ss_within = float(((frame["d"] - frame["g"].map(means)) ** 2).sum())
    ss_between = float((sizes * (means - grand) ** 2).sum())
    ms_within, ms_between = ss_within / (n - k), ss_between / (k - 1)
    n0 = (n - float((sizes**2).sum()) / n) / (k - 1)
    var_between = max(0.0, (ms_between - ms_within) / n0)
    sd = float(np.sqrt(ms_within + var_between))
    out.update(bias=grand, sd=sd, loa_low=grand - 1.96 * sd, loa_high=grand + 1.96 * sd,
               sd_within=float(np.sqrt(ms_within)), sd_between=float(np.sqrt(var_between)))
    return out


def cluster_bootstrap_ci(
    frame: pd.DataFrame,
    cluster_col: str,
    statistic: Callable[[pd.DataFrame], float],
    n_boot: int = 2000,
    seed: int = 0,
    min_clusters: int = MIN_CLUSTERS_FOR_CI,
) -> Optional[Tuple[float, float]]:
    """Percentile 95 % CI by resampling whole clusters (participants) with replacement.

    Returns None when there are fewer than ``min_clusters`` clusters: with a
    handful of participants a bootstrap interval is not trustworthy.
    """
    clusters = [group for _, group in frame.groupby(cluster_col)]
    if len(clusters) < min_clusters:
        return None
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_boot):
        picks = rng.integers(0, len(clusters), len(clusters))
        value = statistic(pd.concat([clusters[i] for i in picks]))
        if np.isfinite(value):
            values.append(value)
    if len(values) < n_boot / 2:
        return None
    low, high = np.percentile(values, [2.5, 97.5])
    return float(low), float(high)


@dataclass
class EvaluationReport:
    """Complete evaluation of a set of window pairs."""

    coverage: Dict[str, Any]
    window_metrics: Dict[str, float]
    candidate_metrics: Dict[str, float]
    recording_table: pd.DataFrame
    recording_metrics: Dict[str, float]
    recording_ci: Dict[str, Optional[Tuple[float, float]]]
    participant_table: pd.DataFrame
    condition_table: pd.DataFrame
    bland_altman_recordings: Dict[str, Any]
    bland_altman_windows: Dict[str, Any]
    notes: List[str] = field(default_factory=list)


def _recording_table(accepted: pd.DataFrame, all_pairs: pd.DataFrame) -> pd.DataFrame:
    totals = all_pairs[all_pairs["rr_ref"].notna()].groupby("recording_id").size().rename("n_windows_with_ref")
    if accepted.empty:
        return pd.DataFrame(columns=["recording_id", "participant_id", "session_id", "condition",
                                     "n_windows_with_ref", "n_ok", "rr_ref", "rr_est", "error", "mae_windows"])
    grouped = accepted.assign(abs_err=(accepted["rr_est"] - accepted["rr_ref"]).abs()).groupby("recording_id")
    table = grouped.agg(
        participant_id=("participant_id", "first"),
        session_id=("session_id", "first"),
        condition=("condition", "first"),
        n_ok=("rr_est", "size"),
        rr_ref=("rr_ref", "mean"),
        rr_est=("rr_est", "mean"),
        mae_windows=("abs_err", "mean"),
    )
    table["error"] = table["rr_est"] - table["rr_ref"]
    table = table.join(totals).reset_index()
    return table[["recording_id", "participant_id", "session_id", "condition", "n_windows_with_ref",
                  "n_ok", "rr_ref", "rr_est", "error", "mae_windows"]]


def evaluate(pairs: pd.DataFrame, n_boot: int = 2000, seed: int = 0) -> EvaluationReport:
    """Evaluate window pairs at window, recording, participant and condition level."""
    missing = set(PAIR_COLUMNS) - set(pairs.columns)
    if missing:
        raise ValueError(f"pairs table lacks columns: {sorted(missing)}")
    notes: List[str] = []
    with_ref = pairs[pairs["rr_ref"].notna()]
    accepted = with_ref[(with_ref["status"] == STATUS_OK) & with_ref["rr_est"].notna()]
    candidates = with_ref[with_ref["rr_candidate"].notna()]

    coverage = {
        "n_windows": int(len(pairs)),
        "n_windows_with_reference": int(len(with_ref)),
        "n_accepted": int(len(accepted)),
        "accepted_fraction": float(len(accepted) / len(with_ref)) if len(with_ref) else float("nan"),
        "n_recordings": int(pairs["recording_id"].nunique()),
        "n_participants": int(pairs["participant_id"].nunique()),
        "status_counts": pairs["status"].value_counts().to_dict(),
    }

    window_metrics = agreement_metrics(accepted["rr_ref"], accepted["rr_est"], p_values=False)
    candidate_metrics = agreement_metrics(candidates["rr_ref"], candidates["rr_candidate"], p_values=False)
    notes.append(
        "Window-level statistics are descriptive: overlapping windows of one recording are not "
        "independent, so no p-values are given for them."
    )

    rec = _recording_table(accepted, pairs)
    recording_metrics = agreement_metrics(rec["rr_ref"], rec["rr_est"]) if len(rec) else agreement_metrics([], [])
    n_participants = int(rec["participant_id"].nunique()) if len(rec) else 0
    recording_ci: Dict[str, Optional[Tuple[float, float]]] = {"mae": None, "bias": None}
    if len(rec):
        recording_ci["mae"] = cluster_bootstrap_ci(rec, "participant_id", lambda d: float(d["error"].abs().mean()), n_boot, seed)
        recording_ci["bias"] = cluster_bootstrap_ci(rec, "participant_id", lambda d: float(d["error"].mean()), n_boot, seed)
    if n_participants < MIN_CLUSTERS_FOR_CI:
        notes.append(
            f"Only {n_participants} participant(s) with accepted data: no confidence intervals are reported and "
            "the results describe this sample only; they do not generalise."
        )
    if len(rec) and len(rec) > n_participants:
        notes.append(
            "Several recordings per participant: recording-level p-values ignore this clustering and are "
            "optimistic. Prefer the cluster-bootstrap intervals."
        )
    if len(rec) < 3:
        notes.append("Fewer than 3 recordings with accepted data: correlation is undefined or meaningless.")
    if len(rec) and np.ptp(rec["rr_ref"].to_numpy()) < 4.0:
        notes.append(
            "Reference rates span less than 4 breaths/min: correlation is weakly identified (restricted range)."
        )

    if len(rec):
        participant_table = (
            rec.assign(abs_error=rec["error"].abs())
            .groupby("participant_id")
            .agg(n_recordings=("recording_id", "size"), mae=("abs_error", "mean"), bias=("error", "mean"))
            .reset_index()
        )
        condition_rows = []
        for condition, group in rec.groupby("condition"):
            metrics = agreement_metrics(group["rr_ref"], group["rr_est"])
            condition_rows.append({"condition": condition, "n_recordings": len(group),
                                   **{k: metrics[k] for k in ("mae", "rmse", "bias", "sd_error")}})
        condition_table = pd.DataFrame(condition_rows)
    else:
        participant_table = pd.DataFrame(columns=["participant_id", "n_recordings", "mae", "bias"])
        condition_table = pd.DataFrame(columns=["condition", "n_recordings", "mae", "rmse", "bias", "sd_error"])

    return EvaluationReport(
        coverage=coverage,
        window_metrics=window_metrics,
        candidate_metrics=candidate_metrics,
        recording_table=rec,
        recording_metrics=recording_metrics,
        recording_ci=recording_ci,
        participant_table=participant_table,
        condition_table=condition_table,
        bland_altman_recordings=bland_altman(rec["rr_ref"], rec["rr_est"]) if len(rec) else bland_altman([], []),
        bland_altman_windows=bland_altman_repeated(
            (accepted["rr_est"] - accepted["rr_ref"]).to_numpy(), accepted["participant_id"].to_numpy()
        ),
        notes=notes,
    )


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    if isinstance(value, tuple):
        return f"[{value[0]:.{digits}f}, {value[1]:.{digits}f}]"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(frame: pd.DataFrame, digits: int = 2) -> str:
    """Render a DataFrame as a Markdown table (no optional dependencies)."""
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    rule = "|" + "|".join("---" for _ in frame.columns) + "|"
    rows = [
        "| " + " | ".join(_fmt(float(v), digits) if isinstance(v, (float, np.floating)) else str(v) for v in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([header, rule] + rows)


def format_report(report: EvaluationReport, title: str = "Evaluation") -> str:
    """Render an :class:`EvaluationReport` as Markdown."""
    c, wm, cm, rm = report.coverage, report.window_metrics, report.candidate_metrics, report.recording_metrics
    ba, bar = report.bland_altman_recordings, report.bland_altman_windows
    lines = [
        f"# {title}", "",
        "Error = estimate - reference, in breaths/min.", "",
        "## Data and coverage", "",
        f"- Participants: {c['n_participants']}, recordings: {c['n_recordings']}, windows: {c['n_windows']}",
        f"- Windows with a reference rate: {c['n_windows_with_reference']}",
        f"- Accepted by the quality gate: {c['n_accepted']} ({_fmt(100 * c['accepted_fraction'], 1)} %)",
        f"- Window statuses: {c['status_counts']}", "",
        "## Recording level (unit of inference)", "",
        "| n | MAE | RMSE | Bias | SD of error | Pearson r | Spearman rho |",
        "|---|-----|------|------|-------------|-----------|--------------|",
        f"| {rm['n']} | {_fmt(rm['mae'])} | {_fmt(rm['rmse'])} | {_fmt(rm['bias'])} | {_fmt(rm['sd_error'])} | "
        f"{_fmt(rm['pearson_r'], 3)} | {_fmt(rm['spearman_rho'], 3)} |", "",
        f"- 95 % CI of MAE (cluster bootstrap over participants): {_fmt(report.recording_ci['mae'])}",
        f"- 95 % CI of bias (cluster bootstrap over participants): {_fmt(report.recording_ci['bias'])}", "",
        "## Bland-Altman", "",
        f"- Recording level: bias {_fmt(ba['bias'])} (95 % CI {_fmt(ba['bias_ci'])}), limits of agreement "
        f"{_fmt(ba['loa_low'])} to {_fmt(ba['loa_high'])}; slope of difference vs mean {_fmt(ba['slope'], 3)} "
        f"(p = {_fmt(ba['slope_p'], 3)})",
        f"- Window level, repeated-measures limits (between + within participant variance): bias {_fmt(bar['bias'])}, "
        f"limits {_fmt(bar['loa_low'])} to {_fmt(bar['loa_high'])} (SD within {_fmt(bar['sd_within'])}, "
        f"between {_fmt(bar['sd_between'])})", "",
        "## Window level (descriptive only)", "",
        "| Set | n | MAE | RMSE | Bias | SD of error | Pearson r | Spearman rho |",
        "|-----|---|-----|------|------|-------------|-----------|--------------|",
        f"| Accepted windows | {wm['n']} | {_fmt(wm['mae'])} | {_fmt(wm['rmse'])} | {_fmt(wm['bias'])} | "
        f"{_fmt(wm['sd_error'])} | {_fmt(wm['pearson_r'], 3)} | {_fmt(wm['spearman_rho'], 3)} |",
        f"| All candidates (gate ignored) | {cm['n']} | {_fmt(cm['mae'])} | {_fmt(cm['rmse'])} | {_fmt(cm['bias'])} | "
        f"{_fmt(cm['sd_error'])} | {_fmt(cm['pearson_r'], 3)} | {_fmt(cm['spearman_rho'], 3)} |", "",
    ]
    if len(report.condition_table):
        lines += ["## By condition (recording level)", "", markdown_table(report.condition_table), ""]
    if len(report.participant_table):
        lines += ["## By participant", "", markdown_table(report.participant_table), ""]
    lines += ["## Notes", ""] + [f"- {note}" for note in report.notes]
    lines += [
        "- A high correlation alone does not show that the two methods agree; judge agreement by MAE, bias and "
        "the limits of agreement.",
        "",
    ]
    return "\n".join(lines)
