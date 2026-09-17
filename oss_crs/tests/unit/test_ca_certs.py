# SPDX-License-Identifier: MIT
import ssl
import stat
from pathlib import Path

import certifi

from oss_crs.src.ca_certs import (
    CA_BUNDLE_NAME,
    CA_DIR_CONTAINER,
    CA_EXTRA_NAME,
    EXTRA_CA_CERTS_ENV,
    ca_env,
    resolve_extra_ca_certs,
    ssl_context,
    validate_extra_ca_certs,
    write_ca_dir,
)

# A throwaway self-signed root, standing in for an internal corporate CA. It must
# be a real certificate: the helpers load it through OpenSSL, which rejects a
# PEM-shaped file that is not valid DER.
ORG_PEM = """-----BEGIN CERTIFICATE-----
MIIDITCCAgmgAwIBAgIUFIm2taiQM0C9/rotoz86lGwsnCEwDQYJKoZIhvcNAQEL
BQAwHzEdMBsGA1UEAwwUT1NTLUNSUyBUZXN0IFJvb3QgQ0EwIBcNMjYwOTA4MTgy
OTA1WhgPMjEyNjA4MTUxODI5MDVaMB8xHTAbBgNVBAMMFE9TUy1DUlMgVGVzdCBS
b290IENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2ukyNzZ7Q+jR
7lgZOLaG8QBuNDoDzpvZsqP3n9kpde2XYSgUPL48LaJvFPPi6W2Kjzmw8627NeiB
TgNGLvIazFwV9cB5ewaZ5R3lsMBsGBNvQl/u33lQEJUjED/1aWTjg1Y4B0Ap+ePL
FEbJ1f8XvapZNh0xfrGjbb1VXMM11YPc4Ch1s9iJafAZyPp4THLV/TCci8K30hX/
+ABSba9ylu4BMSuANg32Our2vIV+uCVB6NT2TPUIoZSy8yXv5W/8GSJkA5CvwKDN
7ZnzlNnCmbyH9fTwhgnLbuaAM8ghJrRGkY3eSMdAoAGITOzNVnRPX6Hmad9kjW9u
FVs+7QClXwIDAQABo1MwUTAdBgNVHQ4EFgQU9IOeUvM0OzmFwnqj2KQtR24lbEEw
HwYDVR0jBBgwFoAU9IOeUvM0OzmFwnqj2KQtR24lbEEwDwYDVR0TAQH/BAUwAwEB
/zANBgkqhkiG9w0BAQsFAAOCAQEARNlypYVCjZoSYR3p5bXMYOs0G1zwHG5i3qOb
xEGlV5R/USJvKmhp8iTjOr+wC5+GyE9/XmDuvTVd2Ulz0t9e2KDQ91N9/+7jEnE4
xCKpYUTuqA15bJnV35AI2AAqWM6geEHsBXsVUZZ7nOW2N59yfcpSxg9lMA075FlH
oGlHWSnIrEY9rCnsvgLkAjdbwlUAEfJCfwJgrg3hBw8XLMEqAPoeFYr99tAWC3Sm
wjO6cXjGzKOlC/D9Ggyd1aNcA4FAc0fU/3mcAorezAF8/DhdL5tbiIpLR4RlmW7h
KoGue9uNO0OkjB6OAUIAWFaIpZCDRiyVq7wemyJ8pL7/SnW3FA==
-----END CERTIFICATE-----
"""


def _write_pem(tmp_path: Path, name: str = "corp.pem") -> Path:
    path = tmp_path / name
    path.write_text(ORG_PEM)
    return path


