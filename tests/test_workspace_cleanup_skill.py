"""THR-259 / TASK-8667 causal tests for the shared workspace-cleanup skill.

Contract-text checks plus behavioral resolver/materialization checks: the ONE
shared daily/manual skill must be a TASK system contract that does not require a
repository, must materialize into BOTH provider roots, and must carry the exact
dispatch markers, own-workspace scope, inventory-only rule, approved seq185
exception rule, and non-force mechanisms.
"""
from __future__ import annotations

import inspect
import json
import os
import platform
import re
import shlex
import shutil
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
PROCEDURE = SKILL_DIR / "scripts" / "run_cleanup_candidate.sh"
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


def test_procedure_is_present_in_skill_package():
    assert PROCEDURE.is_file()
    assert PROCEDURE.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


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
    # The phrase now appears only in the prohibition: cache recursion is the
    # descriptor-rooted primitive, not a standalone rm dispatch.
    assert 'rm -rf -- "$WC_ISOLATED_CANDIDATE"' not in _shipped_procedure(body)
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
    assert "gh api graphql --paginate --slurp" in procedure
    assert "jobs submit" in procedure and "--json" in procedure
    assert "jobs show" in procedure and "--task-id" in procedure
    assert "jobs output" in procedure and "--session-id" in procedure
    assert "_wc_snapshot_tree" in procedure
    assert "_wc_delete_isolated_cache" in procedure
    assert "_wc_verify_post_action" in procedure
    assert 'rm -rf -- "$WC_ISOLATED_CANDIDATE"' not in procedure
    assert "--workspace-cleanup-delete-isolated-v1" in procedure
    assert "os.supports_dir_fd" in procedure and "os.O_NOFOLLOW" in procedure
    assert re.search(
        r"# gate current-use-scan\n\s*_wc_scan_job",
        _shipped_gate_text(body),
    )


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
    assert "owning-task remote branch" in normalized
    assert "head contains (is equal to or descends from) the candidate `HEAD`" in normalized
    assert "When the owning live remote branch exists, it is authoritative" in normalized
    assert "refuse without consulting owning-task or any-task merged-PR alternatives" in normalized
    assert "confirmed merged PR" in normalized
    assert "may not preserve the original commit topology" in normalized
    assert "closed-unmerged" in normalized


def test_skill_invokes_script_without_markdown_extraction_or_eval(body):
    assert 'bash "$SKILL/scripts/run_cleanup_candidate.sh"' in body
    forbidden = (
        "procedure-commands:begin",
        "eligibility-commands:begin",
        "source this block",
        "extract the",
        "eval ",
    )
    normalized = body.lower()
    for recipe in forbidden:
        assert recipe not in normalized


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
    expected_procedure = PROCEDURE.read_bytes()
    for root in (workspace / ".claude" / "skills", workspace / ".agents" / "skills"):
        marker = root / "workspace-cleanup" / "SKILL.md"
        assert marker.is_file(), f"workspace-cleanup not materialized at {marker}"
        assert marker.read_text(encoding="utf-8") == expected
        shipped_procedure = root / "workspace-cleanup" / "scripts" / PROCEDURE.name
        assert shipped_procedure.is_file()
        assert shipped_procedure.read_bytes() == expected_procedure
    # no-repo workspace must not receive repo-only contracts
    assert not (workspace / ".agents" / "skills" / "make-worktree").exists()


# ── F5: behavioral execution of the DELIVERED procedure ───────────────────
#
# These tests execute the real bundled procedure script against a real isolated
# Git fixture with labelled
# synthetic authoritative-response fixtures (happyranch recall/audit, gh). The
# task-output ``eligibility.py`` is not consulted and no test-only copy of the
# decision algorithm is used: the shipped shell runs, and the shipped helper is
# the real one.
#
# Per the controlling repair brief, an unknown REAL scan is a refusal test and
# never a positive control. On a sandboxed host whose PID1 is not the host init
# the shipped helper correctly returns ``unknown``; the positive removal control
# is therefore the separate non-elevated host witness. These tests prove
# mutation-free refusal for gates that return before the literal action begins;
# they do not extend that promise to failures detected after action has started.

ELIG_BEGIN = "# eligibility-commands:begin"
ELIG_END = "# eligibility-commands:end"
G = ["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture"]

OLD = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 30 * 86400))


def _shipped_procedure(body: str) -> str:
    del body
    assert PROCEDURE.is_file(), f"missing packaged procedure: {PROCEDURE}"
    return PROCEDURE.read_text(encoding="utf-8")


def _shipped_gate_text(body: str) -> str:
    procedure = _shipped_procedure(body)
    assert ELIG_BEGIN in procedure and ELIG_END in procedure
    return procedure[procedure.index(ELIG_BEGIN) + len(ELIG_BEGIN):
                     procedure.index(ELIG_END)]


