"""Home-only issuance core. Protected helper wiring and local minting are separate.

The caller supplies one protected directory and pairing owner per home. A minter
must bound its own execution, mint a non-reusable key, and associate its effects
with the operation ID. Recovery confirms absence/revocation, never mints a key.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Protocol

from . import invite_v2
from .home_invite_records import (
    ACTIVATION_PATH, BINDING_KEYS, INVITE_CONFIG_PATH, HomeActivationError, HomeActivationReceipt,
    _generation, _OUTCOMES, IssuerError as IssuerError, HomeInviteConfigRecord as HomeInviteConfigRecord,
)


class MintRefused(Exception):
    """Adapter guarantees this operation created no key. Arguments are private."""


@dataclass(frozen=True, repr=False)
class MintedKey:
    key: str = field(repr=False)
    expires_at: datetime

    def __repr__(self) -> str:
        return "MintedKey(<redacted>)"


class InvitePairing(Protocol):
    def issue_invite_record(self, digest: str, expires_at: datetime) -> int: ...


class JoinKeyMinter(Protocol):
    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        """At most one mint attempt; all other exceptions mean uncertain."""
        ...

    def recover(self, operation_id: str) -> bool:
        """Explicit reconciliation: True only after no usable key can remain."""
        ...


@dataclass(frozen=True, repr=False)
class HomeInviteConfig:
    home_id: str
    control_plane_id: str
    connector_id: str
    ca_der: bytes
    dial_ip: str
    home_endpoint: str
    mode: str = "home"

    def __repr__(self) -> str:
        return "HomeInviteConfig(<redacted>)"


def read_home_invite_config(*, expected_generation: int, root: Path = Path("/"),
                            root_owner: tuple[int, int] = (0, 0)) -> HomeInviteConfigRecord:
    """Read only: require one ready, current-generation, digest-matching B1 pair.

    No CA/key loading, journal access, minter, endpoint discovery or issuer call.
    The coordinator supplies current generation; matching stale files cannot
    declare themselves current. Both parents/leaves stay pinned across the read.
    """
    from .home_activation import HomeActivationStore
    try:
        _generation(expected_generation)
        store = HomeActivationStore(root=root, root_owner=root_owner)
        with store._record(INVITE_CONFIG_PATH) as data:
            config = HomeInviteConfigRecord.parse(data)
            with store._record(ACTIVATION_PATH) as receipt_data:
                receipt = HomeActivationReceipt.parse(receipt_data)
                if (receipt.phase != "ready"
                        or receipt.activation_generation != expected_generation
                        or any(getattr(config, key) != getattr(receipt, key) for key in BINDING_KEYS)
                        or config.home_endpoint != receipt.home_endpoint
                        or hashlib.sha256(data).hexdigest() != receipt.member_digests[INVITE_CONFIG_PATH]):
                    raise HomeActivationError("binding")
        return config
    except HomeActivationError:
        raise IssuerError("configuration") from None


@dataclass(frozen=True, repr=False)
class IssuedInvite:
    operation_id: str
    invite_text: str = field(repr=False)
    expires_at: int

    def __repr__(self) -> str:
        return "IssuedInvite(<redacted>)"


@dataclass(frozen=True)
class InviteObservation:
    phase: str
    expires_at: int | None


def _metadata(fd: int, parent: int, name: str) -> None:
    opened = os.fstat(fd)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise IssuerError("state")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    # Never create/repair an injected tree or follow any literal path component.
    if not path.is_absolute() or ".." in path.parts:
        raise IssuerError("configuration")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise IssuerError("state")
        yield fd
    finally:
        os.close(fd)


def _pairs(items: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise IssuerError("state")
        result[key] = value
    return result


class HomeInviteIssuer:
    """No auth/IPC surface: construct only with protected helper dependencies.

    issue() allocates an operation. issue(operation_id) is a secret-free retry
    observation and NEVER mints or redelivers. An unfinished attempt stays
    uncertain across restart and expiry until explicit recover(operation_id).
    """

    def __init__(self, *, config: HomeInviteConfig, minter: JoinKeyMinter,
                 pairing: InvitePairing, state_dir: Path,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        self._config, self._minter, self._pairing = config, minter, pairing
        self._state_dir = state_dir
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return "HomeInviteIssuer(<redacted>)"

    def _now(self) -> float:
        now = self._now_fn()
        if type(now) is not datetime or now.tzinfo is None:
            raise IssuerError("configuration")
        stamp = now.timestamp()
        if not math.isfinite(stamp) or not 0 <= stamp < 253402300000:
            raise IssuerError("configuration")
        return stamp

    def _binding(self) -> str:
        config = self._config
        if type(config) is not HomeInviteConfig or config.mode != "home":
            raise IssuerError("configuration")
        # Exercise exactly the codec's existing trust/schema rules before mint.
        invite_v2.encode(home_id=config.home_id, control_plane_id=config.control_plane_id,
                         connector_id=config.connector_id, ca_der=config.ca_der,
                         dial_ip=config.dial_ip, home_endpoint=config.home_endpoint,
                         join_key="0" * 48, issued_at=0, expires_at=1)
        values = [config.home_id, config.control_plane_id, config.connector_id,
                  hashlib.sha256(config.ca_der).hexdigest(), config.dial_ip, config.home_endpoint]
        return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()

    def _read(self, parent: int, binding: str) -> dict | None:
        try:
            fd = os.open("invite-issuer.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            _metadata(stream.fileno(), parent, "invite-issuer.json")
            raw = stream.read(4097)
            _metadata(stream.fileno(), parent, "invite-issuer.json")
        if len(raw) > 4096:
            raise IssuerError("state")
        record = json.loads(raw, object_pairs_hook=_pairs)
        if (type(record) is not dict
                or record.keys() != {"operation_id", "binding_digest", "not_before", "expires_at", "outcome"}
                or record["binding_digest"] != binding
                or type(record["operation_id"]) is not str
                or re.fullmatch("[0-9a-f]{32}", record["operation_id"]) is None
                or type(record["outcome"]) is not str or record["outcome"] not in _OUTCOMES
                or any(type(record[k]) not in (int, float) or not math.isfinite(record[k])
                       or not 0 <= record[k] < 253402300300 for k in ("not_before", "expires_at"))):
            raise IssuerError("state")
        return record

    def _write(self, parent: int, record: dict) -> None:
        # A fixed exclusive pending name is deliberate: crash residue refuses.
        fd = os.open("invite-issuer.pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                _metadata(stream.fileno(), parent, "invite-issuer.pending")
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
                stream.flush()
                os.fsync(stream.fileno())
                _metadata(stream.fileno(), parent, "invite-issuer.pending")
            os.replace("invite-issuer.pending", "invite-issuer.json", src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            # Only our exclusive pending inode exists on this path in the
            # protected directory; an earlier crash's residue is never removed.
            try:
                os.unlink("invite-issuer.pending", dir_fd=parent)
            except FileNotFoundError:
                pass

    def _invalidate(self) -> None:
        # No secret exists for this random digest: it cannot be redeemed.
        self._pairing.issue_invite_record(secrets.token_hex(32),
                                         datetime.fromtimestamp(self._now() + 299, timezone.utc))

    @contextmanager
    def _recovery_record(self) -> Iterator[tuple[int, dict | None]]:
        """B11-only protected current selection; release before any IPC.

        The helper mutation slot is already held by the caller. Reuse the
        issuer's custody, binding and nonblocking lock without accepting IDs.
        """
        binding = self._binding()
        with _directory(self._state_dir) as parent:
            fd = os.open("invite-issuer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, "rb") as lock:
                _metadata(lock.fileno(), parent, "invite-issuer.lock")
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise IssuerError("busy") from None
                try:
                    os.stat("invite-issuer.pending", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise IssuerError("state")
                yield parent, self._read(parent, binding)

    def _operate(self, operation_id: str | None, recovery: bool) -> IssuedInvite | None:
        binding = self._binding()
        with _directory(self._state_dir) as parent:
            fd = os.open("invite-issuer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, "rb") as lock:
                _metadata(lock.fileno(), parent, "invite-issuer.lock")
                busy = False
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    busy = True
                if busy:
                    raise IssuerError("busy")
                record = self._read(parent, binding)
                if operation_id is not None:
                    if type(operation_id) is not str or record is None or operation_id != record["operation_id"]:
                        raise IssuerError("superseded")
                    if record["outcome"] in ("minting", "uncertain"):
                        if recovery:
                            self._invalidate()
                            cleared = False
                            try:
                                cleared = self._minter.recover(operation_id)
                            except BaseException:
                                pass
                            if cleared is True:
                                record["outcome"] = "recovered"
                                self._write(parent, record)
                                return None
                        raise IssuerError("uncertain")
                    raise IssuerError("already_issued" if record["outcome"] == "issued" else record["outcome"])
                if recovery:
                    raise IssuerError("superseded")
                if record is not None and record["outcome"] in ("minting", "uncertain"):
                    raise IssuerError("uncertain")
                now = self._now()
                if record is not None and now < record["not_before"]:
                    raise IssuerError("rate_limited")
                issued_at = math.floor(now)
                record = dict(operation_id=secrets.token_hex(16), binding_digest=binding,
                              not_before=now + 60, expires_at=issued_at + 300, outcome="minting")
                self._write(parent, record)  # FILE + DIRECTORY FSYNC BEFORE MINT
                self._invalidate()  # invalidate even if the new mint refuses/crashes
                refused = False
                uncertain = False
                try:
                    minted = self._minter.mint(record["operation_id"],
                                               datetime.fromtimestamp(record["expires_at"], timezone.utc))
                except MintRefused:
                    refused = True
                except BaseException:
                    uncertain = True
                if uncertain:
                    raise IssuerError("uncertain")
                if refused:
                    record["outcome"] = "refused"
                    self._write(parent, record)
                    raise IssuerError("refused")
                # Any malformed/exceptional outcome remains durably 'minting'.
                if type(minted) is not MintedKey or type(minted.expires_at) is not datetime or minted.expires_at.tzinfo is None:
                    raise IssuerError("uncertain")
                expires = math.floor(min(issued_at + 300, minted.expires_at.timestamp()))
                record["expires_at"] = expires
                if self._now() < now or self._now() >= expires or expires <= issued_at:
                    record["outcome"] = "expired"
                    self._write(parent, record)
                    raise IssuerError("expired")
                config = self._config
                encoded = invite_v2.encode(home_id=config.home_id, control_plane_id=config.control_plane_id,
                                           connector_id=config.connector_id, ca_der=config.ca_der,
                                           dial_ip=config.dial_ip, home_endpoint=config.home_endpoint,
                                           join_key=minted.key, issued_at=issued_at, expires_at=expires)
                self._pairing.issue_invite_record(encoded.pair_secret_digest,
                                                  datetime.fromtimestamp(expires, timezone.utc))
                record["outcome"] = "issued"
                self._write(parent, record)  # consume delivery before exposing text
                finished = self._now()
                if finished < now or finished >= expires:
                    record["outcome"] = "expired"
                    self._write(parent, record)
                    raise IssuerError("expired")
                return IssuedInvite(record["operation_id"], encoded.invite_text, expires)

    def _boundary(self, operation_id: str | None, recovery: bool) -> IssuedInvite | None:
        # Leave all handlers before raising. Never attach an adapter/library
        # error (even __context__), arguments or traceback to the public error.
        code = "uncertain"
        try:
            return self._operate(operation_id, recovery)
        except IssuerError as error:
            code = error.args[0]
        except invite_v2.InviteError:
            code = "encoding"
        except (OSError, ValueError, TypeError, OverflowError):
            code = "state"
        except BaseException:
            pass
        del self, operation_id
        raise IssuerError(code)

    def issue(self, operation_id: str | None = None) -> IssuedInvite:
        result = self._boundary(operation_id, False)
        assert result is not None
        return result

    def recover(self, operation_id: str) -> None:
        """Explicit helper recovery; adapter must prove cleanup, with no mint."""
        self._boundary(operation_id, True)

    def observe(self) -> InviteObservation:
        """Read the protected journal without locks, effects or private fields."""
        code = "uncertain"
        try:
            binding = self._binding()
            with _directory(self._state_dir) as parent:
                record = self._read(parent, binding)
            if record is None:
                return InviteObservation("none", None)
            phase = record["outcome"]
            if phase == "issued":
                expires_at = math.floor(record["expires_at"])
                if self._now() >= expires_at:
                    return InviteObservation("expired", None)
                return InviteObservation("issued", expires_at)
            return InviteObservation(phase, None)
        except IssuerError as error:
            code = error.args[0]
        except invite_v2.InviteError:
            code = "encoding"
        except (OSError, ValueError, TypeError, OverflowError):
            code = "state"
        except BaseException:
            pass
        # Match _boundary: raise only after leaving every library handler.
        del self
        raise IssuerError(code)
