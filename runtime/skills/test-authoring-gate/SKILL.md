---
name: test-authoring-gate
description: Use whenever you add or change a test in a HappyRanch repository, or review or QA a PR that does. Requires four answers for each affected test, attributable red/green evidence, deletion keeper proof, and checks for duplicate or self-fulfilling coverage. Includes the HappyRanch contracts where static or shape tests are legitimate and the rule that a failing test is never removed or skipped as cleanup.
license: MIT
---

# Test-authoring gate

Use this gate for every test a PR adds and every existing test whose assertions,
inputs, fixtures, or skip conditions it changes. For a mechanical-only rename,
import move, or formatting edit, put one line in the PR body explaining why the
test's inputs, execution, and assertions are unchanged.

Keep the evidence in the PR body alongside the existing case record. Do not
create a parallel proof framework or a separate evidence package.

## The four answers

For each new or changed test, state:

1. **Observable contract.** Name the behavior or invariant at a boundary the
   test actually observes: route status/body, persisted row, audit row/order,
   callback payload, rendered UI state, CLI output, or another independent
   contract. An internal helper call alone is not an observable contract.
2. **Credible regression.** Name a realistic production change that makes the
   test fail for the intended reason, such as a removed or reordered call, a
   dropped field, the wrong dependency, leaked module state, or a guard applied
   at the wrong boundary.
3. **Why existing coverage does not own it.** Name the nearest tests and the
   precise gap: for example, they stop at an earlier state, skip in normal CI,
   mock the seam, or exercise a different layer-specific risk. Prefer extending
   an existing table or fixture to adding a near-duplicate test.
4. **Test-only production seam.** State whether the test requires a production
   export, parameter, flag, wrapper, injection hook, or dead path used only by
   tests. The default answer is **no**. If the answer is yes, move the test to a
   real production boundary unless separately approved. Test-side isolation
   such as temporary directories, fresh imports, or per-test environment is not
   a production seam.

Tests sharing one contract may share one answer block only when the block names
every covered test or table case. When one scenario legitimately needs tests at
multiple layers, add a distinct-risk statement for the failure unique to each
additional layer.

## Required evidence

Select the evidence type that matches the change:

- **Bug fix — red before fix.** Run the new regression on the pre-fix production
  code and record the failing contract assertion, then its post-fix GREEN result.
  Import errors, fixture crashes, or a different guard failing do not count.
- **Coverage addition — mutation proof.** Temporarily apply the credible
  production regression from answer 2. Record RED output containing the
  observed value and expected value, restore every mutated production file
  byte-exactly, prove the restoration, and record GREEN.
- **Skip-to-run fix — collection proof.** Record the normal command's before and
  after collected/pass/skip counts, plus a mutation proof for the case that now
  executes.
- **Timing, ordering, module-state, process, or port coverage — flake check.**
  After the applicable proof above, run the finished test repeatedly in
  isolation and with its sibling file. Five clean repetitions are the usual
  minimum unless the brief requires more.

For every deletion or consolidation, name the retained **keeper** that owns the
same contract. Mutate the claimed production behavior and show that keeper going
RED, then restore production byte-exactly and show GREEN. “A stronger test
exists” is not evidence without that proof.

## Reviewer and QA checks

Review the answers against the actual diff and assertions, never against the PR
prose alone. In particular:

- confirm answer 1 is what the test observes rather than a mocked helper effect;
- confirm answer 2 is the regression that produced the recorded RED assertion;
- search neighboring tests for the same contract and demand a distinct-risk
  statement when the same mutation already has an owner;
- inspect production changes for a seam used only by tests;
- confirm the test runs in normal CI and any skip-to-run count is attributable;
- confirm every temporarily mutated production path is byte-exactly restored;
- check the junk patterns and any claimed retention-list exception below; and
- for a deletion, independently verify the named keeper fails under the claimed
  production mutation.