def test_procedure_uses_the_documented_gate_commands(body):
    # The executable procedure owns and directly executes every gate. There is
    # no second Markdown source and no extraction/eval path that can drift.
    procedure = _shipped_procedure(body)
    assert "_wc_gate_block" not in procedure
    assert "eval " not in procedure
    names = re.findall(r"^\s*# gate (.+)$", _shipped_gate_text(body), re.M)
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
    assert "gh api graphql --paginate --slurp" in procedure
    assert "totalCount" in procedure and "pageInfo" in procedure
    assert "mergedAt" in procedure and "headRefOid" in procedure
    assert "search/issues --paginate" in procedure
    assert "incomplete_results" in procedure and "total_count" in procedure
    assert "_wc_any_merged_pr_contains_head" in procedure


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
        "    if [ \"$n\" -gt 1 ] && [ \"${WC_ACTION_DRIFT:-}\" = replace-cache ]; then mv \"$WC_REAL_CANDIDATE\" \"$WC_REAL_CANDIDATE.before\" && mkdir \"$WC_REAL_CANDIDATE\"; fi\n"
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
        "        payload=; prev=; for a in \"$@\"; do [ \"$prev\" = --from-file ] && payload=\"$a\"; prev=\"$a\"; done\n"
        "        [ -n \"$payload\" ] || exit 1\n"
        "        cp \"$payload\" \"$WC_JOB_PAYLOAD\"\n"
        "        exec python3 -c 'import datetime,json,os\n"
        "p=json.load(open(os.environ[\"WC_JOB_PAYLOAD\"])); now=datetime.datetime.now(datetime.timezone.utc).isoformat()\n"
        "print(json.dumps({\"id\":\"JOB-1\",\"status\":\"running\",\"created_at\":now,\"started_at\":now,\"cwd_resolved\":os.path.realpath(os.environ[\"WORKSPACE\"]),\"timeout_seconds\":20,\"events_url\":\"/events\",\"authentication\":{\"task_id\":p[\"task_id\"],\"session_id\":p[\"session_id\"]}},sort_keys=True))' ;;\n"
        "      wait)\n"
        "        case \"$WC_JOB_SCENARIO\" in\n"
        "          timeout) echo '{\"status\":\"running\",\"timed_out\":true}' ;;\n"
        "          failed) echo '{\"status\":\"failed\",\"timed_out\":false}' ;;\n"
        "          *) echo '{\"status\":\"completed\",\"timed_out\":false}' ;;\n"
        "        esac ;;\n"
        "      show|output)\n"
        "        echo scan >> \"$WC_SCAN_LOG\"\n"
        "        [ \"$WC_JOB_SCENARIO\" = malformed ] && { echo not-json; exit 0; }\n"
        "        exec python3 -c 'import datetime,json,os\n"
        "p=json.load(open(os.environ[\"WC_JOB_PAYLOAD\"])); scenario=os.environ[\"WC_JOB_SCENARIO\"]; now=datetime.datetime.fromtimestamp(os.stat(os.environ[\"WC_JOB_PAYLOAD\"]).st_mtime,datetime.timezone.utc).isoformat(); created=(\"2000-01-01T00:00:00+00:00\" if scenario==\"stale\" else now)\n"
        "target=os.path.realpath(os.environ[\"WC_REAL_CANDIDATE\"]); containing=os.path.realpath(os.environ[\"CONTAINING\"]); st=os.stat(target); is_cache=os.path.basename(target) in (\"node_modules\",\".venv\"); cst=os.stat(containing)\n"
        "coverage={\"agent_uid\":os.getuid(),\"self_pid\":\"123\",\"target_dev_ino\":[st.st_dev,st.st_ino],\"containing_worktree\":containing if is_cache else None,\"containing_worktree_dev_ino\":[cst.st_dev,cst.st_ino] if is_cache else None,\"target_present\":True,\"containing_worktree_present\":is_cache,\"total_pids\":1,\"same_user\":1,\"root\":0,\"other_user\":0,\"exempt\":0,\"scanned\":1,\"exited\":0,\"unreadable_same_user\":0,\"unreadable_unknown_uid\":0,\"identity_read_errors\":0,\"role_mismatch\":0,\"denied\":0,\"vanished\":0,\"errors\":0,\"truncated\":0,\"maps_truncated\":0,\"fd_truncated\":0,\"threads_truncated\":0,\"new_pids_after\":0,\"reused_pids\":0,\"mnt_ns_differs\":0,\"mnt_ns_path_unverified\":0,\"host_context\":{\"pid1_comm\":\"systemd\",\"pid_ns_agree\":True,\"mnt_agree\":True,\"proc_mounts\":1,\"stacked\":False,\"mountinfo\":\"ok\",\"pid1_ns_readable\":True,\"ok\":True},\"enum_passes\":2}\n"
        "state=\"blocked\" if scenario==\"use\" else (\"unknown\" if scenario==\"unknown\" else \"clear_observation\"); scan={\"state\":state,\"target\":\"/wrong\" if scenario==\"output_mismatch\" else target,\"hits\":[{\"pid\":\"9\"}] if state==\"blocked\" else [],\"reasons\":[\"unknown\"] if state==\"unknown\" else [],\"coverage\":coverage,\"exempt\":[],\"cycles\":[{\"phase\":\"enumerate_pass\",\"pass\":0,\"new\":1}]}\n"
        "stdout=\"\" if scenario==\"missing_output\" else json.dumps(scan,sort_keys=True)+\"\\n\"; task=\"TASK-WRONG\" if scenario==\"wrong_task\" else p[\"task_id\"]; auth={\"task_id\":p[\"task_id\"],\"session_id\":\"sess-wrong\" if scenario==\"wrong_session\" else p[\"session_id\"]}; job={\"id\":\"JOB-2\" if scenario==\"wrong_job\" else \"JOB-1\",\"task_id\":task,\"agent_name\":\"other\" if scenario==\"wrong_agent\" else \"dev_agent\",\"title\":p[\"title\"],\"rationale\":p[\"rationale\"],\"script_text\":\"echo spoof\" if scenario==\"wrong_script\" else p[\"script\"],\"interpreter\":\"zsh\" if scenario==\"wrong_interpreter\" else p[\"interpreter\"],\"cwd_hint\":None,\"cwd_resolved\":\"/wrong\" if scenario==\"wrong_cwd\" else os.path.realpath(os.environ[\"WORKSPACE\"]),\"status\":\"failed\" if scenario==\"wrong_status\" else \"completed\",\"exit_code\":3 if scenario in (\"completed_nonzero\",\"wrong_exit\") else 0,\"reason\":\"spoof\" if scenario==\"wrong_reason\" else None,\"duration_ms\":1,\"created_at\":created,\"started_at\":now,\"finished_at\":\"1999-01-01T00:00:00+00:00\" if scenario==\"wrong_time\" else now}\n"
        "output={\"stdout\":stdout,\"stderr\":\"\",\"truncated_stdout\":scenario==\"output_cap\",\"truncated_stderr\":False,\"total_stdout_bytes\":len(stdout.encode())+(1 if scenario==\"total_mismatch\" else 0),\"total_stderr_bytes\":0}; receipt={\"authentication\":auth,\"job\":job,\"output\":output}; receipt=({\"job\":job} if scenario==\"minimal\" else receipt); receipt[\"extra\"]=1 if scenario==\"extra\" else receipt.get(\"extra\"); receipt.pop(\"extra\",None) if scenario!=\"extra\" else None; print(json.dumps(receipt,sort_keys=True))' ;;\n"
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
        "case \" $* \" in *\" search/issues \"*)\n"
        "  n=$(cat \"$WC_SEARCH_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_SEARCH_COUNT\"\n"
        "  [ \"$WC_SEARCH_SCENARIO\" = fail ] && exit 71\n"
        "  exec python3 -c 'import json,os\n"
        "scenario=os.environ[\"WC_SEARCH_SCENARIO\"]; n=int(open(os.environ[\"WC_SEARCH_COUNT\"]).read()); slug=\"demo/fixture\"\n"
        "if scenario==\"malformed\": print(\"{}\"); raise SystemExit\n"
        "number=91 if scenario==\"changing\" and n%2==0 else 90\n"
        "items=[] if scenario==\"none\" else [{\"number\":number,\"pull_request_url\":f\"https://api.github.com/repos/{slug}/pulls/{number}\"}]\n"
        "if scenario==\"duplicate\": items=items+items\n"
        "total=(len(items)+1 if scenario==\"truncated\" else len(items)); incomplete=scenario==\"incomplete\"\n"
        "print(json.dumps({\"total_count\":total,\"incomplete_results\":incomplete,\"items\":items},sort_keys=True))' ;;\n"
        "esac\n"
        "case \"$2\" in repos/*/pulls/*)\n"
        "  n=$(cat \"$WC_PR_DETAIL_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_PR_DETAIL_COUNT\"\n"
        "  [ \"$WC_OTHER_PR_SCENARIO\" = fail ] && exit 71\n"
        "  exec python3 -c 'import json,os,sys\n"
        "scenario=os.environ[\"WC_OTHER_PR_SCENARIO\"]; n=int(open(os.environ[\"WC_PR_DETAIL_COUNT\"]).read()); number=int(sys.argv[1].rsplit(\"/\",1)[1])\n"
        "if scenario==\"malformed\": print(json.dumps({\"number\":number})); raise SystemExit\n"
        "state=\"MERGED\"; merged=\"2026-01-01T00:00:00Z\"; base=\"main\"; oid=os.environ[\"WC_TARGET_SHA\"]\n"
        "if scenario==\"open\": state,merged=\"OPEN\",None\n"
        "elif scenario==\"closed\": state,merged=\"CLOSED\",None\n"
        "elif scenario==\"nondefault\": base=\"release\"\n"
        "elif scenario==\"bad_merged_at\": merged=None\n"
        "elif scenario==\"bad_oid\": oid=\"not-an-oid\"\n"
        "elif scenario==\"changing\" and n%2==0: oid=\"2\"*40\n"
        "print(json.dumps({\"number\":number,\"state\":state,\"mergedAt\":merged,\"headRefOid\":oid,\"baseRefName\":base},sort_keys=True))' \"$2\" ;;\n"
        "esac\n"
        "case \"$2\" in repos/*/compare/*)\n"
        "  n=$(cat \"$WC_COMPARE_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_COMPARE_COUNT\"\n"
        "  [ \"$WC_COMPARE_SCENARIO\" = fail ] && exit 71\n"
        "  exec python3 -c 'import json,os\n"
        "scenario=os.environ[\"WC_COMPARE_SCENARIO\"]; n=int(open(os.environ[\"WC_COMPARE_COUNT\"]).read()); head=os.environ[\"WC_HEAD\"]; target=os.environ[\"WC_TARGET_SHA\"]\n"
        "if scenario==\"malformed\": print(json.dumps({\"status\":\"ahead\"})); raise SystemExit\n"
        "status=\"ahead\" if scenario in (\"ahead\",\"changing\",\"truncated\") else (\"identical\" if scenario==\"identical\" else (\"behind\" if scenario==\"behind\" else \"diverged\"))\n"
        "if scenario==\"changing\" and n>1: status=\"behind\"\n"
        "count=1 if status==\"ahead\" else 0; total=2 if scenario==\"truncated\" else count; d={\"status\":status,\"ahead_by\":count,\"behind_by\":0 if status in (\"ahead\",\"identical\") else 1,\"total_commits\":total,\"commit_count\":count,\"base_oid\":head,\"merge_base_oid\":head if status in (\"ahead\",\"identical\") else \"0\"*40,\"target_oid\":target}\n"
        "print(json.dumps(d,sort_keys=True))' ;;\n"
        "esac\n"
        "case \"$2\" in repos/*)\n"
        "  n=$(cat \"$WC_REPO_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_REPO_COUNT\"\n"
        "  [ \"$WC_REPO_SCENARIO\" = fail ] && exit 71\n"
        "  exec python3 -c 'import json,os\n"
        "scenario=os.environ[\"WC_REPO_SCENARIO\"]; n=int(open(os.environ[\"WC_REPO_COUNT\"]).read())\n"
        "if scenario==\"malformed\": print(\"{}\"); raise SystemExit\n"
        "branch=\"release\" if scenario==\"changing\" and n%2==0 else \"main\"\n"
        "print(json.dumps({\"default_branch\":branch},sort_keys=True))' ;;\n"
        "esac\n"
        "[ \"$1\" = api ] && [ \"$2\" = graphql ] || { echo 'expected graphql or compare' >&2; exit 1; }\n"
        "exec python3 -c 'import json,os\n"
        "scenario=os.environ.get(\"WC_PR_SCENARIO\",\"none\"); branch=os.environ[\"WC_BRANCH\"]; head=os.environ[\"WC_HEAD\"]\n"
        "def row(number,state=\"MERGED\",merged=\"2026-01-01T00:00:00Z\",oid=None): return {\"number\":number,\"state\":state,\"mergedAt\":merged,\"headRefName\":branch,\"headRefOid\":oid or head}\n"
        "if scenario==\"malformed\": print(\"{}\"); raise SystemExit\n"
        "nodes=[]\n"
        "if scenario in (\"open\",\"beyond100open\"): nodes=[row(i) for i in range(1,102)]+[row(102,\"OPEN\",None)]\n"
        "elif scenario in (\"closed\",\"beyond100closed\"): nodes=[row(i) for i in range(1,102)]+[row(102,\"CLOSED\",None)]\n"
        "elif scenario==\"merged\": nodes=[row(1)]\n"
        "elif scenario==\"merged_target\": nodes=[row(1,oid=os.environ[\"WC_TARGET_SHA\"])]\n"
        "elif scenario==\"mismatch\": nodes=[row(1,oid=\"0\"*40)]\n"
        "elif scenario==\"bad_oid\": nodes=[row(1,oid=\"not-an-oid\")]\n"
        "elif scenario==\"bad_merged_at\": nodes=[row(1,\"MERGED\",None)]\n"
        "elif scenario==\"duplicate\": nodes=[row(1),row(1)]\n"
        "pages=[{\"data\":{\"repository\":{\"pullRequests\":{\"totalCount\":len(nodes),\"nodes\":nodes,\"pageInfo\":{\"hasNextPage\":False,\"endCursor\":None}}}}}]\n"
        "if scenario in (\"beyond100open\",\"beyond100closed\"): pages=[{\"data\":{\"repository\":{\"pullRequests\":{\"totalCount\":len(nodes),\"nodes\":nodes[:100],\"pageInfo\":{\"hasNextPage\":True,\"endCursor\":\"cursor-1\"}}}}},{\"data\":{\"repository\":{\"pullRequests\":{\"totalCount\":len(nodes),\"nodes\":nodes[100:],\"pageInfo\":{\"hasNextPage\":False,\"endCursor\":None}}}}}]\n"
        "print(json.dumps(pages))'\n")
    gh.chmod(0o755)
    rm = bin_dir / "rm"
    rm.write_text(
        "#!/bin/sh\n"
        "case \"${WC_RM_SCENARIO:-normal}\" in\n"
        "  final-dispatch-swap)\n"
        "    /usr/bin/mv \"$WC_ISOLATED_CANDIDATE\" \"$WC_ISOLATED_CANDIDATE.validated\" || exit $?\n"
        "    mkdir \"$WC_ISOLATED_CANDIDATE\" || exit $?\n"
        "    printf 'unvalidated replacement\\n' > \"$WC_ISOLATED_CANDIDATE/replacement\" || exit $?\n"
        "    exec /usr/bin/rm \"$@\" ;;\n"
        "  residual) exit 0 ;;\n"
        "  recreate) /usr/bin/rm \"$@\" || exit $?; mkdir \"$WC_REAL_CANDIDATE\"; exit 0 ;;\n"
        "  protected-change) /usr/bin/rm \"$@\" || exit $?; mv \"$WORKSPACE/output\" \"$WORKSPACE/output.before\" && mkdir \"$WORKSPACE/output\"; exit 0 ;;\n"
        "  *) exec /usr/bin/rm \"$@\" ;;\n"
        "esac\n")
    rm.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text(
        "#!/bin/sh\n"
        "is_action=0\n"
        "for arg in \"$@\"; do\n"
        "  [ \"$arg\" = --workspace-cleanup-delete-isolated-v1 ] && is_action=1\n"
        "done\n"
        "if [ \"$is_action\" = 1 ]; then\n"
        "  case \"${WC_ACTION_SCENARIO:-normal}\" in\n"
        "    final-dispatch-swap)\n"
        "      /usr/bin/mv \"$WC_ISOLATED_CANDIDATE\" \"$WC_ISOLATED_CANDIDATE.validated\" || exit $?\n"
        "      mkdir \"$WC_ISOLATED_CANDIDATE\" || exit $?\n"
        "      printf 'unvalidated replacement\\n' > \"$WC_ISOLATED_CANDIDATE/replacement\" || exit $? ;;\n"
        "    action-fail) exit 71 ;;\n"
        "    partial-delete)\n"
        "      printf 'descriptor-rooted-delete-started\\n' > \"$WC_DELETE_STARTED\" || exit $?\n"
        "      /usr/bin/rm -- \"$WC_ISOLATED_CANDIDATE/deleted.bin\" || exit $?\n"
        "      exit 71 ;;\n"
        "  esac\n"
        "fi\n"
        f"{shlex.quote(sys.executable)} \"$@\"\n"
        "rc=$?\n"
        "if [ \"$is_action\" = 1 ] && [ \"$rc\" = 0 ]; then\n"
        "  case \"${WC_ACTION_SCENARIO:-normal}\" in\n"
        "    residual) mkdir -p \"$WC_ISOLATED_CANDIDATE\" ;;\n"
        "    recreate) mkdir -p \"$WC_REAL_CANDIDATE\" ;;\n"
        "    protected-change) mv \"$WORKSPACE/output\" \"$WORKSPACE/output.before\" && mkdir \"$WORKSPACE/output\" ;;\n"
        "  esac\n"
        "fi\n"
        "exit \"$rc\"\n"
    )
    python.chmod(0o755)
    mv = bin_dir / "mv"
    mv.write_text(
        "#!/bin/sh\n"
        "source=; destination=; for a in \"$@\"; do [ \"$a\" = -- ] && continue; "
        "[ -z \"$source\" ] && source=\"$a\" || destination=\"$a\"; done\n"
        "case \"${WC_MV_SCENARIO:-normal}\" in\n"
        "  final-swap)\n"
        "    if [ \"$source\" = \"$WC_REAL_CANDIDATE\" ]; then\n"
        "      /usr/bin/mv \"$source\" \"$source.validated\" || exit $?\n"
        "      mkdir \"$source\" || exit $?\n"
        "      printf 'uninspected replacement\\n' > \"$source/replacement\" || exit $?\n"
        "    fi\n"
        "    exec /usr/bin/mv \"$@\" ;;\n"
        "  isolation-drift)\n"
        "    /usr/bin/mv \"$@\" || exit $?\n"
        "    if [ \"$source\" = \"$WC_REAL_CANDIDATE\" ]; then\n"
        "      /usr/bin/mv \"$destination\" \"$destination.validated\" || exit $?\n"
        "      mkdir \"$destination\" || exit $?\n"
        "      printf 'isolation replacement\\n' > \"$destination/replacement\" || exit $?\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "  *) exec /usr/bin/mv \"$@\" ;;\n"
        "esac\n")
    mv.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        "echo \"git $*\" >> \"$GIT_LOG\"\n"
        "case \"$*\" in *\"${WC_GIT_FAIL_MATCH:-__never__}\"*) exit 71;; esac\n"
        "case \"$*\" in *\" status --porcelain=v1 -z\")\n"
        "  n=$(cat \"$WC_FULL_STATUS_COUNT\"); n=$((n+1)); echo \"$n\" > \"$WC_FULL_STATUS_COUNT\"\n"
        "  [ -n \"${WC_FULL_STATUS_FAIL_CALL:-}\" ] && [ \"$n\" = \"$WC_FULL_STATUS_FAIL_CALL\" ] && exit 71 ;;\n"
        "esac\n"
        "case \"$*\" in *\" ls-remote --exit-code origin \"*)\n"
        "  case \"${WC_REMOTE_SCENARIO:-missing}\" in\n"
        "    success) printf '%s\\trefs/heads/%s\\n' \"$WC_HEAD\" \"$WC_BRANCH\"; exit 0 ;;\n"
        "    target) printf '%s\\trefs/heads/%s\\n' \"$WC_REMOTE_TARGET_SHA\" \"$WC_BRANCH\"; exit 0 ;;\n"
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
                   git_full_status_fail_call: int | None = None,
                   fail_removed_receipt: bool = False,
                   task_map_second: dict | None = None,
                   job_scenario: str | None = None,
                   remote_scenario: str = "missing",
                   pr_scenario: str | None = None,
                   search_scenario: str = "none",
                   other_pr_scenario: str = "merged",
                   repo_scenario: str = "main",
                   target_sha: str | None = None,
                   remote_target_sha: str | None = None,
                   compare_scenario: str = "diverged",
                   tasks_fail: bool = False,
                   action_drift: str = "", rm_scenario: str = "normal",
                   mv_scenario: str = "normal",
                   action_scenario: str = "normal",
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
    (tmp_path / "compare-count").write_text("0\n")
    (tmp_path / "search-count").write_text("0\n")
    (tmp_path / "pr-detail-count").write_text("0\n")
    (tmp_path / "repo-count").write_text("0\n")
    (tmp_path / "full-status-count").write_text("0\n")
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
        "WC_SEARCH_SCENARIO": search_scenario,
        "WC_SEARCH_COUNT": str(tmp_path / "search-count"),
        "WC_OTHER_PR_SCENARIO": other_pr_scenario,
        "WC_PR_DETAIL_COUNT": str(tmp_path / "pr-detail-count"),
        "WC_REPO_SCENARIO": repo_scenario,
        "WC_REPO_COUNT": str(tmp_path / "repo-count"),
        "WC_REMOTE_SCENARIO": remote_scenario,
        "WC_TARGET_SHA": target_sha or ("1" * 40),
        "WC_REMOTE_TARGET_SHA": remote_target_sha or target_sha or ("1" * 40),
        "WC_COMPARE_SCENARIO": compare_scenario,
        "WC_COMPARE_COUNT": str(tmp_path / "compare-count"),
        "WC_BRANCH": f"task/{containing.name}",
        "WC_HEAD": _git("rev-parse", "HEAD", cwd=containing).stdout.strip(),
        "WC_GH_FAIL": "1" if gh_fail else "0",
        "WC_GIT_FAIL_MATCH": git_fail_match,
        "WC_FULL_STATUS_COUNT": str(tmp_path / "full-status-count"),
        "WC_FULL_STATUS_FAIL_CALL": (
            str(git_full_status_fail_call)
            if git_full_status_fail_call is not None else ""
        ),
        "WC_RECEIPT_FAIL_MARKER": str(tmp_path / "receipt-fail-once"),
        "WC_ACTION_DRIFT": action_drift,
        "WC_RM_SCENARIO": rm_scenario,
        "WC_MV_SCENARIO": mv_scenario,
        "WC_ACTION_SCENARIO": (
            action_scenario if action_scenario != "normal" else rm_scenario
        ),
        "TMPDIR": str(wc_tmp),
    })
    if fail_removed_receipt:
        python = bin_dir / "python3"
        python.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${WC_DECISION:-}\" ] && [ ! -e \"$WC_RECEIPT_FAIL_MARKER\" ]; then\n"
            "  : > \"$WC_RECEIPT_FAIL_MARKER\"\n"
            "  exit 72\n"
            "fi\n"
            f"exec {shlex.quote(sys.executable)} \"$@\"\n"
        )
        python.chmod(0o755)
    script = (
        f'bash {shlex.quote(str(PROCEDURE))} {shlex.quote(str(candidate))} '
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
    assert "-F owner=demo" in r["gh_log"], r["gh_log"]
    assert "-F name=fixture" in r["gh_log"], r["gh_log"]
    assert "-F headRefName=task/TASK-ELIGIBLE" in r["gh_log"], r["gh_log"]
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
    assert (tmp_path / "scan.log").read_text().splitlines() == ["scan"] * 4


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
    assert (tmp_path / "scan.log").read_text().splitlines() == ["scan"] * 2
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


def test_wrong_registered_parent_refuses_and_preserves_path(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    wrong = fx["primary"] / ".claude" / "unexpected" / "TASK-WRONG"
    wrong.parent.mkdir(parents=True)
    _git("worktree", "add", "-b", "task/TASK-WRONG", str(wrong),
         "origin/main", cwd=fx["primary"])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map = {
        "TASK-WRONG": _terminal_task("dev_agent"),
        "TASK-OCC-1": _terminal_task("dev_agent"),
        "TASK-OCC-2": _terminal_task("dev_agent"),
    }
    occurrences = _occurrences("TASK-OCC-1", "TASK-OCC-2")
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=wrong, containing=wrong, task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 2, result
    assert wrong.exists()
    assert "worktree remove" not in result["git_log"]


def test_action_boundary_cache_replacement_refuses_and_preserves_both_paths(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "original").write_text("keep\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        action_drift="replace-cache",
    )
    assert result["rc"] == 2, result
    assert cache.is_dir()
    assert (cache.parent / "node_modules.before" / "original").read_text() == "keep\n"


def test_pre_descriptor_admission_root_swap_refuses_and_preserves_both_objects(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        rm_scenario="final-dispatch-swap",
        action_scenario="final-dispatch-swap",
    )
    assert result["rc"] == 3, result
    assert '"decision":"removed_cache"' not in result["stdout"]
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "isolation_anomaly"
    assert receipt["anomaly"] == "isolation_restore_failed"
    assert (cache / "replacement").read_text() == "unvalidated replacement\n"
    preserved = list(fx["eligible"].glob(
        ".workspace-cleanup-isolate.*/node_modules.validated/validated"
    ))
    assert len(preserved) == 1, preserved
    assert preserved[0].read_text() == "validated bytes\n"


def test_partial_delete_anomaly_accounts_for_isolated_residual_bytes(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "deleted.bin").write_bytes(b"delete me")
    residual = cache / "residual.bin"
    residual.write_bytes(b"x" * 8192)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        action_scenario="partial-delete",
    )
    assert result["rc"] == 3, result
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] == "action_failed"
    isolated = receipt["residual_locations"]["isolated_candidate"]
    assert isolated["exists"] is True
    assert isolated["apparent_bytes"] >= 8192
    assert isolated["allocated_bytes"] >= 8192
    assert receipt["apparent_bytes_after"] >= isolated["apparent_bytes"]
    assert receipt["allocated_bytes_after"] >= isolated["allocated_bytes"]
    assert receipt["measurement_error"] is None


