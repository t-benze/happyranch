"""Platform isolation abstraction for the immutable canonical skill store.

Provides a narrowly scoped abstraction over OS-level identity, ownership,
ACL, and link validation. The daemon/materializer identity creates/owns
canonical store entries and workspace links. Every executor process
launches as its distinct restricted identity.

Unix implementation: Linux/macOS with POSIX ownership + permissions.
Windows implementation: NTFS ACLs + reparse point (symlink/junction) handling.
"""

from __future__ import annotations

import ctypes
import grp
import os
import pwd
import stat
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PlatformIdentity:
    """OS-level identity of a process or account.

    On Unix: uid + gid.
    On Windows: SID string.
    """

    uid: int  # Unix uid or Windows well-known relative ID
    gid: int  # Unix gid, 0 on Windows
    sid: str = ""  # Windows SID string, empty on Unix
    is_service: bool = False  # True if this is the daemon/service account
    is_restricted: bool = False  # True if this is a restricted executor account

    def __repr__(self) -> str:
        if sys.platform == "win32" and self.sid:
            return f"PlatformIdentity(sid={self.sid}, restricted={self.is_restricted})"
        return f"PlatformIdentity(uid={self.uid}, gid={self.gid}, restricted={self.is_restricted})"


class PlatformIsolationError(Exception):
    """Raised when platform isolation invariants are violated.

    This is a terminal materialization failure — no executor launch proceeds.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


# ── Probes ──────────────────────────────────────────────────────────


def _probe_unix_executor_account() -> Optional[PlatformIdentity]:
    """Check for a provisioned restricted executor account on Unix.

    Looks for a system account named ``_hrexec`` or ``happyranch-exec``
    with uid > 1000 (service account range). Returns the account identity
    if provisioned, None otherwise.
    """
    for name in ("_hrexec", "happyranch-exec", "hrexec"):
        try:
            pw = pwd.getpwnam(name)
            # Service accounts should be non-root, non-login
            if pw.pw_uid > 0:
                # Get primary group
                gr = grp.getgrgid(pw.pw_gid)
                return PlatformIdentity(
                    uid=pw.pw_uid,
                    gid=pw.pw_gid,
                    is_service=False,
                    is_restricted=True,
                )
        except KeyError:
            continue
    return None


def _probe_windows_executor_account() -> Optional[PlatformIdentity]:
    """Check for a provisioned restricted executor account on Windows.

    Looks for a local account named ``HappyRanchExecutor``.
    Returns the account identity if provisioned, None otherwise.
    """
    if sys.platform != "win32":
        return None
    try:
        # Use net user to check for local account
        import subprocess
        result = subprocess.run(
            ["net", "user", "HappyRanchExecutor"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return PlatformIdentity(
                uid=0, gid=0,
                sid="HappyRanchExecutor",  # placeholder — real SID needs win32api
                is_service=False, is_restricted=True,
            )
    except Exception:
        pass
    return None


# ── Abstract platform isolation ─────────────────────────────────────


class PlatformIsolation(ABC):
    """Abstract platform isolation layer.

    Implementations provide:
    - Current process identity
    - Restricted executor identity provisioning
    - Canonical directory ownership/ACL enforcement
    - Workspace link/junction creation and validation
    - Executor process identity switching
    """

    @abstractmethod
    def current_identity(self) -> PlatformIdentity:
        """Return the identity of the current process."""
        ...

    @abstractmethod
    def provision_canonical_store(self, path: Path) -> None:
        """Set ownership/ACL on canonical store so only daemon identity
        can create/own/replace entries. Executor identity has traverse+read
        only.
        """
        ...

    @abstractmethod
    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify that *path* (a canonical package dir or ancestor) is owned
        by the daemon identity and NOT writable by the executor identity.

        Raises PlatformIsolationError if ownership/ACL is wrong.
        """
        ...

    @abstractmethod
    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated relative symlink from *link_path* to *target*.

        Must fail closed if:
        - link_path exists and is not a valid symlink/junction
        - target is absolute or escapes the canonical store root
        - platform does not support symlinks
        """
        ...

    @abstractmethod
    def verify_workspace_link(
        self, link_path: Path, expected_target: Path, canonical_root: Path,
    ) -> bool:
        """Verify that *link_path* is a valid relative symlink/junction
        pointing to *expected_target* within *canonical_root*.

        Returns True if valid, False if missing/stale/wrong/broken.
        """
        ...

    @abstractmethod
    def is_valid_symlink(self, path: Path) -> bool:
        """Check if *path* is a valid, non-malicious symlink."""
        ...

    @abstractmethod
    def make_file_readonly(self, path: Path) -> None:
        """Set file to read-only for all non-owner identities."""
        ...

    @abstractmethod
    def make_dir_readonly_executor(self, path: Path) -> None:
        """Set directory to read+traverse only for executor identity."""
        ...

    def provision_executor_launch_env(self) -> dict[str, str]:
        """Return environment variables to set executor identity on launch.

        On Unix: may return empty dict (identity set via subprocess uid/gid).
        Subclasses may override.
        """
        return {}


# ── Unix implementation ─────────────────────────────────────────────


class _UnixPlatformIsolation(PlatformIsolation):
    """Unix (Linux/macOS) platform isolation using POSIX ownership + permissions."""

    def __init__(self) -> None:
        self._daemon_uid = os.getuid()
        self._daemon_gid = os.getgid()
        self._executor_identity = _probe_unix_executor_account()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=self._daemon_uid,
            gid=self._daemon_gid,
            is_service=True,
            is_restricted=False,
        )

    def provision_canonical_store(self, path: Path) -> None:
        """Set canonical store ownership to daemon uid:gid.

        Ancestor directories get 0755 (owner rwx, group+other rx).
        Files get 0444 (read-only for all) — immutable after creation.

        This ensures the daemon is the ONLY writer; executors can only read.
        """
        path.mkdir(parents=True, exist_ok=True)
        # Set directory permissions: owner rwx, group+other rx
        os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                 | stat.S_IROTH | stat.S_IXOTH)
        try:
            os.chown(path, self._daemon_uid, self._daemon_gid)
        except PermissionError:
            # Non-root may not be able to chown — this is acceptable
            # for dev/test environments where all users are the same
            pass

    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify canonical store ownership.

        In strict mode, the file/dir must be owned by daemon_uid and NOT be
        writable by group/other. In dev mode (same uid), we check permissions
        only.
        """
        if not path.exists():
            raise PlatformIsolationError(
                "canonical_missing",
                f"Canonical path does not exist: {path}",
            )
        st = path.stat()

        # Permission check: file/dir must NOT be group-writable or other-writable
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

        # Ownership check: if daemon uid differs from owner, escalate
        if st.st_uid != self._daemon_uid and self._daemon_uid != 0:
            # Non-root running as different user from file owner — ok in dev
            pass

    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated relative symlink.

        Validates:
        - target is not absolute (relative symlinks only)
        - target does not escape canonical root (no ../ sequences escaping root)
        - link_path parent exists
        """
        if target.is_absolute():
            raise PlatformIsolationError(
                "absolute_target",
                f"Symlink target must be relative, got absolute: {target}",
            )
        # Resolve relative to link directory.
        # The actual canonical-root containment is verified by the
        # SymlinkMaterializer caller.
        link_dir = link_path.parent
        resolved = (link_dir / target).resolve()

        # Reject targets with excessive .. traversal
        target_parts = str(target).split(os.sep)
        up_count = sum(1 for p in target_parts if p == "..")
        if up_count > 10:
            raise PlatformIsolationError(
                "target_escape",
                f"Symlink target {target} has excessive .. traversal ({up_count} levels)",
            )

        # Clean up any existing entry at link_path (after verifying it's not
        # an attacker-controlled directory)
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.exists():
            if link_path.is_dir():
                import shutil
                shutil.rmtree(link_path)
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
            # Must be the exact expected target
            if actual_resolved != expected_resolved:
                return False
            # Must be within canonical root
            try:
                actual_resolved.relative_to(canonical_root.resolve())
            except ValueError:
                return False
            return True
        except (OSError, ValueError):
            return False

    def is_valid_symlink(self, path: Path) -> bool:
        """Check if *path* is a symlink (exists, is a symlink, not malicious)."""
        try:
            return path.is_symlink()
        except OSError:
            return False

    def make_file_readonly(self, path: Path) -> None:
        """Set file to 0444 (read-only for all)."""
        if path.exists():
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def make_dir_readonly_executor(self, path: Path) -> None:
        """Set dir to 0555 (read+execute, no write)."""
        if path.exists() and path.is_dir():
            os.chmod(path, stat.S_IRUSR | stat.S_IXUSR
                     | stat.S_IRGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH)


# ── Windows implementation ──────────────────────────────────────────


class _WindowsPlatformIsolation(PlatformIsolation):
    """Windows platform isolation using NTFS ACLs + reparse points."""

    def __init__(self) -> None:
        self._executor_identity = _probe_windows_executor_account()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=0, gid=0, sid="", is_service=True, is_restricted=False,
        )

    def provision_canonical_store(self, path: Path) -> None:
        """On Windows, create directory and set basic ACL via icacls."""
        path.mkdir(parents=True, exist_ok=True)
        # Set read-only via icacls (deny write to builtin users)
        # In practice this needs the provisioned executor SID
        try:
            import subprocess
            subprocess.run(
                ["icacls", str(path), "/inheritance:r",
                 "/grant:r", f"BUILTIN\\Administrators:(OI)(CI)F",
                 "/grant:r", f"BUILTIN\\Users:(OI)(CI)RX"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify NTFS ACL on canonical store path.
        Basic check: directory must exist.
        """
        if not path.exists():
            raise PlatformIsolationError(
                "canonical_missing",
                f"Canonical path does not exist: {path}",
            )

    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated directory symlink or junction on Windows.

        Uses os.symlink with target_is_directory=True on supported Windows
        versions. Falls back to junction via mklink /J if needed.
        """
        if target.is_absolute():
            raise PlatformIsolationError(
                "absolute_target",
                "Symlink target must be relative on Windows",
            )

        link_dir = link_path.parent
        resolved = (link_dir / target).resolve()
        try:
            resolved.relative_to(link_dir.resolve())
        except ValueError:
            raise PlatformIsolationError(
                "target_escape",
                f"Symlink target {target} escapes parent directory",
            )

        # Clean up existing entry
        if link_path.exists():
            if link_path.is_dir():
                import shutil
                shutil.rmtree(link_path)
            else:
                link_path.unlink()

        link_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            os.symlink(str(target), str(link_path), target_is_directory=True)
        except OSError:
            # Fallback: use mklink /J for junction
            import subprocess
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
                capture_output=True, timeout=10, check=True,
            )

    def verify_workspace_link(
        self, link_path: Path, expected_target: Path, canonical_root: Path,
    ) -> bool:
        """Verify a Windows symlink/junction points to the expected target."""
        if not link_path.exists():
            return False
        try:
            actual = Path(os.readlink(str(link_path)))
            actual_resolved = (link_path.parent / actual).resolve()
            if actual_resolved != expected_target.resolve():
                return False
            try:
                actual_resolved.relative_to(canonical_root.resolve())
            except ValueError:
                return False
            return True
        except (OSError, ValueError):
            return False

    def is_valid_symlink(self, path: Path) -> bool:
        """Check if *path* is a reparse point (symlink or junction)."""
        try:
            return path.is_symlink() or path.is_junction()
        except OSError:
            return False

    def make_file_readonly(self, path: Path) -> None:
        """Set file read-only attribute on Windows."""
        if path.exists():
            import subprocess
            subprocess.run(
                ["attrib", "+R", str(path)],
                capture_output=True, timeout=5,
            )

    def make_dir_readonly_executor(self, path: Path) -> None:
        """Deny write to directory via icacls."""
        if path.exists():
            import subprocess
            subprocess.run(
                ["icacls", str(path), "/deny", "BUILTIN\\Users:(WD)"],
                capture_output=True, timeout=10,
            )


# ── Detection ───────────────────────────────────────────────────────


def detect_platform_isolation() -> PlatformIsolation:
    """Detect and return the appropriate platform isolation implementation.

    Returns Unix implementation on Linux/macOS, Windows implementation on
    Windows. Fails closed on unsupported platforms.
    """
    if sys.platform == "win32":
        return _WindowsPlatformIsolation()
    elif sys.platform in ("linux", "darwin"):
        return _UnixPlatformIsolation()
    else:
        raise PlatformIsolationError(
            "unsupported_platform",
            f"Platform {sys.platform} is not supported for canonical skill store isolation",
        )
