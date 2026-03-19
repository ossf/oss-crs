"""Tests for __main__.py scan and drain logic."""

from queue import Queue
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seed_ensembler.__main__ import (
    _drain_new_seeds,
    _drain_crashes,
    _scan_and_batch_seeds,
)
from seed_ensembler.config import Configuration
from seed_ensembler.harness import Harness
from seed_ensembler.libfuzzer_result import LibfuzzerFailure, Sanitizer


class TestDrainNewSeeds:
    def test_copies_new_seeds_to_exchange(self, tmp_path):
        exchange_seeds = tmp_path / "exchange" / "seeds"
        exchange_seeds.mkdir(parents=True)

        # Simulate pool with queued results
        pool = MagicMock()
        q = Queue()
        pool.new_seeds_queue = q

        # Create a seed file that the pool "produced"
        seed_file = tmp_path / "coverage" / "abc123"
        seed_file.parent.mkdir(parents=True)
        seed_file.write_bytes(b"coverage seed")

        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        q.put((harness, [seed_file]))

        count = _drain_new_seeds(pool, exchange_seeds)

        assert count == 1
        assert (exchange_seeds / "abc123").read_bytes() == b"coverage seed"

    def test_skips_nonexistent_seeds(self, tmp_path):
        exchange_seeds = tmp_path / "exchange" / "seeds"
        exchange_seeds.mkdir(parents=True)

        pool = MagicMock()
        q = Queue()
        pool.new_seeds_queue = q

        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        q.put((harness, [Path("/nonexistent/seed")]))

        count = _drain_new_seeds(pool, exchange_seeds)
        assert count == 0

    def test_dedup_existing(self, tmp_path):
        exchange_seeds = tmp_path / "exchange" / "seeds"
        exchange_seeds.mkdir(parents=True)
        (exchange_seeds / "existing").write_bytes(b"old")

        pool = MagicMock()
        q = Queue()
        pool.new_seeds_queue = q

        seed_file = tmp_path / "new_seed"
        seed_file.write_bytes(b"new")
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        q.put((harness, [seed_file]))

        # "existing" already there, "new_seed" is new
        count = _drain_new_seeds(pool, exchange_seeds)
        assert count == 1
        assert (exchange_seeds / "existing").read_bytes() == b"old"

    def test_empty_queue(self, tmp_path):
        exchange_seeds = tmp_path / "exchange" / "seeds"
        exchange_seeds.mkdir(parents=True)

        pool = MagicMock()
        pool.new_seeds_queue = Queue()

        count = _drain_new_seeds(pool, exchange_seeds)
        assert count == 0


