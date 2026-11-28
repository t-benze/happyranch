# Org secrets backend v1 (Slice A)

Founder THR-400 seq35/38: each org stores encrypted values on this machine.
Names and update times stay visible while locked, and Delete still works.
New/Replace require the OS key. The later slices own the Settings page,
post guards, best-effort saved-output masking and login-start packaging.

The shared OS account is the trust boundary. Agents, dependencies and commands
under that account can read injected values. This provides no team/org process
isolation or use-only guarantee. Never print, echo, log, commit or put values in
callback text. Names alone appear in session framing. Changes apply to new
processes; running processes and their existing descendants retain their copies.

## Storage and key custody

`<runtime>/orgs/<slug>/secrets/` is 0700; the fixed lock, encrypted JSON and
temporary publication files are 0600. It is outside workspaces, repos, KB and
threads. Values use AES-256-GCM, random 96-bit nonces, with org path, name,
update time and record revision authenticated as additional data. Writes lock a
stable no-follow inode, publish a complete encrypted snapshot using same-directory
atomic replace and fsync. No plaintext-value/key file or subprocess argument is
used. The metadata/operation receipts contain no values or value-derived digest.

Linux uses `secretstorage` against the default login Secret Service collection,
without creating a collection or prompting to unlock it. macOS uses the explicit
`keyring.backends.macOS.Keyring` adapter pinned to `login.keychain-db`; plugin
discovery and alternative/file backends are never used. `cryptography` supplies
AEAD. The per-org keyring account is a digest of the canonical org root, so two
runtime installations with the same slug have separate keys. Copying/moving an
org is not a secret migration; exports explicitly exclude this root.
The current shipping portability service exposes preflight/reconciliation only,
not an archive exporter. Its classification excludes `secrets` with the named
reason `org_secrets_reentered_on_new_machine`; any later exporter must consume
that classification and require secrets to be entered again on the destination.

Startup probes read a key into memory without creating one. Once loaded, the
daemon retains it if the backing keyring locks. Missing/denied/locked backend
means `locked`; key-status and the existing periodic native-discovery tick
re-probe. A key is created only during the first New with no prior encrypted
store. Independent writers serialize first-key creation. A key left by a failed
first publication is reused; inaccessible existing keys/stores are never
overwritten. Last-delete retains the encrypted-store/receipt history and key;
it removes the session hold even if that key is inaccessible.

Dependencies are locked in `uv.lock`. Linux's system `/usr/bin/python3 -I`
desktop wrapper execs `bin/happyranch-daemon`, which execs the payload's
`python/bin/python3 -I -m runtime.daemon`. The payload builder installs all frozen
production dependencies into that interpreter; system-Python packages are not a
substitute. macOS's PyInstaller spec explicitly includes the lazy Keychain
backend. Installed old payloads without these dependencies need a rebuilt
release; dependency/source delivery is not deployment or packaged acceptance.

## API consumed by the later page

All paths below have prefix `/api/v1/orgs/{slug}` and retain the existing master
bearer requirement. All responses, including errors, carry `Cache-Control:
no-store`. Values are never returned. Secrets is local-only: the existing
connector default-deny refuses every route below with HTTP 403 `unclassified_denied`,
including list, key-status and operation outcomes. The native config-write
policy and connector allowlist are unchanged. On a remote target the later
frontend shows the named unavailable state **Change this on {Host}** without
calling these routes. Any 403 from a Secrets route, regardless of error code,
is a not-sent refusal: keep drafts,
show no failed-save copy and never automatically retry.
No new peer/header-based trust mechanism or listener/bearer change is introduced.

