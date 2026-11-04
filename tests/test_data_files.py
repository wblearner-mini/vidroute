"""Bundled data files: parse identically with PyYAML, satisfy their schemas, stay honest."""

import json
import re
from pathlib import Path

import pytest

from vidroute.core.util.yamlish import load_file

yaml = pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[1]
YAML_FILES = sorted(p for d in ("routing", "models") for p in (ROOT / d).glob("*.yaml"))
SCHEMAS = {
    "llm-profiles.yaml": "llm-profiles.schema.json",
    "host-profiles.yaml": "host-profiles.schema.json",
    "manifest.yaml": "manifest.schema.json",
}


def _schema(name):
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_yamlish_matches_pyyaml(path):
    ours = load_file(path)
    theirs = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert ours == theirs


@pytest.mark.parametrize("name,schema", sorted(SCHEMAS.items()))
def test_schema_valid(name, schema):
    path = next(p for p in YAML_FILES if p.name == name)
    jsonschema.validate(load_file(path), _schema(schema))


def test_all_schemas_are_valid_json_schema():
    for path in (ROOT / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_llm_profile_ids_unique_and_fallback_last():
    profiles = load_file(ROOT / "routing" / "llm-profiles.yaml")["profiles"]
    ids = [p["id"] for p in profiles]
    assert len(ids) == len(set(ids))
    assert ids[-1] == "*"


def test_host_ids_unique_and_unknown_present():
    hosts = load_file(ROOT / "routing" / "host-profiles.yaml")["hosts"]
    ids = [h["id"] for h in hosts]
    names = ids + [a for h in hosts for a in h.get("aliases", [])]
    assert len(names) == len(set(names))
    assert "unknown" in ids


def test_manifest_integrity():
    manifest = load_file(ROOT / "models" / "manifest.yaml")
    ids = [m["id"] for m in manifest["models"]]
    assert len(ids) == len(set(ids))
    groups = set(manifest["env_groups"])
    for m in manifest["models"]:
        if m["package"] == "builtin":
            assert not m["files"] and not m["sources"]
            continue
        assert m["env_group"] in groups, m["id"]
        assert m["sources"], m["id"]
        # Default-path entries must have per-file hashes and be unrestricted.
        if m["status"] == "default":
            assert m["files"], m["id"]
            assert not m["license"]["restricted"] and not m["license"]["gated"], m["id"]
        for src in m["sources"]:
            if src["type"] == "bundled":
                assert src["env_group"] == m["env_group"], m["id"]
                continue
            url = src.get("url") or src.get("base_url")
            host = re.match(r"https://([^/]+)/", url).group(1)
            assert any(host == d or host.endswith("." + d) for d in manifest["allowed_domains"]), url
            if src["kind"] == "files":
                assert m["files"], f"{m['id']}: per-file sources need a files list"
                for name in src.get("layout", {}):
                    assert name in {f["path"] for f in m["files"]}, name


def test_every_capability_has_a_cpu_default_path():
    manifest = load_file(ROOT / "models" / "manifest.yaml")
    for cap in ("asr", "vad", "ocr", "scene", "diarize"):
        ok = [
            m for m in manifest["models"]
            if m["capability"] == cap and m["status"] == "default" and "cpu-lite" in m["tiers"] and not m.get("requires_gpu")
        ]
        assert ok, f"no CPU default path for {cap}"


def test_rules_reference_known_models():
    manifest = load_file(ROOT / "models" / "manifest.yaml")
    known = {m["id"] for m in manifest["models"]}
    rules = load_file(ROOT / "routing" / "rules.yaml")
    local = rules["local"]
    referenced = {mid for chains in local["asr_chains"].values() for chain in chains.values() for mid in chain}
    referenced |= set(local["ocr_chain"]) | set(local["vad_chain"]) | set(local["scene_chain"])
    assert referenced <= known, referenced - known


def test_github_workflows_are_valid_yaml():
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict) and "jobs" in doc, path.name
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses and not uses.startswith("./"):
                    # Third-party actions must be pinned to a full commit SHA.
                    assert re.match(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", uses), f"{path.name}: unpinned {uses}"