class TestDrainCrashes:
    def test_copies_crash_to_povs(self, tmp_path):
        exchange_povs = tmp_path / "exchange" / "povs"
        exchange_povs.mkdir(parents=True)

        pool = MagicMock()
        q = Queue()
        pool.crash_queue = q

        crash_file = tmp_path / "crash.bin"
        crash_file.write_bytes(b"crash input")

        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        failure = LibfuzzerFailure(
            input_path=crash_file,
            output_path=None,
            sanitizer=Sanitizer.ADDRESS,
            summary=b"heap-buffer-overflow",
        )
        q.put((harness, [failure]))

        count = _drain_crashes(pool, exchange_povs)
        assert count == 1
        # Should be named by MD5 of content
        files = list(exchange_povs.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == b"crash input"

    def test_skips_missing_input(self, tmp_path):
        exchange_povs = tmp_path / "exchange" / "povs"
        exchange_povs.mkdir(parents=True)

        pool = MagicMock()
        q = Queue()
        pool.crash_queue = q

        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        failure = LibfuzzerFailure(
            input_path=Path("/nonexistent"),
            output_path=None,
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        q.put((harness, [failure]))

        count = _drain_crashes(pool, exchange_povs)
        assert count == 0

    def test_skips_none_input(self, tmp_path):
        exchange_povs = tmp_path / "exchange" / "povs"
        exchange_povs.mkdir(parents=True)

        pool = MagicMock()
        q = Queue()
        pool.crash_queue = q

        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        failure = LibfuzzerFailure(
            input_path=None,
            output_path=None,
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        q.put((harness, [failure]))

        count = _drain_crashes(pool, exchange_povs)
        assert count == 0

    def test_empty_queue(self, tmp_path):
        exchange_povs = tmp_path / "exchange" / "povs"
        exchange_povs.mkdir(parents=True)

        pool = MagicMock()
        pool.crash_queue = Queue()

        count = _drain_crashes(pool, exchange_povs)
        assert count == 0


class TestScanAndBatchSeeds:
    def test_batches_new_seeds(self, tmp_path):
        config = Configuration(
            temp_dir=tmp_path / "temp",
            submit_root=tmp_path / "submit",
        )
        config.temp_dir.mkdir()

        # Create CRS submit dir with seeds
        seeds_dir = tmp_path / "submit" / "crsA" / "seeds"
        seeds_dir.mkdir(parents=True)
        (seeds_dir / "s1").write_bytes(b"seed1")
        (seeds_dir / "s2").write_bytes(b"seed2")

        pool = MagicMock()
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        seen = set()
        counter = [0]

        _scan_and_batch_seeds(config, pool, harness, seen, counter)

        pool.add_seeds_batch.assert_called_once()
        batch_dir, batch_harness = pool.add_seeds_batch.call_args[0]
        assert batch_harness == harness
        assert batch_dir.is_dir()
        batch_files = list(batch_dir.iterdir())
        assert len(batch_files) == 2

    def test_dedup_across_scans(self, tmp_path):
        config = Configuration(
            temp_dir=tmp_path / "temp",
            submit_root=tmp_path / "submit",
        )
        config.temp_dir.mkdir()

        seeds_dir = tmp_path / "submit" / "crsA" / "seeds"
        seeds_dir.mkdir(parents=True)
        (seeds_dir / "s1").write_bytes(b"seed1")

        pool = MagicMock()
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        seen = set()
        counter = [0]

        _scan_and_batch_seeds(config, pool, harness, seen, counter)
        assert pool.add_seeds_batch.call_count == 1

        # Second scan: same seeds, should not create new batch
        pool.reset_mock()
        _scan_and_batch_seeds(config, pool, harness, seen, counter)
        pool.add_seeds_batch.assert_not_called()

    def test_multiple_crs(self, tmp_path):
        config = Configuration(
            temp_dir=tmp_path / "temp",
            submit_root=tmp_path / "submit",
        )
        config.temp_dir.mkdir()

        for crs in ("crsA", "crsB"):
            seeds_dir = tmp_path / "submit" / crs / "seeds"
            seeds_dir.mkdir(parents=True)
            (seeds_dir / f"{crs}_seed").write_bytes(crs.encode())

        pool = MagicMock()
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))
        seen = set()
        counter = [0]

        _scan_and_batch_seeds(config, pool, harness, seen, counter)

        # All seeds from both CRSes in one batch (< batch_size=32)
        pool.add_seeds_batch.assert_called_once()
        batch_dir = pool.add_seeds_batch.call_args[0][0]
        assert len(list(batch_dir.iterdir())) == 2

    def test_empty_submit_dir(self, tmp_path):
        config = Configuration(
            temp_dir=tmp_path / "temp",
            submit_root=tmp_path / "submit",
        )
        config.temp_dir.mkdir()
        (tmp_path / "submit").mkdir()

        pool = MagicMock()
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))

        _scan_and_batch_seeds(config, pool, harness, set(), [0])
        pool.add_seeds_batch.assert_not_called()

    def test_skips_symlinks_in_seeds(self, tmp_path):
        config = Configuration(
            temp_dir=tmp_path / "temp",
            submit_root=tmp_path / "submit",
        )
        config.temp_dir.mkdir()

        seeds_dir = tmp_path / "submit" / "crsA" / "seeds"
        seeds_dir.mkdir(parents=True)
        real = seeds_dir / "real"
        real.write_bytes(b"data")
        (seeds_dir / "link").symlink_to(real)

        pool = MagicMock()
        harness = Harness(name="fuzzer", path_in_out_dir=Path("/out/fuzzer"))

        _scan_and_batch_seeds(config, pool, harness, set(), [0])

        batch_dir = pool.add_seeds_batch.call_args[0][0]
        assert len(list(batch_dir.iterdir())) == 1