| Method / path | Input | Success |
| --- | --- | --- |
| GET `/secrets` | none | 200 `{key_state, revision, secrets:[{name,updated_at,revision}]}` |
| POST `/secrets` | `{name,value}` | 201 `{name,updated_at,revision,operation_id}` |
| PUT `/secrets/{name}` | `{value}` | 200 `{name,updated_at,revision,operation_id}` |
| DELETE `/secrets/{name}` | none | 200 `{name,deleted:true,revision,operation_id}`; works locked |
| POST `/secrets/key-status` | none | 200 `{key_state,hold_reason:null|"org_secrets_locked"}` |
| GET `/secrets/operations/{operation_id}` | none | 200 committed receipt or `{operation_id,outcome:"unknown"}` |

Every CRUD mutation requires one `X-Secret-Operation` header containing a fresh
canonical lowercase UUID. The frontend allocates it **before** its single send
and retains it with the original target/org owner. A committed receipt is
atomically saved with the mutation and contains `outcome:"committed"`, action
(`create|replace|delete`), name, revision, and updated_at or deleted. It remains
authoritative for that operation after an intervening mutation. Reads never
replay the write, recreate a deleted secret or compare a saved value. Reusing a
retained operation ID returns 409 `operation_reused`, without applying anything.

The last 256 committed receipts per org are retained. An unobserved/evicted ID
returns **unknown**, including after a request lost before commit; it never
asserts no effect or licenses replay. Timestamps/list state alone cannot settle
an unknown replacement. This is same-authority outcome observation, not a
per-user receipt owner or a new identity/authentication system. The TS mirrors
send mutations once (`replayOnUnauthorized:false`) and expose outcome reads.

Errors use the existing client envelope `{detail:{code,message}}` with closed
messages, never body/validation/dependency exception text. Codes include:
422 invalid_name/reserved_name/empty_value/invalid_value/invalid_request/
invalid_operation_id; 413 value_too_large/environment_budget_exceeded;
409 secret_exists (message directs to Replace)/operation_reused;
404 secret_not_found; 423 key_locked; 503 store_unavailable.
Remote connector policy returns 403 `unclassified_denied`. Metadata and Delete
do not re-read the OS keyring or prompt. Malformed ciphertext holds delivery;
structurally unreadable metadata safely refuses rather than guessing a list.

## Name, value and environment limits

Names are **uppercase ASCII only**, a whole-string `[A-Z_][A-Z0-9_]*` match,
1–128 characters. Lowercase is invalid_name, never silently normalized.
Duplicate and reserved checks case-fold; duplicates direct to Replace.
Trailing LF/CR, embedded newline, equals and NUL are invalid names.
Values are 1–65,536 UTF-8 bytes, NUL-free; multiline and whitespace are
preserved exactly. There are at most 128 secrets and 131,072 aggregate bytes
including names, equals and terminators. A new-process environment also reserves
half the OS ARG_MAX for arguments/pointers/wrappers, capped at 1 MiB of env.
Excess refuses before provider spawn with a closed environment-budget code.

Reserved prefixes (case-insensitive): `HAPPYRANCH_`, `ANTHROPIC_`, `OPENAI_`,
`CLAUDE_`, `CODEX_`, `OPENCODE_`, `PI_`, `UV_`, `PIP_`, `DYLD_`, `BEDROCK_`,
`VERTEX_`, `GOOGLE_CLOUD_`, `AZURE_OPENAI_`, `LC_`, `PYTHON` (all names starting
with PYTHON), `NODE_`, `LD_`, `BASH` and `ZSH`.

