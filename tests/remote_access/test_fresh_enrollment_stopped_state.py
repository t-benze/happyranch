"""THR-228 seq275 — bounded affirmative stopped-state enforcement.

These tests drive the *production* fresh-enrollment seam: the real
``runtime.remote_access.cli`` module entry, the real ``systemctl`` subprocess
query and its strict named-property parse against a closed private fixture.
The old boolean service-state injection cannot express a query error, timeout,
unavailable unit or malformed observation, so every case here goes through the
actual command boundary and asserts the complete unchanged snapshot after each
refusal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import types

import pytest

from runtime.remote_access import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
_SIDECAR_UNIT = "happyranch-tsnet-sidecar.service"
_SHOW_ARGV = f"show -p LoadState -p ActiveState -p SubState {_SIDECAR_UNIT}"
_STOPPED = b"LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
_DROPIN_BYTES = b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
_SIBLING_BYTES = b"# unrelated sibling\n"
_CATEGORY = "error: fresh_enrollment_transition_failed"


def _write_systemctl(bindir: Path) -> Path:
    """Install the closed private ``systemctl`` fixture (never the host manager)."""
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "systemctl"
    script.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$SYSTEMCTL_CALLS"\n'
        'if [ -n "$SYSTEMCTL_PIDFILE" ]; then\n'
        '  printf \'%s\' "$$" > "$SYSTEMCTL_PIDFILE"\n'
        '  exec sleep "$SYSTEMCTL_SLEEP"\n'
        "fi\n"
        'case "$1" in\n'
        "  show)\n"
        '    cat "$SYSTEMCTL_SHOW_OUTPUT"\n'
        '    exit "$SYSTEMCTL_SHOW_EXIT"\n'
        "    ;;\n"
        "  daemon-reload)\n"
        '    exit "$SYSTEMCTL_RELOAD_EXIT"\n'
        "    ;;\n"
        "  *)\n"
        "    exit 99\n"
        "    ;;\n"
        "esac\n"
    )
    script.chmod(0o700)
    return script


def _enrollment_case(
    root: Path,
    *,
    marker: bool = True,
    dropin: bool = False,
    sibling: bool = True,
    source_mode: int = 0o600,
) -> dict[str, Path]:
    """Create one synthetic fresh-enrollment fixture (no live credential)."""
    case = root / "case"
    (case / "state").mkdir(parents=True)
    (case / "unit.d").mkdir()
    source = case / "enrollment.key"
    source.write_bytes(b"synthetic-one-use\n")
    source.chmod(source_mode)
    marker_path = case / "state" / "credential.consumed"
    if marker:
        marker_path.write_bytes(b"synthetic-consumed\n")
        marker_path.chmod(0o600)
    dropin_path = case / "unit.d" / "10-enrollment-credential.conf"
    if dropin:
        dropin_path.write_bytes(b"[Service]\nLoadCredential=enrollment.key:/prior\n")
        dropin_path.chmod(0o640)
    if sibling:
        sibling_path = case / "unit.d" / "zz-unrelated.conf"
        sibling_path.write_bytes(_SIBLING_BYTES)
        sibling_path.chmod(0o644)
    return {"case": case, "source": source, "marker": marker_path, "dropin": dropin_path}


def _snapshot(root: Path) -> dict[str, list[object]]:
    """Complete lstat identity (type/mode/uid/gid + bytes) of a fixture tree."""
    entries: dict[str, list[object]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        value: list[object] = [
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_uid,
            info.st_gid,
        ]
        if stat.S_ISREG(info.st_mode):
            value.append(path.read_bytes())
        entries[str(path.relative_to(root))] = value
    return entries


def _fixture_env(
    root: Path,
    *,
    bindir: Path,
    show_output: bytes,
    show_exit: int = 0,
    reload_exit: int = 0,
) -> tuple[dict[str, str], Path]:
    calls = root / "systemctl.calls"
    calls.write_bytes(b"")
    show_file = root / "systemctl.show"
    show_file.write_bytes(show_output)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    }
    env.update(
        {
            "PATH": str(bindir) + os.pathsep + env.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SYSTEMCTL_CALLS": str(calls),
            "SYSTEMCTL_SHOW_OUTPUT": str(show_file),
            "SYSTEMCTL_SHOW_EXIT": str(show_exit),
            "SYSTEMCTL_RELOAD_EXIT": str(reload_exit),
        }
    )
    return env, calls


def _invoke_cli(fixture: dict[str, Path], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "runtime.remote_access.cli",
            "prepare-fresh-enrollment",
            "--source",
            str(fixture["source"]),
            "--marker",
            str(fixture["marker"]),
            "--dropin",
            str(fixture["dropin"]),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_subprocess_imports_candidate_source(tmp_path: Path) -> None:
    """Pin candidate source identity for every real-CLI case."""
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    env, _ = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED)
    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import runtime.remote_access.cli as c; print(c.__file__)",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert Path(probe.stdout.strip()) == REPO_ROOT / "runtime" / "remote_access" / "cli.py"


_REFUSALS: list[tuple[str, bytes, int]] = [
    ("active", b"LoadState=loaded\nActiveState=active\nSubState=running\n", 0),
    ("activating", b"LoadState=loaded\nActiveState=activating\nSubState=start\n", 0),
    ("deactivating", b"LoadState=loaded\nActiveState=deactivating\nSubState=stop\n", 0),
    ("reloading", b"LoadState=loaded\nActiveState=reloading\nSubState=reload\n", 0),
    ("failed", b"LoadState=loaded\nActiveState=failed\nSubState=failed\n", 0),
    ("load-not-found", b"LoadState=not-found\nActiveState=inactive\nSubState=dead\n", 0),
    ("load-masked", b"LoadState=masked\nActiveState=inactive\nSubState=dead\n", 0),
    ("unknown-active-state", b"LoadState=loaded\nActiveState=unknown\nSubState=dead\n", 0),
    ("missing-sub-state", b"LoadState=loaded\nActiveState=inactive\n", 0),
    ("missing-load-state", b"ActiveState=inactive\nSubState=dead\n", 0),
    (
        "duplicate-active-state",
        b"LoadState=loaded\nActiveState=inactive\nActiveState=inactive\nSubState=dead\n",
        0,
    ),
    (
        "extra-property",
        b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n",
        0,
    ),
    ("contradictory-sub-state", b"LoadState=loaded\nActiveState=inactive\nSubState=running\n", 0),
    ("empty-output", b"", 0),
    ("no-separator", b"LoadState\nActiveState=inactive\nSubState=dead\n", 0),
    ("blank-line", b"LoadState=loaded\n\nActiveState=inactive\nSubState=dead\n", 0),
    ("control-bearing-value", b"LoadState=loaded\nActiveState=inactive\nSubState=dead\x01evil\n", 0),
    ("whitespace-value", b"LoadState=loaded\nActiveState=inactive\nSubState= \n", 0),
    ("trailing-space-value", b"LoadState=loaded\nActiveState=inactive\nSubState=dead \n", 0),
    ("non-utf8", b"LoadState=loaded\nActiveState=inactive\nSubState=\xff\xfe\n", 0),
    ("oversize", b"ActiveState=" + b"a" * 5000 + b"\n", 0),
    ("nonzero-exit-affirmative-looking", _STOPPED, 1),
    ("nonzero-exit-empty", b"", 3),
    ("empty-final-record", b"LoadState=loaded\nActiveState=inactive\nSubState=dead\n\n", 0),
]


@pytest.mark.parametrize(
    ("label", "show_output", "show_exit"),
    _REFUSALS,
    ids=[case[0] for case in _REFUSALS],
)
def test_refusals_leave_complete_snapshot_unchanged(
    tmp_path: Path, label: str, show_output: bytes, show_exit: int
) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _fixture_env(
        tmp_path, bindir=bindir, show_output=show_output, show_exit=show_exit
    )
    before = _snapshot(fixture["case"])
    for attempt in (1, 2):
        result = _invoke_cli(fixture, env)
        assert (result.returncode, result.stdout) == (1, ""), (label, attempt, result.stderr)
        assert result.stderr.strip() == _CATEGORY, (label, attempt)
        assert _snapshot(fixture["case"]) == before, (label, attempt)
        assert not fixture["dropin"].with_name(fixture["dropin"].name + ".new").exists()
    # The property query ran once per invocation and no reload was ever reached.
    assert calls.read_text().splitlines() == [_SHOW_ARGV, _SHOW_ARGV], label


@pytest.mark.parametrize(
    ("label", "show_output"),
    [
        ("canonical", _STOPPED),
        (
            "reordered-properties",
            b"SubState=dead\nActiveState=inactive\nLoadState=loaded\n",
        ),
    ],
    ids=["canonical", "reordered-properties"],
)
def test_stopped_positive_performs_exact_transition(
    tmp_path: Path, label: str, show_output: bytes
) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=False)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=show_output)
    result = _invoke_cli(fixture, env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), label
    assert not fixture["marker"].exists()
    assert fixture["dropin"].read_bytes() == _DROPIN_BYTES
    assert fixture["dropin"].stat().st_mode & 0o777 == 0o600
    assert fixture["source"].read_bytes() == b"synthetic-one-use\n"
    assert fixture["source"].stat().st_mode & 0o777 == 0o600
    assert (fixture["case"] / "unit.d" / "zz-unrelated.conf").read_bytes() == _SIBLING_BYTES
    assert calls.read_text().splitlines() == [_SHOW_ARGV, "daemon-reload"], label


def test_stopped_positive_without_marker_and_with_prior_dropin(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=False, dropin=True)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED)
    result = _invoke_cli(fixture, env)
    assert (result.returncode, result.stderr) == (0, "")
    assert not fixture["marker"].exists()
    assert fixture["dropin"].read_bytes() == _DROPIN_BYTES
    assert fixture["dropin"].stat().st_mode & 0o777 == 0o600
    assert calls.read_text().splitlines() == [_SHOW_ARGV, "daemon-reload"]


def test_source_capability_refusal_is_retained_after_stopped_proof(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True, source_mode=0o644)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED)
    before = _snapshot(fixture["case"])
    result = _invoke_cli(fixture, env)
    # The existing credential-capability refusal (its pre-existing
    # PackageError presentation is deliberately unchanged and outside the
    # seq275 stopped-state radius) still happens after the stopped proof and
    # before any mutation or reload.
    assert result.returncode == 1
    assert _snapshot(fixture["case"]) == before
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


def test_executable_unavailable_refuses_unchanged(tmp_path: Path) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, _ = _fixture_env(tmp_path, bindir=empty_bin, show_output=_STOPPED)
    env["PATH"] = str(empty_bin)
    before = _snapshot(fixture["case"])
    result = _invoke_cli(fixture, env)
    assert (result.returncode, result.stderr.strip()) == (1, _CATEGORY)
    assert _snapshot(fixture["case"]) == before


def test_bounded_timeout_refuses_and_reaps_child(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, _ = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED)
    pid_file = tmp_path / "systemctl.pid"
    env["SYSTEMCTL_PIDFILE"] = str(pid_file)
    env["SYSTEMCTL_SLEEP"] = "30"
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    result = _invoke_cli(fixture, env)
    elapsed = time.monotonic() - started
    # Production bound is 5s; the outer test bound is 30s. Whatever the slack,
    # the CLI must have refused well before the fixture's 30s sleep completed.
    assert 4.0 <= elapsed < 20.0, elapsed
    assert (result.returncode, result.stderr.strip()) == (1, _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_observe_sidecar_stopped_pins_query_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert cli._observe_sidecar_stopped() is None
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


def test_observe_sidecar_stopped_refuses_query_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    env, _ = _fixture_env(tmp_path, bindir=bindir, show_output=_STOPPED, show_exit=1)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(OSError, match="service state unavailable"):
        cli._observe_sidecar_stopped()


# ---------------------------------------------------------------------------
# TASK8607 F1 — raw record framing is validated before any normalization.
# ---------------------------------------------------------------------------

_UNSUPPORTED_FRAMING: list[tuple[str, bytes]] = [
    ("carriage-return", b"LoadState=loaded\rActiveState=inactive\rSubState=dead\n"),
    ("crlf", b"LoadState=loaded\r\nActiveState=inactive\r\nSubState=dead\r\n"),
    ("file-separator", b"LoadState=loaded\x1cActiveState=inactive\x1cSubState=dead\n"),
    ("group-separator", b"LoadState=loaded\x1dActiveState=inactive\x1dSubState=dead\n"),
    ("record-separator", b"LoadState=loaded\x1eActiveState=inactive\x1eSubState=dead\n"),
    ("vertical-tab", b"LoadState=loaded\x0bActiveState=inactive\x0bSubState=dead\n"),
    ("form-feed", b"LoadState=loaded\x0cActiveState=inactive\x0cSubState=dead\n"),
    ("nel", b"LoadState=loaded\xc2\x85ActiveState=inactive\xc2\x85SubState=dead\n"),
    (
        "unicode-line-separator",
        "LoadState=loaded\u2028ActiveState=inactive\u2028SubState=dead\n".encode(),
    ),
    (
        "unicode-paragraph-separator",
        "LoadState=loaded\u2029ActiveState=inactive\u2029SubState=dead\n".encode(),
    ),
    ("embedded-nul", b"LoadState=loaded\nActiveState=inactive\nSubState=dead\x00\n"),
    ("embedded-escape", b"LoadState=loaded\nActiveState=inactive\nSubState=dead\x1bevil\n"),
]


@pytest.mark.parametrize(
    ("label", "show_output"),
    _UNSUPPORTED_FRAMING,
    ids=[case[0] for case in _UNSUPPORTED_FRAMING],
)
def test_unsupported_record_framing_refuses_unchanged(
    tmp_path: Path, label: str, show_output: bytes
) -> None:
    """A non-LF separator must never be normalized into a record boundary."""
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=show_output)
    before = _snapshot(fixture["case"])
    for attempt in (1, 2):
        result = _invoke_cli(fixture, env)
        assert (result.returncode, result.stdout) == (1, ""), (label, attempt, result.stderr)
        assert result.stderr.strip() == _CATEGORY, (label, attempt, result.stderr)
        assert _snapshot(fixture["case"]) == before, (label, attempt)
        assert not fixture["dropin"].with_name(fixture["dropin"].name + ".new").exists()
    assert calls.read_text().splitlines() == [_SHOW_ARGV, _SHOW_ARGV], label


@pytest.mark.parametrize(
    ("label", "show_output"),
    [
        ("trailing-lf", _STOPPED),
        ("no-trailing-lf", b"LoadState=loaded\nActiveState=inactive\nSubState=dead"),
        (
            "reordered-no-trailing-lf",
            b"SubState=dead\nActiveState=inactive\nLoadState=loaded",
        ),
    ],
    ids=["trailing-lf", "no-trailing-lf", "reordered-no-trailing-lf"],
)
def test_supported_final_record_forms_authorize_transition(
    tmp_path: Path, label: str, show_output: bytes
) -> None:
    """The chosen supported format is LF-delimited records with an optional
    single trailing LF; every property permutation stays order-independent."""
    bindir = tmp_path / "bin"
    _write_systemctl(bindir)
    fixture = _enrollment_case(tmp_path, marker=True, dropin=False)
    env, calls = _fixture_env(tmp_path, bindir=bindir, show_output=show_output)
    result = _invoke_cli(fixture, env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), label
    assert not fixture["marker"].exists()
    assert fixture["dropin"].read_bytes() == _DROPIN_BYTES
    assert calls.read_text().splitlines() == [_SHOW_ARGV, "daemon-reload"], label


# ---------------------------------------------------------------------------
# TASK8607 F2 — bounded streaming capture, one absolute deadline, owned reaping.
# ---------------------------------------------------------------------------


def _write_producer_systemctl(bindir: Path, code: str) -> Path:
    """Install a closed fixture whose ``show`` execs one owned Python producer.

    ``exec`` replaces the shell, so the producer is our direct child and its PID
    is stable for reaping observation. It never touches the host manager.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    producer = bindir / "producer.py"
    producer.write_text(code, encoding="utf-8")
    script = bindir / "systemctl"
    script.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$SYSTEMCTL_CALLS"\n'
        'if [ "$1" = "show" ]; then\n'
        '  exec "$PRODUCER_PYTHON" "$PRODUCER_SCRIPT"\n'
        "fi\n"
        "exit 0\n"
    )
    script.chmod(0o700)
    return producer


