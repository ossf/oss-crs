from pathlib import Path
from typing import Optional
import hashlib
import os

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry, CRSComposeEnv
from .ui import MultiTaskProgress, TaskResult
from .target import Target, file_lock
from .templates import renderer
from .utils import TmpDockerCompose
from .workdir import WorkDir

CRS_YAML_PATH = "oss-crs/crs.yaml"


def init_crs_repo(
    name,
    repo_url: str,
    branch: str,
    dest_path: Path,
    skip_if_exists: bool = False,
) -> TaskResult:
    # Use file lock to prevent race conditions when multiple runs access same CRS repo
    lock_path = dest_path.parent / f".{name}.lock"
    with file_lock(lock_path):
        # Skip init if repo exists and skip_if_exists is True
        if skip_if_exists and dest_path.exists():
            return TaskResult(success=True)

        tasks = []
        if dest_path.exists():
            tasks = [
                (
                    "Git Fetch",
                    lambda progress: progress.run_command_with_streaming_output(
                        cmd=["git", "fetch", "--recurse-submodules", "origin", branch],
                        cwd=dest_path,
                    ),
                ),
                (
                    "Git Reset",
                    lambda progress: progress.run_command_with_streaming_output(
                        cmd=["git", "reset", "--hard", f"origin/{branch}"],
                        cwd=dest_path,
                    ),
                ),
            ]
        else:
            tasks = [
                (
                    "Cloning CRS repository",
                    lambda progress: progress.run_command_with_streaming_output(
                        cmd=[
                            "git",
                            "clone",
                            "--recurse-submodules",
                            "--branch",
                            branch,
                            repo_url,
                            str(dest_path),
                        ]
                    ),
                ),
            ]
        with MultiTaskProgress(tasks, title=f"Init CRS: {name}") as progress:
            return progress.run_added_tasks()


