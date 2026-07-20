# SPDX-License-Identifier: MIT
"""Azure Spot VM runtime for single-container libFuzzer-style OSS-CRS paths.

This module intentionally implements the smallest useful Azure runtime:

- `prepare` and `build-target` stay local.
- `run_env: azure` stages already-built artifacts to Blob Storage.
- A fresh Spot VM pulls the staged payload, runs a single CRS
  container remotely, periodically checkpoints artifacts, and uploads a final
  archive on normal completion.
- Local restoration falls back to the latest checkpoint archive when the final
  archive is missing (for example after Spot eviction).

The implementation is explicitly scoped to single-runtime-container bug-finding
CRS shapes. This includes `crs-libfuzzer`, `atlantis-multilang-given_fuzzer`,
and external-LiteLLM `atlantis-multilang-wo-concolic`; internal LiteLLM
sidecars are still rejected with clear validation errors.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

import yaml

from .env_policy import build_run_service_env, resolve_env_references
from .cpuset import parse_cpuset
from .utils import normalize_run_id as _normalize_resume_run_id

if TYPE_CHECKING:
    from .crs import CRS
    from .llm import LLM
    from .target import Target
    from .workdir import WorkDir


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    azure_cfg = env.get("AZURE_CONFIG_DIR")
    if azure_cfg:
        Path(azure_cfg).mkdir(parents=True, exist_ok=True)
    return env


def _run_command(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_command_env())
    return proc.returncode, proc.stdout, proc.stderr


def _must_run(
    cmd: list[str],
    error_prefix: str,
    *,
    cwd: Optional[Path] = None,
    redact_values: Optional[list[str]] = None,
) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=_command_env(),
    )
    if proc.returncode != 0:
        rendered = " ".join(cmd)
        for value in redact_values or []:
            if value:
                rendered = rendered.replace(value, "***")
        raise RuntimeError(
            f"{error_prefix}\n"
            f"Command: {rendered}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def _log(msg: str) -> None:
    print(f"Azure Spot VM: {msg}", flush=True)


def _copy_tree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_symlink():
            target.unlink(missing_ok=True)
            target.symlink_to(os.readlink(item))
        elif item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(item, target, follow_symlinks=False)


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean value for {name}: {raw!r}")


def _parse_csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return ()
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values:
        raise RuntimeError(f"{name} must contain at least one non-empty value")
    return values


def _parse_nonnegative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be zero or greater")
    return value


def _parse_spot_max_price_env(name: str, default: str) -> str:
    raw = os.environ.get(name, default).strip()
    if raw == "-1":
        return raw
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive decimal value or -1") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero, or -1")
    return raw


@dataclass(frozen=True)
class AzureSpotVmConfig:
    resource_group: str
    location: str
    storage_account: str
    storage_container: str
    acr_name: str
    vm_admin_username: str
    ssh_public_key_path: Path
    vm_sizes: tuple[str, ...]
    vm_os_disk_size_gb: int
    vm_image: str
    vm_name_prefix: str
    keep_failed_vm: bool
    sync_interval_seconds: int
    vm_zones: tuple[str, ...]
    spot_max_price: str
    enable_ssh: bool
    input_cache_enabled: bool
    rebuild_cache_enabled: bool
    require_prebuilt_images: bool = False

    @property
    def acr_login_server(self) -> str:
        return f"{self.acr_name}.azurecr.io"

    @property
    def docker_registry(self) -> str:
        return f"{self.acr_login_server}/oss-crs"

    @classmethod
    def from_env(cls) -> "AzureSpotVmConfig":
        required = {
            "OSS_CRS_AZURE_RESOURCE_GROUP": os.environ.get(
                "OSS_CRS_AZURE_RESOURCE_GROUP"
            ),
            "OSS_CRS_AZURE_LOCATION": os.environ.get("OSS_CRS_AZURE_LOCATION"),
            "OSS_CRS_AZURE_STORAGE_ACCOUNT": os.environ.get(
                "OSS_CRS_AZURE_STORAGE_ACCOUNT"
            ),
            "OSS_CRS_AZURE_STORAGE_CONTAINER": os.environ.get(
                "OSS_CRS_AZURE_STORAGE_CONTAINER"
            ),
            "OSS_CRS_AZURE_ACR_NAME": os.environ.get("OSS_CRS_AZURE_ACR_NAME"),
            "OSS_CRS_AZURE_VM_ADMIN_USERNAME": os.environ.get(
                "OSS_CRS_AZURE_VM_ADMIN_USERNAME"
            ),
            "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH": os.environ.get(
                "OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH"
            ),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing required Azure env vars: " + ", ".join(sorted(missing))
            )
        required_values = cast(dict[str, str], required)

        ssh_public_key_path = Path(
            required_values["OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH"]
        ).expanduser()
        if not ssh_public_key_path.exists():
            raise RuntimeError(
                "SSH public key path does not exist: "
                f"{required_values['OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH']}"
            )

        sync_interval_raw = os.environ.get("OSS_CRS_AZURE_SYNC_INTERVAL_SECONDS", "15")
        try:
            sync_interval = int(sync_interval_raw)
        except ValueError as exc:
            raise RuntimeError(
                "OSS_CRS_AZURE_SYNC_INTERVAL_SECONDS must be an integer"
            ) from exc
        if sync_interval <= 0:
            raise RuntimeError(
                "OSS_CRS_AZURE_SYNC_INTERVAL_SECONDS must be greater than zero"
            )

        vm_size = os.environ.get("OSS_CRS_AZURE_VM_SIZE", "Standard_D4as_v5")
        vm_sizes = _parse_csv_env("OSS_CRS_AZURE_VM_SIZE_CANDIDATES") or (vm_size,)
        vm_zones = _parse_csv_env("OSS_CRS_AZURE_VM_ZONES")
        vm_os_disk_size_gb = _parse_nonnegative_int_env(
            "OSS_CRS_AZURE_VM_OS_DISK_SIZE_GB", 256
        )
        if vm_os_disk_size_gb <= 0:
            raise RuntimeError(
                "OSS_CRS_AZURE_VM_OS_DISK_SIZE_GB must be greater than zero"
            )

        return cls(
            resource_group=required_values["OSS_CRS_AZURE_RESOURCE_GROUP"],
            location=required_values["OSS_CRS_AZURE_LOCATION"],
            storage_account=required_values["OSS_CRS_AZURE_STORAGE_ACCOUNT"],
            storage_container=required_values["OSS_CRS_AZURE_STORAGE_CONTAINER"],
            acr_name=required_values["OSS_CRS_AZURE_ACR_NAME"],
            vm_admin_username=required_values["OSS_CRS_AZURE_VM_ADMIN_USERNAME"],
            ssh_public_key_path=ssh_public_key_path,
            vm_sizes=vm_sizes,
            vm_os_disk_size_gb=vm_os_disk_size_gb,
            vm_image=os.environ.get("OSS_CRS_AZURE_VM_IMAGE", "Ubuntu2204"),
            vm_name_prefix=os.environ.get(
                "OSS_CRS_AZURE_VM_NAME_PREFIX", "oss-crs-run"
            ),
            keep_failed_vm=_parse_bool_env("OSS_CRS_AZURE_KEEP_FAILED_VM", False),
            sync_interval_seconds=sync_interval,
            vm_zones=vm_zones,
            spot_max_price=_parse_spot_max_price_env(
                "OSS_CRS_AZURE_SPOT_MAX_PRICE", "0.50"
            ),
            enable_ssh=_parse_bool_env("OSS_CRS_AZURE_ENABLE_SSH", False),
            input_cache_enabled=_parse_bool_env(
                "OSS_CRS_AZURE_INPUT_CACHE_ENABLED", True
            ),
            rebuild_cache_enabled=_parse_bool_env(
                "OSS_CRS_AZURE_REBUILD_CACHE_ENABLED", True
            ),
            require_prebuilt_images=_parse_bool_env(
                "OSS_CRS_AZURE_REQUIRE_PREBUILT_IMAGES", False
            ),
        )


@dataclass(frozen=True)
class AzureAcrAuth:
    login_server: str
    username: str
    access_token: str


@dataclass(frozen=True)
class AzureVmNames:
    vm_name: str
    public_ip_name: str
    nic_name: str
    nsg_name: str
    vnet_name: str
    subnet_name: str
    os_disk_name: str

    @classmethod
    def for_run(cls, prefix: str, run_id: str) -> "AzureVmNames":
        suffix = hashlib.sha256(run_id.encode()).hexdigest()[:8]
        base = f"{prefix}-{suffix}".lower()
        return cls(
            vm_name=base,
            public_ip_name=f"{base}-ip",
            nic_name=f"{base}VMNic",
            nsg_name=f"{base}-nsg",
            vnet_name=f"{base}-vnet",
            subnet_name="default",
            os_disk_name=f"{base}-osdisk",
        )


@dataclass(frozen=True)
class AzureRuntimeShape:
    crs: "CRS"
    module_name: str
    module_config: Any
    runtime_modules: tuple[tuple[str, Any], ...] = ()

    def modules(self) -> tuple[tuple[str, Any], ...]:
        return self.runtime_modules or ((self.module_name, self.module_config),)


@dataclass(frozen=True)
class AzureInputCachePlan:
    cache_key: str
    build_out_blob: str
    fuzz_proj_blob: str
    target_source_blob: str
    build_out_url: str
    fuzz_proj_url: str
    target_source_url: str


class AzureQueryError(RuntimeError):
    """An Azure existence query failed without proving the resource is absent."""


class AzureRunDeadlineExceeded(RuntimeError):
    """The caller's run deadline expired before remote execution could start."""