def test_isolation_identity_drift_refuses_and_preserves_both_objects(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        mv_scenario="isolation-drift",
    )
    assert result["rc"] == 3, result
    assert '"decision":"removed_cache"' not in result["stdout"]
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "isolation_anomaly"
    assert receipt["anomaly"] == "isolation_restore_failed"
    assert (cache / "replacement").read_text() == "isolation replacement\n"
    preserved = list(fx["eligible"].glob(
        ".workspace-cleanup-isolate.*/node_modules.validated/validated"
    ))
    assert len(preserved) == 1, preserved
    assert preserved[0].read_text() == "validated bytes\n"


def test_descriptor_rooted_action_primitive_failure_restores_without_success(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        action_scenario="action-fail",
    )
    assert result["rc"] == 2, result
    assert '"decision":"removed_cache"' not in result["stdout"]
    assert (cache / "validated").read_text() == "validated bytes\n"
    assert list(fx["eligible"].glob(".workspace-cleanup-isolate.*")) == []


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
    "remote,pr",
    [("target", "none"), ("missing", "merged_target")],
)
def test_preservation_when_head_is_local_ancestor_of_own_branch_or_merged_pr(
        tmp_path, body, remote, pr):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    candidate_head = _git("rev-parse", "HEAD", cwd=fx["eligible"]).stdout.strip()
    (fx["eligible"] / "later.txt").write_text("later\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "later", cwd=fx["eligible"])
    target = _git("rev-parse", "HEAD", cwd=fx["eligible"]).stdout.strip()
    _git("reset", "--hard", candidate_head, cwd=fx["eligible"])
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario=remote,
        pr_scenario=pr, target_sha=target,
    )
    assert result["rc"] == 0, result
    assert result["git_log"].count("worktree remove") == 1


