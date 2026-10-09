# Ordinary Jenkins job helper

This standalone Python helper operates an already-authorized **existing** Freestyle or Pipeline job on Unix (Linux/macOS). It requires standard-library Python 3.12–3.14 and an owner-controlled receipt/output directory. The same ordinary Jenkins APIs work for either job type; no Jenkinsfile, custom parameters, three-hook template, runtime adapter, node setup or plugin installation is needed.

This PR852 distribution is a **candidate pending independent review, QA and exact-head CI**. Do not claim it is accepted or that a live Jenkins run succeeded. The old B2 catalog description saying “reviewed” is premature; this document is the candidate's current acceptance statement.

## Immutable retrieval and copy

Run these commands only after the acceptance gates and normal publication are verified. `SOURCE` is an immutable commit containing the exact helper; the checksum assertion must succeed before installation. A fresh temporary clone avoids reusing another task's checkout. The published PR may contain a later documentation commit with identical helper bytes.

~~~sh
set -eu
SOURCE=fae65226ecc14b85aef63d1e7ef3488f6d857edc
JENKINS_STAGE=$(mktemp -d)
git clone --no-checkout https://github.com/t-benze/happyranch.git "$JENKINS_STAGE/source"
git -C "$JENKINS_STAGE/source" fetch origin "$SOURCE"
git -C "$JENKINS_STAGE/source" show "$SOURCE:scripts/jenkins_jobs.py" > "$JENKINS_STAGE/jenkins_jobs.py"
python3 -c 'import hashlib,sys; assert hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()=="417c208e5b986ae511f1b8bbe88d4ee1cf5cee7d8c83a5346d1e9f0f7f698be4", "helper checksum mismatch"' "$JENKINS_STAGE/jenkins_jobs.py"
JENKINS_TOOL_DIR="$HOME/.local/share/happyranch-tools"
install -d -m 700 "$JENKINS_TOOL_DIR"
install -m 700 "$JENKINS_STAGE/jenkins_jobs.py" "$JENKINS_TOOL_DIR/jenkins_jobs.py"
~~~

The checksum uses Python on both Linux and macOS. Keep that installed copy and record its hash with each job receipt. Retrieval verification and copied ordinary invocations are tested against fake Jenkins; there is no live-controller acceptance claim.

## Existing job invocation

