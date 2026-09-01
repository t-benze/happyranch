# Thread reply breaker persistence and compatibility substrate

> Status: current
> Current Source: `runtime/infrastructure/database.py`
> Notes: PR A defines persistence/compatibility; serial PR B activates runtime behavior without API/web expansion.

THR-200 PR A adds two SQLite tables and three indexes. One episode row is keyed
by `(thread_id, agent_name, executor_key)` and has a unique immutable
`episode_id`; receipts are keyed by immutable `invocation_token`. The episode
state vocabulary (`closed`, `open`, `probe`) and receipt vocabulary (`failure`,
`success`) are storage constraints for the serial runtime unit. No current
producer writes these rows in PR A and absence of an episode is represented by
no row.

The shipping `Database` initializer creates missing objects with additive
`CREATE ... IF NOT EXISTS` statements. It does not alter, drop, reinterpret, or
backfill any existing column. Genuine SQLite artifacts pinned to application
commits `e197b20` (pre-substrate) and `2c068bb` (first substrate), including an
interrupted first-table stage, prove forward creation, repair, representative
legacy-row preservation, and idempotent reopen. The rollback/read harness loads
and executes the actual pinned `e197b20` application `Database` module and
asserts its real `ThreadRecord` and `ThreadMessage` return contract.

This is a single-version rollout boundary: stop daemon admission before moving
between application versions. PR A does not authorize mixed-version writes or
claim that an older process understands new episode rows.

Serial PR B consumes this substrate. A `(thread, agent)` with
`required_through_seq > acknowledged_through_seq`, both ownership slots NULL,
and no episode row becomes cooldown-probe eligible after 15 minutes, and
must be excluded while it holds a `held` deferral in an `open` exchange. The
approved threshold 3, structured final-provider-failure taxonomy, eviction
separation and count-once semantics are active. OPEN coalesces with no launch;
one durable HALF_OPEN probe succeeds closed/reset or fails back to a rearmed
15-minute cooldown. Breaker recovery never releases a held exchange. Receipts,
leases, settlement and redacted audits are idempotent across restart,
concurrency and stale callbacks. PR B adds no API/web projection or manual action.