@pytest.mark.parametrize(
    "remote,pr",
    [("target", "none"), ("missing", "merged_target")],
)
def test_preservation_refuses_diverged_own_branch_or_merged_pr_head(
        tmp_path, body, remote, pr):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    divergent = _git("rev-parse", "origin/main", cwd=fx["eligible"]).stdout.strip()
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario=remote,
        pr_scenario=pr, target_sha=divergent,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "remote,pr",
    [("target", "none"), ("missing", "merged_target")],
)
def test_preservation_uses_double_read_closed_compare_when_target_is_not_local(
        tmp_path, body, remote, pr):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    target = "1" * 40
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario=remote,
        pr_scenario=pr, target_sha=target, compare_scenario="ahead",
    )
    assert result["rc"] == 0, result
    compare_calls = [line for line in result["gh_log"].splitlines()
                     if "/compare/" in line]
    # Eligibility is intentionally re-derived twice, and each proof is itself
    # double-read, so one successful action produces exactly four calls.
    assert len(compare_calls) == 4, result["gh_log"]


@pytest.mark.parametrize(
    "compare", ["fail", "malformed", "changing", "truncated", "diverged"],
)
def test_preservation_refuses_unproven_remote_compare(tmp_path, body, compare):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario="target",
        target_sha="1" * 40, compare_scenario=compare,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "remote,pr,gh_fail",
    [
        ("missing", "open", False),
        ("missing", "closed", False),
        ("missing", "beyond100open", False),
        ("missing", "beyond100closed", False),
        ("missing", "bad_merged_at", False),
        ("missing", "duplicate", False),
        ("missing", "malformed", False),
        ("missing", "none", True),
        ("mismatch", "none", False),
        ("malformed", "none", False),
        ("fail", "merged", False),
        ("success", "bad_oid", False),
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


def _run_other_task_pr_preservation(tmp_path, body, **kwargs):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "fix-forward.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "fix-forward candidate", cwd=fx["eligible"])
    task_map, occurrences = _complete_cleanup_evidence()
    options = {
        "marker": MANUAL_FIRST_LINE,
        "candidate": fx["eligible"],
        "containing": fx["eligible"],
        "task_map": task_map,
        "audit_trigger": occurrences,
        "scan_state": "clear_observation",
        "remote_scenario": "missing",
        "pr_scenario": "none",
        "search_scenario": "merged",
        "other_pr_scenario": "merged",
        "repo_scenario": "main",
        "target_sha": "1" * 40,
        "compare_scenario": "ahead",
    }
    options.update(kwargs)
    if options["target_sha"] == "candidate":
        options["target_sha"] = _git(
            "rev-parse", "HEAD", cwd=fx["eligible"],
        ).stdout.strip()
    return fx, _run_procedure(tmp_path, body, fx, bin_dir, **options)


@pytest.mark.parametrize(
    "compare,target", [("ahead", "1" * 40), ("identical", "candidate")],
)
def test_preservation_by_other_task_merged_pr_with_independent_containment(
        tmp_path, body, compare, target):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, compare_scenario=compare, target_sha=target,
    )
    assert result["rc"] == 0, result
    assert result["git_log"].count("worktree remove") == 1
    assert not fx["eligible"].exists()
    assert result["gh_log"].count("search/issues") == 4
    assert result["gh_log"].count("/pulls/90") == 4
    assert result["gh_log"].count("/compare/") == 4


