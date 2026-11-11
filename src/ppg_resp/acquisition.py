"""Data acquisition: load recorded PPG files or record new data over Serial.

The raw samples and their timestamps are kept exactly as acquired in a
:class:`PPGRecording`; every later stage works on copies.

Supported inputs
----------------
* CSV/TXT with a header. Time column: ``timestamp_s`` / ``time_s`` / ``time`` /
  ``t`` (seconds) or ``timestamp_ms`` / ``time_ms`` / ``millis``. Value column:
  ``ppg`` / ``pleth`` / ``ir`` / ``value`` / ``raw`` / ``red``.
* The firmware stream format ``sample_index,device_us,ir,red,marker``.
* Headerless files: one column (values, ``fs_hz`` required) or two columns
  (seconds, value).
* Lines starting with ``#`` are comments.
"""

from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from .errors import DataFormatError

TIME_COLUMNS_S = ("timestamp_s", "time_s", "time", "t", "timestamp", "time [s]")
TIME_COLUMNS_MS = ("timestamp_ms", "time_ms", "millis", "ms")
VALUE_COLUMNS = ("ppg", "pleth", "ir", "value", "raw", "red")
SERIAL_HEADER = "sample_index,device_us,ir,red,marker"
_MICROS_WRAP = 2**32
START_TIMEOUT_S = 15.0  # give up if the board sends no valid sample for this long


@dataclass
class PPGRecording:
    """One raw PPG recording.

    Attributes:
        time_s: Sample times in seconds, starting at 0 for the first sample.
        raw: Raw sensor values, untouched (NaN marks a missing value).
        fs_hz: Sampling frequency estimated from the time base.
        markers_s: Times of breath-marker events recorded with the signal
            (empty if none). They are ground truth input, never used by the
            estimator.
        meta: Free-form metadata (sensor, participant, condition, ...).
    """

    time_s: np.ndarray
    raw: np.ndarray
    fs_hz: float
    markers_s: np.ndarray = field(default_factory=lambda: np.empty(0))
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0]) if self.time_s.size > 1 else 0.0


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _read_table(path: Path) -> Tuple[pd.DataFrame, bool]:
    """Parse a delimited text file; returns the numeric table and whether it had a header."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DataFormatError(f"cannot read {path}: {exc}") from exc
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise DataFormatError(f"{path} is empty (no data lines)")

    first = lines[0]
    if "," in first:
        sep = ","
    elif ";" in first:
        sep = ";"
    elif "\t" in first:
        sep = "\t"
    else:
        sep = r"\s+"
    tokens = [tok for tok in re.split(sep, first.strip()) if tok != ""]
    has_header = any(not _is_number(tok) for tok in tokens)

    try:
        df = pd.read_csv(
            io.StringIO("\n".join(lines)),
            sep=sep,
            header=0 if has_header else None,
            engine="python",
            on_bad_lines="skip",
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataFormatError(f"cannot parse {path}: {exc}") from exc
    if df.empty:
        raise DataFormatError(f"{path} contains a header but no samples")
    if has_header:
        df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.apply(pd.to_numeric, errors="coerce")
    return df, has_header


def _unwrap_micros(us: np.ndarray) -> np.ndarray:
    """Undo the 32-bit rollover of the microcontroller's ``micros()`` counter."""
    us = us.astype(float)
    wraps = np.concatenate([[0.0], np.cumsum(np.diff(us) < -_MICROS_WRAP / 2)])
    return us + wraps * _MICROS_WRAP


def _first_present(columns: List[str], candidates: Tuple[str, ...]) -> Optional[str]:
    return next((c for c in candidates if c in columns), None)


