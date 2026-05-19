# Web — Talks surface (PR 11) detail spec

**Tracks:** GitHub issue #21 (PR 11 in umbrella `2026-05-18-web-app-complete-feature-set-design.md` §6.5).

**Convention:** Companion to `web/ARCHITECTURE.md` (layers), `web/UI_SPEC.md` (UX), `web/DESIGN_SYSTEM.md` (tokens/primitives). Same shape as the implicit detail specs that landed Tasks (PR 7).

---

## 1. Goal

Founder-facing read + lifecycle surface for **talks** — 1:1 founder↔agent conversations. Mirrors the existing CLI surface (`grassland talk {start,resume,abandon,end,list,show}`) and adds the in-talk `dispatch` action. Reuses the threads `MessageBubble` for the transcript view.

## 2. Non-goals

- No SSE for talks (transcript is materialized only at `end`; polling 60s on the detail view is sufficient).
- No editing of transcripts from the UI. The `end` dialog accepts a markdown transcript verbatim (the founder pastes from their CLI session); the UI never re-parses or re-flows it.
- No talk-resume from the UI in v1 (CLI `grassland talk resume` is the supported path for re-attaching to the agent session).
- No new design-system patterns. `TalkTranscript` is a thin transform from `TalkRecord.transcript` into a `MessageBubble` list and ships inside the feature folder.

## 3. Wire contract (current daemon shape, not what `talks.ts` says today)

Source of truth: `src/daemon/routes/talks.py::_talk_to_dict`. **The current `web/src/lib/api/types.ts::TalkRecord` is wrong** — it claims `agent`, `abandoned_at`, `reason`, none of which the daemon returns. Fixing it is part of this PR.

### TalkRecord wire shape

```jsonc
{
  "talk_id": "TALK-0001",
  "agent_name": "engineering_head",
  "status": "open" | "closed" | "abandoned",
  "started_at": "2026-05-18T10:00:00Z",
  "ended_at": "2026-05-18T11:12:00Z",   // null while open or abandoned
  "summary": "…",                         // null until end_talk
  "topic_list": ["…"],                    // [] until end_talk
  "new_learnings_count": 3,               // 0 until end_talk
  "new_kb_slugs": ["…"],                  // [] until end_talk
  "transcript_path": "/.../TALK-0001.md", // null until end_talk
  "transcript": "…full markdown body…"    // present on GET detail when ≤256 KiB AND status=closed
}
```

### Endpoint payloads

| Verb + path | Body | Notes |
|---|---|---|
| `POST /talks` | `{agent_name: string}` | 409 `talk_already_open` carries `prior_open_talk_id`, `prior_started_at`. |
| `GET /talks` | query: `agent?`, `status?`, `limit?` (default 50) | Returns `{talks: TalkRecord[]}`. |
| `GET /talks/{id}` | — | Returns `TalkRecord` with optional `transcript` field. |
| `POST /talks/{id}/abandon` | `{reason: string}` (required, not blank) | 400 `talk_not_open` if not OPEN. |
| `POST /talks/{id}/end` | `{summary, topic_list?, transcript_markdown, learnings?, kb_slugs?}` | All fields validated; 400 `unknown_kb_slug` for bad slug. |
| `POST /talks/{id}/dispatch` | `{brief, target_agent?, team?}` | 422 `empty_brief`. 403 `cross_team_dispatch_forbidden`, etc. |
| `POST /talks/{id}/resume` | — | **Not used by the web UI in v1** (CLI only). |

The `EndTalkBody.learnings` field is `[{text: string}, ...]`. KB slugs must already exist (founder uses `grassland kb add` first).

## 4. Information architecture

Mirrors threads:

```
+--------------------------+-----------------------------------------+
| Inbox (340 px)           | Detail                                  |
|--------------------------|-----------------------------------------|
| status tabs              | ThreadHeader-style row (talk_id, agent, |
|   [open|closed|abandoned]|   status, started_at, summary if any)   |
| filter input             | actions: End / Abandon / Dispatch       |
|                          | ───── (only enabled when status=open)   |
| InboxRow list            |                                         |
|   subject = agent name   | Transcript area                         |
|   meta    = status time  |   • status=open: empty-state             |
|                          |     "Talk is open. Use grassland talk    |
|   active = current id    |      resume to converse, then End to     |
|                          |      record this transcript."            |
|                          |   • status=closed: transcript markdown   |
|                          |     rendered via MessageBubble per line  |
|                          |     "section" (see §5).                  |
|                          |   • status=abandoned: empty-state        |
+--------------------------+-----------------------------------------+
```

The footer empty-state copy varies by status. No composer (the founder converses via CLI; the UI only records the outcome).

## 5. Transcript rendering

`TalkRecord.transcript` is a free-form markdown blob with no enforced structure. The simplest faithful rendering is a single `MessageBubble` with `variant="system"` carrying the full markdown body — that gets us the dark/light theming and the spacing for free, without parsing speakers we can't reliably identify.