@pytest.mark.parametrize("own_branch_relation", ["diverged", "behind"])
def test_live_own_branch_noncontainment_refuses_before_any_task_pr_fallback(
        tmp_path, body, own_branch_relation):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    candidate_head = _git(
        "rev-parse", "HEAD", cwd=fx["eligible"],
    ).stdout.strip()
    if own_branch_relation == "diverged":
        (fx["primary"] / "divergent.txt").write_text("divergent\n")
        _git("add", "-A", cwd=fx["primary"])
        _git("commit", "-m", "divergent own branch", cwd=fx["primary"])
        own_branch_head = _git(
            "rev-parse", "HEAD", cwd=fx["primary"],
        ).stdout.strip()
    else:
        own_branch_head = _git(
            "rev-parse", "origin/main", cwd=fx["eligible"],
        ).stdout.strip()
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario="target",
        remote_target_sha=own_branch_head, pr_scenario="none",
        search_scenario="merged", other_pr_scenario="merged",
        repo_scenario="main", target_sha=candidate_head,
        compare_scenario="identical",
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert "search/issues" not in result["gh_log"]
    assert "/pulls/" not in result["gh_log"]
    assert "/compare/" not in result["gh_log"]
    assert fx["eligible"].exists()


def test_live_own_branch_containment_error_refuses_before_pr_fallback(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    (fx["eligible"] / "candidate.txt").write_text("candidate\n")
    _git("add", "-A", cwd=fx["eligible"])
    _git("commit", "-m", "candidate", cwd=fx["eligible"])
    candidate_head = _git(
        "rev-parse", "HEAD", cwd=fx["eligible"],
    ).stdout.strip()
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", remote_scenario="target",
        remote_target_sha=candidate_head, pr_scenario="none",
        search_scenario="merged", other_pr_scenario="merged",
        repo_scenario="main", target_sha=candidate_head,
        compare_scenario="identical",
        git_fail_match=(
            f"merge-base --is-ancestor {candidate_head} {candidate_head}"
        ),
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert "search/issues" not in result["gh_log"]
    assert "/pulls/" not in result["gh_log"]
    assert "/compare/" not in result["gh_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize("other_pr", ["open", "closed", "nondefault"])
def test_other_task_unmerged_or_nondefault_pr_never_preserves(
        tmp_path, body, other_pr):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, other_pr_scenario=other_pr,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "search", ["incomplete", "truncated", "duplicate", "changing",
               "malformed", "fail"],
)
def test_other_task_pr_discovery_refuses_incomplete_or_ambiguous_evidence(
        tmp_path, body, search):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, search_scenario=search,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "field,scenario",
    [
        ("other_pr_scenario", "malformed"),
        ("other_pr_scenario", "changing"),
        ("other_pr_scenario", "fail"),
        ("other_pr_scenario", "bad_merged_at"),
        ("other_pr_scenario", "bad_oid"),
        ("repo_scenario", "malformed"),
        ("repo_scenario", "changing"),
        ("repo_scenario", "fail"),
    ],
)
def test_other_task_pr_confirmation_refuses_malformed_changing_or_failed_reads(
        tmp_path, body, field, scenario):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, **{field: scenario},
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "compare",
    ["fail", "malformed", "changing", "truncated", "diverged", "behind"],
)
def test_other_task_pr_confirmation_refuses_unproven_compare(
        tmp_path, body, compare):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, compare_scenario=compare,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize("own_pr", ["open", "closed"])
