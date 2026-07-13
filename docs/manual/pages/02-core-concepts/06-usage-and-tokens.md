# Usage & Tokens

**Purpose:** Understand what HappyRanch measures when agents work.

## Tokens, Not Dollars

HappyRanch tracks token usage. It does not show a real-dollar cost meter.

That is intentional for v1: actual dollar cost depends on the executor, model,
provider plan, and pricing outside HappyRanch. Use the Usage surface to see
relative consumption and spot runaway work, not to read a billing statement.

## Where You See It

- **Web:** `/orgs/:slug/usage`
- **CLI:** `happyranch tokens`

![placeholder: Usage surface showing token totals by agent](TODO)

The old `/spend` path redirects to `/usage`.

## How to Read It

Usage helps answer:

- Which agents are consuming the most tokens?
- Did a task or week spike unexpectedly?
- Is one team's work materially heavier than another's?

It does not answer:

- What was my exact bill?
- Which provider charged which amount?
- Whether a model was worth the cost in dollars.

## Grounded Technical Facts

- Usage route: `/orgs/:slug/usage`.
- `/spend` redirects to `/usage`.
- CLI: `happyranch tokens [--agent <name>]`.
- There is no real-dollar cost meter in the current product.
