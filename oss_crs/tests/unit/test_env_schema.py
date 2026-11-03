# SPDX-License-Identifier: MIT
"""Unit tests for environment variable schema and validation rules."""

import pytest

from oss_crs.src.env_schema import (
    BUILD_SENSITIVE_USER_OVERRIDABLE,
    ENV_KEY_PATTERN,
    RESERVED_SYSTEM_EXACT,
    RESERVED_SYSTEM_PREFIXES,
    is_reserved_system_key,
    validate_additional_env_keys,
)


def test_env_key_pattern_valid_identifiers():
    valid_keys = [
        "A",
        "FOO",
        "FOO_BAR",
        "_INTERNAL",
        "__PRIVATE__",
        "VAR_123",
        "lowercase_var",
        "MixedCase_123",
    ]
    for key in valid_keys:
        assert ENV_KEY_PATTERN.match(key) is not None, f"Expected {key} to be valid"


def test_env_key_pattern_invalid_identifiers():
    invalid_keys = [
        "",
        "123VAR",
        "VAR-NAME",
        "VAR.NAME",
        "VAR NAME",
        "VAR@NAME",
        "VAR$NAME",
        "VAR=VALUE",
    ]
    for key in invalid_keys:
        assert ENV_KEY_PATTERN.match(key) is None, f"Expected {key} to be invalid"


def test_is_reserved_system_key():
    # Exact reserved keywords
    for exact in RESERVED_SYSTEM_EXACT:
        assert is_reserved_system_key(exact) is True

    # Prefixed reserved keys
    for prefix in RESERVED_SYSTEM_PREFIXES:
        assert is_reserved_system_key(f"{prefix}CUSTOM_VAR") is True
        assert is_reserved_system_key(f"{prefix}") is True

    # Non-reserved user variables
    user_keys = [
        "CUSTOM_KEY",
        "MY_APP_ENV",
        "VERSION_2",
        "NOT_OSS_CRS_VAR",
        "OSS_CRS",  # Missing trailing underscore
        "SANITIZER",
        "FUZZING_ENGINE",
    ]
    for key in user_keys:
        assert is_reserved_system_key(key) is False


def test_build_sensitive_user_overridable_keys():
    expected_keys = {"SANITIZER", "FUZZING_ENGINE", "ARCHITECTURE", "FUZZING_LANGUAGE"}
    assert BUILD_SENSITIVE_USER_OVERRIDABLE == expected_keys


def test_validate_additional_env_keys_success():
    input_env = {
        "KEY_ONE": "value1",
        "KEY_TWO": 123,
        "_SECRET": "abc",
        "NUMERIC_STR": "456",
    }
    result = validate_additional_env_keys(input_env, scope="TestScope")
    assert result == {
        "KEY_ONE": "value1",
        "KEY_TWO": "123",
        "_SECRET": "abc",
        "NUMERIC_STR": "456",
    }


def test_validate_additional_env_keys_empty():
    assert validate_additional_env_keys({}, scope="TestScope") == {}


def test_validate_additional_env_keys_invalid_key_raises():
    invalid_inputs = [
        {"INVALID-KEY": "val"},
        {"123BAD": "val"},
        {"KEY WITH SPACE": "val"},
        {"": "val"},
    ]
    for bad_map in invalid_inputs:
        with pytest.raises(ValueError, match="invalid env var key"):
            validate_additional_env_keys(bad_map, scope="BuildConfig.additional_env")


def test_validate_additional_env_keys_scope_in_error_message():
    custom_scope = "CustomModule.environment"
    with pytest.raises(ValueError) as excinfo:
        validate_additional_env_keys({"bad-key": "v"}, scope=custom_scope)
    assert custom_scope in str(excinfo.value)
