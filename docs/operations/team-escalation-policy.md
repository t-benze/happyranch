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

Daemon startup observes and initializes the eligible Engineering selector
through that same transaction-owning initializer BEFORE any startup
recovery/enqueue and before the API becomes available. Initialization is
observation of authentic legacy history or a genuinely empty store — never an
activation. A missing, corrupt or unselected history, or an initializer-audit
failure, refuses the launch with no partial initializer instead of manufacturing
empty state or silently selecting legacy. Repeat/reopen reuses the same
deterministic initializer with no duplicate rows, an already-selected v2 family
stays v2, and an ineligible roster initializes no unrelated team. Dynamic launch,
immutable launch binding, strict completion admission, automatic continuation,
authenticated tagged generation admission, next-result spend, and startup/reaper
recovery are implemented in the PR878 candidate. They consume the exact selected
and session-pinned identity; startup initialization itself still grants none of
that authority.

The B2b2 client decodes the current projection and both history streams as
discriminated `empty`/`legacy_v1`/`v2` types, rejecting malformed, mixed,
unsupported or selector-less projections rather than casting v2 into the legacy
clause shape or falling back to the latest legacy. The observed `selector_id` is
threaded from the read/draft base through the provider and every legacy
activation/rollback call site; the wire never refetches or substitutes today's
selector, infers it from a legacy family epoch, or auto-retries a conflict with a
fresh base. v2 paired/activation requests mirror the backend fields, carry the
two texts together with distinct stable create/activation IDs and
`based_on_selector_id`/`expected_selector_id`, and are not trimmed or rewritten
in transport. Real/mock hooks exist for the v2 control and v2 history, and a
successful v2 control invalidates the current projection plus the v2 history
stream while each family keeps its own pagination. The selector epoch stays
distinct from the legacy family epoch. A selected v2 family renders the complete
paired editor and never fabricates a v1 draft or v1 mutation. The current
candidate also projects authenticated v2 history; legacy outcomes remain
scoped to an authenticated legacy-family projection. Mixed, corrupt,
unsupported, or ineligible targets fail closed without fallback.

Selector-aware control API and paired editor:

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
not create or activate a production policy and does not deploy anything. Before
any future activation, deploy compatible code to every manager launch and
completion consumer and drain all old consumers. Source merge is not rollout;
rollback is compatible-code-only once v2 state may have been persisted.
