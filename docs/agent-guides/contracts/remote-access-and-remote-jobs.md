# Remote access and remote jobs contracts

> Extracted from the repository-root `AGENTS.md` on 2026-10-30 and maintained
> here. The THR-327 shipped Linux lifecycle and bounded M1 receipt below extend
> the original phase-specific implementation records; those earlier slice limits
> do not describe the whole current managed lane. Historical evidence remains
> historical, and every unexecuted acceptance obligation stays explicit.

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

- **Linux systemd supervision (THR-327 seq187/195).** The foreground `cmd/happyranch-tsnet-sidecar` loads validated JSON, waits for bounded `tsnet.Server.Up` and the existing connector probe/listener gates, then emits one `READY=1`. It sends `WATCHDOG=1` every `WATCHDOG_USEC/2` for the selected `WATCHDOG_PID`; SIGTERM/SIGINT emits `STOPPING=1` and stops the sidecar. Startup or serving failure exits nonzero. The Python connector waits for initial readiness under `TimeoutStartSec=120`; after READY, readiness or listener/thread loss closes admission and exits nonzero, with no in-process re-arm or second READY. Healthy persisted-revocation rotation remains inside the lifecycle boundary. Both rendered services use `Type=notify`, `Restart=on-failure`, `RestartSec=1`, `WatchdogSec=30`, and `StartLimitIntervalSec=300`/`StartLimitBurst=5`. In system mode, connector crashes after READY get the full five starts in 300 seconds; when the connector fails before READY, the `Upholds`/`BindsTo` pair adds start-limit checks, so restarts can be refused after as few as three real starts. The unit stays failed until the same explicit `reset-failed`/start recovery; a refused unit may report `Result=signal`, and its journal’s `Start request repeated too quickly` line identifies the refusal. The prior 30/60/120s backoff (3/600s) belonged to the removed Go supervisor and is superseded; systemd is the only supervisor. The sidecar has `After=` and `BindsTo=happyranch-connector.service`; the system-mode connector adds `Upholds=happyranch-tsnet-sidecar.service` (systemd >= 249) so connector recovery brings the sidecar back. Both have `PartOf=happyranch-managed.target`, whose `Wants=` names both; intentionally stopping the target stops the connector and its Upholds relationship. Existing hardening stays; only the sidecar adds `AF_NETLINK` for tsnet Linux interface monitoring. The bounded managed installer and passing initial systemd M1 lifecycle are described below. Consumed-credential restart admission is implemented (THR-327 seq187/227): after the existing no-symlink and owner-directory gates, a regular, current-user-owned `0600` marker with the exact SHA-256 digest of `RoleIdentity + NUL + ControlURL` admits restart with an empty auth key only when credential `Lstat` returns ENOENT. Any present credential entry (including a dangling symlink), invalid marker or other lookup error refuses without reading the credential. Marker ENOENT retains the one-time validate/read/redeem/durable-commit/delete enrollment path. The production tsnet adapter refuses enrollment and restart before tsnet construction if `TS_AUTHKEY`, `TS_AUTH_KEY`, `TSNET_FORCE_LOGIN` or `TS_DEBUGSYSPOLICY_AUTH_KEY` is set, including empty values; its error names the variable and asks that it be unset without exposing its value. Only the explicitly passed credential may supply an auth key; restart reuses the configured persistent node state. Restart never re-runs consumption or rewrites the marker and still requires engine start, the existing receipt gates (including durable state and expected-peer visibility), connector probe and listener success. Marker reads use nonblocking/no-follow opens and verify the opened regular file. Real systemd/tailnet restart and reboot acceptance remain **UNEXECUTED** and outside M1.
- **Trust-state publication consistency (THR-327 seq187/227).** `AtomicFileTrustStateStore.load()` holds `LOCK_SH` on the existing owner-only stable `<state>.lock` inode; `transaction()` and `save()` hold `LOCK_EX` across snapshot and anchor publication. Lock acquisition opens without following symlinks or blocking on special files and requires a regular, single-link, current-owner, owner-only inode distinct from the snapshot and configured anchor. Unsafe locks refuse with `StateStoreError`; existing lock metadata is never repaired, and pair-file metadata is never changed by reading. A reader cannot sample a writer's in-progress pair. A load on the same store by the thread already holding the exclusive lock reuses that ownership; other threads/processes still acquire their own shared lock. Readers use the existing bounded acquire and fail closed with `StateStoreError` on contention, never a default/empty state. Every stable-pair validation stays: partial, corrupt, torn, replayed, generation/digest-mismatched, symlinked and loose-permission state still refuses, including when a lock alias targets a loose pair file. A crash between publications leaves a genuinely torn pair that still refuses after the kernel releases the lock. Pairing and request semantics stay unchanged; there is no client-request retry. Post-READY readiness/listener loss still stops admission and exits nonzero. Both loss exits emit the failing gate names and provider/listener/thread booleans to the journal and STATUS, without exception text, paths, state values or credentials.

