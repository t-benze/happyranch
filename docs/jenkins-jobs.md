# Ordinary Jenkins job helper

`scripts/jenkins_jobs.py` is a standalone, standard-library-only operator
helper for an already-authorized, existing Jenkins Freestyle or Pipeline job.
It is not a HappyRanch runtime component, CLI command, scheduler, webhook, or
Jenkins provisioning tool.

## Install a pinned copy

After the reviewed commit is available, verify its exact SHA-256 and copy the
file to an agent-owned persistent tooling directory. Do not run it from an
ephemeral task worktree.

```sh
git show <reviewed-commit>:scripts/jenkins_jobs.py > /tmp/jenkins_jobs.py
sha256sum /tmp/jenkins_jobs.py # must equal the handoff hash
install -m 700 /tmp/jenkins_jobs.py "$HOME/.local/share/happyranch-tools/jenkins_jobs.py"
python "$HOME/.local/share/happyranch-tools/jenkins_jobs.py" --help
```

Use an existing HTTPS controller and an existing API-token reference supplied
through the normal operator environment. The helper reads a token only from the
named environment variable (default `JENKINS_API_TOKEN`), never prints it, and
does not provision, rotate, or store credentials. Jenkins API-token setup and
CSRF requirements remain the administrator's responsibility; see the Jenkins
[Remote access API](https://www.jenkins.io/doc/book/using/remote-access-api/)
and [user API tokens](https://www.jenkins.io/doc/book/using/using-credentials/).

## Submit, reattach, wait, collect, and cancel

Use a fresh receipt path with owner-only permissions. Submission writes durable
intent before the non-idempotent POST; a lost response, 5xx, malformed Location,
or reused receipt must be reconciled with the same receipt, never resubmitted.

```sh
TOOL="$HOME/.local/share/happyranch-tools/jenkins_jobs.py"
RECEIPT="$HOME/.local/state/happyranch/jenkins/request-$(date +%s).json"
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" submit --parameter branch=main
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" --deadline 900 --poll 2 wait
python "$TOOL" --controller https://ci.example/jenkins --job 'folder/release' --receipt "$RECEIPT" show
```

The helper gets job metadata first: it uses `build` for nonparameterized jobs,
`buildWithParameters` for parameterized jobs, accepts only that job's declared
supported parameter types, and rejects undeclared or unsupported parameters
before POST. It validates controller context/origin, queue/build identity URLs,
and refuses redirects before forwarding credentials. Explicit `--allow-http`
is only for separately authorized private-network HTTP.

`wait` reattaches to the recorded queue/build and never submits. It records the
exact build URL before later observation. `SUCCESS` exits 0; `FAILURE`,
`UNSTABLE`, `ABORTED`, `QUEUE_CANCELLED`, timeouts, transport uncertainty, and
unsupported terminal results are distinct non-zero outcomes. A timeout or local
interruption retains identity and does not remotely cancel. `cancel` sends an
explicit queue cancel or build stop for the recorded identity and records only
request acceptance; follow with `wait` to observe a genuine cancellation.

After terminal observation, `collect --output <trusted-dir>` downloads bounded
console output and only artifacts advertised by that exact build. Its receipt
manifest records each download, skip, or error. Defaults cap API JSON at 1 MB,
console text at 1 MB, each artifact at 10 MB, aggregate artifacts at 50 MB, and
artifact count at 32. Keep receipt and collection output in trusted,
non-symlinked directories.

## Normal HappyRanch job invocation

When the existing endpoint/credential authorization permits the operation, run
the helper through a normal durable HappyRanch job with `persistent: true`,
finite `max_runtime_seconds` and `max_output_bytes`, and
`review_required: false`. Bind it to the active task/session and then send the
single-line completion callback with `status: blocked` and
`waiting_on_job_ids`. On resumption, inspect `happyranch jobs show` and
`happyranch jobs output`; verify the exact command, helper SHA-256, recorded
queue/build identity, terminal Jenkins result, and collection manifest. A
rejected or locally failed job is not a Jenkins result.
