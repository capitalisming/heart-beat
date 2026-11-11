from pathlib import Path

import pytest

from ppg_resp.config import Settings, load_settings, settings_from_dict
from ppg_resp.errors import ConfigError

REPO = Path(__file__).resolve().parents[1]


def test_repo_settings_file_loads_and_targets_max30102():
    settings = load_settings(REPO / "config" / "settings.yaml")
    assert settings.sensor.name == "MAX30102"
    assert settings.sensor.invert is True
    assert settings.respiratory.feature == "am"


def test_defaults_are_valid():
    assert Settings().respiratory.band_hz == (0.1, 0.5)


def test_unknown_key_is_rejected():
    with pytest.raises(ConfigError, match="unknown"):
        settings_from_dict({"respiratory": {"bandd_hz": [0.1, 0.5]}})


def test_band_above_nyquist_is_rejected():
    with pytest.raises(ConfigError, match="Nyquist"):
        settings_from_dict({"respiratory": {"band_hz": [0.1, 2.5], "resample_hz": 4.0}})


def test_window_too_short_for_band_is_rejected():
    with pytest.raises(ConfigError, match="too short"):
        settings_from_dict({"respiratory": {"window_s": 10.0}})


def test_missing_file_is_reported():
    with pytest.raises(ConfigError, match="not found"):
        load_settings(REPO / "config" / "does_not_exist.yaml")