A more useful pass: when the transcript starts with `## founder` or `## agent` headings (or `**founder:**` lines) we split on those markers and emit per-speaker bubbles. Implementation lives in `features/talks/TalkTranscript.tsx` (pure function). Worst case (unrecognized format), we fall back to the system-bubble. No design-system change.

## 6. Dialogs

### 6.1 StartTalkDialog

- Inputs: agent name (combobox over `useAgentsList()`).
- Submits `POST /talks { agent_name }`.
- 409 → human message "An open talk with X already exists" + a "Go to open talk" button that navigates to that ID.
- On success: navigate to the new talk's detail.

### 6.2 EndTalkDialog

- Inputs: summary (single-line), transcript markdown (multiline textarea, autosized), optional topic_list (CSV string), optional learnings (one-per-line textarea — each line becomes `{text: line}`), optional kb_slugs (CSV).
- Submits `POST /talks/{id}/end { summary, topic_list, transcript_markdown, learnings, kb_slugs }`.
- Error mapping: `unknown_kb_slug` → "KB slug ‹X› doesn't exist. Add it with `grassland kb add` first."
- Confirm-step: the dialog has a single primary button; no extra confirmation. End is reversible in practice (the talk just becomes a closed record with whatever the founder typed).

### 6.3 AbandonTalkDialog

- Inputs: reason (required textarea).
- Submits `POST /talks/{id}/abandon { reason }`.

### 6.4 DispatchFromTalkDialog

- Inputs: brief (required textarea), optional target_agent (combobox), optional team (text — rarely used).
- Submits `POST /talks/{id}/dispatch { brief, target_agent?, team? }`.
- Error mapping for the most common codes: `empty_brief`, `cross_team_dispatch_forbidden`, `worker_must_self_dispatch`, `target_not_in_team`, `unknown_agent`, `teams_registry_unavailable`.
- On success: toast `Dispatched <task_id>` with a deep-link to `/orgs/:slug/tasks/:task_id`.

## 7. Data flow

| Hook | Source | Polling |
|---|---|---|
| `useTalksList({status})` | `GET /talks?status=&limit=50` | `refetchInterval: 60_000` |
| `useTalk(id)` | `GET /talks/{id}` | `refetchInterval: 60_000` while status=open; `Infinity` otherwise |
| `useStartTalk()` | `POST /talks` mutation | — |
| `useAbandonTalk(id)` | `POST /talks/{id}/abandon` | — |
| `useEndTalk(id)` | `POST /talks/{id}/end` | — |
| `useDispatchFromTalk(id)` | `POST /talks/{id}/dispatch` | — |

All mutations invalidate `['talks', slug, …]` and `['talk', slug, id]` on success.

## 8. Routes + nav

- `/orgs/:slug/talks` — list view (no active talk → EmptyState in the right pane).
- `/orgs/:slug/talks/:talk_id` — detail view.
- TopBar gains a `Talks` nav tab between `KB` and `Audit`. Jump-key `g l` (matching `web/UI_SPEC.md` §0 / §12 keyboard map).

## 9. Architecture-rule compliance

- `lib/api/talks.ts` (already exists) becomes the single source of truth for daemon mirroring. Fixes types + signatures to match wire.
- `DataContext` gains `TalksApi` + `useTalksRoutes`. `AppProvider` wires the real implementation. `PrototypeProvider` wires a no-op mock (no talks sandbox exists yet — every hook returns `isLoading: false, data: {talks: []}`).
- `hooks/talks.ts` exports one-liners that read from `useData().talks`.
- Feature folder `features/talks/` owns the page, dialogs, transcript transform, and strings. It imports only from `@/lib/`, `@/design-system/`, `@/hooks/`.

## 10. Testing

- Vitest unit tests:
  - `features/talks/TalkTranscript.test.tsx` — speaker-split heuristic vs. fallback bubble.
  - `features/talks/TalksPage.test.tsx` — read path: list renders, detail renders both open and closed transcripts, empty state when no slug.
  - `features/talks/write-path.test.tsx` — MSW intercepts each lifecycle endpoint and asserts the outgoing JSON.
- Contract test: `tests/contract/test_openapi_snapshot.py` is unchanged (all talks routes already covered by the snapshot and the TS coverage test).

## 11. Ops

- No new env vars. No daemon changes.
- Bundle size impact: +~3 KB gzipped (one page + four dialogs + a transcript transform; no new design-system patterns).
- Build: `scripts/build_web.sh` continues to work — Vite picks up the new file tree automatically.

## 12. Open questions (intentionally deferred)

- Compact-mode density audit for the transcript area: deferred to PR 13 (umbrella §6.7).
- Light-theme color sweep across `MessageBubble` reused in talks: same — PR 13.
- Resume-from-UI: deferred. The CLI session is the only place where the founder can actually converse with the agent, so the web "Resume" button would only be a navigation, not a session attach. Not worth the surface in v1.
- Forward-talk-into-thread parity with threads: out of scope. Threads already accept `forwarded_from_kind: 'talk'`, so a future enhancement can add a Forward button here.
