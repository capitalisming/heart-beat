"""Ground truth: actual breaths from a source that is independent of the PPG.

Three kinds of reference are supported:

* **events**   - a timestamp for every breath (button marker recorded with the
  PPG stream, an observer's annotations, video labelling);
* **waveform** - a trace from an independent breathing sensor (e.g. a
  respiratory belt), converted to breath events here;
* **count**    - only a total: N breaths in T seconds.

The reference must never be derived from the PPG itself; that would make the
comparison circular. Sources whose name says they are PPG-derived are refused.

Counting rules
--------------
``interval`` (default for windows): whole breath cycles between the first and
last event in the window, ``RR = 60 * (n - 1) / (t_last - t_first)``. Partial
cycles at the window edges are excluded by construction.

``count``: ``RR = N / T_minutes`` with N the events inside [t0, t1). This is
the textbook formula, but in a short window it is quantised: one breath more
or less changes the result by ``60 / T`` breaths/min (1.9 for a 32 s window).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
from scipy.signal import find_peaks

from .acquisition import PPGRecording, _read_table
from .errors import DataFormatError
from .preprocessing import zero_phase_filter

EVENT_COLUMNS = ("breath_time_s", "time_s", "timestamp_s", "time", "t")
WAVEFORM_COLUMNS = ("resp", "respiration", "belt", "value", "reference")
_PPG_WORDS = ("ppg", "pleth", "photopleth")


def _check_independent(source: str) -> None:
    if any(word in source.lower() for word in _PPG_WORDS):
        raise ValueError(
            f"reference source {source!r} is derived from the PPG; a circular reference cannot validate the estimator"
        )


@dataclass
class BreathReference:
    """Independent breathing reference for one recording."""

    kind: str  # "events" or "count"
    source: str
    event_times_s: np.ndarray = field(default_factory=lambda: np.empty(0))
    n_breaths: Optional[int] = None
    duration_s: Optional[float] = None

    def rate(
        self,
        t0: Optional[float] = None,
        t1: Optional[float] = None,
        rule: str = "interval",
        max_interval_ratio: Optional[float] = 2.5,
    ) -> Optional[float]:
        """Reference respiratory rate (breaths/min) in [t0, t1), or None if undefined.

        Args:
            rule: ``"interval"`` or ``"count"`` (see module docstring).
            max_interval_ratio: With the interval rule, a window is refused when
                its longest breath interval exceeds this multiple of the median:
                the likeliest cause is a missed marker, which would silently
                bias the reference. None disables the check.
        """
        if rule not in ("interval", "count"):
            raise ValueError("rule must be 'interval' or 'count'")
        if self.kind == "count":
            if t0 is not None or t1 is not None:
                return None  # a total count cannot be split into windows
            return 60.0 * self.n_breaths / self.duration_s

        events = self.event_times_s
        if t0 is not None:
            events = events[events >= t0]
        if t1 is not None:
            events = events[events < t1]
        if rule == "count":
            if t0 is None or t1 is None or t1 <= t0:
                return None
            return 60.0 * events.size / (t1 - t0)
        if events.size < 3:
            return None
        intervals = np.diff(events)
        if max_interval_ratio is not None and intervals.max() > max_interval_ratio * np.median(intervals):
            return None
        return 60.0 * (events.size - 1) / float(events[-1] - events[0])


def reference_from_events(event_times_s: np.ndarray, source: str) -> BreathReference:
    """Build a reference from breath timestamps (seconds on the recording's time axis)."""
    _check_independent(source)
    events = np.sort(np.asarray(event_times_s, dtype=float))
    events = events[np.isfinite(events)]
    if events.size and np.any(np.diff(events) <= 0):
        raise DataFormatError("duplicate breath timestamps in the reference")
    return BreathReference(kind="events", source=source, event_times_s=events)


def reference_from_markers(recording: PPGRecording, source: str = "button marker") -> BreathReference:
    """Use the breath markers recorded alongside the PPG stream (inherently synchronised)."""
    return reference_from_events(recording.markers_s, source)


def reference_from_count(n_breaths: int, duration_s: float, source: str = "manual count") -> BreathReference:
    """Reference from a total count: RR_reference = N_breaths / T_minutes."""
    _check_independent(source)
    if n_breaths < 0 or duration_s <= 0:
        raise ValueError("n_breaths must be >= 0 and duration_s positive")
    return BreathReference(kind="count", source=source, n_breaths=int(n_breaths), duration_s=float(duration_s))


def reference_from_waveform(
    time_s: np.ndarray,
    values: np.ndarray,
    source: str,
    band_hz: Tuple[float, float] = (0.05, 1.0),
    time_offset_s: float = 0.0,
) -> BreathReference:
    """Convert an independent breathing waveform (e.g. a belt) into breath events.

    Breaths are the maxima of the band-passed trace that stand out by 25 % of
    its 5-95 % range and are at least half the shortest admissible period apart.

    Args:
        time_offset_s: Added to the reference times to align them with the PPG
            time axis (measured synchronisation offset; 0 if recorded together).
    """
    _check_independent(source)
    t = np.asarray(time_s, dtype=float)
    x = np.asarray(values, dtype=float)
    if t.shape != x.shape or t.size < 10:
        raise DataFormatError("reference waveform needs matching time/value arrays with at least 10 samples")
    keep = np.isfinite(t) & np.isfinite(x)
    t, x = t[keep], x[keep]
    fs = 1.0 / float(np.median(np.diff(t)))
    high = min(band_hz[1], 0.45 * fs)
    y = zero_phase_filter(x - x.mean(), fs, low_hz=band_hz[0], high_hz=high, order=2)
    spread = float(np.percentile(y, 95) - np.percentile(y, 5))
    if spread <= 0:
        return BreathReference(kind="events", source=source)
    peaks, _ = find_peaks(y, distance=max(1, int(0.5 * fs / high)), prominence=0.25 * spread)
    return reference_from_events(t[peaks] + time_offset_s, source)


def load_reference(
    path: Union[str, Path], kind: str = "events", source: Optional[str] = None, time_offset_s: float = 0.0
) -> BreathReference:
    """Load a reference file.

    * ``kind="events"``: one breath time (s) per line; column ``breath_time_s``
      (or ``time_s`` / ``t``), or a headerless single column.
    * ``kind="waveform"``: columns ``time_s`` (or ``timestamp_s`` / ``t``) and
      ``resp`` (or ``belt`` / ``value``).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"reference file not found: {path}")
    df, has_header = _read_table(path)
    columns = list(df.columns)
    if kind == "events":
        if has_header:
            column = next((c for c in EVENT_COLUMNS if c in columns), None)
            if column is None:
                raise DataFormatError(f"{path}: no breath time column (expected one of {EVENT_COLUMNS})")
        elif df.shape[1] == 1:
            column = columns[0]
        else:
            raise DataFormatError(f"{path}: headerless event file must have exactly one column")
        return reference_from_events(df[column].to_numpy(dtype=float) + time_offset_s, source or "observer events")
    if kind == "waveform":
        time_col = next((c for c in ("time_s", "timestamp_s", "time", "t") if c in columns), None)
        value_col = next((c for c in WAVEFORM_COLUMNS if c in columns), None)
        if time_col is None or value_col is None:
            raise DataFormatError(f"{path}: waveform reference needs a time column and one of {WAVEFORM_COLUMNS}")
        return reference_from_waveform(
            df[time_col].to_numpy(), df[value_col].to_numpy(), source or "respiratory belt", time_offset_s=time_offset_s
        )
    raise ValueError("kind must be 'events' or 'waveform'")
