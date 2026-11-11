import numpy as np
import pandas as pd
import pytest

from ppg_resp.config import settings_from_dict
from ppg_resp.errors import DataFormatError
from ppg_resp.reporting import load_manifest, run_study, write_evaluation_outputs
from ppg_resp.synthetic import synthetic_ppg

SETTINGS = settings_from_dict({})


def _write_recording(path, rr, seed):
    sim = synthetic_ppg(duration_s=80, rr_bpm=rr, seed=seed)
    marker = np.zeros(sim.values.size, dtype=int)
    marker[np.round(sim.breath_times_s * sim.fs_hz).astype(int)] = 1
    pd.DataFrame({"timestamp_s": sim.time_s, "ppg": sim.values, "marker": marker}).to_csv(path, index=False)
    return sim


def test_study_from_manifest_with_every_reference_type(tmp_path):
    a = _write_recording(tmp_path / "a.csv", 12.0, 1)
    _write_recording(tmp_path / "b.csv", 18.0, 2)
    _write_recording(tmp_path / "c.csv", 15.0, 3)
    pd.DataFrame({"breath_time_s": np.arange(0, 80, 60 / 18.0)}).to_csv(tmp_path / "b_events.csv", index=False)
    (tmp_path / "manifest.csv").write_text(
        "recording_id,participant_id,session_id,condition,ppg_file,reference_type,reference_file,n_breaths,count_duration_s\n"
        "A,P1,S1,slow,a.csv,markers,,,\n"
        "B,P1,S1,fast,b.csv,events,b_events.csv,,\n"
        "C,P2,S1,rest,c.csv,count,,20,80\n"
        "D,P2,S1,rest,missing.csv,markers,,,\n"
    )
    pairs, log = run_study(tmp_path / "manifest.csv", SETTINGS)
    assert a.rr_bpm == 12.0
    assert set(pairs["recording_id"]) == {"A", "B", "C"}
    assert log.set_index("recording_id").loc["D", "status"] == "ERROR"  # logged, not invented

    report = write_evaluation_outputs(pairs, tmp_path / "out", "Test study", preamble="> test", log=log, n_boot=100)
    table = report.recording_table.set_index("recording_id")
    assert table.loc["A", "rr_ref"] == pytest.approx(12.0, abs=0.1)
    assert table.loc["B", "rr_ref"] == pytest.approx(18.0, abs=0.1)
    assert table.loc["C", "rr_ref"] == pytest.approx(15.0)
    assert report.recording_metrics["mae"] < 0.5
    for name in ("report.md", "tables/window_pairs.csv", "tables/recordings.csv", "tables/metrics.json",
                 "tables/processing_log.csv", "figures/10_bland_altman.png"):
        assert (tmp_path / "out" / name).is_file()
    text = (tmp_path / "out" / "report.md").read_text()
    assert text.startswith("# Test study\n\n> test")


def test_manifest_validation(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("recording_id,ppg_file\nA,a.csv\n")
    with pytest.raises(DataFormatError, match="lacks column"):
        load_manifest(bad)
    dup = tmp_path / "dup.csv"
    dup.write_text("recording_id,participant_id,ppg_file,reference_type\nA,P,a.csv,markers\nA,P,b.csv,markers\n")
    with pytest.raises(DataFormatError, match="duplicate"):
        load_manifest(dup)
