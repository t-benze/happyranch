# Guest credential and wait lifecycle (THR-362 slice 2)

Required by THR-386 seq28, case C18 in TASK-19141's accepted/amended case record.
This finite abstraction checks two seats with five-attempt lockout, founder
New code, one redemption, fixed invite-time expiry, end, per-seat wait ownership,
message arrival, poll/cancel and private-state reconstruction. It is review
evidence, not a Python refinement proof, runtime dependency or CI gate.

| Action | Production owner |
|---|---|
| Wrong / Join | ThreadGuestsMixin.join_thread_guest selects `(thread_id, guest_id)` and commits count/refusal or hash/join audit/system message |
| NewCode | Existing replace_thread_guest_code resets unused invite count without extending seat lifetime |
| End / Authenticate | Existing _end_thread_guest_uncommitted and authenticate_thread_guest clear both hashes and retain ended seat |
| StartWait / Poll / Cancel | guests._call waiters keyed by original org/seat, credential read on every poll, finally cleanup |
| Message | Existing arrival function; data available to a subsequent wait poll |
| Restart | Persisted seat/digest/count remains; process-owned wait calls disappear |

`current.cfg` checks TypeOK, SingleUse, EndedPermanent, TokenScope, FixedDeadline,
OneWait and NoDisclosureAfterEnd. `negative-end-check.cfg` deliberately omits
poll's credential/end recheck and must violate NoDisclosureAfterEnd. No fairness
or wall-clock liveness is claimed: Poll abstracts timeout/data/end completion,
and real one-second polling/early return is measured by C13/C14. Invite lapse
and seat expiry are folded into time/end; there is no calendar, cryptographic
entropy, hash-collision, crash-during-write, file, routing, rate or read-floor
proof. C01-C17 own the real ASGI/SQLite/render/setup assertions.

Run from this directory using the pinned official jar and `/usr/bin/java` recipe
in [task lifecycle](../task-lifecycle/README.md#run-tlc), substituting module
`GuestSeats`, config `current.cfg` or `negative-end-check.cfg`, and distinct
absolute task-scratch metadirs. Use one worker, fp 0, seed 1 and Xmx512m.
Actual author results are recorded below after execution, independently of
Python behavioral verification. No live daemon restart occurs.

Author result, 2026-11-25, TLC 2.19 (official v1.7.4 jar SHA-256
936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88),
OpenJDK 21.0.13.1: current exit 0, 33,153 generated / 6,144 distinct states,
queue empty, depth 18. Negative exit 12, NoDisclosureAfterEnd violated,
1,750 generated / 572 distinct, depth 6. Logs and exact command receipts are
`output/TASK-19141/tlc-current.log`, `tlc-negative.log` and `verification.md`
(retrievable with happyranch recall). The initial negative run exposed a
model-expression precedence error (exit 75, not a regression counterexample);
parentheses repaired it before these final runs.
