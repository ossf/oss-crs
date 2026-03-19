"""Libfuzzer execution environments for running harness binaries.

Provides Docker-based execution of libfuzzer merge and single-seed
operations.  Each invocation mounts seed directories, build output, and
working directories into an isolated container.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .libfuzzer_result import LibfuzzerMergeResult, LibfuzzerSingleExecResult
from .util import compress_str, fs_copy

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mount mapping
# ---------------------------------------------------------------------------

@dataclass
class LibfuzzerMounts:
    """Host/guest path pairs for all directories mounted into the container."""

    out_dir_host: Path
    out_dir_guest: Path
    work_dir_host: Path
    work_dir_guest: Path
    artifact_prefix_host: Path
    artifact_prefix_guest: Path
    seed_dirs_host: list[Path]
    seed_dirs_guest: list[Path]
    other_mount_dirs_host: list[Path]
    other_mount_dirs_guest: list[Path]

    def iter_all_pairs(self) -> Iterator[tuple[Path, Path]]:
        yield self.out_dir_host, self.out_dir_guest
        yield self.work_dir_host, self.work_dir_guest
        yield self.artifact_prefix_host, self.artifact_prefix_guest
        for host, guest in zip(self.seed_dirs_host, self.seed_dirs_guest):
            yield host, guest
        for host, guest in zip(
            self.other_mount_dirs_host, self.other_mount_dirs_guest
        ):
            yield host, guest

    def host_to_guest(self, path: Path) -> Path:
        for host, guest in self.iter_all_pairs():
            if path.is_relative_to(host):
                return guest / path.relative_to(host)
        return path

    def guest_to_host(self, path: Path) -> Path:
        for host, guest in self.iter_all_pairs():
            if path.is_relative_to(guest):
                return host / path.relative_to(guest)
        return path


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Timeouts:
    """Timeout configuration for a libfuzzer invocation."""

    overall: float | None
    per_seed: float | None


class LibfuzzerInvocation:
    """A prepared libfuzzer invocation ready to execute."""

    def __init__(
        self,
        environment: LibfuzzerEnvironment,
        timeouts: Timeouts,
        mounts: LibfuzzerMounts,
        harness_filename: str,
    ):
        self.environment = environment
        self.timeouts = timeouts
        self.mounts = mounts
        self.harness_filename = harness_filename

    def _merge_args(self) -> list[str]:
        """Build libfuzzer ``-merge`` arguments."""
        args = [
            "-merge=1",
            f"-artifact_prefix={self.mounts.artifact_prefix_guest}/",
        ]
        if self.timeouts.per_seed is not None:
            args.append(f"-timeout={round(self.timeouts.per_seed)}")
        for d in self.mounts.seed_dirs_guest:
            args.append(str(d))
        return args

    def _single_exec_args(self, seed: Path) -> list[str]:
        """Build libfuzzer single-execution arguments."""
        args = [f"-artifact_prefix={self.mounts.artifact_prefix_guest}/"]
        if self.timeouts.per_seed is not None:
            args.append(f"-timeout={round(self.timeouts.per_seed)}")
        args.append(str(seed))
        return args

    def _docker_cmd_prefix(self) -> list[str]:
        """Build the ``docker run`` prefix with volume mounts."""
        cmd = ["docker", "run", "--rm"]

        for dir_host, dir_guest in self.mounts.iter_all_pairs():
            cmd += ["-v", f"{dir_host}:{dir_guest}"]

        cmd += [
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--shm-size=2g",
            "--workdir",
            str(self.mounts.out_dir_guest),
            self.environment.runner_image,
            str(self.mounts.out_dir_guest / self.harness_filename),
        ]

        return cmd

    def _map_failure_paths(
        self,
        result: LibfuzzerMergeResult | LibfuzzerSingleExecResult,
    ) -> None:
        """Translate guest paths in failure records back to host paths."""
        if isinstance(result, LibfuzzerSingleExecResult):
            failures = [result.failure] if result.failure else []
        else:
            failures = result.failures

        for failure in failures:
            if failure.input_path is not None:
                failure.input_path = self.mounts.guest_to_host(
                    failure.input_path
                )
            if failure.output_path is not None:
                failure.output_path = self.mounts.guest_to_host(
                    failure.output_path
                )

    def run_merge(self) -> LibfuzzerMergeResult:
        """Execute ``libfuzzer -merge=1`` and return parsed results."""
        if len(self.mounts.seed_dirs_guest) < 2:
            raise RuntimeError(
                "libfuzzer -merge requires at least two seed directories"
            )

        cmd = self._docker_cmd_prefix() + self._merge_args()

        if self.environment.verbose:
            log.info("docker cmd: %s", shlex.join(cmd))
        else:
            log.info("docker cmd: %s", compress_str(shlex.join(cmd)))

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                timeout=self.timeouts.overall,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            elapsed = time.time() - start_time

            if self.environment.verbose:
                log.info("libfuzzer output: %r", proc.stderr)

            result = LibfuzzerMergeResult.from_stderr(
                proc.stderr,
                return_code=proc.returncode,
                execution_time=elapsed,
            )

        except subprocess.TimeoutExpired as e:
            elapsed = time.time() - start_time
            if self.environment.verbose:
                log.info("libfuzzer output (timeout): %r", e.stderr)
            result = LibfuzzerMergeResult.from_stderr(
                e.stderr or b"",
                execution_time=elapsed,
                was_aborted=True,
            )

        result.deduplicate()
        self._map_failure_paths(result)
        return result

    def run_single_exec(self, seed: Path) -> LibfuzzerSingleExecResult:
        """Execute a single seed and return parsed results."""
        cmd = self._docker_cmd_prefix() + self._single_exec_args(
            self.mounts.host_to_guest(seed)
        )
        log.info("%s", shlex.join(cmd))

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                timeout=self.timeouts.overall,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            elapsed = time.time() - start_time

            if self.environment.verbose:
                log.info("libfuzzer output: %r", proc.stderr)

            result = LibfuzzerSingleExecResult.from_path_and_stderr(
                seed,
                proc.stderr,
                return_code=proc.returncode,
                execution_time=elapsed,
            )

        except subprocess.TimeoutExpired as e:
            elapsed = time.time() - start_time
            if self.environment.verbose:
                log.info("libfuzzer output (timeout): %r", e.stderr)
            result = LibfuzzerSingleExecResult.from_path_and_stderr(
                seed,
                e.stderr or b"",
                execution_time=elapsed,
                was_aborted=True,
            )

        self._map_failure_paths(result)
        return result


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

class LibfuzzerEnvironment:
    """Docker-based environment for running libfuzzer harnesses.

    Sets up host-side directories and prepares invocations that mount
    them into ephemeral Docker containers.  Works with both native
    C/C++ harnesses (ELF binaries) and Jazzer harnesses (shell scripts
    wrapping ``jazzer_driver``), as long as the runner image includes
    the necessary runtime (JVM for Jazzer).

    Args:
        guest_root_dir: Host directory used as the root for guest mounts.
        guest_artifact_prefix_dir: Host directory for crash artifacts.
        runner_image: Docker image name containing the harness runtime.
        verbose: Enable debug-level logging of libfuzzer output.
    """

    def __init__(
        self,
        guest_root_dir: Path,
        guest_artifact_prefix_dir: Path,
        runner_image: str,
        *,
        verbose: bool = False,
    ):
        self.guest_root_dir = guest_root_dir
        self.guest_artifact_prefix_dir = guest_artifact_prefix_dir
        self.runner_image = runner_image
        self.verbose = verbose

    def set_up(self) -> None:
        """Create the guest root directory."""
        self.guest_root_dir.mkdir(exist_ok=True)

    def prepare_invocation(
        self,
        harness_path_in_out_dir: Path,
        seed_dirs: list[Path],
        other_mount_dirs: list[Path],
        *,
        per_seed_timeout: float | None = None,
        overall_timeout: float | None = None,
    ) -> LibfuzzerInvocation:
        """Prepare a libfuzzer invocation with the given seeds and harness.

        Copies the harness's parent directory to a staging area that will
        be mounted as ``/out`` inside the container, and creates a temporary
        working directory for the harness to use.
        """
        mounts = LibfuzzerMounts(
            out_dir_host=self.guest_root_dir / "out",
            out_dir_guest=Path("/out"),
            work_dir_host=self.guest_root_dir / "work",
            work_dir_guest=Path("/work"),
            artifact_prefix_host=self.guest_artifact_prefix_dir,
            artifact_prefix_guest=Path("/artifact_prefix"),
            seed_dirs_host=seed_dirs,
            seed_dirs_guest=[
                Path(f"/seeds_{i + 1:03d}") for i in range(len(seed_dirs))
            ],
            other_mount_dirs_host=other_mount_dirs,
            other_mount_dirs_guest=[
                Path(f"/other_{i + 1:03d}")
                for i in range(len(other_mount_dirs))
            ],
        )

        # Prepare /out dir (copy harness binary and siblings).
        if mounts.out_dir_host.is_dir():
            shutil.rmtree(mounts.out_dir_host)
        fs_copy(harness_path_in_out_dir.parent, mounts.out_dir_host)

        # Prepare /work dir (harness may create files here at runtime).
        if mounts.work_dir_host.is_dir():
            shutil.rmtree(mounts.work_dir_host)
        mounts.work_dir_host.mkdir()

        timeouts = Timeouts(overall_timeout, per_seed_timeout)
        return LibfuzzerInvocation(
            self, timeouts, mounts, harness_path_in_out_dir.name
        )
