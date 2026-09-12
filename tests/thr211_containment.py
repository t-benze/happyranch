"""Test-only filesystem preparation for the THR-211 Jenkins candidate.

This module deliberately creates no process ownership abstraction.  A private
directory can contain a test plan; it cannot identify, reap, or contain a
daemon or an escaped descendant.
"""
from __future__ import annotations

import os
import json
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent


@dataclass(frozen=True)
class PrivateTestPaths:
    """Fresh, private locations passed to fake plans as data."""

    root: Path
    plans: Path
    artifacts: Path


def prepare_private_test_paths(root: Path) -> PrivateTestPaths:
    """Create no-follow private plan/artifact directories below ``root``.

    Existing paths, symlinks, and non-directories fail closed.  The caller
    owns ``root`` (pytest's ``tmp_path`` or Pipeline's fresh workspace).
    """
    parent = root.parent
    # The caller supplies a canonical absolute private boundary (on macOS this
    # means the resolved temporary-directory path, never a /var -> /private/var
    # alias).  Walk every component from its filesystem anchor with O_NOFOLLOW,
    # then create children through pinned descriptors.  This detects existing
    # symlink/non-directory ancestry; it is not hostile same-UID race isolation.
    if root.name in {"", ".", ".."} or ".." in root.parts:
        raise ValueError("private test root needs a simple child name")
    if not parent.is_absolute():
        raise ValueError("private test root must be absolute")
    fds: list[int] = []
    acquired: list[tuple[int, str]] = []
    errors: list[BaseException] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent_fd = os.open(parent.anchor, flags)
        fds.append(parent_fd)
        for component in parent.parts[1:]:
            parent_fd = os.open(component, flags, dir_fd=parent_fd)
            fds.append(parent_fd)
        try:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            raise FileExistsError(f"private test root already exists: {root}") from None
        acquired.append((parent_fd, root.name))
        root_fd = os.open(root.name, flags, dir_fd=parent_fd)
        fds.append(root_fd)
        for child in ("plans", "artifacts"):
            os.mkdir(child, 0o700, dir_fd=root_fd)
            acquired.append((root_fd, child))
            child_fd = os.open(child, flags, dir_fd=root_fd)
            fds.append(child_fd)
            child_stat = os.fstat(child_fd)
            if (not stat.S_ISDIR(child_stat.st_mode) or child_stat.st_mode & 0o077
                    or child_stat.st_uid != os.getuid()):
                raise RuntimeError(f"unsafe private test path: {root / child}")
    except BaseException as error:
        errors.append(error)
        for directory_fd, name in reversed(acquired):
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as cleanup_error:
                cleanup_error.add_note(f"removing acquired {name!r} below {root}")
                errors.append(cleanup_error)
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError as cleanup_error:
                cleanup_error.add_note(f"closing descriptor {fd}; acquired root={root}")
                errors.append(cleanup_error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("private test path acquisition/cleanup failed", errors)
    plans = root / "plans"
    artifacts = root / "artifacts"
    return PrivateTestPaths(root=root, plans=plans, artifacts=artifacts)


def plan_environment(paths: PrivateTestPaths) -> dict[str, str]:
    """Return the narrow plan-path contract; no ambient TMPDIR inference."""
    return {"HAPPYRANCH_TEST_PLAN_DIR": os.fspath(paths.plans)}


def build_review_required_job_script(sentinel: Path) -> str:
    """Return the separately run job body with exclusive private output."""
    return f"umask 077; set -C; : > {shlex.quote(os.fspath(sentinel))}"


def _plan_start(plans: Path) -> str:
    # Each invocation owns only its mktemp directory and registered files.
    # State/rendezvous files outlive an invocation for the test driver's assertions.
    return "#!/usr/bin/env bash\n" + f"plan_dir={shlex.quote(str(plans))}\n" + dedent('''\
        set -eu
        umask 077
        task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"
        run_dir=""
        owned=()
        cleanup() {
            primary=$?
            trap - EXIT HUP INT TERM
            set +e
            secondary=0
            for path in ${owned[@]+"${owned[@]}"}; do
                rm -f -- "$path"
                code=$?
                if [ "$code" -ne 0 ]; then
                    printf 'cleanup file %s exit=%s primary=%s\n' "$path" "$code" "$primary" >&2
                    secondary=1
                fi
            done
            if [ -n "$run_dir" ]; then
                rmdir -- "$run_dir"
                code=$?
                if [ "$code" -ne 0 ]; then
                    printf 'cleanup directory %s exit=%s primary=%s\n' "$run_dir" "$code" "$primary" >&2
                    secondary=1
                fi
            fi
            if [ "$primary" -ne 0 ]; then exit "$primary"; fi
            exit "$secondary"
        }
        trap cleanup EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM
        run_dir=$(mktemp -d "$plan_dir/invocation-XXXXXX")
        new_file() {
            local created
            created=$(mktemp "$run_dir/$2-XXXXXX")
            owned+=("$created")
            printf -v "$1" '%s' "$created"
        }
    ''')


def _json_file(variable: str, payload: dict, **fields: str | tuple[str, ...]) -> str:
    """Serialize runtime strings as JSON data, including nested job-id lists."""
    program = "import json,sys; p=json.loads(sys.argv[1]); "
    arguments = [shlex.quote(json.dumps(payload))]
    index = 2
    for key, value in fields.items():
        if isinstance(value, tuple):
            program += f"p[{key!r}]=sys.argv[{index}:{index + len(value)}]; "
            arguments.extend(value)
            index += len(value)
        else:
            program += f"p[{key!r}]=sys.argv[{index}]; "
            arguments.append(value)
            index += 1
    program += "json.dump(p,sys.stdout)"
    return f"python3 -c {shlex.quote(program)} {' '.join(arguments)} > \"${variable}\"\n"


def _completion(payload: dict) -> str:
    return 'new_file report completion\n' + _json_file(
        "report", payload, task_id='"$task_id"', session_id='"$session_id"', agent='"$agent"',
    ) + 'happyranch report-completion --from-file "$report" --org "$org_slug"\n'


def _submit(payload: dict, suffix: str = "") -> str:
    return f"new_file payload{suffix} submit{suffix}\nnew_file log{suffix} log{suffix}\n" + _json_file(
        f"payload{suffix}", payload, task_id='"$task_id"', session_id='"$session_id"',
    ) + (
        f'happyranch jobs submit --from-file "$payload{suffix}" --org "$org_slug" > "$log{suffix}" 2>&1 || {{ code=$?; cat "$log{suffix}" >&2; exit "$code"; }}\n'
        f'cat "$log{suffix}" >&2\n'
        f'job{suffix}=$(grep -oE "JOB-[0-9]+" "$log{suffix}" | head -1)\n'
        f'[ -n "$job{suffix}" ] || {{ echo "ERROR: could not parse JOB id" >&2; exit 1; }}\n'
    )


def _counter(path: Path) -> str:
    # Open without following a symlink; never truncate before checking the file.
    program = dedent('''\
        import os, stat, sys
        path = sys.argv[1]
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r+') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise RuntimeError('unsafe plan counter')
            n = int(stream.read() or '0') + 1
            stream.seek(0)
            stream.write(str(n) + '\\n')
            stream.truncate()
        print(n)
    ''')
    return f"n=$(python3 -c {shlex.quote(program)} {shlex.quote(str(path))})\n"


def build_threads_plan(plans: Path) -> str:
    return _plan_start(plans) + 'new_file payload compose\n' + _json_file("payload", {
        "composer": "engineering_head", "subject": "int test loop in",
        "recipients": ["payment_agent"], "body_markdown": "looping payment_agent in",
    }) + (
        'happyranch threads compose --org "$org_slug" --task-id "$task_id" --session-id "$session_id" --from-file "$payload" >&2\n'
    ) + _completion({"status": "completed", "confidence": 90, "summary": "composed thread"})


def build_thread_reply_plan(plans: Path) -> str:
    # Same positional ABI as fake_claude's thread plan: thread, token, agent, org, purpose.
    return _plan_start(plans) + 'new_file payload thread-reply\n' + _json_file("payload", {
        "body_markdown": "got it", "in_response_to_seq": 1,
    }, thread_id='"$task_id"', invocation_token='"$session_id"', speaker='"$agent"') + (
        'happyranch threads reply --org "$org_slug" --thread-id "$task_id" --from-file "$payload" >&2\n'
    )


def build_review_required_plan(plans: Path) -> str:
    return _plan_start(plans) + _submit({
        "title": "touch e2e sentinel", "rationale": "integration test needs founder approval",
        "script": build_review_required_job_script(plans / "happyranch-job-e2e-sentinel"),
        "interpreter": "bash", "review_required": True,
    }) + 'new_file report completion\n' + _json_file("report", {
        "status": "blocked", "confidence": 50, "risks_flagged": [],
        "dependencies": [], "suggested_reviewer_focus": [],
    }, task_id='"$task_id"', session_id='"$session_id"', agent='"$agent"', summary='"Awaiting $job"') + (
        'happyranch report-completion --from-file "$report" --org "$org_slug"\n'
    )


def build_persistent_plan(plans: Path) -> str:
    submitted = shlex.quote(str(plans / "job-submitted.txt"))
    stopped = shlex.quote(str(plans / "founder-acted.txt"))
    return _plan_start(plans) + (
        f'submitted={submitted}\nstopped={stopped}\n'
        '[ ! -e "$submitted" ] && [ ! -L "$submitted" ] || exit 1\n'
        '[ ! -e "$stopped" ] && [ ! -L "$stopped" ] || exit 1\n'
    ) + _submit({
        "title": "persistent dev loop", "rationale": "long-running background task",
        "script": "echo starting; sleep 60", "interpreter": "bash",
        "review_required": False, "persistent": True,
    }) + '(' + build_review_required_job_script(plans / "job-submitted.txt") + ')\n' + dedent('''\
        ready=0
        for ((i=0; i<600; i++)); do
            [ ! -L "$stopped" ] || exit 1
            if [ -f "$stopped" ]; then ready=1; break; fi
            sleep 0.1
        done
        [ "$ready" = 1 ] || { echo "ERROR: founder sentinel timeout" >&2; exit 1; }
    ''') + _completion({
        "status": "completed", "summary": '{"action":"done","summary":"loop launched, founder stopped"}',
        "confidence": 90, "risks_flagged": [], "dependencies": [], "suggested_reviewer_focus": [],
    })


def _blocked_completion(multi: bool) -> str:
    jobs = ('"$job_a"', '"$job_b"') if multi else ('"$job"',)
    summary = '"Waiting for $job_a and $job_b before proceeding."' if multi else '"Waiting for $job to finish before proceeding."'
    # Retain the original direct-HTTP protocol payload (no task_id field).
    return 'new_file report completion\n' + _json_file("report", {
        "status": "blocked", "confidence": 0, "risks_flagged": [],
        "dependencies": [], "suggested_reviewer_focus": [],
    }, session_id='"$session_id"', agent='"$agent"', output_summary=summary, waiting_on_job_ids=jobs) + dedent('''\
        port=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.port")
        token=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.token")
        curl -fsS -X POST "http://127.0.0.1:$port/api/v1/orgs/$org_slug/tasks/$task_id/completion" -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d @"$report" >&2
    ''')


def build_blocked_multi_plan(plans: Path) -> str:
    script = _plan_start(plans) + _counter(plans / "invocation_counter") + 'if [ "$n" = 1 ]; then\n'
    for suffix, word in (("a", "first"), ("b", "second")):
        script += _submit({
            "title": f"multi-job e2e job {suffix.upper()}",
            "rationale": f"{word} of two jobs — multi-job integration test",
            "script": f"echo job-{suffix}-ran", "interpreter": "bash",
            "review_required": True, "persistent": False,
        }, f"_{suffix}")
        script += f'(set -C; printf "%s\\n" "$job_{suffix}" > {shlex.quote(str(plans / f"job_{suffix}.id"))})\n'
    return script + _blocked_completion(True) + 'else\n' + _completion({
        "status": "completed", "confidence": 90,
        "summary": '{"action":"done","summary":"completed after both jobs unblocked"}',
    }) + 'fi\n'


def build_blocked_autonomous_plan(plans: Path) -> str:
    return _plan_start(plans) + _counter(plans / "invocation_counter") + 'if [ "$n" = 1 ]; then\n' + _submit({
        "title": "autonomous e2e job", "rationale": "auto-run integration test",
        "script": "echo autonomous-job-ran", "interpreter": "bash",
        "review_required": False, "persistent": False,
    }) + _blocked_completion(False) + 'else\n' + _completion({
        "status": "completed", "confidence": 90,
        "summary": '{"action":"done","summary":"completed after job unblock"}',
    }) + 'fi\n'


def build_content_plan(plans: Path) -> str:
    script = _plan_start(plans) + 'case "$agent" in\ncontent_manager)\n' + _counter(plans / "cm_step.txt")
    for n, summary, decision in (
        (1, "delegating to writer", {"action": "delegate", "agent": "content_writer", "prompt": "Write a comprehensive Macau visa guide for UK tourists"}),
        (2, "delegating to QA", {"action": "delegate", "agent": "content_qa", "prompt": "Review the draft"}),
        (3, "content approved", {"action": "done", "summary": "content approved"}),
    ):
        script += ('if [ "$n" = 1 ]; then\n' if n == 1 else 'elif [ "$n" = 2 ]; then\n' if n == 2 else 'else\n')
        script += _completion({"status": "completed", "summary": summary, "confidence": 90, "decision": decision})
    script += 'fi\n;;\n'
    for agent, summary, confidence in (("content_writer", "Draft completed", 85), ("content_qa", "VERDICT: PASS - content is accurate", 90)):
        script += agent + ')\n' + _completion({"status": "completed", "summary": summary, "confidence": confidence}) + ';;\n'
    return script + '*) echo "Unknown agent: $agent" >&2; exit 1;;\nesac\n'
