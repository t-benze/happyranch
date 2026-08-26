"""POSIX platform operations for the canonical skill store.

Provides narrowly scoped platform operations for the canonical skill store
and workspace link architecture.

**SUPPORTED: macOS (darwin) and Linux.**
Windows and unknown platforms are NOT supported; attempts to use them fail
closed with an explicit error.

**Delivery model:**

The executor and daemon share the same OS identity. Linked,
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
from pathlib import Path

logger = logging.getLogger(__name__)


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

    Supported POSIX implementations provide:
    - Workspace symlink creation and validation
    - Executor process launch
    """

    @abstractmethod
    def create_relative_symlink(
        self, target: Path, link_path: Path, *, workspace_root: Path,
    ) -> None:
        """Create a validated relative symlink from *link_path* to *target*.

        **Containment contract (THR-190 PR-B):** the write MUST land inside
        the REAL workspace. Immediately before link creation/replacement the
        implementation enforces resolved-parent containment against
        *workspace_root*: every path component of ``link_path.parent`` below
        the resolved workspace root must be a genuine directory (no-follow
        admission — a symlinked provider root or nested skills root is a
        pre-positioned escape and fails closed), and the resolved parent must
        be inside the resolved workspace root. The link is created atomically
        (temporary symlink + ``os.replace``) through a no-follow, pinned
        parent directory fd so a concurrent swap of the parent path cannot
        redirect the write.

        Must fail closed if:
        - link_path is an ordinary directory (never rmtree)
        - link_path's parent chain is not contained in workspace_root
        - target is absolute or escapes the canonical store root
        - platform does not support symlinks
        """
        ...

    @abstractmethod
    def withdraw_workspace_link(
        self, link_path: Path, *, workspace_root: Path,
    ) -> None:
        """Remove a workspace link at *link_path* contained in *workspace_root*.

        Same no-follow admission as ``create_relative_symlink``: the parent
        chain below the resolved workspace root must be genuine directories
        (never symlinks) and the unlink is performed through a no-follow,
        pinned parent directory fd. Removes symlinks and regular files;
        refuses ordinary directories (never deletes their content).

        No-op when *link_path* does not exist.
        """
        ...

    @abstractmethod
    def admit_skills_directory(
        self, skills_dir: Path, *, workspace_root: Path,
    ) -> None:
        """No-follow admission of a provider/nested skills root.

        Verifies every EXISTING path component of *skills_dir* below the
        resolved workspace root is a genuine directory (lstat, no-follow). A
        symlink at any level — a pre-positioned workspace/provider/nested-
        skills path — raises ``PlatformIsolationError`` with code
        ``escaped_parent``. A missing component (fresh workspace, nothing
        admitted yet) is a no-op: later writes create real directories.
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


# ── Same-owner POSIX implementation ─────────────────────────────────


class _PosixSameOwnerIsolation(PlatformIsolation):
    """Shared same-owner implementation for supported POSIX platforms.

    The executor and daemon share the same OS identity — there is NO
    OS-level isolation. An agent-controlled executor process can
    read/write/chmod the canonical skill store and anything else the
    daemon account can reach.

    - Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal.
    - Do NOT claim OS-level isolation, immutable, or protected targets.
    - A same-UID process may mutate, race validation, and affect
      active/overlapping sessions.
    """

    # ── Containment helpers (THR-190 PR-B) ──────────────────────────
    # Threat principal: a SANDBOXED Codex/Pi agent with workspace-write can
    # pre-position symlinked workspace/provider/nested-skills paths (e.g.
    # <ws>/.claude, <ws>/.claude/skills, <ws>/.agents/skills, deeper
    # nested entries) that point outside the real workspace. On the next
    # session start the daemon's materializer must NOT follow those
    # pre-positioned parents when creating/replacing/withdrawing links —
    # doing so would write/unlink/replace files OUTSIDE the real workspace
    # under the daemon's identity. An unsandboxed Claude session (which
    # can already reach anything the daemon can) is NOT the defended
    # principal.
    #
    # Containment is enforced structurally HERE, at the actual lowest-level
    # link writer, immediately before each link creation/replacement — not
    # by a route manifest, caller convention, lexical-only check, or a
    # one-time earlier validation.

    def _resolve_contained_parent(
        self, link_path: Path, workspace_root: Path, *, create: bool,
    ) -> Path | None:
        """Resolve the REAL parent directory of *link_path*, contained in the workspace.

        Walks every path component of ``link_path.parent`` below the resolved
        workspace root with lstat (no-follow):
        - a symlink at ANY level below the workspace root (pre-positioned
          workspace/provider/nested-skills path) fails closed;
        - an existing component must be a genuine directory;
        - a missing component is created as a REAL directory when
          ``create=True``; when ``create=False`` a missing component means
          there is nothing to admit/withdraw and ``None`` is returned.

        The returned path is the fully-real parent (every component verified
        genuine below the resolved workspace root).
        """
        real_ws = workspace_root.resolve()
        if create:
            # Preserve prior mkdir(parents=True) behavior: a missing
            # workspace root is created as a genuine directory (the resolved
            # path's ancestors are daemon-owned).
            if not real_ws.is_dir():
                try:
                    real_ws.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise PlatformIsolationError(
                        "containment_root_invalid",
                        f"Cannot create workspace root {real_ws}: {exc}",
                    ) from exc
        elif not real_ws.is_dir():
            return None  # nothing to admit/withdraw below a missing root
        try:
            rel_parts = link_path.parent.relative_to(workspace_root).parts
        except ValueError:
            raise PlatformIsolationError(
                "link_outside_workspace",
                f"Link parent {link_path.parent} is not lexically inside "
                f"workspace {workspace_root}",
            ) from None
        cur = real_ws
        for part in rel_parts:
            cur = cur / part
            try:
                st = cur.lstat()
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    cur.mkdir()
                except FileExistsError:
                    pass  # a racing creator appeared — re-stat below
                st = cur.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise PlatformIsolationError(
                    "escaped_parent",
                    f"Symlink in link parent chain: {cur} — refusing to "
                    f"follow (no-follow admission). Link {link_path} cannot "
                    f"be created inside a symlinked workspace/provider/"
                    f"nested-skills path.",
                )
            if not stat.S_ISDIR(st.st_mode):
                raise PlatformIsolationError(
                    "escaped_parent",
                    f"Non-directory in link parent chain: {cur}",
                )
        # Belt-and-suspenders: the resolved parent must stay inside the
        # resolved workspace root (structural, after the no-follow walk).
        if not cur.resolve(strict=False).is_relative_to(real_ws):
            raise PlatformIsolationError(
                "escaped_parent",
                f"Resolved link parent {cur} escapes workspace {real_ws}",
            )
        return cur

    def _open_contained_parent(self, parent: Path) -> int:
        """Open *parent* (verified genuine) with a no-follow directory fd.

        The pinned fd keeps the write bound to the verified inode inside the
        real workspace even if the path is concurrently swapped for a
        symlink between admission and the write.
        """
        try:
            return os.open(
                str(parent),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise PlatformIsolationError(
                "escaped_parent",
                f"Cannot pin no-follow directory fd for {parent}: {exc}",
            ) from exc

    def create_relative_symlink(
        self, target: Path, link_path: Path, *, workspace_root: Path,
    ) -> None:
        """Create a validated relative symlink on a supported POSIX host.

        Validates:
        - target is not absolute (relative symlinks only)
        - target does not escape canonical root (no excessive ../ traversal)
        - resolved-parent containment inside the REAL workspace: no-follow
          admission of every parent component below the resolved workspace
          root, then an atomic tmp-symlink + ``os.replace`` through a
          no-follow pinned parent dirfd immediately before publication.

        **Safe repair:** existing entries are replaced atomically through the
        pinned dirfd. Ordinary directories are NEVER recursively deleted.
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

        # Resolved-parent containment: no-follow admission of every parent
        # component below the real workspace, creating missing real dirs.
        parent = self._resolve_contained_parent(
            link_path, workspace_root, create=True,
        )
        assert parent is not None  # create=True never returns None

        # SAFE REMOVAL / REPLACEMENT: only replace symlinks or files, NEVER
        # ordinary dirs — through the pinned no-follow parent dirfd.
        fd = self._open_contained_parent(parent)
        try:
            try:
                st = os.lstat(link_path.name, dir_fd=fd)
            except FileNotFoundError:
                st = None
            if (
                st is not None
                and stat.S_ISDIR(st.st_mode)
                and not stat.S_ISLNK(st.st_mode)
            ):
                raise PlatformIsolationError(
                    "ordinary_dir_at_link_path",
                    f"Expected symlink at {link_path} but found ordinary "
                    "directory. Refusing to recursively delete — remove "
                    "manually or use withdraw_skill first.",
                )

            # Atomic create-or-replace: readers see either the old complete
            # symlink or the new one, never a gap (a bare unlink + symlink
            # leaves OSError: Invalid argument to concurrent readers).
            tmp_name = ".tmp." + link_path.name
            try:
                os.unlink(tmp_name, dir_fd=fd)
            except FileNotFoundError:
                pass  # stale temp from a crashed prior materialization
            os.symlink(str(target), tmp_name, dir_fd=fd)
            try:
                os.replace(
                    tmp_name, link_path.name,
                    src_dir_fd=fd, dst_dir_fd=fd,
                )
            except OSError as exc:
                # Defend against an ordinary dir appearing at the final
                # component between the lstat above and the replace.
                try:
                    st2 = os.lstat(link_path.name, dir_fd=fd)
                except FileNotFoundError:
                    st2 = None
                if (
                    st2 is not None
                    and stat.S_ISDIR(st2.st_mode)
                    and not stat.S_ISLNK(st2.st_mode)
                ):
                    try:
                        os.unlink(tmp_name, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                    raise PlatformIsolationError(
                        "ordinary_dir_at_link_path",
                        f"Expected symlink at {link_path} but found ordinary "
                        "directory. Refusing to recursively delete.",
                    ) from exc
                raise PlatformIsolationError(
                    "link_creation_failed",
                    f"Failed to atomically replace symlink {link_path}: {exc}",
                ) from exc
        finally:
            os.close(fd)

    def withdraw_workspace_link(
        self, link_path: Path, *, workspace_root: Path,
    ) -> None:
        """Remove a workspace link, contained in the real workspace.

        No-follow admission of the parent chain; the unlink happens through
        a pinned no-follow parent dirfd. Removes symlinks and regular files;
        refuses ordinary directories. No-op when the entry is absent.
        """
        parent = self._resolve_contained_parent(
            link_path, workspace_root, create=False,
        )
        if parent is None:
            return  # parent chain absent — nothing to withdraw
        fd = self._open_contained_parent(parent)
        try:
            try:
                st = os.lstat(link_path.name, dir_fd=fd)
            except FileNotFoundError:
                return  # already gone
            if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                raise PlatformIsolationError(
                    "ordinary_dir_not_withdrawable",
                    f"Expected symlink at {link_path} but found ordinary "
                    f"directory — refusing to delete potentially valuable "
                    f"content",
                )
            os.unlink(link_path.name, dir_fd=fd)
        finally:
            os.close(fd)

    def admit_skills_directory(
        self, skills_dir: Path, *, workspace_root: Path,
    ) -> None:
        """No-follow admission of a provider/nested skills root.

        Every EXISTING component of *skills_dir* below the resolved workspace
        root must be a genuine directory; a symlink at any level raises
        ``escaped_parent``. A missing component (fresh workspace) is a no-op.
        """
        # Walk skills_dir itself (all components including the final one).
        real_ws = workspace_root.resolve()
        if not real_ws.is_dir():
            return  # nothing to admit below a missing workspace root
        try:
            rel_parts = skills_dir.relative_to(workspace_root).parts
        except ValueError:
            raise PlatformIsolationError(
                "link_outside_workspace",
                f"Skills dir {skills_dir} is not lexically inside workspace "
                f"{workspace_root}",
            ) from None
        cur = real_ws
        for part in rel_parts:
            cur = cur / part
            try:
                st = cur.lstat()
            except FileNotFoundError:
                return  # missing tail — nothing to admit yet
            if stat.S_ISLNK(st.st_mode):
                raise PlatformIsolationError(
                    "escaped_parent",
                    f"Symlink at skills root: {cur} — refusing to follow "
                    f"(no-follow admission). Provider/nested skills roots "
                    f"must be genuine directories inside the real workspace.",
                )
            if not stat.S_ISDIR(st.st_mode):
                raise PlatformIsolationError(
                    "escaped_parent",
                    f"Non-directory at skills root: {cur}",
                )

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
        """Launch a subprocess directly under the daemon's identity.

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
        except (OSError, subprocess.SubprocessError) as exc:
            raise PlatformIsolationError(
                "executor_launch_failed",
                f"Failed to launch executor process: {exc}",
            ) from exc


class _MacOSPlatformIsolation(_PosixSameOwnerIsolation):
    """macOS same-owner canonical-store operations."""


class _LinuxPlatformIsolation(_PosixSameOwnerIsolation):
    """Linux same-owner canonical-store operations.

    Requires native POSIX relative-symlink, same-directory ``os.replace``,
    chmod, and direct subprocess semantics. These primitives provide atomic
    publication and detection-only integrity checks; they do not isolate the
    canonical store from another process running under the daemon UID.
    """


# ── Detection ───────────────────────────────────────────────────────

# Canonical platform names for error messages
_SUPPORTED_PLATFORMS = frozenset({"darwin", "linux"})


def detect_platform_isolation() -> PlatformIsolation:
    """Detect and return the platform isolation implementation.

    macOS (darwin) and Linux use the explicit same-owner POSIX
    implementations. Windows and unknown platforms raise
    PlatformIsolationError with an explicit failure message; there is no
    fallback.
    """
    if sys.platform == "darwin":
        return _MacOSPlatformIsolation()
    if sys.platform == "linux":
        return _LinuxPlatformIsolation()
    raise PlatformIsolationError(
        "unsupported_platform",
        "Canonical skill store operations require macOS (darwin) or Linux. "
        f"Current platform '{sys.platform}' is not supported. "
        "Unsupported platforms fail closed — no fallback.",
    )
