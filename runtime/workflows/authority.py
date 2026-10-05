"""Org-scoped workflow authority publication and recovery.

U2A is deliberately only the producer half of the F4-D contract. Supported
authority writers fence and publish coherent generations through this module;
later units may call :meth:`verify_admission_ready`, but this unit does not wire
that read into task admission, activation, dispatch, callbacks, or legacy work.

The guarantee is cooperative among supported writers in the daemon process.
Direct same-UID file or SQLite edits remain outside it. SQLite transactions are
short and never span snapshot capture or filesystem publication.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.org_config import resolve_org_setting_reviewer_agents
from runtime.orchestrator.org_validation import validate_team_membership
from runtime.orchestrator.teams import TeamsRegistry

if TYPE_CHECKING:
    import sqlite3

    from runtime.infrastructure.database import Database
    from runtime.models import AuthorityPolicySelector


logger = logging.getLogger(__name__)


class WorkflowAuthorityError(RuntimeError):
    """Closed machine-readable workflow-authority failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AuthorityReadiness:
    namespace: str
    generation: int
    snapshot_digest: str
    snapshot_bytes: bytes


@dataclass(frozen=True)
class AuthorityAdmissionCapture:
    """Discovery captured outside every durable lease and writer transaction."""

    ready: AuthorityReadiness
    pointer: tuple
    profile_names: tuple[str, ...]
    profile_digests: tuple[tuple[str, str], ...]
    profile_mirror: tuple


@dataclass(frozen=True)
class ProfileFenceBinding:
    """One profile operation's immutable org-publication binding."""

    journal_id: str
    publisher_invocation: str
    profile_fence: int


class _SupportedWriterInterval:
    """One process-serialized writer with short durable mutation leases."""

    def __init__(
        self,
        coordinator: WorkflowAuthorityCoordinator,
        *,
        publisher: str,
    ) -> None:
        self._coordinator = coordinator
        self.publisher = publisher
        self.fenced = False
        self.fence_journal_id: str | None = None
        self.publisher_invocation: str | None = None

    @contextmanager
    def canonical_change(self) -> Iterator[None]:
        """Fence and own only one synchronous canonical mutation segment."""
        owner = f"workflow-writer:{self.publisher}:{uuid.uuid4().hex}"
        self._coordinator._acquire_lease(owner)
        try:
            self.fence_journal_id = self._coordinator._fence_under_lease(
                reason=self.publisher,
                publisher_invocation=owner,
            )
            self.publisher_invocation = owner
            self.fenced = True
            yield
        finally:
            self._coordinator._release_lease(owner)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkflowAuthorityError("authority_snapshot_not_canonical") from exc


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _agent_projection(agent, *, status: str) -> dict[str, object]:
    return {
        "allow_rules": list(agent.allow_rules),
        "description_digest": _digest((agent.description or "").encode("utf-8")),
        "executor": agent.executor,
        "model": agent.model,
        "name": agent.name,
        "repos": dict(sorted(agent.repos.items())),
        "role": agent.role,
        "status": status,
        "system_prompt_digest": _digest(agent.system_prompt.encode("utf-8")),
        "team": agent.team,
    }


