# Threads Broadcast-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `addressed_to` concept from threads. Every thread message broadcasts a `REPLY` invocation to every participant except the speaker. Agents triage via a "decline-by-default" doctrine injected into the thread-invocation prompt for `purpose=REPLY` only. Declines become silent (no transcript row, no turn increment). All in-thread Feishu founder pings are removed; the web UI is the sole surface for ongoing thread participation. Web UI adds a per-message "status strip" so silent declines are visible at a glance.

**Architecture:** Three behavioral flips (mint loop, turn projection, decline mechanics) gated behind a backward-compat-friendly preparatory step (make `addressed_to` Pydantic-optional first). Then a clean removal sweep across bodies, models, helpers, audit shape, Feishu, web UI, CLI, and tests. The thread invocation prompt grows one new section, gated to `purpose=REPLY` only. The `GET /threads/{id}` response grows a `responder_status` array per message powered by a small new DB query joining `thread_invocations` on `triggering_seq`.

**Tech Stack:** Python 3.13 + FastAPI + Pydantic v2 + SQLite (WAL); React 18 + TypeScript strict + Tailwind 3 + TanStack Query v5 (`web/`); pytest with `tests/integration/fake_claude.sh` for end-to-end coverage.

**Spec:** `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`. Read it before starting — every task references its section numbers.

**Test commands (memorize):**

```bash
# Python unit tests only (fast):
uv run pytest tests/ -v -x

# Python integration tests (spawns real daemon + fake CLIs; required for any
# route or thread_runner change):
uv run pytest tests/ -v -m integration -x

# Both:
uv run pytest tests/ -v -m "" -x

# OpenAPI snapshot:
uv run pytest tests/contract/test_openapi_snapshot.py -v

# Web tests:
cd web && npm test -- --run

# Web type check:
cd web && npm run typecheck

# Web build (catches type errors the test runner misses):
cd web && npm run build
```

**Commit convention** (matches existing history): `<type>(<scope>): <subject>`. Type ∈ `feat|fix|refactor|test|docs|chore`. Scope examples: `threads`, `web`, `feishu`, `cli`. Subject in imperative.

---

## File Structure

**New files:**

- `tests/test_thread_broadcast_routing.py` — unit tests for the new mint loop, turn accounting, decline mechanics.
- `tests/test_thread_responder_status.py` — unit tests for the new `responder_status` join query and API surface.
- `tests/integration/test_threads_broadcast_e2e.py` — end-to-end coverage of broadcast + silent decline across multiple agents using `fake_claude.sh`.
- `web/src/features/threads/ResponderStatusStrip.tsx` — the per-message status strip component.
- `web/src/features/threads/ResponderStatusStrip.test.tsx` — RTL coverage.

**Heavily modified:**

- `src/daemon/routes/threads.py` — remove addressing helpers + selective-mint loop; new broadcast mint; new GET response shape with `responder_status`; decline behavior change.
- `src/daemon/thread_runner.py` — inject decline-by-default doctrine for REPLY invocations.
- `src/daemon/feishu_listener.py` — remove inbound `thread_addressed` reply-routing branch.
- `src/infrastructure/feishu/notifier.py` — remove `send_thread_addressed` + `send_thread_reply`.
- `src/infrastructure/audit_logger.py` — drop `addressed_to` from `log_thread_message_sent` signature.
- `src/infrastructure/database.py` — drop `addressed_to` from `append_thread_message` write path; add `list_invocations_for_thread_grouped_by_seq` helper for the responder_status join.
- `src/models.py` — drop `ThreadMessage.addressed_to`; add `ResponderStatus` + `ResponderStatusEntry` response models.
- `src/cli.py` — drop hardcoded `addressed_to` in compose payload.
- `web/src/lib/api/threads.ts` — drop `addressed_to` param from `sendThreadFollowUp`; update `ThreadMessage` to remove field, add `responder_status` join field.
- `web/src/lib/api/types.ts` — same.
- `web/src/features/threads/ThreadsPage.tsx` — remove addressing picker; remove badge rendering; wire status strip.
- `web/src/features/threads/MessageCard.tsx` — remove badge; render status strip below.
- `web/src/design-system/providers/_mock-threads.ts` + `web/src/mocks/messages.ts` — remove addressed_to from samples; add responder_status samples.

**Lightly touched:**

- `tests/integration/test_threads_*.py` (multiple files) — drop `addressed_to` from compose/send/compose-as-agent payloads; rewrite assertions that depend on selective addressing.
- `tests/contract/openapi.json` — regenerated.
- `CLAUDE.md` — threads section update.
- `protocol/skills/thread/SKILL.md` — remove `to:` field guidance; pointer to doctrine in invocation prompt.
- `docs/superpowers/specs/2026-05-13-threads-design.md` — append "superseded by 2026-05-30-thread-broadcast-only-design.md (addressing model)" pointer.

---

## Task 1: Make `addressed_to` Pydantic-optional and ignored on the wire

**Why first:** Subsequent tasks remove the field from request bodies. Pydantic v2 already silently ignores unknown fields by default, but the **current** code makes `addressed_to` semantically load-bearing (required in compose body, defaulted to `["@all"]` in send/compose-as-agent). Making it explicitly optional + unused is the prep step that lets later tasks delete callers without breaking parsing.

**Files:**
- Modify: `src/daemon/routes/threads.py:62-83` — `ComposeBody`, `SendBody`, `ComposeAsAgentBody`
- Modify: `tests/test_thread_validation.py` (or equivalent) if it asserts on `addressed_to_*` validation codes

**Steps:**

- [ ] **Step 1: Locate the three bodies.** Run:
  ```bash
  grep -n "addressed_to" src/daemon/routes/threads.py | grep -E "BaseModel|class |    addressed_to" | head -20
  ```
  Expected: lines around `ComposeBody`, `SendBody`, `ComposeAsAgentBody` definitions.

- [ ] **Step 2: Change `ComposeBody.addressed_to` from required to optional with default `None`.**

  Find:
  ```python
  class ComposeBody(BaseModel):
      subject: str
      recipients: list[str]
      body_markdown: str
      addressed_to: list[str]  # currently required, no default
      forwarded_from_id: str | None = None
      forwarded_from_kind: str | None = None
  ```

  Change to:
  ```python
  class ComposeBody(BaseModel):
      subject: str
      recipients: list[str]
      body_markdown: str
      addressed_to: list[str] | None = None  # DEPRECATED: ignored; broadcasts to all participants
      forwarded_from_id: str | None = None
      forwarded_from_kind: str | None = None
  ```

- [ ] **Step 3: Repeat for `SendBody` and `ComposeAsAgentBody`.** Both currently default to `["@all"]`; change to `list[str] | None = None`.

- [ ] **Step 4: Find every call site that reads `body.addressed_to`.** Run:
  ```bash
  grep -n "body.addressed_to\|body\.addressed_to" src/daemon/routes/threads.py
  ```
  At each site, the field is still being used to drive routing. **Do not change behavior yet** — just substitute `body.addressed_to or ["@all"]` so a missing/None field defaults to the existing broadcast behavior.

  Example (`compose_thread` line ~134):
  ```python
  _validate_addressed_to(body.addressed_to, body.recipients)
  ```
  becomes:
  ```python
  _validate_addressed_to(body.addressed_to or ["@all"], body.recipients)
  ```

  Same treatment at line ~162 (`_resolve_addressed_agents`), line ~184 (`append_thread_message addressed_to=`), line ~194 (`log_thread_message_sent`), and equivalents in `compose_thread_as_agent` and `send_message_endpoint`.

- [ ] **Step 5: Run the unit tests.**
  ```bash
  uv run pytest tests/ -v -x
  ```
  Expected: PASS. (Any test that posted `addressed_to: ["@all"]` still posts it and still gets the same behavior; any test that omitted it now also works.)

- [ ] **Step 6: Run integration tests.**
  ```bash
  uv run pytest tests/ -v -m integration -x
  ```
  Expected: PASS. No behavioral change.

- [ ] **Step 7: Commit.**
  ```bash
  git add src/daemon/routes/threads.py
  git commit -m "$(cat <<'EOF'
  refactor(threads): make addressed_to optional on request bodies

  Preparatory step for broadcast-only routing. Pydantic field becomes
  optional (None default) and call sites fall back to ['@all'] when
  absent. No behavior change; lets subsequent tasks remove the field
  from callers without breaking parsing.
  EOF
  )"
  ```

---

## Task 2: Flip the mint loop to broadcast

**Why now:** This is the core behavioral change. Once flipped, every `kind=message` insert mints `REPLY` invocations for every participant except the speaker, regardless of what was in `addressed_to`. The field is now functionally dead even though it's still parsed.

