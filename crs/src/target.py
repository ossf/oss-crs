from pathlib import Path
from typing import Optional
import hashlib
import shutil
import subprocess
import tempfile
import git

from .config.target import TargetConfig
from .ui import MultiTaskProgress, TaskResult
from .utils import generate_random_name, rm_with_docker
from . import ui

TEMPLATES_DIR = Path(__file__).parent / "templates"
# oss-fuzz sparse checkout fetched by scripts/setup-third-party.sh
THIRD_PARTY_OSS_FUZZ = Path(__file__).parent.parent.parent / "third_party" / "oss-fuzz"

# Scripts sourced from oss-fuzz: dst_name → path relative to THIRD_PARTY_OSS_FUZZ
OSS_FUZZ_SCRIPTS = {
    "compile": "infra/base-images/base-builder/compile",
    "replay_build.sh": "infra/base-images/base-builder/replay_build.sh",
    "make_build_replayable.py": "infra/base-images/base-builder/make_build_replayable.py",
    "reproduce": "infra/base-images/base-runner/reproduce",
    "run_fuzzer": "infra/base-images/base-runner/run_fuzzer",
    "parse_options.py": "infra/base-images/base-runner/parse_options.py",
}

# Custom scripts from our templates directory.
CUSTOM_SCRIPTS = ["oss_crs_handler.sh", "oss_crs_builder_server.py"]


def _ensure_third_party_oss_fuzz() -> bool:
    """Auto-fetch oss-fuzz third-party scripts if not present."""
    if (THIRD_PARTY_OSS_FUZZ / ".git").is_dir():
        return True
    setup_script = THIRD_PARTY_OSS_FUZZ.parent.parent / "scripts" / "setup-third-party.sh"
    if not setup_script.exists():
        return False
    result = subprocess.run(
        ["bash", str(setup_script)],
        cwd=str(setup_script.parent.parent),
    )
    return result.returncode == 0


def extract_name_from_proj_path(proj_path: str) -> str:
    tmp = proj_path.split("/")
    return tmp[-1] or tmp[-2]


