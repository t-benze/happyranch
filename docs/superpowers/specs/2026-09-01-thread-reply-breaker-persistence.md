# Thread reply breaker persistence and compatibility substrate

> Status: current
> Current Source: `runtime/infrastructure/database.py`
> Notes: THR-200 PR A only; runtime behavior is not activated by this unit.

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

Runtime behavior belongs wholly to serial PR B after PR A passes review, QA,
authoritative CI, and guarded merge. In particular, PR B must implement, without
partial PR-A behavior, the founder's requirement: a `(thread, agent)` with
`required_through_seq > acknowledged_through_seq`, both ownership slots NULL,
and no episode row must become cooldown-probe eligible after its cooldown, and
must be excluded while it holds a `held` deferral in an `open` exchange. The
approved threshold 3, 15-minute timer, failure taxonomy, eviction separation,
and visible copy/default decisions remain PR-B constraints and are not activated
by this substrate.