class WorkflowAuthorityCoordinator:
    """Publish one canonical authority generation for one organization."""

    def __init__(
        self,
        *,
        db: Database,
        org_slug: str,
        root: Path,
        teams: TeamsRegistry,
    ) -> None:
        self._db = db
        self._org_slug = org_slug
        self._root = Path(root)
        self._teams = teams
        self._cache: dict[str, tuple[int, str]] = {}
        # Process-local serialization complements the durable cross-process
        # lease. It is never treated as protection from arbitrary same-UID
        # mutation. Async route writers first take the coroutine lock so a
        # multi-stage writer may retain this gate across awaits without a
        # durable lease or SQLite transaction spanning those awaits.
        self._publisher_lock = threading.RLock()
        self._async_writer_lock = asyncio.Lock()
        self._profile_coordinator = None

    @property
    def namespace(self) -> str:
        return f"org/{self._org_slug}"

    @property
    def canonical_path(self) -> Path:
        return self._root / "org" / ".workflow-authority.json"

    def capture_canonical_snapshot(self) -> bytes:
        """Discover canonical org inputs without profile/publication leases."""
        paths = OrgPaths(root=self._root)
        fresh_teams = TeamsRegistry.load(self._root)
        validate_team_membership(paths, fresh_teams)

        teams = [
            {
                "manager": fresh_teams.manager_for_team(name).name,
                "name": name,
                "workers": sorted(fresh_teams.manager_for_team(name).workers),
            }
            for name in fresh_teams.teams()
        ]
        in_memory_teams = [
            {
                "manager": self._teams.manager_for_team(name).name,
                "name": name,
                "workers": sorted(self._teams.manager_for_team(name).workers),
            }
            for name in self._teams.teams()
        ]
        if in_memory_teams != teams:
            raise WorkflowAuthorityError("authority_team_cache_incoherent")

        active = list(prompt_loader.list_agents(paths))
        pending = list(prompt_loader.list_pending(paths))
        projected: dict[str, dict[str, object]] = {}
        for status, definitions in (("active", active), ("pending", pending)):
            for definition in definitions:
                if definition.name in projected:
                    raise WorkflowAuthorityError("authority_agent_identity_conflict")
                projected[definition.name] = _agent_projection(
                    definition, status=status,
                )

        # The complete effective membership must be coherent. A pending worker
        # is legitimate after enrollment; every other team identity must have
        # a matching canonical definition and every active worker must appear
        # in its declared team's worker set.
        for team in teams:
            manager = projected.get(str(team["manager"]))
            if not (
                manager is not None
                and manager["status"] == "active"
                and manager["role"] == "manager"
                and manager["team"] == team["name"]
            ):
                raise WorkflowAuthorityError("authority_manager_incoherent")
            for worker_name in team["workers"]:
                worker = projected.get(worker_name)
                if not (
                    worker is not None
                    and worker["role"] != "manager"
                    and worker["team"] == team["name"]
                ):
                    raise WorkflowAuthorityError("authority_worker_incoherent")
        for definition in active:
            if definition.role == "manager":
                continue
            if definition.team not in fresh_teams.teams() or (
                definition.name
                not in fresh_teams.manager_for_team(definition.team).workers
            ):
                raise WorkflowAuthorityError("authority_worker_incoherent")

        known_active = {definition.name for definition in active}
        reviewer_agents = resolve_org_setting_reviewer_agents(
            self._db, known_agents=known_active,
        )
        if any(name not in known_active for name in reviewer_agents):
            raise WorkflowAuthorityError("authority_reviewer_incoherent")

        policy_store = AuthorityPolicyStore(self._db)
        selectors = []
        for team in fresh_teams.teams():
            selector = policy_store.get_authority_selector(team)
            selectors.append({
                "selector": None if selector is None else selector.model_dump(mode="json"),
                "team": team,
            })

        return _canonical_json({
            "active_policy_selectors": selectors,
            "agents": [projected[name] for name in sorted(projected)],
            "machine_global_profiles": [],
            "org_slug": self._org_slug,
            "reviewer_agents": list(reviewer_agents),
            "schema_version": 1,
            "teams": teams,
        })

    def capture_snapshot(self) -> bytes:
        """Compose canonical discovery with its complete profile closure."""
        snapshot = json.loads(self.capture_canonical_snapshot())
        profiles = self._profile_projection()
        if self._profile_coordinator is not None:
            self._profile_coordinator.validate_requirements(snapshot["agents"], profiles)
        snapshot["machine_global_profiles"] = profiles
        return _canonical_json(snapshot)

    def _profile_projection(self) -> list[dict[str, object]]:
        """Return the coherent machine-profile requirements for this org.

        The profile coordinator owns these rows.  A ready authority snapshot
        never contains an unbound or stale requirement: such an org remains
        fenced and therefore cannot reach this projection as admitted state.
        """
        with self._db._lock:
            rows = self._db._conn.execute(
                "SELECT d.profile_name,d.consumer_identity,d.bound_generation,"
                "d.state,s.generation,s.profile_digest,s.state AS store_state,"
                "r.published_generation "
                "FROM workflow_profile_dependencies d "
                "LEFT JOIN workflow_profile_store s "
                "ON s.profile_name=d.profile_name "
                "LEFT JOIN workflow_profile_registry r "
                "ON r.profile_name=d.profile_name "
                "WHERE d.org_namespace=? AND d.state IN ('active','unbound') "
                "ORDER BY d.profile_name,d.consumer_identity",
                (self.namespace,),
            ).fetchall()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            bound = int(row["bound_generation"])
            if (
                row["state"] != "active"
                or row["store_state"] != "active"
                or row["generation"] is None
                or int(row["generation"]) != bound
                or row["published_generation"] is None
                or int(row["published_generation"]) != bound
            ):
                raise WorkflowAuthorityError("authority_pointer_not_ready")
            profile_name = str(row["profile_name"])
            current = grouped.setdefault(
                profile_name,
                {
                    "consumers": [],
                    "generation": bound,
                    "profile_digest": str(row["profile_digest"]),
                    "profile_name": profile_name,
                },
            )
            if (
                current["generation"] != bound
                or current["profile_digest"] != str(row["profile_digest"])
            ):
                raise WorkflowAuthorityError("authority_pointer_not_ready")
            consumers = current["consumers"]
            assert isinstance(consumers, list)
            consumers.append(str(row["consumer_identity"]))
        return [grouped[name] for name in sorted(grouped)]

    @contextmanager
    def _transaction(self):
        """Own a short coordinator transaction without borrowing schema APIs."""
        with self._db._lock:
            conn = self._db._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    @staticmethod
    def _pointer(conn: sqlite3.Connection, namespace: str) -> tuple[int, str | None, str | None, str, int]:
        row = conn.execute(
            "SELECT current_generation,journal_id,snapshot_digest,state,profile_fence "
            "FROM workflow_authority_pointers WHERE namespace=?",
            (namespace,),
        ).fetchone()
        if row is None:
            return (0, None, None, "ready", 0)
        return (
            int(row["current_generation"]),
            row["journal_id"],
            row["snapshot_digest"],
            str(row["state"]),
            int(row["profile_fence"]),
        )

    @staticmethod
    def _active_journal(conn: sqlite3.Connection, namespace: str):
        return conn.execute(
            "SELECT id,generation,expected_generation,snapshot_bytes,snapshot_digest,"
            "publisher,publisher_invocation,profile_fence,state,recovery_owner,file_phase_owner "
            "FROM workflow_publication_journals "
            "WHERE namespace=? AND state NOT IN ('cache_installed','aborted') "
            "ORDER BY rowid DESC LIMIT 1",
            (namespace,),
        ).fetchone()

    def _prepared_recovery_binding(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[str, str] | None:
        active = self._active_journal(conn, self.namespace)
        if active is None or str(active["state"]) != "prepared":
            return None
        snapshot = bytes(active["snapshot_bytes"])
        if (
            int(active["generation"]) != int(active["expected_generation"]) + 1
            or _digest(snapshot) != str(active["snapshot_digest"])
        ):
            raise WorkflowAuthorityError("authority_active_journal_invalid")
        return str(active["id"]), str(active["publisher_invocation"])

    def _acquire_lease(self, owner: str) -> None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT owner_token,owner_pid FROM workflow_publication_leases "
                "WHERE namespace=?",
                (self.namespace,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO workflow_publication_leases VALUES (?,?,?)",
                    (self.namespace, owner, os.getpid()),
                )
            elif not _pid_is_live(int(row["owner_pid"])):
                conn.execute(
                    "DELETE FROM workflow_publication_leases WHERE namespace=? "
                    "AND owner_token=?",
                    (self.namespace, str(row["owner_token"])),
                )
                conn.execute(
                    "INSERT INTO workflow_publication_leases VALUES (?,?,?)",
                    (self.namespace, owner, os.getpid()),
                )
            else:
                raise WorkflowAuthorityError("publication_lease_busy")

    def _release_lease(self, owner: str) -> None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT owner_token,owner_pid FROM workflow_publication_leases "
                "WHERE namespace=?",
                (self.namespace,),
            ).fetchone()
            if row is None or (
                str(row["owner_token"]), int(row["owner_pid"])
            ) != (owner, os.getpid()):
                raise WorkflowAuthorityError("publication_lease_owner_required")
            conn.execute(
                "DELETE FROM workflow_publication_leases WHERE namespace=? "
                "AND owner_token=?",
                (self.namespace, owner),
            )

    def _journal(self, conn: sqlite3.Connection, journal_id: str):
        row = conn.execute(
            "SELECT id,generation,expected_generation,snapshot_bytes,snapshot_digest,"
            "publisher,publisher_invocation,profile_fence,state,recovery_owner,file_phase_owner "
            "FROM workflow_publication_journals WHERE namespace=? AND id=?",
            (self.namespace, journal_id),
        ).fetchone()
        if row is None:
            raise WorkflowAuthorityError("authority_pointer_journal_missing")
        snapshot = bytes(row["snapshot_bytes"])
        if (
            int(row["generation"]) != int(row["expected_generation"]) + 1
            or _digest(snapshot) != row["snapshot_digest"]
        ):
            raise WorkflowAuthorityError("authority_pointer_journal_invalid")
        return row

    def _fence_under_lease(
        self,
        *,
        reason: str,
        publisher_invocation: str,
        increment_profile_fence: bool = False,
    ) -> str:
        """Fence and durably bind it to one publication invocation.

        ``prepared`` is the only pre-file state and is therefore the only state
        a later fencing writer may supersede. Its snapshot fields carry the
        verified predecessor until the owning invocation refreshes them with
        its post-mutation capture immediately before reserving the file phase.
        Once file ownership is reserved, a later writer must fail closed and
        leave recovery to complete the already-started publication.
        """
        with self._transaction() as conn:
            active = self._active_journal(conn, self.namespace)
            if active is not None:
                if str(active["publisher_invocation"]).startswith(
                    "profile-coordinator:"
                ):
                    coordinator_invocation = str(
                        active["publisher_invocation"]
                    ).removeprefix("profile-coordinator:")
                    profile_operation = conn.execute(
                        "SELECT 1 FROM workflow_profile_operations "
                        "WHERE coordinator_invocation=? "
                        "AND state NOT IN ('published','aborted') LIMIT 1",
                        (coordinator_invocation,),
                    ).fetchone()
                    if profile_operation is not None:
                        raise WorkflowAuthorityError(
                            "authority_publication_in_progress"
                        )
                if str(active["state"]) != "prepared":
                    raise WorkflowAuthorityError("authority_publication_in_progress")
                changed = conn.execute(
                    "UPDATE workflow_publication_journals SET state='aborted' "
                    "WHERE id=? AND namespace=? AND state='prepared'",
                    (str(active["id"]), self.namespace),
                ).rowcount
                if changed != 1:
                    raise WorkflowAuthorityError("authority_publication_in_progress")
            generation, journal_id, digest, state, profile_fence = self._pointer(
                conn, self.namespace,
            )
            if increment_profile_fence:
                profile_fence += 1
            if state not in {"ready", "fenced"}:
                raise WorkflowAuthorityError("authority_pointer_not_ready")
            if generation == 0:
                predecessor = _canonical_json({})
            else:
                if journal_id is None or digest is None:
                    raise WorkflowAuthorityError("authority_pointer_not_ready")
                previous = self._journal(conn, journal_id)
                predecessor = bytes(previous["snapshot_bytes"])
                if (
                    previous["state"] != "cache_installed"
                    or int(previous["generation"]) != generation
                    or previous["snapshot_digest"] != digest
                    or _digest(predecessor) != digest
                ):
                    raise WorkflowAuthorityError("authority_pointer_not_ready")
            fence_journal_id = f"WAJ-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO workflow_publication_journals("
                "id,namespace,generation,expected_generation,snapshot_bytes,"
                "snapshot_digest,publisher,publisher_invocation,profile_fence,"
                "state,recovery_owner,file_phase_owner"
                ") VALUES (?,?,?,?,?,?,?,?,?,'prepared','workflow-recovery',NULL)",
                (
                    fence_journal_id,
                    self.namespace,
                    generation + 1,
                    generation,
                    predecessor,
                    _digest(predecessor),
                    reason,
                    publisher_invocation,
                    profile_fence,
                ),
            )
            conn.execute(
                "INSERT INTO workflow_authority_pointers("
                "namespace,current_generation,journal_id,snapshot_digest,state,profile_fence"
                ") VALUES (?,?,?,?, 'fenced',?) "
                "ON CONFLICT(namespace) DO UPDATE SET "
                "state='fenced',profile_fence=excluded.profile_fence",
                (
                    self.namespace, generation, journal_id, digest,
                    profile_fence,
                ),
            )
            self._cache.pop(self.namespace, None)
            return fence_journal_id

    @contextmanager
    def profile_change_interval(
        self,
        *,
        reason: str,
        coordinator_invocation: str,
        resume: bool = False,
        expected_revision: tuple | None = None,
    ) -> Iterator[ProfileFenceBinding]:
        """Hold the org publisher gate for one machine-profile operation.

        The machine-global profile lease is acquired by the caller before this
        interval, establishing the only supported order: profile lease then
        org publication gate/lease.  The durable publication lease is held
        only while the profile fence is created; the process gate stays held
        through the caller's store mutation and bound republish.
        """
        with self._publisher_lock:
            publisher_invocation = f"profile-coordinator:{coordinator_invocation}"
            if resume:
                with self._db._lock:
                    active = self._active_journal(self._db._conn, self.namespace)
                    if (
                        active is not None
                        and str(active["state"]) == "prepared"
                        and str(active["publisher_invocation"])
                        == publisher_invocation
                    ):
                        existing_binding = ProfileFenceBinding(
                            journal_id=str(active["id"]),
                            publisher_invocation=publisher_invocation,
                            profile_fence=int(active["profile_fence"]),
                        )
                    else:
                        existing_binding = None
                if existing_binding is not None:
                    yield existing_binding
                    return
            owner = f"{publisher_invocation}:{uuid.uuid4().hex}"
            self._acquire_lease(owner)
            try:
                if expected_revision is None:
                    with self._db._lock:
                        active = self._active_journal(self._db._conn, self.namespace)
                    if active is not None and not str(active["publisher_invocation"]).startswith("profile-coordinator:"):
                        raise WorkflowAuthorityError("authority_publication_in_progress")
                if expected_revision is not None:
                    with self._db._lock:
                        pointer = self._pointer(self._db._conn, self.namespace)
                        active = self._active_journal(self._db._conn, self.namespace)
                        current = (pointer, None if active is None else tuple(active))
                    if current != expected_revision:
                        raise WorkflowAuthorityError("authority_capture_changed")
                journal_id = self._fence_under_lease(
                    reason=reason,
                    publisher_invocation=publisher_invocation,
                    increment_profile_fence=True,
                )
                with self._db._lock:
                    active = self._active_journal(self._db._conn, self.namespace)
                    if active is None or str(active["id"]) != journal_id:
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    profile_fence = int(active["profile_fence"])
            finally:
                self._release_lease(owner)
            yield ProfileFenceBinding(
                journal_id=journal_id,
                publisher_invocation=publisher_invocation,
                profile_fence=profile_fence,
            )

    def publish_profile_change(
        self,
        *,
        publisher: str,
        binding: ProfileFenceBinding,
        canonical_snapshot: bytes | None = None,
    ) -> int:
        """Publish a profile-only generation without rescanning org files.

        The prepared journal already contains the coherent predecessor.  Only
        the machine-profile projection changes, so patching that field avoids
        holding the profile lease across an agent/team filesystem scan.
        """
        with self._publisher_lock, self._db._lock:
            active = self._active_journal(self._db._conn, self.namespace)
            if (
                active is None
                or str(active["id"]) != binding.journal_id
                or str(active["publisher_invocation"])
                != binding.publisher_invocation
                or int(active["profile_fence"]) != binding.profile_fence
            ):
                raise WorkflowAuthorityError("publication_fence_superseded")
            predecessor = json.loads(bytes(active["snapshot_bytes"]))
        if not isinstance(predecessor, dict):
            raise WorkflowAuthorityError("authority_snapshot_not_canonical")
        if canonical_snapshot is not None:
            predecessor = json.loads(canonical_snapshot)
        profiles = self._profile_projection()
        if self._profile_coordinator is not None:
            self._profile_coordinator.validate_requirements(predecessor["agents"], profiles)
        predecessor["machine_global_profiles"] = profiles
        snapshot = _canonical_json(predecessor)
        return self._publish_current_locked(
            publisher=publisher,
            fence_journal_id=binding.journal_id,
            publisher_invocation=binding.publisher_invocation,
            snapshot_override=snapshot,
        )

    def _begin_publication_fence(self, *, publisher: str) -> tuple[str, str]:
        invocation = f"workflow-writer:{publisher}:{uuid.uuid4().hex}"
        self._acquire_lease(invocation)
        try:
            journal_id = self._fence_under_lease(
                reason=publisher,
                publisher_invocation=invocation,
            )
        finally:
            self._release_lease(invocation)
        return journal_id, invocation

    def fence(self, *, reason: str) -> None:
        """Durably deny later workflow admission before an authority mutation."""
        with self._publisher_lock:
            owner = f"workflow-fence:{uuid.uuid4().hex}"
            self._acquire_lease(owner)
            try:
                self._fence_under_lease(
                    reason=reason,
                    publisher_invocation=owner,
                )
            finally:
                self._release_lease(owner)

    @contextmanager
    def writer_interval(
        self,
        *,
        publisher: str,
    ) -> Iterator[_SupportedWriterInterval]:
        """Own a synchronous writer batch while leasing only mutations."""
        with self._publisher_lock:
            interval = _SupportedWriterInterval(self, publisher=publisher)
            try:
                yield interval
            except BaseException:
                if interval.fenced:
                    self.publish_after_supported_change(
                        publisher=f"{publisher}:compensation",
                        fence_journal_id=interval.fence_journal_id,
                        publisher_invocation=interval.publisher_invocation,
                    )
                raise
            else:
                if interval.fenced:
                    self.publish_after_supported_change(
                        publisher=publisher,
                        fence_journal_id=interval.fence_journal_id,
                        publisher_invocation=interval.publisher_invocation,
                    )

        # The one-shot compatibility writer discovers workspace YAML while
        # owning only its process gate. Reconcile its completed canonical batch
        # after that gate releases, so profile acquisition never reverses the
        # profile -> org publisher order and discovery never runs under leases.
        if (
            publisher == "agent_yaml_frontmatter_migration"
            and interval.fenced and self._profile_coordinator is not None
        ):
            try:
                self._profile_coordinator.reconcile_supported_roster_batch(
                    self, completed_writer_invocation=interval.publisher_invocation,
                )
            except Exception:
                logger.exception(
                    "profile closure remains fenced after supported roster batch org=%s",
                    self._org_slug,
                )

    @asynccontextmanager
    async def async_writer_interval(
        self,
        *,
        publisher: str,
    ) -> AsyncIterator[_SupportedWriterInterval]:
        """Own one async writer through its terminal success/compensation.

        The process gate may span awaits, scans, clone/network work, or host
        bootstrap. Durable ownership is acquired separately by
        ``canonical_change`` and therefore never spans those operations.
        Every async participating route uses this outer lock, while the
        thread lock also serializes synchronous startup and thread-pool
        writers with the interval.
        """
        async with self._async_writer_lock:
            with self._publisher_lock:
                interval = _SupportedWriterInterval(self, publisher=publisher)
                try:
                    yield interval
                except BaseException:
                    if interval.fenced:
                        self.publish_after_supported_change(
                            publisher=f"{publisher}:compensation",
                            fence_journal_id=interval.fence_journal_id,
                            publisher_invocation=interval.publisher_invocation,
                        )
                    raise
                else:
                    if interval.fenced:
                        self.publish_after_supported_change(
                            publisher=publisher,
                            fence_journal_id=interval.fence_journal_id,
                            publisher_invocation=interval.publisher_invocation,
                        )

    @contextmanager
    def supported_change(self, *, publisher: str) -> Iterator[None]:
        """Fence one synchronous supported writer through its mutation.

        The durable publication lease covers only the synchronous canonical
        mutation/compensation span.  It is released before snapshot capture,
        while the process-local writer gate remains held through publication.
        This keeps scans and later network/host work outside the durable lease.
        A failed legacy mutation is republished only when its own compensation
        has already restored a coherent snapshot; incoherence stays fenced.
        """
        with self.writer_interval(publisher=publisher) as interval:
            with interval.canonical_change():
                yield

    @asynccontextmanager
    async def supported_change_async(self, *, publisher: str) -> AsyncIterator[None]:
        """Async-route form for a synchronous canonical mutation body."""
        async with self.async_writer_interval(publisher=publisher) as interval:
            with interval.canonical_change():
                yield

    def ensure_authority_selector(
        self,
        *,
        team: str,
        publisher: str,
    ) -> AuthorityPolicySelector:
        """Initialize a missing selector inside one conditional writer interval.

        The authenticated existing-selector path is read-only and therefore
        does not fence or advance the authority generation. The process gate
        closes the ordinary in-process check-to-initialize race; the durable
        invocation binding keeps independent coordinators fail-closed.
        """
        store = AuthorityPolicyStore(self._db)
        with self.writer_interval(publisher=publisher) as interval:
            selector = store.get_authority_selector(team)
            if selector is None:
                with interval.canonical_change():
                    selector = store.ensure_authority_selector(team)
            return selector

    def publish_current(
        self,
        *,
        publisher: str,
        fence_journal_id: str | None = None,
        publisher_invocation: str | None = None,
    ) -> int:
        """Capture then publish the next generation through durable stages."""
        with self._publisher_lock:
            if (fence_journal_id is None) != (publisher_invocation is None):
                raise WorkflowAuthorityError("publication_fence_binding_incomplete")
            if fence_journal_id is None or publisher_invocation is None:
                fence_journal_id, publisher_invocation = self._begin_publication_fence(
                    publisher=publisher,
                )
            return self._publish_current_locked(
                publisher=publisher,
                fence_journal_id=fence_journal_id,
                publisher_invocation=publisher_invocation,
            )

    def _publish_current_locked(
        self,
        *,
        publisher: str,
        fence_journal_id: str,
        publisher_invocation: str,
        snapshot_override: bytes | None = None,
    ) -> int:
        """Publish while the process-local writer/publisher gate is held."""
        snapshot = (
            self.capture_snapshot()
            if snapshot_override is None
            else snapshot_override
        )
        snapshot_digest = _digest(snapshot)
        with self._publisher_lock:
            owner = f"{publisher}:{uuid.uuid4().hex}"
            self._acquire_lease(owner)
            try:
                with self._transaction() as conn:
                    active = self._active_journal(conn, self.namespace)
                    generation, _old_journal, _old_digest, state, profile_fence = self._pointer(
                        conn, self.namespace,
                    )
                    if (
                        state != "fenced"
                        or active is None
                        or str(active["id"]) != fence_journal_id
                        or str(active["state"]) != "prepared"
                        or str(active["publisher_invocation"]) != publisher_invocation
                        or int(active["expected_generation"]) != generation
                        or int(active["generation"]) != generation + 1
                        or int(active["profile_fence"]) != profile_fence
                    ):
                        raise WorkflowAuthorityError("publication_fence_superseded")
                    changed = conn.execute(
                        "UPDATE workflow_publication_journals SET "
                        "snapshot_bytes=?,snapshot_digest=?,publisher=? "
                        "WHERE id=? AND namespace=? AND state='prepared' "
                        "AND publisher_invocation=?",
                        (
                            snapshot,
                            snapshot_digest,
                            publisher,
                            fence_journal_id,
                            self.namespace,
                            publisher_invocation,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise WorkflowAuthorityError("publication_fence_superseded")
                with self._transaction() as conn:
                    changed = conn.execute(
                        "UPDATE workflow_publication_journals "
                        "SET state='file_phase_reserved',file_phase_owner=? "
                        "WHERE id=? AND namespace=? AND state='prepared' "
                        "AND publisher_invocation=?",
                        (
                            owner,
                            fence_journal_id,
                            self.namespace,
                            publisher_invocation,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise WorkflowAuthorityError("publication_phase_owner_required")
                path = self.canonical_path
                path.parent.mkdir(parents=True, exist_ok=True)
                staging = path.with_name(
                    f"{path.name}.{fence_journal_id}.staging",
                )
                staging.write_bytes(snapshot)
                os.replace(staging, path)
                with self._transaction() as conn:
                    changed = conn.execute(
                        "UPDATE workflow_publication_journals "
                        "SET state='canonical_published' "
                        "WHERE id=? AND namespace=? AND state='file_phase_reserved' "
                        "AND file_phase_owner=?",
                        (fence_journal_id, self.namespace, owner),
                    ).rowcount
                    if changed != 1:
                        raise WorkflowAuthorityError("publication_phase_owner_required")
                with self._transaction() as conn:
                    current, _old_id, _old_digest, pointer_state, current_profile_fence = self._pointer(
                        conn, self.namespace,
                    )
                    active = self._active_journal(conn, self.namespace)
                    if (
                        current != generation
                        or pointer_state != "fenced"
                        or current_profile_fence != profile_fence
                        or active is None
                        or str(active["id"]) != fence_journal_id
                        or str(active["publisher_invocation"]) != publisher_invocation
                    ):
                        raise WorkflowAuthorityError("publication_pointer_cas_stale")
                    changed = conn.execute(
                        "UPDATE workflow_publication_journals "
                        "SET state='pointer_committed' "
                        "WHERE id=? AND namespace=? AND state='canonical_published' "
                        "AND file_phase_owner=?",
                        (fence_journal_id, self.namespace, owner),
                    ).rowcount
                    if changed != 1:
                        raise WorkflowAuthorityError("publication_pointer_cas_stale")
                    conn.execute(
                        "INSERT INTO workflow_authority_pointers("
                        "namespace,current_generation,journal_id,snapshot_digest,state,profile_fence"
                        ") VALUES (?,?,?,?, 'ready',?) "
                        "ON CONFLICT(namespace) DO UPDATE SET "
                        "current_generation=excluded.current_generation,"
                        "journal_id=excluded.journal_id,"
                        "snapshot_digest=excluded.snapshot_digest,state='ready',"
                        "profile_fence=excluded.profile_fence",
                        (
                            self.namespace, generation + 1, fence_journal_id,
                            snapshot_digest, profile_fence,
                        ),
                    )
                self._cache[self.namespace] = (generation + 1, snapshot_digest)
                with self._transaction() as conn:
                    changed = conn.execute(
                        "UPDATE workflow_publication_journals SET state='cache_installed' "
                        "WHERE id=? AND namespace=? AND state='pointer_committed' "
                        "AND file_phase_owner=?",
                        (fence_journal_id, self.namespace, owner),
                    ).rowcount
                    if changed != 1:
                        self._cache.pop(self.namespace, None)
                        raise WorkflowAuthorityError("publication_cache_stamp_failed")
                return generation + 1
            finally:
                self._release_lease(owner)

    def publish_after_supported_change(
        self,
        *,
        publisher: str,
        fence_journal_id: str | None = None,
        publisher_invocation: str | None = None,
    ) -> bool:
        """Best-effort post-commit publication preserving the writer contract.

        A supported writer fences before its legacy mutation. Once that legacy
        write has committed, publication failure must not falsely report that
        the legacy write rolled back. The route therefore keeps its existing
        result while this method logs the bounded failure and leaves the
        pointer fenced for startup or a later supported writer to recover.
        """
        try:
            self.publish_current(
                publisher=publisher,
                fence_journal_id=fence_journal_id,
                publisher_invocation=publisher_invocation,
            )
        except Exception:
            logger.exception(
                "workflow authority publication failed after supported writer "
                "org=%s publisher=%s; pointer remains fenced",
                self._org_slug,
                publisher,
            )
            return False
        return True

    def _verify_ready_locked(self, conn: sqlite3.Connection) -> AuthorityReadiness:
        if self._active_journal(conn, self.namespace) is not None:
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        generation, journal_id, digest, state, _profile_fence = self._pointer(
            conn, self.namespace,
        )
        if generation == 0:
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        if state != "ready" or journal_id is None or digest is None:
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        journal = self._journal(conn, journal_id)
        snapshot = bytes(journal["snapshot_bytes"])
        if (
            int(journal["generation"]) != generation
            or journal["snapshot_digest"] != digest
            or journal["state"] != "cache_installed"
            or _digest(snapshot) != digest
        ):
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        if not self.canonical_path.is_file() or self.canonical_path.read_bytes() != snapshot:
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        if self._cache.get(self.namespace) != (generation, digest):
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        return AuthorityReadiness(self.namespace, generation, digest, snapshot)

    def verify_admission_ready(self) -> AuthorityReadiness:
        """Read-only readiness check for later units; U2A has no consumer."""
        with self._publisher_lock, self._db._lock:
            return self._verify_ready_locked(self._db._conn)

    def capture_admission(self) -> AuthorityAdmissionCapture:
        """Capture the complete org and profile closure without leasing scans."""
        with self._db._lock:
            before = self._pointer(self._db._conn, self.namespace)
        ready = self.verify_admission_ready()
        snapshot = self.capture_snapshot()
        if snapshot != ready.snapshot_bytes:
            raise WorkflowAuthorityError("workflow_activation_authority_stale")
        projection = json.loads(snapshot)["machine_global_profiles"]
        names = tuple(sorted(item["profile_name"] for item in projection))
        coordinator = self._profile_coordinator
        if coordinator is None:
            raise WorkflowAuthorityError("authority_profile_coordinator_unavailable")
        mirror = coordinator._dependency_mirror_snapshot(coordinator.orgs[self._org_slug])
        effective = tuple((name, coordinator.profile_digest(name)) for name in names)
        if any(dict(effective)[item["profile_name"]] != item["profile_digest"] for item in projection):
            raise WorkflowAuthorityError("authority_pointer_not_ready")
        with self._db._lock:
            if before != self._pointer(self._db._conn, self.namespace):
                raise WorkflowAuthorityError("workflow_activation_authority_stale")
        return AuthorityAdmissionCapture(ready, before, names, effective, mirror)

    @contextmanager
    def _admission_transaction(self) -> Iterator[sqlite3.Connection]:
        """Retain writer ownership through rollback of a failed admission commit.

        The shared publication transaction contract is unchanged. Activation
        and prelaunch require their failed commit to roll back before the org
        publication lease can be released or another writer can observe it.
        """
        with self._db._lock:
            conn = self._db._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def admission_writer(self, capture: AuthorityAdmissionCapture) -> Iterator[sqlite3.Connection]:
        """Consume captured bytes under profile→org→SQLite ownership.

        Async consumers acquire their coroutine locks before entering. All
        discovery/effective profile reads precede this context. The yielded
        connection is the sole admission transaction; no await or host work
        may occur until this context exits.
        """
        coordinator = self._profile_coordinator
        if coordinator is None:
            raise WorkflowAuthorityError("authority_profile_coordinator_unavailable")
        with ExitStack() as stack:
            for name in capture.profile_names:
                stack.enter_context(coordinator.profile_read(name))
                coordinator._assert_no_active_operation(name)
            stack.enter_context(self._publisher_lock)
            owner = f"workflow-admission:{uuid.uuid4().hex}"
            self._acquire_lease(owner)
            try:
                with self._admission_transaction() as conn:
                    pointer = self._pointer(conn, self.namespace)
                    if pointer != capture.pointer or self._active_journal(conn, self.namespace) is not None:
                        raise WorkflowAuthorityError("workflow_activation_authority_stale")
                    generation, journal_id, digest, state, _fence = pointer
                    if state != "ready" or journal_id is None or self._cache.get(self.namespace) != (generation, digest):
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    journal = self._journal(conn, journal_id)
                    if (journal["state"] != "cache_installed" or journal["generation"] != generation
                            or journal["snapshot_digest"] != digest
                            or bytes(journal["snapshot_bytes"]) != capture.ready.snapshot_bytes
                            or _digest(capture.ready.snapshot_bytes) != digest):
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    # SELECT-only projection and mirror checks consume captured
                    # global digests; never call the file-reading readiness or
                    # effective-profile resolver under these ownership scopes.
                    org = coordinator.orgs[self._org_slug]
                    if coordinator._dependency_mirror_snapshot(org) != capture.profile_mirror:
                        raise WorkflowAuthorityError("workflow_activation_authority_stale")
                    profiles = self._profile_projection()
                    if (tuple(sorted(item["profile_name"] for item in profiles)) != capture.profile_names
                            or profiles != json.loads(capture.ready.snapshot_bytes)["machine_global_profiles"]
                            or any(dict(capture.profile_digests)[item["profile_name"]] != item["profile_digest"]
                                   for item in profiles)):
                        raise WorkflowAuthorityError("workflow_activation_authority_stale")
                    yield conn
            finally:
                self._release_lease(owner)

    def recover(self) -> str:
        """Reconcile one interrupted publisher using only durable ownership."""
        with self._publisher_lock:
            # A prepared journal has not reserved or replaced canonical bytes.
            # Inspect it without acquiring the write lease so an incoherent
            # cold-start re-capture performs zero durable writes. Publication
            # still validates the journal id and invocation after capture.
            with self._db._lock:
                if self._prepared_recovery_binding(self._db._conn) is not None:
                    return "prepared_unpublished"
            owner = f"workflow-recovery:{uuid.uuid4().hex}"
            self._acquire_lease(owner)
            try:
                with self._db._lock:
                    active = self._active_journal(self._db._conn, self.namespace)
                    pointer = self._pointer(self._db._conn, self.namespace)
                if active is None:
                    generation, journal_id, digest, state, _profile_fence = pointer
                    if generation == 0:
                        self._cache.pop(self.namespace, None)
                        return "uninitialized_no_authority"
                    if state == "fenced":
                        self._cache.pop(self.namespace, None)
                        return "fenced_no_admission"
                    if journal_id is None or digest is None:
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    with self._db._lock:
                        journal = self._journal(self._db._conn, journal_id)
                        snapshot = bytes(journal["snapshot_bytes"])
                    if (
                        journal["state"] != "cache_installed"
                        or int(journal["generation"]) != generation
                        or journal["snapshot_digest"] != digest
                        or not self.canonical_path.is_file()
                        or self.canonical_path.read_bytes() != snapshot
                    ):
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    self._cache[self.namespace] = (generation, digest)
                    return "rehydrated_coherent"

                journal_id = str(active["id"])
                generation = int(active["generation"])
                expected = int(active["expected_generation"])
                snapshot = bytes(active["snapshot_bytes"])
                digest = str(active["snapshot_digest"])
                state = str(active["state"])
                if generation != expected + 1 or _digest(snapshot) != digest:
                    raise WorkflowAuthorityError("authority_active_journal_invalid")
                path = self.canonical_path
                staging = path.with_name(f"{path.name}.{journal_id}.staging")

                if state == "prepared":
                    # No canonical bytes were reserved or replaced. Preserve
                    # this invocation-owned fence so cold recovery can
                    # re-capture against it without churning durable state.
                    # A concurrent writer may still supersede this pre-file
                    # state; the bound publication CAS then refuses the stale
                    # recovery attempt.
                    return "prepared_unpublished"

                if state == "file_phase_reserved":
                    if staging.is_file():
                        if staging.read_bytes() != snapshot:
                            raise WorkflowAuthorityError("authority_staging_snapshot_mismatch")
                        os.replace(staging, path)
                        state = "canonical_published"
                        with self._transaction() as conn:
                            conn.execute(
                                "UPDATE workflow_publication_journals "
                                "SET state='canonical_published' WHERE id=? AND namespace=?",
                                (journal_id, self.namespace),
                            )
                    elif path.is_file() and path.read_bytes() == snapshot:
                        state = "canonical_published"
                        with self._transaction() as conn:
                            conn.execute(
                                "UPDATE workflow_publication_journals "
                                "SET state='canonical_published' WHERE id=? AND namespace=?",
                                (journal_id, self.namespace),
                            )
                    else:
                        with self._transaction() as conn:
                            conn.execute(
                                "UPDATE workflow_publication_journals SET state='aborted' "
                                "WHERE id=? AND namespace=?",
                                (journal_id, self.namespace),
                            )
                        return "aborted_unpublished"

                if state in {"canonical_published", "forward_recovery_required"}:
                    if path.is_file() and path.read_bytes() != snapshot:
                        raise WorkflowAuthorityError("authority_canonical_snapshot_mismatch")
                    if not path.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(snapshot)
                    with self._transaction() as conn:
                        current, _jid, _old_digest, _pointer_state, profile_fence = self._pointer(
                            conn, self.namespace,
                        )
                        if current != expected or profile_fence != int(active["profile_fence"]):
                            raise WorkflowAuthorityError("publication_pointer_cas_stale")
                        conn.execute(
                            "UPDATE workflow_publication_journals "
                            "SET state='pointer_committed' WHERE id=? AND namespace=?",
                            (journal_id, self.namespace),
                        )
                        conn.execute(
                            "INSERT INTO workflow_authority_pointers VALUES (?,?,?,?, 'ready',?) "
                            "ON CONFLICT(namespace) DO UPDATE SET "
                            "current_generation=excluded.current_generation,"
                            "journal_id=excluded.journal_id,"
                            "snapshot_digest=excluded.snapshot_digest,state='ready',"
                            "profile_fence=excluded.profile_fence",
                            (
                                self.namespace, generation, journal_id, digest,
                                int(active["profile_fence"]),
                            ),
                        )
                    state = "pointer_committed"

                if state == "pointer_committed":
                    if not path.is_file() or path.read_bytes() != snapshot:
                        raise WorkflowAuthorityError("authority_pointer_not_ready")
                    self._cache[self.namespace] = (generation, digest)
                    with self._transaction() as conn:
                        conn.execute(
                            "UPDATE workflow_publication_journals "
                            "SET state='cache_installed' WHERE id=? AND namespace=?",
                            (journal_id, self.namespace),
                        )
                    return "recovered_coherent"
                raise WorkflowAuthorityError("authority_recovery_state_unknown")
            finally:
                self._release_lease(owner)

    def recover_or_publish(self, *, publisher: str = "org-startup") -> int:
        """Cold-start reconciliation followed by one initial/fenced republish."""
        outcome = self.recover()
        if outcome == "prepared_unpublished":
            with self._db._lock:
                binding = self._prepared_recovery_binding(self._db._conn)
                if binding is None:
                    raise WorkflowAuthorityError("publication_fence_superseded")
                fence_journal_id, publisher_invocation = binding
            return self.publish_current(
                publisher=publisher,
                fence_journal_id=fence_journal_id,
                publisher_invocation=publisher_invocation,
            )
        if outcome in {"uninitialized_no_authority", "fenced_no_admission", "aborted_unpublished"}:
            return self.publish_current(publisher=publisher)
        return self.verify_admission_ready().generation
