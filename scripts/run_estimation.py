#!/usr/bin/env python3
"""Estimate the respiratory rate of one PPG recording.

Example:
    python scripts/run_estimation.py data/sample/SYNTHETIC_rr12.csv --reference markers
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import DEFAULT_CONFIG, REPO_ROOT

from ppg_resp.acquisition import load_recording
from ppg_resp.config import FEATURES, load_settings
from ppg_resp.errors import PPGError
from ppg_resp.ground_truth import load_reference, reference_from_count, reference_from_markers
from ppg_resp.pipeline import run_pipeline
from ppg_resp.signal_quality import STATUS_OK
from ppg_resp.visualization import save_recording_figures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="PPG recording (CSV/TXT)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="settings YAML")
    parser.add_argument("--fs", type=float, default=None, help="declared sampling frequency in Hz")
    parser.add_argument("--channel", default=None, help="value column to analyse (e.g. ir, red)")
    parser.add_argument("--feature", choices=FEATURES, default=None, help="modulation to analyse (default: am)")
    parser.add_argument("--outdir", type=Path, default=None, help="output folder (default: results/runs/<name>)")
    parser.add_argument(
        "--reference", default=None,
        help="independent breathing reference: 'markers' (button column in the recording), "
             "a breath-event file, or 'count:N' (N breaths counted over the whole recording)",
    )
    parser.add_argument("--no-figures", action="store_true", help="skip figure generation")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        recording = load_recording(args.input, fs_hz=args.fs, channel=args.channel)
        result = run_pipeline(recording, settings, args.feature)
        reference = None
        if args.reference == "markers":
            reference = reference_from_markers(recording)
        elif args.reference and args.reference.startswith("count:"):
            reference = reference_from_count(int(args.reference.split(":", 1)[1]), recording.duration_s)
        elif args.reference:
            reference = load_reference(args.reference)
    except (PPGError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out = args.outdir or REPO_ROOT / "results" / "runs" / args.input.stem
    out.mkdir(parents=True, exist_ok=True)
    summary = result.summary()
    if reference is not None:
        summary["reference_source"] = reference.source
        summary["rr_reference_bpm"] = reference.rate()
    if result.pre is not None:
        summary["preprocessing_warnings"] = result.pre.report.warnings
    result.to_frame().round(4).to_csv(out / "windows.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if not args.no_figures:
        save_recording_figures(result, out / "figures", reference)

    report = result.pre.report if result.pre is not None else None
    print(f"Recording : {args.input}  ({recording.duration_s:.1f} s, fs = {recording.fs_hz:.2f} Hz)")
    if report is not None:
        for warning in report.warnings:
            print(f"  note    : {warning}")
    print(f"Pulses    : {summary['n_beats']} detected, heart rate "
          f"{summary['hr_bpm']:.1f} bpm" if summary["hr_bpm"] else f"Pulses    : {summary['n_beats']} detected")
    print(f"Windows   : {summary['n_ok']} of {summary['n_windows']} accepted")
    if result.status == STATUS_OK:
        print(f"RESPIRATORY RATE (median of accepted windows): {summary['rr_bpm']:.1f} breaths/min "
              f"[range {summary['rr_min_bpm']:.1f}-{summary['rr_max_bpm']:.1f}]")
        print(f"Signal quality index (median): {summary['sqi_median']:.0f} / 100  (a heuristic, not a probability)")
    else:
        print(f"STATUS: {result.status.replace('_', ' ')} - no respiratory rate is reported")
        for reason in result.reasons:
            print(f"  reason  : {reason}")
        print(f"  advice  : {summary['advice']}")
    if reference is not None:
        rate = reference.rate()
        shown = f"{rate:.1f} breaths/min" if rate is not None else "undefined (too few breath events)"
        print(f"Reference ({reference.source}): {shown}")
    print(f"Outputs   : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
