# SPDX-License-Identifier: MIT
"""WebUI integration helpers for CRSCompose.

These functions implement the run-time pieces that feed the optional WebUI
dashboard (oss-crs-infra/webui): a best-effort coverage-instrumentation build
and a final-snapshot reconciliation posted to the WebUI after teardown.

They are free functions taking the ``CRSCompose`` instance as ``compose`` rather
than methods, so the webui concern lives in one module instead of being woven
through ``crs_compose.py``. They reach into ``compose`` for shared plumbing
(work dir, config, CRS list, spend summary).
"""

import io
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

import requests
from rich.console import Console

from . import constants
from .templates import renderer
from .ui import MultiTaskProgress, TaskResult
from .utils import TmpDockerCompose, log_dim, log_warning

if TYPE_CHECKING:
    from .crs_compose import CRSCompose
    from .target import Target


def build_coverage_best_effort(
    compose: "CRSCompose",
    target: "Target",
    target_base_image: str,
    build_id: str,
    sanitizer: str,
    target_source_path: Optional[Path] = None,
) -> None:
    """Run the coverage build as a best-effort step — never fails the run,
    and never surfaces a build error to the console.

    The coverage-report build feeds only the webui's coverage panel. Some
    targets cannot produce it — e.g. nginx, whose custom ``auto/configure``
    rejects the ``-fprofile-instr-generate -fcoverage-mapping`` instrumentation
    on its ``--with-ld-opt`` feature test. Such a failure must neither abort
    the run nor spam a red error panel: coverage-guided fuzzing still works
    (that instrumentation lives in the main build), and the publisher handles
    a missing coverage build gracefully (the dashboard just omits the
    coverage panel). We render the build to a throwaway console so its
    progress and any error panel stay hidden, then emit a single outcome line.
    """
    log_dim("Building coverage instrumentation (best-effort)...")
    # Capture the build's output to a buffer (instead of the real console) so
    # the noisy progress/error panel stays hidden. On failure we DON'T discard
    # it: it's written to a log so an unexpected coverage failure remains fully
    # diagnosable — only the visible panel is suppressed, not the evidence.
    buf = io.StringIO()
    try:
        with MultiTaskProgress(
            tasks=[], title="Coverage Build", console=Console(file=buf)
        ) as progress:
            progress.add_task(
                "coverage build",
                lambda p: build_coverage(
                    compose,
                    target,
                    target_base_image,
                    build_id,
                    sanitizer,
                    p,
                    target_source_path=target_source_path,
                ),
            )
            success = progress.run_added_tasks().success
    except Exception:  # unexpected crash in the coverage build itself
        success = False

    if success:
        log_dim("Coverage build complete.")
        return

    # Non-fatal: preserve the captured output so the failure is recoverable.
    log_path = None
    try:
        cov_dir = compose.work_dir.get_build_output_dir(
            constants.COVERAGE_CRS_NAME, target, build_id, sanitizer
        )
        cov_dir.mkdir(parents=True, exist_ok=True)
        log_path = cov_dir / "coverage-build.log"
        log_path.write_text(buf.getvalue())
    except OSError:
        log_path = None
    msg = (
        "Coverage build unavailable for this target; continuing without the "
        "dashboard coverage panel (fuzzing is unaffected)."
    )
    if log_path is not None:
        msg += f" See {log_path} for details."
    log_warning(msg)


def ensure_coverage_build(
    compose: "CRSCompose",
    target: "Target",
    build_id: Optional[str],
    sanitizer: str,
) -> bool:
    """Build the coverage binary on demand when CRS builds already exist.

    Used on the ``run --web-ui`` path when the target was built previously
    (so the main build is skipped) but the best-effort coverage build is
    missing. If the coverage build already exists this is a no-op.

    Returns ``False`` only on a fatal error — the target's base image cannot be
    resolved — signalling the caller to abort the run. Returns ``True``
    otherwise (coverage already present, freshly built, or best-effort skipped).
    """
    assert build_id is not None
    cov_build_dir = compose.work_dir.get_build_output_dir(
        constants.COVERAGE_CRS_NAME, target, build_id, sanitizer, create=False
    )
    if (cov_build_dir / "coverage-build").is_dir():
        return True

    target_base_image = target.build_docker_image()
    if target_base_image is None:
        return False

    resolved_source_path = (
        target.repo_path.resolve()
        if target._has_repo
        else compose.work_dir.get_target_source_dir(
            target, build_id, sanitizer, create=False
        )
    )
    build_coverage_best_effort(
        compose,
        target,
        target_base_image,
        build_id,
        sanitizer,
        target_source_path=resolved_source_path,
    )
    return True


