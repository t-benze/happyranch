# Retire protocol documents and relocate runtime skills

Status: implementation completed in `refactor/protocol-retirement` following user authorization; full CI and release verification remain pending. See `2026-09-08-protocol-migration-receipt.md`.
Baseline: repository HEAD `01d5ede5`, reviewed 2026-09-08.

## Outcome and authority

Remove the top-level `protocol/` directory after migrating its active consumers.
Required agent behavior is owned by runtime implementation: deterministic gates
where possible, and explicit runtime-owned evaluation/review workflows where
semantic judgment is required. Evaluator output remains fallible evidence.
Documents explain behavior; a Markdown instruction is not proof of enforcement.

The final organization is:

- `runtime/`: validation, authorization, orchestration, prompt construction, and
  runtime-owned behavior evaluation.
- `runtime/skills/bundled/<slug>/`: the ten current bundled skill packages and
  their supporting assets. These are versioned runtime instructions delivered
  through the existing canonical store, not a second policy authority.
- `docs/agent-guides/`: concise explanations of current implementation, organized
  by the existing six surfaces. No replacement protocol/specification collection.
- Tests and the existing OpenAPI snapshot: executable contracts and regressions.
- Existing historical specs and Git history: past designs and decisions.

Do not invent new restrictions to make old prose true. Org-specific examples
(spending limits, refund authority, team names, persistent Support Agent sessions)
are not automatically kernel requirements. Do not move thousands of lines into
the guides unchanged. No production activation, live cleanup, data migration,
or weakening of authorization is implied by this cleanup.

## Required inventory before deletion

Create a temporary migration table with one row per distinct behavioral
requirement, not one row per paragraph. Record:

`source location | requirement | current producer/validator/consumer | test evidence | disposition | owner/PR`

Allowed dispositions:

1. **Enforced:** point to the actual boundary and meaningful test. Remove the
   redundant normative prose; retain only useful explanation.
2. **Required but unenforced:** name a concrete implementation gap and its
   negative acceptance case. Resolve it in a separate behavior PR before deleting
   the last operational instruction for that requirement.
3. **Advisory:** explicitly optional guidance. Keep only where useful in a runtime
   skill, or in an existing guide for developers. It is not an acceptance gate.
4. **Obsolete:** delete, including abandoned APIs, states, and design suggestions.

Never silently downgrade an intended requirement to advisory. Unclear policy is
a bounded decision item with an explicit recommendation, not a reason to block
unrelated migrations. The table is a migration receipt, not a new standing spec.

## File disposition

| Current file | Final treatment |
| --- | --- |
| `05-runtime-blueprint.md` | Delete. Use the existing project-layout guide and CLAUDE navigation. Reconcile its authority-policy summary against current code. |
| `05e-dashboard.md` | Delete after extracting still-useful cache/lock explanations into the web guide. Drop obsolete mockups, LAN/read-only claims, and invented API paths. Git retains history. |
| `05b-agent-runtime.md` | Delete after mapping its requirements. Put necessary explanations in executor/configuration/features guides; behavior and launch contracts remain in implementation/tests. |
| `05c-orchestrator.md` | Delete after mapping requirements. Consolidate current state, authority, recovery, and skill-delivery explanations into the corresponding existing guides. |
| `00-completion-contract.md` | Delete after establishing code-owned completion requirements and runtime-supplied usage guidance. Reuse actual HTTP/CLI/internal models rather than creating a second schema. |
| `06-knowledge-base.md` | Delete after separating KB admission rules from optional editorial advice. Code owns checks; useful advice is delivered in the existing task skill. |
| `skills/*` | Relocate to `runtime/skills/bundled/*` through an independent migration. Preserve slugs, context eligibility, and provider-root delivery. |

## Delivery sequence

### PR 1 — inventory and retire the redundant navigation/design documents

- Complete the dependency map and requirement inventory, starting with the known
  review findings. Check all six Markdown documents and all ten skill packages.
- Retire the blueprint and dashboard document after reconciling their unique
  current content. Update `CLAUDE.md`, relevant guides, current implementation
  references, packaging, and the system-assistant knowledge inventory.
- Replace the blueprint-dependent source-root marker in `runtime/system_assistant.py`
  with a stable existing package/root marker. Test source-checkout and installed
  package discovery; deleting a document must not disable discovery.
- Correct the removed scripts/SR-NNN workflow in `start-task` to the existing
  reviewed-jobs/JOB-NNN mechanism. Publish this skill change normally; do not
  edit canonical package bytes in place.
- Preserve append-only historical specs. Update their index's current-source
  guidance; historical links may remain explicitly historical.

Acceptance: active navigation contains no deleted-file references; assistant
knowledge discovery works; the task skill gives an executable reviewed-job path.

### PR 2 — resolve required behavior gaps, in bounded domain PRs

This stage is driven by the inventory, not a promise to enforce every old sentence.
For each confirmed gap, specify the trigger, authoritative evidence, rejection or
review outcome, and retry/recovery behavior before implementation.

Initial domains to audit:

- Completion: actual wire payload versus internal `CompletionReport`, session
  binding, required evidence, owner/subtask decisions, verdict consumption, job
  waits, and when a task may legitimately become terminal. In particular, assess
  how a pushed-PR evidence requirement can be established from recorded facts;
  a nonempty self-reported `local_ci` object alone is not verification.
- KB: input validation, duplicates, mutation authorization, and editorial advice.
  Do not add an LLM content gate merely because the old guideline recommends
  long-lived knowledge. If semantic admission is required, design its review
  lifecycle, failure policy, and evaluation evidence explicitly.
