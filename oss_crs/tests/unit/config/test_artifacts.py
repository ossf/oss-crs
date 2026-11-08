# SPDX-License-Identifier: MIT
"""Unit tests for the artifacts output models in oss_crs.src.config.artifacts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oss_crs.src.config.artifacts import (
    ArtifactsOutput,
    CRSArtifacts,
    ExchangeDir,
    MetaArtifactCounts,
    MetaCRSStats,
    MetaLLMStats,
    MetaSidecarStats,
    MetaTotals,
    RunLogs,
    RunMeta,
)
from oss_crs.src.workdir import WorkDir

SAN = "address"
RUN_ID = "run1"
BUILD_ID = "build1"


def _target(harness: str | None = "fuzz_target") -> SimpleNamespace:
    """Minimal stand-in for Target: WorkDir only reads these two members."""
    return SimpleNamespace(
        target_harness=harness,
        get_workdir_target_key=lambda: "tkey",
    )


@pytest.fixture
def work_dir(tmp_path: Path) -> WorkDir:
    return WorkDir(tmp_path / "work")


def _write_meta(work_dir: WorkDir, content: str) -> Path:
    path = work_dir.get_run_meta_file(RUN_ID, SAN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# --------------------------------------------------------------------------
# ExchangeDir
# --------------------------------------------------------------------------


def test_exchange_dir_defaults_are_none():
    assert ExchangeDir().model_dump() == {
        "base": None,
        "pov": None,
        "seed": None,
        "bug_candidate": None,
        "report": None,
        "patch": None,
        "diff": None,
    }


def test_exchange_dir_from_work_dir_builds_all_paths(work_dir):
    target = _target()

    ex = ExchangeDir.from_work_dir(work_dir, target, RUN_ID, SAN)

    base = work_dir.get_exchange_dir(target, RUN_ID, SAN, create=False)
    assert ex.base == str(base)
    assert ex.pov == str(base / "povs")
    assert ex.seed == str(base / "seeds")
    assert ex.bug_candidate == str(base / "bug-candidates")
    assert ex.report == str(base / "reports")
    assert ex.patch == str(base / "patches")
    assert ex.diff == str(base / "diffs")


def test_exchange_dir_from_work_dir_does_not_create_directories(work_dir):
    ex = ExchangeDir.from_work_dir(work_dir, _target(), RUN_ID, SAN)

    assert not Path(ex.base).exists()


def test_exchange_dir_uses_no_harness_scope_without_harness(work_dir):
    ex = ExchangeDir.from_work_dir(work_dir, _target(harness=None), RUN_ID, SAN)

    assert Path(ex.base).name == WorkDir.NO_HARNESS_SCOPE


# --------------------------------------------------------------------------
# RunLogs
# --------------------------------------------------------------------------


def test_run_logs_from_work_dir_builds_paths(work_dir):
    target = _target()

    logs = RunLogs.from_work_dir(work_dir, target, RUN_ID, SAN)

    base = work_dir.get_run_logs_dir(target, RUN_ID, SAN, create=False)
    assert logs.base == str(base)
    assert logs.compose_stdout_log == str(base / "docker-compose.stdout.log")
    assert logs.compose_stderr_log == str(base / "docker-compose.stderr.log")
    assert logs.service_logs == str(base / "services")


def test_run_logs_from_work_dir_does_not_create_directories(work_dir):
    logs = RunLogs.from_work_dir(work_dir, _target(), RUN_ID, SAN)

    assert not Path(logs.base).exists()


# --------------------------------------------------------------------------
# Meta* models
# --------------------------------------------------------------------------


def test_meta_models_have_zero_defaults():
    assert MetaArtifactCounts().model_dump() == {
        "povs": 0,
        "seeds": 0,
        "patches": 0,
        "bug_candidates": 0,
        "reports": 0,
    }
    assert MetaLLMStats().model_dump() == {
        "credits_used": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    assert MetaSidecarStats().model_dump() == {
        "patch_builds": 0,
        "patch_tests": 0,
        "pov_runs": 0,
    }


def test_meta_composite_models_do_not_share_default_instances():
    a, b = MetaCRSStats(), MetaCRSStats()
    a.artifacts.povs = 5

    assert b.artifacts.povs == 0
    assert MetaTotals().artifacts is not MetaTotals().artifacts


def test_meta_models_reject_wrong_types():
    with pytest.raises(ValidationError):
        MetaArtifactCounts(povs="many")


# --------------------------------------------------------------------------
# RunMeta.from_work_dir
# --------------------------------------------------------------------------


def test_run_meta_missing_file_returns_defaults_with_path(work_dir):
    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.path == str(work_dir.get_run_meta_file(RUN_ID, SAN))
    assert meta.totals == MetaTotals()
    assert meta.crs == {}


def test_run_meta_parses_totals_and_crs(work_dir):
    _write_meta(
        work_dir,
        json.dumps(
            {
                "totals": {
                    "artifacts": {"povs": 3, "seeds": 7},
                    "llm": {"credits_used": 1.5, "prompt_tokens": 10},
                    "sidecar": {"pov_runs": 2},
                },
                "crs": {
                    "crs-a": {"artifacts": {"patches": 1}},
                    "crs-b": {"llm": {"completion_tokens": 4}},
                },
            }
        ),
    )

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.totals.artifacts.povs == 3
    assert meta.totals.artifacts.seeds == 7
    assert meta.totals.artifacts.patches == 0
    assert meta.totals.llm.credits_used == 1.5
    assert meta.totals.llm.prompt_tokens == 10
    assert meta.totals.sidecar.pov_runs == 2
    assert set(meta.crs) == {"crs-a", "crs-b"}
    assert meta.crs["crs-a"].artifacts.patches == 1
    assert meta.crs["crs-b"].llm.completion_tokens == 4


@pytest.mark.parametrize("content", ["{not json", "", "\x00\x01"])
def test_run_meta_invalid_json_returns_defaults(work_dir, content):
    _write_meta(work_dir, content)

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.totals == MetaTotals()
    assert meta.crs == {}


@pytest.mark.parametrize("content", ["[]", "[1, 2]", '"text"', "42", "null"])
def test_run_meta_non_object_json_returns_defaults(work_dir, content):
    _write_meta(work_dir, content)

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.totals == MetaTotals()
    assert meta.crs == {}


def test_run_meta_null_totals_and_missing_crs(work_dir):
    _write_meta(work_dir, json.dumps({"totals": None}))

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.totals == MetaTotals()
    assert meta.crs == {}


def test_run_meta_non_dict_crs_is_ignored(work_dir):
    _write_meta(work_dir, json.dumps({"crs": ["crs-a"]}))

    assert RunMeta.from_work_dir(work_dir, RUN_ID, SAN).crs == {}


def test_run_meta_null_crs_entry_uses_defaults(work_dir):
    _write_meta(work_dir, json.dumps({"crs": {"crs-a": None}}))

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.crs == {"crs-a": MetaCRSStats()}


def test_run_meta_ignores_unknown_keys(work_dir):
    _write_meta(
        work_dir,
        json.dumps({"unknown": 1, "totals": {"artifacts": {"povs": 1, "x": 2}}}),
    )

    assert RunMeta.from_work_dir(work_dir, RUN_ID, SAN).totals.artifacts.povs == 1


def test_run_meta_unreadable_file_returns_defaults(work_dir):
    # A directory at the meta path exists() but cannot be read as text (OSError).
    work_dir.get_run_meta_file(RUN_ID, SAN).mkdir(parents=True)

    meta = RunMeta.from_work_dir(work_dir, RUN_ID, SAN)

    assert meta.totals == MetaTotals()
    assert meta.crs == {}


def test_run_meta_wrong_value_type_raises_validation_error(work_dir):
    # Invalid shapes inside valid JSON are not swallowed: only parse/IO errors are.
    _write_meta(work_dir, json.dumps({"totals": {"artifacts": {"povs": "x"}}}))

    with pytest.raises(ValidationError):
        RunMeta.from_work_dir(work_dir, RUN_ID, SAN)


# --------------------------------------------------------------------------
# CRSArtifacts.from_work_dir
# --------------------------------------------------------------------------


def test_crs_artifacts_defaults_are_none():
    assert all(v is None for v in CRSArtifacts().model_dump().values())


def test_crs_artifacts_from_work_dir_builds_paths(work_dir):
    target = _target()

    art = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", target, BUILD_ID, RUN_ID, SAN, exchange_dir_base="/ex"
    )

    submit = work_dir.get_submit_dir("crs-a", target, RUN_ID, SAN, create=False)
    assert art.build == str(
        work_dir.get_build_output_dir("crs-a", target, BUILD_ID, SAN, create=False)
    )
    assert art.submit_dir == str(submit)
    assert art.pov == str(submit / "povs")
    assert art.seed == str(submit / "seeds")
    assert art.bug_candidate == str(submit / "bug-candidates")
    assert art.report == str(submit / "reports")
    assert art.patch == str(submit / "patches")
    assert art.harness == str(submit / "harnesses")
    assert art.fetch == "/ex"
    assert art.shared == str(
        work_dir.get_shared_dir("crs-a", target, RUN_ID, SAN, create=False)
    )
    assert art.log_dir == str(
        work_dir.get_log_dir("crs-a", target, RUN_ID, SAN, create=False)
    )
    run_logs = work_dir.get_run_logs_dir(target, RUN_ID, SAN, create=False)
    assert art.run_logs == str(run_logs / "crs" / "crs-a")


@pytest.mark.parametrize("build_id", [None, ""])
def test_crs_artifacts_without_build_id_has_no_build(work_dir, build_id):
    art = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", _target(), build_id, RUN_ID, SAN
    )

    assert art.build is None


def test_crs_artifacts_fetch_defaults_to_none(work_dir):
    art = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", _target(), BUILD_ID, RUN_ID, SAN
    )

    assert art.fetch is None


def test_crs_artifacts_sidecar_metrics_only_when_file_exists(work_dir):
    target = _target()
    metrics = work_dir.get_sidecar_metrics_file("crs-a", target, RUN_ID, SAN)

    before = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", target, BUILD_ID, RUN_ID, SAN
    )
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text("{}\n")
    after = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", target, BUILD_ID, RUN_ID, SAN
    )

    assert before.sidecar_metrics is None
    assert after.sidecar_metrics == str(metrics)


def test_crs_artifacts_from_work_dir_does_not_create_directories(work_dir):
    art = CRSArtifacts.from_work_dir(
        work_dir, "crs-a", _target(), BUILD_ID, RUN_ID, SAN
    )

    for p in (art.build, art.submit_dir, art.shared, art.log_dir):
        assert not Path(p).exists()


def test_crs_artifacts_are_isolated_per_crs(work_dir):
    target = _target()

    a = CRSArtifacts.from_work_dir(work_dir, "crs-a", target, BUILD_ID, RUN_ID, SAN)
    b = CRSArtifacts.from_work_dir(work_dir, "crs-b", target, BUILD_ID, RUN_ID, SAN)

    assert a.submit_dir != b.submit_dir
    assert a.run_logs != b.run_logs
    assert a.build != b.build


# --------------------------------------------------------------------------
# ArtifactsOutput
# --------------------------------------------------------------------------


def test_artifacts_output_requires_run_id():
    with pytest.raises(ValidationError):
        ArtifactsOutput()


def test_artifacts_output_minimal_to_json_excludes_none():
    out = ArtifactsOutput(run_id=RUN_ID)

    assert json.loads(out.to_json()) == {"run_id": RUN_ID, "crs": {}}


def test_artifacts_output_to_json_default_indent_is_two():
    text = ArtifactsOutput(run_id=RUN_ID).to_json()

    assert text.splitlines()[1].startswith("  ")
    assert not text.splitlines()[1].startswith("   ")


def test_artifacts_output_to_json_custom_indent():
    text = ArtifactsOutput(run_id=RUN_ID).to_json(indent=4)

    assert text.splitlines()[1].startswith("    ")


def test_artifacts_output_to_json_full_roundtrip(work_dir):
    target = _target()
    out = ArtifactsOutput(
        build_id=BUILD_ID,
        run_id=RUN_ID,
        sanitizer=SAN,
        meta=RunMeta.from_work_dir(work_dir, RUN_ID, SAN),
        exchange_dir=ExchangeDir.from_work_dir(work_dir, target, RUN_ID, SAN),
        run_logs=RunLogs.from_work_dir(work_dir, target, RUN_ID, SAN),
        crs={
            "crs-a": CRSArtifacts.from_work_dir(
                work_dir, "crs-a", target, BUILD_ID, RUN_ID, SAN
            )
        },
    )

    data = json.loads(out.to_json())

    assert data["build_id"] == BUILD_ID
    assert data["run_id"] == RUN_ID
    assert data["sanitizer"] == SAN
    assert set(data) == {
        "build_id",
        "run_id",
        "sanitizer",
        "meta",
        "exchange_dir",
        "run_logs",
        "crs",
    }
    # None-valued optional fields (fetch, sidecar_metrics) are dropped.
    assert "fetch" not in data["crs"]["crs-a"]
    assert "sidecar_metrics" not in data["crs"]["crs-a"]
    assert data["crs"]["crs-a"]["submit_dir"].endswith(
        str(Path("SUBMIT_DIR") / "fuzz_target")
    )
    # Defaults (zeros) are not None, so they are kept.
    assert data["meta"]["totals"]["artifacts"]["povs"] == 0
    # The output validates back into the same model.
    assert ArtifactsOutput.model_validate(data) == ArtifactsOutput.model_validate_json(
        out.to_json()
    )
