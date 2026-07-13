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
   which supported CLIs are found on `PATH`.
3. **Success:** enter the new org dashboard.

![placeholder: Onboarding Create step with slug input and executor readiness panel](TODO)

The executor readiness panel is informational. It helps you see whether a CLI
such as `claude` or `codex` is available, but it does not replace the evolving
executor-connect flow described on the next page.

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

## Next

Go to [04 - Connect an Agentic CLI](04-connect-an-agentic-cli.md).