**Files:**
- Modify: `src/daemon/routes/threads.py` — compose_thread, compose_thread_as_agent, send_message_endpoint, reply_thread_endpoint
- Create: `tests/test_thread_broadcast_routing.py`

**Steps:**

- [ ] **Step 1: Write the failing test first.** Create `tests/test_thread_broadcast_routing.py`:

  ```python
  """Broadcast-mint routing tests for threads.

  Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §4
  """
  from __future__ import annotations

  import pytest
  from fastapi.testclient import TestClient

  from tests.conftest_helpers import make_org_with_agents  # if exists, else inline below


  @pytest.fixture
  def three_agent_thread(client: TestClient, org_slug: str):
      """Compose a thread with three approved-agent participants."""
      r = client.post(
          f"/api/v1/orgs/{org_slug}/threads",
          json={
              "subject": "broadcast routing test",
              "recipients": ["alpha", "bravo", "charlie"],
              "body_markdown": "kickoff",
          },
      )
      assert r.status_code == 200, r.text
      return r.json()["thread_id"]


  def test_founder_compose_mints_one_invocation_per_participant(
      client, org_slug, three_agent_thread, db
  ):
      """§4: every kind=message mints REPLY for every participant except speaker.
      The founder is not a participant, so all three agents get invocations."""
      thread_id = three_agent_thread
      rows = db.execute(
          "SELECT agent_name, purpose, status FROM thread_invocations "
          "WHERE thread_id=? ORDER BY agent_name",
          (thread_id,),
      ).fetchall()
      agent_names = sorted(r["agent_name"] for r in rows)
      assert agent_names == ["alpha", "bravo", "charlie"]
      assert all(r["purpose"] == "reply" for r in rows)
      assert all(r["status"] == "pending" for r in rows)


  def test_agent_reply_excludes_self_from_broadcast(
      client, org_slug, three_agent_thread, db, agent_token
  ):
      """§4: speaker self-exclusion. When 'alpha' replies, bravo + charlie get
      invocations but alpha does NOT."""
      thread_id = three_agent_thread
      # alpha consumes its existing invocation by replying
      alpha_inv = db.execute(
          "SELECT invocation_token FROM thread_invocations "
          "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
          (thread_id,),
      ).fetchone()
      r = client.post(
          f"/api/v1/orgs/{org_slug}/threads/{thread_id}/reply",
          json={
              "invocation_token": alpha_inv["invocation_token"],
              "body_markdown": "alpha responding",
          },
      )
      assert r.status_code == 200, r.text

      # After alpha's reply: bravo + charlie should each have a NEW pending
      # invocation; alpha should have NO new pending invocation.
      rows = db.execute(
          "SELECT agent_name, COUNT(*) AS n FROM thread_invocations "
          "WHERE thread_id=? AND status='pending' GROUP BY agent_name",
          (thread_id,),
      ).fetchall()
      pending = {r["agent_name"]: r["n"] for r in rows}
      assert pending.get("bravo") == 1
      assert pending.get("charlie") == 1
      assert "alpha" not in pending


  def test_founder_not_pinged_on_agent_reply(
      client, org_slug, three_agent_thread, db
  ):
      """§4: the founder is not in thread_participants and is never a
      mint target. No row with agent_name='founder' or '@founder' should
      exist after any agent reply."""
      thread_id = three_agent_thread
      # Verify no founder-targeted invocations ever exist for this thread.
      rows = db.execute(
          "SELECT agent_name FROM thread_invocations WHERE thread_id=?",
          (thread_id,),
      ).fetchall()
      names = {r["agent_name"] for r in rows}
      assert "founder" not in names
      assert "@founder" not in names
  ```

  Note: existing test infrastructure should already provide `client`, `org_slug`, `db`, `agent_token` fixtures. If not, copy the patterns from `tests/test_threads_*.py` neighboring files. **Adapt fixture imports to match the actual conftest** — don't guess.

- [ ] **Step 2: Run the new tests to verify they fail.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py -v
  ```
  Expected: `test_founder_compose_mints_one_invocation_per_participant` may PASS (it omits `addressed_to`, which defaults to `["@all"]` per Task 1, which already mints for all recipients). `test_agent_reply_excludes_self_from_broadcast` should FAIL — current reply logic doesn't mint for any other participant. If both pass already, that's because the `@all` path already broadcasts at compose-time; the meaningful new assertion is the agent-reply broadcast, which is the failing case.

- [ ] **Step 3: Modify `compose_thread` to broadcast unconditionally.** In `src/daemon/routes/threads.py` (around line 162):

  Find:
  ```python
      addressed_agents = _resolve_addressed_agents(body.addressed_to or ["@all"], body.recipients)

      if len(addressed_agents) > turn_cap:
          raise HTTPException(...)
  ```

  Replace with:
  ```python
      # Broadcast model: every recipient (== future participant) gets a
      # REPLY invocation. The founder is not a participant; no founder
      # mint. Self-exclusion is moot at compose time (founder is the
      # speaker and not in recipients).
      addressed_agents = list(body.recipients)
  ```

  Also remove the `turn_cap_exceeded` guard at compose time — spec §7 turn projection simplifies to `+1` per message. Leave the message-level check for later in this task. (For compose specifically, the just-inserted message is the first one, so `turns_used==0` and `1 <= turn_cap` always holds for any positive turn_cap.)

- [ ] **Step 4: Modify `compose_thread_as_agent` the same way.** Find the mint loop (line ~445-455 region):

  Find:
  ```python
      resolved = _resolve_addressed_agents(body.addressed_to or ["@all"], recipients)
      # ... existing dedup / founder-exclusion logic ...
      for name in addressed_agents_for_mint:
          inv = org.db.mint_thread_invocation(...)
  ```

  Replace with broadcast over participants (excluding composer, who is the speaker):
  ```python
      # Broadcast: every participant except the composer-speaker gets a REPLY.
      # @founder is not a participant; no founder mint.
      addressed_agents_for_mint = [
          name for name in recipients if name != body.composer
      ]
      for name in addressed_agents_for_mint:
          inv = org.db.mint_thread_invocation(
              thread_id=thread_id, agent_name=name,
              triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
          )
          tokens_to_enqueue.append(inv.invocation_token)
  ```

- [ ] **Step 5: Modify `send_message_endpoint` and `reply_thread_endpoint`.** Locate via:
  ```bash
  grep -n "async def send_message\|async def reply_thread\|mint_thread_invocation" src/daemon/routes/threads.py
  ```

  At each mint site, replace the addressed-driven loop with broadcast-over-participants-minus-speaker:

  ```python
  # Inside the message-append transaction, after the message row exists:
  participants = org.db.list_thread_participants(thread_id)
  speaker = "founder" if endpoint_is_founder_send else <agent name from invocation>
  for p in participants:
      if p.agent_name == speaker:
          continue
      inv = org.db.mint_thread_invocation(
          thread_id=thread_id, agent_name=p.agent_name,
          triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
      )
      tokens_to_enqueue.append(inv.invocation_token)
  ```

  Confirm the actual speaker derivation matches each route's existing logic (founder send vs. agent reply use different sources).

- [ ] **Step 6: Remove `_verify_addressed` call from `reply_thread_endpoint`.** Find:
  ```bash
  grep -n "_verify_addressed" src/daemon/routes/threads.py
  ```
  Delete the call (do NOT delete the function definition itself — that's Task 7's job). The new model: any participant can reply to any message; eligibility is determined entirely by holding a valid invocation token.

- [ ] **Step 7: Run the broadcast tests.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py -v
  ```
  Expected: all three tests PASS.

- [ ] **Step 8: Run the full thread test suite to surface old-test breakage.**
  ```bash
  uv run pytest tests/ -v -k threads
  ```
  Expected: a chunk of pre-existing tests will FAIL because they assert "only addressed agents got an invocation" or similar selective-routing semantics. Note the failing test names for Task 11 (test fixture sweep).

  **Do not fix them yet** — they'll be addressed in bulk in Task 11. The point of this step is to *enumerate* them.

- [ ] **Step 9: Run integration tests.**
  ```bash
  uv run pytest tests/ -v -m integration -k threads -x
  ```
  Expected: some failures. Same disposition — note and defer to Task 11.

- [ ] **Step 10: Commit.**
  ```bash
  git add src/daemon/routes/threads.py tests/test_thread_broadcast_routing.py
  git commit -m "$(cat <<'EOF'
  feat(threads): broadcast REPLY invocations to all participants

  Removes selective-addressing routing. Every kind=message mints a REPLY
  for every participant except the speaker. The founder is not a
  participant, so she is never minted. Replaces _resolve_addressed_agents
  / _verify_addressed selective logic at all four mint sites (compose,
  compose-as-agent, send, reply).

  Old tests that asserted narrow addressing fail; cleanup follows in
  Task 11 (test fixture sweep).
  EOF
  )"
  ```

