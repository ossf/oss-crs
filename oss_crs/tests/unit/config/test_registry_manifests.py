# SPDX-License-Identifier: MIT
"""Unit tests for registry manifests and crs-mantis registration."""

from pathlib import Path

import yaml

from oss_crs.src.config.crs import CRSType
from oss_crs.src.config.crs_compose import (
    CRSComposeConfig,
    CRSSource,
    resolve_source_from_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY_DIR = REPO_ROOT / "registry"
EXAMPLE_MANTIS_DIR = REPO_ROOT / "example" / "crs-mantis"


def test_crs_mantis_manifest_exists():
    """Verify registry/crs-mantis.yaml exists and is valid YAML."""
    manifest_path = REGISTRY_DIR / "crs-mantis.yaml"
    assert manifest_path.is_file(), f"Missing {manifest_path}"

    with open(manifest_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "crs-mantis"
    assert "auditing" in data["type"]
    assert data["source"]["url"] == "https://github.com/Team-Atlanta/crs-mantis.git"
    assert data["source"]["ref"] == "main"


def test_resolve_source_from_registry_mantis():
    """Verify resolve_source_from_registry resolves crs-mantis properly."""
    source = resolve_source_from_registry("crs-mantis")
    assert isinstance(source, CRSSource)
    assert source.url == "https://github.com/Team-Atlanta/crs-mantis.git"
    assert source.ref == "main"
    assert source.local_path is None


def test_crs_mantis_compose_resolution():
    """Verify compose manifest without explicit source resolves crs-mantis from registry."""
    compose_data = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-1", "memory": "8G"},
        "crs-mantis": {
            "cpuset": "2-7",
            "memory": "16G",
            "llm_budget": 15,
            "additional_env": {
                "MANTIS_SKILLS": "threat-model,research,review,report",
            },
        },
    }

    config = CRSComposeConfig.from_dict(compose_data)
    assert "crs-mantis" in config.crs_entries
    entry = config.crs_entries["crs-mantis"]
    assert entry.source is not None
    assert entry.source.url == "https://github.com/Team-Atlanta/crs-mantis.git"
    assert entry.source.ref == "main"
    assert (
        entry.additional_env["MANTIS_SKILLS"] == "threat-model,research,review,report"
    )
    assert entry.llm_budget == 15


def test_example_crs_mantis_compose_parses():
    """Verify example/crs-mantis/compose.yaml parses correctly into CRSComposeConfig."""
    compose_file = EXAMPLE_MANTIS_DIR / "compose.yaml"
    assert compose_file.is_file(), f"Missing {compose_file}"

    with open(compose_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    config = CRSComposeConfig.from_dict(data)
    assert "crs-mantis" in config.crs_entries
    entry = config.crs_entries["crs-mantis"]
    assert entry.source.url == "https://github.com/Team-Atlanta/crs-mantis.git"
    assert entry.additional_env["MANTIS_MODEL"] == "gemini/gemini-2.5-pro"
    assert config.llm_config is not None
    assert config.llm_config.litellm is not None


def test_example_crs_mantis_litellm_config():
    """Verify example/crs-mantis/litellm-config.yaml is well-formed."""
    litellm_file = EXAMPLE_MANTIS_DIR / "litellm-config.yaml"
    assert litellm_file.is_file(), f"Missing {litellm_file}"

    with open(litellm_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "model_list" in data
    model_names = [m["model_name"] for m in data["model_list"]]
    assert "gemini/gemini-2.5-pro" in model_names
    assert "claude-sonnet-4-6" in model_names


def test_all_registry_manifests_conform_to_schema():
    """Iterate through all registry/*.yaml files and ensure schema conformance."""
    yaml_files = list(REGISTRY_DIR.glob("*.yaml"))
    assert len(yaml_files) > 0

    valid_types = {t.value for t in CRSType}

    for yaml_path in yaml_files:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict), f"{yaml_path.name} is not a valid dict"
        assert "name" in data, f"{yaml_path.name} missing 'name'"
        assert isinstance(data["name"], str) and data["name"], (
            f"{yaml_path.name} invalid or empty 'name'"
        )
        assert "source" in data, f"{yaml_path.name} missing 'source'"

        # Verify source
        src = data["source"]
        assert isinstance(src, dict)
        if "url" in src:
            assert "ref" in src, f"{yaml_path.name} url source missing ref"
            assert src["url"].startswith("http"), (
                f"{yaml_path.name} invalid url: {src['url']}"
            )

        # Verify type if present
        if "type" in data:
            assert isinstance(data["type"], list), (
                f"{yaml_path.name} type must be a list"
            )
            for t in data["type"]:
                assert t in valid_types, f"{yaml_path.name} invalid type '{t}'"
