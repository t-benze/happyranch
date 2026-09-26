"""THR-259 / TASK-8667 causal tests for the shared workspace-cleanup skill.

Contract-text checks plus behavioral resolver/materialization checks: the ONE
shared daily/manual skill must be a TASK system contract that does not require a
repository, must materialize into BOTH provider roots, and must carry the exact
dispatch markers, own-workspace scope, inventory-only rule, approved seq185
exception rule, and non-force mechanisms.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import materialize_workspace_skills
from runtime.skills.skill_md import frontmatter_admission_violations
from runtime.skills.sources import bundled_skills_dir
from runtime.skills.system_contracts import (
    SessionContext,
    list_system_contracts,
    resolve_system_contracts_for_session,
)

SKILL_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime" / "skills" / "bundled" / "workspace-cleanup"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
HELPER = SKILL_DIR / "scripts" / "check_path_use.py"
MANUAL_FIRST_LINE = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"
DAEMON_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"


@pytest.fixture(scope="module")
def body() -> str:
    assert SKILL_MD.is_file(), f"missing packaged skill: {SKILL_MD}"
    return SKILL_MD.read_text(encoding="utf-8")


def test_frontmatter_is_admissible_and_named_for_slug(body):
    assert frontmatter_admission_violations(body) == []
    assert body.startswith("---\nname: workspace-cleanup\n")


def test_helper_is_present_in_skill_package():
    assert HELPER.is_file()
    assert HELPER.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


def test_manual_and_daemon_dispatch_markers(body):
    lines = {ln.strip() for ln in body.splitlines()}
    assert MANUAL_FIRST_LINE in lines
    assert DAEMON_MARKER in lines


def test_own_workspace_scope_and_inventory_only(body):
    normalized = " ".join(body.split())
    assert "inventory-only" in normalized
    assert "your own workspace" in normalized
    assert "Never touch another agent's workspace" in normalized
    assert "Never use elevation" in normalized


def test_non_force_only_and_no_broad_deletion(body):
    normalized = " ".join(body.split())
    assert "worktree remove" in normalized
    assert "Never** `--force`" in normalized or "Never `--force`" in normalized
    assert "rm -rf" in normalized
    # R6: the no-production-residue prohibition is scoped to THIS implementation
    # and witness task, not a permanent veto on the skill's future authorized use.
    assert "implementation and witness task" in normalized
    assert "deletes no production residue" in normalized
    assert "never deletes production residue" not in normalized


def test_approved_exception_names_and_roles(body):
    normalized = " ".join(body.split())
    for name in ("sshd-session", "systemd", "sd-pam", "ssh-agent",
                 "gpg-agent", "gcr-ssh-agent"):
        assert name in normalized
    assert "exact readable process name AND its exact bounded cgroup" in normalized
    assert "deliberately" in normalized and "not inspected" in normalized


def test_confirmed_exit_contract_distinguishes_absent_stat_from_incomplete_status(body):
    normalized = " ".join(body.split())
    assert (
        "An independently absent PID/TID `stat` confirms that sampled process "
        "or thread exited."
    ) in normalized
    assert (
        "If the corresponding `stat` remains readable, a missing, unreadable, "
        "or incomplete `status` is an incomplete identity -> `unknown`, not "
        "confirmed exit."
    ) in normalized
    assert "A missing per-thread `stat`/`status`" not in normalized


def test_retention_and_ordinal_contract(body):
    normalized = " ".join(body.split())
    assert "first **two**" in normalized
    assert "24 hours" in normalized
    assert "seven terminal days" in normalized
    assert "zero" in normalized


def test_helper_reference_is_relative_to_skill(body):
    assert "scripts/check_path_use.py" in body


def test_effectiveness_contract_uses_complete_history_and_exact_host_job(body):
    procedure = _shipped_procedure(body)
    assert "happyranch audit" in procedure and "--all-pages" in procedure
    assert "happyranch jobs submit" in procedure
    assert "happyranch jobs wait" in procedure
    assert "happyranch jobs show" in procedure
    assert "happyranch jobs output" in procedure
    assert "clear_observation" in procedure
    assert "scan_receipt_mismatch" in procedure
    assert "# gate current-use-scan\n_wc_scan_job" in _shipped_gate_text(body)


def test_effectiveness_contract_is_zsh_safe_and_preserves_dirty_cache_only(body):
    procedure = _shipped_procedure(body)
    assert "local owner status" not in procedure
    assert "task_status=" in procedure
    assert 'case "$task_status"' in procedure
    assert "_wc_is_cache" in procedure
    assert "dirty whole-worktree" in " ".join(body.split()).lower()
    assert "Source bytes and Git status" in body


def test_effectiveness_contract_names_remote_and_merged_preservation(body):
    normalized = " ".join(body.split())
    assert "freshly verified matching remote task branch" in normalized
    assert "confirmed merged PR" in normalized
    assert "may not preserve the original commit topology" in normalized
    assert "closed-unmerged" in normalized


# ── Behavioral resolver/materialization ───────────────────────────────────

def test_resolver_includes_workspace_cleanup_without_repos(tmp_path):
    workspace = tmp_path / "ws_no_repos"
    workspace.mkdir()
    resolved = resolve_system_contracts_for_session(
        SessionContext.TASK, workspace=workspace,
    )
    by_id = {sc.id: sc for sc in resolved}
    assert "workspace-cleanup" in by_id
    assert by_id["workspace-cleanup"].requires_repo is False
    assert by_id["workspace-cleanup"].source_path == (
        "runtime/skills/bundled/workspace-cleanup/SKILL.md"
    )
    # not exposed to non-task contexts
    for context in (SessionContext.THREAD, SessionContext.DREAM,
                    SessionContext.BOOTSTRAP):
        others = {sc.id for sc in resolve_system_contracts_for_session(
            context, workspace=workspace)}
        assert "workspace-cleanup" not in others


def test_declared_contract_source_matches_release(body):
    contracts = {sc.id: sc for sc in list_system_contracts()}
    contract = contracts["workspace-cleanup"]
    source = bundled_skills_dir() / contract.id / "SKILL.md"
    assert source.is_file()
    assert source.read_text(encoding="utf-8") == body


def test_materializes_into_both_provider_roots_for_no_repo_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    materialize_workspace_skills(
        workspace,
        Settings(),
        slug="test",
        context="task",
        provider="codex",
        agent_name="dev_agent",
        team="engineering",
        skills_root=tmp_path / "no-managed-skills",
    )
    expected = SKILL_MD.read_text(encoding="utf-8")
    for root in (workspace / ".claude" / "skills", workspace / ".agents" / "skills"):
        marker = root / "workspace-cleanup" / "SKILL.md"
        assert marker.is_file(), f"workspace-cleanup not materialized at {marker}"
        assert marker.read_text(encoding="utf-8") == expected
    # no-repo workspace must not receive repo-only contracts
    assert not (workspace / ".agents" / "skills" / "make-worktree").exists()


# ── F5: behavioral execution of the DELIVERED procedure ───────────────────
#
# These tests extract the shipped ``procedure-commands`` control flow out of the
# real SKILL.md and execute it against a real isolated Git fixture with labelled
# synthetic authoritative-response fixtures (happyranch recall/audit, gh). The
# task-output ``eligibility.py`` is not consulted and no test-only copy of the
# decision algorithm is used: the shipped shell runs, and the shipped helper is
# the real one.
#
# Per the controlling repair brief, an unknown REAL scan is a refusal test and
# never a positive control. On a sandboxed host whose PID1 is not the host init
# the shipped helper correctly returns ``unknown``; the positive removal control
# is therefore the separate non-elevated host witness, while these tests prove
# that every refusal branch (including an unknown scan) issues no mutation.

PROC_BEGIN = "<!-- procedure-commands:begin -->"
PROC_END = "<!-- procedure-commands:end -->"
ELIG_BEGIN = "<!-- eligibility-commands:begin -->"
ELIG_END = "<!-- eligibility-commands:end -->"
G = ["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture"]

OLD = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 30 * 86400))


def _shipped_procedure(body: str) -> str:
    assert PROC_BEGIN in body and PROC_END in body
    block = body[body.index(PROC_BEGIN):body.index(PROC_END)]
    match = re.search(r"```bash\n(.*?)```", block, re.S)
    assert match, "no bash procedure block in SKILL.md"
    return match.group(1)


def _shipped_gate_text(body: str) -> str:
    assert ELIG_BEGIN in body and ELIG_END in body
    block = body[body.index(ELIG_BEGIN):body.index(ELIG_END)]
    match = re.search(r"```bash\n(.*?)```", block, re.S)
    assert match, "no bash eligibility block in SKILL.md"
    return match.group(1)


def test_procedure_uses_the_documented_gate_commands(body):
    # The executable procedure extracts its gates from the documented block, so
    # the two can never drift: assert the procedure references the block markers
    # and the documented block still carries every gate name.
    procedure = _shipped_procedure(body)
    assert "eligibility-commands:begin" in procedure
    assert "eligibility-commands:end" in procedure
    names = re.findall(r"^# gate (.+)$", _shipped_gate_text(body), re.M)
    assert len(names) >= 12, names
    for required in ("workspace-scope", "canonical-shape", "non-primary", "registration",
                     "ownership", "filesystem-ownership", "protected-task",
                     "not-symlink", "same-filesystem", "clean",
                     "durable-preservation-and-pr", "retention-age",
                     "current-use-scan"):
        assert required in names


def _shipped_gate_map(body: str) -> dict:
    gates: dict[str, list[str]] = {}
    current = None
    for raw in _shipped_gate_text(body).splitlines():
        line = raw.strip()
        if line.startswith("# gate "):
            current = line[len("# gate "):].strip()
            gates[current] = []
        elif current and line:
            gates[current].append(line)
    return {name: "\n".join(lines) for name, lines in gates.items()}


def _run_shipped_gate(body, name, env):
    gates = _shipped_gate_map(body)
    return subprocess.run(["bash", "-c", gates[name]], env=env,
                          capture_output=True, text=True)


def test_r5_ownership_and_retention_gates_use_authoritative_facts(body, tmp_path):
    # R5: the literal gates must establish their named facts from the
    # authoritative task record, never from a directory mtime or shared OS UID.
    ts_old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                           time.gmtime(time.time() - 30 * 86400))
    ts_young = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                             time.gmtime(time.time() - 3600))
    ts_mid = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                           time.gmtime(time.time() - 3 * 86400))
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"assigned_agent": "dev_agent",
                                "status": "completed", "completed_at": ts_old}))
    young = tmp_path / "young.json"
    young.write_text(json.dumps({"assigned_agent": "dev_agent",
                                 "status": "completed", "completed_at": ts_young}))
    nonterm = tmp_path / "nonterm.json"
    nonterm.write_text(json.dumps({"assigned_agent": "dev_agent",
                                   "status": "in_progress",
                                   "completed_at": None}))
    base = dict(os.environ, AGENT="dev_agent")

    def run(name, task_json, age=None):
        env = dict(base, TASK_JSON=str(task_json))
        if age is not None:
            env["AGE_SECONDS"] = str(age)
        return _run_shipped_gate(body, name, env)

    assert run("ownership", good).returncode == 0
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"assigned_agent": "someone_else",
                                 "status": "completed", "completed_at": ts_old}))
    assert run("ownership", other).returncode != 0
    assert run("retention-age", good, age=86400).returncode == 0
    mid = tmp_path / "mid.json"
    mid.write_text(json.dumps({"assigned_agent": "dev_agent",
                               "status": "completed", "completed_at": ts_mid}))
    assert run("retention-age", mid, age=86400).returncode == 0
    assert run("retention-age", mid, age=7 * 86400).returncode != 0
    assert run("retention-age", young, age=86400).returncode != 0
    assert run("retention-age", nonterm, age=86400).returncode != 0


def test_r5_preservation_gate_calls_fresh_remote_and_all_pr_states(body):
    procedure = _shipped_procedure(body)
    assert 'ls-remote --exit-code origin "refs/heads/$branch"' in procedure
    assert 'gh pr list --repo "$repo_slug" --head "$branch" --state all' in procedure
    assert "mergedAt" in procedure and "headRefOid" in procedure


def _git(*args, cwd=None):
    result = subprocess.run(G + list(args), cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _build_procedure_fixture(root: Path) -> dict:
    workspace = root / "ws"
    primary = workspace / "repos" / "demo"
    primary.mkdir(parents=True)
    origin = root / "origin.git"
    _git("init", "--bare", "-b", "main", str(origin))
    _git("init", "-b", "main", str(primary))
    (primary / "README.md").write_text("fixture\n")
    (primary / ".gitignore").write_text("node_modules/\n.venv/\n")
    (primary / "package-lock.json").write_text("{}\n")
    _git("add", "-A", cwd=primary)
    _git("commit", "-m", "base", cwd=primary)
    _git("remote", "add", "origin", str(origin), cwd=primary)
    _git("push", "-u", "origin", "main", cwd=primary)
    # Bind the owning repository for the gh PR lookup while keeping origin/main
    # reachable (the remote-tracking ref already exists locally).
    _git("remote", "set-url", "origin",
         "https://github.com/demo/fixture.git", cwd=primary)
    wt_root = primary / ".claude" / "worktrees"
    wt_root.mkdir(parents=True)

    def add_wt(name: str, branch: str) -> Path:
        path = wt_root / name
        _git("worktree", "add", "-b", branch, str(path), "origin/main", cwd=primary)
        return path

    eligible = add_wt("TASK-ELIGIBLE", "task/TASK-ELIGIBLE")
    dirty = add_wt("TASK-DIRTY", "task/TASK-DIRTY")
    (dirty / "scratch.txt").write_text("uncommitted\n")
    alias = workspace / "repos-alias"
    alias.symlink_to(workspace / "repos")
    return {"workspace": workspace, "primary": primary, "origin": origin,
            "eligible": eligible, "dirty": dirty, "alias": alias}


def _write_stubs(bin_dir: Path) -> None:
    hr = bin_dir / "happyranch"
    hr.write_text(
        "#!/bin/sh\n"
        "last=\"\"\n"
        "for a in \"$@\"; do last=\"$a\"; done\n"
        "case \"$1\" in\n"
        "  recall)\n"
        "    exec python3 -c 'import json,os,sys\n"
        "m=json.load(open(os.environ[\"WC_TASK_MAP\"]))\n"
        "d=m.get(sys.argv[1], {\"error\": \"missing\"})\n"
        "if isinstance(d,dict) and d.get(\"task_id\") is None: d=dict(d); d[\"task_id\"]=sys.argv[1]\n"
        "print(json.dumps(d))' \"$last\" ;;\n"
        "  tasks)\n"
        "    [ \"${WC_TASKS_FAIL:-0}\" = \"1\" ] && exit 1\n"
        "    n=$(cat \"$WC_TASKS_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_TASKS_COUNT\"\n"
        "    if [ \"$n\" -gt 1 ] && [ -n \"$WC_TASKS_SECOND\" ]; then exec cat \"$WC_TASKS_SECOND\"; fi\n"
        "    exec cat \"$WC_TASKS_FIRST\" ;;\n"
        "  audit)\n"
        "    [ \"${WC_AUDIT_FAIL:-0}\" = \"1\" ] && exit 1\n"
        "    n=$(cat \"$WC_TRIGGER_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_TRIGGER_COUNT\"\n"
        "    first=\"$WC_AUDIT_TRIGGER\"; second=\"$WC_AUDIT_TRIGGER_SECOND\"\n"
        "    if [ \"$n\" -gt 1 ] && [ -n \"$second\" ]; then exec cat \"$second\"; fi\n"
        "    exec cat \"$first\" ;;\n"
        "  jobs)\n"
        "    case \"$2\" in\n"
        "      submit)\n"
        "        [ \"$WC_JOB_SCENARIO\" = rejected ] && exit 1\n"
        "        cp \"$last\" \"$WC_JOB_PAYLOAD\"\n"
        "        echo 'ok: submitted JOB-1 (status=completed). Self-block your task referencing this ID.' ;;\n"
        "      wait)\n"
        "        case \"$WC_JOB_SCENARIO\" in\n"
        "          timeout) echo '{\"status\":\"running\",\"timed_out\":true}' ;;\n"
        "          failed) echo '{\"status\":\"failed\",\"timed_out\":false}' ;;\n"
        "          *) echo '{\"status\":\"completed\",\"timed_out\":false}' ;;\n"
        "        esac ;;\n"
        "      show)\n"
        "        exec python3 -c 'import datetime,json,os\n"
        "p=json.load(open(os.environ[\"WC_JOB_PAYLOAD\"])); scenario=os.environ[\"WC_JOB_SCENARIO\"]\n"
        "created=(\"2000-01-01T00:00:00+00:00\" if scenario==\"stale\" else datetime.datetime.now(datetime.timezone.utc).isoformat())\n"
        "task=(\"TASK-WRONG\" if scenario==\"mismatched\" else p[\"task_id\"])\n"
        "exit_code=(3 if scenario==\"completed_nonzero\" else 0)\n"
        "print(\"JOB-1   completed   submitted \"+created); print(\"Agent:        dev_agent\"); print(\"Task:         \"+task); print(\"Interpreter:  \"+p[\"interpreter\"]); print(\"Cwd hint:     (workspace root)\"); print(); print(\"Title:        \"+p[\"title\"]); print(); print(\"Rationale:\"); print(\"  \"+p[\"rationale\"]); print(); print(\"Script:\"); print(\"  \"+p[\"script\"].rstrip()); print(); print(\"Exit code:    \"+str(exit_code))' ;;\n"
        "      output)\n"
        "        echo scan >> \"$WC_SCAN_LOG\"\n"
        "        [ \"$WC_JOB_SCENARIO\" = output_cap ] && exit 1\n"
        "        echo '--- stdout ---'\n"
        "        case \"$WC_JOB_SCENARIO\" in\n"
        "          malformed) echo 'not-json' ;;\n"
        "          missing_output) : ;;\n"
        "          use) printf '{\"state\":\"blocked\",\"target\":\"%s\"}\\n' \"$WC_REAL_CANDIDATE\" ;;\n"
        "          unknown) printf '{\"state\":\"unknown\",\"target\":\"%s\"}\\n' \"$WC_REAL_CANDIDATE\" ;;\n"
        "          output_mismatch) printf '{\"state\":\"clear_observation\",\"target\":\"/wrong\"}\\n' ;;\n"
        "          *) printf '{\"state\":\"clear_observation\",\"target\":\"%s\"}\\n' \"$WC_REAL_CANDIDATE\" ;;\n"
        "        esac\n"
        "        echo '--- stderr ---' ;;\n"
        "      *) exit 1 ;;\n"
        "    esac ;;\n"
        "  *) echo 'unsupported' >&2; exit 1 ;;\n"
        "esac\n")
    hr.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "echo \"gh $*\" >> \"$GH_LOG\"\n"
        "[ \"${WC_GH_FAIL:-0}\" = \"1\" ] && exit 71\n"
        "repo=\"\"; prev=\"\"\n"
        "for a in \"$@\"; do [ \"$prev\" = \"--repo\" ] && repo=\"$a\"; prev=\"$a\"; done\n"
        "[ -n \"$repo\" ] || { echo 'missing --repo' >&2; exit 1; }\n"
        "case \"${WC_PR_SCENARIO:-none}\" in\n"
        "  none) echo '[]' ;;\n"
        "  open) printf '[{\"number\":1,\"state\":\"OPEN\",\"mergedAt\":null,\"headRefName\":\"%s\",\"headRefOid\":\"%s\"}]\\n' \"$WC_BRANCH\" \"$WC_HEAD\" ;;\n"
        "  closed) printf '[{\"number\":1,\"state\":\"CLOSED\",\"mergedAt\":null,\"headRefName\":\"%s\",\"headRefOid\":\"%s\"}]\\n' \"$WC_BRANCH\" \"$WC_HEAD\" ;;\n"
        "  merged) printf '[{\"number\":1,\"state\":\"MERGED\",\"mergedAt\":\"2026-01-01T00:00:00Z\",\"headRefName\":\"%s\",\"headRefOid\":\"%s\"}]\\n' \"$WC_BRANCH\" \"$WC_HEAD\" ;;\n"
        "  mismatch) printf '[{\"number\":1,\"state\":\"MERGED\",\"mergedAt\":\"2026-01-01T00:00:00Z\",\"headRefName\":\"%s\",\"headRefOid\":\"0000000000000000000000000000000000000000\"}]\\n' \"$WC_BRANCH\" ;;\n"
        "  malformed) echo '{}' ;;\n"
        "esac\n")
    gh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        "echo \"git $*\" >> \"$GIT_LOG\"\n"
        "case \"$*\" in *\"${WC_GIT_FAIL_MATCH:-__never__}\"*) exit 71;; esac\n"
        "case \"$*\" in *\" ls-remote --exit-code origin \"*)\n"
        "  case \"${WC_REMOTE_SCENARIO:-missing}\" in\n"
        "    success) printf '%s\\trefs/heads/%s\\n' \"$WC_HEAD\" \"$WC_BRANCH\"; exit 0 ;;\n"
        "    mismatch) printf '%040d\\trefs/heads/%s\\n' 0 \"$WC_BRANCH\"; exit 0 ;;\n"
        "    malformed) echo malformed; exit 0 ;;\n"
        "    fail) exit 71 ;;\n"
        "    *) exit 2 ;;\n"
        "  esac ;;\n"
        "esac\n"
        "exec /usr/bin/git \"$@\"\n")
    git.chmod(0o755)


def _run_procedure(tmp_path: Path, body: str, fx: dict, bin_dir: Path, *,
                   marker: str | None, agent: str = "dev_agent",
                   task_map: dict | None = None, audit=None,
                   audit_all=None, audit_trigger=None,
                   audit_all_second=None, audit_trigger_second=None,
                   audit_fail: bool = False, scan_state: str = "unknown",
                   candidate: Path, containing: Path,
                   acting: str = "TASK-ACTING", open_pr: int = 0,
                   gh_fail: bool = False, git_fail_match: str = "",
                   task_map_second: dict | None = None,
                   job_scenario: str | None = None,
                   remote_scenario: str = "missing",
                   pr_scenario: str | None = None,
                   tasks_fail: bool = False,
                   shell: str = "bash"):
    task_map = task_map if task_map is not None else {}
    audit = audit if audit is not None else []
    audit_all = audit if audit_all is None else audit_all
    audit_trigger = audit if audit_trigger is None else audit_trigger
    audit_all_second = audit_all if audit_all_second is None else audit_all_second
    audit_trigger_second = (
        audit_trigger if audit_trigger_second is None else audit_trigger_second
    )
    def task_rows(values):
        rows = []
        for task_id, value in values.items():
            row = dict(value, task_id=task_id)
            if task_id == containing.name:
                row["brief"] = "ordinary owner task"
            rows.append(row)
        return rows

    (tmp_path / "task-map.json").write_text(json.dumps(task_map))
    for name, payload in (
        ("tasks-first.json", task_rows(task_map)),
        ("tasks-second.json", task_rows(task_map_second or task_map)),
        ("audit-trigger.json", audit_trigger),
        ("audit-trigger-second.json", audit_trigger_second),
    ):
        (tmp_path / name).write_text(json.dumps(payload))
    (tmp_path / "tasks-count").write_text("0\n")
    (tmp_path / "trigger-count").write_text("0\n")
    proc_src = tmp_path / "proc.sh"
    proc_src.write_text(_shipped_procedure(body))
    fixture_skill = tmp_path / "shipped-skill"
    (fixture_skill / "scripts").mkdir(parents=True, exist_ok=True)
    (fixture_skill / "SKILL.md").write_text(body)
    scan_helper = fixture_skill / "scripts" / "check_path_use.py"
    scan_helper.write_text(
        "import json,os,sys\n"
        "with open(os.environ['WC_SCAN_LOG'],'a') as fh: fh.write('scan\\n')\n"
        "state=os.environ['WC_SCAN_STATE']\n"
        "print(json.dumps({'state':state,'reasons':[]}))\n"
        "raise SystemExit(0 if state=='clear_observation' else (3 if state=='blocked' else 2))\n"
    )
    wc_tmp = tmp_path / "wc-tmp"
    wc_tmp.mkdir(exist_ok=True)
    git_log = tmp_path / "git.log"
    git_log.write_text("")
    gh_log = tmp_path / "gh.log"
    gh_log.write_text("")
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        "CLEANUP_MARKER": marker or "",
        "WORKSPACE": str(fx["workspace"]),
        "PRIMARY": str(fx["primary"]),
        "AGENT": agent,
        "ORG": "test-org",
        "SKILL": str(fixture_skill),
        "ACTING_TASK": acting,
        "SESSION_ID": "sess-fixture",
        "GIT_LOG": str(git_log),
        "GH_LOG": str(gh_log),
        "WC_TASK_MAP": str(tmp_path / "task-map.json"),
        "WC_TASKS_FIRST": str(tmp_path / "tasks-first.json"),
        "WC_TASKS_SECOND": str(tmp_path / "tasks-second.json"),
        "WC_TASKS_COUNT": str(tmp_path / "tasks-count"),
        "WC_TASKS_FAIL": "1" if tasks_fail else "0",
        "WC_AUDIT_TRIGGER": str(tmp_path / "audit-trigger.json"),
        "WC_AUDIT_TRIGGER_SECOND": str(tmp_path / "audit-trigger-second.json"),
        "WC_TRIGGER_COUNT": str(tmp_path / "trigger-count"),
        "WC_AUDIT_FAIL": "1" if audit_fail else "0",
        "WC_SCAN_LOG": str(tmp_path / "scan.log"),
        "WC_JOB_PAYLOAD": str(tmp_path / "job-payload.json"),
        "WC_JOB_SCENARIO": job_scenario or scan_state,
        "WC_REAL_CANDIDATE": str(candidate.resolve()),
        "AGE_SECONDS": (
            "86400" if candidate.name in ("node_modules", ".venv") else "604800"
        ),
        "WC_PR_SCENARIO": pr_scenario or ("open" if open_pr else "none"),
        "WC_REMOTE_SCENARIO": remote_scenario,
        "WC_BRANCH": f"task/{containing.name}",
        "WC_HEAD": _git("rev-parse", "HEAD", cwd=containing).stdout.strip(),
        "WC_GH_FAIL": "1" if gh_fail else "0",
        "WC_GIT_FAIL_MATCH": git_fail_match,
        "TMPDIR": str(wc_tmp),
    })
    script = (
        f'. {shlex.quote(str(proc_src))}\n'
        f'run_cleanup_candidate {shlex.quote(str(candidate))} '
        f'{shlex.quote(str(containing))}\n'
        'printf "RC=%s\\n" "$?"\n'
    )
    result = subprocess.run([shell, "-c", script], env=env,
                            capture_output=True, text=True)
    rc_line = [ln for ln in result.stdout.splitlines() if ln.startswith("RC=")]
    rc = int(rc_line[-1].split("=", 1)[1]) if rc_line else None
    return {"rc": rc, "stdout": result.stdout, "stderr": result.stderr,
            "git_log": git_log.read_text(), "gh_log": gh_log.read_text()}


def _occurrences(*task_ids: str) -> list[dict]:
    return [{"id": i, "task_id": tid, "agent": "dev_agent",
             "action": "workspace_cleanup_triggered"}
            for i, tid in enumerate(task_ids, 1)]


def _terminal_task(agent: str, *, age_days: int = 30,
                   marker: str = DAEMON_MARKER) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                       time.gmtime(time.time() - age_days * 86400))
    return {"assigned_agent": agent, "status": "completed", "completed_at": ts,
            "brief": marker + "\nfixture", "task_id": None}


def test_f5_procedure_refuses_without_marker_and_never_mutates(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    before = _git("worktree", "list", "--porcelain", cwd=fx["primary"]).stdout
    for marker in (None, "SOMETHING ELSE (manual-dispatch)"):
        res = _run_procedure(tmp_path, body, fx, bin_dir, marker=marker,
                             candidate=fx["eligible"], containing=fx["eligible"])
        assert res["rc"] == 2, res
        assert "worktree remove" not in res["git_log"], res["git_log"]
        assert fx["eligible"].exists()
        assert _git("worktree", "list", "--porcelain",
                    cwd=fx["primary"]).stdout == before
        assert "inventory_only" in res["stdout"]


def test_f5_procedure_refuses_before_action_for_each_branch(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    before = _git("worktree", "list", "--porcelain", cwd=fx["primary"]).stdout

    def run(**kw):
        kw.setdefault("marker", MANUAL_FIRST_LINE)
        kw.setdefault("candidate", fx["eligible"])
        kw.setdefault("containing", fx["eligible"])
        res = _run_procedure(tmp_path, body, fx, bin_dir, **kw)
        return res

    good_map = {"TASK-ELIGIBLE": _terminal_task(agent),
                "TASK-OCC-1": _terminal_task(agent),
                "TASK-OCC-2": _terminal_task(agent)}

    # R7: fewer than two distinct prior terminal occurrences -> report-only.
    r = run(task_map={k: v for k, v in good_map.items()
                      if k != "TASK-OCC-2"},
            audit=_occurrences("TASK-OCC-1"))
    assert r["rc"] == 2 and "report_only" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # R6.5: a nonterminal same-owner peer refuses.
    bad_peer = dict(good_map)
    bad_peer["TASK-OCC-2"] = dict(
        _terminal_task(agent), status="in_progress", completed_at=None,
    )
    r = run(task_map=bad_peer, audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "nonterminal_peer" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # duplicates collapse: the same occurrence twice is still one.
    r = run(task_map={k: v for k, v in good_map.items()
                      if k != "TASK-OCC-2"},
            audit=_occurrences("TASK-OCC-1", "TASK-OCC-1"))
    assert r["rc"] == 2 and "report_only_ordinal:1" in r["stdout"], r

    # R5: authoritative owner mismatch (OS UID is never used).
    r = run(task_map={"TASK-ELIGIBLE": _terminal_task("someone_else")},
            audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "owner_mismatch" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # R5: nonterminal owning task refuses.
    nonterm = dict(good_map)
    nonterm["TASK-ELIGIBLE"] = {"assigned_agent": agent, "status": "in_progress",
                                "completed_at": None}
    r = run(task_map=nonterm, audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "target_nonterminal" in r["stdout"], r

    # gate refusal: dirty worktree (all joins complete).
    dirty_map = {k: v for k, v in good_map.items() if k != "TASK-ELIGIBLE"}
    dirty_map["TASK-DIRTY"] = _terminal_task(agent)
    r = _run_procedure(tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
                       candidate=fx["dirty"], containing=fx["dirty"],
                       task_map=dirty_map,
                       audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "eligibility_gate" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # R4: an UNKNOWN real scan (this sandbox's PID1 is not the host init) is a
    # refusal, never a positive control -- the previous unconditional removal is
    # exactly what this asserts can no longer happen.
    r = run(task_map=good_map, audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "eligibility_gate" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]
    assert fx["eligible"].exists()
    assert _git("worktree", "list", "--porcelain",
                cwd=fx["primary"]).stdout == before


def test_f5_pr_query_is_bound_to_primary_repository(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    good_map = {"TASK-ELIGIBLE": _terminal_task(agent),
                "TASK-OCC-1": _terminal_task(agent),
                "TASK-OCC-2": _terminal_task(agent)}
    r = _run_procedure(tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
                       candidate=fx["eligible"], containing=fx["eligible"],
                       task_map=good_map,
                       audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    # The PR lookup must name the owning repository, never the workspace cwd.
    assert r["gh_log"], "gh was not invoked while evaluating no-open-pr"
    assert "--repo demo/fixture" in r["gh_log"], r["gh_log"]
    assert "--head task/TASK-ELIGIBLE" in r["gh_log"], r["gh_log"]
    assert "worktree remove" not in r["git_log"]


def test_f5_procedure_refuses_symlinked_ancestor(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    good_map = {"TASK-ELIGIBLE": _terminal_task(agent),
                "TASK-OCC-1": _terminal_task(agent),
                "TASK-OCC-2": _terminal_task(agent)}
    aliased = (fx["alias"] / "demo" / ".claude" / "worktrees" / "TASK-ELIGIBLE")
    r = _run_procedure(tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
                       candidate=aliased, containing=aliased,
                       task_map=good_map,
                       audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2, r
    assert "worktree remove" not in r["git_log"]
    assert fx["alias"].is_symlink()
    assert fx["eligible"].exists()


def test_f5_procedure_refuses_on_scan_unknown_before_action(tmp_path, body):
    # Exercise the real shipped scanner with a deterministic observation cap.
    # Host-context detection legitimately varies between direct and managed job
    # execution, but an exhausted cap must always be UNKNOWN and therefore feed
    # the procedure's already-covered no-removal refusal branch.
    fx = _build_procedure_fixture(tmp_path)
    scan = subprocess.run(
        [sys.executable, str(HELPER), "--target", str(fx["eligible"]),
         "--containing-worktree", str(fx["eligible"]), "--json",
         "--max-pids", "1"],
        capture_output=True, text=True)
    assert scan.returncode != 0, scan.stdout
    payload = json.loads(scan.stdout)
    assert payload["state"] == "unknown"
    assert "enumeration_truncated" in payload["reasons"]


@pytest.mark.parametrize("marker", [MANUAL_FIRST_LINE, DAEMON_MARKER])
def test_r4_exact_marker_two_distinct_terminal_joins_runs_literal_action(
        tmp_path, body, marker):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
        "TASK-MANUAL": _terminal_task(agent, marker=MANUAL_FIRST_LINE),
    }
    before = _git("worktree", "list", "--porcelain", cwd=fx["primary"]).stdout
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=marker,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map,
        audit_all=_occurrences("TASK-OCC-1", "TASK-OCC-2", "TASK-OCC-1",
                               "TASK-MANUAL"),
        audit_trigger=_occurrences("TASK-OCC-1", "TASK-OCC-2", "TASK-OCC-1"),
        scan_state="clear_observation",
    )
    assert result["rc"] == 0, result
    assert before != _git("worktree", "list", "--porcelain",
                          cwd=fx["primary"]).stdout
    assert not fx["eligible"].exists()
    assert result["git_log"].count("worktree remove") == 1
    assert (tmp_path / "scan.log").read_text().splitlines() == ["scan", "scan"]


@pytest.mark.parametrize(
    "scenario",
    ["open_pr", "young", "unreachable_head", "git_status_error",
     "gh_error", "missing_manifest", "audit_error"],
)
def test_r5_complete_procedure_refuses_literal_gate_and_join_failures_without_action(
        tmp_path, body, scenario):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    candidate = fx["eligible"]
    if scenario == "missing_manifest":
        candidate = fx["eligible"] / "src" / "node_modules"
        candidate.mkdir(parents=True)
    if scenario == "unreachable_head":
        (fx["eligible"] / "LOCAL.txt").write_text("local only\n")
        _git("add", "-A", cwd=fx["eligible"])
        _git("commit", "-m", "local only", cwd=fx["eligible"])
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(
            agent, age_days=1 if scenario == "young" else 30),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
    }
    occurrences = _occurrences("TASK-OCC-1", "TASK-OCC-2")
    before = _git("worktree", "list", "--porcelain", cwd=fx["primary"]).stdout
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=candidate, containing=fx["eligible"], task_map=task_map,
        audit_all=occurrences, audit_trigger=occurrences,
        scan_state="clear_observation",
        open_pr=1 if scenario == "open_pr" else 0,
        gh_fail=scenario == "gh_error",
        git_fail_match="status --porcelain" if scenario == "git_status_error" else "",
        audit_fail=scenario == "audit_error",
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert candidate.exists()
    assert _git("worktree", "list", "--porcelain",
                cwd=fx["primary"]).stdout == before


def test_r4_manual_peer_contributes_zero_to_first_two(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-MANUAL": _terminal_task(agent, marker=MANUAL_FIRST_LINE),
    }
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map,
        audit_all=_occurrences("TASK-OCC-1", "TASK-MANUAL"),
        audit_trigger=_occurrences("TASK-OCC-1"),
        scan_state="clear_observation",
    )
    assert result["rc"] == 2 and "report_only_ordinal:1" in result["stdout"], result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "scenario, expected_rc",
    [("relevant_missing", 2), ("conflicting", 2),
     ("unrelated_saturated", 0)],
)
def test_r4_relevant_bad_history_refuses_but_unrelated_saturation_does_not(
        tmp_path, body, scenario, expected_rc):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
    }
    audit_all = _occurrences("TASK-OCC-1", "TASK-OCC-2")
    audit_trigger = list(audit_all)
    if scenario == "relevant_missing":
        audit_trigger += _occurrences("TASK-MISSING")
    elif scenario == "conflicting":
        task_map["TASK-CONFLICT"] = _terminal_task(agent, marker=MANUAL_FIRST_LINE)
        audit_all.append({"task_id": "TASK-CONFLICT", "action": "workspace_cleanup_triggered"})
        audit_trigger.append({"task_id": "TASK-CONFLICT", "action": "workspace_cleanup_triggered"})
    else:
        task_map.update({
            f"TASK-SAT-{i}": dict(
                _terminal_task(agent), brief="ordinary unrelated task",
            )
            for i in range(1001)
        })
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_all=audit_all, audit_trigger=audit_trigger,
        scan_state="clear_observation",
    )
    assert result["rc"] == expected_rc, result
    if expected_rc:
        assert "worktree remove" not in result["git_log"]
        assert fx["eligible"].exists()
    else:
        assert result["git_log"].count("worktree remove") == 1
        assert not fx["eligible"].exists()


def test_r4_new_manual_peer_after_claim_refuses_before_fresh_scan_and_action(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
    }
    task_map_second = {
        **task_map,
        "TASK-NEW-PEER": {
            "assigned_agent": agent,
            "status": "in_progress",
            "completed_at": None,
            "brief": MANUAL_FIRST_LINE + "\nfixture",
            "task_id": None,
        },
    }
    first = _occurrences("TASK-OCC-1", "TASK-OCC-2")
    second = first + [{"task_id": "TASK-NEW-PEER", "action": "session_start"}]
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_all=first, audit_trigger=first,
        audit_all_second=second, audit_trigger_second=first,
        task_map_second=task_map_second,
        scan_state="clear_observation",
    )
    assert result["rc"] == 2 and "nonterminal_peer:TASK-NEW-PEER" in result["stdout"], result
    assert "worktree remove" not in result["git_log"]
    assert (tmp_path / "scan.log").read_text().splitlines() == ["scan"]
    assert fx["eligible"].exists()


def test_r5_noncanonical_candidate_shape_refuses_before_removal(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bad = fx["eligible"] / "ordinary-subdirectory"
    bad.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    agent = "dev_agent"
    task_map = {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
    }
    occurrences = _occurrences("TASK-OCC-1", "TASK-OCC-2")
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=bad, containing=fx["eligible"], task_map=task_map,
        audit_all=occurrences, audit_trigger=occurrences,
        scan_state="clear_observation",
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert bad.exists()


def _complete_cleanup_evidence(agent="dev_agent"):
    return {
        "TASK-ELIGIBLE": _terminal_task(agent),
        "TASK-OCC-1": _terminal_task(agent),
        "TASK-OCC-2": _terminal_task(agent),
    }, _occurrences("TASK-OCC-1", "TASK-OCC-2")


@pytest.mark.parametrize(
    "proof,remote,pr",
    [("remote", "success", "none"), ("merged", "missing", "merged")],
)
def test_preservation_by_fresh_remote_branch_or_confirmed_merged_pr(
        tmp_path, body, proof, remote, pr):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / f"{proof}.txt").write_text("durable evidence\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", proof, cwd=fx["eligible"])
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario=remote,
        pr_scenario=pr,
    )
    assert result["rc"] == 0, result
    assert result["git_log"].count("worktree remove") == 1
    assert not fx["eligible"].exists()


@pytest.mark.parametrize(
    "remote,pr,gh_fail",
    [
        ("missing", "open", False),
        ("missing", "closed", False),
        ("missing", "malformed", False),
        ("missing", "none", True),
        ("mismatch", "none", False),
        ("malformed", "none", False),
        ("fail", "merged", False),
        ("success", "mismatch", False),
    ],
)
def test_preservation_refuses_bad_remote_or_pr_evidence(
        tmp_path, body, remote, pr, gh_fail):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "local.txt").write_text("not on main\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "local", cwd=fx["eligible"])
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario=remote,
        pr_scenario=pr, gh_fail=gh_fail,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "job_scenario",
    ["use", "unknown", "completed_nonzero", "failed", "timeout",
     "output_cap", "rejected", "malformed", "mismatched", "stale",
     "missing_output", "output_mismatch"],
)
def test_host_job_receipt_failures_never_fall_back_or_mutate(
        tmp_path, body, job_scenario):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        job_scenario=job_scenario,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()
    # The only scanner command is carried in the submitted job payload. The
    # cleanup shell never executes the fixture helper directly.
    payload = tmp_path / "job-payload.json"
    if payload.exists():
        assert "check_path_use.py" in json.loads(payload.read_text())["script"]
    assert not (tmp_path / "shipped-skill" / "scan-direct.log").exists()


@pytest.mark.parametrize("source", ["tasks", "audit"])
def test_complete_history_command_failure_refuses_without_mutation(
        tmp_path, body, source):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", tasks_fail=source == "tasks",
        audit_fail=source == "audit",
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


def test_real_shipped_procedure_executes_under_zsh(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", shell="zsh",
    )
    assert result["rc"] == 0, result
    assert not fx["eligible"].exists()


@pytest.mark.parametrize("cache_name", ["node_modules", ".venv"])
def test_dirty_worktree_cache_only_removal_preserves_source_and_status(
        tmp_path, body, cache_name):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    cache = fx["eligible"] / cache_name
    cache.mkdir()
    (cache / "large.bin").write_bytes(b"cache" * 100)
    source = fx["eligible"] / "dirty-source.txt"
    source.write_bytes(b"precious untracked bytes\x00\xff")
    before_source = source.read_bytes()
    before_status = subprocess.run(
        ["git", "-C", str(fx["eligible"]), "status", "--porcelain=v1", "-z"],
        check=True, capture_output=True,
    ).stdout
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 0, result
    assert not cache.exists()
    assert fx["eligible"].exists()
    assert source.read_bytes() == before_source
    after_status = subprocess.run(
        ["git", "-C", str(fx["eligible"]), "status", "--porcelain=v1", "-z"],
        check=True, capture_output=True,
    ).stdout
    assert after_status == before_status
    assert "worktree remove" not in result["git_log"]
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_cache"
    assert receipt["allocated_bytes_after"] == 0


@pytest.mark.parametrize("kind", ["nested", "symlink", "missing_manifest", "protected"])
def test_cache_shape_manifest_symlink_and_protection_fail_closed(
        tmp_path, body, kind):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    if kind == "nested":
        cache = fx["eligible"] / "web" / "node_modules"
        cache.mkdir(parents=True)
    elif kind == "symlink":
        real = tmp_path / "external-cache"
        real.mkdir()
        cache = fx["eligible"] / "node_modules"
        cache.symlink_to(real, target_is_directory=True)
    else:
        cache = fx["eligible"] / "node_modules"
        cache.mkdir()
    task_map, occurrences = _complete_cleanup_evidence()
    if kind == "missing_manifest":
        (fx["eligible"] / "package-lock.json").unlink()
    if kind == "protected":
        task_map["TASK-ELIGIBLE"]["output_summary"] = "worktree-deferred: preserve"
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 2, result
    assert cache.exists() or cache.is_symlink()
    assert "worktree remove" not in result["git_log"]
