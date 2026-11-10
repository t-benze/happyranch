# Manual Mac native probe

`happyranch-mac-native-probe` is the **one** new job authorized by Founder
THR-346 msg6 (root TASK-15047, S0 TASK-15050). It observes Darwin prerequisites
for the frozen Mac app. S0 does not build the app, run a daemon, fix runtime
code, install tools, or change the host. Connector/launchd work is excluded.

Upload `config.xml.in` verbatim with Jenkins's ordinary `createItem` API after
checking that this exact item does not already exist. S0 permits pre-merge
creation from the PR head. If the item already exists, reconcile its owner and
configuration; do not create another item or overwrite it blindly. Record the
source commit and configuration SHA256 with the creation receipt. This is a
Freestyle job with NullSCM: neither controller Git nor the Git SCM plugin is
used. The existing `mac-mini` label and build-timeout plugin must be available;
do not install or reconfigure them on this authorization.

The controller browser URL is `http://192.168.18.174:8081`. Use only the existing
authorized credential environment/reference for its ordinary API; never embed
credentials in the template, source checkout, parameters or archived evidence.
Missing credentials block creation/submission. No controller executors, sudo,
credential configuration, daemon deployment or restart is needed.

Submit manually with:

- `SOURCE_SHA`: required 40-hex commit. The shell validates it, initializes a
  fresh empty checkout, fetches **only that SHA** with `--depth 1 --no-tags`,
  checks out detached, and checks `HEAD` against the requested SHA. Global Git
  configuration and credential helpers are disabled for this public fetch.
- `SUITE`: defaults to `truth`. The shell accepts only a bounded identifier;
  the pinned Python dispatcher's argparse allowlist validates the actual suite
  before spawning anything. This is deliberately a string parameter so future
  reviewed slice PRs can extend the dispatcher without editing the Jenkins job.

There are no SCM/cron/remote-token/upstream/downstream triggers, concurrent
builds, controller checkout or credential bindings. The absolute build timeout
is 30 minutes. The EXIT trap removes only the build's fresh checkout scratch;
`evidence/` stays for archival. Catchable cancellation runs cleanup; SIGKILL,
agent loss or machine failure may leave scratch and must be reported honestly.
The next run does not sweep previous scratch. Jenkins retains 20 builds/30 days.

## Probe contract

From a checkout, using an existing Python 3.12–3.14 interpreter:

```sh
python3 scripts/mac_native/probe.py --suite truth --output /absolute/probe.json
```

Only `truth` ships in S0. JSON goes to stdout and the optional output path;
human observations go to stderr. Linux prints `darwin-only`, emits skipped
JSON and exits 0. Darwin findings, including a broken repo decoder or unavailable
mount API, are data. Internal exceptions, incomplete process-tree cleanup and
output errors exit nonzero. There are no third-party Python dependencies.

The probe imports the pinned checkout's `_LibProc._ProcBsdinfo` when possible;
otherwise it explicitly labels a byte-identical copy of the S0 base decoder
(`9ed24be93`). That fallback is historical, not an assertion about a future
changed checkout. The independent corrected layout includes `pbi_xstatus`
after `pbi_status`, following Apple's
[`proc_info.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h).
The 64-bit mount API layout follows Apple's
[`mount.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/mount.h).
Both decoder sizes and kernel byte counts are recorded. Each process reports
its own PID/PPID/PGID using `os`; child creation windows and a native `ps`
reference supply separate start-time/state evidence. Status is a momentary
sample, so two successive calls may legitimately differ. The probe's parent
spawns a child with `start_new_session=True`, and that child spawns and reaps a
grandchild. Cleanup sends TERM to the owned group, waits for the child to reap,
escalates only if needed, and checks both PID absence and group absence.

Other observations include `O_PATH`/no-follow flags, `/proc`/mountinfo presence,
raw `mount` samples, ctypes getmntinfo/statfs feasibility and mount points,
`df -Pi`/`df -i` header plus one row, and a fresh `$TMPDIR` case-collision trial.
The latter describes that temporary volume only; it must not be generalized
to all APFS volumes. macOS/architecture/Python/UID/RLIMIT_NOFILE facts accompany
the observations. Probe cleanup operates only on its own tree/temp directory.

Archive and collect `evidence/probe.json`, `probe.log`, `build.log` and
`identity.txt`. Record request → queue ID → exact build number/URL/result,
source SHA, suite, node, Python identity and scratch cleanup. SUCCESS alone
does not prove the observations or cleanup: inspect the JSON and identity.
S0 allocates one build; environmental correction permits at most three total.
A lost POST response never authorizes blind resubmission. Inspect the original
queue/build; do not use `lastBuild` as identity. Use a durable HappyRanch job for
a long wait/collection and report in-flight state rather than holding a session.
This job is separate from lint/static-only required PR checks and Product E2E;
no GitHub workflow or CI trigger is added.
