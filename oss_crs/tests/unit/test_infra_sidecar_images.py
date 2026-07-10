"""Unit tests for prepare-time building of the shared infra sidecar images.

The exchange, lifecycle and builder/runner sidecars are target- and
CRS-module-independent singletons. ``CRSCompose.__prepare_oss_crs_infra``
builds them once with stable tags so the run phase reuses them instead of
rebuilding per run. These tests verify the build is driven for every
registered image and that a build failure is surfaced -- without invoking
real Docker.
"""

from types import SimpleNamespace
from unittest.mock import patch

from oss_crs.src.constants import (
    ALPINE_IMAGE,
    OSS_CRS_INFRA_SIDECAR_IMAGES,
    OSS_CRS_INTERNAL_LLM_IMAGES,
    OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES,
)
from oss_crs.src.crs_compose import CRSCompose, _lifecycle_needed
from oss_crs.src.ui import TaskResult


def _fake_crs(*, is_bug_fixing, is_bug_fixing_ensemble, dockerfiles=("d.Dockerfile",)):
    """Build a minimal CRS stand-in for lifecycle-need predicate tests."""
    modules = {
        f"m{i}": SimpleNamespace(dockerfile=df) for i, df in enumerate(dockerfiles)
    }
    return SimpleNamespace(
        config=SimpleNamespace(
            is_bug_fixing=is_bug_fixing,
            is_bug_fixing_ensemble=is_bug_fixing_ensemble,
            crs_run_phase=SimpleNamespace(modules=modules),
        )
    )


# A CRS topology that *does* require the lifecycle sidecar: a bug-fix ensemble
# plus a non-ensemble bug-fixing CRS with a run-phase module to watch.
_LIFECYCLE_CRS_LIST = [
    _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=True),
    _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=False),
]
# Number of base sidecars built when lifecycle is/ isn't needed.
_BASE_WITH_LIFECYCLE = len(OSS_CRS_INFRA_SIDECAR_IMAGES)
_BASE_WITHOUT_LIFECYCLE = len(OSS_CRS_INFRA_SIDECAR_IMAGES) - 1


def _build_infra_sidecar_images():
    """Invoke the name-mangled private method on a minimal fake instance.

    ``__build_infra_sidecar_images`` reads nothing off ``self``, so a bare
    stand-in is sufficient.
    """
    return CRSCompose._CRSCompose__build_infra_sidecar_images(SimpleNamespace())


def _pull_internal_llm_images():
    """Invoke the internal-LLM prefetch on a minimal fake instance."""
    return CRSCompose._CRSCompose__pull_internal_llm_images(SimpleNamespace())


def _pull_cleanup_image():
    """Invoke the alpine cleanup-image prefetch on a minimal fake instance."""
    return CRSCompose._CRSCompose__pull_cleanup_image(SimpleNamespace())


def _prepare_oss_crs_infra(mode: str, exists: bool = True, crs_list=None):
    """Invoke ``__prepare_oss_crs_infra`` with a faked LLM mode + CRS list."""
    fake = SimpleNamespace(
        llm=SimpleNamespace(mode=mode, exists=lambda: exists),
        crs_list=crs_list if crs_list is not None else [],
    )
    # The method dispatches to the (name-mangled) helpers on self; bind the real
    # implementations to the stand-in instance. The build helper may be called
    # with an explicit registry (needed base set / internal-LLM sidecars), so
    # the stand-in must forward positional args.
    fake._CRSCompose__build_infra_sidecar_images = lambda *a: (
        CRSCompose._CRSCompose__build_infra_sidecar_images(fake, *a)
    )
    fake._CRSCompose__needed_infra_sidecar_images = lambda: (
        CRSCompose._CRSCompose__needed_infra_sidecar_images(fake)
    )
    fake._CRSCompose__pull_internal_llm_images = lambda: (
        CRSCompose._CRSCompose__pull_internal_llm_images(fake)
    )
    fake._CRSCompose__pull_cleanup_image = lambda: (
        CRSCompose._CRSCompose__pull_cleanup_image(fake)
    )
    # The oss-crs-deps image build shells out to a real ``docker build`` and is
    # exercised separately; stub it to a success here so prepare proceeds.
    fake._CRSCompose__build_oss_crs_deps = lambda: TaskResult(success=True)
    return CRSCompose._CRSCompose__prepare_oss_crs_infra(fake)


