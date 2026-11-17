# SPDX-License-Identifier: MIT
from unittest.mock import MagicMock, patch

from oss_crs.src.utils import buildx_local_output_error


def _inspect_result(stdout: str = "Name: default\nDriver: docker\n"):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def test_default_docker_driver_allows_implicit_local_output():
    cmd = ["docker", "buildx", "build", "-t", "image:tag", "."]

    with patch(
        "oss_crs.src.utils.subprocess.run", return_value=_inspect_result()
    ) as run:
        assert buildx_local_output_error(cmd, "build target") is None

    run.assert_called_once_with(
        ["docker", "buildx", "inspect"], capture_output=True, text=True
    )


def test_non_docker_driver_rejects_implicit_local_output():
    cmd = ["docker", "buildx", "bake", "-f", "docker-bake.hcl"]
    inspect_result = _inspect_result("Name: ci-builder\nDriver: docker-container\n")

    with patch("oss_crs.src.utils.subprocess.run", return_value=inspect_result):
        error = buildx_local_output_error(cmd, "build CRS images locally")

    assert error is not None
    assert "`docker-container` driver" in error
    assert "Use the `docker` driver" in error
    assert "single-platform build with `--load`" in error
    assert "publishing uses `--push`" in error
    assert "No build was started." in error


def test_explicit_load_and_push_skip_driver_inspection():
    with patch("oss_crs.src.utils.subprocess.run") as run:
        assert (
            buildx_local_output_error(
                ["docker", "buildx", "build", "--load", "."], "build target"
            )
            is None
        )
        assert (
            buildx_local_output_error(
                ["docker", "buildx", "bake", "--push"], "publish CRS images"
            )
            is None
        )

    run.assert_not_called()


def test_inspect_failure_is_actionable():
    inspect_result = MagicMock(returncode=1, stdout="", stderr="no builder")

    with patch("oss_crs.src.utils.subprocess.run", return_value=inspect_result):
        error = buildx_local_output_error(
            ["docker", "buildx", "build", "."], "build target"
        )

    assert error is not None
    assert "Cannot determine the active Buildx builder" in error
    assert "no builder" in error