---

## Task 3: Simplify turn-cap projection

**Why now:** Spec §7 — `turns_used` increments per `kind=message`, not per minted invocation. Projection at send-time simplifies from `turns_used + pending_load + len(addressed)` to `turns_used + 1`.

**Files:**
- Modify: `src/daemon/routes/threads.py` — at every send-time projection check
- Modify: `src/infrastructure/database.py` — `increment_thread_turns_used` callers may need adjusting (check if it's called per-invocation today)

**Steps:**

- [ ] **Step 1: Find every `turns_used` increment site.**
  ```bash
  grep -rn "turns_used\|increment_thread_turns_used\|count_pending_turn_obligations" src/
  ```
  Expected output: increment calls in `reply_thread_endpoint`, `decline_thread_endpoint`, `send_message_endpoint`, `compose_*`. Projection checks call `count_pending_turn_obligations` + `len(addressed)`.

- [ ] **Step 2: Write a unit test for the new rule.** Append to `tests/test_thread_broadcast_routing.py`:

  ```python
  def test_turns_used_increments_per_message_not_per_invocation(
      client, org_slug, three_agent_thread, db
  ):
      """§7: turns_used increments once per kind=message row, regardless of
      participant count."""
      thread_id = three_agent_thread
      # After compose (1 message, 3 participants), turns_used should be 1, not 3.
      row = db.execute(
          "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
      ).fetchone()
      assert row["turns_used"] == 1

      # After one agent reply (another message), turns_used should be 2.
      alpha_inv = db.execute(
          "SELECT invocation_token FROM thread_invocations "
          "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
          (thread_id,),
      ).fetchone()
      r = client.post(
          f"/api/v1/orgs/{org_slug}/threads/{thread_id}/reply",
          json={"invocation_token": alpha_inv["invocation_token"],
                "body_markdown": "alpha responding"},
      )
      assert r.status_code == 200
      row = db.execute(
          "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
      ).fetchone()
      assert row["turns_used"] == 2
  ```

- [ ] **Step 3: Verify the test fails.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py::test_turns_used_increments_per_message_not_per_invocation -v
  ```
  Expected: FAIL. Today's compose increments by `len(addressed)` (==3 for a 3-agent thread), and reply increments by 1, so the first assertion sees 3 not 1.

- [ ] **Step 4: Move all `increment_thread_turns_used` calls to fire ONCE per message append.** The canonical pattern:

  Find:
  ```python
  for name in addressed_agents:
      inv = org.db.mint_thread_invocation(...)
      tokens_to_enqueue.append(inv.invocation_token)
      org.db.increment_thread_turns_used(thread_id)  # if inside the loop
  ```
  or
  ```python
  org.db.increment_thread_turns_used(thread_id, by=len(addressed_agents))  # if outside
  ```

  Replace with a single increment per message append:
  ```python
  seq = org.db.append_thread_message(...)
  org.db.increment_thread_turns_used(thread_id, by=1)
  for name in addressed_agents_for_mint:
      inv = org.db.mint_thread_invocation(...)
      tokens_to_enqueue.append(inv.invocation_token)
  ```

  Apply at all four message-append sites: `compose_thread`, `compose_thread_as_agent`, `send_message_endpoint`, `reply_thread_endpoint`.

- [ ] **Step 5: Simplify the projection check.** Find:
  ```bash
  grep -n "turn_cap\|projected" src/daemon/routes/threads.py
  ```
  At each projection site (currently `projected = turns_used + pending_load + len(addressed)` or similar):

  Find the existing logic, e.g.:
  ```python
  pending_load = org.db.count_pending_turn_obligations(thread_id)
  projected = thread.turns_used + pending_load + len(addressed_agents)
  if projected > thread.turn_cap:
      raise HTTPException(status_code=429, detail={
          "code": "turn_cap_exceeded",
          "used": thread.turns_used, "cap": thread.turn_cap,
          "requested": len(addressed_agents),
      })
  ```

  Replace with:
  ```python
  projected = thread.turns_used + 1
  if projected > thread.turn_cap:
      raise HTTPException(status_code=429, detail={
          "code": "turn_cap_exceeded",
          "used": thread.turns_used, "cap": thread.turn_cap,
          "requested": 1,
      })
  ```

  **Do not remove `count_pending_turn_obligations` itself** — spec §7 notes the function is still consulted by the task-followup auto-extend path.

- [ ] **Step 6: Run the turn-accounting test.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py::test_turns_used_increments_per_message_not_per_invocation -v
  ```
  Expected: PASS.

- [ ] **Step 7: Run integration tests for thread turn-cap behavior.**
  ```bash
  uv run pytest tests/ -v -m integration -k "turn_cap or turn_count or extend" -x
  ```
  Expected: some pre-existing tests may fail because they expected `len(addressed)`-based accounting. Catalog and defer to Task 11.

- [ ] **Step 8: Commit.**
  ```bash
  git add src/daemon/routes/threads.py tests/test_thread_broadcast_routing.py
  git commit -m "$(cat <<'EOF'
  refactor(threads): turns_used increments per message, not per invocation

  Spec §7. Cap default 500 now means literally 500 messages. Projection
  simplifies from turns_used + pending_load + len(addressed) to
  turns_used + 1 because mint happens after append and per-recipient
  cost is gone. count_pending_turn_obligations is preserved for the
  task-followup auto-extend path which still needs it.
  EOF
  )"
  ```

---

## Task 4: Silent decline (no transcript row, no turn increment)

**Why now:** Spec §6 — decline endpoint stops inserting a `thread_messages` row and stops incrementing `turns_used`. Invocation-row update (status='declined', consumed_at, decline_reason) stays exactly as today.

**Files:**
- Modify: `src/daemon/routes/threads.py` — `decline_thread_endpoint`
- Modify: `src/infrastructure/audit_logger.py` — `log_thread_decline_consumed` payload (add optional `reason`)

**Steps:**

- [ ] **Step 1: Write a unit test for silent decline.** Append to `tests/test_thread_broadcast_routing.py`:

  ```python
  def test_decline_writes_no_transcript_row(
      client, org_slug, three_agent_thread, db
  ):
      """§6: decline endpoint consumes invocation but inserts no
      thread_messages row and does NOT increment turns_used."""
      thread_id = three_agent_thread
      # Capture pre-decline state
      pre_msgs = db.execute(
          "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id=?",
          (thread_id,),
      ).fetchone()["n"]
      pre_turns = db.execute(
          "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
      ).fetchone()["turns_used"]

      # Get alpha's pending invocation and decline it
      alpha_inv = db.execute(
          "SELECT invocation_token FROM thread_invocations "
          "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
          (thread_id,),
      ).fetchone()
      r = client.post(
          f"/api/v1/orgs/{org_slug}/threads/{thread_id}/decline",
          json={
              "invocation_token": alpha_inv["invocation_token"],
              "reason": "no material to add",
          },
      )
      assert r.status_code == 200, r.text

      # Post-decline assertions
      post_msgs = db.execute(
          "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id=?",
          (thread_id,),
      ).fetchone()["n"]
      post_turns = db.execute(
          "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
      ).fetchone()["turns_used"]
      assert post_msgs == pre_msgs, "decline must not insert a thread_messages row"
      assert post_turns == pre_turns, "decline must not increment turns_used"

      # Invocation row was updated correctly
      inv_row = db.execute(
          "SELECT status, consumed_at, decline_reason FROM thread_invocations "
          "WHERE invocation_token=?",
          (alpha_inv["invocation_token"],),
      ).fetchone()
      assert inv_row["status"] == "declined"
      assert inv_row["consumed_at"] is not None
      assert inv_row["decline_reason"] == "no material to add"
  ```

- [ ] **Step 2: Verify the test fails.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py::test_decline_writes_no_transcript_row -v
  ```
  Expected: FAIL on `post_msgs == pre_msgs` (current decline writes a row) and/or `post_turns == pre_turns` (current decline increments turns).

- [ ] **Step 3: Locate the decline endpoint.**
  ```bash
  grep -n "async def decline_thread_endpoint\|@router.*decline" src/daemon/routes/threads.py
  ```

- [ ] **Step 4: Modify the decline handler.** The current handler shape is approximately:

  ```python
  async with org.db_lock:
      ok = org.db.mark_invocation_declined(
          token=body.invocation_token,
          status=ThreadInvocationStatus.DECLINED,
          decline_reason=body.reason,
      )
      if not ok:
          raise HTTPException(...)
      seq = org.db.append_thread_message(
          thread_id=thread_id, speaker=agent_name,
          kind=ThreadMessageKind.DECLINE,
          decline_reason=body.reason,
      )
      org.db.increment_thread_turns_used(thread_id)
      AuditLogger(org.db).log_thread_decline_consumed(
          thread_id, agent_name=agent_name, seq=seq,
      )
      await _publish_thread_event(...)
  ```

  Replace with:
  ```python
  async with org.db_lock:
      ok = org.db.mark_invocation_declined(
          token=body.invocation_token,
          status=ThreadInvocationStatus.DECLINED,
          decline_reason=body.reason,
      )
      if not ok:
          raise HTTPException(...)
      AuditLogger(org.db).log_thread_decline_consumed(
          thread_id, agent_name=agent_name, reason=body.reason,
      )
      # No thread_messages row, no turns_used increment.
  await _publish_thread_event(
      org, slug, thread_id=thread_id,
      seq=None,                 # no message; event consumers tolerate None
      speaker=agent_name,
      kind="decline_status",    # new event kind for status-strip listeners
      preview=None, status="open",
  )
  ```

  Adjust `seq=None` handling in `_publish_thread_event` if that signature doesn't accept None — make it accept None for status-only events.

- [ ] **Step 5: Update `log_thread_decline_consumed` to accept optional `reason`.** In `src/infrastructure/audit_logger.py`:

  Find the existing method (whatever its current signature) and add a `reason: str | None = None` kwarg whose value goes into the payload:

  ```python
  def log_thread_decline_consumed(
      self, thread_id: str, *, agent_name: str, reason: str | None = None
  ) -> None:
      payload: dict[str, object] = {"agent_name": agent_name}
      if reason:
          payload["reason"] = reason
      self.log_event(
          kind="thread_decline_consumed",
          task_id=thread_id,
          payload=payload,
      )
  ```

- [ ] **Step 6: Run the silent-decline test.**
  ```bash
  uv run pytest tests/test_thread_broadcast_routing.py::test_decline_writes_no_transcript_row -v
  ```
  Expected: PASS.

- [ ] **Step 7: Run integration tests touching decline.**
  ```bash
  uv run pytest tests/ -v -m integration -k decline -x
  ```
  Expected: some pre-existing tests will fail (they expect decline to write a transcript row). Catalog and defer to Task 11.

- [ ] **Step 8: Commit.**
  ```bash
  git add src/daemon/routes/threads.py src/infrastructure/audit_logger.py tests/test_thread_broadcast_routing.py
  git commit -m "$(cat <<'EOF'
  feat(threads): silent decline — no transcript row, no turn increment

  Spec §6. Decline endpoint keeps its wire shape and still updates the
  invocation row (status=declined, consumed_at, decline_reason) but no
  longer inserts a thread_messages row and no longer increments
  turns_used. Optional reason flows into the thread_decline_consumed
  audit payload. _publish_thread_event learns a decline_status kind so
  the web status-strip listener can react without a message row.
  EOF
  )"
  ```

---

## Task 5: Inject decline-by-default doctrine into thread invocation prompt

**Why now:** Spec §5 — agents need the doctrine at the decision point. Gated to `purpose=REPLY` only (not BOOTSTRAP, CLOSE_OUT, TASK_FOLLOWUP).

**Files:**
- Modify: `src/daemon/thread_runner.py:86-120` — `build_thread_prompt`
- Modify: `tests/test_thread_runner.py` (or create if missing)

**Steps:**

- [ ] **Step 1: Write a unit test for the doctrine injection.** Create or append to `tests/test_thread_prompt_doctrine.py`:

  ```python
  """Decline-by-default doctrine injection for thread REPLY invocations.

  Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §5
  """
  from __future__ import annotations

  from src.daemon.thread_runner import build_thread_prompt
  # Adapt these imports to actual module paths; the spec lists thread_runner.py:109
  # as the construction site.

  DOCTRINE_HEADER = "Decline-by-Default in Threads"


  def _build(purpose: str, **overrides):
      # Minimal valid args — fill in with the simplest fixtures that mirror
      # neighboring tests in tests/test_thread_runner.py. Defaults shown:
      defaults = {
          "thread": _fake_thread(),
          "participants": [_fake_participant("alpha"), _fake_participant("bravo")],
          "messages": [_fake_message(seq=1, speaker="founder", body="kickoff")],
          "invocation_token": "tok-x",
          "invoked_agent": "alpha",
          "purpose": purpose,
          "triggering_seq": 1,
      }
      defaults.update(overrides)
      return build_thread_prompt(**defaults)


  def test_doctrine_appears_for_reply_purpose():
      prompt = _build(purpose="reply")
      assert DOCTRINE_HEADER in prompt
      assert "decline" in prompt.lower()


  def test_doctrine_absent_for_bootstrap_purpose():
      prompt = _build(purpose="bootstrap")
      assert DOCTRINE_HEADER not in prompt


  def test_doctrine_absent_for_close_out_purpose():
      prompt = _build(purpose="close_out")
      assert DOCTRINE_HEADER not in prompt


  def test_doctrine_absent_for_task_followup_purpose():
      prompt = _build(purpose="task_followup")
      assert DOCTRINE_HEADER not in prompt
  ```

  Fill in `_fake_thread`, `_fake_participant`, `_fake_message` helpers using the simplest object shapes that satisfy `build_thread_prompt`'s field reads. Crib from any existing test that touches this builder; if none exists, look at `src/models.py` for `ThreadRecord`, `ThreadParticipant`, `ThreadMessage` shapes.

- [ ] **Step 2: Verify the failing test.**
  ```bash
  uv run pytest tests/test_thread_prompt_doctrine.py -v
  ```
  Expected: all four tests FAIL on `DOCTRINE_HEADER in prompt` (or absent for non-REPLY purposes).

- [ ] **Step 3: Add the doctrine section to `build_thread_prompt`.** In `src/daemon/thread_runner.py`:

  After the existing `note = _purpose_note(...)` line and BEFORE the final return:

  ```python
  doctrine = _decline_by_default_doctrine() if purpose == "reply" else ""
  ```

  Inside the return string, insert `doctrine` between the "You have been invoked because:" block and the "Your invocation_token" block:

  ```python
  return (
      f"You are participating in thread {thread.id}: \"{thread.subject}\".\n\n"
      f"Participants: {parts_str}.\n"
      f"Started: {thread.started_at.isoformat()}. {forwarded}\n\n"
      f"Full message history follows. Most recent message is at the bottom.\n\n"
      f"---\n{history}\n\n"
      f"You have been invoked because:\n  {note}\n\n"
      f"{doctrine}"
      f"Your invocation_token for this turn is: {invocation_token}\n"
      f"Include this token in every callback payload (reply, decline, dispatch,\n"
      f"close-out). It authorizes this single turn and is single-use for the\n"
      f"terminal callback (reply/decline/close-out).\n\n"
      f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
  )
  ```

  Add the helper at module scope (above `build_thread_prompt`):

  ```python
  def _decline_by_default_doctrine() -> str:
      return (
          "## Decline-by-Default in Threads\n\n"
          "This invocation was minted because a new message was posted to this\n"
          "thread. Every participant gets an invocation on every message — that\n"
          "does NOT mean every participant should reply.\n\n"
          "Default behavior: call `happyranch threads decline --from-file <payload>`\n"
          "with no reason. Your invocation is consumed silently; no transcript\n"
          "entry is written.\n\n"
          "Reply (with `happyranch threads reply --from-file <payload>`) only when\n"
          "ALL of the following hold:\n"
          "- The latest message contains a question, request, or hand-off that\n"
          "  you can uniquely answer based on your role.\n"
          "- You have substantive content to add — not acknowledgment, not\n"
          "  \"I agree\", not \"noted\".\n"
          "- No other participant has already covered the same ground in a\n"
          "  recent reply.\n\n"
          "The founder is a participant; she reads the full thread in the web UI.\n"
          "You do not need to \"keep her informed\" by replying.\n\n"
          "If you are unsure: decline. The thread can always be re-engaged by\n"
          "another message.\n\n"
      )
  ```

- [ ] **Step 4: Run the doctrine tests.**
  ```bash
  uv run pytest tests/test_thread_prompt_doctrine.py -v
  ```
  Expected: all four tests PASS.

- [ ] **Step 5: Run integration tests touching thread runner.**
  ```bash
  uv run pytest tests/ -v -m integration -k thread -x
  ```
  Expected: pre-existing failures from Tasks 2-4 still present. The doctrine change shouldn't introduce new failures.

- [ ] **Step 6: Commit.**
  ```bash
  git add src/daemon/thread_runner.py tests/test_thread_prompt_doctrine.py
  git commit -m "$(cat <<'EOF'
  feat(threads): inject decline-by-default doctrine for REPLY invocations

  Spec §5. Per-invocation prompt gains a Decline-by-Default section
  gated to purpose=REPLY. BOOTSTRAP / CLOSE_OUT / TASK_FOLLOWUP
  invocations remain unchanged — agents in those flows have specific
  obligations and shouldn't see decline framing.
  EOF
  )"
  ```

---

## Task 6: Add `responder_status` to `GET /threads/{id}` response

**Why now:** Spec §9 — the web status strip needs this data. Additive change; no behavior break.

**Files:**
- Modify: `src/infrastructure/database.py` — add `list_invocations_for_thread_grouped_by_seq` helper
- Modify: `src/daemon/routes/threads.py` — extend `get_thread_endpoint` response
- Modify: `src/models.py` — add `ResponderStatusEntry` model
- Create: `tests/test_thread_responder_status.py`

**Steps:**

- [ ] **Step 1: Write a unit test for the join query and API surface.** Create `tests/test_thread_responder_status.py`:

  ```python
  """responder_status field on GET /threads/{id}.

  Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §9
  """
  from __future__ import annotations


  def test_responder_status_present_on_get(client, org_slug, three_agent_thread):
      """Every kind=message in the thread has a responder_status array
      with one entry per non-speaker participant."""
      thread_id = three_agent_thread
      r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
      assert r.status_code == 200
      data = r.json()
      kickoff = data["messages"][0]
      assert kickoff["kind"] == "message"
      statuses = kickoff["responder_status"]
      agents = sorted(s["agent_name"] for s in statuses)
      assert agents == ["alpha", "bravo", "charlie"]
      assert all(s["status"] == "pending" for s in statuses)
      assert all(s["responded_at"] is None for s in statuses)


  def test_responder_status_reflects_replied_state(
      client, org_slug, three_agent_thread, db
  ):
      thread_id = three_agent_thread
      alpha_inv = db.execute(
          "SELECT invocation_token FROM thread_invocations "
          "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
          (thread_id,),
      ).fetchone()
      client.post(
          f"/api/v1/orgs/{org_slug}/threads/{thread_id}/reply",
          json={"invocation_token": alpha_inv["invocation_token"],
                "body_markdown": "alpha responding"},
      )

      r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
      kickoff = r.json()["messages"][0]
      alpha_entry = next(s for s in kickoff["responder_status"] if s["agent_name"] == "alpha")
      assert alpha_entry["status"] == "replied"   # wire-renamed from DB 'consumed'
      assert alpha_entry["responded_at"] is not None


  def test_responder_status_reflects_declined_state(
      client, org_slug, three_agent_thread, db
  ):
      thread_id = three_agent_thread
      alpha_inv = db.execute(
          "SELECT invocation_token FROM thread_invocations "
          "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
          (thread_id,),
      ).fetchone()
      client.post(
          f"/api/v1/orgs/{org_slug}/threads/{thread_id}/decline",
          json={"invocation_token": alpha_inv["invocation_token"]},
      )

      r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
      kickoff = r.json()["messages"][0]
      alpha_entry = next(s for s in kickoff["responder_status"] if s["agent_name"] == "alpha")
      assert alpha_entry["status"] == "declined"
      assert alpha_entry["responded_at"] is not None
  ```

- [ ] **Step 2: Verify the failing test.**
  ```bash
  uv run pytest tests/test_thread_responder_status.py -v
  ```
  Expected: FAIL — `responder_status` key not present in response.

- [ ] **Step 3: Add the DB join helper.** In `src/infrastructure/database.py`, add:

  ```python
  @_synchronized
  def list_invocations_for_thread_grouped_by_seq(
      self, thread_id: str
  ) -> dict[int, list[dict[str, object]]]:
      """Return {triggering_seq: [{agent_name, status, consumed_at}, ...]}
      for every REPLY invocation in this thread.

      Used by GET /threads/{id} to build the per-message responder_status
      strip. Status values are the raw DB values (pending/consumed/declined/
      failed); the route's response builder renames consumed → replied.
      """
      rows = self._conn.execute(
          "SELECT triggering_seq, agent_name, status, consumed_at "
          "FROM thread_invocations "
          "WHERE thread_id = ? AND purpose = 'reply' "
          "ORDER BY triggering_seq, agent_name",
          (thread_id,),
      ).fetchall()
      grouped: dict[int, list[dict[str, object]]] = {}
      for r in rows:
          entry = {
              "agent_name": r["agent_name"],
              "status": r["status"],
              "consumed_at": r["consumed_at"],
          }
          grouped.setdefault(r["triggering_seq"], []).append(entry)
      return grouped
  ```

- [ ] **Step 4: Add the response model.** In `src/models.py`:

  ```python
  class ResponderStatusEntry(BaseModel):
      agent_name: str
      status: Literal["pending", "replied", "declined", "failed"]
      responded_at: str | None
  ```

  (Place near the existing `ThreadMessage` model. Use the existing `Literal`/`BaseModel` imports already at the top of the file.)

- [ ] **Step 5: Extend the GET response.** In `src/daemon/routes/threads.py`, locate `_msg_to_dict` (around line 516) and `get_thread_endpoint` (around line 591).

  Modify `_msg_to_dict` to accept an optional responder list:
  ```python
  def _msg_to_dict(m, responders: list[dict] | None = None) -> dict:
      d = {
          "seq": m.seq,
          "speaker": m.speaker,
          "kind": m.kind.value if hasattr(m.kind, "value") else m.kind,
          "body_markdown": m.body_markdown,
          "addressed_to": m.addressed_to,   # legacy; removed in Task 7
          "decline_reason": m.decline_reason,
          "system_payload": m.system_payload,
          "created_at": m.created_at.isoformat(),
      }
      if responders is not None:
          d["responder_status"] = [
              {
                  "agent_name": e["agent_name"],
                  "status": "replied" if e["status"] == "consumed" else e["status"],
                  "responded_at": e["consumed_at"],
              }
              for e in responders
          ]
      else:
          d["responder_status"] = []
      return d
  ```

  Modify `get_thread_endpoint` to fetch and pass the grouped invocations:
  ```python
  @router.get("/threads/{thread_id}")
  async def get_thread_endpoint(slug: str, thread_id: str, org: OrgDep) -> dict:
      thread = org.db.get_thread(thread_id)
      if thread is None:
          raise HTTPException(status_code=404, detail={"code": "thread_not_found"})
      participants = org.db.list_thread_participants(thread_id)
      messages = org.db.list_thread_messages(thread_id)
      responders_by_seq = org.db.list_invocations_for_thread_grouped_by_seq(thread_id)
      return {
          "thread": _thread_row_to_dict(thread),
          "participants": [p.agent_name for p in participants],
          "messages": [
              _msg_to_dict(m, responders=responders_by_seq.get(m.seq) if m.kind == ThreadMessageKind.MESSAGE else None)
              for m in messages
          ],
      }
  ```

- [ ] **Step 6: Run the responder_status tests.**
  ```bash
  uv run pytest tests/test_thread_responder_status.py -v
  ```
  Expected: all three tests PASS.

- [ ] **Step 7: Regenerate OpenAPI snapshot.**
  ```bash
  HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
  uv run pytest tests/contract/test_openapi_snapshot.py -v
  ```
  Expected: first run regenerates; second confirms snapshot matches.

- [ ] **Step 8: Commit.**
  ```bash
  git add src/infrastructure/database.py src/models.py src/daemon/routes/threads.py tests/test_thread_responder_status.py tests/contract/openapi.json
  git commit -m "$(cat <<'EOF'
  feat(threads): add responder_status to GET /threads/{id}

  Spec §9. Per-message join surfaces the per-participant reply state
  (pending / replied / declined / failed) for the web UI status strip.
  Wire rename consumed → replied happens in _msg_to_dict; the DB stays
  canonical with its existing four status values.
  EOF
  )"
  ```

---

## Task 7: Remove `addressed_to` from bodies, models, helpers, audit shape, and DB write path

**Why now:** With routing flipped (Task 2), turn-accounting fixed (Task 3), decline silent (Task 4), doctrine injected (Task 5), and responder_status in place (Task 6), the `addressed_to` field is fully dead code. Time to delete it.

**Files:**
- Modify: `src/daemon/routes/threads.py` — bodies, helpers, mint loops, GET response
- Modify: `src/infrastructure/audit_logger.py` — `log_thread_message_sent` signature
- Modify: `src/infrastructure/database.py` — `append_thread_message` signature; `ThreadMessage` field
- Modify: `src/models.py` — `ThreadMessage.addressed_to` field

**Steps:**

- [ ] **Step 1: Drop `addressed_to` from all three request bodies.** In `src/daemon/routes/threads.py`:

  Delete the line `addressed_to: list[str] | None = None` from `ComposeBody`, `SendBody`, `ComposeAsAgentBody`. Pydantic v2 default-ignores unknown fields, so callers that still post it get silent acceptance (spec §10 grace).

- [ ] **Step 2: Delete the addressing helpers.** Remove three functions wholesale:

  ```bash
  # Sanity check before deletion: confirm no remaining call sites
  grep -n "_validate_addressed_to\|_resolve_addressed_agents\|_verify_addressed\|FOUNDER_LITERAL\|_maybe_notify_founder_addressed" src/daemon/routes/threads.py
  ```

  All remaining references should be the definitions themselves (no callers). Delete:
  - `_validate_addressed_to` (lines ~86-99)
  - `_resolve_addressed_agents` (lines ~102-105)
  - `_verify_addressed` (line ~672)
  - The `FOUNDER_LITERAL = "@founder"` constant (line ~35)
  - `_maybe_notify_founder_addressed` (lines ~228-245) — Feishu paths cleaned up in Task 8; this caller-side wrapper goes now.

  Also delete any remaining `body.addressed_to or ["@all"]` expressions inside the route bodies — they're dead too. Replace with a direct list-comprehension over `body.recipients` (compose) or `org.db.list_thread_participants(thread_id)` (reply/send).

- [ ] **Step 3: Drop the `addressed_to` arg from `append_thread_message`.** In `src/infrastructure/database.py`, find the method:
  ```bash
  grep -n "def append_thread_message" src/infrastructure/database.py
  ```

  Remove the `addressed_to` parameter and the column from the INSERT statement. **Keep the column in the schema** — old rows must remain readable. The change is "stop writing, keep reading":
  ```sql
  -- Before: INSERT INTO thread_messages (..., addressed_to_json, ...) VALUES (..., ?, ...)
  -- After:  INSERT INTO thread_messages (..., addressed_to_json, ...) VALUES (..., NULL, ...)
  ```

  Or simpler: drop the column entirely from the INSERT column list; SQLite will default it to NULL.

- [ ] **Step 4: Drop the field from `ThreadMessage`.** In `src/models.py`, remove `addressed_to: list[str] | None` from the `ThreadMessage` model.

  Also update the read path in `database.py` — wherever `ThreadMessage(...)` is constructed from a row, stop passing `addressed_to`.

- [ ] **Step 5: Drop `addressed_to` from `log_thread_message_sent`.** In `src/infrastructure/audit_logger.py`:
  ```python
  def log_thread_message_sent(
      self, thread_id: str, *, seq: int, speaker: str, kind: str,
      # addressed_to removed
  ) -> None:
      self.log_event(
          kind="thread_message_sent",
          task_id=thread_id,
          payload={"seq": seq, "speaker": speaker, "kind": kind},
      )
  ```

  Update all call sites (grep first):
  ```bash
  grep -rn "log_thread_message_sent" src/
  ```
  Remove the `addressed_to=` kwarg from each.

- [ ] **Step 6: Drop `addressed_to` from `_msg_to_dict`.** In `src/daemon/routes/threads.py`:

  Remove the `"addressed_to": m.addressed_to,` line (the model no longer has the field; this would AttributeError otherwise).

- [ ] **Step 7: Run unit tests.**
  ```bash
  uv run pytest tests/ -v -x -k "not integration"
  ```
  Expected: tests from Tasks 2-6 still pass. Some pre-existing tests will fail because they still reference `addressed_to` in payloads or assertions — defer to Task 11.

- [ ] **Step 8: Regenerate OpenAPI snapshot.**
  ```bash
  HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
  uv run pytest tests/contract/test_openapi_snapshot.py -v
  ```

- [ ] **Step 9: Commit.**
  ```bash
  git add src/daemon/routes/threads.py src/infrastructure/audit_logger.py src/infrastructure/database.py src/models.py tests/contract/openapi.json
  git commit -m "$(cat <<'EOF'
  refactor(threads): drop addressed_to from bodies, models, audit, write path

  Spec §10. Field is gone from request bodies, ThreadMessage model,
  audit log payload, and the DB write path. The thread_messages.
  addressed_to_json column stays as nullable for backward read compat
  with pre-migration rows; a later cleanup migration drops it.
  FOUNDER_LITERAL, _validate_addressed_to, _resolve_addressed_agents,
  _verify_addressed, _maybe_notify_founder_addressed all deleted —
  zero live call sites remain.
  EOF
  )"
  ```

---

## Task 8: Remove in-thread Feishu paths (outbound notifier + inbound listener branch)

**Why now:** Spec §8. With `_maybe_notify_founder_addressed` already gone (Task 7), the notifier-side `send_thread_addressed` is orphaned. The inbound listener still has a `thread_addressed` reply-routing branch.

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py` — remove `send_thread_addressed`, `send_thread_reply`
- Modify: `src/daemon/feishu_listener.py` — remove `thread_addressed` branch in `_handle_event_async`

