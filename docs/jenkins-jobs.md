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

`Jenkinsfile` is a manually invoked candidate for the separately authorized
THR-211 diagnostic. Its checked-in form deliberately fails closed for
`DIAGNOSTIC`: the manager, not a worker or this Pipeline definition, must first
bind the immutable source/config identity, all-executor and same-UID admission
receipt, and the single diagnostic intent. `SETUP` and `ABORT` are distinct,
non-pytest receipt modes and keep `pytest_exit=null`.

Before any installation, an authorized Jenkins owner must create an immutable
SCM-backed Pipeline definition pinned to the reviewed source SHA and
Jenkinsfile digest *before* evaluation. A post-checkout comparison is not an
identity control. The owner must also provide the bounded admission window,
node/account identity, cancellation contact, and evidence covering all three
executors, non-Jenkins same-UID launches, and shared `/tmp` containment. An
idle-node snapshot, account name, or `disableConcurrentBuilds` is insufficient.

The only diagnostic workload, after those manager-owned gates, is exactly:

~~~sh
uv run --frozen pytest tests/integration/ -v -m integration --junitxml=artifacts/integration.xml
~~~

The candidate does not run it and no worker may POST a probe or diagnostic.
Preparation must use private, no-follow HOME/XDG/cache/state/runtime/tmp,
uv-cache, venv, daemon-home, plan and artifact paths before `uv`, Python,
plugins, or checkout imports; allowlist the child environment and record
effective Python 3.12/uv/interpreter/architecture, lock, source, Pipeline and
module/plugin origins. Preserve the real pytest exit independently from
cleanup/archive failures and emit bounded cleanup/observer/archive receipts.
Use finite 15m allocation, 15m setup, 30m workload, 5m cleanup and 70m
observation bounds. Process lifetime containment remains a separate F04
feasibility obligation: path ownership, env cookies, timeouts, and numeric
PID/PGID checks do not establish daemon or descendant ownership.
