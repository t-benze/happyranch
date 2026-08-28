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
  (Slice A/B/C all land in one restart). Non-schema UI/API follow-ups to
  Phase 1 (e.g. the TASK-5553 purpose-fidelity wire change) activate with
  the same restart and do **not** change `DEPLOY_EPOCH` semantics or any
  release criterion below.
- Record the restart as the **deployment epoch** (`DEPLOY_EPOCH`, UTC
  ISO-8601). Every one-month release query below is measured from this epoch,
  **never from the merge timestamp** — otherwise a deploy gap reads as a
  Phase-1 failure (THR-198 seq 29). Each query counts ONLY rows in the
  half-open one-month window `[DEPLOY_EPOCH, DEPLOY_EPOCH + 1 month)`
  (`enqueued_at >= DEPLOY_EPOCH AND enqueued_at < DEPLOY_EPOCH_PLUS_1_MONTH`).
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

`DEPLOY_EPOCH_PLUS_1_MONTH` is the ISO-8601 UTC instant exactly one calendar
month after `DEPLOY_EPOCH` (day-of-month clamped to month end), e.g.
`date -u -d "$DEPLOY_EPOCH + 1 month"` (GNU date) or
`date -u -v+1m "$DEPLOY_EPOCH"` (macOS BSD date). Every query below is
windowed to `[DEPLOY_EPOCH, DEPLOY_EPOCH_PLUS_1_MONTH)` so a wake enqueued
at or after the +1-month instant is excluded from the measurement.

### 5a. Supersession rate (target ≈ 0, vs baseline 10.4% happyranch / 13.2% tourism-org)

The consultant's `LEAD()` definition (THR-198 seq 2) — a reply wake is
superseded when the next wake for the same pair was enqueued before this one
started. Measured over the one-month deployment window: the next wake is the
next in-window wake for the pair (the final in-window wake per pair has no
in-window successor and is therefore never counted superseded):

```sql
WITH r AS (
  SELECT thread_id, agent_name, enqueued_at, started_at,
         lead(enqueued_at) OVER (PARTITION BY thread_id, agent_name
                                 ORDER BY enqueued_at, id) AS nxt
  FROM thread_invocations WHERE purpose = 'reply'
   AND enqueued_at >= '<DEPLOY_EPOCH>'              -- one-month window
   AND enqueued_at <  '<DEPLOY_EPOCH_PLUS_1_MONTH>' -- [epoch, epoch + 1 month)
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
agent_name)`. Measured over the one-month deployment window:

```sql
SELECT count(*) AS dup_pairs FROM (
  SELECT thread_id, agent_name, count(*) AS n
  FROM thread_invocations
  WHERE status = 'pending' AND purpose = 'reply'
    AND enqueued_at >= '<DEPLOY_EPOCH>'
    AND enqueued_at <  '<DEPLOY_EPOCH_PLUS_1_MONTH>'
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

**Last-ness is derived over ALL conversational REPLY wakes for the pair** —
next/last order is computed per `(thread_id, agent_name)` over every REPLY
wake (successful or not), and only THEN are the terminal failure/timeout
candidates filtered to the one-month deployment window. A failed wake that
has any later REPLY wake (a successful reply, a decline, or a later failed
retry) has a successor and is **not** counted as last-for-pair:

```sql
WITH r AS (
  -- Next-wake ordering over ALL conversational REPLY wakes per pair
  -- (unbounded — a post-window successor still makes an in-window failed
  -- wake NOT last). `id` breaks enqueued_at ties deterministically.
  SELECT thread_id, agent_name, enqueued_at, status,
         lead(enqueued_at) OVER (PARTITION BY thread_id, agent_name
                                 ORDER BY enqueued_at, id) AS nxt
  FROM thread_invocations
  WHERE purpose = 'reply'
),
f AS (
  -- The counted population: terminal failure/timeout candidates inside the
  -- one-month deployment window [DEPLOY_EPOCH, DEPLOY_EPOCH + 1 month).
  SELECT * FROM r
  WHERE status IN ('failed', 'timeout')
    AND enqueued_at >= '<DEPLOY_EPOCH>'
    AND enqueued_at <  '<DEPLOY_EPOCH_PLUS_1_MONTH>'
)
SELECT count(*) AS failed_or_timeout,
       sum(CASE WHEN nxt IS NULL THEN 1 ELSE 0 END) AS last_for_pair
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
- query window: <DEPLOY_EPOCH .. DEPLOY_EPOCH_PLUS_1_MONTH> (UTC)
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
  **Update (THR-198 seq 108-110, 2026-08-25):** the founder approved the
  Phase-2 mention-routing program. **Slice A (additive storage + pure
  resolver) landed** `threads.mention_routing_enabled` (INTEGER NOT NULL
  DEFAULT 1) and `thread_messages.mentions_json` (TEXT), both additive and
  idempotent, with store-seam persistence of derived mentions. **Slice B
  (this PR) enables production mention routing** — the resolver is wired
  into every conversational REPLY wake-selection seam at write time with
  the per-thread setting and the ratified matrix (disabled/zero-valid →
  broadcast; valid participant mentions → exactly that set), plus the
  founder-only `POST /threads/{id}/mention-routing` toggle and
  `happyranch threads mention-routing` CLI (audited
  `thread_mention_routing_changed`). TASK_FOLLOWUP/BOOTSTRAP stay isolated;
  GH-688 per-pair coalescing/claim/settlement/follow-on/rollback semantics
  are preserved. **Slice C (merged) adds the per-thread web control** — a
  founder-only direct "Mention routing" button in the thread-detail header
  opens a dialog whose switch truthfully renders the thread's
  current state, persists explicit changes through the same strict-boolean
  API, prevents duplicate mutation in flight, and rolls back + surfaces a
  visible error on failure. **Slice D (merged) ships the read-only Phase-2
  release-measurement harness and release-record format** (§8 below); it
  performs no production observation — the founder-approved acceptance
  measurement remains **pending** at the Phase-2 epoch and is recorded only
  when the window completes. Mention **priority/fairness, autocomplete, and
  active-respondent fallback remain out of scope**; `addressed_to_json`
  remains unwritten/unread with its separate cleanup plan.

