# Team escalation policy operator contract

The web surface is currently available only for the roster-confirmed
`engineering/engineering_manager/manager` tuple. The server is authoritative;
workers and every other manager receive the same `policy_surface_not_available`
404 and their Agent response and DOM contain no policy surface.

`GET .../team-escalation-policy/history` and `/outcomes` accept `cursor >= 0`
and `1 <= limit <= 50`. Pages are stable newest-first immutable receipt views.
History omits policy prose and prompts. Outcomes show only durable identity
pins and causal task/result/session/thread/hook/envelope receipts. Missing or
corrupt joins are `receipt_incomplete`, never inferred success. Raw evaluator
responses, rationale, proposed reason, prompts, policy content, credentials,
and secrets are never projected.

Save creates an immutable inactive release. Save & activate is offered only
when the shipped S7 readiness contract reports exact 100% recall across its
closed negative-control corpus; the activation route independently checks the
same contract and performs CAS. A first activation becomes epoch 1
`bootstrap`; a rollback creates a new `reactivate_rollback` epoch pointing to
an older immutable release. Conflicts leave the saved release inactive and
require a refresh. All mutations truthfully attribute only the `shared local
operator credential`.

Landing this code, redeploying it, satisfying the readiness contract, and
creating/activating a production policy are distinct gates. This delivery does
not create or activate a production policy and does not deploy anything.