def test_builds_every_registered_sidecar_image_with_stable_tag():
    """Each registered sidecar image is built with its stable tag + context."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _build_infra_sidecar_images()

    assert result.success is True
    # One `docker build -t <tag> <context>` per registered image.
    assert len(calls) == len(OSS_CRS_INFRA_SIDECAR_IMAGES)
    built_tags = {cmd[cmd.index("-t") + 1] for cmd in calls}
    assert built_tags == set(OSS_CRS_INFRA_SIDECAR_IMAGES.values())
    for cmd in calls:
        assert cmd[:3] == ["docker", "build", "-t"]
        tag = cmd[cmd.index("-t") + 1]
        context = cmd[-1]
        # Context path ends with the subdir registered for this tag.
        subdir = next(sd for sd, t in OSS_CRS_INFRA_SIDECAR_IMAGES.items() if t == tag)
        assert context.endswith(f"oss-crs-infra/{subdir}")


def test_build_failure_is_surfaced():
    """A failing docker build aborts and returns the error output."""

    def fake_run(cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="boom")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _build_infra_sidecar_images()

    assert result.success is False
    assert "boom" in (result.error or "")


def test_build_stops_at_first_failure():
    """The build short-circuits on the first failing image."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stderr="boom")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _build_infra_sidecar_images()

    assert result.success is False
    assert len(calls) == 1


def test_internal_llm_images_are_pulled_with_pinned_digests():
    """Each internal LLM image is pulled by its pinned digest, then tagged."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_internal_llm_images()

    assert result.success is True
    # One `docker pull <digest>` plus one `docker tag <digest> <local>` per image.
    pulls = [cmd for cmd in calls if cmd[:2] == ["docker", "pull"]]
    tags = [cmd for cmd in calls if cmd[:2] == ["docker", "tag"]]
    assert len(pulls) == len(OSS_CRS_INTERNAL_LLM_IMAGES)
    assert len(tags) == len(OSS_CRS_INTERNAL_LLM_IMAGES)
    pulled = {cmd[-1] for cmd in pulls}
    assert pulled == set(OSS_CRS_INTERNAL_LLM_IMAGES)
    # Each pinned digest is tagged with its registered stable local tag.
    tagged = {(cmd[-2], cmd[-1]) for cmd in tags}
    assert tagged == set(OSS_CRS_INTERNAL_LLM_IMAGES.items())


def test_internal_llm_tag_failure_is_surfaced():
    """A failing docker tag after a successful pull is surfaced."""

    def fake_run(cmd, **_kwargs):
        if cmd[:2] == ["docker", "tag"]:
            return SimpleNamespace(returncode=1, stderr="tag boom")
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_internal_llm_images()

    assert result.success is False
    assert "tag boom" in (result.error or "")


def test_internal_llm_pull_failure_is_surfaced():
    """A failing docker pull aborts and returns the error output."""

    def fake_run(cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="pull boom")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_internal_llm_images()

    assert result.success is False
    assert "pull boom" in (result.error or "")


def test_cleanup_image_is_pulled_by_digest_and_tagged_alpine():
    """The alpine cleanup image is pulled by its pinned digest, then tagged."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_cleanup_image()

    assert result.success is True
    # One `docker pull <digest>` and one `docker tag <digest> alpine`.
    assert calls == [
        ["docker", "pull", ALPINE_IMAGE],
        ["docker", "tag", ALPINE_IMAGE, "alpine"],
    ]


def test_cleanup_image_pull_failure_is_surfaced():
    """A failing docker pull of the cleanup image aborts with its error."""

    def fake_run(cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="pull boom")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_cleanup_image()

    assert result.success is False
    assert "pull boom" in (result.error or "")


def test_cleanup_image_tag_failure_is_surfaced():
    """A failing docker tag after a successful pull is surfaced."""

    def fake_run(cmd, **_kwargs):
        if cmd[:2] == ["docker", "tag"]:
            return SimpleNamespace(returncode=1, stderr="tag boom")
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _pull_cleanup_image()

    assert result.success is False
    assert "tag boom" in (result.error or "")