def _producer_env(
    root: Path, *, bindir: Path, producer: Path, tmp_path: Path, **extra: str
) -> tuple[dict[str, str], Path]:
    env, calls = _fixture_env(root, bindir=bindir, show_output=b"")
    env.update(
        {
            "PRODUCER_PYTHON": sys.executable,
            "PRODUCER_SCRIPT": str(producer),
            "PRODUCER_PIDFILE": str(tmp_path / "producer.pid"),
            "PRODUCER_DONE": str(tmp_path / "producer.done"),
            **extra,
        }
    )
    return env, calls


def _assert_reaped(pid_file: Path) -> None:
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_streaming_producer_over_cap_is_stopped_and_reaped(tmp_path: Path) -> None:
    """A sustained over-cap stream must be cut off during capture, not drained."""
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(
        bindir,
        "import os\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "target = int(os.environ['PRODUCER_BYTES'])\n"
        "chunk = b'x' * 4096\n"
        "written = 0\n"
        "while written < target:\n"
        "    step = min(4096, target - written)\n"
        "    os.write(1, chunk[:step])\n"
        "    written += step\n"
        "open(os.environ['PRODUCER_DONE'], 'w').write('delivered')\n",
    )
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _producer_env(
        tmp_path,
        bindir=bindir,
        producer=producer,
        tmp_path=tmp_path,
        PRODUCER_BYTES=str(8 * 1024 * 1024),
    )
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    result = _invoke_cli(fixture, env)
    elapsed = time.monotonic() - started
    assert (result.returncode, result.stdout, result.stderr.strip()) == (1, "", _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    # The 8 MiB producer never reached its completion marker: the reader stopped
    # at the cap instead of draining the whole stream.
    assert not (tmp_path / "producer.done").exists()
    assert not fixture["dropin"].with_name(fixture["dropin"].name + ".new").exists()
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert elapsed < 5.0, elapsed
    _assert_reaped(tmp_path / "producer.pid")


def test_capture_retains_at_most_cap_plus_one(tmp_path: Path) -> None:
    """Focused finite-retention assertion for the bounded reader itself."""
    code = (
        "import os\n"
        "target = int(os.environ['PRODUCER_BYTES'])\n"
        "chunk = b'y' * 4096\n"
        "written = 0\n"
        "while written < target:\n"
        "    step = min(4096, target - written)\n"
        "    os.write(1, chunk[:step])\n"
        "    written += step\n"
    )
    long_process = subprocess.Popen(
        [sys.executable, "-c", code],
        env={**os.environ, "PRODUCER_BYTES": str(64 * 1024)},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5.0
    retained, overflowed = cli._capture_bounded_query_output(long_process, deadline)
    cli._close_and_reap_query_process(long_process, deadline)
    assert overflowed is True
    assert len(retained) == cli._SERVICE_QUERY_MAX_OUTPUT_BYTES + 1

    short_process = subprocess.Popen(
        [sys.executable, "-c", code],
        env={**os.environ, "PRODUCER_BYTES": "137"},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5.0
    retained, overflowed = cli._capture_bounded_query_output(short_process, deadline)
    cli._close_and_reap_query_process(short_process, deadline)
    assert overflowed is False
    assert retained == b"y" * 137


def test_slow_partial_output_refuses_on_absolute_deadline(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(
        bindir,
        "import os, time\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\n')\n"
        "time.sleep(float(os.environ['PRODUCER_SLEEP']))\n"
        "os.write(1, b'ActiveState=inactive\\nSubState=dead\\n')\n",
    )
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _producer_env(
        tmp_path, bindir=bindir, producer=producer, tmp_path=tmp_path, PRODUCER_SLEEP="30"
    )
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    result = _invoke_cli(fixture, env)
    elapsed = time.monotonic() - started
    assert (result.returncode, result.stderr.strip()) == (1, _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert 4.0 <= elapsed < 20.0, elapsed
    _assert_reaped(tmp_path / "producer.pid")


def test_eof_with_lingering_child_refuses_and_reaps(tmp_path: Path) -> None:
    """Valid-looking output is not sufficient: the owned child must also exit."""
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(
        bindir,
        "import os, time\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n"
        "os.close(1)\n"
        "time.sleep(float(os.environ['PRODUCER_SLEEP']))\n",
    )
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _producer_env(
        tmp_path, bindir=bindir, producer=producer, tmp_path=tmp_path, PRODUCER_SLEEP="30"
    )
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    result = _invoke_cli(fixture, env)
    elapsed = time.monotonic() - started
    assert (result.returncode, result.stderr.strip()) == (1, _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert 4.0 <= elapsed < 20.0, elapsed
    _assert_reaped(tmp_path / "producer.pid")


def test_valid_output_then_nonzero_exit_refuses(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(
        bindir,
        "import os\n"
        "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n"
        "raise SystemExit(3)\n",
    )
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls = _producer_env(tmp_path, bindir=bindir, producer=producer, tmp_path=tmp_path)
    before = _snapshot(fixture["case"])
    result = _invoke_cli(fixture, env)
    assert (result.returncode, result.stdout, result.stderr.strip()) == (1, "", _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


# ---------------------------------------------------------------------------
# TASK8623 F2 — the observer confirms its owned child is reaped on return.
#
# These cases drive the production ``_observe_sidecar_stopped`` seam in-process
# against a real controlled ``systemctl`` child. The owned ``Popen`` is captured
# and its reaped/pipe-closed state is asserted at the instant the observer
# returns — before any fixture cleanup or test-side wait/poll that could perform
# the reaping itself. The retained TASK8607 CLI-level cases above cannot see
# this: they only poll the child PID after the CLI process has exited, by which
# time process teardown has already reaped it.
# ---------------------------------------------------------------------------


def _observe_owned_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    producer_code: str,
    **extra_env: str,
) -> tuple[subprocess.Popen[bytes], OSError | None, float, Path]:
    """Drive the real observer in-process; capture the exact owned child."""
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(bindir, producer_code)
    env, calls = _producer_env(
        tmp_path, bindir=bindir, producer=producer, tmp_path=tmp_path, **extra_env
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    owned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(*args, **kwargs)
        owned.append(child)
        return child

    # Localize the patch to the production module's ``subprocess`` reference so
    # the test process itself keeps the real ``Popen``.
    monkeypatch.setattr(
        cli,
        "subprocess",
        types.SimpleNamespace(
            Popen=recording_popen,
            PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    started = time.monotonic()
    error: OSError | None = None
    try:
        cli._observe_sidecar_stopped()
    except OSError as exc:
        error = exc
    elapsed = time.monotonic() - started
    assert len(owned) == 1, owned
    return owned[0], error, elapsed, calls


def _assert_owned_child_reaped_now(child: subprocess.Popen[bytes]) -> None:
    """Ownership assertions only — no wait/poll that could reap for us."""
    assert child.returncode is not None, "observer returned with owned child unreaped"
    assert child.stdout is not None and child.stdout.closed, "owned pipe still open"


def test_observer_confirms_reaping_on_no_output_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os, time\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n",
    )
    # Ownership first: the observer itself must have killed and reaped its child.
    _assert_owned_child_reaped_now(child)
    assert isinstance(error, OSError)
    assert "service state unavailable" in str(error)
    assert 4.0 <= elapsed < 20.0, elapsed
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert int((tmp_path / "producer.pid").read_text()) == child.pid


def test_observer_confirms_reaping_on_slow_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os, time\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\n')\n"
        "time.sleep(float(os.environ['PRODUCER_SLEEP']))\n"
        "os.write(1, b'ActiveState=inactive\\nSubState=dead\\n')\n",
        PRODUCER_SLEEP="30",
    )
    _assert_owned_child_reaped_now(child)
    assert isinstance(error, OSError)
    assert 4.0 <= elapsed < 20.0, elapsed
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert int((tmp_path / "producer.pid").read_text()) == child.pid


def test_observer_confirms_reaping_on_eof_with_lingering_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os, time\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n"
        "os.close(1)\n"
        "time.sleep(float(os.environ['PRODUCER_SLEEP']))\n",
        PRODUCER_SLEEP="30",
    )
    _assert_owned_child_reaped_now(child)
    assert isinstance(error, OSError)
    assert 4.0 <= elapsed < 20.0, elapsed
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert int((tmp_path / "producer.pid").read_text()) == child.pid


def test_observer_confirms_reaping_on_overflow_while_producer_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "target = int(os.environ['PRODUCER_BYTES'])\n"
        "chunk = b'x' * 4096\n"
        "written = 0\n"
        "while written < target:\n"
        "    step = min(4096, target - written)\n"
        "    os.write(1, chunk[:step])\n"
        "    written += step\n",
        PRODUCER_BYTES=str(8 * 1024 * 1024),
    )
    _assert_owned_child_reaped_now(child)
    assert isinstance(error, OSError)
    assert elapsed < 5.0, elapsed
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
    assert int((tmp_path / "producer.pid").read_text()) == child.pid


def test_observer_confirms_reaping_on_stopped_success_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n",
    )
    _assert_owned_child_reaped_now(child)
    assert error is None
    assert child.returncode == 0
    assert elapsed < 5.0, elapsed
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


def test_observer_confirms_reaping_on_nonzero_exit_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child, error, elapsed, calls = _observe_owned_child(
        tmp_path,
        monkeypatch,
        "import os\n"
        "open(os.environ['PRODUCER_PIDFILE'], 'w').write(str(os.getpid()))\n"
        "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n"
        "raise SystemExit(3)\n",
    )
    _assert_owned_child_reaped_now(child)
    assert isinstance(error, OSError)
    assert child.returncode == 3
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


def test_close_and_reap_query_process_reaps_when_deadline_expired() -> None:
    """Direct helper regression mirroring the retained manager red proof."""
    for _ in range(3):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; print('entered', flush=True); time.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert child.stdout.readline() == b"entered\n"
        cli._close_and_reap_query_process(child, time.monotonic() - 0.01)
        # Inspect the owned state before any test-side wait/poll.
        assert child.returncode is not None
        assert child.stdout.closed is True
        child.wait(timeout=2)


# ---------------------------------------------------------------------------
# TASK8644 R1 — a query that completes successfully after the absolute deadline
# must still be refused. The causal case is a *real scheduling pause* of the
# isolated observer across the one deadline: the closed fixture writes valid
# stopped bytes, closes stdout, then SIGSTOPs exactly its parent (the observer),
# sleeps past the 5 s budget and exits 0. When the observer resumes, a resumed
# POSIX wait can collect that already-exited child and report success. No
# candidate source, time or subprocess return value is mocked.
# ---------------------------------------------------------------------------


_LATE_EXIT_PRODUCER = (
    "import os, signal, time\n"
    "from pathlib import Path\n"
    "Path(os.environ['QUERY_PID']).write_text(str(os.getpid()))\n"
    "Path(os.environ['QUERY_PARENT']).write_text(str(os.getppid()))\n"
    "Path(os.environ['QUERY_START_AT']).write_text(str(time.monotonic()))\n"
    "os.write(1, b'LoadState=loaded\\nActiveState=inactive\\nSubState=dead\\n')\n"
    "os.close(1)\n"
    "time.sleep(0.2)\n"
    "os.kill(os.getppid(), signal.SIGSTOP)\n"
    "time.sleep(5.2)\n"
    "Path(os.environ['QUERY_EXIT_AT']).write_text(str(time.monotonic()))\n"
    "os._exit(0)\n"
)

# This driver runs the *real* production ``_observe_sidecar_stopped`` in-process
# and records the state of the Popen it owns at the exact instant the observer
# returns, so the test never has to wait/poll/reap the product's child itself.
_LATE_EXIT_INPROCESS_DRIVER = '''\
import json
import subprocess
import sys
import types

sys.path.insert(0, sys.argv[2])
from runtime.remote_access import cli

owned = []
real_popen = subprocess.Popen


def recording_popen(*args, **kwargs):
    child = real_popen(*args, **kwargs)
    owned.append(child)
    return child


cli.subprocess = types.SimpleNamespace(
    Popen=recording_popen,
    PIPE=subprocess.PIPE,
    DEVNULL=subprocess.DEVNULL,
    TimeoutExpired=subprocess.TimeoutExpired,
)
error = None
try:
    cli._observe_sidecar_stopped()
except OSError as exc:
    error = str(exc)
child = owned[0] if owned else None
payload = {
    "error": error,
    "owned_count": len(owned),
    "returncode": None if child is None else child.returncode,
    "stdout_closed": None if child is None or child.stdout is None else child.stdout.closed,
}
with open(sys.argv[1], "w") as handle:
    json.dump(payload, handle)
'''


def _wait_for_file(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"fixture file never appeared: {path}")


def _wait_for_process_stopped(pid: int, timeout: float) -> bool:
    """Wait until the kernel reports this process in the stopped ('T') state."""
    deadline = time.monotonic() + timeout
    stat_path = Path(f"/proc/{pid}/stat")
    while time.monotonic() < deadline:
        try:
            text = stat_path.read_text()
        except OSError:
            return False
        closing = text.rfind(")")
        if closing != -1 and len(text) > closing + 2 and text[closing + 2] == "T":
            return True
        time.sleep(0.02)
    return False


def _resume_and_collect(process: subprocess.Popen[bytes], query_parent: Path, started: float) -> None:
    """Confirm the fixture really paused the observer, then resume past expiry."""
    _wait_for_file(query_parent, 10.0)
    assert _wait_for_process_stopped(process.pid, 10.0), "fixture never paused the observer"
    remaining = (started + 6.2) - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    os.kill(process.pid, signal.SIGCONT)


def _late_exit_env(
    tmp_path: Path, fixture: dict[str, Path]
) -> tuple[dict[str, str], Path, dict[str, Path]]:
    bindir = tmp_path / "bin"
    producer = _write_producer_systemctl(bindir, _LATE_EXIT_PRODUCER)
    env, calls = _producer_env(tmp_path, bindir=bindir, producer=producer, tmp_path=tmp_path)
    markers = {
        "pid": tmp_path / "query.pid",
        "parent": tmp_path / "query.parent",
        "start": tmp_path / "query.start-at",
        "exit": tmp_path / "query.exit-at",
    }
    env.update(
        QUERY_PID=str(markers["pid"]),
        QUERY_PARENT=str(markers["parent"]),
        QUERY_START_AT=str(markers["start"]),
        QUERY_EXIT_AT=str(markers["exit"]),
    )
    return env, calls, markers


def _late_query_lifetime(markers: dict[str, Path]) -> float:
    return float(markers["exit"].read_text()) - float(markers["start"].read_text())


@pytest.mark.parametrize("attempt", (1, 2))
def test_late_exit_after_absolute_deadline_refuses_unchanged(
    tmp_path: Path, attempt: int
) -> None:
    """A successful exit observed after the 5 s budget must not mutate anything."""
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls, markers = _late_exit_env(tmp_path, fixture)
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "runtime.remote_access.cli",
            "prepare-fresh-enrollment",
            "--source",
            str(fixture["source"]),
            "--marker",
            str(fixture["marker"]),
            "--dropin",
            str(fixture["dropin"]),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _resume_and_collect(process, markers["parent"], started)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.returncode is None:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            process.kill()
            process.wait(timeout=5)
    # The query truly outlived the one absolute five-second observation budget.
    assert _late_query_lifetime(markers) > 5.0
    assert (process.returncode, stdout, stderr.strip()) == (1, "", _CATEGORY)
    assert _snapshot(fixture["case"]) == before
    assert not fixture["dropin"].with_name(fixture["dropin"].name + ".new").exists()
    assert calls.read_text().splitlines() == [_SHOW_ARGV]


def test_late_exit_refusal_reaps_owned_child_before_return(tmp_path: Path) -> None:
    """Owned reaping/pipe closure at the observer return instant, late-exit path."""
    fixture = _enrollment_case(tmp_path, marker=True, dropin=True)
    env, calls, markers = _late_exit_env(tmp_path, fixture)
    driver = tmp_path / "observer_driver.py"
    driver.write_text(_LATE_EXIT_INPROCESS_DRIVER, encoding="utf-8")
    result_path = tmp_path / "observer-result.json"
    before = _snapshot(fixture["case"])
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-B", str(driver), str(result_path), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _resume_and_collect(process, markers["parent"], started)
        process.communicate(timeout=30)
    finally:
        if process.returncode is None:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            process.kill()
            process.wait(timeout=5)
    assert process.returncode == 0, process.stderr
    assert _late_query_lifetime(markers) > 5.0
    result = json.loads(result_path.read_text())
    # Ownership is asserted at the observer's own return, before any test-side
    # wait/poll/fixture cleanup could have reaped the owned child for it.
    assert result["owned_count"] == 1
    assert result["error"] == "service state unavailable"
    assert result["returncode"] is not None
    assert result["stdout_closed"] is True
    assert _snapshot(fixture["case"]) == before
    assert calls.read_text().splitlines() == [_SHOW_ARGV]