class CRS:
    @classmethod
    def from_yaml_file(cls, crs_path: Path, work_dir: "WorkDir") -> "CRS":
        config = CRSConfig.from_yaml_file(crs_path / CRS_YAML_PATH)
        return cls(config.name, crs_path, work_dir, None, None)

    @classmethod
    def from_crs_compose_entry(
        cls,
        name: str,
        entry: CRSEntry,
        work_dir: "WorkDir",
        crs_compose_env: CRSComposeEnv,
        skip_init: bool = False,
    ) -> "CRS":
        if entry.source.local_path:
            return cls(
                name, Path(entry.source.local_path), work_dir, entry, crs_compose_env
            )
        else:
            # crs_src is a sibling directory to the work_dir
            path = work_dir.path / "../crs_src" / name
            init_result = init_crs_repo(
                name,
                entry.source.url,
                entry.source.ref,
                path,
                skip_if_exists=skip_init,
            )
            if init_result.success:
                return cls(name, path, work_dir, entry, crs_compose_env)
            detail = init_result.error or "unknown repository initialization error"
            raise ValueError(
                f"Failed to initialize CRS from entry: {name}; reason: {detail}"
            )

    def __init__(
        self,
        name: str,
        crs_path: Path,
        work_dir: WorkDir,
        resource: Optional[CRSEntry],
        crs_compose_env: Optional[CRSComposeEnv],
    ):
        self.name = name
        self.crs_path = crs_path.expanduser().resolve()
        self.config = CRSConfig.from_yaml_file(self.crs_path / CRS_YAML_PATH)
        self.work_dir = work_dir
        self.resource = resource
        self.crs_compose_env = crs_compose_env

    def prepare(
        self,
        publish: bool = False,
        docker_registry: Optional[str] = None,
        multi_task_progress: Optional[MultiTaskProgress] = None,
    ) -> "TaskResult":
        """
        Run docker buildx bake to prepare CRS images.

        Args:
            publish: If True, push baked images to the docker registry.
            docker_registry: Override registry for push/cache. If set, overrides config.
            multi_task_progress: Optional progress tracker. If not provided, creates one.

        Returns:
            True if bake succeeded, False otherwise.
        """
        if self.config.prepare_phase is None:
            return TaskResult(success=True)

        # Create a single-task progress if not provided
        standalone = multi_task_progress is None
        # Determine the registry to use (parameter overrides config)
        registry = docker_registry if docker_registry else self.config.docker_registry
        version = self.config.version

        # Build HCL file path (relative to crs_path)
        hcl_path = self.crs_path / self.config.prepare_phase.hcl

        # Build the base command
        cmd = ["docker", "buildx", "bake", "-f", str(hcl_path)]

        # Add cache-from options (buildx silently ignores unavailable sources)
        if registry:
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-from=type=registry,ref={cache_ref_version}",
                    f"--set=*.cache-from=type=registry,ref={cache_ref_latest}",
                ]
            )

        # Add push and cache-to options if publishing
        if publish:
            if not registry:
                error_msg = (
                    "Cannot publish without a docker registry. "
                    "Provide docker_registry parameter or set it in config."
                )
                return TaskResult(success=False, error=error_msg)

            cmd.append("--push")
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-to=type=registry,ref={cache_ref_version},mode=max",
                    f"--set=*.cache-to=type=registry,ref={cache_ref_latest},mode=max",
                ]
            )

        # Set up environment for bake:
        # - forward CRS entry additional_env for prepare-phase overrides
        # - keep VERSION pinned to the CRS config value
        env = os.environ.copy()
        if self.resource:
            env.update(self.resource.additional_env)
        env["VERSION"] = version

        # Display command info
        info_text = (
            f"HCL: {hcl_path}\n"
            f"Version: {version}\n"
            f"Registry: {registry or 'N/A'}\n"
            f"Publish: {publish}"
        )

        if standalone:
            # TODO
            pass
        else:
            return multi_task_progress.run_command_with_streaming_output(
                cmd=cmd, cwd=self.crs_path, env=env, info_text=info_text
            )

    def __is_supported_target(self, target: Target) -> bool:
        # TODO: implement proper check based on self.config.supported_target
        return True

    def build_target(
        self,
        target: Target,
        target_base_image: str,
        progress: MultiTaskProgress,
        build_id: str,
        sanitizer: str,
    ) -> "TaskResult":
        if (
            self.config.target_build_phase is None
            or not self.config.target_build_phase.builds
        ):
            return TaskResult(success=True)
        if not self.__is_supported_target(target):
            # TODO: warn instead of error?
            return TaskResult(
                success=False,
                error=f"Skipping target {target.name} for CRS {self.name} as it is not supported.",
            )
        # Filter out snapshot builds — they are handled by target.create_snapshot()
        # at the CRSCompose level, not by the docker-compose build pipeline
        non_snapshot_builds = [
            b for b in self.config.target_build_phase.builds if not b.snapshot
        ]
        if not non_snapshot_builds:
            return TaskResult(success=True)
        build_work_dir = self.work_dir.get_crs_build_dir(
            self.name, target, build_id, sanitizer
        )
        for build_config in non_snapshot_builds:
            build_name = build_config.name
            progress.add_task(
                build_name,
                lambda p, build_name=build_name, build_config=build_config: (
                    self.__build_target_one(
                        target,
                        target_base_image,
                        build_name,
                        build_config,
                        build_work_dir,
                        build_id,
                        sanitizer,
                        p,
                    )
                ),
            )
        return progress.run_added_tasks()

    def is_target_built(
        self,
        target: Target,
        target_base_image: str,
        progress: MultiTaskProgress,
        build_id: str,
        sanitizer: str,
    ) -> TaskResult:
        if (
            self.config.target_build_phase is None
            or not self.config.target_build_phase.builds
        ):
            return TaskResult(success=True)
        if not self.__is_supported_target(target):
            return TaskResult(
                success=False,
                error=f"Skipping target {target.name} for CRS {self.name} as it is not supported.",
            )
        # Filter out snapshot builds — snapshots produce Docker images, not files in
        # the build-out directory, so they have no outputs to check here
        non_snapshot_builds = [
            b for b in self.config.target_build_phase.builds if not b.snapshot
        ]
        if not non_snapshot_builds:
            return TaskResult(success=True)
        build_out_dir = self.work_dir.get_build_output_dir(
            self.name, target, build_id, sanitizer
        )
        for build_config in non_snapshot_builds:
            build_name = build_config.name
            progress.add_task(
                f"Check build outputs for {build_name}",
                lambda p, build_config=build_config: self.__check_outputs(
                    build_config,
                    build_out_dir,
                    p,
                ),
            )
        return progress.run_added_tasks()

    def __check_outputs(
        self, build_config, build_out_dir, progress=None
    ) -> "TaskResult":
        output_paths = []
        for output in build_config.outputs:
            output_path = build_out_dir / output
            output_paths.append(output_path)

        def check_output(progress, output_path):
            if output_path.exists():
                return TaskResult(success=True)
            else:
                skip_file = output_path.parent / f".{output_path.name}.skip"
                if skip_file.exists():
                    progress.add_note(f"Output is skipped as {skip_file.name} exists.")
                    return TaskResult(
                        success=True,
                    )
                return TaskResult(success=False)

        if progress:
            for output_path in output_paths:
                progress.add_task(
                    f"{output_path}", lambda p, o=output_path: check_output(p, o)
                )
            return progress.run_added_tasks()
        else:
            all_exist = all(p.exists() for p in output_paths)
            return TaskResult(success=all_exist)

    def __build_target_one(
        self,
        target,
        target_base_image: str,
        build_name: str,
        build_config,
        build_work_dir: Path,
        build_id: str,
        sanitizer: str,
        progress: MultiTaskProgress,
    ) -> "TaskResult":
        build_out_dir = self.work_dir.get_build_output_dir(
            self.name, target, build_id, sanitizer
        )
        build_cache_path = build_out_dir / f".{build_name}.cache"
        docker_compose_output = ""

        def prepare_docker_compose_file(
            progress, project_name: str, tmp_docker_compose: TmpDockerCompose
        ) -> "TaskResult":
            rendered = renderer.render_build_target_docker_compose(
                self,
                target,
                target_base_image,
                build_config,
                build_out_dir,
                build_id,
                sanitizer,
            )
            tmp_docker_compose.docker_compose.write_text(rendered)
            return TaskResult(success=True)

        def build_docker_compose(
            progress, project_name: str, tmp_docker_compose
        ) -> TaskResult:
            nonlocal docker_compose_output
            ret = progress.docker_compose_build(
                project_name,
                tmp_docker_compose.docker_compose,
            )
            docker_compose_output = ret.output if ret.success else ret.error
            return ret

        def run_docker_compose(
            progress, project_name: str, tmp_docker_compose
        ) -> "TaskResult":
            nonlocal docker_compose_output
            image_hash = get_image_content_hash(
                f"{project_name}-target_builder", progress
            )
            if image_hash is None:
                return TaskResult(
                    success=False,
                    error="Failed to get target_builder image hash.",
                )

            if build_cache_path.exists():
                if build_cache_path.read_text() == image_hash:
                    if self.__check_outputs(build_config, build_out_dir).success:
                        progress.add_note(
                            "Build cache is up-to-date. Skipping target build."
                        )
                        return TaskResult(success=True)
                    progress.add_note(
                        "Build cache hit but outputs missing. Rebuilding."
                    )
            ret = progress.docker_compose_run(
                project_name, tmp_docker_compose.docker_compose, "target_builder"
            )

            if ret.success:
                docker_compose_output = ret.output
            else:
                docker_compose_output = ret.error
            build_cache_path.write_text(image_hash)
            return ret

        with TmpDockerCompose(progress, "crs") as tmp_docker_compose:
            project_name = tmp_docker_compose.project_name
            progress.add_task(
                "Prepare docker compose file",
                lambda p: prepare_docker_compose_file(
                    p, project_name, tmp_docker_compose
                ),
            )
            progress.add_task(
                "Prepare docker images defined in docker compose file",
                lambda p: build_docker_compose(p, project_name, tmp_docker_compose),
            )
            progress.add_task(
                "Build target by executing the docker compose",
                lambda p: run_docker_compose(p, project_name, tmp_docker_compose),
            )
            progress.add_task(
                "Check outputs",
                lambda p: self.__check_outputs(build_config, build_out_dir, p),
            )

            result = progress.run_added_tasks()
            if result.success:
                return TaskResult(success=True)
            docker_compose_contents = tmp_docker_compose.docker_compose.read_text()
            error = result.error or ""
            error += "\n"
            if docker_compose_output:
                error += (
                    f"📝 Docker compose output:\n---\n{docker_compose_output}\n---\n"
                )
            error += (
                f"📝 Docker compose file contents:\n---\n{docker_compose_contents}\n---"
            )
            return TaskResult(success=False, error=error)


def get_image_content_hash(
    image_name: str, progress: MultiTaskProgress
) -> Optional[str]:
    cmd = [
        "docker",
        "inspect",
        "--format",
        "{{json .RootFS.Layers}}",
        image_name,
    ]
    ret = progress.run_command_with_streaming_output(
        cmd=cmd,
        cwd=None,
    )
    if not ret.success:
        return None
    layers_json = ret.output.strip()
    return hashlib.sha256(layers_json.encode()).hexdigest()[:12]
