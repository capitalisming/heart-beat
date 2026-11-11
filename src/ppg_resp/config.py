"""Central, validated configuration.

All tunable numbers live here (and in ``config/settings.yaml``) so that no
module carries hidden constants tuned to a single recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import yaml

from .errors import ConfigError

FEATURES = ("am", "am_detrended", "bw", "fm")
METHODS = ("fft", "welch", "autocorr", "peak_count")
INTERPOLATIONS = ("linear", "pchip")


@dataclass(frozen=True)
class SensorConfig:
    """Sensor-specific facts; swapping the sensor means changing only this block."""

    name: str = "generic"
    invert: bool = False
    valid_min: Optional[float] = None
    valid_max: Optional[float] = None


@dataclass(frozen=True)
class PreprocessingConfig:
    lowpass_hz: float = 8.0
    pulse_band_hz: Tuple[float, float] = (0.5, 4.0)
    filter_order: int = 3
    min_fs_hz: float = 20.0
    max_interp_gap_s: float = 0.3
    timestamp_jitter_tol: float = 0.05
    fs_mismatch_tol: float = 0.05
    hampel_window: int = 7
    hampel_k: float = 6.0
    artifact_window_s: float = 2.0
    artifact_high_ratio: float = 3.0
    artifact_low_ratio: float = 0.2


@dataclass(frozen=True)
class PulseConfig:
    min_hr_bpm: float = 35.0
    max_hr_bpm: float = 200.0
    envelope_window_s: float = 4.0
    min_prominence: float = 1.0
    refine_window_s: float = 0.10
    ibi_tolerance: float = 0.30
    amp_ratio_low: float = 0.33
    amp_ratio_high: float = 3.0
    local_beats: int = 11


@dataclass(frozen=True)
class RespiratoryConfig:
    feature: str = "am"
    resample_hz: float = 4.0
    interpolation: str = "linear"
    max_beat_gap_s: float = 3.0
    band_hz: Tuple[float, float] = (0.1, 0.5)
    filter_order: int = 2
    window_s: float = 32.0
    step_s: float = 8.0
    primary_method: str = "fft"


@dataclass(frozen=True)
class QualityConfig:
    min_beats: int = 16
    min_coverage: float = 0.8
    min_regularity: float = 0.7
    min_cycles: float = 3.0
    min_band_width_hz: float = 0.05
    sqi_threshold: float = 70.0
    agreement_tol_bpm: float = 3.0


@dataclass(frozen=True)
class Settings:
    sensor: SensorConfig = field(default_factory=SensorConfig)
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    pulse: PulseConfig = field(default_factory=PulseConfig)
    respiratory: RespiratoryConfig = field(default_factory=RespiratoryConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject physically inconsistent settings before any data is touched."""
        pre, pulse, resp, qual = self.preprocessing, self.pulse, self.respiratory, self.quality

        _check_band("preprocessing.pulse_band_hz", pre.pulse_band_hz)
        _check_band("respiratory.band_hz", resp.band_hz)
        if resp.band_hz[1] >= resp.resample_hz / 2:
            raise ConfigError(
                f"respiratory.band_hz upper edge {resp.band_hz[1]} Hz must be below the "
                f"Nyquist frequency of resample_hz ({resp.resample_hz / 2} Hz)"
            )
        if pre.lowpass_hz <= pre.pulse_band_hz[0]:
            raise ConfigError("preprocessing.lowpass_hz must exceed the pulse band lower edge")
        if not 0 < pulse.min_hr_bpm < pulse.max_hr_bpm:
            raise ConfigError("pulse.min_hr_bpm must be positive and below max_hr_bpm")
        if pre.hampel_window < 3 or pre.hampel_window % 2 == 0:
            raise ConfigError("preprocessing.hampel_window must be an odd integer >= 3")
        if pre.filter_order < 1 or resp.filter_order < 1:
            raise ConfigError("filter orders must be >= 1")
        if resp.feature not in FEATURES:
            raise ConfigError(f"respiratory.feature must be one of {FEATURES}")
        if resp.primary_method not in METHODS:
            raise ConfigError(f"respiratory.primary_method must be one of {METHODS}")
        if resp.interpolation not in INTERPOLATIONS:
            raise ConfigError(f"respiratory.interpolation must be one of {INTERPOLATIONS}")
        if resp.step_s <= 0 or resp.window_s <= 0:
            raise ConfigError("respiratory.window_s and step_s must be positive")
        min_window = qual.min_cycles / resp.band_hz[0]
        if resp.window_s < min_window:
            raise ConfigError(
                f"respiratory.window_s={resp.window_s} s is too short: {qual.min_cycles} cycles "
                f"of the slowest admissible breathing ({resp.band_hz[0]} Hz) need {min_window:.1f} s"
            )
        if not 0 <= qual.sqi_threshold <= 100:
            raise ConfigError("quality.sqi_threshold must lie in [0, 100]")


def _check_band(name: str, band: Tuple[float, float]) -> None:
    if len(band) != 2 or not 0 < band[0] < band[1]:
        raise ConfigError(f"{name} must be [low, high] with 0 < low < high, got {band}")


def _build(cls: type, data: Any) -> Any:
    """Build a (nested) settings dataclass from a plain dict, rejecting unknown keys."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"section for {cls.__name__} must be a mapping, got {type(data).__name__}")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown setting(s) for {cls.__name__}: {sorted(unknown)}")
    defaults = cls()
    kwargs = {}
    for key, value in data.items():
        default = getattr(defaults, key)
        if is_dataclass(default):
            kwargs[key] = _build(type(default), value)
        elif isinstance(default, tuple):
            try:
                kwargs[key] = tuple(float(v) for v in value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{key} must be a list of numbers") from exc
        else:
            kwargs[key] = value
    return cls(**kwargs)


def settings_from_dict(data: Optional[dict]) -> Settings:
    """Create :class:`Settings` from a nested dict (missing keys keep their defaults)."""
    return _build(Settings, data)


def load_settings(path: Union[str, Path, None] = None) -> Settings:
    """Load settings from a YAML file, or return generic defaults when ``path`` is None."""
    if path is None:
        return Settings()
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"settings file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    return settings_from_dict(data)
