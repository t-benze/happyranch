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

## THR-211 containment Pipeline candidate

`Jenkinsfile` now contains executable private, detached, frozen preparation and
failure publication. It is a **held candidate**, not an installed job or a
successful setup/abort proof. The shipping `live_daemon` and `live_daemon_idle`
fixtures still start/stop through `daemon.sh` without a lifecycle `finally` or
bounded subprocess calls. Foreground `runtime.daemon.__main__.main` owns its
socket, but does not supply a test-owned independent lifetime mechanism for
escaped executors/jobs through parent loss. The controlled fixture tests expose
that gap without importing integration tests or launching a daemon. No fixture
repair, native-Mac containment, or acceptance is claimed.

Every mode (`SETUP`, `ABORT`, `DIAGNOSTIC`) runs the same independent preparation
if admitted, then exits78/HELD before any daemon/pytest. SETUP and ABORT remain
separate requests for future non-pytest lifecycle probes; they currently cannot
complete those probes. There is no parameter that releases F04. A later reviewed
implementation must establish the lifetime/observer mechanism before changing
this gate. A preparation result, synthetic self-expiring child, or green CI is
not that proof. ONE diagnostic remains UNUSED and parent-held.

### Installed definition and admission readback (manager-owned, not performed)

Use an ordinary **inline Pipeline script**, `CpsFlowDefinition`, not “Pipeline
script from SCM”. This Scripted Pipeline has no implicit checkout or `agent`
directive. Its first repository operation is the explicit detached fetch/checkout
inside private setup. Do not add `checkout scm`, a mutable branch loader,
`load`/`evaluate` of a fetched script, or automatic triggers. No Jenkins helper,
new plugin, credential, account or controller configuration is a dependency.

Before evaluation, the authorized manager/owner must:

1. Retrieve `Jenkinsfile` from the reviewed immutable commit, verify its SHA256
   against the accepted handoff, and construct the full inline script from those
   exact bytes plus a **literal** `binding.setVariable('THR211_INSTALL', map)`
   prefix. The map is installed configuration, never derived from parameters,
   build environment, caller receipt strings, workspace files or fetched SCM.
2. Supply exactly these string keys: `REQUEST`, `MODE`, `SOURCE` (40 hex),
   `PIPELINE` and `LOCK` (SHA256 of Jenkinsfile/uv.lock), `CONFIG` (SHA256 of the
   canonical admission record excluding its own digest), `NODE`, `ACCOUNT`,
   `START`, `EXPIRY` (UTC epoch seconds), `PYTHON`, `UV`, `GIT` (absolute effective
   tool paths), and `ARCH` (native architecture). The owner retains the record
   and digest independently. These are required real values, not supplied example
   admission or a ready request. The shell matches request/mode/source/Pipeline,
   actual node and effective account, validates all shapes, and checks the window
   before checkout. It requires at least50m remaining after allocation and a
   window no longer than70m. Missing bindings fail even before node allocation.
3. Tie that installation to the actual human administrator, all-three-executor
   and non-Jenkins same-UID scheduled/manual inventory, enforceable participant
   admission, contact/cancellation owner, and one request/window. A signature,
   job-level concurrency exclusion, or idle snapshot does not enforce admission.
   A same-UID process can still race files/processes; this Pipeline is not OS
   isolation. No readiness record exists while these owner prerequisites are absent.
4. Serialize the literal prefix and reviewed script as XML text in the existing
   authorized job's `<definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition">`
   `<script>` element, using an XML serializer, preserving its approved sandbox
   setting and other configuration. After the separately authorized installation,
   GET that exact job's `config.xml`, parse it, compare **the complete script and
   map bytes** with the locally prepared XML, and retain the full config SHA256
   separately from the map's admission digest. Recheck immediately before the
   one authorized submission; a post-checkout digest cannot replace this check.

Use only the established fixed API origin `http://127.0.0.1:8081` and protected
netrc **reference**, never token contents or parameter credentials. Use bounded
ordinary GET/POST calls per KB `jenkins-job-setup-and-durable-observation`; no
`scripts/jenkins_jobs.py` dependency applies to this THR-211 path. Installation,
readback against a controller, probe submission and diagnostic submission remain
parent-owned and have not been performed by this implementation.

The only diagnostic workload, after those manager-owned gates, is exactly:

~~~sh
uv run --frozen pytest tests/integration/ -v -m integration --junitxml=artifacts/integration.xml
~~~

