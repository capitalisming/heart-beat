import json

import numpy as np
import pytest

from ppg_resp.acquisition import load_recording, parse_serial_line, recording_from_arrays
from ppg_resp.errors import DataFormatError


def test_csv_with_header_and_seconds(tmp_path):
    path = tmp_path / "rec.csv"
    path.write_text("timestamp_s,ppg\n0.00,1\n0.01,2\n0.02,3\n0.03,4\n")
    rec = load_recording(path)
    assert rec.fs_hz == pytest.approx(100.0)
    np.testing.assert_allclose(rec.raw, [1, 2, 3, 4])
    np.testing.assert_allclose(rec.time_s, [0, 0.01, 0.02, 0.03])


def test_millisecond_timestamps_and_comments(tmp_path):
    path = tmp_path / "rec.csv"
    path.write_text("# a comment\ntimestamp_ms;ppg\n1000;5\n1020;6\n1040;7\n")
    rec = load_recording(path)
    assert rec.fs_hz == pytest.approx(50.0)
    assert rec.time_s[0] == 0.0


def test_headerless_txt_needs_fs(tmp_path):
    path = tmp_path / "rec.txt"
    path.write_text("1\n2\n3\n4\n")
    with pytest.raises(DataFormatError, match="fs_hz"):
        load_recording(path)
    rec = load_recording(path, fs_hz=25.0)
    assert rec.fs_hz == pytest.approx(25.0)
    assert rec.duration_s == pytest.approx(3 / 25)


def test_headerless_two_columns_whitespace(tmp_path):
    path = tmp_path / "rec.txt"
    path.write_text("0.0 10\n0.5 11\n1.0 12\n")
    rec = load_recording(path)
    assert rec.fs_hz == pytest.approx(2.0)


def test_empty_and_header_only_files(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(DataFormatError, match="empty"):
        load_recording(empty)
    header_only = tmp_path / "header.csv"
    header_only.write_text("timestamp_s,ppg\n")
    with pytest.raises(DataFormatError):
        load_recording(header_only)


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        load_recording("no_such_file.csv")


def test_missing_values_are_kept_as_nan(tmp_path):
    path = tmp_path / "rec.csv"
    path.write_text("timestamp_s,ppg\n0.0,1\n0.1,\n0.2,oops\n0.3,4\n")
    rec = load_recording(path)
    assert np.isnan(rec.raw).sum() == 2
    assert rec.raw.size == 4


def test_firmware_format_uses_device_clock_and_markers(tmp_path):
    true_fs = 98.0  # sensor oscillator deviates from the nominal 100 Hz
    n = 2000
    idx = np.arange(n)
    us = (idx / true_fs * 1e6).astype(np.int64) + 123456
    marker = np.zeros(n, dtype=int)
    marker[[500, 501, 1500]] = 1  # a held button (2 samples) is one event
    lines = ["sample_index,device_us,ir,red,marker"]
    lines += [f"{i},{u},{100000 + i % 7},{90000},{m}" for i, u, m in zip(idx, us, marker)]
    path = tmp_path / "rec.csv"
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(".meta.json").write_text(json.dumps({"participant_id": "P01"}))

    rec = load_recording(path, fs_hz=100.0)
    assert rec.fs_hz == pytest.approx(true_fs, rel=1e-3)
    assert rec.meta["declared_fs_hz"] == 100.0
    assert rec.meta["participant_id"] == "P01"
    assert rec.meta["value_column"] == "ir"
    np.testing.assert_allclose(rec.markers_s, [500 / true_fs, 1500 / true_fs], rtol=1e-3)
    assert load_recording(path, channel="red").raw[0] == 90000


def test_micros_rollover_is_unwrapped(tmp_path):
    n = 1000
    idx = np.arange(n)
    us = (2**32 - 3_000_000 + idx * 10_000) % 2**32  # wraps after 3 s
    lines = ["sample_index,device_us,ir,red,marker"]
    lines += [f"{i},{u},1000,1000,0" for i, u in zip(idx, us)]
    path = tmp_path / "rec.csv"
    path.write_text("\n".join(lines) + "\n")
    assert load_recording(path).fs_hz == pytest.approx(100.0, rel=1e-3)


def test_unknown_value_column(tmp_path):
    path = tmp_path / "rec.csv"
    path.write_text("timestamp_s,foo\n0,1\n1,2\n")
    with pytest.raises(DataFormatError, match="value column"):
        load_recording(path)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("12,3456,100000,90000,0", (12, 3456, 100000, 90000, 0)),
        ("12,3456,100000,90000,1\r\n", (12, 3456, 100000, 90000, 1)),
        ("# ERROR sensor not found", None),
        ("sample_index,device_us,ir,red,marker", None),
        ("12,3456,1000", None),
        ("12,3456,100000,90000,7", None),
        ("12,34x6,100000,90000,0", None),
        ("", None),
    ],
)
def test_parse_serial_line(line, expected):
    assert parse_serial_line(line) == expected


def test_recording_from_arrays_validation():
    with pytest.raises(DataFormatError):
        recording_from_arrays(np.array([1.0]), fs_hz=100.0)
    with pytest.raises(DataFormatError):
        recording_from_arrays(np.zeros(10), fs_hz=0.0)
    rec = recording_from_arrays(np.zeros(10), fs_hz=10.0)
    assert rec.duration_s == pytest.approx(0.9)


class _FakeLink:
    """Stand-in for serial.Serial: boot noise first, then firmware lines."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def reset_input_buffer(self):
        pass

    def readline(self):
        return (self._lines.pop(0) + "\n").encode() if self._lines else b""


def _patch_serial(monkeypatch, lines):
    import sys
    import types

    fake = types.ModuleType("serial")
    fake.SerialException = OSError
    fake.Serial = lambda port, baud, timeout: _FakeLink(lines)
    monkeypatch.setitem(sys.modules, "serial", fake)


def test_record_serial_skips_boot_noise_and_keeps_only_valid_lines(tmp_path, monkeypatch):
    from ppg_resp import acquisition

    lines = ["partial,li", "ESP-ROM:esp32s3-20210327", "# arduino_monitor 1.0",
             "sample_index,device_us,ir,red,marker"]
    lines += [f"{i},{i * 10000},{118000 + i},{95000},{1 if i == 3 else 0}" for i in range(50)]
    lines.insert(20, "12,garbage")
    _patch_serial(monkeypatch, lines)
    clock = iter(x * 0.01 for x in range(10_000))
    monkeypatch.setattr(acquisition.time, "monotonic", lambda: next(clock))

    out = acquisition.record_serial("FAKE", tmp_path / "rec.csv", duration_s=0.5, meta={"participant_id": "P01"})
    rec = load_recording(out, fs_hz=100.0)
    assert 20 <= rec.raw.size <= 50
    assert rec.raw[0] == 118000 and rec.markers_s.size == 1
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["participant_id"] == "P01" and meta["n_rejected_lines"] >= 2
    assert any("arduino_monitor" in note for note in meta["device_notes"])


def test_record_serial_without_data_fails_and_leaves_no_file(tmp_path, monkeypatch):
    from ppg_resp import acquisition

    _patch_serial(monkeypatch, ["# ERROR MAX30102 not found on I2C"] * 5)
    clock = iter(float(x) for x in range(10_000))
    monkeypatch.setattr(acquisition.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="MAX30102 not found"):
        acquisition.record_serial("FAKE", tmp_path / "rec.csv", duration_s=5)
    assert not (tmp_path / "rec.csv").exists()
