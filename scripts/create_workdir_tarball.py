#!/usr/bin/env python3

import argparse
import os
import tarfile
import tempfile
from pathlib import Path


def should_include(path: Path) -> bool:
    posix = path.as_posix()
    return (
        ("/builds/" in posix and posix.endswith("/BUILD_OUT_DIR/build"))
        or posix.endswith("/logs")
        or posix.endswith("/EXCHANGE_DIR")
        or ("/runs/" in posix and "/crs/" in posix and posix.endswith("/SUBMIT_DIR"))
    )


def find_roots(workdir_root: Path) -> list[Path]:
    roots: set[Path] = set()

    def ignore_walk_error(_: OSError) -> None:
        return None

    for dirpath, dirnames, _filenames in os.walk(
        workdir_root, topdown=True, followlinks=False, onerror=ignore_walk_error
    ):
        current = Path(dirpath)
        current_posix = current.as_posix()

        if "/builds/" in current_posix and current.name == "BUILD_OUT_DIR":
            if "build" in dirnames:
                roots.add(current / "build")
            dirnames[:] = []
            continue

        matched_dirnames = []
        for dirname in dirnames:
            candidate = current / dirname
            if should_include(candidate):
                roots.add(candidate)
                matched_dirnames.append(dirname)

        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in matched_dirnames
            and not (current.name == "BUILD_OUT_DIR" and dirname == "src")
        ]

    return sorted(roots)


def write_note_tarball(output: Path, message: str) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        note_path = Path(tmp_dir) / "README.txt"
        note_path.write_text(f"{message}\n")
        with tarfile.open(output, "w:gz") as tar:
            tar.add(note_path, arcname="README.txt")


def write_roots_tarball(output: Path, roots: list[Path]) -> None:
    common_root = Path(os.path.commonpath([str(path) for path in roots]))
    archive_base = common_root.parent
    with tarfile.open(output, "w:gz") as tar:
        for root in roots:
            tar.add(root, arcname=root.relative_to(archive_base))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir-root", default="/tmp/oss-crs-work/crs_compose")
    parser.add_argument("--output", required=True)
    parser.add_argument("--empty-message", required=True)
    args = parser.parse_args()

    workdir_root = Path(args.workdir_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    roots = find_roots(workdir_root) if workdir_root.exists() else []
    if roots:
        write_roots_tarball(output, roots)
    else:
        write_note_tarball(output, args.empty_message)

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