- Permissions and escalation: remove nonexistent role-based blocking and
  `waiting_for_approval` examples; identify actual enforced boundaries without
  claiming comprehensive detection of external side effects.
- Other load-bearing contracts in 05b/05c: match canonical skill integrity,
  thread delivery/resume/breaker behavior, authority, resource containment,
  cleanup dormancy, remote-access limits, and remote-job dormancy to code/tests.

Existing authorization, schema, compatibility, and load-bearing review gates
apply to behavior changes. Such changes get separate reviews; a documentation
cleanup is not authorization to alter protected runtime policy.

Acceptance: each required rule has a concrete implementation and behavior test,
or an explicit unresolved decision that keeps its deletion out of the next PR.
The absence of gaps is a valid result; do not manufacture implementation work.

### PR 3 — retire remaining protocol documents and their prompt discovery

- Supply concise completion/KB usage guidance through runtime-owned prompts and
  existing skills. Keep deterministic field constraints in the actual request
  models; generate schema references from those models where needed. Guidance
  may remain Markdown runtime assets, but cannot substitute for a required gate.
- Remove `resolve_protocol_doc_manifest` and its plumbing from task, fresh and
  resumed/fallback thread, wake, dream, and schedule producers. Preserve unrelated
  skill indexes, active-policy sections, and transcript/continuation semantics.
- Do not replace the protocol manifest by injecting every developer guide into
  ordinary org-agent prompts. The system assistant may retain the existing guide
  inventory for operator/developer help.
- Remove the four remaining documents, their wheel includes, system-assistant
  knowledge entries, and active links. Update capabilities/bootstrap pointers.
- Replace tests that only assert old prose with behavior tests where the assertion
  represented a real guarantee. Retain useful prompt/discovery tests against the
  new runtime assets; do not delete test coverage merely to make deletion pass.

Acceptance: no launch depends on a protocol Markdown document. Valid callbacks
and invalid-session/job-wait cases retain their intended outcomes. The existing
guides describe one coherent current behavior without historical amendments.

### PR 4 — relocate skill source packages and remove the folder

- Move all ten packages, including `make-worktree/worktree_guard.py`, into
  `runtime/skills/bundled/`. Keep skill bodies unchanged in this PR except for
  necessary source/reference paths; preserve the guard's existing consistency test.
- Introduce one package-aware source resolver. Update
  `runtime/skills/system_contracts.py`, workspace materialization, managed release
  source consumers discovered by the inventory, and assistant skill discovery.
  Test source-tree and installed-wheel execution without a checkout present.
- Replace hard-coded thread/jobs skill references with the delivered skill
  location or index. Guidance must identify both `.claude/skills` and
  `.agents/skills` where provider roots are named.
- Audit `Settings.protocol_dir`, `get_protocol_dir()`, environment/YAML overrides,
  the settings API/OpenAPI field, fixtures, and release/deployment packaging.
  Inventory configured overrides before changing the setting. Migrate known
  overrides explicitly; unknown/non-default overrides must produce a clear
  migration refusal rather than silently selecting a different source. Any
  temporary compatibility adapter needs a removal condition and must not restore
  wholesale copy, automatic byte repair, or a lenient integrity fallback.
- Ship bundled skill files explicitly in the wheel; do not assume Python package
  discovery includes all Markdown/support assets. Existing assistant knowledge
  copies must not become an alternate launch source.
- Preserve canonical package identity/provenance semantics. Determine whether
  changed source paths affect manifests/hashes; publish resulting versions through
  the normal release process. Preserve valid older packages and evidence. Never
  rewrite ledger hashes or heal a mismatched package from local source.

Acceptance: `protocol/` is absent from the checkout and release artifact. All
session contexts plus executor-switch/bootstrap still resolve the same eligible
skill set, with both-root link validation and launch-time integrity refusal intact.

### Release verification — separate from merge

- Build and install a wheel in an isolated location; verify assistant discovery,
  bundled skills/support assets, and representative prompt construction without
  repository-path fallback.
- Run focused suites for completion/KB, prompt producers, assistant packaging,
  system contracts, canonical materialization/integrity, and the worktree guard.
  Add missing deployment-shape coverage only where current tests do not prove it.
- Run required integration checks for changed launch/callback behavior, then the
  normal complete CI gates. Long/full verification follows the existing durable
  jobs contract; do not claim passing results without receipts.
- Deploy through the ordinary release process and verify the serving version,
  new asset locations, real skill links, and representative callback behavior.
  Do not activate policies or run cleanup as deployment smoke tests.
- Rollback uses the prior complete release and its matching assets/configuration.
  Preserve historical canonical packages; never improvise rollback by editing
  hashes, links, or corrupt package bytes. Document configuration rollback if the
  old release still expects a protocol-directory override.

## Completion criteria

- No active production, packaging, prompt, or navigation reference requires the
  removed directory. Remaining references are explicitly historical or bounded
  compatibility tests/diagnostics, each classified by the migration inventory.
- No required behavior is backed solely by a deleted sentence or a relocated
  guide. Runtime instructions explain how to satisfy implemented contracts.
- Existing skill integrity, scope, permissions, dormant-feature boundaries, and
  session semantics are preserved unless changed by a separately reviewed PR.
- Source and installed-release checks pass; deployment verification is recorded
  separately. The temporary inventory records the disposition of every rule.

Plan-file change radius: this file only; no executable symbols, callers, config
consumers, or production behavior change. Risk: low, documentation-only.
