# THR-055 B1 Proof Matrix / Spec Parity — Revision R5 (TASK-4573)
Frozen: 2026-08-06. Maps every requirement to its executable test/proof and the exact durable data asserted.

## Requirement 1: Canonical injection/materialization

| Field | Detail |
|---|---|
| **Contract** | `create-skill` registered as 7th `SYSTEM_CONTRACTS` entry (TASK context, requires_repo=True) |
| **Production seam** | `runtime/skills/system_contracts.py` — `SYSTEM_CONTRACTS` tuple + `resolve_system_contracts_for_session` |
| **Materialization** | `runtime/orchestrator/workspace_adapters.py` — `_materialize_unified_canonical` → `_materialize_context_union` canonical store + symlink pipeline |
| **Materialization test** | `tests/test_system_contract_materialization.py` — all 80 tests pass; `create-skill` is created in protocol/skills/ alongside other contracts |
| **Context union test** | `tests/test_system_contract_materialization.py::TestCrossContextSystemContractRetention::test_task_thread_task_preserves_start_task_across_both_roots` — asserts `create-skill` survives across context switches |
| **Invalid context no-op** | `tests/test_system_contract_materialization.py::TestUnknownContextNoOp::test_unknown_context_preserves_existing_valid_state` — unknown context does not mutate workspace |
| **Dual-root proof** | `tests/test_system_contract_materialization.py::TestCrossContextSystemContractRetention` — all 4 cross-context tests verify `.claude/skills/` AND `.agents/skills/` |
| **System contract count** | `tests/test_system_contracts.py::test_system_contracts_are_7` — asserts `len(SYSTEM_CONTRACTS) == 7` |
| **CLI display** | `tests/test_skills_cli.py::TestSystemContractsCliDisplay::test_effective_shows_system_contracts_section` — asserts "Total: 7 contract(s)" |
| **Fixture count** | All 11 test files with hardcoded 6-contract lists updated to include `create-skill` |

## Requirement 2: Protected namespace

| Field | Detail |
|---|---|
| **Contract** | Route and lifecycle share one canonical `protected_slugs` frozenset built from `SYSTEM_CONTRACTS` + release registry |
| **Production seam** | `runtime/daemon/routes/skills.py:1686-1696` — builds live `protected_slugs` set from release catalog + system contracts |
| **Service pass** | `protected_slugs` passed to `SkillLifecycleService.submit_proposal(protected_slugs=protected_slugs)` |
| **System slug rejection** | `tests/daemon/test_skills_create_routes.py::test_protected_slug_rejected` — rejects `create-skill`, `todos`, and `start-task` slugs with 409 |
| **Release slug rejection** | Covered by same test — release-managed slugs also rejected |

## Requirement 3: Provenance and deterministic validation

| Field | Detail |
|---|---|
| **Contract** | Derive org, agent, task, session from SessionTracker; derive task_brief_digest from active task binding; validate before durable write; persist provenance in lifecycle event |
| **Production seam** | `runtime/daemon/routes/skills.py:1687-1690` — derives `task_brief_digest` from `org.db.get_task(task_id).brief` |
| **Validation** | Route calls `_validate_skill_package()` BEFORE `submit_proposal()` |
| **Durable record** | Lifecycle event of type `"provenance_recorded"` stored with metadata: `verified_org_slug`, `task_brief_digest`, `validation_ok`, `validation_reason_codes`, `validator_version: "B1-create-skill-route-v1"` |
| **Response provenance** | `tests/daemon/test_skills_create_routes.py::test_create_skill_success` — response includes `provenance.verified_org_slug`, `provenance.task_brief_digest`, `provenance.validation` |
| **No schema migration** | Provenance stored in `LifecycleEvent.metadata` dict, not as new DB columns — no migration required |

## Requirement 4: Concurrency

| Field | Detail |
|---|---|
| **Contract** | Deterministic genuinely overlapping requests through SessionTracker barriers proving clear-before-persist and replacement/stale-session ordering |
| **Barriers** | Existing `SessionTracker._pre_lease_barrier` and `._proposal_barrier` test seams wired into route |
| **Clear-before-persist** | `TestCreateSkillConcurrency.test_terminal_clear_wins_before_durable_commit` — Thread A pauses at pre_lease barrier; Thread B clears session; Thread A resumes → session_not_current, zero residue |
| **Replacement test** | `TestCreateSkillConcurrency.test_clear_before_persist_zero_residue` — Thread A at proposal_barrier (inside lease); released → persists (201); clear succeeds; second POST with old session → 403, zero residue |
| **Valid binding** | `TestCreateSkillConcurrency.test_valid_binding_persists_real_package` — Thread A reaches proposal_barrier; released → 201 with provenance |
| **Loser zero residue** | `TestCreateSkillConcurrency.test_concurrent_loser_zero_residue_all_surfaces` — 403 loser leaves no artifact, package, event, or session residue |

