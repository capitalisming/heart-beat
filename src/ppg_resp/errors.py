"""Exception types shared by all modules."""


class PPGError(Exception):
    """Base class for every error raised deliberately by this package."""


class ConfigError(PPGError, ValueError):
    """A setting is missing, unknown or physically inconsistent."""


class DataFormatError(PPGError, ValueError):
    """An input file or array cannot be interpreted as a PPG recording."""


class InsufficientDataError(PPGError):
    """There is too little usable signal to run a processing step."""
