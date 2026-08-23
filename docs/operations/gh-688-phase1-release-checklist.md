# GH-688 Phase 1 (thread reply-wake coalescing) — release checklist / runbook

> **Status:** a **release record + verification runbook** for the merged
> GitHub #688 Phase 1 implementation (Slices A/B/C). It performs **no
> deployment** — a merge is explicitly **not** a deployment here, and this
> document's PR performs none. The runtime only executes the coalescing code
> after a **daemon restart**, and the restart moment — not the merge time — is
> the measurement epoch for every post-deploy release criterion below.
>
> The post-deploy owner is **engineering_manager** (or a named release
> operator explicitly approved at deployment time). This runbook records the
> evidence an operator must capture and the exact queries to run **one month
> after the deployment epoch**.
>
> No production observation is claimed by this document or by the PR that
> ships it. Every baseline figure below is quoted from the pre-Phase-1
> measurements recorded in thread THR-198 (consultant analysis, seq 2/8) —
> they describe the state **before** the daemon restart that activates
> Phase 1.

## 1. Deployment model (read first)

- **Merging this PR is NOT deploying.** The daemon is a long-lived Python
  process; merged code on disk is inert until the process restarts.
- The **deployment event is a daemon restart** that loads the merged head
  (Slice A/B/C all land in one restart).
- Record the restart as the **deployment epoch** (`DEPLOY_EPOCH`, UTC
  ISO-8601). Every one-month release query below is measured from this epoch,
  **never from the merge timestamp** — otherwise a deploy gap reads as a
  Phase-1 failure (THR-198 seq 29).
- Phase-1 behavior is only "live" once the restart happened; do not claim
  supersession-rate movement or audit rows from before the restart.

## 2. Pre-restart record (fill in at the deployment window)

| Field | Value (operator fills) |
| --- | --- |
| Restart timestamp (UTC, ISO-8601) | `DEPLOY_EPOCH = <YYYY-MM-DDTHH:MM:SSZ>` |
| Deployed/served version + commit | `git -C <runtime>/repos/happyranch rev-parse HEAD` + `git log -1 --format=%h` |
| Daemon status/health evidence | `ps -o pid,lstart,etime,command -p <PID>` and `GET /api/v1/health` (`{"status":"ok","active_runtime":...}`) |
| Served `web/dist` evidence | the SPA bundle served from the restarted daemon's `web/dist` (path in the daemon command line / active runtime) |
| Deployment epoch recorded in this release record | §6 template |

Operational sequence (all existing surfaces, nothing invented):

1. Confirm the merge is on the checkout the daemon will run: `git -C <runtime>/repos/happyranch fetch origin && git -C <runtime>/repos/happyranch log -1 origin/main --format=%H` (must equal the intended Slice C head or later).
2. Record the pre-restart daemon evidence (PID, lstart, health).
3. Restart the daemon via the org's normal restart procedure (out of scope here — no new restart mechanism is introduced by Phase 1).
4. Within minutes of restart, capture the post-restart evidence: new PID/lstart, `GET /api/v1/health` `ok`, and the served version/commit.
5. Write `DEPLOY_EPOCH` into the release record (§6) immediately — a later reader must never mistake a deploy gap for a design failure.

## 3. Topology determination (required BEFORE applying the two-org metric)

The release criterion is two-org (happyranch + tourism-org), so the operator
must first establish and **record** whether both orgs are served by **one
shared daemon/process topology** or by **separate deployment epochs**:

- One shared daemon: a single `python -m runtime.daemon` process serves both
  orgs from the same runtime root (`/health` returns one `active_runtime`).
  Then there is **one** deployment epoch and both orgs activate Phase 1 at
  the same restart.
- Separate processes/runtimes (or orgs on different hosts / different
  restarts): each org has its **own** deployment epoch; measure each org's
  one-month window from its own epoch and record the difference.

Evidence to record:

```bash
# One line per daemon process serving the orgs:
ps -o pid,lstart,command -p "$(pgrep -f 'runtime.daemon' | tr '\n' ',' | sed 's/,$//')"
# For each org, the active runtime root from the health endpoint:
curl -fsS -H "Authorization: Bearer $HAPPYRANCH_TOKEN" "$DAEMON_URL/api/v1/health"
```

Record the conclusion in §6 (`topology_conclusion`): `shared_daemon` (one
epoch) or `separate_epochs` (list per-org epochs).

## 4. Post-deploy owner

- **Owner:** `engineering_manager` (default) or a named release operator
  explicitly approved at deployment time. The owner runs §5 at
  `DEPLOY_EPOCH + 1 month` and files the completed §6 record.
- The owner verifies the daemon has been continuously running (no second
  restart mid-window invalidates the epoch — if a restart occurs, re-record a
  new epoch and restart the one-month clock for the affected orgs).

## 5. One-month release queries (run per org, at DEPLOY_EPOCH + 1 month)

Authoritative source: the org SQLite database at
`<DAEMON_HOME>/orgs/<SLUG>/happyranch.db` (read-only via `sqlite3`; the daemon
must not be running writes against it concurrently during a read — a brief
read is fine, or use `happyranch` API surfaces where noted). `DAEMON_HOME` is
the runtime root reported by `/api/v1/health` (`active_runtime`).

