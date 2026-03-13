"""Unit tests for oss_crs.src.config.crs_compose module."""

import pytest
from pydantic import ValidationError
from oss_crs.src.config.crs_compose import (
    CRSSource,
    CRSComposeConfig,
    ResourceConfig,
    CRSEntry,
    LLMConfig,
    remove_keys,
)


class TestCRSSource:
    """Tests for CRSSource - verifies mutual exclusivity of source types."""

    def test_url_source_requires_ref(self):
        """URL-based source must include a ref."""
        # Valid
        source = CRSSource(url="https://github.com/org/repo.git", ref="main")
        assert source.url and source.ref

        # Invalid - missing ref
        with pytest.raises(ValidationError, match="'ref' is required"):
            CRSSource(url="https://github.com/org/repo.git")

    def test_local_path_is_standalone(self):
        """local_path cannot be combined with url/ref."""
        # Valid
        source = CRSSource(local_path="/path/to/crs")
        assert source.local_path

        # Invalid - combined with url
        with pytest.raises(ValidationError, match="cannot be combined"):
            CRSSource(local_path="/path", url="https://github.com/org/repo.git", ref="main")

    def test_must_specify_source(self):
        """Must provide either url or local_path."""
        with pytest.raises(ValidationError, match="Either 'url' or 'local_path'"):
            CRSSource()


class TestResourceConfig:
    """Tests for ResourceConfig validation."""

    def test_valid_resource_config(self):
        """Standard resource configs should work."""
        config = ResourceConfig(cpuset="0-3", memory="16G")
        assert config.cpuset == "0-3"
        assert config.memory == "16G"

    def test_rejects_invalid_cpuset(self):
        """Invalid cpuset format should be rejected."""
        with pytest.raises(ValidationError, match="Invalid cpuset"):
            ResourceConfig(cpuset="invalid", memory="8G")

    def test_rejects_invalid_memory(self):
        """Invalid memory format should be rejected."""
        with pytest.raises(ValidationError, match="Invalid memory"):
            ResourceConfig(cpuset="0", memory="invalid")

    def test_llm_budget_must_be_positive(self):
        """llm_budget must be > 0 if specified."""
        # Valid
        config = ResourceConfig(cpuset="0", memory="8G", llm_budget=100)
        assert config.llm_budget == 100

        # Invalid
        with pytest.raises(ValidationError):
            ResourceConfig(cpuset="0", memory="8G", llm_budget=0)


class TestCRSEntry:
    """Tests for CRSEntry model."""

    def test_additional_env_defaults_to_empty(self):
        """additional_env should default to empty dict, even if None."""
        entry = CRSEntry(cpuset="0-3", memory="8G", additional_env=None)
        assert entry.additional_env == {}

    def test_additional_env_rejects_invalid_key(self):
        with pytest.raises(ValidationError, match="invalid env var key"):
            CRSEntry(
                cpuset="0-3",
                memory="8G",
                additional_env={"BAD-KEY": "value"},
            )


class TestLLMConfig:
    """Tests for LLMConfig mode selection and validation."""

    def test_accepts_internal_mode_with_config_path(self, tmp_path):
        litellm_file = tmp_path / "litellm.yaml"
        litellm_file.write_text("model_list: []\n")
        config = LLMConfig(
            litellm={
                "mode": "internal",
                "internal": {"config_path": str(litellm_file)},
            }
        )
        assert config.litellm.mode.value == "internal"

    def test_accepts_external_mode_with_env_sources(self):
        config = LLMConfig(
            litellm={
                "mode": "external",
                "external": {
                    "url_env": "LITELLM_URL",
                    "key_env": "LITELLM_API_KEY",
                },
            }
        )
        assert config.litellm.mode.value == "external"

    def test_rejects_external_without_oneof_sources(self):
        with pytest.raises(ValidationError, match="exactly one of 'url' or 'url_env'"):
            LLMConfig(
                litellm={
                    "mode": "external",
                    "external": {
                        "key_env": "LITELLM_API_KEY",
                    },
                }
            )

    def test_backward_compatible_old_litellm_config_shape(self, tmp_path):
        litellm_file = tmp_path / "litellm.yaml"
        litellm_file.write_text("model_list: []\n")
        data = {
            "run_env": "local",
            "docker_registry": "local",
            "oss_crs_infra": {"cpuset": "0-1", "memory": "8G"},
            "llm_config": {"litellm_config": str(litellm_file)},
            "dummy-crs": {
                "cpuset": "2-3",
                "memory": "8G",
                "source": {"local_path": "/tmp/dummy-crs"},
            },
        }
        config = CRSComposeConfig.from_dict(data)
        assert config.llm_config is not None
        assert config.llm_config.litellm.mode.value == "internal"

        with pytest.raises(ValidationError, match="exactly one of 'key' or 'key_env'"):
            LLMConfig(
                litellm={
                    "mode": "external",
                    "external": {
                        "url": "https://litellm.example.com",
                    },
                }
            )


class TestRemoveKeys:
    """Tests for remove_keys - verifies recursive key removal."""

    def test_removes_keys_recursively(self):
        """Should remove specified keys at all nesting levels."""
        data = {
            "keep": 1,
            "remove": 2,
            "nested": {
                "keep": 3,
                "remove": 4,
                "deeper": {"remove": 5},
            },
            "list": [{"keep": 6, "remove": 7}],
        }

        result = remove_keys(data, ["remove"])

        assert result == {
            "keep": 1,
            "nested": {
                "keep": 3,
                "deeper": {},
            },
            "list": [{"keep": 6}],
        }

    def test_preserves_structure_with_no_matching_keys(self):
        """Structure should be unchanged if no keys match."""
        data = {"a": {"b": {"c": 1}}}
        result = remove_keys(data, ["x", "y"])
        assert result == data
