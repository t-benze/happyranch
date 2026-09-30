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
- uv exactly `0.12.21`, installed inside the disposable VM, verified with
  `uv --version`, and used for `uv sync --frozen`. The venv and uv caches live
  under container-local `/tmp`, never the host-mounted source.
- The image's Debian repositories supply `bash` and `curl`, which the existing
  integration fixtures invoke; their resolved package versions and Python's
  effective version are recorded in `identity.txt`. This changes only the
  disposable VM and introduces no repository dependency.

Kernel readiness follows the proven Apple 1.5.0 sequence. If
`~/Library/Application Support/com.apple.container/kernels/default.kernel-arm64`
already resolves, installation is skipped. Otherwise the job runs bounded
`container system kernel set --recommended` without `--debug`. When
`container system status` is not `running`, it runs bounded
`container system start --disable-kernel-install --timeout 120`, then verifies
`running`. It leaves the kernel, apiserver, and image cache in place.

Each build uses a unique container name, arm64, and `--rm`. It does not request
privileged mode, added capabilities, host PID, host networking, SSH forwarding,
sockets, credentials, or environment inheritance. The only
host bind mounts are the detached source at `/workspace/src` read-only and the
build-owned `$WORKSPACE/artifacts` at `/workspace/artifacts` read-write. `HOME`
is a container-local `/tmp` directory. Because the Mac currently has
`machine.homeMount = "rw"`, the VM records `/proc/self/mountinfo` and fails
unless the complete set of host-backed virtiofs mount destinations is exactly
those two paths; `/Users/...` exposure therefore cannot pass silently.

Inside the VM the command mirrors the hosted nightly seam:

~~~text
python scripts/run_bounded_output.py --output /workspace/artifacts/integration.log --max-bytes 1048576 -- uv run pytest tests/ -v -m integration --basetemp=/tmp/happyranch-pytest -p no:cacheprovider --junitxml=/workspace/artifacts/integration.xml
~~~

It then runs `scripts/nightly_integration_summary.py`. The job archives the
JUnit XML, bounded log, Markdown summary, mount evidence, identity record,
cleanup evidence, and any bounded failure record. Cleanup always attempts
`container rm -f <unique-name>`, records `container ls -a`, and fails with
distinct exit 90 when pytest passed but absence could not be verified. A real
nonzero pytest status is preserved even when cleanup also fails.

The Pipeline has a 55-minute absolute timeout: 30 minutes matching the hosted
nightly budget, plus 8 minutes for the bounded recommended-kernel download,
2.5 minutes for system start, 10 minutes for image pull/frozen sync, and about
4.5 minutes for checkout, evidence, and cleanup. The inner container command is
also bounded to 42 minutes. macOS has no GNU `timeout`; all host-side bounds are
Python subprocess deadlines and do not leave watchdog children holding Jenkins
pipes open.

### Create and run after merge

The engineering manager creates the live job only after this definition is
merged. In Jenkins, create a Pipeline item with concurrent builds disabled and
no automatic triggers. Choose **Pipeline script from SCM**, Git, the public
repository URL above, the `main` branch, no repository credentials, and script
path `ci/jenkins/mac-integration/Jenkinsfile`. Confirm the resolved agent is
`mac-mini`, then run **Build with Parameters** using the approved full commit
SHA. Review `identity.txt` and `mount-evidence.txt` before accepting the test
counts, and require `cleanup.txt` to say `cleanup_verified_absent=true`.

Creating, editing, or running that live Jenkins item is an operator action, not
part of repository verification. Never run this integration command on the
HappyRanch Linux daemon host, directly or through a HappyRanch job.
