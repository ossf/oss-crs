# SPDX-License-Identifier: MIT
from enum import Enum
import os
import hashlib
import secrets
import shutil
import stat
import tempfile
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


def _is_unchanged(src_st: os.stat_result, dst: Path) -> bool:
    """rsync-style quick check: same size and mtime means the copy is current."""
    try:
        dst_st = dst.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(dst_st.st_mode)
        and dst_st.st_size == src_st.st_size
        and dst_st.st_mtime_ns == src_st.st_mtime_ns
    )


def _copy_metadata(src_st: os.stat_result, dst: str) -> None:
    os.chmod(dst, stat.S_IMODE(src_st.st_mode))
    os.utime(dst, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
    if os.geteuid() == 0:
        os.chown(dst, src_st.st_uid, src_st.st_gid)


def _copy_entry(src: Path, dst: Path) -> None:
    """Copy one file or symlink to dst, atomically replacing any existing file.

    Data is written to a dotfile in dst's directory and renamed into place, so
    readers (and watchers that ignore dotfiles) never see a partial file.
    """
    src_st = src.lstat()
    if stat.S_ISLNK(src_st.st_mode):
        tmp = dst.parent / f".{dst.name}.{secrets.token_hex(8)}"
        os.symlink(os.readlink(src), tmp)
        os.replace(tmp, dst)
        return
    if not stat.S_ISREG(src_st.st_mode):
        # Like rsync without --devices/--specials: skip FIFOs, sockets, devices.
        return
    if _is_unchanged(src_st, dst):
        return

    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        _copy_metadata(src_st, tmp)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_tree(src: Path, dst: Path, exclude: frozenset[str], delete: bool) -> None:
    """Merge src's contents into dst.

    Entries named in exclude are skipped at every depth. With delete, entries
    only in dst are removed, except excluded ones (as with rsync --delete).
    """
    dst.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    with os.scandir(src) as entries:
        for entry in entries:
            if entry.name in exclude:
                continue
            copied.add(entry.name)
            entry_src = Path(entry.path)
            entry_dst = dst / entry.name
            if entry.is_dir(follow_symlinks=False):
                # A file or symlink where src has a directory is replaced (the
                # top-level dst is followed if it is a symlink, as with rsync).
                if entry_dst.is_symlink() or entry_dst.is_file():
                    entry_dst.unlink()
                _copy_tree(entry_src, entry_dst, exclude, delete)
                continue
            if delete and entry_dst.is_dir() and not entry_dst.is_symlink():
                shutil.rmtree(entry_dst)
            _copy_entry(entry_src, entry_dst)
    if delete:
        with os.scandir(dst) as entries:
            stale = [
                Path(e.path)
                for e in entries
                if e.name not in copied and e.name not in exclude
            ]
        for path in stale:
            _remove(path)
    _copy_metadata(src.stat(), str(dst))


def copy_path(
    src: Path,
    dst: Path,
    *,
    exclude: frozenset[str] = frozenset(),
    delete: bool = False,
) -> None:
    """Copy a file or directory with ``rsync -a`` semantics, in pure Python.

    - Directory: its contents are merged into dst (like ``rsync -a src/ dst/``).
      ``exclude`` and ``delete`` behave like rsync's ``--exclude=<name>`` and
      ``--delete``.
    - File: copied to dst, or into dst if dst is an existing directory.

    Permissions and timestamps are preserved (ownership too, when running as
    root), symlinks are copied as symlinks, and files whose size and mtime
    already match are skipped.
    """
    src = Path(src)
    dst = Path(dst)
    # Create parent directories
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        _copy_tree(src, dst, exclude, delete)
        return
    if dst.is_dir():
        dst = dst / src.name
    _copy_entry(src, dst)


def copy_files(src_dir: Path, names: list[str], dst_dir: Path) -> None:
    """Copy direct child files of src_dir named in names into dst_dir."""
    if not names:
        return

    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        _copy_entry(src_dir / name, dst_dir / name)


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
