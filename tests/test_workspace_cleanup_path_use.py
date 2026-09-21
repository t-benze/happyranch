"""THR-259 seq185 causal tests for the bundled workspace-cleanup helper.

Exercises the REAL production helper at
``runtime/skills/bundled/workspace-cleanup/scripts/check_path_use.py`` (loaded by
path) against the deterministic ``FakeProc`` seam. The exception contract is an
exact readable process name AND its exact bounded cgroup role; a qualifying
exception is deliberately uninspected, and every other unreadable same-user
member is unknown.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    REPO_ROOT / "runtime" / "skills" / "bundled" / "workspace-cleanup"
    / "scripts" / "check_path_use.py"
)

TARGET = "/work/wt/TASK-X"
STAT_MAP = {TARGET: (2049, 42)}
SELF = "9999"
MNT = "mnt:[4026]"
USER_NS = "user:[4026531837]"
PID_NS = "pid:[4026]"
UID = 1000
USER_SLICE = f"/user.slice/user-{UID}.slice"
USER_SVC = f"{USER_SLICE}/user@{UID}.service"
APP = f"{USER_SVC}/app.slice"


@pytest.fixture(scope="module")
def cpu():
    assert HELPER.is_file(), f"missing packaged helper: {HELPER}"
    spec = importlib.util.spec_from_file_location("wc_check_path_use", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["wc_check_path_use"] = module
    spec.loader.exec_module(module)
    return module


def _spec(pid, *, comm="bash", uid=UID, cgroup="", ppid="1", exe=None,
          exe_stat=(0, 0o755, True), cwd="/home/benze", root="/", maps=(),
          fds=None, threads=None):
    return {
        "pid": str(pid),
        "uid": [uid, uid, uid, uid] if isinstance(uid, int) else list(uid),
        "comm": comm, "ppid": str(ppid), "exe": exe, "exe_stat": exe_stat,
        "cwd": cwd, "root": root, "maps": list(maps), "fds": dict(fds or {}),
        "threads": dict(threads or {}), "cgroup": cgroup,
        "ns": {"pid": PID_NS, "mnt": MNT, "user": USER_NS},
        "starttime": 100,
    }


def _proc(cpu, *procs, deny=()):
    spec = {SELF: _spec(SELF, comm="check_path_use")}
    for p in procs:
        spec[str(p["pid"])] = p
    return cpu.FakeProc(spec, deny=list(deny), stat_map=STAT_MAP)


def _deny_all(pid):
    return [(pid, r) for r in ("exe", "cwd", "root", "maps", "fd", "task",
                               "ns/mnt", "ns/user")]


def _scan(cpu, proc, **kw):
    return cpu.scan(TARGET, proc=proc, self_pid=SELF, **kw)


def _exempt_roles(res):
    return sorted(e["role"] for e in res.exempt)


@pytest.mark.parametrize("comm,unit,role", [
    ("sshd-session", f"{USER_SLICE}/session-8.scope", "sshd-session"),
    ("ssh-agent", f"{APP}/ssh-agent.service", "ssh-agent"),
    ("ssh-agent", f"{APP}/gcr-ssh-agent.service", "ssh-agent"),
    ("gcr-ssh-agent", f"{APP}/gcr-ssh-agent.service", "gcr-ssh-agent"),
    ("gpg-agent", f"{APP}/gpg-agent.service", "gpg-agent"),
    ("systemd", f"{USER_SVC}/init.scope", "systemd --user"),
    ("(sd-pam)", f"{USER_SVC}/init.scope", "(sd-pam)"),
])
def test_qualifying_name_and_role_is_uninspected_exemption(cpu, comm, unit, role):
    p = _spec("600", comm=comm, cgroup=unit)
    res = _scan(cpu, _proc(cpu, p, deny=_deny_all("600")))
    assert _exempt_roles(res) == [role]
    assert res.state == "clear_observation"
    assert res.coverage["denied"] == 0


@pytest.mark.parametrize("comm,unit", [
    ("ssh-agent", ""),                                  # name only
    ("bash", f"{APP}/ssh-agent.service"),               # role only
    ("ssh-agent", f"{APP}/gpg-agent.service"),          # expected name/wrong unit
    ("ssh-agent", f"{APP}/ssh-agent.service.bak"),      # suffix lookalike
    ("ssh-agent", f"{APP}/xssh-agent.service"),         # prefix lookalike
    ("sshd-session", f"{USER_SLICE}/session-8.scope.d"),  # scope lookalike
    ("ssh-agent", "/user.slice/user-1001.slice/user@1001.service/app.slice/ssh-agent.service"),
    ("ssh-agent", "/random.service"),                   # arbitrary unit
])
def test_name_only_or_wrong_role_is_scanned_not_exempt(cpu, comm, unit):
    p = _spec("610", comm=comm, cgroup=unit)
    res = _scan(cpu, _proc(cpu, p))
    assert res.exempt == []
    assert res.coverage["scanned"] >= 1


def test_qualifying_exception_with_unreadable_exe_and_namespace(cpu):
    p = _spec("620", comm="ssh-agent", cgroup=f"{APP}/ssh-agent.service", exe=None)
    res = _scan(cpu, _proc(cpu, p, deny=_deny_all("620")))
    assert _exempt_roles(res) == ["ssh-agent"]
    assert res.state == "clear_observation"


def test_qualifying_exception_holding_target_is_excluded(cpu):
    p = _spec("621", comm="ssh-agent", cgroup=f"{APP}/gcr-ssh-agent.service",
              fds={"3": TARGET + "/held"})
    res = _scan(cpu, _proc(cpu, p))
    assert res.hits == []
    assert _exempt_roles(res) == ["ssh-agent"]
    assert res.state == "clear_observation"


def test_name_only_holding_target_blocks(cpu):
    p = _spec("622", comm="ssh-agent", cgroup="", fds={"3": TARGET + "/held"})
    res = _scan(cpu, _proc(cpu, p))
    assert res.state == "blocked"
    assert any(h["kind"] == "fd" for h in res.hits)


def test_unreadable_non_exempt_member_is_unknown(cpu):
    p = _spec("630", comm="bash")
    res = _scan(cpu, _proc(cpu, p, deny=[("630", "cwd")]))
    assert res.state == "unknown"
    assert res.exempt == []


def test_missing_cgroup_cannot_exempt(cpu):
    p = _spec("631", comm="ssh-agent", cgroup="")
    res = _scan(cpu, _proc(cpu, p, deny=[("631", "cgroup"), ("631", "cwd"),
                                         ("631", "task")]))
    assert res.state == "unknown"
    assert res.exempt == []


def test_root_denial_is_not_a_veto(cpu):
    root = _spec("1", comm="systemd", uid=0, cgroup="/init.scope")
    member = _spec("700", comm="bash")
    res = _scan(cpu, _proc(cpu, root, member, deny=_deny_all("1")))
    assert res.coverage["root"] >= 1
    assert res.state == "clear_observation"
    assert res.exempt == []


def test_role_mismatch_is_counted(cpu):
    p = _spec("632", comm="ssh-agent", cgroup="")
    res = _scan(cpu, _proc(cpu, p))
    assert res.coverage["role_mismatch"] == 1
    assert res.exempt == []


def test_classify_helper_exact_pairs(cpu):
    # Direct unit check of the exported classifier table.
    assert cpu.expected_exception_role("sshd-session", UID, f"{USER_SLICE}/session-1.scope") == "sshd-session"
    assert cpu.expected_exception_role("sshd-session", UID, f"{USER_SLICE}/session-1.scope/x") is None
    assert cpu.expected_exception_role("systemd", UID, f"{USER_SVC}/init.scope") == "systemd --user"
    assert cpu.expected_exception_role("(sd-pam)", UID, f"{USER_SVC}/init.scope") == "(sd-pam)"
    assert cpu.expected_exception_role("ssh-agent", UID, f"{APP}/ssh-agent.service") == "ssh-agent"
    assert cpu.expected_exception_role("ssh-agent", UID, f"{APP}/gcr-ssh-agent.service") == "ssh-agent"
    assert cpu.expected_exception_role("gpg-agent", UID, f"{APP}/gpg-agent.service") == "gpg-agent"
    assert cpu.expected_exception_role("ssh-agent", UID, None) is None
