"""Same-host cooperative coordination for machine-global executor profiles.

The existing ``executor_profiles.yaml`` file remains the sole machine-global
profile store and the process-wide executor registry remains its cache.  U1A
installed profile relations in every org database, so this module uses those
relations as per-org dependency/operation mirrors and uses a stable owner-only
``flock`` file for the one genuinely machine-global serialization primitive.

Supported writers acquire profile lease(s) before any org publication gate.
No publication path acquires a profile lease, keeping the graph acyclic.  The
kernel releases a held flock on process death; arbitrary same-UID file/DB edits
remain outside this cooperative guarantee.
"""
from __future__ import annotations

from runtime.infrastructure.producer_admission import NativeWriterLifecycle, historical_disk_sweep
from runtime.orchestrator.producer_move import native_writer

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field
from contextvars import ContextVar

_move_batch_owner = ContextVar("profile_move_batch_owner", default=None)
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.workflows.authority import (
    ProfileFenceBinding,
    WorkflowAuthorityError,
    _SupportedWriterInterval,
    _pid_is_live,
)

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState


logger = logging.getLogger(__name__)

_LOCK_DIR_MODE = 0o700
_LOCK_FILE_MODE = 0o600
_LOCK_WAIT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.01


class ProfileCoordinatorError(RuntimeError):
    """Closed machine-readable profile coordination failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _EffectiveProfile:
    digest: str
    state: str
    resolvable: bool


@dataclass(frozen=True)
class _ProfileOperation:
    operation_id: str
    profile_name: str
    operation_kind: str
    members: tuple[str, ...]
    target_generation: int
    prior: _EffectiveProfile
    recovery_state: str = "captured"


@dataclass(frozen=True)
class _OrgDependencyCapture:
    desired: dict[str, str]
    profile_names: tuple[str, ...]
    revision: tuple
    snapshot: bytes | None
    empty_skeleton: bool = False
    empty_canonical_inputs: bool = False
    refusal: str | None = None
    effective_profiles: dict[str, _EffectiveProfile] = field(default_factory=dict)
    canonical_error: str | None = None
    effective_identity: tuple = ()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _dependency_profile(executor: str) -> str | None:
    profile = get_registry().get_profile(executor)
    return None if profile is not None and profile.kind == "builtin" else executor.lower()


class _ConsumerWriterInterval(_SupportedWriterInterval):
    """Own lifecycle dependency changes alongside the existing canonical fence."""

    def __init__(self, coordinator, *, org, publisher, consumer, executor, preserve, native_disposition):
        super().__init__(org.workflow_authority, publisher=publisher, native_disposition=native_disposition)
        self.profiles = coordinator
        self.org = org
        self.consumer = consumer
        self.executor = executor
        self.preserve = preserve
        self.owned_profiles: set[str] = set()
        self.initial_mirror = None
        self.initial_bytes = None
        self.initial_profiles = {}

    @contextmanager
    def canonical_change(self):
        paths = OrgPaths(root=self.org.root)
        # Bounded single-identity capture, before any profile/publication lease.
        before = prompt_loader.load_agent_snapshot(paths, self.consumer)
        pending = prompt_loader.load_pending_agent(paths, self.consumer) if before is None else None
        source = _dependency_profile(before[0].executor) if before is not None else None
        executor = (
            before[0].executor if self.preserve and before is not None
            else pending.executor if self.preserve and pending is not None
            else self.executor
        )
        target = _dependency_profile(executor) if executor is not None else None
        self.owned_profiles.update(name for name in (source, target) if name is not None)
        with self.profiles.dependency_writer(sorted(self.owned_profiles)):
            with self.org.workflow_authority._publisher_lock:
                fresh = prompt_loader.load_agent_snapshot(paths, self.consumer)
                fresh_pending = prompt_loader.load_pending_agent(paths, self.consumer) if before is None else None
                if fresh_pending != pending:
                    raise ProfileCoordinatorError("profile_consumer_changed")
                if (None if fresh is None else fresh[1]) != (None if before is None else before[1]):
                    raise ProfileCoordinatorError("profile_consumer_changed")
                mirror = self.profiles._dependency_mirror_snapshot(self.org)
                if self.initial_mirror is None:
                    self.initial_mirror = mirror
                    self.initial_bytes = None if before is None else before[2]
                    self.initial_profiles = {
                        name: (self.profiles.profile_digest(name), max(1, self.profiles._max_generation(name)))
                        for name in self.owned_profiles
                    }
                with super().canonical_change():
                    try:
                        if source != target:
                            self.profiles.rebind_consumer(
                                org=self.org, consumer_identity=self.consumer,
                                from_profile=source, to_profile=target,
                            )
                        yield
                        after = prompt_loader.load_agent_snapshot(paths, self.consumer)
                        actual = _dependency_profile(after[0].executor) if after is not None else None
                        if actual != target:
                            if actual is not None and actual not in self.owned_profiles:
                                raise ProfileCoordinatorError("profile_consumer_changed")
                            restored = (None if after is None else after[2]) == self.initial_bytes
                            globals_unchanged = all(
                                self.profiles.profile_digest(name) == digest
                                and self.profiles._max_generation(name) <= generation
                                for name, (digest, generation) in self.initial_profiles.items()
                            )
                            if restored and globals_unchanged:
                                self.profiles._restore_dependency_mirror(
                                    self.org, self.initial_mirror,
                                    profile_names=self.owned_profiles,
                                )
                            else:
                                self.profiles.rebind_consumer(
                                    org=self.org, consumer_identity=self.consumer,
                                    from_profile=target, to_profile=actual,
                                )
                    except BaseException:
                        self.profiles._restore_dependency_mirror(self.org, mirror)
                        raise


class ProfileCoordinator:
    """Coordinate global profile writers with all dependent organizations."""

    def __init__(
        self,
        *,
        daemon_home: Path,
        orgs: dict[str, OrgState],
        lock_wait_seconds: float = _LOCK_WAIT_SECONDS,
        capture_orgs: Callable | None = None,
        hold_startup: Callable | None = None,
    ) -> None:
        self._daemon_home = Path(daemon_home)
        self.orgs = orgs
        self._attachment_capture = capture_orgs
        self._lock_wait_seconds = lock_wait_seconds
        self._hold_startup = hold_startup
        self._startup_holds: dict[str, str] = {}
        self._startup_hold_profiles: set[str] = set()
        self._startup_unknown = False

    @contextmanager
    def _capture_orgs(self) -> Iterator[dict[str, OrgState]]:
        if self._attachment_capture is None:
            # Standalone coordinators retain their original caller-owned map.
            yield dict(self.orgs)
        else:
            with self._attachment_capture() as orgs:
                yield orgs

    def assert_org_admission(self, slug: str) -> None:
        # Process-held membership is checked before a retained object's DB/map.
        if slug in self._startup_holds:
            raise WorkflowAuthorityError("authority_pointer_not_ready")

    @property
    def _lock_dir(self) -> Path:
        return self._daemon_home / "profile-coordinator-locks"

    def _lock_path(self, profile_name: str) -> Path:
        key = hashlib.sha256(profile_name.lower().encode("utf-8")).hexdigest()
        return self._lock_dir / f"{key}.lock"

    @contextmanager
    def _profile_lease(
        self,
        profile_name: str,
        *,
        wait: bool,
    ) -> Iterator[None]:
        """Acquire one stable cross-process flock; never unlink its inode."""
        lock_dir = self._lock_dir
        lock_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(lock_dir, _LOCK_DIR_MODE)
        path = self._lock_path(profile_name)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd: int | None = None
        handed_off = False
        try:
            fd = os.open(path, flags, _LOCK_FILE_MODE)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ProfileCoordinatorError("profile_coordinator_lock_invalid")
            os.fchmod(fd, _LOCK_FILE_MODE)
            deadline = time.monotonic() + self._lock_wait_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    # A native attachment can own this lease while its loop
                    # remains serving. A synchronous loop caller must use the
                    # existing busy refusal, never sleep behind that worker.
                    try:
                        asyncio.get_running_loop()
                    except RuntimeError:
                        on_loop = False
                    else:
                        on_loop = True
                    if on_loop or not wait or time.monotonic() >= deadline:
                        raise ProfileCoordinatorError(
                            "profile_coordinator_busy"
                        ) from exc
                    time.sleep(_LOCK_POLL_SECONDS)
            handed_off = True
            yield
        except ProfileCoordinatorError:
            raise
        except OSError as exc:
            if handed_off:
                raise
            raise ProfileCoordinatorError("profile_coordinator_unavailable") from exc
        finally:
            if fd is not None:
                os.close(fd)

    @contextmanager
    def _profile_leases(
        self,
        profile_names: Sequence[str],
        *,
        wait: bool,
    ) -> Iterator[tuple[str, ...]]:
        names = tuple(sorted({name.strip().lower() for name in profile_names if name.strip()}))
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(self._profile_lease(name, wait=wait))
            yield names

    @contextmanager
    def profile_read(self, profile_name: str) -> Iterator[None]:
        """Serialize a paired durable/cache read against profile mutation."""
        with self._profile_lease(profile_name, wait=False):
            yield

    @contextmanager
    def dependency_writer(self, profile_names: Sequence[str]) -> Iterator[None]:
        """Serialize a supported consumer rebind before its org writer gate."""
        with self._profile_leases(profile_names, wait=True) as names:
            for name in names or (None,):
                self._assert_no_active_operation(name)
            yield

    @asynccontextmanager
    async def consumer_writer(self, *, org, publisher, consumer, executor=None, preserve=False, teams=()):
        """Serialize async roster writers without leasing scans or awaited work.

        The coroutine serialization lock is never acquired by global profile
        operations. Each synchronous canonical segment separately takes profile
        leases before the org publisher gate. Publication/compensation capture
        runs only after that segment has released all profile leases.
        """
        with native_writer(org.db, publisher=publisher, agents=(consumer,), teams=teams) as disposition:
            org.workflow_authority._profile_coordinator = self
            async with org.workflow_authority._async_writer_lock:
                interval = _ConsumerWriterInterval(
                    self, org=org, publisher=publisher, consumer=consumer,
                    executor=executor, preserve=preserve,
                    native_disposition=disposition,
                )
                try:
                    yield interval
                except BaseException:
                    if interval.fenced:
                        if not org.workflow_authority.publish_after_supported_change(
                            publisher=f"{publisher}:compensation",
                            fence_journal_id=interval.fence_journal_id,
                            publisher_invocation=interval.publisher_invocation,
                        ):
                            disposition.uncertain = True
                    raise
                else:
                    if interval.fenced:
                        if not org.workflow_authority.publish_after_supported_change(
                            publisher=publisher, fence_journal_id=interval.fence_journal_id,
                            publisher_invocation=interval.publisher_invocation,
                        ):
                            disposition.uncertain = True

    def validate_requirements(self, definitions, projection) -> None:
        desired = {
            definition["name"]: profile
            for definition in definitions if definition["status"] == "active"
            if (profile := _dependency_profile(definition["executor"])) is not None
        }
        actual = {
            consumer: entry["profile_name"]
            for entry in projection for consumer in entry["consumers"]
        }
        if desired != actual:
            raise ProfileCoordinatorError("profile_dependency_incoherent")

    def _effective_profile(self, profile_name: str) -> _EffectiveProfile:
        profiles = load_runtime_profiles()
        registry = get_registry()
        config = profiles.get(profile_name)
        if config is None:
            # Preserve the existing in-process registry seam used by callers
            # that install an already-validated custom profile directly.  It
            # is deliberately only a compatibility projection: a restart
            # still requires the durable runtime profile store.
            registered = registry.get_profile(profile_name)
            if registered is not None and registered.kind != "builtin":
                return _EffectiveProfile(
                    digest=_digest(asdict(registered)),
                    state="active",
                    resolvable=(
                        registry._resolve_custom_adapter_eligibility(registered)
                        is not None
                    ),
                )
            return _EffectiveProfile(
                digest=_digest({"profile_name": profile_name, "state": "removed"}),
                state="removed",
                resolvable=False,
            )
        profile = registry.get_profile(profile_name)
        resolvable = (
            profile is not None
            and (
                profile.kind == "builtin"
                or registry._resolve_custom_adapter_eligibility(profile) is not None
            )
        )
        return _EffectiveProfile(
            digest=_digest(config),
            state="active",
            resolvable=resolvable,
        )

    def profile_digest(self, profile_name: str) -> str:
        """Return the global digest used by production closure coherence."""
        return self._effective_profile(profile_name).digest

    @staticmethod
    def _effective_file_identity(path: Path) -> tuple:
        """Re-read identity only; never load YAML or hash executables in leases."""
        def identity(metadata: os.stat_result) -> tuple:
            return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
                    metadata.st_uid, metadata.st_gid, metadata.st_nlink,
                    metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

        try:
            link = path.lstat()
        except FileNotFoundError:
            return (None, None, False)
        try:
            target = path.stat()
        except FileNotFoundError:
            return (identity(link), None, False)
        return (identity(link), identity(target), os.access(path, os.X_OK))

    def _effective_input_identity(self, names: Sequence[str], executables: Sequence[Path]) -> tuple:
        from runtime.orchestrator.adapter_store import _store_path as adapter_store_path
        from runtime.orchestrator.runtime_executor_store import _store_path as profile_store_path
        registry = get_registry()
        registered = _canonical_bytes({
            name: None if (profile := registry.get_profile(name)) is None else asdict(profile)
            for name in names
        })
        paths = (profile_store_path(), adapter_store_path(), *executables)
        return (id(registry), registered,
                tuple((str(path), self._effective_file_identity(path)) for path in paths))

    def _capture_effective_profiles(self, names: Sequence[str]) -> tuple[dict[str, _EffectiveProfile], tuple]:
        """Bookend every global input to the existing effective projection."""
        if not names:
            return {}, ()
        from runtime.orchestrator.adapter_store import load_adapters
        # Store/registry discovery itself is bracketed before discovering the
        # selected executable paths, including currently ineligible adapters.
        stores = self._effective_input_identity(names, ())
        adapters = load_adapters()
        registry = get_registry()
        executables = set()
        for name in names:
            profile = registry.get_profile(name)
            command = "" if profile is None else profile.command_adapter_id or ""
            if profile is not None and profile.kind != "builtin" and command.startswith("custom-adapter:"):
                entry = adapters.get(command[len("custom-adapter:"):])
                if entry is not None:
                    executables.add(Path(entry.executable))
        paths = tuple(sorted(executables))
        before = self._effective_input_identity(names, paths)
        if stores != (before[0], before[1], before[2][:2]):
            raise ProfileCoordinatorError("profile_consumer_changed")
        effective = {name: self._effective_profile(name) for name in names}
        if before != self._effective_input_identity(names, paths):
            raise ProfileCoordinatorError("profile_consumer_changed")
        return effective, (tuple(names), paths, before)

    def _validate_effective_inputs(self, identity: tuple) -> None:
        # Caller owns ALL selected profiles, before any org/durable lease. A
        # cross-process store replacement is visible even if this registry did
        # not change and the org has never joined the published inventory.
        if identity:
            names, paths, expected = identity
            if self._effective_input_identity(names, paths) != expected:
                raise ProfileCoordinatorError("profile_consumer_changed")

    @staticmethod
    @contextmanager
    def _transaction(org: OrgState) -> Iterator[sqlite3.Connection]:
        with org.db._lock:
            conn = org.db._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                try:
                    conn.commit()
                except BaseException:
                    # This context owns the transaction; do not leak a failed commit.
                    conn.rollback()
                    raise

    def _max_generation(self, profile_name: str) -> int:
        with self._capture_orgs() as orgs:
            generation = 0
            for org in orgs.values():
                with org.db._lock:
                    row = org.db._conn.execute(
                        "SELECT generation FROM workflow_profile_store "
                        "WHERE profile_name=?",
                        (profile_name,),
                    ).fetchone()
                if row is not None:
                    generation = max(generation, int(row["generation"]))
            return generation

    def _required_members(self, profile_name: str) -> tuple[str, ...]:
        with self._capture_orgs() as orgs:
            members: list[str] = []
            for slug, org in sorted(orgs.items()):
                with org.db._lock:
                    row = org.db._conn.execute(
                        "SELECT 1 FROM workflow_profile_dependencies "
                        "WHERE org_namespace=? AND profile_name=? "
                        "AND state IN ('active','unbound') LIMIT 1",
                        (org.workflow_authority.namespace, profile_name),
                    ).fetchone()
                if row is not None:
                    members.append(slug)
            return tuple(members)

    def _assert_no_active_operation(
        self, profile_name: str | None, *, move_org=None, attaching_org=None, captured_orgs=None,
    ) -> None:
        """Check native DB owners only; diagnostic discovery owns no leases.

        Ordinary writers retain diagnostic holds until successful native org
        attachment. Its already-open DB is revalidated here before mutation;
        only add_org removes the diagnostic after joining the shared org map.
        A None profile still checks diagnostics for EMPTY dependency sets.
        """
        if (self._startup_unknown
                or (self._startup_holds and profile_name is None
                    and (captured_orgs is None or self._startup_holds.keys() & set(captured_orgs)))
                or profile_name in self._startup_hold_profiles):
            raise ProfileCoordinatorError("profile_consumer_changed")
        with self._capture_orgs() as orgs:
            diagnostics = getattr(self, "_move_diagnostic_orgs", {})
            admitted = _move_batch_owner.get()
            for slug, root in tuple(diagnostics.items()):
                owner = move_org if move_org is not None else attaching_org
                if owner is None or slug != owner.slug or root != owner.root:
                    raise ProfileCoordinatorError("profile_team_move_diagnostic_org_held")
                # Discovery/strict path resolution/open/close already belong to the
                # outside-lease startup/attachment owner. Never reacquire a DB or
                # erase a diagnostic based on absence observed by a profile writer.
                with owner.db._lock:
                    row = owner.db._team_move_coordinator.active_uncommitted()
                if move_org is not None:
                    if row is None or admitted != (slug, row["operation_id"]):
                        raise ProfileCoordinatorError("profile_team_move_diagnostic_org_held")
                elif row is not None:
                    raise ProfileCoordinatorError("profile_team_move_diagnostic_org_held")
            if profile_name is None:
                return
            for org in orgs.values():
                with org.db._lock:
                    row = org.db._conn.execute(
                        "SELECT state FROM workflow_profile_operations "
                        "WHERE profile_name=? AND state NOT IN ('published','aborted') "
                        "ORDER BY rowid DESC LIMIT 1",
                        (profile_name,),
                    ).fetchone()
                if row is not None:
                    raise ProfileCoordinatorError(
                        f"profile_operation_in_progress:{row['state']}"
                    )

    def _insert_operation_rows(
        self,
        operation: _ProfileOperation,
        *,
        coordinator_invocation: str,
    ) -> None:
        with self._capture_orgs() as orgs:
            members_json = _canonical_bytes(list(operation.members)).decode("utf-8")
            created_at = datetime.now(timezone.utc).isoformat()
            for slug in operation.members:
                org = orgs[slug]
                with self._transaction(org) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO workflow_profile_operations("
                        "id,profile_name,operation_kind,captured_members,"
                        "target_generation,state,profile_digest,"
                        "coordinator_invocation,compensation_generation,created_at"
                        ") VALUES (?,?,?,?,?,'captured',?,?,0,?)",
                        (
                            operation.operation_id,
                            operation.profile_name,
                            operation.operation_kind,
                            members_json,
                            operation.target_generation,
                            operation.prior.digest,
                            coordinator_invocation,
                            created_at,
                        ),
                    )
                    # The kernel flock is the authoritative live-owner test.  A
                    # surviving diagnostic PID can have been recycled after a
                    # crash, so it must not override the already-acquired flock.
                    conn.execute(
                        "INSERT INTO workflow_profile_leases(profile_name,owner_token,owner_pid) "
                        "VALUES (?,?,?) ON CONFLICT(profile_name) DO UPDATE SET "
                        "owner_token=excluded.owner_token,owner_pid=excluded.owner_pid",
                        (operation.profile_name, coordinator_invocation, os.getpid()),
                    )

    def _set_operation_state(
        self,
        operation: _ProfileOperation,
        state: str,
        *,
        profile_digest: str | None = None,
    ) -> None:
        with self._capture_orgs() as orgs:
            for slug in operation.members:
                org = orgs[slug]
                with self._transaction(org) as conn:
                    if profile_digest is None:
                        conn.execute(
                            "UPDATE workflow_profile_operations SET state=? WHERE id=?",
                            (state, operation.operation_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE workflow_profile_operations "
                            "SET state=?,profile_digest=? WHERE id=?",
                            (state, profile_digest, operation.operation_id),
                        )

    def _release_diagnostic_leases(
        self,
        operations: Sequence[_ProfileOperation],
        *,
        coordinator_invocation: str,
    ) -> bool:
        with self._capture_orgs() as orgs:
            released = True
            for operation in operations:
                for slug in operation.members:
                    org = orgs[slug]
                    try:
                        with self._transaction(org) as conn:
                            conn.execute(
                                "DELETE FROM workflow_profile_leases "
                                "WHERE profile_name=? AND owner_token=?",
                                (operation.profile_name, coordinator_invocation),
                            )
                    except Exception:
                        released = False
                        # This row is diagnostic only; the kernel flock is the
                        # ownership authority and is still released by the outer
                        # context. Startup will overwrite a stale diagnostic row.
                        logger.exception(
                            "profile diagnostic lease cleanup failed org=%s profile=%s",
                            slug,
                            operation.profile_name,
                        )
            return released

    def _apply_store_state(
        self,
        operation: _ProfileOperation,
        effective: _EffectiveProfile,
    ) -> None:
        with self._capture_orgs() as orgs:
            for slug in operation.members:
                org = orgs[slug]
                with self._transaction(org) as conn:
                    existing = conn.execute(
                        "SELECT generation FROM workflow_profile_store "
                        "WHERE profile_name=?",
                        (operation.profile_name,),
                    ).fetchone()
                    if existing is not None and int(existing["generation"]) > operation.target_generation:
                        raise ProfileCoordinatorError("profile_generation_stale")
                    conn.execute(
                        "INSERT INTO workflow_profile_store("
                        "profile_name,generation,profile_digest,state) VALUES (?,?,?,?) "
                        "ON CONFLICT(profile_name) DO UPDATE SET "
                        "generation=excluded.generation,"
                        "profile_digest=excluded.profile_digest,state=excluded.state",
                        (
                            operation.profile_name,
                            operation.target_generation,
                            effective.digest,
                            effective.state,
                        ),
                    )
                    if effective.state == "removed":
                        conn.execute(
                            "UPDATE workflow_profile_dependencies SET state='unbound' "
                            "WHERE profile_name=? AND state='active'",
                            (operation.profile_name,),
                        )
                    else:
                        conn.execute(
                            "UPDATE workflow_profile_dependencies "
                            "SET bound_generation=?,state='active' "
                            "WHERE profile_name=? AND state IN ('active','unbound')",
                            (operation.target_generation, operation.profile_name),
                        )
                    conn.execute(
                        "UPDATE workflow_profile_operations "
                        "SET state='store_committed',profile_digest=? WHERE id=?",
                        (effective.digest, operation.operation_id),
                    )

    def _apply_registry_state(
        self,
        operation: _ProfileOperation,
        effective: _EffectiveProfile,
    ) -> bool:
        with self._capture_orgs() as orgs:
            publishable = effective.state == "removed" or effective.resolvable
            for slug in operation.members:
                org = orgs[slug]
                with self._transaction(org) as conn:
                    if publishable:
                        conn.execute(
                            "INSERT INTO workflow_profile_registry("
                            "profile_name,published_generation) VALUES (?,?) "
                            "ON CONFLICT(profile_name) DO UPDATE SET "
                            "published_generation=excluded.published_generation",
                            (operation.profile_name, operation.target_generation),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM workflow_profile_registry WHERE profile_name=?",
                            (operation.profile_name,),
                        )
            return publishable

    def _closure_coherent(self, org: OrgState, *, effective_profiles: dict[str, _EffectiveProfile] | None = None) -> bool:
        with org.db._lock:
            rows = org.db._conn.execute(
                "SELECT d.profile_name,d.state,d.bound_generation,s.generation,"
                "s.profile_digest,s.state AS store_state,r.published_generation "
                "FROM workflow_profile_dependencies d "
                "LEFT JOIN workflow_profile_store s "
                "ON s.profile_name=d.profile_name "
                "LEFT JOIN workflow_profile_registry r "
                "ON r.profile_name=d.profile_name "
                "WHERE d.org_namespace=? AND d.state IN ('active','unbound')",
                (org.workflow_authority.namespace,),
            ).fetchall()
        for row in rows:
            profile_name = str(row["profile_name"])
            effective = (self._effective_profile(profile_name) if effective_profiles is None
                         else effective_profiles.get(profile_name))
            if effective is None:
                raise ProfileCoordinatorError("profile_consumer_changed")
            if not (
                row["state"] == "active"
                and row["store_state"] == "active"
                and row["generation"] is not None
                and int(row["generation"]) == int(row["bound_generation"])
                and row["published_generation"] is not None
                and int(row["published_generation"])
                == int(row["bound_generation"])
                and row["profile_digest"] == effective.digest
                and effective.resolvable
            ):
                return False
        return True

    def _republish(
        self,
        *,
        operations: Sequence[_ProfileOperation],
        bindings: dict[str, ProfileFenceBinding],
        publisher: str,
        expected_fenced: set[str],
        effective_profiles: dict[str, _EffectiveProfile] | None = None,
    ) -> bool:
        with self._capture_orgs() as orgs:
            all_published = True
            for slug in sorted(expected_fenced):
                org = orgs[slug]
                if not self._closure_coherent(org, effective_profiles=effective_profiles):
                    all_published = False
                    continue
                try:
                    org.workflow_authority.publish_profile_change(
                        publisher=publisher,
                        binding=bindings[slug],
                    )
                except Exception:
                    all_published = False
                    logger.exception(
                        "profile dependent republish failed org=%s publisher=%s",
                        slug,
                        publisher,
                    )
            if not all_published:
                for operation in operations:
                    self._set_operation_state(operation, "forward_recovery_required")
            return all_published

    def _finalize(
        self,
        *,
        operations: Sequence[_ProfileOperation],
        bindings: dict[str, ProfileFenceBinding],
        publisher: str,
        mutation_succeeded: bool,
        effective_profiles: dict[str, _EffectiveProfile] | None = None,
    ) -> bool:
        fenced = set(bindings)
        all_resolved = True
        changed_operations: list[_ProfileOperation] = []
        unchanged_operations: list[_ProfileOperation] = []
        for operation in operations:
            effective = (self._effective_profile(operation.profile_name) if effective_profiles is None
                         else effective_profiles[operation.profile_name])
            changed = (
                effective.digest != operation.prior.digest
                or operation.recovery_state
                in {"store_committed", "published", "forward_recovery_required"}
            )
            if changed:
                self._apply_store_state(operation, effective)
                publishable = self._apply_registry_state(operation, effective)
                if not publishable:
                    all_resolved = False
                    self._set_operation_state(
                        operation,
                        "forward_recovery_required",
                        profile_digest=effective.digest,
                    )
                changed_operations.append(operation)
            else:
                # Keep the operation discoverable until every captured org is
                # either coherently republished or truthfully left fenced.
                unchanged_operations.append(operation)

        republished = self._republish(
            operations=operations,
            bindings=bindings,
            publisher=publisher if mutation_succeeded else f"{publisher}:compensation",
            expected_fenced=fenced,
            effective_profiles=effective_profiles,
        )
        if republished:
            for operation in changed_operations:
                effective = (self._effective_profile(operation.profile_name) if effective_profiles is None
                             else effective_profiles[operation.profile_name])
                if effective.state == "removed" or effective.resolvable:
                    self._set_operation_state(
                        operation, "published", profile_digest=effective.digest,
                    )
            for operation in unchanged_operations:
                self._set_operation_state(operation, "aborted")
        return republished and all_resolved

    @contextmanager
    def _operation_with_leases_held(
        self,
        names: Sequence[str],
        *,
        operation_kind: str,
        publisher: str,
    ) -> Iterator[None]:
        """Create the durable U1A claim after profile leases are held."""
        with self._capture_orgs() as orgs:
            coordinator_invocation = uuid.uuid4().hex
            operations = tuple(
                _ProfileOperation(
                    operation_id=f"WPO-{uuid.uuid4().hex}",
                    profile_name=name,
                    operation_kind=operation_kind,
                    members=self._required_members(name),
                    target_generation=self._max_generation(name) + 1,
                    prior=self._effective_profile(name),
                )
                for name in names
            )
            members = sorted({slug for op in operations for slug in op.members})
            for slug in members:
                org = orgs[slug]
                with org.db._lock:
                    active = org.workflow_authority._active_journal(org.db._conn, org.workflow_authority.namespace)
                if active is not None and not str(active["publisher_invocation"]).startswith("profile-coordinator:"):
                    raise ProfileCoordinatorError("profile_consumer_changed")
            with ExitStack() as move_writers:
                dispositions = []
                try:
                    for slug in members:
                        dispositions.append(move_writers.enter_context(native_writer(orgs[slug].db,
                            publisher=f'profile:{publisher}')))
                except Exception as exc:
                    # Earlier acquired reservations have not entered any profile
                    # operation/fence/body. Only those completed reservations may
                    # settle; a failing reservation retains its own native evidence.
                    for disposition in dispositions:
                        disposition.no_effect_error = exc
                    raise
                for operation in operations:
                    self._insert_operation_rows(
                        operation,
                        coordinator_invocation=coordinator_invocation,
                    )
                bindings: dict[str, ProfileFenceBinding] = {}
                with ExitStack() as org_stack:
                    for slug in members:
                        bindings[slug] = org_stack.enter_context(
                            orgs[slug].workflow_authority.profile_change_interval(
                                reason=f"profile:{','.join(names)}:{publisher}",
                                coordinator_invocation=coordinator_invocation,
                            )
                        )
                    for operation in operations:
                        self._set_operation_state(operation, "fenced")
                    try:
                        yield
                    except BaseException:
                        try:
                            reconciled = self._finalize(
                                operations=operations,
                                bindings=bindings,
                                publisher=publisher,
                                mutation_succeeded=False,
                            )
                            if not reconciled:
                                for disposition in dispositions:
                                    disposition.uncertain = True
                        except Exception:
                            for disposition in dispositions:
                                disposition.uncertain = True
                            logger.exception(
                                "profile compensation publication deferred publisher=%s",
                                publisher,
                            )
                        raise
                    else:
                        try:
                            reconciled = self._finalize(
                                operations=operations,
                                bindings=bindings,
                                publisher=publisher,
                                mutation_succeeded=True,
                            )
                            if not reconciled:
                                for disposition in dispositions:
                                    disposition.uncertain = True
                        except Exception:
                            for disposition in dispositions:
                                disposition.uncertain = True
                            # The existing writer already committed. Preserve its
                            # response contract while the durable operation/fence
                            # remains discoverable for cold forward recovery.
                            logger.exception(
                                "profile post-commit publication deferred publisher=%s",
                                publisher,
                            )
                    finally:
                        released = self._release_diagnostic_leases(
                            operations,
                            coordinator_invocation=coordinator_invocation,
                        )
                        if not released:
                            for disposition in dispositions:
                                disposition.uncertain = True

    @contextmanager
    def operation(
        self,
        profile_names: Sequence[str],
        *,
        operation_kind: str,
        publisher: str,
    ) -> Iterator[None]:
        """Fence dependents, run one existing writer, then publish coherently."""
        if operation_kind not in {"register", "rebind", "remove"}:
            raise ProfileCoordinatorError("profile_operation_kind_invalid")
        with self._profile_leases(profile_names, wait=False) as names:
            for name in names or (None,):
                self._assert_no_active_operation(name)
            if not names:
                yield
                return
            with self._operation_with_leases_held(
                names,
                operation_kind=operation_kind,
                publisher=publisher,
            ):
                yield

    @contextmanager
    def claimed_operation(
        self,
        profile_names: Sequence[str],
        *,
        operation_kind: str,
        publisher: str,
        terminal_check: Callable[[], bool],
    ) -> Iterator[bool]:
        """Claim a retryable writer once, before any U1A fence or mutation.

        The stable profile flock serializes independent processes. After it is
        acquired, the durable terminal check lets a loser return the winner's
        outcome without creating a second operation row, fence, or publication.
        A winner then records the existing U1A operation/lease claim before the
        caller can mutate adapter or profile state.
        """
        if operation_kind not in {"register", "rebind", "remove"}:
            raise ProfileCoordinatorError("profile_operation_kind_invalid")
        with self._profile_leases(profile_names, wait=True) as names:
            for name in names or (None,):
                self._assert_no_active_operation(name)
            if not names:
                yield True
                return
            if terminal_check():
                yield False
                return
            with self._operation_with_leases_held(
                names,
                operation_kind=operation_kind,
                publisher=publisher,
            ):
                yield True

    def _roster_revision(self, org: OrgState) -> tuple:
        """Complete DB/native bookend; no discovery or filesystem I/O."""
        from runtime.infrastructure.producer_admission import NativeWriterStore, native_owner_uncommitted
        authority = org.workflow_authority
        with org.db._lock:
            conn = org.db._conn
            pointer = authority._pointer(conn, authority.namespace)
            active = authority._active_journal(conn, authority.namespace)
            leases = tuple(tuple(r) for r in conn.execute(
                "SELECT namespace,owner_token,owner_pid FROM workflow_publication_leases ORDER BY namespace"
            ))
            if any(not isinstance(r[1], str) or not r[1] or type(r[2]) is not int or r[2] <= 0 for r in leases):
                raise ProfileCoordinatorError("profile_consumer_changed")
            live = any(r[0] == authority.namespace and _pid_is_live(r[2]) for r in leases)
            operations = tuple(tuple(r) for r in conn.execute("SELECT * FROM workflow_profile_operations ORDER BY id"))
            journals = tuple(tuple(r) for r in conn.execute("SELECT * FROM workflow_publication_journals ORDER BY id"))
            history = NativeWriterStore(org.db, org.slug).history_uncommitted()
            native = native_owner_uncommitted(org.db)
            writers = () if native is None else (native.writer_epoch, _canonical_bytes(native.writers))
            return (pointer, None if active is None else tuple(active), live, leases,
                    self._dependency_mirror_snapshot(org), operations, journals,
                    tuple((key, value.model_dump_json()) for key, value in sorted(history.items())),
                    writers, (id(org), org.slug, org.root, id(org.db), org.db.db_path, id(authority)),
                    tuple(tuple(r) for r in conn.execute("SELECT * FROM workflow_profile_leases ORDER BY profile_name")),
                    tuple(tuple(r) for r in conn.execute("SELECT id,task_id,agent,payload FROM audit_log "
                                                        "WHERE action='team_move_native_writer' ORDER BY id")))

    def _capture_org_dependencies(self, org: OrgState, *, completed_writer_invocation: str | None = None,
                                  attachment: bool = False) -> _OrgDependencyCapture:
        capture = self._observe_org_dependencies(org, completed_writer_invocation=completed_writer_invocation,
                                                 attachment=attachment)
        if capture.refusal:
            error = ProfileCoordinatorError(capture.refusal)
            error.capture = capture
            raise error
        return capture

    def _observe_org_dependencies(self, org: OrgState, *, completed_writer_invocation: str | None = None,
                                  attachment: bool = False) -> _OrgDependencyCapture:
        for _attempt in range(3):
            before = self._roster_revision(org)
            async_writer = org.workflow_authority._async_writer_lock.locked()
            paths = OrgPaths(root=org.root)
            definitions = list(prompt_loader.list_agents(paths))
            # The default skeleton has no canonical consumers of either status.
            # Discover both statuses and teams only outside every durable lease
            # and transaction; empty dependency rows cannot establish this fact.
            empty_inputs = attachment and not (
                definitions or prompt_loader.list_pending(paths)
                or any(manager.name is not None or manager.workers for manager in TeamsRegistry.load(org.root)._teams.values())
                or any(manager.name is not None or manager.workers for manager in org.teams._teams.values())
            )
            capture_error = None
            try:
                snapshot = org.workflow_authority.capture_canonical_snapshot()
            except WorkflowAuthorityError as exc:
                capture_error = exc.code
                # Existing startup can expose an invalid legacy roster only
                # with admission fenced. Dependency discovery still completes;
                # an invalid canonical snapshot is never a ready publication.
                snapshot = None
            desired = {
                definition.name: profile
                for definition in definitions
                if (profile := _dependency_profile(definition.executor)) is not None
            }
            with org.db._lock:
                outstanding = {
                    str(row["profile_name"])
                    for row in org.db._conn.execute(
                        "SELECT DISTINCT profile_name FROM workflow_profile_dependencies "
                        "WHERE org_namespace=? AND state IN ('active','unbound')",
                        (org.workflow_authority.namespace,),
                    ).fetchall()
                }
            profile_names = tuple(sorted(set(desired.values()) | outstanding))
            effective_profiles, effective_identity = self._capture_effective_profiles(profile_names)
            after = self._roster_revision(org)
            if before != after:
                continue
            ordinary = (
                snapshot is not None and after[1] is not None
                and not str(after[1][6]).startswith("profile-coordinator:")
                and str(after[1][6]) != completed_writer_invocation
            )
            # A prepared ordinary writer may be paused outside its durable
            # lease. Its rejected observation supplies membership, never consent.
            refusal = "profile_consumer_changed" if ordinary or async_writer or after[2] else None
            active = after[1]
            initial_publication = active is None or (
                active[5] == "org-startup"
                and str(active[6]).startswith("workflow-writer:org-startup:")
                and active[8] == "prepared"
                and active[1:3] == (1, 0) and active[10] is None
            )
            empty_skeleton = bool(
                empty_inputs and capture_error == "authority_reviewer_incoherent"
                and after[0][:3] == (0, None, None) and after[0][3] == "fenced"
                and initial_publication
            )
            return _OrgDependencyCapture(
                desired, tuple(sorted(set(desired.values()) | outstanding)),
                after, snapshot, empty_skeleton, bool(empty_inputs), refusal, effective_profiles, capture_error,
                effective_identity,
            )
        raise ProfileCoordinatorError("profile_consumer_changed")

    def _validate_capture(self, org: OrgState, revision: tuple) -> None:
        if (self._roster_revision(org) != revision or revision[2]
                or org.workflow_authority._async_writer_lock.locked()):
            raise ProfileCoordinatorError("profile_consumer_changed")

    def _validate_owned_capture(self, org: OrgState, revision: tuple, owner: str) -> None:
        # The original capture is checked before acquisition too. Only THIS
        # newly acquired lease may differ; every other DB/native image stays exact.
        fresh = self._roster_revision(org)
        leases = tuple(r for r in revision[3] if r[0] != org.workflow_authority.namespace)
        leases = tuple(sorted((*leases, (org.workflow_authority.namespace, owner, os.getpid()))))
        expected = (*revision[:2], True, leases, *revision[4:])
        if fresh != expected or org.workflow_authority._async_writer_lock.locked():
            raise ProfileCoordinatorError("profile_consumer_changed")

    def _dependency_mirror_snapshot(self, org: OrgState) -> tuple[tuple[object, ...], ...]:
        """Capture every local relation that determines the profile projection."""
        with org.db._lock:
            dependencies = org.db._conn.execute(
                "SELECT org_namespace,profile_name,consumer_identity,"
                "bound_generation,state FROM workflow_profile_dependencies "
                "ORDER BY org_namespace,profile_name,consumer_identity"
            ).fetchall()
            stores = org.db._conn.execute(
                "SELECT profile_name,generation,profile_digest,state "
                "FROM workflow_profile_store ORDER BY profile_name"
            ).fetchall()
            registry = org.db._conn.execute(
                "SELECT profile_name,published_generation "
                "FROM workflow_profile_registry ORDER BY profile_name"
            ).fetchall()
        if any(
            row["org_namespace"] != org.workflow_authority.namespace
            or not isinstance(row["profile_name"], str) or not row["profile_name"]
            or not isinstance(row["consumer_identity"], str) or not row["consumer_identity"]
            or type(row["bound_generation"]) is not int or row["bound_generation"] < 0
            or row["state"] not in {"active", "unbound", "removed"}
            for row in dependencies
        ) or any(
            not isinstance(row["profile_name"], str) or not row["profile_name"]
            or type(row["generation"]) is not int or row["generation"] < 0
            or row["state"] not in {"active", "removed"}
            or not isinstance(row["profile_digest"], str) or len(row["profile_digest"]) != 64
            for row in stores
        ) or any(
            not isinstance(row["profile_name"], str) or not row["profile_name"]
            or type(row["published_generation"]) is not int or row["published_generation"] < 0
            for row in registry
        ):
            raise ProfileCoordinatorError("profile_dependency_incoherent")
        return tuple(
            [("dependency", *tuple(row)) for row in dependencies]
            + [("store", *tuple(row)) for row in stores]
            + [("registry", *tuple(row)) for row in registry]
        )

    def _restore_dependency_mirror(self, org, mirror, *, profile_names=None):
        names = None if profile_names is None else tuple(sorted(profile_names))
        if names == ():
            return
        with self._transaction(org) as conn:
            for table in ("workflow_profile_dependencies", "workflow_profile_registry", "workflow_profile_store"):
                if names is None:
                    conn.execute(f"DELETE FROM {table}")
                else:
                    conn.execute(f"DELETE FROM {table} WHERE profile_name IN ({','.join('?' for _ in names)})", names)
            for kind, *row in mirror:
                profile_name = row[1] if kind == "dependency" else row[0]
                if names is not None and profile_name not in names:
                    continue
                table, count = {
                    "dependency": ("workflow_profile_dependencies", 5),
                    "store": ("workflow_profile_store", 4),
                    "registry": ("workflow_profile_registry", 2),
                }[kind]
                conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(count))})", row)

    def _sync_org_dependencies(self, org: OrgState, desired: dict[str, str], *, effective_profiles: dict[str, _EffectiveProfile]) -> bool:
        """Consume validated canonical discovery; never scan under leases."""
        org.workflow_authority._profile_coordinator = self
        # Effective inputs were discovered before the caller acquired profile
        # leases. No SQLite transaction spans filesystem I/O.
        before = self._dependency_mirror_snapshot(org)
        with self._transaction(org) as conn:
            existing_rows = conn.execute(
                "SELECT profile_name,consumer_identity,state "
                "FROM workflow_profile_dependencies WHERE org_namespace=?",
                (org.workflow_authority.namespace,),
            ).fetchall()
            for row in existing_rows:
                if desired.get(str(row["consumer_identity"])) != str(row["profile_name"]):
                    conn.execute(
                        "UPDATE workflow_profile_dependencies SET state='removed' "
                        "WHERE org_namespace=? AND profile_name=? AND consumer_identity=?",
                        (
                            org.workflow_authority.namespace,
                            str(row["profile_name"]),
                            str(row["consumer_identity"]),
                        ),
                    )
            for consumer, profile_name in sorted(desired.items()):
                effective = effective_profiles[profile_name]
                store = conn.execute(
                    "SELECT generation FROM workflow_profile_store WHERE profile_name=?",
                    (profile_name,),
                ).fetchone()
                generation = int(store["generation"]) if store is not None else (
                    1 if effective.state == "active" else 0
                )
                conn.execute(
                    "INSERT INTO workflow_profile_store("
                    "profile_name,generation,profile_digest,state) VALUES (?,?,?,?) "
                    "ON CONFLICT(profile_name) DO UPDATE SET "
                    "profile_digest=excluded.profile_digest,state=excluded.state",
                    (profile_name, generation, effective.digest, effective.state),
                )
                if effective.state == "active" and effective.resolvable:
                    conn.execute(
                        "INSERT INTO workflow_profile_registry("
                        "profile_name,published_generation) VALUES (?,?) "
                        "ON CONFLICT(profile_name) DO UPDATE SET "
                        "published_generation=excluded.published_generation",
                        (profile_name, generation),
                    )
                else:
                    conn.execute(
                        "DELETE FROM workflow_profile_registry WHERE profile_name=?",
                        (profile_name,),
                    )
                conn.execute(
                    "INSERT INTO workflow_profile_dependencies("
                    "org_namespace,profile_name,consumer_identity,"
                    "bound_generation,state) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(org_namespace,profile_name,consumer_identity) "
                    "DO UPDATE SET bound_generation=excluded.bound_generation,"
                    "state=excluded.state",
                    (
                        org.workflow_authority.namespace,
                        profile_name,
                        consumer,
                        generation,
                        "active" if effective.state == "active" and effective.resolvable else "unbound",
                    ),
                )
        after = self._dependency_mirror_snapshot(org)
        return before != after

    def synchronize_all_dependencies(self, *, publish: bool = True) -> set[str]:
        """Capture outside leases, then validate and synchronize each closure."""
        with self._capture_orgs() as orgs:
            captured = [(slug, org, self._capture_org_dependencies(org)) for slug, org in sorted(orgs.items())]
            return self._synchronize_captured(captured, publish=publish)

    def reconcile_supported_roster_batch(self, authority, *, completed_writer_invocation):
        """Close the existing one-shot migration after its canonical batch exits.

        The writer supplies its own completed invocation, never another live
        writer's journal. This exit owns no publisher/profile lease or scan
        transaction; legacy mutation/consumption/response semantics stay intact.
        """
        with self._capture_orgs() as orgs:
            for slug, org in orgs.items():
                if org.workflow_authority is authority:
                    capture = self._capture_org_dependencies(
                        org, completed_writer_invocation=completed_writer_invocation,
                    )
                    return self._synchronize_captured([(slug, org, capture)], publish=True)
            raise ProfileCoordinatorError("profile_dependency_incoherent")

    def _synchronize_captured(self, captured, *, publish):
        profile_names = sorted({name for _, _, capture in captured for name in capture.profile_names})
        with self._profile_leases(profile_names, wait=True) as names:
            for _, _, capture in captured:
                self._validate_effective_inputs(capture.effective_identity)
            for name in names or (None,):
                self._assert_no_active_operation(name, captured_orgs=tuple(slug for slug, _, _ in captured))
            with ExitStack() as publishers:
                for _, org, _ in sorted(captured, key=lambda item: item[0]):
                    publishers.enter_context(org.workflow_authority._publisher_lock)
                changed = set()
                for _, org, capture in captured:
                    with org.workflow_authority._publisher_lock:
                        self._validate_capture(org, capture.revision)
                for slug, org, capture in captured:
                    desired, revision, snapshot = capture.desired, capture.revision, capture.snapshot
                    with org.workflow_authority._publisher_lock:
                        owner = f"profile-dependency-validation:{uuid.uuid4().hex}"
                        self._validate_capture(org, revision)
                        org.workflow_authority._acquire_lease(owner)
                        try:
                            self._validate_owned_capture(org, revision, owner)
                            if self._sync_org_dependencies(org, desired, effective_profiles=capture.effective_profiles):
                                changed.add(slug)
                        finally:
                            org.workflow_authority._release_lease(owner)
                        if publish and slug in changed:
                            if snapshot is None:
                                org.workflow_authority.fence(reason="profile:canonical-incoherent")
                            else:
                                self._publish_dependency_change(org, canonical_snapshot=snapshot, expected_revision=revision[:2],
                                                                    effective_profiles=capture.effective_profiles)
                return changed

    def _authority_profile_projection_coherent(self, org: OrgState) -> bool:
        try:
            readiness = org.workflow_authority.verify_admission_ready()
        except Exception:
            return False
        snapshot = json.loads(readiness.snapshot_bytes)
        return (
            isinstance(snapshot, dict)
            and snapshot.get("machine_global_profiles")
            == org.workflow_authority._profile_projection()
        )

    @contextmanager
    def dynamic_org_attachment(self, org: OrgState) -> Iterator[None]:
        """Synchronize and attach one org without escaping profile capture."""
        capture = self._capture_org_dependencies(org, attachment=True)
        desired, revision, snapshot = capture.desired, capture.revision, capture.snapshot
        with self._profile_leases(capture.profile_names, wait=True) as names:
            self._validate_effective_inputs(capture.effective_identity)
            for name in names or (None,):
                self._assert_no_active_operation(name, attaching_org=org)
            with org.workflow_authority._publisher_lock:
                owner = f"profile-dependency-validation:{uuid.uuid4().hex}"
                self._validate_capture(org, revision)
                org.workflow_authority._acquire_lease(owner)
                try:
                    self._validate_owned_capture(org, revision, owner)
                    if capture.empty_canonical_inputs and not capture.empty_skeleton:
                        raise ProfileCoordinatorError("profile_dependency_incoherent")
                    if capture.empty_skeleton:
                        # Never discharge a surviving consumer/operation merely
                        # because the canonical files are empty. Cold recovery
                        # owns that evidence, including operations with no rows.
                        with org.db._lock:
                            unfinished = org.db._conn.execute(
                                "SELECT 1 FROM workflow_profile_operations "
                                "WHERE state NOT IN ('published','aborted') LIMIT 1"
                            ).fetchone()
                            outstanding = org.db._conn.execute(
                                "SELECT 1 FROM workflow_profile_dependencies "
                                "WHERE state IN ('active','unbound') LIMIT 1"
                            ).fetchone()
                        if unfinished is not None or outstanding is not None:
                            raise ProfileCoordinatorError("profile_dependency_incoherent")
                    changed = self._sync_org_dependencies(org, desired, effective_profiles=capture.effective_profiles)
                finally:
                    org.workflow_authority._release_lease(owner)
                if snapshot is None and not capture.empty_skeleton:
                    org.workflow_authority.fence(reason="profile:canonical-incoherent")
                    raise ProfileCoordinatorError("profile_dependency_incoherent")
                if not capture.empty_skeleton and (changed or not self._authority_profile_projection_coherent(org)):
                    if not self._publish_dependency_change(org, canonical_snapshot=snapshot, expected_revision=revision[:2],
                                                                effective_profiles=capture.effective_profiles):
                        raise ProfileCoordinatorError("profile_dependency_incoherent")
                if not capture.empty_skeleton and (
                    not self._closure_coherent(org, effective_profiles=capture.effective_profiles)
                    or not self._authority_profile_projection_coherent(org)
                ):
                    raise ProfileCoordinatorError("profile_dependency_incoherent")
            # Final attachment inserts into the shared mapping before these
            # leases are released, so the next writer must capture it. An earlier
            # unpublished preparation must enter again with fresh inputs at final
            # insertion; releasing preparation's leases supplies no admission.
            yield

    def _publish_dependency_change(
        self, org: OrgState, *, canonical_snapshot: bytes | None = None, expected_revision=None,
        effective_profiles: dict[str, _EffectiveProfile] | None = None,
    ) -> bool:
        invocation = f"dependency-sync-{uuid.uuid4().hex}"
        with org.workflow_authority.profile_change_interval(
            reason="profile:dependency-sync",
            coordinator_invocation=invocation,
            expected_revision=expected_revision,
        ) as binding:
            if self._closure_coherent(org, effective_profiles=effective_profiles):
                org.workflow_authority.publish_profile_change(
                    publisher="profile-dependency-sync",
                    binding=binding,
                    canonical_snapshot=canonical_snapshot,
                )
                return True
        return False

    def rebind_consumer(
        self,
        *,
        org: OrgState,
        consumer_identity: str,
        from_profile: str | None,
        to_profile: str | None,
    ) -> None:
        """Update exactly one consumer row while the caller holds profile lease(s)."""
        target_name = to_profile.lower() if to_profile is not None else None
        target_effective = (
            self._effective_profile(target_name)
            if target_name is not None
            else None
        )
        if target_effective is not None and (
            target_effective.state != "active" or not target_effective.resolvable
        ):
            raise ProfileCoordinatorError("profile_target_not_published")
        target_generation = (
            max(1, self._max_generation(target_name))
            if target_name is not None
            else None
        )
        with self._transaction(org) as conn:
            if from_profile is not None:
                conn.execute(
                    "UPDATE workflow_profile_dependencies SET state='removed' "
                    "WHERE org_namespace=? AND profile_name=? AND consumer_identity=? "
                    "AND state IN ('active','unbound')",
                    (
                        org.workflow_authority.namespace,
                        from_profile.lower(),
                        consumer_identity,
                    ),
                )
            if target_name is None or target_effective is None:
                return
            row = conn.execute(
                "SELECT generation FROM workflow_profile_store WHERE profile_name=?",
                (target_name,),
            ).fetchone()
            assert target_generation is not None
            generation = (
                int(row["generation"])
                if row is not None
                else target_generation
            )
            conn.execute(
                "INSERT INTO workflow_profile_store("
                "profile_name,generation,profile_digest,state) VALUES (?,?,?,'active') "
                "ON CONFLICT(profile_name) DO UPDATE SET "
                "profile_digest=excluded.profile_digest,state='active'",
                (target_name, generation, target_effective.digest),
            )
            conn.execute(
                "INSERT INTO workflow_profile_registry("
                "profile_name,published_generation) VALUES (?,?) "
                "ON CONFLICT(profile_name) DO UPDATE SET "
                "published_generation=excluded.published_generation",
                (target_name, generation),
            )
            conn.execute(
                "INSERT INTO workflow_profile_dependencies("
                "org_namespace,profile_name,consumer_identity,bound_generation,state"
                ") VALUES (?,?,?,?,'active') "
                "ON CONFLICT(org_namespace,profile_name,consumer_identity) "
                "DO UPDATE SET bound_generation=excluded.bound_generation,state='active'",
                (
                    org.workflow_authority.namespace,
                    target_name,
                    consumer_identity,
                    generation,
                ),
            )

    def _discover_operation_groups(self) -> dict[str, set[str]]:
        with self._capture_orgs() as orgs:
            rows = []
            for org in orgs.values():
                with org.db._lock:
                    rows.extend(org.db._conn.execute(
                        "SELECT coordinator_invocation,profile_name,state FROM workflow_profile_operations"
                    ).fetchall())
            active = {r["coordinator_invocation"] for r in rows if r["state"] not in ("published", "aborted")}
            groups: dict[str, set[str]] = {}
            for row in rows:
                if row["coordinator_invocation"] in active:
                    invocation, name = row["coordinator_invocation"], row["profile_name"]
                    if not isinstance(invocation, str) or not invocation or not isinstance(name, str) or not name:
                        raise ProfileCoordinatorError("profile_operation_members_unknown")
                    groups.setdefault(invocation, set()).add(name)
            return groups

    def _load_group_operations(
        self, coordinator_invocation: str, profile_names: Sequence[str],
    ) -> tuple[_ProfileOperation, ...]:
        """Read a complete invocation, including every original member mirror."""
        with self._capture_orgs() as orgs:
            operation_rows: dict[str, list[tuple[str, sqlite3.Row]]] = {}
            for slug, org in orgs.items():
                with org.db._lock:
                    rows = org.db._conn.execute(
                        "SELECT * FROM workflow_profile_operations WHERE coordinator_invocation=?",
                        (coordinator_invocation,),
                    ).fetchall()
                for row in rows:
                    operation_rows.setdefault(row["profile_name"], []).append((slug, row))
            if set(operation_rows) != set(profile_names):
                raise ProfileCoordinatorError("profile_operation_missing")
            rank = {"captured": 0, "fenced": 1, "store_committed": 2,
                    "forward_recovery_required": 3, "published": 4, "aborted": 4}
            operations = {}
            for name, mirrors in operation_rows.items():
                first = mirrors[0][1]
                try:
                    members = json.loads(first["captured_members"])
                except (TypeError, ValueError) as exc:
                    raise ProfileCoordinatorError("profile_operation_members_unknown") from exc
                if (not isinstance(members, list)
                        or any(not isinstance(slug, str) or not slug or slug not in orgs for slug in members)):
                    raise ProfileCoordinatorError("profile_operation_members_unknown")
                if len(set(members)) != len(members):
                    raise ProfileCoordinatorError("profile_operation_incoherent")
                if {slug for slug, _ in mirrors} != set(members) or len(mirrors) != len(members):
                    raise ProfileCoordinatorError("profile_operation_missing")
                identity_fields = ("id", "profile_name", "operation_kind", "captured_members",
                                   "target_generation", "coordinator_invocation", "compensation_generation", "created_at")
                identity = tuple(first[key] for key in identity_fields)
                if (not isinstance(name, str) or not name or first["operation_kind"] not in {"register", "rebind", "remove"}
                        or type(first["target_generation"]) is not int or first["target_generation"] <= 0
                        or any(not isinstance(first[key], str) or not first[key] for key in
                               ("id", "coordinator_invocation", "created_at"))):
                    raise ProfileCoordinatorError("profile_operation_incoherent")
                rows = [row for _, row in mirrors]
                for row in rows:
                    if (tuple(row[key] for key in identity_fields) != identity
                            or row["state"] not in rank
                            or type(row["compensation_generation"]) is not int or row["compensation_generation"] < 0
                            or not isinstance(row["profile_digest"], str) or len(row["profile_digest"]) != 64
                            or any(c not in "0123456789abcdef" for c in row["profile_digest"])):
                        raise ProfileCoordinatorError("profile_operation_incoherent")
                if {r["state"] for r in rows} >= {"published", "aborted"}:
                    raise ProfileCoordinatorError("profile_operation_incoherent")
                prior_rows = [r for r in rows if r["state"] in {"captured", "fenced"}]
                if len({r["profile_digest"] for r in prior_rows}) > 1:
                    raise ProfileCoordinatorError("profile_operation_incoherent")
                operations[name] = _ProfileOperation(
                    operation_id=first["id"], profile_name=name, operation_kind=first["operation_kind"],
                    members=tuple(members), target_generation=first["target_generation"],
                    prior=_EffectiveProfile((prior_rows or rows)[0]["profile_digest"], "active", False),
                    recovery_state=max((r["state"] for r in rows), key=rank.__getitem__),
                )
            return tuple(operations[name] for name in sorted(operations))

    def _members_are_already_ready(
        self,
        operations: Sequence[_ProfileOperation],
        *, effective_profiles: dict[str, _EffectiveProfile] | None = None,
    ) -> bool:
        with self._capture_orgs() as orgs:
            members = {slug for operation in operations for slug in operation.members}
            for slug in members:
                org = orgs[slug]
                try:
                    readiness = org.workflow_authority.verify_admission_ready()
                except Exception:
                    return False
                if not self._closure_coherent(org, effective_profiles=effective_profiles):
                    return False
                snapshot = json.loads(readiness.snapshot_bytes)
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("machine_global_profiles")
                    != org.workflow_authority._profile_projection()
                ):
                    return False
            return True

    def _recover_group(
        self,
        coordinator_invocation: str,
        profile_names: Sequence[str],
        *,
        expected_operations: Sequence[_ProfileOperation],
        expected_captures: dict[str, _OrgDependencyCapture],
    ) -> None:
        with self._capture_orgs() as orgs:
            operations = self._load_group_operations(coordinator_invocation, profile_names)
            if operations != tuple(expected_operations):
                raise ProfileCoordinatorError("profile_consumer_changed")
            captures = {slug: self._capture_org_dependencies(orgs[slug])
                        for slug in {slug for op in operations for slug in op.members}}
            for slug, capture in captures.items():
                original = expected_captures[slug]
                if (capture.revision != original.revision or capture.desired != original.desired
                        or capture.profile_names != original.profile_names or capture.snapshot != original.snapshot):
                    raise ProfileCoordinatorError("profile_consumer_changed")
            closure_profiles = sorted(set(profile_names) | {name for c in captures.values() for name in c.profile_names})
            effective_profiles, effective_identity = self._capture_effective_profiles(closure_profiles)
            already_ready = self._members_are_already_ready(operations, effective_profiles=effective_profiles)
            with self._profile_leases(closure_profiles, wait=False):
                self._validate_effective_inputs(effective_identity)
                with ExitStack() as publishers:
                    for slug in sorted(captures):
                        publishers.enter_context(orgs[slug].workflow_authority._publisher_lock)
                    if operations != self._load_group_operations(coordinator_invocation, profile_names):
                        raise ProfileCoordinatorError("profile_consumer_changed")
                    for slug, capture in captures.items():
                        with orgs[slug].workflow_authority._publisher_lock:
                            self._validate_capture(orgs[slug], capture.revision)
                    # A crash after all canonical publications but before the final
                    # operation-row update must not manufacture another generation.
                    if already_ready:
                        for operation in operations:
                            effective = effective_profiles[operation.profile_name]
                            terminal = (
                                "published"
                                if effective.digest != operation.prior.digest
                                or operation.recovery_state
                                in {"store_committed", "published", "forward_recovery_required"}
                                else "aborted"
                            )
                            self._set_operation_state(
                                operation, terminal, profile_digest=effective.digest,
                            )
                        self._release_diagnostic_leases(
                            operations,
                            coordinator_invocation=coordinator_invocation,
                        )
                        return
                    for operation in operations:
                        self._insert_operation_rows(
                            operation,
                            coordinator_invocation=coordinator_invocation,
                        )
                    bindings: dict[str, ProfileFenceBinding] = {}
                    members = sorted({slug for op in operations for slug in op.members})
                    with ExitStack() as stack:
                        for slug in members:
                            bindings[slug] = stack.enter_context(
                                orgs[slug].workflow_authority.profile_change_interval(
                                    reason="profile:startup-recovery",
                                    coordinator_invocation=coordinator_invocation,
                                    resume=True,
                                )
                            )
                        for operation in operations:
                            if operation.recovery_state in {"captured", "fenced"}:
                                self._set_operation_state(operation, "fenced")
                        self._finalize(
                            operations=operations,
                            bindings=bindings,
                            publisher="profile-startup-recovery",
                            mutation_succeeded=True,
                            effective_profiles=effective_profiles,
                        )
                        self._release_diagnostic_leases(
                            operations,
                            coordinator_invocation=coordinator_invocation,
                        )

    def reconcile_startup(self) -> None:
        """Classify complete startup evidence, recover eligible groups, then sync.

        Refusals latch for this attachment. An ordinary local journal is never
        adopted; only the existing complete eligible profile groups can recover.
        """
        with self._capture_orgs() as orgs:
            captures = {}
            edges: dict[str, set[str]] = {slug: set() for slug in orgs}
            reasons = dict(self._startup_holds)
            unknown = self._startup_unknown or bool(getattr(self, "_move_diagnostic_orgs", None))
            first = next(iter(reasons.values()), None)
            def refused(slugs: Sequence[str], exc: Exception, *, uncertain: bool = False) -> None:
                nonlocal first, unknown
                cause = getattr(exc, "code", str(exc))
                first = first or cause
                unknown = unknown or uncertain
                logger.warning("profile startup held: %s", cause, exc_info=True)
                for slug in slugs:
                    reasons.setdefault(slug, cause)

            for slug, org in sorted(orgs.items()):
                try:
                    captures[slug] = self._capture_org_dependencies(org, attachment=True)
                except ProfileCoordinatorError as exc:
                    capture = getattr(exc, "capture", None)
                    if capture is None:
                        refused((slug,), exc, uncertain=True)
                    else:
                        captures[slug] = capture
                        refused((slug,), exc)
                except (WorkflowAuthorityError, sqlite3.DatabaseError, ValueError, OSError, OrgConsistencyError) as exc:
                    refused((slug,), exc, uncertain=True)
                if slug in captures:
                    capture = captures[slug]
                    edges[slug].update(capture.profile_names)
                    if capture.canonical_error is not None and not capture.empty_skeleton:
                        reasons.setdefault(slug, capture.canonical_error)
                        first = first or capture.canonical_error
                    # Strict authenticated native ownership, independently of a
                    # PID, kernel lease or an ordinary journal's current phase.
                    # THR320seq477: a stale nightly sweep record supplies no
                    # live owner (same predicate as the org startup hold). An
                    # absent native owner keeps every record blocking.
                    live = json.loads(capture.revision[8][1]) if capture.revision[8] else None
                    history = [NativeWriterLifecycle.model_validate_json(raw, strict=True)
                               for _, raw in capture.revision[7]]
                    if (any(row.state != "settled"
                            and (live is None or not historical_disk_sweep(row.writer_id, row, live))
                            for row in history)
                            or live):
                        reasons.setdefault(slug, "profile_consumer_changed")
                    try:
                        self._validate_capture(org, capture.revision)
                    except (ProfileCoordinatorError, WorkflowAuthorityError, sqlite3.DatabaseError,
                            ValueError, OSError, OrgConsistencyError) as exc:
                        refused((slug,), exc, uncertain=True)

            groups = {}
            operations = {}
            try:
                groups = self._discover_operation_groups()
                for invocation, names in sorted(groups.items()):
                    # All profiles of ONE invocation form one hyperedge, even
                    # if a current canonical consumer or mirror is absent.
                    for slug in edges:
                        if edges[slug] & names:
                            edges[slug].update(names)
                    # Preserve known captured-only members even if a missing or
                    # invalid mirror later makes the complete group ineligible.
                    for capture in captures.values():
                        for row in capture.revision[5]:
                            if row[7] != invocation:
                                continue
                            try:
                                members = json.loads(row[3])
                            except (TypeError, ValueError):
                                members = None
                            if not isinstance(members, list) or any(not isinstance(m, str) or m not in orgs for m in members):
                                unknown = True
                            else:
                                for member in members:
                                    edges[member].update(names)
                    try:
                        operations[invocation] = self._load_group_operations(invocation, sorted(names))
                        for op in operations[invocation]:
                            for slug in op.members:
                                edges[slug].update(names)
                    except ProfileCoordinatorError as exc:
                        affected = tuple(slug for slug in edges if edges[slug] & names)
                        refused(affected, exc, uncertain=exc.code == "profile_operation_members_unknown")
            except (sqlite3.DatabaseError, ValueError, OSError, ProfileCoordinatorError,
                    WorkflowAuthorityError, OrgConsistencyError) as exc:
                refused(tuple(orgs), exc, uncertain=True)

            def closure() -> None:
                if unknown:
                    for slug in orgs:
                        reasons.setdefault(slug, first or "profile_consumer_changed")
                changed = True
                while changed:
                    profiles = {name for slug in reasons for name in edges.get(slug, ())}
                    added = {slug for slug in edges if edges[slug] & profiles and slug not in reasons}
                    for slug in added:
                        reasons[slug] = "profile_consumer_changed: " + (first or next(iter(reasons.values())))
                    changed = bool(added)

            # Close the WHOLE discovery before the first recovery/sync effect.
            # Movement may have introduced a previously unknown hyperedge.
            for slug, capture in captures.items():
                try:
                    self._validate_capture(orgs[slug], capture.revision)
                except (ProfileCoordinatorError, WorkflowAuthorityError, sqlite3.DatabaseError,
                        ValueError, OSError, OrgConsistencyError) as exc:
                    refused((slug,), exc, uncertain=True)
            closure()
            for invocation, names in sorted(groups.items()):
                if invocation not in operations:
                    continue
                members = {slug for op in operations[invocation] for slug in op.members}
                if members & reasons.keys():
                    continue
                try:
                    self._recover_group(invocation, sorted(names),
                                        expected_operations=operations[invocation], expected_captures=captures)
                    # Recovery changes owned journals/mirrors; take complete new
                    # captures only for this eligible group's actual members.
                    for slug in members:
                        renewed = self._capture_org_dependencies(orgs[slug])
                        if set(renewed.profile_names) - edges[slug]:
                            raise ProfileCoordinatorError("profile_consumer_changed")
                        captures[slug] = renewed
                except (ProfileCoordinatorError, WorkflowAuthorityError, sqlite3.DatabaseError,
                        ValueError, OSError, OrgConsistencyError) as exc:
                    # Renewed group/canonical discovery or a post-recovery
                    # capture may have lost a member/edge absent from the old
                    # map. No later component may use that independence proof.
                    refused(tuple(members), exc, uncertain=True)
                    closure()

            self._startup_holds.update(reasons)
            self._startup_hold_profiles.update(name for slug in reasons for name in edges.get(slug, ()))
            self._startup_unknown = unknown
            # Synchronize disjoint components independently. A failed capture
            # invalidates its complete connected scope BEFORE any member writes.
            remaining = set(orgs) - reasons.keys()
            while remaining:
                component = {min(remaining)}
                while True:
                    names = {name for slug in component for name in edges[slug]}
                    expanded = component | {slug for slug in remaining if edges[slug] & names}
                    if expanded == component:
                        break
                    component = expanded
                remaining.difference_update(component)
                try:
                    self._synchronize_captured([(slug, orgs[slug], captures[slug]) for slug in sorted(component)], publish=True)
                except (ProfileCoordinatorError, WorkflowAuthorityError, sqlite3.DatabaseError,
                        ValueError, OSError, OrgConsistencyError) as exc:
                    # These are fresh reads/validation after classification;
                    # an incomplete current map cannot prove another scope
                    # independent. Initial complete ordinary refusals remain
                    # local and still permit their proved disjoint components.
                    refused(tuple(component), exc, uncertain=True)
                    closure()
                    remaining.difference_update(reasons)
                    self._startup_holds.update(reasons)
                    self._startup_hold_profiles.update(name for slug in reasons for name in edges.get(slug, ()))
                    self._startup_unknown = unknown
            self._startup_unknown = unknown
            if self._hold_startup is not None:
                self._hold_startup(self._startup_holds)

    def _move_batch_inputs(self, org: OrgState, operation_id: str) -> tuple:
        from runtime.infrastructure.team_move_store import MoveRequest, _decode
        move = org.db._team_move_coordinator
        row = move.store.read()
        if row is None or row["operation_id"] != operation_id:
            raise ProfileCoordinatorError("profile_move_owner_required")
        request = _decode(MoveRequest, row["request_bytes"])
        import base64
        from runtime.orchestrator.agent_def import parse_agent_text
        definitions = tuple(parse_agent_text(base64.b64decode(image.before_base64).decode().replace("\r\n", "\n"),
            expected_name=agent) for agent, image in zip(request.affected_agents, request.files[:2]))
        names = tuple(sorted({name for definition in definitions if (name := _dependency_profile(definition.executor)) is not None}))
        return definitions, names

    @asynccontextmanager
    async def consumer_batch_writer(self, *, org, operation_id, native=None):
        """ONE D-owned interval for the exact two unchanged executor consumers.

        The operation row itself is the durable native writer: emitting a C3
        writer whose failed tail becomes immutable unknown would prevent the
        forward-recovery operation from reconciling its OWN known partial work.
        No other native writer's history is adopted/cleared. The ordinary C3 hold
        still refuses every conflicting writer. Empty dependency sets are real.
        """
        if native is None:
            definitions, names = self._move_batch_inputs(org, operation_id)
        else:
            definitions, names = await native(lambda: self._move_batch_inputs(org, operation_id))
        org.workflow_authority._profile_coordinator = self
        async with org.workflow_authority._async_writer_lock:
            interval = _MoveBatchInterval(self, org, operation_id, definitions, names)
            yield interval
            # Deliberately no automatic compensation or publication on exit.
            # D owns the immutable decision and exact SAME publication journal.


@dataclass(frozen=True)
class _MoveBatchCapture:
    operation_revision: int
    roster_revision: tuple
    dependency_mirror: tuple
    database_changes: tuple[int, int]
    writer_epoch: int


class _MoveBatchInterval:
    def __init__(self, profiles, org, operation_id, definitions, names):
        self.profiles, self.org, self.operation_id = profiles, org, operation_id
        self.definitions, self.names = definitions, names

    def _observation(self):
        """Only native DB/revision/epoch facts; safe in a short DB-only leaf."""
        db = self.org.db
        move = db._team_move_coordinator
        with db._lock:
            row = move.active_uncommitted()
            if row is None or row["operation_id"] != self.operation_id:
                raise ProfileCoordinatorError("profile_move_owner_required")
            return _MoveBatchCapture(row["state_revision"], self.profiles._roster_revision(self.org)[:2],
                self.profiles._dependency_mirror_snapshot(self.org),
                (db._conn.total_changes, db._conn.execute("PRAGMA data_version").fetchone()[0]),
                move.producers.writer_epoch)

    def capture(self):
        """Complete native discovery before any profile/publication lease."""
        before = self._observation()
        current = tuple(prompt_loader.load_agent(OrgPaths(self.org.root), d.name) for d in self.definitions)
        if any(actual is None or (actual.executor, actual.model, actual.reasoning_effort, actual.allow_rules, actual.repos)
                != (original.executor, original.model, original.reasoning_effort, original.allow_rules, original.repos)
                for original, actual in zip(self.definitions, current)):
            raise ProfileCoordinatorError("profile_move_consumer_changed")
        projection = self.org.workflow_authority._profile_projection()
        from runtime.workflows.authority import _agent_projection
        definitions = [_agent_projection(d, status="active") for d in prompt_loader.list_agents(OrgPaths(self.org.root))]
        self.profiles.validate_requirements(definitions, projection)
        if not self.profiles._closure_coherent(self.org):
            raise ProfileCoordinatorError("profile_dependency_incoherent")
        self.revalidate(before)
        return before

    def revalidate(self, capture):
        if self._observation() != capture:
            raise ProfileCoordinatorError("profile_move_capture_changed")

    @contextmanager
    def segment(self, capture):
        """Only synchronous mutation; no discovery/census/publication capture."""
        token = _move_batch_owner.set((self.org.slug, self.operation_id))
        try:
            with self.profiles._profile_leases(self.names, wait=True):
                for name in self.names or (None,):
                    self.profiles._assert_no_active_operation(name, move_org=self.org)
                with self.org.workflow_authority._publisher_lock:
                    self.revalidate(capture)
                    yield
        finally:
            _move_batch_owner.reset(token)