class TestResolveExtraCACerts:
    def test_cli_wins_over_compose_and_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(EXTRA_CA_CERTS_ENV, str(tmp_path / "env.pem"))
        resolved = resolve_extra_ca_certs(
            cli_value=tmp_path / "cli.pem",
            compose_value=str(tmp_path / "compose.pem"),
        )
        assert resolved == tmp_path / "cli.pem"

    def test_compose_wins_over_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(EXTRA_CA_CERTS_ENV, str(tmp_path / "env.pem"))
        resolved = resolve_extra_ca_certs(
            cli_value=None, compose_value=str(tmp_path / "compose.pem")
        )
        assert resolved == tmp_path / "compose.pem"

    def test_env_used_as_fallback(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(EXTRA_CA_CERTS_ENV, str(tmp_path / "env.pem"))
        assert resolve_extra_ca_certs() == tmp_path / "env.pem"

    def test_none_when_nothing_configured(self, monkeypatch) -> None:
        monkeypatch.delenv(EXTRA_CA_CERTS_ENV, raising=False)
        assert resolve_extra_ca_certs() is None

    def test_empty_compose_value_falls_through_to_env(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(EXTRA_CA_CERTS_ENV, str(tmp_path / "env.pem"))
        assert resolve_extra_ca_certs(compose_value="") == tmp_path / "env.pem"

    def test_expands_env_vars_and_user(self, tmp_path, monkeypatch) -> None:
        """A checked-in compose can defer to the environment via ${VAR}."""
        monkeypatch.setenv("CORP_CA_DIR", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_extra_ca_certs(compose_value="${CORP_CA_DIR}/corp.pem") == (
            tmp_path / "corp.pem"
        )
        assert resolve_extra_ca_certs(compose_value="~/corp.pem") == (
            tmp_path / "corp.pem"
        )


class TestValidateExtraCACerts:
    def test_accepts_pem_file(self, tmp_path) -> None:
        assert validate_extra_ca_certs(_write_pem(tmp_path)).success is True

    def test_rejects_missing_path(self, tmp_path) -> None:
        result = validate_extra_ca_certs(tmp_path / "nope.pem")
        assert result.success is False
        assert "does not exist" in (result.error or "")

    def test_rejects_directory(self, tmp_path) -> None:
        result = validate_extra_ca_certs(tmp_path)
        assert result.success is False
        assert "not a file" in (result.error or "")

    def test_rejects_non_pem_file(self, tmp_path) -> None:
        der = tmp_path / "corp.crt"
        der.write_bytes(b"\x30\x82\x01\x0a not pem")
        result = validate_extra_ca_certs(der)
        assert result.success is False
        assert "no certificate" in (result.error or "")
        # The error should tell the user how to convert, not how to skip verification.
        assert "openssl x509" in (result.error or "")

    def test_rejects_pem_shaped_file_openssl_cannot_load(self, tmp_path) -> None:
        """The marker alone is not enough; a corrupt body must be caught here."""
        broken = tmp_path / "broken.pem"
        broken.write_text(
            "-----BEGIN CERTIFICATE-----\nnot-base64!!\n-----END CERTIFICATE-----\n"
        )
        result = validate_extra_ca_certs(broken)
        assert result.success is False
        assert "not a valid PEM certificate file" in (result.error or "")

    def test_accepts_bundle_of_several_certs(self, tmp_path) -> None:
        """Root plus intermediates concatenated into one file."""
        chain = tmp_path / "chain.pem"
        chain.write_text(ORG_PEM * 2)
        assert validate_extra_ca_certs(chain).success is True


class TestWriteCADir:
    def test_writes_both_pems_world_readable(self, tmp_path) -> None:
        src = _write_pem(tmp_path)
        ca_dir = Path(write_ca_dir(tmp_path / "compose", src))

        extra = ca_dir / CA_EXTRA_NAME
        bundle = ca_dir / CA_BUNDLE_NAME
        assert extra.read_text() == ORG_PEM
        for path in (extra, bundle):
            assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert stat.S_IMODE(ca_dir.stat().st_mode) == 0o755

    def test_creates_missing_parent(self, tmp_path) -> None:
        """The compose tmp dir may not exist yet when rendering."""
        src = _write_pem(tmp_path)
        ca_dir = Path(write_ca_dir(tmp_path / "absent" / "nested", src))
        assert (ca_dir / CA_BUNDLE_NAME).exists()

    def test_bundle_appends_org_cert_to_public_roots(self, tmp_path) -> None:
        src = _write_pem(tmp_path)
        ca_dir = Path(write_ca_dir(tmp_path / "compose", src))
        bundle = (ca_dir / CA_BUNDLE_NAME).read_text()

        # Public roots survive, so api.anthropic.com keeps verifying.
        assert Path(certifi.where()).read_text().rstrip("\n") in bundle
        # The org cert is present exactly once.
        assert bundle.count(ORG_PEM.strip()) == 1

    def test_bundle_is_a_usable_trust_store(self, tmp_path) -> None:
        """The merged bundle must actually load as a CA file."""
        src = _write_pem(tmp_path)
        ca_dir = Path(write_ca_dir(tmp_path / "compose", src))
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(ca_dir / CA_BUNDLE_NAME))
        assert context.cert_store_stats()["x509_ca"] > 0

    def test_handles_source_without_trailing_newline(self, tmp_path) -> None:
        src = tmp_path / "corp.pem"
        src.write_text(ORG_PEM.rstrip("\n"))
        ca_dir = Path(write_ca_dir(tmp_path / "compose", src))
        bundle = (ca_dir / CA_BUNDLE_NAME).read_text()
        assert "-----END CERTIFICATE-----" in bundle
        # No PEM block may be glued to the previous one.
        assert "-----BEGIN CERTIFICATE----------END" not in bundle


class TestCAEnv:
    def test_node_gets_extra_pem_and_others_get_bundle(self) -> None:
        env = ca_env()
        bundle = f"{CA_DIR_CONTAINER}/{CA_BUNDLE_NAME}"
        assert env["SSL_CERT_FILE"] == bundle
        assert env["REQUESTS_CA_BUNDLE"] == bundle
        assert env["CURL_CA_BUNDLE"] == bundle
        # Node appends this to its built-in roots and drops the whole file on a
        # single parse failure, so it must not receive the merged bundle.
        assert env["NODE_EXTRA_CA_CERTS"] == f"{CA_DIR_CONTAINER}/{CA_EXTRA_NAME}"

    def test_sets_no_ssl_cert_dir(self) -> None:
        """C/OpenSSL consumers keep the container's own hashed cert dir."""
        assert "SSL_CERT_DIR" not in ca_env()

    def test_honors_custom_dir(self) -> None:
        env = ca_env("/somewhere/else")
        assert env["SSL_CERT_FILE"] == f"/somewhere/else/{CA_BUNDLE_NAME}"


class TestSSLContext:
    def test_none_without_extra_ca(self) -> None:
        assert ssl_context(None) is None

    def test_adds_to_default_roots_rather_than_replacing(self, tmp_path) -> None:
        """load_verify_locations is additive, so public roots must survive."""
        default_cas = ssl.create_default_context().cert_store_stats()["x509_ca"]
        context = ssl_context(_write_pem(tmp_path))
        assert context is not None
        assert context.cert_store_stats()["x509_ca"] >= default_cas
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
