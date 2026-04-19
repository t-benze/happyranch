# Knowledge Base Guideline

Authoritative reference for what goes in `<runtime>/kb/`, how to write entries,
and how deletion works. Agents read this on demand; `CLAUDE.md` points at it.
Rules here are org policy — they apply equally to every agent.

## What belongs

Two content types, both with a 12+ month expected useful lifespan:

- **Precedents.** Resolved escalations, founder decisions, incident post-mortems.
- **Domain reference.** SOPs, regulatory rules, partner-API quirks, visa
  requirements, payment-flow details, refund policy details.

Contribute when any is true:

- You found a factual rule other agents would need (rate limit, regulatory
  deadline, partner contract term).
- You consulted the KB and an entry was wrong or outdated — update it.
- You made a non-trivial procedural decision worth preserving as a mini-SOP
  (not a one-off workaround).

## What does not belong

- **Agent-private operational learnings** → `learnings.md` in your workspace.
- **Task progress notes or drafts** → `artifacts/<task_id>/`.
- **Mirrors of `protocol/` docs.** The authoritative copy is in `protocol/`;
  the KB complements, never duplicates.
- **Fast-changing state** (prices, current partner list, promotions).
- **Anything specific to a single task** with no cross-agent value.

## Entry shape

YAML frontmatter + markdown body. Required fields: `slug`, `title`, `type`,
`topic`. Optional: `tags`, `source_task`, `supersedes`. Server-stamped
(do not set manually): `authored_by`, `authored_at`, `updated_by`, `updated_at`.

`slug` is kebab-case ASCII (`^[a-z0-9][a-z0-9-]{0,63}$`). Filename is
`<slug>.md`; the slug and filename are the canonical identity.

`type` is exactly one of `reference` or `precedent`. `topic` is a single
kebab-case token (e.g. `visa`, `payment`, `partner-api`, `engineering`) —
free-form, used for grouping in `_index.md`.

Body is markdown, ≤32 KiB.

## Collisions

On `add` the daemon runs two checks inside a write lock:

1. **`slug_exists`** — a file with that slug already exists. Use
   `opc kb update` instead, or pick a different slug.
2. **`near_duplicate`** — title similarity >0.70 or ≥2 shared tags. Response
   lists candidate slugs. Either update the existing entry (strongly preferred)
   or pass `--force-new-sibling` if the topic is genuinely distinct.

Default to update. `--force-new-sibling` is intended for genuinely distinct
scopes (e.g. "Alipay refund" vs. "WeChat Pay refund") that happen to share
vocabulary.

## Edit etiquette

- Stamp your agent via `--agent <you>`; the daemon records it as `updated_by`.
- Preserve prior factual content unless it is wrong. Add; do not silently
  rewrite.
- Use `supersedes: <old-slug>` to explicitly deprecate rather than delete.

## Deletion

- **Only the Engineering Head can delete.** Others get a 403.
- `--confirm` is mandatory.
- **Deletion is irreversible** unless the runtime dir is git-initialized.
  The founder can force-delete via `--as-founder` but the guideline is to
  supersede instead.

## Hands-on

Read the full CLI surface with `opc kb --help`. The `start-task` skill
documents when to consult and when to contribute.