```bash
DB=<DAEMON_HOME>/orgs/happyranch/happyranch.db     # repeat for tourism-org
sqlite3 -readonly "$DB"
```

### 5a. Supersession rate (target ≈ 0, vs baseline 10.4% happyranch / 13.2% tourism-org)

The consultant's `LEAD()` definition (THR-198 seq 2) — a reply wake is
superseded when the next wake for the same pair was enqueued before this one
started:

```sql
WITH r AS (
  SELECT thread_id, agent_name, enqueued_at, started_at,
         lead(enqueued_at) OVER (PARTITION BY thread_id, agent_name
                                 ORDER BY enqueued_at) AS nxt
  FROM thread_invocations WHERE purpose = 'reply'
   AND enqueued_at >= '<DEPLOY_EPOCH>'        -- month-long window from the epoch
)
SELECT count(*) AS total,
       sum(CASE WHEN nxt IS NOT NULL
                 AND (started_at IS NULL OR started_at > nxt)
                THEN 1 ELSE 0 END) AS superseded
FROM r;
```

Result: `superseded / total` must be ≈ 0. If the rate has **not** moved
toward zero, the coalescing did not bind in production — treat this as the
falsification threshold (THR-198 seq 2/8), not as green evidence.

### 5b. Duplicate pending conversational REPLY rows per pair (must be 0)

Phase 1's invariant is at most one unstarted REPLY per `(thread_id,
agent_name)`:

```sql
SELECT count(*) AS dup_pairs FROM (
  SELECT thread_id, agent_name, count(*) AS n
  FROM thread_invocations
  WHERE status = 'pending' AND purpose = 'reply'
    AND enqueued_at >= '<DEPLOY_EPOCH>'
  GROUP BY thread_id, agent_name
  HAVING n > 1
);
```

Result must be `0`. Any pair with `n > 1` is a Phase-1 binding failure.

### 5c. Failed/timeout last-for-pair ratio (against baseline 10/305 = 3.3%)

The intentional Phase-1 trade-off: a failed/timeout wake is retried by the
**next conversational arrival**, not hot-looped. The hazard is a pair whose
failed wake was its **last** — that agent goes silent until a new message.
Baseline measured pre-Phase-1: 10 of 305 failed+timeout were last-for-pair
(happyranch 6/204, tourism-org 4/101).

```sql
WITH f AS (
  SELECT thread_id, agent_name, enqueued_at,
         max(enqueued_at) OVER (PARTITION BY thread_id, agent_name) AS last_enq
  FROM thread_invocations
  WHERE purpose = 'reply'
    AND status IN ('failed', 'timeout')
    AND enqueued_at >= '<DEPLOY_EPOCH>'
)
SELECT count(*) AS failed_or_timeout,
       sum(CASE WHEN enqueued_at = last_enq THEN 1 ELSE 0 END) AS last_for_pair
FROM f;
```

Record `last_for_pair / failed_or_timeout`. This ratio is expected to stay
**bounded** (same order as 3.3%); a rising ratio is the trigger to revisit
the next-arrival retry policy (THR-198 seq 8) — not a reason to hot-loop.

### 5d. Audit-surface cross-check (optional, API-safe)

The six lifecycle actions are queryable through the existing audit API with
the unchanged `task_id = THR-*` scope convention — no SQL needed:

```bash
curl -fsS -H "Authorization: Bearer $HAPPYRANCH_TOKEN" \
  "$DAEMON_URL/api/v1/orgs/happyranch/audit?action=thread_reply_wake_created&since=<DEPLOY_EPOCH>"
# repeat for thread_reply_wake_coalesced / _claimed / _settled / _cancelled / _recovered
```

A healthy post-deploy org shows created + coalesced rows as messages flow and
settled rows after replies/declines/failures. No `task_id` semantics changed.

## 6. Release-record template (operator fills and files)

```markdown
# GH-688 Phase 1 release record — <org: happyranch | tourism-org | shared>

- deployment_epoch (UTC): <YYYY-MM-DDTHH:MM:SSZ>
- deployed_version: <git rev-parse HEAD>
- deployed_commit_short: <abbrev>
- restart_evidence: PID <n>, lstart <...>, /health {"status":"ok",...}
- topology_conclusion: <shared_daemon | separate_epochs — with evidence>
- (separate epochs only) second org epoch: <...>
- query window: <DEPLOY_EPOCH .. DEPLOY_EPOCH+1 month> (UTC)
- 5a supersession: total=..., superseded=... (rate=...%) — target ≈ 0
- 5b duplicate pending pairs: <n> — target 0
- 5c failed/timeout last-for-pair: <last>/<total> (<pct>) — baseline 10/305 (3.3%)
- 5d audit cross-check: created=..., coalesced=..., claimed=..., settled=...,
  cancelled=..., recovered=... (window since epoch)
- operator: <name/role>
- noted anomalies: <none | ...>
```

## 7. What this document/PR does NOT do

- It does **not** deploy, restart, or mutate production state.
- It does **not** claim any production observation of Phase-1 behavior.
- It does **not** change schema, auth, permissions, or the
  `audit_log.task_id` scope convention.
- It does **not** open Phase 2 (mention priority, `addressed_to_json`
  writer/doctrine, fairness) — that remains gated on a separate founder
  decision (THR-198 seq 7/10/20).
