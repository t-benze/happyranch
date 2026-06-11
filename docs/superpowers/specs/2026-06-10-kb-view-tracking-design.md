# KB View Tracking (Agent-CLI Reads) Design

> Status: implemented
> Current Source: runtime/daemon/routes/kb.py, runtime/infrastructure/database.py, cli/commands/kb.py, tests/contract/openapi.json
> Notes: Founder ruling THR-009. Caller-signal mechanism documented in KB `kb-view-tracking-caller-signal`. Supersedes the failed first attempt (TASK-064).

**Date:** 2026-06-10

## Problem

We want to learn which shared-KB entries are actually useful, scoped to
**agent consult reads only** (founder decision, THR-009). The natural place to
count is the entry read at `GET /api/v1/orgs/{slug}/kb/{entry_slug}` (`get_kb`).
That read is also served to the founder-facing web SPA, and the daemon has a
single shared bearer token that encodes no per-caller identity — so a route
handler cannot tell an agent-CLI read apart from a web read by default.

The first attempt (TASK-064) failed because it tried to infer the caller
implicitly. The corrected mechanism is an explicit surface-tag header.

## Decision

1. **Caller signal (source label, not auth).** The agent CLI client
   (`OpcClient`) adds a descriptive request header `X-HappyRanch-Surface: cli`
   to every request. The web SPA never sends it. A route counts a view only
   when that header is present. The header is a source *label* — authz is never
   gated on it. See KB `kb-view-tracking-caller-signal` for the full rationale
   and rejected alternatives (User-Agent sniffing, per-agent bearer identity).

2. **Count rule.** `get_kb` reads `X-HappyRanch-Surface` and records a view
   only when it equals `cli` **and** the entry was read successfully. The record
   happens after the successful read, never on the 404 path. Web reads (header
   absent) and 404s do not increment.

3. **Store (additive).** A new `kb_views` table holds the tally — it does not
   live in the entry markdown and does not route through `audit_log` (which
   overloads `task_id`). Columns: `slug TEXT PRIMARY KEY`, `view_count INTEGER
   NOT NULL DEFAULT 0`, `last_viewed_at TEXT`. `Database.record_kb_view(slug)`
   UPSERTs (insert at 1, else increment + restamp `last_viewed_at`).
   `Database.kb_view_stats()` returns rows ordered by `view_count DESC`, then
   `last_viewed_at DESC`.

4. **Failure isolation.** The `record_kb_view` call in `get_kb` is wrapped in
   try/except and logs-and-continues (mirrors the non-fatal `regenerate_index`
   pattern). A tracking-write failure never 500s the read; the 200 body always
   returns.

5. **Read surface.** `happyranch kb stats` (CLI) is backed by a new daemon
   endpoint `GET /api/v1/orgs/{slug}/kb/stats`, returning entries by view count
   descending with `last_viewed_at`. The route is declared **before**
   `/kb/{entry_slug}` so the literal path is not captured by the slug path
   param (same ordering reason as `/kb/search`). The stats endpoint is a pure
   read of the tally and never increments; it is an agent-CLI surface and is not
   wired into the SPA.

## Out Of Scope

- Per-agent attribution (which agent read what). The shared token cannot carry
  it; that is a larger change to the auth surface — see
  `kb-view-tracking-caller-signal`.
- A founder web panel for view stats. The metric is CLI-only for now.
