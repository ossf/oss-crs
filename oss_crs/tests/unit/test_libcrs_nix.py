"""Unit tests for oss_crs.src.libcrs_nix module."""

from unittest.mock import patch, MagicMock
from pathlib import Path

from oss_crs.src.libcrs_nix import (
    docker_available,
    deps_image_exists,
    build_deps_image,
)
from oss_crs.src.constants import OSS_CRS_DEPS_IMAGE


class TestDockerAvailable:
    """Tests for docker_available."""

    @patch("oss_crs.src.libcrs_nix.shutil.which")
    def test_returns_true_when_docker_on_path(self, mock_which):
        mock_which.return_value = "/usr/bin/docker"
        assert docker_available() is True

    @patch("oss_crs.src.libcrs_nix.shutil.which")
    def test_returns_false_when_docker_not_found(self, mock_which):
        mock_which.return_value = None
        assert docker_available() is False


class TestDepsImageExists:
    """Tests for deps_image_exists."""

    @patch("oss_crs.src.libcrs_nix.subprocess.run")
    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    def test_returns_true_when_image_inspect_succeeds(self, mock_avail, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert deps_image_exists() is True

    @patch("oss_crs.src.libcrs_nix.subprocess.run")
    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    def test_returns_false_when_image_inspect_fails(self, mock_avail, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert deps_image_exists() is False

    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=False)
    def test_returns_false_when_docker_not_available(self, mock_avail):
        assert deps_image_exists() is False


class TestBuildDepsImage:
    """Tests for build_deps_image."""

    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=False)
    def test_returns_false_when_docker_not_available(self, mock_avail):
        success, detail = build_deps_image(Path("/fake/libcrs"))
        assert success is False
        assert "docker" in detail.lower()

    @patch("oss_crs.src.libcrs_nix.deps_image_exists", return_value=True)
    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    def test_skips_build_when_image_already_exists(self, mock_avail, mock_exists):
        success, detail = build_deps_image(Path("/fake/libcrs"))
        assert success is True
        assert detail == ""

    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    @patch("oss_crs.src.libcrs_nix.deps_image_exists", return_value=False)
    def test_returns_false_when_flake_nix_not_found(self, mock_exists, mock_avail):
        success, detail = build_deps_image(Path("/fake/nonexistent"))
        assert success is False
        assert "flake.nix" in detail

    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    @patch("oss_crs.src.libcrs_nix.deps_image_exists", return_value=False)
    def test_returns_false_when_dockerfile_not_found(
        self, mock_exists, mock_avail, tmp_path
    ):
        # flake.nix present but deps.Dockerfile missing
        (tmp_path / "flake.nix").write_text("{}")
        success, detail = build_deps_image(tmp_path)
        assert success is False
        assert "deps.Dockerfile" in detail

    @patch("oss_crs.src.libcrs_nix.subprocess.run")
    @patch("oss_crs.src.libcrs_nix.deps_image_exists", return_value=False)
    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    def test_docker_build_success(self, mock_avail, mock_exists, mock_run, tmp_path):
        """A successful `docker build` returns (True, "")."""
        (tmp_path / "flake.nix").write_text("{}")
        (tmp_path / "deps.Dockerfile").write_text("FROM scratch\n")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        success, detail = build_deps_image(tmp_path)

        assert (success, detail) == (True, "")
        # Verify the issued command is a `docker build` tagging oss-crs-deps
        # and passing the NIX_BUILDER_IMAGE build-arg.
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["docker", "build"]
        assert "-t" in cmd
        assert f"{OSS_CRS_DEPS_IMAGE}:latest" in cmd
        assert any(a.startswith("NIX_BUILDER_IMAGE=") for a in cmd)

    @patch("oss_crs.src.libcrs_nix.subprocess.run")
    @patch("oss_crs.src.libcrs_nix.deps_image_exists", return_value=False)
    @patch("oss_crs.src.libcrs_nix.docker_available", return_value=True)
    def test_returns_false_when_docker_build_fails(
        self, mock_avail, mock_exists, mock_run, tmp_path
    ):
        (tmp_path / "flake.nix").write_text("{}")
        (tmp_path / "deps.Dockerfile").write_text("FROM scratch\n")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="docker build error", stderr=""
        )

        success, detail = build_deps_image(tmp_path)

        assert success is False
        assert "docker build error" in detail