**Steps:**

- [ ] **Step 1: Confirm `notify_thread_compose` is independent.** Per spec §8, this stays.
  ```bash
  grep -n "def notify_thread_compose\|notify_thread_compose(" src/infrastructure/feishu/notifier.py src/daemon/
  ```
  Verify it's a separate function with separate call sites unrelated to `send_thread_addressed`.

- [ ] **Step 2: Delete `send_thread_addressed`.** In `src/infrastructure/feishu/notifier.py:401`, remove the entire `send_thread_addressed` method.

- [ ] **Step 3: Delete `send_thread_reply`.** In `src/infrastructure/feishu/notifier.py:25`, remove the entire `send_thread_reply` method. This is the inbound founder-reply-to-card path; the listener branch in the next step is its caller.

- [ ] **Step 4: Find and remove the `thread_addressed` branch in the listener.**
  ```bash
  grep -n "thread_addressed\|_handle_event_async" src/daemon/feishu_listener.py
  ```
  In `_handle_event_async`, locate the branch that routes a founder Feishu reply to `send_thread_reply` (or directly posts to a thread). Remove that branch entirely. The 8-step pipeline stays for `escalation`, `failure`, top-level `dispatch`, and `job_request`.

- [ ] **Step 5: Update audit / `escalation_notifications.kind` handling.** Spec §8: stop writing new rows with `kind='thread_addressed'`. Since `_maybe_notify_founder_addressed` is already deleted, there should be no remaining writer. Verify:
  ```bash
  grep -rn "'thread_addressed'\|\"thread_addressed\"" src/
  ```
  Expected: only references in the listener branch we just deleted, or in old test fixtures (Task 11). If anything else references it, investigate.

