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
import os
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


def _load_helper_module():
    assert HELPER.is_file(), f"missing packaged helper: {HELPER}"
    spec = importlib.util.spec_from_file_location("wc_check_path_use_static", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Loaded once at collection so module-level fault-injection subclasses can be
# defined; the ``cpu`` fixture below loads an equivalent module for the tests.
_HC = _load_helper_module()


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
    # Inject the fixture uid explicitly. ``scan`` otherwise falls back to
    # ``os.getuid()``, which differs from the fixture uid on CI runners
    # (runner uid 1001 vs fixture 1000) and would classify every fake
    # same-user process as ``other_user``.
    return cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID, **kw)


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


def test_scan_membership_uses_injected_agent_uid_not_host_uid(cpu):
    """Membership must come from the injected ``agent_uid``.

    CI runs the suite as a different uid than the fixture (GitHub ``runner``
    uid 1001 vs fixture 1000). The scan must classify the deterministic
    FakeProc population from the injected uid, never the host process uid.
    """
    other_uid = 4242 if os.getuid() != 4242 else 4243
    user_slice = f"/user.slice/user-{other_uid}.slice"
    app = f"{user_slice}/user@{other_uid}.service/app.slice"
    p = _spec("640", uid=other_uid, comm="ssh-agent",
              cgroup=f"{app}/ssh-agent.service")
    res = cpu.scan(TARGET, proc=_proc(cpu, p), self_pid=SELF,
                   agent_uid=other_uid)
    assert res.coverage["agent_uid"] == other_uid
    assert _exempt_roles(res) == ["ssh-agent"]
    assert res.coverage["same_user"] == 1
    # The same population is out of scope under a different agent uid.
    foreign = cpu.scan(TARGET, proc=_proc(cpu, p), self_pid=SELF,
                       agent_uid=os.getuid())
    assert foreign.exempt == []
    assert foreign.coverage["other_user"] == 1


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


# ── F1 containing worktree ────────────────────────────────────────────────

CACHE_WT = "/work/wt/TASK-X"
CACHE = CACHE_WT + "/node_modules"
CMAP = {CACHE_WT: (2049, 42), CACHE: (2049, 77)}


def _cache_proc(cpu, cls=None, member_cwd=CACHE_WT + "/src", **member_kw):
    cls = cls or cpu.FakeProc
    self_p = _spec(SELF, comm="check_path_use")
    member = _spec("600", cwd=member_cwd, **member_kw)
    return cls({SELF: self_p, "600": member}, stat_map=dict(CMAP))


def test_f1_cache_candidate_observes_occupied_containing_worktree(cpu):
    res = cpu.scan(CACHE, proc=_cache_proc(cpu), self_pid=SELF, agent_uid=UID)
    assert res.state == "blocked"
    assert res.coverage["containing_worktree"] == CACHE_WT


def test_f1_explicit_containing_worktree_argument(cpu):
    res = cpu.scan(CACHE, proc=_cache_proc(cpu), self_pid=SELF, agent_uid=UID,
                   containing_worktree=CACHE_WT)
    assert res.state == "blocked"


def test_f1_missing_containing_worktree_is_unknown_not_fallback(cpu):
    # The cache itself resolves but its containing worktree does not: unknown.
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600")},
                        stat_map={CACHE: (2049, 77)})
    res = cpu.scan(CACHE, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert "containing_worktree_unavailable" in res.reasons


def test_f1_cache_sibling_without_explicit_scan_would_have_cleared(cpu):
    # The literal path alone is clear; only the containing worktree blocks.
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600", cwd=CACHE_WT + "/src")},
                        stat_map={CACHE: (2049, 77)})
    literal_only = cpu.scan(CACHE, proc=proc, self_pid=SELF, agent_uid=UID,
                            containing_worktree=CACHE)
    assert literal_only.state == "clear_observation"


# ── F2 identity / exit semantics ──────────────────────────────────────────


class _CredentialChange(_HC.FakeProc):
    def read_text(self, pid, rel, limit):
        if pid == "600" and rel == "stat":
            self.n = getattr(self, "n", 0) + 1
            if self.n == 2:
                self.spec[pid]["uid"] = [UID, UID, UID, UID + 1]
        return super().read_text(pid, rel, limit)


def test_f2_credential_change_during_identity_recheck_is_unknown(cpu):
    res = cpu.scan(TARGET, proc=_cache_proc(cpu, _CredentialChange), self_pid=SELF,
                   agent_uid=UID)
    assert res.state == "unknown"
    assert any(r.startswith("credential_change") for r in res.reasons)


class _ReuseDuringScan(_HC.FakeProc):
    def readlink(self, pid, rel):
        if pid == "600" and rel == "cwd":
            self.spec[pid]["starttime"] = 200
        return super().readlink(pid, rel)


