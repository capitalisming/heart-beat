"""Heart-monitor screen logic (display.py) against a fake 128x64 display.

The fake records pixels, so the tests can check where things are drawn. They
cannot show how it looks on the real OLED or whether the I2C traffic works.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

DIR = Path(__file__).resolve().parents[1] / "hardware" / "firmware" / "micropython"
spec = importlib.util.spec_from_file_location("display_py", DIR / "display.py")
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class FakeOled:
    """Records what a framebuf-style display would show."""

    def __init__(self):
        self.px = np.zeros((64, 128), dtype=bool)
        self.texts = []
        self.shows = 0

    def fill(self, c):
        self.px[:] = bool(c)
        self.texts = []

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x and x + w <= 128 and 0 <= y and y + h <= 64, (x, y, w, h)
        self.px[y : y + h, x : x + w] = bool(c)

    def line(self, x0, y0, x1, y1, c):
        assert 0 <= x0 < 128 and 0 <= x1 < 128 and 0 <= y0 < 64 and 0 <= y1 < 64, (x0, y0, x1, y1)
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for t in range(n + 1):
            self.px[round(y0 + (y1 - y0) * t / n), round(x0 + (x1 - x0) * t / n)] = bool(c)

    def text(self, s, x, y, c=1):
        self.texts.append(s)

    def show(self):
        self.shows += 1


def sweep(monitor, seconds, hz=1.2):
    for k in range(int(seconds * 100)):
        monitor.push(300.0 * np.sin(2 * np.pi * hz * k / 100))


def test_bpm_digits_and_status_are_drawn_in_the_top_band():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 3)
    monitor.draw(72.0, True, 300.0, False)
    assert oled.shows == 1
    assert oled.px[2:22, 24:70].any()                 # big digits
    assert "BPM" in oled.texts and "OK" in oled.texts


def test_each_digit_has_its_own_pattern():
    seen = set()
    for digit in "0123456789":
        oled = FakeOled()
        monitor = display.Monitor(oled)
        monitor._big_text(digit, 0, 0, 4)
        seen.add(oled.px[:20, :12].tobytes())
    assert len(seen) == 10


def test_heart_is_bigger_while_flashing():
    quiet, flash = FakeOled(), FakeOled()
    display.Monitor(quiet).draw(72.0, True, 300.0, False)
    display.Monitor(flash).draw(72.0, True, 300.0, True)
    assert flash.px[3:21, 0:21].sum() > quiet.px[3:21, 0:21].sum()


def test_no_finger_shows_prompt_and_dashes_without_waveform():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 3)
    monitor.draw(72.0, False, 300.0, True)
    assert "Put finger" in oled.texts and "---" in oled.texts
    assert not oled.px[display.WAVE_TOP :, :].any()   # nothing but text below the divider


def test_weak_pulse_is_labelled():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 3)
    monitor.draw(0.0, True, 10.0, False)
    assert "WEAK" in oled.texts


def test_waveform_stays_in_its_area_and_fills_it():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 5)
    monitor.draw(72.0, True, 300.0, False)
    area = oled.px[display.WAVE_TOP :, :]
    assert area.any()
    rows = np.flatnonzero(area.any(axis=1))
    assert rows.max() - rows.min() > 20               # a 600-count swing uses most of the height


def test_sweep_leaves_a_gap_ahead_of_the_write_position():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 5)                                  # more than one screen: all columns valid
    start = monitor.x
    monitor.draw(72.0, True, 300.0, False)
    for k in range(display.GAP):
        assert not oled.px[display.WAVE_TOP :, (start + k) % 128].any()
    assert oled.px[display.WAVE_TOP :, (start + display.GAP + 5) % 128].any()


def test_small_noise_is_not_blown_up():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    for k in range(600):
        monitor.push(3.0 * np.sin(k))                  # +/-3 counts, far below MIN_RANGE
    monitor.draw(0.0, True, 3.0, False)
    rows = np.flatnonzero(oled.px[display.WAVE_TOP :, :].any(axis=1))
    assert rows.max() - rows.min() < 10


def test_clear_wave_removes_the_old_trace():
    oled = FakeOled()
    monitor = display.Monitor(oled)
    sweep(monitor, 3)
    monitor.clear_wave()
    monitor.draw(0.0, True, 0.0, False)
    assert not oled.px[display.WAVE_TOP :, :].any()


def test_three_digit_rate_fits_before_the_label():
    oled = FakeOled()
    display.Monitor(oled).draw(188.0, True, 300.0, False)
    assert oled.px[2:22, 24:68].any() and not oled.px[2:22, 68:80].any()


def test_message_screen():
    oled = FakeOled()
    display.Monitor(oled).message("MAX30102 not found", "check the wiring")
    assert oled.texts == ["MAX30102 not found", "check the wiring"] and oled.shows == 1
