# SPDX-License-Identifier: MIT
"""Unit tests for scripts/verify-patch-all.py helpers and entrypoint."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify-patch-all.py"
_spec = importlib.util.spec_from_file_location("verify_patch_all", _SCRIPT)
vpa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vpa)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _make_benchmark(
    root: Path,
    name: str,
    harnesses: list[dict],
    cpvs: dict[tuple[str, str], tuple[list[str], list[str]]] | None = None,
    write_meta: bool = True,
) -> Path:
    """Create ``root/<name>/.aixcc`` with meta.yaml and per-cpv patch/blob files.

    ``cpvs`` maps (harness, vuln_keyword) -> (patch_names, blob_names). A key
    that is absent creates no directories for that cpv.
    """
    proj = root / name
    (proj / ".aixcc").mkdir(parents=True)
    if write_meta:
        (proj / ".aixcc" / "meta.yaml").write_text(
            yaml.safe_dump({"harness_files": harnesses})
        )
    for (harness, cpv), (patches, blobs) in (cpvs or {}).items():
        cpv_dir = proj / ".aixcc" / harness / cpv
        (cpv_dir / "patches").mkdir(parents=True, exist_ok=True)
        (cpv_dir / "blobs").mkdir(parents=True, exist_ok=True)
        for p in patches:
            _touch(cpv_dir / "patches" / p)
        for b in blobs:
            _touch(cpv_dir / "blobs" / b)
    return proj


def _harness(name: str, *vulns: str) -> dict:
    return {"name": name, "vulns": [{"vuln_keyword": v} for v in vulns]}


# --------------------------------------------------------------------------
# Module setup
# --------------------------------------------------------------------------


def test_compose_file_is_written_with_expected_content():
    assert vpa.COMPOSE.is_file()
    assert vpa.COMPOSE.read_text() == vpa.COMPOSE_CONTENT


def test_compose_content_is_valid_yaml_with_expected_services():
    data = yaml.safe_load(vpa.COMPOSE_CONTENT)

    assert data["run_env"] == "local"
    assert data["docker_registry"] == "local"
    assert "oss_crs_infra" in data
    assert "builder-sidecar-lite" in data


def test_project_root_and_script_paths():
    assert vpa.PROJECT_ROOT == _SCRIPT.parent.parent
    assert vpa.SCRIPT == _SCRIPT.parent / "verify-patch-inc.sh"


# --------------------------------------------------------------------------
# discover_targets
# --------------------------------------------------------------------------


def test_discover_targets_empty_dir(tmp_path):
    assert list(vpa.discover_targets(tmp_path)) == []


def test_discover_targets_yields_first_patch_and_blobs_dir(tmp_path):
    proj = _make_benchmark(
        tmp_path,
        "bench-a",
        [_harness("h1", "cpv-1")],
        {("h1", "cpv-1"): (["b.diff", "a.diff"], ["blob2", "blob1"])},
    )

    targets = list(vpa.discover_targets(tmp_path))

    cpv_dir = proj / ".aixcc" / "h1" / "cpv-1"
    assert targets == [(proj, "h1", cpv_dir / "patches" / "a.diff", cpv_dir / "blobs")]


def test_discover_targets_skips_projects_without_meta(tmp_path):
    _make_benchmark(tmp_path, "no-meta", [], write_meta=False)
    (tmp_path / "no-aixcc").mkdir()
    _touch(tmp_path / "stray-file.txt")

    assert list(vpa.discover_targets(tmp_path)) == []


def test_discover_targets_projects_are_sorted(tmp_path):
    for name in ("zeta", "alpha", "mid"):
        _make_benchmark(
            tmp_path,
            name,
            [_harness("h", "c")],
            {("h", "c"): (["p"], ["b"])},
        )

    assert [t[0].name for t in vpa.discover_targets(tmp_path)] == [
        "alpha",
        "mid",
        "zeta",
    ]


def test_discover_targets_only_first_cpv_per_benchmark(tmp_path):
    _make_benchmark(
        tmp_path,
        "bench",
        [_harness("h1", "cpv-1", "cpv-2")],
        {
            ("h1", "cpv-1"): (["p1"], ["b1"]),
            ("h1", "cpv-2"): (["p2"], ["b2"]),
        },
    )

    targets = list(vpa.discover_targets(tmp_path))

    assert len(targets) == 1
    assert targets[0][2].name == "p1"


def test_discover_targets_only_first_harness_per_benchmark(tmp_path):
    _make_benchmark(
        tmp_path,
        "bench",
        [_harness("h1", "c"), _harness("h2", "c")],
        {("h1", "c"): (["p"], ["b"]), ("h2", "c"): (["p"], ["b"])},
    )

    assert [t[1] for t in vpa.discover_targets(tmp_path)] == ["h1"]


def test_discover_targets_falls_through_to_next_usable_cpv(tmp_path):
    _make_benchmark(
        tmp_path,
        "bench",
        [_harness("h1", "no-dirs", "empty-patches", "empty-blobs", "good")],
        {
            ("h1", "empty-patches"): ([], ["b"]),
            ("h1", "empty-blobs"): (["p"], []),
            ("h1", "good"): (["good.diff"], ["b"]),
        },
    )

    targets = list(vpa.discover_targets(tmp_path))

    assert len(targets) == 1
    assert targets[0][2].parent.parent.name == "good"


def test_discover_targets_falls_through_to_next_harness(tmp_path):
    _make_benchmark(
        tmp_path,
        "bench",
        [_harness("h1", "nothing"), _harness("h2", "ok")],
        {("h2", "ok"): (["p"], ["b"])},
    )

    assert [t[1] for t in vpa.discover_targets(tmp_path)] == ["h2"]


def test_discover_targets_requires_both_patches_and_blobs_dirs(tmp_path):
    proj = _make_benchmark(tmp_path, "bench", [_harness("h", "c")])
    # patches dir exists, blobs dir does not
    _touch(proj / ".aixcc" / "h" / "c" / "patches" / "p")

    assert list(vpa.discover_targets(tmp_path)) == []


@pytest.mark.parametrize(
    "meta",
    [{}, {"harness_files": []}, {"harness_files": [{"name": "h"}]}],
)
def test_discover_targets_meta_without_vulns_yields_nothing(tmp_path, meta):
    proj = tmp_path / "bench"
    (proj / ".aixcc").mkdir(parents=True)
    (proj / ".aixcc" / "meta.yaml").write_text(yaml.safe_dump(meta))

    assert list(vpa.discover_targets(tmp_path)) == []


def test_discover_targets_is_lazy_generator(tmp_path):
    _make_benchmark(
        tmp_path, "b", [_harness("h", "c")], {("h", "c"): (["p"], ["b"])}
    )

    gen = vpa.discover_targets(tmp_path)

    assert iter(gen) is gen


# --------------------------------------------------------------------------
# get_artifacts
# --------------------------------------------------------------------------


@pytest.fixture
def fake_run(monkeypatch):
    """Patch subprocess.run and record calls; behavior set via .result/.raises."""
    state = SimpleNamespace(calls=[], result=None, raises=None)

    def run(cmd, **kwargs):
        state.calls.append((cmd, kwargs))
        if state.raises is not None:
            raise state.raises
        return state.result

    monkeypatch.setattr(vpa.subprocess, "run", run)
    return state


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_get_artifacts_returns_parsed_json_and_builds_command(fake_run, tmp_path):
    fake_run.result = _completed(json.dumps({"crs": {"a": {}}}))
    proj = tmp_path / "proj"

    result = vpa.get_artifacts(proj, "harness-1", "vp-proj")

    assert result == {"crs": {"a": {}}}
    (cmd, kwargs), = fake_run.calls
    assert cmd == [
        "uv",
        "run",
        "oss-crs",
        "artifacts",
        "--compose-file",
        str(vpa.COMPOSE),
        "--fuzz-proj-path",
        str(proj),
        "--target-harness",
        "harness-1",
        "--build-id",
        "vp-proj",
        "--run-id",
        "vp-proj",
    ]
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "cwd": str(vpa.PROJECT_ROOT),
    }


def test_get_artifacts_nonzero_exit_returns_none(fake_run, tmp_path):
    fake_run.result = _completed(json.dumps({"ok": True}), returncode=1)

    assert vpa.get_artifacts(tmp_path, "h", "b") is None


def test_get_artifacts_invalid_json_returns_none(fake_run, tmp_path):
    fake_run.result = _completed("not json")

    assert vpa.get_artifacts(tmp_path, "h", "b") is None


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd="uv", timeout=30),
        FileNotFoundError("uv"),
        OSError("boom"),
    ],
)
def test_get_artifacts_subprocess_errors_return_none(fake_run, tmp_path, exc):
    fake_run.raises = exc

    assert vpa.get_artifacts(tmp_path, "h", "b") is None


# --------------------------------------------------------------------------
# get_timing_from_artifacts
# --------------------------------------------------------------------------


def _artifacts_for(log_dir: Path, *extra_crs: tuple[str, Path]) -> dict:
    crs = {"crs-a": {"log_dir": str(log_dir)}}
    for name, d in extra_crs:
        crs[name] = {"log_dir": str(d)}
    return {"crs": crs}


def test_get_timing_reads_json_from_log_dir(tmp_path):
    _touch(
        tmp_path / "verify_patch_timing.json",
        json.dumps({"rebuild": 1.5, "test": 2.5}),
    )

    assert vpa.get_timing_from_artifacts(_artifacts_for(tmp_path)) == {
        "rebuild": 1.5,
        "test": 2.5,
    }


def test_get_timing_missing_file_returns_none(tmp_path):
    assert vpa.get_timing_from_artifacts(_artifacts_for(tmp_path)) is None


def test_get_timing_invalid_json_returns_none(tmp_path):
    _touch(tmp_path / "verify_patch_timing.json", "{broken")

    assert vpa.get_timing_from_artifacts(_artifacts_for(tmp_path)) is None


@pytest.mark.parametrize("artifacts", [{}, {"crs": {}}, {"crs": None}, None])
def test_get_timing_malformed_artifacts_return_none(artifacts):
    assert vpa.get_timing_from_artifacts(artifacts) is None


def test_get_timing_uses_first_crs_only(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _touch(first / "verify_patch_timing.json", json.dumps({"rebuild": 1}))
    _touch(second / "verify_patch_timing.json", json.dumps({"rebuild": 99}))

    result = vpa.get_timing_from_artifacts(
        _artifacts_for(first, ("crs-b", second))
    )

    assert result == {"rebuild": 1}


def test_get_timing_missing_log_dir_key_looks_in_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert vpa.get_timing_from_artifacts({"crs": {"crs-a": {}}}) is None

    _touch(tmp_path / "verify_patch_timing.json", json.dumps({"test": 3}))
    assert vpa.get_timing_from_artifacts({"crs": {"crs-a": {}}}) == {"test": 3}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


@pytest.fixture
def harness_env(monkeypatch, tmp_path):
    """Run main() against a temp benchmarks dir with subprocess/artifacts faked."""
    benchmarks = tmp_path / "benchmarks"
    benchmarks.mkdir()
    state = SimpleNamespace(
        benchmarks=benchmarks,
        script_calls=[],
        returncodes={},  # project name -> script exit code (default 0)
        artifacts={},  # project name -> artifacts dict (default None)
    )

    def run(cmd, **kwargs):
        state.script_calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=state.returncodes.get(Path(cmd[1]).name, 0))

    monkeypatch.setattr(vpa, "BENCHMARKS_DIR", benchmarks)
    monkeypatch.setattr(vpa, "SELECTED_BENCHMARKS", None)
    monkeypatch.setattr(vpa.subprocess, "run", run)
    monkeypatch.setattr(
        vpa,
        "get_artifacts",
        lambda proj, harness, build_id: state.artifacts.get(proj.name),
    )
    return state


def _run_main(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["verify-patch-all.py", *args])
    return vpa.main()


def _add_bench(env, name: str) -> Path:
    return _make_benchmark(
        env.benchmarks,
        name,
        [_harness("h", "c")],
        {("h", "c"): (["fix.diff"], ["blob"])},
    )


def test_main_missing_benchmarks_dir_returns_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vpa, "BENCHMARKS_DIR", tmp_path / "missing")

    rc = _run_main(monkeypatch)

    assert rc == 1
    assert "Benchmarks dir not found" in capsys.readouterr().err


def test_main_all_pass_returns_0_and_invokes_script(harness_env, monkeypatch, capsys):
    proj = _add_bench(harness_env, "bench-a")

    rc = _run_main(monkeypatch)

    out = capsys.readouterr().out
    assert rc == 0
    assert "Running 1 benchmarks" in out
    assert "PASS  bench-a (h)" in out
    assert "1/1 passed" in out
    (cmd, kwargs), = harness_env.script_calls
    cpv_dir = proj / ".aixcc" / "h" / "c"
    assert cmd == [
        str(vpa.SCRIPT),
        str(proj),
        "h",
        str(cpv_dir / "patches" / "fix.diff"),
        str(cpv_dir / "blobs"),
        str(vpa.COMPOSE),
    ]
    assert kwargs == {"cwd": str(vpa.PROJECT_ROOT)}


def test_main_failure_returns_1_and_reports_fail(harness_env, monkeypatch, capsys):
    _add_bench(harness_env, "bench-a")
    _add_bench(harness_env, "bench-b")
    harness_env.returncodes["bench-b"] = 3

    rc = _run_main(monkeypatch)

    out = capsys.readouterr().out
    assert rc == 1
    assert "PASS  bench-a (h)" in out
    assert "FAIL  bench-b (h)" in out
    assert "1/2 passed" in out


def test_main_no_targets_is_vacuous_success(harness_env, monkeypatch, capsys):
    rc = _run_main(monkeypatch)

    assert rc == 0
    assert "Running 0 benchmarks" in capsys.readouterr().out
    assert harness_env.script_calls == []


def test_main_limit_caps_number_of_benchmarks(harness_env, monkeypatch):
    for name in ("a", "b", "c"):
        _add_bench(harness_env, name)

    _run_main(monkeypatch, "-n", "2")

    assert [Path(c[0][1]).name for c in harness_env.script_calls] == ["a", "b"]


def test_main_limit_zero_runs_all(harness_env, monkeypatch):
    for name in ("a", "b", "c"):
        _add_bench(harness_env, name)

    _run_main(monkeypatch, "--limit", "0")

    assert len(harness_env.script_calls) == 3


def test_main_selected_benchmarks_filter(harness_env, monkeypatch):
    for name in ("a", "b", "c"):
        _add_bench(harness_env, name)
    monkeypatch.setattr(vpa, "SELECTED_BENCHMARKS", ["b", "unknown"])

    _run_main(monkeypatch)

    assert [Path(c[0][1]).name for c in harness_env.script_calls] == ["b"]


def test_main_prints_timing_and_log_paths(harness_env, monkeypatch, capsys, tmp_path):
    _add_bench(harness_env, "bench-a")
    log_dir = tmp_path / "logs"
    _touch(
        log_dir / "verify_patch_timing.json",
        json.dumps({"rebuild": 1.5, "test": 2.5}),
    )
    harness_env.artifacts["bench-a"] = {
        "crs": {"crs-a": {"log_dir": str(log_dir), "run_logs": "/rl/crs"}},
        "run_logs": {
            "compose_stdout_log": "/o.log",
            "compose_stderr_log": "/e.log",
            "service_logs": "/services",
        },
    }

    rc = _run_main(monkeypatch)

    out = capsys.readouterr().out
    assert rc == 0
    assert "Timing: rebuild=1.5s  test=2.5s" in out
    assert "rebuild=1.5s test=2.5s" in out
    assert "ALL TIMINGS (JSON)" in out
    assert '"rebuild": 1.5' in out
    assert "LOG PATHS" in out
    assert f"crs_log_dir: {log_dir}" in out
    assert "run_logs: /rl/crs" in out
    assert "compose_stdout: /o.log" in out
    assert "compose_stderr: /e.log" in out
    assert "service_logs: /services" in out


def test_main_without_artifacts_has_no_timing_or_log_sections(
    harness_env, monkeypatch, capsys
):
    _add_bench(harness_env, "bench-a")

    _run_main(monkeypatch)

    out = capsys.readouterr().out
    assert "ALL TIMINGS" not in out
    assert "LOG PATHS" not in out


def test_main_artifacts_without_crs_still_collects_run_logs(
    harness_env, monkeypatch, capsys
):
    _add_bench(harness_env, "bench-a")
    harness_env.artifacts["bench-a"] = {
        "run_logs": {"compose_stdout_log": "/only-run-level.log"}
    }

    rc = _run_main(monkeypatch)

    out = capsys.readouterr().out
    assert rc == 0
    assert "compose_stdout: /only-run-level.log" in out
    assert "crs_log_dir" not in out
