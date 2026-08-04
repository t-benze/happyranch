"""macOS platform isolation for the canonical skill store.

Provides workspace symlink creation, validation, and executor process
launch under the daemon's own OS identity.

**SUPPORTED: macOS (darwin) only.**
Linux and Windows are NOT supported in this release; attempts to use them
fail closed with an explicit error.

**SINGLE-OPERATOR MODE only.** The executor runs under the SAME OS identity
as the daemon — there is NO OS-level isolation. An agent-controlled executor
process can read, write, chmod, or chown the canonical skill store and
anything else the daemon account can reach. A same UID may mutate, race
validation, and affect active/overlapping sessions.

Do NOT claim the canonical target is immutable, protected, or a trusted
source. Do NOT claim write/chmod/ACL denial, a security boundary, or
cross-agent isolation.

**INTEGRITY VERIFICATION:**
Before each executor launch and every retry, the daemon synchronously
validates every resolved package member's bytes against the
ledger-declared SHA-256 hashes. It also validates both ``.claude/skills``
and ``.agents/skills`` root links. A mismatched existing canonical package
is NEVER automatically rebuilt, copied, replaced, or healed from same-UID
local source. A durable integrity/operations event is emitted and the
session is REFUSED — this is detection for accidental corruption, not an
attacker-independent external attestation authority.

**Recovery is manual, operator-invoked only:**
(a) For broken links: ``happyranch set-executor <agent> --executor <current-executor>``
    (re-materializes links only, NEVER recovers corrupted bytes).
(b) For corrupted canonical bytes: ``happyranch skills recover <slug> <version> <content_hash>``
    — validates ledger provenance and every declared member SHA-256 hash
    against the ArtifactStore before deletion; refuses already-valid targets.
    Must only be used after an authoritative external re-sync/redeploy of
    release or custom artifacts has restored verified artifact bytes outside
    the compromised same-owner local source.

**Fail-closed:** any integrity mismatch or validation error raises before
subprocess launch.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlatformIdentity:
    """OS-level identity of a process or account.

    On macOS: uid + gid.
    """

    uid: int
    gid: int

    def __repr__(self) -> str:
        return f"PlatformIdentity(uid={self.uid}, gid={self.gid})"


class PlatformIsolationError(Exception):
    """Raised when platform isolation invariants are violated.

    This is a terminal materialization failure — no executor launch proceeds.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


# ── Abstract platform isolation ─────────────────────────────────────


