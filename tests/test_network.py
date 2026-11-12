"""Choosing package / Python / model mirrors for an install (install.network)."""

import pytest

from vidroute.core import config
from vidroute.core.install import envs, network
from vidroute.core.install.manifest import load_manifest
from vidroute.core.install.models import order_sources


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in network.INDEX_ENV + network.PYTHON_ENV:
        monkeypatch.delenv(var, raising=False)


def prober(down=()):
    seen = []

    def probe(url, timeout):
        seen.append(url)
        return {"ok": not any(d in url for d in down)}

    probe.seen = seen
    return probe


def test_everything_reachable_keeps_the_official_sources():
    net = network.resolve("auto", probe=prober())
    assert (net["pypi_index"], net["python_mirror"], net["model_mirror"], net["region"]) == (None, None, "auto", None)
    assert network.uv_env(net) == {} and network.pip_args(net) == [] and network.describe(net) is None


def test_mainland_china_network_uses_mirrors_for_everything():
    net = network.resolve("auto", probe=prober(down=["huggingface.co"]))
    assert net["region"] == "cn"
    assert net["pypi_index"] == network.PYPI_MIRRORS[0] and net["python_mirror"] == network.PYTHON_MIRRORS[0]
    assert net["model_mirror"] == "cn"
    assert network.uv_env(net) == {"UV_DEFAULT_INDEX": network.PYPI_MIRRORS[0],
                                   "UV_PYTHON_INSTALL_MIRROR": network.PYTHON_MIRRORS[0]}
    assert network.pip_args(net) == ["--index-url", network.PYPI_MIRRORS[0]]
    assert "中国大陆" in network.describe(net)


def test_second_mirror_when_the_first_is_down():
    net = network.resolve("auto", probe=prober(down=["huggingface.co", "aliyun"]))
    assert net["pypi_index"] == network.PYPI_MIRRORS[1]


def test_only_pypi_unreachable():
    net = network.resolve("auto", probe=prober(down=["pypi.org"]))
    assert net["region"] is None and net["pypi_index"] == network.PYPI_MIRRORS[0] and net["python_mirror"] is None


def test_forced_cn_does_not_probe():
    probe = prober()
    net = network.resolve("cn", probe=probe)
    assert probe.seen == [] and net["pypi_index"] == network.PYPI_MIRRORS[0] and net["model_mirror"] == "cn"


def test_the_users_settings_win(monkeypatch):
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://pypi.example.org/simple/")
    config.set_value("python_mirror", "official")
    net = network.resolve("auto", probe=prober(down=["huggingface.co"]))
    assert net["pypi_index"] == "https://pypi.example.org/simple/" and net["python_mirror"] is None
    assert network.uv_env(net) == {}  # the user's variable is inherited as is
    assert any("$UV_DEFAULT_INDEX" in r for r in net["reasons"])


def test_offline_or_nothing_to_fetch_means_no_probing():
    probe = prober()
    network.resolve("auto", need_packages=False, need_python=False, need_models=False, probe=probe)
    config.set_value("offline", True)
    network.resolve("auto", probe=probe)
    assert probe.seen == []


def test_explicit_model_mirror_is_kept():
    assert network.resolve("modelscope", probe=prober(down=["huggingface.co"]))["model_mirror"] == "modelscope"


def test_cn_source_order_puts_modelscope_then_hf_mirror_first():
    m = load_manifest()
    ocr = [s.type for s in order_sources(m.model("ocr-ppocrv6-small"), "cn")]
    assert ocr[:2] == ["bundled", "modelscope"]
    sv = order_sources(m.model("asr-sensevoice-small-int8"), "cn")  # no ModelScope source: hf-mirror, then GitHub
    assert "hf-mirror.com" in sv[0].base_url and sv[1].type == "github" and "huggingface.co" in sv[2].base_url


def test_worker_env_gets_the_chosen_mirrors():
    net = network.resolve("cn", probe=prober())
    env = envs.uv_env(net)
    assert env["UV_DEFAULT_INDEX"] == network.PYPI_MIRRORS[0] and env["UV_NO_CONFIG"] == "1"


def test_config_rejects_plain_http_mirrors():
    from vidroute.core.errors import UsageError

    with pytest.raises(UsageError):
        config.set_value("pypi_index", "http://insecure.example/simple")
    config.set_value("mirror", "cn")
    assert config.get("mirror") == "cn"
