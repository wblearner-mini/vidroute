import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Tests never touch the real ~/.vidroute."""
    home = tmp_path / "vidroute-home"
    monkeypatch.setenv("VIDROUTE_HOME", str(home))
    return home
