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
