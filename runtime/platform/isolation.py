"""macOS platform isolation for the canonical skill store.

Provides narrowly scoped OS-level identity, ownership, and permissions for
the canonical skill store and workspace link architecture.

**SUPPORTED: macOS (darwin) only.**
Linux and Windows are NOT supported in this release; attempts to use them
fail closed with an explicit error.

**Delivery model:**

The executor and daemon share the same OS identity on macOS. Linked,
validated relative skill links live under BOTH ``.claude/skills`` and
``.agents/skills``. Every user-facing and executor-facing guidance surface
names both roots. Guidance is operational, not a technical security boundary.

The executor runs under the SAME OS identity as the daemon — there is NO
OS-level isolation. An agent-controlled executor process can read/write/chmod
the canonical skill store and anything else the daemon account can reach.
A same UID may mutate, race validation, and affect active/overlapping
sessions. Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal —
do NOT claim the target is immutable, protected, or that write/chmod/ACL
denial exists. Do not describe byte targets, local sources, ArtifactStore,
or links as OS-immutable, ACL-protected, trusted, executor-only
writable/unwritable, or automatically recovered.

**INTEGRITY VERIFICATION:**
Before each executor launch (and at retry-time before Popen/retry), the
daemon compares actual canonical package content against the separately
retained expected manifest (system source tree for system-contract skills,
ArtifactStore manifest for lifecycle skills). Both ``.claude/skills`` and
``.agents/skills`` root links are validated. On mismatch, malformed/broken/
malicious link, or event-persistence failure: a durable visible integrity
event is emitted and the session is REFUSED before Popen/retry — the
mismatched package is NEVER automatically rebuilt, repaired, or healed from
any same-UID local source (including the ArtifactStore, source tree, or any
local copy). Recovery is manual, operator-invoked only, through
``happyranch skills recover``, after an authoritative external
re-sync/redeploy. This is detection for accidental corruption — it is NOT
an attacker-independent external attestation authority. Policy withdrawal
and atomic link repair remain safe.

**Fail-closed:** any isolation violation or integrity mismatch raises
before subprocess launch.
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
    is_service: bool = False  # True if this is the daemon/service account

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
    - Canonical directory ownership/permission checks
    - Workspace symlink creation and validation
    - Executor process launch
    """

    @abstractmethod
    def current_identity(self) -> PlatformIdentity:
        """Return the identity of the current process."""
        ...

    @abstractmethod
    def provision_canonical_store(self, path: Path) -> None:
        """Set ownership/permissions on canonical store."""
        ...

    @abstractmethod
    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify canonical store ownership.

        Raises PlatformIsolationError if ownership/permissions are wrong.
        """
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
        """Launch a subprocess as the executor.

        The executor launches directly under the daemon's identity — there
        is NO OS-level isolation. Integrity verification (see module
        docstring) runs before launch for DETECTION-ONLY corruption
        detection with FAIL-CLOSED refusal.

        Raises PlatformIsolationError if launch fails.
        """
        ...


# ── macOS implementation ────────────────────────────────────────────


class _MacOSPlatformIsolation(PlatformIsolation):
    """macOS platform isolation using POSIX ownership + permissions.

    The executor and daemon share the same OS identity — there is NO
    OS-level isolation. An agent-controlled executor process can
    read/write/chmod the canonical skill store and anything else the
    daemon account can reach.

    - Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal.
    - Do NOT claim OS-level isolation, immutable, or protected targets.
    - A same-UID process may mutate, race validation, and affect
      active/overlapping sessions.
    - Canonical store permissions are verified before each launch.
    """

    def __init__(self) -> None:
        self._daemon_uid = os.getuid()
        self._daemon_gid = os.getgid()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=self._daemon_uid,
            gid=self._daemon_gid,
            is_service=True,
        )

    def provision_canonical_store(self, path: Path) -> None:
        """Set canonical store ownership to daemon uid:gid.

        Ancestor directories get 0755 (owner rwx, group+other rx).
        The executor runs under the daemon's uid so this is cosmetic.
        """
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                 | stat.S_IROTH | stat.S_IXOTH)
        try:
            os.chown(path, self._daemon_uid, self._daemon_gid)
        except PermissionError:
            # Non-root may not be able to chown — acceptable for dev/test.
            pass

    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify canonical store ownership and permissions.

        The path must be owned by daemon uid and NOT be writable by
        group/other. The executor runs under the daemon's uid so it can
        bypass these permissions — this is a best-effort health check.

        Raises PlatformIsolationError on any violation.
        """
        if not path.exists():
            raise PlatformIsolationError(
                "canonical_missing",
                f"Canonical path does not exist: {path}",
            )
        st = path.stat()

        # Permission check: must NOT be group-writable or other-writable
        mode = stat.S_IMODE(st.st_mode)
        if mode & stat.S_IWGRP:
            raise PlatformIsolationError(
                "canonical_group_writable",
                f"Canonical path is group-writable: {path}",
            )
        if mode & stat.S_IWOTH:
            raise PlatformIsolationError(
                "canonical_other_writable",
                f"Canonical path is world-writable: {path}",
            )

        # Ownership check: daemon must be the owner
        if st.st_uid != self._daemon_uid:
            raise PlatformIsolationError(
                "canonical_wrong_owner",
                f"Canonical path {path} is owned by uid={st.st_uid}, "
                f"expected daemon uid={self._daemon_uid}",
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
        """Launch a subprocess as the executor on macOS.

        The executor launches DIRECTLY under the daemon's own identity —
        there is NO OS-level isolation. The executor can read, write, or
        chmod anything the daemon can reach. Integrity verification (see
        module docstring) runs before launch for DETECTION-ONLY corruption
        detection with FAIL-CLOSED refusal; it is NOT a security boundary.

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

# Canonical platform names for error messages
_SUPPORTED_PLATFORMS = frozenset({"darwin"})


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
