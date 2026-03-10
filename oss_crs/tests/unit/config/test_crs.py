"""Unit tests for oss_crs.src.config.crs module."""

import pytest
from pydantic import ValidationError
from oss_crs.src.config.crs import (
    CRSConfig,
    PreparePhase,
    BuildConfig,
    CRSRunPhaseModule,
    _validate_dockerfile_value,
)


class TestDockerfileValidation:
    """Tests for dockerfile path validation."""

    def test_accepts_framework_module_reference(self):
        """oss-crs-infra: prefix identifies framework-provided modules."""
        result = _validate_dockerfile_value("oss-crs-infra:default-builder")
        assert result == "oss-crs-infra:default-builder"

    def test_accepts_dockerfile_paths(self):
        """Standard Dockerfile paths should be accepted."""
        assert _validate_dockerfile_value("Dockerfile") == "Dockerfile"
        assert _validate_dockerfile_value("build.Dockerfile") == "build.Dockerfile"

    def test_rejects_non_dockerfile_paths(self):
        """Non-Dockerfile files should be rejected."""
        with pytest.raises(ValueError, match="valid Dockerfile"):
            _validate_dockerfile_value("config.yaml")


class TestPreparePhase:
    """Tests for PreparePhase - HCL file configuration."""

    def test_requires_hcl_extension(self):
        """HCL file must have .hcl extension."""
        phase = PreparePhase(hcl="build.hcl")
        assert phase.hcl == "build.hcl"

        with pytest.raises(ValidationError, match=".hcl extension"):
            PreparePhase(hcl="build.yaml")


class TestBuildConfig:
    """Tests for BuildConfig - build step configuration."""

    def test_outputs_cannot_escape_directory(self):
        """Output paths cannot contain '..' to prevent directory traversal."""
        with pytest.raises(ValidationError, match="cannot contain"):
            BuildConfig(name="build", dockerfile="Dockerfile", outputs=["../escape"])

    def test_snapshot_flag(self):
        """snapshot flag controls whether build creates a snapshot image."""
        normal = BuildConfig(name="build", dockerfile="Dockerfile")
        assert normal.snapshot is False

        snapshot = BuildConfig(name="build", dockerfile="Dockerfile", snapshot=True)
        assert snapshot.snapshot is True

    def test_rejects_invalid_additional_env_key(self):
        with pytest.raises(ValidationError, match="invalid env var key"):
            BuildConfig(
                name="build",
                dockerfile="Dockerfile",
                additional_env={"BAD-KEY": "value"},
            )


class TestCRSRunPhaseModule:
    """Tests for CRSRunPhaseModule - run phase service configuration."""

    def test_snapshot_module_can_omit_dockerfile(self):
        """Snapshot-based modules don't need their own Dockerfile."""
        module = CRSRunPhaseModule(run_snapshot=True)
        assert module.dockerfile is None

    def test_non_snapshot_requires_dockerfile(self):
        """Non-snapshot modules must specify a Dockerfile."""
        with pytest.raises(ValidationError, match="dockerfile is required"):
            CRSRunPhaseModule(run_snapshot=False)

    def test_rejects_invalid_additional_env_key(self):
        with pytest.raises(ValidationError, match="invalid env var key"):
            CRSRunPhaseModule(
                run_snapshot=True,
                additional_env={"BAD-KEY": "value"},
            )


def _minimal_crs_config(**overrides) -> dict:
    """Return minimal valid CRSConfig dict, with optional overrides."""
    base = {
        "name": "test-crs",
        "type": ["bug-finding"],
        "version": "1.0.0",
        "crs_run_phase": {
            "main": {"dockerfile": "Dockerfile"},
        },
        "supported_target": {
            "mode": ["full"],
            "language": ["c"],
            "sanitizer": ["address"],
            "architecture": ["x86_64"],
        },
    }
    base.update(overrides)
    return base


