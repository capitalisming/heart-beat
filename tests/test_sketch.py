"""Static checks of the Arduino sketch against the host-side parser.

These read the sketch as text. They keep the stream format and the sketch in
step; they do not compile it and cannot replace running it on a board.
"""

import re
from pathlib import Path

from ppg_resp.acquisition import SERIAL_HEADER, parse_serial_line

SKETCH = (Path(__file__).resolve().parents[1] / "hardware" / "firmware" / "arduino_monitor"
          / "arduino_monitor.ino").read_text(encoding="utf-8")


def test_csv_header_matches_the_host_parser():
    assert f'Serial.println(F("{SERIAL_HEADER}"))' in SKETCH


def test_csv_fields_are_printed_in_the_documented_order():
    block = SKETCH.split("#if SERIAL_MODE == 0")[2].split("#elif")[0]   # per-sample block of handleSample()
    printed = [m.group(1) for line in block.splitlines() if (m := re.search(r"Serial\.print(?:ln)?\((.*)\);", line))]
    fields = [arg for arg in printed if arg != "','"]
    assert fields == ["sampleIndex", "(uint32_t)micros()", "ir", "red", "marker"]


def test_sample_line_in_that_format_parses():
    assert parse_serial_line("3334,176979269,52822,58361,1") == (3334, 176979269, 52822, 58361, 1)


def test_comment_lines_are_ignored_by_the_recorder():
    for line in ("# HR bpm=72 finger=1 pulse_height=310", "# WARN fifo_overflow lost=3 total_lost=3"):
        assert parse_serial_line(line) is None


def test_serial_mode_default_is_the_recordable_one():
    assert re.search(r"^#define SERIAL_MODE 0\b", SKETCH, re.M)


def test_sketch_folder_and_file_names_match():
    path = Path(__file__).resolve().parents[1] / "hardware" / "firmware" / "arduino_monitor"
    assert (path / "arduino_monitor.ino").is_file() and (path / "BeatDetector.h").is_file()
