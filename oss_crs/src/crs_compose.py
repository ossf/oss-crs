import shutil
import subprocess
import re
import json
import os
import hashlib
from pathlib import Path
from typing import Optional
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .llm import LLM
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target
from .templates import renderer
from .utils import TmpDockerCompose, normalize_run_id, generate_run_id, rm_with_docker, log_success, log_warning, log_dim
from .workdir import WorkDir
from .cgroup import (
    check_cgroup_parent_available,
    create_run_cgroups,
    cleanup_cgroup,
)


class CRSCompose:
    @classmethod
    def from_yaml_file(
        cls, compose_file: Path, work_dir: Path, skip_crs_init: bool = False
    ) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir, skip_crs_init=skip_crs_init)

    def __init__(
        self, config: CRSComposeConfig, work_dir: Path, skip_crs_init: bool = False
    ):
        hash = config.md5_hash()
        self.config = config
        self.llm = LLM(self.config.llm_config)
        self.work_dir = WorkDir(work_dir / f"crs_compose/{hash}")
        self.crs_compose_env = CRSComposeEnv(self.config.run_env)
        self.crs_list = [
            CRS.from_crs_compose_entry(
                name,
                crs_cfg,
                self.work_dir,
                self.crs_compose_env,
                skip_init=skip_crs_init,
            )
            for name, crs_cfg in self.config.crs_entries.items()
        ]
        builder_count = sum(1 for crs in self.crs_list if crs.config.is_builder)
        if builder_count > 1:
            raise ValueError("At most one CRS entry with type 'builder' is allowed")
        self.deadline: Optional[float] = None

    @property
    def _builder_crs(self) -> Optional[CRS]:
        for crs in self.crs_list:
            if crs.config.is_builder:
                return crs
        return None

    @property
    def _any_needs_snapshot(self) -> bool:
        """Check if any CRS needs a snapshot (new-style or old-style builder)."""
        return (
            any(crs.config.has_snapshot for crs in self.crs_list)
            or self._builder_crs is not None
        )

    def _resolve_target_build_options(
        self,
        target: Target,
        *,
        sanitizer: str | None = None,
    ) -> tuple[str, str, str] | None:
        target_env = target.get_target_env()
        if sanitizer is not None:
            return (
                sanitizer,
                target_env.get("engine", Target.DEFAULT_ENGINE),
                target_env.get("architecture", Target.DEFAULT_ARCHITECTURE),
            )
        compose_sanitizers: set[str] = set()
        for crs in self.crs_list:
            if crs.resource and crs.resource.additional_env:
                v = crs.resource.additional_env.get("SANITIZER")
                if v:
                    compose_sanitizers.add(str(v))
        if len(compose_sanitizers) > 1:
            print(
                "Error: conflicting SANITIZER values in compose additional_env: "
                + ", ".join(sorted(compose_sanitizers))
            )
            return None
        compose_sanitizer = next(iter(compose_sanitizers), None)
        return (
            compose_sanitizer or target_env.get("sanitizer", Target.DEFAULT_SANITIZER),
            target_env.get("engine", Target.DEFAULT_ENGINE),
            target_env.get("architecture", Target.DEFAULT_ARCHITECTURE),
        )

    def resolve_effective_sanitizer(
        self, target: Target, sanitizer: str | None = None
    ) -> str | None:
        resolved = self._resolve_target_build_options(target, sanitizer=sanitizer)
        if resolved is None:
            return None
        return resolved[0]

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def get_latest_build_id(
        self,
        target: Target,
        sanitizer: str,
    ) -> str | None:
        """Find the latest build-id (by unix timestamp) for the given target and sanitizer."""
        latest_build_id = None
        latest_score = float("-inf")

        builds_dir = self.work_dir.get_builds_dir(sanitizer)
        if not builds_dir.exists():
            return None

        for build_id_dir in builds_dir.iterdir():
            if not build_id_dir.is_dir():
                continue
            build_id = build_id_dir.name
            # Check if any CRS has build output for this target
            for crs in self.crs_list:
                build_out_dir = self.work_dir.get_build_output_dir(
                    crs.name, target, build_id, sanitizer, create=False
                )
                if build_out_dir.exists():
                    # Prefer embedded unix timestamp; fall back to directory mtime.
                    match = re.search(r"\d{10}", build_id)
                    if match:
                        score = float(int(match.group()))
                    else:
                        score = build_id_dir.stat().st_mtime
                    if score > latest_score:
                        latest_score = score
                        latest_build_id = build_id
                    break  # Found at least one CRS with this build, no need to check others
        return latest_build_id

    def _get_build_metadata_path(
        self, target: Target, build_id: str, sanitizer: str, create_parent: bool = True
    ) -> Path:
        return self.work_dir.get_build_metadata_file(
            target, build_id, sanitizer, create_parent=create_parent
        )

    def _write_build_metadata(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
        diff_sha256: Optional[str],
        bug_candidate_sha256: Optional[str],
        input_sha256: Optional[str],
    ) -> None:
        metadata_path = self._get_build_metadata_path(
            target, build_id, sanitizer, create_parent=True
        )
        metadata = {
            "build_id": build_id,
            "sanitizer": sanitizer,
            "diff_sha256": diff_sha256,
            "bug_candidate_sha256": bug_candidate_sha256,
            "input_sha256": input_sha256,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _hash_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _hash_bug_candidate_input(
        self,
        bug_candidate: Optional[Path],
        bug_candidate_dir: Optional[Path],
    ) -> Optional[str]:
        if bug_candidate is not None:
            return self._hash_file(bug_candidate)
        if bug_candidate_dir is not None:
            hasher = hashlib.sha256()
            for root, dirs, files in os.walk(bug_candidate_dir):
                dirs.sort()
                for name in sorted(files):
                    f = Path(root) / name
                    rel = f.relative_to(bug_candidate_dir).as_posix()
                    file_hash = self._hash_file(f)
                    hasher.update(rel.encode())
                    hasher.update(b"\0")
                    hasher.update(file_hash.encode())
                    hasher.update(b"\n")
            return hasher.hexdigest()
        return None

    @staticmethod
    def _hash_directed_inputs(
        diff_sha256: Optional[str], bug_candidate_sha256: Optional[str]
    ) -> Optional[str]:
        if diff_sha256 is None and bug_candidate_sha256 is None:
            return None
        payload = (
            f"diff_sha256={diff_sha256 or ''}\n"
            f"bug_candidate_sha256={bug_candidate_sha256 or ''}\n"
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _read_build_metadata(
        self, target: Target, build_id: str, sanitizer: str
    ) -> Optional[dict]:
        metadata_path = self._get_build_metadata_path(
            target, build_id, sanitizer, create_parent=False
        )
        if not metadata_path.is_file():
            return None
        try:
            content = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(content, dict):
            return None
        return content

    def _prepare_build_fetch_dir(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
        diff: Optional[Path],
        bug_candidate: Optional[Path],
        bug_candidate_dir: Optional[Path],
    ) -> Optional[Path]:
        if diff is None and bug_candidate is None and bug_candidate_dir is None:
            return None
        build_fetch_dir = self.work_dir.get_build_fetch_dir(
            target, build_id, sanitizer, create=False
        )
        if build_fetch_dir.exists():
            rm_with_docker(build_fetch_dir)
        build_fetch_dir.mkdir(parents=True, exist_ok=True)
        if diff is not None:
            diff_subdir = build_fetch_dir / "diffs"
            diff_subdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(diff, diff_subdir / "ref.diff")
        if bug_candidate is not None:
            bc_subdir = build_fetch_dir / "bug-candidates"
            bc_subdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bug_candidate, bc_subdir / bug_candidate.name)
        if bug_candidate_dir is not None:
            bc_subdir = build_fetch_dir / "bug-candidates"
            bc_subdir.mkdir(parents=True, exist_ok=True)
            for f in bug_candidate_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(bug_candidate_dir)
                    dst = bc_subdir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
        return build_fetch_dir

    def __prepare_oss_crs_infra(
        self, publish: bool = False, docker_registry: str = None
    ) -> "TaskResult":
        # TODO
        return TaskResult(success=True)

    def prepare(self, publish: bool = False) -> bool:
        # Collect task names (infra + all CRS)
        tasks = [
            (
                "oss-crs-infra",
                lambda progress: self.__prepare_oss_crs_infra(
                    publish=publish, docker_registry=self.config.docker_registry
                ),
            )
        ]
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.prepare(
                        publish=publish,
                        docker_registry=self.config.docker_registry,
                        multi_task_progress=progress,
                    ),
                )
            )

        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Prepare",
        ) as progress:
            return progress.run_added_tasks().success

        return True

    def build_target(
        self,
        target: Target,
        build_id: str | None = None,
        sanitizer: str | None = None,
        bug_candidate: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
        diff: Optional[Path] = None,
    ) -> bool:
        sanitizer_explicit = sanitizer is not None
        resolved_options = self._resolve_target_build_options(
            target,
            sanitizer=sanitizer,
        )
        if resolved_options is None:
            return False
        sanitizer, _, _ = resolved_options

        # Normalize build_id at library boundary; generate timestamp-based ID if not provided
        build_id = normalize_run_id(build_id) if build_id else generate_run_id()

        target_base_image = target.build_docker_image()
        if target_base_image is None:
            return False

        tasks = []

        # Create snapshot(s) if any CRS needs a snapshot
        if self._any_needs_snapshot:
            # Collect unique sanitizers from all snapshot builds across all CRSes
            snapshot_sanitizers: set[str] = set()

            for crs in self.crs_list:
                for build_config in crs.config.snapshot_builds:
                    if sanitizer_explicit:
                        snap_sanitizer = sanitizer
                    else:
                        # Default sanitizer is address and can be overridden by
                        # module-level additional_env.
                        snap_sanitizer = build_config.additional_env.get(
                            "SANITIZER", sanitizer
                        )
                    snapshot_sanitizers.add(snap_sanitizer)

            # Old-style builder CRS also triggers a snapshot
            if self._builder_crs is not None and not snapshot_sanitizers:
                snapshot_sanitizers.add(sanitizer)

            for snap_sanitizer in sorted(snapshot_sanitizers):
                tasks.append(
                    (
                        f"Create snapshot ({snap_sanitizer})",
                        lambda p, s=snap_sanitizer: target.create_snapshot(s, p),
                    )
                )

        if bug_candidate is not None and bug_candidate_dir is not None:
            print("Error: --bug-candidate and --bug-candidate-dir are mutually exclusive.")
            return False
        if bug_candidate is not None and not bug_candidate.exists():
            print(f"Error: --bug-candidate path does not exist: {bug_candidate}")
            return False
        if bug_candidate is not None and not bug_candidate.is_file():
            print(
                "Error: --bug-candidate must be a file. "
                "Use --bug-candidate-dir for directories."
            )
            return False
        if bug_candidate_dir is not None and not bug_candidate_dir.exists():
            print(f"Error: --bug-candidate-dir path does not exist: {bug_candidate_dir}")
            return False
        if bug_candidate_dir is not None and not bug_candidate_dir.is_dir():
            print("Error: --bug-candidate-dir must be a directory.")
            return False

        diff_sha256: Optional[str] = None
        bug_candidate_sha256 = self._hash_bug_candidate_input(
            bug_candidate, bug_candidate_dir
        )
        if diff is not None:
            if not diff.is_file():
                print(f"Error: Diff file does not exist: {diff}")
                return False
            diff_sha256 = hashlib.sha256(diff.read_bytes()).hexdigest()
        input_sha256 = self._hash_directed_inputs(diff_sha256, bug_candidate_sha256)
        input_hash = input_sha256[:12] if input_sha256 else None

        build_fetch_dir = self._prepare_build_fetch_dir(
            target=target,
            build_id=build_id,
            sanitizer=sanitizer,
            diff=diff,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
        )

        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.build_target(
                        target,
                        target_base_image,
                        progress,
                        build_id,
                        sanitizer,
                        build_fetch_dir=build_fetch_dir,
                        diff_path=diff,
                        bug_candidate_dir=bug_candidate if bug_candidate else bug_candidate_dir,
                        input_hash=input_hash,
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Build Target",
        ) as progress:
            ret = progress.run_added_tasks()
            if ret.success:
                self._write_build_metadata(
                    target=target,
                    build_id=build_id,
                    sanitizer=sanitizer,
                    diff_sha256=diff_sha256,
                    bug_candidate_sha256=bug_candidate_sha256,
                    input_sha256=input_sha256,
                )
            return ret.success

        return True

    def run(
        self,
        target: Target,
        run_id: str | None = None,
        build_id: str | None = None,
        sanitizer: str | None = None,
        pov: Optional[Path] = None,
        pov_dir: Optional[Path] = None,
        diff: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        bug_candidate: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
    ) -> bool:
        resolved_options = self._resolve_target_build_options(
            target,
            sanitizer=sanitizer,
        )
        if resolved_options is None:
            return False
        sanitizer, _, _ = resolved_options

        # Normalize IDs at library boundary
        run_id = normalize_run_id(run_id) if run_id else generate_run_id()

        # Auto-detect cgroup-parent availability
        cgroup_parent, _ = check_cgroup_parent_available()

        # Determine build_id: use provided, find latest, or generate new
        if build_id:
            build_id = normalize_run_id(build_id)
        else:
            # Look for latest existing build for this target/sanitizer
            build_id = self.get_latest_build_id(target, sanitizer)
            # build_id may be None if no builds exist yet

        diff_sha256: Optional[str] = None
        if diff is not None and not diff.is_file():
            print(f"Error: Diff file does not exist: {diff}")
            return False
        if diff is not None:
            diff_sha256 = hashlib.sha256(diff.read_bytes()).hexdigest()
        if bug_candidate is not None and bug_candidate_dir is not None:
            print("Error: --bug-candidate and --bug-candidate-dir are mutually exclusive.")
            return False
        if bug_candidate is not None and not bug_candidate.exists():
            print(f"Error: --bug-candidate path does not exist: {bug_candidate}")
            return False
        if bug_candidate is not None and not bug_candidate.is_file():
            print(
                "Error: --bug-candidate must be a file. "
                "Use --bug-candidate-dir for directories."
            )
            return False
        if bug_candidate_dir is not None and not bug_candidate_dir.exists():
            print(f"Error: --bug-candidate-dir path does not exist: {bug_candidate_dir}")
            return False
        if bug_candidate_dir is not None and not bug_candidate_dir.is_dir():
            print("Error: --bug-candidate-dir must be a directory.")
            return False
        bug_candidate_sha256 = self._hash_bug_candidate_input(
            bug_candidate, bug_candidate_dir
        )
        input_sha256 = self._hash_directed_inputs(diff_sha256, bug_candidate_sha256)
        if seed_dir is not None and not seed_dir.is_dir():
            print(f"Error: Seed directory does not exist: {seed_dir}")
            return False
        if not self.__validate_before_run(
            target,
            diff=diff,
            pov=pov,
            pov_dir=pov_dir,
            seed_dir=seed_dir,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
        ):
            return False
        target.init_repo()

        # Check if we need to build
        if build_id:
            need_build = not self.__check_target_built(target, build_id, sanitizer)
        else:
            need_build = True  # No builds exist yet

        # Also check if snapshot image exists when any CRS needs a snapshot
        if not need_build and self._any_needs_snapshot:
            snapshot_tag = target.get_snapshot_image_name(sanitizer)
            check = subprocess.run(
                ["docker", "image", "inspect", snapshot_tag],
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                need_build = True

        if input_sha256 is not None and build_id and not need_build:
            metadata = self._read_build_metadata(target, build_id, sanitizer)
            if metadata is None:
                print(
                    "Error: build metadata is missing for this build. "
                    "Rebuild with directed inputs to run directed fuzzing safely."
                )
                return False
            build_input_sha256 = metadata.get("input_sha256")
            if not build_input_sha256:
                build_input_sha256 = self._hash_directed_inputs(
                    metadata.get("diff_sha256"),
                    metadata.get("bug_candidate_sha256"),
                )
            if build_input_sha256 != input_sha256:
                print(
                    "Error: directed input set does not match the one used for the selected build.\n"
                    f"  expected_input_sha256: {build_input_sha256}\n"
                    f"  actual_input_sha256:   {input_sha256}\n"
                    "Rebuild with the requested directed inputs or choose a matching build-id."
                )
                return False

        if need_build:
            # Generate new build_id if we don't have one
            if not build_id:
                build_id = generate_run_id()
            if not self.build_target(
                target,
                build_id,
                sanitizer,
                bug_candidate=bug_candidate,
                bug_candidate_dir=bug_candidate_dir,
                diff=diff,
            ):
                return False

        # Ensure snapshot_image_tag is set for the run phase, even if
        # build_target() was skipped because the target was already built.
        if self._any_needs_snapshot and target.snapshot_image_tag is None:
            target.snapshot_image_tag = target.get_snapshot_image_name(sanitizer)

        # Collect POV files from --pov and --pov-dir
        pov_files: list[Path] = []
        if pov is not None:
            pov_files.append(pov)
        if pov_dir is not None:
            pov_files.extend(f for f in pov_dir.iterdir() if f.is_file())

        # build_id is guaranteed to be set at this point (either found or generated)
        assert build_id is not None

        # Write build_id to run directory for later retrieval (e.g., by artifacts command)
        self.work_dir.write_build_id_for_run(run_id, sanitizer, build_id)

        return self.__run(
            target,
            run_id=run_id,
            build_id=build_id,
            sanitizer=sanitizer,
            pov_files=pov_files,
            diff_path=diff,
            seed_dir=seed_dir,
            cgroup_parent=cgroup_parent,
            bug_candidate=bug_candidate if bug_candidate else bug_candidate_dir,
        )

    def _validate_required_inputs(
        self,
        *,
        diff: Optional[Path] = None,
        pov: Optional[Path] = None,
        pov_dir: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        bug_candidate: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
    ) -> TaskResult:
        """Validate that all CRS-declared required_inputs are provided."""
        provided: set[str] = set()
        if diff is not None:
            provided.add("diff")
        if pov is not None or pov_dir is not None:
            provided.add("pov")
        if seed_dir is not None:
            provided.add("seed")
        if bug_candidate is not None or bug_candidate_dir is not None:
            provided.add("bug-candidate")

        errors: list[str] = []
        for crs in self.crs_list:
            if not crs.config.required_inputs:
                continue
            missing = set(crs.config.required_inputs) - provided
            if missing:
                flags = ", ".join("--" + m for m in sorted(missing))
                errors.append(
                    f"CRS '{crs.name}' requires inputs {sorted(missing)} "
                    f"but they were not provided. "
                    f"Please provide: {flags}"
                )
        if errors:
            return TaskResult(success=False, error="\n".join(errors))
        return TaskResult(success=True)

    def __validate_before_run(
        self,
        target: Target,
        *,
        diff: Optional[Path] = None,
        pov: Optional[Path] = None,
        pov_dir: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        bug_candidate: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
    ) -> bool:
        tasks = [
            (
                "Validate required inputs for CRS targets",
                lambda _: self._validate_required_inputs(
                    diff=diff,
                    pov=pov,
                    pov_dir=pov_dir,
                    seed_dir=seed_dir,
                    bug_candidate=bug_candidate,
                    bug_candidate_dir=bug_candidate_dir,
                ),
            ),
        ]

        if self.llm.exists():
            tasks.extend([
                (
                    "Validate required LLMs for CRS targets",
                    lambda _: self.llm.validate_required_llms(self.crs_list),
                ),
                (
                    "Validate required environment variables for LiteLLM",
                    lambda _: self.llm.validate_required_envs(),
                ),
            ])

        with MultiTaskProgress(
            tasks=tasks,
            title="Validate Configuration for Running",
        ) as progress:
            return progress.run_added_tasks().success

    def __check_target_built(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
    ) -> bool:
        target_base_image = target.get_docker_image_name()
        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.is_target_built(
                        target, target_base_image, progress, build_id, sanitizer
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Check Target Built",
        ) as progress:
            return progress.run_added_tasks().success

        return True

    def __run(
        self,
        target: Target,
        run_id: str,
        build_id: str,
        sanitizer: str,
        pov_files: list[Path] | None = None,
        diff_path: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        cgroup_parent: bool = False,
        bug_candidate: Optional[Path] = None,
    ) -> bool:
        if self.crs_compose_env.run_env == RunEnv.LOCAL:
            return self.__run_local(
                target,
                run_id=run_id,
                build_id=build_id,
                sanitizer=sanitizer,
                pov_files=pov_files or [],
                diff_path=diff_path,
                seed_dir=seed_dir, 
                cgroup_parent=cgroup_parent,
                bug_candidate=bug_candidate,
            )
        else:
            print(f"TODO: Support run env {self.crs_compose_env.run_env}")
            return False

    def __run_local(
        self,
        target: Target,
        run_id: str,
        build_id: str,
        sanitizer: str,
        pov_files: list[Path] | None = None,
        diff_path: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        cgroup_parent: bool = False,
        bug_candidate: Optional[Path] = None,
    ) -> bool:
        # Create cgroups if cgroup_parent mode is enabled
        worker_cgroup_path: Optional[Path] = None
        cgroup_parents: Optional[dict[str, str]] = None

        if cgroup_parent:
            try:
                worker_cgroup_path, cgroup_parents = create_run_cgroups(
                    run_id, "run", self.crs_list
                )
                log_success(f"Created cgroups at: {worker_cgroup_path}")
            except OSError as e:
                log_warning(f"Failed to create cgroups: {e}")
                log_warning("Falling back to per-container resource limits.")
                cgroup_parents = None

        with MultiTaskProgress(
            tasks=[],
            title="CRS Compose Run",
            deadline=self.deadline,
        ) as progress:
            with TmpDockerCompose(
                progress, "crs_compose", run_id=run_id, auto_cleanup=False
            ) as tmp_docker_compose:
                project_name = tmp_docker_compose.project_name
                actual_run_id = tmp_docker_compose.run_id
                docker_compose_path = tmp_docker_compose.docker_compose
                assert project_name is not None
                assert actual_run_id is not None
                assert docker_compose_path is not None
                progress.add_cleanup_task(
                    "Capture Docker Compose Logs",
                    lambda p: self.__capture_compose_logs(
                        project_name=project_name,
                        docker_compose_path=docker_compose_path,
                        target=target,
                        run_id=actual_run_id,
                        sanitizer=sanitizer,
                    ),
                )
                progress.add_cleanup_task(
                    "Cleanup Docker Compose",
                    lambda p: p.docker_compose_down(project_name, docker_compose_path),
                )
                tasks = [
                    (
                        "Prepare Running Environment",
                        lambda progress: self.__prepare_local_running_env(
                            project_name,
                            target,
                            tmp_docker_compose,
                            actual_run_id,
                            build_id,
                            sanitizer,
                            progress,
                            pov_files=pov_files or [],
                            diff_path=diff_path,
                            seed_dir=seed_dir,
                            cgroup_parents=cgroup_parents,
                            bug_candidate=bug_candidate,
                        ),
                    ),
                    (
                        "Run CRSs!",
                        lambda progress: self.__run_local_running_env(
                            project_name, tmp_docker_compose, progress
                        ),
                    ),
                ]
                progress.add_tasks(tasks)
                ret = progress.run_added_tasks()

                # Cleanup cgroups after run
                if worker_cgroup_path is not None:
                    success, msg = cleanup_cgroup(worker_cgroup_path)
                    if not success:
                        log_dim(f"Note: Cgroup cleanup deferred: {msg}")

                if ret.success or ret.interrupted:
                    self.__show_result_local(target, actual_run_id, sanitizer, progress)
                    return ret.success
                return False

        return False

    def __capture_compose_logs(
        self,
        *,
        project_name: str,
        docker_compose_path: Path,
        target: Target,
        run_id: str,
        sanitizer: str,
    ) -> TaskResult:
        """Persist docker-compose and per-service logs under run-scoped logs dir.

        This step is best-effort: failures are recorded to files but do not fail
        the run. Teardown failures are handled separately by compose cleanup.
        """
        logs_dir = self.work_dir.get_run_logs_dir(target, run_id, sanitizer)
        services_dir = logs_dir / "services"
        crs_logs_root = logs_dir / "crs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        services_dir.mkdir(parents=True, exist_ok=True)
        crs_logs_root.mkdir(parents=True, exist_ok=True)

        cmd_base = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
        ]

        try:
            def _run_capture(cmd: list[str]) -> tuple[str, str, int]:
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30
                    )
                except subprocess.TimeoutExpired:
                    return "", "Command timed out while capturing logs.", 124
                return result.stdout or "", result.stderr or "", result.returncode

            def _run_to_files(
                cmd: list[str], stdout_path: Path, stderr_path: Path
            ) -> int:
                with stdout_path.open("w") as stdout_f:
                    with stderr_path.open("w") as stderr_f:
                        try:
                            result = subprocess.run(
                                cmd,
                                stdout=stdout_f,
                                stderr=stderr_f,
                                text=True,
                                timeout=30,
                            )
                            return result.returncode
                        except subprocess.TimeoutExpired:
                            stderr_f.write("Command timed out while capturing logs.\n")
                            return 124

            compose_logs_rc = _run_to_files(
                [*cmd_base, "logs", "--no-color", "--timestamps"],
                logs_dir / "docker-compose.stdout.log",
                logs_dir / "docker-compose.stderr.log",
            )

            services_stdout, services_stderr, services_rc = _run_capture(
                [*cmd_base, "config", "--services"]
            )
            if services_stderr:
                (logs_dir / "services-config.stderr.log").write_text(services_stderr)

            service_names = [
                line.strip() for line in services_stdout.splitlines() if line.strip()
            ]
            (logs_dir / "services.json").write_text(
                json.dumps(service_names, indent=2, sort_keys=True)
            )

            failed_services: dict[str, int] = {}
            failed_links: dict[str, str] = {}
            for service in service_names:
                safe_name = self._safe_service_name(service)
                service_stdout_log = services_dir / f"{safe_name}.stdout.log"
                service_stderr_log = services_dir / f"{safe_name}.stderr.log"
                rc = _run_to_files(
                    [*cmd_base, "logs", "--no-color", "--timestamps", service],
                    service_stdout_log,
                    service_stderr_log,
                )
                if rc != 0:
                    failed_services[service] = rc

                owner_crs = self._service_owner_crs(service)
                if owner_crs is not None:
                    crs_dir = crs_logs_root / owner_crs
                    crs_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        self._link_or_copy(
                            service_stdout_log, crs_dir / service_stdout_log.name
                        )
                        self._link_or_copy(
                            service_stderr_log, crs_dir / service_stderr_log.name
                        )
                    except Exception as exc:
                        failed_links[service] = f"{type(exc).__name__}: {exc}"

            metadata = {
                "compose_logs_rc": compose_logs_rc,
                "services_command_rc": services_rc,
                "failed_service_logs": failed_services,
                "failed_log_links": failed_links,
            }
            (logs_dir / "capture-metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True)
            )

            warnings: list[str] = []
            if compose_logs_rc != 0:
                warnings.append(
                    "Failed to capture docker compose aggregate logs; "
                    "see docker-compose.stderr.log."
                )
            if services_rc != 0:
                warnings.append(
                    "Failed to list docker compose services; "
                    "see services-config.stderr.log."
                )
            if failed_services:
                warnings.append(
                    "Some service logs failed to capture; see capture-metadata.json."
                )
            if failed_links:
                warnings.append(
                    "Some per-CRS log links failed; see capture-metadata.json."
                )
            if warnings:
                (logs_dir / "capture-warning.txt").write_text("\n".join(warnings) + "\n")
        except Exception as exc:
            (logs_dir / "capture-warning.txt").write_text(
                "Unexpected failure during compose log capture.\n"
                f"{type(exc).__name__}: {exc}\n"
            )

        # Log capture is always best-effort; never fail the run on this task.
        return TaskResult(success=True)

    @staticmethod
    def _safe_service_name(service: str) -> str:
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", service).strip("-")
        return safe_name or "unknown-service"

    def _service_owner_crs(self, service: str) -> Optional[str]:
        for crs in self.crs_list:
            if service.startswith(f"{crs.name}_"):
                return crs.name
        return None

    @staticmethod
    def _link_or_copy(src: Path, dst: Path) -> None:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            try:
                rel_src = os.path.relpath(src, start=dst.parent)
                dst.symlink_to(rel_src)
            except OSError:
                shutil.copy2(src, dst)

    def __show_result_local(
        self, target: Target, run_id: str, sanitizer: str, progress: MultiTaskProgress
    ) -> None:
        crs_results = [
            {
                "name": crs.name,
                "submit_dir": self.work_dir.get_submit_dir(
                    crs.name, target, run_id, sanitizer
                ),
            }
            for crs in self.crs_list
        ]
        return progress.show_run_result(crs_results)

    def __prepare_local_running_env(
        self,
        project_name: str,
        target: Target,
        tmp_docker_compose: TmpDockerCompose,
        run_id: str,
        build_id: str,
        sanitizer: str,
        progress: MultiTaskProgress,
        pov_files: list[Path] | None = None,
        diff_path: Optional[Path] = None,
        seed_dir: Optional[Path] = None,
        cgroup_parents: Optional[dict[str, str]] = None,
        bug_candidate: Optional[Path] = None,
    ) -> TaskResult:
        def prepare_docker_compose(progress: MultiTaskProgress) -> TaskResult:
            content, warnings = renderer.render_run_crs_compose_docker_compose(
                self,
                tmp_docker_compose,
                project_name,
                target,
                run_id,
                build_id=build_id,
                sanitizer=sanitizer,
                cgroup_parents=cgroup_parents,
            )
            for warning in warnings:
                progress.add_note(warning)
            tmp_docker_compose.docker_compose.write_text(content)
            return TaskResult(success=True)

        def cleanup_exchange_dir(progress: MultiTaskProgress) -> TaskResult:
            exchange_dir = self.work_dir.get_exchange_dir(target, run_id, sanitizer)
            rm_with_docker(exchange_dir)
            return TaskResult(success=True)

        def cleanup_shared_dir(
            progress: MultiTaskProgress, crs_name: str
        ) -> TaskResult:
            rm_with_docker(
                self.work_dir.get_shared_dir(crs_name, target, run_id, sanitizer)
            )
            return TaskResult(success=True)

        def cleanup_shared_dirs(progress: MultiTaskProgress) -> TaskResult:
            for crs in self.crs_list:
                progress.add_task(
                    f"Clean up shared directory for {crs.name}",
                    lambda p, name=crs.name: cleanup_shared_dir(p, name),
                )
            return progress.run_added_tasks()

        def _get_exchange_dir() -> Path:
            return self.work_dir.get_exchange_dir(target, run_id, sanitizer)

        def copy_povs(progress: MultiTaskProgress) -> TaskResult:
            pov_subdir = _get_exchange_dir() / "povs"
            pov_subdir.mkdir(parents=True, exist_ok=True)
            for f in pov_files:
                shutil.copy2(f, pov_subdir / f.name)
            return TaskResult(success=True)

        def copy_diff(progress: MultiTaskProgress) -> TaskResult:
            diff_subdir = _get_exchange_dir() / "diffs"
            diff_subdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(diff_path, diff_subdir / "ref.diff")
            return TaskResult(success=True)

        def copy_seeds(progress: MultiTaskProgress) -> TaskResult:
            seed_subdir = _get_exchange_dir() / "seeds"
            seed_subdir.mkdir(parents=True, exist_ok=True)
            for f in seed_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, seed_subdir / f.name)
            return TaskResult(success=True)

        def copy_bug_candidates(progress: MultiTaskProgress) -> TaskResult:
            bc_subdir = _get_exchange_dir() / "bug-candidates"
            bc_subdir.mkdir(parents=True, exist_ok=True)
            if bug_candidate.is_file():
                shutil.copy2(bug_candidate, bc_subdir / bug_candidate.name)
            elif bug_candidate.is_dir():
                for f in bug_candidate.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(bug_candidate)
                        dst = bc_subdir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
            return TaskResult(success=True)

        progress.add_task("Clean up exchange directory", cleanup_exchange_dir)
        progress.add_task("Clean up shared directories", cleanup_shared_dirs)

        if pov_files:
            progress.add_task("Copy POV files to exchange dir", copy_povs)

        if diff_path:
            progress.add_task("Copy diff file to exchange dir", copy_diff)

        if seed_dir:
            progress.add_task("Copy seed files to exchange dir", copy_seeds)

        if bug_candidate:
            progress.add_task("Copy bug-candidate SARIF to exchange dir", copy_bug_candidates)

        progress.add_task(
            "Prepare combined docker compose file", prepare_docker_compose
        )
        progress.add_task(
            "Build docker images in the combined docker compose file",
            lambda progress: progress.docker_compose_build(
                project_name, tmp_docker_compose.docker_compose
            ),
        )

        return progress.run_added_tasks()

    def __run_local_running_env(
        self,
        project_name: str,
        tmp_docker_compose: TmpDockerCompose,
        progress: MultiTaskProgress,
    ) -> TaskResult:
        ret = progress.docker_compose_up(
            project_name, str(tmp_docker_compose.docker_compose)
        )
        if ret.success:
            return ret
        ret.error += "\n\n📝 Depending on your Dockerfile, You might need to run `uv run oss-crs prepare` to apply your changes."
        return ret