- [ ] **Step 6: Run Feishu unit tests.**
  ```bash
  uv run pytest tests/ -v -x -k feishu
  ```
  Expected: tests for `send_thread_addressed` / `send_thread_reply` may fail — those should be deleted (spec §13 explicitly accepts this; no replacement). Tests for `notify_thread_compose` and other notifier surfaces should pass.

  Delete the now-orphaned tests in the same commit.

- [ ] **Step 7: Run integration tests for the Feishu listener.**
  ```bash
  uv run pytest tests/ -v -m integration -k feishu -x
  ```
  Expected: any test that simulated a founder Feishu reply to a thread card will fail (the path no longer exists). Delete those tests in this commit; they're testing a removed feature.

- [ ] **Step 8: Commit.**
  ```bash
  git add src/infrastructure/feishu/notifier.py src/daemon/feishu_listener.py tests/
  git commit -m "$(cat <<'EOF'
  refactor(feishu): remove in-thread push and inbound reply routing

  Spec §8. send_thread_addressed (outbound founder-addressed push) and
  send_thread_reply (inbound founder-reply-to-card path) both deleted.
  Listener's thread_addressed branch removed. notify_thread_compose
  (agent-opens-a-thread heads-up) kept — that surface still serves a
  purpose since the founder needs to learn the thread exists before
  she can read it in the web UI.

  Tests for the deleted surfaces also removed.
  EOF
  )"
  ```

