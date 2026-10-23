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


@pytest.fixture(autouse=True)
def no_network_probes(monkeypatch):
    """Install plans probe mirrors (install.network); tests see every site answering, without network."""
    from vidroute.core.install import network

    monkeypatch.setattr(network, "check_mirror", lambda url, timeout=0: {"ok": True, "ms": 1, "status": 200})


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """A fake user home for agent-host integration: the real ~/.claude, ~/.agents ... are never touched."""
    from vidroute.core.integrate import hosts

    fake = tmp_path / "user"
    fake.mkdir()
    monkeypatch.setattr(hosts, "user_home", lambda: fake)
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "OPENCLAW_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    return fake