def test_f2_pid_reuse_during_member_reads_is_unknown(cpu):
    res = cpu.scan(TARGET, proc=_cache_proc(cpu, _ReuseDuringScan,
                                            member_cwd="/home/benze"),
                   self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("identity_changed_after_scan" in r or "pid_reuse" in r
               for r in res.reasons)


def _threaded_proc(cpu, cls=None, threads=None):
    cls = cls or cpu.FakeProc
    member = _spec("600")
    member["threads"] = threads or {"601": {"cwd": "/home/benze", "root": "/",
                                            "exe": "/usr/bin/bash", "maps": [],
                                            "fds": {}, "starttime": 100}}
    return cls({SELF: _spec(SELF), "600": member}, stat_map=dict(STAT_MAP))


class _TidReuse(_HC.FakeProc):
    def readlink(self, pid, rel):
        if pid == "600" and rel == "task/601/cwd":
            self.spec[pid]["threads"]["601"]["starttime"] = 200
        return super().readlink(pid, rel)


def test_f2_tid_reuse_during_thread_reads_is_unknown(cpu):
    res = cpu.scan(TARGET, proc=_threaded_proc(cpu, _TidReuse), self_pid=SELF,
                   agent_uid=UID)
    assert res.state == "unknown"
    assert any("thread_reuse" in r for r in res.reasons)


class _TidCredentialChange(_HC.FakeProc):
    def readlink(self, pid, rel):
        if pid == "600" and rel == "task/601/cwd":
            self.spec[pid]["threads"]["601"]["uid"] = [UID, UID, UID, UID + 1]
        return super().readlink(pid, rel)


def test_f2_tid_credential_change_during_thread_reads_is_unknown(cpu):
    res = cpu.scan(TARGET, proc=_threaded_proc(cpu, _TidCredentialChange),
                   self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("thread_credential_change" in r for r in res.reasons)


def test_f2_still_present_pid_with_enoent_cwd_is_unknown(cpu):
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600")},
                        vanish=[("600", "cwd")], stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("cwd_vanished_but_present" in r for r in res.reasons)


def test_f2_still_present_tid_with_enoent_maps_is_unknown(cpu):
    proc = _threaded_proc(cpu)
    proc.vanish.add(("600", "task/601/maps"))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("maps_vanished_but_present" in r for r in res.reasons)


def test_f2_confirmed_exit_is_not_unknown(cpu):
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600")},
                        vanish=[("600", "stat")], stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "clear_observation"
    assert res.coverage["exited"] >= 1


def test_f2_permission_error_is_unknown(cpu):
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600")},
                        deny=[("600", "cwd")], stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert res.coverage["denied"] >= 1


def test_f2_positive_hit_precedence_despite_incomplete_evidence(cpu):
    incomplete = _spec("600")
    holder = _spec("601", fds={"3": TARGET + "/held"})
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": incomplete, "601": holder},
                        deny=[("600", "cwd")], stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID)
    assert res.state == "blocked"
    assert res.hits


# ── F3 target identity revalidation ───────────────────────────────────────


class _TargetReplacement(_HC.FakeProc):
    def readlink(self, pid, rel):
        if pid == "600" and rel == "cwd":
            self.stat_map[TARGET] = (2049, 999)
        return super().readlink(pid, rel)


def test_f3_target_replaced_during_scan_is_unknown(cpu):
    res = cpu.scan(TARGET, proc=_cache_proc(cpu, _TargetReplacement, member_cwd="/home/benze"),
                   self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("target_identity_changed" in r for r in res.reasons)


class _ContainingReplacement(_HC.FakeProc):
    def readlink(self, pid, rel):
        if pid == "600" and rel == "cwd":
            self.stat_map[CACHE_WT] = (2049, 999)
        return super().readlink(pid, rel)


def test_f3_containing_worktree_replaced_during_scan_is_unknown(cpu):
    res = cpu.scan(CACHE,
                   proc=_cache_proc(cpu, _ContainingReplacement,
                                    member_cwd="/home/benze"),
                   self_pid=SELF, agent_uid=UID)
    assert res.state == "unknown"
    assert any("target_identity_changed" in r for r in res.reasons)


def test_f3_realproc_stat_cache_cannot_mask_final_check(cpu, tmp_path):
    real = cpu.RealProc()
    target = tmp_path / "candidate"
    target.write_text("first\n")
    first = real.stat_path(str(target))
    assert first.kind == cpu.OK
    first_id = (first.value.st_dev, first.value.st_ino)
    # Atomically replace the path with a different inode while the cached stat
    # still holds the original identity.
    replaced = False
    for i in range(50):
        candidate = tmp_path / f"replacement-{i}"
        candidate.write_text("second\n")
        cand_id = (candidate.stat().st_dev, candidate.stat().st_ino)
        if cand_id != first_id:
            candidate.replace(target)
            replaced = True
            break
    assert replaced, "could not obtain a distinct inode for the replacement"
    cached = real.stat_path(str(target))
    fresh = real.stat_path_fresh(str(target))
    assert (cached.value.st_dev, cached.value.st_ino) == first_id
    assert (fresh.value.st_dev, fresh.value.st_ino) != first_id


# ── F4 conflicting cgroup observations ────────────────────────────────────

UNIT = f"{APP}/ssh-agent.service"


def test_f4_conflicting_cgroup_never_exempts_either_order(cpu):
    for cgroup in (f"0::{UNIT}\n1:name=systemd:/unrelated.service\n",
                   f"1:name=systemd:/unrelated.service\n0::{UNIT}\n"):
        p = _spec("600", comm="ssh-agent", cgroup=cgroup,
                  fds={"3": TARGET + "/held"})
        res = _scan(cpu, _proc(cpu, p))
        assert res.exempt == []
        assert res.state == "blocked"


def test_f4_same_path_in_both_hierarchies_still_exempts(cpu):
    p = _spec("600", comm="ssh-agent",
              cgroup=f"0::{UNIT}\n1:name=systemd:{UNIT}\n")
    res = _scan(cpu, _proc(cpu, p))
    assert _exempt_roles(res) == ["ssh-agent"]


# ── F6 shared deadline + caps ─────────────────────────────────────────────


class _LateClock:
    def __init__(self):
        self.now = 0.0
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


class _DeadlineDuringMember(_HC.FakeProc):
    def __init__(self, *a, clock=None, **kw):
        self._clock = clock
        super().__init__(*a, **kw)

    def readlink(self, pid, rel):
        if pid == "600" and rel == "cwd" and self._clock is not None:
            self._clock.now = 100.0
        return super().readlink(pid, rel)


def test_f6_deadline_expiry_inside_member_is_unknown(cpu):
    clock = _LateClock()
    proc = _DeadlineDuringMember(
        {SELF: _spec(SELF), "600": _spec("600")}, clock=clock,
        stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID, clock=clock,
                   bounds=cpu.Bounds(deadline_seconds=1.0))
    assert res.state == "unknown"
    assert "deadline_exceeded" in res.reasons


def test_f6_pid_cap_saturation_is_unknown(cpu):
    proc = cpu.FakeProc({SELF: _spec(SELF), "600": _spec("600"), "601": _spec("601")},
                        stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID,
                   bounds=cpu.Bounds(max_pids=1))
    assert res.state == "unknown"
    assert any("enumeration_truncated" in r for r in res.reasons)


def test_f6_thread_cap_saturation_is_unknown(cpu):
    proc = _threaded_proc(cpu, threads={
        "601": {"cwd": "/home/benze", "root": "/", "exe": "/usr/bin/bash",
                "maps": [], "fds": {}, "starttime": 100},
        "602": {"cwd": "/home/benze", "root": "/", "exe": "/usr/bin/bash",
                "maps": [], "fds": {}, "starttime": 100},
    })
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID,
                   bounds=cpu.Bounds(max_threads=1))
    assert res.state == "unknown"
    assert res.coverage["threads_truncated"] >= 1


class _CountingProc(_HC.FakeProc):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.task_reads = 0

    def listdir(self, pid, rel, max_entries):
        if rel.startswith("task/"):
            self.task_reads += 1
        return super().listdir(pid, rel, max_entries)

    def read_text(self, pid, rel, max_bytes):
        if rel.startswith("task/"):
            self.task_reads += 1
        return super().read_text(pid, rel, max_bytes)

    def readlink(self, pid, rel):
        if rel.startswith("task/"):
            self.task_reads += 1
        return super().readlink(pid, rel)


def test_f6_no_extra_reads_after_thread_cap_exhaustion(cpu):
    proc = _CountingProc(
        {SELF: _spec(SELF), "600": _threaded_proc(cpu).spec["600"]},
        stat_map=dict(STAT_MAP))
    res = cpu.scan(TARGET, proc=proc, self_pid=SELF, agent_uid=UID,
                   bounds=cpu.Bounds(max_threads=0))
    assert res.state == "unknown"
    assert proc.task_reads == 0


# ── shipping CLI invocation (not a differently-called function) ────────────


def test_shipping_cli_invocation_passes_containing_worktree(cpu, monkeypatch,
                                                            capsys):
    import json

    # ``main`` derives the scan subject from the *invoking* user, exactly as the
    # shipped procedure runs. Pin that derivation to the deterministic fixture
    # identity so the outcome never depends on the CI runner's uid (GitHub
    # ``runner`` uid 1001 vs fixture 1000) — the same reason the ``_scan``
    # helper injects ``agent_uid=UID`` explicitly. The shipped argument
    # parsing, containing-worktree plumbing and exit-code mapping are still
    # exercised unchanged.
    monkeypatch.setattr(cpu.os, "getuid", lambda: UID)
    monkeypatch.setattr(cpu.os, "getpid", lambda: int(SELF))
    proc = _cache_proc(cpu)
    monkeypatch.setattr(cpu, "RealProc", lambda *a, **k: proc)
    rc = cpu.main(["--target", CACHE, "--containing-worktree", CACHE_WT, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert payload["state"] == "blocked"
    assert payload["coverage"]["containing_worktree"] == CACHE_WT
