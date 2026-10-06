# SPDX-License-Identifier: MIT
"""Resolving OSS-CRS's own options from the host it runs on.

An option may be set by a CLI flag, by a compose-file key, or by an ``OSS_CRS_*``
variable in the environment ``oss-crs`` is invoked from, in that order of
precedence. These helpers read only the host process environment; they are
unrelated to the env vars OSS-CRS passes into CRS containers.
"""

import os
from pathlib import Path
from typing import Optional


def resolve_option(
    env_var: str,
    *,
    cli: Optional[str | Path] = None,
    compose: Optional[str] = None,
) -> Optional[str]:
    """Return the first configured value: *cli*, then *compose*, then ``$env_var``.

    A CLI value counts whenever given. An empty compose value or env var counts as
    unset, so ``extra_ca_certs: ${UNSET_VAR}`` falls through to the environment.
    """
    if cli is not None:
        return str(cli)
    if compose:
        return compose
    return os.environ.get(env_var) or None


def expand_path(value: str) -> Path:
    """Expand ``$VAR``/``${VAR}`` and ``~`` in a user-supplied path."""
    return Path(os.path.expandvars(value)).expanduser()
