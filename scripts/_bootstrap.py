"""Make ``ppg_resp`` importable when the scripts are run from a source checkout."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
DEFAULT_CONFIG = REPO_ROOT / "config" / "settings.yaml"
