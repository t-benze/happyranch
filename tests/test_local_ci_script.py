"""Tests for ``scripts/local_ci.sh``.

Two behaviors are covered against the real shell entrypoint with controlled
fake tool shims on a temporary ``PATH``:

1. **Node-24 runtime guard (web/all).** The web/all targets must run under
   effective Node.js major exactly 24 (the repository ``.nvmrc`` declaration,
   matching the GitHub "Web (Node 24)" job):

   * a fake ``v26`` node is rejected before any ``uv`` or ``npm`` work runs;
   * a fake ``v24`` node permits the web/all commands to execute through shims;
   * the optional ``nvm`` selection branch selects ``v24`` and re-verifies;
   * a malformed/absent ``.nvmrc`` declaration fails closed.

2. **Per-run pytest basetemp lifecycle (python/integration/all).** Each Python
   pytest invocation must receive an explicit, freshly and uniquely created
   ``--basetemp`` beneath the effective ``TMPDIR``, and the wrapper must remove
   exactly that directory on success, pytest/setup failure and catchable
   HUP/INT/TERM — never ``TMPDIR`` itself or any sibling/pre-existing content.

The fakes shadow the real tools by prepending a temp ``bin`` dir to ``PATH``;
``NVM_DIR``/``HOME`` are pointed at empty dirs so the selection branch cannot
accidentally pick up a real Node 24 installed on the host.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "local_ci.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COLOUR_GATE_COMMAND = "bash scripts/verify-design-system-colour-gate.sh"

NODE_VERSION_FILE_VAR = "LOCAL_CI_FAKE_NODE_VERSION_FILE"
INVOCATION_LOG_VAR = "LOCAL_CI_FAKE_INVOCATION_LOG"

# Controls for the richer fake ``uv`` used by the basetemp-lifecycle tests.
BASETEMP_CAPTURE_VAR = "LOCAL_CI_FAKE_BASETEMP_CAPTURE"
BASETEMP_DURING_VAR = "LOCAL_CI_FAKE_BASETEMP_DURING"
UV_SYNC_EXIT_VAR = "LOCAL_CI_FAKE_UV_SYNC_EXIT"
UV_RUN_EXIT_VAR = "LOCAL_CI_FAKE_UV_RUN_EXIT"
UV_RUN_SLEEP_VAR = "LOCAL_CI_FAKE_UV_RUN_SLEEP"
UV_RUN_READY_VAR = "LOCAL_CI_FAKE_UV_RUN_READY"
RM_EXIT_VAR = "LOCAL_CI_FAKE_RM_EXIT"
MKTEMP_CAPTURE_VAR = "LOCAL_CI_FAKE_MKTEMP_CAPTURE"

SIGNAL_EXIT = {
    signal.SIGHUP: 128 + signal.SIGHUP,
    signal.SIGINT: 128 + signal.SIGINT,
    signal.SIGTERM: 128 + signal.SIGTERM,
}


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _write_bytes(path: Path, data: bytes) -> None:
    """Write raw bytes to a file (binary-capable; can embed a real NUL byte).

    Distinct from ``Path.write_text``, which encodes a Python ``str`` and can
    only produce text — a literal backslash-zero escape would be written as the
    two characters ``\\0``, never a real ``\\x00`` byte. Embedded-NUL fixtures
    must go through this helper.
    """
    path.write_bytes(data)


def _fake_node(bin_dir: Path) -> None:
    """A fake ``node`` whose ``--version`` is read from an env-controlled file."""
    _write_executable(
        bin_dir / "node",
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        '  cat "${LOCAL_CI_FAKE_NODE_VERSION_FILE}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )


def _fake_recorder(bin_dir: Path, name: str) -> None:
    """A fake tool (npm/npx) that records its invocation and exits 0."""
    _write_executable(
        bin_dir / name,
        "#!/usr/bin/env bash\n"
        f'echo "{name} $*" >> "${{LOCAL_CI_FAKE_INVOCATION_LOG}}"\n'
        "exit 0\n",
    )


def _fake_uv(bin_dir: Path) -> None:
    """A fake ``uv`` that models sync/run and captures the pytest basetemp.

    * ``uv sync ...`` records and exits with ``LOCAL_CI_FAKE_UV_SYNC_EXIT``.
    * ``uv run ...`` records, extracts the ``--basetemp`` value, writes it to
      ``LOCAL_CI_FAKE_BASETEMP_CAPTURE``, appends whether that directory exists
      to ``LOCAL_CI_FAKE_BASETEMP_DURING`` (and drops a marker inside it), then
      optionally signals readiness, optionally ``exec sleep``s (for the signal
      tests), and exits with ``LOCAL_CI_FAKE_UV_RUN_EXIT``.
    """
    _write_executable(
        bin_dir / "uv",
        "#!/usr/bin/env bash\n"
        'echo "uv $*" >> "${LOCAL_CI_FAKE_INVOCATION_LOG}"\n'
        'if [ "${1:-}" = "sync" ]; then\n'
        '  exit "${LOCAL_CI_FAKE_UV_SYNC_EXIT:-0}"\n'
        "fi\n"
        'if [ "${1:-}" = "run" ]; then\n'
        '  basetemp=""\n'
        '  prev=""\n'
        '  for arg in "$@"; do\n'
        '    if [ "$prev" = "--basetemp" ]; then basetemp="$arg"; fi\n'
        '    prev="$arg"\n'
        "  done\n"
        '  if [ -n "${LOCAL_CI_FAKE_BASETEMP_CAPTURE:-}" ]; then\n'
        '    printf \'%s\' "$basetemp" > "${LOCAL_CI_FAKE_BASETEMP_CAPTURE}"\n'
        "  fi\n"
        '  if [ -n "${LOCAL_CI_FAKE_BASETEMP_DURING:-}" ]; then\n'
        '    if [ -n "$basetemp" ] && [ -d "$basetemp" ]; then\n'
        '      printf \'exists=yes\\n\' >> "${LOCAL_CI_FAKE_BASETEMP_DURING}"\n'
        '      printf \'marker\\n\' > "$basetemp/pytest-during-marker"\n'
        "    else\n"
        '      printf \'exists=no\\n\' >> "${LOCAL_CI_FAKE_BASETEMP_DURING}"\n'
        "    fi\n"
        "  fi\n"
        '  if [ -n "${LOCAL_CI_FAKE_UV_RUN_READY:-}" ]; then\n'
        '    printf \'ready\\n\' > "${LOCAL_CI_FAKE_UV_RUN_READY}"\n'
        "  fi\n"
        '  if [ -n "${LOCAL_CI_FAKE_UV_RUN_SLEEP:-}" ]; then\n'
        '    exec sleep "${LOCAL_CI_FAKE_UV_RUN_SLEEP}"\n'
        "  fi\n"
        '  exit "${LOCAL_CI_FAKE_UV_RUN_EXIT:-0}"\n'
        "fi\n"
        "exit 0\n",
    )


def _setup_fake_env(
    tmp_path: Path,
    node_version: str | bytes,
    *,
    raw: bool = False,
) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    version_file = tmp_path / "node-version"
    # ``raw`` writes the exact text (used for CRLF / lone-CR output forms that
    # must be emitted verbatim); otherwise a single normal trailing LF is added
    # to model a canonical ``node --version`` line. A ``bytes`` value is always
    # written verbatim so embedded-NUL output fixtures emit a real ``\x00``.
    if isinstance(node_version, bytes):
        version_file.write_bytes(node_version)
    else:
        version_file.write_text(node_version if raw else node_version + "\n")
    log_file = tmp_path / "invocations.log"
    _fake_node(bin_dir)
    for name in ("npm", "npx"):
        _fake_recorder(bin_dir, name)
    _fake_uv(bin_dir)
    _write_executable(
        bin_dir / "df",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'Filesystem Inodes IUsed IFree IUse% Mounted on'\n"
        "printf '%s\\n' \"${LOCAL_CI_FAKE_DF_ROW:-tmpfs 100 10 90 10% /tmp}\"\n",
    )
    return bin_dir, version_file, log_file


def _run_local_ci(
    target: str,
    bin_dir: Path,
    version_file: Path,
    log_file: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env[NODE_VERSION_FILE_VAR] = str(version_file)
    env[INVOCATION_LOG_VAR] = str(log_file)
    # Default: no version manager available, so the selection branch cannot
    # accidentally select a real Node 24 installed on the host.
    env["NVM_DIR"] = str(version_file.parent / "no-nvm-dir")
    env["HOME"] = str(version_file.parent)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT), target],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _build_tree(tmp_path: Path, declaration: str | bytes | None) -> Path:
    """Build a standalone repo-like tree with a script copy and optional .nvmrc."""
    tree = tmp_path / "repo"
    (tree / "scripts").mkdir(parents=True)
    (tree / "web" / "scripts").mkdir(parents=True)
    script_copy = tree / "scripts" / "local_ci.sh"
    script_copy.write_text(SCRIPT.read_text())
    script_copy.chmod(0o755)
    colour_gate = tree / "web" / "scripts" / "verify-design-system-colour-gate.sh"
    colour_gate.write_text("#!/usr/bin/env bash\nexit 0\n")
    colour_gate.chmod(0o755)
    if declaration is not None:
        nvmrc = tree / ".nvmrc"
        if isinstance(declaration, bytes):
            nvmrc.write_bytes(declaration)
        else:
            nvmrc.write_text(declaration)
    return tree


def _run_in_tree(
    tree: Path,
    target: str,
    bin_dir: Path,
    version_file: Path,
    log_file: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the copied script inside a standalone tree with the fake toolchain."""
    script_copy = tree / "scripts" / "local_ci.sh"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env[NODE_VERSION_FILE_VAR] = str(version_file)
    env[INVOCATION_LOG_VAR] = str(log_file)
    env["NVM_DIR"] = str(version_file.parent / "no-nvm-dir")
    env["HOME"] = str(version_file.parent)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(script_copy), target],
        cwd=str(tree),
        env=env,
        capture_output=True,
        text=True,
    )


