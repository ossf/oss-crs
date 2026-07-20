# SPDX-License-Identifier: MIT
from __future__ import annotations

import io
import json
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from oss_crs.src.azure_spot_vm import (
    AzureAcrAuth,
    AzureInputCachePlan,
    AzureQueryError,
    AzureRuntimeShape,
    AzureSpotVmConfig,
    AzureSpotVmRunSubmitter,
    AzureVmNames,
)


def _make_config(tmp_path: Path) -> AzureSpotVmConfig:
    pubkey = tmp_path / "id_ed25519.pub"
    pubkey.write_text("ssh-ed25519 AAAA test\n")
    return AzureSpotVmConfig(
        resource_group="rg",
        location="eastus",
        storage_account="storageacct",
        storage_container="runs",
        acr_name="acrname",
        vm_admin_username="azureuser",
        ssh_public_key_path=pubkey,
        vm_sizes=("Standard_D4as_v5",),
        vm_os_disk_size_gb=256,
        vm_image="Ubuntu2204",
        vm_name_prefix="oss-crs-run",
        keep_failed_vm=False,
        sync_interval_seconds=15,
        vm_zones=(),
        spot_max_price="0.50",
        enable_ssh=False,
        input_cache_enabled=True,
        rebuild_cache_enabled=True,
    )


def _make_crs(
    tmp_path: Path,
    name: str = "crs-libfuzzer",
    *,
    modules: dict[str, SimpleNamespace] | None = None,
    is_bug_fixing: bool = False,
    is_bug_fixing_ensemble: bool = False,
    is_triage: bool = False,
    is_seed_filter: bool = False,
) -> SimpleNamespace:
    if modules is None:
        module_config = SimpleNamespace(
            dockerfile="runner.Dockerfile",
            additional_env={"EXTRA_FLAG": "1"},
        )
        modules = {"runner": module_config}
    return SimpleNamespace(
        name=name,
        crs_path=tmp_path / name,
        resource=SimpleNamespace(
            cpuset="4-7",
            memory="16G",
            additional_env={"FROM_CRS": "1"},
        ),
        config=SimpleNamespace(
            version="1.2.3",
            is_bug_fixing=is_bug_fixing,
            is_bug_fixing_ensemble=is_bug_fixing_ensemble,
            is_triage=is_triage,
            is_seed_filter=is_seed_filter,
            crs_run_phase=SimpleNamespace(modules=modules),
        ),
    )


def _make_target(tmp_path: Path, *, has_repo: bool = True) -> SimpleNamespace:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text("language: c\n")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("repo\n")

    return SimpleNamespace(
        engine="libfuzzer",
        target_harness="fuzz_target",
        proj_path=proj,
        repo_path=repo,
        _has_repo=has_repo,
        get_target_env=lambda: {
            "name": "demo-target",
            "language": "c",
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "repo_path": "/src",
            "harness": "fuzz_target",
        },
        get_docker_image_name=lambda: "demo-target:abcdef",
    )


def _make_workdir(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        get_build_output_dir=lambda *_a, **_k: tmp_path / "build_out",
        get_target_source_dir=lambda *_a, **_k: tmp_path / "target_source",
        get_submit_dir=lambda *_a, **_k: tmp_path / "restored_submit",
        get_shared_dir=lambda *_a, **_k: tmp_path / "restored_shared",
        get_log_dir=lambda *_a, **_k: tmp_path / "restored_log",
        get_exchange_dir=lambda *_a, **_k: tmp_path / "restored_exchange",
        get_rebuild_out_dir=lambda *_a, **_k: tmp_path / "restored_rebuild",
        get_run_logs_dir=lambda *_a, **_k: tmp_path / "run_logs",
    )


