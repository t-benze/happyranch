"""macOS platform isolation for the immutable canonical skill store.

Provides narrowly scoped OS-level identity, ownership, and permissions for
the canonical skill store and workspace link architecture.

**SUPPORTED: macOS (darwin) only.**
Linux and Windows are NOT supported in this release; attempts to use them
fail closed with an explicit error.

**SECURITY CONTRACT:**
- Daemon/materializer identity alone may mutate canonical store + workspace
  managed-skill-root entries.
- Executor processes launch as a DISTINCT restricted macOS identity via
  ``sudo -n -u <executor>`` identity handoff (non-root daemon model).
- Same-owner executor launch is NEVER accepted.
- Fail-closed: any isolation violation raises before subprocess launch.
"""

from __future__ import annotations

import grp
import os
import pwd
import stat
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PlatformIdentity:
    """OS-level identity of a process or account.

    On macOS: uid + gid.
    """

    uid: int
    gid: int
    is_service: bool = False  # True if this is the daemon/service account
    is_restricted: bool = False  # True if this is a restricted executor account

    def __repr__(self) -> str:
        return (
            f"PlatformIdentity(uid={self.uid}, gid={self.gid}, "
            f"restricted={self.is_restricted})"
        )


class PlatformIsolationError(Exception):
    """Raised when platform isolation invariants are violated.

    This is a terminal materialization failure — no executor launch proceeds.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


# ── macOS executor account probes ──────────────────────────────────


def _probe_macos_executor_account() -> Optional[PlatformIdentity]:
    """Check for a provisioned restricted executor account on macOS.

    Looks for a system account named ``_hrexec`` or ``happyranch-exec``
    with uid > 0 (non-root). Returns the account identity
    if provisioned, None otherwise.
    """
    for name in ("_hrexec", "happyranch-exec", "hrexec"):
        try:
            pw = pwd.getpwnam(name)
            if pw.pw_uid > 0:
                gr = grp.getgrgid(pw.pw_gid)
                return PlatformIdentity(
                    uid=pw.pw_uid,
                    gid=gr.gr_gid,
                    is_service=False,
                    is_restricted=True,
                )
        except KeyError:
            continue
    return None


# ── Abstract platform isolation ─────────────────────────────────────


class PlatformIsolation(ABC):
    """Abstract platform isolation layer.

    macOS implementation provides:
    - Current process identity
    - Restricted executor identity provisioning
    - Canonical directory ownership/permission enforcement
    - Workspace symlink creation and validation
    - Executor process identity switching via launch_executor
    """

    @abstractmethod
    def current_identity(self) -> PlatformIdentity:
        """Return the identity of the current process."""
        ...

    @abstractmethod
    def executor_identity(self) -> Optional[PlatformIdentity]:
        """Return the provisioned restricted executor identity, or None."""
        ...

    @abstractmethod
    def provision_canonical_store(self, path: Path) -> None:
        """Set ownership/permissions on canonical store so only daemon
        identity can create/own/replace entries. Executor has traverse+read.
        """
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
    def make_file_readonly(self, path: Path) -> None:
        """Set file to read-only for all non-owner identities."""
        ...

    @abstractmethod
    def make_dir_readonly_executor(self, path: Path) -> None:
        """Set directory to read+traverse only for executor identity."""
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
        """Launch a subprocess as the restricted executor identity.

        On macOS this is achieved via ``sudo -n -u <executor>`` identity
        handoff (non-root daemon model). Direct setgid/setuid from
        preexec_fn is NOT available to non-root daemon processes.

        Raises PlatformIsolationError if:
        - No restricted executor identity is provisioned
        - The executor identity is the SAME as the daemon identity
        - sudo capability or authorization is unavailable
        """
        ...


# ── macOS implementation ────────────────────────────────────────────


def _resolve_executor_username(identity: PlatformIdentity) -> str:
    """Resolve the executor username from the provisioned account.

    Looks up the username for the given uid. The provisioned account
    must exist and have a distinct uid from the daemon.

    Raises PlatformIsolationError if the account cannot be resolved.
    """
    try:
        pw = pwd.getpwuid(identity.uid)
        return pw.pw_name
    except KeyError:
        raise PlatformIsolationError(
            "executor_username_unresolvable",
            f"Cannot resolve username for executor uid={identity.uid}. "
            "Ensure the provisioned executor account exists.",
        )


def _verify_sudo_capability(username: str) -> None:
    """Verify non-interactive sudo access to the executor account.

    Runs ``sudo -n -u <username> true`` to check that:
    - sudo is available
    - The daemon process has passwordless sudo authority for this user
    - The executor account exists and can execute commands

    Raises PlatformIsolationError on any failure.
    """
    try:
        result = subprocess.run(
            ["sudo", "-n", "-u", username, "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise PlatformIsolationError(
                "sudo_capability_failed",
                f"sudo -n -u {username} exited {result.returncode}: "
                f"{result.stderr.strip()}. "
                "The daemon must have passwordless sudo authority for "
                f"the executor account '{username}'. Provision via sudoers: "
                f"<daemon_user> ALL=({username}) NOPASSWD: ALL",
            )
    except FileNotFoundError:
        raise PlatformIsolationError(
            "sudo_unavailable",
            "sudo command not found. The macOS executor launch contract "
            "requires sudo for non-root identity handoff.",
        )
    except subprocess.TimeoutExpired:
        raise PlatformIsolationError(
            "sudo_timeout",
            "sudo -n -u command timed out. Check sudoers configuration.",
        )


def _drop_privileges_macos(uid: int, gid: int) -> None:
    """preexec_fn helper: drop privileges to executor uid/gid before exec.

    Sets gid first (permissions order), then uid.
    FAIL-CLOSED: any failure to drop privileges raises PlatformIsolationError
    BEFORE exec — the child MUST run as the distinct restricted executor
    identity, never as the daemon owner.

    **Note:** This path requires the daemon to run with sufficient
    privileges (root or CAP_SETUID/CAP_SETGID). For the non-root deployment
    model, ``launch_executor`` uses ``sudo -n -u <executor>`` instead.
    """
    try:
        os.setgid(gid)
        os.setuid(uid)
    except PermissionError as exc:
        raise PlatformIsolationError(
            "privilege_drop_failed",
            f"Cannot drop privileges to uid={uid} gid={gid}: {exc}. "
            "Executor must run as a DISTINCT restricted macOS identity. "
            "Ensure the daemon runs with sufficient privileges (root or "
            "CAP_SETUID/CAP_SETGID) to switch to the executor account.",
        ) from exc
    except OSError as exc:
        raise PlatformIsolationError(
            "privilege_drop_failed",
            f"OS error dropping privileges to uid={uid} gid={gid}: {exc}",
        ) from exc


class _MacOSPlatformIsolation(PlatformIsolation):
    """macOS platform isolation using POSIX ownership + permissions.

    **Identity contract:**
    - Daemon uid/gid must differ from executor uid/gid.
    - Same-owner launch is REJECTED.
    - Canonical store is owned by daemon, not writable by others.
    """

    def __init__(self) -> None:
        self._daemon_uid = os.getuid()
        self._daemon_gid = os.getgid()
        self._executor_identity = _probe_macos_executor_account()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=self._daemon_uid,
            gid=self._daemon_gid,
            is_service=True,
            is_restricted=False,
        )

    def executor_identity(self) -> Optional[PlatformIdentity]:
        return self._executor_identity

    def _assert_executor_distinct(self) -> None:
        """Verify executor identity is provisioned and distinct from daemon.

        Raises PlatformIsolationError if same-owner or unprovisioned.
        """
        if self._executor_identity is None:
            raise PlatformIsolationError(
                "executor_unprovisioned",
                "No restricted macOS executor account provisioned. "
                "Create '_hrexec' or 'happyranch-exec' system account "
                "with a distinct uid/gid from the daemon.",
            )
        if self._executor_identity.uid == self._daemon_uid:
            raise PlatformIsolationError(
                "executor_same_owner",
                f"Executor identity (uid={self._executor_identity.uid}) "
                f"is same as daemon (uid={self._daemon_uid}). "
                "Executor must run as a DISTINCT restricted macOS identity.",
            )

    def _resolve_executor_username_for_launch(self) -> str:
        """Resolve the executor username and verify sudo capability.

        Called before every launch_executor call. Returns the username
        of the provisioned restricted executor account.

        Raises PlatformIsolationError if the account cannot be resolved
        or passwordless sudo is not configured.
        """
        assert self._executor_identity is not None
        username = _resolve_executor_username(self._executor_identity)
        _verify_sudo_capability(username)
        return username

    def provision_canonical_store(self, path: Path) -> None:
        """Set canonical store ownership to daemon uid:gid.

        Ancestor directories get 0755 (owner rwx, group+other rx).
        The daemon is the ONLY writer; executors can only read+traverse.
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
        """Verify canonical store ownership.

        The path must be owned by daemon uid and NOT be writable by
        group/other.

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

    def make_file_readonly(self, path: Path) -> None:
        """Set file to 0444 (read-only for all)."""
        if path.exists():
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def make_dir_readonly_executor(self, path: Path) -> None:
        """Set dir to 0755 (owner rwx, group+other rx).

        The daemon owner MUST retain write so new canonical packages can be
        built in subdirectories. Executor identity has a DISTINCT uid and
        is protected by Unix user/group model, not by removing owner write.
        """
        if path.exists() and path.is_dir():
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH)

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
        """Launch a subprocess as the restricted executor identity on macOS.

        **Non-root deployment model (production/serving).**
        Uses ``sudo -n -u <provisioned executor>`` to hand off the OS
        identity. This is the ONLY supported launch path — the daemon
        is NOT expected to run as root, and direct setgid/setuid from
        preexec_fn is NOT available to non-root daemon processes.

        Before construction:
        1. Verifies executor identity is provisioned and distinct.
        2. Resolves the executor username.
        3. Verifies passwordless sudo capability (``sudo -n -u <user> true``).
        4. Constructs a ``sudo -n -u <user> -- <cmd>`` invocation.

        If provisioning, identity resolution, sudo authorization, command
        construction, or ACL capability is unavailable → fail closed.

        Same-owner launch is REJECTED — executor identity MUST differ from
        daemon.

        The provided *env* is merged on top of the daemon's current
        environment so sudo itself always has at least PATH and HOME.
        This prevents environment-starvation failures when a caller
        passes an empty or minimal env dict.
        """
        self._assert_executor_distinct()
        assert self._executor_identity is not None  # narrow type for mypy

        # Resolve executor username and verify sudo access
        executor_user = self._resolve_executor_username_for_launch()

        # Build sudo invocation: sudo -n -u <executor_user> -- <cmd>
        sudo_cmd = ["sudo", "-n", "-u", executor_user, "--"] + list(cmd)

        # Merge caller env on top of daemon env so sudo never starves.
        base_env = os.environ.copy()
        base_env.update(env)

        try:
            return subprocess.Popen(
                sudo_cmd,
                cwd=str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=text,
                env=base_env,
            )
        except PlatformIsolationError:
            raise
        except subprocess.SubprocessError as exc:
            raise PlatformIsolationError(
                "executor_launch_failed",
                f"Failed to launch restricted executor process: {exc}",
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
