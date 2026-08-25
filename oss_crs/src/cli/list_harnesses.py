# SPDX-License-Identifier: MIT
"""Build an OSS-Fuzz project and list the runnable harnesses it produces."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from ..env_policy import OSS_FUZZ_TARGET_ENV
from ..target import Target, file_lock
from ..utils import rm_with_docker

CACHE_SCHEMA_VERSION = 1
FUZZ_TARGET_MARKER = b"LLVMFuzzerTestOneInput"
NATIVE_LANGUAGES = {"c", "c++", "go", "rust", "swift", "lua"}
IGNORED_NAMES = {"centipede", "llvm-symbolizer"}
IGNORED_SUFFIXES = {
    ".a",
    ".class",
    ".covreport",
    ".dict",
    ".jar",
    ".json",
    ".o",
    ".options",
    ".profraw",
    ".so",
    ".txt",
    ".yaml",
    ".yml",
    ".zip",
}


def _error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)


def _warning(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def add_list_harnesses_command(subparsers, default_work_dir: Path) -> None:
    parser = subparsers.add_parser(
        "list-harnesses",
        help="Build an OSS-Fuzz project and list its runnable harnesses",
    )
    parser.add_argument(
        "--fuzz-proj-path",
        dest="target_proj_path",
        type=Path,
        required=True,
        help="Path to an OSS-Fuzz project directory",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=default_work_dir,
        help="Working directory used to cache compiled harnesses",
    )


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _cache_dir(work_dir: Path, project_dir: Path) -> Path:
    path_key = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:12]
    return work_dir / "list-harnesses" / f"{project_dir.name}-{path_key}"


def _build_key(
    *,
    project_hash: str,
    project_repository_head: str | None,
    source_repository_head: str | None,
    target: Target,
) -> str:
    state = {
        "schema": CACHE_SCHEMA_VERSION,
        "project_hash": project_hash,
        "project_repository_head": project_repository_head,
        "source_repository_head": source_repository_head,
        "language": target.language,
        "engine": target.engine,
        "sanitizer": _build_sanitizer(target),
        "architecture": target.architecture,
    }
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _build_sanitizer(target: Target) -> str:
    # OSS-Fuzz's helper defaults JavaScript projects to an uninstrumented build.
    return "none" if target.language == "javascript" else target.sanitizer


def _compile_project(target: Target, image_tag: str, out_dir: Path) -> bool:
    target_env = target.get_target_env()
    target_env["sanitizer"] = _build_sanitizer(target)
    environment = {
        name: str(target_env[key]) for name, key in OSS_FUZZ_TARGET_ENV.items()
    }
    environment.update(
        {
            "PROJECT_NAME": str(target_env["name"]),
            "HELPER": "True",
        }
    )
    work_dir = out_dir.parent / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    platform = "linux/arm64" if target.architecture == "aarch64" else "linux/amd64"
    command = [
        "docker",
        "run",
        "--privileged",
        "--shm-size=2g",
        "--platform",
        platform,
        "--rm",
    ]
    for name, value in environment.items():
        command.extend(["-e", f"{name}={value}"])
    command.extend(
        [
            "-v",
            f"{out_dir.resolve()}:/out",
            "-v",
            f"{work_dir.resolve()}:/work",
            image_tag,
            "compile",
        ]
    )
    try:
        return (
            subprocess.run(
                command,
                stdout=sys.stderr,
                stderr=subprocess.STDOUT,
            ).returncode
            == 0
        )
    except OSError as exc:
        _error(f"Failed to run Docker: {exc}")
        return False


def _contains_any(path: Path, needles: tuple[bytes, ...]) -> bool:
    overlap = max(map(len, needles), default=1) - 1
    previous = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                window = previous + chunk
                if any(needle in window for needle in needles):
                    return True
                previous = window[-overlap:] if overlap else b""
    except OSError:
        return False
    return False


def _contains_all(path: Path, needles: tuple[bytes, ...]) -> bool:
    return all(_contains_any(path, (needle,)) for needle in needles)


def _file_prefix(path: Path, size: int = 20) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.read(size)
    except OSError:
        return b""


def _is_native_executable(path: Path, prefix: bytes) -> bool:
    if path.stat().st_mode & 0o111:
        return True
    if not prefix.startswith(b"\x7fELF") or len(prefix) < 18:
        return False
    byte_order = "little" if prefix[5] == 1 else "big"
    elf_type = int.from_bytes(prefix[16:18], byte_order)
    return elf_type in (2, 3)  # ET_EXEC or ET_DYN (PIE)


def _is_harness(path: Path, language: str) -> bool:
    name = path.name
    if (
        not path.is_file()
        or name in IGNORED_NAMES
        or name.startswith(("afl-", "jazzer_", "."))
        or path.suffix.lower() in IGNORED_SUFFIXES
    ):
        return False

    prefix = _file_prefix(path)
    has_marker = _contains_any(path, (FUZZ_TARGET_MARKER,))
    if language in NATIVE_LANGUAGES:
        return _is_native_executable(path, prefix) and has_marker

    is_launcher = bool(path.stat().st_mode & 0o111) or prefix.startswith(b"#!")
    if not is_launcher:
        return False
    if has_marker:
        return True
    if language == "jvm":
        return _contains_all(path, (b"jazzer_driver", b"--target_class"))
    if language == "python":
        return _contains_all(path, (b"atheris", b"Fuzz"))
    if language == "javascript":
        return _contains_all(path, (b"jazzer", b"node"))
    if language == "ruby":
        return _contains_all(path, (b"ruby", b"fuzz"))
    return False


def find_harnesses(out_dir: Path, language: str) -> list[str]:
    """Return sorted runnable harness names from an OSS-Fuzz /out directory."""
    if not out_dir.is_dir():
        return []
    return sorted(
        path.name for path in out_dir.iterdir() if _is_harness(path, language)
    )


def _clear_build_artifacts(cache_dir: Path, image_tag: str) -> None:
    for path in (cache_dir / "out", cache_dir / "work"):
        if path.exists():
            rm_with_docker(path, image_tag=image_tag)


def handle_list_harnesses(args) -> bool:
    project_dir = args.target_proj_path
    if not project_dir.is_dir():
        _error(f"OSS-Fuzz project directory does not exist: {project_dir}")
        return False
    if not (project_dir / "Dockerfile").is_file():
        _error(f"OSS-Fuzz project is missing Dockerfile: {project_dir}")
        return False

    target = Target(args.work_dir, project_dir, None)
    cache_dir = _cache_dir(args.work_dir, project_dir)
    lock_path = cache_dir.parent / f"{cache_dir.name}.lock"
    with file_lock(lock_path):
        return _handle_list_harnesses_locked(target, cache_dir)


def _handle_list_harnesses_locked(target: Target, cache_dir: Path) -> bool:
    out_dir = cache_dir / "out"
    metadata_file = cache_dir / "metadata.json"
    previous = _read_metadata(metadata_file)

    project_hash = target.get_repo_hash()
    project_repository_head = target.get_project_repository_head()
    source_repository_head = target.get_source_repository_head()
    if source_repository_head is None and target.main_repo:
        can_reuse_previous_head = (
            previous.get("project_hash") == project_hash
            and previous.get("project_repository_head") == project_repository_head
        )
        if can_reuse_previous_head:
            source_repository_head = previous.get("source_repository_head")
        _warning(
            f"Could not resolve HEAD for {target.main_repo}; "
            "using the last cached revision when available."
        )

    build_key = _build_key(
        project_hash=project_hash,
        project_repository_head=project_repository_head,
        source_repository_head=source_repository_head,
        target=target,
    )
    cache_hit = previous.get("build_key") == build_key and out_dir.is_dir()

    if not cache_hit:
        image_tag = target.build_docker_image(
            force_rebuild=True, console=Console(stderr=True)
        )
        if image_tag is None:
            _error(f"Failed to build OSS-Fuzz project image: {target.name}")
            return False
        _clear_build_artifacts(cache_dir, image_tag)
        out_dir.mkdir(parents=True, exist_ok=True)
        if not _compile_project(target, image_tag, out_dir):
            _error(f"Failed to compile OSS-Fuzz project: {target.name}")
            return False
        _write_metadata(
            metadata_file,
            {
                "schema": CACHE_SCHEMA_VERSION,
                "build_key": build_key,
                "project_hash": project_hash,
                "project_repository_head": project_repository_head,
                "source_repository_head": source_repository_head,
                "image_tag": image_tag,
                "language": target.language,
            },
        )

    for harness in find_harnesses(out_dir, target.language):
        print(harness)
    return True