class TestAzureSpotVmConfig:
    def test_from_env_requires_expected_values(self, monkeypatch: pytest.MonkeyPatch):
        for name in [
            "OSS_CRS_AZURE_RESOURCE_GROUP",
            "OSS_CRS_AZURE_LOCATION",
            "OSS_CRS_AZURE_STORAGE_ACCOUNT",
            "OSS_CRS_AZURE_STORAGE_CONTAINER",
            "OSS_CRS_AZURE_ACR_NAME",
            "OSS_CRS_AZURE_VM_ADMIN_USERNAME",
            "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH",
        ]:
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(RuntimeError, match="Missing required Azure env vars"):
            AzureSpotVmConfig.from_env()

    def test_from_env_applies_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pubkey = tmp_path / "id_ed25519.pub"
        pubkey.write_text("ssh-ed25519 AAAA test\n")
        env = {
            "OSS_CRS_AZURE_RESOURCE_GROUP": "rg",
            "OSS_CRS_AZURE_LOCATION": "eastus",
            "OSS_CRS_AZURE_STORAGE_ACCOUNT": "storageacct",
            "OSS_CRS_AZURE_STORAGE_CONTAINER": "runs",
            "OSS_CRS_AZURE_ACR_NAME": "acrname",
            "OSS_CRS_AZURE_VM_ADMIN_USERNAME": "azureuser",
            "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH": str(pubkey),
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = AzureSpotVmConfig.from_env()

        assert cfg.vm_sizes == ("Standard_D4as_v5",)
        assert cfg.vm_os_disk_size_gb == 256
        assert cfg.vm_image == "Ubuntu2204"
        assert cfg.vm_name_prefix == "oss-crs-run"
        assert cfg.keep_failed_vm is False
        assert cfg.sync_interval_seconds == 15
        assert cfg.vm_zones == ()
        assert cfg.spot_max_price == "0.50"
        assert cfg.enable_ssh is False
        assert cfg.input_cache_enabled is True
        assert cfg.rebuild_cache_enabled is True
        assert cfg.require_prebuilt_images is False
        assert cfg.docker_registry == "acrname.azurecr.io/oss-crs"

    def test_from_env_parses_candidate_sizes_and_zones(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pubkey = tmp_path / "id_ed25519.pub"
        pubkey.write_text("ssh-ed25519 AAAA test\n")
        env = {
            "OSS_CRS_AZURE_RESOURCE_GROUP": "rg",
            "OSS_CRS_AZURE_LOCATION": "eastus",
            "OSS_CRS_AZURE_STORAGE_ACCOUNT": "storageacct",
            "OSS_CRS_AZURE_STORAGE_CONTAINER": "runs",
            "OSS_CRS_AZURE_ACR_NAME": "acrname",
            "OSS_CRS_AZURE_VM_ADMIN_USERNAME": "azureuser",
            "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH": str(pubkey),
            "OSS_CRS_AZURE_VM_SIZE": "Standard_D4as_v5",
            "OSS_CRS_AZURE_VM_SIZE_CANDIDATES": "Standard_D2s_v3, Standard_D4s_v3",
            "OSS_CRS_AZURE_VM_ZONES": "1, 2",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = AzureSpotVmConfig.from_env()

        assert cfg.vm_sizes == ("Standard_D2s_v3", "Standard_D4s_v3")
        assert cfg.vm_zones == ("1", "2")

    def test_from_env_parses_spot_max_price_and_ssh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pubkey = tmp_path / "id_ed25519.pub"
        pubkey.write_text("ssh-ed25519 AAAA test\n")
        env = {
            "OSS_CRS_AZURE_RESOURCE_GROUP": "rg",
            "OSS_CRS_AZURE_LOCATION": "eastus",
            "OSS_CRS_AZURE_STORAGE_ACCOUNT": "storageacct",
            "OSS_CRS_AZURE_STORAGE_CONTAINER": "runs",
            "OSS_CRS_AZURE_ACR_NAME": "acrname",
            "OSS_CRS_AZURE_VM_ADMIN_USERNAME": "azureuser",
            "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH": str(pubkey),
            "OSS_CRS_AZURE_SPOT_MAX_PRICE": "0.25",
            "OSS_CRS_AZURE_ENABLE_SSH": "true",
            "OSS_CRS_AZURE_VM_OS_DISK_SIZE_GB": "128",
            "OSS_CRS_AZURE_INPUT_CACHE_ENABLED": "false",
            "OSS_CRS_AZURE_REBUILD_CACHE_ENABLED": "false",
            "OSS_CRS_AZURE_REQUIRE_PREBUILT_IMAGES": "true",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = AzureSpotVmConfig.from_env()

        assert cfg.spot_max_price == "0.25"
        assert cfg.enable_ssh is True
        assert cfg.vm_os_disk_size_gb == 128
        assert cfg.input_cache_enabled is False
        assert cfg.rebuild_cache_enabled is False
        assert cfg.require_prebuilt_images is True


def test_build_and_push_module_image_can_require_prebuilt_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = AzureSpotVmConfig(
        **{**_make_config(tmp_path).__dict__, "require_prebuilt_images": True}
    )
    submitter = AzureSpotVmRunSubmitter(cfg, llm=None)
    crs = _make_crs(tmp_path)
    crs.crs_path.mkdir(parents=True, exist_ok=True)
    (crs.crs_path / "runner.Dockerfile").write_text("FROM scratch\n")
    target = _make_target(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    docker_build_called = False

    monkeypatch.setattr(
        submitter,
        "_image_exists_in_registry",
        lambda _tag: False,
    )

    def fake_must_run(*_args, **_kwargs):
        nonlocal docker_build_called
        docker_build_called = True

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)

    with pytest.raises(RuntimeError, match="local builds are disabled"):
        submitter._build_and_push_module_image(
            shape=shape,
            target=target,
            module_name="runner",
            module_config=shape.module_config,
        )

    assert docker_build_called is False


def test_shared_infra_bootstrap_creates_only_missing_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    show_rc = {
        ("group", "show"): 1,
        ("acr", "show"): 0,
        ("storage", "account", "show"): 1,
        ("storage", "container", "show"): 0,
    }
    created: list[list[str]] = []

    def fake_run_command(cmd: list[str]):
        key3 = tuple(cmd[1:4])
        key2 = tuple(cmd[1:3])
        return show_rc.get(key3, show_rc.get(key2, 0)), "", ""

    def fake_must_run(cmd: list[str], _error_prefix: str, **kwargs):
        created.append(cmd)
        return ""

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._run_command", fake_run_command)
    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)
    monkeypatch.setattr(
        submitter,
        "_ensure_storage_account_key",
        lambda: "secret",
    )
    monkeypatch.setattr(
        submitter,
        "_ensure_acr_login",
        lambda: AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )

    auth = submitter._ensure_shared_infra_and_login()

    assert auth.login_server == "acrname.azurecr.io"
    assert any(cmd[1:3] == ["group", "create"] for cmd in created)
    assert any(cmd[1:4] == ["storage", "account", "create"] for cmd in created)
    assert not any(cmd[1:3] == ["acr", "create"] for cmd in created)
    assert not any(cmd[1:4] == ["storage", "container", "create"] for cmd in created)


def test_prepare_run_payload_stages_expected_inputs(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    crs.crs_path.mkdir(parents=True, exist_ok=True)
    target = _make_target(tmp_path, has_repo=False)
    workdir = _make_workdir(tmp_path)
    build_out = workdir.get_build_output_dir()
    build_out.mkdir(parents=True, exist_ok=True)
    (build_out / "artifact.txt").write_text("artifact\n")
    target_source = workdir.get_target_source_dir()
    target_source.mkdir(parents=True, exist_ok=True)
    (target_source / "src.c").write_text("int main(void) { return 0; }\n")

    pov = tmp_path / "pov.bin"
    pov.write_bytes(b"boom")
    diff = tmp_path / "ref.diff"
    diff.write_text("--- a\n+++ b\n")
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    (seed_dir / "seed1").write_text("seed")
    bug_candidate_dir = tmp_path / "bug-candidates"
    bug_candidate_dir.mkdir()
    (bug_candidate_dir / "candidate.txt").write_text("candidate")

    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    staging_dir = tmp_path / "payload"

    submitter._prepare_run_payload(
        staging_dir=staging_dir,
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
        pov_files=[pov],
        diff_path=diff,
        seed_dir=seed_dir,
        bug_candidate=bug_candidate_dir,
        timeout_seconds=90,
        runner_images={"runner": "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag"},
        checkpoint_upload_url="https://example/checkpoint",
        final_upload_url="https://example/final",
        status_upload_url="https://example/status",
        acr_auth=AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )

    assert (staging_dir / "build_out" / "crs-libfuzzer" / "artifact.txt").exists()
    assert (staging_dir / "fetch" / "povs" / "pov.bin").exists()
    assert (staging_dir / "fetch" / "diffs" / "ref.diff").exists()
    assert (staging_dir / "fetch" / "seeds" / "seed1").exists()
    assert (staging_dir / "fetch" / "bug-candidates" / "candidate.txt").exists()
    assert (staging_dir / "fuzz_proj" / "project.yaml").exists()
    assert (staging_dir / "target_source" / "src.c").exists()
    metadata = json.loads((staging_dir / "runtime" / "metadata.json").read_text())
    assert metadata["target_name"] == "demo-target"
    assert metadata["target_engine"] == "libfuzzer"
    assert metadata["target_harness"] == "fuzz_target"
    assert metadata["target_architecture"] == "x86_64"
    compose = yaml.safe_load(
        (staging_dir / "runtime" / "docker-compose.yaml").read_text()
    )
    service = compose["services"]["crs-libfuzzer_runner"]
    assert service["image"].endswith(":tag")
    assert service["environment"]["OSS_CRS_RUN_ENV_TYPE"] == "local"
    assert service["cpuset"] == "4-7"
    assert "/opt/oss-crs/payload/fetch:/OSS_CRS_FETCH_DIR:ro" in service["volumes"]
    script = (staging_dir / "runtime" / "run.sh").read_text()
    assert "docker login acrname.azurecr.io" in script
    assert "write_status pulling-image || true" in script
    assert "-f /opt/oss-crs/payload/runtime/docker-compose.yaml pull" in script
    assert 'timeout --foreground "${remaining}s"' in script


def test_prepare_run_payload_uses_external_litellm_secret(tmp_path: Path) -> None:
    llm = SimpleNamespace(
        mode="external",
        exists=lambda: True,
        get_crs_api_url=lambda: "https://litellm.example.test",
        get_crs_api_key=lambda: "secret-key",
    )
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=llm)
    crs = _make_crs(tmp_path, name="atlantis-multilang-wo-concolic")
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    build_out = workdir.get_build_output_dir()
    build_out.mkdir(parents=True, exist_ok=True)
    (build_out / "artifact.txt").write_text("artifact\n")
    staging_dir = tmp_path / "payload"
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    submitter._prepare_run_payload(
        staging_dir=staging_dir,
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
        pov_files=[],
        diff_path=None,
        seed_dir=None,
        bug_candidate=None,
        timeout_seconds=90,
        runner_images={"runner": "acrname.azurecr.io/oss-crs/atlantis/runner:tag"},
        checkpoint_upload_url="https://example/checkpoint",
        final_upload_url="https://example/final",
        status_upload_url="https://example/status",
        acr_auth=AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )

    secret_file = staging_dir / "runtime" / "oss_crs_llm_api_key"
    assert secret_file.read_text() == "secret-key"
    compose = yaml.safe_load(
        (staging_dir / "runtime" / "docker-compose.yaml").read_text()
    )
    service = compose["services"]["atlantis-multilang-wo-concolic_runner"]
    assert service["environment"]["OSS_CRS_LLM_API_URL"] == (
        "https://litellm.example.test"
    )
    assert service["environment"]["OSS_CRS_LLM_API_KEY_FILE"] == (
        "/run/secrets/oss_crs_llm_api_key"
    )
    assert "secret-key" not in json.dumps(service["environment"])
    assert compose["secrets"]["oss_crs_llm_api_key"]["file"] == (
        "/opt/oss-crs/payload/runtime/oss_crs_llm_api_key"
    )
    metadata = json.loads((staging_dir / "runtime" / "metadata.json").read_text())
    assert metadata["llm_mode"] == "external"
    assert metadata["llm_api_url_present"] is True


def test_render_remote_compose_resolves_controller_env_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ${VAR} in additional_env (e.g. CLAUDE_CODE_OAUTH_TOKEN) must be resolved
    # from the controller environment before upload: docker-compose on the remote
    # Spot VM cannot see the launching shell's environment, so an unresolved
    # reference would render blank on the VM and break OAuth auth.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-secret")
    monkeypatch.delenv("A_MISSING_VAR", raising=False)
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path, name="crs-bug-finding-reference")
    crs.resource.additional_env["CLAUDE_CODE_OAUTH_TOKEN"] = (
        "${CLAUDE_CODE_OAUTH_TOKEN}"
    )
    crs.resource.additional_env["WITH_DEFAULT"] = "${A_MISSING_VAR:-fallback}"
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    build_out = workdir.get_build_output_dir()
    build_out.mkdir(parents=True, exist_ok=True)
    (build_out / "artifact.txt").write_text("artifact\n")
    staging_dir = tmp_path / "payload"
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    submitter._prepare_run_payload(
        staging_dir=staging_dir,
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
        pov_files=[],
        diff_path=None,
        seed_dir=None,
        bug_candidate=None,
        timeout_seconds=90,
        runner_images={
            "runner": (
                "acrname.azurecr.io/oss-crs/crs-bug-finding-reference/runner:tag"
            )
        },
        checkpoint_upload_url="https://example/checkpoint",
        final_upload_url="https://example/final",
        status_upload_url="https://example/status",
        acr_auth=AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )

    compose = yaml.safe_load(
        (staging_dir / "runtime" / "docker-compose.yaml").read_text()
    )
    env = compose["services"]["crs-bug-finding-reference_runner"]["environment"]
    # Present controller var is baked in literally (no ${...} left for the VM).
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-secret"
    # The ${VAR:-default} operator is honored when the var is absent.
    assert env["WITH_DEFAULT"] == "fallback"


def test_render_remote_compose_rejects_unresolved_controller_env_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("A_MISSING_VAR", raising=False)
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    crs.resource.additional_env["UNRESOLVED"] = "${A_MISSING_VAR}"
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    with pytest.raises(RuntimeError, match="A_MISSING_VAR"):
        submitter._render_remote_compose(
            shape=shape,
            target=_make_target(tmp_path),
            run_id="run-1",
            sanitizer="address",
            runner_images={"runner": "example.invalid/runner:tag"},
        )


def test_prepare_run_payload_uses_cached_stable_inputs(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    build_out = workdir.get_build_output_dir()
    build_out.mkdir(parents=True, exist_ok=True)
    (build_out / "artifact.txt").write_text("artifact\n")
    staging_dir = tmp_path / "payload"
    input_cache = AzureInputCachePlan(
        cache_key="cache-key",
        build_out_blob="cache/inputs/cache-key/build_out.tgz",
        fuzz_proj_blob="cache/inputs/cache-key/fuzz_proj.tgz",
        target_source_blob="cache/inputs/cache-key/target_source.tgz",
        build_out_url="https://example/build-out",
        fuzz_proj_url="https://example/fuzz-proj",
        target_source_url="https://example/target-source",
    )
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    submitter._prepare_run_payload(
        staging_dir=staging_dir,
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
        pov_files=[],
        diff_path=None,
        seed_dir=None,
        bug_candidate=None,
        timeout_seconds=90,
        runner_images={"runner": "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag"},
        checkpoint_upload_url="https://example/checkpoint",
        final_upload_url="https://example/final",
        status_upload_url="https://example/status",
        acr_auth=AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
        input_cache=input_cache,
        rebuild_cache_url="https://example/rebuild",
    )

    assert not (staging_dir / "build_out" / "crs-libfuzzer" / "artifact.txt").exists()
    assert not (staging_dir / "fuzz_proj" / "project.yaml").exists()
    assert not (staging_dir / "target_source" / "README.md").exists()
    metadata = json.loads((staging_dir / "runtime" / "metadata.json").read_text())
    assert metadata["input_cache_key"] == "cache-key"
    assert metadata["input_cache_blobs"]["build_out"].endswith("build_out.tgz")
    assert metadata["rebuild_cache_url_present"] is True
    script = (staging_dir / "runtime" / "run.sh").read_text()
    assert 'cache_build_out_url="https://example/build-out"' in script
    assert 'cache_fuzz_proj_url="https://example/fuzz-proj"' in script
    assert 'cache_target_source_url="https://example/target-source"' in script
    assert 'rebuild_cache_url="https://example/rebuild"' in script
    assert 'download_cache_archive "${cache_build_out_url}"' in script
    assert 'download_cache_archive "${rebuild_cache_url}"' in script


def test_input_cache_key_changes_when_build_changes(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    build_out = tmp_path / "build_out"
    build_out.mkdir()
    (build_out / "artifact").write_text("v1")
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    key1 = submitter._input_cache_key(
        shape=shape,
        target=target,
        build_id="build-1",
        sanitizer="address",
        runner_image="image:tag",
        build_out_dir=build_out,
        fuzz_proj_dir=target.proj_path,
        target_source_dir=target.repo_path,
    )
    (build_out / "artifact").write_text("v2")
    key2 = submitter._input_cache_key(
        shape=shape,
        target=target,
        build_id="build-1",
        sanitizer="address",
        runner_image="image:tag",
        build_out_dir=build_out,
        fuzz_proj_dir=target.proj_path,
        target_source_dir=target.repo_path,
    )

    assert key1 != key2


def test_context_hash_preserves_symlink_without_reading_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact").write_text("artifact")
    (source / "external-cache").symlink_to("/root/.cache/bazel/private")

    key1 = AzureSpotVmRunSubmitter._context_hash([source])
    (source / "external-cache").unlink()
    (source / "external-cache").symlink_to("/root/.cache/bazel/other")
    key2 = AzureSpotVmRunSubmitter._context_hash([source])

    assert key1 != key2


def test_create_directory_archive_preserves_symlink_without_reading_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact").write_text("artifact")
    (source / "external-cache").symlink_to("/root/.cache/bazel/private")
    archive = tmp_path / "archive.tgz"

    AzureSpotVmRunSubmitter._create_directory_archive(source, archive)

    with tarfile.open(archive, "r:gz") as tar:
        link = tar.getmember("external-cache")
        artifact = tar.extractfile("artifact")
        assert link.issym()
        assert link.linkname == "/root/.cache/bazel/private"
        assert artifact is not None
        assert artifact.read() == b"artifact"


def test_prepare_input_cache_uploads_missing_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    build_out = workdir.get_build_output_dir()
    build_out.mkdir(parents=True, exist_ok=True)
    (build_out / "artifact").write_text("artifact")
    uploaded: list[str] = []
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    monkeypatch.setattr(
        submitter,
        "_ensure_directory_cache_blob",
        lambda _source, blob: uploaded.append(blob),
    )
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda blob, **_kwargs: f"https://example/{blob}",
    )

    plan = submitter._prepare_input_cache(
        shape=shape,
        target=target,
        work_dir=workdir,
        build_id="build-1",
        sanitizer="address",
        runner_image="image:tag",
        sas_expires_at=None,
    )

    assert plan is not None
    assert uploaded == [
        plan.build_out_blob,
        plan.fuzz_proj_blob,
        plan.target_source_blob,
    ]
    assert plan.build_out_url.endswith("/build_out.tgz")


def test_vm_command_and_cleanup_generation(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    submitter = AzureSpotVmRunSubmitter(cfg, llm=None)
    names = AzureVmNames.for_run(cfg.vm_name_prefix, "run-123")
    create_cmd = submitter._build_vm_create_command(
        names,
        vm_size=cfg.vm_sizes[0],
        zone="1",
    )
    cleanup_cmds = submitter._build_cleanup_commands(names)

    assert "--priority" in create_cmd
    assert "Spot" in create_cmd
    assert "--zone" in create_cmd
    assert "1" in create_cmd
    assert "--eviction-policy" in create_cmd
    assert "Delete" in create_cmd
    assert "--max-price" in create_cmd
    assert create_cmd[create_cmd.index("--max-price") + 1] == "0.50"
    assert create_cmd[create_cmd.index("--nsg-rule") + 1] == "NONE"
    assert "--nic-name" not in create_cmd
    assert names.nic_name == f"{names.vm_name}VMNic"
    assert "--nic-delete-option" in create_cmd
    assert "--os-disk-delete-option" in create_cmd
    assert create_cmd[create_cmd.index("--os-disk-size-gb") + 1] == "256"
    assert any(cmd[1:4] == ["network", "nic", "delete"] for cmd in cleanup_cmds)
    assert any(cmd[1:3] == ["disk", "delete"] for cmd in cleanup_cmds)
    assert any(cmd[1:4] == ["network", "vnet", "delete"] for cmd in cleanup_cmds)


def test_create_spot_vm_retries_capacity_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _make_config(tmp_path)
    cfg = AzureSpotVmConfig(
        **{
            **cfg.__dict__,
            "vm_sizes": ("Standard_D2s_v5", "Standard_D2s_v3"),
            "vm_zones": ("1", "2"),
        }
    )
    submitter = AzureSpotVmRunSubmitter(cfg, llm=None)
    names = AzureVmNames.for_run(cfg.vm_name_prefix, "run-123")
    attempted: list[list[str]] = []

    def fake_must_run(cmd: list[str], _error_prefix: str, **_kwargs):
        attempted.append(cmd)
        rendered = " ".join(cmd)
        if "--size Standard_D2s_v5" in rendered:
            raise RuntimeError(
                "Failed to create Azure Spot VM\n"
                "Exception Details:\t(SkuNotAvailable) capacity restrictions"
            )
        if "--zone 1" in rendered:
            raise RuntimeError(
                "Failed to create Azure Spot VM\n"
                "Exception Details:\t(SkuNotAvailable) capacity restrictions"
            )
        return ""

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)
    monkeypatch.setattr(submitter, "_vm_size_vcpus", lambda _size: 2)

    submitter._create_spot_vm(names, required_vcpus=2)

    assert len(attempted) == 4
    assert any("--size" in cmd and "Standard_D2s_v3" in cmd for cmd in attempted)
    assert attempted[-1][-2:] == ["--zone", "2"]


def test_create_spot_vm_skips_size_smaller_than_cpuset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _make_config(tmp_path)
    cfg = AzureSpotVmConfig(
        **{
            **cfg.__dict__,
            "vm_sizes": ("Standard_D2s_v5", "Standard_D8s_v5"),
        }
    )
    submitter = AzureSpotVmRunSubmitter(cfg, llm=None)
    names = AzureVmNames.for_run(cfg.vm_name_prefix, "run-123")
    attempted: list[list[str]] = []
    monkeypatch.setattr(
        submitter,
        "_vm_size_vcpus",
        lambda size: 2 if size == "Standard_D2s_v5" else 8,
    )
    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm._must_run",
        lambda cmd, *_a, **_k: attempted.append(cmd) or "",
    )

    submitter._create_spot_vm(names, required_vcpus=8)

    assert len(attempted) == 1
    assert "Standard_D8s_v5" in attempted[0]


def test_remote_run_script_installs_compose_fallbacks(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    script = submitter._build_remote_run_script(
        shape=shape,
        timeout_seconds=90,
        deadline_epoch_seconds=123456,
        checkpoint_upload_url="https://example/checkpoint",
        final_upload_url="https://example/final",
        status_upload_url="https://example/status",
        acr_auth=AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )

    assert "install_compose_binary()" in script
    assert "curl -sSfL" in script
    assert "/usr/local/lib/docker/cli-plugins/docker-compose" in script
    assert "run_compose()" in script
    assert "write_status bootstrapping || true" in script
    assert "write_status pulling-image || true" in script
    assert "-f /opt/oss-crs/payload/runtime/docker-compose.yaml pull" in script
    assert "deadline_epoch=123456" in script
    assert 'timeout --foreground "${remaining}s" "${compose_cmd[@]}"' in script
    assert script.index("write_status pulling-image") < script.index("run_compose()")
    assert "metadata/scheduledevents?api-version=2020-07-01" in script
    assert '"(Preempt|Terminate|Redeploy|Reboot|Freeze)"' in script
    assert "write_status eviction-notice || true" in script
    assert "cancel_marker=/opt/oss-crs/runtime/cancel-requested" in script
    assert 'write_status "${final_state}" "${compose_rc}" || true' in script
    assert "final_state=cancelled" in script
    assert "compose_rc=130" in script
    assert "flock -x 9" in script
    assert "rsync -a --delete" in script
    assert "/opt/oss-crs/checkpoint-snapshot" in script
    assert 'metadata.json "${snapshot}/metadata.json"' in script
    assert "run-status.json metadata.json" in script
    assert "scheduled_events_pid=$!" in script
    assert "scheduled-events.json" in script
    assert 'kill "${scheduled_events_pid}"' in script
    assert "/opt/oss-crs/payload/fetch/seeds" in script
    assert "/opt/oss-crs/resume/submit/crs-libfuzzer/*/seeds" in script
    assert "create_seed_corpus_archives" in script
    assert 'Path(str(harness_bin) + "_seed_corpus.zip")' in script
    compose_pipeline = "run_compose 2>&1 | tee"
    assert script.index("trap - ERR") < script.index(compose_pipeline)
    assert script.index(compose_pipeline) < script.rindex("trap 'on_error $?' ERR")

    script_path = tmp_path / "run.sh"
    script_path.write_text(script)
    subprocess.run(["bash", "-n", str(script_path)], check=True)


def test_resolve_resume_blob_prefers_final(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    checked: list[str] = []

    def fake_exists(name: str) -> bool:
        checked.append(name)
        return name.endswith("final.tgz")

    submitter._blob_exists = fake_exists  # type: ignore[method-assign]

    assert submitter._resolve_resume_blob("old-run") == "results/old-run/final.tgz"
    assert checked == ["results/old-run/final.tgz"]


def test_run_command_script_installs_checkpoint_dependencies(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)

    script = submitter._build_run_command_script("https://example/inputs.tgz")

    assert "apt-get install -y docker.io curl rsync" in script
    assert "docker-compose-plugin" not in script
    assert 'curl -sSf "https://example/inputs.tgz" -o /opt/oss-crs/inputs.tgz' in script
    assert "systemctl start oss-crs-run.service" in script
    assert "ExecStart=/opt/oss-crs/payload/runtime/run.sh" in script
    assert "\n/opt/oss-crs/payload/runtime/run.sh\n" not in script


def test_remote_stop_script_marks_cancel_and_stops_compose() -> None:
    script = AzureSpotVmRunSubmitter._build_remote_stop_script()

    assert "touch /opt/oss-crs/runtime/cancel-requested" in script
    assert "stop --timeout 30" in script
    assert "systemctl stop oss-crs-run.service" not in script


def test_request_remote_stop_waits_for_final_blob(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    names = AzureVmNames.for_run(submitter.config.vm_name_prefix, "run-1")
    blob_checks = iter((False, False, True))
    invoked_scripts: list[str] = []

    monkeypatch.setattr(submitter, "_blob_exists", lambda _name: next(blob_checks))
    monkeypatch.setattr(submitter, "_vm_exists", lambda _name: True)
    monkeypatch.setattr("oss_crs.src.azure_spot_vm.time.sleep", lambda _seconds: None)

    def fake_must_run(cmd: list[str], *_args, **_kwargs) -> str:
        script_arg = cmd[cmd.index("--scripts") + 1]
        invoked_scripts.append(Path(script_arg.removeprefix("@")).read_text())
        return ""

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)

    submitter._request_remote_stop(names, "results/run-1/final.tgz")

    assert len(invoked_scripts) == 1
    assert "cancel-requested" in invoked_scripts[0]


def test_run_id_collision_rejected(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    names = AzureVmNames.for_run("oss-crs-run", "run-1")
    submitter._blob_exists = lambda name: name.endswith("final.tgz")  # type: ignore[method-assign]
    submitter._vm_exists = lambda _name: False  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="already has live resources"):
        submitter._ensure_run_id_available("run-1", names)


def test_resume_metadata_mismatch_rejected(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    def fake_download(_blob_name: str, destination: Path) -> bool:
        with tarfile.open(destination, "w:gz") as tar:
            payload = json.dumps(
                {
                    "crs_name": "crs-libfuzzer",
                    "module_name": "runner",
                    "build_id": "old-build",
                    "sanitizer": "address",
                    "target_engine": "libfuzzer",
                    "target_harness": "fuzz_target",
                    "target_name": "demo-target",
                }
            ).encode()
            info = tarfile.TarInfo(name="metadata.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        return True

    submitter._download_blob_if_exists = fake_download  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="previous archive metadata"):
        submitter._validate_resume_blob_metadata(
            resume_blob="results/old/final.tgz",
            shape=shape,
            target=target,
            build_id="new-build",
            sanitizer="address",
        )


def test_resume_metadata_missing_is_accepted_for_legacy_archives(
    tmp_path: Path,
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    def fake_download(_blob_name: str, destination: Path) -> bool:
        with tarfile.open(destination, "w:gz") as tar:
            payload = b"artifact"
            info = tarfile.TarInfo(name="submit/crs-libfuzzer/povs/crash-1")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        return True

    submitter._download_blob_if_exists = fake_download  # type: ignore[method-assign]

    submitter._validate_resume_blob_metadata(
        resume_blob="results/old/final.tgz",
        shape=shape,
        target=target,
        build_id="new-build",
        sanitizer="address",
    )


def test_prepare_rebuild_cache_extracts_rebuild_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    uploaded: list[str] = []
    sas_created: list[str] = []

    def fake_must_run(cmd: list[str], *_args, **_kwargs) -> str:
        if cmd[0] == "curl":
            archive_path = Path(cmd[cmd.index("-o") + 1])
            with tarfile.open(archive_path, "w:gz") as tar:
                payload = b"object"
                info = tarfile.TarInfo(name="rebuild_out/cache/object.o")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            return ""
        if cmd[1:4] == ["storage", "blob", "upload"]:
            uploaded.append(cmd[cmd.index("--name") + 1])
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)
    monkeypatch.setattr(submitter, "_blob_exists", lambda _blob: False)
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda blob, **_kwargs: sas_created.append(blob) or f"https://example/{blob}",
    )

    url = submitter._prepare_rebuild_cache(
        resume_archive_url="https://example/resume.tgz?sas",
        cache_key="cache-key",
        sas_expires_at=None,
    )

    assert len(uploaded) == 1
    assert uploaded[0].startswith("cache/rebuild/cache-key/")
    assert uploaded[0].endswith(".tgz")
    assert sas_created == uploaded
    assert url == f"https://example/{uploaded[0]}"


def test_prepare_rebuild_cache_skips_archives_without_rebuild_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    uploaded: list[str] = []

    def fake_must_run(cmd: list[str], *_args, **_kwargs) -> str:
        if cmd[0] == "curl":
            archive_path = Path(cmd[cmd.index("-o") + 1])
            with tarfile.open(archive_path, "w:gz") as tar:
                payload = b"seed"
                info = tarfile.TarInfo(name="submit/crs-libfuzzer/seeds/seed")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            return ""
        if cmd[1:4] == ["storage", "blob", "upload"]:
            uploaded.append(cmd[cmd.index("--name") + 1])
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._must_run", fake_must_run)
    monkeypatch.setattr(submitter, "_blob_exists", lambda _blob: False)

    assert (
        submitter._prepare_rebuild_cache(
            resume_archive_url="https://example/resume.tgz?sas",
            cache_key="cache-key",
            sas_expires_at=None,
        )
        is None
    )
    assert uploaded == []


def test_rebuild_cache_key_ignores_renewed_sas_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    checked: list[str] = []

    def fake_exists(blob_name: str) -> bool:
        checked.append(blob_name)
        return True

    monkeypatch.setattr(submitter, "_blob_exists", fake_exists)
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda blob, **_kwargs: f"https://example/{blob}",
    )

    for sas in ("first-signature", "renewed-signature"):
        submitter._prepare_rebuild_cache(
            resume_archive_url=(
                "https://storage.blob.core.windows.net/runs/results/old/final.tgz"
                f"?sig={sas}&se=2099-01-01"
            ),
            cache_key="cache-key",
            sas_expires_at=None,
        )

    assert len(checked) == 2
    assert checked[0] == checked[1]


def test_cleanup_raises_when_resources_remain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    names = AzureVmNames.for_run("oss-crs-run", "run-1")
    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm._run_command", lambda _cmd: (0, "", "")
    )
    monkeypatch.setattr(
        submitter,
        "_remaining_run_resources",
        lambda _vm_name: [names.vm_name],
    )
    monkeypatch.setattr("oss_crs.src.azure_spot_vm.time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="still exist after cleanup"):
        submitter._cleanup_vm(names)


def test_cleanup_is_idempotent_when_resources_are_already_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    commands: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[int, str, str]:
        commands.append(cmd)
        return 3, "", "ResourceNotFound: resource was not found"

    monkeypatch.setattr("oss_crs.src.azure_spot_vm._run_command", fake_run)
    monkeypatch.setattr(submitter, "_remaining_run_resources", lambda _name: [])

    submitter.cleanup_run("run-1")

    assert commands == []


def test_cleanup_retries_until_dependent_resources_are_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    names = AzureVmNames.for_run("oss-crs-run", "run-1")
    remaining = iter(([names.nic_name], [names.nic_name], []))
    sleeps: list[int] = []
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm._run_command",
        lambda cmd: commands.append(cmd) or (0, "", ""),
    )
    monkeypatch.setattr(
        submitter, "_remaining_run_resources", lambda _name: next(remaining)
    )
    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    submitter._cleanup_vm(names)

    assert sleeps == [5]
    assert len(commands) == 2 * len(submitter._build_cleanup_commands(names))


def test_existence_queries_do_not_treat_cli_failures_as_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm._run_command",
        lambda _cmd: (1, "", "connection reset by peer"),
    )

    with pytest.raises(AzureQueryError, match="whether Azure VM"):
        submitter._vm_exists("vm-1")
    with pytest.raises(AzureQueryError, match="whether Azure blob"):
        submitter._blob_exists("results/run/final.tgz")


def test_vm_exists_returns_false_only_for_explicit_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    monkeypatch.setattr(
        "oss_crs.src.azure_spot_vm._run_command",
        lambda _cmd: (1, "", "(ResourceNotFound) The Resource was not found"),
    )

    assert submitter._vm_exists("missing-vm") is False


def test_wait_for_completion_retries_transient_query_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    names = AzureVmNames.for_run("oss-crs-run", "run-1")
    attempts = 0

    def fake_blob_exists(blob_name: str) -> bool:
        nonlocal attempts
        assert blob_name == "results/run-1/final.tgz"
        attempts += 1
        if attempts == 1:
            raise AzureQueryError("temporary Azure outage")
        return True

    monkeypatch.setattr(submitter, "_blob_exists", fake_blob_exists)
    monkeypatch.setattr("oss_crs.src.azure_spot_vm.time.sleep", lambda _seconds: None)

    submitter._wait_for_remote_completion(
        vm_names=names,
        final_blob="results/run-1/final.tgz",
        checkpoint_blob="results/run-1/checkpoint.tgz",
        status_blob="results/run-1/run-status.json",
        timeout_seconds=30,
    )

    assert attempts == 2


def test_sas_expiry_tracks_timeout() -> None:
    short = AzureSpotVmRunSubmitter._sas_expiry(60)
    long = AzureSpotVmRunSubmitter._sas_expiry(24 * 60 * 60)

    assert long > short
    assert long - datetime.now(timezone.utc) > timedelta(hours=29)


def test_resolve_resume_blob_falls_back_to_normalized_id(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    checked: list[str] = []

    def fake_exists(name: str) -> bool:
        checked.append(name)
        return name == "results/my-run-b4dbea/final.tgz"

    submitter._blob_exists = fake_exists  # type: ignore[method-assign]

    assert submitter._resolve_resume_blob("My Run") == "results/my-run-b4dbea/final.tgz"
    assert "results/My Run/final.tgz" not in checked


def test_submit_and_wait_fails_when_remote_run_restores_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    shape = AzureRuntimeShape(
        crs=_make_crs(tmp_path),
        module_name="runner",
        module_config=_make_crs(tmp_path).config.crs_run_phase.modules["runner"],
    )
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)

    monkeypatch.setattr(submitter, "_validate_supported_shape", lambda *_a: shape)
    monkeypatch.setattr(submitter, "_ensure_run_id_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_ensure_shared_infra_and_login",
        lambda: AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )
    monkeypatch.setattr(
        submitter,
        "_build_and_push_runner_image",
        lambda *_a, **_k: "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag",
    )
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda *_a, **_k: "https://example/blob-sas",
    )
    monkeypatch.setattr(
        submitter,
        "_upload_run_inputs",
        lambda **_k: "https://example/inputs.tgz",
    )
    monkeypatch.setattr(submitter, "_create_spot_vm", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_invoke_remote_runner",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(submitter, "_wait_for_remote_completion", lambda **_k: None)
    monkeypatch.setattr(
        submitter,
        "_restore_results",
        lambda **_k: None,
    )
    monkeypatch.setattr(submitter, "_cleanup_vm", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="without uploading a final archive"):
        submitter.submit_and_wait(
            [shape.crs],
            target,
            workdir,
            "run-1",
            "build-1",
            "address",
            90,
            [],
            None,
            None,
            None,
        )


def test_submit_and_wait_returns_timeout_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)

    monkeypatch.setattr(submitter, "_validate_supported_shape", lambda *_a: shape)
    monkeypatch.setattr(submitter, "_ensure_run_id_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_ensure_shared_infra_and_login",
        lambda: AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )
    monkeypatch.setattr(
        submitter,
        "_build_and_push_runner_image",
        lambda *_a, **_k: "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag",
    )
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda *_a, **_k: "https://example/blob-sas",
    )
    monkeypatch.setattr(
        submitter,
        "_upload_run_inputs",
        lambda **_k: "https://example/inputs.tgz",
    )
    monkeypatch.setattr(submitter, "_create_spot_vm", lambda *_a, **_k: None)
    monkeypatch.setattr(submitter, "_invoke_remote_runner", lambda *_a, **_k: None)
    monkeypatch.setattr(submitter, "_wait_for_remote_completion", lambda **_k: None)
    monkeypatch.setattr(submitter, "_restore_results", lambda **_k: "final")
    monkeypatch.setattr(
        submitter,
        "_read_restored_exit_code",
        lambda **_k: 124,
    )
    monkeypatch.setattr(submitter, "_cleanup_vm", lambda *_a, **_k: None)

    assert (
        submitter.submit_and_wait(
            [shape.crs],
            target,
            workdir,
            "run-1",
            "build-1",
            "address",
            90,
            [],
            None,
            None,
            None,
        )
        == 124
    )


