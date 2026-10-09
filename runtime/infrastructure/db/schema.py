from __future__ import annotations

import json
import sqlite3


class AuthorityAuditMigrationRefusal(Exception):
    """Legacy ``authority_audit`` rows reference candidates that do not exist,
    so the candidate-FK retrofit was refused atomically. The legacy schema and
    data are left intact for inspection."""


# Corrected DB-level lifecycle-guard trigger body. Requires the referenced
# ``authority_evaluations`` row's disposition to exactly mirror the candidate's
# frozen disposition (created -> evaluated: NEW.disposition; evaluated ->
# consumed: OLD.disposition), so an append-only escalate evaluation can never
# durably freeze a continue_same_root candidate. Used by BOTH the fresh
# ``_create_authority_tables`` script and the legacy retrofit, so the two
# surfaces can never drift.
_AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL = """
            CREATE TRIGGER IF NOT EXISTS authority_candidates_lifecycle_guard
                BEFORE UPDATE OF lifecycle_state, disposition, consumed_at
                ON authority_candidates
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'invalid authority candidate lifecycle transition')
                    WHERE NOT (
                        -- created -> evaluated: requires a committed evaluation row
                        -- whose disposition exactly equals the freshly set
                        -- disposition (NULL -> value), so an append-only escalate
                        -- evaluation can never freeze a continue_same_root candidate;
                        -- consumed_at is still NULL.
                        (OLD.lifecycle_state = 'created' AND OLD.disposition IS NULL
                         AND NEW.lifecycle_state = 'evaluated'
                         AND NEW.disposition IS NOT NULL
                         AND NEW.consumed_at IS NULL
                         AND EXISTS (SELECT 1 FROM authority_evaluations e
                                     WHERE e.candidate_id = NEW.id
                                       AND e.disposition = NEW.disposition))
                        OR
                        -- evaluated -> consumed: exactly-once; disposition frozen;
                        -- a committed evaluation row with the SAME frozen
                        -- disposition must exist; consumed_at is stamped exactly
                        -- once (NULL -> value).
                        (OLD.lifecycle_state = 'evaluated'
                         AND NEW.lifecycle_state = 'consumed'
                         AND NEW.disposition IS OLD.disposition
                         AND OLD.consumed_at IS NULL
                         AND NEW.consumed_at IS NOT NULL
                         AND EXISTS (SELECT 1 FROM authority_evaluations e
                                     WHERE e.candidate_id = NEW.id
                                       AND e.disposition = OLD.disposition))
                        OR
                        -- no-op on the guarded columns (e.g. updated_at-only writes).
                        (NEW.lifecycle_state = OLD.lifecycle_state
                         AND NEW.disposition IS OLD.disposition
                         AND NEW.consumed_at IS OLD.consumed_at)
                    );
                END;
"""


