# Reviewed legacy Shared Artifacts classification

Source delivery only; no live import or rollout is authorized by this guide.
The [contract](../agent-guides/contracts/shared-artifacts.md) and
[approved design](../specs/2026-11-23-shared-artifacts-retention.md#legacy-inventory-and-classification)
govern retention and release gates. All discovered legacy bytes count toward
the decimal 5,000,000,000-byte quota immediately. **Classification reclaims no
space.** This importer never unlinks bytes or changes cleanup configuration.

## Operator coordination

The importer takes the existing `OrgAccessFence` exclusively **before opening
the database**, and retains it through database closure. A live daemon, native
session or other cooperative org holder refuses admission (`org_access_busy`).
Do not stop/restart anything merely to run it: arrange a separately authorized
offline operator interval. Use the same daemon home/registry as the runtime;
an alternate home is not a way to evade existing holders. `--runtime ABS` can
select an explicit runtime; otherwise the existing registered active runtime is
used. No listener, provider, scheduler or new daemon route is started.

Normal Database construction performs its existing schema validation/bootstrap;
the importer adds no schema. Dry-run rolls back artifact discovery, simulated
review receipts and lifecycle changes. It does not establish B's first-preview
time or any release acceptance. Apply uses the existing writer transaction per
item: classification, review provenance, retirement and audits commit together.
An interruption may leave earlier items classified; rerun revalidates each
current row and reports already classified items as ineligible. There is no new
retry/crash/concurrency protocol and no additional TLA+ model.

## Manifest and review

Use UTF-8 JSON, absolute file paths and singly linked regular files (maximum
32 MiB/document, 100,000 items). Unknown keys, duplicate JSON keys/names, wildcard
decisions and non-lowercase SHA-256 values are refused. Arrays retain order.
No prefix, mtime, extension, closed-task link or `artifact_put` audit determines
ownership, consent or expiry. TASK-18706's CSV is evidence input, not an applying
manifest: refresh exact hashes, dependencies and attribution before review.

Inventory version 1 has this shape (every displayed field is required; unresolved
attribution is explicitly null):

```json
{
  "version": 1,
  "org": "example",
  "source_task_id": "TASK-18706",
  "items": [{
    "name": "design/source.zip",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "size_bytes": 123,
    "dependency_evidence": ["Exact checked task/thread/skill reader evidence and check time"],
    "owner_agent_id": "dev_agent",
    "manager_agent_id": "engineering_manager",
    "origin_task_id": "TASK-123",
    "origin_thread_id": null,
    "purpose": "Reusable downloadable design source",
    "classification": "reference",
    "reviewed_expires_at": "2027-01-01T00:00:00Z",
    "reference_reason": "Needed for the reviewed design"
  }]
}
```

Classification is exactly `default`, `reference`, `retire` or `undecided`.
Default/reference need a known permanent owner, a known executable manager,
exactly one existing same-org origin, purpose and an explicit future UTC expiry
at most 90/180 elapsed days **from classification**. Reference also needs a
reason. Missing expiry is held, never automatically assigned. A retire decision
has null expiry, full attribution and reviewed justification in purpose/evidence;
it grants no retention and can only enter the seven-day recoverable path.
Unknown owners/managers and undecided entries stay unclassified with a routing
reason. Their review must be completed with their manager (or storage-management
thread/Founder if routing remains unresolved).

Review version 1 has `version`, `org`, `inventory_digest`, ordered `decisions`
and `acceptance`:

```json
{
  "version": 1,
  "org": "example",
  "inventory_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "decisions": [{"name": "design/source.zip", "classification": "reference"}],
  "acceptance": {"task_id": "TASK-456", "result_id": 789}
}
```

Every decision must match the inventory's exact ordered name/classification.
Digests are SHA-256 over parsed model JSON, UTF-8, sorted object keys, compact
`,`/`:` separators, Unicode unescaped, no whitespace/newline; arrays retain
order. No defaults are inserted into these schemas. `digest(Inventory)` and
`digest(Review)` in `runtime.infrastructure.shared_artifact_inventory` provide
the canonical calculations; whitespace/key ordering changes do not change them.

The manager first reviews the inventory and decisions and computes
`approval_digest(review)` over the entire review **excluding `acceptance`**.
Their authenticated task completion summary must contain this standalone line:

```text
SHARED_ARTIFACT_REVIEW_ACCEPTED <approval_digest>
```

After completion, add its exact task ID and INTEGER result ID to `acceptance`;
then compute the final review digest. Excluding the later citation avoids a
circular hash. The importer reads the existing task/result records: assigned
agent and result agent must both be permanent `engineering_manager`, the task
and result must be completed, the result session must match the task's retained
session, and that result must contain the exact acceptance line. Review time
comes from the persisted result. A reviewer string, self-written file, unrelated
acceptance, another agent's result or a bare historical inventory endorsement
does not satisfy this check. Existing completion intake owns authentication;
this tool neither creates a manager result nor changes authorization.

## Preview and apply

```bash
python -m runtime.daemon.artifact_legacy_import --org ORG \
  --inventory /absolute/reviewed-inventory.json \
  --review /absolute/engineering-manager-review.json --dry-run
```

Dry-run is the default. Output includes canonical digests, measured usage,
global refusal reasons, exact per-item metadata/reference outcome/routing reason,
and exact unlisted legacy names. Unlisted items remain unclassified. Missing or
invalid review files still permit a refused per-item preview. An invalid
inventory refuses without attempting classification.

```bash
python -m runtime.daemon.artifact_legacy_import --org ORG \
  --inventory /absolute/reviewed-inventory.json \
  --review /absolute/engineering-manager-review.json --apply \
  --inventory-digest EXACT_INVENTORY_SHA256 --review-digest EXACT_REVIEW_SHA256
```

Both digests must match. Apply also needs B's persisted first complete preview
at least seven days ago and the separate persisted preview acceptance. The
importer never sets those gates or source-protection verification. Authenticated
review receipts use A's immutable row; classification cites the exact receipt.
Current size/hash, literal no-follow ancestry, `legacy_unclassified` state,
pending operations, known attribution and B's current reference proof are
revalidated inside each item's transaction. Changed/missing/unsafe bytes and
indeterminate references stay unclassified. Incomplete accounting refuses apply
entirely, without reporting a quota discount. Live dependencies are audited;
default/reference grants remain bounded and B continues to defer expiry while
the dependency lives. Retire additionally needs effective `enforce`, verified
source protection and absent live references. Preview/off cannot retire.

Canonical custom-skill sources stay counted; B verifies version/materialization/
recovery readers and audits/rechecks every deferral. Classification is no source
relocation, exemption or destructive activation. Only B's separately gated
scheduler can later unlink after the full recovery interval. Keep the exact
reviewed files as review evidence. Nonzero exit is refusal/error; a later error
may follow already committed items and is not evidence of atomic batch rollback.

## Synthetic verification

`uv run --frozen python scripts/test_shared_artifacts_d.py` exercises finite
private SQLite/filesystem cases, including the 7,521-file /1,770,412,702-byte
TASK-18706 shape. No live org, daemon lifespan or Product E2E is used. The script
is slice-owned and is not a required CI check.