## Requirement 5: CLI-to-daemon transport

| Field | Detail |
|---|---|
| **Contract** | Production-real `happyranch skills create --from-file <path> --session-id <id> --org alpha` invokes `cmd_skills_create` through real httpx construction captured by forwarding ASGI adapter |
| **Success test** | `TestSkillsCreateCliIntegration.test_cli_create_success_through_forwarding_adapter` — captures method=POST, path=`/api/v1/orgs/alpha/skills/agent`, query `session_id`, body JSON, token-free headers, base URL, X-HappyRanch-Surface header |
| **Malformed JSON** | `test_cli_create_malformed_package` — malformed JSON → exit 1 |
| **Missing session** | `test_cli_create_missing_session_id` — no `--session-id` → exit 1 |
| **Missing file** | `test_cli_create_missing_from_file` — no `--from-file` → exit 1 |
| **Body identity rejected** | `test_cli_create_body_identity_rejected_client_side` — `task_id` in body → client-side rejection, exit 1 |

## Requirement 6: Documentation parity

| Field | Detail |
|---|---|
| **SKILL.md** | Updated validation section to reflect actual B1 behavior (deterministic validation runs before persist, findings recorded in lifecycle event). Updated "What happens after" to state B1 creates new proposals only; updates deferred to B2 |
| **05b-agent-runtime.md** | "replaces the proposal-review ceremony" → "is an additional agent-authoring path alongside the existing propose workflow; both create PROPOSED lifecycle records" |
| **05c-orchestrator.md** | Same fix as 05b |

## Test evidence

| Suite | Command | Result |
|---|---|---|
| Create skill routes + concurrency | `pytest tests/daemon/test_skills_create_routes.py` | 28 passed |
| System contracts | `pytest tests/test_system_contracts.py` | passed |
| OpenAPI snapshot | `pytest tests/contract/test_openapi_snapshot.py` | 4 passed |
| CLI tests (all) | `pytest tests/test_skills_cli.py` | passed |
| Materialization | `pytest tests/test_system_contract_materialization.py tests/test_skills_session_index.py` | 80 passed |
| Production paths | `pytest tests/daemon/test_system_contract_production_paths.py` | 5 passed |
| Full suite | `pytest tests/` | 1788 passed, 1 pre-existing failure (unrelated) |

## Files changed

| File | Change |
|---|---|
| `protocol/05b-agent-runtime.md` | Fix B1 replacement claim |
| `protocol/05c-orchestrator.md` | Fix B1 replacement claim |
| `protocol/skills/create-skill/SKILL.md` | Align validation/provenance claims with implementation |
| `runtime/daemon/routes/skills.py` | F2: pass protected_slugs. F3: add task_brief_digest, validation, provenance event. F4: add barrier test seams |
| `tests/daemon/test_skills_create_routes.py` | F4: barrier-driven concurrency tests (5 tests). F2: protected slug assertion updates |
| `tests/daemon/test_system_contract_production_paths.py` | F1: add create-skill to seeded contracts |
| `tests/test_skills_cli.py` | F5: 5 CLI integration tests |
| `tests/test_system_contract_materialization.py` | F1: add create-skill to all seeded lists |
| `tests/test_canonical_production_bound.py` | F1: add create-skill |
| `tests/test_canonical_skill_store.py` | F1: add create-skill |
| `tests/test_orchestrator.py` | F1: add create-skill |
| `tests/test_orchestrator_current_time.py` | F1: add create-skill |
| `tests/test_prelaunch_integrity_validation.py` | F1: add create-skill |
| `tests/test_skill_cutover_completeness.py` | F1: add create-skill |
| `tests/test_skills_session_index.py` | F1: add create-skill |
| `tests/test_thr070_skill_freshness.py` | F1: add create-skill |
| `tests/test_thr103_repo_refresh.py` | F1: add create-skill |
| `tests/test_workspace_adapters.py` | F1: add create-skill |
