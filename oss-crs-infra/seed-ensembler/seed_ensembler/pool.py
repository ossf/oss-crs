"""Worker pool for parallel libfuzzer merge operations.

Manages multiple worker processes that test seeds for coverage
contribution using ``libfuzzer -merge=1``.  New coverage seeds and
crash-triggering seeds are reported via multiprocessing queues.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import shutil
import traceback
from dataclasses import dataclass
from multiprocessing import Process, Queue
from pathlib import Path
from queue import Empty
from typing import Iterator

from .config import Configuration
from .direct_handler import DirectEnvironment
from .harness import Harness
from .libfuzzer_handler import LibfuzzerEnvironment
from .libfuzzer_result import (
    LibfuzzerFailure,
    LibfuzzerMergeResult,
    Sanitizer,
)

log = logging.getLogger(__name__)

WORKER_QUEUE_MAX_SIZE = 1024

# Each individual seed gets this many seconds before being killed.
PER_SEED_TIMEOUT = 1.0
# Overall timeout = max(MIN_OVERALL_TIMEOUT, num_seeds * this).
OVERALL_TIMEOUT_SEED_FACTOR = 0.5
MIN_OVERALL_TIMEOUT = 10.0

MAIN_QUEUE_GET_TIMEOUT = 0.2
SLOW_SEEDS_QUEUE_GET_TIMEOUT = 0.2

# When re-testing slow seeds, multiply the scorable timeout by this.
TIMEOUT_BUFFER_FACTOR = 1.4


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SeedsBatch:
    """A directory of seeds to test for a specific harness."""

    path: Path
    harness: Harness
    script_id: int | None = None


@dataclass
class SlowSeed:
    """A single seed to re-test with a longer timeout."""

    path: Path
    harness: Harness


# ---------------------------------------------------------------------------
# Pure helpers (easily unit-tested)
# ---------------------------------------------------------------------------

def make_flat_symlink_tree(
    original_dir: Path,
    new_dir: Path,
    *,
    as_if: Path | None = None,
) -> None:
    """Create symlinks in *new_dir* pointing to all entries in *original_dir*.

    If *as_if* is provided, symlink targets are rewritten as if
    *original_dir* were at that path instead.  This is useful for creating
    symlinks that are valid inside a Docker container.
    """
    if as_if is not None:
        as_if = as_if.resolve()

    new_dir.mkdir(exist_ok=True)

    for item in original_dir.iterdir():
        src = new_dir / item.name
        dst = item.resolve()
        if as_if is not None:
            dst = as_if / dst.relative_to(original_dir.resolve())
        src.symlink_to(dst)


def map_failures_to_inputs(
    batch_path: Path,
    result: LibfuzzerMergeResult,
) -> Iterator[LibfuzzerFailure]:
    """Map libfuzzer crash artifacts back to the original input seeds.

    Yields ``LibfuzzerFailure`` objects with ``input_path`` updated to
    point to the corresponding seed file from *batch_path*.  Failures
    that cannot be mapped are logged and discarded.
    """
    batch_seed_paths = list(batch_path.iterdir())

    sha1_to_host_path: dict[str, Path] = {}
    batch_seeds_cache: dict[Path, bytes] = {}

    for failure in result.failures:
        # Easy case: input_path already determined from libfuzzer output.
        if failure.input_path is not None:
            yield failure
            continue

        # Heuristic: single seed + single failure = likely match.
        if len(result.failures) == 1 and len(batch_seed_paths) == 1:
            failure.input_path = batch_seed_paths[0]
            yield failure
            continue

        if failure.output_path is None:
            log.warning(
                "Discarding seed with no input and no output path"
            )
            continue

        # Discard failures attributed to the empty-string hash.
        if failure.output_path.name.endswith(
            "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        ):
            log.warning("Discarding failure attributed to empty seed")
            continue

        # Try to match by SHA-1 from the output path filename / contents.
        target_sha1s = {failure.output_path.name[-40:]}
        output_file_len = None
        try:
            output_file_data = failure.output_path.read_bytes()
            target_sha1s.add(
                hashlib.sha1(output_file_data).hexdigest()
            )
            output_file_len = len(output_file_data)
        except FileNotFoundError:
            pass

        prefix_matches: list[Path] = []

        for target_sha1 in target_sha1s:
            failure.input_path = sha1_to_host_path.get(target_sha1)

            if failure.input_path is None:
                for check_file in batch_path.iterdir():
                    check_data = batch_seeds_cache.get(check_file)
                    if check_data is None:
                        check_data = check_file.read_bytes()
                        batch_seeds_cache[check_file] = check_data

                    check_sha1 = hashlib.sha1(check_data).hexdigest()
                    sha1_to_host_path[check_sha1] = check_file

                    if check_sha1 == target_sha1:
                        failure.input_path = check_file
                        break

                    # Check if the output is a prefix of this seed.
                    if (
                        output_file_len is not None
                        and output_file_len < len(check_data)
                        and hashlib.sha1(
                            check_data[:output_file_len]
                        ).hexdigest()
                        == target_sha1
                    ):
                        prefix_matches.append(check_file)

            if failure.input_path is not None:
                break

        if failure.input_path is None and len(prefix_matches) == 1:
            failure.input_path = prefix_matches[0]

        if failure.input_path is None:
            log.warning(
                "Could not identify seed for crash (output=%s)",
                failure.output_path,
            )
        else:
            yield failure


# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------

class Worker(Process):
    """Worker process that runs libfuzzer merge operations."""

    def __init__(
        self,
        proc_i: int,
        input_queue: Queue,
        new_seeds_queue: Queue,
        crash_queue: Queue,
        slow_seeds_queue: Queue,
        coverage_seeds_dir: Path,
        crashing_seeds_dir: Path,
        temp_dir: Path,
        runner_image: str,
        *,
        direct: bool = False,
        verbose: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.proc_i = proc_i
        self.input_queue = input_queue
        self.new_seeds_queue = new_seeds_queue
        self.crash_queue = crash_queue
        self.slow_seeds_queue = slow_seeds_queue
        self.coverage_seeds_dir = coverage_seeds_dir
        self.crashing_seeds_dir = crashing_seeds_dir
        self.crashing_seeds_dir_size = 0
        self.temp_dir = temp_dir
        self.runner_image = runner_image
        self.direct = direct
        self.verbose = verbose

    def _get_queue_item(self) -> SeedsBatch | SlowSeed:
        """Retrieve a batch from the main queue, or a slow seed if idle."""
        while True:
            try:
                return self.input_queue.get(timeout=MAIN_QUEUE_GET_TIMEOUT)
            except Empty:
                if self.input_queue.qsize() == 0:
                    try:
                        return self.slow_seeds_queue.get(
                            timeout=SLOW_SEEDS_QUEUE_GET_TIMEOUT
                        )
                    except Empty:
                        continue
                else:
                    continue

    def run(self) -> None:
        """Main worker loop."""
        self.coverage_seeds_dir.mkdir(exist_ok=True)
        self.crashing_seeds_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)

        artifact_prefix = self.temp_dir / "artifact_prefix"

        if self.direct:
            direct_env = DirectEnvironment(
                artifact_prefix, verbose=self.verbose,
            )
            direct_env.set_up()
        else:
            libfuzzer_root = self.temp_dir / "libfuzzer_root"
            docker_env = LibfuzzerEnvironment(
                libfuzzer_root,
                artifact_prefix,
                self.runner_image,
                verbose=self.verbose,
            )
            docker_env.set_up()

        while True:
            try:
                item = self._get_queue_item()
                if isinstance(item, SeedsBatch):
                    if self.direct:
                        self._process_seeds_batch_direct(direct_env, item)
                    else:
                        self._process_seeds_batch(docker_env, item)
                else:
                    if self.direct:
                        self._process_slow_seed_direct(direct_env, item)
                    else:
                        self._process_slow_seed(docker_env, item)
            except Exception:
                log.error(traceback.format_exc())

    def _report_crashes(
        self, harness: Harness, failures: list[LibfuzzerFailure]
    ) -> None:
        """Put valid crash-triggering seeds on the crash queue."""
        if not failures:
            return

        valid = []
        for failure in failures:
            if failure.input_path is None:
                log.warning(
                    "Skipping crash for %s: no input path", harness.name
                )
                continue
            if not failure.input_path.exists():
                log.warning(
                    "Skipping crash for %s: %s not found",
                    harness.name,
                    failure.input_path,
                )
                continue
            valid.append(failure)

        if valid:
            self.crash_queue.put((harness, valid))

    def _process_seeds_batch(
        self, env: LibfuzzerEnvironment, batch: SeedsBatch
    ) -> None:
        """Test a batch of seeds for coverage and crash detection."""
        temp_coverage_dir = self.temp_dir / "coverage_seeds"
        shutil.rmtree(temp_coverage_dir, ignore_errors=True)

        if self.verbose:
            log.info("Processing %s", batch)

        harness_coverage_dir = self.coverage_seeds_dir / batch.harness.name
        harness_coverage_dir.mkdir(exist_ok=True)

        num_existing = len(list(harness_coverage_dir.iterdir()))
        num_new = len(list(batch.path.iterdir()))
        num_total = num_existing + num_new

        invoc = env.prepare_invocation(
            batch.harness.path_in_out_dir,
            [temp_coverage_dir, batch.path],
            [harness_coverage_dir],
            overall_timeout=max(
                MIN_OVERALL_TIMEOUT,
                num_total * OVERALL_TIMEOUT_SEED_FACTOR,
            ),
            per_seed_timeout=PER_SEED_TIMEOUT,
        )

        make_flat_symlink_tree(
            harness_coverage_dir,
            temp_coverage_dir,
            as_if=invoc.mounts.other_mount_dirs_guest[0],
        )

        invoc.mounts.artifact_prefix_host.mkdir(exist_ok=True)
        result = invoc.run_merge()

        # Move new coverage seeds to the shared coverage directory.
        new_seeds: list[Path] = []
        for file in temp_coverage_dir.iterdir():
            if not file.is_symlink():
                dst = harness_coverage_dir / file.name
                if not dst.is_file():
                    new_seeds.append(dst)
                    try:
                        os.rename(file, dst)
                    except OSError:
                        shutil.copy(file, dst)
                    except FileExistsError:
                        pass

        # Move crash seeds to a persistent directory.
        new_failures: list[LibfuzzerFailure] = []
        for failure in map_failures_to_inputs(batch.path, result):
            src = failure.input_path
            dst = (
                self.crashing_seeds_dir
                / f"{self.crashing_seeds_dir_size:09d}.bin"
            )
            self.crashing_seeds_dir_size += 1
            try:
                os.rename(src, dst)
            except FileNotFoundError:
                log.warning(
                    "Duplicate failure: %s already moved", src
                )
                continue
            except OSError:
                shutil.copy(src, dst)

            failure.input_path = dst
            new_failures.append(failure)

        result.failures = new_failures

        # Clean up temporary directories.
        shutil.rmtree(batch.path, ignore_errors=True)
        shutil.rmtree(temp_coverage_dir, ignore_errors=True)
        shutil.rmtree(
            invoc.mounts.artifact_prefix_host, ignore_errors=True
        )

        if self.verbose:
            log.info(
                "Batch done: %d new seeds, %d crashes",
                len(new_seeds),
                len(new_failures),
            )

        if new_seeds:
            self.new_seeds_queue.put((batch.harness, new_seeds))

        # Split crashes by category.
        timeouts, _exits, crashes = result.split_by_category()

        if batch.harness.scorable_timeout_duration is not None:
            for failure in timeouts.failures:
                if self.verbose:
                    log.info("Registering slow seed: %s", failure)
                self.slow_seeds_queue.put(
                    SlowSeed(failure.input_path, batch.harness)
                )

        self._report_crashes(batch.harness, crashes.failures)

    def _process_slow_seed(
        self, env: LibfuzzerEnvironment, seed: SlowSeed
    ) -> None:
        """Re-test a slow seed with an extended timeout."""
        if self.verbose:
            log.info("Processing slow seed: %s", seed)

        if seed.harness.scorable_timeout_duration is None:
            return

        long_timeout = (
            seed.harness.scorable_timeout_duration * TIMEOUT_BUFFER_FACTOR
        )

        invoc = env.prepare_invocation(
            seed.harness.path_in_out_dir,
            [],
            [seed.path.parent],
            overall_timeout=long_timeout,
            per_seed_timeout=long_timeout,
        )

        invoc.mounts.artifact_prefix_host.mkdir(exist_ok=True)
        result = invoc.run_single_exec(seed.path)

        if self.verbose:
            log.info("Slow seed done: %s", result)

        shutil.rmtree(
            invoc.mounts.artifact_prefix_host, ignore_errors=True
        )

        DEFAULT_SUMMARY = b"(timed out)"
        delete_seed = True

        if (
            result.was_aborted
            or result.execution_time >= long_timeout - 0.1
        ):
            failure = LibfuzzerFailure(
                seed.path, None, Sanitizer.TIMEOUT, DEFAULT_SUMMARY
            )
            self._report_crashes(seed.harness, [failure])
            delete_seed = False
        elif result.failure is not None and result.failure.is_timeout():
            if result.failure.summary is None:
                result.failure.summary = DEFAULT_SUMMARY
            self._report_crashes(seed.harness, [result.failure])
            delete_seed = False

        if delete_seed:
            seed.path.unlink(missing_ok=True)

    # ----- Direct execution mode (no Docker) -----

    def _process_seeds_batch_direct(
        self, env: DirectEnvironment, batch: SeedsBatch,
    ) -> None:
        """Test a batch of seeds using direct execution."""
        temp_coverage_dir = self.temp_dir / "coverage_seeds"
        shutil.rmtree(temp_coverage_dir, ignore_errors=True)

        if self.verbose:
            log.info("Processing (direct) %s", batch)

        harness_coverage_dir = self.coverage_seeds_dir / batch.harness.name
        harness_coverage_dir.mkdir(exist_ok=True)

        num_existing = len(list(harness_coverage_dir.iterdir()))
        num_new = len(list(batch.path.iterdir()))
        num_total = num_existing + num_new

        # For direct execution, symlink existing corpus into temp dir.
        # libfuzzer -merge takes: <dest_corpus> <new_seeds>
        # It merges new_seeds into dest_corpus, keeping only coverage-adding ones.
        temp_coverage_dir.mkdir(exist_ok=True)
        for item in harness_coverage_dir.iterdir():
            (temp_coverage_dir / item.name).symlink_to(item.resolve())

        env.artifact_dir.mkdir(parents=True, exist_ok=True)

        result = env.run_merge(
            batch.harness.path_in_out_dir,
            [temp_coverage_dir, batch.path],
            per_seed_timeout=PER_SEED_TIMEOUT,
            overall_timeout=max(
                MIN_OVERALL_TIMEOUT,
                num_total * OVERALL_TIMEOUT_SEED_FACTOR,
            ),
        )

        # New coverage seeds: non-symlink files in temp_coverage_dir.
        new_seeds: list[Path] = []
        for file in temp_coverage_dir.iterdir():
            if not file.is_symlink():
                dst = harness_coverage_dir / file.name
                if not dst.is_file():
                    new_seeds.append(dst)
                    try:
                        os.rename(file, dst)
                    except OSError:
                        shutil.copy(file, dst)

        # Map crash artifacts back to input seeds.
        new_failures: list[LibfuzzerFailure] = []
        for failure in map_failures_to_inputs(batch.path, result):
            src = failure.input_path
            dst = (
                self.crashing_seeds_dir
                / f"{self.crashing_seeds_dir_size:09d}.bin"
            )
            self.crashing_seeds_dir_size += 1
            try:
                os.rename(src, dst)
            except FileNotFoundError:
                log.warning("Duplicate failure: %s already moved", src)
                continue
            except OSError:
                shutil.copy(src, dst)
            failure.input_path = dst
            new_failures.append(failure)

        result.failures = new_failures

        shutil.rmtree(batch.path, ignore_errors=True)
        shutil.rmtree(temp_coverage_dir, ignore_errors=True)
        shutil.rmtree(env.artifact_dir, ignore_errors=True)
        env.artifact_dir.mkdir(exist_ok=True)

        if self.verbose:
            log.info(
                "Batch done (direct): %d new seeds, %d crashes",
                len(new_seeds), len(new_failures),
            )

        if new_seeds:
            self.new_seeds_queue.put((batch.harness, new_seeds))

        timeouts, _exits, crashes = result.split_by_category()
        if batch.harness.scorable_timeout_duration is not None:
            for failure in timeouts.failures:
                self.slow_seeds_queue.put(
                    SlowSeed(failure.input_path, batch.harness)
                )
        self._report_crashes(batch.harness, crashes.failures)

    def _process_slow_seed_direct(
        self, env: DirectEnvironment, seed: SlowSeed,
    ) -> None:
        """Re-test a slow seed with extended timeout (direct execution)."""
        if self.verbose:
            log.info("Processing slow seed (direct): %s", seed)

        if seed.harness.scorable_timeout_duration is None:
            return

        long_timeout = (
            seed.harness.scorable_timeout_duration * TIMEOUT_BUFFER_FACTOR
        )

        result = env.run_single_exec(
            seed.harness.path_in_out_dir,
            seed.path,
            per_seed_timeout=long_timeout,
            overall_timeout=long_timeout,
        )

        if self.verbose:
            log.info("Slow seed done (direct): %s", result)

        DEFAULT_SUMMARY = b"(timed out)"
        delete_seed = True

        if (
            result.was_aborted
            or result.execution_time >= long_timeout - 0.1
        ):
            failure = LibfuzzerFailure(
                seed.path, None, Sanitizer.TIMEOUT, DEFAULT_SUMMARY
            )
            self._report_crashes(seed.harness, [failure])
            delete_seed = False
        elif result.failure is not None and result.failure.is_timeout():
            if result.failure.summary is None:
                result.failure.summary = DEFAULT_SUMMARY
            self._report_crashes(seed.harness, [result.failure])
            delete_seed = False

        if delete_seed:
            seed.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

class LibfuzzerPool:
    """Pool of worker processes for parallel seed testing.

    Args:
        config: Ensembler configuration.

    Attributes:
        new_seeds_queue: ``(Harness, list[Path])`` tuples for seeds that
            added new coverage.
        crash_queue: ``(Harness, list[LibfuzzerFailure])`` tuples for
            crash-triggering seeds.
    """

    def __init__(self, config: Configuration, *, direct: bool = False):
        # runner_image required only in Docker mode
        if not direct and config.runner_image is None:
            raise ValueError(
                "runner_image is required for LibfuzzerPool in Docker mode"
            )

        self.config = config
        self.direct = direct
        self.workers: list[Worker] = []
        self.input_queues: list[Queue] = []
        self.slow_seeds_queue: Queue = Queue()
        self.new_seeds_queue: Queue = Queue()
        self.crash_queue: Queue = Queue()

        coverage_seeds_dir = config.temp_dir / "coverage_seeds"
        coverage_seeds_dir.mkdir(exist_ok=True, parents=True)

        for i in range(config.worker_pool_size):
            input_queue: Queue = Queue(WORKER_QUEUE_MAX_SIZE)
            self.input_queues.append(input_queue)

            worker_crashing_dir = (
                config.temp_dir / f"proc_{i:03d}_crashes"
            )
            worker_temp_dir = config.temp_dir / f"proc_{i:03d}_temp"

            shutil.rmtree(worker_crashing_dir, ignore_errors=True)

            # Avoid reusing temp dirs that may have stale mounts.
            j = 2
            while worker_temp_dir.is_dir():
                worker_temp_dir = (
                    config.temp_dir / f"proc_{i:03d}_temp_{j}"
                )
                j += 1

            worker = Worker(
                i,
                input_queue,
                self.new_seeds_queue,
                self.crash_queue,
                self.slow_seeds_queue,
                coverage_seeds_dir,
                worker_crashing_dir,
                worker_temp_dir,
                config.runner_image or "unused",
                direct=direct,
                verbose=config.verbose,
            )
            self.workers.append(worker)

        for w in self.workers:
            w.start()

    def add_seeds_batch(
        self,
        path: Path,
        harness: Harness,
        script_id: int | None = None,
    ) -> None:
        """Submit a directory of seeds for testing."""
        batch = SeedsBatch(path, harness, script_id)
        random.choice(self.input_queues).put(batch)
