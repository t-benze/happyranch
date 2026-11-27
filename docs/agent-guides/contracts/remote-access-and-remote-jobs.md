# Remote access and remote jobs contracts

Disposable agent payload paths use `"${TMPDIR:-/tmp}/payload.json"` (expand
before writing with a file tool). Task-owned jobs already receive their task
scratch environment. Unbound thread invocations keep the default environment;
this convention adds no invocation directory, cleanup, host mount or remote
access permission. See [temporary storage](../agent-executors-and-permissions.md#task-temporary-storage-thr-307-seq430)
for browser-profile placement and Unix socket path limits.

## Thread guests remain local (THR-362 slice 1)

The three POST seat routes (`/threads/{id}/guests`,
`/threads/{id}/guests/{guest_id}/new-code`, and
`/threads/{id}/guests/{guest_id}/revoke`) remain absent from
`runtime/remote_access/route_policy.json` and are denied by default.
Each additionally requires a loopback peer, the master bearer and the existing
human-only gate. Remote GET thread detail can display safe seat metadata,
never codes/tokens/hashes. Founder decision 13 denies even remote Revoke.
Slice 2 also keeps POST `/guests/mcp` and `/guests/setup/{app_id}` absent from
the remote policy and denied. MCP accepts only local tool-argument credentials
and rejects non-loopback peer/Host, wrong port, every Origin and HTTP credentials.
Setup requires the existing founder bearer/human gate and loopback peer. Neither
route is browser-consumed. No remote authority or policy entry is added.

> Extracted from the repository-root `AGENTS.md` on 2026-10-30 and maintained
> here. The THR-327 shipped Linux lifecycle and bounded M1 receipt below extend
> the original phase-specific implementation records; those earlier slice limits
> do not describe the whole current managed lane. Historical evidence remains
> historical, and every unexecuted acceptance obligation stays explicit.

## Public host compatibility metadata (THR-387 S4a)

`GET /api/v1/health` is already remote-allowed. Its additive `api_contract`
(`current=1`, `min_client=1`) and `release` (version+source SHA or `"unreleased"`)
describe host compatibility without changing the endpoint set or access policy.
Existing fields and conditional `host_sessions` remain; see the
[health response and contract-bump rule](../web-and-cli.md#host-compatibility-health-metadata).
Native readiness/reconnect/Restart compatibility checks are implemented on Linux
in PR #1767 (merged `921f126e`). See the
[Linux admission/reset boundaries](../../specs/2026-11-17-web-native-bridge.md#v2-compatibility-rule).
Mac readiness/reconnect compatibility checks are implemented in source in
PR #1839 (MAC-1). Native Mac qualification is recorded by the PR's
exact-final-head Mac build/QA evidence; see the
[MAC-1 section in `app/mac/README.md`](../../../app/mac/README.md#mac-1-main-app-scheme-and-native-api-transport).

## macOS connector lifecycle slice (THR-360, C1+C2)

The source `happyranch-connector` CLI selects systemd on Linux and a per-user
LaunchAgent on macOS. Other platforms refuse service management with `error:`
and a nonzero exit. Linux unit rendering, dedicated-account installation,
systemctl argv and service-error text stay unchanged. Lifecycle errors, including
missing systemctl, now reach the CLI error boundary without a traceback.

On macOS the founder's own user owns
`~/Library/LaunchAgents/ai.happyranch.connector.plist`; no admin, sudo or
system-wide LaunchDaemon is used. The existing `system` setting must be false.
The default Linux unit name maps to this fixed label; an explicit custom
reverse-DNS `unit_name` selects an independent agent. `ProgramArguments` contains
an absolute executable and the source config path (or explicit `exec_start`);
no config contents, token, bearer or credential environment is embedded.
Custom argv must also contain no secrets. Stdout/stderr go to
`~/Library/Logs/HappyRanch/<label>.{stdout,stderr}.log`. This slice installs the
service definition only: it does not stage or migrate credentials, policy or
trust state. The source config must remain available. Frozen-app entry dispatch
and paths that survive bundle moves/updates remain pending.

Rendered plists explicitly set `LimitLoadToSessionType` to the array
`["Aqua", "Background"]`, permitting GUI-login and headless user sessions.
Install/upgrade render this key when replacing an older definition; validation
still accepts old backups without it, and rollback restores their original bytes.
`Disabled=true`, exact-label overrides and all other rendered keys are unchanged.

The backend probes `gui/<current uid>` first. An explicit `Could not find
domain for` response, or exit 125 with `Domain does not support specified
action` (for example SSH/Jenkins without an Aqua session), permits a
`user/<current uid>` probe. Fallback occurs only if that probe succeeds;
otherwise the error names both domain diagnostics. Other GUI errors refuse.
The selected domain is pinned for the operation; later launchctl failures never
switch domains or escalate privileges.

Without GUI login, an explicitly started `user/<uid>` service runs for the
current boot/background session while that domain exists. It does not establish
logged-out or reboot persistence. Autoload of `~/Library/LaunchAgents` after
boot requires a GUI login; a headless session requires an existing user domain
and explicit start. The packaged Mac app runs in the GUI domain.

- `install` round-trips the plist through stdlib `plistlib` before writing an
  owner-only file; replacing a plist first creates an owner-only backup under
  `LaunchAgents/backups`. Backups are written to a nonselectable temporary file,
  flushed and fsynced, then atomically renamed into the backup set; failed
  reads/writes never publish an empty or partial candidate.
  Installation does not bootstrap/start the process.
  `enable`/`disable` use launchctl's service-target overrides and do not stop a
  loaded process. `install --no-enable` leaves existing enablement unchanged;
  a fresh plist defaults to disabled, so it does not autostart at login.
  Overrides match the exact label; sibling/custom/probe labels do not affect
  it. Without an exact override, the plist's `Disabled` value applies;
  malformed exact-label overrides refuse.
- `start` bootstraps an unloaded agent or kickstarts a loaded agent. A manual
  start of a disabled agent temporarily enables it for bootstrap and restores
  its disabled override, preserving the absence of login autostart.
  `restart` uses `kickstart -k` and requires an already loaded agent.
- `KeepAlive=true` recovers after every process exit, including SIGKILL and exit
  zero; `ThrottleInterval=10` limits repeated rapid launches. `stop` bootouts the
  service and confirms it is unloaded, so KeepAlive cannot undo the stop.
  Successful bootout may leave a transient `SIGTERMed` service. Stop polls
  `print` at intervals no greater than 0.5s for at most 30s after bootout;
  print and the confirming domain probe share that deadline. Only not-found
  with a still-accessible domain proves absence. Deadline, denied/lost domain
  or malformed print refuses; no plist/state removal follows. An already absent
  service needs no bootout. Composite stop settles the sidecar before the
  connector; replacement and uninstall wait for verified absence before writes.
  `uninstall` bootouts, disables and removes the plist, and is idempotent when
  already absent. launchd may retain the label's disabled override; uninstall
  does not edit launchd's private preference database or remove logs/backups.
- `status` parses top-level `launchctl print` state/PID/last-exit information
  into `ServiceStatus`, with macOS-only `domain` (`gui/<uid>` or `user/<uid>`)
  and nullable `last_exit_code` and `last_exit_signal`. An installed/unloaded
  service differs from a missing plist; permission/domain errors never count as absence. Exit observations
  disappear after bootout. A running PID proves process state only, never
  listener readiness. Only the exact top-level `state = running` with a
  positive PID reports active/running. `waiting`, `not running`, `spawn scheduled`,
  `xpcproxy`, `exited`, `exiting`, `penalty box` and any other nonempty state
  report inactive with PID zero; `sub_state` preserves the raw state, capped at
  2048 characters. Missing/empty state, duplicate observed fields, malformed
  numeric fields, unbalanced braces or quotes, and running without a valid PID
  refuse. Nested fields are ignored; braces inside double-quoted values (including
  escaped quotes/backslashes) do not affect nesting.
- `upgrade` backs up and replaces the plist, then bootouts/bootstraps so launchd
  rereads the new definition. Failed startup attempts restore the backup and
  verify recovery; explicit `rollback` restores the latest backup and likewise
  verifies a running process. The chosen backup is fully read and validated
  before bootout; invalid candidates refuse while the live service stays
  untouched. This is process verification, not readiness.

### macOS readiness, status and recovery (THR-360 C3, PR 2)

The foreground connector owns `<managed-state-root>/<label>.readiness.json`.
C4's final default is
`~/Library/Application Support/HappyRanch/connector/happyranch-connector/ai.happyranch.connector.readiness.json`.
The state root is `~/Library/Application Support/HappyRanch/connector/<state_dir>`;
an explicit `managed_dir_root` still replaces the root preceding `<state_dir>`.
Custom labels use separate readiness files. Status and supervisor construction
never create the record. The provisional C3 `~/.local/state` layout was never
released or deployed, so there is no migration or compatibility reader.

Each record has `pid`, `process_started_at` (wall time at foreground run entry),
`ready`, nullable `last_healthy_wall_time`, increasing `sequence`, and a fixed
non-secret `reason`. The process publishes false/`starting` before readiness
checks, true/`healthy` on first readiness and every completed healthy pass,
false on readiness/listener loss, and false on shutdown. Loss retains the
`readiness-or-listener-lost` reason through shutdown. No arbitrary STATUS text,
exception text, token, key or bearer is stored. Publication uses an exclusive
0600 temporary file in the same directory, flush/fsync then atomic rename;
failed writes retain the previous complete file and remove the pending file.
Directory traversal opens each literal component without following symlinks.
Missing directories are created 0700; an existing writable state leaf must be
owned by the current euid with mode 0700. Existing ancestor modes are preserved.
Publication and pending-file cleanup use the pinned parent descriptor, and
status likewise refuses aliased parents without reading through them.

A separate in-process watchdog starts before the first publication and uses
monotonic time. Only a completed healthy pass, including publication, renews
its deadline. Startup without readiness, a blocked readiness check, loop,
publication or shutdown cannot renew it. At `watchdog_sec` without a healthy
pass it calls `os._exit(75)` without acquiring a loop lock or doing filesystem
I/O. KeepAlive then relaunches the process. Readiness/listener loss still closes
admission and exits 1. The existing `watchdog_sec` and `poll_seconds` values
apply; macOS requires finite positive intervals. Linux retains its sd_notify
READY/WATCHDOG/STOPPING path, unit renderer, installer and status output.

CLI `status` keeps every ServiceStatus/LaunchdStatus field and adds `ready`,
`readiness_state`, `readiness_record_state`, `readiness_max_age_seconds`,
`readiness_age_seconds`, `record_pid`, `process_started_at`,
`last_healthy_wall_time`, `sequence`, and `readiness_reason`. Ready requires
launchd running with a positive PID, the same record PID, record `ready=true`,
and a last healthy timestamp aged from zero through
`watchdog_sec + poll_seconds` inclusive. The margin is exactly one configured
poll interval; future timestamps also fail freshness. Other substates are
`not-loaded`, `starting`, `running-not-ready`, `stale-readiness`, `pid-mismatch`,
`record-missing`, `record-corrupt`, `record-foreign`, or `record-unavailable`.
Not-loaded/starting process state takes precedence; the independent
`readiness_record_state` still reports the file observation. Missing, malformed,
oversized (>4096 bytes), non-regular, symlinked, foreign-owner, multiply linked,
non-0600 or unreadable records never claim readiness or crash status. Invalid
record contents are never echoed. launchctl parsing itself is unchanged.

These are source-level observations, not native acceptance or an authenticated
process-birth identity. The watchdog depends on its Python thread being
scheduled; it cannot promise recovery from host sleep or a runtime/GIL-wide
freeze. Wall-clock discontinuity may temporarily report stale readiness; the
restart deadline remains monotonic. No native proof is claimed in PR 2 and the
sleep-agent native suite is unchanged. C5's managed installer is described below;
C6a delivers Go sidecar build/readiness, C6-loss supplies connector-loss exit,
and C6b below delivers Python integration. C7 below delivers frozen
entry/bundling; C8 below delivers Swift coexistence. Signing and packaged-app acceptance remain pending. The Swift in-app
HomeConnector is untouched; never enable both listeners together.

### macOS credentials and state paths (THR-360 C4, PR 3)

The founder-accepted macOS posture is a connector running as the founder's own
user and reading the local daemon token directly. `daemon_token_path` is required
explicitly. For the bundled Mac app it points to
`~/Library/Application Support/HappyRanch/daemon.token` (expand `~` before use).
`AppDelegate.resolvedDaemonHome()` selects that home and `EnvironmentSanitizer`
passes it as `HAPPYRANCH_DAEMON_HOME` to the daemon. In contrast,
`runtime.runtime.token_file()` defaults to `~/.happyranch/daemon.token` outside
that app environment, so the connector does not infer its token path from it.
Both configured `credentials_directory` and presence of `CREDENTIALS_DIRECTORY`
(even empty) refuse on Darwin with a fixed configuration error; no systemd
credential source or fallback is used.

`DarwinFileDaemonCredentialProvider` retains the file-provider interface and
opens a literal, non-symlink, regular, single-link file owned by the current
euid, with no group/other mode bits. It verifies named/open inode identity and
metadata before and after a bounded read, rejects empty/whitespace, invalid UTF8
and files larger than 64 KiB, and reports only a fixed non-secret failure reason.
It follows no symlink in any parent. Linux provider selection and both existing
Linux credential readers retain their previous semantics and bytes.

`launchd_files.DARWIN_ENROLLMENT_KEY_PATH` defines
`~/Library/Application Support/HappyRanch/connector/happyranch-connector/enrollment.key`.
Source lifecycle overrides resolve enrollment to `<managed-state-root>/enrollment.key`;
C5's managed installer below uses the fixed default layout.
`credentials.read_darwin_enrollment()` accepts an absolute literal path and
returns a bytearray after the same no-follow, inode, owner, single-link and
bounded/nonempty checks, requiring exactly 0600. This is the Linux installer's
key contract with current-euid ownership instead of root ownership; no group
ownership requirement is added. The reader itself never stages or enrolls a key.
C5 stages it as described below; C6a supplies consumption and C6b wires the handoff. Callers expand the default
constant with `.expanduser()` before passing it to the reader. Plists/config arguments and
CLI/status observations carry paths, never daemon-token or enrollment-key values.
The retained standalone connector venue exercises real files, modes, symlinks,
hardlinks and FIFOs on Linux with Darwin selection; alternate-euid checks use
test-side identity substitution against real stat ownership. Native Darwin
credential/path execution is NOT RUN in C4 (no Mac mini run authorized).

### macOS managed installer (THR-360 C5, PR 4)

On Darwin, `python -m runtime.remote_access.installer` supports
`install --enrollment-key-file /absolute/input.key --sidecar-binary /absolute/sidecar
--sidecar-config /absolute/sidecar.json`, `check-drift`, and `uninstall [--purge]`.
C6b below extends this installer to both agents and adds composite lifecycle/status. Linux's installer class/parser and systemd manager remain
unchanged. No sudo, account creation, system domain or LaunchDaemon is used.
Install also accepts `--config /absolute/source.json`, defaulting to the source
CLI's `~/.happyranch/remote_access/config.json`. The operator supplies that config
and its policy as current-user single-link regular 0600 files, at most 64 KiB
each, with literal non-symlink parents. The config requires only a managed
provider, `system=false`, default unit/state names and no `exec_start` or
`managed_dir_root` override. The explicit daemon token path is expanded and
retained; the installer never reads or copies the token.

Assumption: the absolute installing Python interpreter has `runtime.remote_access.cli`
installed/importable outside the checkout. Plist argv uses that source entry:
`<python> -m runtime.remote_access.cli run --managed --config <managed-state-root>/config.json`.
C7 below defines the separate frozen bundle entry. The config is copied
and repointed to `policy.json` and `trust-state.json` under C4's final
`~/Library/Application Support/HappyRanch/connector/happyranch-connector` layout.
Policy bytes are copied; existing trust snapshots/anchors and other state are
never seeded or overwritten. `install.json` records config/policy/plist SHA-256
digests for drift observation, not authentication.

The C4 enrollment reader validates input before writes or launchctl. A 0600 copy
is stored at C4's `enrollment.key` path; the in-memory bytearray is wiped on
success/failure. The original input remains the operator's responsibility.
C6a consumes/deletes the staged key and writes its consumed marker. C6b below
defines staged-key refusal and marker-only reinstall without another key. Keys never enter config, plist, argv, environment,
receipt or output. Partial failure may retain the copy; inspect/revoke and purge
explicitly before retrying. There is no automatic rollback or crash recovery.

Managed files are current-euid, single-link 0600; new directories are 0700.
The connector root and state leaf must be absent or 0700. Existing shared
Library/LaunchAgents/log ancestors keep their modes but must be current-user
owned without group/other write permission. Literal descriptor traversal refuses
symlinks. Existing differing config/policy/receipt/plist, unsafe/foreign/linked/
special entries refuse before managed writes or lifecycle effects. Install and
check-drift refuse any plist pending entry; uninstall accepts only the safe
pending residue described below. State scans refuse cross-device subtrees, depth
over 32 or more than 4096 entries. This is not a racing same-user-writer fence.

`LaunchdServiceManager` remains the only lifecycle owner. Install writes/enables
the plist without bootstrapping, starting or claiming READY, matching the source
LaunchAgent install contract. An identical plist is enabled without another
backup. C6b precreates two label-specific log files per agent at 0600. `check-drift`
observes stored digests and `layout=ok`, plus both agents' process and readiness
records as described below; C5's placeholder `sidecar=not-installed` is superseded. Missing/differing plist/config/policy/receipt/logs, unsafe
layout or a changed source interpreter returns `error:`/exit 1. Native/JSON errors
never echo raw inputs. This proves neither an active service nor readiness.

Uninstall checks the layout, delegates stop/bootout verification, disable and
plist removal to the existing manager, rechecks after stop, then removes only
managed config/policy/receipt and the two fixed logs. Normal uninstall retains
state, enrollment key and readiness record; `--purge` also removes the validated
state subtree and its directory. Source config, operator key, daemon token,
unrelated logs, shared parents and lifecycle backups remain untouched. Missing/
partial installs and repeated removal are supported. Unsafe state refuses before
launchctl/deletion; failed stop prevents removal. The disabled override may remain.
A pending plist left by interrupted publication is accepted only as a current-user,
single-link regular 0600 file, validated through literal parents and an opened
no-follow, nonblocking descriptor. After successful stop/bootout verification,
uninstall revalidates that descriptor and the no-follow named inode before
unlinking the pending file and syncing its parent. Both normal uninstall and
purge remove this residue; repeated removal succeeds. Unsafe pending entries
refuse before lifecycle effects, and failed stop/bootout retains the pending
file, config and stored key. The existing same-user rename-race limitation applies.

The standalone installer tests use temporary filesystem objects and scripted
launchctl observations, with ZERO Mac-mini runs for C5. Native acceptance is
NOT RUN. C6a/C6b deliver sidecar/key integration; C7/C8 below deliver frozen
entry/bundling and Swift coexistence. Combined native acceptance is still
unproven. Never enable both Swift HomeConnector and this connector.

Native evidence is supplied by the `connector-launchd` Mac-mini suite for QA:
a unique harmless `/bin/sleep` agent, install/start/running PID, SIGKILL/new PID,
stop/unloaded, uninstall/absent, and cleanup receipts. It uses no connector
network listener or token and records the actual launchd domain. Source/fake
coverage is not native acceptance, and no native job is run by the maker.
On start failure, the probe records plist lint, mode/owner versus current UID,
session types and a label-filtered launchd log excerpt before cleanup. Optional
observations share the original deadline/caps; their errors never replace the
start failure. Scratch removal is recorded after disposal even on failure.
`connector_launchd.status_observations` retains each lifecycle step's last raw
service `print` and `print-disabled` excerpts (at most 2048 characters each),
with actual exit codes, before parsing can fail. Steps cover initial absence,
install/start, running polls, recovery, stop/stopped, uninstall/absent and cleanup.
Exceptions retain their type/diagnostic and any available raw output; an
unavailable command output is null, never an inferred observation. Polls continue
through inactive/unknown states to the original deadline; timeout and malformed
output still fail. These diagnostics add no commands or budget extension.

References: Apple's [LaunchAgent guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
and [launchd plist manual](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5).
Apple's [loader source](https://github.com/apple-oss-distributions/launchd/blob/main/support/launchctl.c)
also documents the Aqua default under session filtering and accepts session-type
arrays; the older manpage describes only the string form. This does not establish
the OS cause of a particular bootstrap failure; native lifecycle proof is required.

- **Generic remote jobs S1 contracts + dark S2 persistence.** `runtime/remote_jobs/` contains
  strict immutable v1 models, canonical UTF-8 JSON/SHA-256 helpers, whole-job
  bundle validation, the corrected four-part pre-run reuse identity, and the
  closed stable-reason precedence table. It has no shipping caller yet. Runner
  S2 adds only the exact additive, staged, fail-closed SQLite schema in
  `runtime/infrastructure/remote_job_schema.py`: the six runner/workspace/
  attempt/receipt/observation/frame tables (no key-history table), exact
  indexes and composite foreign keys, and five nullable `jobs` linkage
  columns. Existing rows are not backfilled or reinterpreted. Runner
  authentication/enrollment, network transport, observation
  execution, subprocess engines/finalization, coordination/resume, CLI/web,
  deployment, and activation remain unimplemented later slices. Existing local
  jobs and `JobStatus` semantics are unchanged. V0 omits coarse manifests and
  derives the normalized observation-policy digest; its typed parser requires
  admission context for every phase/terminal frame, binds envelope and exact
  bundle-derived phase identities/fences, rejects inconsistent supplied phase
  context, and validates exact-cardinality terminal links against supplied
  canonical `PhaseFinished` receipts before deriving precedence. Public
  context-dependent models fail closed when their admission, phase-spec,
  observation-policy, or canonical-receipt validation context is absent; direct
  construction is not a second lenient validation authority. See
  `docs/agent-guides/agent-executors-and-permissions.md`.

- **Linux systemd supervision (THR-327 seq187/195).** The foreground `cmd/happyranch-tsnet-sidecar` loads validated JSON, waits for bounded `tsnet.Server.Up` and the existing connector probe/listener gates, then emits one `READY=1`. It sends `WATCHDOG=1` every `WATCHDOG_USEC/2` for the selected `WATCHDOG_PID`; SIGTERM/SIGINT emits `STOPPING=1` and stops the sidecar. Startup or serving failure exits nonzero. The Python connector waits for initial readiness under `TimeoutStartSec=120`; after READY, readiness or listener/thread loss closes admission and exits nonzero, with no in-process re-arm or second READY. Healthy persisted-revocation rotation remains inside the lifecycle boundary. Both rendered services use `Type=notify`, `Restart=on-failure`, `RestartSec=1` and `WatchdogSec=30`. The sidecar and legacy/other connector renderings retain `StartLimitIntervalSec=300`/`StartLimitBurst=5`. The fixed managed-identity connector uses `StartLimitIntervalSec=0` (no start-rate limit), with `StartLimitBurst=5` still rendered; see [THR-327 renewal unit upgrade](#thr-327-renewal-unit-upgrade-task-19228) for its retry and existing-host upgrade behavior. For the limited system-mode connector, crashes after READY get the full five starts in 300 seconds; when that connector fails before READY, the `Upholds`/`BindsTo` pair adds start-limit checks, so restarts can be refused after as few as three real starts. That limited unit stays failed until the same explicit `reset-failed`/start recovery; a refused unit may report `Result=signal`, and its journal’s `Start request repeated too quickly` line identifies the refusal. The prior 30/60/120s backoff (3/600s) belonged to the removed Go supervisor and is superseded; systemd is the only supervisor. The sidecar has `After=` and `BindsTo=happyranch-connector.service`; the system-mode connector adds `Upholds=happyranch-tsnet-sidecar.service` (systemd >= 249) so connector recovery brings the sidecar back. Both have `PartOf=happyranch-managed.target`, whose `Wants=` names both; intentionally stopping the target stops the connector and its Upholds relationship. Existing hardening stays; only the sidecar adds `AF_NETLINK` for tsnet Linux interface monitoring. The bounded managed installer and passing initial systemd M1 lifecycle are described below. Consumed-credential restart admission is implemented (THR-327 seq187/227): after the existing no-symlink and owner-directory gates, a regular, current-user-owned `0600` marker with the exact SHA-256 digest of `RoleIdentity + NUL + ControlURL` admits restart with an empty auth key only when credential `Lstat` returns ENOENT. Any present credential entry (including a dangling symlink), invalid marker or other lookup error refuses without reading the credential. Marker ENOENT retains the one-time validate/read/redeem/durable-commit/delete enrollment path. The production tsnet adapter refuses enrollment and restart before tsnet construction if `TS_AUTHKEY`, `TS_AUTH_KEY`, `TSNET_FORCE_LOGIN` or `TS_DEBUGSYSPOLICY_AUTH_KEY` is set, including empty values; its error names the variable and asks that it be unset without exposing its value. Only the explicitly passed credential may supply an auth key; restart reuses the configured persistent node state. Restart never re-runs consumption or rewrites the marker and still requires engine start, the existing receipt gates (including durable state and expected-peer visibility), connector probe and listener success. Marker reads use nonblocking/no-follow opens and verify the opened regular file. Real systemd/tailnet restart and reboot acceptance remain **UNEXECUTED** and outside M1.
- **Trust-state publication consistency (THR-327 seq187/227).** `AtomicFileTrustStateStore.load()` holds `LOCK_SH` on the existing owner-only stable `<state>.lock` inode; `transaction()` and `save()` hold `LOCK_EX` across snapshot and anchor publication. Lock acquisition opens without following symlinks or blocking on special files and requires a regular, single-link, current-owner, owner-only inode distinct from the snapshot and configured anchor. Unsafe locks refuse with `StateStoreError`; existing lock metadata is never repaired, and pair-file metadata is never changed by reading. A reader cannot sample a writer's in-progress pair. A load on the same store by the thread already holding the exclusive lock reuses that ownership; other threads/processes still acquire their own shared lock. Readers use the existing bounded acquire and fail closed with `StateStoreError` on contention, never a default/empty state. Every stable-pair validation stays: partial, corrupt, torn, replayed, generation/digest-mismatched, symlinked and loose-permission state still refuses, including when a lock alias targets a loose pair file. A crash between publications leaves a genuinely torn pair that still refuses after the kernel releases the lock. Pairing and request semantics stay unchanged; there is no client-request retry. Post-READY readiness/listener loss still stops admission and exits nonzero. Both loss exits emit the failing gate names and provider/listener/thread booleans to the journal and STATUS, without exception text, paths, state values or credentials.

- **Managed Linux installation (THR-327 seq187 item 2, seq195/207/227).** `python -m runtime.remote_access.installer install --enrollment-key-file /absolute/input.key` is a root operator action on a prepared host. Binaries, Python environment and validated connector/sidecar configuration must already be provisioned at `/opt/happyranch/` and `/etc/happyranch/`; the installer creates their directory layout but does not populate payload/configuration. The explicit transient enrollment handoff is described below. It creates or validates the dedicated non-root `happyranch:happyranch` account with `/nonexistent` home and a nologin shell. Payload/configuration directories are root-owned `0755`; service state/log/runtime directories belong to that account and are created with the shared renderer's systemd modes (`0755`, except sidecar state `0700`). The installer creates `/var/lib/happyranch-connector` at `0755` and accepts existing `0755` or the owner-only `0700` set by real trust-state saves. The installer's own validation and writes never chmod existing directories or widen their permissions. The credstore mode exception is described below; managed-directory mode requirements remain exact, and group/other-writable directories are refused except for the exact unmanaged `/var/log` ancestor described below. Before any account-creation or systemctl command, install and uninstall inspect all existing destinations for safe modes, types, no-follow traversal and validated ownership. If the service account is absent, an existing service state/log/runtime directory is refused because its required owner cannot be validated; a fresh install can create the account only when those service destinations are absent. The three root-owned `0644` units are exactly the shared renderer's connector, sidecar and `happyranch-managed.target` output. Literal descriptor-relative traversal and exclusive no-follow writes refuse linked/special files, loose/wrong ownership or modes, and differing pre-existing units. Matching units can be reused; there is no transactional upgrade, journal, rollback or interrupted-install recovery.

  The only unmanaged-ancestor exception is the exact path `var/log` relative to the installation root (`/var/log` on a live host): uid `0`, gid resolved from the local `syslog` group, and no world write; group write is permitted for that ancestor only, including stock Ubuntu 24.04 `root:syslog 0775`. The existing root:root, non-group/other-writable case remains valid. No other gid (including `adm`) is added, and a missing `syslog` group grants no exception. Members of `syslog` are trusted to manipulate log-directory entries, including moving or replacing service log directories; this is not a concurrent-entry-mutation fence. Every managed directory, including `/var/log/happyranch-connector` and `/var/log/happyranch-tsnet-sidecar`, and every other ancestor retain their existing ownership/mode rules. Literal descriptor-relative no-follow traversal remains mandatory. Ubuntu's [Noble rsyslog configuration](https://raw.githubusercontent.com/rsyslog/rsyslog-pkg-ubuntu/master/rsyslog/noble/v8-stable/debian/00rsyslog.conf) defines this distribution-owned ancestor.

  The unchanged connector unit declares `StateDirectory=happyranch-connector` without `StateDirectoryMode=`. TASK-15125's review cites systemd v259's [0755 default](https://github.com/systemd/systemd/blob/v259/src/core/execute.c#L655) and [mode application](https://github.com/systemd/systemd/blob/v259/src/core/exec-invoke.c#L3184): systemd's `StateDirectory=` handling may reset an existing `0700` directory to `0755` at service start. The shipped renderer retains that handling; the installer itself does not chmod existing directories. Restart-time mode-reset behavior remains **UNEXECUTED** and outside M1; the passing initial install/request/uninstall run does not test that transition. Fake-command mode-retention evidence covers only installer-owned operations.

  Install reloads systemd, enables and starts the target, then checks each written unit's on-disk SHA-256, exact `FragmentPath`, empty `DropInPaths`, `NeedDaemonReload=no` and `ActiveState=active`. Blocking startup of the rendered `Type=notify` services supplies the systemd READY boundary; the grouping target is not a notify service. There is no exact pre-start loaded-property comparison. A start/drift failure requests target stop and reports any stop failure, without restoring files or disabling the boot enablement; operators must inspect partial state. `check-drift` repeats this observation without changing services. `uninstall` stops the target, then explicitly waits for the sidecar and connector service stops before disabling the target, removing matching safe unit files and reloading systemd. A target-only stop can finish while its `PartOf=` services are still stopping; removing their unit files and reloading early discards the runtime-directory cleanup configuration. The blocking service stops keep that configuration loaded through shutdown for orderly stopping and systemd removal of `/run/happyranch-connector`. The sidecar's `RuntimeDirectoryPreserve=yes` preserves `/run/happyranch-tsnet-sidecar` and its `health.json` diagnostics (THR-327) across stop; receipt-bound B6 Turn off retires only `health.json` and numeric `.health-` temp names under I5f H1 before empty-only rmdir. The directory is cleared at reboot and is not explicitly removed by uninstall. Stop failure aborts before unit removal, reload or state purge. Payload, configuration, service logs, account and persistent state are retained. Only `uninstall --purge` removes the two service state directories; linked/special/foreign-owned state is refused. The bounded enrollment lifecycle and passing hosted M1 receipt, produced with the earlier renderer that removed both runtime directories on stop, are described below. Temp-root/fake-command evidence and static unit verification remain distinct from that live initial READY/uninstall result and do not establish tailnet, restart or recovery acceptance.

  The root operator supplies `install --enrollment-key-file /absolute/input.key`; the input must be a root:root, single-link regular `0600` file, nonempty and at most 64 KiB. Literal no-follow parent traversal and nonblocking/no-follow file opens refuse symlinks and special files. Every install, including reinstall, requires explicit input; this command does not infer a keyless reinstall from persisted state. Use ordinary service restart for an enrolled host; installing a new key over a consumed marker still refuses at the existing marker-plus-input gate. The customer cell issues the single-use, short-expiry key; the installer does not provision it. The original operator input remains the operator's responsibility to remove promptly.

  The installer first creates and fsyncs the root-owned link `/etc/credstore/happyranch-tsnet-enrollment` pointing only to `/etc/happyranch/enrollment.key`, then exclusively stages that fixed source as root:root `0600` and fsyncs it and its directory. Cleanup unlinks and fsyncs the source before removing the link, so interrupted staging/cleanup preserves lookup for any surviving source. `/etc/credstore` is created root-owned `0700`; existing safe `0700` or `0755` is retained. The credential name is reserved to this installation: no inherited or other credstore credential may use it. The sidecar renders `LoadCredential=enrollment-key:happyranch-tsnet-enrollment` and `--credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key`, overriding the JSON credential path before validation. Only the path enters argv; the key never enters argv or environment. Credential-name lookup, unlike an absolute missing source, is nonfatal when absent in both [systemd 255](https://www.freedesktop.org/software/systemd/man/255/systemd.exec.html#LoadCredential=) and this host's systemd 259. After source/link deletion, systemd may also remove the empty per-unit credential directory while still exporting its path in `CREDENTIALS_DIRECTORY` ([v255 directory removal](https://github.com/systemd/systemd/blob/v255/src/core/exec-credential.c#L927-L932), [environment export](https://github.com/systemd/systemd/blob/v255/src/core/exec-invoke.c#L1881-L1891)). The sidecar handles that absent-directory state without creating a directory or an empty credential file. A remaining source/link yields a present copy, which the existing consumed-marker gate refuses.

  Only an exact `CredentialFile == $CREDENTIALS_DIRECTORY/enrollment-key`, with an absolute, clean, non-root credentials directory, selects systemd input semantics. The copy must be regular, single-link, read-only `0400` (or `0440` for systemd's ACL read mask), owned by root or the service user; path no-symlink checks and opened-inode rechecks remain. Only for this exact managed path, ENOENT on the per-unit credential directory itself also counts as absent input; every ancestor above it must still exist as a directory without symlinks. Foreground missing parents, deeper missing ancestors, symlinks and other lookup errors refuse. A valid consumed marker then permits empty-key restart without rewriting the marker; without a marker, absent input still refuses enrollment. Enrollment still requires the engine receipt gates and durable marker commit, but never unlinks or fsyncs the read-only copy's directory. systemd retains that service-private copy until teardown. Foreground inputs retain current-user-owned `0600` validation and deletion plus directory fsync after commit. Marker validation, marker-plus-present-credential refusal, empty-key restart, ambient tsnet-key refusal and all readiness gates remain.

  After both notify services are READY and the existing drift checks succeed, the installer unlinks the fixed source and credstore link and fsyncs both directories. Any failure after staging also attempts this cleanup, including reload/enable/start/drift and stop failures; start/drift failure still requests target stop. Cleanup refusal is an explicit installation error naming the two fixed paths and requiring operator revocation/inspection. Stale enrollment residue refuses reinstall before commands; uninstall removes safe source/link residue before stopping services, with or without `--purge`. A crash or unsafe replaced entry may leave residue requiring explicit operator care; there is no journal or racing-root-writer fence. Retry requires a freshly issued key. The temporary source is the bounded single-use enrollment handoff, not persistent configuration storage. M1 below exercised the initial systemd credential handoff; consumed-state restart and reboot acceptance remain **UNEXECUTED** and outside M1.

- **Managed remote access N0/N1/N2 amendment (THR-097 seq360/seq374/seq379/seq392).** The managed transport value is `embedded`: packaged macOS tsnet/userspace WireGuard is designed to connect to the Linux sidecar in `app/linux/tsnet-sidecar/`, which proxies raw TCP only to the N2 Python connector's explicit `managed` listener on literal `127.0.0.1`. `runtime/remote_access/managed_provider.py` reuses the existing pairing and locked gateway pipeline; the connector alone owns application identity, credentials, epoch, allow-list/route policy and revocation and injects the daemon bearer only on the final `127.0.0.1:<resolved-port>` hop. The N1 core fail-closes credential redemption/listener startup and removes external admission before active connections and engine state; persisted N2 revocation likewise stops the listener first, then seals/closes active flows, then performs downstream runtime cleanup, retaining failed cleanup handles for idempotent retry. Supervisor provider start, READY publication, persisted-revocation reconciliation, and shutdown share one re-entrant lifecycle boundary: either provider start completes first and stop follows it, or stop/revocation completes first and late start is refused/idempotent; partial start is cleaned without retained provider/registry state. Shutdown observably stops the listener, closes registered active flows, and drops cached registry/pairing runtime state exactly once, so repeated shutdown leaves no listener, active flow, or runtime residue. N2 adds no address selector or fallback and exposes readiness only after the existing connector gates pass and the loopback listener binds. The checksum-pinned N1 dependency/license artifacts are unchanged. The original N1/N2 slices defined the core/ingress boundary; the later THR-327 managed Linux command, composite supervision, installer and enrollment handoff are now shipped. M1 below passed the bounded initial systemd lifecycle. Tailnet transport (M2), full-daemon acceptance, restart/reboot recovery and deployment remain **UNEXECUTED** and outside M1; broader provisioning/distribution and N3–N6 obligations remain open. Neither endpoint requires system Tailscale; wildcard, LAN/plaintext, public-Tailscale, and public-DERP fallbacks remain forbidden. TASK-6298/TASK-6289 remains unchanged Supported-DIY, and Unit 4B-2 remains independent.
- **Manual managed M1 (THR-327 seq187): PASSED.** [Hosted run 42978877282](https://github.com/t-benze/happyranch/actions/runs/42978877282) completed successfully on 2026-11-11 at main `455c9c5b689fa25f625e2df8cae1b551c2a1de86` (short `455c9c5b`), through install, both READY services, paired HTTP 200, product uninstall/purge and every residue assertion. This receipt belongs to that source head; later heads are not claimed rerun. `.github/workflows/managed-m1.yml` runs only on `workflow_dispatch` on a disposable Ubuntu 24.04 hosted runner. Its short Bash driver, `scripts/ci/m1_install_ready_uninstall.sh`, accepts an absent `/run/happyranch-tsnet-sidecar` or a retained real directory owned by `happyranch:happyranch` containing only an optional regular, non-symlink `health.json`, and provisions the rendered units' binaries/config, a disposable Headscale cell and expected peer, and a synthetic loopback HTTP backend; it invokes the product installer with a fresh single-use ten-minute file key, observes both notify services active/running, checks drift, makes a paired connector-loopback request, then uninstalls with `--purge` and checks documented residue. Payload, configuration, service logs and account are retained as the installer documents; runner disposal removes the fixture. Before the request and in always-run diagnostics, the script records runtime-directory owner/mode/type and loaded runtime directives; diagnostics also include manager reload timestamps alongside plain unit journals/status/cat and installer streams before stopping fixtures and uploading logs. This is separate from required lint/static checks and daily Product E2E. The passing run removed unit files/target enablement, enrollment source/link/operator input, persistent service state and both runtime directories. **M2 tailnet transport, full-daemon acceptance, restart/reboot recovery and deployment remain UNEXECUTED and outside M1.** The HTTP request enters the connector on loopback and uses a synthetic backend; it does not traverse the tailnet data path or run the full daemon, and initial startup/uninstall does not prove restart/reboot recovery.

- **Manual managed M3 same-boot recovery (THR-327): UNEXECUTED.** Acceptance is defined by `.github/workflows/managed-m3.yml` and `scripts/ci/m3_recovery.sh` and remains unexecuted until the main-only manual workflow passes. The harness reuses M2 provisioning, READY, paired real-daemon tailnet request, logs, teardown and purge residue checks. Its M2 config selects the legacy connector rendering, so its finite-limit assertions do not describe the current managed-identity connector; see [THR-327 renewal unit upgrade](#thr-327-renewal-unit-upgrade-task-19228). It tests connector SIGKILL, sidecar SIGKILL with the same Headscale node ID/IP/count and consumed enrollment (no new preauthkey), connector SIGSTOP/watchdog, intentional stop/start, shipped start-limit exhaustion/reset/start, daemon-fixture restart without reinstall, then uninstall/purge. Each recovery requires both notify services active/running and a paired tailnet HTTP 200, with per-step MainPID, NRestarts, Result and journal evidence. Step 5 crashes the running (READY) connector exactly five times and proves start-limit refusal from the connector journal. Only steps 4 and 5 explicitly start product services after a fault; `BindsTo=` leaving the sidecar stopped is a failure. Both units’ rate accounting is reset at the start of steps 1–4 and 6 to isolate faults. Reboot is outside M3 and remains **UNEXECUTED**.

- **Manual managed M2 (THR-327 seq247): PASSED at main `8165e05d10a0a50238ccaa86aa6d79ed859e4428` and, after the readiness-wait fix, at main `5d5fa8662b3a566fc408d1c65a4a3e4d62aae463`.** [Hosted run 43004289479](https://github.com/t-benze/happyranch/actions/runs/43004289479) passed all seven M2 steps on 2026-11-11 on a fresh Ubuntu 24.04 runner: product install with both services notify-READY and check-drift; paired tailnet → sidecar → connector → real-daemon org teams returned 200; absent and never-paired credentials returned 403 with exactly `{"error":"identity_denied"}`; a second device returned 200, was revoked through the product CLI, then returned 403 with exactly `{"error":"identity_denied"}` after the bounded reconciliation wait. All six forbidden routes returned 403 with their exact categories: executor registration and adapter deletion returned `method_denied`; custom-CLI commit, runtime/use, org deletion and auth bootstrap returned `route_denied`. The synthetic org remained loaded and readable after forbidden DELETE. Product uninstall `--purge` passed the M1 residue rules, and plain logs were uploaded with `if: always()`. The test-owned real `python -m runtime.daemon` uses a disposable home/token and synthetic org; `ExpectedPeer` is startup visibility, not per-connection node authorization. Both manual workflows cache the pinned Headscale binary and Tailscale archive under version+SHA-256 keys; both drivers verify checksums on hits and misses before use and download only absent files. [Rerun 43004588741](https://github.com/t-benze/happyranch/actions/runs/43004588741) at the same head passed step 1 but failed at step 2's first pairing request with curl exit 97 before HTTP: the peer had netmap visibility before endpoint discovery. That run proved cache hits, successful checksum verification and plain-log upload on failure. The M2 script now waits up to 60s for a TSMP ping through WireGuard before its first tailnet request, failing with the last ping output on timeout; HTTP outcomes and assertions are unchanged. After that fix merged (PR #1408), [hosted run 43011862585](https://github.com/t-benze/happyranch/actions/runs/43011862585) and [repeat run 43012067135](https://github.com/t-benze/happyranch/actions/runs/43012067135) at main `5d5fa866` each passed all seven M2 steps on 2026-11-11 with the same results listed above, including cache hits and successful checksum verification for both pinned downloads. At the same head, [M1 run 43012072443](https://github.com/t-benze/happyranch/actions/runs/43012072443) passed with the cached Headscale/Tailscale downloads. These receipts belong to `5d5fa866`; later heads are not claimed rerun. Main-only `workflow_dispatch`, M1's receipt above, required lint/static checks and daily E2E are unchanged. Restart/reboot recovery and deployment remain **UNEXECUTED**.

- **Managed remote access (normative contract, THR-097 merge unit A).** Fixed invariants: one Headscale cell per customer is the primary hostile-tenant boundary; the shared DERP fleet observes/relays only network metadata and WireGuard ciphertext and never grants reachability; the home connector is a **supervised portable Python companion** (never an in-process daemon listener) that enforces an explicit allow-by-method+normalized-template remote allow-list, current paired/current-device identity, and current policy/revocation epoch, failing closed; the daemon stays **loopback-only**; the daemon bearer is injected **only** by the connector on the final connector→127.0.0.1 hop and must never appear in remote input, client, Services, Headscale, DERP, network, fixture secrets, logs, errors, or audit. Revocation denies/closes before or atomically with node removal; never success early. Governing spec: `docs/specs/2026-08-26-managed-remote-access-contract.md`; shipped route policy: `runtime/remote_access/route_policy.json` (118 allow entries; THR-327 R1 v1 excludes executor-binary registration, custom-CLI commit, runtime use, adapter deletion and org deletion; required Source Boundary Lint checks policy consistency); hostile threat fixtures: `tests/contract/managed_remote_access/` (consumed by the retained hostile lab; the former Python contract validator is retired). `route-classification.json` `included` (web coverage) is NOT remotely-allowed-equivalent; Swift `SurfaceAllowList` is legacy deny-list evidence, not authority. **Implementation status:** the portable supervised Python connector core (merge unit C / TASK-5724 phase unit 2) ships in `runtime/remote_access/` with a loopback-only harness and tests at `tests/remote_access/` — strict parser/canonical normalization, versioned route-policy consumer (locked nine-step decision order + every security-relevant nested value validated by exact canonical equality against the Unit-A semantics — contradictory non-empty prose and altered allowed-template lists rejected at load — plus operational state; schema/digest/version/staleness drift fails closed; unknown states never active), connector identity + device-proof verifier seam, current authorization/revocation with live-stream closure via one authoritative `RevocationCoordinator` transaction (the registry seals the retained stream wrapper fail closed before trust-state application — a raising transport close leaves the externally retained handle irrevocably rejecting receive/send, surfaced as `RevocationIncomplete`; the transaction is serialized across concurrent revocations and the stream registry shares/persists the complete cleanup terminal result, so no caller returns success while an in-flight or completed cleanup failure relevant to the sealed generation is unreported; old public `apply_revocation` bypass removed; admission and EVERY ownership/membership mutation — `open` admission, duplicate-id replacement, `close(stream_id)`, the retained-wrapper public `close`, and `close_all` — share ONE atomic lifecycle boundary: the lifecycle lock guards both the sealed flag and registry membership, every mutation seals the affected wrapper(s) atomically with its membership transition (no pop-without-seal ownership escape), any admission not fully registered before the seal fails closed and never returns a usable wrapper, transport open/close and duplicate-replacement callbacks never run under the lifecycle lock but remain INSIDE the revocation acknowledgement barrier (outside-lock transport closes that linearized before the seal are terminal before `close_all` success — revocation never reports success early), and same-thread re-entrant `close_all` from an unfinished transport cleanup callback MUST return fail-closed non-success (founder ruling THR-097 seq140): it may not exclude its own in-flight cleanup and publish success, may not mark the cleanup terminal with an incomplete failed-id set, and may not erase a callback failure that becomes terminal after the re-entrant call — the failure is persisted on the registry and re-surfaced by a later close_all/revoke (REV-007/REV-008), and derived `is_open`/`raise_if_open` are synchronized on the same lock; the gateway never double-closes a handle the registry owns), allow-list enforcement, remote-auth/hop-by-hop stripping, daemon-credential-provider seam, and a forwarder that targets only literal 127.0.0.1 and normalizes every forward/open/stream failure (connection refusal, timeouts, hostile exception text) into stable secret-free denials while deterministically closing partial resources; in-memory persistence abstraction only, no tailnet/external bind, no Headscale/DERP/Services integration, no packaging. **Linux supervised connector packaging (phase unit 3) is IMPLEMENTED** in the same package: an atomic, corruption-detecting, owner-only local trust-state store (``AtomicFileTrustStateStore`` satisfies the ``TrustStateStore`` protocol with a schema-agnostic non-normative envelope — atomic replace/fsync, sha256 corruption detection, symlink/loose-permission rejection, plus a founder-approved (THR-097 seq163) **non-database companion monotonic generation/digest anchor** at ``<state>.anchor`` outside the replaceable snapshot: the envelope generation must equal the anchored generation AND the anchor digest covers the exact snapshot bytes, so any previously valid OLDER snapshot replayed after a newer revocation/generation — including across a new store/process instance — is deterministically rejected, and missing/mismatched/corrupt/stale anchors, partial snapshot/anchor pairs, symlinked anchors, and loose anchor permissions all fail closed; a crash between the snapshot and anchor writes leaves a mismatched pair that load() rejects (the crash-consistency contract is documented in the module); NOT the founder-gated managed persistent schema — no DB, no migration), a ``SystemdCredentialProvider`` (``LoadCredential=`` injection — the service user never reads the daemon home), a five-gate readiness evaluator (``ConnectorReadiness``: daemon literal-127.0.0.1 reachability, credential permissions, current policy, bind identity, non-corrupt trust state — **no listener unless ALL pass**), a least-privilege systemd unit renderer (dedicated service user, empty capability bounding set/ambient capabilities, ``ProtectSystem=strict``, ``NoNewPrivileges``, ``LoadCredential=``, ``Type=notify``+``WatchdogSec``+``Restart=on-failure``; user-mode renders omit the directives the user manager cannot apply — ``PrivateDevices``/``ProtectKernelModules``/capability directives — verified on real systemd), an injectable ``SystemdServiceManager`` (install/uninstall/start/stop/restart/enable/disable/status + upgrade with auto-rollback and rollback), a **LAB-ONLY** conformance provider adapter (explicit ``lab_only`` + concrete lab bind address required; wildcard binds refused; runs the full gateway pipeline and forwards to literal loopback with bearer injection on the final hop; 403 category-only denials; never a product/Supported-DIY lane and NOT closure of THR-034), a ``ConnectorSupervisor`` (readiness-gated foreground loop with sd_notify READY/WATCHDOG/STOPPING — ``READY=1`` is emitted ONLY after the provider actually started and the listener is proven bound, never after a bind/start failure; expected operational listener failures — occupied bind port, permission, unavailable address — are normalized from the socket ``OSError`` to the documented ``LabProviderError`` category at the ``LabProviderAdapter.start()`` boundary (category-only message), so a REAL bind conflict keeps the loop retrying with ``STATUS=provider failed to start; no listener`` and no process exit (no systemd restart-throttle; the listener comes up once the conflict clears) while unexpected defects still propagate loudly; fail-closed listener stop on readiness loss; redacted ``diagnose``; ``install`` stages the service config/policy/state into the declared systemd-managed ``StateDirectory`` location accessible to the dedicated service user and the rendered unit points ``--config`` there — never at a ``~/.happyranch`` path the hardened unit cannot read; the source trust-state pair is only an INITIAL SEED when no managed pair exists — once a managed snapshot+anchor pair exists it is AUTHORITATIVE and reinstall never overwrites/rolls it back with a stale operator source pair (stale = refused, same-generation-different-bytes = refused, identical = no-op, strictly newer = adopted) and stages any advance transactionally (fail closed, no usable mixed pair); the service path automatically consumes ``$CREDENTIALS_DIRECTORY``/``LoadCredential=`` with no redundant config and no daemon-home token read; a provider-less ``run`` configuration is rejected at startup (fail closed, never ``READY=1``)), and the ``python -m runtime.remote_access.cli`` operator surface (run/install/uninstall/start/stop/restart/enable/disable/status/readiness/diagnose/upgrade/rollback; ``run`` requires ``--lab-only`` when a lab provider is configured and refuses to run with no provider at all). Real user-systemd conformance tests run under ``-m integration`` gated on an operational probe with explicit skip reasons: one launches the **ACTUAL rendered ``ConnectorSupervisor``** through the real ``Type=notify`` READY contract and proves the lab listener is bound, an end-to-end request reaches the literal-loopback daemon, restart, and stop-with-listener-gone (upgrade/rollback round-trip, enable/disable, daemon-reload also run against the real user manager); unit tests are deterministic with fakes. The hostile tenant-isolation harness (tranche unit B) ships at `labs/tenant_isolation/`. Provisioning/defaults/retirement remain unimplemented and outside any current PR; the capacity spike (tranche unit D) is deferred with no claim. **Supported-DIY customer-owned-network adapter (phase unit 3A) is IMPLEMENTED** (TASK-6032, fix-forward TASK-6039) as the PRODUCTION Supported-DIY lane in the same package: a customer-network address resolver/validator (`network.py` — the ENCRYPTED `tailscale ip -4` mode ONLY; the former plaintext explicit concrete-address mode is REMOVED and fails closed with a migration message, because bare HTTP on an arbitrary LAN/public interface would breach the fixed no-plaintext-service-path invariant — the customer-owned network is the customer's own WireGuard-encrypted Tailscale/headscale tailnet; wildcard/loopback/multicast/link-local/reserved/broadcast refused, fail closed), the local pairing ceremony engine (`pairing.py` — single-use expiring 8-char codes stored as digests only; redemption issues per-device `hrpair_` credentials at a MONOTONIC epoch, re-pairing invalidates old authority, and every ceremony mutation is SERIALIZED in-process AND across processes through the store's owner-only inter-process transaction (an fcntl.flock on `<state>.lock` making load, generation validation, snapshot publication, and anchor publication ONE serialized mutation boundary — the check-then-save race is closed, so concurrent redemption of one code from threads OR separate processes yields EXACTLY ONE credential, concurrent mutations are never lost, and the snapshot+anchor pair is always published consistently and loadable); revocation through the authoritative `RevocationCoordinator` closes live streams before the persisted epoch advance, per-device and all-devices revocation survive restart, `remove-device` REVOKES FIRST (streams close, epoch advances, persisted) and THEN deletes the record — a removed/lost device can never retain an open stream and the revocation survives restart — and removal denies identically to absent — no existence oracle), the production `DiyProviderAdapter` (`diy_provider.py` — binds ONLY the tailscale-resolved customer-owned-network address, serves the inherited THR-034 wire contract `POST /pair` + `X-HappyRanch-Device-Credential`, consumes/strips the credential at the boundary so it never reaches the daemon, runs the full locked gateway pipeline per request, forwards to literal 127.0.0.1 with bearer injection on the final hop), shared secret-free HTTP serving (`httpd.py` — the lab and diy adapters share one serving path; the lab provider is UNCHANGED in role and remains LAB-ONLY), additive OPTIONAL trust-state envelope fields (`credential_digest` on device records, `pending_pairings` in the payload — pre-3A envelopes still load; digests only, never raw credentials), `ConnectorConfig.diy` (mutually exclusive with lab; `run` requires the explicit `--diy` opt-in; provider-less run still rejected, never `READY=1`), a supervisor that owns ONE authoritative live-stream registry and ONE pairing manager per process shared by the gateway ctx factory, the provider, and the ceremony surface (in-process revocation closes the REAL shipping streams, never an unrelated empty registry), a cross-process revocation reconciliation in the `run` loop (a CLI `revoke`/`remove-device` in a separate process persists the epoch; the connector closes its live streams on the next loop pass bounded by `poll_seconds`, fail closed, and — because the registry is ONE-SHOT — ROTATES the authoritative runtime at a fail-closed lifecycle boundary the moment the registry is sealed: the listener is stopped first (no request/stream is admitted during the handoff), the cached registry and pairing manager are dropped, and the provider and every captured ctx-factory reference are rebuilt and restarted only when readiness passes, so a re-paired or unaffected device opens NEW streams in the same process lifetime and a revoked-with-no-live-streams revoke causes no listener gap), and the CLI ceremony surface (`pair`/`list-devices`/`revoke`/`remove-device`/`pairing-status`/`recovery --factory-reset` — truthful secret-free lifecycle states; `revoke` NEVER claims cross-process stream closure it cannot prove — it reports the persisted epoch and the connector's next-reconciliation closure; recovery deletes BOTH snapshot+anchor files per the store's crash-consistency contract). A REAL-network acceptance (`tests/remote_access/test_diy_acceptance.py`, `-m integration`) runs the away-client wire contract over a real network path to the real `cli run --diy` connector and a real loopback daemon — the customer-network address resolved through the REAL shipping tailscale-mode resolver path with a stub `tailscale ip -4` executable (no tailnet on the host) — proving allowed/forbidden routes, direct daemon/bearer attempts, restart-with-persisted-revocation, re-pair invalidation, replayed/removed credentials, outage fail-closed listener stop + supervised recovery, credential/token leakage scans, and (fix-forward) a GENUINE cross-process SSE negative: a live in-flight stream served by the connector process is closed by a `revoke` from a separate CLI process within one poll interval, and the CLI output carries no false success. The signed-macOS-binary and tailnet-transport hops remain honest residual gaps (this Linux host has no macOS build and no Tailscale client/network; THR-034 signed acceptance is NOT closed), and the macOS production app is unchanged by this PR.

- **VM reboot acceptance (THR-327 goal B).** The authorized VM-only driver `scripts/ci/m3_reboot.sh` and host runner `scripts/ci/m3_reboot_aliyun.py` extend M2/M3 with two clean passes and two reboots per pass. Persistent test units/state, saved pairing and Headscale identity/enrollment checks, automatic notify-READY, disabled-target inactivity for 30 seconds, explicit re-enable and purge residue are described in [the runbook](../../operations/ci-m3-reboot.md). Source delivery alone is not VM acceptance; exact-head phase/boot/identity receipts are required. No product unit or GitHub trigger changes.

- **M4 native macOS outside-client procedure (THR-327 goal C).**
  [The M4 runbook](../../operations/ci-m4-tailnet.md) owns the main-only
  manual `managed-m4.yml` workflow and host Cloud Assistant coordinator. The
  GitHub-hosted Mac builds the unchanged shipped Go C archive and uses its C ABI
  headlessly against trusted443/private DERP999/STUN3478 on the pinned HK VM.
  Existing exact200/403 paired controls, revocation/unaffected controls, forbidden
  routes, actual DERP evidence and application-level exposure checks remain.
  Public-key artifacts and run/actor/key/phase/expiry-bound OAEP ciphertext on
  PR1449 comments need no new secret/setting/scope/port; the runner gets no cloud,
  Headscale admin or daemon bearer. Plaintext enrollment/pairing stays in runner
  memory/private pipes. Public API quotas/account trust remain explicit limits.
  Empty Headscale membership (`null` or `[]`) continues the bounded join wait;
  malformed/schema-invalid responses and unexpected members still refuse.
  The premerge VM-half asserts empty membership through the shipped nodes phase.
  Public failure evidence separates handoff/decrypt/native-process/C-init/C-start/
  join-wait timings, allowlisted exception classes, return codes and fixed-label
  join diagnostics before scratch removal.
  The DERP gate now uses home UDP enforcement; see the TASK-15886 amendment
  below. Authenticated singleton identity and exact real-daemon200 remain jointly
  required. Missing log evidence alone remains non-gating; contradictory region
  evidence vetoes. Native attempt9 **PASSED**; see the acceptance history below.
  Public exposure uses the exact Headscale401 fingerprint defined in the
  [M4 runbook](../../operations/ci-m4-tailnet.md).
  C init/start failures also retain the allowlisted `bridge_error_class` from
  the existing C error accessor, with raw text withheld and unknown patterns
  mapped to `unknown`. The native runner exclusively creates the0700 target
  before its state link; Bridge verifies directory type, owner and0700 mode
  before worker start. Existing/foreign/legacy state refuses; exact-link removal
  and scratch deletion remove the transient state.
  The unchanged archive is statically linked into the harness's native main
  executable `m4-tsnet-host`. An existing C export waits for Go initialization
  before loading the driver's existing Python runtime for the private pipe
  protocol; ABI lookup uses the main executable, never a Go dylib in Python.
  Darwin state/legacy names are `tsnet-m4-tsnet-host`/`tslib-m4-tsnet-host`.
  Before recipient generation/publication, a zero-network precondition uses a
  synthetic key and deliberately malformed log config: init0/start-2 and the
  exact private JSON-decode error must project to `log-dir`. This stops before
  logger/network construction, then reaps the worker/removes the exact state
  link/target. Failure exports only the existing closed class/null and publishes
  no recipient. Later logger/engine success and Darwin execution are NOT RUN
  premerge. Attempt5 (TASK-15766/run43320896544/JOB-6663) remains failed/spent
  with `executable`; the captured Darwin path was empty, while the specific
  runner vector mutation remains inferred. No corresponding shipped-app failure
  is demonstrated; product/Swift execution and immunity are PROOF NOT RUN.
  Host wait entries retain safe labels, counts and elapsed time. `control_waits`
  is always a list: `[]` when no hosted
  wait occurred (VM-only premerge or Linux diagnose-only), otherwise genuine
  wait entries with `failure_label` (null on success), `observations` and
  `elapsed_seconds`. Empty lists carry no fabricated per-wait fields.
  No private text/argv/credential is exported; see the
  runbook for the closed projection. Failed native attempts remain spent and the
  original join cause remains unresolved; evidence repair grants no new run.
  Attempt4 (TASK-15753/run43310355906/JOB-6656) failed at C start-2 after C
  init0. A missing target at C start is refuted by the prior Bridge constructor;
  no native root cause or acceptance is established by this harness repair.
  The host observes the existing pairing marker before revoke. VM ownership
  markers permit logs/purge/teardown/clean-state only after positive terminal
  observation of every prior Cloud Assistant invocation. M4 retains InvokeIds;
  unproved settlement after60s additional read-only polling yields FAIL/UNCERTAIN,
  no further remote command or clean-state clearance, even with no marker.
  Root-only cache ledger3 of5 is retained,
  >24h-valid cache is required, static TLS disables ACME renewal, and zero new
  certificate issuance is permitted.
  VM stays Running. Workflow is nonrequired; daily E2E/PR triggers are unchanged.
  JOB-6610/6615 Linux join failures and JOB-6618 report-only trace failure remain
  recorded; the TUN-bypass hypothesis is still unproven. Linux client mode is
  diagnostic only, with no further TASK-15670 diagnostic allowance. Native Mac
  transport/GitHub handoff/route acceptance is **NOT RUN premerge**; static and
  VM-half encryption/cleanup receipts are separate evidence. Signed Mac.app,
  Swift and GUI on founder hardware remain a separate founder-assisted step;
  secure managed GUI enrollment needs a separately scoped product change, never
  an environment key. This is only the separate-network forced-private-DERP
  slice; all five normative topology/failure rows remain required.


### M4 home UDP enforcement amendment (TASK-15886)

The [runbook](../../operations/ci-m4-tailnet.md#home-udp-enforcement-proof)
owns the exact gate and controls. The pktap/tcpdump parser, observer self-test,
socket observations and manual observer-probe workflow are removed. PR1459
closed log diagnostics remain corroboration; contradictory regions still veto.

Acceptance jointly requires, in order: unchanged authenticated current-run
singleton999/expected endpoint/no-upstream/no-auto-update/no-mesh identity;
test-owned guest nft UDP DROP rules present under the same identity before and
after the window; a fresh native bridge GET with the exact real-daemon200 body
inside that window; and the unchanged client UDP-disable setting. Output matches
the service UID, input the socket's service cgroupv2; non-loopback IPv4/IPv6 UDP
is blocked without changing product code/units. The client acknowledges completed
response consumption over the existing orgs route/credential before the host's
after snapshot. Existing operator/run/attempt/SHA/key/expiry envelopes bind both
receipts. Closed labels and bounded integers only are public. Missing, stale,
contradictory or changed evidence refuses before separate post-proof200/exposure.
The original35s window and all existing deadlines remain; counters are informational.

Pinned Tailscale1.78.0 explicitly falls back to DERP when UDP/STUN fails and uses
TCP443 for DERP; a direct WireGuard path needs bidirectional UDP. Thus the working
peer request under home UDP enforcement used DERP, under the singleton identity.
This trusts home VM root/kernel and pinned process/config inputs; it is not an
in-memory map dump, an all-native-packet observation, signed-app or GUI proof.

Prepare/premerge exercises real ingress/egress positive+negative UDP controls
under a throwaway stand-in UID/cgroup on a private veth/namespace, using3478
without public ingress changes. It proves selector/counter behavior, not the
installed sidecar/native path. Controls remove their resources; teardown removes
the acceptance nft table and clean-state fails on residue. Unsettled F1 writers
still prevent cleanup. No product/bridge/ABI/dependency/auth/permission, SG/SSH,
credential/billing, TLS, binding, pairing or forbidden/refused-route change.
managed-m4.yml remains manual/main-only/non-required;
required CI and Product E2E are unchanged. Certificate ledger stays3/5, zero issuance.


TASK-15887 repairs the local-output control: Linux NF_DROP can return EPERM to
sendto. Only selected output sends may report that errno, and they still require
receiver timeout plus exactly one matching drop; every negative control must
arrive with zero drop increment. Other errors refuse. No package installation
was needed by the source repair; the initial retained clean-state had already
executed nft successfully. Distro nftables/iproute2 packages, if explicitly
installed on a disposable VM, are documented allowed package residue; this never
permits a leftover test table, account, unit, namespace or veth.

The guest and coordinator project UDP control diagnostics to fixed labels
(`passed`, `refused`, `unknown`), a closed stage subcause (unknown values become
`unknown`) and bounded non-Boolean integers only. The coordinator records this
as `udp_control` in summary.json. Raw exceptions/nft stderr stay in a temporary
0700 guest scratch directory and are erased before return, outside log archives.
An early prepare failure before any Headscale start attempt records journal
state `not applicable: fixture not started`; all collected entries still must
be current-run-only. Once fixture start is attempted, the nonempty Headscale
journal requirement remains mandatory; installed-product journal requirements
are unchanged. JOB-6727 remains failed/spent, with its original unknown UDP
subcause and consequent journal failure; this repair does not reclassify it.

M4 native attempt9 **PASSED** ([run 43404266680](https://github.com/t-benze/happyranch/actions/runs/43404266680))
on main `167307599bb6fa77a77b58757231faec57dd9ba9` (short `16730759`, PR1475).
Independent QA TASK-15908 recorded **PASS, 42/42** acceptance rows.
Native attempts1–8 remain failed/spent:

- Attempt1 (TASK-15684): first empty-membership poll returned `null`.
- Attempt2 (TASK-15690): no macOS capacity/steps.
- Attempt3 (TASK-15691): failed before observed membership/install; underlying
  decrypt/join cause remains unresolved.
- Attempt4 (TASK-15753): C init0, immediate C start-2; address wait not reached,
  actual cause unresolved.
- Attempt5 (TASK-15766): init0/start-2, `executable`.
- Attempt6 (TASK-15774): failed at derp-proof; post-proof200/exposure not reached.
- Attempt7 (TASK-15847): failed/spent.
- Attempt8 (TASK-15890/run43394234276): failed at DERP-exposure.

All five observer probes remain failed/spent: runs43368317931,43371075839,
43372501871,43373949599,43377704582. PR1472 stays draft/unmerged salvage; two
unused TASK-15883 probes are retired. Other failures remain spent: Linux
JOB-6610/6615/6618; premerge JOB-6641/6643/6644/6647; JOB-6699 and JOB-6722.
No allowance is replenished.

Custody boundary (engineering_manager TASK-15621): the no-raw-address/PID/tuple/payload rule applies to the packet-proof and capture-diagnostic public fields. The pre-existing reviewed pairs handoff payload synthetic tailnet address and VM service journal metadata are existing public surfaces and are outside that assertion; credentials, enrollment/pairing material and raw capture lines remain forbidden everywhere.

### macOS Go sidecar target (THR-360 C6a, PR 5)

C6a (PR 5) adds the Go-side Darwin/arm64 CGO-disabled target under the existing
`app/linux/tsnet-sidecar` module, using Go 1.23.12 and unchanged go.mod/go.sum.
The required `--readiness-file` flag is an absolute clean literal path; optional
`--watchdog-interval` defaults to 30s and must be positive. Linux refuses both
flags and keeps its existing sd_notify/WATCHDOG_USEC behavior. Runtime Darwin
selection uses C3's exact six-field JSON record and fixed reasons: starting,
healthy, waiting-for-readiness, readiness-or-listener-lost, stopping. Starting
is published before Server.Up; healthy requires all existing enrollment,
durability, expected-peer, loopback-probe and listener gates. Healthy publication
refreshes at half the watchdog interval, capped at 100ms. Listener loss publishes
false and exits nonzero; SIGTERM/SIGINT publish false/stopping before teardown.

C6-loss (PR 6) checks connector TCP reachability only in the Darwin launchd
healthy loop. It dials the configured literal-loopback `ConnectorAddr` every
`min(1s, watchdog_interval/4)`, with timeout
`min(250ms, watchdog_interval/8)` (each floored at 1ns). One failed dial publishes
`ready=false` with the existing C3 reason `readiness-or-listener-lost`, then
exits 1 after service teardown. The normal detection budget is cadence plus
timeout: at most 1.25s with the default 30s watchdog, plus Go scheduling and
readiness-publication/teardown time. For watchdog interval w, the cap-aware
combined probe budget is min(1s,w/4)+min(250ms,w/8), with each term floored at
1ns; for example, w=3s gives 750ms+250ms=1s. The independent monotonic watchdog
still exits 75 if publication or teardown stalls. A successful probe immediately closes the
connection without sending protocol data or credentials. TCP reachability does
not prove application-level health. Connector-loss recovery is sidecar exit +
launchd KeepAlive, without a second supervisor. Linux still relies on systemd
BindsTo; its serving/readiness path is unchanged.

The Go writer mirrors C3's descriptor-relative no-follow parent traversal,
current-euid 0700 writable directory, exclusive 0600 temp, file fsync and atomic
rename. The unchanged Python status reader accepts the record. A separate
monotonic watchdog starts before publication/startup and exits 75 without waiting
for service/filesystem locks after an interval without completed healthy
publication; shutdown remains bounded. launchd owns restart. Go scheduling is
required; host sleep/runtime freeze recovery is unproven. Darwin ignores
CREDENTIALS_DIRECTORY and always uses the existing same-user 0600 one-use
credential-file/consumed-marker/deletion/empty-key restart contract.

`tools/generate_inventory.py --target darwin-arm64 [--check]` maintains separate
`third_party/darwin-arm64/{dependency-inventory.json,THIRD_PARTY_NOTICES.md}`;
Linux's historical library inventory and notices remain byte-identical.
`--check` verifies without writes. C6-loss reconciles Darwin against the command
package graph, adding the already-pinned `github.com/mdlayher/sdnotify@v1.0.0`
module/license omitted by the earlier library-only inventory. Darwin-only build
modules relative to Linux remain `github.com/google/uuid@v1.6.0` and
`github.com/prometheus-community/pro-bing@v0.4.0`, already pinned in go.mod/go.sum.
Build instructions and exact record fields are in the sidecar README. Darwin
checks here are cross-build/test compilation/vet only; native execution is NOT
RUN (zero authorized). C6b below delivers Python handoff/installer integration
and replaces C5's sidecar placeholder with real observations. App bundling/frozen entry, signing and
packaged-app acceptance remain open; Swift HomeConnector is unchanged.


### macOS managed sidecar integration (THR-360 C6b, PR 7)

The Darwin managed installer now stages two per-user LaunchAgents,
`ai.happyranch.connector` and `ai.happyranch.tsnet-sidecar`, rendered and operated
only by `LaunchdServiceManager`. Both retain `KeepAlive=true`, the 10-second
throttle and disabled-by-default plists; install enables both without loading or
starting either. Linux's root installer starts `happyranch-managed.target` and
waits at its Type=notify boundary. Darwin preserves C5's stage-only contract:
launchd has neither that blocking READY boundary nor a composite dependency target.
A successful install/drift check is not an affirmative readiness claim.

Source-mode install requires explicit `--sidecar-binary /absolute/executable`
and `--sidecar-config /absolute/input.json`. The executable must have literal
non-symlink parents, be regular/single-link, root/current-user owned, executable
and not group/other writable. No app-bundle path is inferred. The source Python
entry must remain installed/importable outside the checkout. Sidecar config is
an operator-provisioned current-user 0600 single-link bounded JSON object, with
no unknown/duplicate fields and string values: `ControlURL` (private HTTPS
control, no userinfo), `RoleIdentity` (`home-sidecar-` prefix), `ExpectedPeer`,
`DERPPolicy=private-only`, `ListenAddr` and `ConnectorAddr`. The latter must
match the connector's nonzero managed loopback port and differ from the daemon
port. Optional `StateDir`/`CredentialFile` are replaced by the fixed managed
paths. Config is staged at `<connector-state>/sidecar.json` at 0600. The sidecar
node state is `~/Library/Application Support/HappyRanch/connector/happyranch-tsnet-sidecar`
(current-user 0700). Its plist passes only paths via `--config`,
`--credential-file` (C5's staged `enrollment.key`), and `--readiness-file`
(`<sidecar-state>/ai.happyranch.tsnet-sidecar.readiness.json`), plus
`--watchdog-interval` from the connector's finite positive `watchdog_sec`.
No key/token content or credential environment enters either plist.

`python -m runtime.remote_access.installer start|stop|restart|status|enable|disable`
is the Darwin composite operator surface. `cli` with a managed config delegates
those commands and uninstall to the same owner; managed install uses the installer
entry with the explicit sidecar inputs. Source DIY/lab and Linux retain per-unit
CLI behavior. Start/enable orders connector then sidecar. Stop/disable orders
sidecar then connector. Restart verifies sidecar then connector stopped, then
starts connector followed by sidecar (including bootstrap when unloaded).
If sidecar start fails, the already-started connector may remain; no readiness
or rollback is claimed. If sidecar stop fails, the connector is untouched.
Uninstall verifies both absent using the existing bootout/print observation,
rechecks the layout, disables both and only then removes any plist/config/log.
A stop failure retains every file. Both safe pending plists are handled as in C5.

There is no BindsTo/Upholds equivalent or in-process supervisor. Startup gates
wait for the connector probe/listener and exit nonzero on failure. After startup,
PR1494's Darwin TCP probe publishes ready=false/readiness-or-listener-lost on
connector loss, closes sidecar admission and exits 1. launchd KeepAlive restarts
it; it may cycle under the existing 10-second throttle until the connector is
ready. TCP reachability does not prove protocol health. Login startup has no
guaranteed cross-agent order. The app's Swift HomeConnector stays untouched;
never enable both listeners together.

Status returns `connector` and `sidecar` objects retaining all C3 native process
and readiness fields; composite `ready` requires both observations ready.
`readiness_state=not-installed` means no plist and no loaded service; otherwise
C3's `not-loaded`, `starting`, `running-not-ready`, `ready`, `stale-readiness`,
`pid-mismatch` and record error substates apply. Process activity alone never
claims ready. Drift additionally validates the version-2 receipt's config,
policy, sidecar config and both plist digests, source executable paths, layout,
log metadata and enrollment state. Binary path/metadata checks do not attest its
contents/signature. C5-only receipts require explicit uninstall/reinstall; an
unconsumed stored key requires explicit purge before fresh enrollment. Trust/node
state is never automatically migrated or overwritten.

Enrollment observations are `staged` (key only) or `consumed` (marker only).
Key-only reinstall refuses to replace an unconsumed key; first enrollment needs
`--enrollment-key-file`. Marker-only reinstall requires no new input and verifies
the Go marker bytes: SHA256(`RoleIdentity + NUL + ControlURL`) plus newline.
Mac staging omits empty `excluded_daemon_ports`, preserving pre-B1b config and
receipt bytes on unchanged marker-only reinstalls. Nonempty lists remain
serialized; changed configuration still refuses before writes or launchctl.
Both present, invalid marker, or neither present refuse drift/start; both present
also refuses reinstall. Neither present may install with a fresh explicit input.
Only Go redeems, commits the marker and deletes the staged key; Python does not
fake consumption. Normal uninstall retains enrollment/node/trust/readiness state,
including ambiguous enrollment for inspection. Explicit `--purge` removes both
validated state trees. Removal remains available for ambiguous enrollment but
never for unsafe metadata. Repeated normal/purge uninstall is idempotent.

The existing standalone `scripts.mac_native.test_connector_launchd` and
`test_connector_installer` venues exercise real temporary files and scripted
launchctl boundaries; they prove neither native launchd nor actual Go enrollment.
C6b uses ZERO Mac-mini runs (goal native usage remains 6/8). C7 below adds frozen
entry/bundling and signing enumeration. C8 below supplies coexistence and the
combined source/native suite. Packaged acceptance remains separate. No new
check or CI trigger is added.

### macOS bundled connector entry and sidecar (THR-360 C7, PR 8)

The bundled `Contents/Resources/daemon/happyranch-daemon` accepts the fixed hidden
`--remote-access` dispatch before normal daemon startup. Its arguments go to the
existing connector CLI; `--remote-access installer ...` goes to the existing
Darwin managed installer. Frozen connector plists use
`happyranch-daemon --remote-access run --managed --config <staged-config>`.
Source argv remains Python `-m runtime.remote_access.cli run --managed ...`.
The PyInstaller runtime-submodule hook already collects both paths.

`build-app.sh` separately builds the existing sidecar command with the pinned
Go1.23.12/`GOTOOLCHAIN=local`, readonly module graph, `GOWORK=off`, Darwin/arm64
and cgo disabled. The fixed output is
`Contents/Helpers/happyranch-tsnet-sidecar`; executable/arm64 checks include it.
Frozen installs default to that path, derived only from the shipped daemon
layout. An explicit absolute operator `--sidecar-binary` wins and retains the
existing binary safety checks; source mode still needs that explicit input.
`--sidecar-config` remains required. No new dependency or Swift change is made.

The Darwin sidecar's existing aggregated THIRD_PARTY_NOTICES and dependency
inventory are separate originals in the app licenses manifest, owned by
`runtime:Go-sidecar-Darwin-arm64`; their source names and digests remain visible.
Existing notice validation and archive reconciliation cover both. The local and
Jenkins signing scripts include a sidecar present in their retained baseline
before the outer app, via the existing exec branch/identity with no new
entitlements. The accepted older artifact pins are preserved; actual signing
and new-artifact pin binding remain separate release work.

Assumption: absolute bundle paths are intentional and operator-managed.
`check-drift` refuses missing/nonexecutable plist executables and a connector
entry differing from the current app/source, including a move where the old app
still exists, with uninstall/reinstall guidance. It never rewrites the plist or
watches for app moves. Reinstall retains the existing enrollment/state rules;
normal uninstall preserves state, whereas `--purge` removes managed state.

C7 uses ZERO Mac-mini runs and ZERO app builds. Portable focused tests and Linux
cross-compilation cannot prove packaged execution, launchd, code signing or
native recovery. Combined acceptance remains open and depends on the separate
THR-346 packaged-app check pending founder approval. Budget: PR8/10, Mac mini
6/8 used, slice 0. Linux, the Swift connector, required checks and CI triggers
remain unchanged.

### C8 — Swift coexistence and combined source acceptance (PR 9)

Before Darwin managed `install`, `start`, `restart` or `enable` performs any
managed file write or launchctl operation, it reads the current user's TCP
listeners through OS-shipped `/usr/sbin/lsof` (`-nP -a -u <uid> -iTCP:8443
-sTCP:LISTEN -Fpun`). Apple's lsof 4.91 also emits the default `f` descriptor
field with this selection. The parser validates ASCII descriptor numbers or
documented non-error FD tokens before each file's address; malformed, unknown,
foreign-UID or incomplete file records remain unavailable. The `err` FD token
also refuses. See Apple's pinned [field emission](https://github.com/apple-oss-distributions/lsof/blob/7a8a1b2a3c0f35c30a5fcd0927f31d441c3e5255/lsof/proc.c)
and [FD grammar](https://github.com/apple-oss-distributions/lsof/blob/7a8a1b2a3c0f35c30a5fcd0927f31d441c3e5255/lsof/lsof.8).
A non-loopback listener on 8443 refuses with a fixed,
secret-free Swift HomeConnector message. A failed, malformed, diagnostic-bearing,
oversized (64 KiB) or timed-out (5s) inventory also refuses. Loopback listeners,
including the managed connector, do not conflict. `status` and `check-drift`
include `coexistence.state` (`clear`, `conflict`, `unavailable`) and a fixed
message; composite ready also requires clear inventory. Stop, disable and
uninstall remain available. No Swift preference is invented, and the app's
connector is never stopped or modified by the managed installer.

The signal follows the shipped Swift HomeConnector's port 8443 and tailnet-IP
binding. An unrelated current-user non-loopback listener on that port is a
false positive. The read-only observation cannot serialize a later Swift start,
and a future Swift port change would escape detection until parity is updated.
Operators must still avoid enabling both listeners. This is same-user
cooperative admission, not an atomic exclusion mechanism.

Both managed plists now pin `EnvironmentVariables.HOME` to the installing
process's home and `TMPDIR` when set. No credentials are placed in environment
or argv, and launchctl's shared environment is never changed. Both keys are
part of plist drift comparison: a changed installing HOME/TMPDIR requires
uninstall/reinstall. The generic LaunchAgent renderer's default remains unchanged.
Source mode still requires an absolute interpreter that can import the checkout
outside its working directory; no product PYTHONPATH or WorkingDirectory is added.

The `connector-managed` finally cleanup uses the same <=30s post-bootout settle
per target, <=0.5s polling and domain-confirmed absence. Each attempted target
records its last raw print (<=2048 characters) and elapsed settle seconds;
failure retains owned files/scratch. Its existing total cleanup cap is 100s,
including scratch removal, so later targets/removal receive only the remaining
time. Stop/restart/uninstall
CLI calls allow up to 120s for both agents inside the unchanged 240s lifecycle,
3300s suite and 5400s job budgets. No ExitTimeOut/plist key is changed.

The new allowlisted `connector-managed` native suite uses the existing job's
private HOME/TMPDIR and fixed UID-scoped labels `ai.happyranch.connector` and
`ai.happyranch.tsnet-sidecar`. Before managed effects it proves both services
absent in `user/<uid>` and selectable `gui/<uid>`, and both private plists absent.
Any collision or unavailable observation fails closed without installing or
cleaning preexisting targets. Cleanup ownership begins only after this proof.
The source suite stages an offline scratch venv/.pth using already-bootstrapped
packages, provisions the existing pinned Go1.23.12 toolchain, builds the checked-out
sidecar with `GOFLAGS="-mod=readonly -modcacherw"` in this suite only, then drives
the real source installer CLI. Modules remain readonly; downloaded cache
directories remain writable.
It exercises both-agent install/start/status, exact connector PID SIGKILL and
KeepAlive replacement, stop/drift, coexistence refusal using its own temporary
non-loopback 8443 listener (occupied port refuses), normal uninstall and purge.
Status is recorded verbatim as bounded JSON; the sidecar must remain not ready
with a valid negative readiness record (running-not-ready, starting or PID-mismatch
while launchd retries). Missing/corrupt records and fabricated ready are rejected.
Synthetic config/policy/enrollment/token files and a loopback unavailable control
endpoint provide no real enrollment or daemon. No real credentials are used.
Bounded raw launchctl print/print-disabled excerpts and finally-path service,
plist and scratch receipts survive failures. Scratch removal validates the
created root identity and traverses literal directories through no-follow fds;
symlinks are unlinked without following targets. Permission errors add owner
write/execute only to the contained parent directory fd and retry once. Regular
files, outside targets and the root's parent are never chmodded. Each entry
checks the shared cleanup deadline; depth >=128 refuses. `scratch_removed` is
always a Boolean and `scratch_error` is empty on success or <=512 characters
on failure/retention. Removal failure and cleanup uncertainty still fail the suite;
any suite-local retained scratch remains subject to the existing job EXIT trap.
The existing connector-launchd stand-in suite is unchanged.

C8 maker delivery and this fix-forward use ZERO Mac-mini runs (PRs 9/10;
Mac mini 8/8 spent). Build31 at `bc42318f` passed build/install/start/status and
KeepAlive recovery, then failed at stop and immediate cleanup absence. The
subsequent build32 at `52fa59a9` passed lifecycle and final service/plist absence
but failed removing its Go module cache before the suite scratch receipt.
Build31/build32 scratch residue remains unobserved and untouched; further native
runs are Founder-reserved. The repaired head's combined native suite is NOT RUN
in this leg. A future run proves source
CLI/native lifecycle only: real tailnet enrollment/readiness is unproven, as are
login/reboot persistence and packaged execution. Packaged-app connector acceptance
depends on the separate THR-346 packaged-app check, pending founder approval.
No Jenkins config, dependency, Swift, Linux, Go-source/inventory, signing identity,
CI trigger or required lint/static check changes are included.


### Linux desktop-package provisioning and port contract (THR-327 B1a/B1b)

`ManagedInstaller` and its CLI retain the source layout by default, with
the source interpreter and payload paths. `--layout deb` selects the packaged private
Python (`/opt/happyranch-desktop/python/bin/python3 -I -m`) and packaged sidecar;
`SidecarUnitSpec.binary_path` supplies its ExecStart. Dpkg-owned directories are
only validated: root ownership, no group/world write, literal safe parents and
both executable files, permitting only the shipped `python3 -> python3.12` link.
The configured service UID, primary GID and supplementary groups must have
execute access to both files (including the resolved Python target) and search
access to every payload ancestor through `/opt/happyranch-desktop` and `/opt`.
Unix DAC selects owner bits when the UID matches, otherwise group bits when a
GID matches, otherwise other bits. Missing access raises a path/permission
diagnostic before config/unit publication or start. Fresh-host provisioning
rechecks after creating the account; dpkg paths remain read-only.

The `provision` command accepts connector/sidecar/current-policy JSON, explicit
`--release-user`, optional validated `--data-home`, and an optional absolute
daemon-token source (default `<data_home>/daemon.token`). It validates before effects, creates only the
existing installer-owned account/config layout, and stages root-owned
0640 root:happyranch configs, a 0600 happyranch:happyranch token, and root-owned
0600 layout/config/token provenance. Stable IDs come from the operator;
trust state is fixed under `/var/lib/happyranch-connector`, sidecar state under
`/var/lib/happyranch-tsnet-sidecar`. Credentials remain file-only systemd input;
no key generation, pairing or secret diagnostics are added. Existing files
refuse; partial failure has no automatic rollback or overwrite recovery.
Connector/sidecar state, log and runtime directories are supplied by systemd's
existing directory declarations at service start, as specified in B8; provisioning
checks present custody but leaves absent service-owned directories absent.

The 3b-1b writer sub-part uses exclusive `.<fixed-name>.pending` files under
the protected `/etc/happyranch` directory. It bounds each write to 64 KiB,
sets the exact final owner/mode, fsyncs the complete file, revalidates pinned
literal parent identities/owners/modes and the pending regular single-link
inode, then uses descriptor-relative Linux `renameat2(RENAME_NOREPLACE)` and
fsyncs the parent. Missing libc/kernel support refuses without fallback; even a
target arriving at rename is retained. Fresh preflight
refuses any existing final or pending entry across all six provisioning files
before publication; normalized config sizes are checked before staging. It
never repairs foreign metadata. Ordinary write failure
removes only the inode it exclusively created, preserving a substituted entry.
A crash can retain pending residue; a later file failure can retain earlier
complete files. There is no multi-file recovery transaction. The records-first follow-up
adds only `install-transaction.json` to the fixed writer map, root:root 0600;
arbitrary destinations still refuse. Version-1 provision and transaction bodies
contain exactly `version`, matching `layout`, `digests` and `phase`, with fixed
lowercase SHA-256 digest keys for connector/sidecar/policy JSON, release-daemon
JSON and daemon.token (plus provision.json for a transaction). Only provision
phase `provisioned` and fresh-publication transaction phase `prepared` are accepted;
the replacement-only stopped/quarantine phases are described below. Unknown,
duplicate or secret fields, paths, versions and phases refuse before writing.
The legacy provision command still emits its original unversioned provenance.
Any final or
pending transaction entry refuses provision/install/uninstall/drift before
publication or service calls, without reading it as recovery authority.
Legacy provenance/config/unit contracts remain unchanged.
Replacement/disable recovery (3b-1c), zero-input helper/package wiring with H2
(3b-1d) and home transport (3b-2) remain unavailable in this sub-part.

The 3b-1b-3 Python staging API omits all three config inputs to `provision`, while
still requiring the validated release user/home. It generates fresh IDs,
`managed.profile="linux-managed-identity-v1"`, the fixed private Unix-ingress
profile, and a current 3600-second envelope around the bundled deny-default
policy. Sidecar JSON omits `ExpectedPeer`, `ConnectorAddr`, `ControlURL` and
`control_tls`; the staged bundle has no control binding. There is no placeholder
URL. Only the validated release-home `daemon.token` can supply its token.
All files use the merged fixed-destination writer and existing owner/mode rules.
The v1 provision record carries real fixed-file SHA-256 digests and the existing
`provisioned` phase (staging only), with no secrets or paths. Generation creates
no transaction file and grants no transaction consumption/recovery operation.

The inert staged modern profile still refuses install/uninstall (including
purge), check-drift and ordinary unit publication before any unit write, backup
creation/restoration or service call with `home control binding not provisioned; requires slice 3b-2`, leaving
inventory unchanged. Alternate supervisor install/uninstall/start/stop/restart/
enable/disable/upgrade/rollback/run entries refuse that inert bundle. E2a below
adds only the protected ready ordinary-lifecycle and fixed-account foreground
exceptions under Rulings 15/15a; uninstall and other deferred lifecycle still
refuse home profiles. Legacy omitted/empty profiles retain upgrade/rollback behavior; read-only service status
and diagnostics remain available. Modern service admission
remains unavailable even with a supplied URL. Render-only
`managed_units(connector_config=...)` selects the existing identity renderer
profile, adding exactly `RuntimeDirectoryMode=0700` to the connector. Sidecar and
other unit bytes remain unchanged, including `home_transport=False`.
Legacy/omitted-profile configs and installed units retain main's exact bytes.
The CLI still requires all original operator config inputs. I5d-1 supplies the
installer-owned `publish_home_units()` prerequisite for clean-install Enable;
I5d-2 calls it from public Enable. This separate inert step verifies the
complete protected staged generation and publishes the five existing identity
renderer fragments under the activation-directory lock. It never enables or
starts them. `install()`, `_write_units()` and staged-identity admission remain
unchanged. Installed public Enable/READY acceptance remains I6, not established
by these temporary-root checks. No Go/provider-admission/renderer change is included.

The first 3b-1c sub-part exposes only Python `inventory()` and
`stop_and_confirm()` prerequisites on `ManagedInstaller`; no CLI/helper/package
method selects them. Fixed config/state/runtime locations, five exact unit
fragments, fixed override directories and enable links are inspected without
executing old config/argv. Reads are bounded (64 KiB configs/units, 64 shallow
entries per managed directory); opaque state leaves receive metadata checks only.
The result is clean, modern staged-inert/recorded, known failed/partial, intact
manual requiring migration, or `manual-install-conflict` with non-secret paths.
Safe unrecorded config subsets, including actual interrupted generated writer
prefixes, are known failed/partial after validating each present artifact.
Complete unrecorded legacy configs support modes 0600/0640/0644; recorded
provisioning still requires exact 0640 configs. Owner, no-follow, regular-file,
single-link and group/world-write checks remain mandatory. Stopped intact manual
installs, including legacy-provisioned ones, still need explicit migration.
Unsafe/unknown entries, digests, fragments/drop-ins or final/pending transactions
refuse; release-home tokens are never read by inventory.

For recognized installations only, stop target, connector, sidecar, verifier
socket and verifier service, prove dead inactive/failed state and no queued jobs,
and require zero main/control PIDs and no cgroup for services. Re-inventory before
returning; no file changes occur, including on stop/proof failure. This sampled
observation is not later mutation authority. No Off/cleanup claim is made.
Disable cleanup and token rotation/repair remain fail-closed, as do existing modern
activation entries; journaled replacement closure is described below. The
required temp-root/fake-manager checks remain in `scripts/test_installer_token_custody.py`.
Owned inert Headscale stop/cleanup is delivered separately in the 3b-2f-1
subsection below; helper Turn off and activation-state cleanup remain later work.

The 3b-1c Python-only replacement consumer implements **stop/intent, config
quarantine, fixed state/enrollment, quarantined config and owned unit removal,
fresh inert staging and transaction closure** into ordinary modern staged-inert
inventory. The v1
record still has exactly `version`, `layout`, `digests` and `phase`, with the six
original fixed-file digests (including provision.json) unchanged through unit
removal. Staging replaces only each current new file's digest; the record format
and version do not change.
Ordinary transaction publication still accepts only `prepared`. Only this
consumer advances the closed linear sequence:

`prepared` → `replacement-stopped` → `replacement-quarantine-connector` →
`replacement-quarantine-sidecar` → `replacement-quarantine-policy` →
`replacement-quarantined` → `replacement-remove-trust-state` →
`replacement-remove-trust-anchor` → `replacement-remove-trust-lock` →
`replacement-remove-tsnet-state` → `replacement-remove-consumed` →
`replacement-remove-tsnet-log-config` → `replacement-remove-tsnet-log1` →
`replacement-remove-tsnet-log2` → `replacement-remove-enrollment` →
`replacement-remove-enrollment-link` → `replacement-state-removed` →
`replacement-remove-config-connector` → `replacement-remove-config-sidecar` →
`replacement-remove-config-policy` → `replacement-configs-removed` →
`replacement-remove-target-link` → `replacement-remove-verifier-link` →
`replacement-remove-unit-connector` → `replacement-remove-unit-sidecar` →
`replacement-remove-unit-target` → `replacement-remove-unit-verifier-socket` →
`replacement-remove-unit-verifier-service` → `replacement-remove-dropins-connector` →
`replacement-remove-dropins-sidecar` → `replacement-remove-dropins-target` →
`replacement-remove-dropins-verifier-socket` → `replacement-remove-dropins-verifier-service` →
`replacement-remove-target-wants` → `replacement-units-removed`.

Root:root 0600 journal metadata and exact bytes are checked repeatedly. All
replacement phases require exact generated canonical bytes; `journal_generation`
hashes those bytes. Except for exact current/completed removal and staging intents,
missing files refuse. Unknown/malformed/mismatched records, wrong placements
and unrecognized pending entries refuse before mutation (the exact own-intent
exception is described below). Clean, modern recorded and intact
manual installs still refuse. Journal-free modern staged-inert inventory also
refuses exactly `replacement refused: modern:` without effects; an unjournaled
failed/partial candidate remains unavailable without effects.

For a proven journal-bearing failed/partial candidate, `replace_failed()`
revalidates the recorded release home and reads only its fixed daemon.token
through the merged same-FD source-UID reader before any mutating manager call.
It re-inventories, calls the existing `stop_and_confirm()` owner, and requires
dead inactive/failed states, no jobs, zero service PIDs and no cgroups before
file changes. Stop/proof failure preserves the remaining files; a validated
own-journal pending file may already have been discarded before the later stop
under the recovery rule below. A prepared candidate
still fsyncs only the `replacement-stopped` intent and raises the existing stop
boundary diagnostic. Interruption before that publication can leave `prepared`;
if stopping erased the failed indication, the next call refuses rather than
inventing failure provenance.

A later call resumes the published stopped intent through config quarantine.
Before each rename the existing atomic writer publishes and fsyncs its exact
fixed-path intent, preserving all six digests. Only that intent's source may
be in its original 0640 root:hr state or partially tightened 0600 root:hr/root:root
state. Each previous config must already be quarantined and each later config
must still be at its original path. Exactly one location per config is required.
The consumer tightens the descriptor to root:root 0600, fsyncs it, rechecks the
journal, all digests and pinned parents, then uses atomic no-replace rename to
`/etc/happyranch/.replacement-connector.json`, `.replacement-sidecar.json` or
`.replacement-policy.json` and fsyncs the parent. No old config is executed,
restored or used to select argv/control settings. Only these three original
fixed paths move; unknown files, custom units/drop-ins, the release daemon and
package are untouched. No recursive removal occurs.

After all three renames it publishes `replacement-quarantined` and raises
`replacement paused after config quarantine; removal and fresh staging unavailable`.
A subsequent call consumes that published quarantine boundary to remove only:
`/var/lib/happyranch-connector/{trust-state.json,trust-state.json.anchor,trust-state.json.lock}`,
`/var/lib/happyranch-tsnet-sidecar/{tailscaled.state,credential.consumed,tailscaled.log.conf,tailscaled.log1.txt,tailscaled.log2.txt}`,
`/etc/happyranch/enrollment.key`, then the exact-target reserved
`/etc/credstore/happyranch-tsnet-enrollment` symlink, in the phase order above.
Each fixed-path unlink has its own preceding atomically published/fsynced intent
with the exact six original digests. The existing bounded full inventory, exact
journal/digests and literal parent identities are rechecked before unlink.
Only service-owned single-link regular 0600 state files, the root-owned 0600
enrollment source and the root-owned exact-target enrollment link are removed.
Opaque state is never read, interpreted, copied or restored. This state-removal group removes no directory,
unit, package, release-daemon file or quarantined config.

Interruption after any new intent, unlink or parent fsync leaves services off;
a new instance checks the exact on-disk generation and resumes the current
path if still present, fsyncs its parent if absent, then advances. Earlier
completed removals must remain absent; a reappearing entry refuses. State and
enrollment were optional in the admitted failed installation; their absence
alone does not authenticate a historical journal phase. The protected journal
writer remains the authority, not a filesystem absence inference. Unknown,
malformed, mismatched or placement-inconsistent downgraded records refuse.
These sampled checks assume the existing single protected root writer; they
are not a concurrent-root-writer fence. Pending or uncertain entries are never
automatically repaired or replayed.

At `replacement-state-removed`, the consumer raises exactly
`replacement paused after state removal; config/unit removal and fresh staging unavailable`.
A subsequent call rechecks the source token and stop/quiescence proof, then
removes only the three root-only `.replacement-{connector,sidecar,policy}.json`
copies, in that order. Each unlink has its own published/fsynced exact-generation
intent through the same atomic writer. Descriptor-relative no-follow parent
pinning, single-link regular root:root 0600 metadata, journal and every remaining
fixed-file digest are rechecked before each effect; the removal parent is fsynced.
Only the exact current intent may have its copy present or absent. Later copies
must remain digest-bound; completed copies and all completed state/enrollment
paths must stay absent, including original config locations. All six original
digests remain unchanged in the journal. Once the connector copy is absent,
validated provision provenance selects only the existing exact legacy/identity
unit-byte profile; no old configuration, ID, key, trust or control binding is
reconstructed or executed. Fresh-instance resume handles every intent/unlink/
parent-fsync boundary; unrecognized pending or mismatched records still refuse
safely. Exact own journal-pending intents use the recovery rule below.

At `replacement-configs-removed`, the consumer raises exactly
`replacement paused after config removal; unit removal and fresh staging unavailable`.
A subsequent call consumes this boundary to remove the two reserved enablement
links (`multi-user.target.wants/happyranch-managed.target`, then
`happyranch-managed.target.wants/happyranch-release-verifier.socket`), the five
exact remote unit files in their existing order, their five empty `.d`
directories, then the empty `happyranch-managed.target.wants` directory. Each
fixed removal has its own preceding atomically published/fsynced exact-generation
intent. The shared systemd and `multi-user.target.wants` directories remain.
Unknown drop-ins/target wants, changed unit bytes/metadata or foreign links refuse;
there is no recursive deletion. Config/state absence from completed intents,
all remaining digests, the source-UID/same-FD release token, full bounded inventory,
stop/quiescence and pinned no-follow parents/leaf metadata are checked before
effects. Each removal fsyncs its parent. A fresh instance resumes every new
intent, unlink/rmdir and publication boundary. Only the exact current/completed
unit intent can recognize an absent unit with systemd's cached own fragment,
empty drop-ins and inactive/failed state; quiescence still requires no job,
zero PIDs and no cgroup. No cached foreign fragment or ordinary lifecycle is admitted.

At `replacement-units-removed`, unit removal still raises exactly
`replacement paused after unit removal; fresh staging and transaction closure unavailable`.
A subsequent call stages fresh inert files through `replacement-discard-token` →
`replacement-discard-provision` → `replacement-stage-connector` →
`replacement-stage-sidecar` → `replacement-stage-policy` → `replacement-stage-token` →
`replacement-stage-provision` → `replacement-staged`. The old managed token copy
and provisioning record are individually unlinked/fsynced behind their own intents.
The validated release record remains fixed. The existing `_generated_configs`
generator supplies fresh random UUIDs and the bundled deny-default policy; the
new sidecar identity refers only to the newly staged connector. No original-ID
comparison is required or possible from v1's six whole-file digests. No old config,
quarantined bytes, keys, trust or control settings are read or reused. Enrollment
stays absent: later enrollment requires a fresh single-use key and re-pairing.

Each fresh file uses the existing exclusive-pending atomic writer, its exact mode
and its own preceding fsynced journal digest intent. Only the current destination's
digest may be rebound, and only while that destination and its pending entry are
absent. This permits fresh generation after loss of unpublished UUID bytes; it
never overwrites or regenerates a published file. Completed files must remain
present and byte-bound; future destinations must remain absent. All other digest
slots remain unchanged. The final modern `provision.json` binds the five actual
staged files and the retained journal binds all six. Custody through the same-FD
source-UID reader, full bounded inventory, retired-state/unit/enrollment absence,
quiescence and pinned no-follow parents are rechecked before intent publication
and file effects, including after the pending-file fsync. Replacement recovery
also recognizes the fixed `.install-transaction.json.pending` leaf as interrupted
writer intent only under a pinned no-follow parent: regular single-link file,
exact writer owner/0600 mode, at most 64 KiB, strict canonical v1 bytes. A next-phase
intent must equal exactly the committed generation with only its immediate phase
advance. A same-phase staging rebind may differ only in that phase's current
destination digest, with both destination and its payload-pending leaf absent.
After fresh custody, quiescence, full inventory, digest, parent and leaf rechecks,
recovery discards only this validated journal pending inode and fsyncs its parent;
the normal writer republishes intent and regenerates unpublished content. It never
rolls forward a digest rebind or reconstructs lost UUID/policy bytes. Other pending
content, metadata, generations, phases, digest differences and unknown entries
refuse and remain byte-exact. Payload-pending leaves retain their separate refusal
surface. Fork/real-fsync/os._exit cases cover every replacement journal transition
and all five rebinds; existing SystemExit coverage remains. Machine-power-loss
durability and racing root writers are not claimed.

Inert inventory and journaled replacement recovery validate the full policy
schema/artifact and every recorded file digest without requiring a current
policy. Elapsed time after policy publication, the token intent or
`replacement-staged` cannot strand an otherwise exact generation. Published
policy bytes and their 3600-second lifetime remain unchanged. Read-only partial
inventory before provenance publication also remains classifiable after expiry;
this grants no unjournaled replacement authority. Provisioning admission,
install/start/enable paths and runtime readiness/request admission still require
current policy or refuse activation. An expired staged policy must be re-issued
through a future authorized flow before activation; this slice neither re-issues
it nor implements activation.

Fresh staging pauses at `replacement-staged` with
`replacement paused after fresh staging; retry ReplaceFailed to close transaction`.
The next authenticated call resumes this retained v1 generation through
`replacement-reload` → `replacement-retire-journal`. Each new intent is atomically
published and fsynced with the same six digests before its effect. The existing
quarantine/config removal intents have already destroyed the root-only old
configs/secrets, and the staging intent has already published the final modern
`provision.json` exactly as clean-host generation does. Closure never regenerates
or rewrites those files, IDs or an expired policy.

The reload intent rechecks custody, full fixed inventory, quiescence and pinned
no-follow parents before daemon-reload, then resets retained failed markers only
for fixed managed unit names whose removal precedes this validated journal phase.
Before each reset, fresh show must prove not-found, empty FragmentPath/DropInPaths,
zero supported PID fields (MainPID/ControlPID for services, ControlPID for the
socket, neither for the target), empty Job and failed or inactive ActiveState;
inactive units need no reset. Unknown shapes refuse. The fixed reset-failed argv uses the
existing service-manager transport and timeout. No caller supplies a unit name.
The persisted replacement-reload phase is the durable intent for both idempotent
effects; there is no new phase/schema. V1/v2 journals already at reload, including
already-reloaded not-found/failed units, resume through this narrowly checked
inventory route with unchanged authorization, caller and custody checks.
Authenticated Turn off and ready-restart Enable additionally clear a failed
marker for the fixed five `UNITS` names. Turn off does this after verified
stop and final fragment form/daemon reload, before retiring cleanup evidence.
Ready restart retains its existing receipt, G/O, custody, deadline and no-replay
checks. Each effect freshly verifies the exact owned fragment at its fixed path,
no drop-ins or reload requirement, inactive/failed ActiveState, dead/failed
SubState and empty Job. Services additionally require zero MainPID/ControlPID
and empty ControlGroup; the fixed socket requires zero ControlPID and empty
ControlGroup (its Socket interface has no MainPID). The fixed target has no
execution context. These are the properties exposed by the
[systemd Unit, Service and Socket interfaces](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html).
For an absent fixed unit, `_reload_unit_state` also requires the same supported
PID fields, not-found LoadState and no fragment/drop-ins;
Headscale remains outside `UNITS`; receipt-bound B6 additionally resets its fixed
service name after verified removal and final daemon reload, before retiring the
original activation/account/publication receipts. This separate absent-unit proof
requires exact Id/Names, not-found LoadState, empty FragmentPath/DropInPaths, no
reload requirement, MainPID=ControlPID=0, empty ControlGroup/Job and dead/failed
SubState. A not-found .service still exposes its Service properties; any missing
property refuses, never defaults to zero. B6 rechecks its original receipt/G/O,
identity, process and custody fences before reset and after its fresh after-read.
Reset must re-read as
inactive/dead with the same ownership/quiescence properties. Failure leaves a
fixed `partial_cleanup` outcome with `owned units: reset unconfirmed` during
Turn off, or the existing `ready-restart-failed-turn-off` refusal during Enable.
No paths, native output or contents enter that result. All ordinary inventory
state sets stay strict. Status is read-only and never resets; post-disable staged
refusal is projected through the existing inventory/residue categories.
Ready restart also resets a failed Headscale unit behind its unchanged
require-ready fence: exact home unit bytes/loaded FragmentPath, Id/Names and
the same service quiescence/after-read proof are required before the existing
boot-equivalent start. It retains the original receipt/G/O/custody/deadline and
no-mint/no-replay rules. A failed reset/start still names
`ready-restart-failed-turn-off`; B6 makes that advice safe for a receipted unit.
Disable without a receipt still uses strict Enable capture: already-stranded
failed retained units have no new authenticated recovery admission here.

The retirement intent additionally requires the ordinary no-unit manager shape;
cached own fragments no longer qualify and every absent unit must be inactive.
If reset leaves a unit failed, refuse without looping or adding retry budget.
Immediately before descriptor-relative
journal unlink, closure repeats exact journal/leaf metadata, source-UID/same-FD
token custody, full bounded inventory, quiescence and pinned-parent checks. It
fsyncs the journal's parent last. Unknown, unsafe, unrecognized pending, quarantined or changed
residue refuses with non-secret fixed paths and remains untouched. There is no
unjournaled recovery or restoration of revoked credentials/trust.

While a recognized replacement journal remains, a fresh instance resumes each
exact closure intent or completed effect and converges to retirement without
duplicate effects. After durable retirement, ordinary inventory returns `modern` /
`staged-inert`, with no transaction journal or quarantine residue. A fresh-instance
ReplaceFailed retry then refuses exactly `replacement refused: modern:` with zero
filesystem, journal or service effects, just like clean-host staging. No completion
marker or new durable provenance distinguishes these states. V1's existing phases
and record shape remain readable; policy currentness is not required for closure.
Own journal-pending-intent recovery is covered as above; unsafe or partial pending
residue remains refusal-only. Racing root writers and actual machine-power-loss
durability remain unverified. Until journal retirement,
ordinary inventory/stop/provision/install/uninstall/drift and all ten fixed legacy
system supervisor lifecycle entries retain their final/pending transaction
refusals. Inert modern activation still refuses exactly `home control binding not provisioned; requires slice 3b-2`; E2a below governs already-activated ordinary admission.
No CLI/helper/D-Bus/package caller is added. Disable/account cleanup, token
rotation/repair, 3b-1d and 3b-2 remain outside this sub-part. All five unit strings
in both layouts/profiles and `SidecarUnitSpec.home_transport=False` are unchanged.

Deb install refuses incomplete/unsafe/changed provisioning before unit writes
or service start. Source installs without a provisioning record retain their
independently provisioned configs, but also require the root-owned release install
record described below. Check-drift derives the layout
from the protected record when present, retains exact-unit/loaded-file checks,
revalidates configs/current policy, and compares both the freshly reread token
source and staged copy with the recorded hash. Rotation/missing sources fail
closed. Uninstall keeps existing unit checks and can run despite invalid or
expired provisioning; config/provenance/token files remain retained.

[Desktop package host](../../operations/remote-access-runbook.md#desktop-package-host)
has input fields, commands and deployment holds. Managed ingress must differ
from permanent DEV port 8765 and every `excluded_daemon_ports` entry. Lists
contain only integers in 1..65535 (Booleans are invalid). Source and deb
provisioning stage equal sorted unique connector/sidecar exclusions, always
including 8765. `ConnectorAddr` equals `127.0.0.1:<managed.bind_port>`.
The release daemon's dynamically resolved port must also pass those exclusions.
A managed Linux system configuration must omit the old `daemon_port` field;
other platforms and the existing DIY/lab providers retain their fixed targets.

#### Linux release ownership verification (THR-327 seq425/428, PR-C)

Release data belongs in `$XDG_DATA_HOME/happyranch` (default
`~/.local/share/happyranch`). The installer cannot infer a login environment
through sudo: `--data-home` is the already-resolved absolute directory,
defaulting to `<passwd-home>/.local/share/happyranch`. It must already exist,
belong to the target uid, have no symlink components or group/other write bits,
and be outside the DEV home `~/.happyranch`, `/etc` and `/var`. A `//` absolute
anchor refuses before path-policy comparisons, with a diagnostic naming the
data home. Install-record revalidation and the verifier also reject noncanonical
record spellings (including redundant separators or dot components), lexically
without following symlinks. Provisioning
and install also reject paths hidden/inaccessible in the verifier namespace:
`/tmp`, `/var/tmp`, `/dev`, `/usr/lib/modules`, `/proc/kallsyms`, `/proc/kcore`,
`/proc/kmsg`, `/dev/kmsg`, and their descendants. One shared directive/path table
owns both the rendered verifier hardening and installer rejection set; the
failure names the data home and hiding directive. Comparison follows existing
no-follow traversal (symlinked parents refuse); read-only `/home` stays valid.
No hardening exception or data-home bind mount is added. No shell expansion or
login-environment lookup occurs. Provisioning writes root-owned
0644 `/etc/happyranch/release-daemon.json` with exactly `user`, `uid`,
`data_home`, `unit` (`happyranch-daemon.service`) and `fragment_path`
(`/usr/lib/systemd/user/happyranch-daemon.service`). The recorded uid must still
match passwd at install/drift. Neither DEV data nor port 8765 is changed.
Daemon random-port startup and the desktop shell are delivered separately by
PR-D; PR-C never modifies the packaged daemon unit.

The network-facing connector stays `happyranch:happyranch` with an empty
capability set. A separate root system service, `happyranch-release-verifier`,
is socket-activated at `/run/happyranch-release-verifier/control.sock`:
root:happyranch, mode 0660; its root-owned 0755 parent grants traversal only.
The closed ASCII line protocol accepts only `resolve\n` and
`confirm <connector-local-port> <daemon-port>\n` (decimal ports 1..65535).
No request chooses paths, users, units, flags or commands. Replies are
`port <port>\n`, `ok\n`, or `refused <machine_reason>\n`.

For **every** new upstream TCP connection, including readiness probes:

1. Resolve reads the recorded `<data_home>/daemon.port` using no-follow directory
   descriptors and `O_NOFOLLOW|O_NONBLOCK`; require a single-link regular file,
   recorded uid, at most 16 bytes, decimal 1..65535 with optional final newline,
   stable metadata, and no excluded port (8765 is unconditional).
2. Query the recorded user's local user manager with
   `systemctl --user -M <user>@.host show happyranch-daemon.service -p MainPID,ActiveState,FragmentPath,DropInPaths`.
   Require active, positive MainPID, the exact packaged FragmentPath, and **zero
   drop-ins**, including administrator drop-ins. No exception is tolerated.
   Require process uid and stable MainPID/start-time identity across the proof.
3. Resolve requires the literal `127.0.0.1:<port>` LISTEN inode in `/proc/net/tcp`
   to be an fd of MainPID. The connector connects to that literal address,
   disables automatic reconnection, and asks confirm for its actual local port
   plus resolved daemon port. Confirm rereads port/exclusions/unit and requires
   the server-side ESTABLISHED four-tuple's inode to be an fd of MainPID.
4. Only after confirmation may the connector send the bearer or any HTTP bytes.
   A positive result never survives into another connection. Refusal closes the
   socket, logs a machine reason, and maps to remote HTTP 503 (`daemon_unavailable`).
   No fixed-port fallback exists. Readiness loss still removes admission.

While managed Linux waits for initial readiness, STATUS and the journal name
the failing gates and categories; unchanged failures are journaled only once.
The root verifier journals only closed refusal reasons, never raw evidence or
exception text. These startup diagnostics survive installer rollback.
For managed Linux, the credential readiness gate also journals a closed
`injection_not_configured`, `injection_missing`, `injection_symlink`,
`injection_unreadable`, `injection_permissions`, `injection_empty` or
`unavailable` reason. Unknown provider failures become `unavailable`; no raw
exception, path or credential is logged. Credential readers and acceptance
checks and selection logic remain unchanged; the managed unit has no daemon
`LoadCredential` and the staged `/etc/happyranch/daemon.token` configuration
selects `FileDaemonCredentialProvider`, as on main.

The root verifier reads no token and has no `LoadCredential`. The root installer
copies the validated provisioning input (default `<data_home>/daemon.token`) to
`/etc/happyranch/daemon.token`, owned by `happyranch:happyranch`, mode 0600.
Provisioning, install admission and drift checks require the opened source inode
to belong to the validated release UID: exact 0600, regular, single-link,
nonempty UTF-8, at most 64 KiB. The reader pins every literal no-follow parent
and checks named/opened device and inode before and after reading. It rechecks
file owner, group, mode, link count, size, mtime and ctime, returns only bytes
from that FD, and refuses before publication or service calls when validation
fails. A changed source digest still refuses; this sub-part does not add token
rotation, generated configuration, transactional replacement/disable or helper
installation methods. The future fixed helper must derive the token path from
the authenticated/recorded release home, never a requester-supplied path (H2).
The connector reads this validated copy through the unchanged file provider;
it never reads the provisioning input directly. Daemon-token custody is unchanged
from main. File/PID hints alone are
never authority. Bounds: request 64 bytes, response 80 bytes, one client at a
time with one-second read timeout, two-second manager-query timeout (each),
8192-byte manager output, 4096 process fds, 16384 TCP rows, 512 bytes per row.
Confirm permits up to 250 ms for accept to publish the server fd, rechecking
current evidence; exhaustion refuses. This is sampled ownership evidence, not
an atomic kernel attestation. Root-only hardening settings and their reasons
are documented in the runbook. No new privilege is granted to the connector.

Install enables the verifier socket alongside the managed target; the verifier
service starts on demand. Uninstall stops/removes both units, socket enablement,
socket endpoint, and `release-daemon.json`; config/provenance/token files remain
retained. Install has no automatic partial-failure recovery.

M1 uses a synthetic backend and M2 the actual daemon under the hosted runner's
user manager. Their CI-equivalent root-owned unit occupies the exact packaged
FragmentPath, uses the source-wheel interpreter instead of the packaged binary,
and has no drop-ins. Both bind a random loopback port in test-owned data homes.
M2 additionally points the port file at a non-MainPID listener and requires
`socket_not_owned`. These are manual acceptance workflows, separate from the
required lints. Earlier M1/M2 receipts above attest only their recorded heads.
Shared M3/M4 drivers retain legacy fixture assumptions and need separate
alignment/requalification before reuse; their old receipts do not prove this model.

Standalone fixture checks: `uv run --frozen pytest scripts/test_release_verifier.py -v`.
These do not collect the retired Python unit suite or run installed services.

The shared Go sidecar accepts optional `ExcludedDaemonPorts` (integers in
1..65535) and refuses any matching connector port. Its literal-loopback and
literal-8765 guards remain; absent-list validation is unchanged for existing
Mac configs. Mac staging preserves the earlier bytes when connector exclusions
are absent or empty; pairing/authentication is unchanged. Daemon bearer
authentication cannot substitute for this boundary:
loopback `/auth/bootstrap` exposes the daemon token. No
installation, daemon/service restart, package build or Mac acceptance was
performed by this source slice. The retained standalone installer-script tests
cover temp-root/fake-manager behavior; required PR checks remain lint/static.

### Client helper control/DERP transport (THR-327 seq739/746 slice A)

Both Mac and Linux Clients use `app/mac/tsnet-bridge`. Its control/DERP path
has no OS CONNECT TCP listener. The dedicated process registers an owned x/net
Dialer/ContextDialer before proxy caches initialize and selects it with a private
`ALL_PROXY` scheme. It clears upper/lower HTTP(S)/ALL/NO_PROXY variants first,
installs the fixed in-memory resolver, rejects effective HTTP/system proxy or
bypass selection, and verifies that netns selects the exact owned wrapper before
tsnet starts. Control key/Noise and engine DERP dials therefore use verified
outer home TLS and inner process-local `net.Pipe` TLS. Only the admitted name/IP
on 443/8443 is accepted; both map to literal admitted IP:8443. Refused targets
never reach a forwarding dialer. Exact CA fingerprint/name/IP admission, no
ambient/system/public roots, private issuer custody and failure teardown remain.
Caller cancellation closes its stream; upstream dial/verifier failure seals
the owner. The filesystem Unix runtime relay remains for the separate pipe slice.
The Host sidecar's CONNECT forwarder described below is unchanged.

### Linux home control/DERP transport (THR-327 slice 1b)

The Linux identity profile accepts a protected sidecar `control_tls` record with
`dial_ip` (RFC1918 IPv4) and `ca_sha256` (64 lowercase hex CA DER fingerprint),
only with `ControlURL=https://control.happyranch.invalid:8443`. The canonical home
name requires that record. The dedicated process admits the fixed root-owned,
single-link, bounded exact-one-CA `/etc/happyranch/control-ca.pem` and protected
empty `/etc/happyranch/empty-ca-directory` before constructing clients (exact
root:happyranch 0640 CA file and root:root 0755 empty directory). Setup refuses
with an `ErrConfiguration` error before tsnet construction/dial, preserved by
the sidecar without consuming enrollment. Only home-profile units, selected with
`SidecarUnitSpec.home_transport=True`, add the two fixed
`SSL_CERT_FILE`/`SSL_CERT_DIR` Environment lines. The flag defaults to false;
legacy/non-home units remain byte-identical and retain OS system-root lookup
without SSL_CERT overrides. Slice 3b must set the flag when generating the home
profile. These lines locate the home trust files; the explicit home-only
upstream verifier supplies authentication. All other hardening, including MDWE,
is unchanged.

An ephemeral loopback CONNECT forwarder verifies upstream TLS with only the
admitted home CA and fixed DNS identity, before returning 200. Its only targets
are `control.happyranch.invalid:8443` and `:443`, both mapped to literal
`dial_ip:8443`; absolute HTTP and other/malformed/oversize requests close. It
re-presents a process-local in-memory leaf to pinned tsnet, using a separate
ephemeral-only forced fallback pool. Internally owned proxy/root/resolver setup
precedes clients. Ambient proxy/bypass/keylog/GODEBUG and all `TS_` overrides
refuse without value echo. There is no direct/public retry or host trust/DNS
change. Upstream verification/dial or listener failure closes owned streams;
shutdown/cancellation closes transport and tsnet. Malformed local TLS after
CONNECT also seals admission; EOF or a local TCP reset from a cancelled
controlhttp/DERP attempt closes only its own stream. Closed home engines cannot
create new listeners. Home/address changes require a new dedicated process,
retaining canonical durable identities.

The supported Go tests exercise synthetic CA/environment admission, real pinned
control `/key` + Noise registration/canary and independent DERP region clients
in fresh subprocesses, plus CONNECT rejection/closure, same-CA renewal, redirects
and an independent loopback direct-TCP fallback decoy. Synthetic full tsnet
compositions reach Running/map/private DERP and close admission on failure or
cancellation. Each lifecycle case first cancels a local handshake with a real
TCP reset, then waits for the fixture's DERP receipt within the Up context before
triggering teardown; Running alone does not establish DERP readiness.
Two processes isolate ports/issuers; a renewed-endpoint restart
retains node identity and consumed authority while replacing local issuer/DNS
state. Slice 3b owns protected config/CA/leaf/map generation and transactional
protected dial-IP publication. Successful installed-path startup, full packaged
lifecycle/MDWE, generated Headscale maps, packet-wide public/DNS egress exclusion
and native Darwin remain NOT RUN. See the [sidecar README](../../../app/linux/tsnet-sidecar/README.md) and
[Round-7 specification](../../specs/2026-11-16-one-click-remote-access.md#6-invite-selected-bootstrap-and-secret-handling).

### Linux client memory-only session transport (THR-379 slices 3a-T/8a)

The Linux payload builds `bin/happyranch-home-bridge` from the existing
cross-platform `app/mac/tsnet-bridge` Go module. Its additive
`--home-session-v1` mode runs as the desktop user, with no sudo, polkit,
setuid bit or system service. The existing `--home-bridge-v2` profile/recovery
path and Mac build behavior are unchanged; shared Go sources are used by both builds.
The native session adapter supplies an in-memory saved-record/resume seam in
slice 8a. Slice 8b adds native Secret Service custody and GTK saved reconnect;
transport readiness alone establishes neither custody nor loaded-runtime acceptance.

Legacy launch uses the existing uint32 big-endian length (1..16384) plus closed JSON
on private stdin: exactly `version` (2), `caDER`, `dialIP`, `homeID`,
`controlPlaneID`, `fingerprint`, `controlURL`, `hostname`, `joinKey`,
`socketPath`, `target`. The mode name versions the session interface; the
trust tuple retains v2. `stateDirectory` and `recovery` are forbidden, as are
unknown, duplicate, case-aliased, missing/null, malformed and trailing fields.
The legacy join key must be nonempty. Slice 8a adds closed v3 launch frames
(up to 128 KiB), with required `saveIntent` and `stableNodeID`, and exactly one
of `joinKey` or `networkState`. Save-intent sessions import/export complete
versioned memory state (up to 64 KiB/32 entries) over the private parent pipe.
Resume validates the saved control-plane/node binding before startup and the
authenticated live StableNodeID before readiness; mismatch refuses as -22.
See [spec section 10](../../specs/2026-11-17-web-native-bridge.md#10-ordered-b-delivery-removal-and-effort)
for the exact tuple and bounds. Existing
`validateHomeLaunch` and the pinned home-only control/DERP proxy, private
issuer/roots/resolver, peer-only dial/pin/revalidation, bounded socket relay and
private `0 <Self-StableID>` readiness line remain the transport boundary.
Readiness proves membership only, never pairing approval or loaded runtime UI.
On EOF or a catchable shutdown signal, engine cancellation interrupts private
output and stops runtime admission. The control transport stays available while
tsnet drains its workers, then closes before scratch cleanup is reported complete.

The server uses pinned tsnet v1.78.0 with memory-only state: ordinary joins use
`mem.Store` with `Ephemeral:true`; save-intent sessions use the bounded store
with `Ephemeral:false` to preserve membership for later same-node resume.
Join keys and generated machine/node/private keys remain in process memory;
product bearer and immutable redacted saved records remain in the native adapter.
No keyring, file-backed state, persistent credential cache, consumed marker or
substitute credential store is opened by 8a. No secret enters argv/env or an IPC
file. Founder THR-379 seq60 approved Q3: Linux saved access uses the system
keyring/Secret Service. The native 8b owner uses GI Secret and only the default
login collection, schema ai.happyranch.LinuxClient.v1, exact hashed home/control/
StableNodeID/profile attributes, and verified save/readback/delete. It stores only
SavedRecord resume material; never invitations, pair secrets, handles or bootstrap
tokens. Locked/denied custody needs native permission; unavailable/failed saving
continues session-only with a persistent accessible native warning and static
Retry save. No plaintext or substitute store. Readable saved computers remain
visible if a peer item is unreadable. Inventory limits apply only to this schema;
unrelated login items do not consume capacity. Save/removal isolate malformed peers
with a provably different binding; unreadable or possibly matching identities
retain uncertainty. Reconnect rereads native custody and uses
same-node resume with the loaded-runtime gate; Disconnect retains actually saved
access. Remove verifies exact local absence and reports partial cleanup honestly,
without claiming Host revocation or network unenrollment. Cancellation during a
write/readback invalidates the attempt and waits for that safe point before
teardown, retaining the observed custody outcome. Runtime failure during Retry
save also waits for this safe point and cannot restore Connected. Generated Linux
saved-computer ordinals use the static view's current locale without remounting
controls or changing opaque refs. Source and disposable-keyring
tests do not replace packaged/live, cross-device or different-network acceptance.
The native launcher must provide only its admitted environment, including
`XDG_RUNTIME_DIR`; it must not inherit credential/debug/trust overrides.

`XDG_RUNTIME_DIR` must be set to a literal absolute, current-user-owned 0700
directory with no symlink components; unset/unsafe locations refuse without
falling back to HOME, TMPDIR or a profile. Each session creates its own 0700
child there. Only tsnet's non-credential `tailscaled.log.conf` and filch log
spool are allowed scratch, never private state or bearer storage. Log upload
and upstream diagnostic destinations are disabled. EOF cancels enrollment and
relays; cancellation, catchable HUP/INT/TERM/QUIT and error paths close the
engine and remove that session's scratch. SIGKILL, power loss or OS failure
cannot execute cleanup; any surviving scratch is non-credential, not saved
access. No restart/replay of the join key occurs.

Headscale's current minter requests **non-ephemeral** keys. Consequently each
session-only join registers a **persistent node** on Headscale, which goes
offline after quit; a fresh session/invite creates another node until host
revocation/cleanup. `Ephemeral:true` controls the client's memory-store
compatibility and does not override that server-side key policy. This is a
known limitation, not a dependency for slice 3a and not authority to change
minting or host cleanup.

Go tests use a synthetic TLS/Noise control plane and actual tsnet memory state,
with its production logger enabled, to inspect scratch and teardown; they do
not establish live Headscale, packaged GTK, cross-device or native Mac acceptance.

### Pure home CA and server leaf core (THR-327 slice 3b-2b)

`app/linux/tsnet-sidecar/homeca` is a Go-stdlib-only generation
and validation package. Every issuance takes an explicit `time.Time` and
`io.Reader`; it performs no file, network, process or ambient-clock operation.
The inert root-run materializer below is its only production importer. The core
retains no filesystem API or new dependency. Authenticated `RefreshHomeAddress`,
Headscale TLS installation and lifecycle wiring remain the Linux track's
TASK-16780 work.

| Choice | Contract and reason |
| --- | --- |
| CA and leaf keys | ECDSA P-256 with ECDSA/SHA-256 certificates and PKCS#8 private-key DER. This matches the Mac Go ephemeral issuer and standard Go/Swift Security certificate parsing; Swift's invite DER walker imposes no key-algorithm allowlist. Native Swift execution remains a Mac acceptance obligation. |
| CA lifetime | Ten calendar years from issuance, with CA:TRUE, BasicConstraintsValid, CertSign and pathLen=0 (no intermediate issuers). Expiry/loss/replacement is a home-identity change: authenticated turn-off/re-enable, fresh invites and Linux approval. Never silently generate or repin a replacement CA. |
| Leaf lifetime | 365 days from issuance, capped at the existing CA's NotAfter; the total backdated interval remains below 397 days. Issuance refuses with typed `ErrInsufficientCALifetime` if less than 30 days remain from the supplied current time, with no partial result or new-CA fallback. Exactly 30 days is accepted. |
| Renewal | `LeafRenewalDue(leafDER, caDER, now)` is true at at most 90 days remaining, or for an invalid profile. It grants no issuance/trust authority. `IssueLeaf` uses only the supplied current CA and its matching key; an expired leaf can be recovered under that still-usable CA. No timer is introduced. |
| Clock skew | NotBefore is backdated five minutes, clamped to CA NotBefore for leaves. Issuance dates are UTC whole seconds; validity is `NotBefore <= now < NotAfter`. Backdating never extends NotAfter or permits an expired/not-yet-valid CA. |
| Serial and subject | Positive serials use 159 random bits (20 reader-supplied bytes with the sign bit cleared; all-zero refuses). CA CN is `HappyRanch home CA`; leaf CN is `control.happyranch.invalid`. Names carry no per-home authority; the exact pinned CA DER does. |
| SAN and address | Exactly one DNS SAN, `control.happyranch.invalid`, and only serverAuth EKU. No IP/email/URI/other SAN, wildcard or caller-supplied name/address is accepted. A dial-IP change alone does not require a leaf change; authenticated refresh renews only when the expiry window requires it, under the same CA. |

`NewCA(now, random)` and `IssueLeaf(caDER, caKey, now, random)` return one
certificate DER plus a redacted `PrivateKey`. `ImportPrivateKey` accepts a
copied PKCS#8 P-256 key for later same-CA issuance; `PrivateKey.DER()` is the
explicit sensitive export for custody, never for diagnostics. Every fmt verb,
String and GoString redacts the wrapper. Errors are closed typed categories;
parser/signer/reader errors and private inputs are never wrapped or logged.
Explicit exported key bytes and caller-owned buffers remain sensitive; the
package does not promise memory erasure or protect a caller that logs them.

`Check(caDER, leafDER, now)` admits exactly one self-signed P-256 CA DER
(at most 4096 bytes), verifies CA constraints/signature/current validity, and
validates a current leaf directly to that exact CA with an isolated root pool
and standard DNS/time/serverAuth verification. It additionally closes the raw
SAN extension to one fixed dNSName, refuses CA leaves, and requires the leaf's
window to fit inside the CA's. `Fingerprint` is exactly
64 lowercase hex SHA-256 characters over the supplied DER, without PEM
normalization; hashing alone grants no trust. The certificate outputs fit the
invite-v2 bounded DER walker and existing Linux/Mac Go CA admission checks.

Root must retain the CA signing key separately from Headscale, which receives
only its leaf key and certificate. The CA-only PEM goes to the protected
`/etc/happyranch/control-ca.pem`; no host/system trust-store change is allowed.
PEM encoding belongs to the materializer custody boundary below and uses stdlib
`encoding/pem`; this core has no PEM or filesystem API. Tests exercise generated
CA PEM through the existing Linux `homeTrustPolicy.admit` temporary-root pattern;
installed root custody, real Headscale TLS and native Mac acceptance remain
separate, unexecuted obligations of the wiring/native tracks.

### Inert Linux Headscale custody prefix (THR-327 slice 3b-2f-1)

The root operator entry `python -m runtime.remote_access.installer
prepare-headscale --layout deb` calls `ManagedInstaller.prepare_headscale`.
It only prepares fixed local custody and an inert unit. It never runs Headscale,
creates database users, enables/starts/reloads a unit, or changes the staged
connector bundle. Both exact `home control binding not provisioned; requires
slice 3b-2` refusals remain for inert staging; E2a below admits only the validated
already-ready ordinary profile and fixed-account foreground serving. E2b below
supplies uncalled first unit publication/selection; the ordinary installer default home_transport stays false.
The separate root operator `disable-headscale --layout deb` entry and legacy
`uninstall --layout deb` call `ManagedInstaller.cleanup_headscale`. They stop and
disable only the exact owned Headscale unit, then remove the inert custody and
receipted identity as below. The existing staged-profile uninstall refusal is
unchanged; the explicit Headscale cleanup entry does not activate that profile.
The inert 3b-2f-2 publication below includes config/DERP, server leaf/key, public
CA and empty CA directory handoff and cleanup. Helper Turn off and DB/Noise
writers remain later work.
No live root/systemd acceptance is claimed.

| Surface | Fixed contract |
| --- | --- |
| OS identity | `happyranch-headscale:happyranch-headscale`, separate from hr/happyranch. Validate system UID/GID against protected `/etc/login.defs` (`SYS_UID_MIN/MAX`, `SYS_GID_MIN/MAX`; shadow defaults 101 through regular minimum minus one). Require dedicated primary group, no other supplementary members or primary users, no UID/GID aliases, locked `passwd --status` (`L`), nologin/false shell, `/nonexistent` or literal root-owned home that the service cannot write. Malformed/unavailable observations refuse. No existing account/group/home is repaired. |
| Creation | Existing root-step command runner invokes `useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin happyranch-headscale` only after fixed custody/receipt/unit preflight and proving the group is absent. A successful command must be followed by actual looked-up validated identity; only then publish the creation receipt. |
| Creation receipt | `/var/lib/happyranch-remote-access/headscale-account.json`, root:root 0600 in root:root 0700 parent; exact object `{version:1, account:"happyranch-headscale", uid:integer, gid:integer, group_created_with_user:true}` (JSON keys quoted on disk). Uses the existing pinned no-follow publication parent, exclusive 0600 pending inode, file/directory fsync and atomic no-replace rename. No pre-useradd intent/adoption. A crash after useradd and before successful receipt publication leaves an **unowned** account. Pending/malformed/foreign/mismatched receipts refuse and are preserved. |
| Existing account (ruling A) | Exact expected shape without a valid creation receipt may be reused, but is UNOWNED and never modified or deleted. Preparation visibly returns `account=unowned` and `preservation="preserved: not created by HappyRanch"`; this is a typed visible preservation outcome, not cleanup success or failure. Shape mismatch refuses. |
| Custody | `/etc/happyranch/headscale`, `/var/lib/happyranch-headscale`, `/run/happyranch-headscale`: service UID:GID 0700; policy service UID:GID 0600. Existing directories are validated, never chmod/chown repaired. Empty custody, the exact canonical inert policy, or the final root-receipted config/DERP/leaf/key set below is reusable; unreceipted config/DB/key/socket/pending/unknown entries refuse and are preserved, so preparation cannot adopt a running/unrelated Headscale installation. No payload/notices are changed. |
| Policy/user layout | Fixed `/etc/happyranch/headscale/policy.hujson`, canonical JSON (valid HuJSON) with exactly one `acls` entry: `action=accept`, `proto=tcp`, `src=["happyranch-invite"]`, `dst=["happyranch-node:443"]`. All else denied by absence of a rule. No wildcard/autogroup/tag/SSH grant or invite-to-invite rule. Future node enrollment creates only the Linux-node user `happyranch-node`; future Mac minter receives dedicated user NAME `happyranch-invite`. These database users are not OS users, and are not created by this inert prefix. Port 443 is the spec's connector tailnet port, distinct from LAN control 8443. |
| Unit | Separate exact-byte `render_headscale_unit`, `/etc/systemd/system/happyranch-headscale.service` root:root 0644. Fixed libexec binary and `/etc/happyranch/headscale/config.yaml`, service User/Group, empty capabilities and applicable existing system hardening. Only fixed state/runtime write paths, 0700 systemd directories, UMask 0077, restart limits, control-group kill and bounded start/stop. No Install section. No enable/start/reload during preparation. This Headscale preparation leaves connector/sidecar unit bytes unchanged; the later connector retry change is described in [THR-327 renewal unit upgrade](#thr-327-renewal-unit-upgrade-task-19228). |
| Bounded startup | Tagged v0.25.1 `serve` does not emit sd_notify. The rendered unit uses Type=exec and isolated bundled Python `headscale_service wait-ready` as ExecStartPost: fixed loopback `127.0.0.1:19090` `/metrics`, one-second socket timeouts, 30-second retry deadline inside TimeoutStartSec=40. Tagged Serve opens metrics after HTTPS. This is startup evidence only, not tailnet/connector readiness; the probe is not invoked here. Output is suppressed in the service unit. |
| Port preflight | `headscale_service.preflight_ports(dial_ip)` validates the same canonical RFC1918 IPv4 contract as the renderer, then samples exclusive AF_INET binds at dial_ip TCP8443/UDP3478 and 127.0.0.1 TCP19090/TCP50443. No reuse option, all probes closed. `occupied-port` on EADDRINUSE; other failures `port-check-uncertain`; invalid input `dial-ip`, no raw exceptions. The 3b-2f-2 prefix below now wires selection and this preflight into preparation; native binds remain NOT RUN in maker verification. A check is an observation, not a reservation against a later binder. |
| Preparation output/refusals | Secret-free JSON with `state=inert`, `account=created/owned/unowned`, and `preservation=receipt-recorded` or the visible unowned statement. Closed refusal categories: `deb-layout-required`, `account-shape`, `account-create-uncertain`, `account-receipt-uncertain`, `foreign-unit`, `foreign-custody`, `custody-uncertain`; the config prefix adds `inventory`, `no_address`, `multiple_addresses`, `dial_ip`, `address-changed`, `publication-uncertain`, `occupied-port`, `port-check-uncertain`, `materializer-uncertain` and `cleanup-uncertain` (quiescence refusal). Native command text/output and underlying exception contexts are discarded. Partial objects are preserved for operator inspection; no automatic repair/deletion. |

**Foreign systemd inventory:** Before creating any account, receipt, custody or
unit, preparation uses literal no-follow checks in these default system-unit
load directories: `/etc/systemd/system.control`, `/run/systemd/system.control`,
`/run/systemd/transient`, `/run/systemd/generator.early`, `/etc/systemd/system`,
`/etc/systemd/system.attached`, `/run/systemd/system`,
`/run/systemd/system.attached`, `/run/systemd/generator`,
`/usr/local/lib/systemd/system`, `/usr/lib/systemd/system`, and
`/run/systemd/generator.late`. Also check `/lib/systemd/system` unless literal
`/lib` is a symlink exactly to `usr/lib` or `/usr/lib`, whose already-checked
destination is redundant; that link is never followed. Other aliased/unsafe
parents refuse. At every directory, any `happyranch-headscale.service` object
outside the exact managed `/etc` fragment is foreign, even with identical bytes.
Any `happyranch-headscale.service.d`, `happyranch-.service.d` or `service.d`
object (including empty directories/dangling links) or fixed unit-pending residue
also refuses. Before these checks, reuse the bounded no-follow cleanup link
inventory over that same complete directory list. Any symlink named
`happyranch-headscale.service` or whose literal target basename is that unit
refuses, including dangling wants/requires links and aliases. Publishing the
canonical fragment must not turn a dangling dependency into an enabled unit.
Alias drop-ins (exact-name, dash-prefix or service-wide, in any listed path)
cannot become applicable because the alias itself refuses, with or without a
drop-in. No alias target is followed or adopted. Preparation reports typed
secret-free `foreign-unit` (unsafe parent/I/O: `custody-uncertain`) with zero
effects and preserves foreign objects byte/mode/link-exact. The shared inventory
also enforces this admission during cleanup; its existing config-tree recheck
immediately before disable remains.

The same admission also refuses **any object** whose name starts with
`happyranch-headscale.` anywhere under those unit roots, except the installer's
exact owned `/etc/systemd/system/happyranch-headscale.service` fragment. This
includes same-stem timer/socket/path/mount/automount triggers, masks and drop-in
directories. Every regular unit file and drop-in `.conf` is read as bounded
bytes: a literal `happyranch-headscale` reference refuses, regardless of directive
(Wants/Requires/BindsTo/Upholds/PartOf/OnFailure/OnSuccess/Triggers/Unit/Also or
otherwise). The installer's exact owned fragment is excluded from that content
check. One package-owned helper unit is also excluded, only at
`/usr/lib/systemd/system/happyranch-remote-access-helper.service`, only as a
regular single-link root:root 0644 file with bytes exactly equal to the trusted
in-code `installer.PACKAGED_HELPER_UNIT_BYTES`. Both inventory consumers use the
same predicate after their bounded no-follow read and signature checks; expected
bytes never come from the scanned filesystem. Native packaging tests bind the
shipped unit and build-deb's installed copy to that constant. Changed bytes,
metadata or type, same-name `/etc` or `/run` copies and helper drop-ins gain no
exclusion. Every regular file under `/usr/share/dbus-1/system-services`,
`/usr/local/share/dbus-1/system-services` and `/etc/dbus-1/system-services`, when
present, receives the same literal-byte check with no link following. The
bounded no-follow reader uses the existing 64 KiB per-file limit; read failure,
unsafe or changing files, oversize, or shared 65,536-entry/depth-32 exhaustion
refuses before any preparation effect. Foreign bytes/modes/links/identities stay
unchanged. Cleanup uses this same admission before stop/disable; A–E identity,
receipt authority, partial-cleanup uncertainty and explicit retry are unchanged.

For future home activation, [B5 below](#home-network-engineering-settlements-b1-b18-thr-327-seq577)
keeps the owned-fragment and exact package-helper content exclusions above,
binds home bytes to the current activation receipt, and admits only its one
receipted enablement link; without
that receipt the existing literal-reference/no-link gates apply unchanged.

This is sampled admission in the fixed default roots under a **cooperative
root-writer** assumption. Executables in `system-generators` directories and
root writers acting after preparation are outside this inventory; they can
create later activation policy. No generator execution/inspection or concurrent
root-writer fence is implemented. Custom compiled/environment unit load paths
and native whole-host acceptance remain existing limitations. Preparation never
starts/enables/reloads a unit and grants no later activation authority.

The [systemd.unit load-path and drop-in rules](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html)
justify checking both higher-priority overrides and lower-priority fragments
that `/etc` would shadow; drop-ins merge across paths. This is the fixed default
system layout, not discovery of custom compiled/environment unit paths.

**Manager account/custody rulings A–E (cleanup):**
A is the unowned preservation rule above. B publishes only the actually created
identity; a crash between useradd and receipt publication leaves it unowned.
C requires a valid receipt and fresh name/UID/GID, full shape and unshared-group
observations. Before deletion the exact loaded unit fragment/drop-ins must be
owned, stop and disable must succeed, and fresh systemd state must have no active
job, process or control group. A bounded `ps -eo ruid=,euid=,suid=,fuid=` snapshot
must contain no process with that UID (real/effective/saved/filesystem); failed,
malformed or oversized observations refuse. These are sampled observations under
the existing cooperative root-writer discipline, not an atomic process fence.

For clean Enable admission before the Headscale account exists, there is no
owner UID to match. Only that UID-membership comparison is skipped; the same
systemd property checks and bounded `ps` command, exit status and four-column
ASCII numeric row validation still apply. A receipt-bound cleanup retains its
recorded UID comparison even after the account has been deleted.

Delete user first, then only the receipted group if still present and unshared.
E defines partial failure: successful userdel followed by failed groupdel keeps
the receipt, group and all other remaining objects; never recreate the user.
Return `HeadscaleServiceError(partial-cleanup-uncertain)`, CLI exit1 with no native
diagnostics. Before userdel succeeds, any failure preserves account and receipt
and returns `cleanup-uncertain` (never success). On retry, re-read the valid
receipt and fresh NSS identity: absent name plus no account using the receipt
UID skips userdel. UID reuse under another name, changed same-name shape/UID/GID,
changed group GID, supplementary members or another primary group user refuses
and preserves. A remaining matching empty group gets groupdel only. When user
and group are absent, continue the same fixed custody cleanup; when all objects
are already absent, retire just the receipt. Receipt removal is last, after fresh
confirmation that every receipted object is absent. No failure automatically
retries or repairs anything.

D admits the exact installed unit and canonical policy, empty fixed custody,
and the final publication-receipted config/DERP, server leaf/key, public CA and
empty CA directory described below. The existing no-follow/mode/owner/single-link
inventory remains mandatory. DB, Noise, socket, unreceipted key/config and all
unknown entries refuse and remain, even under a valid account receipt. Revalidate identity, stopped state and inventory before effects.
Foreign/unsafe/unknown entries, pending/tampered receipts and differing units
refuse before any stop/delete. Package payload/notices, installer-state parent,
root-only CA material, connector/daemon/org data and unrelated Headscale installs
are preserved. No recursive purge or adoption of an unrelated install.

D remains the pre-activation cleanup scope without a valid current-generation
`home-activation@1` receipt; [B6 below](#home-network-engineering-settlements-b1-b18-thr-327-seq577)
defines the additional exact, per-type live-state members only with that receipt.

Before stop/disable, cleanup also inventories `/etc/systemd/system` and
`/run/systemd/system` recursively with no-follow descriptor reads. Any symlink
named `happyranch-headscale.service` or whose literal target basename is that
unit is foreign: aliases, wants/requires links, dangling or foreign-target links,
and manually created links to the managed fragment all refuse. This inert
installer creates no alias/enablement links. Refusal is secret-free typed
`cleanup-uncertain`, exit1, preserving identities, receipt, unit, custody and
links without stop or disable. Unrelated links remain untouched and are never
followed. Inventory is bounded to 65,536 entries and depth 32 across both trees;
unsafe, unreadable, changing or exhausted inventory refuses. A fresh link check
also precedes disable after stop. With no matching links, disable has no link
removal to perform under the same cooperative root-writer assumption; this is
not a concurrent-root-writer fence. [systemctl disable](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#disable%20UNIT%E2%80%A6)
can otherwise remove manually created links, so no receipt licenses them.
A–E partial-cleanup uncertainty and explicit retry semantics remain unchanged.

Cleanup JSON returns `state=removed, account=removed` only after owned convergence;
`state=preserved, account="preserved: not created by HappyRanch"` visibly preserves
an unowned account/group while removing the proven inert unit/custody. With no
Headscale objects/account it returns `state=absent, account=absent`; legacy
uninstall keeps its empty output in that case. Every uncertainty returns exit1.
Temp-root/fake-command tests extend the existing owners for creation/deletion,
unowned/crash preservation, tamper/foreign refusal, stopped/process/group checks,
partial failure, UID reuse and group-only/receipt-only retry. Host acceptance and
full helper turn-off remain NOT RUN.

Tagged grammar: [v0.25.1 ACL fields](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/policy/acls_types.go),
[named-user expansion and protocol/port rules](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/policy/acls.go).
Startup basis: [tagged serve command](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/serve.go)
and [listener order](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/app.go).
System ranges: [Ubuntu useradd](https://manpages.ubuntu.com/manpages/resolute/en/man8/useradd.8.html).
Standalone temp-root/fake-command checks: `uv run --frozen --python /usr/bin/python3
pytest scripts/test_headscale_custody.py -v --tb=short`. They exercise preparation
and cleanup, not root acceptance, real NSS/user creation, real systemd or live binds.
The following publication extends configuration/DERP with inert leaf/CA handoff;
admin minter and activation remain deferred.

### Inert Linux Headscale config publication (3b-2f-2 prefix)

The merged prefix delivers LAN selection, port preflight and receipted
config/DERP publication. The 3b-2f-2 remainder extends that same receipt with
inert leaf/key/public-CA/empty-directory handoff and matching owned cleanup. Both exact
activation refusals, `home_transport=False`, every connector/sidecar unit and
the 3b-2f-1 Headscale unit bytes stay unchanged. No control_tls identity is
written; no enable/start/reload or new activation policy is added. The complete
foreign-systemd admission above still precedes preparation effects; no existing
dependency/alias/trigger may become effective through this publication.

**Inventory:** `collect_lan_inventory()` uses literal Linux `NETLINK_ROUTE`
link/address/route dumps, not a shell, PATH, executable, environment or address
input. Include all unicast IPv4/IPv6 default routes across tables and every
ECMP member and its address family without metric/weight ranking. Complete
interface IDs carry their up/loopback flags and local addresses. Preserve table identity: an exactly
four-byte `RTA_TABLE` overrides the `rtm_table` header; absent attributes use
the header. Malformed, duplicate or truncated attributes refuse `inventory`.
Validate every default and every ECMP hop, including known interface indices,
before filtering. Retain missing `RTA_PREFSRC` as an empty source. IPv6 defaults
are ignored when selecting the IPv4 dial IP, including source-less RA defaults.
Among IPv4 defaults, ignore only validated non-main-table defaults with no
preferred source. Source-less main IPv4 defaults refuse `inventory`; only
ignored defaults left gives `no_address`.
Never guess from secondary/primary interface addresses, rank metrics/weights,
filter device names/types or suppress sourced non-main defaults. Source-specific
defaults and unresolved nexthop objects also refuse `inventory`. A resolved
single-interface IPv6 default with `RTA_NH_ID` (30), `RTA_OIF` (4) and no
`RTA_MULTIPATH` (9) is inventoried like any IPv6 default and cannot select the
IPv4 dial address. IPv4 nexthop objects, IPv6 groups/multipath or missing OIF,
and source-specific defaults in either family still refuse. This is a fail-closed limitation for
kernels/routes without an explicit preferred source, not full network support.
Malformed/error/interrupted/truncated dumps, missing identities, I/O, a six-second
observation deadline or shared 4 MiB/65,536-message-per-dump exhaustion refuse
secret-free. These are sampled dumps, not an atomic network-change fence.
The selector supplies `inventory`, `no_address`, `multiple_addresses`
and `dial_ip`; exclusive `preflight_ports(selected_ip)` runs before even account
receipt publication. Before service custody publication, existing quiescence
observations must show the exact inactive unit and no process using its UID;
preparation never stops a running service to obtain admission.

**Publication:** Reuse only the merged config/map renderers and the pinned
no-follow exclusive-pending/file-fsync/atomic-no-replace/directory-fsync writer.
`config.yaml` and `derp.yaml` are service UID:GID 0600 in the existing service
0700 config directory. The fixed materializer owns key generation and
certificate validation; the installer copies only leaf/key/public CA as below.
The final root:root 0600 `headscale-publication.json` in the existing root:root
0700 `/var/lib/happyranch-remote-access` has exactly `version:1`, integer `uid`
and `gid`, selected `dial_ip`, and `files`, binding literal absolute paths to
lowercase SHA-256 digests. The merged config-only record remains admitted; the
complete record binds config.yaml, derp.yaml, server.crt, server.key and
/etc/happyranch/control-ca.pem and adds exactly
`directories:["/etc/happyranch/empty-ca-directory"]`. No alternate paths or
additional members are admitted. Directory ownership/mode comes from the fixed
custody table, not caller data. Receipt admission
also reproduces the renderer bytes from the receipted address. Single-link,
no-follow, exact owner/mode, bounded same-FD reads and metadata rechecks apply
to both files and receipt; unknown/pending entries remain foreign. Matching
bytes alone never prove ownership. Receipt IDs must match the validated account.
An unchanged rerun retains bytes/inodes; a newly selected different address
refuses typed `address-changed` before preflight/publication. No RefreshHomeAddress
is authorized here; inert leaf renewal is described below, with no reload.

**Crash points:** Before the first config publication, the old inert prefix
may remain and be reused. During either exclusive pending write, abrupt death
may leave pending residue: refusal/preservation, no repair. After config.yaml
but before derp.yaml, or after both files but before the final receipt, the
unreceipted files are foreign and both prepare/cleanup refuse visibly. During
receipt publication, pending residue also refuses. Once the final receipt and
directory fsync are durable, lost success is an idempotent complete publication.
No incomplete publication is automatically repaired, adopted or deleted.

**Cleanup:** Fresh complete receipt, directory, owner/mode/link and exact digest
checks precede service effects. After the existing owned-unit stop/disable and
quiescence checks, repeat identity/quiescence/foreign-unit/full-remaining-inventory
checks and pinned-parent digest reads before each member unlink/rmdir/fsync. Only
members deleted by this same invocation may be absent during its following
checks. Retire the exact unchanged publication receipt last, then continue the
existing policy/unit/account cleanup. If removal crashes after either member,
the retained receipt and missing member cause typed cleanup uncertainty on
retry; no adoption/automatic repair/deletion. A failure after receipt retirement
continues under the existing A–E account rules. Edited/foreign/link/mode/pending
objects stay byte/mode/link-exact and never yield success. The separate
`home-ca/` tree (including CA key/staging) is never traversed/deleted; completed
config cleanup reports `home_ca=preserved`. Package payload/notices remain.
The CA readership contract has no discrepancy: spec section 4 explicitly
states `root:hr` means `root:happyranch`; that readership is unchanged.

Focused synthetic temp-root/fake-kernel/fake-manager checks:
`uv run --frozen pytest scripts/test_headscale_custody.py -v`.
Root/systemd/NSS/netlink/port-bind/packaged acceptance remain NOT RUN. This
prefix does not establish working TLS, Headscale readiness or remote access.

**Leaf/CA handoff (3b-2f-2 remainder):** The existing installer command runner
invokes only the fixed root:root 0755, regular single-link packaged
`/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca`. Literal
no-follow parents and source custody are checked first. For absent/empty
`home-ca/`, call `ensure`; otherwise read `status`, then call `renew-leaf`
**only** when its typed `renewal_due` is true. A first handoff of existing
current staging also uses `ensure` to validate the key pair; an unchanged
complete receipted handoff uses read-only status and exact staging digest checks.
It does not call `ensure` on an already receipted leaf, so a threshold crossing
cannot trigger implicit renewal through that verb. Require the closed four-field single-line JSON,
lowercase SHA-256 CA fingerprint, RFC3339 UTC expiries and Boolean due flag;
refuse invalid/failed/oversized output as secret-free `materializer-uncertain`.
No native diagnostics or certificate/key bytes are returned.

The materializer retains all CA/key profile and matching validation. Read only
bounded (4096-byte) root:root 0600 single-link `ca.pem`, `leaf.pem`, `leaf.key`
from literal root:root 0700 `home-ca/`; check `ca.key` metadata without opening
or reading it. Only absent/empty, complete CA pair, or complete CA+leaf pairs
are admitted before invocation; pending/unknown/partial/unsafe state refuses.
Bind canonical public CA PEM DER to the returned fingerprint, require a current
complete leaf status, compare final read-only status with the write verb's
projection, and re-read staging bytes before copying. For an existing complete
publication, staging public/leaf bytes must first match the existing receipt;
changed staging refuses before invoking the materializer. CA rotation is absent.

| New member | Exact custody |
| --- | --- |
| `/etc/happyranch/headscale/server.key`, `server.crt` | Dedicated service UID:GID 0600, regular single-link under the existing service 0700 directory; key published durably before certificate. |
| `/etc/happyranch/control-ca.pem` | root:happyranch 0640, regular single-link, only this home's public CA. Parent root:root 0755. No CA key or system trust-store write. |
| `/etc/happyranch/empty-ca-directory/` | root:root 0755, literal empty directory, link count 2. Parent root:root 0755. |

Files reuse the exclusive pending/file-fsync/atomic-no-replace/directory-fsync
writer. The empty directory uses exclusive same-parent pending mkdir, no-follow
open, exact ownership/mode, directory fsync and the same atomic no-replace
rename. Existing directories/files/pending entries are never adopted as these
new members. Each fresh read and owned-only cleanup repeats the exact fixed
custody, no-follow, link, digest and emptiness checks. Unchanged reruns perform
zero writes, including the source staging and receipt. Address-changed refusal
still precedes all materializer calls.

For an admitted config-only receipt, retain both config inodes and retire only
the exact validated old receipt before publishing the new members and extended
receipt. Inert due renewal validates the complete receipt and quiescence,
removes only its freshly rechecked old leaf key then cert, retires the exact
old publication receipt, and publishes the new key then cert with no-replace
writes and the new complete receipt last. Config/DERP/public-CA/empty-directory
inodes and CA signing key stay unchanged. No renewal of a running service,
service reload, new journal or new provenance mechanism is introduced.

Crash points extend the prefix contract: pending leaf/key/CA/directory or
receipt residue refuses; a crash after any member and before the final receipt
leaves unreceipted state that refuses prepare and cleanup. A crash during old
leaf removal leaves a missing-member receipt and refuses. A crash after old
receipt retirement leaves unreceipted retained config/members and refuses.
Lost success after final receipt fsync is idempotent. A failed materializer may
leave its own preserved staging uncertainty; it never licenses automatic
recovery or deletion. Source changes after renewal but before destination
publication refuse the next preparation rather than silently adopting them.

Cleanup removes only complete receipted config/DERP/leaf/key/public-CA members
and the still-empty receipted CA directory, repeating the existing
identity/quiescence/foreign-activation admission and each digest before unlink
or empty-directory rmdir/fsync. The publication receipt retires **after** all
these members; the account receipt remains last under A–E. A missing member,
unsafe metadata, changed digest, nonempty directory or pending state refuses
and preserves remaining bytes/modes/inodes. Interrupted member removal retains
the receipt and refuses on retry. `home-ca/` is preserved without traversal or
deletion by cleanup; Turn off is 3b-2h. This remains inert and establishes no
TLS, root/systemd or packaged remote-access acceptance.

### Inert Linux home CA materializer (THR-327 slice 3b-2e)

The root-run Go command reuses `homeca`; it adds no new cryptographic profile,
trust store, CA replacement, network action or service. The inert installer
preparation now invokes it through the existing command runner; no supervisor,
helper, D-Bus, polkit or systemd entry calls it yet. The production entry accepts
exactly one verb, with no path, name, address or settings arguments and no
custody environment override. The installed binary is outside PATH and
`HAPPYRANCH_BUNDLED_BIN`.

| Surface | Fixed contract |
| --- | --- |
| Materializer | `/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca`, regular root:root 0755 package file; no wrapper or symlink in `bin/` or `/usr/bin`. |
| Custody | `/var/lib/happyranch-remote-access/home-ca/`, root:root 0700, below root:root 0700 `/var/lib/happyranch-remote-access`. `/var` and `/var/lib` must be root:root 0755; `/` must be root-owned without group/world writes. Only the two managed directories may be created. Existing unsafe metadata is refused, never repaired. |
| CA | `ca.pem` (one canonical certificate PEM) and `ca.key` (one canonical PKCS#8 PRIVATE KEY PEM), regular single-link root:root 0600, each at most 4096 bytes including PEM. The CA key never leaves `home-ca/`. DER imports retain the core's 4096-byte bound. |
| Leaf staging | `leaf.pem` and `leaf.key`, same bounds and custody. These are root-only staging files, never Headscale service custody. The leaf has exactly `control.happyranch.invalid` as its DNS SAN, with no supplied address/name. |
| `ensure` | Requires actual euid 0. Creates a CA only when both CA files and both leaf files are absent. An existing admitted matching CA/key is retained byte-exactly. Issues a leaf if the pair is absent or `LeafRenewalDue`; a current pair is unchanged. Invalid/expired/not-yet-valid CA, mismatched key or CA with less than the core's 30-day issuance minimum refuses before writes, even if an existing leaf is not due. No replacement-CA fallback. |
| `renew-leaf` | Requires actual euid 0 and an existing valid CA/key. Always issues a new leaf/key under that exact CA. It never creates/replaces a CA. |
| `status` | Read-only; protected filesystem permissions still apply. Validates inventory and CA, reads certificates but never private-key contents. Emits exactly `ca_fingerprint`, `ca_not_after`, `leaf_not_after`, `renewal_due` as one JSON line. A missing complete leaf pair has empty `leaf_not_after` and renewal due; absent CA or a half-pair refuses. Invalid certificate encoding refuses; an expired/profile-invalid parsed leaf reports renewal due. Status does not prove private-key matching or readiness. |
| Output / exits | Success emits that fixed secret-free JSON projection on stdout for every verb. Exit 0 is success, 1 is refusal/I/O failure, 2 is usage. Refusals have empty stdout and one fixed stderr category; an output write failure may leave partial stdout and returns 1. No underlying error text, key bytes/DER/PEM or certificate PEM is printed. |
| Refusal categories | `usage` (exit 2); `root-required`, `unsafe-custody` (including unsafe type/owner/group/mode/link count or oversized file), `unexpected-entry` (including any pending residue), `incomplete-state`, `invalid-ca`, `ca-lifetime`, `invalid-key`, `invalid-leaf`, `io-failure`, `busy`, `generation-failure` (exit 1). Categories are typed internally and retain no underlying error. |
| Admission and bounds | Literal descriptor-relative `O_NOFOLLOW` directory/leaf opens; leaves also use nonblocking opens before type checks. Inventory reads at most five names to refuse more than the four fixed leaves. Files use same-FD bounded reads and metadata rechecks. Unknown entries refuse and are preserved. A nonblocking directory flock serializes cooperating commands without adding a lock file; this is not a fence against a separate root writer. |
| Publication / recovery | Generate and validate before publication. Each member uses its exclusive same-directory `.<name>.pending`, exact 0600 owner/mode, file fsync, descriptor-relative rename, directory fsync. Publish the key durably **before** the certificate, for both CA creation and leaf rotation. A crash can leave a key-only pair (`incomplete-state`), pending residue (`unexpected-entry`), or old certificate/new key (`invalid-key` for write verbs); each refuses without further writes or automatic recovery/deletion. A complete CA without a leaf pair may safely issue its first leaf. No newly published CA certificate becomes usable without its already-durable matching key. |
| Test isolation | Only same-package `_test.go` calls supply a temporary root, current test UID/GID and clock/euid observations to the unexported command logic. The shipped entry supplies literal `/`, root:root, actual euid and current time; there is no production flag/environment redirection. Temp-root checks prove owner/mode as observable by the unprivileged test process, not installed root acceptance. |
| 3b-2f handoff | 3b-2f-1 now supplies explicit inert account/receipt/custody preparation, restrictive ACL and separate unit rendering and owned inert cleanup with A–E receipt retry (see above). The 3b-2f-2 prefix now wires LAN selection, port preflight, rendered config/DERP publication, root-only path/digest receipt and owned cleanup. Its remainder now copies **only** the validated staged leaf cert/key into `/etc/happyranch/headscale` for locked `happyranch-headscale` service custody, plus protected CA-only `/etc/happyranch/control-ca.pem` and empty-directory publication/cleanup. Running-service renewal/reload coordination stays deferred to authenticated lifecycle operations. CA signing key stays root-only in `home-ca/`; this command performs no Headscale copy, configuration, user/unit creation, start, reload or cleanup. 3b-2g-1 now provides the inert fixed admin runner and durable minter store/lock below. Activation and helper invocation remain 3b-2g-2. |

### Pure home Headscale renderers and LAN selector (THR-327 slice 3b-2a)

The Linux payload ships pinned Headscale v0.25.1 **inert** at
`/opt/happyranch-desktop/libexec/headscale/headscale` (0755, dpkg root ownership),
outside the bundled-bin PATH surface. Both official native assets are SHA-256
pinned; its exact BSD-3-Clause license, linked dependency inventory/originals
and embedded Go 1.23.4 toolchain notices ship in `licenses/` and the .deb's doc
licenses copy. No wrapper, symlink, service or start/enable is added by bundling.
TASK-16780 owns wiring this path into the home lifecycle; Turn off keeps the
inert packaged binary and notices. See the [Linux release handbook](../../operations/linux-release-handbook.md#build-the-intermediate-payload).

`runtime/remote_access/home_headscale.py` is a pure source unit, now called by
the inert installer config prefix.
`render_headscale_config(dial_ip=..., policy_path=...)` and
`render_private_derp_map(dial_ip=...)` return deterministic, sorted-key YAML
text with a final newline. They perform no filesystem, process, network or
clock operation. They do not install files, validate certificates, bind ports,
author policy/users, or establish readiness. The inert installer prefix above
now owns kernel inventory, selector/preflight
and renderer caller wiring. The fixed materializer owns certificate validation and the installer owns
inert leaf/CA publication. Readiness remains later work; renderers remain pure.

`select_lan_dial_ip(interfaces, default_routes)` consumes immutable tuples of
frozen `LANInterface(index, up, loopback, addresses)` and
`LANDefaultRoute(interface_index, source_ip, table=254, family=AF_INET)` records.
Table IDs are exact integers in the unsigned 32-bit range; legacy two-argument construction
conservatively means main (254). Addresses are literal
local addresses without CIDRs. The collector supplies **every usable default
route**, its selected source and complete interface identities, including IPv6
and equal-cost routes; it must not preselect a winner or omit unresolved sources.
Validate every record and known interface first, then exclude IPv6 defaults
from IPv4 selection. Among IPv4 defaults, ignore only non-main defaults
without a preferred source. A source-less main IPv4 default refuses
`inventory`; an otherwise valid inventory containing only ignored defaults
gives `no_address`. Sourced non-main defaults remain ordinary candidates.
Down/loopback interfaces cannot supply a candidate. A selected source must be
assigned to that interface. Exactly one candidate is accepted; multiple routes
refuse even with the same source. The selector never ranks metrics. Nonselected
secondary interface addresses are not candidates. Missing/duplicate interface
identities, wrong field/container types and unassigned sources refuse as
`inventory`; no candidate gives `no_address`, multiple candidates give
`multiple_addresses`. An invalid candidate gives `dial_ip` before cardinality.

Selection and both renderers reuse `invite_v2._canonical_dial_ip`: canonical
RFC1918 IPv4 only. No normalization, public/CGNAT, loopback, link-local or IPv6
source is accepted. The codec still reports its existing `url` category through
both encode and decode. Renderer failures are `HomeHeadscaleError` with only
`dial_ip` or `policy_path`; no input or parser exception is included/chained.
The policy input must be a direct `.hujson` child of
`/etc/happyranch/headscale/` with a 1–64-character ASCII alphanumeric/underscore/
hyphen basename starting alphanumeric. Empty, relative, aliased lexical paths,
subdirectories and other suffixes refuse. The caller must separately prove
ownership, no symlinks, file availability and reviewed restrictive ACL content.

The v0.25.1 configuration pins HTTPS/control and embedded DERP to
`dial_ip:8443`, STUN to `dial_ip:3478`, metrics to `127.0.0.1:19090`, and TLS
gRPC to `127.0.0.1:50443` with insecure mode false. The URL remains
`https://control.happyranch.invalid:8443`. No wildcard/IPv6 listener, ACME or
port-80 listener is configured. The private map has exactly region 999 and node
999a, `HostName=control.happyranch.invalid`, `IPv4=dial_ip`, `IPv6=none`,
`DERPPort=8443`, `STUNPort=3478`; no `CertName`, `InsecureForTests` or loopback
entry. Its lowercase YAML keys match Headscale's `derp.paths` loader (not the
capitalized wire-JSON names). Empty IPv6 would allow AAAA lookup and is forbidden.
Automatic embedded-region insertion is false; only the protected private map is
loaded. This is a rendering contract, not executed Headscale/relay qualification.

Restrictive defaults beyond the specification's literal pins:

| Keys | Value and reason |
| --- | --- |
| `tls_cert_path`, `tls_key_path` | `/etc/happyranch/headscale/server.crt`, `server.key`; fixed leaf-only custody, no signing key. |
| `noise.private_key_path`, `derp.server.private_key_path` | Separate `/var/lib/happyranch-headscale/noise.key`, `derp.key`; no key reuse or writable arbitrary path. |
| `unix_socket`, `unix_socket_permission` | `/run/happyranch-headscale/headscale.sock`, quoted `0600`; service/root access only. Parent protection is the installer's gate. |
| `policy.mode`, `policy.path` | `file`, validated required input; never empty/default-allow policy or mutable database policy. No content rendered. |
| `acme_url`, `acme_email`, `tls_letsencrypt_hostname`, `tls_letsencrypt_listen` | Empty; no enrollment, challenge listener or external issuer. |
| `tls_letsencrypt_challenge_type` | `TLS-ALPN-01`; v0.25.1 validates this enum even with ACME disabled. Avoid the HTTP-01/80 path. |
| `tls_letsencrypt_cache_dir` | `/var/lib/happyranch-headscale/acme-unused`; unused, still within protected state if later inspected. |
| `derp.server.region_id`, `region_code`, `region_name` | `999`, `happyranch-home`, `HappyRanch Home`; one fixed end-user region, no location disclosure. |
| Map `nodes.name`, `stunonly`, `canport80` | `999a`, false, false; one fixed node, retain the required DERP relay alongside STUN, advertise no HTTP/80 capability. Map region metadata matches the config. |
| `derp.server.ipv4`, `ipv6` | Validated dial IP, `none`; no hostname/AAAA fallback in either explicit or generated tuple. |
| `derp.paths` | Only `/etc/happyranch/headscale/derp.yaml`; no extra maps. |
| `derp.urls`, `auto_update_enabled`, `update_frequency` | Empty list, false, `0s`; no external DERP fetch or refresh worker. |
| `disable_check_updates` | true; no CLI/startup release lookup. |
| `database.type`, `database.sqlite.path` | `sqlite`, `/var/lib/happyranch-headscale/db.sqlite`; local protected state, no external database. |
| `database.debug`, `database.gorm.parameterized_queries`, `skip_err_record_not_found`, `slow_threshold` | false, true, true, 0; suppress debug/not-found/slow-query disclosure and parameter interpolation. |
| `database.gorm.prepare_stmt`, `database.sqlite.write_ahead_log`, `wal_autocheckpoint` | false, false, 1; no prepared-statement cache or normal WAL state copies; earliest positive checkpoint if WAL exists. |
| `prefixes.v4`, `v6`, `allocation` | `100.64.0.0/10`, empty, `random`; supported tailnet identity space, no IPv6 allocation, unpredictable addresses. Prefix size grants no peer access; required policy governs access. |
| `dns.magic_dns`, `base_domain`, `nameservers.global`, `nameservers.split`, `search_domains`, `extra_records` | false, empty, empty list/map/list/list; no DNS takeover, public resolver or injected records. `dns.extra_records_path` is omitted: v0.25.1 rejects both extra-record keys being present, even when empty. No file watcher is configured. |
| `oidc.issuer`, `client_id`, `client_secret`, `client_secret_path` | Empty; no external authentication provider, secret read or alternate enrollment. Inactive OIDC-only defaults remain unused. |
| `cli.address`, `api_key`, `insecure`, `timeout` | Empty, empty, false, `5s`; socket-only administration without standing API credentials, bounded CLI calls. |
| `logtail.enabled`, `log.level`, `log.format` | false, `disabled`, `json`; no telemetry or input-bearing logs, structured format if separately enabled. |
| `randomize_client_port` | false; no additional server-directed client-port behavior. |
| `ephemeral_node_inactivity_timeout` | `120s`, validated upstream default above the mandatory 65s floor; unused for the required persistent home identities, no persistent authority expiry decision. |

Unspecified transport batching/tuning retains upstream defaults; it neither
adds authority nor enables an external service. Environment and service
hardening remain the owning installer's responsibility. In particular this pure
unit cannot prevent Headscale environment overrides, prove custody or promise
packet-wide absence of fallback traffic. Such installed/runtime qualification
remains NOT RUN here. Upstream semantics checked against the tagged
[configuration loader](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/types/config.go),
[listener owner](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/app.go),
[private-map loader](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/derp/derp.go)
and [startup update gate](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/root.go).

Focused commands: `uv run --frozen pytest scripts/test_home_headscale.py -v`
and `uv run --frozen pytest scripts/test_invite_v2.py -v`. These standalone
synthetic checks do not run the retired units, skipped integration suite or
Product E2E and add no CI trigger.

### Linux managed identity ingress v1 (THR-327 slice 1a)

The explicit `linux-managed-identity-v1` profile uses the fixed
`/run/happyranch-connector/ingress.sock`, not a caller-selected endpoint. Go
`Profile` and Python `managed.profile` select it; only omission or an empty
string retains legacy ExpectedPeer/TCP behavior and Mac/DIY hosting is unchanged.
Non-string values (including null) and unknown strings refuse; the Go command
returns configuration exit 2 before state or credential handling. The identity
profile is Linux-only. `ExpectedPeer` and
`ConnectorAddr` are omitted in the new sidecar profile. Enrollment/restart retain
durable state/consumption, private-only config, connector liveness, listener and
watchdog gates; no peer is required for READY. Health makes no expected-peer
selection. An empty paired set grants no access.

Each tsnet connection resolves `LocalClient.WhoIs(remote IP:port)` and requires a
nonempty `Node.StableID`, with no hostname/IP fallback. It opens one fresh Unix
connection and writes one v2 frame: ASCII `HRID`, byte `2`, uint16 big-endian
UTF-8 StableID byte count + StableID, then uint16 big-endian label byte count +
UTF-8 display label (possibly empty); total at most 1024 bytes. The label comes
from that same WhoIs response: `Node.Hostinfo.Hostname`, else `Node.ComputedName`,
else empty. The connector still accepts v1 (no label) exactly as before. HTTP follows unchanged.
The Python listener lives in `diy_provider.py` with its managed transport override
in `managed_provider.py`; the shared `httpd.py` handler checks SO_PEERCRED against
the fixed happyranch service UID before frame parsing. Unknown version, missing,
empty StableID, oversize frame, invalid UTF-8 in either field, CR/LF/NUL in
StableID, truncated or duplicate frames close. Overlong label bytes that exceed
the total frame bound close; valid longer names are normalized and capped below.
Initial frame/request-line reads have a two-second socket timeout. The socket is
0600 in an owned 0700 directory; existing/unsafe socket paths refuse rather than
being overwritten. Identity lives only in the handler's request-local
`ingress_node_identity` context; the separate `ingress_device_label` context is
display-only and never enters admission. Reserved HappyRanch/Tailscale identity headers
are stripped, including duplicates, while the existing device-credential header
continues to carry the device bearer. In the managed identity profile, slice
2b-1 uses this verified frame for pinned pairing and every-request admission.

Slice 2b-1 originally added only `RuntimeDirectoryMode=0700` through
`ConnectorUnitSpec.ingress_profile` for the fixed system service. Existing
hardening, MDWE, AF_UNIX allowance, capabilities, notify/watchdog and
target/bind/uphold relations remain exact. The later managed-identity connector
start-rate-limit change is described in
[THR-327 renewal unit upgrade](#thr-327-renewal-unit-upgrade-task-19228);
sidecar and legacy/other connector limits remain unchanged.
The legacy installer still validates legacy configs; slice 3b owns generated
profile/unit selection and migration. No service was installed or restarted.
CA/TLS/control/DERP trust changes belong to slice 1b. Real Headscale v0.25.1 WhoIs
and native recovery acceptance remain M2/later-slice work.

### Pinned pairing and per-device authorization (slice 2)

PinnedPairingManager is the connector-owned in-process core for THR-327's new
managed profile. Construct it once at connector startup. Its fixed helper-facing
operations are status, issue_invite_record, approve_pending, reject_pending and
revoke_device; helper transport/authentication is separate. Constructor configuration
supplies the anchored store and connector identity; no operation accepts a path, URL
or settings. Approval/rejection requires the exact captured (control_plane_id,
stable_node_id) tuple and pending generation.

The version-3 snapshot extends the existing snapshot/anchor/lock transaction with
one digest-only invitation and one pending approval or unfinished delivery. Approval
limits new managed grants to 32 pinned device records (including revoked pins);
this is not a parse-time limit on historical or DIY records. Each new managed device
has an internal ID, credential digest, monotonic
epoch, expiry, revoked flag and exact control-plane/StableID pin. Anchored v2
records, including more than 32 base-written DIY devices, remain readable and
DIY-usable/revocable/removable without inventing pins; pinned admission reports them as
re-pair-required and denies them. Missing/unknown/mistyped v3 fields, duplicate JSON
keys, torn publication and snapshot/anchor rollback refuse. Literal no-follow
parents, bounded regular owner-only single-link files and descriptor-relative
atomic publication apply. The final parent must be a directory owned by the effective
UID; its mode is tightened to exactly 0700, preserving the shipped owned-0755
installer lifecycle. Symlinked/non-directory/foreign-owned parents refuse; existing
unsafe snapshot, anchor and lock metadata is never repaired into acceptance.
The local anchor cannot detect replacement of both files by a trusted store writer.

Redemption consumes the invitation into the actual node's pending record in one
transaction. It returns a private completion handle and comparison phrase, granting
no runtime access. Approval persists the pin/digest/epoch before making a bearer
available in connector memory. Completion consumes delivery durably and requires
that handle and the same control-plane/node tuple; it returns the bearer once.
Expired/failed/replayed attempts never restore invitations. A matched invite's
digest/generation is fenced in-process before
publication; write failure attempts a durable burn without burning a newer invite.
If storage remains unavailable, the fence denies retries and startup burns the
persisted unfinished invite before serving. Only fresh explicit issuance supersedes
the fence and burns the old pending/delivery handle. Startup burns unfinished invitations, pending approval
and delivery; undelivered devices are revoked, while completed grants survive.
The invitation deadline is at most five minutes and also bounds approval/delivery.

authorization.admit(store, credential, stable_node_id, control_plane_id, now) loads
current anchored trust and checks digest, credential epoch, expiry, revocation,
connector audience and exact pin. Its verdict carries device ID and epoch for stream
registration. Identity comes only from trusted plain arguments: WhoIs StableID and
connector-configured control-plane ID, never headers, hostname or address.
The private HRID v2 label is recorded on pending approval, delivery and paired
device records as optional string `device_label`, without a snapshot version
change or migration. Non-string values refuse; absent fields in v2/v3 stores
load as empty. One Python normalizer strips Unicode Cc/Cf controls and format
characters (including bidi overrides/isolates), drops invalid surrogate code
points, trims and caps plain text at 96 characters before storage/projection.
Helper Status uses this recorded name, with `stable_node_id[:96]` only for empty
names. Recorded names also have a per-snapshot JSON-escaped byte budget: serialize
the complete mutation envelope with recorded names empty, exact legacy fallbacks
intact, the largest duplicate target, the longest mutation method, a 24-byte
finite-float deadline and the newline. If this fixed cost is `F` and there are
`N` recorded names, each projected name gets at most
`floor((32768 - F) / (N + 1))` bytes under the helper's existing ASCII escaping.
Keep the longest whole-character prefix within that budget (at most the original
96 characters); the extra name slot covers the duplicated target. Stored names
are unchanged. This keeps Status and all three mutations within the existing
32768-byte frame at the 32-device capacity. Legacy fallbacks are never shortened;
unrepresentable non-label metadata still refuses the projection without vetoing
store load. The existing helper public projection escapes markup; fingerprint and
comparison phrase remain the comparison authority. Names never affect matching,
pinning, authorization, revocation or stream registration. Historical device
counts and expired grants remain loadable. Headscale membership grants no product authority.
The managed identity listener wires this verdict into request/stream admission
(slice 2b-1); existing DIY and legacy transport paths keep their versioned behavior.

Attributed registrations use StreamRegistry.open with device_id and epoch.
Per-device revocation persists invalidation before closing/acknowledging that device's
registries, fences stale registrations and retains RevocationIncomplete on cleanup
failure. Other devices remain open. Unattributed legacy revocation keeps its existing
conservative closure semantics. Focused synthetic checks use the retained standalone
script venue: uv run --frozen pytest scripts/test_pinned_pairing.py -v --tb=short.
The core slice adds no UI, helper, polkit/control socket or installer.

### Managed request and stream admission (slice 2b-1)

Protected connector JSON uses the additive optional key
`managed.control_plane_id`, matching the design's `control_plane_id` identity.
The later root-helper installation slice writes it. Old configurations without
the key still load; the identity listener refuses pairing and product requests
with HTTP 503 `control_plane_id_missing`. It never infers a control plane from
headers, HRID, hostname, address, URL, tenant/home/connector IDs, or falls back
to the legacy pairing manager. The supervisor constructs one cached
PinnedPairingManager for `linux-managed-identity-v1` and supplies it and the
configured control-plane identity to the protected Unix listener at serving
startup. Read-only managed diagnostics inspect the existing provider and trust
store without constructing this owner or running startup recovery; active
invites, pending approvals and undelivered credentials remain usable.

`POST /pair/invite` takes the raw UTF-8 pairing secret and redeems against that
configured control plane plus the HRID StableID. It returns `state:
pending-approval`, a private `completion_handle`, `comparison_phrase` and
`expires_at`; no product credential is issued before local approval.
`POST /pair/complete` takes the raw UTF-8 completion handle and returns the
one-use `credential` only for the same tuple after durable approval. Failures
return HTTP 403 `pairing_denied`. These exact paths accept no query string.
The legacy `POST /pair` ceremony grants no authority on this listener.

Framing and pin validation have different bounds: HRID v1 accepts at most 1017
UTF-8 identity bytes; v2 accepts at most 1015 minus the label's UTF-8 byte count.
Both reject blank identity and CR/LF/NUL; pins accept at most 256
characters and reject every C0 character, DEL and surrogate. A frame-valid
identity that pins refuse (for example 257 ASCII characters or a nonblank
identity containing TAB) receives a clean 403, without identity/secret echo or
500. A matched invalid redemption consumes its invite, following the core's
consume-before-validation rule. Malformed framing still closes the connection.

Every ordinary request, SSE or WebSocket admission calls authorization.admit
through the pinned manager using the device bearer and this exact node tuple.
Wrong-node/valid-bearer requests deny, regardless of forged identity headers.
The device credential is consumed before forwarding. Successful verdicts carry
device ID and epoch through the existing route/policy/readiness and release
verification gates to StreamRegistry.open, including in-flight HTTP. The
shipped route policy still denies unsupported upgrades (currently all
WebSocket routes); this wiring adds no allowed route.

Supervisor reconciliation of persisted managed revocation fences and closes
the revoked or removed device's attributed epochs without stopping the listener or
rotating/sealing the entire registry. Other devices retain their streams and
can open new requests. Removal through the shipped CLI deletes the device row;
reconciliation also closes attributed owners absent from the trust snapshot and
fences their old epochs, including a delayed first registration. Stale admissions
racing transport allocation fail at registration; later approved epochs remain
eligible. Cleanup failures retain RevocationIncomplete rather than acknowledging
success. The serving loop reports that incomplete result and retries reconciliation
on each pass while keeping unaffected devices serving. Other exceptions retain
their existing handling. DIY/legacy reconciliation keeps its existing whole-registry
closure and rotation. Completed pinned grants survive connector restart;
unfinished ceremonies still burn. Standalone verification:
`PYTHONPATH=$PWD uv run --frozen pytest scripts/test_pinned_pairing.py scripts/test_identity_ingress.py scripts/test_managed_admission.py -v`.
These tests use real temporary Unix sockets, synthetic protected state and
controlled upstream handles. The WS attribution case supplies a synthetic
permitted template to reach registration, separately from the shipped policy's
deny gate. Native WS protocol behavior, live Headscale,
installation and service recovery remain separate acceptance work. The helper
binding is covered in the slice 2b-2 section below.

## Fixed Linux helper source (THR-327 slice 3a)

I5b delivers the Linux host CLI's seven zero-input lifecycle mappings:
`enable` → Enable, `turn-off` → Disable, `create-invite` → IssueInvite,
`cancel-invite` → CancelInvite, `replace-failed` → ReplaceFailed,
`recover-invite` → RecoverInvite and `retry-policy-renewal` → RetryPolicyRenewal.
`issue-invite` remains the polkit action name, with no second CLI alias.
RefreshHomeAddress remains absent; `refresh-home-address` refuses without dispatch.
The CLI retains the desktop-available gate and fresh per-call polkit authorization,
without credential intake/caching or automatic retry. Its transport allows 482/302/122
seconds for 480/300/120-second helper operations, respectively. The shared transport's
CLI opt-in leaves GTK method maps, bridge capabilities and the v1 protocol unchanged.

IssueInvite requires `/dev/tty` before bus access, validates I4a's exact terminal
frame and fresh expiry, and displays the text only there. JSON contains only the
existing nonsecret action fields (`effect`, `inviteState`, UTC `expiresAt`, etc.).
Malformed/late/lost frames produce `outcomeUnknown` / effect `unknown` without
display. ReplaceFailed uses section 7's exact warning and default-No terminal
confirmation before dispatch. Turn off adds no prompt to its fresh native auth.
The other six lifecycle replies acknowledge `accepted` before effects finish.
The CLI subscribes before its single send and consumes the authenticated unicast
`LifecycleCompleted` receipt, correlated to the original method/call serial,
within the original operation budget. Enable and Disable preserve their typed
terminal results; CancelInvite, RecoverInvite, RetryPolicyRenewal and admitted
ReplaceFailed report their named completion or known failure. Effect remains
`unknown`. Uncertain work maps to `outcomeUnknown` for the four CLI-only methods;
valid Enable/Disable receipts preserve typed uncertainty, while a lost/invalid/late
receipt or transport loss retains `outcomeUnknown` / exit 1; the fallback margin
for live-caller revalidation adds no receipt grace. There is no polling or redispatch. Private worker outcomes and
named residue alone are insufficient without that receipt. Admission cannot
establish host denial or cleanup; no paths, identities or success claims are
inferred from it. This is CLI source delivery, not live acceptance.

`remote_access_helper.py` exports `ai.happyranch.RemoteAccess1` at
`/ai/happyranch/RemoteAccess1` with zero-input Status, StatusV2, NextDevice,
ApprovePending, RejectPending and RevokeSelected, plus I5a's seven public
lifecycle/invite methods: Enable, ReplaceFailed, Disable, IssueInvite, CancelInvite,
RecoverInvite and RetryPolicyRenewal. The three device mutations and all seven
lifecycle/invite methods request fresh interactive polkit authorization; admission
is unchanged. Linux CLI consumers are delivered in I5b; bridge Enable/Disable/IssueInvite consumers are delivered in I5c (source only; I6 pending).
RefreshHomeAddress remains absent and B13-deferred. B8's `helper_owner.py` now
derives the release owner from the bus credential UID, passwd and validated
protected release record when present; the optional trusted constructor UID remains a compatibility
pin. The root-only `serve()` connects Gio to the fixed system-bus socket
(`/run/dbus/system_bus_socket`), ignoring address environment overrides. B8 packages
the bus/polkit definitions and root Type=dbus unit; package install registers the
service without running Enable. I3a mounts Enable/ReplaceFailed/Disable
through B9 and the fixed bundled-Python child; I5a exposes all seven lifecycle/invite
methods in public dispatch and introspection. Linux CLI consumer wiring is
delivered in I5b; bridge Enable/Disable/IssueInvite consumers are delivered in
I5c (source only; I6 native/live acceptance pending). RefreshHomeAddress has no
helper method and is deferred to B13. Run the fixed packaged entry with system
Python `-I`, without user settings or argv overrides.

The helper captures caller/UID, target, ownership and snapshot generation before
authorization, then revalidates after it. The 120-second deadline cancels an
outstanding polkit check; uncertain cancellation seals further mutations until
cancellation is acknowledged or that check's terminal reply arrives. A terminal
reply releases the dialog slot even if the authorization deadline or pending-request
expiry refuses the mutation; a late reply never authorizes an expired request. One dialog
is allowed globally. StatusV2 adds the bounded B15 read projection and B16 audits
cover the three existing authenticated device effects (details below). Per-unique-name
cursors disappear on disconnect. Status
contains only bounded public summaries. Error/log categories are closed.
Custom-server URL/key intake methods do not exist and are not planned; outside-home access
uses a fixed build-configured server (see [Outside-home access (fixed operator server)](../../specs/2026-11-16-one-click-remote-access.md#outside-home-access-fixed-operator-server)).
I5a exposes the seven lifecycle/invite methods through the public helper with
unchanged admission, including issuance's one-per-minute rate limit and bounded
policy-renewal installation. Linux CLI consumers are I5b and bridge consumers are
I5c; RefreshHomeAddress remains absent and B13-deferred.

`helper_control.OperationAPI` defines status, approve_pending, reject_pending,
and revoke_device plus the shared trust mutation lock. The new root-only Unix
request handler revalidates the captured snapshot under that same lock before
calling the operation. A ready/commit handshake keeps the connector lock held
while root rechecks the live caller UID and deadline. After its final potentially
blocking status revalidation, the handler checks the monotonic deadline again
immediately before the operation, still under that lock.
Slice 2b-2 binds this interface in the Linux system HRID supervisor to its
existing serving PinnedPairingManager, AtomicFileTrustStateStore and attributed
registry. It does not construct a second ceremony owner for Status/NextDevice or
diagnostics. The supervisor starts/stops a service-owned 0600 private Unix socket
inside the service-owned 0700 runtime directory; existing entries/aliases refuse,
and cleanup removes only its own socket inode. Kernel UID 0 is checked before
parsing. No new network listener or app-selected configuration is introduced.
The release UID comes from the existing fixed root-owned release record through
the verifier's no-follow bounded reader/validator, and the helper independently
compares its own protected UID. Provisioning/activation remain slice 3b.

Snapshot reads validate state plus anchored generation under a shared file lock,
without publishing or recovering ceremony state. The helper projection excludes
legacy unpinned rows (including historical stores with more than 32 DIY devices);
32 managed summaries and the 32768-byte frame remain hard request bounds. An
oversized projection refuses the control request, never truncates/rejects the
underlying legacy store. Labels never reconstruct authority: mutations reload
the real pending generation and exact control-plane/StableID tuple. Snapshot and
pending generations remain distinct, and None/False core results are refusals.

The live manager RLock spans ready/commit and final revalidation, sharing local
serialization with redeem/complete/invite. Each actual mutation also compares
the expected anchored generation inside its exclusive file transaction, so a
cross-process writer after final revalidation refuses. The file flock is never
held across IPC or transport-close callbacks. Revoke maps the actual device ID,
persists its deny before closing only that device's streams, and retains
RevocationIncomplete as an unavailable result and failed control phase. The
helper and control listener distinguish pending-request expiry from paired-credential
expiry: expired grants remain listed and revocable, including their existing HTTP,
SSE and attributed WS streams. Fresh polkit, captured identity/ownership, the
authorization deadline and actual-admission anchored CAS still apply to revocation.
ApprovePending and RejectPending both refuse expired pending requests.
The serving loop retains incomplete-cleanup reporting/reconciliation without
terminating other devices. Expired pending authority remains visibly failed and
cannot be approved; read-only observations never burn it. Fixed production control path:
`/run/happyranch-connector/control.sock`; no credential is returned by this
control protocol. Native dialogs and installed service/package activation remain unproven.

## Home invite encoder (THR-327 slice 3c-1)

`runtime.remote_access.invite_v2.encode` is a pure, home-only envelope encoder.
The normative schema, representations and bounds are the [section 6 v2 wire
literals table](../../specs/2026-11-16-one-click-remote-access.md#v2-wire-literals-pinned).
It generates the invite ID and pairing secret with `secrets`, serializes compact
sorted JSON without escaping slashes, and returns the private `invite_text`,
`pair_secret_digest` and integer `expires_at`. The digest hashes the exact
base64url secret's UTF-8 text, as consumed by `PinnedPairingManager.redeem`.
For `issue_invite_record`, the caller converts that integer deadline to an aware
UTC datetime. Callers floor fractional datetime deadlines before invoking the
integer-only encoder; the encoder refuses floats and booleans. It never extends
a deadline or truncates an oversized invite. The runtime endpoint is validated
as canonical IPv4, an ASCII DNS/tailnet name (LDH labels of 1..63 bytes,
253 bytes total), or bracketed IPv6, followed by exactly `:443`. Empty labels,
edge hyphens, other host punctuation, IPv6 zone IDs and noncanonical IPv4
refuse. Accepted endpoints are preserved byte-for-byte; the endpoint is not
the control dial IP.

`decode(invite_text, now=integer_unix_seconds)` uses an explicit clock, refuses
v1, system trust and non-home modes, and checks the same schema, encodings,
addresses, entropy, fingerprint, times and size limits. Both APIs perform no
I/O or network access. Encoding always emits the fixed control URL without a
slash; decoding also accepts the table's optional single trailing slash and
normalizes only that URL. Results are immutable slotted primitive-field objects,
with redacted display/repr and no Pydantic validation/copy/update API. Invalid
construction, reinitialization, assignment and deletion raise constant-message
`InviteError` refusals; those model refusal frames release input references
before raising and retain no input-bearing structured or chained library error.
Codec parsing refusals also leave library exception handlers before raising
their closed category, so neither cause nor context retains a raw library
exception, including malformed JSON, UTF-8 and address diagnostics.
Explicit transport fields must never be logged or persisted as diagnostics.

Assumption and division of checks (TASK-16835 manager ruling): row 5's
“Exactly one CA DER with CA:TRUE” remains unchanged. A private bounded,
forward-only DER TLV walker follows the reviewed Mac
`RemoteInvite.swift` `InviteCertificate.validate` field order. It requires one
complete Certificate SEQUENCE, no trailing bytes, a correctly ordered TBS and
extensions wrapper, unique extension OIDs, and basicConstraints with cA exactly
DER TRUE (`ff`). It rejects indefinite, nonminimal, truncated and overrunning
lengths. The structure has fixed depth; extension iterations consume input.
This is structural issuance validation, not full X.509 admission. It does not
parse validity dates, signatures or keys: the Go sidecar's
`homeTrustPolicy.admit` performs full `crypto/x509` parsing, CA/key-usage and
validity checks at protected home-CA admission; the Mac verifier retains
Security parsing plus its typed walker/validity checks. Those remain the trust
boundary. No dependency or subprocess parser is added.

Focused standalone verification:
`uv run --frozen pytest scripts/test_invite_v2.py -v`. The tests copy the public
Swift CA bytes and inline field vectors with source citations, check canonical
encoder bytes using fixed test-side entropy, and hand the digest/deadline to the
real pairing manager against temporary synthetic state. Swift's CA-expiration
refusal is a downstream verifier check under the division above. This slice
adds no issuance handler, helper operation, UI, service action or live 3c-2 flow.

### Pure home issuer core (THR-327 slice 3c-2a)

`runtime.remote_access.invite_issuer.HomeInviteIssuer` composes the encoder and
`PinnedPairingManager.issue_invite_record` with an injected `JoinKeyMinter` and
protected state directory. It has no network, subprocess, Headscale adapter,
helper/polkit method, installation or URL/key intake. The future helper
supplies protected `HomeInviteConfig`, a pairing owner bound to the same home,
and exactly one issuer directory per home. Linux inert preparation now supplies
`/var/lib/happyranch-remote-access/home-issuer` (root:root 0700), derived from
the existing installer state root; no helper account is added. It must
authenticate each new issue
and explicit recovery separately under the existing helper contract. This core
does not confer authentication or mint authority on any caller.

The minter associates a non-reusable key with the helper-generated operation
ID, bounds its own execution and returns `MintedKey(key, expires_at)` with an
aware deadline. `MintRefused` guarantees no key was created; every other raised
or malformed mint outcome is uncertain. `recover(operation_id)` is explicit
reconciliation, never another mint: only exact Boolean true, confirming no
usable key remains, releases the uncertain operation for a later fresh issue.
False, errors, restart and expiry preserve the refusal. No automatic new-ID
retry or key recovery/delivery is provided.

`issue()` allocates an operation ID and atomically journals `minting` with file
and directory fsync **before** calling the minter. A random, unredeemable pairing
digest first replaces the old ceremony through the existing pairing API, so a
refused/crashed replacement cannot preserve the prior invite, pending approval
or undelivered credential. On success the actual encoded pairing digest replaces
that reservation. The journal commits `issued` before the private invite text
is returned once. `issue(operation_id)` only observes the matching current
operation's closed refusal (`already_issued` after success); it never mints or
redelivers. An old operation ID returns `superseded` after replacement.

The effective integer deadline is `floor(min(floor(issue_time) + 300,
minted_key_expiry))`; both wire timestamps are whole Unix seconds. Minting
cannot extend it. An expired or slow result is not delivered. A durable
`not_before` deadline enforces one attempted issue per 60 seconds, including
refusal and issuer restart; a backward clock does not release it. A nonblocking
exclusive file lock refuses concurrent/reentrant issuance with `busy`.

`observe()` returns frozen `InviteObservation(phase, expires_at)` from the
validated journal without acquiring a lock, creating files/directories or
calling minting, pairing, recovery or invalidation. An absent journal reports
`none`; other outcomes are returned verbatim, except `issued` at or past its
effective deadline reports `expired`. Only unexpired `issued` exposes the
floored integer deadline; every other phase exposes `None`. In particular,
`minting` and `uncertain` remain unchanged across expiry. Observation never
rewrites the journal or returns invite text, keys, secrets, digests, operation
IDs or configuration bindings; unsafe/malformed/mismatched state raises the
existing closed, chain-free `IssuerError` codes. Protected helper/Status wiring
remains in the later serial 3c-2b slice.

The supplied directory must already exist, be owned by the effective helper
UID and have mode 0700. Every literal parent is traversed without following
symlinks. Journal and lock files must be regular, single-link, owned 0600 files;
unsafe metadata is never repaired. The bounded, strictly parsed journal holds
only an operation ID, a protected-configuration binding digest, deadlines and
an outcome code. Exclusive pending writes use descriptor-relative rename and
fsync. Existing crash residue refuses further writes rather than being erased.
Filesystem faults may leave the old `minting` record or pending residue; they
never authorize another mint. Explicit operator repair of unsafe/crash-residue
metadata remains outside this core.

Raw invite, join key and pair secret stay in the private return path and never
enter journal bytes or logs. Transport objects have redacted representations.
Public `IssuerError` arguments are fixed codes; library/adapter exceptions are
discarded before raising, with no cause or context retained. Callers must keep
the explicit invite field out of diagnostics. Focused standalone verification:
`uv run --frozen pytest scripts/test_invite_issuer.py -v`. Synthetic minters,
temporary real pairing state and an abrupt child-process exit cover this core;
real local minting, protected IPC/authentication and installed/native acceptance
remain in the later serial 3c-2b slice.

### Inert Linux home admin transport (THR-327 slice 3b-2g-1 prefix)

This prefix delivers items 1 and 2: `home_admin.RootHeadscaleAdminOperations`,
`DurableOperationRecordStore` and the uncalled `RootHomeInviteMinter` composition.
Its fixed root runner is `/opt/happyranch-desktop/libexec/remote-access/happyranch-home-admin`.
It reuses pinned `golang.org/x/net/http2` v0.27.0; no new module or credential.
Only v0.25.1 Create/List/ExpirePreAuthKey over the literal local
`/run/happyranch-headscale/headscale.sock` are supported. No CLI key operation.
Root/service-owned literal parents are opened no-follow; service directory is
0700, socket 0600/single-link with service UID/GID and matching SO_PEERCRED.
The installed binary and its parents must be root-owned, without group/world
write; the regular single-link binary is 0755. No endpoint/path/environment
selector exists. Before reading any secret, runner command startup discards the
standard logger's output. This suppresses x/net's separate frame logging even
when inherited `GODEBUG=http2debug=1` or `=2` enabled flags during package init;
clearing `VerboseLogs` alone cannot suppress those flags. No diagnostic sink is
re-enabled, and the private binary response writer is separate.

Requests/responses use bounded binary stdin/stdout pipes, never argv/env/logs;
runner failure is only exit 2 (no RPC sent) or 3 (uncertain), with no diagnostic
stream. Python drops stderr/native exceptions and raises fixed categories
outside handlers. Complete unary uncompressed gRPC framing, HTTP 200 and exactly
one successful grpc-status are required; other server codes remain uncertain.
All response fields follow R1–R3 below, without JSON/protojson. Datetime projects
valid nanos to microseconds by truncation, so expiration never extends.
One original monotonic deadline, capped at five seconds, spans Python custody,
request encoding, the remaining subprocess/socket/RPC/body budget, complete
wire/record projection, per-row user/duplicate-ID validation and final success.
Cooperative checks at bounded byte intervals, between records and after final projection
raise sanitized uncertainty on expiry; no partial or late success is returned.
Strict projection checks minimal varints directly without re-encoding each field,
uses a compiled full-key check and retains a bounded cache of validated timestamp
bytes only, never keys or records. Required fields, complete validation and
duplicate-ID/user checks remain unchanged. The annual maximum-rate history
(527,040 ordinary untagged rows) is exercised under the real five-second deadline;
host sizing measurements must leave transport margin within that shared budget.
The runner also checks its original context after the private response write,
including any preceding durable fence settlement, before returning exit 0.
Input and each projected record are capped at 4096 bytes, response at 128 MiB,
inventory at 600,000 rows and tags at 128 per row. A single HTTP/2 ClientConn
performs one RPC without Transport automatic retries.
That byte cap fits 600,000 ordinary ~200-byte untagged rows, but unusually large
history/tags may hit it first: complete-or-fail, never partial recovery. These
are bounded lifecycle/work assumptions, not hard RSS or OS-I/O deadline proof.

`/var/lib/happyranch-remote-access/home-minter/happyranch-invite` is root:root
0700, reached using literal descriptor-relative no-follow traversal. A
nonblocking directory flock spans the whole mint/recover, including cleanup,
reserve, create and result validation. Cooperating invite writers use this
composition. Reservations use exclusive 0600 files named by SHA256 of operation
ID, containing only UTC creation/deadline timestamps; file and parent fsync
precede success. Within one Headscale DB lifetime, existing/partial slots are never
overwritten, removed or reused. The sole removal exception is B6 retirement after
same-operation DB deletion proof (I3c M1).
The runner independently serializes each user's RPCs using a protected 0600
single-link `rpc.lock`. Before Create it writes/fsyncs an exclusive
`fence.pending`, renames to `fence` and fsyncs the directory. A complete successful
reply settles/removes that fence durably. Timeout, lost/invalid framing or failure
leaves the requested expiration fence across invocations/restart. List and new
Create refuse until that deadline passes; RPC cancellation is no settlement.
A malformed/unsafe fence or any pending residue refuses indefinitely, preserving
operator evidence. Expire may run, but cannot prove cleanup without complete List.
No record/fence auto-cleanup or retention/reset operation is introduced within
one Headscale DB lifetime; the sole removal exception is B6 retirement after
same-operation DB deletion proof (I3c M1).

HomeInviteConfig obtains home_endpoint only from the protected READY activation
receipt, populated by B2 after the local node joins. The stable installation
config carries neither endpoint nor activation generation. Items 4 and 5 now supply
inert issuer-directory custody and the uncalled local-node mint composition.
`prepare-headscale` publishes `home-issuer/` through the installer's literal
no-follow, exclusive pending-directory/no-replace/fsync path and records its
owner and directory identity in root:root 0600
`home-issuer-publication.json` beside it. An existing exact safe empty directory
can be receipted; matching complete custody is idempotent. Unsafe metadata,
foreign contents, pending residue or a mismatched receipt refuses without repair.
An interrupted pending publication is preserved; a complete safe empty directory
without a receipt can be accepted by a later prepare. A retained receipt whose
directory is missing never authorizes prepare to recreate it.

The issuer portion of disable/uninstall removes only the receipted `home-issuer/`
directory, after validating all contents and acquiring any existing issuer lock.
Only the core's regular single-link root:root 0600 journal and empty lock are
admitted; journal shape and canonical bytes must match the core's format, and
`minting`/`uncertain` refuses regardless of expiry. Unknown/edited/unsafe entries,
an orphan journal or lock, pending residue and lock contention preserve the
whole directory with typed secret-free `issuer-cleanup-uncertain`. A removal
interruption retains a cleanup pending marker and reports
`issuer-partial-cleanup-uncertain`; retry preserves that residue. This issuer
cleanup does not remove its parent, `home-ca/`, `home-minter/` or any receipts.
It performs no mint/recovery or key-revocation operation. I3c extends only the
receipt-bound B6 path after DB proof plus successful Disable revocation, with
B16 intent/outcome; no-argument issuer cleanup keeps these rules unchanged.

`home_admin.mint_local_node(operation_id, expires_at)` reuses the same root admin
transport, minter policy and durable reservation store for fixed
`happyranch-node`, with a separate root:root 0700
`home-minter/happyranch-node` directory flock. It returns `MintedKey` only in
memory, enforces at most five minutes, single-use/non-reusable, non-ephemeral and
no ACL tags, and retains existing/partial reservations without reuse within one
Headscale DB lifetime; the sole removal exception is B6 retirement after
same-operation DB deletion proof (I3c M1). No installer,
enable or helper path calls it; activation remains 3b-2g-2.
This inert admin slice changes no unit bytes, default home_transport flag or
activation caller. E2a below supersedes its blanket admission deferral for
validated ready lifecycle and foreground custody only; E2b below supplies an
uncalled first-activation backend. The admin transport itself performs no
service or control_tls effect.
Native root/Headscale/systemd and installed acceptance remain NOT RUN.

### Home-network engineering settlements B1-B18 (THR-327 seq577)

Founder THR-327 seq577 assigns B1–B18 to Engineering. These are implementation
requirements within the approved home-network design, including the manager's
2026-11-18 round-2 corrections to B2/B5/B6/B8 and corrected B6 accounts rule.
They do not claim delivery: the inert prefix, activation refusals and acceptance
status immediately above remain unchanged. Implementing PRs still require
independent security review, scoped QA at the exact head, green required lints
and manager guarded merge.

All points fail closed on unknown, torn, foreign, oversized, linked or mismatched
state: refuse and retain rather than adopt, repair or delete it, except for the
exact owned link/socket operations expressly listed below. Connector/sidecar
remain `happyranch`, Headscale remains `happyranch-headscale`, and the helper
remains root. No new account, package dependency, hosted infrastructure, CI
trigger or timer beyond the R2 policy timer is authorized. Existing v1 records
and journals retain their meanings; no DB schema or `audit_log.task_id` change.
Never expose raw invites, pre-auth keys, pairing secrets, node private/public
keys or digest prefixes in status, logs, audit or public output. Numeric limits
are maxima; smaller implementation limits need sizing evidence.

**B1 — Protected activation/config and endpoint receipt.**
`/etc/happyranch/invite-issuer.json` is root:root 0600, at most 16 KiB, closed
`home-invite-config@2`. Its field set is the format, release UID, installation IDs
(`home_id`, `control_plane_id`, `connector_id`), CA DER SHA-256, dial IP and canonical
control URL. Generation and endpoint belong only to the activation receipt;
legacy @1 bytes refuse. No-receipt Enable capture expects generation 0, including
a first activation after B6 retires the receipt.
`ManagedInstaller.publish_invite_config()` is the sole initial publisher, called
by public Enable with no operator inputs. Under the existing activation-directory flock it derives fields
from complete protected staged-inert provenance, release/source-token binding,
Headscale publication and materializer status. Release UID/IDs, CA and dial IP
come only from those protected sources; control URL is fixed. The existing A5
exclusive-pending/fsync/no-follow rename publishes root:root 0600. Exact-byte reuse
is a no-op; differing bytes, pending residue or unsafe custody refuse. It never
rewrites or deletes; B5/B6/B17 maps are unchanged.
`/var/lib/happyranch-remote-access/home-activation.json` is root:root 0600 in the
existing root:root 0700 parent, at most 16 KiB, closed `home-activation@1`.
It binds the same release UID/IDs, monotonic integer activation generation,
operation ID, CA fingerprint, dial IP, fixed unit/config member digests, observed
local-node StableID, canonical runtime endpoint and phase; its ownership
inventory also enumerates B6 members and the single B5 link. No arbitrary paths
or extra fields are admitted. Observation fields remain unpopulated before join;
only B2 supplies them, and `ready` requires them. Phases are
`prepared → activating → joined → ready`, with terminal `failed_uncertain`.
Each transition uses an exclusive pending write, fsync and descriptor-relative
rename under literal no-follow parents (the existing A5 publication discipline),
never an in-place edit or pending adoption. Only B6 owns deletion; v1 install
records are neither reused nor extended.

G2-A fixes the exact closed top-level keys of `home-invite-config@2` to
`format, release_uid, home_id, control_plane_id, connector_id,
ca_der_sha256, dial_ip, control_url`;
`format` is the literal version above. The exact closed top-level keys of
`home-activation@1` are `format, release_uid, home_id, control_plane_id,
connector_id, activation_generation, operation_id, ca_der_sha256, dial_ip,
member_digests, local_node_stable_id, home_endpoint, phase, ownership`.
`release_uid` uses the existing non-root release UID range, IDs use the v2
invite identity grammar, `activation_generation` is a nonnegative integer
(never Boolean), `operation_id` uses the existing 32 lowercase-hex operation
ID, and `ca_der_sha256` is 64 lowercase hex. `control_url` stores exactly
`https://control.happyranch.invalid:8443`; endpoints use the existing v2
ASCII authority validator and dial IP uses its canonical RFC1918 IPv4 predicate.
Both observation keys are present as JSON null before join; they are populated
together with a valid StableID and endpoint by observation, and required at
`ready`. A joined record may await observation. `failed_uncertain` may retain
an already-observed pair, but a failure from prepared/activating cannot invent
one. Ordinary phase updates preserve release/installation IDs, generation,
operation ID, CA and dial IP, and any already-populated observation. Generation
rollover/recovery remains coordinator-owned; the linear publisher never resets
a terminal record. B4 defines the separate `home-activation-txn@1` fields and
per-effect intent publication below.

`member_digests` is a closed object with exactly these six literal keys, each
a 64 lowercase-hex SHA-256 of the full file bytes, required in **every** phase
and recorded by the coordinator before prepared publication:
`/etc/systemd/system/happyranch-headscale.service`,
`/etc/happyranch/headscale/config.yaml`,
`/etc/happyranch/headscale/derp.yaml`,
`/etc/happyranch/headscale/policy.hujson`,
`/etc/happyranch/control-ca.pem`, and
`/etc/happyranch/invite-issuer.json`.

Prepare, activation verification and ordinary admission read the exact
`policy.hujson` member as `happyranch-headscale:happyranch-headscale` `0600`,
matching its writer. Invite configuration remains root-owned `0600`. The
policy is separate from the Headscale publication record's file map; neither
that map nor the six receipt members change, so retained product publications
and receipts use the same format and digests.
Activation verification selects this fixed custody layout after checking both
service accounts, including when retry or recovery skips Prepare in that process.

The Linux managed connector CLI passes its resolved configuration path to the
supervisor. Home admission still requires exactly `/etc/happyranch/connector.json`
and the fixed connector unit; an alternate path cannot acquire home authority.

Missing, extra or duplicate keys and noncanonical hex refuse. Secret material
(`server.key`, `daemon.token`, `enrollment.key`, CA private key) is never
digested; renewable `server.crt` is excluded because trust binds the CA DER
fingerprint (B13); v1 `connector.json`, `sidecar.json`, `policy.json` retain
their meanings with B4 live field cross-checks owned by G2-C; B6 live members
and the B5 link are separate ownership inventory, not digests.

`ownership` has exactly `members` and `links`: `members` is a
duplicate-free array enumerating exactly the eight literal paths in B6's table
(the five individual files, two sockets and empty directory; I3c extensions are
not receipt ownership members), and `links`
is the single-key object mapping the literal B5 link path to its exact literal
target. No path selection or deletion authority follows from parsing it.
The G2-A reader requires an externally expected generation, a ready receipt,
equal release/installation IDs, CA and dial IP, and the full config-file digest
from that receipt. Generation is checked against the receipt; endpoint comes
only from its ready observation, re-verified through B2. It pins/revalidates both files and
literal parents, refuses either pending entry, and never manufactures an
endpoint. The distinct <=4096-byte issuance journal under
`/var/lib/happyranch-remote-access/home-issuer/invite-issuer.json` and the
existing DER-bearing in-memory `HomeInviteConfig` keep their meanings.
The new receipt publisher/config reader remain uncalled; no mint, unit,
service, activation admission or readiness proof is added by this slice.
Why no founder: internal root-only layout, unchanged UX.

**B2 — Authoritative endpoint observation.**
Root reads the serving sidecar's LocalAPI `Self` result through only
`/run/happyranch-tsnet-sidecar/observe.sock`. Systemd creates the existing
`RuntimeDirectory=happyranch-tsnet-sidecar`, owned `happyranch:happyranch`;
its mode is inherited unchanged (no new `RuntimeDirectoryMode`), and
`RuntimeDirectoryPreserve=yes` stays. No `/run/happyranch-remote-access` parent
is created. Only the home-profile sidecar (`home_transport=True`) creates the
socket, using restrictive umask and fchmod 0600 before listen, owner
`happyranch:happyranch`. Inert/legacy profiles have no observation socket and
retain their unit bytes and M1 residue rule. On start, unlink a pre-existing
entry only if lstat proves a `happyranch`-owned socket with nlink 1; any other
type/owner refuses startup. Sidecar unlinks on orderly stop; B6 owns residual
Turn off cleanup. Existing `health.json` handling stays.

Before connecting, root lstats a real parent directory owned by `happyranch`,
not group/other-writable, and an S_ISSOCK entry owned by `happyranch`, mode 0600,
nlink 1. SO_PEERCRED must identify that UID and the current MainPID of the exact
owned sidecar unit. One connection carries one fixed `observe-self@1` request,
with a 5-second deadline and reply at most 4096 bytes. The reply supplies
StableID, tailnet addresses, control URL and node-key SHA-256, never a raw key.
Root cross-checks control URL, home/control-plane ID, installation and activation
generation, and alone publishes the canonical endpoint into B1. Health, metrics,
hostname, CLI stdout, environment and JS input never supply endpoint authority.

**Manager Ruling 8 (G2-D1, B2/B4):** joined→ready is a separate explicit,
unmounted entry, in the same run or a fresh instance, admitted only from txn
joined with all eight effects done and receipt joined with the same binding,
generation and operation ID and both observation fields null. It performs only
B2 observation, the read-only B3 cross-check below and one A5 receipt publication,
with source-token validation before publication. Observation/admission failure
publishes nothing and leaves both joined records byte-unchanged. It performs no
service, create, mint or config effect. Every other pair retains Ruling 7's
terminal-only forward-run path; this entry itself refuses without mutation.
The read-only ready verifier requires fresh identical StableID, endpoint and
bindings; it never publishes, mints or rekeys.

**Manager Ruling 10 (G2-D1, B2):** home Headscale allocates IPv4 only.
Require exactly one observed address, an IPv4 in `100.64.0.0/10`; accept a host
address or exactly `/32`, normalized to the address. Zero/multiple addresses,
any IPv6, any other prefix or out-of-range IPv4 refuses. `home_endpoint` is
canonical dotted-decimal IPv4 plus `:443`, passing the existing v2 endpoint
validator. Never select by ordering, hostname or MagicDNS.

**Manager Ruling 11 (G2-D1, B2/B3):** SO_PEERCRED must match the UID and current
MainPID of the exact owned sidecar unit. This slice uses an injected owner;
production systemd ownership/admission wiring remains G2-E. The sidecar Config's
one optional closed `home_binding` member contains exactly `home_id,
control_plane_id, connector_id, activation_generation`, with the B1 grammars
and a JSON integer generation (never Boolean/string). Admit it only with
LinuxIdentityProfile, valid nonnil ControlTLS and exact homeURL; refuse it
elsewhere. Absence preserves inert/legacy bytes and behavior. Return the values
loaded at process start in `binding`; root compares them and exact `control_url`
to the protected receipt and externally expected generation. This detects stale
config and is not independent node proof. Root calls only the fixed B3 adapter's
`ListNodes(user="happyranch-node")`, requiring exactly one node with the reply's
node-key digest and that node's addresses containing the Ruling 10 IPv4. Zero,
duplicate, digest or address mismatch refuses. A null receipt StableID is
populated together with the endpoint only in joined→ready; a populated StableID
must match exactly on every later observation.

The exact request bytes are `observe-self@1\n` followed by client write-half-close;
extra or pipelined bytes refuse. The closed UTF-8 JSON reply is at most 4096
bytes, exactly `format, stable_id, tailnet_addresses, control_url,
node_key_sha256, binding`, with format `observe-self@1`; extra/missing/duplicate
keys (including binding keys) refuse. LocalAPI Status.Self supplies node identity
and addresses; current LocalAPI preferences supply control URL. Non-running or
failed engine observations refuse. Socket custody and the one request/reply share
a five-second deadline; root carries the remaining budget into ListNodes.
Production writing of `home_binding` is deferred to G2-E. These components remain
unmounted, with no runtime/CLI/app/D-Bus caller or production home selection.
Why no founder: private IPC between existing processes, unchanged user/network UX.

**B3 — Fixed Headscale DB-user/node administration.**
Extend only the existing root runner/adapter with `ListUsers`, `CreateUser`
(names only `happyranch-node` and `happyranch-invite`), `ListNodes` filtered to
those users. `DeleteNode` lands with B12, once its recorded StableID-to-node-ID
ownership proof exists; it is deferred from G2-B. No generic RPC or secret CLI
argument. Per call: 5 seconds, at most 64 ListUsers rows, 512
ListNodes rows, 4 MiB response and 4096 bytes per record/input. Lists must be
complete; truncation refuses. These new limits do not replace the existing
600,000-row/128 MiB pre-auth-key inventory limits. After a lost CreateUser reply,
re-list by exact name and adopt only the exact user with zero nodes and zero
pre-auth keys outside our fences; otherwise refuse and retain. A foreign extra
user refuses and is never deleted.

G2-B keeps these operations uncalled. `list-users` takes an empty request;
`create-user` and `list-nodes` take exactly one fixed user name through stdin,
never argv. Each ListNodes call requests one of the two users and verifies every
returned row belongs to that user. Closed tagged v0.25.1 schemas reject unknown
fields (including pagination), duplicate identities/singular fields and torn
records. Output projects user ID/name and node ID/user. G2-D1 additively projects
`node_key_sha256` (SHA-256 of the exact stored Headscale node-key text, 64
lowercase hex) and `ip_addresses` (at most 16 distinct canonical host addresses,
no zones or IPv4-mapped IPv6). The private runner output encodes the digest as
field 3 and repeated addresses as field 5 alongside node ID field 1 and user
field 7; missing/empty node-key text, invalid or duplicate addresses refuse.
Raw node/machine/disco/pre-auth keys and other metadata are discarded before
stdout. The Python adapter independently validates these bounded fields.

CreateUser serializes on the existing per-user `rpc.lock` and honors the existing
pre-auth-key fence. Before its sole create, it fsyncs an insert-only root:root
0600 `home-minter/<fixed-user>/user-create` attempt marker containing `1`.
The marker is retained on every outcome within one Headscale DB lifetime; the
sole removal exception is B6 retirement after same-operation DB deletion proof
(I3c M1). A missing user with a prior marker or
an unsafe/torn marker refuses as uncertain, never retries create or repairs it.
Both successful and lost replies require a complete exact-name re-list and
zero nodes/keys under the same original deadline; a successful reply must also
match the re-listed user ID. Existing key fences record no key IDs, so G2-B
cannot prove a nonempty key inventory belongs to our fences and refuses it.
A repeat exact user is returned only after the same empty-inventory proof.
Pre-create refusal uses runner exit 2 / `AdminRefused`; possible create effects
use exit 3 / `MintUncertain`. Reconciliation retains the existing complete
pre-auth-key response bounds, rather than replacing them with the B3 limits.
Why no founder: fixed-name operations on the existing root-only admin transport.

**B4 — Staged/activating/live transaction.**
Use new `home-activation-txn@1`, separate from failed-replacement v1, with fsynced
per-effect intents and digest-bound members. Preserve immutable installation
IDs; validate the same recorded release UID and same-FD token source at start
and before publication. `activating` admits exactly one local-node join, no
public invite, readiness or device approval until `ready`. Only this transaction
may add ControlURL/control_tls from validated `prepared` state; validators admit
the live field set only alongside matching `home-activation@1`. After a crash,
resume only a phase whose intent and on-disk members both verify; otherwise
`failed_uncertain`, requiring Turn off/ReplaceFailed. Never adopt pending files.

G2-C1 fixes `/var/lib/happyranch-remote-access/home-activation-txn.json` as
root:root 0600, at most 16384 bytes inclusive, in the existing root:root 0700
parent. Its exact closed keys are `format, release_uid, home_id,
control_plane_id, connector_id, activation_generation, operation_id,
ca_der_sha256, dial_ip, phase, effects`. Format is `home-activation-txn@1`;
all binding fields use the B1 receipt predicates and must equal that receipt.
Phase is `activating`, `joined` or terminal `failed_uncertain`. `effects` is a
nonempty ordered prefix of exactly `preflight, headscale_start_verify,
db_users, local_node_mint, credential_staging, home_config, managed_services,
joined`. The tuple, effect names, `TRANSACTION_FORMAT` and journal JSON shape
are unchanged. Forward `headscale_start_verify` now renews the same shipped
policy and strictly validates currentness after its intent and before its own
reload/enable/start; the later `managed_services` renewal remains. Old journals
at that intent still parse and follow the existing I3a terminal/recovery rules
without renewal or effect replay. Each entry has exactly
`name, state, member_digests`; state is
`intent` or `done`. Only the last entry may be intent. Each intent is fsynced
before its effect; done is fsynced only after independent verification.
`member_digests` is empty except for done `home_config`, where its exact keys
are `/etc/happyranch/connector.json` and `/etc/happyranch/sidecar.json`, each
binding the complete nonsecret file bytes with 64 lowercase-hex SHA-256.
Prepared B1 members remain bound by the receipt and cannot change. Credentials
remain private transient material, independently custody/consumption-verified
by the owner, never copied or hashed into either record (B1). Missing, duplicate,
extra, arbitrary-path or out-of-order entries refuse. Appending the next intent,
finishing the last intent, and closing all eight done effects as joined are the
only forward updates. A terminal update preserves the exact effect prefix.
Publication reuses A5, with no mkdir, in-place edit or pending adoption. A
nonblocking directory lock spans both records and all effects. Only unattended
policy renewal may wait up to 20 seconds for this same activation-directory
flock, below its unit's TimeoutStartSec=30, then performs the same protected
revalidation. Fresh-auth renewal, RetryPolicyRenewal and every other caller
still refuse immediately on contention. Exhaustion fails closed and keeps the
existing lock-busy diagnostics. The explicit timer start/verification remains;
this wait handles its first firing while Enable still owns the lock.

Source validation gates every forward publication (including phase advances or
token-derived/live material) and service/admin effect. The durable effect boundary
is this operation ID's first fsynced intent: failure before it returns a closed
`token_source_invalid` refusal without changing either record or performing an
effect, allowing a later retry from prepared. Failure at/after it stops all effects
and publishes terminal `failed_uncertain` in txn and receipt. These nonsecret
markers carry no token-derived bytes or live ControlURL/control_tls, admit only
Turn off/ReplaceFailed or the explicit B7 fresh-auth Enable recovery, and are
exempt from source validation while retaining full A5 discipline (Ruling 6).

**Manager Ruling 7 (G2-C1, B4/B7; completed-pair exception in B2 Ruling 8):**
Failed terminal publication is durably indistinguishable from a crash. B4's "resume only" is a permission, tightened in
this slice: a fresh coordinator may start normally only with a prepared receipt
and no transaction intent. Any existing transaction, in any phase or intent/done
prefix (including preflight and joined checkpoints), permits no forward effect
or forward publication. Attempt nonsecret `failed_uncertain` markers in txn-first,
receipt-second order without requiring source verification. If marker publication
still fails, refuse and retain the unchanged remaining bytes; every later fresh
instance repeats this terminal-only path. Already-terminal records stay terminal.
Same-run forward progression is unchanged. B7 explicit fresh-auth Enable recovery
is the sole route back to ready for that failed operation; Turn off/ReplaceFailed
may retire it. G2-C2 owns that recovery and is not implemented in G2-C1.

G2-C1 is an unmounted injected-owner composition ending at `joined`, with null
observation fields. Start and every forward publication revalidate the recorded
release UID and existing installer's same-FD source against the provisioned token.
The coordinator checks receipt/file digests itself; the owner independently checks
current policy/generation, custody and actual service/user/node/credential state.
Preflight samples the existing four exclusive port binds after its durable intent,
before any service/admin effect; an occupied port then terminates the transaction.
No intent or completed prefix is replayable by a fresh instance under Ruling 7.
The original run independently verifies each effect before recording done;
interrupted start/create/mint/staging/config/enroll and lost private mint results
remain terminal, with no automatic forward recovery.
The existing `mint_local_node` retains its durable operation fence: no second mint,
new operation ID or automatic reconciliation. G2-D owns observation/ready; G2-E
must wire production owners, protected live config admission and service integration.
Why no founder: internal transaction with unchanged, fail-closed admission.

**Manager Ruling 13 (G2-E1, B4 home_config owner), corrected by 13a below:**
The B4 home_config effect owns a new deterministic exported renderer, A5 publisher
and independent verifier. This implements the settled staged-to-live transition;
it grants no installer admission. Sidecar live bytes add ONLY
`ControlURL=https://control.happyranch.invalid:8443`, closed slice-1b `control_tls`
(`dial_ip, ca_sha256`) and Ruling 11's closed `home_binding`, derived from protected
B1 receipt/config values. Preserve immutable IDs and every other prepared byte.
Connector fields may change only if already parsed for the home profile; absent
such a need, connector bytes remain unchanged and digest-bound. An unparsed field
is a STOP, not permission to extend a parser. Publication uses literal no-follow
parents, exclusive pending, file/directory fsync and rename, without mkdir or
pending adoption. Independent verification re-reads live bytes, compares them to
the renderer output and checks the transaction's done home_config digests.
The renderer is E2's single source; `_write_provision_file` stays unchanged.

**Manager Ruling 13a (G2-E1, authoritative prepared-byte binding):**
Ruling 13's original phrase "receipt-bound prepared digests" is corrected:
B1's six-member digest set excludes connector/sidecar configs, and B4 admits their
digests only on done home_config. Neither record is extended. The existing
protected `/etc/happyranch/provision.json` supplies the prepared hashes, read only
through existing ManagedInstaller custody/validator rules (literal no-follow
parent, root:root 0600). Require versioned `version==1`, `phase==provisioned`,
matching layout and the existing closed digest keys; legacy unversioned refuses.
Read connector/sidecar bytes once as the renderer inputs, hash-check both against
provenance and apply the existing staged linux-managed-identity-v1 validation
(six-key inert sidecar). Cross-bind their home/connector IDs, and control-plane ID
where carried, to B1. The digest-checked release-daemon.json UID must equal B1's
release UID; B1 generation/operation must equal the coordinator's expected (G,O).
Any mismatch or absence refuses without publication: pre-intent no effect,
post-intent Rulings 6/7 failed_uncertain. Re-hash the exact captured input bytes
immediately before rendering and publish only from those bytes. Done verification
compares fresh live bytes to renderer output and transaction member digests;
B7 recovery uses transaction digests, not provision.json. Do not write provenance.
At the E1 boundary the installer intentionally reported drift/refused the live
profile; E2a below adds the protected validated-state exception. The I5d-0
rollover admission below supersedes E1's original G+1 live-config refusal.
Why no founder: implements settled B4 using an existing protected record with
stricter cross-checks, without schema, dependency, user, UX or check relaxation.

**G2-E1b delivered boundary (B4/Rulings 13/13a):**
`home_live_config.py` supplies the unmounted `HomeLiveConfigOwner` required by
`HomeActivationOwner(config_owner=...)`. Its exported pure
`render_home_live_configs` is the shared E2 renderer. Connector bytes stay exactly
unchanged: the existing Python loader already carries the needed identity fields,
and no additional connector field is needed or invented. Sidecar rendering inserts
only the three authorized fields before the final object brace, retaining every
prepared byte, including whitespace. `home_binding` is part of these protected
root:service-group 0640 sidecar bytes, not a separate file or caller argument.

Assumptions: the future authenticated composition supplies expected G/O and holds
the coordinator's original lock. A valid protected receipt supplies all live
values. Existing installer readers/validators establish versioned provenance and
staged identity custody; the exact connector/sidecar inputs are captured once,
then re-hashed and their no-follow leaf/parent observations revalidated before
render/publication. A5 publication uses new exclusive pending inodes, file and
directory fsync and rename; it creates no directory, adopts no pending entry and
adds no rollback/retry. Source verification gates each config publication.
The two renames are separate effects within the one home_config intent: partial
publication remains Ruling 7 failed_uncertain, never an atomic-pair claim.

The coordinator's existing order independently verifies exact renderer bytes
before recording done digests. During that same original intent only, the owner
uses its captured renderer result; after done, verification additionally requires
both transaction member digests. A fresh/B7 verifier derives prepared bytes by
removing only the exact authorized insertion, reuses the same staged validator
and renderer, and binds the complete live bytes through the transaction digests.
It never uses provision.json as live/recovery authority. No prepared config or
secret digest is added to B1/B4. The standalone temp-root/fake-native composition
reaches home_config done; installed acceptance remains unverified.

E1b's original fail-closed boundary reported provisioned-config drift/refused the
live profile after home_config done. E2a below supplies the validated exception.
For I5d-0, only a prepared receipt paired with an older failed_uncertain txn
admits (a) staged bytes or (b) a proven earlier render: connector equals its provision
digest, and sidecar ends in the exact authorized insertion for integer
0 <= g < receipt generation, with every other field equal to the receipt.
Stripping that insertion must yield the provision sidecar digest. An old done
home_config additionally requires both live digests equal its member_digests
and g equal its txn generation; intent/partial evidence refuses. I5d-2 adds
(c), checked after (a) and (b), only when that older txn has NO home_config record
at all: reuse the generation-0 post-Disable proof below for exactly one retained
insertion with protected stable IDs, any integer g >= 0 (even g >= receipt G),
and a strip digest equal to provision. Retired CA/dial fields are not compared
with the new receipt. An old home_config record, including intent or partial
publication, never uses (c); done evidence keeps (b)'s digest/g rule. Unknown/foreign
tails, strip mismatch, pending residue and custody drift refuse without effects.
Captured data/digests are the proven staged bytes, with the live custody signature.
The next normal home_config perform/_publish alone writes the new render;
recovery never rewrites configs. Current-txn _verify_live matching, renderer,
source checks, B1 digests and B5/B6/B17 stay unchanged. E1b grants no
restoration authority. Installer/supervisor admission, provenance, drift, unit
publication and home_transport selection remain E2; I5d-2 selects that existing
backend through public helper Enable with the unchanged native authorization.

I5d-2's post-Disable admission additionally permits a prepared generation-0
receipt with no transaction to prove the retained connector against its exact
provision digest and strip exactly one deterministic sidecar addition. Its
stable IDs must equal protected provisioning and its old generation must be an
integer >= 0. The stripped bytes must equal the provision sidecar digest; closed
transport fields are retired values, never the next receipt's authority. Captured
data/digests are proven staged bytes and custody signatures remain the live
file's. Normal `home_config` perform/publication alone writes generation 0 with
the new receipt's trust fields. No admission/recovery rewrite is added. L1(c)
reuses this same proof for the bounded older-failed-transaction case above;
all current-transaction checks stay intact.

**Manager Ruling 14 (G2-E1a, retained local-node key history):**
The owner verifier validates the complete happyranch-node pre-auth-key inventory:
an exact tuple of at most `_MAX_INVENTORY_KEYS`, every row through the existing
minter `_record` validator, with unique IDs. Select the current key only from the
operation's durable local-node creation window, inclusive of both endpoints;
exactly one row must have `started_at <= created_at <= expires_at`. It must be
non-reusable, non-ephemeral and untagged, with expiration exactly equal to the
window expiry, and, if unused, still unexpired. Every other row must precede the
window and be used or expired at the verification time. In-window duplicates,
post-window rows and still-usable unused history refuse. Safety assumption:
historical keys must also be non-reusable, because used reusable keys can still
enroll; this is the ruling's permitted stricter history check. History retains
the existing `_record` rules for ephemeral flags and tags, without new restrictions.
This admits B7's retained expired-unused/used rows through the next fenced mint
and credential staging, without changing the minter, recovery or coordinator.
The owner has no separate retained-node singleton check; node observation remains
with the existing B2/B3 observer and its live-node contract.

**G2-E1a delivered boundary and stated assumptions:**
`home_activation_owner.py` supplies unmounted fixed-effect adapters, an independent
same-FD release-token source verifier and the observer's exact-unit process owner.
The coordinator retains all intent/lock/mint/recovery ownership; adapters issue
no retry after a lost native/admin reply. Systemd process observation checks exact
Id/Names/fragment, no drop-ins/reload, active/running, fixed User/Group, no dynamic
user and canonical MainPID against exact unit bytes. UID comes from the existing
account validator for systemd's declared fixed user; B2's SO_PEERCRED independently
checks the actual UID/PID and re-observes process identity after the exchange.
Service verification also uses fixed B3 lists, the reservation window, consumed
credential custody, the RELEASE verifier and B2 node observation. No cached
successful effect reply establishes verification. Native account/certificate/unit
readers remain their original owners; certificate checks invoke status only.

The roughly 1500-line E1 limit is handled by the authorized E1a/E1b split. E1a
REQUIRES an explicitly supplied config owner with no default: its preflight must
implement 13a prepared binding (or txn-bound live verification after publication),
and its home_config perform/verify must implement Ruling 13. E1b now supplies
that explicit dependency as `HomeLiveConfigOwner` above; no default owner or
public caller is selected. E2b now composes this dependency in its uncalled
backend and amends managed_services under Ruling 16 below. Authentication and
expected G/O remain trusted caller preconditions under the activation lock.
E2a supplies protected ordinary admission; E2b alone supplies the first home unit
publication effects. No CLI/D-Bus/helper entry or public Enable is mounted.
Ordinary installation still cannot publish a first home profile.

**Manager Ruling 15 (G2-E2, privilege-separated admission), refined by 15a/16:**
Ordinary root installer install/start/restart/enable and supervisor per-unit
start/restart/enable require euid 0 and complete protected admission. Expected G
comes from live sidecar.json's closed home_binding and must equal both the ready
home-activation@1 receipt (Ruling 9 reader semantics) and matching txn. Require
home_config done; live connector/sidecar bytes equal the shared
render_home_live_configs output from versioned provision.json-bound prepared
inputs (13a), with SHA-256 equal to the txn member digests; and the B5 single
fragment/link gate. Unprovisioned, partial, forged, stale/foreign, pending,
caller-URL-only, failed_uncertain and joined-not-ready refuse. A non-root ordinary
home lifecycle command has no foreground fallback.

Unprivileged foreground supervisor.run and the Linux sidecar instead require the
fixed non-root happyranch account and existing config custody: root-owned,
single-link regular files without group/other write, literal no-follow root-owned
parents. The closed live profile (ControlURL, control_tls, home_binding under
11/13) must parse with consistent IDs in both configs. The serving process never
reads protected receipt/txn/provision records. There is no credential copy, IPC,
ExecStartPre, extra unit line or permission change. Root alone publishes these
bytes through the receipt/txn-bound Ruling 13 owner; B2 independently checks the
serving node before ready and invite issuance still requires a ready receipt.
Absent enrollment input or the existing consumed/empty-key state serves without
mint/rekey. Present input uses only the existing enrollment rules, never a new
mint. Pinned pairing and per-device revocation remain unchanged.

Accepted residual 15(3): systemd restart/boot can re-serve already-published,
root-custodied, txn-verified bytes after a receipt becomes failed_uncertain. It
cannot issue invites, retains pinned per-request pairing/revocation, and B15
reports the condition. Retirement belongs to B6 Turn off/ReplaceFailed; no stop
is added to failure paths (Ruling 7). This explicit residual is not a ready claim.

**Manager Ruling 15a (phase scope):**
Ready-only admission applies to ordinary lifecycle outside activation, valid
restart and the owned-activated classification. Activate/Enable instead retains
the merged coordinator's admission unchanged: root, authenticated expected G/O,
prepared receipt with no txn (or Ruling 12's replaceable older failed txn),
same-FD RELEASE-source checks and operation lock. Effects inside the run require
activating receipt plus the matching exact effect intent at G/O, durable before
effect and independently verified before done (6/7), with the first unit
publication order refined by 16 below. They never use the ordinary lifecycle
path. After joined, publish_ready retains Ruling 8's preconditions. If ready is
not reached, started services remain as they are and ordinary lifecycle refuses;
foreground admission and residual 15(3) govern serving.

Recover retains 12/12a: root, fresh authentication and expected G/O, failed pair
or exact half-published target; resume verifies/publishes only, while rollover
publishes prepared(G+1,new O) only. Later activation is a new operation. Recover
gets no selection/start exception. Non-ready inventory follows Ruling 9: a parsed
current receipt may own its home fragment/link, and live configs with home_config
done must match txn digests. Such entries are owned with phase, not drift or
adoptable. Only ready is owned-activated; reporting grants no lifecycle effect.
All other drift checks remain. These rulings add no privileged entry, caller,
OS-user, permission, dependency, Track B/C or customer-visible behavior change.

**Manager Ruling 16 (first inert-to-home unit publication order):**
The Headscale fragment is a B1 member. Prepare, a step of the backend Enable
entry (item 5), writes render_headscale_unit(home_transport=True) BEFORE computing
the six member digests and publishing prepared(G,O). B4's prepared members never
change afterward. Prepare requires root, authenticated expected G, the existing
nonblocking activation-directory lock spanning Prepare → run → publish_ready,
no receipt/txn/pending (first activation only), same-FD RELEASE checks, Headscale
stopped through the existing observation, and no B5 link. Current fragment bytes
must be exactly inert or exactly home for an idempotent crash retry. Use existing
A5 exclusive pending, fsync and no-follow rename; only inert→home replacement is
allowed. Daemon-reload is then permitted, never enable/start/link. Validate the
other five members with existing readers, compute all six digests, generate O
under the existing 32-lowercase-hex predicate and publish prepared through G2-A.
Backend calls run with that same G/O under the same lock. G+1 after failure comes
only from Ruling 12 rollover over already-home bytes.

Crash residual 16(1): home fragment with no receipt is not admitted by B5 and is
drift. Ordinary lifecycle refuses until Enable is retried (exact home bytes
accepted) or B6 Turn off runs. No other path adopts, repairs or deletes it.

The sidecar unit is not a B1 member. SSL_CERT lines must never be published in
Prepare, because a systemd restart could change a running v1 sidecar's trust.
The minimal E1a amendment belongs to item 3: preflight accepts only exact legacy
or exact home sidecar rendering, still loaded with no drop-ins and
NeedDaemonReload=no on every check; connector/target remain exact. After fsynced
managed_services intent and source revalidation, that effect alone publishes
home sidecar bytes through A5 (replace exact legacy or no-op on exact home),
daemon-reloads, then performs the existing fixed enable/start. Verification
before managed_services done and joined/ready requires exact home bytes.
At/after-intent failure is failed_uncertain with bytes left as they are, no
restore/stop. Non-ready inventory reports the receipt-bound home sidecar as owned
with phase, never drift or adoptable. Coordinator code is unchanged: its B1
pre-intent check sees the prepared home fragment and repeated preflight accepts
either exact sidecar rendering. Existing prepublished-home E1a fixtures remain;
legacy-start and Prepare cases are additive in E2b. No check is loosened: the
replacement has the listed preconditions and the sidecar set contains only two
exact root-custodied renderings. No new privileged entry/caller/user/permission,
dependency, Track B/C or customer-visible change is authorized.

**Manager Ruling 17 (G2-E2b backend crash re-entry):**
Under the held activation-directory flock, Enable classifies the protected current
pair for the expected G/O before Prepare or forward work. A prepared receipt with
no txn keeps its existing admission. A matching txn with a nonterminal receipt
(prepared at preflight intent, activating at any intent/completed prefix, or joined,
including joined txn plus activating receipt) takes only the coordinator's existing
Ruling 7 terminal-marker path: binding-checked failed_uncertain, txn first and
receipt second, exempt from source validation, then refusal. A failed publication
retains the remaining bytes; later attempts repeat only this terminal path.

Ready or both failed_uncertain refuse unchanged: ordinary admission owns ready,
and Ruling 12/12a Recover owns failed pairs. Stale/mismatched G/O, unknown/unsafe
records or pending residue refuse unchanged without adoption, except the I5d-0
B7 pair: prepared receipt, older failed_uncertain txn, equal every binding key
other than generation/operation, and distinct operation IDs. Under the held lock,
backend Enable forwards only that nonmatching pair to normal compose -> run ->
publish_ready. The unchanged coordinator repeats its checks and replaces the old
txn at the first intent with expected=previous. Matching txn Ruling 17 remains
exactly the terminal-marker path above; every other nonmatching pair refuses.
That terminal path cannot Prepare, replay an effect, create O/G, restore units,
start/stop/reload services, mint, call admin APIs, probe ports or publish configs.

The backend passes the actual protected receipt to the unchanged coordinator's
_pending/_fail handling after classifying the pair. _fail independently rereads
and checks both bindings before terminal publication. No prepared receipt is
fabricated, no coordinator entry is added, and run/forward logic stays unchanged.
This remains an inert backend with no production caller or public Enable.

**G2-E2a delivered boundary and assumptions:**
The roughly 1500-line split places items 1/2/4 here. home_admission.py composes
existing readers, protected provenance, shared renderer and HomeUnitGate. It
inverts only the shared exact insertion, authenticates the reconstructed prepared
bytes against provision.json, then rerenders and checks complete live txn bytes.
Ready ordinary install is a keyless re-enable/start of already-validated existing
units, without unit rewriting, key staging or minting. Supervisor start/restart/
enable requires the fixed on-disk connector config to equal its parsed config;
foreground run uses the separate fixed-account custody boundary. Legacy inert
config and unit renderings are unchanged. Existing non-start lifecycle refusals
remain until their separately owned work lands.

Inventory's protected current receipt supplies phase/G for observation of inert
prepared configs; installation IDs and release UID are independently bound through
versioned prepared provenance. Live ordinary admission always takes G from the
sidecar binding. This observation is never an activation/adoption grant. Fixed
home CA/invite/config-directory entries and observe.sock are recognized only with
that binding; unknown config/state/unit/link entries continue to refuse. Before
managed_services done inventory accepts either exact sidecar rendering; after done
it requires home. This comparison performs no publication or unit selection.

**G2-E2b delivered boundary and assumptions (items 3/5):**
`HomeActivationPrepare.prepare(expected_generation, authenticated)` publishes the
home Headscale fragment, daemon-reloads only, validates the protected prepared
inputs and six members, and publishes prepared(G,O) through the existing G2-A
store. It returns (G,O); its exact-home crash retry never touches the sidecar.
No existing receipt, txn or pending entry is admitted by Prepare. A post-rename
failure leaves the documented unreceipted home-fragment drift for explicit retry
or B6; it is never automatically adopted, restored or deleted.

E1a preflight now admits exactly legacy or home sidecar bytes, loaded without
drop-ins or reload debt. Only managed_services with its durable matching intent
replaces exact legacy bytes through A5, reloads, then enables/starts the existing
fixed units. Its done and joined verification require exact home sidecar bytes.
Failure leaves final/pending bytes in place and the coordinator records
failed_uncertain under Ruling 7. E2a's existing phase-aware inventory owns the
receipt-bound home sidecar with phase; no duplicate inventory gate is added.

`HomeActivationBackend.enable` composes Prepare → unchanged coordinator.run →
publish_ready with E1a/E1b owners and B2 observer. One thread-owned adapter borrows
the same held activation-directory flock for the coordinator's nested lock
contexts; other threads and independent descriptors/processes still refuse
nonblocking. An already-prepared expected G with no txn enters unchanged
coordinator admission without rerunning Prepare or rewriting B1 members.
Ruling 17 classifies existing pairs first and routes matching nonterminal pairs
only to the unchanged terminal-marker path; no existing txn is resumed.
`recover(expected_generation, expected_operation_id, fresh_authenticated)` uses
the unchanged Ruling 12/12a recovery with the same production owners; no effect
is executed by recovery. Authentication/freshness and expected G/O are trusted
caller-supplied preconditions, never inferred from the records or a Boolean UI.
I3a mounts this backend through the fixed bundled-Python helper child,
with B8/B9 fresh authorization and protected G/O capture. I5a exposes public Enable;
Linux CLI wiring is delivered in I5b; bridge wiring for Enable/Disable/IssueInvite is delivered in I5c (source only; I6 pending). Under the same held lock, Enable sends only matching
failed receipt/failed txn pairs, or failed receipts with joined recovery-marker
txns, to `recover` with captured G/O. Its protected proofs and no-effect semantics
remain unchanged. The child reports the returned phase, including rollover's
`prepared` (a present non-ready receipt is projected as B15 setup
`setup_unverified`); ready never replays activation; the ordinary start described below is permitted.
No user, unit default or permission change is added. Temp-root/fake-native tests exercise ready and
recovery with real A5 records and observe.sock; installed/live-host operations
remain NOT RUN. Legacy unit renderers are unchanged; only the existing two
sidecar SSL_CERT lines and B5 Headscale lines are selected by these owners.

**B5 — Home-only Headscale boot/stop dependency and inventory gates.**
Only the home Headscale rendering adds
`Before=happyranch-tsnet-sidecar.service` and
`[Install] WantedBy=happyranch-managed.target`. The sidecar gets no Headscale
reference, After/Requires dependency or other unit delta beyond the two A4
SSL_CERT Environment lines. Legacy renderings stay byte-identical; hardening,
MDWE, notify and watchdog stay unchanged. Before orders sidecar stop before
Headscale; Turn off explicitly stops sidecar → connector → Headscale. No
Requires/RequiredBy coupling: Headscale outage leaves sidecar retrying and B15
reports runtime availability.

The literal-reference content exclusions are the exact owned
`/etc/systemd/system/happyranch-headscale.service` fragment and the single
package helper at `/usr/lib/systemd/system/happyranch-remote-access-helper.service`
under the exact bytes/root:root 0644/regular/single-link rule above (K1/K2).
The helper exclusion requires no activation receipt and grants no exclusion to
helper drop-ins, changed files or same-name copies under other roots.
Without a valid current-generation activation receipt only the Headscale
fragment's inert rendering is admitted;
home bytes require SHA-256 equality to the receipt-bound unit digest. No other
unit/drop-in gains an exclusion. Exactly one new link is admitted:
`/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service`
→ exactly `/etc/systemd/system/happyranch-headscale.service`, recorded in that
valid receipt. Without it the inert no-link rule remains; every other matching
link still refuses, including manually created links. Cleanup disables this
one owned link then re-verifies absence. All other foreign-unit/drop-in,
literal-reference and bounded no-follow inventory checks remain mandatory.

**Manager Ruling 9 (G2-D2, B5):** a valid current-generation receipt means the
protected receipt parses as closed `home-activation@1` through the existing A5
no-follow reader, with release UID and all three installation IDs equal to the
caller-supplied expected values, and `activation_generation` equal to the
externally expected generation. **Every phase**, including `failed_uncertain`,
qualifies: member digests and ownership are mandatory in every phase, and
cleanup must still see the owned link. Pending entries, unsafe files, parse
failures and other generations grant no exception: only the inert rendering
and no matching link are admitted. Home bytes must equal the B5 rendering and
their full SHA-256 must equal
`member_digests['/etc/systemd/system/happyranch-headscale.service']`. Only the
literal B5 link/target pair in `ownership.links` qualifies; relative targets,
other load-root copies, aliases, extra links and manual links without that
receipt refuse. Inert fragment bytes remain admissible without a receipt.

G2-D2 exposes only unmounted `HomeUnitGate.inspect()` and
`disable_owned_link(disable)`. The latter inventories before the injected
synchronous disable operation and freshly inventories with the owned link
required absent afterwards; a persistent/replaced link or changed receipt
refuses. Its owner must serialize cooperative root/systemd writers and supply
an authorized operation that removes **only** this exact link, with lifecycle
quiescence established separately. There is no systemctl call, service stop or
receipt deletion in this primitive. Missing links need no disable operation.
The gate retains the inert scanner's load roots, literal-reference checks,
65536-entry shared bound, 32-depth bound and 65536-byte foreign-policy bound,
using literal no-follow parents/readers and revalidating sampled metadata.
E2a now wires this gate into protected ordinary admission and phase-aware
inventory. E2b adds first publication only through Prepare and the intent-bound
managed_services owner; ordinary installation does not publish a first home unit.
I5d-1's `ManagedInstaller.publish_home_units()` fills the clean-install fragment
publication prerequisite; I5d-2 selects it from public Enable. Under the existing
activation-directory flock it re-verifies protected modern provenance, all five
original config/token digests, release binding and same-FD token source. Missing
fragments require a complete current staged-inert bundle. It writes exactly
`managed_units(layout, connector_config=<protected connector.json>)`, with
literal pinned no-follow parents, exclusive `O_EXCL|O_NOFOLLOW` files,
root:root 0644 and file/parent fsync. Exact prefixes survive interruption and
are reused on retry, including before systemd has loaded them. A complete
exact set is reused without writes; the retained home sidecar variant is also
accepted only on this reuse path, by the same renderer accepted by
`HomeActivationOwner._managed_fragments`. Its preserved original sidecar bytes
are checked against provenance; this neither admits nor republishes live config.
All candidate load roots are scanned before effects with shared 65536-entry /
32-depth bounds. Differing files, aliases, override copies, even empty exact,
dash-prefix or type-wide drop-in directories, and pending residue refuse.
Daemon-reload uses the existing manager; the loaded FragmentPath, DropInPaths,
NeedDaemonReload and LoadState must match `_managed_fragments`, with exact Names
also excluding aliases. No enable/start occurs and the closed provision writer
map is unchanged. This sampled inventory retains the cooperative-root/systemd
writer assumption; it is not a racing-writer fence.

Turn off stops, disables and proves quiescence of these five managed fragments,
then retains their root:root 0644 renderer-exact, nonsecret files. A later
publication reuses them without writes. B17 removes them on replacement. B5,
B6 and B17 inventory/deletion maps remain closed and unchanged (I5d prerequisite
ruling Addendum A); retaining fragments grants no active service or admission.
Why no founder: boot persistence for an already-approved service, unchanged UX.

**B6 — Live-state ownership and per-type deletion.**
The earlier stop-time `_inventory` also admits H1 diagnostics already present in
`/run/happyranch-tsnet-sidecar` (I5g): only `health.json` (0644) and numeric
`.health-` temps (0600/0644), regular files with nlink 1, exact existing service
UID:GID, at most 65536 bytes each and eight diagnostics total (excluding the
separately validated `observe.sock`). Inventory uses no-follow metadata through
the literal parent FD and never opens/reads diagnostic contents. Every other
inventory name, directory and custody refusal stays unchanged. Admission grants
no deletion authority: confirmed stop/quiescence still precedes the existing B6
retirement below; post-Disable staging and uninstall behavior stay unchanged.

Only a valid current-generation `home-activation@1` receipt admits its enumerated
fixed members plus the Headscale-written allowlist and I3c extensions below. Require full
quiescence: all owned units inactive, no job/process/cgroup, and ruling C's
bounded UID process snapshot. Every entry uses literal no-follow parent
traversal; absence is fine, wrong type/owner/mode/link count is retained and
reported as incomplete cleanup.

| Exact live-state members | Required type/custody and removal |
| --- | --- |
| `/var/lib/happyranch-headscale/{db.sqlite,db.sqlite-wal,db.sqlite-shm,noise.key,derp.key}` (five files) | S_ISREG, receipted Headscale UID:GID, 0600, nlink 1; fixed-file unlink only. |
| `/run/happyranch-headscale/headscale.sock` | S_ISSOCK, receipted Headscale UID:GID, 0600, nlink 1; unlink only, never connect/read. |
| `/run/happyranch-tsnet-sidecar/observe.sock` | S_ISSOCK, owner `happyranch`, 0600, nlink 1; unlink only, never connect/read. |
| `/var/lib/happyranch-headscale/acme-unused` | Empty real directory, receipted Headscale UID, 0700; rmdir only. |

The first four rows above remain the eight `ownership.members` in B1; the I3c
fixed extensions below do not change the receipt schema.

| Exact I3c/I5f residue | Required type/custody and removal |
| --- | --- |
| `/run/happyranch-tsnet-sidecar/health.json` and names matching `^\.health-[0-9]+$` (I5f H1) | After existing stop/quiescence proof, before the existing rmdir pass: at most eight entries total, each S_ISREG, existing `happyranch:happyranch` UID:GID, nlink 1, at most 65536 bytes; mode exactly 0644 for health.json, 0600 or 0644 for temp files. No-follow lstat via the pinned literal parent FD; validate the whole set before the existing observe.sock unlink and again before diagnostic deletion, recheck each activation signature before dirfd-relative unlink, fsync the parent, then existing empty-only rmdir/fsync. Any other name or unsafe metadata refuses with zero deletions in that directory. Never open/read contents. Applies to every B6 path reaching this pass, including failed revoke; persisted sidecar state retention is unchanged. |
| `/var/lib/happyranch-remote-access/home-ca/{ca.key,ca.pem,leaf.pem,leaf.key}` | S_ISREG, root:root 0600, nlink 1; metadata-only unlink. Parent root:root 0700; rmdir only when empty. |
| `/var/lib/happyranch-connector/{trust-state.json,trust-state.json.anchor,trust-state.json.lock}` | S_ISREG, happyranch:happyranch 0600, nlink 1; metadata-only unlink, only after successful Disable revoke-all. Parent real happyranch:happyranch 0700, nlink 2 when empty; rmdir only when empty. |
| `/var/lib/happyranch-tsnet-sidecar/{tailscaled.state,credential.consumed,tailscaled.log.conf,tailscaled.log1.txt,tailscaled.log2.txt}` | Same service file/parent custody and revoke-success requirement as connector state; no opaque state read. |
| `/var/lib/happyranch-tsnet-sidecar/files/happyranch-node-uid-1` and its `files` parent | Exact pinned-tsnet home-node tree only: both directories service UID:GID, 0700, no symlinks; child empty, parent contains only that child or is empty after partial removal. Inventory validates the whole tree; B6 removes the child then empty parent only after successful revoke and existing quiescence proofs. Other names, contents or custody refuse. No recursive removal or opaque contents read. |
| `/var/lib/happyranch-remote-access/home-activation-txn.json` | Existing bound B4/A5 root:root 0600, regular single-link transaction; canonical reader and current binding required, then unlink only after DB proof and B16 intent. |
| `/var/lib/happyranch-remote-access/home-issuer/{invite-issuer.json,invite-issuer.lock}` and `home-issuer-publication.json` beside the directory | Existing canonical root:root 0600 regular single-link journal/empty lock and fixed-directory identity receipt; existing nonblocking issuer lock. After DB proof and successful revoke (ceremony burned), B6 also admits canonical minting/uncertain outcomes; pending/unknown/unsafe entries still refuse. Parent root:root 0700, nlink 2; existing fixed unlink/rmdir and parent fsync, B16 intent/outcome. |
| `/var/lib/happyranch-remote-access/home-minter` | Real root:root 0700 directory; only `happyranch-node` and `happyranch-invite` child names admitted; nlink equals 2 plus present children; rmdir only when empty. |
| `home-minter/{happyranch-node,happyranch-invite}` | Real root:root 0700 directories, nlink 2; nonblocking directory flock in that fixed order. Only `operation-[0-9a-f]{64}`, `user-create`, `fence`, `fence.pending`, `rpc.lock` admitted, each S_ISREG root:root 0600 nlink 1, metadata only. Streamed enumeration is bounded at 600,000 entries per user. Existing rpc.lock flock also must be free. Unknown name/type/owner/mode/link count, bound exhaustion or contention refuses the whole minter set unchanged. |

The exact Taildrop allowance also accepts the tree left by v6/v7; no unit or
receipt migration is needed. Pinned tsnet v1.78.0 creates it unconditionally on
peer-API initialization; `TS_DISABLE_TAILDROP` only disables receiving and does
not prevent directory creation. Its exported Server API has no storage-disable
option.

I3c C4/M1 proof is derived inside the existing B6 run: all five Headscale DB,
WAL/SHM, Noise and DERP members have been removed and re-observed absent while
the owned units are quiescent. An already-absent member is admitted only by the
existing receipt-bound partial-removal retry handling. No caller supplies this
proof. Without it all B7/B11/home-minter evidence remains and cleanup is incomplete.
Nonterminal/pending issuer state with an active ceremony refuses; successful
revoke-all proves the ceremony was burned, not that the DB was removed.

M1-M7 scope reservation/marker/fence retention to one Headscale DB lifetime.
After the proof, validate and lock the entire two-user minter set before any
record removal. Per user unlink operation reservations, user-create, fence,
fence.pending, then rpc.lock last; fsync the user directory, release its directory
flock, rmdir it, and fsync its parent. Finally rmdir empty home-minter and fsync
`/var/lib/happyranch-remote-access`. Interruption leaves a subset; retry requires
fresh same-operation B6 proof again. B16 records a durable intent before and
outcome after each fixed user's retirement, with counts only for reservations,
markers, fences and pending fences; never IDs, hashes, contents or key material.
Retaining user-create beyond the DB lifetime would permanently refuse a later
fresh CreateUser. Fresh Enable still requires a new activation G/O CAS.

Fixed custody directories otherwise follow existing D/custody rules: rmdir only
when empty. If descriptor-relative rmdir returns EROFS only for a fixed custody
path whose parent is exactly `/var/lib` or `/run`, ask PID 1 through fixed
`/usr/bin/systemd-run` to execute `/usr/bin/rmdir -- <same fixed path>` in a
transient unit: empty CapabilityBoundingSet/AmbientCapabilities, NoNewPrivileges,
PrivateNetwork, ProtectHome and PrivateTmp, with 20-second start/runtime bounds
and a 30-second caller timeout. The native package declares systemd and coreutils.
The empty scan, owner/mode/signature checks, pinned parents, require/inventory
checks and userdel order remain mandatory. Prove absence through the pinned
parent before proceeding. Missing tools, nonzero exit, timeout or a remaining
directory retain partial cleanup. Other parents and non-EROFS errors refuse;
helper unit hardening, ReadWritePaths and capabilities remain unchanged.
Without a valid activation receipt D still refuses DB/Noise/socket
and unreceipted entries; no-argument callers remain unchanged. Unknown/extra or
unsafe objects remain and report incomplete cleanup; no recursive deletion.
Explicitly destroy the owned CA private key on Turn off. B16 audit survives.

Accounts follow A–E and spec §7 unchanged. Remove a helper-created, validly
receipted `happyranch-headscale` user/group only after the owned unit is stopped
and disabled, no job/process/cgroup, the bounded UID snapshot and fresh identity/
unshared-group checks; userdel precedes receipted unshared groupdel, the receipt
retires last, and partial failure remains `partial-cleanup-uncertain`. Preserve
and visibly report an unowned account under A. Existing `happyranch` handling
stays unchanged. The two Headscale DB users are not OS users and disappear with
the owned DB.
Why no founder: exact ownership/removal under approved Turn off and existing A–E.

I2a supplied the installer B6 executor; I3a privately mounts it through
`ManagedInstaller.cleanup_headscale` with all five explicit expected release UID,
installation IDs and generation arguments. It uses the A5 reader under the
existing activation-directory lock, B5 disable/absence gate, full owned-unit
quiescence and C's UID snapshot before deletion and fresh checks before effects.
Existing no-argument CLI/uninstall calls retain D exactly. The private I3a lifecycle
caller closes admission, revokes and stops services first, in the order below.
It additionally supplies the captured operation ID, checked under B6's existing
receipt lock before any effect; the five identity/generation expectations retain
their meanings. No public Disable, Enable or ReplaceFailed is mounted here.
I2b supplies B17's separate first-intent entry described below.

I3a admission-close ruling (TASK-17385 R1–R4, corrected by R7): Disable holds the single helper
mutation slot for its whole call, blocking all other root-owned admission paths.
It then calls existing B10 `revoke-all@1` on the serving connector. Its first
committed transaction burns the invite, pending approval and undelivered grant
and snapshots the device set; per-device durable denial and attributed stream
closure follow, including removed-owner fencing. Only then stop and disable the
owned connector, sidecar and verifier through the existing installer stop/confirm
path. The lifecycle owner also validates the receipted local Headscale unit and
loaded fragment before stopping it, then checks its existing UID/process proof.
The other recognized remote units are disabled only after quiescence; B6 owns
the receipted Headscale link's disable/absence proof. B6 may run only after confirmed quiescence, using
the five expectations captured from the authenticated activation store.

A restart or supervisor runtime rotation between revoke and stop re-serves no
admissible devices or ceremony after successful revocation. This composition
adds no IPC verb or new meaning to `revoke-all@1`. Revocation non-success
(unavailable, changed, expired or lost reply) records the appropriate B16
refused/uncertain outcome and **still runs stop**. Stop/ownership uncertainty
prevents B6 and reports partial cleanup. **I3c extends the same B6 executor:**
the private helper carries its actual revoke-success Boolean through the fixed
root child, alongside captured identities/G/O; it never comes from D-Bus inputs.
Only successful revoke permits connector trust and persisted sidecar retirement.
Neither purge nor the B17 executor is used for Disable. Complete requires
successful revoke-all AND confirmed stop AND successful B6 AND all admitted
residue retired. Failed revoke ALWAYS produces `partial_cleanup`, naming retained
un-revoked trust/sidecar state. A changed generation permits at most one fresh
read/retry within the original 300-second budget. CA staging and B7/B11/minter
evidence follow the I3c custody/proof/audit rules above; any remainder is incomplete.
I3a's helper worker mounts this composition and records the closed outcome
and safe residue components; I5a exposes the public methods, with Linux CLI
consumers delivered in I5b and bridge Enable/Disable/IssueInvite consumers delivered in I5c (source only; I6 pending).

The executor unlinks only validated fixed members (including the separately
publication-receipted leaf/key handoff), explicitly unlinks the root-custodied
CA private key without reading it, and removes fixed custody directories only
when empty. Absence supports a partial-cleanup retry; existing preparation
publication validation still requires every member. Unknown/unsafe entries
refuse, with no recursive removal. Activation/publication evidence survives
earlier partial failures; the account receipt retires last under A–E.
I3c retires canonical B7/B11 evidence and the whole fixed home-minter record set
only after same-operation DB deletion proof, with B16 intent/outcome persistence.
This is evidence retirement, not recovery, journal adoption or automatic retry.
The fixed CA staging set now includes ca.pem/leaf.pem/leaf.key as well as ca.key;
all are validated and unlinked without reading their contents. Unknown or unsafe
residue prevents account/activation receipt retirement and complete Turn off.

**B7 — Local-node mint uncertainty and explicit recovery.**

Complete Disable retires receipt, transaction, invite configuration and live
authority while retaining the renderer-exact managed fragments and configs.
Turn on admits this retained shape only with complete protected provenance,
exact connector digest and the single proven sidecar addition described in B4.
No B7/B11/minter evidence, live connector/sidecar/Headscale state, pending entry
or other cleanup residue may remain. Partial cleanup retains the existing Retry
turn off route. Initial invite-config publication uses the staged-byte proof
without changing retained config bytes. B6 remains stop/disable/retain and its
closed deletion map is unchanged; the next activation creates fresh authority. Retained
failed markers are reset only with the fresh fixed-name/byte/manager proof above,
before activation/account/publication receipts retire. The five fragments and
configs remain retained; no file or unit bytes change for this recovery.
Persist one root-generated activation operation ID before `mint_local_node`.
A lost create/enroll/start outcome is `failed_uncertain`, never an automatic
new ID. Fresh-auth Enable on that record re-lists the fixed user's keys/nodes:
exactly our fenced used key plus exactly one matching enrolled node observable
through B2 permits resuming to `ready`; no usable key and no node permits one
fresh mint under a new generation. Anything else stays uncertain and requires
Turn off/ReplaceFailed. Valid restart uses empty-key state and never rekeys.
B6's sole retirement exception requires the same-operation DB deletion proof
and B16 audit described above; it does not permit replay inside a DB lifetime.
Why no founder: fail-closed recovery through the existing Enable action.

**Manager Ruling 12 (G2-C2, B7 explicit fresh-auth recovery)** —
engineering_manager, TASK-17421 step 19. Context: main after G2-D1 #1690
(`101d1bc5`), with G2-A #1677, G2-B #1673, G2-C1 #1684 and G2-D2 #1687 merged.

1. G2-C2 introduced the explicit entry `home_activation_recovery.py`; I3a now
   privately mounts it through Enable with B8/B9 fresh authorization and captured
   G/O. Require both receipt and txn `failed_uncertain` for
   externally expected generation G and the same operation O. Other pairs refuse
   unchanged; a matching nonterminal pair with a txn takes Ruling 7's terminal
   marker path first and never recovers in that call. Validate release UID and
   the same-FD source token before any Headscale read and before every publication;
   source failure refuses without publication under Ruling 6 semantics.
2. Use only B3 `list_pre_auth_keys` and `list_nodes` for `happyranch-node`, with
   complete bounded lists. Error, timeout, limit or parse failure retains uncertain
   state unchanged. Our fenced key has creation time inside O's durable
   `CreationWindow`, read without mutation from the existing root-custodied,
   no-follow `operation-<sha256>` reservation. Never rewrite/delete reservations
   within one Headscale DB lifetime; the sole removal exception is B6 retirement
   after same-operation DB deletion proof (I3c M1).
3. RESUME requires exactly one key, inside O's window, used, non-reusable and
   non-ephemeral; exactly one node; B2 observation passing Ruling 11 against that
   node; and independent owner verification of all eight effects, without executing
   any effect. Publish ready(G,O) with the observation pair through a separate
   recovery-only validator. Ordinary `validate_transition` stays unchanged.
   The recovered pair must pass the unchanged G2-D1 ready verifier. One additive
   closed nonsecret txn recovery marker is permitted when necessary. Publish txn
   first, receipt second, through A5.
4. FRESH/ROLLOVER requires zero nodes and zero usable keys (unused and unexpired).
   After all proofs and `_unchanged(receipt, txn)`, the existing B7 recovery owner
   calls the installer's fixed-path `_remove_enrollment()` to retire the abandoned
   generation's enrollment source and intentional credstore link. It rechecks
   `_unchanged(receipt, txn)`, then source-token binding before publishing receipt
   `prepared(G+1,O')` with a new root operation O' (32 lowercase hex). This receipt
   publication remains the sole commit point. Custody or removal failure refuses
   without publishing prepared and retains the failed pair. A crash after source
   removal may leave only the link or neither entry; fresh-auth retry repeats all
   proofs and the idempotent removal. READY/RESUME and half-published branches never
   remove enrollment. No mint, expire or forward effect runs in recovery. Retain
   txn(G) untouched. Coordinator fresh-start admits an existing txn only when
   it is older than the prepared generation and `failed_uncertain`, replacing it
   at the first intent. The I5d-0 backend forwards this pair only for equal stable
   identity binding and distinct operation IDs (Ruling 17); the existing live-config
   owner proves earlier rendered bytes against staged provenance as above. Recovery
   never rewrites invite/live configs; the normal next home_config writes G+1.
   An unused unexpired key keeps uncertainty; callers may
   retry after the 120-second key expires. Crash repeatability is amended by 12a.
5. Everything else refuses unchanged with a closed nonsecret reason and stays
   uncertain for Turn off/ReplaceFailed.
6. Valid-restart empty-key admission belongs to G2-E production wiring.

**Manager Ruling 12a (G2-C2 crash outcomes; amends Ruling 12(4))** —
engineering_manager, TASK-17421 step 20. A5 is unchanged: no pending adoption
and no pending deletion outside B6.

- Pending residue: a crash after exclusive pending inode creation/fsync and
  before rename leaves old finals unchanged plus the retained pending entry.
  This is an admitted fail-closed outcome. Every later recovery or coordinator
  attempt refuses with closed reason `pending_residue`, bytes unchanged, until
  authorized inspection / Turn off / ReplaceFailed; B6 owns removal. Recovery
  repeatability applies only without residue. No reconciliation mechanism or
  A5 writer/reader change is introduced; existing pending tests stay unchanged.
- Half-published RESUME: after txn-first publication and before receipt-second,
  admit exactly the txn already at the same (G,O) recovery target with identical
  observation pair and the still-failed receipt. Repeat the full source-token,
  B3 lists, reservation-window, B2/Ruling 11 and eight-effect evidence, and only
  then publish the receipt. Any failed condition refuses unchanged. No other
  mixed pair is admitted to recovery.
- ROLLOVER publishes only prepared(G+1,O'); txn(G) stays untouched. Crash outcomes
  are exactly the old failed pair, retained pending residue, or the new prepared
  receipt plus the older failed txn. The next Enable's B4 live-config admission
  tries provision-equal bytes, then the exact earlier render, then L1(c)'s retained
  post-Disable proof only if the older txn has no home_config record. That proof
  also applies after repeated rollovers; it captures staged bytes with the live
  signature, and only normal home_config publication renders the new G/trust.
  Older intent/partial home_config publication keeps its existing refusal limit.

G2-C2 implements `HomeActivationRecovery.recover` with externally expected G/O,
a strict fresh-auth precondition and an injected observation owner. It constructs
B2 with the fixed admin's fresh node list required equal to the recovery
singleton. All eight effects are verified, prepared/live members are rechecked,
and both records are compared before publication. No service/admin/config effect
is executed. Reservation reads retain the existing 0700 directory / 0600 regular
single-link file custody, bounded canonical two-UTC-timestamp encoding, and
literal pinned parents; they never create a missing directory.

The additive optional txn key `recovery_observation` is a closed object with
exactly `local_node_stable_id, home_endpoint`, using B1 observation predicates.
It is present only on a complete eight-done `joined` recovery target (or retained
on its subsequent `failed_uncertain` terminal marker). Absence preserves legacy
format bytes; explicit null, extra fields, partial effects or `activating` with
this marker refuse. No new phase is introduced. The recovery-only validator
permits failed→joined with that marker after full verification; ordinary txn
transitions cannot add/change it or leave a failed state. This makes 12a's half
pair distinguishable while keeping G2-D1 `verify_ready` unchanged.

Why no founder: fail-closed recovery through the already-settled B7 Enable action;
no new dependency, UX or production activation authority. Production wiring,
installed fresh-auth/systemd/Headscale acceptance and valid-restart admission
remain G2-E; home_transport remains false.

**B8 — Installed helper, bootstrap and fixed write inventory.**
Package the system D-Bus service for fixed BusName `ai.happyranch.RemoteAccess1`, User=root,
SystemdService=`happyranch-remote-access-helper.service`, Type=dbus, with
`NoNewPrivileges=yes`, `ProtectHome=read-only`, `ProtectSystem=strict`,
`PrivateTmp=yes`, and 60-second idle exit for status/device activity. Each settled
lifecycle operation renews the helper namespace as described in B9. Its exact
`ReadWritePaths=` list is:

```text
/etc
/var/lib/happyranch-remote-access
-/var/lib/happyranch-headscale
-/var/lib/happyranch-connector
-/var/lib/happyranch-tsnet-sidecar
-/run/happyranch-headscale
-/run/happyranch-connector
-/run/happyranch-tsnet-sidecar
-/opt/happyranch
```

`-` permits absence but supplies a writable bind mount only if the directory
existed when that helper process started. Clean `provision()` and
`prepare_headscale()` therefore leave connector/sidecar state, log and runtime
directories and Headscale state/runtime custody absent. Systemd creates them
at first unit start using the existing StateDirectory/RuntimeDirectory/
LogsDirectory declarations and modes. Present directories still undergo the
same exact custody checks, and preflight still inspects them for residue.
Pre-start Headscale policy/config/TLS handoff writes are under `/etc`, with
root CA/issuer custody under packaged `/var/lib/happyranch-remote-access`;
none requires writing inside the absent service-owned state/runtime dirs.
Root-only `/etc` and packaged installer-state creation retain their existing
rules. A temporary-root test enumerates actual write-open/mkdir events and
created paths for the composed provision → prepare_headscale → publish_home_units
→ publish_invite_config → forward Enable path, comparing
them to the shipped B8 list; optional entries count only when present before
the sequence. This is source/temporary-root evidence, not a live sandbox proof.
Before lifecycle capture, polkit or B16 intent, the helper checks `statvfs`
on the six optional service state/runtime paths above. Absent paths are allowed;
an existing read-only path or an unavailable observation refuses `unavailable`
with zero authorization/effects/B16 intent and schedules helper exit. This
fail-closed guard does not pre-create service directories or change the unit.
`/etc` permits shadow-utils' lock/temp/rename for passwd,
group, shadow and gshadow, and includes the existing happyranch, credstore and
systemd paths. System accounts skip lastlog/faillog, so `/var/log` stays
read-only; `/usr`, `/boot`, `/opt/happyranch-desktop` and everything else stay
read-only. The kernel sandbox is a coarse backstop: in-code fixed-path,
exact-name/no-follow checks still bind every effect (exact credstore link,
owned units and B17-listed legacy shapes only).

The single root helper owns all effects. System Python `-I`/already-required
Gio handles bus/polkit; the fixed bundled Python `-I` installer and existing
root-step runner (`useradd`/`userdel`/`groupdel`, `systemctl`) are children in
that same sandbox. No shell, user imports/settings, second privileged executor,
transient unit or different user. Before auth, derive clean-host release UID
from the actual bus credential UID and exactly one matching `pwd.getpwall()`
row. UID 0 and system UIDs below 1000 refuse `owner_unsupported`; zero rows
refuse `owner_unknown`, multiple rows refuse `owner_ambiguous`. If the fixed
root-owned `/etc/happyranch/release-daemon.json` exists, reuse the release
verifier's trusted-parent, no-follow, single-link and closed-record validation;
its UID/user must match the sender/passwd row or refuse `owner_mismatch`.
Its `data_home` wins; otherwise use only passwd-home + `.local/share/happyranch`,
never XDG/environment/caller input. Validate that single candidate by
descriptor-relative no-follow traversal: a real directory owned by the UID,
not group/world-writable, outside passwd-home/`.happyranch`, `/`, `/etc` and
`/var`. Missing/invalid evidence refuses `release_home_invalid`; no username
or path crosses the boundary. These five bootstrap refusal codes are closed.

THR-327 seq613 release-home creation is owned only by the packaged Linux
desktop/daemon launcher, using its existing absolute-XDG/default resolver. It
creates the final folder with explicit 0700 and no-follow verification of real
directory type, current UID and exact 0700 before publishing any state, including
GUI desktop.lock or execing the service daemon. Only a newly created verified
inode may have umask-stripped owner bits restored through its descriptor. Existing
unsafe folders are never chmod/chown repaired or accepted for remote access; a
message names the folder and cause (including group/world writes) and says it
must be private to the user. Existing daemon startup behavior is retained; Enable
still refuses `release_home_invalid` under the unchanged helper-owner checks.
Safe existing modes are retained. Shared ensure_daemon_home, non-release homes,
other daemon file modes and systemd UMask are unchanged. XDG creation does not
extend the helper's protected release-binding policy.

The packaged entry exposes Status/StatusV2/NextDevice/ApprovePending/RejectPending/
RevokeSelected plus I5a's seven public lifecycle/invite methods, with unchanged
admission. Linux CLI consumers are I5b and bridge consumers are I5c;
RefreshHomeAddress remains absent and B13-deferred. I3a supplies the stdlib-only
`helper_lifecycle.py` sibling and lifecycle mount; I3b adds the stdlib-only
`helper_invite.py` pipe adapter and CancelInvite/RecoverInvite mounting; I4a adds
zero-input IssueInvite with the exact issue-invite auth_admin/no-keep action.
I4b-2 adds RetryPolicyRenewal with its exact retry-policy-renewal auth_admin/no-keep
action. Its fixed existing bundled-Python `-I -m
runtime.remote_access.home_activation_backend` child accepts only the helper's
closed bounded capture/effect request, empty environment and original timeout.
Its native runner selects the existing account tools `useradd`, `userdel` and
`groupdel` by their fixed `/usr/sbin/` paths: the empty environment's default
`/bin:/usr/bin` search cannot find them on Debian/Ubuntu. Account arguments,
ownership validation and deadlines are unchanged.
Enable admission/capture catch-alls journal only fixed internal stage names and
exception class through local syslog. The Enable-effect child failure adds one
local warning containing only exception class, innermost product source-relative
file:line and the fixed enable step, following at most eight explicit causes or
suppressed contexts through closed wrappers. Source paths are limited to
160 characters and class names to 64. No message, argument, local, user path or
credential is formatted. Logging failure preserves closed refusal/exit and
all existing budgets and uncertainty. See the [local diagnostic runbook](../../operations/remote-access-runbook.md#local-home-enable-failure-diagnosis)
for the exact helper journal and read command. Founder THR-327 seq643 assigns
home-network acceptance to founder-run local Linux testing for this milestone;
this product change adds no hosted M1 run or harness behavior.
It captures protected activation G/O (including absence), initial Enable config
generation, and B17 inventory/journal generation. There is no bus-supplied UID,
path or generation. Already-designed enable/replace-failed/disable polkit actions
use the same auth_admin/no-keep defaults; bus policy and sandbox are unchanged.
Focused bootstrap/package verification uses
`/usr/bin/python3 -B packaging/linux/test_build_deb.py -v`; the existing
standalone `scripts/test_remote_access_helper.py` private-bus command also
checks refusal before connector reads/auth. These focused checks do not install
or start the helper. Daemon bearer is never setup authority. Package install only
registers the service and never runs Enable.
Why no founder: pins the approved root helper and existing dependencies/effects.

**B9 — Lifecycle capture, transport and operation deadlines.**
Enable/Disable/ReplaceFailed/RefreshHomeAddress capture sender unique name, UID
and protected installation generation without requiring a connector. Revalidate
under the one global mutation slot before every effect; no queued prompts.
Authentication remains 120 seconds. Lifecycle calls return accepted/refused
within the existing approximately 122-second native transport; the root
operation has a total 480-second budget including auth, with remaining time
passed to every step and no late success. Disable has a 300-second budget;
status at most 2 seconds. B15 conveys progress/terminal outcomes using
`ready`, `off`, `in_progress`, `failed`, `uncertain`, `partial_cleanup`.
Transport loss after dispatch never means no change.

Enable, Disable, ReplaceFailed, CancelInvite, RecoverInvite and RetryPolicyRenewal
send a destination-addressed `LifecycleCompleted(s)` signal on
`ai.happyranch.RemoteAccess1`, before captured slot/name release, to the original
unique caller only. Its sole method reply stays JSON `"accepted"` (admission).
The closed, duplicate-free, 512-byte frame names that original method,
correlation={serial:original call serial,owner:authenticated helper unique name},
outcome=failed|completed, and optional category on failed only. Enable may also
carry optional closed `firewallWarning` display advice (see F2 below). No broadcast,
cache, lookup, replay, cross-caller transfer or automatic Enable follows.
Disable's failed receipt has only `partial_cleanup`, `failed` or `uncertain`
as its category; it never sends residue paths. Only a settled Off produces its
completed receipt. CLI Turn off subscribes before admission on the same
connection and consumes only its own method/serial/unique-owner receipt within
the original 300s budget (302s caller fallback). It returns immediately with
`turnOffCompleted`/exit 0 or `turnOffFailed` plus that category/exit 1. Both retain
unknown effect; the receipt supplies no reusable revoke or cleanup authority.
Lost receipt retains `outcomeUnknown`; no poll, reconnect or mutation replay is
added. GUI Disable still uses its admission and fresh Status observations;
it cannot consume the Enable-only window event.

The four CLI-only methods use the same original connection, method/serial/owner
correlation, subscribe-before-send ordering and single receipt consumption.
ReplaceFailed keeps its original 480s budget (482s fallback); CancelInvite,
RecoverInvite and RetryPolicyRenewal keep 120s (122s fallback). Their worker
settlement sends and flushes the receipt before releasing its capture. A settled
completion returns `replaceFailedCompleted`, `cancelInviteCompleted`,
`recoverInviteCompleted` or `policyRenewalCompleted`, respectively, with exit 0.
A known failure returns `lifecycleFailed` with the closed category `failed`,
`refused` or `partial_cleanup`, exit 1. These receipts retain effect `unknown`
and carry no residue paths or cleanup authority. An uncertain worker sends
category `uncertain`; CLI projects it as `outcomeUnknown`, as it does for a lost,
invalid or late receipt. No Status polling, mutation replay, extra budget or
window command/event is added. The GUI command set remains unchanged.

Only HeadscaleServiceError.category at the actual prepare_headscale call mints
preparation provenance. The private child emits one exact preparationFailure
object/newline on exit 1, at most 256 bytes; its decoder raises PreparationFailed,
separate from GenerationUnavailable/capture categories. Its original 19 typed
preparation tokens are
deb-layout-required, issuer-state-uncertain, account-shape,
account-receipt-uncertain, foreign-unit, foreign-custody, publication-uncertain,
address-changed, account-create-uncertain, cleanup-uncertain,
materializer-uncertain, custody-uncertain, inventory, no_address,
multiple_addresses, dial_ip, dial-ip, occupied-port, port-check-uncertain.
Both dial spellings survive separately. Text, stderr, causes and duck-typed
properties never mint provenance. Earlier provisioning effects remain uncertain.
The same closed transport also carries the later lifecycle categories
ready-restart-failed-turn-off, activation-retry-later, activation-prepared-retry
and activation-unverified; these do not assert typed preparation provenance.

Delivery runs once after settlement attempts and before release under the
original operation deadline, preserving a typed reason even if audit later
fails. Receipt checks retain the slot/caller UID/B8 owner; they do not compare
the old snapshot after a legitimate first generation. All pre-effect G/O/CAS
checks remain. The fixed 2s UID read starts only with more than 2s left, with
time checked after blocking reads. One cancellable async flush uses the
remaining budget on the worker's own main context. Lost delivery changes no
effect, journal, ownership or cleanup evidence.
After each Enable, ReplaceFailed, Disable, RetryPolicyRenewal, IssueInvite,
CancelInvite or RecoverInvite operation settles, including terminal refusal,
the helper seals admission and exits through its idle/close path after the
accepted/terminal reply and B16 settlement attempts finish. It waits for any
inflight authorization/effect and StatusV2 worker; unresolved ownership keeps
the slot sealed while the worker is still running. After a lifecycle worker's
effects return, its finally waits for the RLock (main-context holders never join
that worker), records completion or uncertain, releases its captured slot and
seals admission for exit even if complete raises. It never synthesizes success.
This does not preempt a still-blocked effect or audit operation. The well-known
bus name is released before loop exit, so the
next call auto-activates a fresh Type=dbus instance and mount namespace. Calls
racing the old instance receive an existing non-success error, never success.
Status/StatusV2 and device methods otherwise retain the 60-second idle behavior.
In particular, service directories first created during Enable are available
to an immediate Disable in the new namespace. Installed/native confirmation
remains I6a's managed-M1 acceptance, separate from temporary-root evidence.
Why no founder: supports the approved progress/ready/failure screens unchanged.

B9 implementation: `helper_lifecycle.py` supplies connector-independent capture,
the existing helper's single mutation-slot adapter, monotonic budgets and closed
accepted/refused admission before effects. Capture and admission revalidation
share the original approximately 122-second call deadline. Each injected effect revalidates the
bus UID, B8 owner binding and opaque protected-generation token under that slot;
busy calls refuse without queuing. Remaining time includes capture/auth and is
passed to each step, with expired returns suppressed. Executors must enforce the
supplied timeout; these primitives do not preempt blocking code. Auth transport
uncertainty retains the slot until terminal reply/cancellation acknowledgement.
The six outcomes reuse B15's `status_v2.SetupPhase`, excluding UNKNOWN/STALE.
The native 120-second auth and approximately 122-second transport are unchanged.
I5a exports all seven lifecycle/invite methods on the public helper, retaining
I3a's native polkit CheckAuthorization callback with the captured unique bus sender
and exact action. Linux CLI consumers are I5b and bridge consumers are I5c;
RefreshHomeAddress remains absent and B13-deferred. Admission is unchanged;
the terminal/cancellation rules retain the same slot through the entire worker.
The slot is captured before authorization; its callback constructs the worker
under the helper lock, then starts it only after leaving that lock. The slot stays
captured across unlock/start, so another caller still refuses as busy; the worker
retains current-caller revalidation and the nonblocking effect guards.
Worker startup failure clears safely without a second post-acceptance method
reply/error. Genuine
effect uncertainty retains ownership until the worker returns; returned-worker
settlement uncertainty is recorded as uncertain and releases the slot while
sealing admission for process exit. The next activation recovers the durable
journal. The host's actual stuck-busy mechanism remains unproved. See the
[replacement/slot model](../../specs/formal/replacement-closure/README.md).
Except for IssueInvite below, it replies accepted before effects and keeps the original 480/300-second budget
(120 seconds total for I3b CancelInvite/RecoverInvite, including capture/auth).
I4b-2 RetryPolicyRenewal also returns accepted before its worker and retains
120 seconds total, including capture/auth, P1 recovery/publication and B16.
After the audit outcome, it revalidates the live sender UID, B8 owner and
protected G/O, then checks the original deadline before publishing its terminal
result. That validation includes the final blocking read; no later blocking read
can turn an expired operation into success. Lost/late results remain uncertain.
IssueInvite also keeps its original 120-second budget: after B16 settlement
and the final blocking revalidation read, both invite expiry and remaining
budget must still validate immediately before its sole reply (I4a below).
The child revalidates protected activation G/O and release UID before each effect;
B6 rechecks operation inside its existing lock. Native uncertainty seals the slot
until terminal reply/cancellation acknowledgement. I5a public calls select this
existing mount with unchanged admission; Linux CLI consumers are I5b and bridge
consumers are I5c. RefreshHomeAddress remains absent and B13-deferred.

**I5c Linux window consumer (source delivery; I6 native/live NOT RUN):** only
`host.enable`, `host.turnOff` and `host.createInvite` join the existing v1 mapping
and capability sets. They share `request_host` and its I4a/I5b terminal-frame
validator on the existing dedicated system bus, zero inputs, fresh interactive
authorization and original 482/302/122-second native transport budgets. Accepted
Enable/Disable replies project unknown effect, host denial and host/network
cleanup, with no fabricated snapshot or completion. The window retains its bus
caller; subsequent Status/host.stateChanged observations drive presentation.
Enable's separate terminal observer subscribes before the one low-level send,
checks destination/signature/no-FD/raw closed frame, and authenticates against
the original full admission reply. One early candidate may await that callback.
It survives admission clearing active_operation and unrelated Status reads.
Acceptance ends at original send+480s; the original 482s fallback supplies no
grace. Invalid, unsupported, late or wrong-owner/serial frames are ignored.
Caller/document/context retirement, unsubscribe or a new explicit Enable ends
that observer; helper restart never rebinds it.

Subscribed Main documents receive optional native 32-hex operationId on
admission and one closed host.enableFinished event after reply delivery, only
to that original current listener. Preparation failure projects exactly
{code:enablePreparationFailed,category,effect:unknown,recovery:nativeRecovery}.
CLI prints one terminal JSON/exit 1, or
{code:enableCompleted,effect:unknown,recovery:refresh}/exit 0 for a supported
completed worker path. Category-free failed/lost results keep outcomeUnknown.
Older peers keep fallback. Completion never means Ready, Off or cleanup.
GUI retains its translated failure reason while starting a genuinely
post-terminal Status read. Earlier reads/events cannot confirm state; only a
fresh known applicable status can display Off/Ready. Native Status completions
from an earlier terminal epoch return unavailable; newer-epoch waiters start or
join a fresh Status after the older slot completes. Same-epoch coalescing, the
waiter cap, mutation ownership and native timeouts remain unchanged.
Unavailable/off absence evidence stays unconfirmed. Read failure cannot erase a known failure or enable
unsafe actions. Check current status is read-only. Close drops component
observation; reopening rereads without replay.
Post-dispatch loss/timeout/cancellation/late reply stays unknown with one dispatch
and no automatic retry. Polkit denial means permissionDenied and no effect.
Client paste/confirmation reservations and mutation/Status busy rules remain.
IssueInvite gives the web only closed issuance outcome and authoritative expiry,
never invite bytes. Immediately before display the native GTK sheet rechecks
expiry and the original deadline. It shows visible complete text with explicit
Copy only: the invite label is non-selectable and non-focusable. It explains
that Linux-owner approval is also required, shows absolute local expiry and an
m:ss countdown, and provides Close. Expiry drops the text and disables Copy;
expiry/close best-effort clears the clipboard only while it still
holds our copied text. Close does not cancel the invite, and other apps/clipboard
history can retain a copy. No desktop means unavailable/nativePermission before
dispatch. ReplaceFailed/CancelInvite/RecoverInvite/RetryPolicyRenewal remain
CLI-only, RefreshHomeAddress remains B13-deferred, Mac host remains closed,
and StatusV2 consumption remains deferred (the window still reads Status v1).
No schema, helper, polkit, run-as, sandbox, IPC, daemon route, dependency or
packaging-list change is part of I5c. Source and synthetic/native-widget evidence
do not establish genuine helper/polkit, packaged or cross-device acceptance.

I1 supplies `helper.generations` (`helper_generation.HomeGenerationReader`) as the
concrete protected reader. Track A's additive `HomeActivationStore.current()` reads
and pins the current receipt without an expected generation, preserving all existing
parent/leaf custody, size, pending-entry and before/after checks. Only a safely absent
leaf under the valid protected parent returns None; missing/unsafe parents, malformed
receipts and pending evidence refuse. It performs no publication, recovery or repair.
The immutable snapshot compares generation, operation ID and release UID; phase is
display metadata excluded from equality, so own phase progress does not fence the
next step. A different operation in the same generation does fence B9 effects.
System Python loads only stdlib package-owned siblings. Its fixed existing bundled
Python child (`/opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.home_activation`)
runs the zero-argument read entry with empty environment, no stdin and the supplied
remaining timeout. It returns only the closed bounded snapshot, never full receipt,
node identity or secrets. No installer effect runs; root/sandbox ownership is unchanged.
I3a mounts the existing activation backend, B6 and B17 in that fixed child.
Native authorization precedes every call; B16 intents bracket activation/replacement
and each Disable revoke, stop and cleanup boundary. Initial Enable config generation
and B17 inventory/journal generation are protected capture fields distinct from
activation G/O. Replacement remains staged-inert and never activates automatically.
I5d-2 completes initial public Enable composition. Before auth, a missing receipt
admits exactly a UID-bound @2 config, a clean fixed inventory, complete protected
staging, or the proven post-Disable shape in B4/B6. Capture records the pre-state,
expected generation 0 and the existing inventory/provenance token, including
protected Headscale/issuer publication identity. Pending, unsafe, partial/unknown
and source/UID drift refuse; recognized failures retain ReplaceFailed routing.
A retained ready receipt with incomplete Headscale publication also refuses
before auth, preserving Retry turn off after partial cleanup.

Under the one activation-directory flock, the worker repeats capture/CAS and
uses omitted-input `provision` only for clean inventory. Release user/data_home
come exclusively from the authenticated UID's existing B8 bootstrap binding and
unique passwd row, never caller input. It then calls `prepare_headscale`,
`publish_home_units`, `publish_invite_config` and the existing forward backend.
Present staging is verified against provenance/source; existing outputs use
their owners' exact reuse rules. Both publication owners borrow the held open
file description, with literal directory-identity revalidation, rather than
acquiring an independent flock. There is no new privileged owner or lock.
Each boundary checks the same original 480-second deadline; the fixed runner
clips native calls to that remainder, and the existing helper checks after B16
settlement before terminal success. No step renews the budget.

Interruption after a complete staging step permits only exact owner reuse on the
next fresh-auth Enable; unknown/pending/partial residue is never adopted or
deleted. Forward activation retains Ruling 17 and B7 recovery. Ready Enable may perform only the ordinary boot-equivalent start of exact enabled owned units, then joined verification; it never replays activation. Disable, replacement closure, invite and renewal effects and the
closed B5/B6/B17 maps remain unchanged. Temp-root tests exercise all five owners,
including replacement closure, repeated Turn off/Turn on, CAS, refusal and expiry;
native/NSS/materializer/LAN responses are simulated. Installed acceptance remains I6.
Focused verification
uses the existing private-bus
venue: `uv run --frozen --python /usr/bin/python3 pytest scripts/test_helper_lifecycle.py scripts/test_remote_access_helper.py -v --tb=short`.

**B10 — Root-to-connector issuance IPC.**
Extend existing `control.sock` OperationAPI only with `invite-reserve@1`
(expected generation, digest, deadline), `invite-invalidate@1`, `revoke-all@1`.
Bodies remain at most 32768 bytes; connector verifies root SO_PEERCRED and
expected-generation CAS. Root mints/encodes; only digest/deadline cross, never
bearer, key or invite text. The serving connector is the sole pairing owner,
with no second manager/store. Lock order: helper mutation slot → issuer journal
→ IPC; never hold trust-file flock across IPC. Unavailable connector refuses
IssueInvite before mint; lost reserve reply records uncertainty for B11.

Wire bodies are closed JSON objects terminated by one newline. All three use
`method`, `expected_generation` (nonnegative integer) and `deadline` (finite
monotonic authorization deadline, expired refused, at most 120 seconds ahead).
`invite-reserve@1` additionally requires `digest` (exactly 64 lowercase hex) and
`expires_at` (integer Unix UTC seconds, future and at most 300 seconds ahead,
matching the existing pairing TTL). No other keys or duplicate keys are accepted.
Under the serving manager mutation lock, the connector checks ownership and
generation, sends exactly `{"result":"ready"}`, and requires exactly
`{"commit":true}` after root's live-caller/deadline revalidation hook. It rechecks
snapshot/time bounds before the actual anchored CAS transaction. The original
authorization deadline is carried into every mutation and checked again after
acquiring each exclusive trust transaction, before new effects; reserve also
rechecks invite expiry there. Success is
exactly `{"result":"ok","generation":N}`: the committed generation enables the
next invalidate/revoke CAS without assuming a generation increment. Refusals use
only the existing `{"error":category}` vocabulary. Lost final replies remain
uncertain, including when the reserve already committed.

`InviteOperationAPI` is optional and separate from the unchanged slice-2
`OperationAPI`; an implementation without it refuses these operations as
`unavailable`. `invite_control.InviteControl` accepts the existing
serving manager, constructing no owner/store/listener. Revoke-all burns the
ceremony, then uses existing per-device durable-deny/attributed-stream closure,
with generation CAS at every transaction. It also fences and acknowledges
removed owners through the registry's existing removed-device cleanup, outside
the trust flock. Cleanup failure on any owner does not prevent revoking the rest
and yields `unavailable`; a racing writer refuses `changed`, preserving any prior
denies. Expiry refuses `expired` before new writes, while necessary cleanup of
committed work completes; bulk refusal can follow partial durable effects and
never claims rollback. Supervisor `_HelperOperations` now implements the optional
capability by delegating reserve, invalidate and revoke-all to this adapter over
its current serving pinned pairing manager. It resolves that owner per call,
refuses an absent owner and carries the unchanged generation/deadline into the
manager's anchored transactions. The same manager mutation lock still spans the
control ready/commit exchange; wire framing, root SO_PEERCRED and CAS checks are
unchanged. Private helper lifecycle mounting is I3a; I4a privately mounts
zero-input IssueInvite / ai.happyranch.remote-access.issue-invite, with fresh
native auth_admin (never _keep), B8 kernel UID/owner binding and the same helper
mutation slot. Root selects protected current config/CA and connector generation,
composing only HomeInviteIssuer, the existing local minter and serving-owner B10
facade. The core's random undeliverable digest reservation invalidates the old
ceremony before mint, then the actual digest is reserved; each acknowledged
B10 generation supplies the next CAS. The existing invite-user lock remains
nonblocking; issuer journal precedes IPC, without a trust-file flock across IPC.
The original 120-second capture/auth/worker deadline bounds admin RPCs and live
caller revalidation. One attempted issue per minute and consumed delivery remain
issuer-owned. Connector unavailability refuses before mint; lost reserve replies
persist uncertainty in the same issuer journal for B11. No public helper method
is opened by this capability.

Why no founder: fixed operations on existing root-only private IPC.

**B11 — Explicit invite cancel/recover.**
Add zero-input `CancelInvite()` / `ai.happyranch.remote-access.cancel-invite`
and `RecoverInvite()` / `ai.happyranch.remote-access.recover-invite`, each fresh
polkit auth with the existing `auth_admin`, never `_keep`, defaults. Root selects
the protected current operation, never an app-supplied ID. Cancel invalidates
in connector first, then expires the key; report network cleanup separately.
Recover proves no usable key remains and consumes the old ceremony. Only a later
IssueInvite, still limited to one/minute, creates fresh authority.
Why no founder: names already-approved Cancel and authenticated recovery actions.

B11 backend assumptions and inert boundary (`invite_recovery.py`): the freshly
authenticated root caller holds the helper mutation slot throughout, supplies
the protected current connector generation, the remaining original monotonic
authorization/operation deadline (at most 120 seconds ahead), and the live-caller
revalidation hook. The functions accept no operation ID. They select the current
entry using the issuer's existing protected binding/metadata checks and
nonblocking journal lock. Lock order is helper slot → short journal transaction
→ IPC: the journal lock is released before IPC and minter calls; no trust flock
is held across IPC. A durable `uncertain` intent fences new issuance before
dispatch; final publication reacquires the journal and compares the entire
selected record. Lock contention, unsafe/pending state or binding drift refuses
without repair. An absent current entry returns invite `none`, cleanup
`not_needed`; it does not claim recovery or infer that Headscale has no keys.

Both operations acknowledge B10 connector invalidation before key cleanup.
Cancel expires keys through the minter's existing cleanup-and-complete-relist
proof, rather than treating one expire reply as proof. The injected B11 minter
must implement `recover(operation_id, *, deadline)` with the existing exact-true,
no-usable-key and settled-late-create semantics, clipping **every** admin step
to the remaining original deadline. The inert `HeadscaleRecoveryMinter` supplies
that adapter around the existing Headscale cleanup/relist implementation and
injected admin transport, clipping each list/expire timeout and refusing create.
Its caller also holds the existing dedicated invite-user mutation lock within
the original budget. There is no unbounded legacy fallback or new lock owner.
Deadline checks bracket journal, IPC, revalidation, minter and final publication;
a late reply never yields `complete`. OS I/O and dependency time bounds remain
cooperative, not a hard interruption guarantee.

B11 recovery does not delete its journal. Only receipt-bound B6 may retire it
after same-operation DB deletion proof and B16 intent/outcome. Canonical
nonterminal journals additionally require successful Disable revocation, proving
no active ceremony; pending/unknown entries remain incomplete. The no-argument
issuer cleanup contract is unchanged.

Results use closed outcomes `complete`, `refused`, `uncertain`,
`partial_cleanup` and the existing B15 invite/cleanup enums, without changing
StatusV2 bytes. Acknowledged product invalidation can report invite `cancelled`
with cleanup `uncertain` independently. Lost replies after dispatch retain
`uncertain`, never “no change” or rollback. Only acknowledged invalidation plus
exact-true cleanup permits terminal `recovered` journal publication for either
action. This preserves the existing issuer/installer journal vocabulary and
meaning (no usable key remains); Cancel's returned invite phase is separately
`cancelled`. All other cases preserve uncertainty and the existing `not_before`
rate limit. Recovery mints nothing; only a later separately authorized
IssueInvite creates fresh authority. Returned results contain no operation IDs
or invitation/key material.

I3b mounts the two zero-input methods; I5a exposes them with the other five
lifecycle/invite methods on the public helper with unchanged admission. Linux CLI
consumers are I5b and bridge consumers are I5c; RefreshHomeAddress remains absent
and B13-deferred. They share I3a's native fresh CheckAuthorization capture, B8
binding and single helper mutation slot through worker settlement. The original
monotonic deadline is passed unchanged to the fixed child and B11; auth consumes that same 120-second
budget. `helper_invite_backend.HelperInviteBackend` derives B1 config and public
CA from the current protected activation binding, checks their digests/custody,
and obtains the connector generation from existing B10 status. A safely absent
issuer entry needs no connector availability and still returns none/not_needed.
It composes the existing HomeInviteIssuer with no local pairing manager, the
existing invite-user directory lock/store/admin transport, and
HeadscaleRecoveryMinter. All journal transactions release before IPC/minter;
the user lock remains held within the original budget. Every B11 live-caller
hook uses the existing ready/commit framing over the fixed child's pipes to
revalidate the kernel bus UID, B8 owner and protected activation G/O/config/CA
token in the Gio parent. No new connector IPC verb, listener, owner, store,
minter, dependency, bus policy or sandbox change is introduced.

B16 writes invite_cancelled/invite_recovered intent before child effects. A
settled complete or partial_cleanup uses I3a's succeeded outcome mapping;
refused uses refused and lost/late replies use the existing uncertain record.
B9's original deadline is rechecked after B16 settlement, before the terminal
result and StatusV2 observations are published. If the audit append returns
after that deadline, the helper reports uncertain on all three result axes and
records uncertainty without rewriting the settled audit outcome; it publishes
no fresh successful invite/cleanup observation. The slot is released as before.
Timely B11 result tuples pass through unchanged: acknowledged invalidation followed by
recover != True remains partial_cleanup/cancelled/uncertain. No audit vocabulary
or journal meaning changes. StatusV2 projects only existing invite/cleanup
observations, pinned to the authenticated UID and activation G/O; B15 applies
their monotonic age/staleness. Missing observations after restart or binding
drift remain unknown. I5a includes both methods in public METHODS, introspection
and dispatch; Linux CLI wiring is delivered in I5b, with only the existing bridge
commands wired in I5c. I4a mounts IssueInvite as specified below; B14 remains I4b. Evidence retirement was delivered in I3c. Focused standalone backend checks:
`uv run --frozen --python /usr/bin/python3 pytest scripts/test_invite_recovery.py`.

I4a mounted issuance transport (manager T1–T4):

- **T1 timing:** IssueInvite alone sends no pre-worker accepted reply. Its sole
  authenticated D-Bus reply follows worker return, B16 outcome persistence and
  the final blocking live-caller/protected-generation read, then invite-expiry
  validation and the original 120-second remaining-budget check immediately
  before delivery. The helper slot stays held through that reply attempt;
  native approximately 122-second transport
  is unchanged. Enable/ReplaceFailed/Disable/CancelInvite/RecoverInvite keep
  accepted-before-effects. Refusal before effects uses a closed existing error;
  lost, late, malformed or expired post-dispatch results use the private
  IssueInvite `uncertain` error (explicitly admitted by its error sanitizer).
  Lost reply delivery never licenses a second reply, retry delivery, fetch,
  polling method or alternate channel. B11 settles retained uncertainty.
- **T2 D-Bus success:** existing `(s)` compact JSON contains exactly
  `{"result":"complete","invite":"<text>","expires_at":<int>}`. Text and
  integer Unix expiry are exactly the issuer's IssuedInvite values.
- **T3 child frame:** terminal success is the same exact three-key JSON object;
  nonterminal `{"result":"ready"}` / `{"commit":true}` is unchanged. Failure
  frames contain only the closed nonsecret result (`refused` or `uncertain`).
  `helper_invite.read_frame` gains keyword `limit=4096`; existing callers retain
  their default. Issuance permits at most 272 frames on the same deadline
  (before/after checks for the existing minter cap of 128 usable keys); B11
  retains its 16-frame bound. Only the IssueInvite child exchange supplies `limit=6144` for
  the pinned <=5500-byte invite plus envelope/newline. Oversize refuses, never
  truncates. Parent rejects duplicate/extra/missing keys, anything except one
  `hrinvite2:` prefix with `[A-Za-z0-9_-]` remainder, total text length >5500,
  noninteger (including Boolean) expiry or expiry outside `(now, now+300]`.
  Validation and the original deadline are checked again after B16 settlement
  and the final blocking revalidation read, immediately before the secret reply.
  If either expires during that read, deliver only uncertain: retain the consumed
  issuer journal and settled B16 outcome, append B16 uncertainty, and never
  roll back, re-mint or deliver the invite text.
- **T4 secrecy:** text exists only in memory and that one authenticated reply;
  never in StatusV2, retained B16 audit, issuer journal, logs, exception/error
  text or stderr. Child stderr remains DEVNULL. Error mapping uses categories,
  never frame representations. StatusV2 retains only existing invite phase and
  nonsecret expiry fields; connector receives only digest/deadline via B10.
  I5a exposes IssueInvite in public METHODS/introspection/dispatch; Linux CLI/bridge
  consumers are wired in I5b/I5c.

**B12 — Bounded recorded-node cleanup.**
At approval record control-plane StableID → Headscale node ID using B2-style
authoritative observation. Delete only recorded-owned nodes on revoke,
rejected/abandoned redemption or Turn off, never hostname or age alone and never
on a cleanup timer. At most 16 deletions per action, re-observe each before and
verify after. Report remainder pending until the next explicit action. Immediate
product denial/stream closure never waits for network cleanup success.
Why no founder: bounded removal of approved abandoned/removed-node authority.

**B13 — Live address refresh.**
`RefreshHomeAddress()` uses `home-refresh-txn@1`: close admission → invalidate
current invite through B10 → same-CA leaf renewal when SAN changes or due →
publish config/DERP map/control_tls/issuer binding/receipt as one digest-bound
set under a new generation → restart owned units → fresh B2 observation →
reopen. Settle any uncertain issuer journal with B11 first; refresh refuses
while unsettled and never rewrites that journal. Preserve rate-limit state,
home/control-plane/connector/node identities and consumed marker. Rollback
never restores an old invite; fixed DNS-SAN/trust rules remain.
Why no founder: the approved address-change flow and identities are unchanged.

**B14 — Fixed-policy renewal timer.**
`happyranch-remote-access-policy-renew.service` is root, oneshot, with no
inputs/environment/URL; its `.timer` has `OnUnitActiveSec=1800s`, `OnBootSec=60s`,
`Persistent=false`. Under the installation lock it re-stamps only bytes matching
the current shipped package route artifact, retaining the 3600-second envelope.
Failure lets existing expiry close access. `RetryPolicyRenewal()` remains the
fresh-auth retry. Turn off stops, disables and unlinks these owned units;
their files remain package payload. Artifact mismatch refuses until B18 repair
rewraps the installed policy; another timer tick cannot repair a package upgrade.
Why no founder: pins the names/cadence of the already R2-approved mechanism.

The inert B14 renderer/decision module is `runtime/remote_access/policy_renew_units.py`;
`scripts/test_policy_renew_units.py` fixes the exact UTF-8 unit bytes and renewal
boundaries. The zero-argument renderers have no environment, credentials, URL,
path or command-argument selectors and perform no installation or activation.
The root oneshot uses the existing `happyranch` group to preserve root:hr policy
ownership with empty capability sets. It preserves compatible helper/owned-unit
hardening, including strict filesystem protection, hidden homes, private tmp/dev,
kernel/control-group protections and bounded resources. Its only writable
exceptions are `/etc/happyranch` (atomic policy replacement) and
`/var/lib/happyranch-remote-access` (installation lock); no network is required.
The 30-second start timeout bounds a stuck future writer; failure never extends
the installed expiry. Both units are part of `happyranch-managed.target`; only
the timer has an Install section, for that target.

**Delivered in I4b-1:** `ManagedInstaller.renew_policy` holds the existing
activation-directory lock across protected reads, the decision and publication.
Automatic mode uses `renewal_candidate` unchanged: identical shipped artifact,
active valid structure, current 3600-second envelope and no backwards time.
Fresh-auth mode is used by private Retry and forward Enable; it applies those
same checks but permits an expired installed envelope. Both increment revision,
re-stamp issuance and retain all other envelope fields. Artifact equality uses
`policy.canonical_json` SHA-256, excluding envelope timestamps and formatting.
No key is renewed. Refusal never extends expiry.

For renewal, the existing `/etc/happyranch/install-transaction.json` carries a
closed `policy-renewal@1` record, bounded to 64 KiB, with old/new policy and provision
SHA-256 values and base64 new bytes. The protected no-follow atomic writer
fsyncs the journal before either destination, replaces `policy.json` first and
`provision.json` second, preserving ownership/modes, then unlinks and fsyncs the
journal last. Only the policy digest changes in the provision record. Recovery
under the same lock rolls forward only when BOTH destinations match their old
or new digest; malformed/unsafe journals or any other digest refuse without
effects and retain the journal. A crash-left `.policy.json.pending` or
`.provision.json.pending` is recoverable only with that durable valid journal,
exact new bytes/digest, and the destination's regular single-link owner/mode.
Both destinations and every pending entry are checked before effects. The same
owner discards the validated pending inode (unlink + parent fsync), then
republishes from the journal through the protected writer in policy/provision
order. Unknown names, substituted bytes, unsafe custody and any pending entry
without a durable journal refuse without effects; an unjournaled first intent
is never adopted. Expired durable new bytes may roll forward;
consumers still enforce expiry. All ordinary admission, inventory, replacement,
provision, uninstall and other install-journal readers refuse this format.
Activation receipt format, `MEMBER_PATHS`, B5, transaction `EFFECTS` and B6 stay
unchanged.

`RENEW_EXECUTABLE` is the fixed zero-option
`/opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew`. Its package
launcher uses the fixed bundled Python with `-I` and an empty environment,
automatic mode only, closed nonsecret errors and nonzero refusal exits. Both
units ship at `/usr/lib/systemd/system/` with exact renderer bytes and no
package enable/start action. The existing helper-service payload is the precedent.

The connector observes only a changed no-follow policy stat signature in its
existing supervised lifecycle-lock pass, before readiness. It reuses `load_policy`
validation and swaps its one consumer only for a current envelope with greater
revision and nondecreasing issuance. Existing readiness and per-request contexts
read that consumer. A valid swap or refused update while cached policy remains
current causes no restart or stream closure. Refusal retains the cache. On cached
expiry, the existing readiness gate, shutdown/stream closure, return 1 and
systemd `Restart=on-failure` remain unchanged. Startup still reads the file fresh.

For a prepared retry (an existing prepared receipt, or the exact prepared
publication before its first receipt), fresh-auth Enable first validates all
retained inputs under the activation lock, inspecting expiry only for renewal.
It then re-stages the identical shipped policy through the existing protected
renewal journal/writer and creates a fresh strict coordinator before activation
preflight. A refused or ineffective renewal cannot start a unit. Matching old
activation intents are refused before this path; recovery and ready receipts
never enter it. The normal first-start renewal and strict check still follow.

**Delivered in I4b-2 (THR-327 restoration ordering):** Fresh-auth forward Enable
from staged-inert or post-Disable inventory defers ONLY policy expiry during
capture, inert publication and the forward owner's preflight. Exact shipped
artifact/digest, provenance, owner/mode, token source and custody checks remain.
After `headscale_start_verify` intent is durable, that effect calls the existing
`_renew_policy_locked(fresh_auth=True)` under the SAME held activation-directory
flock, through the live-config owner. That owner accepts only P1's exact
policy-digest update to its captured provision record; all other captured inputs
retain their original custody signatures. It enforces `require_current` on the
renewed policy, and the forward owner restores strict checks BEFORE this effect's
`daemon_reload`, Headscale enable and Headscale start. Renewal refusal or a
noncurrent renewed policy enables/starts zero units. Earlier inert publication
retains its existing reloads and read-only manager inspection. Every subsequent
check, admission and runtime gate is strict. The later `managed_services`
re-stamp remains, with timer enable before verifier socket/target enable and
managed target start. Ready Enable may start exact enabled owned units as at boot, then verify joined; it never replays activation. I3a fresh-auth recovery
still only verifies and
publishes (or rolls over to prepared); it never renews policy, links the timer or
replays any other effect. A later forward Enable runs the existing effect path.
The existing managed-fragment check also validates both renewal units before
any mutating manager call: exact `/usr/lib/systemd/system/<unit>` FragmentPath,
empty DropInPaths, Names containing only that unit, loaded state without a
pending daemon-reload, and timer Unit equal to the renewal service. Protected
no-follow reads require root-owned, single-link 0644 package fragments with exact
renderer bytes. Overrides, aliases, stale loads or fragment drift refuse Enable.
Ready Enable accepts an active/enabled timer either waiting, or running with
that verified renewal oneshot in activating/start and Result=success. Immediate
first firing and an already in-flight renewal may wait for Enable's held flock;
Enable never waits for that renewal while holding the flock. It retains the
original deadline and capture/G/O, current-policy and custody revalidation.
Failed/inactive timers, foreign units and incorrect service linkage still
refuse; no activation replay, mint or receipt/transaction update is allowed.

Disable's existing stop step stops the timer and renewal service, disables the
timer through its manager, and confirms both units' quiescence and the literal
timer-link absence before B6. It repeats that proof before cleanup. A remaining
link or unconfirmed unit returns `partial_cleanup` naming it, without B6.
Renewal residue never short-circuits the existing owned remote-unit safety stop:
`stop_and_confirm` and the independently receipt-bound Headscale stop are still
attempted, including after failed revoke or a refused remote inventory proof.
Every ownership gate remains required; a refusal is accumulated as residue.
Under the same lock, Disable also recovers an existing `policy-renewal@1`
install journal using P1; refusal retains it and returns `partial_cleanup`
naming `/etc/happyranch/install-transaction.json`. It never creates a renewal
when there is no journal. Any renewal residue or stop/quiescence uncertainty
prevents B6. Failed revoke alone does not: after all remote and renewal
stop/quiescence proofs, timer-link absence and journal recovery succeed, B6
still runs, deleting the Headscale DB live members while retaining connector
trust/sidecar state and evidence requiring revoke success. Disable reports
`partial_cleanup` naming that un-revoked trust state. Complete requires revoke
success AND all proofs AND B6 success AND no residue
(I3a R4/R7b, I3c C3/C5). `policy.json` and
`provision.json` survive Turn off.

Private zero-input `RetryPolicyRenewal` uses the existing B8/B9 capture and
mutation slot, the exact noncached auth_admin action, B16 policy_renew
intent/outcome, the original 120-second budget and the post-B16 final validation
described in B9. It calls `renew_policy(fresh_auth=True)` with its private
live-capture callback, which rechecks G/O and the parent UID/B8 under P1's same
flock before publication. The automatic entry supplies no callback and keeps
its original behavior. The existing
ready/commit pipe framing performs live-caller validation around child work.
It does not restart the connector; I4b-1 pickup observes the updated envelope.

StatusV2 uses only its existing `policy` state/age fields: valid current policy
with an active enabled waiting timer and a successful renewal service is
`current`; active renewal is `renewing`; expired policy is `expired`; a failed
service, inactive/disabled timer or artifact mismatch is `renewal_failed`;
an install journal is `uncertain`. Unavailable/unsafe evidence stays `unknown`.
These read-only observations grant no activation/admission authority.
Only the timer receives its manager-owned Install link; receipt membership and
B5 remain unchanged. Ready admission does not require this timer link: policy
expiry remains the gate. `_unit_wants_names` and the B17 replacement veto remain
unchanged; Turn off must remove the timer link before replacement. Unit files remain dpkg
payload after Turn off. I5a opens the helper public lifecycle/invite gate;
Linux CLI consumers are delivered in I5b; bridge Enable/Disable/IssueInvite consumers are delivered in I5c (source only; I6 pending).

**B18 dependency:** a package upgrade with a different shipped route artifact
makes renewal refuse until B18 repair/upgrade rewraps it. I4b-1 adds no upgrade
repair path, activation authority or live installation.

**B15 — Compatible versioned status.**
Keep `Status()` v1 byte-compatible. Add zero-input, read-only `StatusV2()`, no
polkit action: closed JSON at most 16384 bytes, with separate setup phase,
runtime availability, invite phase/deadline, address state, policy state and
cleanup state, each supporting unknown/stale and observation age. With connector
stopped report runtime unavailable, never infer Off. Bridge/CLI consumers switch
together in the consuming PR; secret/public-output restrictions apply.

The pure model is `runtime/remote_access/status_v2.py`. I1 exports StatusV2 on the
existing helper interface; Linux bridge/CLI consumers read its named setup detail
after a v1 failed/pending/paused response. The method retains
Status's bus UID and B8 owner validation, accepts no arguments/FDs and never prompts
polkit. It sends the canonical model JSON directly as the D-Bus string (no double
JSON encoding). The existing destination policy already admits it; no policy changes.
One read worker runs without the mutation slot or main context; concurrent StatusV2
calls refuse busy. An independent 1.9-second watchdog reserves response overhead within
the original 2-second status budget, suppresses late replies and keeps a stalled worker
sealed until it settles. It never queues new workers or waits on a mutation-held lock.
A present prepared/activating/joined activation receipt maps to setup
`setup_unverified`; prepared staging without an activation receipt maps to
`prepared_no_receipt`. A ready receipt requires a
fresh exact ordinary inventory, active required units, serving connector and
current/renewing policy before display as ready/available. Otherwise it names
runtime_unavailable, setup_unverified, partial_cleanup or renewal_failed.
Failed_uncertain and published preparation without an activation receipt have
their own named states. Missing/unreadable receipt alone never proves Off;
a receipt bound to a different UID refuses. A fresh successful connector snapshot
supplies runtime available only when ready; otherwise unavailable. Unreachable connector
supplies runtime unavailable independently of a recognized setup receipt.
THR-327 closed-host reads additionally reuse the unchanged read-only Enable
capture when connector status is unavailable: only a clean, staged-inert or
post-Disable capture with no activation/replacement/invite binding and a verified
inventory proves Off. Owner, custody, digests, residue and quiescence checks
remain mandatory. This display proof never feeds device/lifecycle mutations,
claims a slot, repairs files or prompts authorization. Failed/unrecognized proof
refuses `host-state-unrecognized`, including unavailable bounded proof; existing
owner refusals retain their exact D-Bus categories. The Linux CLI/bridge projects
these to `hostStateUnrecognized` with a plain installation-verification message
(English and Simplified Chinese in the GUI). CLI stdout keeps its closed JSON
envelope; the fixed English diagnostic goes to stderr. Main and CLI still call
Status, projecting proven Off as known/offline with empty devices and the existing
Turn on control. StatusV2 projects setup off/runtime unavailable. Its 1.9-second
watchdog/2-second budget and the existing Status transport timeout stay unchanged.
I3b/I4a project invite/cleanup
observations, and I4b-2 reads policy/timer state through the same fixed bundled
child into the existing policy dimension; address remains unknown. Observation ages measure time
since the reads with a monotonic clock and pass through the existing stale normalization.
No device data, IDs or receipt fields cross StatusV2. Status v1 serialization is unchanged.
`StatusV2` requires the six dimensions
`setup`, `runtime`, `invite`, `address`, `policy`, `cleanup`; wire JSON additionally
has integer `version: 2` and may include the optional `firewall` observation
specified in the THR-327 F2 amendment below. Each dimension has exactly `state`
and `age_seconds`; invite additionally has `deadline_utc_seconds`. All vocabularies include
`unknown` and `stale`, plus only:

- setup: `ready`, `off`, `in_progress`, `failed`, `uncertain`, `partial_cleanup`,
  `prepared_no_receipt`, `failed_uncertain`, `runtime_unavailable`,
  `setup_unverified`, `renewal_failed`;
- runtime: `available`, `unavailable` (never `off`);
- invite: `none`, `minting`, `encoding`, `pairing`, `issued`, `pending_approval`,
  `consumed`, `cancelled`, `recovered`, `expired`, `refused`, `uncertain`;
- address: `current`, `changed`, `refreshing`, `identity_changed`, `failed`, `uncertain`;
- policy: `current`, `renewing`, `expired`, `renewal_failed`, `uncertain`;
- cleanup: `not_needed`, `in_progress`, `complete`, `pending`, `partial_cleanup`,
  `failed`, `uncertain`.

B15 supplied no freshness threshold: use a conservative fixed **30 seconds**.
Supplied observation ages are monotonic-clock-derived integer seconds in
`0..2147483647`; missing observations are `unknown` with null age. Older than
30 seconds becomes `stale` with the actual bounded age; fresh states have ages
at most 30, stale ages are greater than 30, and only unknown has null age.
Out-of-range ages refuse rather than clamp. Connector builder input has closed
`running`, `stopped`, `unreachable`, `unknown`, `stale` states with the same age
rules; it is not another wire dimension. Fresh stopped/unreachable overrides
runtime to unavailable without changing setup. Missing/old connector evidence
makes runtime unknown/stale; running requires a separate runtime observation,
using the older dependency age. No observation alone fabricates setup Off.

Invite deadlines are integer Unix UTC seconds in `0..9223372036854775807`,
required only for `issued`/`pending_approval`, null otherwise. They are the
supplied effective invite expiry, never a monotonic authorization deadline.
At or past expiry the pure builder reports `expired` with null deadline;
unknown/stale observations carry no countdown. This display does not extend
lifetime, consume authority, recover or perform any effects. Callers still
validate the existing effective five-minute invite lifetime before supplying it.
The canonical encoder uses sorted compact UTF-8 JSON and one final newline;
the inclusive **16384-byte** cap refuses oversize rather than truncating. The
strict decoder enforces the cap before parsing, exact keys/types/vocabularies,
no duplicate keys at any depth, bounded integers and no nonfinite numbers.
Ordinary JSON whitespace and an optional final newline are accepted. No strings
beyond fixed keys/enums, arrays, labels, IDs, digests, keys, bearers or invite text
are admitted. This model adds no status mutation, polkit action or v1 change.
Why no founder: supplies the existing screens without changing user-visible UX.

**B16 — Protected retained audit.**
`/var/lib/happyranch-remote-access/home-audit.jsonl`, root:root 0600, is
append-only with closed events: activation phases, invite issued/cancelled/
recovered/expired, device approved/rejected/revoked, refresh, policy renew/fail,
Turn off component outcomes and uncertain. Records at most 1024 bytes, file at
most 1 MiB; rotate once to `.1` at cap, retaining one generation. Record intent
before effect and uncertainty after lost settlement. Audit survives Turn off
and replacement and is never automatically removed; no raw secrets or node-key/
digest-prefix output, no DB or audit-task-ID reinterpretation.
The stdlib writer is `runtime/remote_access/home_audit.py`. I1 wires the existing
ApprovePending/RejectPending/RevokeSelected helper effects: after fresh authorization
and revalidation, persist device_approved/device_rejected/device_revoked intent before
connector commit, then succeeded on acknowledged completion. Before-commit refusal
records refused; lost settlement after commit admission records uncertain. If outcome
persistence fails after an effect, attempt uncertain and return the fixed unavailable
error; no successful audit acknowledgement is inferred. Intent persistence failure
prevents the effect. IDs are local random audit correlation labels, never the target
StableID, record ID, activation operation ID or a digest. Read calls emit no audits.
I3a privately brackets activation/replacement with activation intents and each
Disable revoke/stop/B6 step with turn_off component intents. An acknowledged
step records succeeded, a proven pre-dispatch refusal records refused, and lost
effect/audit settlement records uncertain. A failed revoke record never suppresses
the separately audited stop attempt; incomplete residue remains a closed partial
operation result. I3b also brackets private invite cancel/recover with the existing
invite events and lifecycle outcome mapping; I4a brackets issuance with
invite_issued intent and succeeded/refused/uncertain settlement before delivery.
I4b-2 brackets private RetryPolicyRenewal with `policy_renew` intent and
succeeded/refused/uncertain settlement. Audit or final blocking validation past
the original B9 deadline appends uncertainty and suppresses terminal success;
the policy/provision journal keeps P1's recovery semantics.
If expiry or the original B9 deadline lapses during the final blocking read,
the succeeded outcome and consumed issuer journal remain; append uncertain
before the sole uncertain error, without delivering invite text.
B16 adds no audit-specific public method. I5a exposes the seven lifecycle/invite
methods with unchanged admission; Linux CLI consumers are I5b and bridge consumers
are I5c. RefreshHomeAddress remains absent and B13-deferred.
The closed event spellings are
`activation`, `invite_issued`, `invite_cancelled`, `invite_recovered`,
`invite_expired`, `device_approved`, `device_rejected`, `device_revoked`, `refresh`,
`policy_renew`, `policy_fail`, `turn_off`, `uncertain`. Activation uses the existing
`prepared`, `activating`, `joined`, `ready`, `failed_uncertain` phases. Turn off
components are `sidecar`, `connector`, `headscale`, `units`, `live_state`,
`credentials`, `accounts`; outcomes are `succeeded`, `failed`, `refused`, plus
Turn off's `absent`, `retained`, `partial_cleanup`. Lost settlement instead uses
an explicit `uncertain` event/stage/outcome referencing the original intent.
I3c minter retirement additionally permits a closed `counts` object only for
`turn_off`/`uncertain`, `live_state`, and fixed subject-home-minter-node/invite
labels. Its exact keys are reservations, markers, fences, pending_fences; each
is a real integer in 0..600,000 and their sum is at most 600,000. No operation
IDs/hashes or record contents are emitted. Existing records omit counts and
retain their bytes. Other records contain only `version=1`, `event`, `stage` (`intent`, `outcome`,
`uncertain`), `intent_id`, `operation_id`, `timestamp_ns`, nullable `subject_id`,
`phase`, `component`, `outcome`. IDs are local audit labels, at most 320 ASCII
bytes each: `intent-`, `op-`, `subject-` respectively, followed by lowercase
alphanumeric segments of 1–12 characters separated by `-`. Key/token words,
hex segments of 8+ characters and long unbroken key/base64 strings refuse;
node identities, digest prefixes, arbitrary labels and diagnostics are not ID
inputs. The complete JSON line including newline must fit 1024 bytes or refuse
before I/O. The existing parent must be owned by the expected UID/GID and not
group/world writable; all literal parents are opened without following links.
B6 reuses the actual held activation-directory open file description, after
literal-parent identity/custody validation, so B16 cannot deadlock by acquiring
a separate flock on its own locked parent. It neither unlocks nor takes ownership
of that borrowed descriptor. Cooperating appends/rotation lock that directory through file fsync and directory
fsync before every successful append acknowledgement, including a reopened file
left by an interrupted create, and after rename. An unsafe existing file or `.1`
refuses without repair; only cap rotation replaces `.1`, never creating `.2` or
cleaning other entries.
The API persists an intent before returning its reference; callers own effect
ordering and settlement, and a reference confers no authorization/replay proof.
An I/O failure may follow a partial append and must be treated as uncertain.
Before a subsequent append to the same file, a non-newline tail is preserved
byte-for-byte and terminated with one newline in the same write as the next
record. The separator counts toward the file cap, not the record cap; a torn
tail alone never triggers rotation. A tail-read failure refuses without writing.
After an I/O failure the file may contain at most one newline-terminated
unparseable fragment per failed append; readers skip lines that fail JSON
parsing. Every successfully acknowledged record is a complete JSON line.
Why no founder: the approved nonsecret retained audit, with bounded storage.

**B17 — First replacement intent for recognized failed installation.**
I2b delivers `ManagedInstaller.begin_replacement`; I3a privately mounts it from
the fresh-auth ReplaceFailed owner, which supplies an explicit expected
release UID and exact observed generation. The installer captures the complete fixed
inventory, binds the derived validated release owner, then publishes/fsyncs the new
`replacement-intent@2` at `install-transaction.json` before handing off to the existing
per-intent recovery owner. The helper captures that inventory/journal
generation before authentication and revalidates it before dispatch. I5a exposes
public ReplaceFailed; Linux operator CLI selection is delivered in I5b. I5c wires only
the existing bridge commands.
An existing exact v1 journal already has its first intent: the entry validates its
expected generation/release UID and resumes that journal without converting it.
This entry independently revalidates every remaining original/quarantined config's
schema and cross-config bindings, including when v1 provenance digests match.
The admitted historical classes are the existing validated
`replacement_inventory`/`known-failed-or-partial` shapes: (1) recognized complete
legacy/manual or modern configs/units with an observed failed owned unit;
(2) validated incomplete fixed config/unit sets, including interrupted generated
writer prefixes and partial legacy provenance; (3) exact journal-bearing v1
replacement phases/intents already recognized by that consumer. This includes
the founder host's recognized failed manual shape; a merely stopped intact
manual installation remains migration-required. Legacy unrecorded configs keep
their safe 0600/0640/0644 admission; recorded provisioning keeps exact 0640.
All present artifacts, cross-config bindings, provenance/digests, fixed unit
bytes, owners/modes/no-follow/single-link and loaded-fragment/drop-in checks
must validate. Existing 64 KiB config/unit and 64-entry shallow-directory bounds
remain; opaque state gets metadata checks only, never execution or restoration.

Missing digests are recorded as `absent`, never synthesized. The closed canonical
v2 record has exactly `format`, `version=2`, `layout`, `phase`, `digests`, `release`
and `inventory`. Its six digest slots use SHA-256 or literal `absent`; the complete
fixed-key inventory captures no-follow metadata, file digests and exact link targets.
Opaque state has metadata only. Directory identity/custody is captured without
mutable directory times. This is not a caller-supplied path manifest.
Owner selection follows spec §1: a validated root-owned release record, otherwise
the expected UID's passwd home plus `.local/share/happyranch`; the fixed source token
passes the existing same-FD UID/custody reader before intent publication or stop.
The first writer revalidates inventory, owner, pending inode and literal parents
before no-replace publication. An interrupted first publication without a final
journal remains a conflict, never unjournaled recovery authority.
For unit-only remnants whose config directory is absent, creating the fixed journal
parent additionally requires existing full quiescence; it never stops services before
the first intent or creates an arbitrary parent. An unproved stop retains those remnants.

New first-admission authority is additive: v1 meanings/recovery stay unchanged,
and all other paths retain their unjournaled mutation refusal. V2 uses the existing
quarantine/removal/staging/closure owner, preserving each originally absent config
and each legacy config's admitted mode. Removed state and unit entries must still
match their inventoried metadata. A v2-only `replacement-stage-release` intent can
publish the derived release record when an interrupted writer never reached it;
its original digest remains `absent` until that fresh publication intent. Successful
closure produces ordinary modern staged-inert inventory, never activation.
Recognized source renderings refer only to `/opt/happyranch/venv/bin/python` and
`/opt/happyranch/bin/happyranch-tsnet-sidecar`; any legacy objects removed there
must be inventoried and match the listed shape exactly, never a recursive prefix
purge. Clean/healthy/manual, modern staged-inert/recorded or unknown installations
are not replacement candidates without the positive failed/partial proof above.
Preserve the PR1632 exception: validated own-journal pending discard may precede
a later stop failure; other replacement file changes require proved quiescence.
Why no founder: implements approved recognized-failure recovery without terminal UX.

**B18 — Explicit token repair and known-version upgrade.**
No new method: fresh-auth `Enable()` repairs only a positively classified
`repair_required` live profile; already-ready Enable permits only ordinary start and joined verification, never activation replay.
Close admission/quiesce → same recorded release UID/home → same-FD token read
→ transactional exact-file/digest update → loaded-unit/READY revalidation →
reopen. Preserve installation IDs, pins, epochs and revocations. Package postinst
never repairs; its only action is a configure-time system-manager daemon-reload
when systemd is running (failure tolerated), so the strict renewal-unit
`NeedDaemonReload=no` check can pass after package replacement. It never touches
user managers or protected state. After upgrade classify only a listed known
prior profile version as repair_required for one Enable with devices retained.
Unknown/torn versions remain closed with a fixed category, never automatic migration or trust reset.
Why no founder: the approved authenticated repair/keep-devices upgrade behavior.

### Unwired home join-key adapter (THR-327 slice 3b-2c)

`runtime.remote_access.home_minter.HeadscaleJoinKeyMinter` satisfies the existing
`JoinKeyMinter` using injected `HeadscaleAdminOperations`, an invite user **NAME**,
a UTC clock and `OperationRecordStore`. It does no I/O, builds no argv and imports
no subprocess/socket/gRPC client or environment access. TASK-16780 owns the
concrete protected local admin-socket transport and root invocation, delivered
in the inert 3b-2g-1 prefix above by home_admin and the fixed packaged Go runner;
user/ACL creation and helper activation wiring remain later work. Nothing
here installs, invokes or proves real Headscale behavior or native acceptance.

Tagged v0.25.1 contracts relied on (verified read-only; not a CLI/JSON adapter):

| RPC / projection | Relied-on fields and behavior | Tagged upstream source |
| --- | --- | --- |
| `CreatePreAuthKey` | Request `user` (name), `reusable`, `ephemeral`, `expiration`; transport sends no `acl_tags`. Response `pre_auth_key`. | [service RPCs](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/headscale.proto#L43-L66), [request/response](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto#L19-L27), [GetUserByName before creation](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/grpcv1.go#L142-L176) |
| `PreAuthKey` | String `user`, `id`, `key`; Boolean `reusable`, `ephemeral`, `used`; timestamp `expiration`, `created_at`; repeated string `acl_tags`. Transport returns every field as the typed record, with UTC datetimes and tuple tags; absent/ambiguous data must not be invented. | [complete proto record](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto#L7-L17) |
| Key creation | 24 random bytes encoded as 48 lowercase hexadecimal characters; newly persisted key is unused. | [database creation](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/db/preauth_keys.go#L37-L86), [key generation](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/db/preauth_keys.go#L165-L172) |
| `ListPreAuthKeys` | Request `user`; response repeated `pre_auth_keys`. Complete user inventory includes used/expired keys. No partial/filtered/truncated snapshot may report success. | [proto](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto#L36-L38), [server](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/grpcv1.go#L201-L224), [database list](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/db/preauth_keys.go#L96-L111) |
| `ExpirePreAuthKey` | Request `user`, raw `key`; empty response. Server checks ownership and sets expiration to its current time. RPC success alone is insufficient; independently re-list. | [proto](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto#L29-L34), [server ownership check](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/grpcv1.go#L179-L198), [database update](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/db/preauth_keys.go#L155-L163) |

**Hard transport contract:** raw keys and request/response bodies stay in memory
and protected request fields; never argv, env, URLs, logs or exceptions.
Headscale v0.25.1 CLI **`preauthkeys expire` is NOT an acceptable transport**:
[`expire KEY` copies raw `args[0]` into the request](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/preauthkeys.go#L196-L223).
Every operation must honor its supplied timeout. `AdminRefused` is allowed only
when the transport proves create made no state change; a timeout, lost reply or
unknown server error cannot satisfy that guarantee. The adapter discards raw
exceptions, leaves handlers, then raises fixed `refused`/`uncertain` categories
`from None`, retaining neither cause nor context.

A successful list must also be ordered after prior create effects, including a
timed-out create. If an earlier request could still commit a usable key later,
the transport must refuse list until settlement or the requested key deadline
has passed. This is an additional transport obligation, **not a claim that the
tagged RPC guarantees cancellation/settlement**. An empty snapshot while create
can still commit cannot prove recovery. Concrete transport acceptance must
establish this ordering; the synchronous fake only models settled effects.

The invite user MUST be dedicated to Mac invite keys. The helper must serialize
mint/recover with all other writers for that user. Before each create the adapter
lists, expires every unused unexpired key and re-lists to prove cleanup. It also
expires an encountered used reusable key, which can still enroll again. Recovery
does the same for **all** keys of that user without a creation-window filter:
lost create replies or unavailable local records cannot hide a key. Used
single-use keys and keys with expiration <= now are unusable. A later recovery
can therefore succeed by time after a failed expire, but every call still needs
a complete clean re-list. Any transport, parse or clock doubt returns False.

Mint rejects a nonpositive, >300-second, naive or non-UTC deadline before any
admin call. It rounds down to whole seconds and rechecks after cleanup and store
persistence. The store atomically/durably reserves each operation's creation
window before its sole create; reservations survive refusal/recovery/restart and
cannot be overwritten or reused. Store failure refuses without create. Create
always requests non-reusable, non-ephemeral, untagged keys. A malformed result,
used key, user mismatch, expired result or server expiration beyond the requested
deadline is uncertain; success returns the actual server expiration.

UNPINNED -> restrictive choice:

- Admin calls get a five-second timeout. Every row in each complete inventory
  is validated, including inert expired keys and used single-use keys; all IDs
  must be unique and nonempty. Only keys that can still enroll count toward the
  128-key operational/cleanup cap, including used reusable and unused unexpired
  keys. Inert history consumes no expire RPCs. Both pre-cleanup and independent
  post-expire inventories must pass; malformed/unknown/ambiguous rows refuse
  even when their apparent expiration or used flag suggests they are inert.
- A separate whole-inventory ceiling is 600,000 rows. Sizing assumption: a
  one-year lifecycle at the real issuer's maximum one mint/minute/home needs
  at most `366 * 24 * 60 = 527,040` rows, leaving 72,960 rows of margin (about
  50 days). This is a bounded lifecycle, not indefinite retention. Mint accepts
  at most 599,999 existing rows so even a lost create reply leaves a complete
  recoverable inventory of at most 600,000. At capacity, mint returns typed
  `MintRefused("refused")` before reservation/create; recovery still processes
  600,000 rows. Beyond the ceiling recovery returns False, never proof from a
  truncated list; waiting for expiration cannot clear that whole-list refusal.
  Validation is linear with a bounded ID set and at most 128 retained cleanup
  candidates. This row bound does not certify transport byte/RSS or wall-time
  limits; the concrete transport must bound decoding and honor its timeout,
  refusing incomplete results. No filtered-success read is authorized.
- Named operator remediation at that lifecycle ceiling: **retire and recreate
  the dedicated invite user** in a separately authorized maintenance operation.
  Retire its enrolled nodes first (disruptive: affected clients must re-enroll),
  then use Headscale v0.25.1 `DeleteUser` and `CreateUser` for the same dedicated
  name and re-establish the existing user/ACL bindings before resuming issuance.
  [`DeleteUser`/`CreateUser` exist](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/headscale.proto#L16-L41);
  [`DestroyUser` refuses users with nodes, deletes their keys, then deletes the user](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/db/users.go#L44-L77).
  Node retirement is supported by the existing
  [`DeleteNode` RPC](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/grpcv1.go#L349-L379).
  There is [no delete-preauthkey RPC in this tagged service](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/headscale.proto#L43-L68).
  Expire alone never removes history. This adapter performs no retention/reset,
  adds no admin operation, and grants no live maintenance authority. Preserve
  durable operation reservations and the issuer journal across maintenance;
  reconcile any uncertain operation explicitly, never retry its create.
- Zero UTC offset and complete typed timestamps/Booleans/strings are required;
  no JSON/protojson or string/int coercion, ACL tags or key-format fallback.
  R1: omitted reusable/ephemeral/used decode as false and omitted acl_tags as
  empty, exactly following the tagged proto3 implicit-presence schema, not an
  application default. R2: id/key/user must be present and nonempty and pass
  minter validation; expiration/created_at must be present with valid seconds
  (-62135596800 through 253402300799), nanos (0 through 999999999) and UTC.
  R3: malformed varints/lengths, wrong known-field wire types, truncation,
  repeated singular fields and unknown field numbers in PreAuthKey or response
  messages refuse. See [tagged schema](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto)
  and [protobuf field presence](https://protobuf.dev/programming-guides/field_presence/).
- Expire all unused invite-user keys, including older operations, rather than
  relying on timestamps to attribute an uncertain creation.
- Durable insert-only operation reservations prevent a second create for an ID;
  this module selects no persistence format, socket path, user, ACL or transport.
- Refuse successful list snapshots while an earlier create could still commit
  a usable key; no timeout/cancellation-to-settlement inference is allowed.

Standalone synthetic verification: `uv run --frozen pytest
scripts/test_home_minter.py scripts/test_invite_issuer.py -v`. Fake operations
model the tagged fields/expiration semantics; they do not establish protected
socket custody, real server execution, Linux/Mac integration or installed access.

### THR-327 closed-host restoration diagnostics

Staged-inert and post-Disable absence proof permits only existing bounded H1
`health.json` / `.health-N` diagnostic metadata in the preserved sidecar runtime
directory, with the sidecar inactive and quiescent. No other live socket/state
is admitted, including verifier `control.sock`. These diagnostics never establish
readiness: HomeObserver still requires the live `observe.sock` SO_PEERCRED peer,
current unit PID/UID, activation binding and independent Headscale identity.
Status and StatusV2 only read; valid aged retained staging reports Off and an
explicit, authenticated Turn on performs the renewal above. No retained-file
manipulation or other host action is required for that validated shape.

`hostStateUnrecognized` retains its code and adds optional closed category
`residue|owner|provenance|custody|inventory|quiescence|timeout|status_unavailable|invite_rate_limited|unknown|unverified|not_applicable`. The child
emits only `{refusal: category}` on Enable, Disable, ReplaceFailed, IssueInvite,
CancelInvite or RecoverInvite capture refusal; HomeLifecycleClient
validates the closed frame; helper D-Bus uses `host_state_unrecognized_<category>`.
The shared native request projection, CLI JSON, Main protocol/relay and Connection
parser preserve it to English and Simplified Chinese copy. Legacy owner-binding
refusals map to owner; attributed generic capture failures and missing, invalid
or legacy unknown categories display as unverified. Raw transport uncertainty
still retains unknown effect.
No path, file content, UID, exception message or secret crosses this boundary.


### THR-327 ready restart and Disable retry (TASK-19228)

A ready receipt is retained after failed stop. It is not evidence of live runtime
availability. Disable alone accepts activating/deactivating as stoppable states
of exact owned loaded units; every fragment, load-root, drop-in and byte check
remains unchanged. Ordinary inventory keeps its closed active/inactive/failed set.
Every Turn off invokes the serving revoke-all anew before stop and B6. A previous
reply, audit, stopped unit or absent IPC is never revoke proof. Revoke-all with
the current generation also succeeds on an already-revoked device set.

Fresh-auth Turn on again with a ready receipt validates ordinary ready/joined
admission and exact inventory, starts the exact enabled Headscale unit and
managed target if down, then runs the existing joined verification. This is the
same service admission as boot, with no receipt change, mint, credential or
activation replay. Missing enablement, failed start or failed joined verification
refuses as `ready-restart-failed-turn-off` (recovery: Turn off).

Failed/unavailable serving revoke still runs stop and B6, retains trust and
persisted sidecar state and reports partial_cleanup naming them. This does not
authorize ordinary restart of a partly removed publication. The existing failed-
revoke partial state has no established fresh-Enable/replacement/purge exit;
that sub-case remains a decision, not an offline revocation exception.


### THR-327 renewal unit upgrade (TASK-19228)

TASK-19401: the fixed Linux managed-identity connector changes only
`StartLimitIntervalSec=300` to `StartLimitIntervalSec=0`. It keeps
`Restart=on-failure`, `RestartSec=1`, `TimeoutStartSec=120`, the 30-second
watchdog, all hardening and all readiness gates. A missing desktop daemon
therefore keeps retrying startup instead of exhausting the start limit.
No listener or request admission opens until all gates pass. The sidecar's
`After`/`BindsTo` connector and the connector's `Upholds` sidecar are unchanged.
With the daemon restored and every other gate healthy, allow up to 250 seconds
for the existing two 120-second startup budgets, restart delay and readiness
polling; normally the connector notices within its five-second poll and the
sidecar follows. This is a source budget requiring installed-systemd acceptance.
Persistent unrelated failures also keep retrying; this changes no failure into
readiness. Sidecar rate limiting remains unchanged.

An already-READY d05afaac/98a87688 host retains its exact previous connector
unit across package upgrade. With **no button**, it retains the old retry
behaviour, including a terminal failure already reached. The new package's
configure-only postinst reloads the system manager after replacing packaged
renewal fragments, including upgrades from packages with no postinst; it does
not republish the connector unit. After a successful reload, authenticated
**Turn on again** uses the existing ready receipt/ordinary admission and
activation lock to atomically republish only those exact previous bytes, then daemon-reloads
before the existing failed-unit reset/start/joined checks. Capture and Status
only recognize the previous rendering; they never publish or repair. Receipt,
transaction, G/O, token source, enabled-link, custody and deadline checks remain;
no mint or activation replay occurs. Unknown bytes refuse.

The renewal-unit loaded-property comparison remains strict. A refusal now
uses the same syslog warning path as the neighboring activation failure and
journals `remote-access-renewal-unit-mismatch unit=service|timer property=<fixed
property name>` without the observed value. TASK-19410 proved package replacement
left `NeedDaemonReload=yes` before this postinst existed. The helper never
reloads before inventory or admits stale loaded properties; if the package's
best-effort reload fails or another property mismatches, Turn on again still
refuses `ready-restart-failed-turn-off`. Installed upgrade acceptance of this
postinst remains the manager's VM lane. Transitional-unit admission,
partial-cleanup recovery and richer waiting-for-daemon Status are unchanged.

The fixed renewal oneshot uses User=root, Group=root and
SupplementaryGroups=happyranch, retaining empty capability sets and existing
hardening. Pending publication skips fchown only when fstat already reports the
exact required UID:GID; all final custody checks still apply. Fresh-auth Enable
under the activation lock accepts only the exact previous v6/v7 renewal service
bytes (Group=happyranch) or current bytes at the fixed packaged path. A previous
unit is atomically republished with pinned-parent, no-pending, exact owner/mode,
loaded-fragment, drop-in, G/O and deadline checks, followed by daemon-reload.
Unknown bytes still refuse. No receipt/config/credential migration occurs.
Ready Enable also ensures the exact renewal timer enablement link exists; it
never returns ready after refusing that check. Package and renderer bytes match.


### THR-327 no-mint recovery and live-key retry (TASK-19228)

B7 retains existing recovery from a valid present mint reservation, including
lost journal effects. Only definite ENOENT from the no-follow reservation reader
can be absent; malformed or unsafe records still refuse. Without a mint intent
AND without a reservation, fresh complete empty key and node lists permit the
existing prepared-generation rollover, with all source/member/CAS checks and no
effect replay. Any key or node in that no-mint case refuses. A still-live unused
key refuses as `activation-retry-later`, retaining the full original key window.
A returned prepared rollover reports `activation-prepared-retry`, never Enable
completed; the next fresh-auth Enable uses the new generation.


THR-327 helper worker contention (TASK-19228): effect_guard waits at most one
second for a transient main-context reader, then revalidates the unchanged
operation deadline and capture before any effect. Admission and main-context
status remain nonblocking. A lifecycle step that never executes is refused,
never credited as successful revoke/stop/cleanup. No budget is renewed.


The ready restart / retry-later / prepared-retry categories are carried through
the existing Enable terminal channel and its closed native/schema/web parsers,
with English and Simplified Chinese recovery hints. TASK-19228 also delivered
generic settled Enable refusal naming and the checked manager-plan projection,
as documented under Settled Enable and unattributed Status refusals below.
Status reads live setup detail
without any durable marker. Status v1 retains its five-field shape and phase
vocabulary, mapping named failures to failed/pending/paused with ready=false.
After Status, native consumers make one read-only StatusV2 call and admit only
fresh (age at most 30s), closed setup/invitation detail; unavailable/older peers
keep the original v1 result. Detail never promotes an inconsistent v1 setup to
ready. After helper restart, the existing protected HomeInviteIssuer.observe
reader supplies the current journal phase/deadline through the fixed child,
with UID/G/O matching and configuration checks before/after the read. It creates
no minter, serving pairing owner, lock, commit or repair. A same-helper settled
invite observation retains its original uncertainty/staleness rules. No IDs or
private issuer fields enter StatusV2. The native bridge exposes journal phases
minting/encoding/pairing/refused/uncertain/recovered, with EN/zh-CN explanations;
expired invites have no countdown. Missing/unsafe evidence stays unconfirmed.
An acknowledged per-device RevokeSelected confirms denial/stream closure but
performs no network-membership cleanup; that action's networkCleanup is
notApplicable, never an invented confirmed network removal. Failed/lost
acknowledgements retain their existing unknown-effect handling. Both web views
ship English and Simplified Chinese reasons/recovery actions. Named unhealthy
detail suppresses device/selection claims (null, never fabricated empty access).
Ordinary inventory still refuses transitional ActiveStates; Status names
setup_unverified, without granting ownership. Both Host views offer the existing
Turn off attempt in that state, retaining native confirmation, exact ownership,
same-invocation serving revoke and quiescence requirements. CLI Disable terminal
delivery is documented under B9. The #1876 channel originally wrapped typed
preparation-stage errors and Enable completion only. Settled later activation/
recovery failures now use the closed activation-unverified extension below;
this does not attribute them to preparation.

Ready Turn on again validates the exact renewal service/timer bytes and loaded
identities, restores and verifies the owned timer enablement link before target
startup, then explicitly starts an inactive timer and requires active/enabled
with waiting substate, or running with the verified renewal oneshot in
activating/start and Result=success. The latter includes immediate first firing
and an owned renewal waiting for the same flock; Enable returns without waiting
under that flock. Each effect retains the original deadline and captured
UID/G/O, ready admission and custody checks. Failed verification returns
ready-restart-failed-turn-off; it never mints or replays activation, nor rewrites
the ready receipt or transaction.


THR-327 foreground Linux client observation (TASK-19228): after the initial
runtimeReachable frame, the existing foreground owner performs authenticated
health reads at one-second intervals (no overlapping reads or transport retries).
A later error terminates through the existing named CLI error and bounded local
session cleanup. Only the existing revocation_denied/revocation_stream_closed
responses map to revoked; generic identity denial remains accessUnavailable,
never proof of confirmed revocation. Transport loss remains homeUnavailable and
malformed HTTP remains protocol in CLI JSON; neither collapses to unavailable
or proves revocation. This adds no network/auth/credential change,
no keyring deletion, and no UI-loaded/Connected claim. The native GUI's existing
health action already distinguishes revokedConfirmed from accessDenied.

### Owned Headscale listeners during activation retry (THR-327 R6)

Inert preparation still samples the four exclusive IPv4 binds. At the activation
coordinator's preflight only, occupied-port may instead pass fresh attribution
of all four fixed listeners to the exact owned active Headscale unit. Existing
receipt/member/config/source checks precede this observation; exact fragment
bytes, loaded path, names, no drop-ins, service UID and MainPID are checked on
both sides. Bounded kernel socket tables and that PID's fd set must attribute
every matching IPv4 listener to it; wildcard/foreign/missing or ambiguous
listeners refuse. A matching IPv6 port conservatively refuses. Process UID and
start time are rechecked against PID reuse. No running-unit-only exemption,
port reservation, stop, replay, new unit rendering or upgrade format is added.
The ordinary headscale_start_verify effect and its fresh admin verification
still follow preflight.

### Settled Enable and unattributed Status refusals (THR-327 G)

The original #1876 preparation diagnostic applies only to the actual typed
preparation call. Later activation/recovery exceptions therefore had no such
reason; the private child emitted no frame, and the parent settled a categoryless
failed/uncertain result. The legacy `preparationFailure` field and
`enablePreparationFailed` native code now also carry the one closed
`activation-unverified` category for other settled Enable failures. This names
an unverified activation, never preparation provenance or absence of effects.
No message, cause, path, credential or duck-typed category is reflected. Typed
reasons still win; late/lost/invalid transport still cannot claim no effect.
The destination-addressed terminal receipt keeps its original caller, auth,
serial/owner and deadline checks. Both Host presentations carry bilingual
recovery copy and retain unknown effect until a fresh observation.

Status failures with no attributable closed stage use `unverified`, including
normalizing the legacy `unknown` category at the native display boundary. The
old wire value remains accepted for compatibility. Known stage categories
remain unchanged. This is display only, with no new ownership or cleanup proof.
The Linux handbook's checked status projection records success, refusal and
named unavailable evidence before atomic file replacement; it does not assert
that every CLI response is a success object or treat file-write success as READY.


### Attributed lifecycle refusals

Enable, Turn off, replace-failed and invite capture refusals retain the same closed
host-state category through the fixed child, D-Bus, native CLI and both Host
views. Recognized inapplicable actions use `not_applicable` with `effect=none`
before admission. Turn off with no activation receipt first proves recognized
absence or preparation through strict Enable capture. Replace-failed accepts
only the existing `known-failed-or-partial` installer candidate and its exact
inventory/journal generation; a home activation receipt never qualifies. It is
not home activation recovery. Unsafe inventory retains its attributed refusal.
No replacement, cleanup, mint, replay or authentication gate is widened.

With no activation receipt, IssueInvite/CancelInvite/RecoverInvite likewise
require strict Enable capture before returning not_applicable. Device actions
whose connector snapshot is unavailable reuse the existing strict absence proof
only to refuse: proven Off returns not_applicable, otherwise unverified. Its
display snapshot never admits a device mutation. CLI revoke with fresh known
Off status refuses not_applicable before selection or mutation dispatch.

When IssueInvite's existing issuer rate fence positively refuses before journal
write, invalidation or mint, its private terminal result is rate_limited. Only
that exact result projects invite_rate_limited with effect none, through CLI
and both bilingual Host views. It means a recent invite must be used or the
caller must wait; a generic refused or unavailable result does not prove that
cause and retains its existing effect semantics.

The helper's short Status read expiry is projected as hostStateUnrecognized /
timeout, never invite or policy expiry. Helper unavailable and D-Bus NoReply
for Status use status_unavailable. Both are no-effect reads and retain their
closed category through CLI and both bilingual Host views. Other action and
transport-loss effect semantics remain unchanged.

A positively refused IssueInvite with no invite-state effect does not overwrite
an earlier same-generation issued observation with unknown. Status keeps the
original observation age and expiry and its existing UID/generation guards;
fresh helpers still use the existing protected journal reader. Dispatched
uncertainty remains uncertain. No privileged read or invite delivery is added.

A prepared activation receipt which has not reached ready is displayed as
`setup_unverified`, rather than an unqualified pending state. Prepared staging
without an activation receipt remains `prepared_no_receipt`. Attributed capture
failures default to `unverified`, with fixed custody, publication/residue and
inventory stages preserved. Transport loss and effects already dispatched
retain unknown effect; the no-change copy applies only to pre-admission
`not_applicable` refusals. B4 and pre-Headscale R7 remain terminal limits.


### Local lifecycle failure diagnostics

The helper journals closed action/step/stage tokens and exception classes for
Enable, Turn off and replace-failed refusals, including caught safety-stop
failures. Enable also retains its shipped source-relative location and closed
stage. User-facing results remain category-only. Automatic policy renewal
journals the same redacted failure dimensions; a suppressed flock
`BlockingIOError(EAGAIN)` is named `lock-busy`, and still exits unsuccessfully.
No failure or skipped work is marked successful.

B6 records the innermost shipped source-relative file:line (including suppressed
transaction error context), exception class and failing directory ordinal with
`step=cleanup-directory` and a
fixed internal reason (or a closed native errno reason). The unchanged order is
1 Headscale configuration, 2 Headscale persistent state, 3 Headscale runtime,
4 home CA staging, 5 sidecar runtime, and, only after successful serving revoke,
6 Taildrop files, 7 connector state and 8 sidecar state. Ordinal zero is a
non-directory step. The diagnostic counter is never an admission input.
User/group retirement order, custody/revocation checks, unit sandbox, expiry
and timer schedule are unchanged. The bounded unattended
lock wait and EROFS removal behavior are specified above. No arbitrary exception message,
path, file contents, identity, credential or invite is journaled. Journal
failure preserves the original refusal and retained evidence.

### THR-327 F2: read-only Linux host firewall advisory

Firewall evidence never vetoes Enable. No firewall inspection runs in Enable
preflight or before setup effects. After the lifecycle outcome is settled, the
existing fixed child may supply optional display advice within the original
deadline: only when more than six seconds remain, allowing at most two seconds
for advice and preserving the remaining terminal/auth budget. Read errors
produce `unverified`; insufficient remaining budget omits the observation.
Neither advice nor its failure changes the settled outcome, authorization,
audit, receipts, publication, setup READY or runtime availability.

The fixed C-locale `/usr/sbin/ufw status verbose` observation has three named
advisory categories:

- `likely_blocked`: recognized active UFW, default deny incoming, neither an
  IPv4 user allow for 8443/tcp nor one for 3478/udp, and stock IPv4 before/after
  templates. This is a probability: live INPUT allows from initialization or
  other owners can exist even when both files are stock.
- `clear`: UFW is inactive, or recognized user allows cover both ports with
  stock templates. This says nothing about end-to-end connectivity.
- `unverified`: any active customization, unreadable/unknown evidence, partial
  allow, default allow/reject without both user allows, command failure,
  rate limit, unknown profile/interface, malformed output or timeout.

LAN-scoped allows and port lists/ranges count as possible allows; IPv6-only
allows do not cover the IPv4 home dial address. Fixed rule-file reads are
read-only, no-follow, regular-file-only and limited to 64 KiB each. Any change,
including harmless customizations, symlinks, missing files or oversized and
non-ASCII content, downgrades active observations to `unverified`. Stock hashes
never establish live rules. No iptables/nft execution or new capability is used.
Missing UFW supplies no observation; firewalld/plain nftables are not inferred.

StatusV2's optional `firewall` observation uses `state` and `age_seconds`, with
`likely_blocked|unverified|clear` plus internal `unknown|stale` freshness states.
Unknown is omitted. Status advice reserves 300 ms of the original two-second
response deadline, leaving 200 ms before the unchanged 1.9-second watchdog. It
is skipped when the resulting advice budget is at most 100 ms. Advice timeout
or error omits the field and still returns the collected V2 observations; it
never extends the original deadline. Frames without the field and original
Status v1 retain compatibility. Fresh observations (at most 30 seconds old)
project to the existing optional native host `firewallWarning`. Enable's settled terminal and
successful native/CLI result may carry the same optional closed advisory.
Absent advice never substitutes for setup/effect evidence. `clear` is carried
without warning text; English and Simplified Chinese host views and CLI say
that the **home computer's firewall probably blocks** the home ports for
`likely_blocked`, and tell the user to allow **8443/tcp and 3478/udp from the
home LAN only**, then check Status. Unverified advice is conditional on failed
connections. No rule contents, addresses, paths or stderr cross the boundary.

The remote client snapshot does not carry host firewall Status. Replacing the
client's generic home-network-unavailable explanation therefore needs a new
protocol field and remains follow-up; this change adds none to that snapshot.

Inspection never adds/removes/modifies firewall rules. Automatic rule management
remains Founder-reserved. Service user, sandbox, capabilities, ReadWritePaths,
R2 custody, home-only trust and authentication/authorization remain unchanged.
Installed-package/UFW and cross-device qualification remain separate from the
standalone temporary-file/native-simulation venue.
