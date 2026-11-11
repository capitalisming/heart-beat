"""Reporting: run a study from a manifest and write tables, figures and a report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from .acquisition import load_recording
from .config import Settings
from .errors import DataFormatError, PPGError
from .evaluation import PAIR_COLUMNS, EvaluationReport, evaluate, format_report, window_pairs
from .ground_truth import BreathReference, load_reference, reference_from_count, reference_from_markers
from .pipeline import run_pipeline
from .visualization import save_evaluation_figures, save_recording_figures

MANIFEST_REQUIRED = ("recording_id", "participant_id", "ppg_file", "reference_type")
REFERENCE_TYPES = ("markers", "events", "waveform", "count")


def _text(row: pd.Series, key: str, default: str = "") -> str:
    value = row.get(key, default)
    return default if pd.isna(value) or str(value).strip() == "" else str(value).strip()


def _number(row: pd.Series, key: str) -> Optional[float]:
    value = row.get(key)
    return None if value is None or pd.isna(value) or str(value).strip() == "" else float(value)


def load_manifest(path: Union[str, Path]) -> pd.DataFrame:
    """Read the study manifest (one row per recording) and check its columns."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    manifest = pd.read_csv(path, comment="#", dtype=str)
    manifest.columns = [c.strip().lower() for c in manifest.columns]
    missing = [c for c in MANIFEST_REQUIRED if c not in manifest.columns]
    if missing:
        raise DataFormatError(f"manifest lacks column(s): {missing}")
    if manifest["recording_id"].duplicated().any():
        raise DataFormatError("manifest has duplicate recording_id values")
    return manifest


def reference_for_row(row: pd.Series, base: Path, recording) -> BreathReference:
    """Build the independent breathing reference named by one manifest row."""
    kind = _text(row, "reference_type").lower()
    offset = _number(row, "time_offset_s") or 0.0
    source = _text(row, "reference_source") or None
    if kind == "markers":
        return reference_from_markers(recording, source or "button marker")
    if kind in ("events", "waveform"):
        file = _text(row, "reference_file")
        if not file:
            raise DataFormatError(f"{row['recording_id']}: reference_file is required for type {kind!r}")
        return load_reference(base / file, kind=kind, source=source, time_offset_s=offset)
    if kind == "count":
        n, duration = _number(row, "n_breaths"), _number(row, "count_duration_s")
        if n is None or duration is None:
            raise DataFormatError(f"{row['recording_id']}: n_breaths and count_duration_s are required for type 'count'")
        return reference_from_count(int(n), duration, source or "manual count")
    raise DataFormatError(f"{row['recording_id']}: reference_type must be one of {REFERENCE_TYPES}")


def run_study(
    manifest_path: Union[str, Path],
    settings: Settings,
    feature: Optional[str] = None,
    figures_dir: Optional[Union[str, Path]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Process every recording of a manifest.

    Returns:
        (window pairs for all recordings, per-recording processing log).
        A recording that cannot be processed is logged with its error and
        skipped; it never contributes invented numbers.
    """
    manifest = load_manifest(manifest_path)
    base = Path(manifest_path).parent
    pairs: List[pd.DataFrame] = []
    log: List[Dict[str, Any]] = []
    for _, row in manifest.iterrows():
        rid = row["recording_id"]
        entry: Dict[str, Any] = {"recording_id": rid, "participant_id": row["participant_id"]}
        try:
            recording = load_recording(
                base / row["ppg_file"], fs_hz=_number(row, "fs_hz"), channel=_text(row, "channel") or None
            )
            reference = reference_for_row(row, base, recording)
            result = run_pipeline(recording, settings, feature)
            entry.update(result.summary())
            entry["reference_source"] = reference.source
            entry["rr_reference_whole_recording"] = reference.rate()
            pairs.append(
                window_pairs(
                    result, reference, rid, row["participant_id"],
                    _text(row, "session_id", "unknown"), _text(row, "condition", "unspecified"),
                )
            )
            if figures_dir is not None:
                save_recording_figures(result, Path(figures_dir) / rid, reference)
        except (PPGError, FileNotFoundError, ValueError) as exc:
            entry.update(status="ERROR", reasons=f"{type(exc).__name__}: {exc}")
        log.append(entry)
    all_pairs = pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(columns=PAIR_COLUMNS)
    return all_pairs, pd.DataFrame(log)


def write_evaluation_outputs(
    pairs: pd.DataFrame,
    out_dir: Union[str, Path],
    title: str,
    preamble: str = "",
    figure_note: str = "",
    log: Optional[pd.DataFrame] = None,
    n_boot: int = 2000,
) -> EvaluationReport:
    """Evaluate window pairs and write tables/, figures/ and report.md under ``out_dir``."""
    out_dir = Path(out_dir)
    tables, figures = out_dir / "tables", out_dir / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    report = evaluate(pairs, n_boot=n_boot)

    pairs.round(4).to_csv(tables / "window_pairs.csv", index=False)
    report.recording_table.round(4).to_csv(tables / "recordings.csv", index=False)
    report.participant_table.round(4).to_csv(tables / "participants.csv", index=False)
    report.condition_table.round(4).to_csv(tables / "conditions.csv", index=False)
    if log is not None:
        log.to_csv(tables / "processing_log.csv", index=False)
    metrics = {
        "coverage": report.coverage,
        "recording_level": report.recording_metrics,
        "recording_level_ci": report.recording_ci,
        "window_level_accepted": report.window_metrics,
        "window_level_all_candidates": report.candidate_metrics,
        "bland_altman_recordings": {k: v for k, v in report.bland_altman_recordings.items() if k not in ("mean", "diff")},
        "bland_altman_windows_repeated": report.bland_altman_windows,
        "notes": report.notes,
    }
    (tables / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    save_evaluation_figures(report.recording_table, figures, "recording", figure_note)
    text = format_report(report, title)
    if preamble:
        heading, _, rest = text.partition("\n")
        text = f"{heading}\n\n{preamble.strip()}\n{rest}"
    text += (
        "\n## Figures\n\n"
        "![Estimate vs reference](figures/08_estimate_vs_reference.png)\n\n"
        "![Error distribution](figures/09_error_distribution.png)\n\n"
        "![Bland-Altman](figures/10_bland_altman.png)\n"
    )
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    return report
