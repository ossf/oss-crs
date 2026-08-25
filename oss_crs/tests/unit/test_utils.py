# SPDX-License-Identifier: MIT
"""Unit tests for oss_crs.src.utils module."""

from pathlib import Path

import pytest
import questionary
import re
from prompt_toolkit.keys import Keys
from questionary.prompts.common import InquirerControl

from oss_crs.src import utils
from oss_crs.src.utils import _enter_selects_pointed_choice, normalize_run_id


def test_rm_with_docker_preserves_default_alpine_command(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    utils.rm_with_docker(tmp_path / "out")

    assert commands == [
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/data",
            utils.OSS_CRS_ALPINE_TAG,
            "rm",
            "-rf",
            "/data/out",
        ]
    ]


def test_rm_with_docker_overrides_target_image_entrypoint(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    utils.rm_with_docker(tmp_path / "out", image_tag="project:tag")

    assert commands == [
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/data",
            "--entrypoint",
            "rm",
            "project:tag",
            "-rf",
            "/data/out",
        ]
    ]


class _FakeApp:
    def __init__(self):
        self.result = None

    def exit(self, result=None, **kwargs):
        self.result = result


class _FakeEvent:
    def __init__(self):
        self.app = _FakeApp()


def _press_enter(question) -> _FakeApp:
    """Invoke the binding prompt_toolkit would dispatch for Enter (the last)."""
    enter_binding = [
        binding
        for binding in question.application.key_bindings.bindings
        if binding.keys == (Keys.ControlM,)
    ][-1]
    event = _FakeEvent()
    enter_binding.handler(event)
    return event.app


def _checkbox(**kwargs):
    return questionary.checkbox(
        "Select:",
        choices=[
            questionary.Choice("first", "first"),
            questionary.Choice("second", "second"),
        ],
        **kwargs,
    )


def test_checkbox_enter_selects_highlighted_item_when_none_checked():
    question = _checkbox()
    _enter_selects_pointed_choice(question)

    assert _press_enter(question).result == ["first"]


def test_checkbox_enter_keeps_explicit_selection():
    question = _checkbox()
    _enter_selects_pointed_choice(question)
    # Simulate the user checking the second row before pressing Enter.
    ic = next(
        control
        for control in question.application.layout.find_all_controls()
        if isinstance(control, InquirerControl)
    )
    ic.selected_options = ["second"]

    assert _press_enter(question).result == ["second"]


def test_checkbox_enter_respects_validator():
    question = _checkbox(validate=lambda selected: len(selected) > 1)
    _enter_selects_pointed_choice(question)

    # The pointed-at fallback is a single value, so validation rejects it and
    # the prompt stays open (no exit result).
    assert _press_enter(question).result is None


class TestNormalizeRunId:
    """Tests for normalize_run_id function.

    Focuses on the important behavioral guarantees:
    - Collision prevention via hash suffix
    - Deterministic output
    - Filesystem safety
    - Unicode handling
    """

    def test_hash_prevents_collisions(self):
        """Inputs that normalize to different base strings should have different hashes."""
        # These normalize to genuinely different base strings
        result1 = normalize_run_id("test-run")
        result2 = normalize_run_id("test_run")
        result3 = normalize_run_id("alpha-beta")

        results = {result1, result2, result3}
        assert len(results) == 3, "Hash suffix should prevent collisions"

        # Inputs that normalize to the SAME base string should produce the same output
        # (case, spaces, and special chars are stripped/lowered before hashing)
        assert normalize_run_id("test-run") == normalize_run_id("TEST-RUN")
        assert normalize_run_id("test-run") == normalize_run_id("test run")

    def test_idempotent(self):
        """Calling normalize_run_id on an already-normalized ID returns the same value."""
        inputs = ["my-build-123", "test_run", "1778522723j6"]
        for input_id in inputs:
            once = normalize_run_id(input_id)
            twice = normalize_run_id(once)
            assert once == twice, (
                f"normalize_run_id is not idempotent for '{input_id}': "
                f"'{once}' != '{twice}'"
            )

    def test_deterministic(self):
        """Same input should always produce same output."""
        inputs = [
            "my-test-run-123",
            "Test Run With Spaces",
            "special@chars#here!",
            "test-日本語-run",  # Mixed unicode + ascii
        ]
        for input_id in inputs:
            result1 = normalize_run_id(input_id)
            result2 = normalize_run_id(input_id)
            assert result1 == result2, (
                f"Output should be deterministic for '{input_id}'"
            )

    def test_filesystem_safe(self):
        """Result should be safe for filesystem use across platforms."""
        dangerous_inputs = [
            "test/run",  # Unix path separator
            "test\\run",  # Windows path separator
            "test:run",  # Windows drive separator
            "test*run",  # Glob wildcard
            "test?run",  # Glob wildcard
            'test"run',  # Quote
            "test<run>",  # Angle brackets
            "test|run",  # Pipe
            "CON",  # Windows reserved name
            "test\x00run",  # Null byte
            "test\nrun",  # Newline
        ]
        # Only lowercase alphanumeric, hyphens, and underscores allowed
        safe_pattern = re.compile(r"^[a-z0-9_-]+$")

        for dangerous in dangerous_inputs:
            try:
                result = normalize_run_id(dangerous)
                assert safe_pattern.match(result), (
                    f"'{result}' from '{dangerous}' is not filesystem safe"
                )
            except ValueError:
                # Empty result after normalization is also acceptable
                pass

    def test_unicode_handling(self):
        """Unicode characters should be handled gracefully."""
        unicode_inputs = [
            "test-日本語-run",
            "tëst-rün",
            "тест",
            "🚀rocket",
        ]
        safe_pattern = re.compile(r"^[a-z0-9_-]+$")

        for unicode_input in unicode_inputs:
            try:
                result = normalize_run_id(unicode_input)
                assert safe_pattern.match(result), (
                    f"'{result}' from '{unicode_input}' is not valid"
                )
            except ValueError:
                # If all chars are unicode, result may be empty - that's ok
                pass

    def test_empty_input_raises(self):
        """Empty or non-alphanumeric-only strings should raise."""
        with pytest.raises(ValueError, match="at least one alphanumeric"):
            normalize_run_id("")

        with pytest.raises(ValueError, match="at least one alphanumeric"):
            normalize_run_id("@#$%^&*()")

    def test_path_separator_is_normalized(self):
        """Path separators should be normalized like other delimiters."""
        result = normalize_run_id("../escape")
        assert result.startswith("escape-")
        result2 = normalize_run_id(r"test\\run")
        assert result2.startswith("test-run-")
