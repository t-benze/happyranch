"""Private coordinated D operation owner; no public apply surface.

E must derive FounderAdmission at the existing admitted service boundary. These
models/internal methods confer no authentication. No constructor starts work.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import asdict, dataclass

from runtime.infrastructure.team_move_store import (
    CompletedReceipt, Diagnostic, ExpectedInputs, ForwardDecision, FounderAdmission,
    MoveRequest, Phase, Progress, PublicationIdentity, RecoveryIdentity, TeamMoveError,
    WorkspaceIdentity, _bytes, _decode, _digest, _now, _transaction, _unique_object,
)
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import parse_agent_text
from runtime.orchestrator.canonical_roster import canonical_roster
from runtime.orchestrator.team_move_canonical import AGENTS, capture_images, check_parents, read_regular, replace_owned, validate_candidate, _identity
from runtime.orchestrator.team_move_context import canonical, materialize_context, plan_context, verify_context


@dataclass(frozen=True)
class MovePreview:
    request: MoveRequest
    target_authority_bytes: bytes
    before_contexts: tuple
    after_contexts: tuple
    blockers: tuple[dict, ...]
    writer_epoch: int
    database_changes: tuple[int, int]


class TeamMoveOperation:
    def __init__(self, org, profiles):
        self.org, self.profiles = org, profiles
        self.move = org.db._team_move_coordinator
        self.store = self.move.store

    def _current_inputs(self, images, before_contexts, after_contexts):
        from runtime.identities.registry import read_name_metadata
        authority = self.org.workflow_authority
        ready = authority.verify_admission_ready()
        snapshot = authority.capture_snapshot()
        if snapshot != ready.snapshot_bytes:
            raise TeamMoveError("team_move_current_authority_stale")
        labels = read_name_metadata(self.org)
        if any(agent not in labels or labels[agent].current_label is None for agent in (*AGENTS, "founder")):
            raise TeamMoveError("team_move_current_names_unavailable")
        paths = OrgPaths(self.org.root)
        config, _config_identity = read_regular(paths.org_config_path)
        self._validate_policy_config(config)
        profiles = authority._profile_projection()
        with self.org.db._lock:
            dependencies = self.profiles._dependency_mirror_snapshot(self.org)
        document = json.loads(snapshot)
        return ExpectedInputs(canonical_digest=_digest(snapshot + b"\x00" + config),
            roster_digest=images[-1].before_digest,
            naming_digest=_digest(canonical({key: asdict(value) for key, value in sorted(labels.items())})),
            profile_digest=_digest(canonical(profiles)), dependency_digest=_digest(canonical(dependencies)),
            policy_digest=_digest(canonical(self._policy_inputs([s["team"] for s in document["active_policy_selectors"]]))),
            skill_selection_digest=_digest(canonical([list(plan.specs) for plan in (*before_contexts, *after_contexts)])),
            permission_digest=_digest(canonical([plan.manifest() for plan in (*before_contexts, *after_contexts)])),
            authority_namespace=ready.namespace, authority_generation=ready.generation,
            authority_digest=ready.snapshot_digest, agent_revisions=tuple(image.before_digest for image in images[:2]))

    @staticmethod
    def _validate_policy_config(raw):
        import yaml
        try:
            config = yaml.safe_load(raw)
            policy = config.get("skills", {}) if isinstance(config, dict) else None
            if not isinstance(policy, dict) or set(policy) - {"org", "teams", "agents"}:
                raise ValueError("unknown skill policy")
            leaves = [policy.get("org", {})]
            for family in ("teams", "agents"):
                mapping = policy.get(family, {})
                if not isinstance(mapping, dict):
                    raise ValueError("unknown skill policy")
                leaves.extend(mapping.values())
            for leaf in leaves:
                if not isinstance(leaf, dict) or set(leaf) - {"allow", "deny"}:
                    raise ValueError("unknown skill policy")
                for items in leaf.values():
                    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                        raise ValueError("unknown skill policy")
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            raise TeamMoveError("team_move_policy_configuration_unsupported") from exc

    def _policy_inputs(self, teams):
        """Pin actual native selected policy records, including retired history."""
        from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
        from runtime.orchestrator.active_authority_policy import render_active_team_policy
        store = AuthorityPolicyStore(self.org.db)
        result = []
        for team in teams:
            selector = store.get_authority_selector(team)
            item = {"team": team, "selector": None if selector is None else selector.model_dump(mode="json"), "activation": None, "release": None}
            if selector is not None and selector.family == "legacy_v1":
                activation = store.get_activation(selector.legacy_activation_id)
                release = None if activation is None else store.get_release(activation.release_id)
                if activation is None or release is None or activation.team != team or release.team != team:
                    raise TeamMoveError("team_move_selected_policy_unknown")
                render_active_team_policy(release=release, activation=activation)
                item.update(activation=activation.model_dump(mode="json"), release=release.model_dump(mode="json"))
            elif selector is not None and selector.family == "v2":
                activation = store.get_v2_activation(selector.v2_activation_id)
                release = None if activation is None else store.get_v2_release(activation.release_id)
                if (activation is None or release is None or activation.team != team or release.team != team
                        or release.policy_digest != activation.release_digest or activation.selector_epoch != selector.selector_epoch):
                    raise TeamMoveError("team_move_selected_policy_unknown")
                item.update(activation=activation.model_dump(mode="json"), release=release.model_dump(mode="json"))
            elif selector is not None and selector.family != "empty":
                raise TeamMoveError("team_move_selected_policy_unknown")
            result.append(item)
        return result

    def _target_snapshot(self, request):
        """Project through native agent/team rendering without mutating a source."""
        from runtime.workflows.authority import _agent_projection, validate_authority_snapshot
        current = json.loads(self.org.workflow_authority.capture_snapshot())
        candidate = validate_candidate(self.org.root, request.files)
        definitions = {agent: parse_agent_text(base64.b64decode(image.after_base64).decode().replace("\r\n", "\n"), expected_name=agent)
            for agent, image in zip(AGENTS, request.files[:2])}
        current["agents"] = [_agent_projection(definitions[a["name"]], status="active") if a["name"] in definitions else a for a in current["agents"]]
        current["teams"] = [{"name": name, "manager": candidate.manager_for_team(name).manager_value(typed=True),
            "workers": sorted(candidate.manager_for_team(name).workers)} for name in candidate.teams()]
        current["active_policy_selectors"] = [s for s in current["active_policy_selectors"] if s["team"] != "consultant"]
        current["default_team"], current["task_default_team"] = candidate.default_team, candidate.task_default_team
        validate_authority_snapshot(current)
        return canonical(current)

    def preview(self, *, drain_threads: bool) -> MovePreview:
        """Pure selection/readback only; never invokes a mutating adapter."""
        if type(drain_threads) is not bool:
            raise TeamMoveError("team_move_drain_consent")
        with self.org.db._lock:
            initial = (self.move.producers.writer_epoch, self._database_observation())
        with canonical_roster(self.org.root):
            images = capture_images(self.org.root)
            before, after, workspaces = [], [], []
            for agent, image in zip(AGENTS, images[:2]):
                old = parse_agent_text(base64.b64decode(image.before_base64).decode().replace("\r\n", "\n"), expected_name=agent)
                new = parse_agent_text(base64.b64decode(image.after_base64).decode().replace("\r\n", "\n"), expected_name=agent)
                old_plan, new_plan = plan_context(self.org, old), plan_context(self.org, new)
                # Any new selected package is a new team-derived capability.
                # The approved move grants none; removal is allowed and explicit.
                old_specs = {canonical(s) for s in old_plan.specs}
                if any(canonical(s) not in old_specs for s in new_plan.specs):
                    raise TeamMoveError("team_move_unexpected_skill_grant")
                before.append(old_plan); after.append(new_plan)
                workspaces.append(WorkspaceIdentity(agent_id=agent, path=str(new_plan.workspace), identity=_identity(new_plan.workspace.lstat())))
            expected = self._current_inputs(images, before, after)
            request = MoveRequest(version=1, org_slug=self.org.slug, source_team="consultant", target_team="default",
                target_principal="founder", affected_agents=AGENTS, demote="consultant_head", retire_source=True,
                drain_threads=drain_threads, expected=expected, files=images, workspaces=tuple(workspaces))
            target = self._target_snapshot(request)
        blockers, epoch, changes = self.census(request)
        with self.org.db._lock:
            if initial != (epoch, changes) or initial != (self.move.producers.writer_epoch, self._database_observation()):
                raise TeamMoveError("team_move_preview_inputs_changed")
        return MovePreview(request, target, tuple(before), tuple(after), blockers, epoch, changes)

    def census(self, request, *, owned_publication=None):
        """Compose actual C1/C2/C3/native host/workflow facts, without caps.

        Only the exact operation's journal can be recognized as its own writer;
        all other publication records remain blockers. Pending thread obligations
        remain held and are not consumed as a clearance side effect.
        """
        blockers = list(self.move.producers.census(request))
        if owned_publication is not None:
            with self.org.db._lock:
                journal = self.org.workflow_authority._journal(self.org.db._conn, owned_publication.journal_id)
                if (journal["publisher_invocation"], journal["generation"]) != (
                        owned_publication.writer_invocation_id, owned_publication.generation):
                    raise TeamMoveError("team_move_publication_owner_changed")
            blockers = [b for b in blockers if not (b["kind"] == "publication_writer" and b["owner_id"] == owned_publication.journal_id)]
        db = self.org.db
        def add(kind, owner):
            blockers.append({"kind": kind, "owner_id": owner, "remedy": "Settle the actual native owner and cleanup; retain the move hold"})
        # Host census is never a bounded health/receipt DTO and uses no logical-ID
        # parsing. Unknown residue attribution is conservatively a hard blocker.
        host = getattr(self.org.orchestrator, "_host_supervisor", None)
        if host is None:
            add("host_owner_unavailable", request.source_team)
        else:
            blockers.extend(host.team_move_census(self.org.slug, request.affected_agents))
        threads = self.move.threads.census(request.affected_agents)
        for fact in threads:
            if fact["code"] != "thread_pending_obligation":
                blockers.append({"kind": fact["code"], "owner_id": str(fact.get("invocation_id", fact.get("thread_id", "unknown"))), "fact": fact,
                    "remedy": "Wait only for a captured running thread; resolve other ownership through its native owner"})
        with db._lock:
            causal = set()
            for row in db._conn.execute("SELECT id FROM tasks"):
                task = db.get_task(row["id"])
                if self.move._task_closure_uncommitted(task, request):
                    causal.add(task.id)
                    if task.status.value not in {"completed", "failed", "cancelled", "superseded"}:
                        add("task_causal_work", task.id)
            for reservation in self.move.blockers():
                if reservation["agent_id"] in request.affected_agents or reservation["task_id"] in causal:
                    add("task_preparation_or_cleanup", reservation["owner_id"])
            tables = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if "task_pause_controls" in tables:
                from runtime.infrastructure.task_pause_controls import decode_journal
                for row in db._conn.execute("SELECT root_task_id,held,admission_journal_json FROM task_pause_controls"):
                    if row["held"] and row["root_task_id"] in causal:
                        add("paused_causal_root", row["root_task_id"])
                    for entry in decode_journal(row["admission_journal_json"])["entries"]:
                        if entry["agent"] in request.affected_agents or entry["task_id"] in causal:
                            add("pause_unsettled_entry", entry["id"])
            if "workflow_instances" in tables:
                from runtime.infrastructure.workflow_schema import validate_workflow_schema
                validate_workflow_schema(db._conn, expected_org_slug=self.org.slug)
                def referenced(value):
                    if isinstance(value, dict):
                        return any(value.get(k) in request.affected_agents for k in ("principal", "agent", "manager_agent") if isinstance(value.get(k), str)) or value.get("team") == request.source_team or any(referenced(v) for v in value.values())
                    return isinstance(value, list) and any(referenced(v) for v in value)
                for row in db._conn.execute("SELECT i.*,b.binding_bytes FROM workflow_instances i JOIN workflow_binding_snapshots b ON b.id=i.binding_snapshot_id"):
                    affected = row["root_task_id"] in causal or referenced(json.loads(row["binding_bytes"]))
                    if not affected:
                        continue
                    if row["status"] not in {"complete", "cancelled"}:
                        add("workflow_instance", row["id"])
                    for table in ("workflow_draft_dispatch_intents", "workflow_dispatch_operations", "workflow_request_task_bridges"):
                        if table in tables:
                            for tail in db._conn.execute(f"SELECT * FROM {table} WHERE instance_id=?", (row["id"],)):
                                if tail["state"] not in {"completed", "cancelled", "failed"}:
                                    add("workflow_tail", tail["id"] if "id" in tail.keys() else tail["request_id"])
                    # Review outbox is joined through its actual operation FK.
                    for tail in db._conn.execute("SELECT o.* FROM workflow_dispatch_outbox o JOIN workflow_dispatch_operations d ON d.id=o.operation_id WHERE d.instance_id=?", (row["id"],)):
                        if tail["state"] not in {"completed", "cancelled"}:
                            add("workflow_outbox", tail["id"])
            epoch, changes = self.move.producers.writer_epoch, self._database_observation()
        return tuple(blockers), epoch, changes

    def reserve(self, *, operation_key: str, request_bytes: bytes, admission: FounderAdmission):
        """Future E supplies an actually admitted founder; no caller actor field."""
        request = _decode(MoveRequest, request_bytes)
        if request.org_slug != self.org.slug or admission.org_slug != self.org.slug:
            raise TeamMoveError("team_move_service_org_mismatch")
        existing = self.store.read(operation_key=operation_key)
        if existing is not None:
            if existing["request_bytes"] != request_bytes:
                raise TeamMoveError("team_move_changed_body")
            return existing
        fresh = self.preview(drain_threads=request.drain_threads)
        if _bytes(fresh.request) != request_bytes:
            raise TeamMoveError("team_move_stale_preview")
        with self.move.gate, self.org.db._lock:
            if (self.move.producers.writer_epoch != fresh.writer_epoch
                    or self._database_observation() != fresh.database_changes):
                raise TeamMoveError("team_move_reservation_inputs_changed")
            row = self.store.reserve(operation_key=operation_key, request_bytes=request_bytes, admission=admission)
            if row["phase"] == "waiting":
                self.move.threads.capture_running_uncommitted(row["operation_id"])
            return row

    def _database_observation(self):
        """Local writes plus SQLite's actual other-connection change counter."""
        return (self.org.db._conn.total_changes,
            self.org.db._conn.execute("PRAGMA data_version").fetchone()[0])

    def _row(self, key):
        row = self.store.read(operation_key=key)
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        return row

    def _transition(self, row, phase, **kwargs):
        return self.store.transition(row["operation_id"], expected_revision=row["state_revision"], phase=phase, **kwargs)

    def _unchanged(self, request):
        for image in request.files:
            raw, identity = read_regular(check_parents(self.org.root, image.path))
            if _digest(raw) != image.before_digest or identity != image.identity:
                raise TeamMoveError("team_move_precommit_state_changed")

    def _abandon(self, row, code):
        request = _decode(MoveRequest, row["request_bytes"])
        self._unchanged(request)
        with self.move.gate, self.org.db._lock:
            if self.org.db._conn.execute("SELECT 1 FROM audit_log WHERE task_id IN (?,?) AND action='thread_session_invalidated' LIMIT 1",
                    tuple("config:team-move:" + row["operation_id"] + ":" + agent for agent in AGENTS)).fetchone() is not None:
                raise TeamMoveError("team_move_precommit_reset_present")
            return self._transition(row, Phase.REFUSED, diagnostic=Diagnostic(code=code))

    async def advance(self, *, operation_key: str, expected_revision: int):
        """One bounded private invocation; terminal replay performs no effects."""
        row = self._row(operation_key)
        if row["phase"] in {"complete", "refused", "abandoned"}:
            return row
        if row["state_revision"] != expected_revision or row["phase"] != "waiting":
            raise TeamMoveError("team_move_explicit_recovery_required")
        request = _decode(MoveRequest, row["request_bytes"])
        if request.drain_threads:
            await self.move.threads.bounded_drain(row["operation_id"], timeout_seconds=30)
        owner = None
        primary = None
        result = None
        async with self.profiles.consumer_batch_writer(org=self.org, operation_id=row["operation_id"]) as batch:
            try:
                with batch.segment():
                    fresh = self.preview(drain_threads=request.drain_threads)
                    if _bytes(fresh.request) != row["request_bytes"]:
                        result = self._abandon(row, "team_move_stale_preview")
                    elif fresh.blockers:
                        result = self._abandon(row, "team_move_agents_busy")
                    else:
                        owner = "move_" + uuid.uuid4().hex
                        self._owner_started(row, owner)
                        with self.move.gate, self.org.db._lock:
                            if (self.move.producers.writer_epoch != fresh.writer_epoch
                                    or self._database_observation() != (fresh.database_changes[0] + 1, fresh.database_changes[1])):
                                raise TeamMoveError("team_move_census_changed")
                            decision = ForwardDecision(request_digest=row["request_digest"], revalidated_inputs_digest=_digest(_bytes(request.expected)),
                                census_digest=_digest(canonical(fresh.blockers)), writer_invocation_id=owner, decided_at=_now())
                            row = self._transition(row, Phase.COMMIT_DECIDED, decision=decision)
                if result is None:
                    result = self._forward(row, batch)
            except BaseException as exc:
                primary = exc
                if owner is not None:
                    self._retain_repair(operation_key, owner, exc)
                raise
            finally:
                if owner is not None:
                    try:
                        self._owner_finished(self._row(operation_key), owner)
                    except BaseException as failure:
                        if primary is None:
                            raise
                        primary.add_note("team move owner-finish persistence failed; retain hold: " + type(failure).__name__)
        if result["phase"] in {"complete", "refused", "abandoned"}:
            await self.redeliver()
        return result

    def _retain_repair(self, key, owner, primary):
        """A loser never changes another invocation's operation to repair."""
        try:
            current = self._row(key)
            if current["decision_bytes"] is None or current["phase"] in {"complete", "repair_required"}:
                return
            decision = _decode(ForwardDecision, current["decision_bytes"])
            progress = _decode(Progress, current["progress_bytes"])
            actual = progress.recovery[-1].owner_id if progress.recovery else decision.writer_invocation_id
            if actual == owner:
                self._transition(current, Phase.REPAIR_REQUIRED, diagnostic=self._diagnostic(primary))
        except BaseException as failure:
            primary.add_note("team move repair persistence failed; durable hold retained: " + type(failure).__name__)

    @staticmethod
    def _diagnostic(error):
        import re
        code = getattr(error, "code", None)
        if isinstance(error, OSError) and type(error.errno) is int:
            code = "team_move_io_errno_" + str(abs(error.errno))
        if not isinstance(code, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", code) is None:
            code = "team_move_native_" + type(error).__name__[:160]
        return Diagnostic(code=code)

    def _after_plans(self, request):
        plans, before_plans = [], []
        for agent, image, workspace in zip(AGENTS, request.files[:2], request.workspaces):
            if (str(self.org.root / "workspaces" / agent) != workspace.path
                    or _identity((self.org.root / "workspaces" / agent).lstat()) != workspace.identity):
                raise TeamMoveError("team_move_workspace_identity_changed")
            definition = parse_agent_text(base64.b64decode(image.after_base64).decode().replace("\r\n", "\n"), expected_name=agent)
            plans.append(plan_context(self.org, definition))
            old = parse_agent_text(base64.b64decode(image.before_base64).decode().replace("\r\n", "\n"), expected_name=agent)
            before_plans.append(plan_context(self.org, old))
        if (_digest(canonical([list(p.specs) for p in (*before_plans, *plans)])) != request.expected.skill_selection_digest
                or _digest(canonical([p.manifest() for p in (*before_plans, *plans)])) != request.expected.permission_digest):
            raise TeamMoveError("team_move_context_selection_drift")
        return tuple(plans)

    def _forward(self, row, batch):
        request = _decode(MoveRequest, row["request_bytes"])
        progress = _decode(Progress, row["progress_bytes"])
        authority = self.org.workflow_authority
        if row["phase"] in {"commit_decided", "fenced", "reset"}:
            self._unchanged(request)
            self._verify_stable_inputs(request)
            self._after_plans(request)
        elif row["phase"] in {"context", "publish"}:
            self._verify_stable_inputs(request)
        if row["phase"] == "commit_decided":
            with batch.segment():
                target = self._target_snapshot(request)
                journal = authority.begin_move_publication(row["operation_id"])
                identity = PublicationIdentity(writer_invocation_id=journal["publisher_invocation"], journal_id=journal["id"],
                    namespace=authority.namespace, generation=journal["generation"], digest=_digest(target))
                progress = progress.model_copy(update={"publication": identity})
                row = self._transition(row, Phase.FENCED, progress=progress)
        if row["phase"] == "fenced":
            row = self._transition(row, Phase.RESET)
        if row["phase"] == "reset":
            for index, agent in enumerate(AGENTS):
                saved = progress.agents[index].reset
                actual = (self._read_reset(row, agent) if saved is not None
                    else self.move.threads.reset_operation_agent(row["operation_id"], agent))
                if saved is not None and saved != actual:
                    raise TeamMoveError("team_move_saved_reset_mismatch")
                if saved is None:
                    agents = list(progress.agents)
                    agents[index] = agents[index].model_copy(update={"reset": actual})
                    progress = progress.model_copy(update={"agents": tuple(agents)})
                    row = self._transition(row, Phase.RESET, progress=progress)
            row = self._transition(row, Phase.CANONICAL)
        if row["phase"] == "canonical":
            self._after_plans(request)
            with batch.segment():
                def observed_after(path):
                    nonlocal row, progress
                    if path not in progress.canonical_after:
                        progress = progress.model_copy(update={"canonical_after": tuple(sorted((*progress.canonical_after, path)))})
                        row = self._transition(row, Phase.CANONICAL, progress=progress)
                with authority.move_mutation(row["operation_id"], progress.publication):
                    replace_owned(self.org.root, request.files, self.org.teams, observed_after=observed_after)
            self._verify_stable_inputs(request)
            row = self._transition(row, Phase.CONTEXT)
        if row["phase"] == "context":
            plans = self._after_plans(request)
            for index, plan in enumerate(plans):
                saved = progress.agents[index].context
                actual = verify_context(self.org, plan) if saved is not None else materialize_context(self.org, plan)
                if saved is not None and actual != saved:
                    raise TeamMoveError("team_move_saved_context_mismatch")
                if saved is None:
                    agents = list(progress.agents)
                    agents[index] = agents[index].model_copy(update={"context": actual})
                    progress = progress.model_copy(update={"agents": tuple(agents)})
                    row = self._transition(row, Phase.CONTEXT, progress=progress)
            row = self._transition(row, Phase.PUBLISH)
        if row["phase"] == "publish":
            with batch.segment():
                authority.publish_move(row["operation_id"], progress.publication)
                return self._complete(row, request, progress)
        raise TeamMoveError("team_move_forward_phase_unknown")

    def _complete(self, row, request, progress):
        """Actual native readback, followed by a short DB-only receipt/release CAS."""
        from runtime.orchestrator.org_validation import validate_team_membership
        with canonical_roster(self.org.root):
            validate_team_membership(OrgPaths(self.org.root), self.org.teams)
            for image in request.files:
                raw, identity = read_regular(check_parents(self.org.root, image.path))
                if _digest(raw) != image.after_digest or identity.mode != image.identity.mode:
                    raise TeamMoveError("team_move_completion_canonical_changed")
            plans = self._after_plans(request)
            for index, plan in enumerate(plans):
                if verify_context(self.org, plan) != progress.agents[index].context:
                    raise TeamMoveError("team_move_completion_context_changed")
                if self._read_reset(row, AGENTS[index]) != progress.agents[index].reset:
                    raise TeamMoveError("team_move_completion_reset_changed")
            ready = self.org.workflow_authority.verify_admission_ready()
            current = self.org.workflow_authority.capture_snapshot()
            self._verify_stable_inputs(request)
            with self.org.db._lock:
                pointer = self.org.workflow_authority._pointer(self.org.db._conn, ready.namespace)
            if (current != ready.snapshot_bytes or (ready.namespace, ready.generation, ready.snapshot_digest, pointer[1]) !=
                    (progress.publication.namespace, progress.publication.generation, progress.publication.digest, progress.publication.journal_id)):
                raise TeamMoveError("team_move_completion_current_inputs_changed")
            blockers, epoch, changes = self.census(request, owned_publication=progress.publication)
            if blockers:
                raise TeamMoveError("team_move_completion_owners_unsettled")
            evidence = _digest(canonical({"authority": _digest(current), "contexts": [p.manifest() for p in plans],
                "profiles": self.org.workflow_authority._profile_projection(), "resets": [a.reset.model_dump() for a in progress.agents]}))
        receipt = CompletedReceipt(version=1, operation_id=row["operation_id"], request_digest=row["request_digest"], phase="complete",
            state_revision=row["state_revision"] + 1, committed=True, progress_digest=_digest(row["progress_bytes"]),
            current_inputs_digest=evidence, settled_census_digest=_digest(canonical(blockers)), publication=progress.publication)
        with self.move.gate, _transaction(self.org.db):
            if self._database_observation() != changes or self.move.producers.writer_epoch != epoch:
                raise TeamMoveError("team_move_completion_capture_changed")
            return self.store._complete_uncommitted(row, receipt)

    def _verify_stable_inputs(self, request):
        from runtime.identities.registry import read_name_metadata
        from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
        authority = self.org.workflow_authority
        with self.org.db._lock:
            rows = self.org.db._conn.execute(
                "SELECT snapshot_bytes FROM workflow_publication_journals WHERE namespace=? AND generation=? AND snapshot_digest=? AND state='cache_installed'",
                (request.expected.authority_namespace, request.expected.authority_generation, request.expected.authority_digest),
            ).fetchall()
            dependencies = self.profiles._dependency_mirror_snapshot(self.org)
        if len(rows) != 1 or _digest(bytes(rows[0]["snapshot_bytes"])) != request.expected.authority_digest:
            raise TeamMoveError("team_move_predecessor_authority_unknown")
        original = bytes(rows[0]["snapshot_bytes"])
        config, _identity = read_regular(OrgPaths(self.org.root).org_config_path)
        labels = read_name_metadata(self.org)
        self._validate_policy_config(config)
        selectors = self._policy_inputs([s["team"] for s in json.loads(original)["active_policy_selectors"]])
        if (_digest(original + b"\x00" + config) != request.expected.canonical_digest
                or _digest(canonical({k: asdict(v) for k, v in sorted(labels.items())})) != request.expected.naming_digest
                or _digest(canonical(authority._profile_projection())) != request.expected.profile_digest
                or _digest(canonical(dependencies)) != request.expected.dependency_digest
                or _digest(canonical(selectors)) != request.expected.policy_digest):
            raise TeamMoveError("team_move_current_dependency_drift")

    def _read_reset(self, row, agent):
        """Read-only native reset reconciliation at PUBLISH/COMPLETE."""
        from runtime.infrastructure.team_move_store import ResetEvidence
        with self.org.db._lock:
            rows = self.org.db._conn.execute("SELECT thread_id,agent_name,agent_session_id,last_resumed_seq FROM thread_participants WHERE agent_name=? ORDER BY thread_id", (agent,)).fetchall()
            if any(r["agent_session_id"] is not None or r["last_resumed_seq"] != 0 for r in rows):
                raise TeamMoveError("team_move_reset_post_state")
            audits = self.org.db._conn.execute("SELECT id,agent,payload FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id", ("config:team-move:" + row["operation_id"] + ":" + agent,)).fetchall()
            if len(audits) != (1 if rows else 0) or (audits and (audits[0]["agent"] != "founder" or json.loads(audits[0]["payload"], object_pairs_hook=_unique_object) != {"reason": "team_move", "rows": len(rows), "name": agent})):
                raise TeamMoveError("team_move_reset_audit_unknown")
            return ResetEvidence(rows=len(rows), audit_id=audits[0]["id"] if audits else None,
                post_state_digest=_digest(json.dumps([tuple(r) for r in rows], separators=(",", ":"), ensure_ascii=False).encode()))

    def _owner_started(self, row, owner):
        from runtime.platform.process_census import default_process_reader
        identity = default_process_reader().start_identity(os.getpid())
        if identity is None:
            raise TeamMoveError("team_move_owner_process_unknown")
        with _transaction(self.org.db):
            self.org.db.insert_audit_log_uncommitted(task_id="config:team-move:" + row["operation_id"], agent="founder", action="team_move_owner_started",
                payload={"owner_id": owner, "pid": os.getpid(), "start_identity": identity, "request_digest": row["request_digest"]})

    def _owner_finished(self, row, owner):
        with _transaction(self.org.db):
            prior = self.org.db._conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='team_move_owner_finished'", ("config:team-move:" + row["operation_id"],)).fetchall()
            if any(json.loads(a["payload"]).get("owner_id") == owner for a in prior):
                return
            self.org.db.insert_audit_log_uncommitted(task_id="config:team-move:" + row["operation_id"], agent="founder", action="team_move_owner_finished",
                payload={"owner_id": owner, "request_digest": row["request_digest"]})

    async def recover(self, *, operation_key: str, expected_revision: int):
        """One explicit recovery; no TTL, automatic retry or supplied idle flag."""
        row = self._row(operation_key)
        if row["phase"] in {"complete", "refused", "abandoned"}:
            return row
        if row["state_revision"] != expected_revision:
            raise TeamMoveError("team_move_stale_revision")
        if row["phase"] == "waiting":
            return self._abandon(row, "team_move_precommit_abandoned")
        progress = _decode(Progress, row["progress_bytes"])
        decision = _decode(ForwardDecision, row["decision_bytes"])
        previous = progress.recovery[-1].owner_id if progress.recovery else decision.writer_invocation_id
        proof = self._previous_owner_gone(row, previous)
        if row["phase"] != "repair_required":
            row = self._transition(row, Phase.REPAIR_REQUIRED, diagnostic=Diagnostic(code="team_move_interrupted_owner"))
        request = _decode(MoveRequest, row["request_bytes"])
        blockers, _epoch, _changes = self.census(request, owned_publication=progress.publication)
        if blockers:
            raise TeamMoveError("team_move_recovery_native_owners_unsettled")
        owner = "move_" + uuid.uuid4().hex
        recovery = RecoveryIdentity(previous_owner_id=previous, owner_id=owner, claim_id=uuid.uuid4().hex, previous_owner_quiescence_digest=_digest(canonical(proof)))
        primary = None
        started = False
        async with self.profiles.consumer_batch_writer(org=self.org, operation_id=row["operation_id"]) as batch:
            try:
                self._owner_started(row, owner)
                started = True
                progress = progress.model_copy(update={"recovery": progress.recovery + (recovery,)})
                row = self._transition(row, Phase(row["resume_phase"]), progress=progress)
                result = self._forward(row, batch)
            except BaseException as exc:
                primary = exc
                self._retain_repair(operation_key, owner, exc)
                raise
            finally:
                if started:
                    try:
                        self._owner_finished(row, owner)
                    except BaseException as failure:
                        if primary is None:
                            raise
                        primary.add_note("team move owner-finish persistence failed: " + type(failure).__name__)
        if result["phase"] == "complete":
            await self.redeliver()
        return result

    def _previous_owner_gone(self, row, owner):
        from runtime.platform.process_census import default_process_reader
        with self.org.db._lock:
            audits = self.org.db._conn.execute("SELECT id,agent,action,payload FROM audit_log WHERE task_id=? AND action IN ('team_move_owner_started','team_move_owner_finished') ORDER BY id", ("config:team-move:" + row["operation_id"],)).fetchall()
        decoded = []
        for audit in audits:
            payload = json.loads(audit["payload"], object_pairs_hook=_unique_object)
            keys = {"owner_id", "request_digest", "pid", "start_identity"} if audit["action"] == "team_move_owner_started" else {"owner_id", "request_digest"}
            if (not isinstance(payload, dict) or set(payload) != keys or audit["agent"] != "founder"
                    or payload["request_digest"] != row["request_digest"] or not isinstance(payload["owner_id"], str)
                    or (audit["action"] == "team_move_owner_started" and (
                        type(payload["pid"]) is not int or payload["pid"] <= 0 or not isinstance(payload["start_identity"], str) or not payload["start_identity"]))):
                raise TeamMoveError("team_move_recovery_owner_unknown")
            decoded.append((audit, payload))
        selected = [(audit, payload) for audit, payload in decoded if payload["owner_id"] == owner]
        starts = [(a, p) for a, p in selected if a["action"] == "team_move_owner_started"]
        ends = [(a, p) for a, p in selected if a["action"] == "team_move_owner_finished"]
        if len(starts) != 1 or len(ends) > 1 or any(a["agent"] != "founder" or p.get("request_digest") != row["request_digest"] for a, p in selected):
            raise TeamMoveError("team_move_recovery_owner_unknown")
        start, facts = starts[0]
        if ends:
            if ends[0][0]["id"] <= start["id"]:
                raise TeamMoveError("team_move_recovery_owner_unknown")
            return {"owner_id": owner, "start_audit": start["id"], "finish_audit": ends[0][0]["id"]}
        try:
            os.kill(facts["pid"], 0)
        except ProcessLookupError:
            return {"owner_id": owner, "start_audit": start["id"], "process_absent": True}
        except (PermissionError, OSError) as exc:
            raise TeamMoveError("team_move_recovery_process_unknown") from exc
        current = default_process_reader().start_identity(facts["pid"])
        if current is None or current == facts["start_identity"]:
            raise TeamMoveError("team_move_recovery_owner_live_or_unknown")
        return {"owner_id": owner, "start_audit": start["id"], "different_process_identity": current}

    async def redeliver(self):
        """After durable release, existing deduplicating owners retain semantics."""
        if self.store.read() is not None:
            raise TeamMoveError("team_move_release_not_committed")
        if not hasattr(self.org.orchestrator, "redeliver_team_move_tasks"):
            raise TeamMoveError("team_move_recovery_reattach_for_redelivery")
        self.org.orchestrator.redeliver_team_move_tasks()
        await self.move.threads.redeliver_after_release(self.org.thread_queue)
        self.move.producers.redeliver_after_release(self.org)
        # Armed schedules are intentionally not claimed here. The normal timer
        # alone decides due/missed/expiry using original persisted deadlines.
