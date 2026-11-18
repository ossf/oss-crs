# SPDX-License-Identifier: MIT
"""Unit tests for TargetConfig specification and parsing."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oss_crs.src.config.target import (
    FuzzingEngine,
    TargetArch,
    TargetConfig,
    TargetLanguage,
    TargetSanitizer,
)


def test_target_config_minimal_valid():
    config = TargetConfig(language=TargetLanguage.C)
    assert config.language == TargetLanguage.C
    assert config.main_repo is None
    assert config.base_os_version == "legacy"
    assert config.sanitizers == [TargetSanitizer.ASAN, TargetSanitizer.UBSAN]
    assert config.architectures == [TargetArch.X86_64]
    assert config.fuzzing_engines == [
        FuzzingEngine.LIBFUZZER,
        FuzzingEngine.AFL,
        FuzzingEngine.HONGGFUZZ,
        FuzzingEngine.CENTIPEDE,
    ]


def test_target_config_custom_values():
    config = TargetConfig(
        language=TargetLanguage.CPP,
        main_repo="https://github.com/example/project.git",
        base_os_version="ubuntu-24-04",
        sanitizers=[TargetSanitizer.MSAN, TargetSanitizer.COVERAGE],
        architectures=[TargetArch.AARCH64, TargetArch.I386],
        fuzzing_engines=[FuzzingEngine.LIBFUZZER, FuzzingEngine.WYCHEPROOF],
    )
    assert config.language == TargetLanguage.CPP
    assert config.main_repo == "https://github.com/example/project.git"
    assert config.base_os_version == "ubuntu-24-04"
    assert config.sanitizers == [TargetSanitizer.MSAN, TargetSanitizer.COVERAGE]
    assert config.architectures == [TargetArch.AARCH64, TargetArch.I386]
    assert config.fuzzing_engines == [FuzzingEngine.LIBFUZZER, FuzzingEngine.WYCHEPROOF]


def test_target_config_missing_required_language():
    with pytest.raises(ValidationError):
        TargetConfig.model_validate({})


def test_target_config_from_yaml_string():
    yaml_text = """
    language: rust
    main_repo: https://github.com/rust-lang/example.git
    sanitizers:
      - address
    architectures:
      - x86_64
    fuzzing_engines:
      - afl
    """
    config = TargetConfig.from_yaml(yaml_text)
    assert config.language == TargetLanguage.RUST
    assert config.main_repo == "https://github.com/rust-lang/example.git"
    assert config.sanitizers == [TargetSanitizer.ASAN]
    assert config.architectures == [TargetArch.X86_64]
    assert config.fuzzing_engines == [FuzzingEngine.AFL]


def test_target_config_normalize_experimental_sanitizers():
    # OSS-Fuzz syntax allows experimental flag dictionaries: [{address: {experimental: true}}]
    yaml_text = """
    language: c++
    sanitizers:
      - address
      - memory:
          experimental: true
      - undefined
    """
    config = TargetConfig.from_yaml(yaml_text)
    assert config.sanitizers == [
        TargetSanitizer.ASAN,
        TargetSanitizer.MSAN,
        TargetSanitizer.UBSAN,
    ]


def test_target_config_normalize_invalid_experimental_sanitizer():
    bad_data = {
        "language": "c",
        "sanitizers": [{"address": {}, "memory": {}}],  # multiple keys in single dict
    }
    with pytest.raises(ValidationError, match="must contain one sanitizer"):
        TargetConfig.model_validate(bad_data)


def test_target_config_from_yaml_file(tmp_path: Path):
    target_yaml_path = tmp_path / "project.yaml"
    target_yaml_path.write_text(
        """
        language: jvm
        base_os_version: ubuntu-22-04
        sanitizers:
          - address
        fuzzing_engines:
          - centipede
        """
    )
    config = TargetConfig.from_yaml_file(target_yaml_path)
    assert config.language == TargetLanguage.JVM
    assert config.base_os_version == "ubuntu-22-04"
    assert config.sanitizers == [TargetSanitizer.ASAN]
    assert config.fuzzing_engines == [FuzzingEngine.CENTIPEDE]
