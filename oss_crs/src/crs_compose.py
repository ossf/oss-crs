import shutil
import subprocess
import re
from pathlib import Path
from typing import Optional
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .llm import LLM
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target
from .templates import renderer
from .utils import TmpDockerCompose, normalize_run_id, generate_run_id, rm_with_docker
from .workdir import WorkDir


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path, work_dir: Path, skip_crs_init: bool = False) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir, skip_crs_init=skip_crs_init)

    def __init__(self, config: CRSComposeConfig, work_dir: Path, skip_crs_init: bool = False):
        hash = config.md5_hash()
        self.config = config
        self.llm = LLM(self.config.llm_config)
        self.work_dir = WorkDir(work_dir / f"crs_compose/{hash}")
        self.crs_compose_env = CRSComposeEnv(self.config.run_env)
        self.crs_list = [
            CRS.from_crs_compose_entry(
                name, crs_cfg, self.work_dir, self.crs_compose_env, skip_init=skip_crs_init
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

    @staticmethod
    def _get_sanitizer(target: Target) -> str:
        return target.get_target_env().get("sanitizer", "address")

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def get_latest_build_id(self, target: Target, sanitizer: str) -> str | None:
        """Find the latest build-id (by unix timestamp) for the given target and sanitizer."""
        latest_build_id = None
        latest_ts = 0

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
                    # Extract first 10-digit sequence (unix timestamp) from build_id
                    match = re.search(r'\d{10}', build_id)
                    if match:
                        ts = int(match.group())
                        if ts > latest_ts:
                            latest_ts = ts
                            latest_build_id = build_id
                    break  # Found at least one CRS with this build, no need to check others
        return latest_build_id

    def get_build_id_for_run(self, run_id: str, sanitizer: str) -> str | None:
        """Get the build-id that was used for a specific run."""
        return self.work_dir.read_build_id_for_run(run_id, sanitizer)

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

    def build_target(self, target: Target, build_id: str | None = None, sanitizer: str | None = None) -> bool:
        # Normalize build_id at library boundary; generate timestamp-based ID if not provided
        build_id = normalize_run_id(build_id) if build_id else generate_run_id()
        sanitizer = sanitizer if sanitizer else self._get_sanitizer(target)

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

        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.build_target(
                        target, target_base_image, progress, build_id, sanitizer
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Build Target",
        ) as progress:
            return progress.run_added_tasks().success

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
    ) -> bool:
        # Normalize IDs at library boundary
        run_id = normalize_run_id(run_id) if run_id else generate_run_id()
        sanitizer = sanitizer if sanitizer else self._get_sanitizer(target)

        # Determine build_id: use provided, find latest, or generate new
        if build_id:
            build_id = normalize_run_id(build_id)
        else:
            # Look for latest existing build for this target/sanitizer
            build_id = self.get_latest_build_id(target, sanitizer)
            # build_id may be None if no builds exist yet

        if diff is not None and not diff.is_file():
            print(f"Error: Diff file does not exist: {diff}")
            return False
        if seed_dir is not None and not seed_dir.is_dir():
            print(f"Error: Seed directory does not exist: {seed_dir}")
            return False
        if not self.__validate_before_run(target):
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
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                need_build = True

        if need_build:
            # Generate new build_id if we don't have one
            if not build_id:
                build_id = generate_run_id()
            if not self.build_target(target, build_id, sanitizer):
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

        return self.__run(target, run_id=run_id, build_id=build_id, sanitizer=sanitizer, pov_files=pov_files, diff_path=diff, seed_dir=seed_dir)

    def __validate_before_run(self, target: Target) -> bool:
        tasks = [
            (
                "Validate required LLMs for CRS targets",
                lambda _: self.llm.validate_required_llms(self.crs_list),
            ),
            (
                "Validate required environment variables for LiteLLM",
                lambda _: self.llm.validate_required_envs(),
            ),
        ]
        with MultiTaskProgress(
            tasks=tasks,
            title="Validate Configuration for Running",
        ) as progress:
            return progress.run_added_tasks().success

        return True

    def __check_target_built(self, target: Target, build_id: str, sanitizer: str) -> bool:
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

    def __run(self, target: Target, run_id: str, build_id: str, sanitizer: str, pov_files: list[Path] | None = None, diff_path: Optional[Path] = None, seed_dir: Optional[Path] = None) -> bool:
        if self.crs_compose_env.run_env == RunEnv.LOCAL:
            return self.__run_local(
                target,
                run_id=run_id,
                build_id=build_id,
                sanitizer=sanitizer,
                pov_files=pov_files or [],
                diff_path=diff_path,
                seed_dir=seed_dir
            )
        else:
            print(f"TODO: Support run env {self.crs_compose_env.run_env}")
            return False

    def __run_local(self, target: Target, run_id: str, build_id: str, sanitizer: str, pov_files: list[Path] | None = None, diff_path: Optional[Path] = None, seed_dir: Optional[Path] = None) -> bool:
        with MultiTaskProgress(
            tasks=[],
            title="CRS Compose Run",
            deadline=self.deadline,
        ) as progress:
            with TmpDockerCompose(
                progress, "crs_compose", run_id=run_id
            ) as tmp_docker_compose:
                project_name = tmp_docker_compose.project_name
                actual_run_id = tmp_docker_compose.run_id
                assert project_name is not None
                assert actual_run_id is not None
                tasks = [
                    (
                        "Prepare Running Environment",
                        lambda progress: self.__prepare_local_running_env(
                            project_name, target, tmp_docker_compose,
                            actual_run_id, build_id, sanitizer, progress,
                            pov_files=pov_files or [],
                            diff_path=diff_path,
                            seed_dir=seed_dir,
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
                if ret.success or ret.interrupted:
                    self.__show_result_local(target, actual_run_id, sanitizer, progress)
                    return ret.success
                return False

        return False

    def __show_result_local(self, target: Target, run_id: str, sanitizer: str, progress: MultiTaskProgress) -> None:
        crs_results = [
            {"name": crs.name, "submit_dir": self.work_dir.get_submit_dir(crs.name, target, run_id, sanitizer)}
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
    ) -> TaskResult:
        def prepare_docker_compose(progress: MultiTaskProgress) -> TaskResult:
            content = renderer.render_run_crs_compose_docker_compose(
                self,
                tmp_docker_compose,
                project_name,
                target,
                run_id,
                build_id=build_id,
                sanitizer=sanitizer,
            )
            tmp_docker_compose.docker_compose.write_text(content)
            return TaskResult(success=True)

        def cleanup_exchange_dir(progress: MultiTaskProgress) -> TaskResult:
            exchange_dir = self.work_dir.get_exchange_dir(target, run_id, sanitizer)
            rm_with_docker(exchange_dir)
            return TaskResult(success=True)

        def cleanup_shared_dir(progress: MultiTaskProgress, crs_name: str) -> TaskResult:
            rm_with_docker(self.work_dir.get_shared_dir(crs_name, target, run_id, sanitizer))
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

        progress.add_task("Clean up exchange directory", cleanup_exchange_dir)
        progress.add_task("Clean up shared directories", cleanup_shared_dirs)

        if pov_files:
            progress.add_task("Copy POV files to exchange dir", copy_povs)

        if diff_path:
            progress.add_task("Copy diff file to exchange dir", copy_diff)

        if seed_dir:
            progress.add_task("Copy seed files to exchange dir", copy_seeds)

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
