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
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
from runtime.workflows.authority import ProfileFenceBinding

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


class ProfileCoordinator:
    """Coordinate global profile writers with all dependent organizations."""

    def __init__(
        self,
        *,
        daemon_home: Path,
        orgs: dict[str, OrgState],
        lock_wait_seconds: float = _LOCK_WAIT_SECONDS,
    ) -> None:
        self._daemon_home = Path(daemon_home)
        self.orgs = orgs
        self._lock_wait_seconds = lock_wait_seconds

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
                    if not wait or time.monotonic() >= deadline:
                        raise ProfileCoordinatorError(
                            "profile_coordinator_busy"
                        ) from exc
                    time.sleep(_LOCK_POLL_SECONDS)
            yield
        except ProfileCoordinatorError:
            raise
        except OSError as exc:
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
            for name in names:
                self._assert_no_active_operation(name)
            yield

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
                conn.commit()

    def _max_generation(self, profile_name: str) -> int:
        generation = 0
        for org in self.orgs.values():
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
        members: list[str] = []
        for slug, org in sorted(self.orgs.items()):
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

    def _assert_no_active_operation(self, profile_name: str) -> None:
        for org in self.orgs.values():
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
        members_json = _canonical_bytes(list(operation.members)).decode("utf-8")
        created_at = datetime.now(timezone.utc).isoformat()
        for slug in operation.members:
            org = self.orgs[slug]
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
        for slug in operation.members:
            org = self.orgs[slug]
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
    ) -> None:
        for operation in operations:
            for slug in operation.members:
                org = self.orgs[slug]
                try:
                    with self._transaction(org) as conn:
                        conn.execute(
                            "DELETE FROM workflow_profile_leases "
                            "WHERE profile_name=? AND owner_token=?",
                            (operation.profile_name, coordinator_invocation),
                        )
                except Exception:
                    # This row is diagnostic only; the kernel flock is the
                    # ownership authority and is still released by the outer
                    # context. Startup will overwrite a stale diagnostic row.
                    logger.exception(
                        "profile diagnostic lease cleanup failed org=%s profile=%s",
                        slug,
                        operation.profile_name,
                    )

    def _apply_store_state(
        self,
        operation: _ProfileOperation,
        effective: _EffectiveProfile,
    ) -> None:
        for slug in operation.members:
            org = self.orgs[slug]
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
        publishable = effective.state == "removed" or effective.resolvable
        for slug in operation.members:
            org = self.orgs[slug]
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

    def _closure_coherent(self, org: OrgState) -> bool:
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
            if not (
                row["state"] == "active"
                and row["store_state"] == "active"
                and row["generation"] is not None
                and int(row["generation"]) == int(row["bound_generation"])
                and row["published_generation"] is not None
                and int(row["published_generation"])
                == int(row["bound_generation"])
                and row["profile_digest"] == self.profile_digest(profile_name)
                and self._effective_profile(profile_name).resolvable
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
    ) -> bool:
        all_published = True
        for slug in sorted(expected_fenced):
            org = self.orgs[slug]
            if not self._closure_coherent(org):
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
    ) -> None:
        fenced = set(bindings)
        changed_operations: list[_ProfileOperation] = []
        unchanged_operations: list[_ProfileOperation] = []
        for operation in operations:
            effective = self._effective_profile(operation.profile_name)
            changed = (
                effective.digest != operation.prior.digest
                or operation.recovery_state
                in {"store_committed", "published", "forward_recovery_required"}
            )
            if changed:
                self._apply_store_state(operation, effective)
                publishable = self._apply_registry_state(operation, effective)
                if not publishable:
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
        )
        if republished:
            for operation in changed_operations:
                effective = self._effective_profile(operation.profile_name)
                if effective.state == "removed" or effective.resolvable:
                    self._set_operation_state(
                        operation, "published", profile_digest=effective.digest,
                    )
            for operation in unchanged_operations:
                self._set_operation_state(operation, "aborted")

    @contextmanager
    def _operation_with_leases_held(
        self,
        names: Sequence[str],
        *,
        operation_kind: str,
        publisher: str,
    ) -> Iterator[None]:
        """Create the durable U1A claim after profile leases are held."""
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
        for operation in operations:
            self._insert_operation_rows(
                operation,
                coordinator_invocation=coordinator_invocation,
            )
        bindings: dict[str, ProfileFenceBinding] = {}
        members = sorted({slug for op in operations for slug in op.members})
        with ExitStack() as org_stack:
            for slug in members:
                bindings[slug] = org_stack.enter_context(
                    self.orgs[slug].workflow_authority.profile_change_interval(
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
                    self._finalize(
                        operations=operations,
                        bindings=bindings,
                        publisher=publisher,
                        mutation_succeeded=False,
                    )
                except Exception:
                    logger.exception(
                        "profile compensation publication deferred publisher=%s",
                        publisher,
                    )
                raise
            else:
                try:
                    self._finalize(
                        operations=operations,
                        bindings=bindings,
                        publisher=publisher,
                        mutation_succeeded=True,
                    )
                except Exception:
                    # The existing writer already committed. Preserve its
                    # response contract while the durable operation/fence
                    # remains discoverable for cold forward recovery.
                    logger.exception(
                        "profile post-commit publication deferred publisher=%s",
                        publisher,
                    )
            finally:
                self._release_diagnostic_leases(
                    operations,
                    coordinator_invocation=coordinator_invocation,
                )

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
            if not names:
                yield
                return
            for name in names:
                self._assert_no_active_operation(name)
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
            if not names:
                yield True
                return
            for name in names:
                self._assert_no_active_operation(name)
            if terminal_check():
                yield False
                return
            with self._operation_with_leases_held(
                names,
                operation_kind=operation_kind,
                publisher=publisher,
            ):
                yield True

    def _dependency_profiles_for_org(self, org: OrgState) -> tuple[str, ...]:
        """Return canonical desired and outstanding profile names for one org."""
        definitions = list(prompt_loader.list_agents(OrgPaths(root=org.root)))
        registry = get_registry()
        desired = {
            definition.name: definition.executor.lower()
            for definition in definitions
            if (
                (profile := registry.get_profile(definition.executor)) is None
                or profile.kind != "builtin"
            )
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
        return tuple(sorted(set(desired.values()) | outstanding))

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
        return tuple(
            [("dependency", *tuple(row)) for row in dependencies]
            + [("store", *tuple(row)) for row in stores]
            + [("registry", *tuple(row)) for row in registry]
        )

    def _sync_org_dependencies(self, org: OrgState) -> bool:
        """Mirror canonical active-agent profile requirements into one org DB."""
        definitions = list(prompt_loader.list_agents(OrgPaths(root=org.root)))
        registry = get_registry()
        desired = {
            definition.name: definition.executor.lower()
            for definition in definitions
            if (
                (profile := registry.get_profile(definition.executor)) is None
                or profile.kind != "builtin"
            )
        }
        # Resolve the machine-global YAML/registry view before opening the
        # org transaction.  No SQLite transaction spans filesystem I/O.
        effective_profiles = {
            profile_name: self._effective_profile(profile_name)
            for profile_name in sorted(set(desired.values()))
        }
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
                        "active" if effective.state == "active" else "unbound",
                    ),
                )
        after = self._dependency_mirror_snapshot(org)
        return before != after

    def synchronize_all_dependencies(self, *, publish: bool = True) -> set[str]:
        """Mirror all consumers while holding canonical profile leases."""
        profile_names = sorted(
            {
                profile_name
                for org in self.orgs.values()
                for profile_name in self._dependency_profiles_for_org(org)
            }
        )
        with self._profile_leases(profile_names, wait=True) as names:
            for name in names:
                self._assert_no_active_operation(name)
            changed = {
                slug
                for slug, org in sorted(self.orgs.items())
                if self._sync_org_dependencies(org)
            }
            if publish:
                for slug in sorted(changed):
                    self._publish_dependency_change(self.orgs[slug])
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
        profile_names = self._dependency_profiles_for_org(org)
        with self._profile_leases(profile_names, wait=True) as names:
            for name in names:
                self._assert_no_active_operation(name)
            changed = self._sync_org_dependencies(org)
            if changed or not self._authority_profile_projection_coherent(org):
                if not self._publish_dependency_change(org):
                    raise ProfileCoordinatorError("profile_dependency_incoherent")
            if (
                not self._closure_coherent(org)
                or not self._authority_profile_projection_coherent(org)
            ):
                raise ProfileCoordinatorError("profile_dependency_incoherent")
            # The caller inserts the org into the shared mapping before these
            # profile leases are released, so the next writer must capture it.
            yield

    def _publish_dependency_change(self, org: OrgState) -> bool:
        invocation = f"dependency-sync-{uuid.uuid4().hex}"
        with org.workflow_authority.profile_change_interval(
            reason="profile:dependency-sync",
            coordinator_invocation=invocation,
        ) as binding:
            if self._closure_coherent(org):
                org.workflow_authority.publish_profile_change(
                    publisher="profile-dependency-sync",
                    binding=binding,
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
        groups: dict[str, set[str]] = {}
        for org in self.orgs.values():
            with org.db._lock:
                rows = org.db._conn.execute(
                    "SELECT coordinator_invocation,profile_name "
                    "FROM workflow_profile_operations "
                    "WHERE state NOT IN ('published','aborted')"
                ).fetchall()
            for row in rows:
                groups.setdefault(str(row["coordinator_invocation"]), set()).add(
                    str(row["profile_name"])
                )
        return groups

    def _load_group_operations(
        self,
        coordinator_invocation: str,
        profile_names: Sequence[str],
    ) -> tuple[_ProfileOperation, ...]:
        operation_rows: dict[str, list[sqlite3.Row]] = {}
        for org in self.orgs.values():
            with org.db._lock:
                rows = org.db._conn.execute(
                    "SELECT id,profile_name,operation_kind,captured_members,"
                    "target_generation,state,profile_digest "
                    "FROM workflow_profile_operations "
                    "WHERE coordinator_invocation=?",
                    (coordinator_invocation,),
                ).fetchall()
            for row in rows:
                name = str(row["profile_name"])
                if name not in profile_names:
                    continue
                operation_rows.setdefault(name, []).append(row)
        if set(operation_rows) != set(profile_names):
            raise ProfileCoordinatorError("profile_operation_missing")
        rank = {
            "captured": 0,
            "fenced": 1,
            "store_committed": 2,
            "forward_recovery_required": 3,
            "published": 4,
            "aborted": 4,
        }
        operations: dict[str, _ProfileOperation] = {}
        for name, rows in operation_rows.items():
            first = rows[0]
            identity = (
                str(first["id"]),
                str(first["operation_kind"]),
                str(first["captured_members"]),
                int(first["target_generation"]),
            )
            if any(
                (
                    str(row["id"]),
                    str(row["operation_kind"]),
                    str(row["captured_members"]),
                    int(row["target_generation"]),
                )
                != identity
                for row in rows[1:]
            ):
                raise ProfileCoordinatorError("profile_operation_incoherent")
            state = max((str(row["state"]) for row in rows), key=rank.__getitem__)
            prior_rows = [
                row for row in rows if str(row["state"]) in {"captured", "fenced"}
            ]
            prior_digest = str((prior_rows or rows)[0]["profile_digest"])
            operations[name] = _ProfileOperation(
                operation_id=identity[0],
                profile_name=name,
                operation_kind=identity[1],
                members=tuple(json.loads(identity[2])),
                target_generation=identity[3],
                prior=_EffectiveProfile(
                    digest=prior_digest,
                    state="active",
                    resolvable=False,
                ),
                recovery_state=state,
            )
        return tuple(operations[name] for name in sorted(operations))

    def _members_are_already_ready(
        self,
        operations: Sequence[_ProfileOperation],
    ) -> bool:
        members = {slug for operation in operations for slug in operation.members}
        for slug in members:
            org = self.orgs[slug]
            try:
                readiness = org.workflow_authority.verify_admission_ready()
            except Exception:
                return False
            if not self._closure_coherent(org):
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
    ) -> None:
        with self._profile_leases(profile_names, wait=False):
            operations = self._load_group_operations(
                coordinator_invocation, profile_names,
            )
            # A crash after all canonical publications but before the final
            # operation-row update must not manufacture another generation.
            if self._members_are_already_ready(operations):
                for operation in operations:
                    effective = self._effective_profile(operation.profile_name)
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
                        self.orgs[slug].workflow_authority.profile_change_interval(
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
                )
                self._release_diagnostic_leases(
                    operations,
                    coordinator_invocation=coordinator_invocation,
                )

    def reconcile_startup(self) -> None:
        """Recover interrupted work, then publish current exact dependencies."""
        for invocation, names in sorted(self._discover_operation_groups().items()):
            self._recover_group(invocation, sorted(names))
        changed = self.synchronize_all_dependencies(publish=False)
        for slug in sorted(changed):
            org = self.orgs[slug]
            try:
                org.workflow_authority.verify_admission_ready()
            except Exception:
                # An interrupted operation owns this fence; its recovery above
                # either completed it or intentionally left the org fenced.
                continue
            self._publish_dependency_change(org)
