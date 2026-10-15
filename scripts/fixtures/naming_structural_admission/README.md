# N-SAC-1 finite synthetic verification

Run each B-SA-01 through B-SA-08 in a separate Python 3.14 process from the
selected guarded checkout, using its frozen locked environment:

```
uv run --frozen python scripts/verify_naming_structural_admission.py --case B-SA-01 --scratch /absolute/workspace/.happyranch/task-tmp/TASK-ID/new-child --evidence /absolute/owned/output/new-child
```

The task scratch parent must already exist, belong to this user and have a
canonical path. Both destination children must be absent. The driver creates
private directories and never accepts a DB path, URI, runtime or repair input.
It sets isolated daemon home before product imports. It leaves evidence and
scratch for the owning task's preservation/cleanup contract. Each case has a
120-second process alarm. No providers, workers, scheduler, listener or lifespan
are started. The real identity router and bearer dependencies run via ASGI.
This command is explicitly scoped synthetic verification, outside automated
lint-only checks, general integration and daily Product E2E.

FIX-H8-1 provenance is frozen in manifest.json before the production edit:
release_literals.json and naming-v1.sql are immutable fa2ef222 assets, read as
data only; observed-eight.json contains precisely the accepted packet's eight
DDL differences. All raw SQL hashes are asserted after construction and ordinary
Database reopen. Fixtures use real generic/workflow/Pause setup, typed synthetic
sentinels and shipping canonical renderers. They are not a production DB copy or
a reconstruction of the complete actual upgrade chronology.

OBS-1 captures complete object inventories, PRAGMA metadata, named typed values,
rowids, sequences and canonical file hashes/identities. S0→S1 fixture/constructor
setup is separate from S1→S2 naming and S2→S3 real OrgState reopen. Reopen reports
all separately owned settings/publication changes. Refusals require exact logical
preservation. Success permits only naming effects. B-SA-07 bounds capacity
calibration to three fresh fixtures; absence of observed post-effect failure is
reported MISSING. Traces and actual SQLite error names accompany rollback proof.
Existing validator controls are attributed separately from new structural guards.
The independent full case/four-answer acceptance record belongs to TASK11597;
TASK11601 output records actual commands, failures, repairs and missing results.