def _build_tmp_root(tmp_path: Path) -> Path:
    """A controlled ``TMPDIR`` containing unrelated sentinel siblings.

    The basetemp must be created beneath this directory and must never disturb
    the sentinels — proving cleanup is limited to the exact created child.
    """
    tmp_root = tmp_path / "tmpdir"
    tmp_root.mkdir()
    sentinel_file = tmp_root / "unrelated-sentinel.txt"
    sentinel_file.write_text("keep me\n")
    sentinel_dir = tmp_root / "unrelated-sentinel-dir"
    sentinel_dir.mkdir()
    (sentinel_dir / "nested.txt").write_text("keep me too\n")
    return tmp_root


def _assert_sentinels_survive(tmp_root: Path) -> None:
    assert (tmp_root / "unrelated-sentinel.txt").read_text() == "keep me\n"
    assert (tmp_root / "unrelated-sentinel-dir" / "nested.txt").read_text() == (
        "keep me too\n"
    )


def _fake_rm(bin_dir: Path) -> None:
    """A fake ``rm`` that records and exits with the controlled status."""
    _write_executable(
        bin_dir / "rm",
        "#!/usr/bin/env bash\n"
        'echo "rm $*" >> "${LOCAL_CI_FAKE_INVOCATION_LOG}"\n'
        'exit "${LOCAL_CI_FAKE_RM_EXIT:-0}"\n',
    )


