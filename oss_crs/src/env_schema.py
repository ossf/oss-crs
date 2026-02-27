import re
from typing import Mapping


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Build-sensitive keys are intentionally user-overridable via additional_env.
BUILD_SENSITIVE_USER_OVERRIDABLE = {
    "SANITIZER",
    "FUZZING_ENGINE",
    "ARCHITECTURE",
    "FUZZING_LANGUAGE",
}

# Reserved framework-owned keys.
RESERVED_SYSTEM_PREFIXES = ("OSS_CRS_",)
RESERVED_SYSTEM_EXACT = {"VERSION"}


def is_reserved_system_key(key: str) -> bool:
    if key in RESERVED_SYSTEM_EXACT:
        return True
    return any(key.startswith(prefix) for prefix in RESERVED_SYSTEM_PREFIXES)


def validate_additional_env_keys(
    env_map: Mapping[str, str], *, scope: str
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in env_map.items():
        key = str(raw_key)
        if not ENV_KEY_PATTERN.match(key):
            raise ValueError(
                f"{scope} has invalid env var key '{key}'. "
                "Expected pattern: [A-Za-z_][A-Za-z0-9_]*"
            )
        normalized[key] = str(raw_value)
    return normalized