---

## Task 9: Web — drop `addressed_to` from API client + types + mocks

**Why now:** Routes no longer emit or accept the field. Type drift will surface in the next web build.

**Files:**
- Modify: `web/src/lib/api/threads.ts`
- Modify: `web/src/lib/api/types.ts`
- Modify: `web/src/lib/api/threads.test.ts`
- Modify: `web/src/design-system/providers/_mock-threads.ts`
- Modify: `web/src/mocks/messages.ts`

**Steps:**

- [ ] **Step 1: Update `types.ts`.** Find:
  ```bash
  grep -n "addressed_to" web/src/lib/api/types.ts
  ```
  Remove the `addressed_to: string[] | null` field from `ThreadMessage`. Add:
  ```typescript
  export type ResponderStatus = "pending" | "replied" | "declined" | "failed";

  export interface ResponderStatusEntry {
    agent_name: string;
    status: ResponderStatus;
    responded_at: string | null;
  }
  ```
  Add `responder_status: ResponderStatusEntry[]` to `ThreadMessage`.

- [ ] **Step 2: Update `threads.ts`.** Drop the `addressed_to?: string[]` param from `sendThreadFollowUp` and remove it from the request body construction:
  ```typescript
  export async function sendThreadFollowUp(args: {
    org: string;
    threadId: string;
    body_markdown: string;
  }): Promise<...> {
    return api.post(`/api/v1/orgs/${args.org}/threads/${args.threadId}/send`, {
      body_markdown: args.body_markdown,
    });
  }
  ```

  Repeat for `composeThread` (drop `addressed_to` if present).