class TestRequiredInputs:
    """Tests for required_inputs field validation."""

    def test_none_by_default(self):
        """required_inputs defaults to None when not specified."""
        config = CRSConfig.from_dict(_minimal_crs_config())
        assert config.required_inputs is None

    def test_accepts_valid_input_names(self):
        """All valid input names are accepted."""
        config = CRSConfig.from_dict(
            _minimal_crs_config(required_inputs=["diff", "pov", "seed", "bug-candidate"])
        )
        assert set(config.required_inputs) == {"diff", "pov", "seed", "bug-candidate"}

    def test_removes_duplicates(self):
        """Duplicate input names are removed."""
        config = CRSConfig.from_dict(
            _minimal_crs_config(required_inputs=["diff", "diff", "pov"])
        )
        assert len(config.required_inputs) == 2
        assert set(config.required_inputs) == {"diff", "pov"}

    def test_rejects_unknown_input_names(self):
        """Unknown input names raise a validation error."""
        with pytest.raises(ValidationError, match="Unknown input names"):
            CRSConfig.from_dict(
                _minimal_crs_config(required_inputs=["diff", "unknown-input"])
            )

    def test_empty_list_accepted(self):
        """required_inputs=[] is valid and distinct from None."""
        config = CRSConfig.from_dict(_minimal_crs_config(required_inputs=[]))
        assert config.required_inputs == []

    def test_single_item_accepted(self):
        """A single-element list is accepted."""
        config = CRSConfig.from_dict(_minimal_crs_config(required_inputs=["pov"]))
        assert config.required_inputs == ["pov"]


class TestCRSTypeProperties:
    """Tests for CRSConfig type-based properties."""

    def test_bug_finding_is_not_bug_fixing(self):
        config = CRSConfig.from_dict(_minimal_crs_config(type=["bug-finding"]))
        assert config.is_bug_fixing is False
        assert config.is_ensemble is False

    def test_bug_fixing(self):
        config = CRSConfig.from_dict(_minimal_crs_config(type=["bug-fixing"]))
        assert config.is_bug_fixing is True
        assert config.is_ensemble is False

    def test_bug_fixing_ensemble_implies_bug_fixing(self):
        """bug-fixing-ensemble alone is sufficient — no need to also list bug-fixing."""
        config = CRSConfig.from_dict(_minimal_crs_config(type=["bug-fixing-ensemble"]))
        assert config.is_bug_fixing is True
        assert config.is_ensemble is True


class TestSidecarDeploymentConditions:
    """Tests for exchange/lifecycle sidecar deployment logic.

    These conditions are computed in renderer.py and passed to the run template.
    We test the logic directly here to avoid heavy template rendering setup.
    """

    @staticmethod
    def _configs(*type_lists) -> list[CRSConfig]:
        return [
            CRSConfig.from_dict(_minimal_crs_config(name=f"crs-{i}", type=types))
            for i, types in enumerate(type_lists)
        ]

    @staticmethod
    def _compute_conditions(configs):
        from oss_crs.src.config.crs import CRSType

        bug_finding_count = sum(
            1 for c in configs
            if CRSType.BUG_FINDING in c.type and not c.is_builder
        )
        bug_finding_ensemble = bug_finding_count > 1
        bug_fix_ensemble = any(c.is_ensemble for c in configs)
        needs_exchange = bug_finding_ensemble or bug_fix_ensemble
        return bug_finding_ensemble, bug_fix_ensemble, needs_exchange

    def test_single_bug_fixing_no_sidecars(self):
        """Single bug-fixing CRS needs no exchange or lifecycle."""
        configs = self._configs(["bug-fixing"])
        bf_ens, bfix_ens, exchange = self._compute_conditions(configs)
        assert bf_ens is False
        assert bfix_ens is False
        assert exchange is False

    def test_multiple_bug_finding_gets_exchange_only(self):
        """Multiple bug-finding CRSes need exchange but not lifecycle."""
        configs = self._configs(["bug-finding"], ["bug-finding"])
        bf_ens, bfix_ens, exchange = self._compute_conditions(configs)
        assert bf_ens is True
        assert bfix_ens is False
        assert exchange is True

    def test_bug_fix_ensemble_gets_exchange_and_lifecycle(self):
        """Bug-fix ensemble scenario needs both exchange and lifecycle."""
        configs = self._configs(
            ["bug-fixing"], ["bug-fixing"], ["bug-fixing-ensemble"],
        )
        bf_ens, bfix_ens, exchange = self._compute_conditions(configs)
        assert bf_ens is False
        assert bfix_ens is True
        assert exchange is True
