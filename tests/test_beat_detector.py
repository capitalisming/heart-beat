"""On-device heart-beat detector: the MicroPython version and its C++ twin.

Both run on the same simulated signals. The C++ header is compiled with the
host compiler (tests are skipped if none is installed). Simulated signals
verify the code, not accuracy on people.
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from ppg_resp.synthetic import synthetic_ppg

FIRMWARE = Path(__file__).resolve().parents[1] / "hardware" / "firmware"
spec = importlib.util.spec_from_file_location("beat_py", FIRMWARE / "micropython" / "beat.py")
beat_py = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beat_py)


def counts(hr, seconds=60, scale=1.0, noise=0.02, seed=0, **kw):
    """MAX30102-like raw counts: large level, inverted small pulse."""
    sim = synthetic_ppg(duration_s=seconds, hr_bpm=hr, am_depth=0.15, noise_sd=noise, seed=seed,
                        sensor_like=True, **kw)
    return 120000 + (sim.values - 120000) * scale


def run_python(values):
    detector = beat_py.BeatDetector(100, True)
    beats = [i for i, x in enumerate(values) if detector.update(x)]
    return beats, detector.bpm


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if compiler is None:
        pytest.skip("no C++ compiler available")
    exe = tmp_path_factory.mktemp("cpp") / "beat_harness"
    subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", f"-I{FIRMWARE / 'arduino_monitor'}",
         str(Path(__file__).parent / "beat_harness.cpp"), "-o", str(exe)],
        check=True, capture_output=True, text=True,
    )
    return exe


def run_cpp(exe, values):
    text = "\n".join(f"{x:.4f}" for x in values) + "\n"
    out = subprocess.run([str(exe)], input=text, capture_output=True, text=True, check=True).stdout.split("\n")
    beats = [int(line.split()[0]) for line in out if line and not line.startswith("end")]
    final = float(next(line for line in out if line.startswith("end")).split()[1])
    return beats, final


@pytest.mark.parametrize("hr", [45, 60, 72, 90, 120, 150, 180])
def test_python_detector_recovers_heart_rate(hr):
    beats, bpm = run_python(counts(hr))
    assert bpm == pytest.approx(hr, abs=1.5)
    expected = hr * 58 / 60  # beats after the 2 s warm-up
    assert abs(len(beats) - expected) <= 3


@pytest.mark.parametrize("hr", [60, 75, 100, 140])
def test_cpp_matches_python(harness, hr):
    values = counts(hr, seed=hr)
    py_beats, py_bpm = run_python(values)
    cpp_beats, cpp_bpm = run_cpp(harness, values)
    assert abs(len(py_beats) - len(cpp_beats)) <= 1
    assert cpp_bpm == pytest.approx(py_bpm, abs=1.0)
    assert cpp_bpm == pytest.approx(hr, abs=1.5)
    common = min(len(py_beats), len(cpp_beats))
    assert np.max(np.abs(np.array(py_beats[:common]) - np.array(cpp_beats[:common]))) <= 1


def test_works_for_weak_and_for_strong_signals():
    for scale in (0.1, 1.0, 5.0):
        _, bpm = run_python(counts(80, scale=scale))
        assert bpm == pytest.approx(80, abs=2.0), scale


def test_no_pulse_means_no_heart_rate():
    rng = np.random.default_rng(0)
    quiet = 52800 + rng.normal(0, 3.0, 6000)          # the flat readings seen without a pulse
    beats, bpm = run_python(quiet)
    assert bpm == 0 and len(beats) < 5
    assert run_python(np.full(6000, 52800.0)) == ([], 0.0)


def test_heart_rate_is_dropped_when_the_pulse_disappears():
    values = np.concatenate([counts(70, seconds=30), np.full(800, counts(70, seconds=30)[-1])])
    detector = beat_py.BeatDetector(100, True)
    for x in values[:3000]:
        detector.update(x)
    assert detector.bpm == pytest.approx(70, abs=1.5)
    for x in values[3000:]:
        detector.update(x)
    assert detector.bpm == 0


def test_reset_forgets_the_rhythm():
    detector = beat_py.BeatDetector(100, True)
    for x in counts(70, seconds=20):
        detector.update(x)
    assert detector.bpm > 0
    detector.reset()
    assert detector.bpm == 0 and detector.n == 0 and detector.intervals == []


def test_rate_change_is_followed():
    first, second = counts(60, seconds=40), counts(100, seconds=40, seed=3)
    detector = beat_py.BeatDetector(100, True)
    for x in np.concatenate([first, second]):
        detector.update(x)
    assert detector.bpm == pytest.approx(100, abs=2.0)


def test_displayed_waveform_points_up_at_each_beat():
    values = counts(72, seconds=30)
    detector = beat_py.BeatDetector(100, True)
    at_beat = []
    for x in values:
        if detector.update(x):
            at_beat.append(detector.value)
    assert np.median(at_beat) > 0 and min(at_beat[3:]) > 0