# THR-229 checkpoint B1: the accepted control-plane subset of the approved v2
# dual-text persistence. These are strictly additive tables used only by the
# private callable v2 control store; B1 wires no startup/route/launch/queue/UI
# reader or writer. The remaining nine approved candidate/binding/envelope/
# recovery tables belong to a later unit. ``authority_policy_v2_control_audit``
# is the accepted control persistence: append-only events plus the request
# receipts bound by (team, kind, request_id).
_AUTHORITY_POLICY_V2_CONTROL_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS authority_policy_v2_releases (
                id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0 AND version <= 2147483647),
                title TEXT NOT NULL,
                what_to_escalate TEXT NOT NULL,
                what_not_to_escalate TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                policy_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(team, policy_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_releases_team_policy_version
                ON authority_policy_v2_releases(team, policy_id, version DESC);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_releases_no_update
                BEFORE UPDATE ON authority_policy_v2_releases
                BEGIN SELECT RAISE(ABORT, 'v2 authority policy releases are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_releases_no_delete
                BEFORE DELETE ON authority_policy_v2_releases
                BEGIN SELECT RAISE(ABORT, 'v2 authority policy releases cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_activations (
                id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                selector_epoch INTEGER NOT NULL
                    CHECK(selector_epoch > 0 AND selector_epoch <= 2147483647),
                release_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_releases(id) ON DELETE RESTRICT,
                release_digest TEXT NOT NULL,
                previous_selector_id TEXT,
                action TEXT NOT NULL
                    CHECK(action IN ('bootstrap','activate','reactivate_rollback')),
                request_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                activation_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(team, selector_epoch),
                UNIQUE(team, request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_activations_team_epoch
                ON authority_policy_v2_activations(team, selector_epoch DESC);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_activations_validate_insert
                BEFORE INSERT ON authority_policy_v2_activations
                BEGIN
                    SELECT RAISE(ABORT, 'v2 authority activation release mismatch')
                    WHERE NOT EXISTS (
                        SELECT 1 FROM authority_policy_v2_releases r
                        WHERE r.id=NEW.release_id AND r.team=NEW.team
                          AND r.policy_digest=NEW.release_digest);
                END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_activations_no_update
                BEFORE UPDATE ON authority_policy_v2_activations
                BEGIN SELECT RAISE(ABORT, 'v2 authority activations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_activations_no_delete
                BEFORE DELETE ON authority_policy_v2_activations
                BEGIN SELECT RAISE(ABORT, 'v2 authority activations cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_active_selector (
                team TEXT PRIMARY KEY,
                selector_id TEXT NOT NULL UNIQUE,
                family TEXT NOT NULL CHECK(family IN ('empty','legacy_v1','v2')),
                selector_epoch INTEGER NOT NULL
                    CHECK(selector_epoch >= 0 AND selector_epoch <= 2147483647),
                previous_selector_id TEXT,
                legacy_activation_id TEXT
                    REFERENCES authority_policy_activations(id) ON DELETE RESTRICT,
                v2_activation_id TEXT
                    REFERENCES authority_policy_v2_activations(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                CHECK (
                    (family='empty' AND selector_epoch=0 AND previous_selector_id IS NULL
                     AND legacy_activation_id IS NULL AND v2_activation_id IS NULL)
                    OR (family='legacy_v1' AND selector_epoch>=1
                        AND legacy_activation_id IS NOT NULL AND v2_activation_id IS NULL)
                    OR (family='v2' AND selector_epoch>=1
                        AND legacy_activation_id IS NULL AND v2_activation_id IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS authority_policy_active_selector_history (
                selector_id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                family TEXT NOT NULL CHECK(family IN ('empty','legacy_v1','v2')),
                selector_epoch INTEGER NOT NULL
                    CHECK(selector_epoch >= 0 AND selector_epoch <= 2147483647),
                previous_selector_id TEXT,
                legacy_activation_id TEXT
                    REFERENCES authority_policy_activations(id) ON DELETE RESTRICT,
                v2_activation_id TEXT
                    REFERENCES authority_policy_v2_activations(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                UNIQUE(team, selector_epoch),
                CHECK (
                    (family='empty' AND selector_epoch=0 AND previous_selector_id IS NULL
                     AND legacy_activation_id IS NULL AND v2_activation_id IS NULL)
                    OR (family='legacy_v1' AND selector_epoch>=1
                        AND legacy_activation_id IS NOT NULL AND v2_activation_id IS NULL)
                    OR (family='v2' AND selector_epoch>=1
                        AND legacy_activation_id IS NULL AND v2_activation_id IS NOT NULL)
                )
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_active_selector_history_team_epoch
                ON authority_policy_active_selector_history(team, selector_epoch DESC);
            CREATE TRIGGER IF NOT EXISTS authority_policy_active_selector_history_no_update
                BEFORE UPDATE ON authority_policy_active_selector_history
                BEGIN SELECT RAISE(ABORT, 'authority selector history is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_active_selector_history_no_delete
                BEFORE DELETE ON authority_policy_active_selector_history
                BEGIN SELECT RAISE(ABORT, 'authority selector history cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_control_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team TEXT NOT NULL,
                request_id TEXT,
                request_digest TEXT,
                kind TEXT NOT NULL CHECK(kind IN
                    ('selector_initialized_empty','selector_initialized_legacy',
                     'release_created','activation_selected','activation_rejected')),
                release_id TEXT,
                activation_id TEXT,
                selector_id TEXT,
                action TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_authority_policy_v2_control_audit_request
                ON authority_policy_v2_control_audit(team, kind, request_id)
                WHERE request_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_control_audit_team
                ON authority_policy_v2_control_audit(team, id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_control_audit_no_update
                BEFORE UPDATE ON authority_policy_v2_control_audit
                BEGIN SELECT RAISE(ABORT, 'v2 authority control audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_control_audit_no_delete
                BEFORE DELETE ON authority_policy_v2_control_audit
                BEGIN SELECT RAISE(ABORT, 'v2 authority control audit is append-only'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_session_bindings (
                binding_id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                selector_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                release_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_releases(id) ON DELETE RESTRICT,
                policy_version INTEGER NOT NULL
                    CHECK(policy_version > 0 AND policy_version <= 2147483647),
                policy_digest TEXT NOT NULL,
                activation_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_activations(id) ON DELETE RESTRICT,
                contract_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                model_id TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(root_task_id, manager_agent, manager_session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_session_bindings_team_selector
                ON authority_policy_v2_session_bindings(team, selector_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_session_bindings_no_update
                BEFORE UPDATE ON authority_policy_v2_session_bindings
                BEGIN SELECT RAISE(ABORT, 'v2 authority session bindings are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_session_bindings_no_delete
                BEFORE DELETE ON authority_policy_v2_session_bindings
                BEGIN SELECT RAISE(ABORT, 'v2 authority session bindings cannot be deleted'); END;

            -- THR-229 checkpoint C2: result-keyed immutable attempt journal.
            -- The callback admission transaction inserts exactly one
            -- ``admitted`` row bound to the immutable task result, the
            -- authenticated launch binding and the v2 contract.  The identity
            -- columns (including owner_attempt_id) never change; later C work
            -- advances ``stage``/``finalization_state`` under its own
            -- transaction-owned methods, which this unit deliberately does not
            -- implement or expose.
            CREATE TABLE IF NOT EXISTS authority_policy_v2_attempts (
                attempt_id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                result_id INTEGER NOT NULL
                    REFERENCES task_results(id) ON DELETE RESTRICT,
                binding_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                release_id TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                selector_id TEXT NOT NULL,
                stage TEXT NOT NULL CHECK(stage IN
                    ('admitted','claimed','claim_audited','evaluated',
                     'evaluation_audited','consumed','consumed_audited')),
                finalization_state TEXT NOT NULL
                    CHECK(finalization_state IN
                        ('unfinalized','continued','refused','owner_lost')),
                refusal_code TEXT,
                origin_boot_id TEXT NOT NULL,
                owner_attempt_id TEXT NOT NULL,
                assessment_digest TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(root_task_id, manager_agent, manager_session_id, result_id)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_attempts_result
                ON authority_policy_v2_attempts(result_id);
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_attempts_team_root
                ON authority_policy_v2_attempts(team, root_task_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_attempts_identity_immutable
                BEFORE UPDATE ON authority_policy_v2_attempts
                WHEN OLD.attempt_id IS NOT NEW.attempt_id
                  OR OLD.team IS NOT NEW.team
                  OR OLD.root_task_id IS NOT NEW.root_task_id
                  OR OLD.manager_agent IS NOT NEW.manager_agent
                  OR OLD.manager_session_id IS NOT NEW.manager_session_id
                  OR OLD.result_id IS NOT NEW.result_id
                  OR OLD.binding_id IS NOT NEW.binding_id
                  OR OLD.contract_id IS NOT NEW.contract_id
                  OR OLD.contract_version IS NOT NEW.contract_version
                  OR OLD.contract_digest IS NOT NEW.contract_digest
                  OR OLD.release_id IS NOT NEW.release_id
                  OR OLD.activation_id IS NOT NEW.activation_id
                  OR OLD.activation_epoch IS NOT NEW.activation_epoch
                  OR OLD.selector_id IS NOT NEW.selector_id
                  OR OLD.origin_boot_id IS NOT NEW.origin_boot_id
                  OR OLD.owner_attempt_id IS NOT NEW.owner_attempt_id
                  OR OLD.assessment_digest IS NOT NEW.assessment_digest
                BEGIN SELECT RAISE(ABORT, 'v2 attempt identity is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_attempts_no_delete
                BEFORE DELETE ON authority_policy_v2_attempts
                BEGIN SELECT RAISE(ABORT, 'v2 attempt journal cannot be deleted'); END;
            -- THR-229 checkpoint C3d1: finalized attempts are terminal.  The
            -- greatest committed stage is retained on finalization, and once
            -- ``finalization_state`` leaves ``unfinalized`` the row can never
            -- reopen, advance or rewrite its refusal evidence.  While
            -- ``unfinalized``, only the exact forward-only stage transitions
            -- (or a single terminal finalization) are accepted; a refused or
            -- owner_lost terminal always carries a non-null refusal_code.
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_attempts_finalization_guard
                BEFORE UPDATE ON authority_policy_v2_attempts
                WHEN NOT (
                    (OLD.finalization_state IS NOT 'unfinalized'
                     AND NEW.stage IS OLD.stage
                     AND NEW.finalization_state IS OLD.finalization_state
                     AND NEW.refusal_code IS OLD.refusal_code)
                    OR
                    (OLD.finalization_state IS 'unfinalized' AND (
                        (NEW.finalization_state IS 'unfinalized'
                         AND NEW.refusal_code IS NULL
                         AND NEW.stage = CASE OLD.stage
                             WHEN 'admitted' THEN 'claimed'
                             WHEN 'claimed' THEN 'claim_audited'
                             WHEN 'claim_audited' THEN 'evaluated'
                             WHEN 'evaluated' THEN 'evaluation_audited'
                             WHEN 'evaluation_audited' THEN 'consumed'
                             WHEN 'consumed' THEN 'consumed_audited'
                             ELSE NULL END)
                        OR
                        (NEW.finalization_state IN ('continued','refused','owner_lost')
                         AND NEW.stage IS OLD.stage
                         AND (NEW.finalization_state IS 'continued'
                              OR NEW.refusal_code IS NOT NULL))
                    ))
                )
                BEGIN SELECT RAISE(ABORT, 'v2 attempt finalization is terminal'); END;

            -- THR-229 checkpoint C3b: durable candidate (K), policy pin (P) and
            -- the closed candidate-audit event (a1 claim stage).  The candidate
            -- is created by the claim transaction from one immutable admitted
            -- result/attempt and is bound to the authenticated pinned
            -- release/activation/selector.  ``claim_key`` uniqueness prevents a
            -- second candidate for the same exact causal tuple.  Identity
            -- columns are immutable; the audit is append-only and requires a
            -- real candidate FK.  No dummy candidate and no free-form payload.
            CREATE TABLE IF NOT EXISTS authority_policy_v2_candidates (
                candidate_id TEXT PRIMARY KEY,
                claim_key TEXT NOT NULL,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_attempts(attempt_id) ON DELETE RESTRICT,
                result_id INTEGER NOT NULL
                    REFERENCES task_results(id) ON DELETE RESTRICT,
                binding_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                release_id TEXT NOT NULL,
                policy_version INTEGER NOT NULL
                    CHECK(policy_version > 0 AND policy_version <= 2147483647),
                policy_digest TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                selector_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                model_id TEXT NOT NULL,
                causal_result_id INTEGER NOT NULL,
                causal_result_digest TEXT NOT NULL,
                origin_boot_id TEXT NOT NULL,
                owner_attempt_id TEXT NOT NULL,
                schema_raw_digest TEXT NOT NULL,
                schema_inventory_digest TEXT NOT NULL,
                schema_object_count INTEGER NOT NULL
                    CHECK(schema_object_count >= 0 AND schema_object_count <= 100000),
                permission_surface_digest TEXT NOT NULL,
                lifecycle_stage TEXT NOT NULL DEFAULT 'created'
                    CHECK(lifecycle_stage IN ('created','evaluated','consumed')),
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(claim_key),
                UNIQUE(root_task_id, manager_agent, manager_session_id, result_id)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_candidates_result
                ON authority_policy_v2_candidates(result_id);
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_candidates_team_root
                ON authority_policy_v2_candidates(team, root_task_id);
            -- C3c: the candidate is no longer wholly immutable.  Every identity
            -- and frozen-evidence column stays immutable; only the forward-only
            -- lifecycle stage may advance created -> evaluated -> consumed.
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_candidates_lifecycle_guard
                BEFORE UPDATE ON authority_policy_v2_candidates
                WHEN (
                    OLD.candidate_id IS NOT NEW.candidate_id
                 OR OLD.claim_key IS NOT NEW.claim_key
                 OR OLD.team IS NOT NEW.team
                 OR OLD.root_task_id IS NOT NEW.root_task_id
                 OR OLD.manager_agent IS NOT NEW.manager_agent
                 OR OLD.manager_session_id IS NOT NEW.manager_session_id
                 OR OLD.attempt_id IS NOT NEW.attempt_id
                 OR OLD.result_id IS NOT NEW.result_id
                 OR OLD.binding_id IS NOT NEW.binding_id
                 OR OLD.contract_id IS NOT NEW.contract_id
                 OR OLD.contract_version IS NOT NEW.contract_version
                 OR OLD.contract_digest IS NOT NEW.contract_digest
                 OR OLD.release_id IS NOT NEW.release_id
                 OR OLD.policy_version IS NOT NEW.policy_version
                 OR OLD.policy_digest IS NOT NEW.policy_digest
                 OR OLD.activation_id IS NOT NEW.activation_id
                 OR OLD.activation_epoch IS NOT NEW.activation_epoch
                 OR OLD.selector_id IS NOT NEW.selector_id
                 OR OLD.provider_id IS NOT NEW.provider_id
                 OR OLD.executor_kind IS NOT NEW.executor_kind
                 OR OLD.model_id IS NOT NEW.model_id
                 OR OLD.causal_result_id IS NOT NEW.causal_result_id
                 OR OLD.causal_result_digest IS NOT NEW.causal_result_digest
                 OR OLD.origin_boot_id IS NOT NEW.origin_boot_id
                 OR OLD.owner_attempt_id IS NOT NEW.owner_attempt_id
                 OR OLD.schema_raw_digest IS NOT NEW.schema_raw_digest
                 OR OLD.schema_inventory_digest IS NOT NEW.schema_inventory_digest
                 OR OLD.schema_object_count IS NOT NEW.schema_object_count
                 OR OLD.permission_surface_digest IS NOT NEW.permission_surface_digest
                 OR OLD.created_at IS NOT NEW.created_at
                 OR NOT (
                        (OLD.lifecycle_stage='created' AND NEW.lifecycle_stage='evaluated')
                     OR (OLD.lifecycle_stage='evaluated' AND NEW.lifecycle_stage='consumed')
                 )
                )
                BEGIN SELECT RAISE(ABORT, 'v2 candidate identity is immutable and lifecycle is forward-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_candidates_no_delete
                BEFORE DELETE ON authority_policy_v2_candidates
                BEGIN SELECT RAISE(ABORT, 'v2 candidate cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_pins (
                candidate_id TEXT PRIMARY KEY
                    REFERENCES authority_policy_v2_candidates(candidate_id) ON DELETE RESTRICT,
                claim_key TEXT NOT NULL,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                result_id INTEGER NOT NULL,
                binding_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                selector_id TEXT NOT NULL,
                policy_version INTEGER NOT NULL
                    CHECK(policy_version > 0 AND policy_version <= 2147483647),
                policy_digest TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                model_id TEXT NOT NULL,
                schema_raw_digest TEXT NOT NULL,
                schema_inventory_digest TEXT NOT NULL,
                schema_object_count INTEGER NOT NULL
                    CHECK(schema_object_count >= 0 AND schema_object_count <= 100000),
                permission_surface_digest TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_pins_team_root
                ON authority_policy_v2_pins(team, root_task_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_pins_no_update
                BEFORE UPDATE ON authority_policy_v2_pins
                BEGIN SELECT RAISE(ABORT, 'v2 policy pin is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_pins_no_delete
                BEFORE DELETE ON authority_policy_v2_pins
                BEGIN SELECT RAISE(ABORT, 'v2 policy pin cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_candidate_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_candidates(candidate_id) ON DELETE RESTRICT,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                event TEXT NOT NULL CHECK(event IN ('claimed','evaluated','consumed','refused','final')),
                claim_key TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                result_id INTEGER NOT NULL,
                owner_attempt_id TEXT NOT NULL,
                origin_boot_id TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(candidate_id, event)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_candidate_audit_candidate
                ON authority_policy_v2_candidate_audit(candidate_id, id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_candidate_audit_no_update
                BEFORE UPDATE ON authority_policy_v2_candidate_audit
                BEGIN SELECT RAISE(ABORT, 'v2 candidate audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_candidate_audit_no_delete
                BEFORE DELETE ON authority_policy_v2_candidate_audit
                BEGIN SELECT RAISE(ABORT, 'v2 candidate audit is append-only'); END;

            -- THR-229 checkpoint C3c: the persisted evaluation (V).  Exactly
            -- one immutable evaluation per candidate (identity equals the
            -- candidate identity) records the derived clause-free outcome ONCE
            -- together with the bounded sanitized assessment or the honest
            -- invalid-assessment diagnostic.  No lifecycle or authority is
            -- encoded in an overloaded column: the evaluation outcome is its
            -- own closed column.
            CREATE TABLE IF NOT EXISTS authority_policy_v2_evaluations (
                evaluation_id TEXT PRIMARY KEY
                    REFERENCES authority_policy_v2_candidates(candidate_id) ON DELETE RESTRICT,
                candidate_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_candidates(candidate_id) ON DELETE RESTRICT,
                claim_key TEXT NOT NULL,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                result_id INTEGER NOT NULL,
                binding_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                selector_id TEXT NOT NULL,
                policy_version INTEGER NOT NULL
                    CHECK(policy_version > 0 AND policy_version <= 2147483647),
                policy_digest TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                model_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN
                    ('escalate_applies','continue_applies','neither_apply',
                     'uncertain','invalid')),
                assessment_digest TEXT NOT NULL,
                diagnostic_code TEXT CHECK(diagnostic_code IS NULL OR diagnostic_code IN
                    ('missing_assessment','null_assessment','malformed_output',
                     'binding_mismatch')),
                what_to_escalate_json TEXT,
                what_not_to_escalate_json TEXT,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(candidate_id),
                CHECK((what_to_escalate_json IS NULL) =
                      (what_not_to_escalate_json IS NULL)),
                CHECK(what_to_escalate_json IS NOT NULL OR diagnostic_code IS NOT NULL),
                CHECK(what_to_escalate_json IS NULL OR diagnostic_code IS NULL)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_evaluations_result
                ON authority_policy_v2_evaluations(result_id);
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_evaluations_team_root
                ON authority_policy_v2_evaluations(team, root_task_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_evaluations_no_update
                BEFORE UPDATE ON authority_policy_v2_evaluations
                BEGIN SELECT RAISE(ABORT, 'v2 evaluation is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_evaluations_no_delete
                BEFORE DELETE ON authority_policy_v2_evaluations
                BEGIN SELECT RAISE(ABORT, 'v2 evaluation cannot be deleted'); END;

            -- THR-229 checkpoint C3d2: the three remaining approved additive
            -- tables.  E is the active continuation envelope (unique by
            -- candidate, immutable authenticated tuple, forward-only
            -- active -> consumed); N is the continuation notification whose
            -- APV2N identity is the immutable generation G (unique by envelope
            -- and exact causal tuple, closed lifecycle); D is the root-dispatch
            -- pointer keyed by root that admits only one non-retired generation
            -- and preserves the expected causal owner/session.  Publication,
            -- admission and spend transition writers are later units.
            CREATE TABLE IF NOT EXISTS authority_policy_v2_continue_envelopes (
                envelope_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL UNIQUE
                    REFERENCES authority_policy_v2_candidates(candidate_id) ON DELETE RESTRICT,
                claim_key TEXT NOT NULL,
                team TEXT NOT NULL,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL
                    REFERENCES authority_policy_v2_attempts(attempt_id) ON DELETE RESTRICT,
                result_id INTEGER NOT NULL
                    REFERENCES task_results(id) ON DELETE RESTRICT,
                binding_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                release_id TEXT NOT NULL,
                policy_version INTEGER NOT NULL
                    CHECK(policy_version > 0 AND policy_version <= 2147483647),
                policy_digest TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                activation_epoch INTEGER NOT NULL
                    CHECK(activation_epoch > 0 AND activation_epoch <= 2147483647),
                selector_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                model_id TEXT NOT NULL,
                causal_result_id INTEGER NOT NULL,
                causal_result_digest TEXT NOT NULL,
                evaluation_outcome TEXT NOT NULL
                    CHECK(evaluation_outcome IN ('continue_applies')),
                origin_boot_id TEXT NOT NULL,
                owner_attempt_id TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL DEFAULT 'active'
                    CHECK(lifecycle_state IN ('active','consumed')),
                spending_result_id INTEGER,
                decision_state TEXT
                    CHECK(decision_state IS NULL OR decision_state IN
                        ('ready','claimed','applied','refused')),
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK((lifecycle_state='consumed') = (spending_result_id IS NOT NULL)),
                CHECK((lifecycle_state='consumed') = (decision_state IS NOT NULL))
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_continue_envelopes_team_root
                ON authority_policy_v2_continue_envelopes(team, root_task_id);
            -- THR-229 checkpoint C3d3c1: the RESULT-KEYED spend receipt is unique
            -- per spending result, so a distinct later R2 authority attempt or
            -- generation B can never share (or be overwritten through) the exact
            -- consumed envelope of the causal generation.
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_authority_policy_v2_continue_envelopes_spending_result
                ON authority_policy_v2_continue_envelopes(spending_result_id)
                WHERE spending_result_id IS NOT NULL;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_continue_envelopes_lifecycle_guard
                BEFORE UPDATE ON authority_policy_v2_continue_envelopes
                WHEN (
                    OLD.envelope_id IS NOT NEW.envelope_id
                 OR OLD.candidate_id IS NOT NEW.candidate_id
                 OR OLD.claim_key IS NOT NEW.claim_key
                 OR OLD.team IS NOT NEW.team
                 OR OLD.root_task_id IS NOT NEW.root_task_id
                 OR OLD.manager_agent IS NOT NEW.manager_agent
                 OR OLD.manager_session_id IS NOT NEW.manager_session_id
                 OR OLD.attempt_id IS NOT NEW.attempt_id
                 OR OLD.result_id IS NOT NEW.result_id
                 OR OLD.binding_id IS NOT NEW.binding_id
                 OR OLD.contract_id IS NOT NEW.contract_id
                 OR OLD.contract_version IS NOT NEW.contract_version
                 OR OLD.contract_digest IS NOT NEW.contract_digest
                 OR OLD.release_id IS NOT NEW.release_id
                 OR OLD.policy_version IS NOT NEW.policy_version
                 OR OLD.policy_digest IS NOT NEW.policy_digest
                 OR OLD.activation_id IS NOT NEW.activation_id
                 OR OLD.activation_epoch IS NOT NEW.activation_epoch
                 OR OLD.selector_id IS NOT NEW.selector_id
                 OR OLD.provider_id IS NOT NEW.provider_id
                 OR OLD.executor_kind IS NOT NEW.executor_kind
                 OR OLD.model_id IS NOT NEW.model_id
                 OR OLD.causal_result_id IS NOT NEW.causal_result_id
                 OR OLD.causal_result_digest IS NOT NEW.causal_result_digest
                 OR OLD.evaluation_outcome IS NOT NEW.evaluation_outcome
                 OR OLD.origin_boot_id IS NOT NEW.origin_boot_id
                 OR OLD.owner_attempt_id IS NOT NEW.owner_attempt_id
                 OR OLD.created_at IS NOT NEW.created_at
                 OR NOT (
                        (OLD.lifecycle_state='active' AND OLD.spending_result_id IS NULL
                         AND OLD.decision_state IS NULL
                         AND NEW.lifecycle_state='active' AND NEW.spending_result_id IS NULL
                         AND NEW.decision_state IS NULL)
                     OR (OLD.lifecycle_state='active' AND OLD.spending_result_id IS NULL
                         AND OLD.decision_state IS NULL
                         AND NEW.lifecycle_state='consumed' AND NEW.spending_result_id IS NOT NULL
                         AND NEW.decision_state='ready')
                     OR (OLD.lifecycle_state='consumed' AND OLD.spending_result_id IS NOT NULL
                         AND OLD.decision_state IS NOT NULL
                         AND NEW.lifecycle_state='consumed'
                         AND NEW.spending_result_id IS OLD.spending_result_id
                         AND (NEW.decision_state IS OLD.decision_state
                              OR (OLD.decision_state='ready'
                                  AND NEW.decision_state IN ('claimed','refused'))
                              OR (OLD.decision_state='claimed'
                                  AND NEW.decision_state IN ('applied','refused'))))
                 )
                )
                BEGIN SELECT RAISE(ABORT, 'v2 continuation envelope identity is immutable and lifecycle is forward-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_continue_envelopes_no_delete
                BEFORE DELETE ON authority_policy_v2_continue_envelopes
                BEGIN SELECT RAISE(ABORT, 'v2 continuation envelope cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_recovery_notifications (
                notification_id TEXT PRIMARY KEY,
                envelope_id TEXT NOT NULL UNIQUE
                    REFERENCES authority_policy_v2_continue_envelopes(envelope_id) ON DELETE RESTRICT,
                candidate_id TEXT NOT NULL,
                result_id INTEGER NOT NULL
                    REFERENCES task_results(id) ON DELETE RESTRICT,
                root_task_id TEXT NOT NULL,
                manager_agent TEXT NOT NULL,
                manager_session_id TEXT NOT NULL,
                selector_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'needed' CHECK(state IN
                    ('needed','publishing','published','admitted','settled','invalidated')),
                publication_attempt INTEGER NOT NULL DEFAULT 0
                    CHECK(publication_attempt >= 0 AND publication_attempt <= 2147483647),
                publisher_boot_id TEXT,
                lease_deadline TEXT,
                next_session_id TEXT,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(result_id, candidate_id, root_task_id, manager_session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_recovery_notifications_root
                ON authority_policy_v2_recovery_notifications(root_task_id, manager_agent, manager_session_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_recovery_notifications_lifecycle_guard
                BEFORE UPDATE ON authority_policy_v2_recovery_notifications
                WHEN (
                    OLD.notification_id IS NOT NEW.notification_id
                 OR OLD.envelope_id IS NOT NEW.envelope_id
                 OR OLD.candidate_id IS NOT NEW.candidate_id
                 OR OLD.result_id IS NOT NEW.result_id
                 OR OLD.root_task_id IS NOT NEW.root_task_id
                 OR OLD.manager_agent IS NOT NEW.manager_agent
                 OR OLD.manager_session_id IS NOT NEW.manager_session_id
                 OR OLD.selector_id IS NOT NEW.selector_id
                 OR OLD.created_at IS NOT NEW.created_at
                 OR NOT (
                        OLD.state IS NEW.state
                     OR (OLD.state='needed' AND NEW.state IN ('publishing','invalidated'))
                     OR (OLD.state='publishing' AND NEW.state IN ('published','needed','admitted','invalidated'))
                     OR (OLD.state='published' AND NEW.state IN ('admitted','publishing','invalidated'))
                     OR (OLD.state='admitted' AND NEW.state IN ('settled','invalidated'))
                 )
                )
                BEGIN SELECT RAISE(ABORT, 'v2 recovery notification identity is immutable and lifecycle is forward-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_recovery_notifications_no_delete
                BEFORE DELETE ON authority_policy_v2_recovery_notifications
                BEGIN SELECT RAISE(ABORT, 'v2 recovery notification cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_v2_root_dispatch (
                root_task_id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL UNIQUE
                    REFERENCES authority_policy_v2_recovery_notifications(notification_id) ON DELETE RESTRICT,
                envelope_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','admitted','retired')),
                expected_manager_agent TEXT NOT NULL,
                expected_manager_session_id TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_v2_root_dispatch_generation
                ON authority_policy_v2_root_dispatch(generation_id);
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_root_dispatch_lifecycle_guard
                BEFORE UPDATE ON authority_policy_v2_root_dispatch
                WHEN (
                    OLD.root_task_id IS NOT NEW.root_task_id
                 OR OLD.created_at IS NOT NEW.created_at
                 OR (
                        OLD.generation_id IS NEW.generation_id
                        AND (
                            OLD.envelope_id IS NOT NEW.envelope_id
                         OR OLD.expected_manager_agent IS NOT NEW.expected_manager_agent
                         OR OLD.expected_manager_session_id IS NOT NEW.expected_manager_session_id
                         OR NOT (
                                (OLD.state='pending' AND NEW.state IN ('pending','admitted','retired'))
                             OR (OLD.state='admitted' AND NEW.state IN ('admitted','retired'))
                             OR (OLD.state='retired' AND NEW.state='retired')
                         )
                        )
                    )
                 OR (
                        OLD.generation_id IS NOT NEW.generation_id
                        AND NOT (OLD.state='retired' AND NEW.state='pending')
                    )
                )
                BEGIN SELECT RAISE(ABORT, 'v2 root dispatch admits one live generation and is forward-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_v2_root_dispatch_no_delete
                BEFORE DELETE ON authority_policy_v2_root_dispatch
                BEGIN SELECT RAISE(ABORT, 'v2 root dispatch cannot be deleted'); END;
"""


_AUTHORITY_POLICY_ACTIVATIONS_VALIDATE_INSERT_SQL = """
CREATE TRIGGER authority_policy_activations_validate_insert
BEFORE INSERT ON authority_policy_activations
BEGIN
    SELECT RAISE(ABORT, 'authority activation release/team mismatch')
    WHERE NOT EXISTS (SELECT 1 FROM authority_policy_releases r
                      WHERE r.id=NEW.release_id AND r.team=NEW.team);
    SELECT RAISE(ABORT, 'authority activation predecessor mismatch')
    WHERE (
        (SELECT COUNT(*) FROM authority_policy_activations a
         WHERE a.team=NEW.team)=0
        AND (NEW.epoch!=1 OR NEW.previous_activation_id IS NOT NULL
             OR NEW.expected_previous_epoch NOT IN (0))
    ) OR (
        (SELECT COUNT(*) FROM authority_policy_activations a
         WHERE a.team=NEW.team)>0
        AND NOT EXISTS (
            SELECT 1 FROM authority_policy_activations a
            WHERE a.id=NEW.previous_activation_id AND a.team=NEW.team
              AND a.epoch=NEW.expected_previous_epoch
              AND NEW.epoch=a.epoch+1
              AND a.epoch=(SELECT MAX(last.epoch)
                           FROM authority_policy_activations last
                           WHERE last.team=NEW.team))
    );
    SELECT RAISE(ABORT, 'authority activation action/history mismatch')
    WHERE (
        (SELECT COUNT(*) FROM authority_policy_activations a
         WHERE a.team=NEW.team)=0
        AND NEW.action!='bootstrap'
    ) OR (
        (SELECT COUNT(*) FROM authority_policy_activations a
         WHERE a.team=NEW.team)>0
        AND (
            NEW.action='bootstrap'
            OR (NEW.action='activate' AND EXISTS (
                SELECT 1 FROM authority_policy_activations a
                WHERE a.team=NEW.team AND a.release_id=NEW.release_id
            ))
            OR (NEW.action='reactivate_rollback' AND (
                NOT EXISTS (
                    SELECT 1 FROM authority_policy_activations a
                    WHERE a.team=NEW.team AND a.release_id=NEW.release_id
                )
                OR NEW.release_id=(
                    SELECT a.release_id FROM authority_policy_activations a
                    WHERE a.team=NEW.team ORDER BY a.epoch DESC LIMIT 1
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM authority_policy_releases target
                    JOIN authority_policy_activations current_activation
                      ON current_activation.team=NEW.team
                     AND current_activation.epoch=(
                         SELECT MAX(last.epoch)
                         FROM authority_policy_activations last
                         WHERE last.team=NEW.team
                     )
                    JOIN authority_policy_releases current
                      ON current.id=current_activation.release_id
                    WHERE target.id=NEW.release_id
                      AND target.policy_id=current.policy_id
                      AND target.version<current.version
                )
            ))
        )
    );
END;
"""


def _rebuild_indexes_for(
    table: str,
    conn: sqlite3.Connection,
    statements: list[tuple[str, list]],
    dropped_col: str | None = None,
) -> None:
    """Append CREATE INDEX statements for *table* after a table-rebuild.

    Called during the old-SQLite fallback path of the talk-removal migration.
    The rebuild drops all indexes on the original table; this helper re-creates
    them by reading sqlite_master. When *dropped_col* is set, skip any index
    whose SQL references it (the index is already dropped in step 1 of the
    migration).
    """
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    for (sql,) in rows:
        # Keep CREATE UNIQUE INDEX / CREATE INDEX as-is.
        if not sql.upper().startswith("CREATE "):
            continue
        if dropped_col and dropped_col in sql:
            continue
        statements.append((sql, []))


class SchemaMixin:
    def _migrate_remote_job_schema(self) -> None:
        """Install the exact additive generic remote-job S2 schema."""
        from runtime.infrastructure.remote_job_schema import migrate_remote_job_schema

        migrate_remote_job_schema(
            self._conn,
            stage_hook=getattr(self, "_remote_schema_stage_hook", None),
        )

    def _retire_skill_lifecycle_if_present(self) -> None:
        """Permanently remove legacy lifecycle tables and their content blobs."""
        tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'skill_lifecycle_%'"
            )
        }
        if not tables:
            return
        artifact_keys: list[str] = []
        if "skill_lifecycle_packages" in tables:
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(skill_lifecycle_packages)")}
            if "content_artifact_key" in columns:
                artifact_keys = [
                    row[0]
                    for row in self._conn.execute(
                        "SELECT content_artifact_key FROM skill_lifecycle_packages WHERE content_artifact_key IS NOT NULL"
                    )
                ]
        try:
            self._conn.execute("BEGIN")
            ordered = (
                "skill_lifecycle_materializations",
                "skill_lifecycle_assignments",
                "skill_lifecycle_events",
                "skill_lifecycle_packages",
            )
            for table in (*[name for name in ordered if name in tables], *sorted(tables - set(ordered))):
                self._conn.execute(f"DROP TABLE IF EXISTS {table}")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        from runtime.infrastructure.artifact_store import ArtifactStore, ArtifactNotFound

        store = ArtifactStore(self.db_path.parent / "artifacts")
        for artifact_key in artifact_keys:
            try:
                store.delete(artifact_key)
            except ArtifactNotFound:
                pass

    def _migrate_jobs_table_if_needed(self) -> None:
        """Rename legacy ``script_requests`` table to ``jobs`` and ripple the
        rename through audit_log + escalation_notifications.

        Idempotent: if ``jobs`` already exists OR ``script_requests`` does not
        exist, this is a no-op. Must run BEFORE ``_create_tables`` so the
        ``CREATE TABLE IF NOT EXISTS jobs`` below becomes a no-op on an
        already-migrated DB.

        See spec docs/superpowers/specs/2026-05-26-jobs-design.md §6.2.
        """
        # `executescript` does not return rows, so use a plain execute+fetchall
        # to inspect the schema first.
        existing = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('script_requests', 'jobs')"
            ).fetchall()
        }
        if "script_requests" not in existing or "jobs" in existing:
            return

        # Drive the migration as one explicit transaction. `executescript`
        # would issue an implicit COMMIT at start AND swallow rollback on
        # mid-script failure — leaving the DB half-migrated and the
        # idempotency check above tripping on the next startup (jobs table
        # exists but audit/notifications still reference SR-NNN). Each
        # statement goes through `execute` so any failure raises with the
        # full transaction rolled back.
        # SQLite 3.35+ supports DROP COLUMN; we rely on that for
        # `timeout_seconds`.
        migration_statements = [
            "ALTER TABLE script_requests RENAME TO jobs",

            "ALTER TABLE jobs ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN max_output_bytes INTEGER NOT NULL DEFAULT 52428800",
            "ALTER TABLE jobs ADD COLUMN stdout_bytes INTEGER",
            "ALTER TABLE jobs ADD COLUMN stderr_bytes INTEGER",
            "ALTER TABLE jobs ADD COLUMN reason TEXT",
            "ALTER TABLE jobs ADD COLUMN max_runtime_seconds INTEGER",

            "UPDATE jobs SET max_runtime_seconds = timeout_seconds"
            " WHERE timeout_seconds IS NOT NULL",
            "ALTER TABLE jobs DROP COLUMN timeout_seconds",

            # Backfill: every legacy script_request was a founder-approved one-shot.
            "UPDATE jobs SET review_required = 1 WHERE review_required = 0",

            # Force-fail orphaned 'running' rows (daemon has clearly exited by now).
            "UPDATE jobs"
            "   SET status = 'failed',"
            "       reason = 'daemon_crash',"
            "       finished_at = COALESCE(finished_at, started_at, created_at)"
            " WHERE status = 'running'",

            # ID rewrite SR-NNN -> JOB-NNN.
            "UPDATE jobs SET id = 'JOB-' || SUBSTR(id, 4) WHERE id LIKE 'SR-%'",

            # File-path rewrite scripts/SR- -> jobs/JOB-.
            "UPDATE jobs"
            "   SET stdout_path = REPLACE(REPLACE(stdout_path, '/scripts/SR-', '/jobs/JOB-'),"
            "                             '/scripts/', '/jobs/')"
            " WHERE stdout_path IS NOT NULL",
            "UPDATE jobs"
            "   SET stderr_path = REPLACE(REPLACE(stderr_path, '/scripts/SR-', '/jobs/JOB-'),"
            "                             '/scripts/', '/jobs/')"
            " WHERE stderr_path IS NOT NULL",

            # Ripple through cross-referencing tables.
            "UPDATE escalation_notifications"
            "   SET task_id = 'JOB-' || SUBSTR(task_id, 4)"
            " WHERE kind = 'script_request' AND task_id LIKE 'SR-%'",
            "UPDATE escalation_notifications"
            "   SET kind = 'job_request'"
            " WHERE kind = 'script_request'",

            # Audit rewrites. NB: real columns are `action` and `payload`
            # (NOT `kind`/`payload_json` — spec §6.2 corrected).
            # task_id values in audit_log never contain SR-NNN — only audit
            # payloads do (via script_id references), so the broad REPLACE
            # on payload below is safe.
            "UPDATE audit_log"
            "   SET action = 'job_' || SUBSTR(action, 8)"
            " WHERE action LIKE 'script_%'",
            "UPDATE audit_log"
            "   SET payload = REPLACE(payload, '\"script_id\"', '\"job_id\"')"
            " WHERE payload LIKE '%\"script_id\"%'",
            "UPDATE audit_log"
            "   SET payload = REPLACE(payload, '\"SR-', '\"JOB-')"
            " WHERE payload LIKE '%\"SR-%'",

            # Rename indexes.
            "DROP INDEX IF EXISTS idx_script_requests_task",
            "DROP INDEX IF EXISTS idx_script_requests_agent",
            "DROP INDEX IF EXISTS idx_script_requests_status",
            "DROP INDEX IF EXISTS idx_script_requests_created_at",
            "CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id)",
            "CREATE INDEX IF NOT EXISTS jobs_status_idx  ON jobs(status)",
        ]
        try:
            self._conn.execute("BEGIN")
            for stmt in migration_statements:
                self._conn.execute(stmt)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def _migrate_drop_talk_surface_if_needed(self) -> None:
        """Drop the talks table, four talk-reference columns, and five talk indexes.

        Idempotent: inspects PRAGMA table_info and sqlite_master; if the talk
        columns/table are already absent, returns immediately (no-op).

        Wraps every statement in one explicit BEGIN/COMMIT with rollback on
        exception. Uses the version-guarded DROP COLUMN / table-rebuild hybrid
        from the spec (runtime already hard-requires SQLite >= 3.35, so the
        fallback branch is belt-and-suspenders).

        Must run BEFORE ``_create_tables`` so ``CREATE TABLE IF NOT EXISTS``
        becomes a no-op on the already-dropped table/columns.

        Migration ordering (single transaction):
        1. Drop the 5 talk-related indexes.
        2. Drop the 3 columns (tasks/jobs/threads) + table-rebuild fallback.
        3. Reconcile session_token_usage.talk_id (DROP COLUMN or rebuild).
        4. DROP TABLE IF EXISTS talks.
        5. Leave audit_log untouched (talk_* rows preserved per decision #6).
        """
        # Idempotency guard: verify ALL FOUR targets are already gone
        # (talks table + the 3 talk_id columns on tasks/jobs/threads).
        # session_token_usage.talk_id is checked per-column below.
        existing_tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "talks" not in existing_tables:
            all_gone = True
            for table, col in (
                ("tasks", "dispatched_from_talk_id"),
                ("jobs", "submitted_from_talk_id"),
                ("threads", "composed_from_talk_id"),
                ("session_token_usage", "talk_id"),
            ):
                tbl_cols = {
                    row["name"]
                    for row in self._conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                if col in tbl_cols:
                    all_gone = False
                    break
            if all_gone:
                return

        sqlite_version = sqlite3.sqlite_version_info
        can_drop_column = sqlite_version >= (3, 35, 0)

        statements: list[tuple[str, list]] = []

        # 1. Drop talk-related indexes.
        for idx in (
            "idx_talks_agent_status",
            "idx_talks_started",
            "idx_tasks_dispatched_from_talk_id",
            "idx_threads_composed_from_talk",
            "idx_session_token_usage_talk",
        ):
            statements.append((f"DROP INDEX IF EXISTS {idx}", []))

        # 2. Drop the three talk-reference columns.
        for table, col in (
            ("tasks", "dispatched_from_talk_id"),
            ("jobs", "submitted_from_talk_id"),
            ("threads", "composed_from_talk_id"),
        ):
            cols = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if col not in cols:
                continue
            if can_drop_column:
                statements.append((f"ALTER TABLE {table} DROP COLUMN {col}", []))
            else:
                # Table-rebuild fallback: explicit CREATE TABLE (
                # full DDL minus the talk column), INSERT SELECT explicit
                # cols, DROP old, RENAME new, recreate indexes.
                info_rows = self._conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
                keep_cols = [r["name"] for r in info_rows if r["name"] != col]
                col_list = ", ".join(keep_cols)
                # Build the new-column DDL from PRAGMA info.
                col_defs = []
                for r in info_rows:
                    if r["name"] == col:
                        continue
                    cname = r["name"]
                    ctype = r["type"]
                    notnull = r["notnull"]
                    dflt = r["dflt_value"]
                    pk = r["pk"]
                    parts = [cname, ctype]
                    if notnull:
                        parts.append("NOT NULL")
                    if dflt is not None:
                        parts.append(f"DEFAULT {dflt}")
                    col_def = " ".join(parts)
                    # PRIMARY KEY handled separately in the table DDL.
                    col_defs.append(col_def)
                # Collect PK columns.
                pk_cols = [r["name"] for r in info_rows if r["pk"] and r["name"] != col]
                pk_clause = ""
                if pk_cols:
                    pk_clause = f", PRIMARY KEY ({', '.join(pk_cols)})"
                stmt_create = (
                    f"CREATE TABLE {table}_new (\n  "
                    + ",\n  ".join(col_defs)
                    + f"{pk_clause}\n)"
                )
                stmt_insert = (
                    f"INSERT INTO {table}_new ({col_list}) "
                    f"SELECT {col_list} FROM {table}"
                )
                statements.append((stmt_create, []))
                statements.append((stmt_insert, []))
                statements.append((f"DROP TABLE {table}", []))
                statements.append((f"ALTER TABLE {table}_new RENAME TO {table}", []))
                # Recreate indexes lost by the rebuild, skipping talk-column indexes.
                _rebuild_indexes_for(table, self._conn, statements, dropped_col=col)

        # 3. session_token_usage.talk_id.
        stu_cols = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(session_token_usage)"
            ).fetchall()
        }
        if "talk_id" in stu_cols:
            if can_drop_column:
                statements.append(
                    ("ALTER TABLE session_token_usage DROP COLUMN talk_id", [])
                )
            else:
                info_rows = self._conn.execute(
                    "PRAGMA table_info(session_token_usage)"
                ).fetchall()
                keep_cols = [r["name"] for r in info_rows if r["name"] != "talk_id"]
                col_list = ", ".join(keep_cols)
                # Build the new-column DDL from PRAGMA info.
                col_defs = []
                for r in info_rows:
                    if r["name"] == "talk_id":
                        continue
                    cname = r["name"]
                    ctype = r["type"]
                    notnull = r["notnull"]
                    dflt = r["dflt_value"]
                    pk = r["pk"]
                    parts = [cname, ctype]
                    if notnull:
                        parts.append("NOT NULL")
                    if dflt is not None:
                        parts.append(f"DEFAULT {dflt}")
                    col_def = " ".join(parts)
                    col_defs.append(col_def)
                # Collect PK columns.
                pk_cols = [r["name"] for r in info_rows if r["pk"] and r["name"] != "talk_id"]
                pk_clause = ""
                if pk_cols:
                    pk_clause = f", PRIMARY KEY ({', '.join(pk_cols)})"
                stmt_create = (
                    f"CREATE TABLE session_token_usage_new (\n  "
                    + ",\n  ".join(col_defs)
                    + f"{pk_clause}\n)"
                )
                stmt_insert = (
                    f"INSERT INTO session_token_usage_new ({col_list}) "
                    f"SELECT {col_list} FROM session_token_usage"
                )
                statements.append((stmt_create, []))
                statements.append((stmt_insert, []))
                statements.append(("DROP TABLE session_token_usage", []))
                statements.append((
                    "ALTER TABLE session_token_usage_new RENAME TO session_token_usage", []
                ))
                # Recreate indexes lost by the rebuild, skipping talk-column indexes.
                _rebuild_indexes_for(
                    "session_token_usage", self._conn, statements, dropped_col="talk_id"
                )

        # 4. Drop the talks table.
        if "talks" in existing_tables:
            statements.append(("DROP TABLE IF EXISTS talks", []))

        # Execute as one transaction.
        if not statements:
            return
        try:
            self._conn.execute("BEGIN")
            for stmt, params in statements:
                self._conn.execute(stmt, params)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _ensure_task_attachments_storage_key_unique(self) -> None:
        """Idempotent, transactional preflight: enforce storage_key uniqueness.

        Handles legacy v1 task_attachments tables where the pre-constraint
        schema allowed duplicate storage_key rows. Legacy duplicates are
        preserved for readability — marked with legacy_status='duplicate_v1'
        — and their keys cannot be newly claimed (guarded by a pre-insert
        existence check in insert_task_attachment and
        insert_task_with_attachments).

        All preflight steps — additive legacy_status column creation,
        duplicate detection/marking, and named index creation — run inside
        a single SQLite BEGIN IMMEDIATE / COMMIT transaction. On any error
        the entire preflight rolls back, leaving the database schema, data,
        and indexes exactly as they were before this invocation.

        For clean databases a full UNIQUE index is created. For databases
        with legacy duplicates a partial UNIQUE index (WHERE
        legacy_status IS NULL) enforces uniqueness on new non-legacy claims.
        """
        existing_tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "task_attachments" not in existing_tables:
            return

        # Idempotence: if our named index already exists, migration is done.
        existing_idx = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_attachments'"
            ).fetchall()
        }
        if "idx_task_attachments_storage_key_unique" in existing_idx:
            return

        # Check whether legacy_status column already exists.
        cols_before = {
            row[1]
            for row in self._conn.execute(
                "PRAGMA table_info('task_attachments')"
            ).fetchall()
        }
        has_legacy_col = "legacy_status" in cols_before

        # Single transaction: ALTER TABLE (if needed), duplicate detection,
        # marking, and index creation all inside one BEGIN / COMMIT scope.
        # On any error the entire preflight rolls back, leaving the database
        # schema, data, and indexes exactly as they were before this call.
        try:
            self._conn.execute("BEGIN IMMEDIATE")

            # Additive: ensure legacy_status column exists.
            if not has_legacy_col:
                self._conn.execute(
                    "ALTER TABLE task_attachments "
                    "ADD COLUMN legacy_status TEXT"
                )

            # Detect duplicate storage_key rows among non-legacy rows.
            # These can only exist on databases created before the UNIQUE
            # constraint was introduced (v1 pre-index schema).
            dupes = self._conn.execute(
                "SELECT storage_key FROM task_attachments "
                "WHERE legacy_status IS NULL "
                "GROUP BY storage_key HAVING COUNT(*) > 1"
            ).fetchall()

            if dupes:
                # Mark ALL rows sharing each duplicate key as legacy.
                for (dup_key,) in dupes:
                    self._conn.execute(
                        "UPDATE task_attachments SET legacy_status = ? "
                        "WHERE storage_key = ?",
                        ("duplicate_v1", dup_key),
                    )
                # Partial unique index: only non-legacy rows must be unique.
                # Legacy duplicates are excluded from the unique guard — their
                # keys are protected from new claims by pre-insert existence
                # checks in the insert methods.
                self._conn.execute(
                    "CREATE UNIQUE INDEX "
                    "idx_task_attachments_storage_key_unique "
                    "ON task_attachments(storage_key) "
                    "WHERE legacy_status IS NULL"
                )
            else:
                # Full unique index for clean databases (v0 or clean v1).
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_task_attachments_storage_key_unique "
                    "ON task_attachments(storage_key)"
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'engineering',
                brief TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'task',
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                parent_task_id TEXT,
                final_output_summary TEXT,
                final_output_dir TEXT,
                executor_pid INTEGER
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manager_supersessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                predecessor_task_id TEXT NOT NULL UNIQUE,
                successor_task_id TEXT NOT NULL UNIQUE,
                original_root_task_id TEXT NOT NULL,
                actor_agent TEXT NOT NULL,
                actor_session_id TEXT NOT NULL,
                rationale TEXT NOT NULL,
                attestation_evidence TEXT NOT NULL,
                predecessor_brief TEXT NOT NULL,
                successor_brief TEXT NOT NULL,
                predecessor_brief_sha256 TEXT NOT NULL,
                successor_brief_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(predecessor_task_id) REFERENCES tasks(id),
                FOREIGN KEY(successor_task_id) REFERENCES tasks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_manager_supersessions_original_root
                ON manager_supersessions(original_root_task_id);
            CREATE TRIGGER IF NOT EXISTS manager_supersessions_no_update
                BEFORE UPDATE ON manager_supersessions
                BEGIN SELECT RAISE(ABORT, 'manager supersessions are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS manager_supersessions_no_delete
                BEFORE DELETE ON manager_supersessions
                BEGIN SELECT RAISE(ABORT, 'manager supersessions are append-only'); END;

            CREATE TABLE IF NOT EXISTS task_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                output_summary TEXT,
                decision_json TEXT,
                confidence_score INTEGER,
                learnings TEXT,
                risks_flagged TEXT,
                duration_seconds INTEGER,
                token_count INTEGER,
                estimated_cost REAL,
                output_dir TEXT,
                created_at TEXT NOT NULL
            );

            -- THR-247: an additive, one-shot recovery fence.  Runtime
            -- invocation identity, provider continuity, and the recovered
            -- invocation deliberately remain distinct values.
            CREATE TABLE IF NOT EXISTS task_completion_recoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                origin_session_id TEXT NOT NULL,
                recovery_session_id TEXT NOT NULL UNIQUE,
                provider_session_id TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'claimed',
                accepted_result_id INTEGER,
                accepted_result_session_id TEXT,
                settled_at TEXT,
                UNIQUE(task_id, agent, origin_session_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_task_completion_recoveries_task
                ON task_completion_recoveries(task_id, agent, origin_session_id);

            CREATE TABLE IF NOT EXISTS dreams (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                summary TEXT,
                transcript_path TEXT,
                new_learnings_count INTEGER NOT NULL DEFAULT 0,
                kb_candidate_count INTEGER NOT NULL DEFAULT 0,
                founder_thread_id TEXT,
                session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(agent_name, local_date)
            );
            CREATE INDEX IF NOT EXISTS idx_dreams_agent_date
                ON dreams(agent_name, local_date);
            CREATE INDEX IF NOT EXISTS idx_dreams_status
                ON dreams(status);

            CREATE TABLE IF NOT EXISTS dream_kb_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dream_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                rationale TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                promoted_kb_slug TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dream_id, slug),
                FOREIGN KEY (dream_id) REFERENCES dreams(id)
            );
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_dream
                ON dream_kb_candidates(dream_id);
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_status
                ON dream_kb_candidates(status);

            CREATE TABLE IF NOT EXISTS work_hours (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                slot TEXT NOT NULL,
                mode TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                routine_count INTEGER NOT NULL DEFAULT 0,
                dropped_count INTEGER NOT NULL DEFAULT 0,
                spawned_task_ids TEXT,
                spawned_task_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                transcript_path TEXT,
                session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(agent_name, local_date, slot)
            );
            CREATE INDEX IF NOT EXISTS idx_work_hours_agent_date
                ON work_hours(agent_name, local_date);
            CREATE INDEX IF NOT EXISTS idx_work_hours_status
                ON work_hours(status);

            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                team TEXT NOT NULL DEFAULT 'engineering',
                kind TEXT NOT NULL,
                fire_at TEXT NOT NULL,
                recurrence TEXT,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                normalized_brief TEXT NOT NULL,
                source_instruction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'armed',
                active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                indefinite INTEGER NOT NULL DEFAULT 0,
                spawned_task_ids TEXT,
                last_fired_at TEXT,
                fire_count INTEGER NOT NULL DEFAULT 0,
                session_id TEXT,
                error TEXT,
                transcript_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedules_agent_status
                ON schedules(agent_name, status);
            CREATE INDEX IF NOT EXISTS idx_schedules_status_fire_at
                ON schedules(status, fire_at);

            CREATE TABLE IF NOT EXISTS session_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT,
                agent      TEXT NOT NULL,
                session_id TEXT NOT NULL,
                executor   TEXT NOT NULL,
                model      TEXT,
                input_tokens          INTEGER,
                output_tokens         INTEGER,
                cache_read_tokens     INTEGER,
                cache_creation_tokens INTEGER,
                reasoning_tokens      INTEGER,
                usage_raw_json TEXT,
                scope_type TEXT,
                scope_id TEXT,
                thread_id TEXT,
                invocation_purpose TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (task_id, agent, session_id)
            );

            CREATE TABLE IF NOT EXISTS escalation_notifications (
                feishu_message_id TEXT PRIMARY KEY,
                org_slug          TEXT NOT NULL,
                task_id           TEXT NOT NULL,
                chat_id           TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                expires_at        TEXT NOT NULL,
                consumed_at       TEXT,
                consumed_by       TEXT,
                kind              TEXT NOT NULL DEFAULT 'escalation'
            );
            CREATE INDEX IF NOT EXISTS idx_escalation_notifications_task
                ON escalation_notifications (task_id);

            CREATE TABLE IF NOT EXISTS processed_event_ids (
                org_slug          TEXT NOT NULL,
                feishu_event_id   TEXT NOT NULL,
                processed_at      TEXT NOT NULL,
                outcome           TEXT NOT NULL,
                reason            TEXT,
                PRIMARY KEY (org_slug, feishu_event_id)
            );

            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                started_at TEXT NOT NULL,
                archived_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                forwarded_from_id TEXT,
                forwarded_from_kind TEXT,
                turn_cap INTEGER NOT NULL DEFAULT 500,
                turns_used INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                transcript_path TEXT,
                pinned_at TEXT,
                mention_routing_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
            CREATE INDEX IF NOT EXISTS idx_threads_started ON threads(started_at);

            CREATE TABLE IF NOT EXISTS thread_participants (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL,
                agent_session_id TEXT,
                last_resumed_seq INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (thread_id, agent_name),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_participants_agent
                ON thread_participants(agent_name);

            CREATE TABLE IF NOT EXISTS thread_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                kind TEXT NOT NULL,
                body_markdown TEXT,
                addressed_to_json TEXT,
                decline_reason TEXT,
                system_payload_json TEXT,
                sent_from_task_id TEXT,
                mentions_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
                ON thread_messages(thread_id, seq);

            CREATE TABLE IF NOT EXISTS thread_message_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                message_seq INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                artifact_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id),
                UNIQUE(thread_id, message_seq, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_message_attachments_message
                ON thread_message_attachments(thread_id, message_seq);

            CREATE TABLE IF NOT EXISTS thread_scoped_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id TEXT NOT NULL UNIQUE,
                thread_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_scoped_attachments_thread
                ON thread_scoped_attachments(thread_id);

            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                storage_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                legacy_status TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(id),
                UNIQUE(task_id, ordinal),
                UNIQUE(storage_key)
            );
            CREATE INDEX IF NOT EXISTS idx_task_attachments_task
                ON task_attachments(task_id);

            CREATE TABLE IF NOT EXISTS thread_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                invocation_token TEXT NOT NULL UNIQUE,
                triggering_seq INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                enqueued_at TEXT NOT NULL,
                started_at TEXT,
                consumed_at TEXT,
                session_id TEXT,
                executor TEXT,
                model TEXT,
                reply_message_seq INTEGER,
                dispatched_task_id TEXT,
                decline_reason TEXT,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_token
                ON thread_invocations(invocation_token);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_thread
                ON thread_invocations(thread_id);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_pending
                ON thread_invocations(status) WHERE status = 'pending';
            -- GitHub #688 Phase 1 Slice A: additive, provider-neutral
            -- per-(thread_id, agent_name) conversational REPLY delivery state.
            -- Intentionally dark until Slice B wires the route/runner
            -- activation; no existing writer/runner path reads or writes it.
            -- Invocation rows in ``thread_invocations`` remain the immutable
            -- per-attempt authority; this table only records which single
            -- queued/running REPLY token currently owns each pair's delivery
            -- obligation plus the acknowledged/required watermarks.
            CREATE TABLE IF NOT EXISTS thread_reply_delivery_state (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                acknowledged_through_seq INTEGER NOT NULL DEFAULT 0,
                required_through_seq INTEGER NOT NULL DEFAULT 0,
                queued_invocation_token TEXT,
                running_invocation_token TEXT,
                running_from_seq INTEGER,
                running_through_seq INTEGER,
                last_terminal_reason TEXT,
                last_terminal_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (thread_id, agent_name),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_reply_delivery_state_thread
                ON thread_reply_delivery_state(thread_id);

            -- THR-200 PR E: one additive breaker row per continuity identity.
            -- Absence is the authoritative CLOSED representation. Outcome
            -- receipts bind terminal accounting to immutable invocation tokens.
            CREATE TABLE IF NOT EXISTS thread_reply_breaker_episodes (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                executor_key TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('closed','open','probe')),
                consecutive_failures INTEGER NOT NULL DEFAULT 0
                    CHECK (consecutive_failures >= 0),
                opened_at TEXT,
                cooldown_until TEXT,
                probe_lease_id TEXT,
                last_failure_category TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (thread_id, agent_name, executor_key),
                UNIQUE (episode_id),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_reply_breaker_due
                ON thread_reply_breaker_episodes(state, cooldown_until);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_reply_breaker_probe_lease
                ON thread_reply_breaker_episodes(probe_lease_id)
                WHERE probe_lease_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS thread_reply_breaker_receipts (
                invocation_token TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('failure','success')),
                failure_category TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_thread_reply_breaker_receipts_episode
                ON thread_reply_breaker_receipts(episode_id);

            -- TASK-5966 (strict mention-led exchange): additive exchange-epoch
            -- gate. One row per mention-led exchange; rows are append-only and
            -- ``state`` is the only mutable field. The partial unique index
            -- enforces at most one OPEN exchange per thread (write-time
            -- invariant). ``open_seq`` is the opening founder-mention message;
            -- ``close_seq`` is the last conversational message inside E;
            -- ``deferred_count`` = |D(E)| frozen at open (audit/measurement).
            CREATE TABLE IF NOT EXISTS thread_reply_exchange (
                thread_id        TEXT NOT NULL,
                exchange_id      INTEGER NOT NULL,
                state            TEXT NOT NULL CHECK (state IN ('open','released','suppressed')),
                open_seq         INTEGER NOT NULL,
                close_seq        INTEGER NOT NULL,
                opened_at        TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                closed_at        TEXT,
                close_reason     TEXT CHECK (close_reason IN
                    ('quiescence','max_priority_wait','founder_aborted',
                     'thread_archived','participant_removed','corrupt')),
                deferred_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (thread_id, exchange_id),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trex_open
                ON thread_reply_exchange(thread_id) WHERE state = 'open';
            CREATE INDEX IF NOT EXISTS idx_trex_state
                ON thread_reply_exchange(state);
            CREATE INDEX IF NOT EXISTS idx_trex_thread_seq
                ON thread_reply_exchange(thread_id, open_seq);

            -- Frozen per-(exchange, deferred agent) rows; D(E) is frozen at
            -- open (write-time-frozen doctrine). Rows are audit/sweep
            -- substrate: release is watermark-based (every pair with unread
            -- content gets one slot-checked catch-up at closure, which is a
            -- superset of D(E)).
            CREATE TABLE IF NOT EXISTS thread_exchange_deferrals (
                thread_id        TEXT NOT NULL,
                exchange_id      INTEGER NOT NULL,
                agent_name       TEXT NOT NULL,
                state            TEXT NOT NULL CHECK (state IN ('held','released','suppressed')),
                created_at       TEXT NOT NULL,
                released_at      TEXT,
                mint_token_prefix TEXT,
                catchup_pending  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (thread_id, exchange_id, agent_name),
                FOREIGN KEY (thread_id, exchange_id)
                    REFERENCES thread_reply_exchange(thread_id, exchange_id)
            );
            CREATE INDEX IF NOT EXISTS idx_txed_state
                ON thread_exchange_deferrals(state);
            CREATE INDEX IF NOT EXISTS idx_txed_thread
                ON thread_exchange_deferrals(thread_id, exchange_id);

            CREATE TABLE IF NOT EXISTS jobs (
                id                       TEXT PRIMARY KEY,
                task_id                  TEXT NOT NULL,
                agent_name               TEXT NOT NULL,
                title                    TEXT NOT NULL,
                rationale                TEXT,
                script_text              TEXT NOT NULL,
                interpreter              TEXT NOT NULL,
                cwd_hint                 TEXT,
                review_required          INTEGER NOT NULL DEFAULT 0,
                persistent               INTEGER NOT NULL DEFAULT 0,
                max_runtime_seconds      INTEGER,
                max_output_bytes         INTEGER NOT NULL DEFAULT 52428800,
                status                   TEXT NOT NULL DEFAULT 'pending',
                exit_code                INTEGER,
                reason                   TEXT,
                duration_ms              INTEGER,
                stdout_head              TEXT,
                stderr_head              TEXT,
                stdout_path              TEXT,
                stderr_path              TEXT,
                stdout_bytes             INTEGER,
                stderr_bytes             INTEGER,
                cwd_resolved             TEXT,
                started_at               TEXT,
                finished_at              TEXT,
                reviewed_at              TEXT,
                reviewed_by              TEXT,
                reject_reason            TEXT,
                created_at               TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id);
            CREATE INDEX IF NOT EXISTS jobs_status_idx  ON jobs(status);

            CREATE TABLE IF NOT EXISTS kb_views (
                slug           TEXT PRIMARY KEY,
                view_count     INTEGER NOT NULL DEFAULT 0,
                last_viewed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS org_settings (
                section     TEXT NOT NULL PRIMARY KEY,
                value_json  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                updated_by  TEXT DEFAULT 'founder'
            );
            CREATE TABLE IF NOT EXISTS skill_validation_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id       TEXT NOT NULL,
                slug           TEXT NOT NULL,
                agent          TEXT,
                source         TEXT NOT NULL DEFAULT 'user_authored',
                severity       TEXT NOT NULL DEFAULT 'info',
                ok             INTEGER NOT NULL DEFAULT 1,
                version        TEXT,
                findings       TEXT,
                reason_codes   TEXT,
                created_at     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sve_skill_id
                ON skill_validation_events(skill_id);
            CREATE INDEX IF NOT EXISTS idx_sve_agent
                ON skill_validation_events(agent);

            -- THR-055 B2 Slice A1: additive custom-skill schema. These tables
            -- are intentionally dark until the later route/resolver slices.
            CREATE TABLE IF NOT EXISTS custom_skills (
                id                  TEXT PRIMARY KEY,
                org_slug            TEXT NOT NULL,
                slug                TEXT NOT NULL,
                name                TEXT NOT NULL,
                description         TEXT NOT NULL DEFAULT '',
                policy_class        TEXT NOT NULL DEFAULT 'standard_operational' CHECK (policy_class = 'standard_operational'),
                origin_kind         TEXT NOT NULL CHECK (origin_kind IN ('agent', 'human')),
                origin_agent        TEXT,
                created_at          TEXT NOT NULL,
                created_by          TEXT NOT NULL,
                current_version_id  INTEGER,
                retired_at          TEXT,
                retired_by          TEXT,
                retired_reason      TEXT,
                purged_at           TEXT,
                purge_id            TEXT,
                FOREIGN KEY (current_version_id) REFERENCES custom_skill_versions(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_skills_org_slug
                ON custom_skills(org_slug, slug);
            CREATE INDEX IF NOT EXISTS idx_custom_skills_origin_agent
                ON custom_skills(origin_agent) WHERE origin_agent IS NOT NULL;

            CREATE TABLE IF NOT EXISTS custom_skill_versions (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id               TEXT NOT NULL REFERENCES custom_skills(id),
                parent_version_id      INTEGER REFERENCES custom_skill_versions(id),
                content_hash           TEXT NOT NULL,
                content_artifact_key   TEXT NOT NULL,
                skill_md_cache         TEXT,
                references_manifest    TEXT,
                assets_manifest        TEXT,
                validation_state       TEXT NOT NULL DEFAULT 'validation_required' CHECK (validation_state IN ('valid', 'invalid', 'validation_required')),
                validator_version       TEXT,
                validation_findings     TEXT,
                created_at              TEXT NOT NULL,
                author_kind             TEXT NOT NULL CHECK (author_kind IN ('agent', 'human')),
                author_identity          TEXT NOT NULL,
                source_task_id           TEXT,
                source_session_id        TEXT,
                task_brief_digest         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_csv_skill_id ON custom_skill_versions(skill_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_csv_skill_hash ON custom_skill_versions(skill_id, content_hash);

            CREATE TABLE IF NOT EXISTS custom_skill_eligibility_rules (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id      TEXT NOT NULL REFERENCES custom_skills(id),
                scope_type    TEXT NOT NULL CHECK (scope_type IN ('org', 'team', 'agent')),
                scope_target  TEXT,
                effect        TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
                created_at    TEXT NOT NULL,
                created_by    TEXT NOT NULL,
                superseded_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cser_skill_current
                ON custom_skill_eligibility_rules(skill_id) WHERE superseded_at IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cser_current_scope_unique
                ON custom_skill_eligibility_rules(skill_id, scope_type, COALESCE(scope_target, '')) WHERE superseded_at IS NULL;

            CREATE TABLE IF NOT EXISTS custom_skill_eligibility_events (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id                TEXT NOT NULL REFERENCES custom_skills(id),
                actor                   TEXT NOT NULL,
                preview_revision        INTEGER NOT NULL,
                rule_set_json           TEXT NOT NULL,
                affected_newly_visible  TEXT NOT NULL,
                affected_newly_hidden   TEXT NOT NULL,
                created_at              TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_csee_skill_id ON custom_skill_eligibility_events(skill_id);

            CREATE TABLE IF NOT EXISTS custom_skill_materializations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id        TEXT NOT NULL REFERENCES custom_skills(id),
                agent_name      TEXT NOT NULL,
                task_id         TEXT,
                session_context TEXT NOT NULL CHECK (session_context IN ('task', 'thread', 'wake', 'dream')),
                session_id      TEXT NOT NULL,
                version_id      INTEGER NOT NULL REFERENCES custom_skill_versions(id),
                content_hash    TEXT NOT NULL,
                success         INTEGER NOT NULL DEFAULT 0,
                error_message   TEXT,
                created_at      TEXT NOT NULL,
                CHECK ((session_context = 'task' AND task_id IS NOT NULL) OR (session_context != 'task'))
            );
            CREATE INDEX IF NOT EXISTS idx_csm_skill_agent ON custom_skill_materializations(skill_id, agent_name);
            CREATE INDEX IF NOT EXISTS idx_csm_session ON custom_skill_materializations(session_id);

            CREATE TABLE IF NOT EXISTS custom_skill_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id      TEXT NOT NULL REFERENCES custom_skills(id),
                event_type    TEXT NOT NULL CHECK (event_type IN ('created', 'version_saved', 'validated', 'retired', 'restored')),
                actor         TEXT NOT NULL,
                version_id    INTEGER REFERENCES custom_skill_versions(id),
                metadata_json TEXT,
                created_at    TEXT NOT NULL,
                task_id       TEXT,
                session_id    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cse_skill_id ON custom_skill_events(skill_id);

            CREATE TABLE IF NOT EXISTS custom_skill_purge_events (
                purge_id       TEXT PRIMARY KEY,
                skill_id       TEXT NOT NULL UNIQUE REFERENCES custom_skills(id),
                org_slug       TEXT NOT NULL,
                slug           TEXT NOT NULL,
                actor          TEXT NOT NULL,
                purged_at      TEXT NOT NULL,
                physical_erasure INTEGER NOT NULL DEFAULT 0 CHECK (physical_erasure = 0)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cspe_org_slug
                ON custom_skill_purge_events(org_slug, slug);

            """)
        for ddl in (
            "ALTER TABLE custom_skills ADD COLUMN purged_at TEXT",
            "ALTER TABLE custom_skills ADD COLUMN purge_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        self._migrate_session_token_usage_scope_columns()
        self._migrate_thread_invocation_attribution_columns()
        self._migrate_thread_invocation_reply_message_link()
        # Best-effort migration for DBs created before `status` existed. SQLite
        # has no IF NOT EXISTS for ADD COLUMN; swallow the duplicate-column
        # error so this is idempotent across restarts.
        try:
            self._conn.execute(
                "ALTER TABLE task_results ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id)"
        )
        # Thread attachment thread_attachment_id column (additive, TASK-1616).
        try:
            self._conn.execute(
                "ALTER TABLE thread_message_attachments ADD COLUMN thread_attachment_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # TASK-6057: ``thread_exchange_deferrals.catchup_pending`` (additive on
        # this PR's own unmerged exchange table). Fresh DBs get it from the
        # CREATE TABLE; DBs created by an earlier commit of this branch are
        # upgraded best-effort (idempotent across restarts).
        try:
            self._conn.execute(
                "ALTER TABLE thread_exchange_deferrals ADD COLUMN "
                "catchup_pending INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        # NOTE: the for-loop below contains DDL (RENAME COLUMN) that has no
        # explicit commit; the commit() following the UPDATE team='engineering'
        # block durably persists those DDLs. Don't insert returning code between.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN final_output_summary TEXT",
            # Manager-only structured decision payload (serialized NextStep
            # JSON). NULL for worker rows. Replaces the prose-in-output_summary
            # double-encoding contract — see TASK-071 post-mortem.
            "ALTER TABLE task_results ADD COLUMN decision_json TEXT",
            # crew → team rename (SQLite >= 3.25). Idempotent: fails on
            # DBs where the column is already `team` or already renamed.
            "ALTER TABLE tasks RENAME COLUMN crew TO team",
            # Per-agent output-dir rename (2026-06-02). Idempotent: fails on DBs
            # where the column is already `final_output_dir`/`output_dir` (fresh or
            # already-renamed). See docs/superpowers/plans/2026-06-01-rename-assets-to-artifacts.md.
            "ALTER TABLE tasks RENAME COLUMN final_artifact_dir TO final_output_dir",
            "ALTER TABLE task_results RENAME COLUMN artifact_dir TO output_dir",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        # Remap legacy team value: 'product_engineering' → 'engineering'.
        try:
            self._conn.execute(
                "UPDATE tasks SET team='engineering' WHERE team='product_engineering'"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # Path-string rewrite: stored relative paths under 'artifacts/' point at the
        # pre-rename per-agent dir. Rewrite to 'output/' so recall resolves correctly.
        # Idempotent: re-running matches no rows once paths have been rewritten.
        try:
            self._conn.execute(
                "UPDATE tasks SET final_output_dir = 'output/' || substr(final_output_dir, length('artifacts/') + 1) "
                "WHERE final_output_dir LIKE 'artifacts/%'"
            )
            self._conn.execute(
                "UPDATE task_results SET output_dir = 'output/' || substr(output_dir, length('artifacts/') + 1) "
                "WHERE output_dir LIKE 'artifacts/%'"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # THR-105 recurrence v2: nullable terminal cause for naturally ended
        # schedules. Existing rows intentionally remain NULL.
        try:
            self._conn.execute("ALTER TABLE schedules ADD COLUMN end_reason TEXT")
        except sqlite3.OperationalError:
            pass

        # --- Task-status redesign migration (idempotent) ---
        # Add new columns; swallow duplicate errors on subsequent startups.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
            "ALTER TABLE tasks ADD COLUMN note TEXT",
            "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
            # cancelled_at: founder-initiated cancellation marker. Distinct
            # from completed_at/status=failed so run_step can recognise a
            # SIGTERM'd session as "cancelled" (not a retryable failure) and
            # idempotent _fail calls don't overwrite the founder's note.
            "ALTER TABLE tasks ADD COLUMN cancelled_at TEXT",
            # Revisit link: see docs/superpowers/specs/2026-04-23-revisit-root-link-design.md.
            # Sideways reference to the predecessor root of a revisit; NULL for
            # non-revisit tasks. walk_ancestors MUST NOT follow this column —
            # that's the attempt-isolation invariant from the v2 revisit spec.
            "ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT",
            # Liveness heartbeat: queue worker stamps this while a subprocess
            # is alive so `happyranch details` can show progress on long-running
            # tasks. Distinct from updated_at (which advances on any write).
            "ALTER TABLE tasks ADD COLUMN last_heartbeat TEXT",
            # Per-task subprocess timeout override. NULL → resolver falls
            # through to org/config.yaml then Settings default. Founder sets
            # via `happyranch revisit --session-timeout-seconds`; inherited from
            # parent on delegate and from predecessor root on revisit.
            "ALTER TABLE tasks ADD COLUMN session_timeout_seconds INTEGER",
            # Job-blocking link: spec §3.1. JSON array of JOB-NNN IDs that must
            # complete before this task can proceed. NULL means unblocked.
            "ALTER TABLE tasks ADD COLUMN blocked_on_job_ids TEXT",
            # Completion-report job-wait list: JSON array of JOB-NNN IDs the
            # agent asked to block on. Persisted alongside the task_result row
            # so run_step can read it back via _read_completion_from_db.
            "ALTER TABLE task_results ADD COLUMN waiting_on_job_ids TEXT",
            # Worker-reported verdict (free string: APPROVE, PASS, REQUEST_CHANGES,
            # etc.). Used by inline delegation chains to gate auto-advance to the
            # next leg without consuming the manager's orchestration_step_count.
            # NULL for non-chain or non-verdict workers.
            "ALTER TABLE task_results ADD COLUMN verdict TEXT",
            # Push-PR local CI evidence (additive, nullable). A JSON object
            # with command + exit_code persisted losslessly so audit and
            # reconstruction round-trips preserve it.
            "ALTER TABLE task_results ADD COLUMN local_ci TEXT",
            # Thread agent-session resume (issue #53). agent_session_id holds the
            # resumable agent session for this (thread, agent); NULL = none yet /
            # evicted. last_resumed_seq is the highest thread message seq the stored
            # session has been shown — the delta watermark, advanced only on a
            # successful turn.
            "ALTER TABLE thread_participants ADD COLUMN agent_session_id TEXT",
            "ALTER TABLE thread_participants ADD COLUMN last_resumed_seq INTEGER NOT NULL DEFAULT 0",
            # Legacy cleanup: drop the dead `type` column (dropped from the
            # current schema in the Task-4 refactor; never read, only a
            # "general" sentinel was written). Idempotent via the try/except
            # below — DROP of an absent column raises OperationalError.
            "ALTER TABLE tasks DROP COLUMN type",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # task_type column + one-time provenance backfill. Coupled in a single
        # try/except so the backfill UPDATE runs EXACTLY ONCE — when ADD COLUMN
        # succeeds on the first upgrade. On later startups (and on fresh DBs,
        # where CREATE TABLE already defines the column) ADD raises
        # duplicate-column and the whole block is skipped. Existing rows with a
        # parent were spawned from an ongoing task, so under the new model they
        # are subtasks (leaf); roots keep the 'task' default. Without this
        # backfill an in-flight pre-existing child would be mis-typed 'task' and
        # run_step would parse its plain completion as a NextStep decision and
        # escalate. (A task_type='task' row never has a parent, so the predicate
        # is provenance-correct and safe even if it ever re-ran.)
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'task'"
            )
            self._conn.execute(
                "UPDATE tasks SET task_type='subtask' WHERE parent_task_id IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass
        # Index the reverse lookup (`WHERE revisit_of_task_id = ?`).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id)"
        )
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN dispatched_from_thread_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN active_chain TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN active_fanout TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # THR-079: executor OS pid for daemon-restart liveness probe.
        # Persisted at _on_started (orchestrator.py); read by _sweep_on_startup.
        # NULL for pre-migration rows (fail-closed on first post-deploy restart).
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN executor_pid INTEGER"
            )
        except sqlite3.OperationalError:
            pass
        # THR-090 Track A: current session id, persisted at _on_started
        # alongside executor_pid. Used by the daemon-restart sweep to scope
        # orphaned-result detection to the CURRENT session only. A prior-step
        # result row carries a different session uuid and must never match.
        # NULL for pre-migration rows (fail-closed: no session-scoped match
        # → falls through to dead-pid FAIL path).
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN current_session_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # THR-090 Track B: timestamp of first zombie detection for the
        # ongoing zombie reaper. Set on first flag; cleared (NULL) on
        # recovery; used for flag-then-cancel-on-TTL. NULL default —
        # never been flagged. Additive-only.
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN zombie_flagged_at TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_thread_id "
            "ON tasks(dispatched_from_thread_id) "
            "WHERE dispatched_from_thread_id IS NOT NULL"
        )
        # Agent-initiated threads: composer attribution + session binding.
        # Sideways refs — NOT walked by walk_ancestors. Mutually exclusive at
        # insert time (daemon enforces); default 'founder' preserves all
        # existing rows on first migration.
        for ddl in (
            "ALTER TABLE threads ADD COLUMN composed_by TEXT NOT NULL DEFAULT 'founder'",
            "ALTER TABLE threads ADD COLUMN composed_from_task_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_task "
            "ON threads(composed_from_task_id) "
            "WHERE composed_from_task_id IS NOT NULL"
        )
        # Dream-originated threads: dream attribution marker (design-overhaul A4).
        # Additive nullable; existing rows stay NULL.
        try:
            self._conn.execute(
                "ALTER TABLE threads ADD COLUMN composed_from_dream_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_dream "
            "ON threads(composed_from_dream_id) "
            "WHERE composed_from_dream_id IS NOT NULL"
        )
        # Founder-workspace pin state (THR-209): additive nullable timestamp;
        # non-NULL = pinned. Existing rows (and fresh inserts) stay NULL until
        # the founder pins. Presentation-only — never affects messages,
        # participants, unread, or lifecycle.
        try:
            self._conn.execute(
                "ALTER TABLE threads ADD COLUMN pinned_at TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # Task-session post-to-existing-thread provenance (THR-027): the task id
        # whose live session appended a message via POST /threads/{id}/post-as-agent.
        # Additive nullable; existing rows + founder/compose/reply messages stay
        # NULL. No index — provenance is read by message, never queried by task.
        try:
            self._conn.execute(
                "ALTER TABLE thread_messages ADD COLUMN sent_from_task_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # Phase-2 thread mention routing (THR-198, seq 108-110 approval):
        # per-thread default-enabled switch + per-message structured mention
        # signal. Both additive and statement-identical to the fresh CREATE
        # definitions above — existing threads adopt the enabled default,
        # historical messages stay NULL, no replay. Storage only in Slice A;
        # routing behavior lands in Slice B.
        try:
            self._conn.execute(
                "ALTER TABLE threads ADD COLUMN "
                "mention_routing_enabled INTEGER NOT NULL DEFAULT 1"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE thread_messages ADD COLUMN mentions_json TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # kind column for escalation_notifications: 'escalation' (default) or
        # 'failure'. Additive; existing rows keep the default.
        try:
            self._conn.execute(
                "ALTER TABLE escalation_notifications ADD COLUMN kind "
                "TEXT NOT NULL DEFAULT 'escalation'"
            )
        except sqlite3.OperationalError:
            pass

        # --- Revisit link backfill ---
        # Historical revisit rows (created before revisit_of_task_id existed)
        # have the column but no value; the link lives only in audit_log's
        # revisit_of entry. Populate the column from those entries.
        # IS NULL guard makes this safely idempotent across restarts.
        self._backfill_revisit_of_task_id()

        # One-shot data remap. Guard with a sentinel so re-runs are no-ops.
        applied = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks' "
            "AND sql LIKE '%block_kind%'"
        ).fetchone()
        if applied is not None:
            # Fold final_output_summary → note where not already set.
            self._conn.execute(
                "UPDATE tasks SET note = final_output_summary "
                "WHERE note IS NULL AND final_output_summary IS NOT NULL"
            )
            # Old-world → new-world status mapping. Each UPDATE is narrow so
            # re-running is a no-op (no rows match the WHERE clause the 2nd time).
            self._conn.execute("UPDATE tasks SET status='completed' WHERE status='approved'")
            self._conn.execute("UPDATE tasks SET status='failed'    WHERE status='rejected'")
            self._conn.execute(
                "UPDATE tasks SET status='blocked', block_kind='escalated' "
                "WHERE status='escalated'"
            )
            # Normalize dead legacy values.
            self._conn.execute("UPDATE tasks SET status='failed' WHERE status='in_review'")
            # --- THR-037 Change B (Path B) live-row migration ---
            # Collapse the surfaced `blocked` vocabulary into the stored model:
            #   blocked(escalated)    → escalated (top-level), block_kind cleared
            #   blocked(delegated)    → in_progress, reason kept in block_kind
            #   blocked(blocked_on_job) → in_progress, reason kept in block_kind
            # Idempotent: each UPDATE's WHERE matches zero rows on re-run.
            # LIVE rows only — historical terminal rows (failed + cancelled_at)
            # are LEFT AS-IS; only new cancellations write status='cancelled'
            # (derivations read cancelled_at, not the status label). Forward-only
            # posture; the reverse migration is published in the Path-B spec
            # (docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md).
            # No DDL: neither status nor block_kind has a CHECK constraint, so
            # the new values are application-enum-only.
            self._conn.execute(
                "UPDATE tasks SET status='escalated', block_kind=NULL "
                "WHERE status='blocked' AND block_kind='escalated'"
            )
            self._conn.execute(
                "UPDATE tasks SET status='in_progress' "
                "WHERE status='blocked' AND block_kind='delegated'"
            )
            self._conn.execute(
                "UPDATE tasks SET status='in_progress' "
                "WHERE status='blocked' AND block_kind='blocked_on_job'"
            )
            # --- THR-080 Slice A: rename resolved_superseded -> superseded ---
            # One-way DB row rewrite (founder-ratified). No dual-read; code
            # reads only 'superseded' after this. Idempotent across restarts.
            self._conn.execute(
                "UPDATE tasks SET status='superseded' "
                "WHERE status='resolved_superseded'"
            )
            self._conn.commit()

        # Cleanup activity indexes follow the legacy column migrations above.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cleanup_tasks_agent_created_id "
            "ON tasks(assigned_agent,created_at DESC,id DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cleanup_trigger_task_agent "
            "ON audit_log(task_id,agent) WHERE action='workspace_cleanup_triggered'"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cleanup_results_task_agent_id "
            "ON task_results(task_id,agent,id DESC)"
        )

    def _create_authority_tables(self) -> None:
        """THR-181 Track A Slice 1: additive durable authority foundation.

        Creates three dedicated, clearly named ``authority_*`` tables plus
        their indexes and DB-level protections. This is *purely additive* —
        it never alters ``audit_log``, ``tasks``, or any existing column/row
        meaning (``audit_log.task_id`` scope prefixes and
        ``tasks.blocked_on_job_ids`` / revisit/lineage fields are untouched).

        Idempotent: every statement is ``IF NOT EXISTS``, so re-opening the
        same database (or a pre-migration v0 file) is a no-op on the second
        run. Boot ordering: called from ``__init__`` after ``_create_tables``
        so the FK target exists before the tables that reference it.

        Append-only surfaces (``authority_evaluations`` and
        ``authority_audit``) carry BEFORE UPDATE / BEFORE DELETE triggers that
        RAISE(ABORT). ``authority_candidates`` blocks deletion and any change
        to its identity columns, while allowing the narrow lifecycle
        transition (created -> evaluated -> consumed) performed only through
        the persistence API below.
        """
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS authority_policy_releases (
                id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                title TEXT NOT NULL,
                normative_text TEXT NOT NULL,
                clauses_json TEXT NOT NULL,
                continuation_phrase TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                policy_digest TEXT NOT NULL UNIQUE,
                based_on_release_id TEXT REFERENCES authority_policy_releases(id) ON DELETE RESTRICT,
                actor_kind TEXT NOT NULL CHECK(actor_kind='shared_local_operator_credential'),
                created_at TEXT NOT NULL,
                UNIQUE(team, policy_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_releases_team_version
                ON authority_policy_releases(team, version DESC);
            CREATE TRIGGER IF NOT EXISTS authority_policy_releases_validate_insert
                BEFORE INSERT ON authority_policy_releases
                BEGIN
                    SELECT RAISE(ABORT, 'authority policy release base/team mismatch')
                    WHERE NEW.based_on_release_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM authority_policy_releases r
                        WHERE r.id=NEW.based_on_release_id AND r.team=NEW.team);
                END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_releases_no_update
                BEFORE UPDATE ON authority_policy_releases
                BEGIN SELECT RAISE(ABORT, 'authority policy releases are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_releases_no_delete
                BEFORE DELETE ON authority_policy_releases
                BEGIN SELECT RAISE(ABORT, 'authority policy releases cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_policy_activations (
                id TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                epoch INTEGER NOT NULL CHECK(epoch > 0),
                release_id TEXT NOT NULL REFERENCES authority_policy_releases(id) ON DELETE RESTRICT,
                previous_activation_id TEXT REFERENCES authority_policy_activations(id) ON DELETE RESTRICT,
                expected_previous_epoch INTEGER CHECK(expected_previous_epoch IS NULL OR expected_previous_epoch >= 0),
                action TEXT NOT NULL CHECK(action IN ('activate','reactivate_rollback','bootstrap')),
                actor_kind TEXT NOT NULL CHECK(actor_kind='shared_local_operator_credential'),
                request_id TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                activation_digest TEXT NOT NULL,
                UNIQUE(team, epoch)
            );
            CREATE INDEX IF NOT EXISTS idx_authority_policy_activations_team_epoch
                ON authority_policy_activations(team, epoch DESC);
            CREATE TRIGGER IF NOT EXISTS authority_policy_activations_no_update
                BEFORE UPDATE ON authority_policy_activations
                BEGIN SELECT RAISE(ABORT, 'authority policy activations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_policy_activations_no_delete
                BEFORE DELETE ON authority_policy_activations
                BEGIN SELECT RAISE(ABORT, 'authority policy activations cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_candidates (
                id                       TEXT PRIMARY KEY,
                claim_key                TEXT NOT NULL UNIQUE,
                root_task_id             TEXT NOT NULL,
                team                     TEXT NOT NULL,
                manager_agent            TEXT NOT NULL,
                manager_session_id       TEXT NOT NULL,
                causal_event_id          TEXT NOT NULL,
                causal_event_digest      TEXT NOT NULL,
                causal_result_id         TEXT,
                policy_id                TEXT NOT NULL,
                policy_version           TEXT NOT NULL,
                policy_digest            TEXT NOT NULL,
                prompt_id                TEXT NOT NULL,
                prompt_version           TEXT NOT NULL,
                prompt_digest            TEXT NOT NULL,
                model_id                 TEXT NOT NULL,
                model_version            TEXT NOT NULL,
                model_digest             TEXT NOT NULL,
                snapshot_digest          TEXT NOT NULL,
                snapshot_retention_class TEXT NOT NULL DEFAULT 'digest_only'
                    CHECK (snapshot_retention_class IN ('digest_only','shadow','indefinite')),
                snapshot_redaction_class TEXT NOT NULL DEFAULT 'redacted'
                    CHECK (snapshot_redaction_class IN ('none','redacted')),
                fence_results_json       TEXT,
                disposition              TEXT
                    CHECK (disposition IS NULL OR disposition IN
                        ('continue_same_root','escalate','not_applicable','evaluator_error')),
                lifecycle_state          TEXT NOT NULL DEFAULT 'created'
                    CHECK (lifecycle_state IN ('created','evaluated','consumed')),
                consumed_at              TEXT,
                created_at               TEXT NOT NULL,
                updated_at               TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_candidates_root
                ON authority_candidates(root_task_id);
            CREATE INDEX IF NOT EXISTS idx_authority_candidates_root_outcome
                ON authority_candidates(root_task_id, disposition);

            CREATE TRIGGER IF NOT EXISTS authority_candidates_no_delete
                BEFORE DELETE ON authority_candidates
                BEGIN SELECT RAISE(ABORT, 'authority candidates cannot be deleted'); END;
            CREATE TRIGGER IF NOT EXISTS authority_candidates_identity_immutable
                BEFORE UPDATE ON authority_candidates
                WHEN OLD.claim_key != NEW.claim_key
                  OR OLD.root_task_id != NEW.root_task_id
                  OR OLD.team != NEW.team
                  OR OLD.manager_agent != NEW.manager_agent
                  OR OLD.manager_session_id != NEW.manager_session_id
                  OR OLD.causal_event_id != NEW.causal_event_id
                  OR OLD.causal_event_digest != NEW.causal_event_digest
                  OR OLD.causal_result_id IS NOT NEW.causal_result_id
                  OR OLD.policy_id != NEW.policy_id
                  OR OLD.policy_version != NEW.policy_version
                  OR OLD.policy_digest != NEW.policy_digest
                  OR OLD.prompt_id != NEW.prompt_id
                  OR OLD.prompt_version != NEW.prompt_version
                  OR OLD.prompt_digest != NEW.prompt_digest
                  OR OLD.model_id != NEW.model_id
                  OR OLD.model_version != NEW.model_version
                  OR OLD.model_digest != NEW.model_digest
                  OR OLD.snapshot_digest != NEW.snapshot_digest
                  OR OLD.snapshot_retention_class != NEW.snapshot_retention_class
                  OR OLD.snapshot_redaction_class != NEW.snapshot_redaction_class
                  OR OLD.fence_results_json IS NOT NEW.fence_results_json
                  OR OLD.created_at != NEW.created_at
                BEGIN
                    SELECT RAISE(ABORT, 'authority candidate identity is immutable');
                END;

            CREATE TABLE IF NOT EXISTS authority_candidate_policy_pins (
                candidate_id TEXT PRIMARY KEY REFERENCES authority_candidates(id) ON DELETE RESTRICT,
                release_id TEXT NOT NULL REFERENCES authority_policy_releases(id) ON DELETE RESTRICT,
                activation_id TEXT NOT NULL REFERENCES authority_policy_activations(id) ON DELETE RESTRICT,
                activation_epoch INTEGER NOT NULL CHECK(activation_epoch > 0),
                provider_id TEXT NOT NULL,
                executor_kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS authority_candidate_policy_pins_validate_insert
                BEFORE INSERT ON authority_candidate_policy_pins
                BEGIN
                    SELECT RAISE(ABORT, 'authority candidate pin identity mismatch')
                    WHERE NOT EXISTS (
                        SELECT 1 FROM authority_candidates c
                        JOIN authority_policy_releases r ON r.id=NEW.release_id
                        JOIN authority_policy_activations a ON a.id=NEW.activation_id
                        WHERE c.id=NEW.candidate_id AND c.team=r.team
                          AND c.team=a.team AND a.release_id=r.id
                          AND a.epoch=NEW.activation_epoch
                          AND c.policy_id=r.policy_id
                          AND c.policy_version=CAST(r.version AS TEXT)
                          AND c.policy_digest=r.policy_digest
                    );
                END;
            CREATE TRIGGER IF NOT EXISTS authority_candidate_policy_pins_no_update
                BEFORE UPDATE ON authority_candidate_policy_pins
                BEGIN SELECT RAISE(ABORT, 'authority candidate policy pins are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS authority_candidate_policy_pins_no_delete
                BEFORE DELETE ON authority_candidate_policy_pins
                BEGIN SELECT RAISE(ABORT, 'authority candidate policy pins cannot be deleted'); END;

            CREATE TABLE IF NOT EXISTS authority_evaluations (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id             TEXT NOT NULL UNIQUE REFERENCES authority_candidates(id),
                disposition              TEXT NOT NULL
                    CHECK (disposition IN
                        ('continue_same_root','escalate','not_applicable','evaluator_error')),
                disposition_code         TEXT NOT NULL
                    CHECK (disposition_code IN
                        ('continue_same_root','escalate','not_applicable','evaluator_error',
                         'low_confidence','timeout','malformed_output','injection_guard','audit_failure')),
                response_digest          TEXT NOT NULL,
                response_retention_class TEXT NOT NULL DEFAULT 'digest_only'
                    CHECK (response_retention_class IN ('digest_only','shadow','indefinite')),
                response_redaction_class TEXT NOT NULL DEFAULT 'redacted'
                    CHECK (response_redaction_class IN ('none','redacted')),
                fence_results_json       TEXT,
                created_at               TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_evaluations_candidate
                ON authority_evaluations(candidate_id);

            CREATE TRIGGER IF NOT EXISTS authority_evaluations_no_update
                BEFORE UPDATE ON authority_evaluations
                BEGIN SELECT RAISE(ABORT, 'authority evaluations are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_evaluations_no_delete
                BEFORE DELETE ON authority_evaluations
                BEGIN SELECT RAISE(ABORT, 'authority evaluations are append-only'); END;

            CREATE TABLE IF NOT EXISTS authority_audit (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL REFERENCES authority_candidates(id),
                event_type   TEXT NOT NULL
                    CHECK (event_type IN
                        ('candidate_claimed','candidate_claim_lost',
                         'evaluation_recorded','candidate_consumed')),
                payload_json TEXT,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_audit_candidate
                ON authority_audit(candidate_id);

            CREATE TRIGGER IF NOT EXISTS authority_audit_no_update
                BEFORE UPDATE ON authority_audit
                BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS authority_audit_no_delete
                BEFORE DELETE ON authority_audit
                BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;

            -- THR-181 Track A (founder lifecycle envelope): the single-use continuation
            -- envelope. Minted ATOMICALLY with ``commit_authority_continue_
            -- same_root`` (state='active'), bound 1:1 to the authority
            -- candidate/evaluation and to the immutable causal task-result row,
            -- and consumed EXACTLY ONCE by the continued turn's normally
            -- validated manager decision (``active -> consumed``), or spent
            -- fail-closed when the lifecycle window aborts (``violated``).
            -- DB-level enforcement (not only Python): no delete, immutable
            -- identity, and a narrow finite lifecycle transition. Additive
            -- table (same class as the merged authority_candidates/evaluations/
            -- audit foundation) — no existing column or overloaded-column
            -- semantics is touched.
            CREATE TABLE IF NOT EXISTS authority_continue_envelopes (
                id                   TEXT PRIMARY KEY,
                candidate_id         TEXT NOT NULL UNIQUE REFERENCES authority_candidates(id),
                root_task_id         TEXT NOT NULL,
                team                 TEXT NOT NULL,
                manager_agent        TEXT NOT NULL,
                manager_session_id   TEXT NOT NULL,
                causal_event_id      TEXT NOT NULL,
                causal_event_digest  TEXT NOT NULL,
                policy_id            TEXT NOT NULL,
                policy_version       TEXT NOT NULL,
                policy_digest        TEXT NOT NULL,
                clause_id            TEXT NOT NULL,
                action               TEXT NOT NULL
                    CHECK (action IN ('escalate_to_founder','continue_same_root')),
                state                TEXT NOT NULL DEFAULT 'active'
                    CHECK (state IN ('active','consumed','violated')),
                consumed_at          TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_authority_envelopes_root_state
                ON authority_continue_envelopes(root_task_id, state);

            CREATE TRIGGER IF NOT EXISTS authority_continue_envelopes_no_delete
                BEFORE DELETE ON authority_continue_envelopes
                BEGIN SELECT RAISE(ABORT, 'authority continue envelopes cannot be deleted'); END;
            CREATE TRIGGER IF NOT EXISTS authority_continue_envelopes_identity_immutable
                BEFORE UPDATE ON authority_continue_envelopes
                WHEN OLD.candidate_id != NEW.candidate_id
                  OR OLD.root_task_id != NEW.root_task_id
                  OR OLD.team != NEW.team
                  OR OLD.manager_agent != NEW.manager_agent
                  OR OLD.manager_session_id != NEW.manager_session_id
                  OR OLD.causal_event_id != NEW.causal_event_id
                  OR OLD.causal_event_digest != NEW.causal_event_digest
                  OR OLD.policy_id != NEW.policy_id
                  OR OLD.policy_version != NEW.policy_version
                  OR OLD.policy_digest != NEW.policy_digest
                  OR OLD.clause_id != NEW.clause_id
                  OR OLD.action != NEW.action
                  OR OLD.created_at != NEW.created_at
                BEGIN
                    SELECT RAISE(ABORT, 'authority continue envelope identity is immutable');
                END;
            CREATE TRIGGER IF NOT EXISTS authority_continue_envelopes_lifecycle_guard
                BEFORE UPDATE ON authority_continue_envelopes
                WHEN NOT (
                    -- active -> consumed|violated: exactly-once; consumed_at
                    -- is stamped exactly once (NULL -> value).
                    (OLD.state = 'active' AND NEW.state IN ('consumed','violated')
                     AND OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL)
                    -- no-op on the guarded columns (e.g. updated_at-only writes).
                    OR (OLD.state = NEW.state
                        AND NEW.consumed_at IS OLD.consumed_at)
                )
                BEGIN
                    SELECT RAISE(ABORT, 'authority continue envelope lifecycle is restricted');
                END;

            -- DB-level lifecycle enforcement (not only Python): blocks fabrication
            -- through a raw ``Database.execute`` UPDATE. Only the intended finite
            -- transitions are permitted, disposition and consumed_at are immutable
            -- once set, and the ``evaluated``/``consumed`` states require a
            -- consistent ``authority_evaluations`` row for the candidate whose
            -- disposition exactly mirrors the candidate's frozen disposition.
            -- The trigger body references ``authority_evaluations`` (created above),
            -- which SQLite resolves at trigger execution time. Databases created at
            -- earlier reviewed heads that already carry the weaker trigger body are
            -- upgraded by ``_retrofit_authority_lifecycle_trigger_if_needed``.
            {_AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL}
            {_AUTHORITY_POLICY_V2_CONTROL_SCHEMA_SQL}
        """)
        self._conn.commit()

    def _migrate_dark_authority_activation_seal_if_needed(self) -> None:
        """Upgrade only an empty interrupted-development activation table.

        S1 is unmerged and dark.  A pre-seal table containing rows has no
        truthful stored activation seal to preserve, so reopening fails closed
        instead of inventing provenance.  Empty interrupted tables can safely
        receive the final required column before normal creation resumes.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='authority_policy_activations'"
        ).fetchone()
        if exists is None:
            return
        columns = {
            row["name"] for row in self._conn.execute(
                "PRAGMA table_info(authority_policy_activations)"
            )
        }
        if "activation_digest" in columns:
            return
        if self._conn.execute(
            "SELECT 1 FROM authority_policy_activations LIMIT 1"
        ).fetchone() is not None:
            raise ValueError(
                "populated dark authority activations lack truthful activation_digest"
            )
        self._conn.execute(
            "ALTER TABLE authority_policy_activations "
            "ADD COLUMN activation_digest TEXT NOT NULL"
        )
        self._conn.commit()

    def _retrofit_authority_audit_fk_if_needed(self) -> None:
        """Idempotent forward retrofit of the ``authority_audit`` candidate FK.

        The corrective head (07eaaed0) added
        ``authority_audit.candidate_id REFERENCES authority_candidates(id)``,
        but that reference lives only inside ``CREATE TABLE IF NOT EXISTS``. A
        database created at the prior reviewed head (405697a0) therefore
        retains an ``authority_audit`` table WITHOUT the FK after opening the
        corrected build — ``CREATE TABLE IF NOT EXISTS`` is a no-op on the
        already-existing table, so a raw ``Database.execute`` orphan INSERT
        succeeds and commits. Fresh-database tests cannot see this.

        This retrofit upgrades that legacy table in place, atomically and
        idempotently:

        * If ``authority_audit`` is absent, this is a no-op — the corrected
          ``_create_authority_tables`` creates it with the FK on fresh files.
        * If the table already carries the FK, this is a no-op (idempotent).
        * If the table is the legacy no-FK shape, it is rebuilt as a
          transactionally-safe replacement table: every valid row is copied
          verbatim (id, candidate_id, event_type, payload_json, created_at —
          identity and order preserved), the old table is dropped, the new
          table is renamed into place, and the index + append-only triggers
          are recreated. All DDL and the row copy run inside ONE explicit
          transaction, so a mid-migration failure rolls back with no partial
          replacement, no synthetic empty table, and no data loss; a later
          reopen retries the whole migration.
        * Legacy orphan audit rows (a ``candidate_id`` with no matching
          ``authority_candidates`` row) are never deleted, rewritten, or
          re-parented. The migration refuses atomically — raising
          :class:`AuthorityAuditMigrationRefusal` before any mutation — and
          leaves the old schema/data intact for inspection.

        ``executescript`` is deliberately avoided: it issues an implicit
        COMMIT and swallows mid-script rollback, which would defeat the
        atomicity requirement. Every statement runs through ``execute`` so a
        failure raises with the whole transaction rolled back.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority_audit'"
        ).fetchone()
        if exists is None:
            return
        fks = self._conn.execute(
            "PRAGMA foreign_key_list(authority_audit)"
        ).fetchall()
        if fks:
            return
        orphan_count = self._conn.execute(
            "SELECT COUNT(*) FROM authority_audit a "
            "WHERE NOT EXISTS (SELECT 1 FROM authority_candidates c "
            "WHERE c.id = a.candidate_id)"
        ).fetchone()[0]
        if orphan_count:
            raise AuthorityAuditMigrationRefusal(
                f"authority_audit contains {orphan_count} legacy orphan row(s) whose "
                "candidate_id has no matching authority_candidates row; refusing to "
                "retrofit the candidate FK. The legacy schema and data are left intact "
                "for inspection."
            )
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                """
                CREATE TABLE authority_audit__new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL REFERENCES authority_candidates(id),
                    event_type   TEXT NOT NULL
                        CHECK (event_type IN
                            ('candidate_claimed','candidate_claim_lost',
                             'evaluation_recorded','candidate_consumed')),
                    payload_json TEXT,
                    created_at   TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "INSERT INTO authority_audit__new "
                "(id, candidate_id, event_type, payload_json, created_at) "
                "SELECT id, candidate_id, event_type, payload_json, created_at "
                "FROM authority_audit"
            )
            self._conn.execute("DROP TABLE authority_audit")
            self._conn.execute(
                "ALTER TABLE authority_audit__new RENAME TO authority_audit"
            )
            self._conn.execute(
                "CREATE INDEX idx_authority_audit_candidate "
                "ON authority_audit(candidate_id)"
            )
            self._conn.execute(
                "CREATE TRIGGER authority_audit_no_update "
                "BEFORE UPDATE ON authority_audit "
                "BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;"
            )
            self._conn.execute(
                "CREATE TRIGGER authority_audit_no_delete "
                "BEFORE DELETE ON authority_audit "
                "BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;"
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _retrofit_authority_policy_activation_trigger_if_needed(self) -> None:
        """Create or retrofit the activation validator without per-open DDL.

        The full normalized stored definition is compared with the canonical
        statement, rather than checking one repaired predicate, so any absent,
        legacy, or otherwise stale trigger is rebuilt exactly once. A database
        that already carries the canonical trigger returns before DROP/CREATE,
        preserving ordinary-open WAL/SHM history.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger'"
            " AND name='authority_policy_activations_validate_insert'"
        ).fetchone()

        def normalized(sql: str) -> str:
            return " ".join(sql.strip().removesuffix(";").split())

        expected = normalized(_AUTHORITY_POLICY_ACTIVATIONS_VALIDATE_INSERT_SQL)
        if row is not None and normalized(row["sql"]) == expected:
            return
        try:
            self._conn.execute(
                "DROP TRIGGER IF EXISTS authority_policy_activations_validate_insert"
            )
            self._conn.execute(_AUTHORITY_POLICY_ACTIVATIONS_VALIDATE_INSERT_SQL)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _retrofit_authority_lifecycle_trigger_if_needed(self) -> None:
        """Idempotent forward retrofit of the lifecycle-guard trigger body.

        ``CREATE TRIGGER IF NOT EXISTS`` inside ``_create_authority_tables`` is
        a no-op on a database that already carries the trigger, so a database
        created at an earlier reviewed head (which embedded the weaker body
        that accepted ANY evaluation row) would keep the weak body forever.
        This retrofit drops and recreates the trigger ONLY when the stored
        body is the legacy one (detected by the missing disposition-mirroring
        condition); on every later open it is a no-op, so a database never
        carries per-boot DDL churn and the ``-wal``/``-shm`` history stays
        stable. The recreated body is the exact
        ``_AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL`` constant used by
        ``_create_authority_tables``, so the two surfaces cannot drift.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger'"
            " AND name='authority_candidates_lifecycle_guard'"
        ).fetchone()
        if row is not None and "e.disposition = NEW.disposition" in row["sql"]:
            return
        self._conn.execute(
            "DROP TRIGGER IF EXISTS authority_candidates_lifecycle_guard"
        )
        self._conn.execute(_AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL)
        self._conn.commit()

    def _migrate_session_token_usage_scope_columns(self) -> None:
        """Add scope columns and make task_id nullable for conversation usage."""
        columns = {
            row["name"]: row
            for row in self._conn.execute(
                "PRAGMA table_info(session_token_usage)"
            ).fetchall()
        }
        if columns.get("task_id") and columns["task_id"]["notnull"]:
            self._conn.execute(
                "ALTER TABLE session_token_usage RENAME TO session_token_usage_old"
            )
            self._conn.execute(
                """CREATE TABLE session_token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id    TEXT,
                    agent      TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    executor   TEXT NOT NULL,
                    model      TEXT,
                    input_tokens          INTEGER,
                    output_tokens         INTEGER,
                    cache_read_tokens     INTEGER,
                    cache_creation_tokens INTEGER,
                    reasoning_tokens      INTEGER,
                    usage_raw_json TEXT,
                    scope_type TEXT,
                    scope_id TEXT,
                    thread_id TEXT,
                    invocation_purpose TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (task_id, agent, session_id)
                )"""
            )
            self._conn.execute(
                """INSERT INTO session_token_usage
                   (id, task_id, agent, session_id, executor, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, reasoning_tokens, usage_raw_json,
                    scope_type, scope_id, created_at)
                   SELECT id, task_id, agent, session_id, executor, model,
                          input_tokens, output_tokens, cache_read_tokens,
                          cache_creation_tokens, reasoning_tokens, usage_raw_json,
                          'task', task_id, created_at
                     FROM session_token_usage_old"""
            )
            self._conn.execute("DROP TABLE session_token_usage_old")
            columns = {
                row["name"]: row
                for row in self._conn.execute(
                    "PRAGMA table_info(session_token_usage)"
                ).fetchall()
            }

        for name in (
            "scope_type",
            "scope_id",
            "thread_id",
            "invocation_purpose",
        ):
            if name not in columns:
                try:
                    self._conn.execute(
                        f"ALTER TABLE session_token_usage ADD COLUMN {name} TEXT"
                    )
                except sqlite3.OperationalError:
                    pass

        self._conn.execute(
            "UPDATE session_token_usage SET scope_type = 'task' "
            "WHERE scope_type IS NULL"
        )
        self._conn.execute(
            "UPDATE session_token_usage SET scope_id = task_id "
            "WHERE scope_id IS NULL AND task_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_task "
            "ON session_token_usage (task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_agent "
            "ON session_token_usage (agent, created_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_scope "
            "ON session_token_usage (scope_type, scope_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_thread "
            "ON session_token_usage (thread_id) WHERE thread_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_token_usage_scope_unique "
            "ON session_token_usage ("
            "COALESCE(scope_type, 'task'), COALESCE(scope_id, task_id), "
            "agent, session_id)"
        )
        self._conn.commit()

    def _migrate_thread_invocation_attribution_columns(self) -> None:
        """Add nullable invocation-time executor/model without rewriting rows."""
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(thread_invocations)"
            ).fetchall()
        }
        for name in ("executor", "model"):
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE thread_invocations ADD COLUMN {name} TEXT"
                )
        self._conn.commit()

    def _migrate_thread_invocation_reply_message_link(self) -> None:
        """Add the nullable reply-result link and its one-wake/one-message index."""
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(thread_invocations)"
            ).fetchall()
        }
        if "reply_message_seq" not in columns:
            self._conn.execute(
                "ALTER TABLE thread_invocations "
                "ADD COLUMN reply_message_seq INTEGER"
            )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_thread_invocations_reply_message "
            "ON thread_invocations(thread_id, reply_message_seq) "
            "WHERE reply_message_seq IS NOT NULL"
        )
        self._conn.commit()

    def _backfill_revisit_of_task_id(self) -> None:
        # Called from _create_tables during __init__, which is single-threaded
        # by construction (Database is instantiated once per daemon, before
        # any worker threads start). Accessing self._conn directly without
        # @_synchronized is therefore safe here; do not call from elsewhere.
        cursor = self._conn.execute(
            "SELECT task_id, payload FROM audit_log WHERE action = 'revisit_of'"
        )
        for row in cursor.fetchall():
            if not row["payload"]:
                continue
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            predecessor_root = payload.get("predecessor_root")
            if not predecessor_root:
                continue
            self._conn.execute(
                "UPDATE tasks SET revisit_of_task_id = ? "
                "WHERE id = ? AND revisit_of_task_id IS NULL",
                (predecessor_root, row["task_id"]),
            )
        self._conn.commit()
