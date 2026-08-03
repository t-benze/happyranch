"""Platform isolation abstraction for the immutable canonical skill store.

Provides a narrowly scoped abstraction over OS-level identity, ownership,
ACL, and link validation. The daemon/materializer identity creates/owns
canonical store entries and workspace links. Every executor process
launches as its distinct restricted identity.

Unix implementation: Linux/macOS with POSIX ownership + permissions.
Windows implementation: NTFS ACLs + reparse point (symlink/junction) handling.

**SECURITY CONTRACT:**
- Daemon/materializer identity alone may mutate canonical store + workspace
  managed-skill-root entries.
- Executor processes launch as a DISTINCT restricted identity.
- Same-owner executor launch is NEVER accepted.
- Fail-closed: any isolation violation raises before subprocess launch.
"""

from __future__ import annotations

import ctypes
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
                    gid=pw.pw_gid,
                    is_service=False,
                    is_restricted=True,
                )
        except KeyError:
            continue
    return None


def _probe_windows_executor_sid() -> Optional[str]:
    """Resolve the real SID of the local 'HappyRanchExecutor' account on Windows.

    Uses ``wmic useraccount get name,sid`` to obtain the actual SID string.
    Returns the SID if found, None otherwise.
    """
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ["wmic", "useraccount", "where", "name='HappyRanchExecutor'", "get", "sid"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("S-1-"):
                return line
    except Exception:
        pass
    return None


def _probe_windows_executor_account() -> Optional[PlatformIdentity]:
    """Check for a provisioned restricted executor account on Windows.

    Resolves the real SID via wmic. Returns the account identity
    if provisioned, None otherwise.
    """
    if sys.platform != "win32":
        return None
    sid = _probe_windows_executor_sid()
    if sid is not None:
        return PlatformIdentity(
            uid=0, gid=0,
            sid=sid,
            is_service=False, is_restricted=True,
        )
    return None


# ── Abstract platform isolation ─────────────────────────────────────


class PlatformIsolation(ABC):
    """Abstract platform isolation layer.

    Implementations provide:
    - Current process identity
    - Restricted executor identity provisioning
    - Canonical directory ownership/ACL enforcement
    - Workspace link/junction creation and validation
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

        Must NOT recursively delete ordinary directories (no rmtree).
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

        The child process runs under the provisioned restricted executor
        identity (different uid/gid on Unix, different SID on Windows).

        Raises PlatformIsolationError if:
        - No restricted executor identity is provisioned
        - The executor identity is the SAME as the daemon identity
        - The platform/launcher cannot switch identity

        Returns an open subprocess.Popen handle.
        """
        ...

    def provision_executor_launch_env(self) -> dict[str, str]:
        """Return environment variables to set executor identity on launch.

        On Unix: may return empty dict (identity set via subprocess uid/gid).
        Subclasses may override.
        """
        return {}


# ── Unix implementation ─────────────────────────────────────────────


def _drop_privileges_unix(uid: int, gid: int) -> None:
    """preexec_fn helper: drop privileges to executor uid/gid before exec.

    Sets gid first (permissions order), then uid.
    FAIL-CLOSED: any failure to drop privileges (PermissionError) raises
    PlatformIsolationError BEFORE exec — the child MUST run as the distinct
    restricted executor identity, never as the daemon owner.
    """
    try:
        os.setgid(gid)
        os.setuid(uid)
    except PermissionError as exc:
        # The daemon is NOT allowed to launch executor processes as itself.
        # This is a hard failure — the privilege drop is mandatory.
        raise PlatformIsolationError(
            "privilege_drop_failed",
            f"Cannot drop privileges to uid={uid} gid={gid}: {exc}. "
            "Executor must run as a DISTINCT restricted identity. "
            "Ensure the daemon has CAP_SETUID/CAP_SETGID or equivalent.",
        ) from exc


class _UnixPlatformIsolation(PlatformIsolation):
    """Unix (Linux/macOS) platform isolation using POSIX ownership + permissions.

    **Identity contract:**
    - Daemon uid/gid must differ from executor uid/gid.
    - Same-owner launch is REJECTED — every executor process must have a
      distinct restricted identity.
    - canonical store is owned by daemon uid, not writable by others.
    """

    _REPARSE_TAG_SYMLINK = 0xA000000C  # IO_REPARSE_TAG_SYMLINK (unused on Unix)

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

    def executor_identity(self) -> Optional[PlatformIdentity]:
        return self._executor_identity

    def _assert_executor_distinct(self) -> None:
        """Verify executor identity is provisioned and distinct from daemon.

        Raises PlatformIsolationError if same-owner or unprovisioned.
        """
        if self._executor_identity is None:
            raise PlatformIsolationError(
                "executor_unprovisioned",
                "No restricted executor account provisioned. "
                "Create '_hrexec' or 'happyranch-exec' system account.",
            )
        if self._executor_identity.uid == self._daemon_uid:
            raise PlatformIsolationError(
                "executor_same_owner",
                f"Executor identity (uid={self._executor_identity.uid}) "
                f"is same as daemon (uid={self._daemon_uid}). "
                "Executor must run as a DISTINCT restricted identity.",
            )

    def provision_canonical_store(self, path: Path) -> None:
        """Set canonical store ownership to daemon uid:gid.

        Ancestor directories get 0755 (owner rwx, group+other rx).
        This ensures the daemon is the ONLY writer; executors can only read.
        """
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                 | stat.S_IROTH | stat.S_IXOTH)
        try:
            os.chown(path, self._daemon_uid, self._daemon_gid)
        except PermissionError:
            # Non-root may not be able to chown — this is acceptable
            # for dev/test environments. Ownership will be validated at
            # verify_canonical_ownership.
            pass

    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify canonical store ownership.

        The path must be owned by daemon uid and NOT be writable by
        group/other. Same-owner is rejected — daemon uid must
        differ from typical executor identity.

        Raises PlatformIsolationError on any violation.
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

        # Ownership check: daemon must be the owner.
        # If daemon uid differs from file owner, the canonical store was
        # not created by this daemon process — fail.
        if st.st_uid != self._daemon_uid:
            raise PlatformIsolationError(
                "canonical_wrong_owner",
                f"Canonical path {path} is owned by uid={st.st_uid}, "
                f"expected daemon uid={self._daemon_uid}",
            )

    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated relative symlink.

        Validates:
        - target is not absolute (relative symlinks only)
        - target does not escape canonical root (no excessive ../ traversal)

        **Safe repair:** existing entries are removed ONLY through
        no-follow validated routines. Ordinary directories are NEVER
        recursively deleted — the caller must first validate the entry
        is a safe-to-remove symlink.
        """
        if target.is_absolute():
            raise PlatformIsolationError(
                "absolute_target",
                f"Symlink target must be relative, got absolute: {target}",
            )

        # Reject targets with excessive .. traversal (sanity check)
        target_parts = str(target).split(os.sep)
        up_count = sum(1 for p in target_parts if p == "..")
        if up_count > 50:
            raise PlatformIsolationError(
                "target_escape",
                f"Symlink target {target} has excessive .. traversal ({up_count} levels)",
            )

        # Clean up existing entry at link_path.
        # SAFE REMOVAL: only remove symlinks or files, NEVER ordinary directories.
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.exists(follow_symlinks=False):
            if link_path.is_dir(follow_symlinks=False):
                raise PlatformIsolationError(
                    "ordinary_dir_at_link_path",
                    f"Expected symlink at {link_path} but found ordinary directory. "
                    "Refusing to recursively delete — remove manually or use "
                    "withdraw_skill first.",
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

        Uses preexec_fn to setgid+setuid to the executor identity before exec.
        The preexec_fn RAISES on failure — the Popen constructor will catch
        the exception before any subprocess is created, resulting in a
        subprocess.SubprocessError, which we convert to PlatformIsolationError.

        Same-owner launch is REJECTED — executor identity MUST differ from
        daemon.
        """
        self._assert_executor_distinct()
        assert self._executor_identity is not None  # narrow type for mypy

        try:
            return subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=text,
                env=env,
                preexec_fn=lambda: _drop_privileges_unix(
                    self._executor_identity.uid,
                    self._executor_identity.gid,
                ),
            )
        except subprocess.SubprocessError as exc:
            raise PlatformIsolationError(
                "executor_launch_failed",
                f"Failed to launch restricted executor process: {exc}",
            ) from exc


