"""Unit tests for _validate_machine_resources step in CRSCompose."""

from typing import cast

import pytest

from oss_crs.src.config.crs_compose import CRSComposeConfig
from oss_crs.src.crs_compose import CRSCompose

MOCK_CPU_COUNT = 12
MOCK_MEMORY = 32 * (1024**3)


class _FakeResource:
    def __init__(self, cpuset: str, memory: str):
        self.cpuset = cpuset
        self.memory = memory


class _FakeComposeConfig:
    def __init__(self, infra: _FakeResource, crs_entries: dict):
        self.oss_crs_infra = infra
        self.crs_entries = crs_entries


def _make_compose(
    infra_cpuset: str,
    infra_memory: str = "4G",
    crs_entries: dict | None = None,
) -> CRSCompose:
    compose = object.__new__(CRSCompose)
    compose.config = cast(
        CRSComposeConfig,
        _FakeComposeConfig(
            _FakeResource(infra_cpuset, infra_memory),
            crs_entries or {},
        ),
    )
    return compose


@pytest.fixture
def patched_resources(monkeypatch):
    """Patch machine resource detection with standard mock values.

    Returns a tuple of (warnings, errors) lists.
    """
    warnings = []
    errors = []
    monkeypatch.setattr("oss_crs.src.crs_compose.os.cpu_count", lambda: MOCK_CPU_COUNT)
    monkeypatch.setattr("oss_crs.src.crs_compose.get_host_memory", lambda: MOCK_MEMORY)
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_warning", lambda msg: warnings.append(msg)
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_error", lambda msg: errors.append(msg)
    )
    return warnings, errors


## CPU and memory checks


def test_cpu_within_bounds_no_warning(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose(f"0-{MOCK_CPU_COUNT - 2}")
    assert compose._validate_machine_resources() is True
    assert len(errors) == 0


def test_cpu_at_max_valid_id_no_warning(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose(f"0-{MOCK_CPU_COUNT - 1}")
    assert compose._validate_machine_resources() is True
    assert len(errors) == 0


def test_cpu_equals_machine_count_errors(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose(f"0-{MOCK_CPU_COUNT}")
    assert compose._validate_machine_resources() is False
    assert len(errors) == 1
    assert "does not have adequate resources" in errors[0]


def test_cpu_exceeds_machine_count_errors(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose(f"0-{MOCK_CPU_COUNT + 1}")
    assert compose._validate_machine_resources() is False
    assert len(errors) == 1
    assert "does not have adequate resources" in errors[0]


def test_memory_at_exact_limit_no_warning(patched_resources):
    warnings, errors = patched_resources
    exact_memory = f"{MOCK_MEMORY // (1024**3)}G"
    compose = _make_compose("0-3", exact_memory)
    assert compose._validate_machine_resources() is True
    assert len(errors) == 0


def test_memory_over_limit_errors(patched_resources):
    warnings, errors = patched_resources
    over_memory = f"{(MOCK_MEMORY // (1024**3)) + 1}G"
    compose = _make_compose("0-3", over_memory)
    assert compose._validate_machine_resources() is False
    assert len(errors) == 1
    assert "does not have adequate resources" in errors[0]


def test_crs_entry_exceeds_cpu_count_errors(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose(
        "0-3",
        crs_entries={"crs-a": _FakeResource(f"0-{MOCK_CPU_COUNT}", "4G")},
    )
    assert compose._validate_machine_resources() is False
    assert len(errors) == 1
    assert "does not have adequate resources" in errors[0]


def test_combined_memory_across_entries_errors(patched_resources):
    warnings, errors = patched_resources
    half_memory = f"{MOCK_MEMORY // (1024**3) // 2 + 1}G"
    compose = _make_compose(
        "0-3",
        infra_memory=half_memory,
        crs_entries={
            "crs-a": _FakeResource("0-3", half_memory),
        },
    )
    assert compose._validate_machine_resources() is False
    assert len(errors) == 1
    assert "does not have adequate resources" in errors[0]


def test_both_adequate_no_warning(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose("0-3", "4G")
    assert compose._validate_machine_resources() is True
    assert len(errors) == 0


## undetectable machine resources checks


def test_undetectable_cpu_skips_check(monkeypatch):
    warnings = []
    errors = []
    monkeypatch.setattr("oss_crs.src.crs_compose.os.cpu_count", lambda: None)
    monkeypatch.setattr("oss_crs.src.crs_compose.get_host_memory", lambda: MOCK_MEMORY)
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_warning", lambda msg: warnings.append(msg)
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_error", lambda msg: errors.append(msg)
    )

    compose = _make_compose("0-3")
    assert compose._validate_machine_resources() is True
    assert len(warnings) == 1
    assert "Could not determine machine resources" in warnings[0]
    assert len(errors) == 0


def test_undetectable_memory_skips_check(monkeypatch):
    warnings = []
    errors = []
    monkeypatch.setattr("oss_crs.src.crs_compose.os.cpu_count", lambda: MOCK_CPU_COUNT)
    monkeypatch.setattr("oss_crs.src.crs_compose.get_host_memory", lambda: None)
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_warning", lambda msg: warnings.append(msg)
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.log_error", lambda msg: errors.append(msg)
    )

    compose = _make_compose("0-3")
    assert compose._validate_machine_resources() is True
    assert len(warnings) == 1
    assert "Could not determine machine resources" in warnings[0]
    assert len(errors) == 0


# parsing error checks


def test_invalid_cpuset_warns(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose("not-a-cpuset", "4G")
    compose._validate_machine_resources()
    assert any("Failed to validate cpuset" in w for w in warnings)


def test_invalid_memory_string_warns(patched_resources):
    warnings, errors = patched_resources
    compose = _make_compose("0-3", "notmemory")
    compose._validate_machine_resources()
    assert any("Failed to parse memory" in w for w in warnings)
