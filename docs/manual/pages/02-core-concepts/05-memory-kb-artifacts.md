# Memory, KB & Artifacts

**Purpose:** Know where HappyRanch stores agent notes, shared knowledge, and
files.

These three stores solve different problems. Mixing them up is the fastest way
to lose track of work.

## Memory

Memory is private to one agent. It holds that agent's operational learnings and
preferences across sessions.

You rarely manage memory directly as the founder-operator. It exists so an
agent can remember how it works without turning every note into shared policy.

## Knowledge Base

The KB is shared, durable knowledge for the whole org: founder rulings,
confirmed policies, partner/API quirks, reusable procedures, and decisions that
future agents should not re-litigate.

Use the KB for knowledge that should outlive one task.

- **Web:** `/orgs/:slug/kb`
- **CLI:** `happyranch kb ...`

## Artifacts

Artifacts are files: reports, exports, screenshots, PDFs, images, generated
patches, and other blobs a task produces.

Artifacts are where you retrieve file outputs from completed work.

- **Web:** `/orgs/:slug/artifacts`
- **CLI:** `happyranch artifacts list`, `happyranch artifacts get <name> --output <path>`

![placeholder: Knowledge and Artifacts surfaces showing a KB entry and a generated report](TODO)

## The Rule

| Store | Scope | Put this there |
|---|---|---|
| Memory | One agent | Private operational notes |
| KB | Whole org | Durable shared knowledge and rulings |
| Artifacts | Whole org | Files and generated deliverables |

If it is a file, look in Artifacts. If it is a durable rule or decision, look in
the KB. If it is an agent's private working note, it belongs in Memory.

## Grounded Technical Facts

- KB routes: `/orgs/:slug/kb`, `/orgs/:slug/kb/*`.
- Artifacts route: `/orgs/:slug/artifacts`.
- CLI command groups: `memory ...`, `kb ...`, and `artifacts ...`.
