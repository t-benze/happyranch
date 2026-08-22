# 03 - Create a Runtime + Your First Org

**Purpose:** Create the container where HappyRanch stores orgs, then create your
first org.

## The Model

HappyRanch has two containers:

- **Runtime container:** a directory on your machine, such as
  `~/happyranch-runtime`. One active runtime holds one or more orgs.
- **Org:** one isolated company workspace inside the runtime. It has its own
  database, agents, workspaces, threads, KB, jobs, and artifacts.

Use the CLI path if you want the most direct setup. Use web onboarding if you
prefer a guided browser flow after the daemon is running.

## Path A: CLI Setup

From inside the repo, with the daemon running:

```bash
happyranch init ~/happyranch-runtime
happyranch orgs init my-company
happyranch init-agent
```

Recommended optional step:

```bash
happyranch assistant init
happyranch assistant status
```

What each command does:

| Command | What happens |
|---|---|
| `happyranch init ~/happyranch-runtime` | Creates and activates the runtime container |
| `happyranch orgs init my-company` | Creates the first org |
| `happyranch init-agent` | Initializes agent workspaces for the org |
| `happyranch assistant init` | Prepares the runtime-global assistant for the Cmd-K dock |

Org slugs are lowercase letters, digits, and hyphens, 1-40 characters.

## Path B: Web Onboarding

Open:

```text
http://127.0.0.1:8765/onboarding
```

The current stable onboarding shell is:

1. **Welcome:** start org creation.
2. **Create org:** enter the org slug. The page also shows executor readiness:
   which executor profiles have been registered with a valid machine-local
   binary path (via `executor-binaries register`).
3. **Success:** enter the new org dashboard.

![placeholder: Onboarding Create step with slug input and executor readiness panel](TODO)

The executor readiness panel is informational. It reports whether each profile
has been registered with a valid explicit binary path — a CLI merely installed
on `PATH` is not launchable without that registration. The panel does not
replace explicit per-profile binary setup, which is described on the next page.

## What Exists After Setup

Your runtime now has a structure like:

```text
~/happyranch-runtime/
├── happyranch.yaml
├── system/
│   └── assistant/
└── orgs/
    └── my-company/
        ├── happyranch.db
        ├── org/
        ├── workspaces/
        ├── kb/
        ├── threads/
        ├── jobs/
        └── artifacts/
```

## Multi-Org Basics

You can create and switch runtime/org context later:

```bash
happyranch orgs list
happyranch orgs init my-other-org
happyranch use ~/another-runtime
happyranch orgs unload my-other-org
```

For per-org commands, HappyRanch resolves the org from `--org <slug>`,
`HAPPYRANCH_ORG_SLUG`, or the only org in the active runtime.

## Relocating An Org (Org Portability)

Relocation is a founder, CLI-only, current-v2 operation. It moves one
**quiescent** org (no live tasks/sessions/jobs/invocations/dreams/work-hours and
no armed or firing schedules) into an **unused** same-slug destination in
another schema-v2 runtime that is **otherwise non-empty** (at least one other
org already exists). The archive is plaintext and unsigned: mutating
requests must acknowledge `trust_acknowledged: true`, and a checksum proves
corruption, not sender identity.

```bash
# 1. Read-only readiness check (fix every blocker before continuing)
happyranch orgs portability-preflight my-company

# 2. Export (writes a data-only archive to an absolute path)
happyranch orgs portability-export my-company --from-file /tmp/export.json
#   /tmp/export.json: {"archive_path": "/abs/my-company.archive", "trust_acknowledged": true}

# 3. Inspect the archive (optional)
happyranch orgs portability-inspect my-company --from-file /tmp/inspect.json
#   /tmp/inspect.json: {"archive_path": "/abs/my-company.archive"}

# 4. Import into an unused same-slug destination in another runtime
happyranch orgs portability-import my-company --from-file /tmp/import.json
#   /tmp/import.json: {"archive_path": "/abs/my-company.archive",
#                      "target_runtime": "/abs/another-runtime",
#                      "trust_acknowledged": true}
```

The source org is never deleted or modified by export. Import never overwrites
an existing slug, never runs migrated content, forces imported schedules
inactive (`active=0`), and does not attach/rebind/rearm the imported org (that
is a separate, later step). Receipts are recorded under the reserved
`orgs/_archive` namespace; retrying the same archive digest is a no-op, while a
different digest for the same slug is refused.

## Next

Go to [04 - Connect an Agentic CLI](04-connect-an-agentic-cli.md).
