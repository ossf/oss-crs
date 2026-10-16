# SPDX-License-Identifier: MIT
"""Run provenance and the human-navigable run index.

Runs live at ``<work_dir>/crs_compose/<compose_hash>/<sanitizer>/runs/<run_id>/``.
The compose hash is opaque and a user-supplied run id carries no timestamp, so
finding a run by target or CRS means descending three levels. This module keeps
a symlink index at the root ``--work-dir``::

    <work_dir>/index/
    ├── by-target/<target_name>/<YYYYMMDD-HHMMSS>_<run_id>  -> ../../../crs_compose/<hash>/<sanitizer>/runs/<run_id>
    └── by-crs/<crs_name>/<YYYYMMDD-HHMMSS>_<run_id>        -> (same run directory)

Links are relative so they resolve through a read-only bind mount (the WebUI
container) and survive moving the work directory. The index sits *beside*
``crs_compose/``, never inside it: ``WorkDir``'s enumerators treat every
directory under ``crs_compose/`` as a compose hash and follow symlinks.

Each run also gets ``run-info.json`` next to ``BUILD_ID`` (see
:func:`build_run_info`), written once at run start.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

BY_TARGET = "by-target"
BY_CRS = "by-crs"
INDEX_GROUPS = (BY_TARGET, BY_CRS)
RUN_INFO_FILE_NAME = "run-info.json"


def build_run_info(
    *,
    target,
    run_id: str,
    sanitizer: str,
    build_id: Optional[str],
    compose_hash: str,
    compose_file: Optional[Path],
    crs_names: Iterable[str],
    started: datetime,
) -> dict:
    """Return the ``run-info.json`` payload for a run.

    ``build_id`` is ``None`` for source-only runs, which have no build output.
    ``compose_file`` is ``None`` when the compose was not loaded from a file.
    """
    return {
        "build_id": build_id,
        "compose_file": str(Path(compose_file).resolve()) if compose_file else None,
        "compose_hash": compose_hash,
        "crs_names": list(crs_names),
        "harness": target.target_harness,
        "run_id": run_id,
        "sanitizer": sanitizer,
        "started_at": started.isoformat(timespec="seconds"),
        "started_at_epoch": int(started.timestamp()),
        "target": {
            "key": target.get_workdir_target_key(),
            "name": target.name,
            "proj_path": str(Path(target.proj_path).resolve()),
            "repo_path": (
                str(Path(target.repo_path).resolve()) if target.repo_path else None
            ),
            "source_only": bool(target.source_only),
        },
    }


def make_link_name(started: datetime, run_id: str) -> str:
    """Index link name: ``<YYYYMMDD-HHMMSS>_<run_id>`` so ``ls`` sorts by start time."""
    return f"{started.strftime('%Y%m%d-%H%M%S')}_{run_id}"


def link_run(
    index_dir: Path,
    run_dir: Path,
    *,
    target_name: str,
    crs_names: Iterable[str],
    link_name: str,
    sanitizer: str,
    compose_hash: str,
) -> list[Path]:
    """Link ``run_dir`` under ``by-target/<target_name>`` and ``by-crs/<crs>`` for each CRS.

    Idempotent per run directory: any older link in a group that resolves to the
    same run directory is replaced, so a re-run with the same run id leaves one
    link reflecting the latest start. If ``link_name`` is held by a link to a
    *different* run directory (for example the same ``--run-id`` under two
    sanitizers in the same second), ``_<sanitizer>`` and then
    ``_<sanitizer>_<compose_hash>`` are appended. A real file or directory that
    owns the name is never touched; that group simply gets no link.

    Returns the links that point at ``run_dir`` after the call.
    """
    index_dir = Path(index_dir)
    real_run_dir = os.path.realpath(run_dir)
    group_dirs = [index_dir / BY_TARGET / target_name]
    group_dirs += [index_dir / BY_CRS / name for name in crs_names]
    candidates = (
        link_name,
        f"{link_name}_{sanitizer}",
        f"{link_name}_{sanitizer}_{compose_hash}",
    )

    links: list[Path] = []
    for group_dir in group_dirs:
        link = _link_into_group(group_dir, real_run_dir, candidates)
        if link is not None:
            links.append(link)
    return links


def _link_into_group(
    group_dir: Path, real_run_dir: str, candidates: tuple[str, ...]
) -> Optional[Path]:
    group_dir.mkdir(parents=True, exist_ok=True)

    # Drop stale links to the same run (an earlier start of the same run id).
    for entry in group_dir.iterdir():
        if (
            entry.is_symlink()
            and entry.name not in candidates
            and os.path.realpath(entry) == real_run_dir
        ):
            entry.unlink()

    rel_target = os.path.relpath(real_run_dir, start=os.path.realpath(group_dir))
    for name in candidates:
        link = group_dir / name
        if link.is_symlink():
            if os.path.realpath(link) == real_run_dir:
                return link  # already correct
            continue  # held by a link to another run: try the next name
        if link.exists():
            continue  # a real file or directory owns this name: never touch it
        try:
            link.symlink_to(rel_target)
        except FileExistsError:
            continue  # raced with a parallel run: try the next name
        return link
    return None


def iter_index_links(index_dir: Path) -> list[Path]:
    """Every symlink under ``index/{by-target,by-crs}/*/``: by-target first, sorted within."""
    index_dir = Path(index_dir)
    links: list[Path] = []
    for group in INDEX_GROUPS:
        group_root = index_dir / group
        if not group_root.is_dir():
            continue
        for group_dir in sorted(group_root.iterdir()):
            if not group_dir.is_dir():
                continue
            links.extend(sorted(p for p in group_dir.iterdir() if p.is_symlink()))
    return links


def find_links_to(
    index_dir: Path, run_dir: Path, *, group: str = BY_TARGET
) -> list[Path]:
    """Index links in ``group`` that resolve to ``run_dir``, sorted."""
    real_run_dir = os.path.realpath(run_dir)
    group_root = Path(index_dir) / group
    if not group_root.is_dir():
        return []
    return sorted(
        link
        for group_dir in group_root.iterdir()
        if group_dir.is_dir()
        for link in group_dir.iterdir()
        if link.is_symlink() and os.path.realpath(link) == real_run_dir
    )


def find_prunable_links(index_dir: Path, doomed_dirs: Iterable[Path]) -> list[Path]:
    """Index links that are dangling or resolve into a directory about to be removed."""
    doomed = {os.path.realpath(d) for d in doomed_dirs}
    return [
        link
        for link in iter_index_links(index_dir)
        if not link.exists() or os.path.realpath(link) in doomed
    ]


def unlink_if_dangling(link: Path) -> bool:
    """Remove ``link`` if it is a dangling symlink; drop its group dir if now empty.

    Only ``unlink`` is ever applied to a symlink. Returns True if removed.
    """
    link = Path(link)
    if not link.is_symlink() or link.exists():
        return False
    link.unlink()
    try:
        link.parent.rmdir()
    except OSError:
        pass
    return True
