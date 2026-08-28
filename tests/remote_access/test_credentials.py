"""Daemon-credential-provider seam (contract §6.1 step 8, §7 local_daemon_bearer).

The bearer is read only at the final hop; missing/unreadable/loose-permission
tokens fail closed with the normative local_daemon categories.
"""
from __future__ import annotations

import os
import stat

import pytest

from runtime.remote_access.credentials import (
    CredentialUnavailable,
    FileDaemonCredentialProvider,
    StaticDaemonCredentialProvider,
    SystemdCredentialProvider,
)


def test_static_provider_returns_bearer() -> None:
    provider = StaticDaemonCredentialProvider("abc")
    assert provider.read_bearer() == "abc"


def test_file_provider_reads_token(tmp_path) -> None:
    token_path = tmp_path / "daemon.token"
    token_path.write_text("token-123\n")
    token_path.chmod(0o600)
    provider = FileDaemonCredentialProvider(token_path)
    assert provider.read_bearer() == "token-123"


def test_file_provider_missing_file_fails_closed(tmp_path) -> None:
    provider = FileDaemonCredentialProvider(tmp_path / "nope.token")
    with pytest.raises(CredentialUnavailable):
        provider.read_bearer()


def test_file_provider_empty_token_fails_closed(tmp_path) -> None:
    token_path = tmp_path / "daemon.token"
    token_path.write_text("   \n")
    token_path.chmod(0o600)
    provider = FileDaemonCredentialProvider(token_path)
    with pytest.raises(CredentialUnavailable):
        provider.read_bearer()


def test_file_provider_loose_permissions_fail_closed(tmp_path) -> None:
    token_path = tmp_path / "daemon.token"
    token_path.write_text("token-123")
    token_path.chmod(0o644)  # world-readable — fail closed
    provider = FileDaemonCredentialProvider(token_path)
    with pytest.raises(CredentialUnavailable):
        provider.read_bearer()


def test_file_provider_unreadable_file_fails_closed(tmp_path) -> None:
    token_path = tmp_path / "daemon.token"
    token_path.write_text("token-123")
    token_path.chmod(0o600)
    os.chmod(token_path, 0o000)
    provider = FileDaemonCredentialProvider(token_path)
    try:
        if os.access(token_path, os.R_OK):
            pytest.skip("running as root; permission gate not enforceable")
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()
    finally:
        os.chmod(token_path, 0o600)


def test_file_provider_mode_checks_owner_only_bits(tmp_path) -> None:
    # 0o640 (group-readable) must also fail closed.
    p = tmp_path / "daemon.token"
    p.write_text("t")
    p.chmod(0o640)
    with pytest.raises(CredentialUnavailable):
        FileDaemonCredentialProvider(p).read_bearer()


class TestSystemdCredentialProvider:
    """The systemd ``LoadCredential=`` provider (least privilege: the service
    user never needs direct access to the daemon home; systemd copies the
    credential into a 0600 runtime dir at unit start)."""

    def test_reads_credential_directory_token(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        token = cred_dir / "daemon.token"
        token.write_text("systemd-token-99")
        token.chmod(0o600)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        provider = SystemdCredentialProvider()
        assert provider.read_bearer() == "systemd-token-99"

    def test_explicit_directory_wins_over_env(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        token = cred_dir / "daemon.token"
        token.write_text("explicit-token")
        token.chmod(0o600)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path / "unused"))
        provider = SystemdCredentialProvider(credentials_directory=cred_dir)
        assert provider.read_bearer() == "explicit-token"

    def test_missing_credentials_directory_fails_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        provider = SystemdCredentialProvider()
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()

    def test_missing_token_file_fails_closed(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        provider = SystemdCredentialProvider()
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()

    def test_empty_token_fails_closed(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        token = cred_dir / "daemon.token"
        token.write_text("   \n")
        token.chmod(0o600)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        provider = SystemdCredentialProvider()
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()

    def test_loose_permissions_fail_closed(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        token = cred_dir / "daemon.token"
        token.write_text("token-123")
        token.chmod(0o644)  # world-readable — systemd never does this
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        provider = SystemdCredentialProvider()
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()

    def test_symlinked_token_fails_closed(self, tmp_path, monkeypatch) -> None:
        cred_dir = tmp_path / "credentials"
        cred_dir.mkdir(mode=0o700)
        real = tmp_path / "elsewhere.token"
        real.write_text("token-123")
        real.chmod(0o600)
        token = cred_dir / "daemon.token"
        token.symlink_to(real)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        provider = SystemdCredentialProvider()
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()
