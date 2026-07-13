"""Build the oss-crs-deps Docker image (libCRS + rsync via Nix).

During ``prepare``, the framework builds ``libCRS/deps.Dockerfile`` with
``docker build``.  Its build stage runs Nix inside a pinned ``nixos/nix``
container to produce the ``libcrs-runtime`` closure, then assembles a
minimal ``FROM scratch`` image tagged ``oss-crs-deps:latest``.

The image contains the full ``libcrs-runtime`` Nix closure (so that
``/nix/store`` is populated), together with two baked symlinks::

    /usr/local/bin/libCRS  ->  /nix/store/<hash>-libcrs-runtime/bin/libCRS
    /usr/local/bin/rsync   ->  /nix/store/<hash>-libcrs-runtime/bin/rsync

CRS builder Dockerfiles can then do::

    COPY --from=oss-crs-deps /nix/store /nix/store
    COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
    COPY --from=oss-crs-deps /usr/local/bin/rsync  /usr/local/bin/rsync
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .constants import NIX_BUILDER_IMAGE, OSS_CRS_DEPS_IMAGE

# Dockerfile (relative to the libCRS directory) that builds the deps image.
DEPS_DOCKERFILE_NAME = "deps.Dockerfile"


def docker_available() -> bool:
    """Return True when the ``docker`` CLI is on PATH."""
    return shutil.which("docker") is not None


def deps_image_exists() -> bool:
    """Return True when the oss-crs-deps image is already loaded."""
    if not docker_available():
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", OSS_CRS_DEPS_IMAGE],
        capture_output=True,
    )
    return result.returncode == 0


def build_deps_image(libcrs_dir: Path) -> tuple[bool, str]:
    """Build the ``oss-crs-deps`` Docker image via ``docker build``.

    Builds ``deps.Dockerfile`` with the ``libCRS`` directory as the build
    context.  The Dockerfile's build stage runs ``nix build`` inside a
    pinned ``nixos/nix`` container (passed as the ``NIX_BUILDER_IMAGE``
    build-arg) and copies the resulting runtime closure into a
    ``FROM scratch`` image tagged ``oss-crs-deps:latest``.

    If the image already exists (e.g. from a previous ``prepare``), the
    build is skipped.

    Returns:
        A ``(success, detail)`` tuple.  ``success`` is True on success.
        On failure, ``detail`` contains a human-readable explanation
        (including captured subprocess output) for debugging.  On success
        ``detail`` is an empty string.
    """
    if not docker_available():
        return False, (
            "docker CLI not found on PATH; the oss-crs-deps image is built "
            f"with 'docker build' using the {NIX_BUILDER_IMAGE} container"
        )

    if deps_image_exists():
        return True, ""

    flake_nix = libcrs_dir / "flake.nix"
    if not flake_nix.exists():
        return False, f"flake.nix not found in libCRS directory: {flake_nix}"

    dockerfile = libcrs_dir / DEPS_DOCKERFILE_NAME
    if not dockerfile.exists():
        return (
            False,
            f"{DEPS_DOCKERFILE_NAME} not found in libCRS directory: {dockerfile}",
        )

    docker_cmd = [
        "docker",
        "build",
        "-t",
        f"{OSS_CRS_DEPS_IMAGE}:latest",
        "-f",
        str(dockerfile),
        "--build-arg",
        f"NIX_BUILDER_IMAGE={NIX_BUILDER_IMAGE}",
        str(libcrs_dir.resolve()),
    ]

    result = subprocess.run(
        docker_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        return False, (
            f"'docker build' failed (exit {result.returncode}) while building "
            f"the oss-crs-deps image in {NIX_BUILDER_IMAGE}.\n"
            f"Command: {' '.join(docker_cmd)}\n"
            f"Output:\n{result.stdout}"
        )

    return True, ""
