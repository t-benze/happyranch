# Team escalation policy operator contract

The web surface is currently available only for the roster-confirmed
`engineering/engineering_manager/manager` tuple. The server is authoritative;
workers and every other manager receive the same `policy_surface_not_available`
404 and their Agent response and DOM contain no policy surface.

`GET .../team-escalation-policy/history` and `/outcomes` accept `cursor >= 0`
and `1 <= limit <= 50`. Pages are stable newest-first immutable receipt views.
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

Save creates an immutable inactive release. Save & activate is offered only
when the deployed binary verifies the release-controlled S7 proof artifact:
one production-shaped newly-launched-manager delivery/evaluation/receipt test
plus 14/14 production-seam negative tests. A separate release harness executes
the tests, which assert the actual durable/audit/queue/envelope effects at their
owning seams, and publishes receipts bound to the exact test nodes/source bytes,
process results, generator, and launch/recovery sources. The artifact is pinned
by an exact release-controlled digest. This is reviewed release provenance under
the existing shared-local trust boundary, not cryptographic signer or same-UID
tamper resistance. Public checksum recomputation cannot
replace the release pin; absent, malformed, stale,
mismatched, ambiguous, incomplete, or sub-100% evidence closes the guard. The
activation route independently consumes only that verified result and performs
CAS. A first activation becomes epoch 1
`bootstrap`; a rollback creates a new `reactivate_rollback` epoch pointing to
an older immutable release. Conflicts leave the saved release inactive and
require a refresh. All mutations truthfully attribute only the `shared local
operator credential`.

Landing this code, redeploying it, satisfying the readiness contract, and
creating/activating a production policy are distinct gates. This delivery does
not create or activate a production policy and does not deploy anything.