class Target:
    def __init__(
        self,
        work_dir: Path,
        proj_path: Path,
        repo_path: Optional[Path],
        no_checkout: bool = False,
        target_harness: Optional[str] = None,
    ):
        self.name = extract_name_from_proj_path(str(proj_path))
        self.work_dir = work_dir / "targets" / self.name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.proj_path = proj_path
        if repo_path:
            self.repo_path = repo_path
        else:
            self.repo_path = self.work_dir / "repo"
        self.config = TargetConfig.from_yaml_file(proj_path / "project.yaml")
        self.repo_hash: Optional[str] = None
        self.no_checkout = no_checkout
        self.target_harness = target_harness
        self.snapshot_image_tag: Optional[str] = None

    def get_docker_image_name(self) -> str:
        repo_hash = self.__get_repo_hash()
        return f"{self.name}:{repo_hash}"

    def build_docker_image(self) -> str:
        if not self.init_repo():
            return None
        repo_hash = self.__get_repo_hash()
        image_tag = self.get_docker_image_name()

        # Skip rebuild if the image already exists locally
        check = subprocess.run(
            ["docker", "image", "inspect", image_tag],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            return image_tag

        tasks = [
            (
                "Build docker image with the given repo",
                lambda progress: self.__build_docker_image_with_repo(
                    image_tag, progress
                ),
            ),
        ]

        with MultiTaskProgress(
            tasks, title=f"Building {self.name} docker image"
        ) as progress:
            progress.add_items_to_head(
                [
                    ui.bold(
                        f"Repo hash: {repo_hash} (calculated from {self.repo_path})"
                    ),
                    ui.bold(f"Image tag: {image_tag}"),
                    ui.yellow(
                        f"Note: /src/ will have contents in {self.repo_path}",
                        True,
                    ),
                ]
            )
            if progress.run_added_tasks().success:
                return image_tag
        return None

    def init_repo(self) -> bool:
        no_checkout = self.no_checkout
        title = f"Setting up Target {self.name}"
        head = [
            ui.bold(f"Init {self.name} repo into {self.repo_path}"),
            ui.yellow(
                "Please make sure the repo is accessible without typing your credentials.",
                True,
            ),
        ]
        tasks = []
        if self.repo_path.exists():
            head.append(ui.bold(f"--no-checkout: {no_checkout}, repo exists: True"))
            if no_checkout:
                head.append(ui.yellow("Skipping repository initialization."))
            else:
                head.append(ui.green("Fetching latest changes..."))
                tasks += [
                    ("Git fetch", lambda progress: self.__fetch_repo(progress)),
                    (
                        "Git checkout HEAD",
                        lambda progress: self.__checkout_HEAD(progress),
                    ),
                ]
        else:
            head.append(ui.green("Cloning repository..."))
            tasks += [
                ("Git clone", lambda progress: self.__clone(progress)),
            ]
        with MultiTaskProgress(tasks=tasks, title=title) as progress:
            progress.add_items_to_head(head)
            return progress.run_added_tasks().success

    def __fetch_repo(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = ["git", "fetch", "--recurse-submodules", "origin"]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path,
        )

    def __checkout_HEAD(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = ["git", "reset", "--hard", "origin/HEAD"]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path,
        )

    def __clone(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = [
            "git",
            "clone",
            "--recurse-submodules",
            self.config.main_repo,
            str(self.repo_path),
        ]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path.parent,
        )

    def __get_repo_hash(self) -> str:
        """
        Get a hash representing the current state of the repository.

        If the working directory is clean, returns the current commit hash.
        Otherwise, returns a hash combining the commit hash and the diff of changes.
        """
        if self.repo_hash is not None:
            return self.repo_hash
        repo = git.Repo(self.repo_path)
        commit_hash = repo.head.commit.hexsha

        # Check if working directory is clean
        if not repo.is_dirty(untracked_files=True):
            self.repo_hash = commit_hash[:12]
            return self.repo_hash

        # Dirty working directory - combine commit hash with changes
        hasher = hashlib.sha256()
        hasher.update(commit_hash.encode())

        # Get list of changed tracked files (modified + deleted + staged)
        changed_files = set()
        # Unstaged changes
        for diff_item in repo.index.diff(None):
            changed_files.add(diff_item.a_path)
        # Staged changes
        for diff_item in repo.index.diff("HEAD"):
            changed_files.add(diff_item.a_path)

        # Process tracked changed files in sorted order
        for file_path in sorted(changed_files):
            full_path = self.repo_path / file_path
            hasher.update(f"\n--- changed: {file_path}\n".encode())
            if full_path.exists() and full_path.is_file():
                try:
                    # Read file as binary to handle both text and binary files
                    hasher.update(full_path.read_bytes())
                except Exception:
                    hasher.update(b"<read-error>")
            else:
                # File was deleted
                hasher.update(b"<deleted>")

        # Process untracked files in sorted order
        for file_path in sorted(repo.untracked_files):
            full_path = self.repo_path / file_path
            if full_path.is_file():
                hasher.update(f"\n--- untracked: {file_path}\n".encode())
                try:
                    hasher.update(full_path.read_bytes())
                except Exception:
                    hasher.update(b"<read-error>")

        self.repo_hash = hasher.hexdigest()[:12]
        return self.repo_hash

    def __build_docker_image_with_repo(
        self, image_tag: str, progress: MultiTaskProgress
    ) -> "TaskResult":
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".Dockerfile", delete=True
        ) as tmp_dockerfile:
            added_dockerfile = (self.proj_path / "Dockerfile").read_bytes()
            added_dockerfile += b"\n# Added by CRS Target build\n"
            # Exclude volatile .git files that change on fetch/checkout but aren't needed for history:
            # - FETCH_HEAD: updated every fetch
            # - logs/: reflog entries (not needed for commit history)
            # - refs/remotes/: remote tracking branches (updated on fetch)
            # - ORIG_HEAD: updated on various operations
            # Core history is preserved in: objects/, refs/heads/, refs/tags/, HEAD
            added_dockerfile += (
                f"COPY --exclude=.git/FETCH_HEAD "
                f"--exclude=.git/logs "
                f"--exclude=.git/refs/remotes "
                f"--exclude=.git/ORIG_HEAD "
                f"--from=repo_path . .\n"
            ).encode()
            added_dockerfile += ("COPY . /project_dir\n").encode()
            tmp_dockerfile.write(added_dockerfile)
            tmp_dockerfile.flush()

            # TODO: We might need to consider cache options here later.

            cmd = [
                "docker",
                "buildx",
                "build",
                "--build-context",
                f"repo_path={self.repo_path}",
                "-t",
                image_tag,
                "-f",
                tmp_dockerfile.name,
                str(self.proj_path),
            ]
            return progress.run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.work_dir,
            )

    @staticmethod
    def _resolve_script_path(dst_name: str) -> Optional[Path]:
        """Resolve the source path for a script to copy into the snapshot.

        For oss-fuzz scripts: sourced from third_party/oss-fuzz/.
        For custom scripts: always uses templates directory.
        Returns None if the script cannot be found.
        """
        if dst_name in CUSTOM_SCRIPTS:
            return TEMPLATES_DIR / dst_name

        if dst_name in OSS_FUZZ_SCRIPTS:
            candidate = THIRD_PARTY_OSS_FUZZ / OSS_FUZZ_SCRIPTS[dst_name]
            if candidate.exists():
                return candidate
        return None

    def get_target_env(self) -> dict:
        # TODO: implement this properly
        ret = {
            "name": self.name,
            "language": self.config.language.value,
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
        }

        if self.target_harness:
            ret["harness"] = self.target_harness

        return ret

    def get_snapshot_image_name(self, sanitizer: str) -> str:
        repo_hash = self.__get_repo_hash()
        return f"{self.name}:{repo_hash}-{sanitizer}-snapshot"

    def create_snapshot(
        self, sanitizer: str, progress: MultiTaskProgress
    ) -> TaskResult:
        snapshot_tag = self.get_snapshot_image_name(sanitizer)
        target_base_image = self.get_docker_image_name()
        cache_file = self.work_dir / f".snapshot-{sanitizer}.cache"

        # Check cache: skip if snapshot already exists with matching base image hash
        base_image_hash = self._get_base_image_hash(target_base_image)
        if cache_file.exists() and base_image_hash:
            cached_hash = cache_file.read_text().strip()
            if cached_hash == base_image_hash:
                # Verify the snapshot image still exists
                check_result = subprocess.run(
                    ["docker", "image", "inspect", snapshot_tag],
                    capture_output=True, text=True,
                )
                if check_result.returncode == 0:
                    self.snapshot_image_tag = snapshot_tag
                    return TaskResult(
                        success=True,
                        output=f"Snapshot {snapshot_tag} already exists (cached)",
                    )

        container_name = f"{self.name}-snapshot-{sanitizer}-{generate_random_name(8)}"

        # Clean and create output directory (exclude fuzzer binaries from snapshot).
        # Use docker to remove root-owned files from previous snapshot builds.
        out_dir = self.work_dir / f"snapshot-out-{sanitizer}"
        if out_dir.exists():
            rm_with_docker(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        target_env = self.get_target_env()

        try:
            # Step 1: Run the container (no --rm) to compile the project
            run_cmd = [
                "docker", "run",
                f"--name={container_name}",
                f"--env=SANITIZER={sanitizer}",
                f"--env=FUZZING_ENGINE={target_env.get('engine', 'libfuzzer')}",
                f"--env=ARCHITECTURE={target_env.get('architecture', 'x86_64')}",
                f"--env=FUZZING_LANGUAGE={target_env.get('language', 'c')}",
                f"--env=PROJECT_NAME={self.name}",
                f"-v={out_dir}:/out",
                "--privileged",
                "--shm-size=2g",
                target_base_image,
                "bash", "-c", "compile",
            ]
            result = progress.run_command_with_streaming_output(
                cmd=run_cmd,
                cwd=self.work_dir,
                info_text=f"Compiling {self.name} for snapshot",
            )
            if not result.success:
                return result

            # Step 2: Copy scripts into stopped container.
            # oss-fuzz scripts come from third_party/; custom scripts from templates/.
            if not _ensure_third_party_oss_fuzz():
                return TaskResult(
                    success=False,
                    output="Failed to fetch oss-fuzz third-party scripts. "
                    "Try manually: bash scripts/setup-third-party.sh",
                )
            all_scripts = list(CUSTOM_SCRIPTS) + list(OSS_FUZZ_SCRIPTS)
            for dst_name in all_scripts:
                script_src = self._resolve_script_path(dst_name)
                if script_src is None:
                    return TaskResult(
                        success=False,
                        output=f"Cannot find source for script: {dst_name}.",
                    )
                cp_cmd = [
                    "docker", "cp",
                    str(script_src),
                    f"{container_name}:/usr/local/bin/{dst_name}",
                ]
                result = progress.run_command_with_streaming_output(
                    cmd=cp_cmd,
                    cwd=self.work_dir,
                    info_text=f"Copying {script_src.name} → {dst_name}",
                )
                if not result.success:
                    return result

            # Step 2b: Copy libCRS into stopped container
            libcrs_path = TEMPLATES_DIR.parent.parent.parent / "libCRS"
            cp_libcrs_cmd = [
                "docker", "cp",
                str(libcrs_path),
                f"{container_name}:/tmp/libCRS",
            ]
            result = progress.run_command_with_streaming_output(
                cmd=cp_libcrs_cmd,
                cwd=self.work_dir,
                info_text="Copying libCRS into container",
            )
            if not result.success:
                return result

            # Step 2c: Commit intermediate image, then run setup container
            intermediate_tag = f"{snapshot_tag}-intermediate"
            commit_intermediate_cmd = [
                "docker", "commit", container_name, intermediate_tag,
            ]
            result = progress.run_command_with_streaming_output(
                cmd=commit_intermediate_cmd,
                cwd=self.work_dir,
                info_text="Committing intermediate image",
            )
            if not result.success:
                return result

            # Run a setup container from intermediate to execute all setup commands:
            # - chmod all scripts
            # - copy replay_build.sh to $SRC/ (where compile expects it)
            # - install pip dependencies
            # - install libCRS
            setup_container_name = f"{container_name}-setup"
            # Build chmod targets programmatically from script constants
            chmod_targets = " ".join(
                f"/usr/local/bin/{s}" for s in all_scripts
            )
            chmod_cmd = f"chmod +x {chmod_targets}"
            setup_cmd = [
                "docker", "run",
                f"--name={setup_container_name}",
                "--privileged",
                intermediate_tag,
                "bash", "-c",
                # chmod all scripts in /usr/local/bin/ that we copied
                f"{chmod_cmd} && "
                # Place replay_build.sh where compile looks for it ($SRC/)
                "cp /usr/local/bin/replay_build.sh $SRC/replay_build.sh && "
                # Install Python dependencies for builder server
                "pip3 install fastapi uvicorn python-multipart && "
                # Install libCRS
                "bash /tmp/libCRS/install.sh",
            ]
            result = progress.run_command_with_streaming_output(
                cmd=setup_cmd,
                cwd=self.work_dir,
                info_text="Running setup (chmod, pip install, libCRS)",
            )
            if not result.success:
                return result

            # Step 3: Commit the setup container as final snapshot image
            commit_cmd = [
                "docker", "commit",
                "-c", "ENV REPLAY_ENABLED=1",
                setup_container_name,
                snapshot_tag,
            ]
            result = progress.run_command_with_streaming_output(
                cmd=commit_cmd,
                cwd=self.work_dir,
                info_text=f"Committing snapshot as {snapshot_tag}",
            )
            if not result.success:
                return result

            # Update cache
            if base_image_hash:
                cache_file.write_text(base_image_hash)

            self.snapshot_image_tag = snapshot_tag
            return TaskResult(success=True, output=f"Snapshot created: {snapshot_tag}")
        finally:
            # Always clean up both containers and the intermediate image
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["docker", "rm", "-f", f"{container_name}-setup"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["docker", "rmi", "-f", f"{snapshot_tag}-intermediate"],
                capture_output=True, text=True,
            )

    @staticmethod
    def _get_base_image_hash(image_tag: str) -> Optional[str]:
        """Get the content hash (image ID) of a Docker image."""
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_tag],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
