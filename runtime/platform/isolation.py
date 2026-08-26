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
        pre-positioned escape and fails closed). Components are admitted
        and, where authorized, created RELATIVE to an already-pinned parent
        directory fd in a component-by-component walk rooted at a pinned
        no-follow fd for the REAL workspace root — never re-resolved or
        reopened through a full pathname after admission — and the final
        parent fd is retained through the entire mutation (temporary symlink
        + ``os.replace``), so a concurrent same-UID swap of ANY ancestor
        cannot redirect the write.

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

        Same component-by-component dirfd walk as
        ``create_relative_symlink``: the parent chain below the resolved
        workspace root is walked relative to pinned parent fds (never
        re-resolved through a full pathname) and the unlink is performed
        through the retained final parent fd. Removes symlinks and regular
        files; refuses ordinary directories (never deletes their content).

        No-op when *link_path* does not exist.
        """
        ...

    @abstractmethod
    def admit_skills_directory(
        self, skills_dir: Path, *, workspace_root: Path,
    ) -> int | None:
        """No-follow admission of a provider/nested skills root.

        Walks every EXISTING path component of *skills_dir* below the
        resolved workspace root in a component-by-component dirfd walk rooted
        at a pinned no-follow fd for the REAL workspace root (each component
        opened relative to its already-pinned parent fd). A symlink at any
        level — a pre-positioned workspace/provider/nested-skills path —
        raises ``PlatformIsolationError`` with code ``escaped_parent``.

        Returns the ADMITTED directory fd, retained OPEN so the caller can
        enumerate and mutate through the admitted inode without ever
        re-resolving the full pathname after admission — a same-UID swap of
        an already-admitted ancestor cannot redirect the caller's later
        enumeration/mutation. The caller OWNS the returned fd and must close
        it when done. Returns ``None`` when the directory (or an ancestor)
        is missing — a fresh workspace with nothing admitted yet.
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
    #
    # The walk is ROOTED AT A PINNED FD for the REAL workspace root: every
    # component below the root is admitted (and, where authorized, created)
    # relative to its ALREADY-PINNED parent fd with no-follow semantics, and
    # the final parent fd is retained through the entire mutation (mkdir,
    # stale temporary-parent/temp-link cleanup, temporary-symlink creation,
    # ``os.replace`` repair, withdrawal ``unlink``). A full pathname is never
    # re-resolved or reopened after admission, so a same-UID swap of any
    # already-admitted ancestor cannot redirect a later step.

    def _open_workspace_root(
        self, workspace_root: Path, *, create: bool,
    ) -> int | None:
        """Pin a no-follow directory fd for the REAL (resolved) workspace root.

        The returned fd anchors every subsequent component walk: no later
        pathname resolution can re-resolve or follow an ancestor below this
        pinned inode. Returns ``None`` when the root is missing and *create*
        is False (nothing to admit/withdraw below a missing root).
        """
        real_ws = workspace_root.resolve()
        if create:
            # A missing workspace root is created as a genuine directory (the
            # resolved path's ancestors are daemon-owned — outside the
            # defended window, which covers components BELOW the root).
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
            return os.open(
                str(real_ws),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise PlatformIsolationError(
                "containment_root_invalid",
                f"Cannot pin no-follow workspace-root fd for {real_ws}: {exc}",
            ) from exc

    def _walk_contained_components(
        self, ws_fd: int, rel_parts: tuple[str, ...], *, create: bool,
    ) -> int | None:
        """Admit/create *rel_parts* below the pinned workspace-root fd.

        Every component is opened RELATIVE to its already-pinned parent fd
        with no-follow semantics (``os.open(part, os.O_RDONLY |
        os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)``): a same-UID
        swap of any already-admitted ancestor cannot redirect the lookup,
        and a symlink at the component itself is refused (``ELOOP``). A
        missing component is created as a genuine directory with
        ``os.mkdir(part, dir_fd=parent_fd)`` when *create* is True;
        otherwise ``None`` is returned (nothing to admit/withdraw).

        The caller owns the returned final fd and MUST retain it through the
        entire mutation (link creation/replacement, ``os.replace`` repair,
        withdrawal ``unlink``) — the fd, not any pathname, binds the write
        to the admitted inode.

        Takes ownership of *ws_fd* on every path: on success the final fd is
        returned (all intermediate fds closed); on failure or a ``None``
        result every held fd is closed before returning.
        """
        cur_fd = ws_fd
        try:
            for part in rel_parts:
                try:
                    fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=cur_fd,
                    )
                except FileNotFoundError:
                    if not create:
                        try:
                            os.close(cur_fd)
                        except OSError:
                            pass
                        return None
                    try:
                        os.mkdir(part, dir_fd=cur_fd)
                    except FileExistsError:
                        pass  # a racing creator appeared — re-open below
                    try:
                        fd = os.open(
                            part,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=cur_fd,
                        )
                    except OSError as exc:
                        raise PlatformIsolationError(
                            "escaped_parent",
                            f"Cannot admit created component '{part}' below "
                            f"pinned parent: {exc}",
                        ) from exc
                except OSError as exc:
                    raise PlatformIsolationError(
                        "escaped_parent",
                        f"Symlink or non-directory at component '{part}' in "
                        f"link parent chain (no-follow admission): {exc}",
                    ) from exc
                try:
                    os.close(cur_fd)
                except OSError:
                    pass
                cur_fd = fd
            return cur_fd
        except BaseException:
            try:
                os.close(cur_fd)
            except OSError:
                pass
            raise

    def create_relative_symlink(
        self, target: Path, link_path: Path, *, workspace_root: Path,
    ) -> None:
        """Create a validated relative symlink on a supported POSIX host.

        Validates:
        - target is not absolute (relative symlinks only)
        - target does not escape canonical root (no excessive ../ traversal)
        - resolved-parent containment inside the REAL workspace: a
          component-by-component no-follow dirfd walk rooted at a pinned fd
          for the resolved workspace root (missing parents created as real
          dirs relative to the pinned parent), then an atomic tmp-symlink +
          ``os.replace`` through the retained final parent fd immediately
          before publication.

        **Safe repair:** existing entries are replaced atomically through the
        pinned final parent fd. Ordinary directories are NEVER recursively
        deleted.
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

        # Resolved-parent containment: component-by-component no-follow dirfd
        # walk rooted at a pinned fd for the REAL workspace root — every
        # parent component below the root is admitted (and, where authorized,
        # created) relative to its already-pinned parent fd; the final parent
        # fd is retained through the entire mutation below.
        ws_fd = self._open_workspace_root(workspace_root, create=True)
        assert ws_fd is not None  # create=True never returns None
        try:
            rel_parts = link_path.parent.relative_to(workspace_root).parts
        except ValueError:
            try:
                os.close(ws_fd)
            except OSError:
                pass
            raise PlatformIsolationError(
                "link_outside_workspace",
                f"Link parent {link_path.parent} is not lexically inside "
                f"workspace {workspace_root}",
            ) from None
        parent_fd = self._walk_contained_components(
            ws_fd, rel_parts, create=True,
        )
        assert parent_fd is not None  # create=True never returns None

        # SAFE REMOVAL / REPLACEMENT: only replace symlinks or files, NEVER
        # ordinary dirs — through the retained final parent fd.
        fd = parent_fd
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

        Component-by-component no-follow dirfd walk rooted at a pinned fd for
        the REAL workspace root; the unlink happens through the retained
        final parent fd. Removes symlinks and regular files; refuses ordinary
        directories. No-op when the entry is absent.
        """
        ws_fd = self._open_workspace_root(workspace_root, create=False)
        if ws_fd is None:
            return  # root missing — nothing to withdraw
        try:
            rel_parts = link_path.parent.relative_to(workspace_root).parts
        except ValueError:
            try:
                os.close(ws_fd)
            except OSError:
                pass
            raise PlatformIsolationError(
                "link_outside_workspace",
                f"Link parent {link_path.parent} is not lexically inside "
                f"workspace {workspace_root}",
            ) from None
        parent_fd = self._walk_contained_components(
            ws_fd, rel_parts, create=False,
        )
        if parent_fd is None:
            return  # parent chain absent — nothing to withdraw
        fd = parent_fd
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
    ) -> int | None:
        """No-follow admission of a provider/nested skills root.

        Every EXISTING component of *skills_dir* below the resolved workspace
        root is walked relative to pinned parent fds (never re-resolved
        through a full pathname); a symlink at any level raises
        ``escaped_parent``.

        Returns the ADMITTED directory fd, retained OPEN — the caller owns it
        and MUST retain it through enumeration/mutation (never re-resolve the
        full pathname after admission) and close it when done. Returns
        ``None`` when the directory (or an ancestor) is missing (fresh
        workspace — nothing to admit).
        """
        # Walk skills_dir itself (all components including the final one),
        # rooted at a pinned fd for the REAL workspace root.
        ws_fd = self._open_workspace_root(workspace_root, create=False)
        if ws_fd is None:
            return None  # nothing to admit below a missing workspace root
        try:
            rel_parts = skills_dir.relative_to(workspace_root).parts
        except ValueError:
            try:
                os.close(ws_fd)
            except OSError:
                pass
            raise PlatformIsolationError(
                "link_outside_workspace",
                f"Skills dir {skills_dir} is not lexically inside workspace "
                f"{workspace_root}",
            ) from None
        parent_fd = self._walk_contained_components(
            ws_fd, rel_parts, create=False,
        )
        if parent_fd is None:
            return None  # skills dir (or an ancestor) missing — nothing to admit
        return parent_fd  # caller owns the fd — retained through enumeration

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
