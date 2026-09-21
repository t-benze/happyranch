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


def test_retention_and_ordinal_contract(body):
    normalized = " ".join(body.split())
    assert "first **two**" in normalized
    assert "24 hours" in normalized
    assert "seven terminal days" in normalized
    assert "zero" in normalized


def test_helper_reference_is_relative_to_skill(body):
    assert "scripts/check_path_use.py" in body


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
    for required in ("workspace-scope", "non-primary", "registration",
                     "ownership", "not-symlink", "same-filesystem", "clean",
                     "durable-head", "no-open-pr", "retention-age",
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


def test_r5_no_open_pr_gate_binds_to_primary_repository(body, tmp_path):
    primary = tmp_path / "repos" / "demo"
    primary.mkdir(parents=True)
    _git("init", "-b", "main", str(primary))
    (primary / "README.md").write_text("x\n")
    _git("add", "-A", cwd=primary)
    _git("commit", "-m", "base", cwd=primary)
    _git("branch", "task/TASK-X", cwd=primary)
    _git("remote", "add", "origin",
         "https://github.com/demo/fixture.git", cwd=primary)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho \"$*\" > \"$GH_LOG\"\necho 0\n")
    gh.chmod(0o755)
    env = dict(os.environ, PRIMARY=str(primary), CONTAINING=str(primary),
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
               GH_LOG=str(tmp_path / "gh.log"))
    result = _run_shipped_gate(body, "no-open-pr", env)
    assert result.returncode == 0, result.stderr
    logged = (tmp_path / "gh.log").read_text()
    assert "--repo demo/fixture" in logged, logged


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
        "print(json.dumps(m.get(sys.argv[1], {\"error\": \"missing\"})))' \"$last\" ;;\n"
        "  audit)\n"
        "    exec cat \"$WC_AUDIT\" ;;\n"
        "  *) echo 'unsupported' >&2; exit 1 ;;\n"
        "esac\n")
    hr.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "echo \"gh $*\" >> \"$GH_LOG\"\n"
        "repo=\"\"; prev=\"\"\n"
        "for a in \"$@\"; do [ \"$prev\" = \"--repo\" ] && repo=\"$a\"; prev=\"$a\"; done\n"
        "[ -n \"$repo\" ] || { echo 'missing --repo' >&2; exit 1; }\n"
        "if [ \"${WC_OPEN_PR:-0}\" = \"1\" ]; then echo 1; else echo 0; fi\n")
    gh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        "echo \"git $*\" >> \"$GIT_LOG\"\n"
        "exec /usr/bin/git \"$@\"\n")
    git.chmod(0o755)


def _run_procedure(tmp_path: Path, body: str, fx: dict, bin_dir: Path, *,
                   marker: str | None, agent: str = "dev_agent",
                   task_map: dict | None = None, audit=None,
                   candidate: Path, containing: Path,
                   acting: str = "TASK-ACTING", open_pr: int = 0):
    task_map = task_map if task_map is not None else {}
    audit = audit if audit is not None else []
    (tmp_path / "task-map.json").write_text(json.dumps(task_map))
    (tmp_path / "audit.json").write_text(json.dumps(audit))
    proc_src = tmp_path / "proc.sh"
    proc_src.write_text(_shipped_procedure(body))
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
        "SKILL": str(SKILL_DIR),
        "ACTING_TASK": acting,
        "GIT_LOG": str(git_log),
        "GH_LOG": str(gh_log),
        "WC_TASK_MAP": str(tmp_path / "task-map.json"),
        "WC_AUDIT": str(tmp_path / "audit.json"),
        "WC_OPEN_PR": str(open_pr),
        "TMPDIR": str(wc_tmp),
    })
    script = (
        f'. {shlex.quote(str(proc_src))}\n'
        f'run_cleanup_candidate {shlex.quote(str(candidate))} '
        f'{shlex.quote(str(containing))}\n'
        'printf "RC=%s\\n" "$?"\n'
    )
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True)
    rc_line = [ln for ln in result.stdout.splitlines() if ln.startswith("RC=")]
    rc = int(rc_line[-1].split("=", 1)[1]) if rc_line else None
    return {"rc": rc, "stdout": result.stdout, "stderr": result.stderr,
            "git_log": git_log.read_text(), "gh_log": gh_log.read_text()}


def _occurrences(*task_ids: str) -> list[dict]:
    return [{"task_id": tid, "action": "workspace_cleanup_triggered"}
            for tid in task_ids]


def _terminal_task(agent: str, *, age_days: int = 30) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                       time.gmtime(time.time() - age_days * 86400))
    return {"assigned_agent": agent, "status": "completed", "completed_at": ts}


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
    r = run(task_map=good_map, audit=_occurrences("TASK-OCC-1"))
    assert r["rc"] == 2 and "report_only" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # R6.5: a nonterminal same-owner peer refuses.
    bad_peer = dict(good_map)
    bad_peer["TASK-OCC-2"] = {"assigned_agent": agent, "status": "in_progress",
                              "completed_at": None}
    r = run(task_map=bad_peer, audit=_occurrences("TASK-OCC-1", "TASK-OCC-2"))
    assert r["rc"] == 2 and "nonterminal_peer" in r["stdout"], r
    assert "worktree remove" not in r["git_log"]

    # duplicates collapse: the same occurrence twice is still one.
    r = run(task_map=good_map, audit=_occurrences("TASK-OCC-1", "TASK-OCC-1"))
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
    dirty_map = dict(good_map)
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
    # Directly assert the shipped scan gate is non-zero in this sandbox and that
    # the procedure refuses without issuing the literal removal.
    fx = _build_procedure_fixture(tmp_path)
    scan = subprocess.run(
        [sys.executable, str(HELPER), "--target", str(fx["eligible"]),
         "--containing-worktree", str(fx["eligible"]), "--json"],
        capture_output=True, text=True)
    assert scan.returncode != 0, scan.stdout
    payload = json.loads(scan.stdout)
    assert payload["state"] == "unknown"
    assert "host_context_unestablished" in " ".join(payload["reasons"])
