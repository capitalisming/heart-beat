#!/usr/bin/env python3
"""Evaluate the estimator against independent ground truth for a whole study.

The manifest (CSV, one row per recording) lists the PPG file, who and what was
recorded, and where the breathing reference comes from; see README "Data Format".

Example:
    python scripts/run_evaluation.py data/manifest.csv --outdir results/study
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bootstrap import DEFAULT_CONFIG

from ppg_resp.config import FEATURES, load_settings
from ppg_resp.errors import PPGError
from ppg_resp.reporting import run_study, write_evaluation_outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--feature", choices=FEATURES, default=None)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--title", default="PPG respiratory rate vs ground truth")
    parser.add_argument("--recording-figures", action="store_true", help="also save the 7 stage figures per recording")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
        figures = args.outdir / "recordings" if args.recording_figures else None
        pairs, log = run_study(args.manifest, settings, args.feature, figures)
    except (PPGError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failed = log[log["status"] == "ERROR"]
    for _, row in failed.iterrows():
        print(f"SKIPPED {row['recording_id']}: {row['reasons']}", file=sys.stderr)
    if pairs.empty:
        print("ERROR: no recording could be processed; nothing to evaluate", file=sys.stderr)
        return 2

    report = write_evaluation_outputs(pairs, args.outdir, args.title, log=log)
    c, m = report.coverage, report.recording_metrics
    print(f"Recordings processed: {len(log) - len(failed)} of {len(log)}  (participants: {c['n_participants']})")
    print(f"Windows accepted    : {c['n_accepted']} of {c['n_windows_with_reference']} with a reference")
    print(f"Recording level     : n={m['n']}  MAE={m['mae']:.2f}  RMSE={m['rmse']:.2f}  bias={m['bias']:+.2f} breaths/min")
    for note in report.notes:
        print(f"  note: {note}")
    print(f"Report: {args.outdir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