def test_own_branch_unmerged_pr_still_blocks_other_task_merged_pr(
        tmp_path, body, own_pr):
    fx, result = _run_other_task_pr_preservation(
        tmp_path, body, pr_scenario=own_pr,
    )
    assert result["rc"] == 2, result
    assert "worktree remove" not in result["git_log"]
    assert "search/issues" not in result["gh_log"]
    assert fx["eligible"].exists()


@pytest.mark.parametrize(
    "job_scenario",
    ["use", "unknown", "completed_nonzero", "failed", "timeout",
     "output_cap", "rejected", "malformed", "wrong_job", "wrong_task",
     "wrong_session", "wrong_agent", "wrong_script", "wrong_interpreter",
     "wrong_cwd", "wrong_status", "wrong_exit", "wrong_reason", "wrong_time",
     "stale", "missing_output", "output_mismatch", "total_mismatch",
     "minimal", "extra"],
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
    shell = shutil.which("zsh")
    if shell is None:
        pytest.fail(
            "required real-zsh prerequisite unavailable: "
            f"platform={platform.platform()!r} PATH={os.environ.get('PATH')!r}"
        )
    proof = subprocess.run(
        [shell, "--version"], check=True, capture_output=True, text=True,
    )
    assert proof.stdout.startswith("zsh "), proof
    assert Path(shell).resolve().name == "zsh"
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation", shell=shell,
    )
    assert result["rc"] == 0, result
    assert not fx["eligible"].exists()


