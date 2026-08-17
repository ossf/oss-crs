from pathlib import Path

from oss_crs.src.workdir import WorkDir


class _Target:
    target_harness = None

    def get_docker_image_name(self) -> str:
        return "mock-target:abc123"

    def get_workdir_target_key(self) -> str:
        return self.get_docker_image_name().replace(":", "_")


def test_run_artifact_dirs_use_no_harness_scope(tmp_path: Path) -> None:
    work_dir = WorkDir(tmp_path)
    target = _Target()

    submit_dir = work_dir.get_submit_dir("crs-a", target, "run-1", "address")
    shared_dir = work_dir.get_shared_dir("crs-a", target, "run-1", "address")
    log_dir = work_dir.get_log_dir("crs-a", target, "run-1", "address")
    exchange_dir = work_dir.get_exchange_dir(target, "run-1", "address")
    run_logs_dir = work_dir.get_run_logs_dir(target, "run-1", "address")

    assert submit_dir.name == WorkDir.NO_HARNESS_SCOPE
    assert shared_dir.name == WorkDir.NO_HARNESS_SCOPE
    assert log_dir.name == WorkDir.NO_HARNESS_SCOPE
    assert exchange_dir.name == WorkDir.NO_HARNESS_SCOPE
    assert run_logs_dir.name == WorkDir.NO_HARNESS_SCOPE