def build_coverage(
    compose: "CRSCompose",
    target: "Target",
    target_base_image: str,
    build_id: str,
    sanitizer: str,
    progress: MultiTaskProgress,
    target_source_path: Optional[Path] = None,
) -> TaskResult:
    """Build the target with SANITIZER=coverage for coverage collection."""
    from .config.crs import BuildConfig

    webui_infra_path = (Path(__file__).parent / "../../oss-crs-infra/webui").resolve()

    build_config = BuildConfig(
        name="coverage-build",
        dockerfile=str(webui_infra_path / "coverage-builder.Dockerfile"),
        outputs=["coverage-build", "coverage-src"],
    )

    # Use the infra resource config for the coverage build, with an
    # empty additional_env (ResourceConfig doesn't carry one).
    infra_resource = compose.config.oss_crs_infra
    resource = SimpleNamespace(
        cpuset=infra_resource.cpuset,
        memory=infra_resource.memory,
        additional_env={},
    )

    # Create a synthetic CRS-like object for the renderer
    coverage_crs = SimpleNamespace(
        name=constants.COVERAGE_CRS_NAME,
        crs_path=webui_infra_path,
        config=SimpleNamespace(version="1.0.0"),
        crs_compose_env=compose.crs_compose_env,
        resource=resource,
    )

    build_out_dir = compose.work_dir.get_build_output_dir(
        constants.COVERAGE_CRS_NAME, target, build_id, sanitizer
    )

    def prepare_docker_compose(
        progress, project_name: str, tmp_docker_compose: TmpDockerCompose
    ) -> TaskResult:
        docker_compose_path = tmp_docker_compose.docker_compose
        assert docker_compose_path is not None
        rendered, warnings = renderer.render_build_target_docker_compose(
            coverage_crs,  # type: ignore[arg-type]  # synthetic CRS-like object
            target,
            target_base_image,
            build_config,
            build_out_dir,
            build_id,
            sanitizer,
            target_source_path=target_source_path,
        )
        for warning in warnings:
            progress.add_note(warning)
        docker_compose_path.write_text(rendered)
        return TaskResult(success=True)

    def build_docker_compose(
        progress, project_name: str, tmp_docker_compose
    ) -> TaskResult:
        return progress.docker_compose_build(
            project_name, tmp_docker_compose.docker_compose
        )

    def run_docker_compose(
        progress, project_name: str, tmp_docker_compose
    ) -> TaskResult:
        return progress.docker_compose_run(
            project_name, tmp_docker_compose.docker_compose, "target_builder"
        )

    with TmpDockerCompose(progress, "crs") as tmp_docker_compose:
        project_name = tmp_docker_compose.project_name
        assert project_name is not None
        progress.add_task(
            "Prepare coverage build compose",
            lambda p: prepare_docker_compose(p, project_name, tmp_docker_compose),
        )
        progress.add_task(
            "Build coverage builder image",
            lambda p: build_docker_compose(p, project_name, tmp_docker_compose),
        )
        progress.add_task(
            "Run coverage build",
            lambda p: run_docker_compose(p, project_name, tmp_docker_compose),
        )
        return progress.run_added_tasks()

    return TaskResult(success=False, error="Unreachable")


def publish_final_snapshot(
    compose: "CRSCompose",
    target: "Target",
    run_id: str,
    sanitizer: str,
    outcome: str = "success",
) -> None:
    """Push authoritative final artifact counts to the WebUI after teardown.

    The publisher sidecar stops with the run, so artifacts harvested during
    the final teardown (a late libFuzzer crash, the exchange merge, etc.)
    never reach the dashboard — its last live snapshot can read 0 while the
    on-disk results read 1. Here, on the host and after harvest, we re-read
    the final SUBMIT_DIR/EXCHANGE_DIR counts and post them to the WebUI's
    /finalize endpoint so the finished run reflects the true results.

    Mirrors the publisher's snapshot schema. Best-effort: any failure (WebUI
    not running, network) is swallowed so it never affects the run outcome.
    The default port matches ``ensure_web_ui_running``
    (``constants.WEBUI_DEFAULT_PORT``); a WebUI on a custom port simply won't
    receive this reconciliation.
    """
    data_types = ("povs", "seeds", "bug-candidates", "patches", "diffs")

    def scan(root: Path) -> dict[str, int]:
        return {d: compose.work_dir.count_data_files(root / d) for d in data_types}

    try:
        per_crs = {
            crs.name: scan(
                compose.work_dir.get_submit_dir(
                    crs.name, target, run_id, sanitizer, create=False
                )
            )
            for crs in compose.crs_list
        }
        exchange = scan(
            compose.work_dir.get_exchange_dir(target, run_id, sanitizer, create=False)
        )

        spend_path = compose.work_dir.get_litellm_spend_report_file(
            run_id, sanitizer, create_parent=False
        )
        cost = None
        if spend_path.exists() and spend_path.stat().st_size > 0:
            spend = compose._read_litellm_spend_summary(run_id, sanitizer)
            cost = {
                "total": spend.get("totals", {}).get("credits_used"),
                "per_crs": {
                    name: entry.get("credits_used", 0.0)
                    for name, entry in spend.get("crs", {}).items()
                },
            }

        payload = {
            "per_crs": per_crs,
            "exchange": exchange,
            "cost": cost,
            "outcome": outcome,
            "_meta": {
                "target": target.name,
                "crs_names": [crs.name for crs in compose.crs_list],
                "crs_resources": {
                    crs.name: {
                        "cpuset": crs.resource.cpuset,
                        "memory": crs.resource.memory,
                    }
                    for crs in compose.crs_list
                    if crs.resource
                },
                "harness": target.target_harness,
            },
        }
        requests.post(
            f"http://localhost:{constants.WEBUI_DEFAULT_PORT}/api/runs/{run_id}/finalize",
            json=payload,
            timeout=5,
        )
        log_dim("Published final run totals to WebUI")
    except Exception:
        # Best-effort: a missing/unreachable WebUI must never surface as an
        # error or even a note at teardown — swallow it silently.
        pass