def test_real_zsh_regression_never_substitutes_bash():
    source = inspect.getsource(test_real_shipped_procedure_executes_under_zsh)
    assert 'shell = "bash"' not in source


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
    expected_apparent = sum(
        path.lstat().st_size for path in (cache, cache / "large.bin")
    )
    expected_allocated = sum(
        path.lstat().st_blocks * 512 for path in (cache, cache / "large.bin")
    )
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
    assert receipt["apparent_bytes_before"] == expected_apparent
    assert receipt["allocated_bytes_before"] == expected_allocated
    assert receipt["apparent_bytes_after"] == 0
    assert receipt["allocated_bytes_after"] == 0


def test_nonignored_root_cache_is_refused_before_isolation(tmp_path, body):
    """Causal TASK-7599 regression: root cache is visible as untracked."""
    fx = _build_procedure_fixture(tmp_path)
    (fx["eligible"] / ".gitignore").write_text("web/node_modules/\n")
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    payload = cache / "precious.bin"
    payload.write_bytes(b"must remain byte-identical\x00\xff")
    before = payload.read_bytes()
    status = subprocess.run(
        ["git", "-C", str(fx["eligible"]), "status", "--porcelain=v1",
         "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "?? node_modules/precious.bin" in status
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()

    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )

    assert result["rc"] == 2, result
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt == {"decision": "refused", "reason": "cache_not_gitignored"}
    assert payload.read_bytes() == before
    assert list(fx["eligible"].glob(".workspace-cleanup-isolate.*")) == []


@pytest.mark.parametrize("scenario", ["residual", "recreate"])
def test_cache_post_action_presence_never_reports_success(tmp_path, body, scenario):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "precious").write_text("content\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        rm_scenario=scenario,
    )
    assert result["rc"] == 3, result
    assert cache.exists()
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] in {
        "action_residual", "post_action_candidate_or_protected_changed",
    }
    assert '"decision":"refused"' not in result["stdout"]


def test_candidate_recreated_after_isolated_removal_never_reports_success(
        tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        rm_scenario="recreate",
    )
    assert result["rc"] == 3, result
    assert cache.is_dir()
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] == "post_action_candidate_or_protected_changed"
    assert '"decision":"refused"' not in result["stdout"]


def test_post_delete_git_status_failure_is_anomaly_not_success(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        git_full_status_fail_call=2,
    )
    assert result["rc"] == 3, result
    assert not cache.exists()
    assert (tmp_path / "full-status-count").read_text().strip() == "2"
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] == "post_action_git_status_unavailable"
    assert '"decision":"removed_cache"' not in result["stdout"]
    assert '"decision":"refused"' not in result["stdout"]


@pytest.mark.parametrize("candidate_kind", ["cache", "worktree"])
def test_removed_receipt_failure_is_anomaly_not_success(
        tmp_path, body, candidate_kind):
    fx = _build_procedure_fixture(tmp_path)
    candidate = fx["eligible"]
    if candidate_kind == "cache":
        candidate = candidate / "node_modules"
        candidate.mkdir()
        (candidate / "validated").write_text("validated bytes\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=candidate, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        fail_removed_receipt=True,
    )
    assert result["rc"] == 3, result
    assert not candidate.exists()
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] == "receipt_generation_failed"
    assert '"decision":"removed_cache"' not in result["stdout"]
    assert '"decision":"refused"' not in result["stdout"]