## 8. Phase-2 mention-routing release measurement (THR-198 Slice D)

> **Status:** the measurement **harness** and **release-record format** are
> shipped; the **observation** is explicitly **pending** at the Phase-2
> epoch. No production observation is claimed by this document or by the
> PR that ships it. A diagnostic feature is not proof of its population
> outcome — a shipped harness does not constitute a passed release.

### 8a. Ratified measurement contract (THR-198 seq 87/88/108; epoch seq 128/129)

Three gates (baseline constants are quoted founder-approved inputs, never
re-derived):

1. **G1 — mentioned-message saving (gate).** Mentioned-message decline
   rate = `declined / all` REPLY wakes attributed to a mentioned message
   (one whose persisted `mentions_json` is a non-empty JSON array). Baseline
   293/499 = **58.7%** (August 2026, happyranch); expectation ~24/204 ≈
   **12%** among retained wakes.

   **Attribution semantics (live window):** each wake is attributed to the
   conversational arrival that **minted** it — the `thread_reply_wake_created`
   audit's `through_seq` (both production mint paths record it as the message
   seq being processed) — **never** to `triggering_seq`. GH-688 Phase-1
   coalescing makes `triggering_seq` the retained range floor
   (`acknowledged + 1`), which can be an earlier message that never woke the
   agent (a gap in the pair's coverage): a later unmentioned broadcast wake
   would otherwise be falsely attributed to an earlier mention. A
   `thread_reply_wake_coalesced` arrival mints nothing and never
   re-attributes. A replacement minted by restart recovery has no created
   audit and is attributed to its `thread_reply_wake_recovered`
   (`replacement_queued`) audit's `through_seq` — the pair's required
   watermark at recovery, i.e. the actual wake-causing arrival (never the
   retained range floor). Only genuinely unattributable rows (a follow-on
   minted at settlement, a legacy pre-audit row) fall back to
   `triggering_seq` — production-faithful for follow-ons (the retained
   range contains only arrivals that woke the agent).

   **Evidence ownership and fail-closed parsing.** Every audit lookup is
   scoped by the production ownership tuple — `thread_id` + `agent_name`
   (the wake owner recorded in `audit_log.agent`) + the 8-char
   `token_prefix` — never a weaker key, so an unrelated same-thread,
   same-prefix audit owned by a different agent can never reattribute an
   invocation. Every audit payload is decoded by ONE shared fail-closed
   decoder that returns only JSON objects: a NULL/empty payload,
   undecodable JSON, JSON null, a scalar (string/number/boolean), or a
   list is skipped before any field access — a malformed row can never
   abort a run or fabricate/reassign an attribution. All seq/range fields
   are then parsed fail-closed: a missing,
   non-integer, boolean-like, float, string, non-positive, or internally
   inverted range payload is skipped without exception — the measurement
   never crashes and never fabricates an attribution. `retained_queued`
   recoveries and `thread_reply_wake_coalesced` arrivals are never
   re-attributed.
2. **G2 — founder-message coverage (gate).** Founder messages (kind=message,
   `speaker='founder'`) covered iff ≥1 `purpose='reply'` invocation whose
   authoritative claimed delivery range contains that message's sequence
   reached `status='consumed'`. GH-688 Phase-1 coalescing lets ONE consumed
   REPLY cover an inclusive sequence range — the immutable range claimed at
   claim time (`thread_reply_wake_claimed` audit `from_seq..through_seq`,
   matched by token prefix; floor = the invocation's `triggering_seq`), so a
   founder message coalesced into an earlier wake is covered by that wake
   (an exact-`triggering_seq` join would falsely report it uncovered).
   Baseline **698**; permitted losses **0**.
3. **G3 — org-wide decline rate (report only).** `declined / all` REPLY
   wakes; expect ≈65%; **not** a gate.

Populations: `purpose='reply'` only — **TASK_FOLLOWUP and BOOTSTRAP are
isolated and never enter any population**. A mentioned message is one whose
persisted `mentions_json` is a non-empty JSON array (`[]` = zero valid →
broadcast; `NULL` = legacy/system → not mentioned; malformed JSON is
counted in a diagnostic and treated as not mentioned).

Epoch/window: **Phase-2 epoch = `2026-08-26T14:25:23Z`** (the daemon restart
that activated Slice C1 + the decline-doctrine fix + the stdin fix — NOT the
Slice-B merge). Window = half-open `[epoch, epoch + 1 calendar month)`, the
same convention as §1. While `as_of < window_end`, every observed value is
**interim** — mechanism evidence only, never a release result (seq 129); a
missing/partial window is never reported as a failed release. Every interim
population is bounded by the half-open observation cutoff
`min(as_of, window_end)` — a message or wake after `as_of` never enters an
interim record, so the record is reproducible at its stated instant. A
terminal decline outcome is observable at that instant only when its
`consumed_at` (the schema's single terminal-time stamp — every decline path
stamps it in the same UPDATE that sets `status='declined'`; there is no
separate `declined_at` column) is strictly earlier than the cutoff: a wake
enqueued before the cutoff but declined at or after it stays in the G1/G3
denominator, never the decline numerator, at the earlier observation
instant — a later `as_of` deterministically admits the settled outcome.

Zero-denominator behavior: a metric whose denominator is 0 is reported as
"zero population, not measurable" (`null` rate) — never a failure.

### 8b. Harness invocation (read-only, stdlib-only, isolated)

```bash
DB=<DAEMON_HOME>/orgs/happyranch/happyranch.db   # repeat for tourism-org
uv run python -m runtime.infrastructure.thread_release_measurement \
    --db "$DB" --epoch 2026-08-26T14:25:23Z --mode all \
    --out-json /tmp/phase2-record.json --out-md /tmp/phase2-record.md
```

Opens the DB with `sqlite3 mode=ro`; writes nothing. `--mode live` = the
post-change window measurement; `--mode replay` = baseline reproduction +
Phase-2 projection + zero-loss over the August 2026 baseline window.

The replay's write-time roster is reconstructed from current
`thread_participants` (`added_at <= created_at`) — a documented
**under-approximation**: removed participants are deleted, so mentions of
them classify as invalid → broadcast. Zero-loss violations whose baseline
repliers are ALL absent from the reconstructed roster are recorded as
**artifact candidates**, never as genuine routing failures
(`genuine = violations − artifact_candidates`). The live window uses the
persisted write-time signal and is unaffected by this limitation.

Consumed-REPLY coverage (live G2 and replay zero-loss) uses the
authoritative claimed ranges: a consumed REPLY covers its immutable claimed
range (`thread_reply_wake_claimed` audit `from_seq..through_seq`, matched by
token prefix within the same pair), so a founder message coalesced into an
earlier wake is covered by that wake. A consumed invocation with no claim
audit (a queued-settled wake that was never claimed, or a legacy
pre-coalescing row) covers only its own `triggering_seq` — an honest
under-approximation that never fabricates coverage.

### 8c. Phase-2 release-record template (operator fills at window completion)

```markdown
# THR-198 Phase-2 release record — <org>

- phase2_epoch (UTC): 2026-08-26T14:25:23Z (or later restart, re-pinned)
- deployed_version: <git rev-parse HEAD>; deployed_commit_short: <abbrev>
- restart_evidence: PID <n>, lstart <...>, /health {"status":"ok",...}
- topology_conclusion: <shared_daemon | separate_epochs — with evidence>
- query window: <epoch .. epoch + 1 calendar month> (UTC, half-open)
- G1 saving: wakes=<n>, declines=<n>, rate=<x>% — required: 58.7% → ~12%
  among retained wakes; population <n> mentioned messages
- G2 coverage: covered=<n>/<founder messages> — required: 0 losses vs the
  698 baseline
- G3 org-wide: declines=<n>/<wakes> (<x>%) — report only (expect ≈65%)
- replay projection (baseline window): retained=<n>, projected declines=<n>
  (<x>%), zero-loss violations=<n> (artifacts=<n>)
- interim: <true/false> — true ⇒ values are not a release result
- malformed mentions_json: <n>
- operator: <name/role>; noted anomalies: <none | ...>
```

---

## 9. TASK-5966 — strict mention-led exchange (F1) addendum

> **Status:** merged code is inert until a daemon restart loads it. The
> **F1 epoch** is that restart (`RATIFIED_EPOCH_F1 =
> 2026-08-28T06:22:21Z` in `runtime/infrastructure/thread_release_measurement.py`
> — record the ACTUAL restart instant, not the merge time). The Phase-2
> epoch/window (`2026-08-26T14:25:23Z`) and its G1/G2/G3 criteria are
> **preserved untouched**; F1 is a **separate measurement boundary** because
> the exchange changes the decline/wake populations wholesale (seq 189/194).

### 9.1 Deployment & rollback

- **Deploy:** restart the daemon on the merged head. The new tables
  (`thread_reply_exchange`, `thread_exchange_deferrals`) are created by the
  idempotent startup schema block — no migration file, no ALTER on shipped
  tables. The proposed `threads.reply_exchange_enabled` column and the
  `org_settings.threads.reply_exchange_enabled` org key were REMOVED before
  shipping (TASK-6027 founder ruling).
- **Activation:** mention routing and the strict mention-led exchange are
  UNCONDITIONAL (TASK-6027) — there is no switch to activate. The exchange
  opens on a founder-authored mention with P≠∅ and D≠∅; the shipped
  `threads.mention_routing_enabled` column is an inert legacy compatibility
  field (never read/written for behavior; persisted true/false values
  cannot disable routing).
- **Rollback is code-version rollback / operational containment only**
  (TASK-6027 — the per-thread toggle, the org kill-switch, and the disable
  retirement paths no longer exist). Reverting the code stops new
  exchanges. An open exchange epoch from a reverted deploy is NOT stranded:
  the normal idempotent closure bounds evaluate it at every seam (startup
  reconcile 6e, the next conversational write, the 30s reaper tick) —
  quiescence + 5-minute grace or the 4h absolute fail-open — releasing
  every pair with unacknowledged content via exactly ONE slot-checked
  range-covering catch-up (the same exactly-once enqueue ownership as every
  other wake; startup recovery re-enqueues a minted token if the process
  dies before enqueue). Obligations are never dropped and watermarks stay
  monotonic. No dormant resumable row is ever left behind by a revert.

### 9.2 F1 acceptance (falsifiable, run against the live DB read-only)

- **No-pierce:** `sum(wake_created)` for held pairs inside open exchanges is
  ZERO except the three documented pierce sources (mention of the agent, a
  pre-existing queued wake claimed mid-exchange, TASK_FOLLOWUP/BOOTSTRAP
  isolated broadcasts).
- **Exactly-one catch-up:** per deferred pair per exchange,
  `count(queued tokens covering [open_seq, close_seq]) in {0, 1}` (0 only
  when a pierce/coalesce already covered it).
- **N-to-1:** per founder-mention-led burst, per deferred pair, wake sessions
  drop from up to 5 (shipped) to exactly 1 (fresh corpus: 17 bursts,
  25 sessions saved).
- **G2 containment over the F1 window is at baseline** (0 losses).
- **Bounds:** `EXCHANGE_GRACE = 5 min` idle close (quiescence + no live
  cohort wake), `MAX_PRIORITY_WAIT = 4 h` absolute fail-open (reaper,
  30s tick), abort/archive → suppressed (no catch-up).
- **Replay acceptance:** THR-097 #116 range replay (deferred pairs → exactly
  1 catch-up each), the 17 founder-mention-led burst corpus, THR-198 #136
  preclaimed-wake fixture (pre-arrival settlement never covering; exactly one
  catch-up), and the adversarial race/restart/corruption suite in
  `tests/test_thread_reply_exchange.py`.

### 9.3 F1 release record (append to the record above)

```markdown
- f1_epoch (UTC): <actual restart instant>
- exchange rows: opened=<n>, released=<n>, suppressed=<n> (reasons)
- held wake audits while open (no-pierce violations): <n> (expect 0)
- catch-up wakes minted: <n>; coalesced releases: <n>
- deferred wake sessions per burst: avg=<x> (expect →1)
- G2 containment over F1 window: <covered>/<founder messages> (expect 0 loss)
- rollback drill: code-version rollback (revert deploy) → no new
  exchanges; any open epoch closed by the normal bounds (quiescence+grace
  or 4h fail-open) with exactly-one catch-up per uncovered deferred pair
  (count recorded)
- operator: <name/role>; noted anomalies: <none | ...>
```