def test_prepare_pulls_internal_llm_images_only_in_internal_mode():
    """Internal-mode prepare builds needed sidecars then pulls the LLM images."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _prepare_oss_crs_infra(mode="internal", crs_list=_LIFECYCLE_CRS_LIST)

    assert result.success is True
    verbs = [cmd[1] for cmd in calls]
    # All base sidecars (lifecycle needed here) + the internal-LLM sidecar.
    assert verbs.count("build") == (
        _BASE_WITH_LIFECYCLE + len(OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES)
    )
    # The internal-LLM digests plus the unconditional alpine cleanup pull.
    assert verbs.count("pull") == len(OSS_CRS_INTERNAL_LLM_IMAGES) + 1
    pulled = {cmd[-1] for cmd in calls if cmd[1] == "pull"}
    assert pulled == set(OSS_CRS_INTERNAL_LLM_IMAGES) | {ALPINE_IMAGE}
    # The internal-LLM sidecar is built with its stable tag.
    built_tags = {cmd[cmd.index("-t") + 1] for cmd in calls if cmd[1] == "build"}
    assert set(OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES.values()) <= built_tags


def test_prepare_skips_llm_pull_in_external_mode():
    """External (or disabled) LLM mode builds only the needed base sidecars.

    The alpine cleanup image is still pulled (unconditionally), but no
    internal-LLM images are pulled.
    """
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _prepare_oss_crs_infra(mode="external", crs_list=_LIFECYCLE_CRS_LIST)

    assert result.success is True
    # Only the alpine cleanup image is pulled; no internal-LLM digests.
    pulled = {cmd[-1] for cmd in calls if cmd[:2] == ["docker", "pull"]}
    assert pulled == {ALPINE_IMAGE}
    verbs = [cmd[1] for cmd in calls]
    # Only base sidecars build; the internal-LLM sidecar is skipped.
    assert verbs.count("build") == _BASE_WITH_LIFECYCLE
    built_tags = {cmd[cmd.index("-t") + 1] for cmd in calls if cmd[1] == "build"}
    assert set(OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES.values()).isdisjoint(built_tags)


def test_prepare_skips_llm_pull_when_disabled():
    """When the LLM stack is disabled, prepare pulls only the cleanup image."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _prepare_oss_crs_infra(
            mode="disabled", exists=False, crs_list=_LIFECYCLE_CRS_LIST
        )

    assert result.success is True
    pulled = {cmd[-1] for cmd in calls if cmd[:2] == ["docker", "pull"]}
    assert pulled == {ALPINE_IMAGE}
    verbs = [cmd[1] for cmd in calls]
    assert verbs.count("build") == _BASE_WITH_LIFECYCLE


def test_prepare_skips_lifecycle_build_when_not_needed():
    """A config with no bug-fix ensemble does not build the lifecycle sidecar."""
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    crs_list = [_fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=False)]
    with patch("oss_crs.src.crs_compose.subprocess.run", fake_run):
        result = _prepare_oss_crs_infra(mode="external", crs_list=crs_list)

    assert result.success is True
    verbs = [cmd[1] for cmd in calls]
    assert verbs.count("build") == _BASE_WITHOUT_LIFECYCLE
    built_tags = {cmd[cmd.index("-t") + 1] for cmd in calls if cmd[1] == "build"}
    assert OSS_CRS_INFRA_SIDECAR_IMAGES["lifecycle"] not in built_tags


# ---------------------------------------------------------------------------
# _lifecycle_needed predicate
# ---------------------------------------------------------------------------


def test_lifecycle_not_needed_for_empty_config():
    assert _lifecycle_needed([]) is False


def test_lifecycle_not_needed_without_bug_fix_ensemble():
    crs_list = [
        _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=False),
        _fake_crs(is_bug_fixing=False, is_bug_fixing_ensemble=False),
    ]
    assert _lifecycle_needed(crs_list) is False


def test_lifecycle_not_needed_with_ensemble_but_no_watchable_crs():
    # Only the ensemble CRS is present; nothing for lifecycle to watch.
    crs_list = [_fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=True)]
    assert _lifecycle_needed(crs_list) is False


def test_lifecycle_not_needed_when_watchable_crs_has_no_module():
    crs_list = [
        _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=True),
        _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=False, dockerfiles=()),
    ]
    assert _lifecycle_needed(crs_list) is False


def test_lifecycle_needed_with_ensemble_and_watchable_crs():
    crs_list = [
        _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=True),
        _fake_crs(is_bug_fixing=True, is_bug_fixing_ensemble=False),
    ]
    assert _lifecycle_needed(crs_list) is True
