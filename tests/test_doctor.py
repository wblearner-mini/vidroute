import json
import time

import pytest

from vidroute.core.doctor import gpu, system, tier
from vidroute.core.util import ffmpeg as ff
from vidroute.core.util.proc import run_parallel

# ---------------------------------------------------------------- fixtures

NVIDIA_ONE = "NVIDIA GeForce RTX 3060, 12288, 560.94, 8.6\n"
NVIDIA_TWO = "NVIDIA RTX A4000, 16376, 555.42, 8.6\nNVIDIA GeForce GTX 1650, 4096, 555.42, [N/A]\n"
NVIDIA_OLD = "NVIDIA GeForce GTX 1050 Ti, 4096, 472.12\n"

CIM_SINGLE = json.dumps(
    {"Name": "NVIDIA GeForce RTX 4070", "AdapterRAM": 4293918720, "DriverVersion": "32.0.15.6094",
     "PNPDeviceID": "PCI\\VEN_10DE&DEV_2786&SUBSYS_00000000"}
)
CIM_LIST = json.dumps(
    [
        {"Name": "Intel(R) UHD Graphics 770", "AdapterRAM": 1073741824, "DriverVersion": "31.0.101.4502",
         "PNPDeviceID": "PCI\\VEN_8086&DEV_4680"},
        {"Name": "Microsoft Basic Display Adapter", "AdapterRAM": None, "DriverVersion": "10.0.22621.1",
         "PNPDeviceID": "ROOT\\BASICDISPLAY"},
        {"Name": "AMD Radeon RX 7800 XT", "AdapterRAM": -1, "DriverVersion": "31.0.24027.1012",
         "PNPDeviceID": "PCI\\VEN_1002&DEV_747E"},
    ]
)

CPUINFO_AVX2 = "processor\t: 0\nmodel name\t: AMD Ryzen 7 8745H\nflags\t\t: fpu sse sse2 avx avx2 fma\n"
CPUINFO_NO_AVX2 = "processor\t: 0\nmodel name\t: Intel(R) Celeron(R) N4020\nflags\t\t: fpu sse sse2 sse4_2\n"
CPUINFO_ARM = "processor\t: 0\nFeatures\t: fp asimd evtstrm aes\nCPU implementer\t: 0x41\n"
MEMINFO = "MemTotal:       16303716 kB\nMemFree:         1000000 kB\nMemAvailable:    8000000 kB\n"


# ---------------------------------------------------------------- parsers


def test_parse_nvidia_smi_single():
    (g,) = gpu.parse_nvidia_smi(NVIDIA_ONE, with_compute_cap=True)
    assert g == {"vendor": "nvidia", "name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0,
                 "vram_source": "nvidia-smi", "driver": "560.94", "compute_cap": "8.6"}


def test_parse_nvidia_smi_multi_and_na():
    gpus = gpu.parse_nvidia_smi(NVIDIA_TWO, with_compute_cap=True)
    assert [g["vram_gb"] for g in gpus] == [15.99, 4.0]
    assert gpus[1]["compute_cap"] is None


def test_parse_nvidia_smi_old_driver_without_compute_cap():
    (g,) = gpu.parse_nvidia_smi(NVIDIA_OLD, with_compute_cap=False)
    assert g["compute_cap"] is None and g["vram_gb"] == 4.0


def test_parse_cim_single_object_is_approximate():
    (g,) = gpu.parse_cim_json(CIM_SINGLE)
    assert g["vendor"] == "nvidia"
    assert g["vram_gb"] is None  # AdapterRAM is capped at 4 GiB: never trusted
    assert g["vram_approx_gb"] == 4.0 and g["vram_source"] == "cim-approx"


def test_parse_cim_list_skips_basic_display_and_bad_ram():
    gpus = gpu.parse_cim_json(CIM_LIST)
    assert [g["vendor"] for g in gpus] == ["intel", "amd"]
    assert gpus[1]["vram_approx_gb"] is None and gpus[1]["vram_source"] == "unknown"


def test_parse_cim_empty():
    assert gpu.parse_cim_json("") == []


def test_merge_prefers_nvidia_smi():
    nvidia = {"gpus": gpu.parse_nvidia_smi(NVIDIA_ONE, True)}
    plat = {"gpus": gpu.parse_cim_json(CIM_SINGLE) + gpu.parse_cim_json(CIM_LIST)}
    merged = gpu.merge(nvidia, plat)
    assert [g["vendor"] for g in merged] == ["nvidia", "intel", "amd"]
    assert merged[0]["vram_source"] == "nvidia-smi"