Reserved exact names: PATH, HOME, SHELL, USER, LOGNAME, PWD, OLDPWD, SHLVL, _,
TMPDIR, TMP, TEMP, PYTHONDONTWRITEBYTECODE, PYTHONPATH, PYTHONHOME,
PYTHONSTARTUP, PYTHONINSPECT, VIRTUAL_ENV, XDG_CACHE_HOME, XDG_CONFIG_HOME,
XDG_DATA_HOME, XDG_RUNTIME_DIR, NPM_CONFIG_CACHE (including npm_config_cache),
NODE_COMPILE_CACHE, NODE_OPTIONS, GOCACHE, DBUS_SESSION_BUS_ADDRESS, LD_PRELOAD,
LD_LIBRARY_PATH, GOOGLE_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, XAI_API_KEY,
MISTRAL_API_KEY, DEEPSEEK_API_KEY, AZURE_API_KEY, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_PROFILE, AWS_REGION,
AWS_DEFAULT_REGION, LANG, LANGUAGE, TZ, ENV, SHELLOPTS, PS4, IFS, CDPATH,
GLOBIGNORE, PROMPT_COMMAND, ZDOTDIR, FPATH, SSL_CERT_FILE, SSL_CERT_DIR,
REQUESTS_CA_BUNDLE and CURL_CA_BUNDLE. The reserve owner is
`runtime/org_secrets/names.py`. This intentionally includes whole interpreter,
loader and shell families: startup diagnostics can emit an invalid control value
before any agent code runs. API `reserved_name` copy identifies these control
classes; no input value is included. Delivery independently validates every name;
a legacy now-reserved record closes admission, never injects its value.

The owned inventory is `_ENV_VARS_TO_STRIP`, `_WORKSPACE_CACHE_ENV_DIRS` and
`_callee_env` in `runtime/orchestrator/executors.py`; scratch TMPDIR/TMP/TEMP
and HAPPYRANCH sidecars in `task_scratch.py`; env overlays in `isolation.py`,
`linux_systemd.py` and `macos_process_group.py`; builtin adapter launch wrappers
in `runtime/adapters/`; and Linux desktop `child_environment` plus
`packaging/linux/build-payload.sh` launcher generation. These own PATH, HOME,
LANG/LC_*, HAPPYRANCH_*, PYTHON*, UV_*, VIRTUAL_ENV, cache and scratch names.
The reserved-name keeper checks runtime assignment shapes and independent
interpreter/locale/loader controls, including PYTHONWARNINGS and LC_ALL.

## Session coverage and holds

| Path | Delivery / admission |
| --- | --- |
| Claude, Codex, opencode, pi | Shared `_callee_env`, both native launch specs and direct launches |
| Custom adapter | Same environment on contained and uncontained paths; values never enter AdapterInput |
| Task/subtask | Queue admission and `Orchestrator.run_step` defer before native preparation; durable task state survives restart |
| Thread, including delta/resume/fallback | Existing thread owner safely defers before FIFO/preparation; runnable-token rediscovery retains original invocation |
| Wake/schedule/dream | Existing native producer ownership records safe no-launch deferral; scheduler rediscovery retains original source/SID |
| Local jobs | No additional org-store injection; existing inherited env/scratch rules remain |
| remote_jobs | N/A: contracts-only, no shipping runner; no injection promised |

New contained thread/wake/schedule/dream processes select one current snapshot
at final launch admission, after host/provider capacity waits and before native
commitment. Values, key readiness/hold, names framing and the complete thread
prompt bound use that same snapshot. New/Replace/Delete committed during a wait
therefore affect the admitted child; existing children retain their copies.
Every thread phase re-bounds the actual shared preamble plus body, trimming only
optional history and refusing oversized mandatory context. Direct launch attempts
also select after provider waiting. Authoritative runtime/cache/scratch settings
are applied afterward. Stored reserved names fail
closed. Context lookup uses the canonical org workspace, not a global slug
lookup across runtime installations. Orgs with zero configured secrets never
hold. A locked key never creates an ExecutorResult crash, consumes a callback
or advances thread delivery. Unlock/last-delete permits only otherwise eligible
work: Pause, cancellation, terminal/live/unknown owner and native dedup gates
retain authority. The existing 5-second thread discovery tick probes key state
and republishes deferred task hints; other producers use their existing scheduler
cadence. Re-probe is not synchronous proof that an agent has launched.

`store_for(org_root).current_values()` is the single in-memory current-values
hook for Slice B. It raises a closed error if values cannot be decrypted. It has
no API/CLI retrieval endpoint. Tests and real-platform qualification are separate;
see the task cases/evidence ledger and the [bounded formal model](../specs/formal/org-secrets/README.md).
