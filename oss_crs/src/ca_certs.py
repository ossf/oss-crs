# SPDX-License-Identifier: MIT
"""Support for internal/corporate CA bundles.

Self-hosted LLM endpoints are commonly fronted by a certificate chaining to an
internal CA. The chain is valid; it is simply absent from the trust store of the
containers OSS-CRS starts. This module resolves a user-supplied PEM file and
materializes it, plus the env vars that point the usual clients at it, so those
chains verify. There is deliberately no way here to skip verification.
"""

import os
import ssl
from pathlib import Path
from typing import Optional

import certifi

from .ui import TaskResult

EXTRA_CA_CERTS_ENV = "OSS_CRS_EXTRA_CA_CERTS"

# Where the generated directory is mounted inside containers.
CA_DIR_CONTAINER = "/etc/oss-crs/ca"
# Full trust store: public roots plus the user's CA(s). Consumers that *replace*
# the trust store point here.
CA_BUNDLE_NAME = "bundle.pem"
# The user's CA(s) alone. Consumers that *append* to their own trust store point
# here.
CA_EXTRA_NAME = "extra.pem"

PEM_MARKER = "-----BEGIN CERTIFICATE-----"


def resolve_extra_ca_certs(
    cli_value: Optional[Path] = None,
    compose_value: Optional[str] = None,
) -> Optional[Path]:
    """Resolve the extra CA bundle path.

    Precedence: explicit CLI flag, then the compose file, then the environment.
    """
    if cli_value is not None:
        return _expand(str(cli_value))
    if compose_value:
        return _expand(compose_value)
    env_value = os.environ.get(EXTRA_CA_CERTS_ENV)
    if env_value:
        return _expand(env_value)
    return None


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def validate_extra_ca_certs(path: Path) -> TaskResult:
    """Validate that *path* is a readable PEM file holding at least one cert."""
    if not path.exists():
        return TaskResult(
            success=False,
            error=f"Extra CA bundle does not exist: '{path}'",
        )
    if not path.is_file():
        return TaskResult(
            success=False,
            error=f"Extra CA bundle is not a file: '{path}'",
        )
    try:
        content = path.read_text(errors="replace")
    except OSError as e:
        return TaskResult(
            success=False,
            error=f"Extra CA bundle could not be read: '{path}' ({e})",
        )
    if PEM_MARKER not in content:
        return TaskResult(
            success=False,
            error=(
                f"Extra CA bundle contains no certificate: '{path}'. "
                f"Expected PEM format (a '{PEM_MARKER}' block). "
                "Convert a DER/CRT file with: "
                f"openssl x509 -inform der -in {path.name} -out ca.pem"
            ),
        )
    # Load it for real: a file can carry the PEM marker and still be rejected by
    # OpenSSL (truncated, corrupted base64, DER with a PEM header). Catching that
    # here beats an opaque TLS failure inside a container an hour into a run.
    try:
        ssl.create_default_context().load_verify_locations(cafile=str(path))
    except ssl.SSLError as e:
        return TaskResult(
            success=False,
            error=(
                f"Extra CA bundle is not a valid PEM certificate file: '{path}' ({e}). "
                f"Check it with: openssl x509 -in {path.name} -noout -subject"
            ),
        )
    return TaskResult(success=True)


def write_ca_dir(tmp_dir: Path, src: Path) -> str:
    """Materialize the CA directory for mounting and return its host path.

    Writes two files: ``extra.pem`` (the user's CA(s) verbatim) and
    ``bundle.pem`` (certifi's public roots with the user's CA(s) appended).

    ``bundle.pem`` exists only because Python offers no additive CA env var:
    ``SSL_CERT_FILE`` replaces the trust store rather than extending it, so
    handing it the internal CA alone would break public endpoints.
    """
    extra_pem = src.read_bytes()
    ca_dir = tmp_dir / "ca"
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_dir.chmod(0o755)

    extra_path = ca_dir / CA_EXTRA_NAME
    extra_path.write_bytes(extra_pem)
    extra_path.chmod(0o644)

    public_roots = Path(certifi.where()).read_bytes()
    if not public_roots.endswith(b"\n"):
        public_roots += b"\n"
    bundle_path = ca_dir / CA_BUNDLE_NAME
    bundle_path.write_bytes(public_roots + extra_pem)
    bundle_path.chmod(0o644)

    return str(ca_dir)


def ca_env(ca_dir: str = CA_DIR_CONTAINER) -> dict[str, str]:
    """Env vars pointing the usual TLS clients at the mounted CA files.

    ``NODE_EXTRA_CA_CERTS`` gets ``extra.pem`` rather than the merged bundle:
    Node appends it to its built-in roots, and discards the whole file if any
    block in it fails to parse.
    """
    bundle = f"{ca_dir}/{CA_BUNDLE_NAME}"
    return {
        # Python ssl/urllib, httpx, LiteLLM.
        "SSL_CERT_FILE": bundle,
        # requests does not consult SSL_CERT_FILE.
        "REQUESTS_CA_BUNDLE": bundle,
        # curl/libcurl, and requests' fallback.
        "CURL_CA_BUNDLE": bundle,
        "NODE_EXTRA_CA_CERTS": f"{ca_dir}/{CA_EXTRA_NAME}",
    }


def ssl_context(extra_ca_certs: Optional[Path]) -> Optional[ssl.SSLContext]:
    """A default SSL context with *extra_ca_certs* added, for host-side requests.

    ``load_verify_locations`` is additive on a default context, so no merged
    bundle is needed here. Returns None when no extra CA is configured, letting
    callers fall back to the default context.
    """
    if extra_ca_certs is None:
        return None
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(extra_ca_certs))
    return context
