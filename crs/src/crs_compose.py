from pathlib import Path
from typing import Optional
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .llm import LLM
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target
from .templates import renderer
from .utils import TmpDockerCompose, normalize_run_id, generate_run_id


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path, work_dir: Path, skip_crs_init: bool = False) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir, skip_crs_init=skip_crs_init)

    def __init__(self, config: CRSComposeConfig, work_dir: Path, skip_crs_init: bool = False):
        hash = config.md5_hash()
        self.config = config
        self.llm = LLM(self.config.llm_config)
        self.work_dir = work_dir / f"crs_compose/{hash}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.crs_compose_env = CRSComposeEnv(self.config.run_env)
        self.crs_list = [
            CRS.from_crs_compose_entry(
                name, crs_cfg, self.work_dir, self.crs_compose_env, skip_init=skip_crs_init
            )
            for name, crs_cfg in self.config.crs_entries.items()
        ]
        self.deadline: Optional[float] = None

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def get_latest_build_run_id(self, target: Target) -> str | None:
        """Find the latest build run-id (by unix timestamp) for the given target.

        New structure: builds/<build-id>/crs/<crs-name>/<target>/BUILD_OUT_DIR/
        """
        import re
        target_base_image = target.get_docker_image_name()
        target_key = target_base_image.replace(":", "_")
        latest_run_id = None
        latest_ts = 0

        builds_dir = self.work_dir / "builds"
        if not builds_dir.exists():
            return None

        for build_id_dir in builds_dir.iterdir():
            if not build_id_dir.is_dir():
                continue
            build_id = build_id_dir.name
            # Check if any CRS has build output for this target
            for crs in self.crs_list:
                build_out_dir = build_id_dir / "crs" / crs.name / target_key / "BUILD_OUT_DIR"
                if build_out_dir.exists():
                    # Extract first 10-digit sequence (unix timestamp) from build_id
                    match = re.search(r'\d{10}', build_id)
                    if match:
                        ts = int(match.group())
                        if ts > latest_ts:
                            latest_ts = ts
                            latest_run_id = build_id
                    break  # Found at least one CRS with this build, no need to check others
        return latest_run_id

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

    def build_target(self, target: Target, build_id: str | None = None) -> bool:
        # Normalize build_id at library boundary
        build_id = normalize_run_id(build_id) if build_id else normalize_run_id("default")

        target_base_image = target.build_docker_image()
        if target_base_image is None:
            return False

        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.build_target(
                        target, target_base_image, progress, build_id
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Build Target",
        ) as progress:
            return progress.run_added_tasks().success

        return True

    def run(self, target: Target, run_id: str | None = None, build_id: str | None = None) -> bool:
        """
        Run the CRS compose.

        Args:
            target: The target to run.
            run_id: Run identifier for this run (used for SUBMIT_DIR, FETCH_DIR, SHARED_DIR).
                    If None, generates a timestamp-based ID.
            build_id: Build identifier (used for BUILD_OUT_DIR). If None, uses "default"
                      and will trigger build if needed.
        """
        # Normalize IDs at library boundary
        run_id = normalize_run_id(run_id) if run_id else generate_run_id()
        build_id = normalize_run_id(build_id) if build_id else normalize_run_id("default")

        if not self.__validate_before_run(target):
            return False
        target.init_repo()

        # Check if build exists, build if needed
        if not self.__check_target_built(target, build_id):
            if not self.build_target(target, build_id):
                return False

        return self.__run(target, run_id=run_id, build_id=build_id)

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

    def __check_target_built(self, target: Target, build_id: str) -> bool:
        target_base_image = target.get_docker_image_name()
        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.is_target_built(
                        target, target_base_image, progress, build_id
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Check Target Built",
        ) as progress:
            return progress.run_added_tasks().success

        return True

    def __run(self, target: Target, run_id: str, build_id: str) -> bool:
        if self.crs_compose_env.run_env == RunEnv.LOCAL:
            return self.__run_local(target, run_id=run_id, build_id=build_id)
        else:
            print(f"TODO: Support run env {self.crs_compose_env.run_env}")
            return False

    def __run_local(self, target: Target, run_id: str, build_id: str) -> bool:
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
                            project_name,
                            target,
                            tmp_docker_compose,
                            actual_run_id,
                            build_id,
                            progress,
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
                    self.__show_result_local(target, actual_run_id, progress)
                    return ret.success
                return False

        return False

    def __show_result_local(self, target: Target, run_id: str, progress: MultiTaskProgress) -> None:
        crs_results = [
            {"name": crs.name, "submit_dir": crs.get_submit_dir(target, run_id=run_id)}
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
        progress: MultiTaskProgress,
    ) -> TaskResult:
        def prepare_docker_compose(progress: MultiTaskProgress) -> TaskResult:
            content = renderer.render_run_crs_compose_docker_compose(
                self,
                tmp_docker_compose,
                project_name,
                target,
                run_id,
                build_id=build_id,
            )
            tmp_docker_compose.docker_compose.write_text(content)
            return TaskResult(success=True)

        def cleanup_shared_dirs(progress: MultiTaskProgress) -> TaskResult:
            for crs in self.crs_list:
                progress.add_task(
                    f"Clean up shared directory for {crs.name}",
                    lambda progress, crs=crs: TaskResult(
                        success=crs.cleanup_shared_dir(target, run_id=run_id)
                    ),
                )
            return progress.run_added_tasks()

        progress.add_task("Clean up shared directories", cleanup_shared_dirs)

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
