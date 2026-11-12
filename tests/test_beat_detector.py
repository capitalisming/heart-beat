"""The on-device heart-beat detector (BeatDetector.h) on simulated signals.

The header used by the Arduino sketch is compiled with the host compiler and
fed MAX30102-like counts (large level, small inverted pulse). Tests are
skipped if no C++ compiler is installed. Simulated signals verify the code,
not accuracy on real fingers.
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from ppg_resp.synthetic import synthetic_ppg

SKETCH = Path(__file__).resolve().parents[1] / "hardware" / "firmware" / "arduino_monitor"


def counts(hr, seconds=60, scale=1.0, noise=0.02, seed=0):
    """MAX30102-like raw counts: large level, inverted small pulse."""
    sim = synthetic_ppg(duration_s=seconds, hr_bpm=hr, am_depth=0.15, noise_sd=noise, seed=seed, sensor_like=True)
    return 120000 + (sim.values - 120000) * scale


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if compiler is None:
        pytest.skip("no C++ compiler available")
    exe = tmp_path_factory.mktemp("cpp") / "beat_harness"
    subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", f"-I{SKETCH}",
         str(Path(__file__).parent / "beat_harness.cpp"), "-o", str(exe)],
        check=True, capture_output=True, text=True,
    )

    def feed(*parts):
        """Run the detector over arrays and/or the string "R" (reset); returns (beats, final bpm)."""
        tokens = []
        for part in parts:
            tokens += [part] if isinstance(part, str) else [f"{x:.4f}" for x in part]
        out = subprocess.run([str(exe)], input="\n".join(tokens) + "\n", capture_output=True, text=True,
                             check=True).stdout.splitlines()
        beats = [tuple(float(v) for v in line.split()) for line in out if not line.startswith("end")]
        final = float(next(line for line in out if line.startswith("end")).split()[1])
        return beats, final

    return feed


@pytest.mark.parametrize("hr", [45, 60, 72, 90, 120, 150, 180])
def test_recovers_heart_rate(run, hr):
    beats, bpm = run(counts(hr))
    assert bpm == pytest.approx(hr, abs=1.5)
    assert abs(len(beats) - hr * 58 / 60) <= 3     # beats after the 2 s warm-up


def test_works_for_weak_and_strong_signals(run):
    for scale in (0.1, 1.0, 5.0):
        assert run(counts(80, scale=scale))[1] == pytest.approx(80, abs=2.0), scale


def test_no_pulse_means_no_heart_rate(run):
    quiet = 52800 + np.random.default_rng(0).normal(0, 3.0, 6000)    # flat readings, no pulse
    beats, bpm = run(quiet)
    assert bpm == 0 and len(beats) < 5
    assert run(np.full(6000, 52800.0)) == ([], 0.0)


def test_heart_rate_is_dropped_when_the_pulse_disappears(run):
    values = counts(70, seconds=30)
    beats, _ = run(values)
    assert beats[-1][1] == pytest.approx(70, abs=1.5)
    assert run(values, np.full(800, values[-1]))[1] == 0


def test_reset_forgets_the_rhythm(run):
    values = counts(70, seconds=20)
    assert run(values)[1] > 0
    beats, bpm = run(values, "R", np.full(300, values[-1]))
    assert bpm == 0 and beats[-1][0] < len(values)


def test_rate_change_is_followed(run):
    assert run(counts(60, seconds=40), counts(100, seconds=40, seed=3))[1] == pytest.approx(100, abs=2.0)


def test_displayed_waveform_points_up_at_each_beat(run):
    beats, _ = run(counts(72, seconds=30))
    values = [b[2] for b in beats]
    assert np.median(values) > 0 and min(values[3:]) > 0