- [ ] **Step 3: Update mocks.** In `_mock-threads.ts` and `mocks/messages.ts`, remove every `addressed_to: [...]` line from sample data. Add `responder_status: []` (empty is fine for mocks; specific test fixtures can populate as needed).

- [ ] **Step 4: Run web tests.**
  ```bash
  cd web && npm test -- --run
  ```
  Expected: any test that called `sendThreadFollowUp({ addressed_to: [...] })` or asserted on `message.addressed_to` will fail. Update those in this commit — they were testing the removed feature; the right replacement is an assertion on `responder_status` or just deletion.

- [ ] **Step 5: Type-check.**
  ```bash
  cd web && npm run typecheck
  ```
  Expected: PASS.

- [ ] **Step 6: Build.**
  ```bash
  cd web && npm run build
  ```
  Expected: PASS.

- [ ] **Step 7: Commit.**
  ```bash
  cd /Users/tangbz/projects/my-opc/.claude/worktrees/worktree-thread-broadcast-only
  git add web/src/lib/api/ web/src/design-system/providers/_mock-threads.ts web/src/mocks/messages.ts web/src/lib/api/threads.test.ts
  git commit -m "$(cat <<'EOF'
  refactor(web): drop addressed_to from API client and mocks

  Spec §10 + §9. ThreadMessage no longer carries addressed_to; the new
  responder_status array surfaces per-participant reply state for the
  status-strip component (added in next task). sendThreadFollowUp loses
  its addressed_to param.
  EOF
  )"
  ```

---

## Task 10: Web — remove composer picker + badges, add ResponderStatusStrip

**Why now:** UI surface change matching the new wire shape. The status strip is the load-bearing addition (spec §9: without it silent declines are invisible).

**Files:**
- Modify: `web/src/features/threads/ThreadsPage.tsx`
- Modify: `web/src/features/threads/MessageCard.tsx` (if separate; otherwise modify within `ThreadsPage.tsx`)
- Create: `web/src/features/threads/ResponderStatusStrip.tsx`
- Create: `web/src/features/threads/ResponderStatusStrip.test.tsx`

**Steps:**

- [ ] **Step 1: Create the status-strip component.** `web/src/features/threads/ResponderStatusStrip.tsx`:

  ```tsx
  import { ResponderStatusEntry } from "@/lib/api/types";

  export function ResponderStatusStrip({ statuses }: { statuses: ResponderStatusEntry[] }) {
    if (statuses.length === 0) return null;
    return (
      <div className="text-xs text-neutral-500 mt-1 flex flex-wrap gap-x-3">
        {statuses.map((s) => (
          <span key={s.agent_name}>
            <span className="font-medium">{s.agent_name}</span>:{" "}
            <span className={statusClass(s.status)}>{statusLabel(s.status)}</span>
          </span>
        ))}
      </div>
    );
  }

  function statusLabel(s: ResponderStatusEntry["status"]): string {
    switch (s) {
      case "pending":  return "pending…";
      case "replied":  return "replied";
      case "declined": return "declined";
      case "failed":   return "failed";
    }
  }

  function statusClass(s: ResponderStatusEntry["status"]): string {
    switch (s) {
      case "pending":  return "text-neutral-400";
      case "replied":  return "text-emerald-600";
      case "declined": return "text-neutral-500";
      case "failed":   return "text-amber-600";
    }
  }
  ```

- [ ] **Step 2: Write a test for the component.** `web/src/features/threads/ResponderStatusStrip.test.tsx`:

  ```tsx
  import { render, screen } from "@testing-library/react";
  import { describe, it, expect } from "vitest";
  import { ResponderStatusStrip } from "./ResponderStatusStrip";

  describe("ResponderStatusStrip", () => {
    it("renders empty when no statuses", () => {
      const { container } = render(<ResponderStatusStrip statuses={[]} />);
      expect(container.firstChild).toBeNull();
    });

    it("renders one row per participant with status label", () => {
      render(
        <ResponderStatusStrip
          statuses={[
            { agent_name: "alpha", status: "pending", responded_at: null },
            { agent_name: "bravo", status: "replied", responded_at: "2026-05-30T10:00:00Z" },
            { agent_name: "charlie", status: "declined", responded_at: "2026-05-30T10:01:00Z" },
          ]}
        />,
      );
      expect(screen.getByText("alpha")).toBeInTheDocument();
      expect(screen.getByText("pending…")).toBeInTheDocument();
      expect(screen.getByText("replied")).toBeInTheDocument();
      expect(screen.getByText("declined")).toBeInTheDocument();
    });
  });
  ```

- [ ] **Step 3: Verify the test passes.**
  ```bash
  cd web && npm test -- --run ResponderStatusStrip
  ```
  Expected: PASS.

- [ ] **Step 4: Remove the addressing picker from ThreadsPage.** Find:
  ```bash
  grep -n "addressedTo\|addressed_to\|@all" web/src/features/threads/
  ```
  In `ThreadsPage.tsx`:
  - Delete the `addressedTo` state hook and its setter.
  - Delete the picker UI (likely a `<Select>` or chip-list near the body textarea).
  - Change `onSendFollowUp.mutateAsync({ body_markdown: markdown, addressed_to: addressedTo })` to `onSendFollowUp.mutateAsync({ body_markdown: markdown })`.
  - Delete `addressedTo` references entirely.

- [ ] **Step 5: Remove the badge from MessageCard.** Find the line that renders `m.addressed_to` (probably as `<Badge>To: @all</Badge>` or similar). Delete it.

- [ ] **Step 6: Wire the status strip into MessageCard.** Inside the message card, below the body, render:
  ```tsx
  {m.kind === "message" && <ResponderStatusStrip statuses={m.responder_status ?? []} />}
  ```

- [ ] **Step 7: Run web tests.**
  ```bash
  cd web && npm test -- --run
  ```
  Expected: PASS (any prior assertions about address-picker UI need updating; do that here).

- [ ] **Step 8: Build.**
  ```bash
  cd web && npm run build
  ```
  Expected: PASS.

- [ ] **Step 9: Manual visual check.** Start the daemon and the dev server:
  ```bash
  # Terminal 1 (from the worktree)
  scripts/daemon.sh restart
  # Terminal 2
  cd web && npm run dev
  ```
  Open the threads view on an existing thread (or create a small fresh one). Confirm:
  - Composer has no addressing picker.
  - Messages have no "To:" badges.
  - A status strip appears under each `kind=message` row.

  Take a screenshot at `/tmp/threads-broadcast-ui.png` for the PR description.

- [ ] **Step 10: Commit.**
  ```bash
  git add web/src/features/threads/
  git commit -m "$(cat <<'EOF'
  feat(web): broadcast UI — remove addressing picker, add status strip

  Spec §9. ThreadsPage composer drops the addressed_to picker; MessageCard
  drops the recipient badge. New ResponderStatusStrip component renders
  per-participant reply state (pending / replied / declined / failed)
  below every kind=message row, so silent declines become visible at a
  glance instead of looking like the system is stuck.
  EOF
  )"
  ```

---

## Task 11: Update integration test fixtures + sweep stale assertions

**Why now:** Tasks 2-4 left a pile of broken pre-existing tests. Fix them all in one sweep so the test suite is green end-to-end before docs cleanup.

**Files:**
- Modify: every `tests/integration/test_threads_*.py` and any `tests/test_threads_*.py` that asserts on selective addressing or per-invocation turn accounting
- Modify: `src/cli.py` — drop the hardcoded `addressed_to` (this should be free)

**Steps:**

- [ ] **Step 1: List failing tests.**
  ```bash
  uv run pytest tests/ -v -m "" 2>&1 | grep -E "FAILED|ERROR" | tee /tmp/threads-failing-tests.txt
  ```

