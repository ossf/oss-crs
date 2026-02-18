import shutil
import subprocess
from pathlib import Path
from typing import Optional
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .llm import LLM
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target
from .templates import renderer
from .utils import TmpDockerCompose, rm_with_docker


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path, work_dir: Path) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir)

    def __init__(self, config: CRSComposeConfig, work_dir: Path):
        hash = config.md5_hash()
        self.config = config
        self.llm = LLM(self.config.llm_config)
        self.work_dir = work_dir / f"crs_compose/{hash}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.crs_compose_env = CRSComposeEnv(self.config.run_env)
        self.crs_list = [
            CRS.from_crs_compose_entry(
                name, crs_cfg, self.work_dir, self.crs_compose_env
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

    def build_target(self, target: Target) -> bool:
        target_base_image = target.build_docker_image()
        if target_base_image is None:
            return False

        tasks = []

        # Create snapshot(s) if any CRS needs a snapshot
        if self._any_needs_snapshot:
            # Collect unique sanitizers from all snapshot builds across all CRSes
            snapshot_sanitizers: set[str] = set()
            default_sanitizer = self._get_sanitizer(target)

            for crs in self.crs_list:
                for build_config in crs.config.snapshot_builds:
                    sanitizer = build_config.additional_env.get(
                        "SANITIZER", default_sanitizer
                    )
                    snapshot_sanitizers.add(sanitizer)

            # Old-style builder CRS also triggers a snapshot
            if self._builder_crs is not None and not snapshot_sanitizers:
                snapshot_sanitizers.add(default_sanitizer)

            for sanitizer in sorted(snapshot_sanitizers):
                tasks.append(
                    (
                        f"Create snapshot ({sanitizer})",
                        lambda p, s=sanitizer: target.create_snapshot(s, p),
                    )
                )

        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.build_target(
                        target, target_base_image, progress
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Build Target",
        ) as progress:
            return progress.run_added_tasks().success

    def run(
        self,
        target: Target,
        pov: Optional[Path] = None,
        pov_dir: Optional[Path] = None,
        diff: Optional[Path] = None,
        corpus_dir: Optional[Path] = None,
    ) -> bool:
        if diff is not None and not diff.is_file():
            print(f"Error: Diff file does not exist: {diff}")
            return False
        if corpus_dir is not None and not corpus_dir.is_dir():
            print(f"Error: Corpus directory does not exist: {corpus_dir}")
            return False
        if not self.__validate_before_run(target):
            return False
        target.init_repo()
        need_build = not self.__check_target_built(target)

        # Also check if snapshot image exists when any CRS needs a snapshot
        if not need_build and self._any_needs_snapshot:
            snapshot_tag = target.get_snapshot_image_name(self._get_sanitizer(target))
            check = subprocess.run(
                ["docker", "image", "inspect", snapshot_tag],
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                need_build = True

        if need_build:
            if not self.build_target(target):
                return False

        # Ensure snapshot_image_tag is set for the run phase, even if
        # build_target() was skipped because the target was already built.
        if self._any_needs_snapshot and target.snapshot_image_tag is None:
            target.snapshot_image_tag = target.get_snapshot_image_name(
                self._get_sanitizer(target)
            )

        # Collect POV files from --pov and --pov-dir
        pov_files: list[Path] = []
        if pov is not None:
            pov_files.append(pov)
        if pov_dir is not None:
            pov_files.extend(f for f in pov_dir.iterdir() if f.is_file())

        return self.__run(target, pov_files, diff_path=diff, corpus_dir=corpus_dir)

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

    def __check_target_built(self, target: Target) -> bool:
        target_base_image = target.get_docker_image_name()
        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.is_target_built(
                        target, target_base_image, progress
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Check Target Built",
        ) as progress:
            return progress.run_added_tasks().success

    def __run(self, target: Target, pov_files: list[Path] = None, diff_path: Optional[Path] = None, corpus_dir: Optional[Path] = None) -> bool:
        if self.crs_compose_env.run_env == RunEnv.LOCAL:
            return self.__run_local(target, pov_files or [], diff_path=diff_path, corpus_dir=corpus_dir)
        else:
            print(f"TODO: Support run env {self.crs_compose_env.run_env}")
            return False

    def __run_local(self, target: Target, pov_files: list[Path] = None, diff_path: Optional[Path] = None, corpus_dir: Optional[Path] = None) -> bool:
        with MultiTaskProgress(
            tasks=[],
            title="CRS Compose Run",
            deadline=self.deadline,
        ) as progress:
            with TmpDockerCompose(progress, "crs_compose") as tmp_docker_compose:
                project_name = tmp_docker_compose.project_name
                tasks = [
                    (
                        "Prepare Running Environment",
                        lambda progress: self.__prepare_local_running_env(
                            project_name, target, tmp_docker_compose, progress,
                            pov_files=pov_files or [],
                            diff_path=diff_path,
                            corpus_dir=corpus_dir,
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
                    self.__show_result_local(target, progress)
                    return ret.success
                return False

    def __show_result_local(self, target: Target, progress: MultiTaskProgress) -> None:
        crs_results = [
            {"name": crs.name, "submit_dir": crs.get_submit_dir(target)}
            for crs in self.crs_list
        ]
        return progress.show_run_result(crs_results)

    def __prepare_local_running_env(
        self,
        project_name: str,
        target: Target,
        tmp_docker_compose: TmpDockerCompose,
        progress: MultiTaskProgress,
        pov_files: list[Path] = None,
        diff_path: Optional[Path] = None,
        corpus_dir: Optional[Path] = None,
    ) -> TaskResult:
        def prepare_docker_compose(progress: MultiTaskProgress) -> TaskResult:
            content = renderer.render_run_crs_compose_docker_compose(
                self,
                tmp_docker_compose,
                project_name,
                target,
            )
            tmp_docker_compose.docker_compose.write_text(content)
            return TaskResult(success=True)

        def cleanup_exchange_dir(progress: MultiTaskProgress) -> TaskResult:
            # Exchange dir is shared across all CRSs — use any CRS to get the path
            if self.crs_list:
                exchange_dir = self.crs_list[0].get_exchange_dir(target)
                rm_with_docker(exchange_dir)
            return TaskResult(success=True)

        def cleanup_shared_dirs(progress: MultiTaskProgress) -> TaskResult:
            for crs in self.crs_list:
                progress.add_task(
                    f"Clean up shared directory for {crs.name}",
                    lambda progress, crs=crs: TaskResult(
                        success=crs.cleanup_shared_dir(target)
                    ),
                )
            return progress.run_added_tasks()

        def _get_exchange_dir() -> Path:
            # All CRSs share the same exchange dir — use the first non-builder CRS
            for crs in self.crs_list:
                if not crs.config.is_builder:
                    return crs.get_exchange_dir(target)
            raise ValueError("No non-builder CRS found")

        def copy_povs(progress: MultiTaskProgress) -> TaskResult:
            pov_subdir = _get_exchange_dir() / "pov"
            pov_subdir.mkdir(parents=True, exist_ok=True)
            for f in pov_files:
                shutil.copy2(f, pov_subdir / f.name)
            return TaskResult(success=True)

        def copy_diff(progress: MultiTaskProgress) -> TaskResult:
            diff_subdir = _get_exchange_dir() / "diff"
            diff_subdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(diff_path, diff_subdir / "ref.diff")
            return TaskResult(success=True)

        def copy_corpus(progress: MultiTaskProgress) -> TaskResult:
            seed_subdir = _get_exchange_dir() / "seed"
            seed_subdir.mkdir(parents=True, exist_ok=True)
            for f in corpus_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, seed_subdir / f.name)
            return TaskResult(success=True)

        progress.add_task("Clean up exchange directory", cleanup_exchange_dir)
        progress.add_task("Clean up shared directories", cleanup_shared_dirs)

        if pov_files:
            progress.add_task("Copy POV files to exchange dir", copy_povs)

        if diff_path:
            progress.add_task("Copy diff file to exchange dir", copy_diff)

        if corpus_dir:
            progress.add_task("Copy corpus files to exchange dir", copy_corpus)

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
        ret.error += "\n\n📝 Depending on your Dockerfile, You might need to run `uv run crs-compose prepare` to apply your changes."
        return ret