def test_submit_and_wait_interrupt_stops_restores_then_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    events: list[str] = []

    monkeypatch.setattr(submitter, "_validate_supported_shape", lambda *_a: shape)
    monkeypatch.setattr(submitter, "_ensure_run_id_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_ensure_shared_infra_and_login",
        lambda: AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )
    monkeypatch.setattr(
        submitter,
        "_build_and_push_runner_image",
        lambda *_a, **_k: "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag",
    )
    monkeypatch.setattr(
        submitter, "_create_blob_sas_url", lambda *_a, **_k: "https://example/sas"
    )
    monkeypatch.setattr(
        submitter, "_upload_run_inputs", lambda **_k: "https://example/inputs"
    )
    monkeypatch.setattr(submitter, "_create_spot_vm", lambda *_a, **_k: None)
    monkeypatch.setattr(submitter, "_invoke_remote_runner", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_wait_for_remote_completion",
        lambda **_k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        submitter,
        "_request_remote_stop",
        lambda *_a, **_k: events.append("stop"),
    )
    monkeypatch.setattr(
        submitter,
        "_restore_results",
        lambda **_k: events.append("restore") or "final",
    )
    monkeypatch.setattr(
        submitter, "_cleanup_vm", lambda *_a, **_k: events.append("cleanup")
    )
    monkeypatch.setattr(submitter, "_delete_blob_if_exists", lambda *_a: None)

    with pytest.raises(KeyboardInterrupt):
        submitter.submit_and_wait(
            [crs],
            target,
            workdir,
            "run-1",
            "build-1",
            "address",
            90,
            [],
            None,
            None,
            None,
        )

    assert events == ["stop", "restore", "cleanup"]


