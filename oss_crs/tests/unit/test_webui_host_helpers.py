# SPDX-License-Identifier: MIT
"""Unit tests for oss_crs.src.webui (host-side WebUI integration helpers)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from oss_crs.src import constants, webui
from oss_crs.src.ui import TaskResult
from oss_crs.src.workdir import WorkDir

SAN = "address"
BUILD_ID = "build1"
RUN_ID = "run1"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeProgress:
    """Stand-in for MultiTaskProgress that runs added tasks sequentially."""

    def __init__(self, tasks=None, title=None, console=None, result=None, boom=None):
        self.console = console
        self.title = title
        self.tasks: list[tuple[str, object]] = []
        self.notes: list[str] = []
        self.calls: list[tuple] = []
        self._result = result
        self._boom = boom
        self.step_results: dict[str, TaskResult] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_task(self, name, fn):
        self.tasks.append((name, fn))

    def add_note(self, note):
        self.notes.append(note)

    def docker_compose_build(self, project_name, compose_path):
        self.calls.append(("build", project_name, compose_path))
        return self.step_results.get("build", TaskResult(success=True))

    def docker_compose_run(self, project_name, compose_path, service):
        self.calls.append(("run", project_name, compose_path, service))
        return self.step_results.get("run", TaskResult(success=True))

    def run_added_tasks(self):
        if self._boom is not None:
            raise self._boom
        if self._result is not None:
            return self._result
        last = TaskResult(success=True)
        for _name, fn in self.tasks:
            last = fn(self)
            if not last.success:
                return last
        return last


class FakeTmpDockerCompose:
    """Stand-in for TmpDockerCompose that uses a caller-provided directory."""

    root: Path = Path(".")

    def __init__(self, progress, prefix="proj", **_):
        self.progress = progress
        self.prefix = prefix
        self.docker_compose = None
        self.project_name = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.docker_compose = self.root / "docker-compose.yaml"
        self.project_name = f"{self.prefix}_proj"
        return self

    def __exit__(self, *exc):
        return False


def _target(tmp_path: Path, harness: str | None = "fuzz_target", **extra):
    base = dict(
        name="tgt",
        target_harness=harness,
        get_workdir_target_key=lambda: "tkey",
        build_docker_image=lambda: "tgt-base:latest",
        repo_path=tmp_path / "repo",
        _has_repo=False,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _compose(tmp_path: Path, crs_names=("crs-a",), with_resource=True):
    work_dir = WorkDir(tmp_path / "work")
    crs_list = [
        SimpleNamespace(
            name=n,
            resource=SimpleNamespace(cpuset="0-1", memory="4G") if with_resource else None,
        )
        for n in crs_names
    ]
    return SimpleNamespace(
        work_dir=work_dir,
        config=SimpleNamespace(
            oss_crs_infra=SimpleNamespace(cpuset="2-3", memory="8G")
        ),
        crs_compose_env={"K": "V"},
        crs_list=crs_list,
        _read_litellm_spend_summary=lambda run_id, san: {},
    )


@pytest.fixture
def logs(monkeypatch):
    dim: list[str] = []
    warn: list[str] = []
    monkeypatch.setattr(webui, "log_dim", dim.append)
    monkeypatch.setattr(webui, "log_warning", warn.append)
    return SimpleNamespace(dim=dim, warn=warn)


# --------------------------------------------------------------------------
# build_coverage_best_effort
# --------------------------------------------------------------------------


def _patch_progress(monkeypatch, **kwargs):
    created: list[FakeProgress] = []

    def factory(tasks=None, title=None, console=None):
        p = FakeProgress(tasks=tasks, title=title, console=console, **kwargs)
        created.append(p)
        return p

    monkeypatch.setattr(webui, "MultiTaskProgress", factory)
    return created


def test_best_effort_success_logs_completion_and_no_warning(
    monkeypatch, tmp_path, logs
):
    created = _patch_progress(monkeypatch)
    calls = []
    monkeypatch.setattr(
        webui, "build_coverage", lambda *a, **k: calls.append((a, k)) or TaskResult(True)
    )
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.build_coverage_best_effort(
        compose, target, "img", BUILD_ID, SAN, target_source_path=Path("/src")
    )

    assert logs.dim[0].startswith("Building coverage instrumentation")
    assert logs.dim[-1] == "Coverage build complete."
    assert logs.warn == []
    assert created[0].title == "Coverage Build"
    assert [name for name, _ in created[0].tasks] == ["coverage build"]
    (args, kwargs), = calls
    assert args[:5] == (compose, target, "img", BUILD_ID, SAN)
    assert kwargs == {"target_source_path": Path("/src")}


def test_best_effort_failure_warns_and_writes_log(monkeypatch, tmp_path, logs):
    _patch_progress(monkeypatch, result=TaskResult(success=False, error="x"))
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.build_coverage_best_effort(compose, target, "img", BUILD_ID, SAN)

    log_path = (
        compose.work_dir.get_build_output_dir(
            constants.COVERAGE_CRS_NAME, target, BUILD_ID, SAN, create=False
        )
        / "coverage-build.log"
    )
    assert log_path.is_file()
    assert len(logs.warn) == 1
    assert "Coverage build unavailable" in logs.warn[0]
    assert f"See {log_path} for details." in logs.warn[0]
    assert "Coverage build complete." not in logs.dim


def test_best_effort_exception_is_swallowed(monkeypatch, tmp_path, logs):
    _patch_progress(monkeypatch, boom=RuntimeError("crash"))
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.build_coverage_best_effort(compose, target, "img", BUILD_ID, SAN)

    assert len(logs.warn) == 1
    assert "Coverage build unavailable" in logs.warn[0]


def test_best_effort_log_write_failure_omits_log_pointer(monkeypatch, tmp_path, logs):
    _patch_progress(monkeypatch, result=TaskResult(success=False))
    compose, target = _compose(tmp_path), _target(tmp_path)

    def broken(*_a, **_k):
        raise OSError("disk full")

    compose.work_dir = SimpleNamespace(get_build_output_dir=broken)

    webui.build_coverage_best_effort(compose, target, "img", BUILD_ID, SAN)

    assert len(logs.warn) == 1
    assert "See " not in logs.warn[0]


def test_best_effort_captures_output_in_log_not_console(monkeypatch, tmp_path, logs):
    class Noisy(FakeProgress):
        def run_added_tasks(self):
            self.console.print("coverage exploded")
            return TaskResult(success=False)

    monkeypatch.setattr(
        webui,
        "MultiTaskProgress",
        lambda tasks=None, title=None, console=None: Noisy(console=console),
    )
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.build_coverage_best_effort(compose, target, "img", BUILD_ID, SAN)

    log_path = (
        compose.work_dir.get_build_output_dir(
            constants.COVERAGE_CRS_NAME, target, BUILD_ID, SAN, create=False
        )
        / "coverage-build.log"
    )
    assert "coverage exploded" in log_path.read_text()


# --------------------------------------------------------------------------
# ensure_coverage_build
# --------------------------------------------------------------------------


def test_ensure_requires_build_id(tmp_path):
    with pytest.raises(AssertionError):
        webui.ensure_coverage_build(_compose(tmp_path), _target(tmp_path), None, SAN)


def test_ensure_noop_when_coverage_build_exists(monkeypatch, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)
    (
        compose.work_dir.get_build_output_dir(
            constants.COVERAGE_CRS_NAME, target, BUILD_ID, SAN
        )
        / "coverage-build"
    ).mkdir()
    monkeypatch.setattr(
        webui, "build_coverage_best_effort", lambda *a, **k: pytest.fail("rebuilt")
    )
    target.build_docker_image = lambda: pytest.fail("image resolved")

    assert webui.ensure_coverage_build(compose, target, BUILD_ID, SAN) is True


def test_ensure_returns_false_when_base_image_unresolved(monkeypatch, tmp_path):
    compose = _compose(tmp_path)
    target = _target(tmp_path, build_docker_image=lambda: None)
    monkeypatch.setattr(
        webui, "build_coverage_best_effort", lambda *a, **k: pytest.fail("built")
    )

    assert webui.ensure_coverage_build(compose, target, BUILD_ID, SAN) is False


def test_ensure_builds_with_workdir_source_when_no_repo(monkeypatch, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)
    src = tmp_path / "from-workdir"
    seen = {}
    compose.work_dir.get_target_source_dir = lambda t, b, s, create=True: (
        seen.update(create=create) or src
    )
    calls = []
    monkeypatch.setattr(
        webui, "build_coverage_best_effort", lambda *a, **k: calls.append((a, k))
    )

    assert webui.ensure_coverage_build(compose, target, BUILD_ID, SAN) is True

    (args, kwargs), = calls
    assert args == (compose, target, "tgt-base:latest", BUILD_ID, SAN)
    assert kwargs == {"target_source_path": src}
    assert seen == {"create": False}


def test_ensure_builds_with_resolved_repo_path_when_repo_present(
    monkeypatch, tmp_path
):
    compose = _compose(tmp_path)
    repo = tmp_path / "some" / ".." / "repo"
    target = _target(tmp_path, repo_path=repo, _has_repo=True)
    compose.work_dir.get_target_source_dir = lambda *a, **k: pytest.fail("used workdir")
    calls = []
    monkeypatch.setattr(
        webui, "build_coverage_best_effort", lambda *a, **k: calls.append(k)
    )

    assert webui.ensure_coverage_build(compose, target, BUILD_ID, SAN) is True

    assert calls == [{"target_source_path": repo.resolve()}]


# --------------------------------------------------------------------------
# build_coverage
# --------------------------------------------------------------------------


@pytest.fixture
def coverage_env(monkeypatch, tmp_path):
    FakeTmpDockerCompose.root = tmp_path / "tmp-compose"
    monkeypatch.setattr(webui, "TmpDockerCompose", FakeTmpDockerCompose)
    rendered = {}

    def fake_render(crs, target, image, build_config, build_out, bid, san, **kw):
        rendered.update(
            crs=crs,
            target=target,
            image=image,
            build_config=build_config,
            build_out=build_out,
            bid=bid,
            san=san,
            kw=kw,
        )
        return "rendered: yaml", ["warn-1", "warn-2"]

    monkeypatch.setattr(webui.renderer, "render_build_target_docker_compose", fake_render)
    return rendered


def test_build_coverage_runs_three_tasks_in_order(coverage_env, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)
    progress = FakeProgress()

    result = webui.build_coverage(compose, target, "img", BUILD_ID, SAN, progress)

    assert result.success is True
    assert [n for n, _ in progress.tasks] == [
        "Prepare coverage build compose",
        "Build coverage builder image",
        "Run coverage build",
    ]
    compose_path = FakeTmpDockerCompose.root / "docker-compose.yaml"
    assert progress.calls == [
        ("build", "crs_proj", compose_path),
        ("run", "crs_proj", compose_path, "target_builder"),
    ]


def test_build_coverage_renders_and_writes_compose(coverage_env, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)
    progress = FakeProgress()

    webui.build_coverage(
        compose, target, "img", BUILD_ID, SAN, progress, target_source_path=Path("/s")
    )

    assert (FakeTmpDockerCompose.root / "docker-compose.yaml").read_text() == (
        "rendered: yaml"
    )
    assert progress.notes == ["warn-1", "warn-2"]
    assert coverage_env["image"] == "img"
    assert (coverage_env["bid"], coverage_env["san"]) == (BUILD_ID, SAN)
    assert coverage_env["kw"] == {"target_source_path": Path("/s")}
    assert coverage_env["build_out"] == compose.work_dir.get_build_output_dir(
        constants.COVERAGE_CRS_NAME, target, BUILD_ID, SAN, create=False
    )


def test_build_coverage_uses_infra_resources_for_synthetic_crs(coverage_env, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.build_coverage(compose, target, "img", BUILD_ID, SAN, FakeProgress())

    crs = coverage_env["crs"]
    assert crs.name == constants.COVERAGE_CRS_NAME
    assert crs.crs_compose_env == {"K": "V"}
    assert crs.config.version == "1.0.0"
    assert crs.resource.cpuset == "2-3"
    assert crs.resource.memory == "8G"
    assert crs.resource.additional_env == {}
    assert crs.crs_path.name == "webui"


def test_build_coverage_build_config(coverage_env, tmp_path):
    webui.build_coverage(
        _compose(tmp_path), _target(tmp_path), "img", BUILD_ID, SAN, FakeProgress()
    )

    cfg = coverage_env["build_config"]
    assert cfg.name == "coverage-build"
    assert cfg.outputs == ["coverage-build", "coverage-src"]
    assert Path(cfg.dockerfile).name == "coverage-builder.Dockerfile"


def test_build_coverage_stops_after_failed_image_build(coverage_env, tmp_path):
    progress = FakeProgress()
    progress.step_results["build"] = TaskResult(success=False, error="no image")

    result = webui.build_coverage(
        _compose(tmp_path), _target(tmp_path), "img", BUILD_ID, SAN, progress
    )

    assert result.success is False
    assert [c[0] for c in progress.calls] == ["build"]  # run step never executed


# --------------------------------------------------------------------------
# publish_final_snapshot
# --------------------------------------------------------------------------


@pytest.fixture
def posts(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, {"json": json, "timeout": timeout}))

    monkeypatch.setattr(webui.requests, "post", fake_post)
    return calls


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_publish_posts_counts_and_metadata(posts, logs, tmp_path):
    compose, target = _compose(tmp_path, ("crs-a", "crs-b")), _target(tmp_path)
    submit_a = compose.work_dir.get_submit_dir("crs-a", target, RUN_ID, SAN)
    _touch(submit_a / "povs" / "p1")
    _touch(submit_a / "povs" / "p2")
    _touch(submit_a / "povs" / ".hidden")  # hidden bookkeeping is not counted
    _touch(submit_a / "seeds" / "s1")
    exchange = compose.work_dir.get_exchange_dir(target, RUN_ID, SAN)
    _touch(exchange / "patches" / "x")

    webui.publish_final_snapshot(compose, target, RUN_ID, SAN)

    (url, kw), = posts
    assert url == (
        f"http://localhost:{constants.WEBUI_DEFAULT_PORT}/api/runs/{RUN_ID}/finalize"
    )
    assert kw["timeout"] == 5
    payload = kw["json"]
    assert payload["outcome"] == "success"
    assert payload["cost"] is None
    assert payload["per_crs"]["crs-a"]["povs"] == 2
    assert payload["per_crs"]["crs-a"]["seeds"] == 1
    assert set(payload["per_crs"]["crs-b"].values()) == {0}
    assert set(payload["per_crs"]["crs-a"]) == set(constants.EXCHANGE_DIR_NAMES)
    assert payload["exchange"]["patches"] == 1
    assert payload["_meta"] == {
        "target": "tgt",
        "crs_names": ["crs-a", "crs-b"],
        "crs_resources": {
            "crs-a": {"cpuset": "0-1", "memory": "4G"},
            "crs-b": {"cpuset": "0-1", "memory": "4G"},
        },
        "harness": "fuzz_target",
    }
    assert logs.dim == ["Published final run totals to WebUI"]


def test_publish_custom_outcome_is_forwarded(posts, tmp_path):
    webui.publish_final_snapshot(
        _compose(tmp_path), _target(tmp_path), RUN_ID, SAN, outcome="interrupted"
    )

    assert posts[0][1]["json"]["outcome"] == "interrupted"


def test_publish_omits_resources_for_crs_without_resource(posts, tmp_path):
    compose = _compose(tmp_path, with_resource=False)

    webui.publish_final_snapshot(compose, _target(tmp_path), RUN_ID, SAN)

    assert posts[0][1]["json"]["_meta"]["crs_resources"] == {}
    assert posts[0][1]["json"]["_meta"]["crs_names"] == ["crs-a"]


def test_publish_reports_no_harness_as_none(posts, tmp_path):
    webui.publish_final_snapshot(
        _compose(tmp_path), _target(tmp_path, harness=None), RUN_ID, SAN
    )

    assert posts[0][1]["json"]["_meta"]["harness"] is None


def test_publish_includes_cost_from_spend_report(posts, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)
    spend_file = compose.work_dir.get_litellm_spend_report_file(RUN_ID, SAN)
    spend_file.write_text("{}")
    compose._read_litellm_spend_summary = lambda run_id, san: {
        "totals": {
            "credits_used": 1.25,
            "prompt_tokens": 100,
            "completion_tokens": 40,
        },
        "crs": {
            "crs-a": {
                "credits_used": 0.75,
                "prompt_tokens": 60,
                "completion_tokens": 30,
            },
            "crs-b": {},
        },
    }

    webui.publish_final_snapshot(compose, target, RUN_ID, SAN)

    cost = posts[0][1]["json"]["cost"]
    assert cost == {
        "total": 1.25,
        "per_crs": {"crs-a": 0.75, "crs-b": 0.0},
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "per_crs_tokens": {
            "crs-a": {"prompt_tokens": 60, "completion_tokens": 30},
            "crs-b": {"prompt_tokens": 0, "completion_tokens": 0},
        },
    }


def test_publish_spend_summary_with_missing_sections(posts, tmp_path):
    compose = _compose(tmp_path)
    compose.work_dir.get_litellm_spend_report_file(RUN_ID, SAN).write_text("{}")
    compose._read_litellm_spend_summary = lambda run_id, san: {}

    webui.publish_final_snapshot(compose, _target(tmp_path), RUN_ID, SAN)

    cost = posts[0][1]["json"]["cost"]
    assert cost["total"] is None
    assert cost["prompt_tokens"] == 0
    assert cost["completion_tokens"] == 0
    assert cost["per_crs"] == {}


def test_publish_ignores_empty_spend_report(posts, tmp_path):
    compose = _compose(tmp_path)
    compose.work_dir.get_litellm_spend_report_file(RUN_ID, SAN)  # creates empty file
    compose._read_litellm_spend_summary = lambda *a: pytest.fail("read empty report")

    webui.publish_final_snapshot(compose, _target(tmp_path), RUN_ID, SAN)

    assert posts[0][1]["json"]["cost"] is None


def test_publish_swallows_network_errors(monkeypatch, logs, tmp_path):
    def refuse(*_a, **_k):
        raise ConnectionError("webui not running")

    monkeypatch.setattr(webui.requests, "post", refuse)

    webui.publish_final_snapshot(
        _compose(tmp_path), _target(tmp_path), RUN_ID, SAN
    )  # must not raise

    assert logs.dim == []
    assert logs.warn == []


def test_publish_swallows_unexpected_errors_before_posting(posts, logs, tmp_path):
    compose = _compose(tmp_path)
    compose.crs_list = None  # iteration raises TypeError inside the try block

    webui.publish_final_snapshot(compose, _target(tmp_path), RUN_ID, SAN)

    assert posts == []
    assert logs.dim == []


def test_publish_does_not_create_directories(posts, tmp_path):
    compose, target = _compose(tmp_path), _target(tmp_path)

    webui.publish_final_snapshot(compose, target, RUN_ID, SAN)

    assert not compose.work_dir.get_exchange_dir(
        target, RUN_ID, SAN, create=False
    ).exists()
    assert not compose.work_dir.get_submit_dir(
        "crs-a", target, RUN_ID, SAN, create=False
    ).exists()
