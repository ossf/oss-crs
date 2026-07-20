# SPDX-License-Identifier: MIT
from __future__ import annotations

import inspect
import time
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.config.crs_compose import RunEnv
from oss_crs.src.crs_compose import CRSCompose


def test_run_signature_preserves_positional_build_id_and_sanitizer() -> None:
    params = list(inspect.signature(CRSCompose.run).parameters)

    assert params[:5] == ["self", "target", "run_id", "build_id", "sanitizer"]
    assert params.index("resume_run_id") > params.index("incremental_build")


def test_run_dispatches_to_azure_submitter(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeSubmitter:
        def __init__(self, config, llm):
            captured["config"] = config
            captured["llm"] = llm

        def submit_and_wait(
            self,
            crs_list,
            target,
            work_dir,
            run_id,
            build_id,
            sanitizer,
            timeout_seconds,
            pov_files,
            diff_path,
            seed_dir,
            bug_candidate,
            resume_run_id=None,
        ):
            captured["call"] = {
                "crs_list": crs_list,
                "target": target,
                "work_dir": work_dir,
                "run_id": run_id,
                "build_id": build_id,
                "sanitizer": sanitizer,
                "timeout_seconds": timeout_seconds,
                "pov_files": pov_files,
                "diff_path": diff_path,
                "seed_dir": seed_dir,
                "bug_candidate": bug_candidate,
                "resume_run_id": resume_run_id,
            }
            return 0

    monkeypatch.setattr(
        "oss_crs.src.crs_compose.AzureSpotVmConfig.from_env",
        lambda: "cfg",
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.AzureSpotVmRunSubmitter",
        FakeSubmitter,
    )

    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_compose_env = SimpleNamespace(run_env=RunEnv.AZURE)
    compose.llm = SimpleNamespace()
    compose.crs_list = ["crs-libfuzzer"]
    compose.work_dir = tmp_path / "work"
    compose.deadline = None

    target = SimpleNamespace()

    result = compose._CRSCompose__run(
        target,
        run_id="run-1",
        resume_run_id="run-0",
        build_id="build-1",
        sanitizer="address",
        pov_files=[tmp_path / "pov.bin"],
        diff_path=tmp_path / "ref.diff",
        seed_dir=tmp_path / "seeds",
        bug_candidate=tmp_path / "candidate.txt",
    )

    assert result == 0
    assert captured["config"] == "cfg"
    assert captured["call"]["run_id"] == "run-1"
    assert captured["call"]["resume_run_id"] == "run-0"
    assert captured["call"]["timeout_seconds"] is None


def test_expired_azure_deadline_returns_before_provisioning(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.AzureSpotVmConfig.from_env",
        lambda: (_ for _ in ()).throw(AssertionError("must not provision")),
    )
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_compose_env = SimpleNamespace(run_env=RunEnv.AZURE)
    compose.llm = SimpleNamespace()
    compose.crs_list = ["crs-libfuzzer"]
    compose.work_dir = tmp_path / "work"
    compose.deadline = time.monotonic() - 1

    result = compose._CRSCompose__run(
        SimpleNamespace(),
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert result == 124