class AzureSpotVmRunSubmitter:
    """Submit a supported single-container OSS-CRS run to an Azure Spot VM."""

    def __init__(self, config: AzureSpotVmConfig, llm: Optional["LLM"] = None):
        self.config = config
        self.llm = llm
        self._storage_account_key: Optional[str] = None

    def submit_and_wait(
        self,
        crs_list: list["CRS"],
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        build_id: str,
        sanitizer: str,
        timeout_seconds: Optional[int],
        pov_files: list[Path],
        diff_path: Optional[Path],
        seed_dir: Optional[Path],
        bug_candidate: Optional[Path],
        resume_run_id: Optional[str] = None,
    ) -> int:
        if timeout_seconds is not None and timeout_seconds <= 0:
            return 124
        deadline_epoch_seconds = (
            None if timeout_seconds is None else time.time() + timeout_seconds
        )
        shape = self._validate_supported_shape(crs_list, target)
        vm_names = AzureVmNames.for_run(self.config.vm_name_prefix, run_id)
        acr_auth = self._ensure_shared_infra_and_login()
        if deadline_epoch_seconds is not None and time.time() >= deadline_epoch_seconds:
            return 124

        run_inputs_blob = f"run-inputs/{run_id}/inputs.tgz"
        checkpoint_blob = f"results/{run_id}/checkpoint.tgz"
        final_blob = f"results/{run_id}/final.tgz"
        status_blob = f"results/{run_id}/run-status.json"
        resume_blob = (
            self._resolve_resume_blob(resume_run_id) if resume_run_id else None
        )
        self._ensure_run_id_available(run_id, vm_names)
        if resume_blob:
            self._validate_resume_blob_metadata(
                resume_blob=resume_blob,
                shape=shape,
                target=target,
                build_id=build_id,
                sanitizer=sanitizer,
            )
        sas_expires_at = self._sas_expiry(timeout_seconds)

        runner_images: dict[str, str] = {}
        attempted_input_upload = False
        vm_creation_attempted = False
        vm_created = False
        remote_ok = False
        remote_error: Optional[Exception] = None
        cleanup_blocked_by_query_error = False
        restored_from: Optional[str] = None
        try:
            runner_images = self._build_and_push_runtime_images(shape, target)
            if (
                deadline_epoch_seconds is not None
                and time.time() >= deadline_epoch_seconds
            ):
                raise AzureRunDeadlineExceeded(
                    "Azure run deadline expired while preparing runtime images; "
                    "artifacts and a Spot VM were not created."
                )
            attempted_input_upload = True
            payload_url = self._upload_run_inputs(
                shape=shape,
                target=target,
                work_dir=work_dir,
                run_id=run_id,
                build_id=build_id,
                sanitizer=sanitizer,
                pov_files=pov_files,
                diff_path=diff_path,
                seed_dir=seed_dir,
                bug_candidate=bug_candidate,
                timeout_seconds=timeout_seconds,
                deadline_epoch_seconds=deadline_epoch_seconds,
                runner_images=runner_images,
                checkpoint_upload_url=self._create_blob_sas_url(
                    checkpoint_blob, permissions="rcw", expires_at=sas_expires_at
                ),
                final_upload_url=self._create_blob_sas_url(
                    final_blob, permissions="rcw", expires_at=sas_expires_at
                ),
                status_upload_url=self._create_blob_sas_url(
                    status_blob, permissions="rcw", expires_at=sas_expires_at
                ),
                acr_auth=acr_auth,
                run_inputs_blob=run_inputs_blob,
                resume_archive_url=(
                    self._create_blob_sas_url(
                        resume_blob, permissions="r", expires_at=sas_expires_at
                    )
                    if resume_blob
                    else None
                ),
                sas_expires_at=sas_expires_at,
            )
            if (
                deadline_epoch_seconds is not None
                and time.time() >= deadline_epoch_seconds
            ):
                raise AzureRunDeadlineExceeded(
                    "Azure run deadline expired during artifact preparation; "
                    "the Spot VM was not created."
                )
            vm_creation_attempted = True
            self._create_spot_vm(vm_names, self._required_vcpus(shape))
            vm_created = True
            self._invoke_remote_runner(vm_names, payload_url)
            self._wait_for_remote_completion(
                vm_names=vm_names,
                final_blob=final_blob,
                checkpoint_blob=checkpoint_blob,
                status_blob=status_blob,
                timeout_seconds=timeout_seconds,
                deadline_epoch_seconds=deadline_epoch_seconds,
            )
            remote_ok = True
        except Exception as exc:
            remote_error = exc
            cleanup_blocked_by_query_error = isinstance(exc, AzureQueryError)
        except KeyboardInterrupt:
            if vm_created:
                try:
                    self._request_remote_stop(vm_names, final_blob)
                except Exception as stop_exc:
                    _log(
                        "graceful remote stop failed; restoring the latest "
                        f"available archive before cleanup: {stop_exc}"
                    )
            raise
        finally:
            if attempted_input_upload or vm_created:
                try:
                    restored_from = self._restore_results(
                        shape=shape,
                        target=target,
                        work_dir=work_dir,
                        run_id=run_id,
                        sanitizer=sanitizer,
                        final_blob=final_blob,
                        checkpoint_blob=checkpoint_blob,
                        status_blob=status_blob,
                    )
                except Exception as restore_exc:
                    cleanup_blocked_by_query_error = (
                        cleanup_blocked_by_query_error
                        or isinstance(restore_exc, AzureQueryError)
                    )
                    if remote_error is None:
                        remote_error = restore_exc
                    else:
                        remote_error = RuntimeError(
                            f"{remote_error}\nAlso failed to restore Azure artifacts: "
                            f"{restore_exc}"
                        )

            should_keep = (self.config.keep_failed_vm and not remote_ok) or (
                vm_created and cleanup_blocked_by_query_error
            )
            if vm_creation_attempted and not should_keep:
                try:
                    self._cleanup_vm(vm_names)
                except Exception as cleanup_exc:
                    _log(
                        f"cleanup failed and can be retried with azure-cleanup: {cleanup_exc}"
                    )
                    if remote_error is None:
                        remote_error = cleanup_exc
                    else:
                        remote_error = RuntimeError(
                            f"{remote_error}\nAlso failed to clean up Azure VM: "
                            f"{cleanup_exc}"
                        )
            if attempted_input_upload:
                self._delete_blob_if_exists(run_inputs_blob)
        if remote_error is not None:
            if isinstance(remote_error, AzureRunDeadlineExceeded):
                return 124
            if restored_from == "checkpoint":
                _log("restored partial results from the latest checkpoint archive")
                return 1
            raise remote_error

        if restored_from == "checkpoint":
            _log(
                "remote VM completed without a final archive; restored the latest "
                "checkpoint instead"
            )
            return 1
        if restored_from is None:
            raise RuntimeError(
                "Azure Spot VM run finished without uploading a final archive, "
                "checkpoint archive, or status blob."
            )

        exit_code = self._read_restored_exit_code(
            target=target,
            run_id=run_id,
            sanitizer=sanitizer,
            work_dir=work_dir,
        )
        if exit_code == 124:
            return 124
        if exit_code not in (None, 0):
            return 1
        return 0 if remote_ok else 1

    def _validate_supported_shape(
        self, crs_list: list["CRS"], target: "Target"
    ) -> AzureRuntimeShape:
        if (
            self.llm is not None
            and self.llm.exists()
            and getattr(self.llm, "mode", "internal") != "external"
        ):
            raise RuntimeError(
                "Azure Spot VM run_env supports external LiteLLM only. "
                "Internal LiteLLM sidecars are not supported yet; configure "
                "llm_config.litellm.mode=external or use a no-LLM single-container CRS."
            )
        if len(crs_list) != 1:
            raise RuntimeError(
                "Azure Spot VM run_env currently supports exactly one CRS entry; "
                f"received {len(crs_list)}."
            )

        crs = crs_list[0]
        if crs.config.is_bug_fixing or crs.config.is_bug_fixing_ensemble:
            raise RuntimeError(
                "Azure Spot VM run_env currently supports only bug-finding CRS entries."
            )
        if crs.config.is_triage or crs.config.is_seed_filter:
            raise RuntimeError(
                "Azure Spot VM run_env does not support post-processing CRS types."
            )

        runtime_modules = [
            (name, module)
            for name, module in crs.config.crs_run_phase.modules.items()
            if getattr(module, "dockerfile", None)
        ]
        if len(runtime_modules) != 1 and not (
            crs.name == "atlantis-multilang-wo-concolic"
            and self.llm is not None
            and self.llm.exists()
            and getattr(self.llm, "mode", None) == "external"
        ):
            raise RuntimeError(
                "Azure Spot VM run_env expects exactly one runtime container for "
                f"CRS '{crs.name}'; found {len(runtime_modules)}."
            )
        if len(runtime_modules) != 1:
            module_names = {name for name, _module in runtime_modules}
            expected_modules = {
                "multilang",
                "redis",
                "init_codeindexer",
                "joern",
                "lsp",
            }
            unsupported = sorted(module_names - expected_modules)
            missing = sorted(expected_modules - module_names)
            if unsupported or missing:
                details = []
                if unsupported:
                    details.append(f"unsupported modules: {', '.join(unsupported)}")
                if missing:
                    details.append(f"missing modules: {', '.join(missing)}")
                raise RuntimeError(
                    "Azure Spot VM run_env supports the multi-container "
                    "atlantis-multilang-wo-concolic shape only for the expected "
                    f"module set ({'; '.join(details)})."
                )
        if target.engine != "libfuzzer":
            raise RuntimeError(
                "Azure Spot VM run_env currently supports only libFuzzer targets."
            )
        target_arch = str(target.get_target_env().get("architecture", "")).lower()
        if target_arch and target_arch not in {"x86_64", "amd64"}:
            raise RuntimeError(
                "Azure Spot VM run_env currently supports only x86_64 targets; "
                f"target architecture is {target_arch!r}."
            )
        if not target.target_harness:
            raise RuntimeError(
                "Azure Spot VM run_env requires a target harness for remote runs."
            )

        module_lookup = dict(runtime_modules)
        module_name, module_config = (
            ("multilang", module_lookup["multilang"])
            if "multilang" in module_lookup
            else runtime_modules[0]
        )
        return AzureRuntimeShape(
            crs=crs,
            module_name=module_name,
            module_config=module_config,
            runtime_modules=tuple(runtime_modules),
        )

    def _ensure_shared_infra_and_login(self) -> AzureAcrAuth:
        self._ensure_resource_group()
        self._ensure_acr()
        self._ensure_storage_account()
        self._storage_account_key = self._ensure_storage_account_key()
        self._ensure_storage_container()
        return self._ensure_acr_login()

    def _ensure_resource_group(self) -> None:
        self._ensure_resource_exists(
            show_cmd=[
                "az",
                "group",
                "show",
                "--name",
                self.config.resource_group,
            ],
            create_cmd=[
                "az",
                "group",
                "create",
                "--name",
                self.config.resource_group,
                "--location",
                self.config.location,
            ],
            description="resource group",
        )

    def _ensure_acr(self) -> None:
        self._ensure_resource_exists(
            show_cmd=[
                "az",
                "acr",
                "show",
                "--name",
                self.config.acr_name,
                "--resource-group",
                self.config.resource_group,
            ],
            create_cmd=[
                "az",
                "acr",
                "create",
                "--name",
                self.config.acr_name,
                "--resource-group",
                self.config.resource_group,
                "--location",
                self.config.location,
                "--sku",
                "Basic",
            ],
            description="ACR",
        )

    def _ensure_storage_account(self) -> None:
        self._ensure_resource_exists(
            show_cmd=[
                "az",
                "storage",
                "account",
                "show",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.storage_account,
            ],
            create_cmd=[
                "az",
                "storage",
                "account",
                "create",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.storage_account,
                "--location",
                self.config.location,
                "--sku",
                "Standard_LRS",
                "--kind",
                "StorageV2",
            ],
            description="storage account",
        )

    def _ensure_storage_container(self) -> None:
        show_cmd = [
            "az",
            "storage",
            "container",
            "show",
            "--account-name",
            self.config.storage_account,
            "--account-key",
            self._require_storage_key(),
            "--name",
            self.config.storage_container,
        ]
        create_cmd = [
            "az",
            "storage",
            "container",
            "create",
            "--account-name",
            self.config.storage_account,
            "--account-key",
            self._require_storage_key(),
            "--name",
            self.config.storage_container,
        ]
        self._ensure_resource_exists(
            show_cmd=show_cmd,
            create_cmd=create_cmd,
            description="storage container",
            redact_values=[self._require_storage_key()],
        )

    def _ensure_resource_exists(
        self,
        *,
        show_cmd: list[str],
        create_cmd: list[str],
        description: str,
        redact_values: Optional[list[str]] = None,
    ) -> None:
        rc, _out, _err = _run_command(show_cmd)
        if rc == 0:
            return
        _must_run(
            create_cmd,
            f"Failed to create Azure {description}",
            redact_values=redact_values,
        )

    def _ensure_storage_account_key(self) -> str:
        if self._storage_account_key:
            return self._storage_account_key
        self._storage_account_key = _must_run(
            [
                "az",
                "storage",
                "account",
                "keys",
                "list",
                "--resource-group",
                self.config.resource_group,
                "--account-name",
                self.config.storage_account,
                "--query",
                "[0].value",
                "-o",
                "tsv",
            ],
            "Failed to fetch storage account key",
        )
        return self._storage_account_key

    def _ensure_acr_login(self) -> AzureAcrAuth:
        _must_run(
            ["az", "acr", "login", "--name", self.config.acr_name],
            "Failed to log Docker into Azure Container Registry",
        )
        token_payload = json.loads(
            _must_run(
                [
                    "az",
                    "acr",
                    "login",
                    "--name",
                    self.config.acr_name,
                    "--expose-token",
                    "-o",
                    "json",
                ],
                "Failed to get Azure Container Registry access token",
            )
        )
        access_token = token_payload.get("accessToken")
        if not access_token:
            raise RuntimeError(
                "Azure ACR access token response did not contain accessToken"
            )
        return AzureAcrAuth(
            login_server=token_payload.get("loginServer", self.config.acr_login_server),
            username="00000000-0000-0000-0000-000000000000",
            access_token=access_token,
        )

    @staticmethod
    def _context_hash(paths: list[Path], extra: str = "") -> str:
        exclude_dirs = {".git", "__pycache__", ".venv", "node_modules"}
        hasher = hashlib.sha256()
        hasher.update(extra.encode())
        for path in paths:
            if path.is_symlink():
                hasher.update(str(path).encode())
                hasher.update(os.readlink(path).encode())
                continue
            if path.is_file():
                hasher.update(path.read_bytes())
                continue
            if not path.is_dir():
                continue
            for item in sorted(path.rglob("*")):
                if any(part in exclude_dirs for part in item.parts):
                    continue
                relative = str(item.relative_to(path)).encode()
                if item.is_symlink():
                    hasher.update(relative)
                    hasher.update(b"symlink")
                    hasher.update(os.readlink(item).encode())
                elif item.is_file():
                    hasher.update(relative)
                    hasher.update(item.read_bytes())
        return hasher.hexdigest()[:12]

    @staticmethod
    def _image_exists_in_registry(tag: str) -> bool:
        rc = subprocess.run(
            ["docker", "manifest", "inspect", tag],
            capture_output=True,
            env=_command_env(),
        ).returncode
        return rc == 0

    def _build_and_push_runner_image(
        self, shape: AzureRuntimeShape, target: "Target"
    ) -> str:
        return self._build_and_push_module_image(
            shape=shape,
            target=target,
            module_name=shape.module_name,
            module_config=shape.module_config,
        )

    def _build_and_push_runtime_images(
        self, shape: AzureRuntimeShape, target: "Target"
    ) -> dict[str, str]:
        modules = shape.modules()
        if len(modules) == 1 and modules[0][0] == shape.module_name:
            return {shape.module_name: self._build_and_push_runner_image(shape, target)}
        return {
            module_name: self._build_and_push_module_image(
                shape=shape,
                target=target,
                module_name=module_name,
                module_config=module_config,
            )
            for module_name, module_config in modules
        }

    def _build_and_push_module_image(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        module_name: str,
        module_config: Any,
    ) -> str:
        crs = shape.crs
        repo_root = Path(__file__).resolve().parents[2]
        libcrs_path = repo_root / "libCRS"
        dockerfile = crs.crs_path / module_config.dockerfile
        base_image = target.get_docker_image_name()
        ctx_hash = self._context_hash(
            [dockerfile, crs.crs_path, libcrs_path], extra=base_image
        )
        tag = (
            f"{self.config.docker_registry}/{crs.name}/{module_name}:"
            f"{crs.config.version}-{ctx_hash}"
        )
        cache_tag = (
            f"{self.config.docker_registry}/{crs.name}/{module_name}:"
            f"{crs.config.version}"
        )
        if self._image_exists_in_registry(tag):
            _log(f"runtime image already present in ACR: {tag}")
            return tag
        if self.config.require_prebuilt_images:
            raise RuntimeError(
                "Runtime image is missing from ACR and local builds are disabled "
                f"by OSS_CRS_AZURE_REQUIRE_PREBUILT_IMAGES: {tag}"
            )

        _log(f"building and pushing runtime image: {tag}")
        _must_run(
            [
                "docker",
                "build",
                "--cache-from",
                cache_tag,
                "--build-arg",
                f"target_base_image={base_image}",
                "--build-arg",
                f"crs_version={crs.config.version}",
                "--build-context",
                f"libcrs={libcrs_path}",
                "-t",
                tag,
                "-t",
                cache_tag,
                "-f",
                str(dockerfile),
                str(crs.crs_path),
            ],
            f"Failed to build runtime image for {crs.name}/{module_name}",
        )
        _must_run(
            ["docker", "push", tag],
            f"Failed to push runtime image for {crs.name}/{module_name}",
        )
        return tag

    def _upload_run_inputs(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        build_id: str,
        sanitizer: str,
        pov_files: list[Path],
        diff_path: Optional[Path],
        seed_dir: Optional[Path],
        bug_candidate: Optional[Path],
        timeout_seconds: Optional[int],
        deadline_epoch_seconds: Optional[float],
        runner_images: dict[str, str],
        checkpoint_upload_url: str,
        final_upload_url: str,
        status_upload_url: str,
        acr_auth: AzureAcrAuth,
        run_inputs_blob: str,
        resume_archive_url: Optional[str] = None,
        sas_expires_at: Optional[datetime] = None,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="osscrs-azure-payload-") as tmp:
            payload_root = Path(tmp) / "payload"
            input_cache = self._prepare_input_cache(
                shape=shape,
                target=target,
                work_dir=work_dir,
                build_id=build_id,
                sanitizer=sanitizer,
                runner_image=json.dumps(runner_images, sort_keys=True),
                sas_expires_at=sas_expires_at,
            )
            rebuild_cache_url = None
            if resume_archive_url and resume_archive_url and input_cache:
                rebuild_cache_url = self._prepare_rebuild_cache(
                    resume_archive_url=resume_archive_url,
                    cache_key=input_cache.cache_key,
                    sas_expires_at=sas_expires_at,
                )
            self._prepare_run_payload(
                staging_dir=payload_root,
                shape=shape,
                target=target,
                work_dir=work_dir,
                run_id=run_id,
                build_id=build_id,
                sanitizer=sanitizer,
                pov_files=pov_files,
                diff_path=diff_path,
                seed_dir=seed_dir,
                bug_candidate=bug_candidate,
                timeout_seconds=timeout_seconds,
                deadline_epoch_seconds=deadline_epoch_seconds,
                runner_images=runner_images,
                checkpoint_upload_url=checkpoint_upload_url,
                final_upload_url=final_upload_url,
                status_upload_url=status_upload_url,
                acr_auth=acr_auth,
                resume_archive_url=resume_archive_url,
                input_cache=input_cache,
                rebuild_cache_url=rebuild_cache_url,
            )

            tar_path = Path(tmp) / "inputs.tgz"
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(payload_root, arcname="payload")

            _must_run(
                [
                    "az",
                    "storage",
                    "blob",
                    "upload",
                    "--account-name",
                    self.config.storage_account,
                    "--account-key",
                    self._require_storage_key(),
                    "--container-name",
                    self.config.storage_container,
                    "--name",
                    run_inputs_blob,
                    "--file",
                    str(tar_path),
                    "--overwrite",
                    "true",
                    "--type",
                    "block",
                ],
                "Failed to upload Azure Spot VM inputs",
                redact_values=[self._require_storage_key()],
            )
        return self._create_blob_sas_url(
            run_inputs_blob, permissions="r", expires_at=sas_expires_at
        )

    def _prepare_run_payload(
        self,
        *,
        staging_dir: Path,
        shape: AzureRuntimeShape,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        build_id: str,
        sanitizer: str,
        pov_files: list[Path],
        diff_path: Optional[Path],
        seed_dir: Optional[Path],
        bug_candidate: Optional[Path],
        timeout_seconds: Optional[int],
        deadline_epoch_seconds: Optional[float] = None,
        checkpoint_upload_url: str,
        final_upload_url: str,
        status_upload_url: str,
        acr_auth: AzureAcrAuth,
        runner_images: dict[str, str],
        resume_archive_url: Optional[str] = None,
        input_cache: Optional[AzureInputCachePlan] = None,
        rebuild_cache_url: Optional[str] = None,
    ) -> None:
        build_out_root = staging_dir / "build_out"
        fetch_root = staging_dir / "fetch"
        fuzz_proj_root = staging_dir / "fuzz_proj"
        target_source_root = staging_dir / "target_source"
        runtime_root = staging_dir / "runtime"

        build_out_root.mkdir(parents=True, exist_ok=True)
        fetch_root.mkdir(parents=True, exist_ok=True)
        fuzz_proj_root.mkdir(parents=True, exist_ok=True)
        target_source_root.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)

        build_out_dir = work_dir.get_build_output_dir(
            shape.crs.name, target, build_id, sanitizer, create=False
        )
        if not build_out_dir.exists():
            raise RuntimeError(
                f"Missing BUILD_OUT_DIR for CRS '{shape.crs.name}': {build_out_dir}"
            )
        if input_cache is None:
            _copy_tree_contents(build_out_dir, build_out_root / shape.crs.name)
        self._stage_fetch_inputs(
            fetch_dir=fetch_root,
            pov_files=pov_files,
            diff_path=diff_path,
            seed_dir=seed_dir,
            bug_candidate=bug_candidate,
        )

        target_source_path = (
            target.repo_path
            if target._has_repo
            else work_dir.get_target_source_dir(
                target, build_id, sanitizer, create=False
            )
        )
        if not target_source_path.exists():
            raise RuntimeError(
                "Missing target source directory required for Azure run: "
                f"{target_source_path}"
            )
        if input_cache is None:
            _copy_tree_contents(target.proj_path, fuzz_proj_root)
            _copy_tree_contents(target_source_path, target_source_root)

        llm_api_url, llm_api_key = self._external_llm_credentials()
        if llm_api_key:
            (runtime_root / "oss_crs_llm_api_key").write_text(llm_api_key)

        compose_yaml = self._render_remote_compose(
            shape=shape,
            target=target,
            run_id=run_id,
            sanitizer=sanitizer,
            runner_images=runner_images,
        )
        (runtime_root / "docker-compose.yaml").write_text(compose_yaml)
        (runtime_root / "run.sh").write_text(
            self._build_remote_run_script(
                shape=shape,
                timeout_seconds=timeout_seconds,
                deadline_epoch_seconds=deadline_epoch_seconds,
                checkpoint_upload_url=checkpoint_upload_url,
                final_upload_url=final_upload_url,
                status_upload_url=status_upload_url,
                acr_auth=acr_auth,
                resume_archive_url=resume_archive_url,
                input_cache=input_cache,
                rebuild_cache_url=rebuild_cache_url,
            )
        )
        target_env = target.get_target_env()
        metadata = {
            "crs_name": shape.crs.name,
            "module_name": shape.module_name,
            "run_id": run_id,
            "build_id": build_id,
            "sanitizer": sanitizer,
            "runner_image": runner_images.get(shape.module_name),
            "runtime_images": runner_images,
            "sync_interval_seconds": self.config.sync_interval_seconds,
            "resumed": bool(resume_archive_url),
            "target_name": target_env.get("name"),
            "target_engine": target.engine,
            "target_harness": target.target_harness,
            "target_architecture": target_env.get("architecture"),
            "input_cache_key": input_cache.cache_key if input_cache else None,
            "input_cache_blobs": (
                {
                    "build_out": input_cache.build_out_blob,
                    "fuzz_proj": input_cache.fuzz_proj_blob,
                    "target_source": input_cache.target_source_blob,
                }
                if input_cache
                else None
            ),
            "rebuild_cache_url_present": bool(rebuild_cache_url),
            "llm_mode": self.llm.mode if self.llm and self.llm.exists() else "disabled",
            "llm_api_url_present": bool(llm_api_url),
        }
        (runtime_root / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )

    def _prepare_input_cache(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        work_dir: "WorkDir",
        build_id: str,
        sanitizer: str,
        runner_image: str,
        sas_expires_at: Optional[datetime],
    ) -> Optional[AzureInputCachePlan]:
        if not self.config.input_cache_enabled:
            return None

        build_out_dir = work_dir.get_build_output_dir(
            shape.crs.name, target, build_id, sanitizer, create=False
        )
        if not build_out_dir.exists():
            raise RuntimeError(
                f"Missing BUILD_OUT_DIR for CRS '{shape.crs.name}': {build_out_dir}"
            )
        target_source_path = (
            target.repo_path
            if target._has_repo
            else work_dir.get_target_source_dir(
                target, build_id, sanitizer, create=False
            )
        )
        if not target_source_path.exists():
            raise RuntimeError(
                "Missing target source directory required for Azure run: "
                f"{target_source_path}"
            )

        cache_key = self._input_cache_key(
            shape=shape,
            target=target,
            build_id=build_id,
            sanitizer=sanitizer,
            runner_image=runner_image,
            build_out_dir=build_out_dir,
            fuzz_proj_dir=target.proj_path,
            target_source_dir=target_source_path,
        )
        build_out_blob = f"cache/inputs/{cache_key}/build_out.tgz"
        fuzz_proj_blob = f"cache/inputs/{cache_key}/fuzz_proj.tgz"
        target_source_blob = f"cache/inputs/{cache_key}/target_source.tgz"

        self._ensure_directory_cache_blob(build_out_dir, build_out_blob)
        self._ensure_directory_cache_blob(target.proj_path, fuzz_proj_blob)
        self._ensure_directory_cache_blob(target_source_path, target_source_blob)

        return AzureInputCachePlan(
            cache_key=cache_key,
            build_out_blob=build_out_blob,
            fuzz_proj_blob=fuzz_proj_blob,
            target_source_blob=target_source_blob,
            build_out_url=self._create_blob_sas_url(
                build_out_blob, permissions="r", expires_at=sas_expires_at
            ),
            fuzz_proj_url=self._create_blob_sas_url(
                fuzz_proj_blob, permissions="r", expires_at=sas_expires_at
            ),
            target_source_url=self._create_blob_sas_url(
                target_source_blob, permissions="r", expires_at=sas_expires_at
            ),
        )

    def _input_cache_key(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        build_id: str,
        sanitizer: str,
        runner_image: str,
        build_out_dir: Path,
        fuzz_proj_dir: Path,
        target_source_dir: Path,
    ) -> str:
        target_env = target.get_target_env()
        payload = {
            "crs_name": shape.crs.name,
            "module_name": shape.module_name,
            "crs_version": shape.crs.config.version,
            "runner_image": runner_image,
            "target_name": target_env.get("name"),
            "target_engine": target.engine,
            "target_harness": target.target_harness,
            "target_architecture": target_env.get("architecture"),
            "build_id": build_id,
            "sanitizer": sanitizer,
            "build_out_hash": self._context_hash([build_out_dir]),
            "fuzz_proj_hash": self._context_hash([fuzz_proj_dir]),
            "target_source_hash": self._context_hash([target_source_dir]),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _ensure_directory_cache_blob(self, source_dir: Path, blob_name: str) -> None:
        if self._blob_exists(blob_name):
            _log(f"cache hit for {blob_name}")
            return

        _log(f"uploading cache artifact {blob_name}")
        with tempfile.NamedTemporaryFile(
            prefix="osscrs-cache-", suffix=".tgz", delete=False
        ) as handle:
            archive_path = Path(handle.name)
        try:
            self._create_directory_archive(source_dir, archive_path)
            _must_run(
                [
                    "az",
                    "storage",
                    "blob",
                    "upload",
                    "--account-name",
                    self.config.storage_account,
                    "--account-key",
                    self._require_storage_key(),
                    "--container-name",
                    self.config.storage_container,
                    "--name",
                    blob_name,
                    "--file",
                    str(archive_path),
                    "--overwrite",
                    "true",
                    "--type",
                    "block",
                ],
                f"Failed to upload Azure cache artifact {blob_name}",
                redact_values=[self._require_storage_key()],
            )
        finally:
            archive_path.unlink(missing_ok=True)

    @staticmethod
    def _create_directory_archive(source_dir: Path, archive_path: Path) -> None:
        with tarfile.open(archive_path, "w:gz") as tar:
            AzureSpotVmRunSubmitter._add_directory_to_archive(
                tar, source_dir, arcname="."
            )

    @staticmethod
    def _add_directory_to_archive(
        tar: tarfile.TarFile, source_dir: Path, *, arcname: str
    ) -> None:
        for item in sorted(source_dir.iterdir()):
            item_arcname = item.name if arcname == "." else f"{arcname}/{item.name}"
            tar.add(item, arcname=item_arcname, recursive=False)
            if item.is_symlink():
                continue
            if item.is_dir():
                AzureSpotVmRunSubmitter._add_directory_to_archive(
                    tar, item, arcname=item_arcname
                )

    def _prepare_rebuild_cache(
        self,
        *,
        resume_archive_url: str,
        cache_key: str,
        sas_expires_at: Optional[datetime],
    ) -> Optional[str]:
        if not self.config.rebuild_cache_enabled:
            return None

        # SAS query parameters change whenever credentials are renewed. The blob
        # path is the stable identity because result blobs are immutable once a
        # run id has been accepted.
        resume_blob_path = urllib.parse.unquote(
            urllib.parse.urlsplit(resume_archive_url).path
        )
        resume_id = hashlib.sha256(resume_blob_path.encode()).hexdigest()[:16]
        rebuild_blob = f"cache/rebuild/{cache_key}/{resume_id}.tgz"
        if self._blob_exists(rebuild_blob):
            return self._create_blob_sas_url(
                rebuild_blob, permissions="r", expires_at=sas_expires_at
            )

        with tempfile.TemporaryDirectory(prefix="osscrs-rebuild-cache-") as tmp:
            tmp_path = Path(tmp)
            resume_archive = tmp_path / "resume.tgz"
            extract_dir = tmp_path / "extract"
            rebuild_archive = tmp_path / "rebuild_out.tgz"
            _must_run(
                ["curl", "-sSf", resume_archive_url, "-o", str(resume_archive)],
                "Failed to download Azure resume archive for rebuild cache",
                redact_values=[resume_archive_url],
            )
            extract_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(resume_archive, "r:gz") as tar:
                tar.extractall(path=extract_dir, filter="data")
            rebuild_out = extract_dir / "rebuild_out"
            if not rebuild_out.is_dir():
                return None
            self._create_directory_archive(rebuild_out, rebuild_archive)
            _must_run(
                [
                    "az",
                    "storage",
                    "blob",
                    "upload",
                    "--account-name",
                    self.config.storage_account,
                    "--account-key",
                    self._require_storage_key(),
                    "--container-name",
                    self.config.storage_container,
                    "--name",
                    rebuild_blob,
                    "--file",
                    str(rebuild_archive),
                    "--overwrite",
                    "true",
                    "--type",
                    "block",
                ],
                f"Failed to upload Azure rebuild cache artifact {rebuild_blob}",
                redact_values=[self._require_storage_key()],
            )

        return self._create_blob_sas_url(
            rebuild_blob, permissions="r", expires_at=sas_expires_at
        )

    def _render_remote_compose(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        run_id: str,
        sanitizer: str,
        runner_images: dict[str, str],
    ) -> str:
        target_env = target.get_target_env()
        llm_api_url, llm_api_key = self._external_llm_credentials()
        compose: dict[str, Any] = {"services": {}}
        for module_name, module_config in shape.modules():
            env_plan = build_run_service_env(
                target_env=target_env,
                sanitizer=sanitizer,
                # The runner containers still execute the standard local libCRS
                # path; Azure only changes transport and orchestration around them.
                run_env_type="local",
                crs_name=shape.crs.name,
                module_name=module_name,
                run_id=run_id,
                cpuset=shape.crs.resource.cpuset if shape.crs.resource else "0-3",
                memory_limit=(
                    shape.crs.resource.memory if shape.crs.resource else "16G"
                ),
                module_additional_env=getattr(module_config, "additional_env", None),
                crs_additional_env=(
                    shape.crs.resource.additional_env if shape.crs.resource else None
                ),
                harness=target.target_harness,
                include_fetch_dir=True,
                llm_api_url=llm_api_url,
                llm_api_key=llm_api_key,
                scope=f"{shape.crs.name}:azure:{module_name}",
            )
            # The rendered compose runs on a remote Spot VM whose shell does not
            # carry the controller's launching environment, so docker-compose
            # cannot resolve ``${VAR}`` references in additional_env (e.g.
            # CLAUDE_CODE_OAUTH_TOKEN) — they would render blank on the VM. Bake
            # controller-side values in here before upload. (The external LiteLLM
            # key is deliberately handled out-of-band via a mounted docker secret
            # and is never an additional_env reference, so it is unaffected.)
            resolved_env: dict[str, str] = {}
            unresolved_refs: set[str] = set()
            for env_key, env_value in env_plan.effective_env.items():
                resolved_value, missing = resolve_env_references(env_value, os.environ)
                resolved_env[env_key] = resolved_value
                unresolved_refs |= missing
            if unresolved_refs:
                raise RuntimeError(
                    "Azure run environment references are not set on the "
                    "controller: " + ", ".join(sorted(unresolved_refs))
                )
            service: dict[str, Any] = {
                "image": runner_images[module_name],
                "privileged": True,
                "environment": resolved_env,
                "mem_limit": (
                    shape.crs.resource.memory if shape.crs.resource else "16G"
                ),
                "cpuset": (shape.crs.resource.cpuset if shape.crs.resource else "0-3"),
                "volumes": [
                    f"/opt/oss-crs/payload/build_out/{shape.crs.name}:/OSS_CRS_BUILD_OUT_DIR:ro",
                    "/opt/oss-crs/runtime/rebuild_out:/OSS_CRS_REBUILD_OUT_DIR:rw",
                    f"/opt/oss-crs/runtime/submit/{shape.crs.name}:/OSS_CRS_SUBMIT_DIR:rw",
                    f"/opt/oss-crs/runtime/shared/{shape.crs.name}:/OSS_CRS_SHARED_DIR:rw",
                    f"/opt/oss-crs/runtime/log/{shape.crs.name}:/OSS_CRS_LOG_DIR:rw",
                    "/opt/oss-crs/payload/fetch:/OSS_CRS_FETCH_DIR:ro",
                    "/opt/oss-crs/payload/fuzz_proj:/OSS_CRS_FUZZ_PROJ:ro",
                    "/opt/oss-crs/payload/target_source:/OSS_CRS_TARGET_SOURCE:ro",
                ],
                "networks": {
                    "default": {
                        "aliases": [
                            module_name,
                            f"{module_name}.{shape.crs.name}",
                        ]
                    }
                },
            }
            compose["services"][f"{shape.crs.name}_{module_name}"] = service
        if llm_api_key:
            for service in compose["services"].values():
                service["secrets"] = [
                    {
                        "source": "oss_crs_llm_api_key",
                        "target": "oss_crs_llm_api_key",
                    }
                ]
            compose["secrets"] = {
                "oss_crs_llm_api_key": {
                    "file": "/opt/oss-crs/payload/runtime/oss_crs_llm_api_key"
                }
            }
        return yaml.safe_dump(compose, sort_keys=False)

    def _external_llm_credentials(self) -> tuple[Optional[str], Optional[str]]:
        if self.llm is None or not self.llm.exists():
            return None, None
        if self.llm.mode != "external":
            return None, None
        llm_api_url = self.llm.get_crs_api_url()
        llm_api_key = self.llm.get_crs_api_key()
        if not llm_api_url:
            raise RuntimeError("External LiteLLM URL is empty")
        if not llm_api_key:
            raise RuntimeError("External LiteLLM API key is empty")
        return llm_api_url, llm_api_key

    def _build_remote_run_script(
        self,
        *,
        shape: AzureRuntimeShape,
        timeout_seconds: Optional[int],
        deadline_epoch_seconds: Optional[float] = None,
        checkpoint_upload_url: str,
        final_upload_url: str,
        status_upload_url: str,
        acr_auth: AzureAcrAuth,
        resume_archive_url: Optional[str] = None,
        input_cache: Optional[AzureInputCachePlan] = None,
        rebuild_cache_url: Optional[str] = None,
    ) -> str:
        compose_version = os.environ.get(
            "OSS_CRS_AZURE_DOCKER_COMPOSE_VERSION", "v2.29.7"
        )
        if deadline_epoch_seconds is None and timeout_seconds is not None:
            deadline_epoch_seconds = time.time() + timeout_seconds
        deadline_epoch = (
            0 if deadline_epoch_seconds is None else int(deadline_epoch_seconds)
        )
        return f"""#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
systemctl enable --now docker >/dev/null 2>&1 || true

mkdir -p /opt/oss-crs/runtime/submit/{shape.crs.name}
mkdir -p /opt/oss-crs/runtime/shared/{shape.crs.name}
mkdir -p /opt/oss-crs/runtime/log/{shape.crs.name}
mkdir -p /opt/oss-crs/runtime/exchange
mkdir -p /opt/oss-crs/runtime/rebuild_out
mkdir -p /opt/oss-crs/runtime/run-logs
mkdir -p /opt/oss-crs/checkpoint-snapshot

checkpoint_blob_url={json.dumps(checkpoint_upload_url)}
final_blob_url={json.dumps(final_upload_url)}
status_blob_url={json.dumps(status_upload_url)}
cancel_marker=/opt/oss-crs/runtime/cancel-requested
resume_archive_url={json.dumps(resume_archive_url or "")}
cache_build_out_url={json.dumps(input_cache.build_out_url if input_cache else "")}
cache_fuzz_proj_url={json.dumps(input_cache.fuzz_proj_url if input_cache else "")}
cache_target_source_url={json.dumps(input_cache.target_source_url if input_cache else "")}
rebuild_cache_url={json.dumps(rebuild_cache_url or "")}
sync_interval={int(self.config.sync_interval_seconds)}
deadline_epoch={deadline_epoch}
scheduled_events_url="http://169.254.169.254/metadata/scheduledevents?api-version=2020-07-01"
scheduled_events_poll_interval=5

write_status() {{
  local state="$1"
  local exit_code="${{2:-}}"
  cat > /opt/oss-crs/runtime/run-status.json <<EOF
{{
  "state": "${{state}}",
  "exit_code": "${{exit_code}}",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}}
EOF
  curl -sSf -X PUT -H "x-ms-blob-type: BlockBlob" -H "Content-Type: application/json" \
    --data-binary @/opt/oss-crs/runtime/run-status.json \
    "${{status_blob_url}}" >/dev/null
}}

on_error() {{
  local rc="$1"
  write_status failed "${{rc}}" || true
  exit "${{rc}}"
}}
trap 'on_error $?' ERR

install_compose_binary() {{
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -sSfL \
    "https://github.com/docker/compose/releases/download/{compose_version}/docker-compose-linux-x86_64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
}}

install_compose() {{
  if docker compose version >/dev/null 2>&1; then
    return 0
  fi

  install_compose_binary
  docker compose version >/dev/null 2>&1
}}

resolve_compose() {{
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker compose)
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_BIN=(docker-compose)
    return 0
  fi
  return 1
}}

write_status bootstrapping || true
install_compose
resolve_compose

download_cache_archive() {{
  local url="$1"
  local destination="$2"
  local label="$3"
  if [[ -z "${{url}}" ]]; then
    return 0
  fi
  mkdir -p "${{destination}}"
  local archive="/tmp/oss-crs-cache-${{label}}.tgz"
  curl -sSf "${{url}}" -o "${{archive}}"
  tar xzf "${{archive}}" -C "${{destination}}"
}}

download_cache_archive "${{cache_build_out_url}}" "/opt/oss-crs/payload/build_out/{shape.crs.name}" "build-out"
download_cache_archive "${{cache_fuzz_proj_url}}" "/opt/oss-crs/payload/fuzz_proj" "fuzz-proj"
download_cache_archive "${{cache_target_source_url}}" "/opt/oss-crs/payload/target_source" "target-source"
download_cache_archive "${{rebuild_cache_url}}" "/opt/oss-crs/runtime/rebuild_out" "rebuild-out"

if [[ -n "${{resume_archive_url}}" ]]; then
  mkdir -p /opt/oss-crs/resume /opt/oss-crs/payload/fetch/seeds
  curl -sSf "${{resume_archive_url}}" -o /tmp/oss-crs-resume.tgz
  tar xzf /tmp/oss-crs-resume.tgz -C /opt/oss-crs/resume

  copy_seed_dir() {{
    local seed_dir="$1"
    local destination="$2"
    if [[ -d "${{seed_dir}}" ]]; then
      mkdir -p "${{destination}}"
      cp -a "${{seed_dir}}"/. "${{destination}}"/
    fi
  }}

  copy_seed_dir /opt/oss-crs/resume/submit/{shape.crs.name}/seeds \
    /opt/oss-crs/payload/fetch/seeds
  for seed_dir in /opt/oss-crs/resume/submit/{shape.crs.name}/*/seeds; do
    [[ -d "${{seed_dir}}" ]] || continue
    harness="$(basename "$(dirname "${{seed_dir}}")")"
    copy_seed_dir "${{seed_dir}}" "/opt/oss-crs/payload/fetch/seeds/${{harness}}"
  done
  copy_seed_dir /opt/oss-crs/resume/exchange/seeds \
    /opt/oss-crs/payload/fetch/seeds
  for seed_dir in /opt/oss-crs/resume/exchange/seeds/*; do
    if [[ -d "${{seed_dir}}" ]]; then
      harness="$(basename "${{seed_dir}}")"
      copy_seed_dir "${{seed_dir}}" "/opt/oss-crs/payload/fetch/seeds/${{harness}}"
    fi
  done
fi

create_seed_corpus_archives() {{
  local seed_root=/opt/oss-crs/payload/fetch/seeds
  local build_root=/opt/oss-crs/payload/build_out/{shape.crs.name}/uniafl/build
  if [[ ! -d "${{seed_root}}" || ! -d "${{build_root}}" ]]; then
    return 0
  fi

  python3 - "${{seed_root}}" "${{build_root}}" /opt/oss-crs/payload/runtime/metadata.json <<'PY'
import json
import sys
import zipfile
from pathlib import Path

seed_root = Path(sys.argv[1])
build_root = Path(sys.argv[2])
metadata = json.loads(Path(sys.argv[3]).read_text())
target_harness = metadata.get("target_harness")


def write_zip(harness: str, seed_dir: Path) -> None:
    harness_bin = build_root / harness
    if not harness_bin.exists():
        return
    seeds = sorted(path for path in seed_dir.iterdir() if path.is_file())
    if not seeds:
        return
    archive = Path(str(harness_bin) + "_seed_corpus.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for seed in seeds:
            zf.write(seed, arcname=seed.name)


for harness_dir in sorted(path for path in seed_root.iterdir() if path.is_dir()):
    write_zip(harness_dir.name, harness_dir)

flat_seeds = [path for path in seed_root.iterdir() if path.is_file()]
if target_harness and flat_seeds:
    write_zip(str(target_harness), seed_root)
PY
}}

create_seed_corpus_archives

printf '%s' {json.dumps(acr_auth.access_token)} | docker login {acr_auth.login_server} --username {acr_auth.username} --password-stdin
if (( deadline_epoch == 0 || $(date +%s) < deadline_epoch )); then
  write_status pulling-image || true
  "${{COMPOSE_BIN[@]}}" -f /opt/oss-crs/payload/runtime/docker-compose.yaml pull
else
  echo "Run deadline expired before image pull; skipping remote execution."
fi

archive_snapshot() {{
  local destination="$1"
  local snapshot=/opt/oss-crs/checkpoint-snapshot
  for entry in submit shared log exchange run-logs rebuild_out; do
    mkdir -p "${{snapshot}}/${{entry}}"
    rsync -a --delete "/opt/oss-crs/runtime/${{entry}}/" "${{snapshot}}/${{entry}}/"
  done
  cp /opt/oss-crs/runtime/run-status.json "${{snapshot}}/run-status.json"
  cp /opt/oss-crs/payload/runtime/metadata.json "${{snapshot}}/metadata.json"
  tar czf "${{destination}}" \
    -C "${{snapshot}}" \
    submit shared log exchange run-logs rebuild_out run-status.json metadata.json
}}

upload_checkpoint() (
  # Periodic and eviction-triggered checkpoints may overlap.
  flock -x 9
  archive_snapshot /tmp/oss-crs-checkpoint.tgz
  curl -sSf -X PUT -H "x-ms-blob-type: BlockBlob" \
    --upload-file /tmp/oss-crs-checkpoint.tgz \
    "${{checkpoint_blob_url}}" >/dev/null
) 9>/tmp/oss-crs-checkpoint.lock

monitor_scheduled_events() {{
  local scheduled_events
  while true; do
    scheduled_events="$(
      curl -sS --max-time 2 -H "Metadata:true" "${{scheduled_events_url}}" || true
    )"
    if printf '%s' "${{scheduled_events}}" | grep -Eq \
      '"EventType"[[:space:]]*:[[:space:]]*"(Preempt|Terminate|Redeploy|Reboot|Freeze)"'; then
      printf '%s\n' "${{scheduled_events}}" \
        > /opt/oss-crs/runtime/run-logs/scheduled-events.json
      write_status eviction-notice || true
      upload_checkpoint || true
      return 0
    fi
    sleep "${{scheduled_events_poll_interval}}"
  done
}}

write_status running

monitor_scheduled_events &
scheduled_events_pid=$!

(
  while true; do
    sleep "${{sync_interval}}"
    if [[ ! -e /opt/oss-crs/runtime/run-logs/scheduled-events.json ]]; then
      write_status running || true
    fi
    upload_checkpoint || true
  done
) &
sync_pid=$!

run_compose() {{
  if [[ -e "${{cancel_marker}}" ]]; then
    return 130
  fi
  local compose_cmd=(
    "${{COMPOSE_BIN[@]}}"
    -f /opt/oss-crs/payload/runtime/docker-compose.yaml
    up --abort-on-container-exit
    --exit-code-from {shape.crs.name}_{shape.module_name}
  )
  if (( deadline_epoch > 0 )); then
    local remaining=$((deadline_epoch - $(date +%s)))
    if (( remaining <= 0 )); then
      return 124
    fi
    timeout --foreground "${{remaining}}s" "${{compose_cmd[@]}}"
    return
  fi
  "${{compose_cmd[@]}}"
}}

trap - ERR
set +e
run_compose 2>&1 | tee /opt/oss-crs/runtime/run-logs/docker-compose.log
compose_rc=${{PIPESTATUS[0]}}
set -e
trap 'on_error $?' ERR

if [[ -e "${{cancel_marker}}" ]]; then
  compose_rc=130
  final_state=cancelled
else
  final_state=completed
fi

kill "${{sync_pid}}" >/dev/null 2>&1 || true
wait "${{sync_pid}}" >/dev/null 2>&1 || true
kill "${{scheduled_events_pid}}" >/dev/null 2>&1 || true
wait "${{scheduled_events_pid}}" >/dev/null 2>&1 || true

write_status "${{final_state}}" "${{compose_rc}}" || true
upload_checkpoint || true
archive_snapshot /tmp/oss-crs-final.tgz
curl -sSf -X PUT -H "x-ms-blob-type: BlockBlob" \
  --upload-file /tmp/oss-crs-final.tgz \
  "${{final_blob_url}}" >/dev/null

exit "${{compose_rc}}"
"""

    def _stage_fetch_inputs(
        self,
        *,
        fetch_dir: Path,
        pov_files: list[Path],
        diff_path: Optional[Path],
        seed_dir: Optional[Path],
        bug_candidate: Optional[Path],
    ) -> None:
        if pov_files:
            pov_dir = fetch_dir / "povs"
            pov_dir.mkdir(parents=True, exist_ok=True)
            for src in pov_files:
                shutil.copy2(src, pov_dir / src.name)

        if diff_path is not None:
            diff_dir = fetch_dir / "diffs"
            diff_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(diff_path, diff_dir / "ref.diff")

        if seed_dir is not None:
            staged_seed_dir = fetch_dir / "seeds"
            staged_seed_dir.mkdir(parents=True, exist_ok=True)
            for src in seed_dir.iterdir():
                if src.is_file():
                    shutil.copy2(src, staged_seed_dir / src.name)

        if bug_candidate is not None:
            bc_dir = fetch_dir / "bug-candidates"
            bc_dir.mkdir(parents=True, exist_ok=True)
            if bug_candidate.is_file():
                shutil.copy2(bug_candidate, bc_dir / bug_candidate.name)
            elif bug_candidate.is_dir():
                for src in bug_candidate.rglob("*"):
                    if not src.is_file():
                        continue
                    dst = bc_dir / src.relative_to(bug_candidate)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    @staticmethod
    def _required_vcpus(shape: AzureRuntimeShape) -> int:
        cpuset = shape.crs.resource.cpuset if shape.crs.resource else "0-3"
        return max(parse_cpuset(cpuset)) + 1

    def _vm_size_vcpus(self, vm_size: str) -> int:
        raw = _must_run(
            [
                "az",
                "vm",
                "list-sizes",
                "--location",
                self.config.location,
                "--query",
                f"[?name=='{vm_size}'].numberOfCores | [0]",
                "-o",
                "tsv",
            ],
            f"Failed to determine vCPU count for Azure VM size {vm_size}",
        )
        try:
            return int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"Azure VM size {vm_size} returned invalid vCPU count: {raw!r}"
            ) from exc

    def _create_spot_vm(self, vm_names: AzureVmNames, required_vcpus: int) -> None:
        attempt_errors: list[str] = []
        zones: tuple[Optional[str], ...] = (
            tuple(self.config.vm_zones) if self.config.vm_zones else (None,)
        )
        for vm_size in self.config.vm_sizes:
            available_vcpus = self._vm_size_vcpus(vm_size)
            if available_vcpus < required_vcpus:
                attempt_errors.append(
                    f"Azure VM size {vm_size} has {available_vcpus} vCPUs, "
                    f"but configured cpuset requires CPU indexes below {required_vcpus}."
                )
                continue
            for zone in zones:
                try:
                    _log(
                        "creating Spot VM "
                        f"{vm_names.vm_name} with size={vm_size}"
                        + (f" zone={zone}" if zone else "")
                    )
                    _must_run(
                        self._build_vm_create_command(
                            vm_names,
                            vm_size=vm_size,
                            zone=zone,
                        ),
                        "Failed to create Azure Spot VM",
                    )
                    return
                except RuntimeError as exc:
                    rendered = str(exc)
                    attempt_errors.append(rendered)
                    if not self._is_retryable_vm_create_error(rendered):
                        raise
                    _log(
                        "Spot VM capacity unavailable for "
                        f"size={vm_size}"
                        + (f" zone={zone}" if zone else "")
                        + "; trying next candidate"
                    )
        raise RuntimeError("\n\n".join(attempt_errors))

    def _build_vm_create_command(
        self,
        vm_names: AzureVmNames,
        *,
        vm_size: str,
        zone: Optional[str],
    ) -> list[str]:
        cmd = [
            "az",
            "vm",
            "create",
            "--resource-group",
            self.config.resource_group,
            "--location",
            self.config.location,
            "--name",
            vm_names.vm_name,
            "--image",
            self.config.vm_image,
            "--size",
            vm_size,
            "--priority",
            "Spot",
            "--eviction-policy",
            "Delete",
            "--max-price",
            self.config.spot_max_price,
            "--admin-username",
            self.config.vm_admin_username,
            "--ssh-key-values",
            str(self.config.ssh_public_key_path),
            "--public-ip-address",
            vm_names.public_ip_name,
            "--public-ip-sku",
            "Standard",
            "--nsg",
            vm_names.nsg_name,
            "--nsg-rule",
            "SSH" if self.config.enable_ssh else "NONE",
            "--vnet-name",
            vm_names.vnet_name,
            "--subnet",
            vm_names.subnet_name,
            "--nic-delete-option",
            "Delete",
            "--os-disk-delete-option",
            "Delete",
            "--os-disk-size-gb",
            str(self.config.vm_os_disk_size_gb),
            "--os-disk-name",
            vm_names.os_disk_name,
            "-o",
            "json",
        ]
        if zone:
            cmd.extend(["--zone", zone])
        return cmd

    @staticmethod
    def _is_retryable_vm_create_error(message: str) -> bool:
        lowered = message.lower()
        return (
            "skunotavailable" in lowered
            or "capacity restrictions" in lowered
            or "overconstrainedzonalallocationrequest" in lowered
        )

    def _invoke_remote_runner(self, vm_names: AzureVmNames, payload_url: str) -> None:
        with tempfile.NamedTemporaryFile(
            prefix="osscrs-remote-bootstrap-",
            suffix=".sh",
            mode="w",
            delete=False,
        ) as handle:
            handle.write(self._build_run_command_script(payload_url))
            script_path = Path(handle.name)
        try:
            _must_run(
                [
                    "az",
                    "vm",
                    "run-command",
                    "invoke",
                    "--resource-group",
                    self.config.resource_group,
                    "--name",
                    vm_names.vm_name,
                    "--command-id",
                    "RunShellScript",
                    "--scripts",
                    f"@{script_path}",
                    "-o",
                    "json",
                ],
                "Failed to execute remote Azure Spot VM run command",
            )
        finally:
            script_path.unlink(missing_ok=True)

    @staticmethod
    def _build_remote_stop_script() -> str:
        return """#!/usr/bin/env bash
set -u

mkdir -p /opt/oss-crs/runtime
touch /opt/oss-crs/runtime/cancel-requested

compose_file=/opt/oss-crs/payload/runtime/docker-compose.yaml
if [[ ! -f "${compose_file}" ]]; then
  exit 0
fi

if docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  exit 0
fi

"${compose_cmd[@]}" -f "${compose_file}" stop --timeout 30 || true
"""

    def _request_remote_stop(
        self,
        vm_names: AzureVmNames,
        final_blob: str,
        grace_seconds: int = 180,
    ) -> None:
        """Ask a live remote run to finalize before controller cleanup."""
        if self._blob_exists(final_blob):
            return

        with tempfile.NamedTemporaryFile(
            prefix="osscrs-remote-stop-",
            suffix=".sh",
            mode="w",
            delete=False,
        ) as handle:
            handle.write(self._build_remote_stop_script())
            script_path = Path(handle.name)
        try:
            _log("interrupt received; requesting graceful remote stop")
            _must_run(
                [
                    "az",
                    "vm",
                    "run-command",
                    "invoke",
                    "--resource-group",
                    self.config.resource_group,
                    "--name",
                    vm_names.vm_name,
                    "--command-id",
                    "RunShellScript",
                    "--scripts",
                    f"@{script_path}",
                    "-o",
                    "json",
                ],
                "Failed to request graceful stop from Azure Spot VM",
            )
        finally:
            script_path.unlink(missing_ok=True)

        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if self._blob_exists(final_blob):
                _log("remote run uploaded final results after interruption")
                return
            if not self._vm_exists(vm_names.vm_name):
                break
            time.sleep(min(self.config.sync_interval_seconds, 5))
        _log(
            "remote run did not upload final results during the graceful-stop "
            "window; restoring the latest checkpoint before cleanup"
        )

    def _wait_for_remote_completion(
        self,
        *,
        vm_names: AzureVmNames,
        final_blob: str,
        checkpoint_blob: str,
        status_blob: str,
        timeout_seconds: Optional[int],
        deadline_epoch_seconds: Optional[float] = None,
    ) -> None:
        if deadline_epoch_seconds is not None:
            deadline = (
                time.monotonic() + max(0.0, deadline_epoch_seconds - time.time()) + 1800
            )
        elif timeout_seconds is not None:
            deadline = time.monotonic() + timeout_seconds + 1800
        else:
            deadline = None
        last_query_error: Optional[AzureQueryError] = None
        while True:
            try:
                if self._blob_exists(final_blob):
                    return
                if not self._vm_exists(vm_names.vm_name):
                    if self._blob_exists(checkpoint_blob):
                        raise RuntimeError(
                            "Azure Spot VM stopped before uploading a final archive; "
                            "latest checkpoint will be restored if available."
                        )
                    raise RuntimeError(
                        "Azure Spot VM stopped before uploading any restorable archive."
                    )
                status = self._read_status_blob(status_blob)
                if status and status.get("state") == "failed":
                    raise RuntimeError(
                        "Azure Spot VM remote bootstrap failed"
                        + (
                            f" with exit code {status.get('exit_code')}"
                            if status.get("exit_code")
                            else ""
                        )
                    )
                if status and status.get("state") == "eviction-notice":
                    if self._blob_exists(checkpoint_blob):
                        raise RuntimeError(
                            "Azure Spot VM received an eviction notice; latest checkpoint "
                            "will be restored."
                        )
                last_query_error = None
            except AzureQueryError as exc:
                last_query_error = exc
                _log(f"Azure status query failed; retrying: {exc}")
            if deadline is not None and time.monotonic() > deadline:
                if last_query_error is not None:
                    raise last_query_error
                raise RuntimeError(
                    "Timed out waiting for Azure Spot VM to upload final results."
                )
            time.sleep(min(self.config.sync_interval_seconds, 30))

    def _build_run_command_script(self, payload_url: str) -> str:
        return f"""#!/usr/bin/env bash
set -euo pipefail

cloud-init status --wait || true
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io curl rsync
systemctl enable --now docker

mkdir -p /opt/oss-crs
curl -sSf {json.dumps(payload_url)} -o /opt/oss-crs/inputs.tgz
tar xzf /opt/oss-crs/inputs.tgz -C /opt/oss-crs
chmod +x /opt/oss-crs/payload/runtime/run.sh
mkdir -p /opt/oss-crs/runtime/run-logs
cat > /etc/systemd/system/oss-crs-run.service <<'UNIT'
[Unit]
Description=OSS-CRS Azure run
After=docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/opt/oss-crs
ExecStart=/opt/oss-crs/payload/runtime/run.sh
Restart=no
StandardOutput=append:/opt/oss-crs/runtime/run-logs/systemd-service.log
StandardError=append:/opt/oss-crs/runtime/run-logs/systemd-service.log

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start oss-crs-run.service
"""

    def cleanup_run(self, run_id: str) -> None:
        """Delete only the Azure resources deterministically owned by ``run_id``.

        This is safe to rerun after an interrupted controller cleanup. Result,
        checkpoint, input, and cache blobs are deliberately left untouched.
        """
        vm_names = AzureVmNames.for_run(self.config.vm_name_prefix, run_id)
        _log(f"cleaning resources for run {run_id} ({vm_names.vm_name})")
        self._cleanup_vm(vm_names)
        _log(f"cleanup complete for run {run_id}")

    def _build_cleanup_commands(self, vm_names: AzureVmNames) -> list[list[str]]:
        return [
            [
                "az",
                "vm",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.vm_name,
                "--yes",
            ],
            [
                "az",
                "disk",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.os_disk_name,
                "--yes",
            ],
            [
                "az",
                "network",
                "nic",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.nic_name,
            ],
            [
                "az",
                "network",
                "public-ip",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.public_ip_name,
            ],
            [
                "az",
                "network",
                "nsg",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.nsg_name,
            ],
            [
                "az",
                "network",
                "vnet",
                "delete",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_names.vnet_name,
            ],
        ]

    def _cleanup_vm(self, vm_names: AzureVmNames) -> None:
        commands = self._build_cleanup_commands(vm_names)
        failures: list[str] = []
        remaining = self._remaining_run_resources(vm_names.vm_name)
        if not remaining:
            return
        for attempt in range(3):
            failures = []
            for cmd in commands:
                rc, out, err = _run_command(cmd)
                if rc == 0 or self._is_azure_not_found_error(f"{out}\n{err}"):
                    continue
                failures.append(f"{' '.join(cmd)}\n{err.strip()}")
                _log(
                    "cleanup command failed; remaining resources will be "
                    f"checked before retrying: {' '.join(cmd)}"
                )

            remaining = self._remaining_run_resources(vm_names.vm_name)
            if not remaining:
                return
            if attempt < 2:
                _log(
                    "Azure resources are still being deleted; retrying cleanup: "
                    + ", ".join(remaining)
                )
                time.sleep(5)

        failures.append(
            "Azure resources still exist after cleanup: " + ", ".join(remaining)
        )
        raise RuntimeError("\n\n".join(failures))

    def _vm_exists(self, vm_name: str) -> bool:
        rc, out, err = _run_command(
            [
                "az",
                "vm",
                "show",
                "--resource-group",
                self.config.resource_group,
                "--name",
                vm_name,
            ]
        )
        if rc == 0:
            return True
        message = f"{out}\n{err}"
        if self._is_azure_not_found_error(message):
            return False
        raise AzureQueryError(
            f"Failed to determine whether Azure VM {vm_name!r} exists:\n{message.strip()}"
        )

    def _remaining_run_resources(self, vm_name: str) -> list[str]:
        rc, out, err = _run_command(
            [
                "az",
                "resource",
                "list",
                "--resource-group",
                self.config.resource_group,
                "--query",
                f"[?contains(name, '{vm_name}')].name",
                "-o",
                "tsv",
            ]
        )
        if rc != 0:
            raise AzureQueryError(
                f"Failed to verify Azure cleanup for {vm_name!r}:\n"
                f"{out.strip()}\n{err.strip()}"
            )
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _restore_results(
        self,
        *,
        shape: AzureRuntimeShape,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        sanitizer: str,
        final_blob: str,
        checkpoint_blob: str,
        status_blob: str,
    ) -> Optional[str]:
        with tempfile.TemporaryDirectory(prefix="osscrs-azure-results-") as tmp:
            tmp_path = Path(tmp)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)

            archive_kind: Optional[str] = None
            final_archive = tmp_path / "final.tgz"
            if self._download_blob_if_exists(final_blob, final_archive):
                archive_kind = "final"
                with tarfile.open(final_archive, "r:gz") as tar:
                    tar.extractall(path=extract_dir, filter="data")
            else:
                checkpoint_archive = tmp_path / "checkpoint.tgz"
                if self._download_blob_if_exists(checkpoint_blob, checkpoint_archive):
                    archive_kind = "checkpoint"
                    with tarfile.open(checkpoint_archive, "r:gz") as tar:
                        tar.extractall(path=extract_dir, filter="data")
                else:
                    self._download_status_blob_if_present(
                        status_blob, target, work_dir, run_id, sanitizer
                    )
                    return None

            archived_status = extract_dir / "run-status.json"
            if archive_kind == "final" and archived_status.is_file():
                self._restore_status_file(
                    archived_status, target, work_dir, run_id, sanitizer
                )
            else:
                status_downloaded = self._download_status_blob_if_present(
                    status_blob, target, work_dir, run_id, sanitizer
                )
                if not status_downloaded and archived_status.is_file():
                    self._restore_status_file(
                        archived_status, target, work_dir, run_id, sanitizer
                    )
            self._restore_results_tree(
                extract_dir=extract_dir,
                shape=shape,
                target=target,
                work_dir=work_dir,
                run_id=run_id,
                sanitizer=sanitizer,
            )
            return archive_kind

    def _download_status_blob_if_present(
        self,
        status_blob: str,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        sanitizer: str,
    ) -> bool:
        with tempfile.NamedTemporaryFile(
            prefix="osscrs-status-", suffix=".json", delete=False
        ) as handle:
            status_path = Path(handle.name)
        try:
            if not self._download_blob_if_exists(status_blob, status_path):
                return False
            self._restore_status_file(status_path, target, work_dir, run_id, sanitizer)
            return True
        finally:
            status_path.unlink(missing_ok=True)

    @staticmethod
    def _restore_status_file(
        status_path: Path,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        sanitizer: str,
    ) -> None:
        run_logs_dir = work_dir.get_run_logs_dir(target, run_id, sanitizer)
        run_logs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(status_path, run_logs_dir / "run-status.json")

    def _read_restored_exit_code(
        self,
        *,
        target: "Target",
        run_id: str,
        sanitizer: str,
        work_dir: "WorkDir",
    ) -> Optional[int]:
        status_path = (
            work_dir.get_run_logs_dir(target, run_id, sanitizer) / "run-status.json"
        )
        if not status_path.exists():
            return None
        try:
            payload = json.loads(status_path.read_text())
        except json.JSONDecodeError:
            return None
        raw_exit_code = payload.get("exit_code")
        if raw_exit_code in (None, ""):
            return None
        try:
            return int(raw_exit_code)
        except (TypeError, ValueError):
            return None

    def _restore_results_tree(
        self,
        *,
        extract_dir: Path,
        shape: AzureRuntimeShape,
        target: "Target",
        work_dir: "WorkDir",
        run_id: str,
        sanitizer: str,
    ) -> None:
        submit_src = extract_dir / "submit" / shape.crs.name
        if submit_src.is_dir():
            _copy_tree_contents(
                submit_src,
                work_dir.get_submit_dir(shape.crs.name, target, run_id, sanitizer),
            )

        shared_src = extract_dir / "shared" / shape.crs.name
        if shared_src.is_dir():
            _copy_tree_contents(
                shared_src,
                work_dir.get_shared_dir(shape.crs.name, target, run_id, sanitizer),
            )

        log_src = extract_dir / "log" / shape.crs.name
        if log_src.is_dir():
            _copy_tree_contents(
                log_src,
                work_dir.get_log_dir(shape.crs.name, target, run_id, sanitizer),
            )

        exchange_src = extract_dir / "exchange"
        if exchange_src.is_dir():
            _copy_tree_contents(
                exchange_src,
                work_dir.get_exchange_dir(target, run_id, sanitizer),
            )

        rebuild_out_src = extract_dir / "rebuild_out"
        if rebuild_out_src.is_dir():
            _copy_tree_contents(
                rebuild_out_src,
                work_dir.get_rebuild_out_dir(shape.crs.name, target, run_id, sanitizer),
            )

        run_logs_src = extract_dir / "run-logs"
        if run_logs_src.is_dir():
            _copy_tree_contents(
                run_logs_src,
                work_dir.get_run_logs_dir(target, run_id, sanitizer),
            )

    def _download_blob_if_exists(self, blob_name: str, destination: Path) -> bool:
        rc, out, err = _run_command(
            [
                "az",
                "storage",
                "blob",
                "download",
                "--account-name",
                self.config.storage_account,
                "--account-key",
                self._require_storage_key(),
                "--container-name",
                self.config.storage_container,
                "--name",
                blob_name,
                "--file",
                str(destination),
                "--overwrite",
            ]
        )
        if rc == 0:
            return True
        message = f"{out}\n{err}"
        if self._is_azure_not_found_error(message):
            return False
        raise AzureQueryError(
            f"Failed to download Azure blob {blob_name!r}:\n{message.strip()}"
        )

    def _blob_exists(self, blob_name: str) -> bool:
        rc, out, err = _run_command(
            [
                "az",
                "storage",
                "blob",
                "exists",
                "--account-name",
                self.config.storage_account,
                "--account-key",
                self._require_storage_key(),
                "--container-name",
                self.config.storage_container,
                "--name",
                blob_name,
                "--query",
                "exists",
                "-o",
                "tsv",
            ]
        )
        if rc != 0:
            raise AzureQueryError(
                f"Failed to determine whether Azure blob {blob_name!r} exists:\n"
                f"{out.strip()}\n{err.strip()}"
            )
        value = out.strip().lower()
        if value not in {"true", "false"}:
            raise AzureQueryError(
                f"Azure blob existence query returned an invalid value for "
                f"{blob_name!r}: {out!r}"
            )
        return value == "true"

    @staticmethod
    def _is_azure_not_found_error(message: str) -> bool:
        lowered = message.lower()
        return any(
            marker in lowered
            for marker in (
                "blobnotfound",
                "resourcenotfound",
                "resource not found",
                "was not found",
                "could not be found",
            )
        )

    def _ensure_run_id_available(self, run_id: str, vm_names: AzureVmNames) -> None:
        collisions = [
            name
            for name in (
                f"run-inputs/{run_id}/inputs.tgz",
                f"results/{run_id}/checkpoint.tgz",
                f"results/{run_id}/final.tgz",
                f"results/{run_id}/run-status.json",
            )
            if self._blob_exists(name)
        ]
        if self._vm_exists(vm_names.vm_name):
            collisions.append(f"VM:{vm_names.vm_name}")
        if collisions:
            raise RuntimeError(
                "Azure run id already has live resources or blobs; choose a new "
                f"--run-id. Existing: {', '.join(collisions)}"
            )

    def _read_status_blob(self, status_blob: str) -> Optional[dict[str, Any]]:
        with tempfile.NamedTemporaryFile(
            prefix="osscrs-status-poll-", suffix=".json", delete=False
        ) as handle:
            status_path = Path(handle.name)
        try:
            if not self._download_blob_if_exists(status_blob, status_path):
                return None
            return json.loads(status_path.read_text())
        except json.JSONDecodeError:
            return None
        finally:
            status_path.unlink(missing_ok=True)

    def _resolve_resume_blob(self, run_id: str) -> str:
        candidates = []
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
            candidates.append(run_id)
        try:
            normalized = _normalize_resume_run_id(run_id)
            if normalized not in candidates:
                candidates.append(normalized)
        except ValueError:
            pass
        for candidate in candidates:
            for name in (
                f"results/{candidate}/final.tgz",
                f"results/{candidate}/checkpoint.tgz",
            ):
                if self._blob_exists(name):
                    return name
        raise RuntimeError(
            f"Cannot resume Azure run {run_id!r}: no final or checkpoint archive exists."
        )

    def _validate_resume_blob_metadata(
        self,
        *,
        resume_blob: str,
        shape: AzureRuntimeShape,
        target: "Target",
        build_id: str,
        sanitizer: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="osscrs-azure-resume-meta-") as tmp:
            archive_path = Path(tmp) / "resume.tgz"
            if not self._download_blob_if_exists(resume_blob, archive_path):
                raise RuntimeError(
                    f"Cannot validate Azure resume archive metadata: {resume_blob}"
                )
            with tarfile.open(archive_path, "r:gz") as tar:
                try:
                    metadata_file = tar.extractfile("metadata.json")
                except KeyError:
                    _log(
                        "resume archive has no metadata.json; compatibility could "
                        "not be validated"
                    )
                    return
                if metadata_file is None:
                    return
                metadata = json.loads(metadata_file.read().decode())

        expected = {
            "crs_name": shape.crs.name,
            "module_name": shape.module_name,
            "build_id": build_id,
            "sanitizer": sanitizer,
            "target_engine": target.engine,
            "target_harness": target.target_harness,
        }
        target_name = target.get_target_env().get("name")
        if target_name:
            expected["target_name"] = target_name

        mismatches = [
            f"{key}: previous={metadata.get(key)!r}, current={value!r}"
            for key, value in expected.items()
            if metadata.get(key) is not None and metadata.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(
                "Cannot resume Azure run because the previous archive metadata "
                "does not match this run: " + "; ".join(mismatches)
            )

    def _delete_blob_if_exists(self, blob_name: str) -> None:
        try:
            rc, _out, err = _run_command(
                [
                    "az",
                    "storage",
                    "blob",
                    "delete",
                    "--account-name",
                    self.config.storage_account,
                    "--account-key",
                    self._require_storage_key(),
                    "--container-name",
                    self.config.storage_container,
                    "--name",
                    blob_name,
                ]
            )
            if rc != 0:
                _log(f"failed to delete transient blob {blob_name}: {err.strip()}")
        except Exception as exc:
            _log(f"failed to delete transient blob {blob_name}: {exc}")

    @staticmethod
    def _sas_expiry(timeout_seconds: Optional[int]) -> datetime:
        if timeout_seconds is None:
            return datetime.now(timezone.utc) + timedelta(days=7)
        return datetime.now(timezone.utc) + max(
            timedelta(hours=12), timedelta(seconds=timeout_seconds, hours=6)
        )

    def _create_blob_sas_url(
        self,
        blob_name: str,
        permissions: str = "r",
        *,
        expires_at: Optional[datetime] = None,
    ) -> str:
        expiry = (expires_at or self._sas_expiry(None)).strftime("%Y-%m-%dT%H:%MZ")
        sas = _must_run(
            [
                "az",
                "storage",
                "blob",
                "generate-sas",
                "--account-name",
                self.config.storage_account,
                "--account-key",
                self._require_storage_key(),
                "--container-name",
                self.config.storage_container,
                "--name",
                blob_name,
                "--permissions",
                permissions,
                "--expiry",
                expiry,
                "--https-only",
                "-o",
                "tsv",
            ],
            f"Failed to create SAS for {blob_name}",
            redact_values=[self._require_storage_key()],
        )
        encoded_name = urllib.parse.quote(blob_name, safe="/")
        return (
            f"https://{self.config.storage_account}.blob.core.windows.net/"
            f"{self.config.storage_container}/{encoded_name}?{sas}"
        )

    def _require_storage_key(self) -> str:
        if not self._storage_account_key:
            raise RuntimeError("Azure storage account key has not been initialized")
        return self._storage_account_key