A missing or generic answer, non-attributable RED, unrelated failure, unproved
restoration, unjustified duplicate, test-only production seam, or deletion
without keeper mutation proof is missing required evidence. Reviewers return
`REQUEST_CHANGES`; QA does not treat maker prose as behavioral proof.

## Legitimate static and shape contracts

Static, structural, byte, and order assertions are legitimate when the shape is
itself the independent contract. The four answers and applicable mutation proof
still apply. HappyRanch's retention list includes:

- **Two-surface parity.** The OpenAPI snapshot plus web OpenAPI-coverage and
  TypeScript API mirror (`tests/contract/test_openapi_snapshot.py`,
  `web/src/test/openapi-coverage.test.ts`, `web/src/lib/api/`); and the
  permission/allow-rule projections across Claude `--allowedTools`, the Codex
  sandbox, and opencode `permission.bash` surfaces.
- **Callback payload shape.** Exact HappyRanch CLI callback commands using
  `--from-file`,
  required payload fields, and ordering copied from skills or prompts for task,
  thread, and job callbacks.
- **Audit-row shape and order.** Event kinds, required payload keys, scope
  identity, and ordering whenever order is observable.
- **Migrations and v0/v1 compatibility.** Idempotent reruns, upgraded-versus-
  fresh convergence, and documented v0 database-backed or v1 flat-runtime
  behavior. The test must invoke the production installer or migration; a
  test-side reimplementation is not a keeper.
- **Permission and authentication boundaries.** Positive and negative cases for
  allow-rule generation, sandbox projections, tokens, loopback gates, and
  tenant isolation.
- **Overloaded columns.** The meaning and exact shape of
  `audit_log.task_id` scope prefixes and `tasks.blocked_on_job_ids`, including
  consumers that must not reinterpret them.
- **Source inspection when the source shape is independently consumed.** The
  assertion must fail when the externally consumed key, byte, or path changes
  and survive an identifier-only refactor. Grepping a function's internal call
  order is not an independent contract.

Static, slow, or shape-focused is never by itself a reason to delete a test.

## Junk patterns

A test fails this gate unless a retention-list contract independently justifies
it when it has any of these forms:

- no assertion, `pass`, `assert True`, or an unconditional skip;
- a duplicate definition whose earlier test silently never runs;
- self-comparison or an expected value produced by the code under test;
- a mock or fixture that implements the behavior, receipt, ordering, or payload
  the production path is supposed to produce;
- persistence asserted against a store the production path never writes;
- a migration, policy, or protocol test that reimplements the production logic;
- a negative case that passes because a different guard denies first or the
  claimed path is never reached;
- a test skipped by the normal CI environment;
- a test name that promises a state or boundary the inputs never exercise;
- coverage whose only purpose is keeping a test-only production seam alive; or
- a near-duplicate with no distinct-risk statement.

## Failing tests and removal

Never remove, skip, xfail, marker-gate, weaken, or disable a failing test as
cleanup or to obtain a pass. Reproduce it as a possible product regression and
repair the owner within authority, or escalate. Zero deletions is a valid audit
outcome. A test deletion requires the named keeper and mutation proof above.

## HappyRanch verification

Run the focused owner test first, perform the applicable RED/GREEN proof, then
verify the worktree guard, `git diff --check`, `git diff --stat`, and the full
base-to-head diff against Native Impact Evidence. Run `scripts/local_ci.sh all`
through the durable `jobs` workflow and authenticate the clean committed head,
runtime paths/versions, and exit code. Hosted CI remains authoritative on the
exact PR head.

The HappyRanch integration suite is **SKIPPED under founder THR-211 seq270/271
and THR-243 seq42**. Record that exclusion as SKIPPED, never as PASS or verified
coverage. Do not run or repair that suite unless a later founder ruling changes
the exception.

## Third-party notice

Portions of this skill (the four authoring questions and parts of the
junk-pattern list) are adapted from the OpenClaw `test-audit` skill
(https://github.com/openclaw/openclaw), used under the MIT License:

> MIT License
>
> Copyright (c) 2026 OpenClaw Foundation
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