- [ ] **Step 2: Walk the list and update each test.** For each failing test, the typical fix is one of:
  - **Remove `addressed_to` from request payload** — the field is gone; tests that posted it now violate Pydantic ignore-extras-by-default? They don't; Pydantic ignores them silently. But the cleanup pass should still remove the dead key.
  - **Update assertions on minted invocations** — old: "only the addressed agent has a pending invocation"; new: "every non-speaker participant has a pending invocation".
  - **Update assertions on `turns_used`** — old: incremented by `len(addressed)`; new: incremented by 1 per message.
  - **Update assertions on decline transcript row** — old: expects a `kind='decline'` row; new: no row, status-only change. Reach into `thread_invocations.status` instead.
  - **Delete tests for `_verify_addressed`** — that helper is gone; the eligibility constraint it enforced (speaker-in-addressed-or-@all) doesn't exist anymore. Replace with "any participant holding a valid invocation token can reply".

- [ ] **Step 3: Drop the hardcoded `addressed_to` in CLI compose.** In `src/cli.py` find the line `"addressed_to": ["@all"]` (around line 1999 per the inventory) and delete the key. Also delete any `--to` / `--addressed-to` CLI flags that may have been added for symmetry (grep `--to` carefully — `--to-agents` etc. — and remove only the addressing ones).

- [ ] **Step 4: Run the full test suite.**
  ```bash
  uv run pytest tests/ -v -m "" -x
  ```
  Expected: PASS. If anything fails, iterate.

- [ ] **Step 5: Commit.**
  ```bash
  git add tests/ src/cli.py
  git commit -m "$(cat <<'EOF'
  test(threads): update fixtures + assertions for broadcast model

  Drop addressed_to from all test payloads. Update mint-loop assertions
  from selective to broadcast semantics. Update turn-accounting
  assertions to per-message. Update decline assertions to status-only
  (no transcript row). Delete tests covering removed surfaces
  (_verify_addressed, send_thread_addressed, send_thread_reply).
  CLI compose no longer hardcodes addressed_to.
  EOF
  )"
  ```

---

## Task 12: Docs sweep — CLAUDE.md, thread SKILL.md, prior-spec pointer

**Why now:** Code is settled. Docs need to match.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `protocol/skills/thread/SKILL.md`
- Modify: `docs/superpowers/specs/2026-05-13-threads-design.md`

**Steps:**

- [ ] **Step 1: Update CLAUDE.md threads section.** Find:
  ```bash
  grep -n "addressed_to\|Thread\|threads" CLAUDE.md | head -30
  ```
  In the "Threads foundation" entry (Done item 12) and any thread-related invariants:
  - Remove references to `addressed_to`, `@all`, `@founder` address tokens, selective routing.
  - Add a one-line summary of the new broadcast model: "Every message broadcasts a REPLY invocation to every participant except the speaker; agents triage via decline-by-default doctrine injected into the REPLY invocation prompt; declines are silent (no transcript row, no turn increment)."
  - In the "Feishu notifications" section, remove `thread_addressed` from the enumerated kinds; keep `notify_thread_compose` as the sole thread-related Feishu surface.

- [ ] **Step 2: Update the thread skill file.**
  ```bash
  cat protocol/skills/thread/SKILL.md | head -50
  ```
  - Remove any `to:` field guidance in the payload examples.
  - Add a pointer near the top: "Decision rule for reply-vs-decline lives in the thread invocation prompt (see Decline-by-Default section), not in this skill. This skill covers the operational mechanics."
  - Update all sample reply payloads to omit `addressed_to`.

- [ ] **Step 3: Append supersession pointer to the original threads spec.** At the top of `docs/superpowers/specs/2026-05-13-threads-design.md`, under the existing header:

  ```markdown
  > **Addressing model superseded** by `2026-05-30-thread-broadcast-only-design.md`.
  > The `addressed_to` field, the `@all` / `@founder` tokens, the `_verify_addressed`
  > reply-eligibility check, and the in-thread Feishu founder push are removed.
  > Threads now broadcast every message to all participants; declines are silent.
  > See the new spec for the current routing model.
  ```

- [ ] **Step 4: Run the full test suite one final time.**
  ```bash
  uv run pytest tests/ -v -m "" -x
  cd web && npm test -- --run && npm run build
  ```
  Expected: PASS / PASS / PASS.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/tangbz/projects/my-opc/.claude/worktrees/worktree-thread-broadcast-only
  git add CLAUDE.md protocol/skills/thread/SKILL.md docs/superpowers/specs/2026-05-13-threads-design.md
  git commit -m "$(cat <<'EOF'
  docs(threads): broadcast-only model — CLAUDE.md, skill, spec pointer

  CLAUDE.md threads section reflects the new broadcast model and
  removes references to addressed_to, @all, @founder, thread_addressed
  Feishu kind. Thread SKILL.md points readers at the invocation-prompt
  doctrine for reply-vs-decline judgment and drops the 'to:' payload
  field. Original threads design spec (2026-05-13) gets a supersession
  pointer at the top.
  EOF
  )"
  ```

---

## Final Verification

- [ ] **Run the full test suite.**
  ```bash
  uv run pytest tests/ -v -m "" -x
  ```
  Expected: PASS.

- [ ] **Run the web suite end-to-end.**
  ```bash
  cd web && npm test -- --run && npm run typecheck && npm run build
  ```
  Expected: all PASS.

- [ ] **Smoke-test the daemon + UI together.**
  ```bash
  scripts/daemon.sh restart
  happyranch web
  ```
  Compose a fresh 3-agent thread, send a message, observe that:
  - All three agents get pinged (check `happyranch threads show THR-NNN`).
  - The status strip in the web UI shows three "pending" entries under your message.
  - Trigger a manual decline via `curl` if needed; watch the strip flip to "declined".

- [ ] **Final commit (any leftover docs / cleanup).** If the verification surfaced any small fixes, commit them with `chore: post-implementation cleanup`.

- [ ] **Push the branch.**
  ```bash
  git push -u origin worktree-worktree-thread-broadcast-only
  ```

- [ ] **Open the PR.**
  ```bash
  gh pr create --title "feat(threads): broadcast-only routing, remove addressed_to" --body "$(cat <<'EOF'
  ## Summary
  - Remove `addressed_to` from threads entirely; every message broadcasts a REPLY invocation to every participant except the speaker
  - Silent decline (no transcript row, no turn increment); decline reason persisted to invocation row only
  - Decline-by-default doctrine injected into thread invocation prompt for REPLY only
  - `turns_used` increments per message, not per invocation; projection simplifies to `+1`
  - Remove in-thread Feishu push paths (`send_thread_addressed`, `send_thread_reply`, listener `thread_addressed` branch); keep `notify_thread_compose`
  - Web: drop addressing picker + badges; add per-message `ResponderStatusStrip` so silent declines are visible

  Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`
  Origin: THR-011 silent hand-off bug (finance_agent's prose @-mention of admin_head produced no invocation)

  ## Test plan
  - [x] Unit tests (`uv run pytest tests/ -v -x`)
  - [x] Integration tests (`uv run pytest tests/ -v -m integration -x`)
  - [x] Web tests + typecheck + build (`cd web && npm test -- --run && npm run typecheck && npm run build`)
  - [x] Manual: composed a 3-agent thread, confirmed broadcast + status strip
  EOF
  )"
  ```

---

## Self-Review Checklist (run after writing this plan)

**Spec coverage:**
- [x] §1 Goal — Task 2 (broadcast mint) + Task 4 (silent decline) + Task 5 (doctrine) + Task 8 (Feishu) cover the goal.
- [x] §2 Motivation — addressed in tests + commit messages.
- [x] §3 Non-goals — column kept (Task 7 Step 3), no body @-mention parsing (not in any task), no `expected_responders` hint (not in any task).
- [x] §4 The rule — Task 2.
- [x] §5 Agent doctrine — Task 5.
- [x] §6 Decline mechanics — Task 4.
- [x] §7 Turn-cap accounting — Task 3.
- [x] §8 Feishu — Task 8.
- [x] §9 Web UI — Tasks 9, 10.
- [x] §10 Wire/API — Task 7 (bodies, models, helpers, audit) + Task 6 (responder_status added) + Task 9 (web types).
- [x] §11 CLI — Task 11 Step 3.
- [x] §12 Migration — Task 7 Step 3 (DB schema kept), Task 11 (test fixtures), Task 7 Step 8 (OpenAPI regen).
- [x] §13 Tradeoffs — documented in commits.
- [x] §14 Files touched — match the task file lists.
- [x] §15 Out-of-spec follow-ups — explicitly out of scope; no tasks needed.

**Placeholder scan:** No TBDs / TODOs / "implement later" markers found. Specific helper signatures, fixture imports flagged as "adapt to actual conftest" but with explicit fallback guidance ("crib from neighboring tests").

**Type consistency:** `ResponderStatusEntry` defined identically in Python (Task 6) and TS (Task 9). Status values `pending|replied|declined|failed` consistent across DB doc, Python model, TS type, and React component. `DOCTRINE_HEADER` constant in tests matches the literal `## Decline-by-Default in Threads` in `_decline_by_default_doctrine()`.