- **Managed Linux installation (THR-327 seq187 item 2, seq195/207/227).** `python -m runtime.remote_access.installer install --enrollment-key-file /absolute/input.key` is a root operator action on a prepared host. Binaries, Python environment and validated connector/sidecar configuration must already be provisioned at `/opt/happyranch/` and `/etc/happyranch/`; the installer creates their directory layout but does not populate payload/configuration. The explicit transient enrollment handoff is described below. It creates or validates the dedicated non-root `happyranch:happyranch` account with `/nonexistent` home and a nologin shell. Payload/configuration directories are root-owned `0755`; service state/log/runtime directories belong to that account and are created with the shared renderer's systemd modes (`0755`, except sidecar state `0700`). The installer creates `/var/lib/happyranch-connector` at `0755` and accepts existing `0755` or the owner-only `0700` set by real trust-state saves. The installer's own validation and writes never chmod existing directories or widen their permissions. The credstore mode exception is described below; managed-directory mode requirements remain exact, and group/other-writable directories are refused except for the exact unmanaged `/var/log` ancestor described below. Before any account-creation or systemctl command, install and uninstall inspect all existing destinations for safe modes, types, no-follow traversal and validated ownership. If the service account is absent, an existing service state/log/runtime directory is refused because its required owner cannot be validated; a fresh install can create the account only when those service destinations are absent. The three root-owned `0644` units are exactly the shared renderer's connector, sidecar and `happyranch-managed.target` output. Literal descriptor-relative traversal and exclusive no-follow writes refuse linked/special files, loose/wrong ownership or modes, and differing pre-existing units. Matching units can be reused; there is no transactional upgrade, journal, rollback or interrupted-install recovery.

  The only unmanaged-ancestor exception is the exact path `var/log` relative to the installation root (`/var/log` on a live host): uid `0`, gid resolved from the local `syslog` group, and no world write; group write is permitted for that ancestor only, including stock Ubuntu 24.04 `root:syslog 0775`. The existing root:root, non-group/other-writable case remains valid. No other gid (including `adm`) is added, and a missing `syslog` group grants no exception. Members of `syslog` are trusted to manipulate log-directory entries, including moving or replacing service log directories; this is not a concurrent-entry-mutation fence. Every managed directory, including `/var/log/happyranch-connector` and `/var/log/happyranch-tsnet-sidecar`, and every other ancestor retain their existing ownership/mode rules. Literal descriptor-relative no-follow traversal remains mandatory. Ubuntu's [Noble rsyslog configuration](https://raw.githubusercontent.com/rsyslog/rsyslog-pkg-ubuntu/master/rsyslog/noble/v8-stable/debian/00rsyslog.conf) defines this distribution-owned ancestor.

  The unchanged connector unit declares `StateDirectory=happyranch-connector` without `StateDirectoryMode=`. TASK-15125's review cites systemd v259's [0755 default](https://github.com/systemd/systemd/blob/v259/src/core/execute.c#L655) and [mode application](https://github.com/systemd/systemd/blob/v259/src/core/exec-invoke.c#L3184): systemd's `StateDirectory=` handling may reset an existing `0700` directory to `0755` at service start. The shipped renderer retains that handling; the installer itself does not chmod existing directories. Restart-time mode-reset behavior remains **UNEXECUTED** and outside M1; the passing initial install/request/uninstall run does not test that transition. Fake-command mode-retention evidence covers only installer-owned operations.

  Install reloads systemd, enables and starts the target, then checks each written unit's on-disk SHA-256, exact `FragmentPath`, empty `DropInPaths`, `NeedDaemonReload=no` and `ActiveState=active`. Blocking startup of the rendered `Type=notify` services supplies the systemd READY boundary; the grouping target is not a notify service. There is no exact pre-start loaded-property comparison. A start/drift failure requests target stop and reports any stop failure, without restoring files or disabling the boot enablement; operators must inspect partial state. `check-drift` repeats this observation without changing services. `uninstall` stops the target, then explicitly waits for the sidecar and connector service stops before disabling the target, removing matching safe unit files and reloading systemd. A target-only stop can finish while its `PartOf=` services are still stopping; removing their unit files and reloading early discards the runtime-directory cleanup configuration. The blocking service stops keep that configuration loaded through shutdown, so systemd removes both `/run/happyranch-connector` and `/run/happyranch-tsnet-sidecar` before uninstall proceeds. Stop failure aborts before unit removal, reload or state purge. Payload, configuration, service logs, account and persistent state are retained. Only `uninstall --purge` removes the two service state directories; linked/special/foreign-owned state is refused. The bounded enrollment lifecycle and passing hosted M1 receipt are described below. Temp-root/fake-command evidence and static unit verification remain distinct from that live initial READY/uninstall result and do not establish tailnet, restart or recovery acceptance.

  The root operator supplies `install --enrollment-key-file /absolute/input.key`; the input must be a root:root, single-link regular `0600` file, nonempty and at most 64 KiB. Literal no-follow parent traversal and nonblocking/no-follow file opens refuse symlinks and special files. Every install, including reinstall, requires explicit input; this command does not infer a keyless reinstall from persisted state. Use ordinary service restart for an enrolled host; installing a new key over a consumed marker still refuses at the existing marker-plus-input gate. The customer cell issues the single-use, short-expiry key; the installer does not provision it. The original operator input remains the operator's responsibility to remove promptly.

  The installer first creates and fsyncs the root-owned link `/etc/credstore/happyranch-tsnet-enrollment` pointing only to `/etc/happyranch/enrollment.key`, then exclusively stages that fixed source as root:root `0600` and fsyncs it and its directory. Cleanup unlinks and fsyncs the source before removing the link, so interrupted staging/cleanup preserves lookup for any surviving source. `/etc/credstore` is created root-owned `0700`; existing safe `0700` or `0755` is retained. The credential name is reserved to this installation: no inherited or other credstore credential may use it. The sidecar renders `LoadCredential=enrollment-key:happyranch-tsnet-enrollment` and `--credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key`, overriding the JSON credential path before validation. Only the path enters argv; the key never enters argv or environment. Credential-name lookup, unlike an absolute missing source, is nonfatal when absent in both [systemd 255](https://www.freedesktop.org/software/systemd/man/255/systemd.exec.html#LoadCredential=) and this host's systemd 259. After source/link deletion, systemd may also remove the empty per-unit credential directory while still exporting its path in `CREDENTIALS_DIRECTORY` ([v255 directory removal](https://github.com/systemd/systemd/blob/v255/src/core/exec-credential.c#L927-L932), [environment export](https://github.com/systemd/systemd/blob/v255/src/core/exec-invoke.c#L1881-L1891)). The sidecar handles that absent-directory state without creating a directory or an empty credential file. A remaining source/link yields a present copy, which the existing consumed-marker gate refuses.

  Only an exact `CredentialFile == $CREDENTIALS_DIRECTORY/enrollment-key`, with an absolute, clean, non-root credentials directory, selects systemd input semantics. The copy must be regular, single-link, read-only `0400` (or `0440` for systemd's ACL read mask), owned by root or the service user; path no-symlink checks and opened-inode rechecks remain. Only for this exact managed path, ENOENT on the per-unit credential directory itself also counts as absent input; every ancestor above it must still exist as a directory without symlinks. Foreground missing parents, deeper missing ancestors, symlinks and other lookup errors refuse. A valid consumed marker then permits empty-key restart without rewriting the marker; without a marker, absent input still refuses enrollment. Enrollment still requires the engine receipt gates and durable marker commit, but never unlinks or fsyncs the read-only copy's directory. systemd retains that service-private copy until teardown. Foreground inputs retain current-user-owned `0600` validation and deletion plus directory fsync after commit. Marker validation, marker-plus-present-credential refusal, empty-key restart, ambient tsnet-key refusal and all readiness gates remain.

  After both notify services are READY and the existing drift checks succeed, the installer unlinks the fixed source and credstore link and fsyncs both directories. Any failure after staging also attempts this cleanup, including reload/enable/start/drift and stop failures; start/drift failure still requests target stop. Cleanup refusal is an explicit installation error naming the two fixed paths and requiring operator revocation/inspection. Stale enrollment residue refuses reinstall before commands; uninstall removes safe source/link residue before stopping services, with or without `--purge`. A crash or unsafe replaced entry may leave residue requiring explicit operator care; there is no journal or racing-root-writer fence. Retry requires a freshly issued key. The temporary source is the bounded single-use enrollment handoff, not persistent configuration storage. M1 below exercised the initial systemd credential handoff; consumed-state restart and reboot acceptance remain **UNEXECUTED** and outside M1.

- **Managed remote access N0/N1/N2 amendment (THR-097 seq360/seq374/seq379/seq392).** The managed transport value is `embedded`: packaged macOS tsnet/userspace WireGuard is designed to connect to the Linux sidecar in `app/linux/tsnet-sidecar/`, which proxies raw TCP only to the N2 Python connector's explicit `managed` listener on literal `127.0.0.1`. `runtime/remote_access/managed_provider.py` reuses the existing pairing and locked gateway pipeline; the connector alone owns application identity, credentials, epoch, allow-list/route policy and revocation and injects the daemon bearer only on the final `127.0.0.1:8765` hop. The N1 core fail-closes credential redemption/listener startup and removes external admission before active connections and engine state; persisted N2 revocation likewise stops the listener first, then seals/closes active flows, then performs downstream runtime cleanup, retaining failed cleanup handles for idempotent retry. Supervisor provider start, READY publication, persisted-revocation reconciliation, and shutdown share one re-entrant lifecycle boundary: either provider start completes first and stop follows it, or stop/revocation completes first and late start is refused/idempotent; partial start is cleaned without retained provider/registry state. Shutdown observably stops the listener, closes registered active flows, and drops cached registry/pairing runtime state exactly once, so repeated shutdown leaves no listener, active flow, or runtime residue. N2 adds no address selector or fallback and exposes readiness only after the existing connector gates pass and the loopback listener binds. The checksum-pinned N1 dependency/license artifacts are unchanged. The original N1/N2 slices defined the core/ingress boundary; the later THR-327 managed Linux command, composite supervision, installer and enrollment handoff are now shipped. M1 below passed the bounded initial systemd lifecycle. Tailnet transport (M2), full-daemon acceptance, restart/reboot recovery and deployment remain **UNEXECUTED** and outside M1; broader provisioning/distribution and N3–N6 obligations remain open. Neither endpoint requires system Tailscale; wildcard, LAN/plaintext, public-Tailscale, and public-DERP fallbacks remain forbidden. TASK-6298/TASK-6289 remains unchanged Supported-DIY, and Unit 4B-2 remains independent.
- **Manual managed M1 (THR-327 seq187): PASSED.** [Hosted run 42978877282](https://github.com/t-benze/happyranch/actions/runs/42978877282) completed successfully on 2026-11-11 at main `455c9c5b689fa25f625e2df8cae1b551c2a1de86` (short `455c9c5b`), through install, both READY services, paired HTTP 200, product uninstall/purge and every residue assertion. This receipt belongs to that source head; later heads are not claimed rerun. `.github/workflows/managed-m1.yml` runs only on `workflow_dispatch` on a disposable Ubuntu 24.04 hosted runner. Its short Bash driver, `scripts/ci/m1_install_ready_uninstall.sh`, provisions the rendered units' binaries/config, a disposable Headscale cell and expected peer, and a synthetic loopback HTTP backend; it invokes the product installer with a fresh single-use ten-minute file key, observes both notify services active/running, checks drift, makes a paired connector-loopback request, then uninstalls with `--purge` and checks documented residue. Payload, configuration, service logs and account are retained as the installer documents; runner disposal removes the fixture. Before the request and in always-run diagnostics, the script records runtime-directory owner/mode/type and loaded runtime directives; diagnostics also include manager reload timestamps alongside plain unit journals/status/cat and installer streams before stopping fixtures and uploading logs. This is separate from required lint/static checks and daily Product E2E. The passing run removed unit files/target enablement, enrollment source/link/operator input, persistent service state and both runtime directories. **M2 tailnet transport, full-daemon acceptance, restart/reboot recovery and deployment remain UNEXECUTED and outside M1.** The HTTP request enters the connector on loopback and uses a synthetic backend; it does not traverse the tailnet data path or run the full daemon, and initial startup/uninstall does not prove restart/reboot recovery.

- **Manual managed M3 same-boot recovery (THR-327): UNEXECUTED.** Acceptance is defined by `.github/workflows/managed-m3.yml` and `scripts/ci/m3_recovery.sh` and remains unexecuted until the main-only manual workflow passes. The harness reuses M2 provisioning, READY, paired real-daemon tailnet request, logs, teardown and purge residue checks. It tests connector SIGKILL, sidecar SIGKILL with the same Headscale node ID/IP/count and consumed enrollment (no new preauthkey), connector SIGSTOP/watchdog, intentional stop/start, shipped start-limit exhaustion/reset/start, daemon-fixture restart without reinstall, then uninstall/purge. Each recovery requires both notify services active/running and a paired tailnet HTTP 200, with per-step MainPID, NRestarts, Result and journal evidence. Step 5 crashes the running (READY) connector exactly five times and proves start-limit refusal from the connector journal. Only steps 4 and 5 explicitly start product services after a fault; `BindsTo=` leaving the sidecar stopped is a failure. Both units’ rate accounting is reset at the start of steps 1–4 and 6 to isolate faults. Reboot is outside M3 and remains **UNEXECUTED**.

- **Manual managed M2 (THR-327 seq247): PASSED at main `8165e05d10a0a50238ccaa86aa6d79ed859e4428` and, after the readiness-wait fix, at main `5d5fa8662b3a566fc408d1c65a4a3e4d62aae463`.** [Hosted run 43004289479](https://github.com/t-benze/happyranch/actions/runs/43004289479) passed all seven M2 steps on 2026-11-11 on a fresh Ubuntu 24.04 runner: product install with both services notify-READY and check-drift; paired tailnet → sidecar → connector → real-daemon org teams returned 200; absent and never-paired credentials returned 403 with exactly `{"error":"identity_denied"}`; a second device returned 200, was revoked through the product CLI, then returned 403 with exactly `{"error":"identity_denied"}` after the bounded reconciliation wait. All six forbidden routes returned 403 with their exact categories: executor registration and adapter deletion returned `method_denied`; custom-CLI commit, runtime/use, org deletion and auth bootstrap returned `route_denied`. The synthetic org remained loaded and readable after forbidden DELETE. Product uninstall `--purge` passed the M1 residue rules, and plain logs were uploaded with `if: always()`. The test-owned real `python -m runtime.daemon` uses a disposable home/token and synthetic org; `ExpectedPeer` is startup visibility, not per-connection node authorization. Both manual workflows cache the pinned Headscale binary and Tailscale archive under version+SHA-256 keys; both drivers verify checksums on hits and misses before use and download only absent files. [Rerun 43004588741](https://github.com/t-benze/happyranch/actions/runs/43004588741) at the same head passed step 1 but failed at step 2's first pairing request with curl exit 97 before HTTP: the peer had netmap visibility before endpoint discovery. That run proved cache hits, successful checksum verification and plain-log upload on failure. The M2 script now waits up to 60s for a TSMP ping through WireGuard before its first tailnet request, failing with the last ping output on timeout; HTTP outcomes and assertions are unchanged. After that fix merged (PR #1408), [hosted run 43011862585](https://github.com/t-benze/happyranch/actions/runs/43011862585) and [repeat run 43012067135](https://github.com/t-benze/happyranch/actions/runs/43012067135) at main `5d5fa866` each passed all seven M2 steps on 2026-11-11 with the same results listed above, including cache hits and successful checksum verification for both pinned downloads. At the same head, [M1 run 43012072443](https://github.com/t-benze/happyranch/actions/runs/43012072443) passed with the cached Headscale/Tailscale downloads. These receipts belong to `5d5fa866`; later heads are not claimed rerun. Main-only `workflow_dispatch`, M1's receipt above, required lint/static checks and daily E2E are unchanged. Restart/reboot recovery and deployment remain **UNEXECUTED**.

- **Managed remote access (normative contract, THR-097 merge unit A).** Fixed invariants: one Headscale cell per customer is the primary hostile-tenant boundary; the shared DERP fleet observes/relays only network metadata and WireGuard ciphertext and never grants reachability; the home connector is a **supervised portable Python companion** (never an in-process daemon listener) that enforces an explicit allow-by-method+normalized-template remote allow-list, current paired/current-device identity, and current policy/revocation epoch, failing closed; the daemon stays **loopback-only**; the daemon bearer is injected **only** by the connector on the final connector→127.0.0.1 hop and must never appear in remote input, client, Services, Headscale, DERP, network, fixture secrets, logs, errors, or audit. Revocation denies/closes before or atomically with node removal; never success early. Governing spec: `docs/superpowers/specs/2026-08-26-managed-remote-access-contract.md`; shipped route policy: `runtime/remote_access/route_policy.json` (118 allow entries; THR-327 R1 v1 excludes executor-binary registration, custom-CLI commit, runtime use, adapter deletion and org deletion; required Source Boundary Lint checks policy consistency); hostile threat fixtures: `tests/contract/managed_remote_access/` (consumed by the retained hostile lab; the former Python contract validator is retired). `route-classification.json` `included` (web coverage) is NOT remotely-allowed-equivalent; Swift `SurfaceAllowList` is legacy deny-list evidence, not authority. **Implementation status:** the portable supervised Python connector core (merge unit C / TASK-5724 phase unit 2) ships in `runtime/remote_access/` with a loopback-only harness and tests at `tests/remote_access/` — strict parser/canonical normalization, versioned route-policy consumer (locked nine-step decision order + every security-relevant nested value validated by exact canonical equality against the Unit-A semantics — contradictory non-empty prose and altered allowed-template lists rejected at load — plus operational state; schema/digest/version/staleness drift fails closed; unknown states never active), connector identity + device-proof verifier seam, current authorization/revocation with live-stream closure via one authoritative `RevocationCoordinator` transaction (the registry seals the retained stream wrapper fail closed before trust-state application — a raising transport close leaves the externally retained handle irrevocably rejecting receive/send, surfaced as `RevocationIncomplete`; the transaction is serialized across concurrent revocations and the stream registry shares/persists the complete cleanup terminal result, so no caller returns success while an in-flight or completed cleanup failure relevant to the sealed generation is unreported; old public `apply_revocation` bypass removed; admission and EVERY ownership/membership mutation — `open` admission, duplicate-id replacement, `close(stream_id)`, the retained-wrapper public `close`, and `close_all` — share ONE atomic lifecycle boundary: the lifecycle lock guards both the sealed flag and registry membership, every mutation seals the affected wrapper(s) atomically with its membership transition (no pop-without-seal ownership escape), any admission not fully registered before the seal fails closed and never returns a usable wrapper, transport open/close and duplicate-replacement callbacks never run under the lifecycle lock but remain INSIDE the revocation acknowledgement barrier (outside-lock transport closes that linearized before the seal are terminal before `close_all` success — revocation never reports success early), and same-thread re-entrant `close_all` from an unfinished transport cleanup callback MUST return fail-closed non-success (founder ruling THR-097 seq140): it may not exclude its own in-flight cleanup and publish success, may not mark the cleanup terminal with an incomplete failed-id set, and may not erase a callback failure that becomes terminal after the re-entrant call — the failure is persisted on the registry and re-surfaced by a later close_all/revoke (REV-007/REV-008), and derived `is_open`/`raise_if_open` are synchronized on the same lock; the gateway never double-closes a handle the registry owns), allow-list enforcement, remote-auth/hop-by-hop stripping, daemon-credential-provider seam, and a forwarder that targets only literal 127.0.0.1 and normalizes every forward/open/stream failure (connection refusal, timeouts, hostile exception text) into stable secret-free denials while deterministically closing partial resources; in-memory persistence abstraction only, no tailnet/external bind, no Headscale/DERP/Services integration, no packaging. **Linux supervised connector packaging (phase unit 3) is IMPLEMENTED** in the same package: an atomic, corruption-detecting, owner-only local trust-state store (``AtomicFileTrustStateStore`` satisfies the ``TrustStateStore`` protocol with a schema-agnostic non-normative envelope — atomic replace/fsync, sha256 corruption detection, symlink/loose-permission rejection, plus a founder-approved (THR-097 seq163) **non-database companion monotonic generation/digest anchor** at ``<state>.anchor`` outside the replaceable snapshot: the envelope generation must equal the anchored generation AND the anchor digest covers the exact snapshot bytes, so any previously valid OLDER snapshot replayed after a newer revocation/generation — including across a new store/process instance — is deterministically rejected, and missing/mismatched/corrupt/stale anchors, partial snapshot/anchor pairs, symlinked anchors, and loose anchor permissions all fail closed; a crash between the snapshot and anchor writes leaves a mismatched pair that load() rejects (the crash-consistency contract is documented in the module); NOT the founder-gated managed persistent schema — no DB, no migration), a ``SystemdCredentialProvider`` (``LoadCredential=`` injection — the service user never reads the daemon home), a five-gate readiness evaluator (``ConnectorReadiness``: daemon literal-127.0.0.1 reachability, credential permissions, current policy, bind identity, non-corrupt trust state — **no listener unless ALL pass**), a least-privilege systemd unit renderer (dedicated service user, empty capability bounding set/ambient capabilities, ``ProtectSystem=strict``, ``NoNewPrivileges``, ``LoadCredential=``, ``Type=notify``+``WatchdogSec``+``Restart=on-failure``; user-mode renders omit the directives the user manager cannot apply — ``PrivateDevices``/``ProtectKernelModules``/capability directives — verified on real systemd), an injectable ``SystemdServiceManager`` (install/uninstall/start/stop/restart/enable/disable/status + upgrade with auto-rollback and rollback), a **LAB-ONLY** conformance provider adapter (explicit ``lab_only`` + concrete lab bind address required; wildcard binds refused; runs the full gateway pipeline and forwards to literal loopback with bearer injection on the final hop; 403 category-only denials; never a product/Supported-DIY lane and NOT closure of THR-034), a ``ConnectorSupervisor`` (readiness-gated foreground loop with sd_notify READY/WATCHDOG/STOPPING — ``READY=1`` is emitted ONLY after the provider actually started and the listener is proven bound, never after a bind/start failure; expected operational listener failures — occupied bind port, permission, unavailable address — are normalized from the socket ``OSError`` to the documented ``LabProviderError`` category at the ``LabProviderAdapter.start()`` boundary (category-only message), so a REAL bind conflict keeps the loop retrying with ``STATUS=provider failed to start; no listener`` and no process exit (no systemd restart-throttle; the listener comes up once the conflict clears) while unexpected defects still propagate loudly; fail-closed listener stop on readiness loss; redacted ``diagnose``; ``install`` stages the service config/policy/state into the declared systemd-managed ``StateDirectory`` location accessible to the dedicated service user and the rendered unit points ``--config`` there — never at a ``~/.happyranch`` path the hardened unit cannot read; the source trust-state pair is only an INITIAL SEED when no managed pair exists — once a managed snapshot+anchor pair exists it is AUTHORITATIVE and reinstall never overwrites/rolls it back with a stale operator source pair (stale = refused, same-generation-different-bytes = refused, identical = no-op, strictly newer = adopted) and stages any advance transactionally (fail closed, no usable mixed pair); the service path automatically consumes ``$CREDENTIALS_DIRECTORY``/``LoadCredential=`` with no redundant config and no daemon-home token read; a provider-less ``run`` configuration is rejected at startup (fail closed, never ``READY=1``)), and the ``python -m runtime.remote_access.cli`` operator surface (run/install/uninstall/start/stop/restart/enable/disable/status/readiness/diagnose/upgrade/rollback; ``run`` requires ``--lab-only`` when a lab provider is configured and refuses to run with no provider at all). Real user-systemd conformance tests run under ``-m integration`` gated on an operational probe with explicit skip reasons: one launches the **ACTUAL rendered ``ConnectorSupervisor``** through the real ``Type=notify`` READY contract and proves the lab listener is bound, an end-to-end request reaches the literal-loopback daemon, restart, and stop-with-listener-gone (upgrade/rollback round-trip, enable/disable, daemon-reload also run against the real user manager); unit tests are deterministic with fakes. The hostile tenant-isolation harness (tranche unit B) ships at `labs/tenant_isolation/`. Provisioning/defaults/retirement remain unimplemented and outside any current PR; the capacity spike (tranche unit D) is deferred with no claim. **Supported-DIY customer-owned-network adapter (phase unit 3A) is IMPLEMENTED** (TASK-6032, fix-forward TASK-6039) as the PRODUCTION Supported-DIY lane in the same package: a customer-network address resolver/validator (`network.py` — the ENCRYPTED `tailscale ip -4` mode ONLY; the former plaintext explicit concrete-address mode is REMOVED and fails closed with a migration message, because bare HTTP on an arbitrary LAN/public interface would breach the fixed no-plaintext-service-path invariant — the customer-owned network is the customer's own WireGuard-encrypted Tailscale/headscale tailnet; wildcard/loopback/multicast/link-local/reserved/broadcast refused, fail closed), the local pairing ceremony engine (`pairing.py` — single-use expiring 8-char codes stored as digests only; redemption issues per-device `hrpair_` credentials at a MONOTONIC epoch, re-pairing invalidates old authority, and every ceremony mutation is SERIALIZED in-process AND across processes through the store's owner-only inter-process transaction (an fcntl.flock on `<state>.lock` making load, generation validation, snapshot publication, and anchor publication ONE serialized mutation boundary — the check-then-save race is closed, so concurrent redemption of one code from threads OR separate processes yields EXACTLY ONE credential, concurrent mutations are never lost, and the snapshot+anchor pair is always published consistently and loadable); revocation through the authoritative `RevocationCoordinator` closes live streams before the persisted epoch advance, per-device and all-devices revocation survive restart, `remove-device` REVOKES FIRST (streams close, epoch advances, persisted) and THEN deletes the record — a removed/lost device can never retain an open stream and the revocation survives restart — and removal denies identically to absent — no existence oracle), the production `DiyProviderAdapter` (`diy_provider.py` — binds ONLY the tailscale-resolved customer-owned-network address, serves the inherited THR-034 wire contract `POST /pair` + `X-HappyRanch-Device-Credential`, consumes/strips the credential at the boundary so it never reaches the daemon, runs the full locked gateway pipeline per request, forwards to literal 127.0.0.1 with bearer injection on the final hop), shared secret-free HTTP serving (`httpd.py` — the lab and diy adapters share one serving path; the lab provider is UNCHANGED in role and remains LAB-ONLY), additive OPTIONAL trust-state envelope fields (`credential_digest` on device records, `pending_pairings` in the payload — pre-3A envelopes still load; digests only, never raw credentials), `ConnectorConfig.diy` (mutually exclusive with lab; `run` requires the explicit `--diy` opt-in; provider-less run still rejected, never `READY=1`), a supervisor that owns ONE authoritative live-stream registry and ONE pairing manager per process shared by the gateway ctx factory, the provider, and the ceremony surface (in-process revocation closes the REAL shipping streams, never an unrelated empty registry), a cross-process revocation reconciliation in the `run` loop (a CLI `revoke`/`remove-device` in a separate process persists the epoch; the connector closes its live streams on the next loop pass bounded by `poll_seconds`, fail closed, and — because the registry is ONE-SHOT — ROTATES the authoritative runtime at a fail-closed lifecycle boundary the moment the registry is sealed: the listener is stopped first (no request/stream is admitted during the handoff), the cached registry and pairing manager are dropped, and the provider and every captured ctx-factory reference are rebuilt and restarted only when readiness passes, so a re-paired or unaffected device opens NEW streams in the same process lifetime and a revoked-with-no-live-streams revoke causes no listener gap), and the CLI ceremony surface (`pair`/`list-devices`/`revoke`/`remove-device`/`pairing-status`/`recovery --factory-reset` — truthful secret-free lifecycle states; `revoke` NEVER claims cross-process stream closure it cannot prove — it reports the persisted epoch and the connector's next-reconciliation closure; recovery deletes BOTH snapshot+anchor files per the store's crash-consistency contract). A REAL-network acceptance (`tests/remote_access/test_diy_acceptance.py`, `-m integration`) runs the away-client wire contract over a real network path to the real `cli run --diy` connector and a real loopback daemon — the customer-network address resolved through the REAL shipping tailscale-mode resolver path with a stub `tailscale ip -4` executable (no tailnet on the host) — proving allowed/forbidden routes, direct daemon/bearer attempts, restart-with-persisted-revocation, re-pair invalidation, replayed/removed credentials, outage fail-closed listener stop + supervised recovery, credential/token leakage scans, and (fix-forward) a GENUINE cross-process SSE negative: a live in-flight stream served by the connector process is closed by a `revoke` from a separate CLI process within one poll interval, and the CLI output carries no false success. The signed-macOS-binary and tailnet-transport hops remain honest residual gaps (this Linux host has no macOS build and no Tailscale client/network; THR-034 signed acceptance is NOT closed), and the macOS production app is unchanged by this PR.

- **VM reboot acceptance (THR-327 goal B).** The authorized VM-only driver `scripts/ci/m3_reboot.sh` and host runner `scripts/ci/m3_reboot_aliyun.py` extend M2/M3 with two clean passes and two reboots per pass. Persistent test units/state, saved pairing and Headscale identity/enrollment checks, automatic notify-READY, disabled-target inactivity for 30 seconds, explicit re-enable and purge residue are described in [the runbook](../../../scripts/ci/README-m3-reboot.md). Source delivery alone is not VM acceptance; exact-head phase/boot/identity receipts are required. No product unit or GitHub trigger changes.

- **M4 native macOS outside-client procedure (THR-327 goal C).**
  [The M4 runbook](../../../scripts/ci/README-m4-tailnet.md) owns the main-only
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
  [M4 runbook](../../../scripts/ci/README-m4-tailnet.md).
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

The [runbook](../../../scripts/ci/README-m4-tailnet.md#home-udp-enforcement-proof-task-15886)
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
