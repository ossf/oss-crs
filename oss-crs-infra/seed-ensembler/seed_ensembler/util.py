"""Utility functions for the seed ensembler."""

from __future__ import annotations

import logging
import re
import subprocess
import traceback
import zlib
from base64 import b64encode
from pathlib import Path

log = logging.getLogger(__name__)

LIBFUZZER_SECTION_REGEX = re.compile(r"^\s*\[\s*libfuzzer\s*\]", re.IGNORECASE)
OTHER_SECTION_REGEX = re.compile(r"^\s*\[")
TIMEOUT_EXITCODE_REGEX = re.compile(
    r"^\s*timeout_exitcode\s*=\s*0", re.IGNORECASE
)


def check_if_timeouts_scorable_in_options_file(
    options_file_path: Path,
) -> bool:
    """Check whether timeout-triggering seeds are scorable for a harness.

    Reads the harness ``.options`` file and returns ``False`` if the
    ``[libfuzzer]`` section contains ``timeout_exitcode=0``, which tells
    libfuzzer to exit cleanly on timeout rather than crashing.

    Returns ``True`` (timeouts are scorable) when the file is missing,
    unreadable, or does not contain the opt-out directive.
    """
    try:
        if not options_file_path.is_file():
            return True

        in_libfuzzer_section = False
        for line in options_file_path.open("r", encoding="utf-8"):
            if LIBFUZZER_SECTION_REGEX.match(line):
                in_libfuzzer_section = True
                continue
            elif in_libfuzzer_section and OTHER_SECTION_REGEX.match(line):
                break

            if in_libfuzzer_section and TIMEOUT_EXITCODE_REGEX.match(line):
                return False

        return True

    except Exception:
        log.warning(
            "Error reading %s: %s",
            options_file_path,
            traceback.format_exc(),
        )
        return True


def fs_copy(src: Path, dst: Path) -> None:
    """Copy a file or directory tree using rsync."""
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        src_str = f"{src}/."
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_str = str(src)
    else:
        raise FileNotFoundError(f"{src} does not exist")

    subprocess.run(["rsync", "-a", src_str, str(dst)], check=True)


def compress_str(s: bytes | bytearray | str) -> str:
    """Compress and base64-encode a string for compact logging."""
    if isinstance(s, str):
        s = s.encode("utf-8")
    # Use zlib.compressobj for raw deflate (no header).
    # zlib.compress(wbits=) is Python 3.11+; compressobj works on 3.10.
    co = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    s = co.compress(s) + co.flush()
    return b64encode(s).decode("ascii")