def test_unchanged_isolated_cache_removes_only_exact_candidate(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    (cache / "validated").write_text("validated bytes\n")
    sibling = fx["eligible"] / "preserve-sibling"
    sibling.write_text("preserve\n")
    protected = fx["workspace"] / "output"
    protected.mkdir()
    protected_identity = (protected.lstat().st_dev, protected.lstat().st_ino)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 0, result
    assert json.loads(result["stdout"].splitlines()[-2])["decision"] == "removed_cache"
    assert not cache.exists()
    assert sibling.read_text() == "preserve\n"
    assert (protected.lstat().st_dev, protected.lstat().st_ino) == protected_identity
    assert list(fx["eligible"].glob(".workspace-cleanup-isolate.*")) == []


def test_changed_protected_identity_never_reports_success(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    protected = fx["workspace"] / "output"
    protected.mkdir()
    original_inode = protected.lstat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
        rm_scenario="protected-change",
    )
    assert result["rc"] == 3, result
    assert protected.lstat().st_ino != original_inode
    receipt = json.loads(result["stdout"].splitlines()[-2])
    assert receipt["decision"] == "removed_with_anomaly"
    assert receipt["anomaly"] == "post_action_candidate_or_protected_changed"
    assert receipt["path"] == str(cache)
    assert receipt["apparent_bytes_before"] > 0
    assert receipt["allocated_bytes_before"] >= 0
    assert receipt["apparent_bytes_after"] == 0
    assert receipt["allocated_bytes_after"] == 0
    assert isinstance(receipt["filesystem_free_before"], int)
    assert isinstance(receipt["filesystem_free_after"], int)
    assert isinstance(receipt["filesystem_free_delta"], int)
    assert '"decision":"refused"' not in result["stdout"]


@pytest.mark.parametrize("fault", ["nested_mount", "cross_device", "non_owned"])
def test_recursive_boundary_executes_real_walker_and_refuses_faults(
        tmp_path, body, fault):
    match = re.search(
        r"_wc_snapshot_tree\(\) \{.*?<<'PY'\n(.*?)\nPY\n\}",
        _shipped_procedure(body), re.S,
    )
    assert match, "missing shipped recursive-boundary program"
    program = match.group(1)
    workspace = tmp_path / "workspace"
    primary = workspace / "repos" / "demo"
    containing = primary / ".claude" / "worktrees" / "TASK-WALK"
    candidate = containing / "node_modules"
    child = candidate / "child"
    child.mkdir(parents=True)
    (workspace / "output").mkdir()
    destination = tmp_path / "snapshot.json"
    if fault == "nested_mount":
        program = program.replace(
            "def mountpoints():\n",
            "def mountpoints():\n    return {os.path.abspath(os.path.join(candidate, 'child'))}\n",
            1,
        )
    else:
        injected = """
real_lstat = os.lstat
def injected_lstat(path):
    value = real_lstat(path)
    if os.path.abspath(path) != os.path.abspath(os.path.join(candidate, "child")):
        return value
    class Changed:
        pass
    changed = Changed()
    for name in ("st_dev", "st_ino", "st_mode", "st_uid", "st_size", "st_blocks"):
        setattr(changed, name, getattr(value, name))
    changed.st_dev += int(os.environ.get("INJECT_CROSS_DEVICE", "0"))
    changed.st_uid += int(os.environ.get("INJECT_NON_OWNED", "0"))
    return changed
os.lstat = injected_lstat
"""
        program = program.replace("uid = os.getuid()\n", "uid = os.getuid()\n" + injected, 1)
    script = tmp_path / "walker.py"
    script.write_text(program)
    env = dict(os.environ)
    env["INJECT_CROSS_DEVICE"] = "1" if fault == "cross_device" else "0"
    env["INJECT_NON_OWNED"] = "1" if fault == "non_owned" else "0"
    result = subprocess.run(
        [sys.executable, str(script), str(candidate), str(containing),
         str(primary), str(workspace), str(destination)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0, result
    assert child.exists()


def test_recursive_boundary_refuses_protected_descendant_symlink(tmp_path, body):
    fx = _build_procedure_fixture(tmp_path)
    cache = fx["eligible"] / "node_modules"
    cache.mkdir()
    protected = fx["workspace"] / "output"
    protected.mkdir()
    (cache / "protected-link").symlink_to(protected, target_is_directory=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 2, result
    assert cache.exists() and protected.exists()


def _uv_interpreter(tmp_path: Path, monkeypatch) -> Path:
    fake_home = tmp_path / "fake-home"
    store = fake_home / ".local/share/uv/python/cpython-fixture/bin"
    store.mkdir(parents=True)
    target = store / "python3.13"
    target.write_bytes(b"fixture interpreter bytes\x00\xff")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    return target


def _make_venv(root: Path, target: Path, *, link_name: str = "python") -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text(f"home = {target.parent}\n")
    (root / "bin" / link_name).symlink_to(target)
    return root


def test_uv_venv_cache_removal_preserves_external_interpreter(
        tmp_path, body, monkeypatch):
    fx = _build_procedure_fixture(tmp_path)
    target = _uv_interpreter(tmp_path, monkeypatch)
    before = target.read_bytes()
    cache = _make_venv(fx["eligible"] / ".venv", target)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 0, result
    assert not cache.exists()
    assert target.is_file() and target.read_bytes() == before


def test_worktree_with_nested_uv_venv_removal_preserves_external_interpreter(
        tmp_path, body, monkeypatch):
    fx = _build_procedure_fixture(tmp_path)
    target = _uv_interpreter(tmp_path, monkeypatch)
    before = target.read_bytes()
    nested = _make_venv(fx["eligible"] / "nested" / ".venv", target)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=fx["eligible"], containing=fx["eligible"],
        task_map=task_map, audit_trigger=occurrences,
        scan_state="clear_observation",
    )
    assert result["rc"] == 0, result
    assert not fx["eligible"].exists() and not nested.exists()
    assert target.is_file() and target.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    ["outside_bin", "wrong_name", "outside_store", "missing_cfg",
     "wrong_venv_name", "protected_target"],
)
def test_uv_interpreter_exception_refuses_every_other_external_link(
        tmp_path, body, monkeypatch, fault):
    fx = _build_procedure_fixture(tmp_path)
    target = _uv_interpreter(tmp_path, monkeypatch)
    cache = fx["eligible"] / ".venv"
    venv = cache
    link_name = "python"
    if fault == "wrong_venv_name":
        cache = fx["eligible"] / "node_modules"
        venv = cache / "not-venv"
    if fault == "wrong_name":
        link_name = "pip"
    if fault == "outside_store":
        target = tmp_path / "outside" / "python3.13"
        target.parent.mkdir()
        target.write_bytes(b"outside")
    if fault == "protected_target":
        target = fx["workspace"] / "output" / "python3.13"
        target.parent.mkdir()
        target.write_bytes(b"protected")
    _make_venv(venv, target, link_name=link_name)
    if fault == "outside_store":
        (venv / "pyvenv.cfg").write_text("home = /definitely/not/the/target\n")
    if fault == "outside_bin":
        (venv / "bin" / link_name).unlink()
        (venv / link_name).symlink_to(target)
    if fault == "missing_cfg":
        (venv / "pyvenv.cfg").unlink()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    task_map, occurrences = _complete_cleanup_evidence()
    result = _run_procedure(
        tmp_path, body, fx, bin_dir, marker=MANUAL_FIRST_LINE,
        candidate=cache, containing=fx["eligible"], task_map=task_map,
        audit_trigger=occurrences, scan_state="clear_observation",
    )
    assert result["rc"] == 2, result
    assert cache.exists() and target.exists()


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
