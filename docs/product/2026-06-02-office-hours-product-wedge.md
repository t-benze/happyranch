# Office Hours — Product Wedge (2026-06-02)

> Source: gstack `/office-hours` (Startup mode, pre-product diagnostic).
> Founder: solo operator running a Macau/HK tourism website for foreign tourists,
> single-handedly (build + content + marketing). HappyRanch is the tool to clone himself.
> This is a strategy/product note, not a spec. It records what to keep, what to cut, and the next action.

## The one-sentence product

**Autonomous, verdict-gated agent chains.** Fire one goal, and a sequence of
specialized agents runs it to completion — design → review → implement → QA →
merge-verdict — without the founder babysitting. The verdict gates are what make
"fire and forget" safe to actually do.

Everything else is either *enabling infrastructure* or *ceremony to delete*.

## How we got here (the diagnostic trail)

1. **Demand is real and present.** The founder is not a builder looking for a
   reason to use his tool. He runs a real Macau-tourism business solo and is
   drowning in build/content/marketing. The repo's `hk-macau-tourism` sample org
   is his *actual company*, and he already operates through HappyRanch daily
   (build tasks delegated, content team authoring articles). For a pre-product
   startup, that is the strongest possible starting position.

2. **The status-quo competitor is "just open Claude Code."** The bar HappyRanch
   has to beat is a single `claude -p "write the guide"` call. It clears that bar
   for a concrete, *observed* reason: with specialized agents the context-setup
   cost is paid **once**, into the agent definition, instead of every prompt.

3. **The founder nearly amputated his best feature.** He asserted "the org layer
   probably isn't necessary" and called his coordinator a *router*, not a *judge*.
   Then he proved orchestration's value with this example:

   > EH designs → **reviews with senior dev** → dev implements → qa tests →
   > **back to EH for final verdict to merge** → KB entry →
   > **thread to content manager + founder** → content team follows the KB.

   That example is built **entirely** from the "unnecessary" org layer:
   peer review, a merge **verdict** (a judge, not a router), cross-team
   notification, and teams. Strip the roles, gates, and verdict and the whole
   thing collapses into one unchecked Claude call — i.e. the status quo.

4. **Resolution: it's not org-vs-no-org. It's which org primitives.**
   The real thesis: *"I don't need a human company's org chart (multiple teams,
   multiple managers cross-auditing each other, headcount). I need roles +
   handoffs + verdict gates + propagation, coordinated by one node, running
   autonomously."*

## Load-bearing ranking (founder's own, 3 → 2 → 1 → 4)

1. **#3 Autonomous chaining** — advances step-to-step without the founder. The
   outcome he actually wants (not babysitting).
2. **#2 Verdict gates** — `expect_verdict: APPROVE / PASS`, the merge verdict.
   Coupled to #3: autonomy is only worth having *because* the gates catch bad
   output before it hits the live site. Ship one, you must ship the other.
3. **#1 Role specialization** — table stakes that make each chain step good
   enough to trust. Enabler, not differentiator.
4. **#4 KB-write + cross-team thread-notify** — real, but a month-two feature.

## Keep / Cut

**Keep (the wedge + its enablers):**
- Role-specialized agents with persistent context/memory.
- Chained handoffs with verdict gates — **already built**: `src/orchestrator/chain.py`
  (inline delegation, `expect_verdict`). This *is* #3 + #2.
- The approve-to-merge judge (the coordinator renders a verdict; it is not a dumb router).
- KB-write-on-completion + thread-notify (later).

**Cut (human-org ceremony, useless for a company of one):**
- Multiple teams / team-ownership model.
- Multiple managers.
- **Manager cross-audit** (managers auditing each other — exists because human
  managers distrust each other; the founder is one person).

> The wedge is a **deletion project**, not a build project. The core engine
> already exists; it's buried under ~the org-chart layer. Finding your product by
> deleting most of your code is a rare and good place to be.

## Honest flag: tool, not yet a startup

Demand is proven *from the founder himself*. A startup needs a **second** person
with the same pain — no evidence of that yet. Correct path: **don't build this as
a product. Build it as the thing that runs the Macau business.** If it makes the
solo operation visibly outrun what one person should manage, that becomes the
proof point and demo for whether other solo operators want it. "Do other people
want this?" is the *next* office-hours session.

## Assignment (this week)

Run the exact venue-data-format example end-to-end as a **single inline
delegation chain with the org layer turned off**: one coordinator →
`design → senior_dev(APPROVE) → dev → qa(PASS) → merge-verdict`, fired once,
walk away. No teams hierarchy, no manager-cross-audit.

- **Runs clean** → wedge validated without scaffolding; start cutting.
- **Breaks because some org primitive was load-bearing** → you've found the *one*
  piece of "org" that belongs in the wedge; add it back deliberately instead of
  keeping all of it by default.

Either outcome tells you what to keep. That's the whole game right now.