Configure only the already-authorized controller URL, existing job path, username/API-token **environment references**, and durable local paths. `JENKINS_USERNAME` and `JENKINS_API_TOKEN` are consumed without printing or saving their values. The helper sends preemptive Basic authentication. Use HTTPS; `--allow-http` is only for an explicitly authorized private HTTP endpoint. See Jenkins [scripted-client authentication](https://www.jenkins.io/doc/book/system-administration/authenticating-scripted-clients/) and [ordinary remote API submission](https://www.jenkins.io/doc/book/using/remote-access-api/).

For an existing unparameterized Freestyle job:

~~~sh
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/freestyle smoke' --receipt "$HOME/.local/state/happyranch/jenkins/freestyle-001.json" --deadline 900 submit
~~~

For an existing Pipeline job whose existing definition already declares the string parameter `branch`:

~~~sh
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" --deadline 900 submit --parameter branch=main
~~~

Either job type can have declared parameters or none. Omit `--parameter` for none; existing configured defaults remain Jenkins's responsibility. The helper validates supported declarations before POST and uses `/build` or `/buildWithParameters` as appropriate. Undeclared, unsupported, malformed, duplicate, invalid boolean or invalid choice inputs are rejected. File/upload parameters are unsupported. Each distinct authorized submission needs a fresh receipt path; an existing path is never resubmitted.

## Durable observation and recovery

Reuse the **same receipt** for every observation of that submission:

~~~sh
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" wait
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" show
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" collect --output "$HOME/.local/state/happyranch/jenkins/pipeline-001-output"
~~~

`show` prints the full receipt offline, including exact controller/job/request, numeric queue/build, persisted deadline, remote result and local collection manifest; it works after expiry. `wait` accepts only a complete exact build observation. `SUCCESS`, `FAILURE`, `UNSTABLE`, `ABORTED` and observed `QUEUE_CANCELLED` remain distinct. Missing/unsupported/malformed results are local observation errors, never invented remote terminal results. An observed terminal result is monotonic; later `wait`/`cancel` replay it without another POST.

A lost POST response, 5xx, missing/unsafe Location or queue 404 never causes automatic resubmission. Within the original deadline, after the operator independently identifies the actual numeric build, attach it by verified GET:

~~~sh
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" reconcile --build-number 123
~~~

Queue observations require a positive integer returned ID matching the saved queue ID before executable or cancellation facts are consumed. Missing, wrong or malformed IDs retain the existing receipt without advancing to a build or terminal result. The helper validates the returned number, URL and running/result fields before recording build 123. It never uses `lastBuild`. Once the persisted deadline expires, network operations stop; `--deadline` on resume cannot extend it. Use offline `show` and operator-side Jenkins inspection to resolve that expired receipt; do not edit it to reopen submission or extend the budget. Choose the original deadline to cover queue, execution and collection.

Explicit authorized cancellation uses the same common arguments followed by `cancel`. It records cancellation intent, requests queue cancellation or the exact build stop, and carries that intent through delayed queue-to-build races. A cancellation POST being accepted is distinct from observed termination. Lost cancellation responses retain attempted intent without blind replay; inspect the exact build. Local process interruption or timeout sends no cancellation.

The original absolute deadline spans receipt ownership, lock admission and all network phases. Each HTTP exchange also has a finite `--timeout` (default 15 seconds), including headers, ordinary/error bodies and framing. Receipt reads are regular-file-only, bounded to 2 MB, and locks cannot wait indefinitely. Parent/leaf replacement is detected against the owned receipt generation; do not concurrently edit or move the owner's directories.

Collection independently limits API JSON (1 MB), console (1 MB), each artifact (10 MB), aggregate artifact response bytes (50 MB), and artifact count (32; hard manifest ceiling 1024). Failed/error responses consume the aggregate budget too; a single extra byte may be read to detect overflow, after which no further artifact transfer is admitted when the aggregate is exhausted. Count and aggregate omissions are explicit partial results. Paths are validated before GET, including encoded traversal, and publication uses opened no-follow parents and exclusive file creation. Existing output files are not overwritten. Collection persists each attempted transfer's bytes and errors; Jenkins SUCCESS remains SUCCESS even when local collection is partial. `show` exposes the durable manifest after a local failure. A new collection attempt needs a fresh output destination and stays within the original deadline.

Exit 0 means successful admission/inspection/reconciliation, an observed SUCCESS from wait/cancel, or complete collection of SUCCESS. Exit 2 means an observed non-success result or partial collection; exit 3 means a local validation, transport, deadline or persistence failure. Inspect the receipt in all nonzero cases rather than inferring Jenkins's result from the local process exit.

## Persistent HappyRanch job, blocked callback and resume

Run a potentially long `wait` in an ordinary persistent HappyRanch job. Replace `TASK-EXAMPLE` and `ACTIVE_SESSION` with the actual active task/session and adjust only existing authorized job configuration. Save this JSON as `/tmp/jenkins-wait-job.json`:

~~~json
{
  "task_id": "TASK-EXAMPLE",
  "session_id": "ACTIVE_SESSION",
  "title": "Observe existing Jenkins pipeline build",
  "script": "python3 \"$HOME/.local/share/happyranch-tools/jenkins_jobs.py\" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt \"$HOME/.local/state/happyranch/jenkins/pipeline-001.json\" wait",
  "interpreter": "bash",
  "review_required": false,
  "persistent": true,
  "max_runtime_seconds": 960,
  "max_output_bytes": 65536
}
~~~

`review_required:false` applies only under existing endpoint, credential and command authorization. Otherwise follow the delivered jobs approval workflow. Submit with this single-line command:

~~~sh
happyranch jobs submit --org happyranch --from-file /tmp/jenkins-wait-job.json
~~~

After obtaining the actual `JOB-NNN`, save `/tmp/jenkins-blocked.json` using your active task/session/agent and the returned job ID:

~~~json
{
  "task_id": "TASK-EXAMPLE",
  "session_id": "ACTIVE_SESSION",
  "agent": "ASSIGNED_AGENT",
  "status": "blocked",
  "confidence": 0,
  "summary": "Waiting for the durable Jenkins observation job; no remote terminal result claimed.",
  "waiting_on_job_ids": ["JOB-NNN"]
}
~~~

A manager callback must additionally include its task-role-required `decision` object. Submit the callback as your final action; the runtime resumes the task when the job is terminal:

~~~sh
happyranch report-completion --org happyranch --from-file /tmp/jenkins-blocked.json
~~~

On the resumed task inspect both durable HappyRanch records, even if the job failed or was rejected:

~~~sh
happyranch jobs show JOB-NNN --org happyranch
happyranch jobs output JOB-NNN --org happyranch
python3 "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --controller https://ci.example/jenkins --job 'release folder/pipeline smoke' --receipt "$HOME/.local/state/happyranch/jenkins/pipeline-001.json" show
~~~

Verify the actual job script, installed helper SHA256, interpreter, exit and full output; compare the receipt's controller/job/request/queue/build/deadline/result/manifest with the saved submission. A HappyRanch job failure/rejection is a local execution fact, not Jenkins FAILURE or ABORTED. A successful waiter does not imply artifacts were collected: use the explicit bounded collection operation above, then inspect its manifest. Do not keep the model actively polling while the durable job runs.

The document-only B2 skill `custom:269f9a0b-b6ab-4eb3-a19b-a3cbcbc418c8` remains separate from helper acceptance. A valid successor must match this immutable helper pin. Founder-configured eligibility is still required before materialization; this workflow does not grant eligibility or change credentials. Mac smoke TASK7679 and KB publication TASK7683 are separate evidence, not helper acceptance.

## Mac mini disposable integration job

`ci/jenkins/mac-integration/Jenkinsfile` is the reviewable Declarative Pipeline
definition for the post-merge Mac integration job. Declarative Pipeline keeps
the node selection, sole parameter, absolute timeout, and unconditional evidence
publication in one versioned definition; the stdlib-only host logic lives in
`scripts/jenkins_mac_integration.py` so its validation, argv, cleanup, and exit
mapping are unit tested with a fake `container` executable. This is separate from
the repository-root Jenkinsfile parked in PR #864.

The job has one parameter: `SOURCE_SHA`, which must be exactly 40 hexadecimal
characters. It fetches that exact commit from
`https://github.com/t-benze/happyranch`, checks it out detached, and rejects a
different `git rev-parse HEAD`. The Pipeline definition checkout is not mounted
into the test VM. Do not add a shell/workload parameter, SCM polling, cron, or an
automatic trigger.

The runtime pins are:

- Apple `container` CLI exactly `1.5.0`; any other reported client version fails
  before kernel, system, or container operations.
- `docker.io/library/python:3.12-slim@sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5`,
  the `linux/arm64/v8` OCI manifest selected from Docker Hub's
  `library/python:3.12-slim` index on 2026-10-01. Resolution used the Docker
  Registry v2 token endpoint and fetched the tag index from
  `/v2/library/python/manifests/3.12-slim` with OCI-index and Docker manifest-list
  Accept types, then selected `platform.os=linux`,
  `platform.architecture=arm64`, `platform.variant=v8`. The job records the
  pinned reference and `container image inspect` result.
- uv exactly `0.12.21`, installed inside the disposable VM; its `uv --version`
  output must be exactly `uv 0.12.21`, optionally followed by uv's
  ` (<target triple>)` suffix. It is used for `uv sync --frozen`. The venv and uv
  caches live under container-local `/tmp`, never the host-mounted source.
- The exact direct guest apt bundle is `bash curl iproute2 git`, installed with
  `--no-install-recommends`. `guest-packages.json` records their resolved versions
  and packages added or changed transitively; `identity.txt` also records the
  direct tool versions and effective Python. Bounded `git --version` validates
  the committed parent's Git prerequisite before the workload and records
  `git_version` in `identity.txt`. This guest tool bundle is separate from
  top-level repository dependencies. This repairs a setup omission;
  build #6 did not capture actual `ip` presence or an interface address, so it
  does not prove that omission caused its failure. No repository dependency changes.

Kernel readiness follows the proven Apple 1.5.0 sequence. If
`~/Library/Application Support/com.apple.container/kernels/default.kernel-arm64`
already resolves, installation is skipped. Otherwise the job runs bounded
`container system kernel set --recommended` without `--debug`. When
Apple 1.5.0 returns exit 1 with its exact documented `apiserver is not running`
or `apiserver is not running and not registered with launchd` output, the job
treats that result as stopped and runs bounded
`container system start --disable-kernel-install --timeout 120`, then verifies
`running`. Every other nonzero, malformed, or unexpected status result fails
closed. It leaves the kernel, apiserver, and image cache in place.

Each build uses the deterministic build-scoped container name
`happyranch-integration-<SOURCE_SHA-first-12>-<BUILD_NUMBER>`, arm64, and
`--rm`. It does not request
privileged mode, added capabilities, host PID, host networking, SSH forwarding,
sockets, credentials, or environment inheritance. The only
host bind mounts are the detached source at `/workspace/src` read-only and the
build-owned `$WORKSPACE/artifacts` at `/workspace/artifacts` read-write. `HOME`
is a container-local `/tmp` directory. Because the Mac currently has
`machine.homeMount = "rw"`, the VM records `/proc/self/mountinfo` and fails
unless the complete set of host-backed virtiofs mount destinations is exactly
those two paths; `/Users/...` exposure therefore cannot pass silently.
The verifier also requires source `ro` and artifacts `rw`. The definition-owned
stdlib helper `scripts/jenkins_mac_guest.py` is embedded as bytes in the command
and exclusively materialized under guest `/tmp`, with a SHA256 identity. There
is no third mount or source overlay. Source, definition, helper, image and tool
identities remain separate. Bytecode writes are disabled; before/after source
tree digests (excluding Git metadata and bytecode caches) must match.

Inside the VM the command mirrors the hosted nightly seam:

~~~text
python scripts/run_bounded_output.py --output /workspace/artifacts/integration.log --max-bytes 1048576 -- uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration --basetemp=/tmp/happyranch-pytest -p no:cacheprovider --junitxml=/workspace/artifacts/integration.xml
~~~

The definition-owned runner explicitly names the committed source parent inside
the bounded-output wrapper. The guest's direct `uv run pytest` prefix handling
does not inspect nested child argv. The parent requires a clean committed source
and creates the closed temporary environment and authenticated deterministic
stubs before pytest/conftest collection. A missing parent fails visibly; direct
integration collection without the parent retains the conftest refusal. The
wrapper preserves pytest's selection, arguments, status and 1 MiB log tail;
the guest retains the shared 2,300-second ceiling and 60-second reserve below.

After frozen sync, a bounded `ip -4 -o addr show` probe uses the unchanged
shipping address validator and a real ephemeral bind. Its receipt distinguishes
missing executable, command nonzero/timeout/output cap, malformed or loopback
output, validator refusal and bind failure/success. No fallback address or
network configuration is supplied. The original full workload selection and
assertions remain unchanged.

Before guest exit, `guest-diagnostics.json` captures only recognized pytest
node directories below `/tmp/happyranch-pytest`, in fixed priority order:
two-org, mixed-fleet, DIY revoke, then DIY acceptance (lexical ties). The mixed
prefix is exactly `test_mixed_fleet_roundtrip_use` plus one to four digits.
Limits are four nodes, eight task rows per node, 32 recognized event categories
per node, 8KiB per text input, two seconds per SQLite read and 30 seconds total
capture. All new JSON diagnostics combined are capped at 64KiB. Fixed read-only,
query-only queries correlate alpha/beta `TASK-001` status and result/session-start
counts. Raw notes/logs/prompts/argv/credentials/DB/WAL are never archived;
only fixed recognized categories survive, with explicit omitted/unavailable
fields and unavailable executor evidence. Diagnostic statuses admit the production
`in_progress`, `escalated` and `superseded` literals plus existing literals,
including legacy `running`/`blocked`; an unknown status refuses the whole org.
Known instruction-pair and workspace-readiness refusal stems take priority over
the generic invocation-failed envelope; other retained compatibility markers
remain fixed enums. Marker priority is instruction pair, workspace readiness,
`WorkspaceNotInitialized`, authority selector, `SymlinkMaterializationError`,
executor missing, `session_mismatch`, then generic invocation failed.

Logs of at most 8192 bytes are scanned contiguously once. Larger logs use only
4096-byte head and tail windows from the same owned descriptor, without a
sentinel or middle probe. Only LF delimits physical lines: the head's incomplete
last fragment and the tail through its first LF are conservatively discarded;
windows are never joined. UTF-8 replacement occurs after trimming. Successful
receipts include `log_size_bytes`, `log_read_bytes`, `log_gap_bytes`,
`log_boundary_discarded_bytes`, `log_scanned_bytes` and `log_omitted_bytes`:
scanned = read - boundary-discarded, omitted = gap + boundary-discarded.
`log_truncated` means omitted bytes exist, `log_text_omitted` is always true
(including empty logs), and `log_event_limit_reached` means a 33rd recognized
line existed, while only the first 32 enums survive. File or ancestry refusal
retains no provisional log categories or byte/limit fields. Literal no-follow
ancestry and file identities are rechecked; symlinks, replacement, unknown schemas and changing
inputs refuse. A live WAL is unavailable rather than ignored or modified.
For a closed checkpointed WAL-mode main file, both sidecars must be absent
before and after its lock-free read; otherwise no task facts are retained.
Cause stays unknown when the safe evidence cannot resolve it.

It then runs `scripts/nightly_integration_summary.py`. The job archives the
JUnit XML, bounded log, Markdown summary, mount evidence, identity record,
cleanup evidence, and any bounded failure record. Cleanup always attempts
`container rm -f <build-scoped-name>`, records `container ls -a`, and fails with
distinct exit 90 when pytest passed but absence could not be verified. A real
nonzero pytest status is preserved even when capture or cleanup also fails.
Summary failure keeps its original precedence when workload passed. The host
checks `guest-result.json` and helper/source identities before removal; with
workload and summary successful, cleanup failure 90 precedes evidence failure 91.
An outer hard timeout is 124 with explicitly missing diagnostics, never a claimed
successful capture. Setup failure does not fabricate a JUnit file. Declarative
Pipeline `post { always { ... } }` independently repeats that same bounded
remove-and-absence check and archives `post-cleanup.txt`, so an outer abort does
not depend on the runner's Python `finally` block.

The Pipeline has a 90-minute absolute timeout. The runner's maximum cumulative
subprocess waits are 4,575 seconds (76.25 minutes): four source-checkout calls
at 300 seconds plus a 30-second detached-HEAD check; 15 seconds for CLI version;
480 seconds for the kernel; two 30-second status calls plus 150 seconds for
start; 2,520 seconds for the disposable workload; 30 seconds for image
inspection; and 60 + 30 seconds for runner cleanup and absence verification.
The independent Pipeline-post cleanup budget is another 90 seconds, for a
total bounded requirement of 4,665 seconds (77.75 minutes). The 5,400-second
outer limit is therefore strictly greater by 735 seconds (12.25 minutes),
covering Pipeline/definition-checkout overhead while retaining a finite bound.
The inner container command remains bounded to 42 minutes. One 2,490-second
inner deadline covers source hashing, package update/install, pip, frozen sync,
probe, workload, capture, summary and shutdown; the outer 2,520-second wait
retains 30 seconds margin. Setup command ceilings are 300 seconds for each apt
operation, 600 for pip and frozen sync, and 15 for identity operations. Pip retains
the 60-second capture/summary/shutdown reserve and the helper's 1.5-second
owned-process teardown allowance. The same pinned 19.8 MB uv wheel took 339.128
seconds to download and install in build 6, exceeding the previous 300-second
pip ceiling; 600 seconds covers that observation without guaranteeing future
transfer success or identifying the cause of the slow download. Every
command uses the lesser of its ceiling and the same remaining deadline, including
owned-process-group teardown. Pytest's 2,300 seconds is a maximum conditional on
remaining time, reserving 30 seconds for capture and 30 for summary/shutdown;
it is never extra allowance after setup. macOS has no GNU
`timeout`; all host-side bounds are Python subprocess deadlines and do not leave
watchdog children holding Jenkins pipes open.

Focused shipping-boundary verification in an authorized disposable runner is
`uv run python -m pytest tests/scripts/test_jenkins_mac_integration.py tests/scripts/test_jenkins_mac_guest.py -v -m 'not integration'`.
These include actual emitted wrapper/committed-parent invocation, harmless real
pytest/conftest success and refusal cases, exit/log limits, owned self-expiring
children and private SQLite fixtures. The stub-only launch control establishes
argv/environment/exit composition; it is distinct from real pytest execution.
The emitted-setup control exposes a selected real Git executable only when
its external apt stand-in receives the Git install request, then invokes the
unchanged committed parent. It proves setup/argv composition and refusal on
omission, not apt installation in the pinned arm64 image. Actual resolved Git
and transitive versions remain unavailable until the authorized guest run.
The complete focused command includes socket cases and integration collection
in child processes, so it must never run on the live Linux daemon host, even
through a job. On-host checks are restricted to demonstrably pure offline units.
Clean committed-head `scripts/local_ci.sh all` runs in the existing manually
dispatched `.github/workflows/nightly-integration.yml` Python 3.14/Node 24 lane.
Use finite durable task-owned observation and authenticate the workflow head,
job, command exit and artifacts; required PR CI remains independent. The nightly
integration lane's natural failure or skips are separate adverse evidence.

Historical first-PR capability observation: IPv4/full-suite results at old pin
`ebaf6139ef14e67efae841ab2048b91f69b20096` were NOT RUN pending the
parent-owned post-merge disposable run under THR-211 seq342; that PR did not
complete TASK-9558. Systemd/dbus, user-manager setup, backend probes and the
28 systemd gaps remain HELD/UNTESTED for the separately authorized capability unit.

### Create and run after merge

The engineering manager creates the live job only after this definition is
merged. In Jenkins, create a Pipeline item with concurrent builds disabled and
no automatic triggers. Choose **Pipeline script from SCM**, Git, the public
repository URL above, the `main` branch, no repository credentials, and script
path `ci/jenkins/mac-integration/Jenkinsfile`. Confirm the resolved agent is
`mac-mini`, then run **Build with Parameters** using the approved full commit
SHA. Review `identity.txt` and `mount-evidence.txt` before accepting the test
counts, and require both `cleanup.txt` and `post-cleanup.txt` to say
`cleanup_verified_absent=true`.

Creating, editing, or running that live Jenkins item is an operator action, not
part of repository verification. Never run this integration command on the
HappyRanch Linux daemon host, directly or through a HappyRanch job.