def load_recording(
    path: Union[str, Path],
    fs_hz: Optional[float] = None,
    channel: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> PPGRecording:
    """Load a PPG recording from CSV/TXT.

    Args:
        path: File to read.
        fs_hz: Declared sampling frequency. Required only when the file has no
            time information; otherwise kept as ``meta['declared_fs_hz']`` and
            cross-checked against the timestamps during preprocessing.
        channel: Value column to use (e.g. ``"ir"`` or ``"red"``). Defaults to
            the first known value column present.
        meta: Extra metadata merged over the optional ``<name>.meta.json`` sidecar.

    Raises:
        FileNotFoundError: The file does not exist.
        DataFormatError: The file is empty or cannot be interpreted.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"recording not found: {path}")
    if fs_hz is not None and not fs_hz > 0:
        raise DataFormatError(f"fs_hz must be positive, got {fs_hz}")

    df, has_header = _read_table(path)
    if not has_header:
        if df.shape[1] == 1:
            df.columns = ["ppg"]
        elif df.shape[1] == 2:
            df.columns = ["timestamp_s", "ppg"]
        else:
            raise DataFormatError(
                f"{path}: headerless file with {df.shape[1]} columns is ambiguous; add a header line"
            )
    columns = list(df.columns)

    value_col = channel.lower() if channel else _first_present(columns, VALUE_COLUMNS)
    if value_col is None or value_col not in columns:
        raise DataFormatError(
            f"{path}: no PPG value column found (looked for {channel or VALUE_COLUMNS}, have {columns})"
        )

    info: Dict[str, Any] = {"source_file": str(path), "value_column": value_col}
    if fs_hz is not None:
        info["declared_fs_hz"] = float(fs_hz)

    if "sample_index" in columns:
        df = df.dropna(subset=["sample_index"])
        index = df["sample_index"].to_numpy(dtype=float)
        rate = None
        if "device_us" in columns and len(df) >= 100:
            us = _unwrap_micros(df["device_us"].ffill().bfill().to_numpy())
            span_s = (us[-1] - us[0]) / 1e6
            if span_s >= 5.0 and index[-1] > index[0]:
                # The read-out time of a FIFO batch is jittery, but the mean
                # rate over the whole record is set by the MCU crystal.
                rate = (index[-1] - index[0]) / span_s
                info["fs_source"] = "device clock (sample_index vs device_us)"
        if rate is None:
            if fs_hz is None:
                raise DataFormatError(
                    f"{path}: sample_index without a usable device_us column needs fs_hz"
                )
            rate = float(fs_hz)
            info["fs_source"] = "declared"
        time_s = (index - index[0]) / rate
    else:
        time_col = _first_present(columns, TIME_COLUMNS_S)
        scale = 1.0
        if time_col is None:
            time_col = _first_present(columns, TIME_COLUMNS_MS)
            scale = 1e-3
        if time_col is not None:
            df = df.dropna(subset=[time_col])
            time_s = df[time_col].to_numpy(dtype=float) * scale
            time_s = time_s - time_s[0] if time_s.size else time_s
            info["fs_source"] = f"timestamps ({time_col})"
        elif fs_hz is not None:
            time_s = np.arange(len(df), dtype=float) / float(fs_hz)
            info["fs_source"] = "declared"
        else:
            raise DataFormatError(f"{path}: no time column; pass fs_hz to define the time base")

    if len(df) < 2:
        raise DataFormatError(f"{path}: fewer than 2 samples")
    steps = np.diff(time_s)
    positive = steps[steps > 0]
    if positive.size == 0:
        raise DataFormatError(f"{path}: timestamps do not increase")
    rate_est = 1.0 / float(np.median(positive))

    markers_s = np.empty(0)
    if "marker" in columns:
        flag = df["marker"].fillna(0).to_numpy() > 0
        rising = flag & ~np.concatenate([[False], flag[:-1]])
        markers_s = time_s[rising]

    sidecar = path.with_suffix(".meta.json")
    if sidecar.is_file():
        try:
            info.update(json.loads(sidecar.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise DataFormatError(f"cannot parse metadata sidecar {sidecar}: {exc}") from exc
    if meta:
        info.update(meta)

    return PPGRecording(
        time_s=time_s,
        raw=df[value_col].to_numpy(dtype=float),
        fs_hz=rate_est,
        markers_s=markers_s,
        meta=info,
    )


def recording_from_arrays(
    values: np.ndarray,
    fs_hz: float,
    time_s: Optional[np.ndarray] = None,
    markers_s: Optional[np.ndarray] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> PPGRecording:
    """Wrap in-memory samples as a :class:`PPGRecording` (used by tests and simulations)."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or values.size < 2:
        raise DataFormatError("values must be a 1-D array with at least 2 samples")
    if not fs_hz > 0:
        raise DataFormatError(f"fs_hz must be positive, got {fs_hz}")
    if time_s is None:
        time_s = np.arange(values.size) / float(fs_hz)
    time_s = np.asarray(time_s, dtype=float)
    if time_s.shape != values.shape:
        raise DataFormatError("time_s and values must have the same length")
    info = {"declared_fs_hz": float(fs_hz)}
    info.update(meta or {})
    return PPGRecording(
        time_s=time_s,
        raw=values,
        fs_hz=float(fs_hz),
        markers_s=np.asarray(markers_s if markers_s is not None else [], dtype=float),
        meta=info,
    )


def parse_serial_line(line: str) -> Optional[Tuple[int, int, int, int, int]]:
    """Parse one firmware line ``sample_index,device_us,ir,red,marker``.

    Returns None for comments, the header, and corrupted or partial lines, so
    a noisy serial link can never inject malformed rows into a recording.
    """
    parts = line.strip().split(",")
    if len(parts) != 5:
        return None
    try:
        index, device_us, ir, red, marker = (int(p) for p in parts)
    except ValueError:
        return None
    if min(index, device_us, ir, red) < 0 or marker not in (0, 1):
        return None
    return index, device_us, ir, red, marker


def record_serial(
    port: str,
    out_path: Union[str, Path],
    duration_s: float,
    baud: int = 115200,
    meta: Optional[Dict[str, Any]] = None,
    progress: Optional[Callable[[int, float], None]] = None,
) -> Path:
    """Record the firmware stream to a CSV file plus a ``.meta.json`` sidecar.

    Lines are written exactly as received (after validation); device comment
    lines (``# ...``, including error reports) are stored in the sidecar.

    Raises:
        RuntimeError: pyserial is missing, the port cannot be opened, or no
            valid sample arrived.
    """
    try:
        import serial  # imported lazily: offline analysis must not need it
    except ImportError as exc:
        raise RuntimeError("pyserial is required for live recording: pip install pyserial") from exc
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    device_notes: List[str] = []
    n_samples = 0
    n_rejected = 0
    try:
        link = serial.Serial(port, baud, timeout=1.0)
    except serial.SerialException as exc:
        raise RuntimeError(f"cannot open serial port {port}: {exc}") from exc

    with link, out_path.open("w", encoding="utf-8") as out:
        out.write(SERIAL_HEADER + "\n")
        link.reset_input_buffer()
        link.readline()  # discard the (probably partial) first line
        # Opening the port may reset the board, and boot messages come first.
        # The recording clock therefore starts at the first valid sample.
        opened = time.monotonic()
        started: Optional[float] = None
        while True:
            now = time.monotonic()
            if started is None and now - opened > START_TIMEOUT_S:
                break
            if started is not None and now - started >= duration_s:
                break
            line = link.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue
            if line.startswith("#"):
                device_notes.append(line)
                continue
            if parse_serial_line(line) is None:
                n_rejected += 1
                continue
            if started is None:
                started = time.monotonic()
            out.write(line + "\n")
            n_samples += 1
            if progress is not None and n_samples % 100 == 0:
                progress(n_samples, time.monotonic() - started)

    if n_samples == 0:
        out_path.unlink(missing_ok=True)  # leave no empty recording behind
        raise RuntimeError(
            f"no valid samples received on {port}; device said: {device_notes[-3:] or 'nothing'}"
        )
    info = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "port": port,
        "baud": baud,
        "requested_duration_s": duration_s,
        "n_samples": n_samples,
        "n_rejected_lines": n_rejected,
        "device_notes": device_notes,
    }
    info.update(meta or {})
    out_path.with_suffix(".meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return out_path