def test_submit_and_wait_deletes_inputs_when_vm_create_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    deleted: list[str] = []

    monkeypatch.setattr(submitter, "_validate_supported_shape", lambda *_a: shape)
    monkeypatch.setattr(submitter, "_ensure_run_id_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        submitter,
        "_ensure_shared_infra_and_login",
        lambda: AzureAcrAuth(
            login_server="acrname.azurecr.io",
            username="user",
            access_token="token",
        ),
    )
    monkeypatch.setattr(
        submitter,
        "_build_and_push_runner_image",
        lambda *_a, **_k: "acrname.azurecr.io/oss-crs/crs-libfuzzer/runner:tag",
    )
    monkeypatch.setattr(
        submitter,
        "_create_blob_sas_url",
        lambda *_a, **_k: "https://example/blob-sas",
    )
    monkeypatch.setattr(
        submitter,
        "_upload_run_inputs",
        lambda **_k: "https://example/inputs.tgz",
    )
    monkeypatch.setattr(
        submitter,
        "_create_spot_vm",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("capacity failed")),
    )
    monkeypatch.setattr(submitter, "_restore_results", lambda **_k: None)
    monkeypatch.setattr(
        submitter,
        "_delete_blob_if_exists",
        lambda name: deleted.append(name),
    )

    with pytest.raises(RuntimeError, match="capacity failed"):
        submitter.submit_and_wait(
            [shape.crs],
            target,
            workdir,
            "run-1",
            "build-1",
            "address",
            90,
            [],
            None,
            None,
            None,
        )

    assert deleted == ["run-inputs/run-1/inputs.tgz"]


