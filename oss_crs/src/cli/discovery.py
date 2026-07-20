# SPDX-License-Identifier: MIT
"""Image discovery helpers for clean and export commands."""

import docker
import docker.errors
from pathlib import Path

from ..constants import (
    OSS_CRS_ALPINE_TAG,
    OSS_CRS_DEPS_IMAGE,
    OSS_CRS_INFRA_SIDECAR_IMAGES,
    OSS_CRS_INTERNAL_LLM_IMAGES,
    OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES,
    PRESERVED_BUILDER_REPO,
    PRESERVED_RUNNER_REPO,
)
from ..crs_compose import _lifecycle_needed
from ..utils import log_warning, preserved_runner_image_name


def _dedupe(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def discover_infra_images(crs_compose) -> list[str]:
    """Return framework-level image tags produced during prepare.

    Mirrors ``CRSCompose.__prepare_oss_crs_infra``: always includes
    oss-crs-deps, alpine, and the shared infra sidecars (minus lifecycle
    when it will never be started); adds internal-LLM images when the LLM
    stack is in internal mode.
    """
    tags = [
        f"{OSS_CRS_DEPS_IMAGE}:latest",
        OSS_CRS_ALPINE_TAG,
    ]

    sidecars = dict(OSS_CRS_INFRA_SIDECAR_IMAGES)
    if not _lifecycle_needed(crs_compose.crs_list):
        sidecars.pop("lifecycle", None)
    tags.extend(sidecars.values())

    if crs_compose.llm.exists() and crs_compose.llm.mode == "internal":
        tags.extend(OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES.values())
        tags.extend(OSS_CRS_INTERNAL_LLM_IMAGES.values())

    return _dedupe(tags)


def discover_prepare_images(crs_compose) -> list[str]:
    """Discover framework and CRS images produced during prepare."""
    candidate_tags = discover_infra_images(crs_compose)
    for crs in crs_compose.crs_list:
        try:
            candidate_tags.extend(crs.get_bake_image_tags())
        except Exception as exc:
            log_warning(f"Could not discover prepare images for {crs.name}: {exc}")

        # Target-independent run-phase images are built during prepare.
        crs_run_phase = getattr(crs.config, "crs_run_phase", None)
        if crs_run_phase is not None:
            for module_name, module_config in crs_run_phase.modules.items():
                if not module_config.target_dependent:
                    candidate_tags.append(
                        preserved_runner_image_name(crs.name, module_name)
                    )

    # Only include images that actually exist locally
    client = docker.from_env()
    existing: list[str] = []
    for tag in _dedupe(candidate_tags):
        try:
            client.images.get(tag)
            existing.append(tag)
        except docker.errors.ImageNotFound:
            pass
    return existing


def discover_build_target_images(
    crs_compose, target=None
) -> tuple[list[str], list[str], list[str]]:
    """Discover builder, snapshot, and target-base images scoped to this compose config.

    Uses CRS names from the compose config and build-ids from the workdir to
    match only images belonging to this configuration.

    Returns (builder_tags, snapshot_tags, target_tags).
    """
    client = docker.from_env()
    builder_tags: list[str] = []
    snapshot_tags: list[str] = []
    target_tags: list[str] = []

    crs_names = {crs.name for crs in crs_compose.crs_list}
    build_ids = {b.build_id for b in crs_compose.work_dir.iter_builds()}

    # Preserved builders: oss-crs-builder:{crs_name}-{build_name}-{build_id}
    # Filter by matching crs_name prefix AND build_id suffix
    for img in client.images.list(name=PRESERVED_BUILDER_REPO):
        for tag in img.tags:
            _, _, tag_suffix = tag.partition(":")
            if not tag_suffix:
                continue
            # tag_suffix is "{crs_name}-{build_name}-{build_id}"
            # Check if it starts with any known CRS name and ends with a known build_id
            for crs_name in crs_names:
                prefix = f"{crs_name}-"
                if tag_suffix.startswith(prefix):
                    remainder = tag_suffix[len(prefix) :]
                    # remainder is "{build_name}-{build_id}"
                    for bid in build_ids:
                        if remainder.endswith(f"-{bid}"):
                            builder_tags.append(tag)
                            break
                    break

    # Snapshots: oss-crs-snapshot:{kind}-{crs_name}-{build_name}-{build_id}
    # Same scoping logic
    for img in client.images.list(name="oss-crs-snapshot"):
        for tag in img.tags:
            _, _, tag_suffix = tag.partition(":")
            if not tag_suffix:
                continue
            for crs_name in crs_names:
                if f"-{crs_name}-" in tag_suffix:
                    for bid in build_ids:
                        if tag_suffix.endswith(f"-{bid}"):
                            snapshot_tags.append(tag)
                            break
                    break
            # Also match content-hash snapshots if they're under our build dirs
            # These use format "content-{hash}" and aren't CRS-scoped, but we
            # include them since they were created by builds in this workdir
            if tag_suffix.startswith("test-"):
                for bid in build_ids:
                    if tag_suffix == f"test-{bid}":
                        snapshot_tags.append(tag)
                        break

    # Target base images (only if target provided)
    if target is not None:
        tag = target.get_docker_image_name()
        try:
            client.images.get(tag)
            target_tags.append(tag)
        except docker.errors.ImageNotFound:
            pass

    # Preserved runner images: oss-crs-runner:{crs_name}-{module}-{repo_hash}
    # Produced by build-target for target-dependent run modules. Scope by CRS
    # name (and by target repo hash when a target is provided).
    if target is not None:
        repo_hash = target.get_docker_image_name().rsplit(":", 1)[-1]
    else:
        repo_hash = None
    for img in client.images.list(name=PRESERVED_RUNNER_REPO):
        for tag in img.tags:
            if not tag.startswith(f"{PRESERVED_RUNNER_REPO}:"):
                continue
            _, _, tag_suffix = tag.partition(":")
            if not tag_suffix:
                continue
            for crs_name in crs_names:
                if tag_suffix.startswith(f"{crs_name}-"):
                    if repo_hash is None or tag_suffix.endswith(f"-{repo_hash}"):
                        builder_tags.append(tag)
                    break

    return (
        _dedupe(builder_tags),
        _dedupe(snapshot_tags),
        _dedupe(target_tags),
    )


def discover_run_images(crs_compose) -> list[str]:
    """Discover run-phase images scoped to this compose config.

    Enumerates run-ids from the workdir and matches compose project images
    with the pattern ``crs_compose_{run_id}*``.
    """
    run_ids = {r.run_id for r in crs_compose.work_dir.iter_runs()}
    if not run_ids:
        return []

    client = docker.from_env()
    tags: list[str] = []
    for img in client.images.list():
        for tag in img.tags:
            for rid in run_ids:
                if tag.startswith(f"crs_compose_{rid}"):
                    tags.append(tag)
                    break
    return _dedupe(tags)


def discover_artifact_dirs(work_dir, phase: str) -> list[Path]:
    """Find builds/ and/or runs/ directories under each sanitizer dir.

    Args:
        work_dir: A WorkDir instance.
        phase: One of "prepare", "build-target", "run", or "all".
    """
    dirs: list[Path] = []
    if phase in ("build-target", "all"):
        dirs.extend(b.path for b in work_dir.iter_builds())
    if phase in ("run", "all"):
        dirs.extend(r.path for r in work_dir.iter_runs())
    return dirs
