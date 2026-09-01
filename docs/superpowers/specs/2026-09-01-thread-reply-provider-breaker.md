# Thread reply provider breaker (THR-200 PR E)

Status: current. Founder-resolved contract: THR-200 seq 149.

The breaker is an additive durable state machine scoped to thread, agent, and
executor/model/config continuity. Absence means CLOSED. Structured post-launch
provider failure categories count exactly once per invocation; the third
consecutive failure opens. Resume eligibility and canonical transcript gap
recovery are independent and never count by themselves.

OPEN retains obligations and launches no provider. The daemon timer acquires a
single durable PROBE lease at the 15-minute boundary, excluding held
open-exchange participants. Success acknowledges and closes atomically; failure
reopens. Restart preserves state. Archive, participant removal, or continuity
switch closes. There is no manual action or new same-wake retry/backoff.

Rollout is single-version: stop daemon admission before migration. Rollback
requires admission disabled and proof that zero episodes are OPEN or PROBE;
otherwise it is blocked.

Compatibility evidence is pinned under `tests/fixtures/`: the genuine
pre-breaker application artifact at `e197b20a`, the first breaker artifact at
`2c068bbd`, an interruption after that commit's exact episode-table DDL, and a
vendored pre-breaker application reader from `e197b20a`. The shipping
initializer must preserve representative legacy rows, repair missing additive
objects, reopen idempotently, and remain readable by that old application path
after the operational rollback gate proves zero OPEN/PROBE episodes.
