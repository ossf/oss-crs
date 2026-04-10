from pathlib import Path
from typing import Optional
import hashlib
import json
import os
import subprocess

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry, CRSComposeEnv
from .env_policy import build_prepare_env
from .ui import MultiTaskProgress, TaskResult
from .target import Target, file_lock
from .templates import renderer
from .utils import TmpDockerCompose, log_dim
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
        source = entry.source
        if source is None:
            raise ValueError(f"CRS entry '{name}' is missing source configuration")
        if source.local_path:
            return cls(name, Path(source.local_path), work_dir, entry, crs_compose_env)
        repo_url = source.url
        branch = source.ref
        if repo_url is None or branch is None:
            raise ValueError(
                f"CRS entry '{name}' must define either local_path or both url and ref"
            )
        # crs_src is a sibling directory to the work_dir
        path = work_dir.path / "../crs_src" / name
        init_result = init_crs_repo(
            name,
            repo_url,
            branch,
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

    def _try_pull_prebuilt_images(
        self, hcl_path: Path, env: dict
    ) -> Optional[list[str]]:
        """Try to pull all prebuilt images defined in the bake plan.

        Returns a list of pulled images on success, or None if any pull fails.
        """
        # Get the resolved build plan as JSON
        result = subprocess.run(
            ["docker", "buildx", "bake", "-f", str(hcl_path), "--print"],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.crs_path,
        )
        if result.returncode != 0:
            return None

        try:
            plan = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        targets = plan.get("target", {})
        if not targets:
            return None

        pulled: list[str] = []
        for target_name, target_conf in targets.items():
            tags = target_conf.get("tags", [])
            if not tags:
                continue

            # Find the first registry tag (contains a domain with a dot)
            registry_tag = None
            for tag in tags:
                parts = tag.split("/")
                if len(parts) >= 2 and "." in parts[0]:
                    registry_tag = tag
                    break

            if registry_tag is None:
                log_dim(f"No registry tag for target {target_name}, skipping pull")
                return None

            # Pull from registry
            pull_result = subprocess.run(
                ["docker", "pull", registry_tag],
                capture_output=True,
                text=True,
            )
            if pull_result.returncode != 0:
                log_dim(f"Failed to pull {registry_tag}, falling back to bake")
                return None

            pulled.append(registry_tag)

            # Tag with remaining tags
            for tag in tags:
                if tag != registry_tag:
                    subprocess.run(
                        ["docker", "tag", registry_tag, tag],
                        capture_output=True,
                        text=True,
                    )

        return pulled

    def prepare(
        self,
        multi_task_progress: MultiTaskProgress,
        publish: bool = False,
        docker_registry: Optional[str] = None,
        no_pull: bool = False,
    ) -> "TaskResult":
        """
        Run docker buildx bake to prepare CRS images.

        When not publishing, first tries to pull prebuilt images from the
        registry (based on tags defined in the HCL). Falls back to a full
        bake build if any pull fails.

        Args:
            publish: If True, push baked images to the docker registry.
            docker_registry: Override registry for push/cache. If set, overrides config.
            multi_task_progress: Progress tracker used by the compose-level prepare flow.
            no_pull: If True, skip pulling prebuilt images and always build locally.

        Returns:
            True if bake succeeded, False otherwise.
        """
        if self.config.prepare_phase is None:
            return TaskResult(success=True)

        # Determine the registry to use (parameter overrides config)
        registry = docker_registry if docker_registry else self.config.docker_registry
        version = self.config.version

        # Build HCL file path (relative to crs_path)
        hcl_path = self.crs_path / self.config.prepare_phase.hcl

        # Set up environment for bake with centralized merge policy.
        env_plan = build_prepare_env(
            base_env=os.environ.copy(),
            crs_additional_env=self.resource.additional_env if self.resource else None,
            version=version,
            scope=f"{self.name}:prepare",
        )
        env = env_plan.effective_env
        if hasattr(multi_task_progress, "add_note"):
            for warning in env_plan.warnings:
                multi_task_progress.add_note(warning)

        # When not publishing and pull is enabled, try to pull prebuilt images first.
        # This avoids expensive local builds when images are already in a registry.
        if not publish and not no_pull:
            pulled = self._try_pull_prebuilt_images(hcl_path, env)
            if pulled:
                info_text = (
                    f"Pulled {len(pulled)} prebuilt images from registry\n"
                    + "\n".join(f"  - {img}" for img in pulled)
                )
                if hasattr(multi_task_progress, "add_note"):
                    multi_task_progress.add_note(info_text)
                return TaskResult(success=True)

        # Fall back to building via bake.
        # NOTE: cache-from/cache-to are NOT set here. Each CRS's HCL file
        # defines per-target cache refs with the correct image names.
        cmd = ["docker", "buildx", "bake", "-f", str(hcl_path)]

        if publish:
            if not registry:
                error_msg = (
                    "Cannot publish without a docker registry. "
                    "Provide docker_registry parameter or set it in config."
                )
                return TaskResult(success=False, error=error_msg)
            cmd.append("--push")

        info_text = (
            f"HCL: {hcl_path}\n"
            f"Version: {version}\n"
            f"Registry: {registry or 'N/A'}\n"
            f"Publish: {publish}"
        )

        return multi_task_progress.run_command_with_streaming_output(
            cmd=cmd, cwd=self.crs_path, env=env, info_text=info_text
        )

    def __is_supported_target(self, target: Target) -> bool:
        _ = target
        return True

    def build_target(
        self,
        target: Target,
        target_base_image: str,
        progress: MultiTaskProgress,
        build_id: str,
        sanitizer: str,
        build_fetch_dir: Optional[Path] = None,
        diff_path: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
        input_hash: Optional[str] = None,
        target_source_path: Optional[Path] = None,
    ) -> "TaskResult":
        if (
            self.config.target_build_phase is None
            or not self.config.target_build_phase.builds
        ):
            return TaskResult(success=True)
        if not self.__is_supported_target(target):
            return TaskResult(
                success=True,
                output=f"Skipping target {target.name} for CRS {self.name} as it is not supported.",
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
                        build_fetch_dir=build_fetch_dir,
                        diff_path=diff_path,
                        bug_candidate_dir=bug_candidate_dir,
                        input_hash=input_hash,
                        target_source_path=target_source_path,
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
                success=True,
                output=f"Skipping target {target.name} for CRS {self.name} as it is not supported.",
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
        build_fetch_dir: Optional[Path] = None,
        diff_path: Optional[Path] = None,
        bug_candidate_dir: Optional[Path] = None,
        input_hash: Optional[str] = None,
        target_source_path: Optional[Path] = None,
    ) -> "TaskResult":
        build_out_dir = self.work_dir.get_build_output_dir(
            self.name, target, build_id, sanitizer
        )
        input_cache_suffix = f".{input_hash}" if input_hash else ""
        build_cache_path = build_out_dir / f".{build_name}{input_cache_suffix}.cache"
        docker_compose_output = ""

        def prepare_docker_compose_file(
            progress, project_name: str, tmp_docker_compose: TmpDockerCompose
        ) -> "TaskResult":
            docker_compose_path = tmp_docker_compose.docker_compose
            assert docker_compose_path is not None
            rendered, warnings = renderer.render_build_target_docker_compose(
                self,
                target,
                target_base_image,
                build_config,
                build_out_dir,
                build_id,
                sanitizer,
                build_fetch_dir=build_fetch_dir,
                target_source_path=target_source_path,
            )
            for warning in warnings:
                progress.add_note(warning)
            docker_compose_path.write_text(rendered)
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
            docker_compose_path = tmp_docker_compose.docker_compose
            assert project_name is not None
            assert docker_compose_path is not None
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
            docker_compose_contents = docker_compose_path.read_text()
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
    if ret.output is None:
        return None
    layers_json = ret.output.strip()
    return hashlib.sha256(layers_json.encode()).hexdigest()[:12]
