# SPDX-License-Identifier: MIT
"""Shared fixtures for unit tests."""

import pytest


@pytest.fixture(autouse=True)
def _cli_is_docker(request, monkeypatch):
    """Treat the container CLI as Docker without running `docker --version`.

    Tests that mock `subprocess.run` would otherwise see the probe. Tests of
    the probe itself opt out with `@pytest.mark.real_cli_probe`.
    """
    from oss_crs.src import docker_bin

    probe = docker_bin._cli_is_podman
    provider = docker_bin.compose_v2_provider
    probe.cache_clear()
    provider.cache_clear()
    if request.node.get_closest_marker("real_cli_probe") is None:
        monkeypatch.setattr(docker_bin, "_cli_is_podman", lambda: False)
    yield
    probe.cache_clear()
    provider.cache_clear()