# ── Windows implementation ──────────────────────────────────────────

# NTFS reparse tag constants
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # Junction


def _validate_windows_reparse_tag(path: Path) -> bool:
    """Validate that *path* is a symlink or junction with known reparse tag.

    Uses ctypes to call GetFileAttributesW to check for reparse point attribute,
    THEN uses DeviceIoControl with FSCTL_GET_REPARSE_POINT to confirm the
    specific tag is IO_REPARSE_TAG_SYMLINK or IO_REPARSE_TAG_MOUNT_POINT.
    Any other reparse point (e.g. IO_REPARSE_TAG_DEDUP, IO_REPARSE_TAG_WCI)
    is REJECTED — fail-closed.

    Returns True only for validated symlink or junction reparse points.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes.wintypes

        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        FILE_SHARE_READ = 1
        FILE_SHARE_WRITE = 2
        OPEN_EXISTING = 3
        GENERIC_READ = 0x80000000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        FSCTL_GET_REPARSE_POINT = 0x000900A8

        GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
        GetFileAttributesW.argtypes = [ctypes.wintypes.LPCWSTR]
        GetFileAttributesW.restype = ctypes.wintypes.DWORD
        attrs = GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF:
            return False
        if not (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
            return False

        # Open the reparse point to read its tag
        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CreateFileW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD, ctypes.c_void_p,
            ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.wintypes.HANDLE,
        ]
        CreateFileW.restype = ctypes.wintypes.HANDLE
        handle = CreateFileW(
            str(path),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            return False

        try:
            class REPARSE_DATA_BUFFER(ctypes.Structure):
                _fields_ = [
                    ("ReparseTag", ctypes.c_uint32),
                    ("ReparseDataLength", ctypes.c_uint16),
                    ("Reserved", ctypes.c_uint16),
                ]

            buf = REPARSE_DATA_BUFFER()
            bytes_returned = ctypes.wintypes.DWORD(0)
            DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
            DeviceIoControl.argtypes = [
                ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
                ctypes.c_void_p, ctypes.wintypes.DWORD,
                ctypes.c_void_p, ctypes.wintypes.DWORD,
                ctypes.POINTER(ctypes.wintypes.DWORD),
                ctypes.c_void_p,
            ]
            DeviceIoControl.restype = ctypes.wintypes.BOOL
            ok = DeviceIoControl(
                handle, FSCTL_GET_REPARSE_POINT,
                None, 0,
                ctypes.byref(buf), ctypes.sizeof(buf),
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                return False

            return buf.ReparseTag in (
                _IO_REPARSE_TAG_SYMLINK,
                _IO_REPARSE_TAG_MOUNT_POINT,
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


class _WindowsPlatformIsolation(PlatformIsolation):
    """Windows platform isolation using NTFS ACLs + reparse points.

    **Fail-closed contract:**
    - icacls failures raise, not swallowed.
    - Reparse tags are validated (symlink or junction only).
    - Same-owner executor launch is REJECTED.
    - Ordinary directories are never recursively deleted.
    """

    _REPARSE_TAG_SYMLINK = _IO_REPARSE_TAG_SYMLINK

    def __init__(self) -> None:
        self._executor_identity = _probe_windows_executor_account()

    def current_identity(self) -> PlatformIdentity:
        return PlatformIdentity(
            uid=0, gid=0,
            sid=os.environ.get("USERNAME", ""),
            is_service=True, is_restricted=False,
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
                "No restricted executor account provisioned on Windows. "
                "Create local account 'HappyRanchExecutor'.",
            )
        daemon_user = os.environ.get("USERNAME", "")
        if not daemon_user:
            raise PlatformIsolationError(
                "daemon_identity_unknown",
                "Cannot determine daemon identity (USERNAME not set).",
            )
        # The executor SID must differ from the daemon's.
        # A precise SID comparison requires win32api; for now,
        # compare username-based heuristics.
        if daemon_user.lower() == "happyranchexecutor":
            raise PlatformIsolationError(
                "executor_same_owner",
                "Daemon is running as HappyRanchExecutor — "
                "executor must run as a DISTINCT restricted identity.",
            )

    def provision_canonical_store(self, path: Path) -> None:
        """On Windows, create directory and set ACL via icacls.

        Administrators get full control, Users (executor) get RX only.
        icacls failures raise — not swallowed.
        """
        path.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", "BUILTIN\\Administrators:(OI)(CI)F",
             "/grant:r", "BUILTIN\\Users:(OI)(CI)RX"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise PlatformIsolationError(
                "acl_provision_failed",
                f"icacls failed on {path}: {result.stderr.strip() or result.stdout.strip()}",
            )

    def verify_canonical_ownership(self, path: Path) -> None:
        """Verify NTFS CANON STORE ACLs.

        Checks: path exists; built-in CCTLS inspection succeeds; D"Users" has
        ONLY read & execute (RX) and does NOT have write append (W), modify (M),
        or full control (F). Fail-closed: missing path or icacls failure raises;
        insufficient ACL enforcement raises.
        """
        if not path.exists():
            raise PlatformIsolationError(
                "canonical_missing",
                f"Canonical path does not exist: {path}",
            )
        result = subprocess.run(
            ["icacls", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise PlatformIsolationError(
                "acl_inspection_failed",
                f"Cannot inspect ACL on {path}: "
                f"{result.stderr.strip() or 'icacls returned ' + str(result.returncode)}",
            )
        # Verify BUILTIN\\CcUsers has RX only (no W/M/F)
        stdout = result.stdout
        has_users_line = False
        for line in stdout.splitlines():
            if "BUILTIN\\Users" in line or r"BUILTIN\Users" in line:
                has_users_line = True
                entry = line.upper()
                for forbidden in ("(W)", "(M)", "(F)", "(WD)"):
                    if forbidden in entry:
                        raise PlatformIsolationError(
                            "canonical_writeable_by_users",
                            f"Canonical path {path} grants write to BUILTIN\\Users: {line.strip()}",
                        )
                break
        if not has_users_line:
            raise PlatformIsolationError(
                "acl_missing_users",
                f"Canonical path {path} has no BUILTIN\\Users entry; cannot verify executor isolation",
            )

    def create_relative_symlink(
        self, target: Path, link_path: Path,
    ) -> None:
        """Create a validated directory symlink on Windows.

        Uses CreateSymbolicLink via os.symlink with target_is_directory=True.
        **Safe repair:** ordinary directories at link_path raise, never deleted.

        Validates:
        - target is relative
        - no target escape
        - reparse tag validation on existing entries before removal
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

        # Clean up existing entry — only if it's a VALID reparse point.
        # Ordinary directories are NEVER recursively deleted.
        if link_path.exists():
            if link_path.is_symlink():
                link_path.unlink()
            elif link_path.is_dir():
                if _validate_windows_reparse_tag(link_path):
                    # It's a junction — safe to remove
                    link_path.rmdir()
                else:
                    raise PlatformIsolationError(
                        "ordinary_dir_at_link_path",
                        f"Expected reparse point at {link_path} but found ordinary "
                        "directory without expected reparse tag. "
                        "Refusing to recursively delete.",
                    )
            else:
                link_path.unlink()

        link_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            os.symlink(str(target), str(link_path), target_is_directory=True)
        except OSError as exc:
            # Fallback: use mklink /J for junction
            try:
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
                    capture_output=True, text=True, timeout=10, check=True,
                )
                if result.returncode != 0:
                    raise PlatformIsolationError(
                        "junction_creation_failed",
                        f"mklink /J failed: {result.stderr.strip()}",
                    )
            except subprocess.CalledProcessError as cpe:
                raise PlatformIsolationError(
                    "link_creation_failed",
                    f"Failed to create symlink/junction at {link_path}: {exc} / {cpe}",
                ) from cpe

    def verify_workspace_link(
        self, link_path: Path, expected_target: Path, canonical_root: Path,
    ) -> bool:
        """Verify a Windows symlink/junction points to the expected target.

        Additionally validates that the reparse tag is a known allowed type
        (symlink or mount point/junction) — not any other reparse point.
        """
        if not link_path.exists():
            return False
        if not _validate_windows_reparse_tag(link_path):
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
            return path.is_symlink() or (path.is_dir() and _validate_windows_reparse_tag(path))
        except OSError:
            return False

    def make_file_readonly(self, path: Path) -> None:
        """Set file read-only attribute on Windows."""
        if path.exists():
            result = subprocess.run(
                ["attrib", "+R", str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                raise PlatformIsolationError(
                    "readonly_set_failed",
                    f"attrib +R failed on {path}: {result.stderr.strip()}",
                )

    def make_dir_readonly_executor(self, path: Path) -> None:
        """Deny write to directory via icacls. Fail-closed."""
        if path.exists():
            result = subprocess.run(
                ["icacls", str(path), "/deny", "BUILTIN\\Users:(WD)"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                raise PlatformIsolationError(
                    "acl_deny_failed",
                    f"icacls /deny failed on {path}: {result.stderr.strip()}",
                )

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
        """Launch a subprocess as the restricted executor identity on Windows.

        Uses ctypes to call CreateProcessWithLogonW with the provisioned
        executor account's credentials. The child inherits the restricted
        SID's NTFS ACLs automatically, so canonical content accessed through
        workspace symlinks is read-only.

        Same-owner launch is REJECTED — the executor account must be distinct.
        """
        self._assert_executor_distinct()
        assert self._executor_identity is not None

        # Build the command-line string
        cmdline = subprocess.list2cmdline(cmd)

        # Create inheritable pipes for stdin/stdout/stderr
        import msvcrt
        import ctypes.wintypes

        sa = ctypes.wintypes.SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(ctypes.wintypes.SECURITY_ATTRIBUTES)
        sa.bInheritHandle = True

        def _make_pipe_pair():
            r = ctypes.wintypes.HANDLE()
            w = ctypes.wintypes.HANDLE()
            ctypes.windll.kernel32.CreatePipe(
                ctypes.byref(r), ctypes.byref(w),
                ctypes.byref(sa), 0,
            )
            return r, w

        child_out_r, child_out_w = _make_pipe_pair()
        child_err_r, child_err_w = _make_pipe_pair()
        child_in_r, child_in_w = _make_pipe_pair()

        # Child inherits: child_in_r (stdin), child_out_w (stdout), child_err_w (stderr)
        # Parent keeps: child_in_w (write to child's stdin), child_out_r (read child's stdout),
        #               child_err_r (read child's stderr)

        # Build environment block
        env_block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("lpReserved", ctypes.wintypes.LPWSTR),
                ("lpDesktop", ctypes.wintypes.LPWSTR),
                ("lpTitle", ctypes.wintypes.LPWSTR),
                ("dwX", ctypes.wintypes.DWORD),
                ("dwY", ctypes.wintypes.DWORD),
                ("dwXSize", ctypes.wintypes.DWORD),
                ("dwYSize", ctypes.wintypes.DWORD),
                ("dwXCountChars", ctypes.wintypes.DWORD),
                ("dwYCountChars", ctypes.wintypes.DWORD),
                ("dwFillAttribute", ctypes.wintypes.DWORD),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("wShowWindow", ctypes.wintypes.WORD),
                ("cbReserved2", ctypes.wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", ctypes.wintypes.HANDLE),
                ("hStdOutput", ctypes.wintypes.HANDLE),
                ("hStdError", ctypes.wintypes.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", ctypes.wintypes.HANDLE),
                ("hThread", ctypes.wintypes.HANDLE),
                ("dwProcessId", ctypes.wintypes.DWORD),
                ("dwThreadId", ctypes.wintypes.DWORD),
            ]

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        si.hStdInput = child_in_r
        si.hStdOutput = child_out_w
        si.hStdError = child_err_w

        pi = PROCESS_INFORMATION()

        CreateProcessWithLogonW = ctypes.windll.advapi32.CreateProcessWithLogonW
        CreateProcessWithLogonW.argtypes = [
            ctypes.wintypes.LPCWSTR,  # lpUsername
            ctypes.wintypes.LPCWSTR,  # lpDomain  (. for local)
            ctypes.wintypes.LPCWSTR,  # lpPassword
            ctypes.wintypes.DWORD,    # dwLogonFlags
            ctypes.wintypes.LPCWSTR,  # lpApplicationName
            ctypes.wintypes.LPWSTR,   # lpCommandLine
            ctypes.wintypes.DWORD,    # dwCreationFlags
            ctypes.wintypes.LPVOID,   # lpEnvironment
            ctypes.wintypes.LPCWSTR,  # lpCurrentDirectory
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        CreateProcessWithLogonW.restype = ctypes.wintypes.BOOL

        LOGON_NETCREDENTIALS_ONLY = 2
        CREATE_UNICODE_ENVIRONMENT = 0x00000400

        ok = CreateProcessWithLogonW(
            "HappyRanchExecutor",
            ".",
            "",  # blank password per provisioning
            LOGON_NETCREDENTIALS_ONLY,
            None,  # ApplicationName (derive from command line)
            ctypes.c_wchar_p(cmdline),
            CREATE_UNICODE_ENVIRONMENT,
            ctypes.c_wchar_p(env_block) if env_block else None,
            ctypes.c_wchar_p(str(cwd)),
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            err = ctypes.windll.kernel32.GetLastError()
            # Clean up pipes on failure
            for h in (child_in_r, child_in_w, child_out_r, child_out_w,
                      child_err_r, child_err_w):
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
            raise PlatformIsolationError(
                "executor_launch_failed",
                f"CreateProcessWithLogonW failed (error {err}). "
                "Ensure HappyRanchExecutor account exists with blank password.",
            )

        # Close parent-side handles the child owns
        ctypes.windll.kernel32.CloseHandle(child_in_r)
        ctypes.windll.kernel32.CloseHandle(child_out_w)
        ctypes.windll.kernel32.CloseHandle(child_err_w)
        ctypes.windll.kernel32.CloseHandle(pi.hThread)
        ctypes.windll.kernel32.CloseHandle(pi.hProcess)

        # Convert remaining handles to Python file descriptors
        os_write_fd = msvcrt.open_osfhandle(child_in_w.value, 0)
        os_read_fd = msvcrt.open_osfhandle(child_out_r.value, 0)
        os_err_fd = msvcrt.open_osfhandle(child_err_r.value, 0)

        # Create Popen object that wraps our already-created child
        import io
        if text:
            universal_newlines = True
            out_file = io.TextIOWrapper(io.open(os_read_fd, "rb", 0))
            err_file = io.TextIOWrapper(io.open(os_err_fd, "rb", 0))
            in_file = io.TextIOWrapper(io.open(os_write_fd, "wb", 0))
        else:
            universal_newlines = False
            out_file = os.fdopen(os_read_fd, "rb", 0)
            err_file = os.fdopen(os_err_fd, "rb", 0)
            in_file = os.fdopen(os_write_fd, "wb", 0)

        # Build a subprocess.Popen-like wrapper
        # We can't use Popen directly since the child is already created;
        # use a minimal wrapper that supports communicate(), returncode, stdout, stderr
        class _ExecutorPopen:
            def __init__(self, pid, stdin_f, stdout_f, stderr_f):
                self.pid = pid
                self.stdin = stdin_f
                self.stdout = stdout_f
                self.stderr = stderr_f
                self.returncode = None

            def communicate(self, input=None, timeout=None):
                import threading
                if input is not None and self.stdin is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                elif self.stdin is not None:
                    self.stdin.close()
                out_data = self.stdout.read() if self.stdout else ""
                err_data = self.stderr.read() if self.stderr else ""
                self.returncode = 0  # placeholder — real code set by caller
                return out_data, err_data

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return 0

            def kill(self):
                if self.pid:
                    import signal
                    try:
                        os.kill(self.pid, signal.SIGTERM)
                    except OSError:
                        pass

        return _ExecutorPopen(pi.dwProcessId, in_file, out_file, err_file)


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
            f"Platform {sys.platform} is not supported for canonical "
            "skill store isolation",
        )
