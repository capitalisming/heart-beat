#!/usr/bin/env python3
"""Record a PPG session from the ESP32-S3 + MAX30102 streamer over USB serial.

Writes the raw stream to data/raw/<participant>_<session>_<condition>.csv plus
a .meta.json sidecar, then (optionally) runs the estimator on it.

Examples:
    python scripts/record_serial.py --list
    python scripts/record_serial.py --port /dev/cu.usbserial-0001 --duration 180 \
        --participant P01 --session S1 --condition paced_12 --analyze
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _bootstrap import REPO_ROOT

from ppg_resp.acquisition import record_serial


def list_ports() -> int:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("pyserial is not installed: pip install pyserial", file=sys.stderr)
        return 2
    ports = list(lp.comports())
    if not ports:
        print("No serial ports found. Is the board connected with a data-capable USB cable?")
    for port in ports:
        print(f"{port.device}\t{port.description}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list serial ports and exit")
    parser.add_argument("--port", help="serial port of the board")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=180.0, help="recording length in seconds")
    parser.add_argument("--participant", default="P00", help="anonymous participant code, never a name")
    parser.add_argument("--session", default="S1")
    parser.add_argument("--condition", default="rest", help="e.g. rest, paced_10, paced_20")
    parser.add_argument("--notes", default="")
    parser.add_argument("--outdir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--analyze", action="store_true", help="run the estimator on the new recording")
    args = parser.parse_args(argv)

    if args.list:
        return list_ports()
    if not args.port:
        parser.error("--port is required (use --list to find it)")
    for label in (args.participant, args.session, args.condition):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            parser.error(f"'{label}': use only letters, digits, '_' and '-' in participant/session/condition")

    out = args.outdir / f"{args.participant}_{args.session}_{args.condition}.csv"
    if out.exists():
        print(f"ERROR: {out} already exists; raw recordings are never overwritten", file=sys.stderr)
        return 2
    meta = {
        "participant_id": args.participant,
        "session_id": args.session,
        "condition": args.condition,
        "notes": args.notes,
        "sensor": "MAX30102",
        "controller": "ESP32-S3-WROOM-1",
        "declared_fs_hz": 100.0,
    }

    def progress(n: int, elapsed: float) -> None:
        print(f"\r  {elapsed:6.1f} s  {n} samples  ({n / max(elapsed, 1e-9):.1f} Hz)", end="", flush=True)

    print(f"Recording {args.duration:.0f} s from {args.port}. Keep the finger still. "
          "Press the marker button at the start of every inhalation.")
    try:
        record_serial(args.port, out, args.duration, args.baud, meta, progress)
    except (RuntimeError, ValueError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    print(f"\nSaved {out}")

    if args.analyze:
        import run_estimation

        return run_estimation.main([str(out), "--fs", "100", "--reference", "markers"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
