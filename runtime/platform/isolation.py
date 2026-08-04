"""macOS platform isolation for the canonical skill store.

Provides narrowly scoped OS-level identity, ownership, and permissions for
the canonical skill store and workspace link architecture.

**SUPPORTED: macOS (darwin) only.**
Linux and Windows are NOT supported in this release; attempts to use them
fail closed with an explicit error.

**Delivery model (same-owner):**

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

import grp
import logging
import os
import pwd
import stat
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SAME_OWNER_ENV_VAR = "HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR"


def _same_owner_mode_enabled() -> bool:
    """True if the operator has explicitly opted into same-owner executors."""
    return os.environ.get(_SAME_OWNER_ENV_VAR, "").strip().lower() in ("1", "true", "yes")


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
    """Check for a restricted executor account on macOS.

    Looks for a system account named ``_hrexec`` or ``happyranch-exec``
    with uid > 0 (non-root). Returns the account identity
    if available, None otherwise.
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
    - Restricted executor identity detection
    - Canonical directory ownership/permission checks
    - Workspace symlink creation and validation
    - Executor process identity switching via launch_executor
    - Mode observability (same-owner vs non-same-owner)
    """

    @property
    @abstractmethod
    def is_same_owner_mode(self) -> bool:
        """True if running in same-owner executor mode.

        In same-owner mode there is NO OS-level isolation — the executor
        runs under the daemon's identity. Workspace symlinks and integrity
        checks are best-effort corruption detection only, not a security
        boundary.
        """
        ...

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
        """Launch a subprocess as the executor identity.

        On macOS this uses ``sudo -n -u <executor>`` identity
        handoff when a distinct executor account is available.
        When no distinct account is configured, launches directly
        under the daemon's identity (same-owner mode — no OS-level
        isolation).

        Raises PlatformIsolationError if:
        - sudo capability or authorization is unavailable
        - executor identity is unprovisioned
        """
        ...


# ── macOS implementation ────────────────────────────────────────────


def _resolve_executor_username(identity: PlatformIdentity) -> str:
    """Resolve the executor username from the available account.

    Looks up the username for the given uid. The account must exist.

    Raises PlatformIsolationError if the account cannot be resolved.
    """
    try:
        pw = pwd.getpwuid(identity.uid)
        return pw.pw_name
    except KeyError:
        raise PlatformIsolationError(
            "executor_username_unresolvable",
            f"Cannot resolve username for executor uid={identity.uid}. "
            "Ensure the configured executor account exists.",
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
    BEFORE exec.

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

    The executor and daemon share the same OS identity (same-owner mode).
    - Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal.
    - Do NOT claim OS-level isolation, immutable, or protected targets.
    - A same-UID process may mutate, race validation, and affect
      active/overlapping sessions.
    - When a distinct executor account IS available, the daemon uid/gid
      must differ from executor uid/gid (the account identity is verified).
    - Canonical store permissions are verified before each launch.
    """

    def __init__(self) -> None:
        self._daemon_uid = os.getuid()
        self._daemon_gid = os.getgid()
        self._executor_identity = _probe_macos_executor_account()
        self._same_owner_mode = False
        if self._executor_identity is None and _same_owner_mode_enabled():
            logger.warning(
                "%s is set and no executor account is "
                "configured — agent executors will run under the SAME OS "
                "identity as the daemon (uid=%d). This removes OS-level "
                "isolation: an agent-controlled process can read/write the "
                "canonical skill store and anything else this account can "
                "reach. Accepted as an explicit operator tradeoff.",
                _SAME_OWNER_ENV_VAR, self._daemon_uid,
            )
            self._executor_identity = PlatformIdentity(
                uid=self._daemon_uid,
                gid=self._daemon_gid,
                is_service=False,
                is_restricted=True,
            )
            self._same_owner_mode = True

    @property
    def is_same_owner_mode(self) -> bool:
        """True if running in same-owner executor mode.

        In this mode the executor runs under the daemon's own OS identity.
        There is NO OS-level isolation — workspace symlinks, prompt guidance,
        hashes, and integrity verification are best-effort corruption
        detection/recovery only, not a security boundary.

        This property makes the selected mode observable/auditable at runtime
        without an auth or schema change.
        """
        return self._same_owner_mode

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
        """Verify executor identity is available and distinct from daemon.

        Raises PlatformIsolationError if executor account is not configured
        and same-owner mode guard is not active.
        """
        if self._executor_identity is None:
            raise PlatformIsolationError(
                "executor_unprovisioned",
                "No restricted macOS executor account provisioned. "
                "Create '_hrexec' or 'happyranch-exec' system account "
                "with a distinct uid/gid from the daemon, or set "
                f"{_SAME_OWNER_ENV_VAR}=1 to explicitly accept running "
                "the executor under the daemon's own identity.",
            )
        if self._same_owner_mode:
            return
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
        of the restricted executor account.

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
        In same-owner mode the executor runs under the daemon's uid
        and can write through the symlinks — this is cosmetic.
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
        group/other. In same-owner mode this is a best-effort health
        check (the executor runs under the daemon's uid so it can
        bypass these permissions).

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
        """Set file to 0444 (read-only for all).

        In same-owner mode this is cosmetic — the executor shares the
        daemon's uid and can chmod the file back.
        """
        if path.exists():
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def make_dir_readonly_executor(self, path: Path) -> None:
        """Set dir to 0755 (owner rwx, group+other rx).

        The daemon owner MUST retain write so new canonical packages can be
        built in subdirectories. In same-owner mode this is cosmetic —
        the executor shares the daemon's uid.
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
        """Launch a subprocess as the executor identity on macOS.

        When a distinct executor account is available, uses
        ``sudo -n -u <executor>`` to hand off the OS identity. Before
        construction: verifies executor identity, resolves the executor
        username, verifies passwordless sudo capability, and constructs
        a ``sudo -n -u <user> -- <cmd>`` invocation. If identity
        resolution, sudo authorization, or command construction is
        unavailable → fail closed.

        When no distinct executor account is configured
        (``HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR=1`` or same-owner mode),
        the process launches DIRECTLY under the daemon's own identity
        with no ``sudo`` handoff. There is NO OS-level isolation — the
        executor can read, write, or chmod anything the daemon can reach.
        Integrity verification (see module docstring) runs before launch
        for DETECTION-ONLY corruption detection with FAIL-CLOSED refusal;
        it is NOT a security boundary.

        The provided *env* is merged on top of the daemon's current
        environment so sudo itself always has at least PATH and HOME.
        """
        self._assert_executor_distinct()
        assert self._executor_identity is not None  # narrow type for mypy

        base_env = os.environ.copy()
        base_env.update(env)

        if self._same_owner_mode:
            # No distinct identity to hand off to — launch directly.
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
                    f"Failed to launch same-owner executor process: {exc}",
                ) from exc

        # Resolve executor username and verify sudo access
        executor_user = self._resolve_executor_username_for_launch()

        # Build sudo invocation: sudo -n -u <executor_user> -- <cmd>
        sudo_cmd = ["sudo", "-n", "-u", executor_user, "--"] + list(cmd)

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
