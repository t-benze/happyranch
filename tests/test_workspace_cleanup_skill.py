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
    assert "never deletes production residue" in normalized


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
# These tests parse the literal gate commands out of the SHIPPED SKILL.md and
# execute them against a real isolated Git fixture. The task-output
# ``eligibility.py`` is not consulted; prompt-string presence alone is not proof.

ELIG_BEGIN = "<!-- eligibility-commands:begin -->"
ELIG_END = "<!-- eligibility-commands:end -->"
G = ["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture"]


def _shipped_gates(body: str) -> dict:
    assert ELIG_BEGIN in body and ELIG_END in body
    block = body[body.index(ELIG_BEGIN):body.index(ELIG_END)]
    match = re.search(r"```bash\n(.*?)```", block, re.S)
    assert match, "no bash eligibility block in SKILL.md"
    gates: dict[str, list[str]] = {}
    current = None
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if line.startswith("# gate "):
            current = line[len("# gate "):].strip()
            gates[current] = []
        elif current and line:
            gates[current].append(line)
    assert len(gates) >= 12, gates
    return {name: "\n".join(lines) for name, lines in gates.items()}


def _git(*args, cwd=None):
    result = subprocess.run(G + list(args), cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _write_stubs(bin_dir: Path) -> None:
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "if [ \"$F5_OPEN_PR\" = \"1\" ]; then echo 1; else echo 0; fi\n")
    gh.chmod(0o755)
    py = bin_dir / "python3"
    py.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *\"st_uid==os.getuid()\"*) [ \"$F5_FOREIGN_UID\" = \"1\" ] && exit 1 ;;\n"
        "  *\"st_dev==os.stat\"*) [ \"$F5_DIFF_DEV\" = \"1\" ] && exit 1 ;;\n"
        "esac\n"
        "exec \"$F5_REAL_PYTHON\" \"$@\"\n")
    py.chmod(0o755)


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
    wt_root = primary / ".claude" / "worktrees"
    wt_root.mkdir(parents=True)

    def add_wt(name: str, branch: str) -> Path:
        path = wt_root / name
        _git("worktree", "add", "-b", branch, str(path), "origin/main", cwd=primary)
        return path

    eligible = add_wt("TASK-ELIGIBLE", "task/TASK-ELIGIBLE")
    dirty = add_wt("TASK-DIRTY", "task/TASK-DIRTY")
    (dirty / "scratch.txt").write_text("uncommitted\n")
    young = add_wt("TASK-YOUNG", "task/TASK-YOUNG")
    local = add_wt("TASK-LOCAL", "task/TASK-LOCAL")
    (local / "extra.txt").write_text("local only\n")
    _git("add", "-A", cwd=local)
    _git("commit", "-m", "local only", cwd=local)
    unregistered = wt_root / "TASK-UNREGISTERED"
    unregistered.mkdir()
    link = wt_root / "TASK-LINK"
    link.symlink_to(eligible)
    (eligible / "node_modules").mkdir()
    (eligible / "node_modules" / "pkg.txt").write_text("cache\n")
    badcache_parent = eligible / "subcache"
    (badcache_parent / "node_modules").mkdir(parents=True)
    return {"workspace": workspace, "primary": primary, "origin": origin,
            "eligible": eligible, "dirty": dirty, "young": young, "local": local,
            "unregistered": unregistered, "link": link,
            "badcache": badcache_parent / "node_modules"}


def _gate_env(fx: dict, bin_dir: Path, candidate: Path, containing: Path,
              *, age_seconds: int = 604800, extra=None) -> dict:
    env = dict(os.environ)
    env.update({
        "SKILL": str(SKILL_DIR),
        "WORKSPACE": str(fx["workspace"]),
        "PRIMARY": str(fx["primary"]),
        "CANDIDATE": str(candidate),
        "CONTAINING": str(containing),
        "AGE_SECONDS": str(age_seconds),
        "F5_REAL_PYTHON": sys.executable,
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
    })
    if extra:
        env.update(extra)
    return env


def _run_gate(command: str, env: dict):
    return subprocess.run(["bash", "-c", command], env=env,
                          capture_output=True, text=True)