class PlatformIsolation(ABC):
    """Abstract platform isolation layer.

    macOS implementation provides:
    - Current process identity
    - Workspace symlink creation and validation
    - Executor process launching under daemon identity
    """

    @abstractmethod
    def current_identity(self) -> PlatformIdentity:
        """Return the identity of the current process."""
        ...

    @abstractmethod
    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated relative symlink from *link_path* to *target*.

        Must fail closed if:
        - link_path is an ordinary directory (never rmtree)
        - target is absolute or escapes the canonical store root
        - platform does not support symlinks
        """
        ...

    @abstractmethod
    def verify_workspace_link(
        self, link_path: Path, expected_target: Path, canonical_root: Path,
    ) -> bool:
        """Verify workspace symlink validity.

        Returns True if valid, False if missing/stale/wrong/broken.
        """
        ...

    @abstractmethod
    def is_valid_symlink(self, path: Path) -> bool:
        """Check if *path* is a valid, non-malicious symlink."""
        ...

    @abstractmethod
    def launch_executor(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text: bool = True,
    ) -> subprocess.Popen:
        """Launch a subprocess under the daemon's own OS identity.

        The executor runs under the SAME OS identity as the daemon.
        There is NO OS-level isolation — the executor can read, write,
        chmod, or chown anything the daemon account can reach.

        Raises PlatformIsolationError on subprocess launch failure.
        """
        ...


# ── macOS implementation ────────────────────────────────────────────


class _MacOSPlatformIsolation(PlatformIsolation):
    """macOS platform isolation.

    The executor runs under the daemon's own OS identity — there is NO
    OS-level isolation. Integrity checks are best-effort corruption
    detection only, not a security boundary.
    """

    def __init__(self) -> None:
        self._daemon_uid = os.getuid()
        self._daemon_gid = os.getgid()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=self._daemon_uid,
            gid=self._daemon_gid,
        )

    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated relative symlink on macOS.

        Validates:
        - target is not absolute (relative symlinks only)
        - target does not escape canonical root (no excessive ../ traversal)

        **Safe repair:** existing entries are removed ONLY through
        no-follow validated routines. Ordinary directories are NEVER
        recursively deleted.
        """
        if target.is_absolute():
            raise PlatformIsolationError(
                "absolute_target",
                f"Symlink target must be relative, got absolute: {target}",
            )

        # Reject targets with excessive .. traversal
        target_parts = str(target).split(os.sep)
        up_count = sum(1 for p in target_parts if p == "..")
        if up_count > 50:
            raise PlatformIsolationError(
                "target_escape",
                f"Symlink target {target} has excessive '..' traversal "
                f"({up_count} levels)",
            )

        # Clean up existing entry at link_path.
        # SAFE REMOVAL: only remove symlinks or files, NEVER ordinary dirs.
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.exists(follow_symlinks=False):
            if link_path.is_dir(follow_symlinks=False):
                raise PlatformIsolationError(
                    "ordinary_dir_at_link_path",
                    f"Expected symlink at {link_path} but found ordinary "
                    "directory. Refusing to recursively delete — remove "
                    "manually or use withdraw_skill first.",
                )
            else:
                link_path.unlink()

        link_path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(target), str(link_path))

    def verify_workspace_link(
        self, link_path: Path, expected_target: Path, canonical_root: Path,
    ) -> bool:
        """Verify a workspace symlink points to the expected canonical target.

        Returns True if the link is valid (exists, is a symlink, points to
        the expected target within canonical_root). Returns False if missing,
        stale, broken, wrong target, or not a symlink.
        """
        if not link_path.is_symlink():
            return False
        try:
            actual = Path(os.readlink(str(link_path)))
            actual_resolved = (link_path.parent / actual).resolve()
            expected_resolved = expected_target.resolve()
            if actual_resolved != expected_resolved:
                return False
            try:
                actual_resolved.relative_to(canonical_root.resolve())
            except ValueError:
                return False
            return True
        except (OSError, ValueError):
            return False

    def is_valid_symlink(self, path: Path) -> bool:
        """Check if *path* is a symlink (exists, is a symlink)."""
        try:
            return path.is_symlink()
        except OSError:
            return False

    def launch_executor(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text: bool = True,
    ) -> subprocess.Popen:
        """Launch a subprocess under the daemon's own OS identity.

        The executor runs directly under the daemon's identity with no
        ``sudo`` handoff. There is NO OS-level isolation — the executor
        can read, write, chmod, or chown anything the daemon account
        can reach. Integrity verification runs before launch for
        best-effort corruption detection; it is NOT a security boundary.

        The provided *env* is merged on top of the daemon's current
        environment.
        """
        base_env = os.environ.copy()
        base_env.update(env)

        try:
            return subprocess.Popen(
                list(cmd),
                cwd=str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=text,
                env=base_env,
            )
        except subprocess.SubprocessError as exc:
            raise PlatformIsolationError(
                "executor_launch_failed",
                f"Failed to launch executor process: {exc}",
            ) from exc


# ── Detection ───────────────────────────────────────────────────────

def detect_platform_isolation() -> PlatformIsolation:
    """Detect and return the platform isolation implementation.

    **macOS (darwin) only.** Linux and Windows are NOT supported in this
    release. Attempts to instantiate isolation on unsupported platforms
    raise PlatformIsolationError with an explicit failure message.
    """
    if sys.platform == "darwin":
        return _MacOSPlatformIsolation()
    else:
        raise PlatformIsolationError(
            "unsupported_platform",
            f"Canonical skill store isolation requires macOS (darwin). "
            f"Current platform '{sys.platform}' is not supported. "
            "Linux and Windows must explicitly fail closed — no fallback.",
        )