def _fake_mktemp(bin_dir: Path, real_mktemp: str) -> None:
    """A recording ``mktemp`` that delegates to the real tool.

    Installed only in tests that need to observe the created basetemp path even
    when the wrapper fails before pytest runs (e.g. ``uv sync`` failure). It is
    not part of the default fake env because the Node guard also calls
    ``mktemp``.
    """
    _write_executable(
        bin_dir / "mktemp",
        "#!/usr/bin/env bash\n"
        f"real={real_mktemp!r}\n"
        'out="$("$real" "$@")"\n'
        "rc=$?\n"
        'if [ "$rc" -eq 0 ]; then\n'
        '  echo "mktemp $* -> $out" >> "${LOCAL_CI_FAKE_INVOCATION_LOG}"\n'
        '  if [ -n "${LOCAL_CI_FAKE_MKTEMP_CAPTURE:-}" ]; then\n'
        '    printf \'%s\\n\' "$out" >> "${LOCAL_CI_FAKE_MKTEMP_CAPTURE}"\n'
        "  fi\n"
        "fi\n"
        'printf \'%s\\n\' "$out"\n'
        'exit "$rc"\n',
    )


def _spawn_local_ci(
    target: str,
    bin_dir: Path,
    version_file: Path,
    log_file: Path,
    env_extra: dict[str, str],
) -> subprocess.Popen[str]:
    """Start the real wrapper in its own session so signals can target it.

    ``start_new_session=True`` puts the wrapper and its fake-``uv`` child in a
    dedicated process group (mirroring a terminal foreground job), so sending a
    signal to the group reaches both exactly as Ctrl-C would.
    """
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env[NODE_VERSION_FILE_VAR] = str(version_file)
    env[INVOCATION_LOG_VAR] = str(log_file)
    env["NVM_DIR"] = str(version_file.parent / "no-nvm-dir")
    env["HOME"] = str(version_file.parent)
    env.update(env_extra)
    return subprocess.Popen(
        ["bash", str(SCRIPT), target],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _wait_for_file(path: Path, timeout: float = 15.0) -> bool:
    """Bounded wait for an explicit ready file (no timing sleeps)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


def test_all_rejects_mismatched_node_before_any_work(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v26.0.0")
    result = _run_local_ci("all", bin_dir, version_file, log_file)

    assert result.returncode != 0
    # Neither uv (python) nor npm/npx (web) may have run.
    assert not log_file.exists()
    assert "does not match the repository declaration" in result.stderr
    assert "24" in result.stderr
    assert ".nvmrc" in result.stderr
    assert "nvm use" in result.stderr


def test_web_rejects_mismatched_node_before_npm(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v26.0.0")
    result = _run_local_ci("web", bin_dir, version_file, log_file)

    assert result.returncode != 0
    assert not log_file.exists()
    assert "does not match the repository declaration" in result.stderr


def test_web_permits_valid_node_24_and_runs_commands(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    result = _run_local_ci("web", bin_dir, version_file, log_file)

    assert result.returncode == 0, result.stderr
    log = log_file.read_text()
    assert "npm ci" in log
    assert "npm run lint" in log
    assert "npm run typecheck" in log
    assert "npm run build" in log
    assert "npx vitest run" in log
    # The web target must not run any Python work.
    assert "uv" not in log


def test_unlisted_production_colour_fails_both_shipping_web_entrypoints(
    tmp_path: Path,
) -> None:
    """Local web/all and GitHub Web share the same fail-closed scanner seam."""
    local_ci = SCRIPT.read_text()
    workflow = WORKFLOW.read_text()
    assert local_ci.count(COLOUR_GATE_COMMAND) == 1
    assert workflow.count(f"run: {COLOUR_GATE_COMMAND}") == 1

    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "DeliberateViolation.tsx").write_text(
        'export const violation = <div className="text-rose-600" />\n'
    )
    allowlist = tmp_path / "allowlist.tsv"
    allowlist.write_text("")
    scanner_env = {
        "DESIGN_SYSTEM_SOURCE_ROOT": str(source_root),
        "DESIGN_SYSTEM_HEX_ALLOWLIST": str(allowlist),
    }

    for target in ("web", "all"):
        target_root = tmp_path / target
        target_root.mkdir()
        bin_dir, version_file, log_file = _setup_fake_env(
            target_root, "v24.0.0"
        )
        local_result = _run_local_ci(
            target, bin_dir, version_file, log_file, scanner_env
        )
        assert local_result.returncode == 3
        assert "unlisted hit: DeliberateViolation.tsx" in local_result.stderr
        assert "npm run lint" not in log_file.read_text()

    github_result = subprocess.run(
        ["bash", "scripts/verify-design-system-colour-gate.sh"],
        cwd=REPO_ROOT / "web",
        env={**os.environ, **scanner_env},
        capture_output=True,
        text=True,
    )
    assert github_result.returncode == 3
    assert "unlisted hit: DeliberateViolation.tsx" in github_result.stderr


def test_all_permits_valid_node_24_and_runs_python_then_web(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    result = _run_local_ci("all", bin_dir, version_file, log_file)

    assert result.returncode == 0, result.stderr
    log = log_file.read_text()
    # Python runs first (guard passed), then web.
    assert "uv sync --frozen" in log
    assert "uv run pytest" in log
    assert "npm ci" in log
    assert "npx vitest run" in log
    assert "Temporary-filesystem inode advisory" in result.stdout
    assert "used=10 free=90 total=100 percent=10%" in result.stdout


def test_all_inode_alert_is_advisory_and_fail_open(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    result = _run_local_ci(
        "all", bin_dir, version_file, log_file,
        {"LOCAL_CI_FAKE_DF_ROW": "tmpfs 100 95 5 95% /tmp"},
    )

    assert result.returncode == 0, result.stderr
    assert "used=95 free=5 total=100 percent=95%" in result.stdout
    assert "does not authorize cleanup" in result.stderr
    assert "uv run pytest" in log_file.read_text()


def test_selection_branch_via_nvm_selects_node_24(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v26.0.0")

    # Fake nvm.sh that "selects" Node 24 by rewriting the fake node's version.
    nvm_dir = tmp_path / "nvm"
    nvm_dir.mkdir()
    (nvm_dir / "nvm.sh").write_text(
        "nvm() {\n"
        '  if [ "${1:-}" = "use" ]; then\n'
        f'    echo "v24.0.0" > "{version_file}"\n'
        "    return 0\n"
        "  fi\n"
        "  return 1\n"
        "}\n"
    )

    result = _run_local_ci(
        "web",
        bin_dir,
        version_file,
        log_file,
        env_extra={"NVM_DIR": str(nvm_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert "npm ci" in log_file.read_text()


@pytest.mark.parametrize(
    "declaration",
    [
        "",  # empty file
        ">=24\n",  # range operator prefix
        "24garbage\n",  # trailing junk
        "2 4\n",  # embedded whitespace
        "24.x\n",  # wildcard suffix
        "v24x\n",  # leading v + junk
        " 24\n",  # leading whitespace before the token
        "24 \n",  # trailing whitespace after the token
        "24\t\n",  # trailing tab after the token
        "24\n\n",  # whitespace beyond the line terminator (extra newline)
        "24\r\n",  # CRLF line ending
        "24\r",  # lone trailing carriage return
        b"24\0\n",  # embedded NUL after the token + LF
        b"24\0",  # embedded NUL after the token, no trailing LF
        b"\0 24\n",  # leading NUL before the token
        None,  # missing .nvmrc
    ],
    ids=[
        "empty",
        "range-operator",
        "suffix-junk",
        "embedded-space",
        "suffix-wildcard",
        "v-prefix-junk",
        "leading-whitespace",
        "trailing-whitespace",
        "trailing-tab",
        "extra-newline",
        "crlf",
        "lone-cr",
        "nul-after-token-lf",
        "nul-after-token",
        "nul-leading",
        "missing",
    ],
)
def test_malformed_declaration_fails_closed(
    tmp_path: Path, declaration: str | bytes | None
) -> None:
    tree = _build_tree(tmp_path, declaration)
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    result = _run_in_tree(tree, "web", bin_dir, version_file, log_file)

    assert result.returncode != 0
    assert "missing or malformed" in result.stderr
    # No fake uv/npm/npx work may run before the declaration is rejected.
    assert not log_file.exists()


@pytest.mark.parametrize(
    ("node_version", "raw"),
    [
        pytest.param("v24x", False, id="v24x"),  # missing minor/patch separators
        pytest.param("v24.14garbage", False, id="patch-junk"),  # junk after patch
        pytest.param("v24.14", False, id="missing-patch"),  # missing patch
        pytest.param("junk v24.0.0", False, id="leading-junk"),  # leading junk
        pytest.param(" v24.0.0", False, id="leading-whitespace"),
        pytest.param("v24.0.0 ", False, id="trailing-whitespace"),
        pytest.param("v24.0.0\t", False, id="trailing-tab"),
        # extra newline (whitespace beyond the line terminator)
        pytest.param("v24.0.0\n", False, id="extra-newline"),
        pytest.param("v22.0.0", False, id="major-22"),  # non-24 major
        pytest.param("v26.0.0", False, id="major-26"),  # non-24 major
        pytest.param("v24.14.0\r\n", True, id="crlf"),  # CRLF line ending
        pytest.param("v24.14.0\r", True, id="lone-cr"),  # lone trailing CR
        pytest.param(b"v24.14.0\0\n", False, id="nul-terminal"),
        pytest.param(b"v24\0.14.0\n", False, id="nul-non-terminal"),
    ],
)
def test_malformed_effective_version_fails_closed(
    tmp_path: Path, node_version: str | bytes, raw: bool
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(
        tmp_path, node_version, raw=raw
    )
    result = _run_local_ci("web", bin_dir, version_file, log_file)

    assert result.returncode != 0
    assert "does not match the repository declaration" in result.stderr
    # No fake uv/npm/npx work may run before the effective version is rejected.
    assert not log_file.exists()


def test_web_permits_valid_node_24_with_nonzero_minor_patch(tmp_path: Path) -> None:
    # A canonical v24.x.y with non-zero minor/patch must pass the strict
    # parser (numeric components), matching a real `node --version` like v24.14.0.
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.14.0")
    result = _run_local_ci("web", bin_dir, version_file, log_file)

    assert result.returncode == 0, result.stderr
    assert "npm ci" in log_file.read_text()


@pytest.mark.parametrize("declaration", ["24", "24\n"], ids=["bare-24", "24-lf"])
def test_valid_declaration_accepted(tmp_path: Path, declaration: str) -> None:
    # The declaration accepts only the bare byte token "24" or "24\n"; both
    # must pass the guard and let the web target run the fake npm/npx shims.
    tree = _build_tree(tmp_path, declaration)
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    result = _run_in_tree(tree, "web", bin_dir, version_file, log_file)

    assert result.returncode == 0, result.stderr
    assert "npm ci" in log_file.read_text()


# ── Per-run pytest basetemp lifecycle ─────────────────────────────────────
# Each Python pytest invocation must get an explicit, freshly created and
# uniquely named --basetemp beneath the effective TMPDIR, and the wrapper must
# remove exactly that directory (never TMPDIR, never a sibling) on success,
# failure and catchable signals while preserving the meaningful exit status.


@pytest.mark.parametrize("target", ["python", "integration"], ids=["python", "integration"])
def test_python_and_integration_pass_explicit_unique_basetemp(
    tmp_path: Path, target: str
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"
    during = tmp_path / "basetemp-during"

    result = _run_local_ci(
        target,
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            BASETEMP_CAPTURE_VAR: str(capture),
            BASETEMP_DURING_VAR: str(during),
        },
    )

    assert result.returncode == 0, result.stderr
    log = log_file.read_text()
    assert "uv sync --frozen" in log
    assert "uv run pytest" in log
    assert "--basetemp" in log
    # The directory existed while pytest ran and is gone afterwards.
    assert during.read_text().strip() == "exists=yes"
    basetemp = Path(capture.read_text())
    assert basetemp.parent == tmp_root
    assert basetemp.name.startswith("happyranch-pytest-basetemp")
    assert not basetemp.exists()
    _assert_sentinels_survive(tmp_root)


def test_all_python_phase_passes_basetemp_and_cleans_up(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"
    during = tmp_path / "basetemp-during"

    result = _run_local_ci(
        "all",
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            BASETEMP_CAPTURE_VAR: str(capture),
            BASETEMP_DURING_VAR: str(during),
        },
    )

    assert result.returncode == 0, result.stderr
    log = log_file.read_text()
    assert "uv run pytest" in log
    assert "--basetemp" in log
    assert "npm ci" in log
    assert during.read_text().strip() == "exists=yes"
    basetemp = Path(capture.read_text())
    assert basetemp.parent == tmp_root
    assert not basetemp.exists()
    _assert_sentinels_survive(tmp_root)


def test_two_sequential_invocations_do_not_reuse_a_stale_basetemp(
    tmp_path: Path,
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    seen: list[Path] = []

    for index in range(2):
        capture = tmp_path / f"basetemp-capture-{index}"
        during = tmp_path / f"basetemp-during-{index}"
        result = _run_local_ci(
            "python",
            bin_dir,
            version_file,
            log_file,
            {
                "TMPDIR": str(tmp_root),
                BASETEMP_CAPTURE_VAR: str(capture),
                BASETEMP_DURING_VAR: str(during),
            },
        )
        assert result.returncode == 0, result.stderr
        basetemp = Path(capture.read_text())
        assert during.read_text().strip() == "exists=yes"
        assert not basetemp.exists()
        seen.append(basetemp)

    assert seen[0] != seen[1]
    _assert_sentinels_survive(tmp_root)


def test_pytest_failure_removes_basetemp_and_preserves_exit_status(
    tmp_path: Path,
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"
    during = tmp_path / "basetemp-during"

    result = _run_local_ci(
        "python",
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            BASETEMP_CAPTURE_VAR: str(capture),
            BASETEMP_DURING_VAR: str(during),
            UV_RUN_EXIT_VAR: "7",
        },
    )

    assert result.returncode == 7, result.stderr
    assert during.read_text().strip() == "exists=yes"
    basetemp = Path(capture.read_text())
    assert not basetemp.exists()
    _assert_sentinels_survive(tmp_root)


def test_uv_sync_failure_removes_basetemp_and_preserves_exit_status(
    tmp_path: Path,
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    real_mktemp = shutil.which("mktemp")
    assert real_mktemp is not None
    _fake_mktemp(bin_dir, real_mktemp)
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "mktemp-capture"

    result = _run_local_ci(
        "python",
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            MKTEMP_CAPTURE_VAR: str(capture),
            UV_SYNC_EXIT_VAR: "9",
        },
    )

    assert result.returncode == 9, result.stderr
    # The basetemp was created before sync and removed on the sync failure.
    assert capture.exists(), result.stderr
    basetemp = Path(capture.read_text().strip().splitlines()[-1])
    assert basetemp.parent == tmp_root
    assert not basetemp.exists()
    # pytest never ran.
    assert "uv run pytest" not in log_file.read_text()
    _assert_sentinels_survive(tmp_root)


@pytest.mark.parametrize(
    "signum",
    [signal.SIGHUP, signal.SIGINT, signal.SIGTERM],
    ids=["HUP", "INT", "TERM"],
)
def test_catchable_signal_removes_basetemp_and_preserves_signal_status(
    tmp_path: Path, signum: int
) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"
    ready = tmp_path / "uv-run-ready"

    proc = _spawn_local_ci(
        "python",
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            BASETEMP_CAPTURE_VAR: str(capture),
            UV_RUN_READY_VAR: str(ready),
            UV_RUN_SLEEP_VAR: "30",
        },
    )
    try:
        assert _wait_for_file(ready), "fake uv run never signalled readiness"
        os.killpg(os.getpgid(proc.pid), signum)
        _stdout, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)

    assert proc.returncode == SIGNAL_EXIT[signum], stderr
    basetemp = Path(capture.read_text())
    assert basetemp.parent == tmp_root
    assert not basetemp.exists(), stderr
    _assert_sentinels_survive(tmp_root)


@pytest.mark.parametrize(
    "target", ["web", "help", "not-a-target"], ids=["web", "help", "invalid"]
)
def test_non_python_targets_create_no_basetemp(tmp_path: Path, target: str) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"

    result = _run_local_ci(
        target,
        bin_dir,
        version_file,
        log_file,
        {"TMPDIR": str(tmp_root), BASETEMP_CAPTURE_VAR: str(capture)},
    )

    assert not capture.exists()
    if target == "web":
        assert result.returncode == 0, result.stderr
    elif target == "help":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
    assert not any(
        entry.name.startswith("happyranch-pytest-basetemp")
        for entry in tmp_root.iterdir()
    )


def test_basetemp_creation_failure_is_explicit_and_nonzero(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    # A TMPDIR that does not exist makes mktemp -d fail.
    missing_tmp = tmp_path / "missing-tmpdir"

    result = _run_local_ci(
        "python", bin_dir, version_file, log_file, {"TMPDIR": str(missing_tmp)}
    )

    assert result.returncode != 0
    assert "pytest basetemp" in result.stderr.lower()
    # No uv work at all: creation is refused before sync/pytest.
    assert not log_file.exists()


def test_basetemp_cleanup_failure_is_explicit_and_nonzero(tmp_path: Path) -> None:
    bin_dir, version_file, log_file = _setup_fake_env(tmp_path, "v24.0.0")
    _fake_rm(bin_dir)
    tmp_root = _build_tmp_root(tmp_path)
    capture = tmp_path / "basetemp-capture"

    result = _run_local_ci(
        "python",
        bin_dir,
        version_file,
        log_file,
        {
            "TMPDIR": str(tmp_root),
            BASETEMP_CAPTURE_VAR: str(capture),
            RM_EXIT_VAR: "1",
        },
    )

    assert result.returncode != 0
    assert "failed to remove pytest basetemp" in result.stderr.lower()
    # The failure is reported honestly: the directory is not silently claimed
    # as cleaned, and the run is not a clean local-CI pass.
    basetemp = Path(capture.read_text())
    assert basetemp.exists()
    _assert_sentinels_survive(tmp_root)
