# Product-design workflow U0 feasibility evidence

This U0 patch is deliberately non-production: it adds isolated proposed-schema and authority-limitation tests plus a frozen study preregistration. No route, daemon workflow, UI/CLI, migration, authority coordinator, or compatibility behavior is installed.

The executable fixture calls the actual `Database` initializer on an isolated
historical store, preserves that row, then uses an explicitly labelled test
adapter to install/reopen the proposed additive relations. It checks rollback,
replay, foreign keys and integrity. This is not a numbered production migration
or a claim that the adapter is a supported production migration path. The
authority tests call the actual `manage_agent(update)` route under its real
`teams_lock`: A→B→A demonstrates hash ABA, while same-hash contenders show the
route-local stale-writer rejection. No route-to-workflow transaction, dispatch,
or final join coordinator exists. A later D5 decision must authorize one before
execution workflows can proceed.

Study arms are prohibited until independent review locks the manifest in `output/TASK-7267/preregistration.json`; no arm context, score, or measurement is included here.
