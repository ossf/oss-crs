from enum import Enum
import os
import hashlib
import subprocess
from pathlib import Path


class EnvType(str, Enum):
    LOCAL = "local"


def get_env(key: str, allow_none: bool = False) -> str:
    # Error if key does not exist in environment
    value = os.environ.get(key)
    if value is None:
        if allow_none:
            return ""
        raise KeyError(f"Environment variable '{key}' not found")
    return value


_OSS_CRS_RUN_ENV_TYPE = None


def get_run_env_type() -> EnvType:
    """Lazy-load OSS_CRS_RUN_ENV_TYPE (not available during Docker build)."""
    global _OSS_CRS_RUN_ENV_TYPE
    if _OSS_CRS_RUN_ENV_TYPE is None:
        _OSS_CRS_RUN_ENV_TYPE = EnvType(get_env("OSS_CRS_RUN_ENV_TYPE"))
    return _OSS_CRS_RUN_ENV_TYPE


def rsync_copy(src: Path, dst: Path) -> None:
    # Create parent directories
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        # Directory: copy everything recursively
        subprocess.run(["rsync", "-a", "--", f"{src}/", f"{dst}/"], check=True)
    else:
        # File: just copy it (rsync uses temp-file + atomic rename internally)
        subprocess.run(["rsync", "-a", "--", str(src), str(dst)], check=True)


def is_data_file(path: Path) -> bool:
    """Check if a path is a regular data file (not a directory, not a dotfile)."""
    return path.is_file() and not path.name.startswith(".")



def file_hash(path: Path) -> str:
    """Compute the MD5 checksum of a file."""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
