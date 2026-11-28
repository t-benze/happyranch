# Create team and Create agent

| Field | Value |
| --- | --- |
| Status | canonical behavior contract; original feature and authorized v1.1 UX rework merged; scoped acceptance and evidence limits recorded below |
| Owner | Product Lead; Engineering Manager owns delivery; QA and Product own acceptance |
| Date | 2026-11-29 (v1.1 delivery reconciled; A1–A10 behavior contract unchanged) |
| Source Links | Founder THR-402 seq5/16/33; Engineering seq14–15/32/37; Product seq39; THR-296 seq316, 402–409; THR-387 seq46/61; [v1.1 delivery PR1935](https://github.com/t-benze/happyranch/pull/1935); [accepted wording handoff](https://github.com/t-benze/happyranch/pull/1916#issuecomment-7242490080); [original source baseline](https://github.com/t-benze/happyranch/tree/bda86a149220e0b569826289a55511055e58b9ac); [2026-11-27 source check](https://github.com/t-benze/happyranch/tree/025cc2ee199060edd20d092b7f9ed4f67d609727); [historical May 30 design](https://github.com/t-benze/happyranch/blob/65107878735d082265f039456f0c9e062430dd84/docs/superpowers/specs/2026-05-30-add-org-and-agent-from-web-ui-design.md) |
| Commitment Boundary | records the authorized feature and UX delivery; no roadmap, date, deployment, installed-build or live-roster commitment |
| Founder Decisions | Required: none for this cutline. Ruled: retain Founder-managed Default; no agent movement across teams (THR-296 seq316); remote Client configuration remains read-only (THR-387 seq61). Arbitrary additional Founder-managed teams are outside scope. |

## Delivery evidence and authorized UX rework (reconciled 2026-11-29)

### Original feature delivery

Engineering reported delivery in THR-402 seq14–15: [spec PR1897](https://github.com/t-benze/happyranch/pull/1897)
merged as `0ba4b9ff`, [backend PR1901](https://github.com/t-benze/happyranch/pull/1901)
as `3c8e6bc1`, and [web PR1902](https://github.com/t-benze/happyranch/pull/1902)
as `368fe7fa`. Independent QA TASK-19445 passed the scoped real-daemon
UI → request → persisted team/agent campaign at reviewed head
`b61d084d769b64f4de23d39a64aac7826f939c77`, in EN/ZH and phone/desktop
viewports, with attributable A1–A6 and A8–A10 results. Its
`output/TASK-19445/qa-report.md` lists unexecuted subcases, including separate
stale-choice/reopen checks, naming-authority cases and a valid agent-session
credential. Row-level results do not prove every subcase was executed.

TASK-19453 subsequently passed A7 at the exact packaged Linux client boundary
from merged `368fe7fa`, with a counting HTTP peer, native-routed attempts and
zero forwarded configuration requests/bytes. A genuinely connected managed
remote Host remains unverified, dependent on THR-327; three additional native UI
captures were not obtained. See `output/TASK-19453/a7-report.md` through task
recall. These are identified test venues, not proof of the Founder's current
installed build or of good usability. No deployment claim follows.

### Authorized v1.1 UX delivery

Founder THR-402 seq16 requested UX consolidation after the original delivery.
Product TASK-19575 supplied a design handoff, followed by design reviews
TASK-19692 and TASK-19746. Those proposals/reviews did not themselves authorize
implementation. Engineering seq32 recommended building v1.1 with the four
remaining prototype corrections as N1–N4 acceptance checks; Founder seq33
explicitly authorized the build ("ok start the build").

Engineering TASK-19789 delivered [PR1935](https://github.com/t-benze/happyranch/pull/1935),
reviewed and QA'd at `d20ee84ddd40d05f0df4feed4d82aadfbcd75999`, merged on
2026-11-28 UTC as `5ff2b76746720cfa864d04ef049c81caa25d9a72`. This frontend
rework implements Team and role → Agent responsibilities → compact Runtime →
in-place review, dedicated result panels, and the presentation boundaries below.
It adds no API, schema, backend, native enforcement or creation-owner capability.
The [manual](../../manual/pages/02-core-concepts/02-agents-and-teams.md) and
[web/CLI guide](../../agent-guides/web-and-cli.md) describe the implemented surface.

Independent review TASK-19801 (`output/TASK-19801/review.md`) approved the final
head after TASK-19797's three findings were fixed: outcome-based team completion
even through Create agent → Manager; an explanation plus GET-only Refresh after
a failed team-list read; and computer labels translated at render without
changing original custody. Engineering reported required checks green and
guarded merge in THR-402 seq37.

Independent QA TASK-19803 executed 81 distinct real scratch-daemon browser cases
in EN/zh-CN at 1440×900 and 390×844, including keyboard journeys. Its initial
BLOCK concerned the unavailable packaged A7 venue. TASK-19807 freshly rebound
that same-head evidence and returned a scoped web PASS under Engineering's
explicit unchanged-path scope ruling; it did not rerun the successful campaign.
See `output/TASK-19807/final-qa-report.md`, `scope-ruling.json` and
`receipt-audit.json` through task recall. These are attributed Engineering/QA
records, not new Product execution.

| v1.1 check | Reported web evidence at the reviewed head |
| --- | --- |
| N1 | Editing a refused field, including Use suggestion, clears that value's stale refusal/banner; a corrected deliberate submit succeeds. |
| N2 | Four WebKitGTK native-facade cases: selecting the current computer closes the menu and restores drawer-trigger focus, with no guard or Create. This is presentation evidence. |
| N3 | Executor loading/read failure/confirmed-none has a localized disabled-Create reason; forced pointer/keyboard attempts do not submit; Retry/Recheck only reads. |
| N4 | Locale changes retain disclosures, focus/caret, values, choices and the original owner, with no locale-triggered request; Edit reveals a disclosure before focusing within it. |

QA's A1–A10 table covers the exercised web observations, with subsets and
exclusions stated per row. **Packaged-client remote zero-forward, direct-native
refusal and real target-switch custody were NOT RUN for v1.1.** The older
TASK-19453 A7 proof above remains attributed to its original packaged build;
it is not renewed by this rework. A genuinely connected Host remains outside
that proof. Screen-reader and actual Safari/Firefox checks were not run; the
WebKitGTK facade is not Safari proof. Valid scoped-agent enrollment/approval,
the full unchanged name-collision, stale-choice and Default reconciliation
matrices, and packaged target states were also not rerun. The initial BLOCK
and its evidence remain historical; the scoped PASS does not weaken A7.

No current installed/deployed identity, new packaged acceptance or user-research
usability result is established. The original source/gap inventory below remains
historical. Product accepts the five presentation clarifications in THR-402
seq39; A1–A10 and the settled capability/authority boundaries remain unchanged.

## Problem, goal and cutline

The original discoverability problem was a hidden creation path:
Agents → New agent → Manager → New team name. Provide two explicit actions on
the existing Agents page using one shared creation form. Complete the existing
manager-plus-team and worker-in-existing-team journeys, including truthful
failure recovery. A new team is useful immediately with its manager; no separate
empty agent-led team or Teams navigation section is needed.

This document is the canonical Product acceptance contract for these journeys.
The May 30 design is historical provenance, not deployed proof. It is superseded
here for creation UX; current [team contracts](../../agent-guides/contracts/teams-and-org-admin.md),
[naming contracts](../../agent-guides/contracts/threads-and-naming.md) and
[connection policy](../../agent-guides/web-and-cli.md#remote-read-only-configuration-thr-387-s8-2b)
continue to own their respective invariants. THR-402 seq2–4 contains incomplete
inventory, not a finding that either deployed journey works.

## Users and workflow

The actor is the human Founder administering the selected org on the selected
local runtime. Show both actions in Agents, including its empty state.

1. **Create team:** open the shared form titled “Create team”; explain “Creates
   a new team and its manager.” Require a new team name and the new manager's
   fields below. Role is fixed to Manager. Submit one manager enrollment with
   `new_team`. On confirmed success open the returned manager's canonical detail
   with a completion panel, show the created team, and offer **Create agent in
   the new team** with that team preselected. Completion follows the submitted
   manager/worker outcome, including Manager entered through Create agent.
2. **Create agent:** open the same form titled “Create agent”, default Worker.
   Select an existing team explicitly; when entered from a team context, preselect
   only that still-existing team. The Manager choice says “Creates a new team”
   and switches to the same manager-plus-team fields. Returning to Worker does
   not submit a hidden team-creation field. No implicit first-team selection.
3. **No teams / unavailable data:** distinguish loading, failed read with Retry,
   and a confirmed empty roster. An empty worker chooser offers Create team.
   Never interpret a failed read as zero teams or invent an executor/team.

The v1.1 surface shows the organization by its short code (slug), not its display
name. In the desktop app, the drawer-owned Connection menu lists **This
computer** and the currently connected remote computer, when present; it does
not read a saved-computer list. Selecting the current computer only closes the
menu and returns focus to its drawer trigger. Another computer runs the existing
owner gate, then hands off to the existing Connection dialog and commit path.
Pending/unknown ownership still holds changes; there is no one-click switching
capability added here. The Connection menu is absent in browser mode.

An agent-led team has exactly one manager and zero or more workers. A new
manager creates a new team; it cannot join or replace the manager of an existing
team. A new worker joins exactly one existing team, including the human-managed
Default. Existing agents cannot be selected, moved, copied or promoted here.
Team selection grants no manager authority to workers.

Default is the special Founder-managed team, with no manager agent. New-org
creation provisions it; older orgs may lack it. Existing supported local operator
reconciliation is `happyranch teams ensure-default --org ORG`, followed by a teams
refresh. This is not a generic create-team endpoint or a required first step for
manager creation. HTTP 202 / `authority.ready=false` means provisioning remains
incomplete; repeating the command does not repair authority. Preserve existing
workers and routing pointers. Do not add a second Default or silently change task
routing. A task in a human-managed team still needs an explicit executable owner.

## Fields and defaults

| Field | Required behavior |
| --- | --- |
| Agent name (manager name in Create team) | Required; 1–64 ASCII characters, starts with a letter, then letters/digits/`_`/`-`. Submit `display_name`, never manufacture a permanent ID. Preserve case for display; the naming service owns case-insensitive availability, reserved/former names and canonical-ID generation. |
| Role | Worker by default in Create agent; Manager fixed in Create team. Explain the different team outcome before submit. |
| Existing team | Required for Worker; current authorized roster includes agent- and Founder-managed teams. Refresh if removed/stale; retain other input. |
| New team name | Required for Manager. Product's new-input rule: 1–64 lowercase ASCII characters, starts with a letter, then digits/letters/`_`; no whitespace. Do not rewrite existing team keys. Reject duplicate keys and reserve `default` for Founder-managed Default. Client and server must agree. |
| Team suggestion | Retain the current suggestion from manager name: lowercase, replace hyphens with underscores, remove the last underscore suffix. Label it editable; stop tracking after manual edit. Reopening or changing role must not silently overwrite an edited team name. |
| Executor | Required selection from live selectable registered profiles; initial choice is the first available profile, visibly selected. Loading/error/no registered executor disables submit with Retry or local Executor settings recovery. Never silently change a user-selected executor that becomes unavailable. |
| Description; system prompt | Both required after whitespace check; blank defaults. Preserve authored text. No generated responsibilities, permissions or repository bindings. |

Keep current backend defaults for omitted model/reasoning effort; creation adds no
model, repository or allow-rule editor. Backend validation remains authoritative
at submit, including naming availability and executor validity. New team-input
validation was implemented by PR1901; see the delivery evidence above.

## Authorization, connection and state handling

Reuse existing Founder-authorized local enrollment and current native/API
boundaries. Founder-created agents become active directly; do not add another
approval step. Agent-initiated pending enrollment/approval remains separate.
No new auth, credential, permission, schema or compatibility policy is granted.

On a committed remote Host, keep creation discoverable and explain “Change this
on {Host}”; existing authorized reads stay visible or explain unavailability.
Disable configuration submit and enforce the existing native pre-forward denial
for `POST /agents`. This is a configuration restriction, not a claim that all
remote operations are read-only. Preserve a draft after `remote_read_only`;
never route it to This computer or another org automatically. Missing target
and incompatible runtime use their existing named recovery, not a false empty
roster. Design v1.1 S4m ("creation authority not ready") has no implementation
signal and is excluded from this delivered UX until one exists. This exclusion
does not remove existing authority enforcement, actual authorization refusals,
unknown-result handling or Default provisioning's `authority.ready=false` state.
Services sign-in alone does not grant runtime administration.

| State | User-visible contract |
| --- | --- |
| Editing / cancel | Inline field errors and a reachable submit action. Cancel an unsent draft through existing owner/discard handling; successful completion resets the form for the next creation. |
| Submitting | One in-flight request per action, including rapid click/Enter. Freeze the submitted snapshot. No automatic mutation retry, including auth-refresh replay. Retain the original target/org and pending owner through close/navigation/reopen. Closing a pending or unknown create only hides the surface; the Agents top bar's held-action chip presents the existing `agents.create` owner record for this org and reopens the same owned result without replay or new storage. |
| Confirmed success | A successful enrollment receipt identifies the canonical agent; refresh agents, teams and identities. Navigate using that ID and show its completion panel; a team outcome offers Create agent in the new team. Failed roster/team readback keeps creation confirmed, explains the failure and offers GET-only Refresh of agents and teams. Keep follow-on unavailable until its team is present in the readback; never repeat the completed Create. |
| Known pre-effect refusal | For invalid/reserved name, duplicate team, unknown team or unavailable executor: explain the relevant field, retain other input and permit correction. Distinguish a native “not sent” refusal from an unknown sent result. |
| Unknown result / partial setup | Timeout, interrupted response or bootstrap failure may follow persisted enrollment. Say “Result unknown” or “Agent created; setup incomplete” only as supported by evidence. Keep submitted values and provide Check result against the original org/target. Never assert rollback, resubmit automatically, rename-and-retry or delete a possibly created team/agent. |
| Recovery | Fresh authorized identity/roster/detail reads may locate an existing agent; a matching name alone does not prove this request succeeded or setup completed. Offer View agent and the supported Engineering/setup recovery when readiness is unresolved. A new create is allowed only after the prior outcome is positively resolved. Pending/unknown owners block target/org changes or owner-discard transitions under the existing owner contract. |

"Not sent" uses only the transport's trustworthy typed pre-forward signals:
`NotSentError` for `target_changed`, `ThrottledError` for `too_many_requests`,
and the native 503 `NativeRecoveryError` reasons below. The implemented
`createAgentForm.notSentCopy` cause/next-step mapping is:

| Reason | Existing recovery |
| --- | --- |
| `target_changed` | Open Connection. |
| `too_many_requests` | Wait, then deliberately submit. |
| `busy` | Wait, then deliberately submit. |
| `no_target` | Open Connection. |
| `target_starting` | Recheck readiness (read only). |
| `target_unreachable` | Recheck readiness (read only); Open Connection is also available. |
| `incompatible` | Update the incompatible runtime, then recheck readiness (read only). |

There is no "runtime stopping" signal or cause in this delivered mapping.
An arbitrary HTTP status/code shape does not prove pre-forward refusal: raw
`409 target_changed` and interrupted forwarded changes remain unknown. The
exact typed pre-forward `RemoteReadOnlyError`
(`403 remote_read_only`) remains the distinct configuration-read-only refusal,
retaining the draft without replay. Retry/Recheck never creates an agent;
resubmission after a trustworthy not-sent refusal is always deliberate.

Ship affected app copy in English and Simplified Chinese. Labels, keyboard focus,
errors and actions must work at 390×844 and 1440×900; locale changes preserve draft,
selection, focus and the original action without sending requests.

## Original source inventory and implementation gaps (historical baseline)

Evidence below is source at `bda86a149`, before PR1901/PR1902, not an inventory of
current defects or shipment proof. It preserves the original Engineering gap
assignments against each acceptance ID; use the delivery evidence above for the
later disposition and remaining verification limits.

| IDs | Evidence / classification | Next step |
| --- | --- | --- |
| A1–A2 | [AgentsPage](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/web/src/features/agents/AgentsPage.tsx#L143) exposes only New agent; [shared form](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/web/src/features/agents/AddAgentDialog.tsx#L128) has both role branches. Discoverability gap. | Add explicit entries and shared mode; verify create-team → add-worker without reload. |
| A3–A4 | [Enrollment route](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/runtime/daemon/routes/agents.py#L1462) supports existing-team worker / new-team manager, required text and duplicate checks. Team name is checked only for presence/exact collision. | Add consistent new-team input validation; preserve membership and naming contracts. |
| A5 | [Default route](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/runtime/daemon/routes/teams.py#L20) is bounded provisioning, not standalone named teams. [PR1830](https://github.com/t-benze/happyranch/pull/1830) fixed memory/disk tag mismatch. | Retain fix/regression; test Default enrollment without restart. No new generic team API. |
| A6–A7 | [Remote policy](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/protocol/remote-client-config-policy.v1.json#L34) includes enrollment. Form handles read-only denial; this alone proves no packaged enforcement. | Verify local authority, remote zero-forward refusal and target ownership; reuse current Connection work. |
| A8–A10 | [Form submit](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/web/src/features/agents/AddAgentDialog.tsx#L152) guards pending and navigates on success, but has no creation-owner/unknown-result recovery; [bootstrap](https://github.com/t-benze/happyranch/blob/bda86a149220e0b569826289a55511055e58b9ac/runtime/daemon/routes/agents.py#L1611) can retain enrollment on failure. Completion gap with duplicate/false-failure risk. | Integrate existing owner machinery; prevent replay; distinguish rejected, created-incomplete and unknown. Verify both locales and failure recovery. |

## Acceptance and completion checklist

Each row remains pass/fail; all remain required for feature acceptance. QA records
actual environment, exact source/build, action, expected/observed result and
evidence. The delivery record above distinguishes original feature acceptance from v1.1's scoped web
non-regression and unexecuted subcases; a web-scope PASS is not a new packaged A7
pass or proof that every subcase ran.

| ID | Pass condition |
| --- | --- |
| A1 | From populated and empty Agents, Create team opens the manager-plus-team form; one valid submit yields exactly one new team, one manager and zero workers, then opens that manager's canonical detail. The detail shows a completion panel; Create team offers Create agent in the new team. |
| A2 | Create agent defaults Worker; explicit selection of an agent-led team or Default adds exactly one new worker to that team. Follow-on creation from A1 selects its team. Manager mode creates a new team and never replaces an existing manager. |
| A3 | Empty, whitespace, first-character, 64/65-character and invalid-character boundaries behave as specified. Case-insensitive agent-name collisions, reserved/former names, duplicate team and reserved Default fail before creation; corrected input succeeds. Existing nonconforming team keys remain selectable. |
| A4 | Team/executor loading, read failure, empty and stale-choice cases are distinguishable; Retry refreshes without losing other fields. No submit with invalid or unknown selections. While executors are loading, unreadable or confirmed none, Create is unavailable with a localized reason; Retry only reads. Manual team edits survive role changes/reopen. |
| A5 | Fresh org has human-managed Default; older-org ensure-default → enroll succeeds without restart. Compatible replay preserves workers/routing; conflict refuses; authority-not-ready stays incomplete. No existing agent moves. |
| A6 | Missing/invalid/scoped credentials cannot enroll. Authorized local Founder creation is active without pending approval; agent-session enrollment retains its separate existing authority/approval rules. |
| A7 | Packaged remote Client shows Host-specific read-only recovery and sends zero enrollment bytes to Host, including direct native-routed request attempts. Unsupported target/compatibility states do not create. Target/org switch cannot redirect or discard an in-flight/unknown create. |
| A8 | Rapid click/Enter produces one request; auth renewal, response loss, reopen and locale changes never replay a mutation. Success resets the next form and refreshes the roster without duplicate creation. |
| A9 | Inject a known validation refusal, lost response after persistence and bootstrap failure. Each shows the correct retained state; Check result uses the original target/org; persisted enrollment is not reported absent or fully ready without evidence; supported recovery is reachable. |
| A10 | Both journeys and affected loading/empty/error/recovery states work with keyboard and pointer in en/zh-CN at both viewports; switching locale preserves draft/focus/selection and sends no new requests. |

Success signal: all ten rows pass, zero duplicate creations, zero false-success
or rollback claims, and zero remote configuration forwarding. Source inspection,
documentation, an HTTP health response or green lint does not satisfy this signal.

Engineering's original implementation and authorized v1.1 rework have completed
their reported independent review, scoped QA, required CI and guarded merge.
This documentation reconciliation remains subject to its own normal review,
checks and Engineering-owned guarded merge. Product's acceptance contract above
still requires attributable UI→API→persisted-roster/setup evidence for both
journeys and remote packaged refusal/recovery observations; use the separate
head/venue attributions above rather than extending older proof to v1.1.
Prior QA TASK-18664/JOB-8940 at PR1830 head
`3187281953a0f126a5a873ea1af22f9a7a234419` proves the isolated Default→worker API
regression only; it does not prove the new UI or current deployed behavior.

## Non-goals, costs and remaining decisions

No arbitrary additional Founder-managed teams, manager replacement, agent moves,
bulk enrollment, new top-level Teams surface, routing-policy change, permission
editor, remote write exception, onboarding/account redesign or automatic cleanup.
Reuse existing APIs and owner/recovery mechanisms before proposing new storage.

The tradeoff is deliberate: Create team always requires configuring a manager;
users wanting direct reports use Default. Arbitrary named Founder-managed teams
would need an explicit future Founder policy choice and additional administration,
authority and routing work; they do not block this authorized cutline. Engineering
must identify a concrete incompatibility or required high-risk contract change
before proposing any scope change. No date or new implementation approval gate
is created by this document.
