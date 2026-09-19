# Team escalation policy operator contract

The web surface is currently available only for the roster-confirmed
`engineering/engineering_manager/manager` tuple. The server is authoritative;
workers and every other manager receive the same `policy_surface_not_available`
404 and their Agent response and DOM contain no policy surface.

The eligible Agent detail contains a compact manager-only entry and active-status
card. The full editor, immutable history, and self-evaluation outcomes live on
the dedicated `/orgs/:slug/agents/:agent_name/team-escalation-policy` route.

`GET .../team-escalation-policy/history` and `/outcomes` accept an opaque
server cursor and `1 <= limit <= 50`. The first request omits `cursor`; its
response cursor binds every later page to that initial snapshot and to a
deterministic newest-first keyset. Rows inserted after page one therefore do
not shift, duplicate, or hide rows in the in-progress traversal.
The UI keeps independent cursors for the two lists and exposes keyboard-native
Load more controls, explicit loading/error/empty/end states, and lossless
append of each server page. A later-page failure preserves every loaded row and
leaves an independent keyboard-native retry for that stream's failed cursor;
retry appends the page once without resetting or duplicating earlier rows.
History omits policy prose and prompts. Outcomes show only durable identity
pins and causal task/result/session/thread/hook/envelope receipts. Missing or
corrupt joins are `receipt_incomplete`, never inferred success. Raw evaluator
responses, rationale, proposed reason, prompts, policy content, credentials,
and secrets are never projected.

Save creates an immutable inactive release. Save & activate remains an explicit
founder-authorized action. Every selection is fenced by the authenticated team
selector CAS carried as the client's observed `expected_selector_id`; a missing
base is invalid and explicit `null` means only the observed *initialized empty*
selector (family `empty`, epoch 0) — never "today's live selector", and never a
legacy family epoch promoted into selector authority. The serialized,
idempotent selector initializer observes authenticated legacy history or a
genuinely empty store and is used as a compatible reader/writer backstop; it
never activates policy, repairs corrupt history or fabricates a base. A first
legacy activation from the empty selector becomes the legacy family
`bootstrap`; later legacy selections allocate their own family epoch, and a
v2 -> v1 rollback re-selects an authenticated prior legacy activation without
rewriting its original bytes. A rejected or stale CAS leaves the saved release
inactive and requires a refresh. All mutations truthfully attribute only the
`shared local operator credential`.

Selector-aware control API (B2b1 backend; the web editor mirrors it in B2b2):

- `GET .../team-escalation-policy` returns the discriminated `family`
  (`empty`/`legacy_v1`/`v2`), the observed `selector_id`/`selector_epoch` and,
  when selected, the active release/activation for that family. Empty is an
  explicit `bootstrap_required` projection; a selected v2 family never displays
  the latest legacy activation as active.
- `POST .../team-escalation-policy/releases` (legacy, unchanged path) now
  requires `expected_selector_id`; `action=activate` selects a new legacy
  release and `action=reactivate_rollback` re-selects an authenticated prior
  legacy activation resolved from the named release. The historical
  `expected_previous_epoch` field is retained for wire compatibility only and is
  no longer the selection authority.
- `POST .../team-escalation-policy/v2/releases` is the strict paired
  save+activate: one immutable dual-text release and its single selection, both
  control audits and the receipt commit in ONE transaction. There is no
  independent per-text save and no draft-only v2 creation.
- `POST .../team-escalation-policy/v2/activations` selects or rolls back an
  already-saved v2 release under the same selector CAS and transaction owner.
- `GET .../team-escalation-policy/v2/history` returns bounded, family-specific,
  newest-first v2 release receipts under a stable opaque cursor.

The v2 control routes decode the raw HTTP body before any JSON/Pydantic
normalization, rejecting duplicate members, invalid UTF-8/BOM, non-object
roots, `NaN`/`Infinity`, wrong scalar types, unknown fields and malformed
bounds. A family-specific history cursor is never accepted by the other
family's stream; a malformed cursor and a corrupt store are distinct sanitized
refusals (`invalid_cursor` versus `policy_store_unavailable`).

Landing this code, redeploying it, and creating/activating a production policy
are distinct events. This delivery does
not create or activate a production policy and does not deploy anything.
