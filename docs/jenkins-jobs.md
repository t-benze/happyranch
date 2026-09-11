# Ordinary Jenkins job helper

`scripts/jenkins_jobs.py` is standalone standard-library Python for one already-authorized existing Freestyle or Pipeline job. It is not HappyRanch runtime/CLI integration, Jenkins provisioning, a plugin, or a credential store.

## Pinned installation

After PR review, use its immutable repository commit and verify the published file hash before installing an owner-controlled copy. Never run an ephemeral task-worktree copy.

~~~sh
REPO=https://github.com/t-benze/happyranch.git
COMMIT=45cc7765806af615997e0aeef47b2a22695ed241
TOOL_DIR="$HOME/.local/share/happyranch-tools"
git clone "$REPO" /tmp/happyranch-jenkins-source
git -C /tmp/happyranch-jenkins-source show "$COMMIT:scripts/jenkins_jobs.py" > /tmp/jenkins_jobs.py
sha256sum /tmp/jenkins_jobs.py # must be a0a2eb68491fd9bf0dc2569b015ee9399f334bc7c233940b83c0dd49c6569753
install -d -m 700 "$TOOL_DIR"
install -m 700 /tmp/jenkins_jobs.py "$TOOL_DIR/jenkins_jobs.py"
python "$TOOL_DIR/jenkins_jobs.py" --help
~~~

Set only existing authorized credential references: `JENKINS_USERNAME` and `JENKINS_API_TOKEN`. The helper sends preemptive HTTP Basic authentication (username plus API token), does not print/store/provision/rotate either value, and refuses newline header input. HTTPS is default; private HTTP needs explicit authorization and `--allow-http`. This pinned source is a candidate pending independent review, QA, and exact-head CI; it is not an accepted distribution or evidence of a live Jenkins run. Jenkins documents this at [Authenticating scripted clients](https://www.jenkins.io/doc/book/system-administration/authenticating-scripted-clients/) and [User API tokens](https://www.jenkins.io/doc/book/using/using-credentials/).

## Submit, recover, wait, collect, cancel

~~~sh
TOOL="$HOME/.local/share/happyranch-tools/jenkins_jobs.py"
RECEIPT="$HOME/.local/state/happyranch/jenkins/release-001.json"
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" submit --parameter branch=main
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" --deadline 900 wait
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" collect --output "$HOME/jenkins-output"
~~~

The versioned, locked receipt saves intent before POST, then the numeric queue and exact numeric build identity. Reuse it after interruption; never resubmit on transport/5xx/malformed Location/queue 404. For an uncertain submit, use `reconcile --build-number N` only for a separately verified exact build—never `lastBuild`. URLs are exact controller-context/job/numeric identities; redirects, query/userinfo, traversal, off-origin and mismatches are refused before credential forwarding.

Terminal observation requires `building` exactly `false` and a result. `SUCCESS`, `FAILURE`, `UNSTABLE`, `ABORTED`, queue cancellation, timeout, transport uncertainty and unsupported result have truthful outcomes. Collection records every downloaded/skipped/error item; error is nonzero even with Jenkins SUCCESS. Defaults independently limit API JSON (1 MB), console (1 MB), each artifact (10 MB), aggregate (50 MB) and count (32), including slow stream reads. Destinations are no-follow, exclusive-write and collision-refusing.

`cancel` is explicit only: it calls `queue/cancelItem?id=N` or recorded build stop, then observes queue cancellation or a queue-to-build race. Local timeout/interruption is never remote cancellation.

## Ordinary HappyRanch durable job

Use only when existing endpoint/credential authorization permits it:

~~~json
{"task_id":"TASK-EXAMPLE","session_id":"ACTIVE_SESSION","title":"wait existing Jenkins release","script":"python $HOME/.local/share/happyranch-tools/jenkins_jobs.py --controller https://ci.example/jenkins --job folder/release --receipt $HOME/.local/state/happyranch/jenkins/release-001.json --deadline 900 wait","interpreter":"bash","review_required":false,"persistent":true,"max_runtime_seconds":960,"max_output_bytes":65536}
~~~

Submit it with `happyranch jobs submit --from-file ... --org happyranch`, then callback with active task/session, `status: "blocked"`, and the returned `waiting_on_job_ids`; do not poll. On resume inspect `happyranch jobs show` and `happyranch jobs output`, verify command/hash/receipt identity/result/collection manifest, and report the actual outcome.
