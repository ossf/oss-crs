from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from pydantic import BaseModel, Field


# See https://google.github.io/oss-fuzz/getting-started/new-project-guide/#language
class TargetLangauge(Enum):
    C = "c"
    CPP = "c++"
    GO = "go"
    RUST = "rust"
    PYTHON = "python"
    JVM = "jvm"  # Java, Kotlin, Scala and other JVM-based languages
    SWIFT = "swift"
    JAVASCRIPT = "javascript"
    LUA = "lua"


# See https://google.github.io/oss-fuzz/getting-started/new-project-guide/#sanitizers
class TargetSanitizer(Enum):
    ASAN = "address"
    MSAN = "memory"
    UBSAN = "undefined"


# See https://google.github.io/oss-fuzz/getting-started/new-project-guide/#architectures
class TargetArch(Enum):
    X86_64 = "x86_64"
    I386 = "i386"


# See https://google.github.io/oss-fuzz/getting-started/new-project-guide/#fuzzing_engines-optional
class FuzzingEngine(Enum):
    LIBFUZZER = "libfuzzer"
    AFL = "afl"
    HONGGFUZZ = "honggfuzz"
    CENTIPEDE = "centipede"


class TargetConfig(BaseModel):
    """Configuration for an OSS-Fuzz target project.

    See https://google.github.io/oss-fuzz/getting-started/new-project-guide/
    """

    # Required fields
    language: TargetLangauge = Field(
        ...,
        description="Programming language the project is written in.",
    )

    main_repo: Optional[str] = Field(
        default=None,
        description="Path to source code repository hosting the code, e.g. https://path/to/main/repo.git",
    )

    # Optional fields with defaults based on OSS-Fuzz documentation
    sanitizers: list[TargetSanitizer] = Field(
        default=[TargetSanitizer.ASAN, TargetSanitizer.UBSAN],
        description="list of sanitizers to use. Defaults to address and undefined.",
    )

    architectures: list[TargetArch] = Field(
        default=[TargetArch.X86_64],
        description="list of architectures to fuzz on. Defaults to x86_64.",
    )

    fuzzing_engines: list[FuzzingEngine] = Field(
        default=[
            FuzzingEngine.LIBFUZZER,
            FuzzingEngine.AFL,
            FuzzingEngine.HONGGFUZZ,
            FuzzingEngine.CENTIPEDE,
        ],
        description="list of fuzzing engines to use. Defaults to all supported engines.",
    )

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "TargetConfig":
        """Parse Target config from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: Path) -> "TargetConfig":
        """Parse Target config from YAML file."""
        with open(filepath.resolve(), "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "TargetConfig":
        """Parse Target config from dictionary."""
        return cls.model_validate(data)