def test_f5_delivered_gates_execute_against_real_fixture(tmp_path, body):
    gates = _shipped_gates(body)
    fx = _build_procedure_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stubs(bin_dir)
    trace: list[dict] = []
    old = time.time() - 30 * 86400
    os.utime(fx["eligible"], (old, old))
    os.utime(fx["eligible"] / "node_modules", (old, old))

    # Valid cache control: every applicable shipped gate passes.
    cache = fx["eligible"] / "node_modules"
    cache_gates = ("workspace-scope", "non-primary", "registration", "ownership",
                   "not-symlink", "same-filesystem", "clean", "durable-head",
                   "no-open-pr", "retention-age",
                   "cache-immediate-parent-manifest")
    for name in cache_gates:
        env = _gate_env(fx, bin_dir, cache, fx["eligible"], age_seconds=86400)
        result = _run_gate(gates[name], env)
        trace.append({"gate": name, "scenario": "valid_cache_control",
                      "exit": result.returncode, "stderr": result.stderr.strip()})
        assert result.returncode == 0, (name, result.stdout, result.stderr)

    # The shipping current-use scan gate runs end to end and returns a valid state.
    scan = _run_gate(gates["current-use-scan"],
                     _gate_env(fx, bin_dir, cache, fx["eligible"],
                               age_seconds=86400))
    trace.append({"gate": "current-use-scan", "scenario": "valid_cache_control",
                  "exit": scan.returncode})
    assert scan.returncode in (0, 2, 3)
    assert scan.returncode != 1

    # Valid whole-worktree control (immediate-parent manifest does not apply).
    for name in cache_gates[:-1]:
        env = _gate_env(fx, bin_dir, fx["eligible"], fx["eligible"])
        result = _run_gate(gates[name], env)
        trace.append({"gate": name, "scenario": "valid_worktree_control",
                      "exit": result.returncode, "stderr": result.stderr.strip()})
        assert result.returncode == 0, (name, result.stdout, result.stderr)

    before_registration = _git("worktree", "list", "--porcelain",
                               cwd=fx["primary"]).stdout

    def assert_refused(name: str, candidate: Path, scenario: str,
                       containing: Path | None = None, **extra) -> None:
        env = _gate_env(fx, bin_dir, candidate, containing or candidate, **extra)
        result = _run_gate(gates[name], env)
        assert Path(candidate).exists() or Path(candidate).is_symlink(), \
            f"{name} mutated the candidate"
        assert _git("worktree", "list", "--porcelain",
                    cwd=fx["primary"]).stdout == before_registration, \
            f"{name} changed worktree registration"
        trace.append({"gate": name, "scenario": scenario,
                      "exit": result.returncode})
        assert result.returncode != 0, (name, scenario, result.stderr)

    outside = tmp_path / "outside"
    outside.mkdir()
    assert_refused("workspace-scope", outside, "protected/foreign root")
    assert_refused("non-primary", fx["primary"], "primary checkout")
    assert_refused("registration", fx["unregistered"], "unregistered dir")
    assert_refused("ownership", fx["eligible"], "cross-owner (synthetic uid)",
                   extra={"F5_FOREIGN_UID": "1"})
    assert_refused("not-symlink", fx["link"], "symlink target")
    assert_refused("same-filesystem", fx["eligible"], "shared/other device",
                   extra={"F5_DIFF_DEV": "1"})
    assert_refused("clean", fx["dirty"], "dirty worktree")
    assert_refused("durable-head", fx["local"], "local-only/unreachable HEAD")
    assert_refused("no-open-pr", fx["eligible"], "open PR (synthetic gh)",
                   extra={"F5_OPEN_PR": "1"})
    assert_refused("retention-age", fx["young"], "insufficient age")
    assert_refused("cache-immediate-parent-manifest", fx["badcache"],
                   "missing immediate-parent manifest", age_seconds=86400)

    # Every gate passed for the control, so the literal non-force removal runs.
    removal = subprocess.run(
        ["git", "-C", str(fx["primary"]), "worktree", "remove",
         str(fx["eligible"])], capture_output=True, text=True)
    trace.append({"gate": "literal-removal", "scenario": "valid_control",
                  "exit": removal.returncode, "stderr": removal.stderr.strip()})
    assert removal.returncode == 0
    assert not fx["eligible"].exists()
    assert str(fx["eligible"]) not in _git(
        "worktree", "list", "--porcelain", cwd=fx["primary"]).stdout

    trace_path = tmp_path / "f5-trace.json"
    trace_path.write_text(json.dumps(trace, indent=2) + "\n")
    durable = os.environ.get("TASK8711_F5_TRACE")
    if durable:
        Path(durable).write_text(json.dumps(trace, indent=2) + "\n")
    assert len(trace) == 34, [t["gate"] for t in trace]


def test_f5_procedure_refuses_unmarked_manual_and_requires_exact_markers(body):
    normalized = " ".join(body.split())
    lines = {ln.strip() for ln in body.splitlines()}
    # exact markers only; nothing else grants authority
    assert "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)" in lines
    assert "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)" in lines
    assert "inventory-only" in normalized
    assert "No other skill, task, or prompt grants" in normalized
    # the shipped gate block is the action-time procedure
    assert ELIG_BEGIN in body and ELIG_END in body