def test_restore_results_falls_back_to_checkpoint(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    restored_submit = workdir.get_submit_dir()
    run_logs = workdir.get_run_logs_dir()

    def fake_download(blob_name: str, destination: Path) -> bool:
        if blob_name == "results/run-1/final.tgz":
            return False
        if blob_name == "results/run-1/checkpoint.tgz":
            with tarfile.open(destination, "w:gz") as tar:
                payload = b"artifact"
                info = tarfile.TarInfo(name="submit/crs-libfuzzer/povs/crash-1")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            return True
        if blob_name == "results/run-1/status.json":
            destination.write_text('{"state":"running"}\n')
            return True
        return False

    submitter._download_blob_if_exists = fake_download  # type: ignore[method-assign]

    restored = submitter._restore_results(
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        sanitizer="address",
        final_blob="results/run-1/final.tgz",
        checkpoint_blob="results/run-1/checkpoint.tgz",
        status_blob="results/run-1/status.json",
    )

    assert restored == "checkpoint"
    assert (restored_submit / "povs" / "crash-1").read_text() == "artifact"
    assert (run_logs / "run-status.json").exists()


def test_restore_final_prefers_archived_status_over_stale_blob(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )
    status_download_attempted = False

    def fake_download(blob_name: str, destination: Path) -> bool:
        nonlocal status_download_attempted
        if blob_name == "results/run-1/final.tgz":
            with tarfile.open(destination, "w:gz") as tar:
                payload = json.dumps(
                    {"state": "completed", "exit_code": "124"}
                ).encode()
                info = tarfile.TarInfo(name="run-status.json")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            return True
        if blob_name == "results/run-1/status.json":
            status_download_attempted = True
            destination.write_text('{"state":"running","exit_code":""}\n')
            return True
        return False

    submitter._download_blob_if_exists = fake_download  # type: ignore[method-assign]

    restored = submitter._restore_results(
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        sanitizer="address",
        final_blob="results/run-1/final.tgz",
        checkpoint_blob="results/run-1/checkpoint.tgz",
        status_blob="results/run-1/status.json",
    )

    assert restored == "final"
    assert status_download_attempted is False
    assert (
        submitter._read_restored_exit_code(
            target=target,
            run_id="run-1",
            sanitizer="address",
            work_dir=workdir,
        )
        == 124
    )


def test_restore_results_preserves_status_when_no_archives_exist(
    tmp_path: Path,
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    submitter._storage_account_key = "secret"
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    workdir = _make_workdir(tmp_path)
    shape = AzureRuntimeShape(
        crs=crs,
        module_name="runner",
        module_config=crs.config.crs_run_phase.modules["runner"],
    )

    run_logs = workdir.get_run_logs_dir()

    def fake_download(blob_name: str, destination: Path) -> bool:
        if blob_name == "results/run-1/status.json":
            destination.write_text('{"state":"bootstrapping"}\n')
            return True
        return False

    submitter._download_blob_if_exists = fake_download  # type: ignore[method-assign]

    restored = submitter._restore_results(
        shape=shape,
        target=target,
        work_dir=workdir,
        run_id="run-1",
        sanitizer="address",
        final_blob="results/run-1/final.tgz",
        checkpoint_blob="results/run-1/checkpoint.tgz",
        status_blob="results/run-1/status.json",
    )

    assert restored is None
    assert (run_logs / "run-status.json").read_text() == '{"state":"bootstrapping"}\n'


def test_single_container_multilang_given_fuzzer_shape_supported(
    tmp_path: Path,
) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    module_config = SimpleNamespace(
        dockerfile="oss-crs/dockerfiles/fuzzer.Dockerfile",
        additional_env={},
    )
    crs = _make_crs(
        tmp_path,
        name="atlantis-multilang-given_fuzzer",
        modules={"fuzzer-1": module_config},
    )
    target = _make_target(tmp_path)

    shape = submitter._validate_supported_shape([crs], target)

    assert shape.crs.name == "atlantis-multilang-given_fuzzer"
    assert shape.module_name == "fuzzer-1"

    compose = yaml.safe_load(
        submitter._render_remote_compose(
            shape=shape,
            target=target,
            run_id="run-1",
            sanitizer="address",
            runner_images={
                "fuzzer-1": (
                    "acrname.azurecr.io/oss-crs/"
                    "atlantis-multilang-given_fuzzer/fuzzer-1:tag"
                )
            },
        )
    )
    service = compose["services"]["atlantis-multilang-given_fuzzer_fuzzer-1"]

    assert service["environment"]["OSS_CRS_NAME"] == ("atlantis-multilang-given_fuzzer")
    assert service["environment"]["OSS_CRS_SERVICE_NAME"] == (
        "atlantis-multilang-given_fuzzer_fuzzer-1"
    )
    assert (
        "/opt/oss-crs/payload/build_out/atlantis-multilang-given_fuzzer:"
        "/OSS_CRS_BUILD_OUT_DIR:ro"
    ) in service["volumes"]
    assert (
        "/opt/oss-crs/runtime/submit/atlantis-multilang-given_fuzzer:"
        "/OSS_CRS_SUBMIT_DIR:rw"
    ) in service["volumes"]


def test_unsupported_multilang_llm_config_rejected(tmp_path: Path) -> None:
    fake_llm = SimpleNamespace(mode="internal", exists=lambda: True)
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=fake_llm)
    crs = _make_crs(tmp_path, name="atlantis-multilang-wo-concolic")
    target = _make_target(tmp_path)

    with pytest.raises(RuntimeError, match="supports external LiteLLM only"):
        submitter._validate_supported_shape([crs], target)


def test_external_litellm_config_supported_for_single_container_shape(
    tmp_path: Path,
) -> None:
    fake_llm = SimpleNamespace(mode="external", exists=lambda: True)
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=fake_llm)
    crs = _make_crs(tmp_path, name="atlantis-multilang-wo-concolic")
    target = _make_target(tmp_path)

    shape = submitter._validate_supported_shape([crs], target)

    assert shape.crs is crs
    assert shape.module_name == "runner"