The candidate does not run it and no worker may POST a probe or diagnostic.
The shell checks literal directory ancestry, creates a fresh private workspace
child with HOME/XDG/config/cache/state/runtime/tmp/uv-cache/venv/daemon-home/plans/
artifacts, and verifies a bounded write/read before Git/Python/uv. A clean
`env -i` child excludes credential/proxy/Git/Python/startup/outer fake injection;
only explicit settings and the Jenkins cookie are retained. The cookie is not
ownership authority. Tool parent ancestry must be canonical too. On macOS the
owner establishes the trusted `/private/var/...` temporary boundary before use;
an untrusted `/var` symlink ancestor is refused rather than resolved away.

Detached source SHA/clean state, Jenkinsfile/lock digests, native Python3.12/arch,
frozen sync, effective venv/native interpreter, absolute uv version and relevant
module/plugin origins are checked. `-I` explicitly inserts the selected source;
it does not presume cwd imports. Copied fakes are registered/read back only in
the private daemon-home. The probe never imports integration conftest or starts
the daemon; its `port=null`, `pytest_exit=null`, `observer=UNAVAILABLE`, and
`cleanup=UNKNOWN` remain explicit. Real ephemeral daemon-port readback is held
with F04, not fabricated from a free-port check.

The allocation watchdog has its own15m timeout in a parallel branch and stops
when the node is acquired; it does not wrap later work. Setup15m, held workload30m,
cleanup/publication5m, and external observation70m remain distinct. Setup errors
and controlled abort exits are retained. Receipt writes and both non-empty
archives are attempted independently; all secondary errors, including a cleanup
timeout, appear in the final console JSON without replacing the primary failure.
The earlier archived receipt cannot include later archive errors, so the observer
must collect **both** console and artifacts. Archives disable symlink following.
If the console also fails, the original exception is still retained, but the
final secondary-error receipt is unavailable; external observation must record
that loss as UNKNOWN, never infer a clean cleanup from the earlier archive.
Without a validated private root, publication uses console only. Without node
allocation, `workspace=null`/`ALLOCATION_FAILED` is emitted and no archive is
attempted. Missing artifacts, lost observer, or UNKNOWN never produces PASS.
Private roots/residue are retained for review; no unproved recursive cleanup or
PID-based teardown is attempted.

For any later authorized live request, persist request→queue→exact build identity
once; never retry an uncertain POST or select `lastBuild`. Submit the bounded
70m observer as a finite persistent HappyRanch job using the KB's supported job
payload and immediately park with `waiting_on_job_ids`. On the supported job
result continuation, inspect `jobs show` and `jobs output`, then exact remote
terminal result, console, setup/pytest exits, all errors, cleanup evidence and
artifact manifest. Observer job exit0 alone is not Jenkins/workload PASS. This
document provides no ready owner/window or authorization to install, probe,
cancel unrelated work, or consume the diagnostic.

The six selected test plan families call the builders in
`tests/thr211_containment.py`. The independent thread-reply fixture has its own
fresh plan root; it does not depend on the task-plan environment variable.
Each emitted plan allocates a private invocation directory and registers its
payload/log/completion files for cleanup on normal exit, callback failure, or
HUP/INT/TERM. Cleanup attempts every registered file and the directory, emits
each failure, and retains the primary exit status (or fails if cleanup alone
fails). State counters, job-ID rendezvous files and readiness sentinels remain
in the fixture-owned plan root for driver assertions; they are not transient
callback files. Fixture directory reclamation and daemon/descendant lifetime
containment remain separate F04 obligations. SIGKILL and hostile same-UID
replacement are not covered by the shell traps.

The review-required job serializes its absolute sentinel path into its own
body and uses private exclusive creation, refusing existing files/symlinks.
It does not inherit the parent plan's shell variables. The persistent plan
rejects preexisting readiness files/symlinks before submission and fails when
its bounded wait expires. JSON serialization preserves quoted runtime values
and paths. Both blocked-job stages retain their original protocol fields and
summary decisions; CLI callbacks use single-line absolute `--from-file` paths.

The path helper walks literal absolute ancestry through no-follow directory
descriptors, refuses existing roots, and compensates partial acquisition in
reverse order. Acquisition, removal and descriptor-close errors are preserved
together, with residue named rather than silently accepted. Callers must
supply a trusted canonical temporary boundary: on macOS resolve the trusted
system temporary directory first (for example `/private/var/...`), never
resolve an untrusted candidate or accept a `/var` alias inside its ancestry.
These checks detect preexisting unsafe paths; they are not OS isolation or a
guarantee against hostile same-UID races after validation.

Focused unit tests execute the shipping builders' returned plans and the
actual submitted job bodies with controlled callback, HTTP, sleep and cleanup
tools. They do not import/collect integration tests or contact a daemon or
Jenkins. Ordinary integration remains SKIPPED under THR-243 seq42, and this
plan evidence does not establish F04, Pipeline readiness or live admission.
