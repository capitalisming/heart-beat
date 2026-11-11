import numpy as np
import pandas as pd

from ppg_resp.acquisition import recording_from_arrays
from ppg_resp.ground_truth import reference_from_events
from ppg_resp.pipeline import run_pipeline
from ppg_resp.synthetic import synthetic_ppg
from ppg_resp.visualization import save_evaluation_figures, save_recording_figures


def test_all_recording_figures_are_written(tmp_path):
    sim = synthetic_ppg(duration_s=90, rr_bpm=14)
    result = run_pipeline(recording_from_arrays(sim.values, sim.fs_hz))
    reference = reference_from_events(sim.breath_times_s, "synthetic truth")
    paths = save_recording_figures(result, tmp_path, reference)
    assert len(paths) == 8
    assert all(p.stat().st_size > 5000 for p in paths)


def test_figures_degrade_gracefully_for_unusable_recordings(tmp_path):
    flat = run_pipeline(recording_from_arrays(np.full(3000, 1.0), 100.0))
    names = [p.name for p in save_recording_figures(flat, tmp_path / "flat")]
    assert names == ["01_raw_ppg.png", "02_filtered_ppg.png"]
    short = run_pipeline(recording_from_arrays(synthetic_ppg(duration_s=3).values, 100.0))
    assert [p.name for p in save_recording_figures(short, tmp_path / "short")] == ["01_raw_ppg.png"]


def test_evaluation_figures(tmp_path):
    table = pd.DataFrame(
        {"rr_ref": [10.0, 14.0, 18.0, 22.0], "rr_est": [10.5, 13.6, 18.9, 21.0], "condition": ["a", "a", "b", "b"]}
    )
    out = save_evaluation_figures(table, tmp_path)
    assert set(out) == {"08_estimate_vs_reference", "09_error_distribution", "10_bland_altman"}
    assert all(p.stat().st_size > 5000 for p in out.values())
    empty = save_evaluation_figures(table.iloc[:0], tmp_path / "empty")
    assert all(p.exists() for p in empty.values())