def test_unsupported_multilang_multi_container_shape_rejected(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    modules = {
        "multilang": SimpleNamespace(
            dockerfile="oss-crs/dockerfiles/multilang.Dockerfile",
            additional_env={},
        ),
        "redis": SimpleNamespace(
            dockerfile="oss-crs/dockerfiles/redis.Dockerfile",
            additional_env={},
        ),
        "joern": SimpleNamespace(
            dockerfile="oss-crs/dockerfiles/joern.Dockerfile",
            additional_env={},
        ),
    }
    crs = _make_crs(
        tmp_path,
        name="atlantis-multilang-wo-concolic",
        modules=modules,
    )
    target = _make_target(tmp_path)

    with pytest.raises(RuntimeError, match="expects exactly one runtime container"):
        submitter._validate_supported_shape([crs], target)


def test_unsupported_bug_fixing_shape_rejected(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    wrong_crs = _make_crs(tmp_path, name="crs-codex", is_bug_fixing=True)
    target = _make_target(tmp_path)

    with pytest.raises(RuntimeError, match="supports only bug-finding CRS"):
        submitter._validate_supported_shape([wrong_crs], target)


def test_unsupported_target_architecture_rejected(tmp_path: Path) -> None:
    submitter = AzureSpotVmRunSubmitter(_make_config(tmp_path), llm=None)
    crs = _make_crs(tmp_path)
    target = _make_target(tmp_path)
    target.get_target_env = lambda: {
        "name": "demo-target",
        "architecture": "aarch64",
    }

    with pytest.raises(RuntimeError, match="supports only x86_64 targets"):
        submitter._validate_supported_shape([crs], target)
