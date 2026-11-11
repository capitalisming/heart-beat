#!/usr/bin/env python3
"""OPTIONAL: test the algorithm on a public dataset of real human recordings.

The BIDMC PPG and Respiration Dataset (PhysioNet) holds 53 eight-minute
recordings of intensive-care patients: a fingertip PPG at 125 Hz and breaths
annotated by two people on the simultaneous impedance respiration signal - an
independent reference. Source and licence: https://physionet.org/content/bidmc/

This is NOT the project's own experiment: different sensor (transmissive
clinical pulse oximeter), different population (ICU patients). It answers
"does the code work on real PPG at all?", not "how accurate is the MAX30102
build?". Report it separately and cite the dataset.

Nothing is downloaded unless you run this script. Files go to data/external/
(ignored by git). Afterwards:

    python scripts/run_evaluation.py data/external/bidmc/manifest.csv \
        --config config/bidmc.yaml --outdir results/bidmc
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from _bootstrap import REPO_ROOT

BASE_URL = "https://physionet.org/files/bidmc/1.0.0/bidmc_csv/"
FS_HZ = 125.0
N_SUBJECTS = 53


def parse_subjects(text: str) -> list:
    """'1-5,9' -> [1, 2, 3, 4, 5, 9]"""
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    bad = [s for s in out if not 1 <= s <= N_SUBJECTS]
    if bad:
        raise ValueError(f"subject numbers must be 1-{N_SUBJECTS}, got {bad}")
    return sorted(set(out))


def fetch(name: str, folder: Path) -> Path:
    target = folder / name
    if not target.exists():
        print(f"  downloading {name}")
        urllib.request.urlretrieve(BASE_URL + name, target)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subjects", default="1-53", help="e.g. '1-10' or '1,4,7' (default: all 53, a few MB each)")
    parser.add_argument("--outdir", type=Path, default=REPO_ROOT / "data" / "external" / "bidmc")
    args = parser.parse_args(argv)
    try:
        subjects = parse_subjects(args.subjects)
    except ValueError as exc:
        parser.error(str(exc))
    args.outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for s in subjects:
        tag = f"bidmc_{s:02d}"
        try:
            fetch(f"{tag}_Signals.csv", args.outdir)
            breaths_path = fetch(f"{tag}_Breaths.csv", args.outdir)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {tag}: download failed ({exc}); skipped", file=sys.stderr)
            continue
        breaths = pd.read_csv(breaths_path)
        breaths.columns = [c.strip().lower() for c in breaths.columns]
        first = next((c for c in breaths.columns if "ann1" in c), breaths.columns[0])
        events = pd.to_numeric(breaths[first], errors="coerce").dropna() / FS_HZ
        events_file = f"{tag}_breath_events.csv"
        pd.DataFrame({"breath_time_s": events}).to_csv(args.outdir / events_file, index=False)
        rows.append(
            {
                "recording_id": tag,
                "participant_id": tag,
                "session_id": "icu",
                "condition": "icu_rest",
                "ppg_file": f"{tag}_Signals.csv",
                "fs_hz": FS_HZ,
                "channel": "pleth",
                "reference_type": "events",
                "reference_file": events_file,
                "reference_source": "manual annotation of impedance respiration (annotator 1)",
            }
        )
    if not rows:
        print("ERROR: no recordings prepared", file=sys.stderr)
        return 2
    pd.DataFrame(rows).to_csv(args.outdir / "manifest.csv", index=False)
    print(f"Prepared {len(rows)} recordings in {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