@pytest.mark.parametrize(
    "text,avx2,model",
    [(CPUINFO_AVX2, True, "AMD Ryzen 7 8745H"), (CPUINFO_NO_AVX2, False, "Intel(R) Celeron(R) N4020"),
     (CPUINFO_ARM, False, None)],
)
def test_parse_cpuinfo(text, avx2, model):
    info = system.parse_proc_cpuinfo(text)
    assert info["avx2"] is avx2 and info["model"] == model


def test_parse_meminfo():
    assert system.parse_meminfo(MEMINFO) == {"total_gb": 15.55, "available_gb": 7.63}


def test_ffmpeg_list_parsing():
    enc = " V....D libx264              libx264 H.264\n A....D aac                  AAC\n"
    flt = " TSC select            V->N       Select video frames\n ... silencedetect     A->A       Detect silence.\n"
    assert ff.parse_codec_list(enc, ("libx264", "aac", "h264_nvenc")) == {"libx264": True, "aac": True, "h264_nvenc": False}
    assert ff.parse_codec_list(flt, ("select", "silencedetect", "drawtext")) == {
        "select": True, "silencedetect": True, "drawtext": False}
    assert ff.parse_version("ffmpeg version 8.0.1-3ubuntu2 Copyright (c)") == "8.0.1-3ubuntu2"


# ------------------------------------------------------------------- tiers

X86 = {"x86": True, "avx2": True}


def nv(vram):
    return [{"vendor": "nvidia", "name": "NV", "vram_gb": vram}]


@pytest.mark.parametrize(
    "ram,cpu,gpus,smoke,expected",
    [
        (16, X86, [], None, "cpu-full"),
        (8.0, X86, [], None, "cpu-full"),
        (7.9, X86, [], None, "cpu-lite"),
        (None, X86, [], None, "cpu-lite"),
        (32, {"x86": True, "avx2": False}, [], None, "cpu-lite"),
        (32, {"x86": False, "avx2": None}, [], None, "cpu-full"),  # ARM: AVX2 irrelevant
        (32, {"x86": True, "avx2": None}, [], None, "cpu-full"),  # unknown AVX2 is not treated as missing
        (32, X86, nv(12), None, "cpu-full"),  # GPU alone never upgrades
        (32, X86, nv(12), {"cuda": {"status": "passed"}}, "gpu-cuda"),
        (32, X86, nv(12), {"cuda": {"status": "failed", "reason": "cudnn"}}, "cpu-full"),
        (32, X86, nv(3.9), {"cuda": {"status": "passed"}}, "cpu-full"),  # < 4 GB VRAM
        (32, X86, nv(4.0), {"cuda": {"status": "passed"}}, "gpu-cuda"),
        (32, X86, nv(None), {"cuda": {"status": "passed"}}, "cpu-full"),  # unknown VRAM: conservative
        (6, X86, [], {"vulkan": {"status": "passed"}}, "gpu-vulkan"),
    ],
)
def test_tier_boundaries(ram, cpu, gpus, smoke, expected):
    assert tier.decide(ram, cpu, gpus, smoke)["tier"] == expected


def test_tier_hint_for_unverified_gpu():
    out = tier.decide(32, X86, nv(12), None)
    assert out["gpu_candidate"] == "cuda" and any("asr-faster-whisper" in h for h in out["hints"])


# -------------------------------------------------------------- execution


def test_run_parallel_never_blocks_on_slow_probe():
    start = time.monotonic()
    res = run_parallel({"fast": lambda: 1, "slow": lambda: time.sleep(3) or 2, "boom": lambda: 1 / 0}, timeout=0.5)
    assert time.monotonic() - start < 1.5
    assert res["fast"].ok and res["fast"].value == 1
    assert res["slow"].timed_out and not res["slow"].ok
    assert "ZeroDivisionError" in res["boom"].error


def test_doctor_end_to_end_is_fast_and_cached(monkeypatch):
    from vidroute.core import doctor

    start = time.monotonic()
    data = doctor.run(refresh=True, offline=True)
    assert time.monotonic() - start < 10
    assert data["cached"] is False and data["tier"] in tier.TIERS
    assert set(data["capabilities"]) == {"asr", "vad", "ocr", "scene"}
    assert data["network"]["checked"] is False
    again = doctor.run(refresh=False, offline=True)
    assert again["cached"] is True and again["checked_at"] == data["checked_at"]


def test_doctor_survives_hanging_gpu_probe(monkeypatch):
    from vidroute.core import doctor

    monkeypatch.setattr(doctor, "PROBE_TIMEOUT", 0.5)
    monkeypatch.setattr(gpu, "nvidia_gpus", lambda timeout=0: time.sleep(5))
    start = time.monotonic()
    data = doctor.collect(offline=True)
    assert time.monotonic() - start < 3
    assert "nvidia" in data["probe_errors"]
