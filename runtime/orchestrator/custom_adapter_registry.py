"""Custom adapter registration logic (THR-107 D3).

Implements the D3 registration seam: validates an absolute executable path,
ensures it is a regular executable file, computes SHA-256, runs a bounded
conformance probe using the versioned ``AdapterInput``/``AdapterOutput``
contract, and persists exactly PENDING entries.

D3 ONLY — no founder approval/activation (D4), no profile binding or
custom-adapter launch through executor runtime (D7), no mandatory envelope
enforcement, no SQLite changes, no D5 permission/sandbox/allow-rule/
network/filesystem expansion.

Re-registration semantics: a changed artifact/path/hash/capabilities produces
a plainly pending result — approval is never silently retained.

THR-107 seq244: dependency-manifest extension — validates declared child
executable dependencies at registration and re-validates at launch.
"""
from __future__ import annotations

import json
import logging
import os
import re
import select
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput
from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    _save_adapter_locked,
    acquire_store_lock,
    compute_sha256,
    get_adapter,
    load_adapters,
    release_store_lock,
    save_adapter,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Traversal detection helper (shared with routes/adapters.py)
# ---------------------------------------------------------------------------


def _has_traversal_spelling_registry(raw_path: str) -> bool:
    """Return True if the raw path string contains any traversal component.

    Detects ``..`` directory components in the path before any normalization.
    This catches absolute traversal bypasses such as
    ``<daemon-home>/adapters/../adapters/<id>`` even when ``Path.resolve()``
    would collapse them to the canonical form.

    Only whole-segment ``..`` entries are flagged — a filename containing
    ``..`` as a substring (e.g. ``foo..bar``) is NOT flagged.
    """
    parts = raw_path.split(os.sep)
    return ".." in parts


# ---------------------------------------------------------------------------
# Canonical adapter daemon path (THR-107 seq339/340)
# ---------------------------------------------------------------------------


def compute_canonical_adapter_path(
    canonical_adapter_id: str,
    daemon_home_override: Path | None = None,
) -> tuple[Path, Path]:
    """Return the daemon-managed canonical adapter directory and required
    executable path for a scoped adapter.

    Derives the absolute canonical path from the daemon home and the
    server-authoritative canonical adapter ID.  The canonical adapter ID
    is generated from the intended profile name by ``generate_adapter_id``
    and permits only lowercase alnum/hyphen — so the filename is safe.

    Directory creation:
      - Creates ``<daemon-home>/adapters/`` with mode 0o700 when it does
        not yet exist (user-only, restrictive).
      - Rejects the ``adapters/`` directory if it already exists as a
        symlink (escape / race guard).
      - Does NOT create or write the wrapper executable file — the
        candidate CLI creates it.  The directory merely MUST exist so
        the caller can create a regular file at the canonical path.
      - GET contract-reference may call this to ensure the parent dir;
        it MUST NOT consume or reserve the registration token.

    Target rejection:
      - If the wrapper path already exists as a symlink, raises ValueError.
      - If the wrapper path already exists as a non-regular file, raises
        ValueError.
      - An existing regular file is NOT rejected at this level — it could
        be a prior-scope-registration wrapper.  The submit route validates
        the body.executable matches the exact canonical path.

    Args:
        canonical_adapter_id: The server-derived adapter ID (lowercase
            alnum/hyphen only).
        daemon_home_override: For testing only — overrides the daemon home
            directory.  When ``None`` (normal operation), uses
            ``runtime.runtime.daemon_home()`` which honors
            ``HAPPYRANCH_DAEMON_HOME``.

    Returns:
        (canonical_directory, required_executable_path) — both absolute,
        canonical (symlink-resolved) ``Path`` objects.
    """
    if not canonical_adapter_id or not isinstance(canonical_adapter_id, str):
        raise ValueError(
            f"canonical_adapter_id must be a non-empty string, got {canonical_adapter_id!r}"
        )

    # Derive the daemon home
    if daemon_home_override is not None:
        home = Path(daemon_home_override).resolve()
    else:
        from runtime.runtime import daemon_home
        home = daemon_home().resolve()

    adapters_dir = home / "adapters"

    # ---- Symlink escape guard: the adapters directory itself must NOT be a
    #      symlink (an attacker could substitute a symlink to a sensitive dir).
    if adapters_dir.is_symlink():
        raise ValueError(
            f"adapters directory is a symlink: {adapters_dir}. "
            f"The daemon-managed adapters directory must be a real directory, "
            f"not a symlink. Remove the symlink and create a real directory at "
            f"{adapters_dir}."
        )

    # Create the adapters directory with restrictive owner-only mode.
    # os.makedirs with exist_ok handles the already-exists case, but we
    # also need to set 0o700 when we are the creator.
    if not adapters_dir.exists():
        try:
            adapters_dir.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise ValueError(
                f"Cannot create adapters directory at {adapters_dir}: {exc}"
            )
    else:
        # Directory already exists — verify it is a real directory (not a
        # symlink, already checked above, but double-check after the
        # exist-race window).
        if adapters_dir.is_symlink():
            raise ValueError(
                f"adapters directory is a symlink: {adapters_dir}. "
                f"Remove the symlink and create a real directory at "
                f"{adapters_dir}."
            )
        if not adapters_dir.is_dir():
            raise ValueError(
                f"{adapters_dir} exists but is not a directory."
            )
        # Ensure 0o700 even when the directory already existed (correct
        # a prior world-readable creation).
        try:
            adapters_dir.chmod(0o700)
        except OSError:
            pass  # best-effort

    # The canonical executable path — check for symlink BEFORE resolving
    wrapper_unresolved = adapters_dir / canonical_adapter_id

    # ---- Symlink escape guard: the wrapper path must NOT be a symlink.
    #      Check on the UNRESOLVED path so symlinks are detected even when
    #      their target exists.  resolve() follows symlinks.
    if wrapper_unresolved.is_symlink():
        raise ValueError(
            f"Wrapper path is a symlink: {wrapper_unresolved}. "
            f"The scoped adapter wrapper must be a regular file at its "
            f"canonical path, not a symlink."
        )

    # If the wrapper path already exists (pre-resolve), it must be a regular
    # file (or not exist yet — the candidate CLI creates it).
    if wrapper_unresolved.exists() and not wrapper_unresolved.is_file():
        raise ValueError(
            f"Wrapper path {wrapper_unresolved} exists but is not a regular file. "
            f"The canonical adapter path must be a regular executable file."
        )

    # Now resolve to canonical form for the returned required path
    wrapper_path = wrapper_unresolved.resolve()

    # Also check the resolved adapters_dir for symlink (it was already checked
    # above, but resolve it now for the return value)
    resolved_dir = adapters_dir.resolve()

    return (resolved_dir, wrapper_path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_executable_path(executable: str) -> Path:
    """Validate an adapter executable path.

    Returns the resolved absolute ``Path`` on success.

    Raises ``ValueError`` with an actionable message when:
      - ``executable`` is not an absolute path
      - the path does not exist
      - the path is not a regular file
      - the path is not executable by the current user

    Never performs PATH lookup — only absolute paths are accepted.
    """
    if not executable or not os.path.isabs(executable):
        raise ValueError(
            f"executable must be an absolute path, got {executable!r}. "
            f"PATH lookup is not supported for custom adapters."
        )

    path = Path(executable).resolve()

    if not path.exists():
        raise ValueError(
            f"executable {executable!r} does not exist (resolved to {path})"
        )

    if not path.is_file():
        raise ValueError(
            f"executable {executable!r} is not a regular file (resolved to {path})"
        )

    if not os.access(path, os.X_OK):
        raise ValueError(
            f"executable {executable!r} is not executable (resolved to {path})"
        )

    return path


# ---------------------------------------------------------------------------
# THR-107 seq244: Dependency manifest validation
# ---------------------------------------------------------------------------


def validate_dependency_record(dep: dict) -> dict:
    """Validate a single dependency record at registration/submission time.

    A valid dependency record MUST have:
      - executable: absolute path, exists, regular file, executable
      - sha256: 64-char hex string matching the file's SHA-256 digest

    Returns the normalized dict with resolved absolute executable path.

    Raises ``ValueError`` on any validation failure.
    """
    if not isinstance(dep, dict):
        raise ValueError(f"Dependency record must be a JSON object, got {type(dep).__name__}")

    executable = dep.get("executable")
    if not executable or not isinstance(executable, str):
        raise ValueError(f"Dependency record must have a non-empty 'executable' string")

    declared_sha = dep.get("sha256")
    if not declared_sha or not isinstance(declared_sha, str):
        raise ValueError(f"Dependency record must have a non-empty 'sha256' string")
    if len(declared_sha) != 64:
        raise ValueError(
            f"Dependency sha256 must be a 64-char hex digest, got {len(declared_sha)} chars"
        )
    # Validate it's hex
    try:
        int(declared_sha, 16)
    except (ValueError, TypeError):
        raise ValueError(f"Dependency sha256 is not valid hex: {declared_sha[:20]}...")

    # Validate the executable path (absolute, exists, regular file, executable)
    dep_path = validate_executable_path(executable)

    # Verify the declared hash matches the on-disk file
    actual_hash = compute_sha256(str(dep_path))
    if actual_hash != declared_sha:
        raise ValueError(
            f"Dependency {dep_path} sha256 mismatch: "
            f"declared {declared_sha[:12]}..., actual {actual_hash[:12]}..."
        )

    # Check for duplicate entries (same executable) — the caller checks
    # across the whole manifest, but we validate the record in isolation.
    return {"executable": str(dep_path), "sha256": declared_sha}


def validate_dependency_manifest(
    dependency_manifest_version: int | None,
    dependencies: list[dict] | None,
) -> tuple[int | None, list[dict]]:
    """Validate the dependency manifest extension at registration time.

    For new submissions (non-None manifest version):
      - ``dependency_manifest_version`` must be exactly 1
      - ``dependencies`` must be a non-empty list
      - Each record is validated via ``validate_dependency_record``
      - No duplicate executables are allowed

    For legacy entries (manifest version is None):
      - ``dependencies`` must be None or empty
      - Returns (None, []) — legacy status is preserved

    Returns a tuple of (dependency_manifest_version, normalized_dependencies).
    Raises ``ValueError`` with actionable message on any violation.
    """
    if dependency_manifest_version is None:
        # Legacy entry — no dependency manifest
        if dependencies is not None and len(dependencies) > 0:
            raise ValueError(
                "dependency_manifest_version is required when dependencies are provided"
            )
        return (None, [])
    if not isinstance(dependency_manifest_version, int) or isinstance(dependency_manifest_version, bool):
        raise ValueError(
            f"dependency_manifest_version must be an integer, got {type(dependency_manifest_version).__name__}"
        )
    if dependency_manifest_version != 1:
        raise ValueError(
            f"dependency_manifest_version must be exactly 1, got {dependency_manifest_version}"
        )

    # Dependencies must be a non-empty list
    if dependencies is None or not isinstance(dependencies, list):
        raise ValueError(
            "dependencies must be a non-empty list when dependency_manifest_version is set"
        )
    if len(dependencies) == 0:
        raise ValueError(
            "dependencies must be a non-empty list when dependency_manifest_version is set"
        )

    # Validate each dependency record
    normalized: list[dict] = []
    seen_executables: set[str] = set()
    for dep in dependencies:
        normalized_dep = validate_dependency_record(dep)
        exe = normalized_dep["executable"]
        if exe in seen_executables:
            raise ValueError(
                f"Duplicate dependency executable: {exe!r}. "
                f"Each child executable may appear only once."
            )
        seen_executables.add(exe)
        normalized.append(normalized_dep)

    return (dependency_manifest_version, normalized)


def validate_version(version: str) -> str:
    """Validate an adapter version string.

    Must be a non-empty string. Returns the trimmed version.
    Rejects strings that become empty after trimming whitespace.
    """
    if not version or not isinstance(version, str):
        raise ValueError("version must be a non-empty string")
    trimmed = version.strip()
    if not trimmed:
        raise ValueError("version must be a non-empty string after trimming whitespace")
    return trimmed


def validate_capabilities(capabilities: list[str]) -> list[str]:
    """Validate declared capabilities.

    Must be a list of non-empty strings. Returns the trimmed list.
    No capability semantic validation in D3 (D4/D5 will gate specific caps).
    """
    if not isinstance(capabilities, list):
        raise ValueError("capabilities must be a list of strings")
    cleaned: list[str] = []
    for cap in capabilities:
        if not isinstance(cap, str) or not cap.strip():
            raise ValueError(f"capability {cap!r} must be a non-empty string")
        cleaned.append(cap.strip())
    return cleaned


def validate_workspace_adapter(workspace_adapter: str) -> str:
    """Validate the workspace adapter id.

    Must be one of: claude, codex, opencode, pi.
    """
    valid = {"claude", "codex", "opencode", "pi"}
    if workspace_adapter not in valid:
        raise ValueError(
            f"workspace_adapter must be one of {sorted(valid)}, "
            f"got {workspace_adapter!r}"
        )
    return workspace_adapter


# ---------------------------------------------------------------------------
# Conformance probe
# ---------------------------------------------------------------------------


# Maximum time (in seconds) the conformance probe subprocess may run.
# A well-behaved adapter should complete in well under 10 seconds.
CONFORMANCE_PROBE_TIMEOUT_SECONDS = 30

# Maximum bytes to read from the adapter's stdout during the probe.
# Guards against a misbehaving adapter that writes an unbounded stream.
CONFORMANCE_PROBE_MAX_STDOUT_BYTES = 1_048_576  # 1 MB

# Maximum bytes to read from the adapter's stderr during the probe.
CONFORMANCE_PROBE_MAX_STDERR_BYTES = 1_048_576  # 1 MB


class BoundedReadError(ValueError):
    """Raised when a bounded read exceeds its byte limit."""

    def __init__(self, stream: str, limit: int, actual: int, executable: str):
        super().__init__(
            f"Conformance probe {stream} exceeded {limit} byte limit "
            f"({actual} bytes) for {executable!r}"
        )
        self.stream = stream
        self.limit = limit
        self.actual = actual
        self.executable = executable


def _kill_and_reap(proc: subprocess.Popen) -> None:
    """Kill the subprocess and wait for it to terminate (reap zombie).

    No durable, in-memory, operational, or child-handle residue remains.
    """
    if proc.poll() is not None:
        return  # already exited
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.error("Could not reap process %d after SIGKILL", proc.pid)
        try:
            proc.kill()  # Ensure SIGKILL was sent
        except OSError:
            pass


def _read_bounded(
    proc: subprocess.Popen,
    stdout_limit: int,
    stderr_limit: int,
    timeout: float,
) -> tuple[bytes, bytes]:
    """Read stdout and stderr concurrently with per-stream byte limits.

    Uses ``select.select()`` with a single monotonic deadline to read from
    whichever pipe has data available without blocking. Accumulates bytes
    per-stream and checks byte limits as data arrives (not after buffering).

    On timeout or byte-limit breach, raises the appropriate exception —
    the caller MUST kill and reap the child via ``_kill_and_reap``.

    Returns (stdout_bytes, stderr_bytes) on both-streams-EOF within limits.

    Precondition: stdin has already been closed by the caller.
    """
    if proc.stdout is None or proc.stderr is None:
        raise ValueError(
            "Subprocess stdout/stderr pipes are not available"
        )

    stdout_fd = proc.stdout.fileno()
    stderr_fd = proc.stderr.fileno()

    # Set non-blocking mode so os.read() returns available data immediately.
    for fd in (stdout_fd, stderr_fd):
        try:
            os.set_blocking(fd, False)
        except OSError:
            pass  # fd may be invalid if process already exited

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    deadline = time.monotonic() + timeout
    stdout_eof = False
    stderr_eof = False

    while not (stdout_eof and stderr_eof):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(
                proc.args, timeout,
                output=bytes(stdout_buf),
                stderr=bytes(stderr_buf),
            )

        read_fds: list[int] = []
        if not stdout_eof:
            read_fds.append(stdout_fd)
        if not stderr_eof:
            read_fds.append(stderr_fd)

        if not read_fds:
            break

        try:
            readable, _, _ = select.select(read_fds, [], [], remaining)
        except InterruptedError:
            continue

        if not readable:
            # select timed out → overall probe timeout
            raise subprocess.TimeoutExpired(
                proc.args, timeout,
                output=bytes(stdout_buf),
                stderr=bytes(stderr_buf),
            )

        for fd in readable:
            try:
                chunk = os.read(fd, 65536)
                if not chunk:
                    if fd == stdout_fd:
                        stdout_eof = True
                    else:
                        stderr_eof = True
                    continue
            except BlockingIOError:
                continue
            except OSError:
                # Pipe closed or broken — treat as EOF
                if fd == stdout_fd:
                    stdout_eof = True
                else:
                    stderr_eof = True
                continue

            if fd == stdout_fd:
                new_total = len(stdout_buf) + len(chunk)
                if new_total > stdout_limit:
                    raise BoundedReadError(
                        "stdout", stdout_limit, new_total,
                        proc.args[0] if proc.args else ""
                    )
                stdout_buf.extend(chunk)
            else:
                new_total = len(stderr_buf) + len(chunk)
                if new_total > stderr_limit:
                    raise BoundedReadError(
                        "stderr", stderr_limit, new_total,
                        proc.args[0] if proc.args else ""
                    )
                stderr_buf.extend(chunk)

    # Both streams reached EOF within limits
    return bytes(stdout_buf), bytes(stderr_buf)


def build_probe_input(
    adapter_name: str,
    *,
    prompt_canary: str | None = None,
    invocation_id: str | None = None,
) -> AdapterInput:
    """Build a minimal, deterministic ``AdapterInput`` for the conformance probe.

    The probe input is a lightweight sample invocation that lets the adapter
    demonstrate it can parse the contract and produce valid output. It does
    NOT exercise a real agentic session — it only validates the contract shapes.

    The workspace path is a real temporary directory created by the probe
    runner — the adapter can read/write files there but must operate within
    the probe deadline.
    """
    from runtime.orchestrator.adapter_contract import (
        ExecutorContext,
        InvocationInfo,
        TimeoutInfo,
    )

    return AdapterInput(
        contract_version=1,
        invocation=InvocationInfo(
            invocation_id=invocation_id or "probe-sess-00000000-0000-0000-0000-000000000000",
            task_id=None,
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        ),
        prompt=(
            "conformance-probe: respond with a valid AdapterOutput. "
            f"{prompt_canary}"
            if prompt_canary is not None
            else "conformance-probe: respond with a valid AdapterOutput."
        ),
        workspace="/tmp/happyranch-probe-workspace",
        timeout=TimeoutInfo(
            deadline_seconds=30,
            max_runtime_seconds=30,
        ),
        executor_context=ExecutorContext(
            provider=adapter_name,
            adapter_id="pi",
            adapter_version="1.0.0",
            permission_mode="default",
        ),
    )


def run_conformance_probe(
    executable: str,
    adapter_name: str,
    *,
    require_prompt_delivery: bool = False,
) -> AdapterOutput:
    """Run a bounded stdin/stdout conformance probe against an adapter executable.

    1. Builds a minimal ``AdapterInput`` as JSON.
    2. Spawns the executable as a subprocess, pipes stdin/stdout.
    3. Sends the input JSON via stdin.
    4. Reads stdout (capped at 1 MB), parses as ``AdapterOutput`` JSON.
    5. Validates the output against the contract.
    6. Verifies ``adapter_metadata.adapter`` exactly equals the canonical
       server-derived adapter ID (``adapter_name``) — provenance invariant.

    Returns the parsed ``AdapterOutput`` on success.

    Raises ``ValueError`` with an actionable message when:
      - The subprocess fails to start (OSError, FileNotFoundError)
      - The subprocess times out
      - The subprocess exits non-zero
      - The stdout is not valid JSON
      - The JSON does not validate as ``AdapterOutput``
      - The ``adapter_metadata.contract_version`` is missing or unknown
      - The ``adapter_metadata.adapter`` does not match the canonical adapter ID

    ZERO durable residue is left on failure — this is a pure validation step.
    """
    prompt_canary = None
    invocation_id = None
    if require_prompt_delivery:
        # This opaque, per-invocation proof prevents a static wrapper response
        # from satisfying direct-connect's behavioral gate.
        prompt_canary = f"direct-connect-canary:{secrets.token_urlsafe(24)}"
        invocation_id = f"probe-sess-{uuid.uuid4()}"
    probe_input = build_probe_input(
        adapter_name,
        prompt_canary=prompt_canary,
        invocation_id=invocation_id,
    )
    input_json = probe_input.model_dump_json()

    def direct_failure(reason: str) -> ValueError:
        """Keep direct-projection diagnostics category-only and secret-safe."""
        return ValueError(f"Direct conformance probe {reason}")

    # Prepare the probe workspace — the server creates this directory
    # so the adapter does not need to create it.
    probe_workspace = Path(probe_input.workspace)
    try:
        probe_workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(
            f"Failed to create probe workspace {probe_workspace}: {exc}"
        ) from exc

    try:
        proc = subprocess.Popen(
            [executable],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,  # We handle encoding explicitly
        )
    except (FileNotFoundError, OSError, PermissionError) as exc:
        raise ValueError(
            f"Failed to launch adapter executable {executable!r}: {exc}"
        ) from exc

    # Read stdout and stderr concurrently with byte limits.
    # Do NOT use communicate() — it fully buffers both streams before we can
    # enforce limits. Instead, send stdin, close it, then read from both pipes
    # in a bounded loop.
    try:
        # Send stdin and close it so the adapter can start processing
        if proc.stdin is not None:
            proc.stdin.write(input_json.encode("utf-8"))
            proc.stdin.close()
        conformance_start = time.monotonic()
        stdout_bytes, stderr_bytes = _read_bounded(
            proc,
            stdout_limit=CONFORMANCE_PROBE_MAX_STDOUT_BYTES,
            stderr_limit=CONFORMANCE_PROBE_MAX_STDERR_BYTES,
            timeout=CONFORMANCE_PROBE_TIMEOUT_SECONDS,
        )
        # Both streams reached EOF — wait for the process with the
        # REMAINING budget from the original monotonic deadline.
        # A fresh unconditional timeout would let an adapter escape
        # the deadline merely by closing its streams before hanging.
        remaining = CONFORMANCE_PROBE_TIMEOUT_SECONDS - (
            time.monotonic() - conformance_start
        )
        if remaining <= 0:
            _kill_and_reap(proc)
            if require_prompt_delivery:
                raise direct_failure("timed out")
            raise ValueError(
                f"Conformance probe timed out after "
                f"{CONFORMANCE_PROBE_TIMEOUT_SECONDS}s for {executable!r}"
            )
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_and_reap(proc)
            if require_prompt_delivery:
                raise direct_failure("timed out")
            raise ValueError(
                f"Conformance probe timed out after "
                f"{CONFORMANCE_PROBE_TIMEOUT_SECONDS}s for {executable!r}"
            )
    except subprocess.TimeoutExpired as exc:
        _kill_and_reap(proc)
        if require_prompt_delivery:
            raise direct_failure("timed out") from exc
        stderr_tail = ""
        if isinstance(exc.stderr, bytes):
            stderr_tail = exc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise ValueError(
            f"Conformance probe timed out after "
            f"{CONFORMANCE_PROBE_TIMEOUT_SECONDS}s for {executable!r}. "
            f"stderr tail: {stderr_tail[:500]}"
        )
    except BoundedReadError as exc:
        _kill_and_reap(proc)
        raise ValueError(str(exc)) from exc

    if proc.returncode != 0:
        if require_prompt_delivery:
            raise direct_failure("provider process exited nonzero")
        stderr_tail = ""
        if stderr_bytes:
            stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-2000:]
        raise ValueError(
            f"Conformance probe exited with code {proc.returncode} for "
            f"{executable!r}. stderr tail: {stderr_tail[:500]}"
        )

    if not stdout_bytes:
        if require_prompt_delivery:
            raise direct_failure("has absent terminal output")
        raise ValueError(
            f"Conformance probe produced no stdout for {executable!r}"
        )

    # Decode and parse stdout — enforce byte limits while reading both streams.
    # Reject over-limit, non-UTF8, or non-JSON output with ZERO durable residue.
    try:
        stdout_text = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        if require_prompt_delivery:
            raise direct_failure("has malformed output") from exc
        raise ValueError(
            f"Conformance probe stdout is not valid UTF-8 for {executable!r}: {exc}"
        ) from exc

    # Reject stdout exceeding the byte limit — do not silently truncate
    if len(stdout_bytes) > CONFORMANCE_PROBE_MAX_STDOUT_BYTES:
        raise ValueError(
            f"Conformance probe stdout exceeds {CONFORMANCE_PROBE_MAX_STDOUT_BYTES} "
            f"byte limit ({len(stdout_bytes)} bytes) for {executable!r}"
        )

    try:
        output_dict = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        if require_prompt_delivery:
            raise direct_failure("has malformed output") from exc
        raise ValueError(
            f"Conformance probe stdout is not valid JSON for {executable!r}: {exc}"
        ) from exc

    if not isinstance(output_dict, dict):
        if require_prompt_delivery:
            raise direct_failure("has malformed output")
        raise ValueError(
            f"Conformance probe stdout is not a JSON object for {executable!r}; "
            f"got {type(output_dict).__name__}"
        )

    # Check contract_version in adapter_metadata BEFORE Pydantic validation.
    # Pydantic v2 coerces bool → int, so we must inspect the raw JSON dict to
    # detect booleans, None, floats, and other non-integer values.
    raw_meta = output_dict.get("adapter_metadata")
    if isinstance(raw_meta, dict):
        raw_cv = raw_meta.get("contract_version")
        if raw_cv is None:
            if require_prompt_delivery:
                raise direct_failure("has malformed output")
            raise ValueError(
                f"Conformance probe output has missing contract_version "
                f"for {executable!r}; must be the integer 1"
            )
        if not isinstance(raw_cv, int) or isinstance(raw_cv, bool):
            if require_prompt_delivery:
                raise direct_failure("has malformed output")
            raise ValueError(
                f"Conformance probe output has non-integer contract_version "
                f"{raw_cv!r} (type {type(raw_cv).__name__}) for {executable!r}; "
                f"must be the integer 1"
            )
        if raw_cv != 1:
            if require_prompt_delivery:
                raise direct_failure("has malformed output")
            raise ValueError(
                f"Conformance probe output has unsupported contract_version "
                f"{raw_cv} for {executable!r}; only version 1 is supported in D3"
            )

    # Validate against AdapterOutput schema
    try:
        output = AdapterOutput.model_validate(output_dict)
    except Exception as exc:
        if require_prompt_delivery:
            raise direct_failure("has malformed output") from exc
        raise ValueError(
            f"Conformance probe output does not match AdapterOutput contract "
            f"for {executable!r}: {exc}"
        ) from exc

    # Basic sanity: success must be true
    if not output.success:
        if require_prompt_delivery:
            raise direct_failure("provider reported failure")
        error_msg = output.error or "(no error message)"
        # Safe cap: never leak unbounded output
        capped_error = error_msg[:500]
        stderr_tail = ""
        if stderr_bytes:
            stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-2000:]
        raise ValueError(
            f"Conformance probe reported success=false for {executable!r}. "
            f"Error: {capped_error}. Stderr tail: {stderr_tail[:500]}"
        )

    # THR-107 seq268: provenance invariant — adapter_metadata.adapter MUST
    # exactly equal the stable server-derived canonical adapter ID (adapter_name),
    # never a display name, provider string, or arbitrary identity.
    if output.adapter_metadata.adapter != adapter_name:
        if require_prompt_delivery:
            raise direct_failure("adapter identity mismatch")
        raise ValueError(
            f"Conformance probe adapter identity mismatch: expected "
            f"{adapter_name!r} (the canonical server-derived adapter ID), "
            f"got {output.adapter_metadata.adapter!r}. The adapter wrapper's "
            f"adapter_metadata.adapter MUST exactly equal the stable submitted/"
            f"approved adapter ID — never a display name or provider string."
        )

    if require_prompt_delivery:
        if output.returncode != 0:
            raise direct_failure("return code is inconsistent")
        if output.session_id != probe_input.invocation.invocation_id:
            raise direct_failure("invocation id is missing or does not match")
        if not output.agent_session_id or not output.agent_session_id.strip():
            raise direct_failure("agent session id is missing")
        if output.result is None or output.result.text is None or prompt_canary not in output.result.text:
            raise direct_failure("terminal result did not prove prompt delivery")

    return output


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def generate_adapter_id(name: str) -> str:
    """Generate a stable adapter id from a name.

    Lowercases and replaces non-alphanumeric chars with hyphens.
    Collisions are detected at save time.
    """
    base = name.lower().strip()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    return base.strip("-") or "adapter"


def _find_active_profile_bound(
    adapter_id: str,
    runtime_profiles: dict[str, dict],
) -> str | None:
    """Return the name of an active runtime profile bound to ``adapter_id``.

    A profile is bound when its ``command_adapter_id`` equals
    ``custom-adapter:<adapter_id>``.  Returns ``None`` when no active
    profile is bound.
    """
    target = f"custom-adapter:{adapter_id}"
    for name, cfg in runtime_profiles.items():
        if cfg.get("command_adapter_id") == target:
            return name
    return None


def register_custom_adapter(
    executable: str,
    version: str,
    capabilities: list[str],
    workspace_adapter: str = "pi",
    registered_by: str = "",
    intended_profile_name: str | None = None,
    dependency_manifest_version: int | None = None,
    dependencies: list[dict] | None = None,
) -> AdapterEntry:
    """Register a custom adapter executable.

    This is the D3 registration seam — the single entry point for custom
    adapter registration. It performs the full validation → hash →
    conformance → persistence pipeline.

    Steps:
    1. Validate ``executable`` (absolute path, regular file, executable)
    2. Validate ``version`` (non-empty string)
    3. Validate ``capabilities`` (list of non-empty strings)
    4. Validate ``workspace_adapter`` (one of claude/codex/opencode/pi)
    5. Validate dependency manifest (THR-107 seq244: new submissions MUST
       declare dependencies; legacy None is allowed for backward compat)
    6. Compute SHA-256 of the executable file
    7. Run the conformance probe (bounded stdin/stdout)
    8. Build and persist an ``AdapterEntry`` with status="pending"

    Re-registration: if an adapter with a derived id already exists AND
    its executable/hash/capabilities differ, the new entry replaces the
    old one — ALWAYS as ``status="pending"``.  Approval is never silently
    retained.

    Args:
        intended_profile_name: When set (THR-107 seq141 adapter-submission
            path), the adapter id is server-derived as ``<name>-adapter``
            and the entry records this binding. The submission endpoint
            MUST set this; the generic master-bearer registration path
            leaves it None.
        dependency_manifest_version: When set (THR-107 seq244), the version
            of the dependency manifest contract (must be exactly 1). New
            submissions (both register and submit paths) must declare
            this with a non-empty dependencies list.
        dependencies: List of dependency records, each with ``executable``
            (absolute path) and ``sha256`` (SHA-256 hex). Required when
            ``dependency_manifest_version`` is set.

    Returns the persisted ``AdapterEntry``.

    Raises ``ValueError`` at the first validation failure — ZERO durable
    residue is left before the persistence step.
    """
    # Step 0: Scoped-path enforcement (THR-107 seq339/340) — MUST happen
    # BEFORE validate_executable_path (which calls resolve()) and before
    # any conformance probe, hash computation, or durable persistence.
    # Compare the RAW caller-provided string in its original lexical form
    # to the server-derived canonical path.
    if intended_profile_name is not None:
        if not isinstance(intended_profile_name, str) or not intended_profile_name.strip():
            raise ValueError("intended_profile_name must be a non-empty string")
        intended_profile_name = intended_profile_name.strip()

        _scoped_id = generate_adapter_id(f"{intended_profile_name}-adapter")
        _canonical_dir, _required_path = compute_canonical_adapter_path(_scoped_id)
        _required_str = str(_required_path)

        # Pre-resolve checks on the RAW caller-provided string.
        if not executable or not os.path.isabs(executable):
            raise ValueError(
                f"executable must be an absolute path, got {executable!r}. "
                f"The scoped adapter wrapper must be created at the exact "
                f"canonical path: {_required_str}. The token was NOT consumed "
                f"and remains retryable."
            )

        if _has_traversal_spelling_registry(executable):
            raise ValueError(
                f"executable path contains traversal spelling: "
                f"{executable!r}. The scoped adapter wrapper must be "
                f"created at exactly the canonical path: {_required_str}. "
                f"No '..' components, symlinks, or alternate locations "
                f"are accepted. The token was NOT consumed and remains "
                f"retryable."
            )

        if executable != _required_str:
            raise ValueError(
                f"Scoped adapter {_scoped_id!r} requires the executable at the "
                f"server-owned canonical path: {_required_str}. "
                f"Received: {executable!r}. "
                f"Create your adapter wrapper at exactly the required "
                f"executable path returned by GET /runtime/adapters/"
                f"contract-reference. The token was NOT consumed and "
                f"remains retryable."
            )

    # Step 1–4: Validate inputs (no side effects)
    executable_path = validate_executable_path(executable)
    version = validate_version(version)
    if not version:
        raise ValueError("version must be a non-empty string after trimming whitespace")
    capabilities = validate_capabilities(capabilities)
    workspace_adapter = validate_workspace_adapter(workspace_adapter)

    # Validate intended_profile_name (already validated above if set)
    if intended_profile_name is not None:
        # already validated in Step 0 above
        pass

    # Step 5: Validate dependency manifest (seq244 — before any durable work)
    dep_manifest_version, normalized_deps = validate_dependency_manifest(
        dependency_manifest_version, dependencies
    )

    # Step 6: Compute SHA-256
    file_hash = compute_sha256(str(executable_path))

    # Generate adapter id — server-derived from intended_profile_name when
    # provided, otherwise from the executable filename (master-bearer path).
    if intended_profile_name is not None:
        adapter_id = generate_adapter_id(f"{intended_profile_name}-adapter")
        adapter_name = intended_profile_name

        # Scoped-path enforcement (THR-107 seq339/340) recheck: the
        # executable MUST be at the daemon-managed canonical location.
        # This is the registration-seam recheck — even if the route-layer
        # check is somehow bypassed, this critical boundary rejects foreign
        # paths.  The pre-resolve check (Step 0 above) already caught
        # traversal and non-absolute forms; this post-resolve recheck
        # guards against path-equivalence bypasses via validate_executable_path.
        _canonical_dir, _required_path = compute_canonical_adapter_path(adapter_id)
        if str(executable_path) != str(_required_path):
            raise ValueError(
                f"Scoped adapter {adapter_id!r} requires the executable at the "
                f"server-owned canonical path: {_required_path}. "
                f"Received: {executable_path}. "
                f"Create your adapter wrapper at exactly the required "
                f"executable path returned by GET /runtime/adapters/"
                f"contract-reference. The token was NOT consumed and "
                f"remains retryable."
            )
    else:
        adapter_name = Path(executable).name  # Use filename as default name
        adapter_id = generate_adapter_id(adapter_name)

    # Step 7: Conformance probe (spawns subprocess — no durable residue on failure)
    conformance_output = run_conformance_probe(str(executable_path), adapter_id)

    # seq244: Token-metering truthfulness — if capabilities include
    # "token_metering", the conformance probe MUST produce a valid
    # non-null token_usage with at least one numeric (non-null)
    # accounting field.
    if "token_metering" in capabilities:
        if conformance_output.token_usage is None:
            raise ValueError(
                f"Adapter {adapter_id!r} declares token_metering capability "
                f"but the conformance probe returned no token_usage. "
                f"An adapter declaring token_metering must emit valid, "
                f"non-null token_usage at conformance time."
            )
        tu = conformance_output.token_usage
        if (tu.input_tokens is None and tu.output_tokens is None
                and tu.cache_read_tokens is None and tu.cache_creation_tokens is None
                and tu.reasoning_tokens is None):
            raise ValueError(
                f"Adapter {adapter_id!r} declares token_metering capability "
                f"but the conformance probe returned token_usage with all "
                f"accounting fields null. An adapter declaring token_metering "
                f"must report at least one numeric token count."
            )

    # Step 8: Build and persist atomically with competing writes.
    # Acquire the store lock so that no concurrent approval or
    # registration can interleave between the existing-entry check
    # and the durable write.
    now = datetime.now(timezone.utc).isoformat()

    acquire_store_lock()
    try:
        # Re-registration safety: if an active runtime profile is already
        # bound to this adapter id (via command_adapter_id:
        # custom-adapter:<id>), reject the re-registration rather than
        # silently leaving an active profile targeting a PENDING artifact.
        # The operator must unbind the profile before re-registering.
        from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
        bound_profile = _find_active_profile_bound(adapter_id, load_runtime_profiles())
        if bound_profile is not None:
            raise ValueError(
                f"Cannot re-register adapter {adapter_id!r}: the runtime "
                f"profile {bound_profile!r} is currently bound to it "
                f"(command_adapter_id: custom-adapter:{adapter_id}). "
                f"Remove the profile first via Settings → Executors → "
                f"Custom CLIs, then re-register the adapter."
            )

        # Reload existing entry from disk AT the commit boundary
        existing = get_adapter(adapter_id)

        entry = AdapterEntry(
            id=adapter_id,
            name=adapter_name,
            executable=str(executable_path),
            executable_hash=file_hash,
            version=version,
            capabilities=capabilities,
            contract_version=1,
            workspace_adapter=workspace_adapter,
            status="pending",  # ALWAYS pending
            registered_at=now,
            registered_by=registered_by,
            approved_at=None,
            approved_by=None,
            intended_profile_name=intended_profile_name,
            dependency_manifest_version=dep_manifest_version,
            dependencies=normalized_deps,
        )

        # Re-registration guard: if existing entry differs, status MUST be
        # pending.  If identical (same executable, hash, caps, deps), keep
        # original status (forward-compat for D4 approval).
        if existing is not None:
            if (existing.executable == str(executable_path) and
                    existing.executable_hash == file_hash and
                    existing.version == version and
                    existing.capabilities == capabilities and
                    existing.workspace_adapter == workspace_adapter and
                    existing.dependency_manifest_version == dep_manifest_version and
                    existing.dependencies == normalized_deps):
                # Identical — preserve original registration metadata
                entry = AdapterEntry(
                    id=entry.id,
                    name=entry.name,
                    executable=entry.executable,
                    executable_hash=entry.executable_hash,
                    version=entry.version,
                    capabilities=entry.capabilities,
                    contract_version=entry.contract_version,
                    workspace_adapter=entry.workspace_adapter,
                    status="pending",
                    registered_at=existing.registered_at,
                    registered_by=existing.registered_by,
                    approved_at=None,
                    approved_by=None,
                    intended_profile_name=intended_profile_name,
                    dependency_manifest_version=dep_manifest_version,
                    dependencies=normalized_deps,
                )
            else:
                # Changed — new registration, pending
                pass  # entry already has status="pending" and intended_profile_name

        # Persist atomically (save_adapter reloads + replaces under the
        # same lock, which serializes against competing writers).
        _save_adapter_locked(entry)
    finally:
        release_store_lock()

    return entry


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------


def list_adapters() -> list[AdapterEntry]:
    """Return all registered custom adapters.

    D3: all entries are status "pending" only.
    """
    return list(load_adapters().values())


def _perform_adapter_profile_binding(
    adapter_id: str,
    profile_name: str,
    workspace_adapter: str,
) -> dict:
    """Bind a named custom profile to an APPROVED adapter (THR-107 seq237).

    Precondition: the caller already holds ``acquire_store_lock()`` and the
    adapter has just been transitioned to APPROVED under that lock.  This
    function is NOT reentrant — it does not re-acquire the lock.

    Steps:
    1. Validate intended profile name (no builtin collision)
    2. Validate on-disk adapter integrity (resolve_adapter)
    3. Validate D7B custom adapter binding
    4. Build + validate profile config
    5. Snapshot pre-bind state for compensating rollback
    6. Write durable runtime profile
    7. Replace in-memory registry
    8. Audit the binding

    On any post-durable-write failure, restores pre-request state and
    raises ``ValueError`` so the caller can roll back approval.

    Returns a dict ``{profile_name, command_adapter_id, workspace_adapter_id,
    kind, status, adapter_id}`` describing the bound profile.
    """
    from runtime.orchestrator.executor_registry import ExecutorRegistry, get_registry
    from runtime.orchestrator.runtime_executor_store import (
        load_runtime_profiles,
        remove_runtime_profile,
        save_runtime_profile,
    )
    from runtime.infrastructure.database import Database
    from runtime.runtime import daemon_home

    registry = get_registry()

    # 1. Profile name must not collide with a built-in
    BUILTIN_KINDS_NAMES = {"claude", "codex", "opencode", "pi"}
    if profile_name.lower() in BUILTIN_KINDS_NAMES:
        raise ValueError(
            f"Profile name {profile_name!r} collides with a built-in "
            f"executor. Choose a different name."
        )

    # 2. On-disk integrity check via resolve_adapter (re-validates hash)
    resolved = resolve_adapter(adapter_id)
    if resolved is None:
        raise ValueError(
            f"Adapter {adapter_id!r} is approved but the on-disk "
            f"executable is missing, not executable, or has a hash "
            f"mismatch. Re-register the adapter."
        )

    # 3. D7B custom-adapter validation
    try:
        ExecutorRegistry._validate_custom_adapter_binding(adapter_id)
    except ValueError as exc:
        raise ValueError(str(exc))

    # 4. Build + validate profile config
    profile_cfg = {
        "command": None,
        "argv_template": None,
        "workspace_adapter_id": workspace_adapter,
        "command_adapter_id": f"custom-adapter:{adapter_id}",
    }
    try:
        profile = ExecutorRegistry.validate_custom_profile_config(
            profile_name, profile_cfg
        )
    except ValueError as exc:
        raise ValueError(str(exc))

    # 5. Snapshot pre-request state for compensating rollback
    pre_request_profiles = dict(load_runtime_profiles())
    pre_request_in_memory = registry.get_profile(profile_name)

    # Check for cross-adapter profile conflict
    existing_profile = registry.get_profile(profile_name)
    if existing_profile is not None:
        existing_cmd = getattr(existing_profile, "command_adapter_id", None) or ""
        if existing_cmd != f"custom-adapter:{adapter_id}":
            raise ValueError(
                f"Profile {profile_name!r} already exists and is bound to "
                f"a different adapter ({existing_cmd}). Cannot bind adapter "
                f"{adapter_id!r} to this profile name."
            )

    # Also check durable profiles for cross-adapter conflict
    durable_conflict = any(
        name == profile_name and cfg.get("command_adapter_id") != f"custom-adapter:{adapter_id}"
        for name, cfg in pre_request_profiles.items()
    )
    if durable_conflict:
        raise ValueError(
            f"A durable runtime profile named {profile_name!r} is already "
            f"bound to a different adapter. Remove the existing profile "
            f"first before binding to adapter {adapter_id!r}."
        )

    durable_committed = False
    try:
        # 6. Write the durable runtime store first
        save_runtime_profile(profile_name, profile_cfg)
        durable_committed = True

        # 7. Replace in the in-memory registry
        registry.replace_custom_profile(profile)

        # 8. Audit the successful binding
        audit_db_path = daemon_home() / "runtime-audit.db"
        db = Database(audit_db_path)
        try:
            db.insert_audit_log_uncommitted(
                task_id=f"executor:{profile_name}",
                agent="founder",
                action="executor_registered",
                payload={
                    "adapter_id": adapter_id,
                    "command_adapter_id": f"custom-adapter:{adapter_id}",
                    "workspace_adapter_id": workspace_adapter,
                    "bound_via": "approve_and_bind",
                },
            )
            db.commit()
        finally:
            db.close()
    except BaseException:
        if durable_committed:
            # Compensating rollback: restore both durable and in-memory surfaces
            if profile_name in pre_request_profiles:
                save_runtime_profile(profile_name, pre_request_profiles[profile_name])
            else:
                remove_runtime_profile(profile_name)
            if pre_request_in_memory is not None:
                registry.replace_custom_profile(pre_request_in_memory)
            else:
                registry.unregister_custom_profile(profile_name)
        raise ValueError(
            "Profile binding failed after durable write; "
            "pre-request state has been restored."
        )

    return {
        "profile_name": profile.name,
        "command_adapter_id": profile.command_adapter_id,
        "workspace_adapter_id": profile.workspace_adapter_id,
        "kind": profile.kind,
        "status": "connected",
        "adapter_id": adapter_id,
    }


def approve_adapter(
    adapter_id: str,
    executable: str,
    executable_hash: str,
    version: str,
    capabilities: list[str],
    contract_version: int,
    workspace_adapter: str,
    approved_by: str = "founder/master-bearer",
    auto_bind_profile: bool = False,
    dependency_manifest_version: int | None = None,
    dependencies: list[dict] | None = None,
) -> AdapterEntry:
    """Approve a pending custom adapter (D4 founder-gated approval gate).

    This is a deliberate, explicit transition from durable PENDING to durable
    APPROVED. The approval request MUST bind the exact durable artifact
    snapshot the founder inspected — every material identity fact is compared
    against the durable store entry.

    **THR-107 seq237**: when ``auto_bind_profile`` is True and the adapter
    has a nonempty ``intended_profile_name``, this function atomically
    approves the snapshot AND creates/binds the named custom profile
    (``command_adapter_id: custom-adapter:<id>``) in the same lock-backed
    critical section. If binding fails, the approval is rolled back to
    PENDING and no partial state is left. For idempotent retries, if the
    adapter is already APPROVED and the profile is already bound, the
    existing entry is returned with ``profile_bound: already_bound``.

    **Atomicity (D4 REVISE)**: the durable comparison of all approval
    facts, including the optional dependency manifest, and the
    PENDING→APPROVED transition is serialized with competing
    registration writes via a store-level lock.  After acquiring the lock
    the function reloads the entry from disk and re-validates every fact
    at the commit boundary.  A changed re-registration that won the lock
    first causes the stale approval to reject with no durable overwrite.
    If approval wins first, a subsequent re-registration durably replaces
    the entry with a new PENDING snapshot and cleared provenance.

    Exact-idempotence: if the adapter is already APPROVED with identical
    stored immutable facts, the existing entry is returned unchanged (no
    duplicate writes, no provenance overwrite).

    Raises ``ValueError`` with an actionable message when:
      - The adapter id is unknown
      - The entry is not PENDING (e.g., already-approved incompatible
        repeat, or a malformed state)
      - Any snapshot fact mismatches the store: executable, hash, version,
        capabilities, contract_version, or workspace_adapter
      - Any malformed/empty values are provided
      - A competing re-registration changed the entry between the caller's
        inspection and the commit boundary (stale approval)
      - Profile binding fails (name collision, validation, registry, audit)

    Returns the approved ``AdapterEntry``. When ``auto_bind_profile`` was
    True and the profile was bound, ``entry.profile_bound`` is set to the
    bind result dict (read by the route to include in the response).

    This is NOT authorization for D5/D7/D12 changes — only the approval
    transition within D4 scope.
    """
    # Guard: validate all inputs are non-empty and well-typed
    if not adapter_id or not isinstance(adapter_id, str):
        raise ValueError("adapter_id must be a non-empty string")
    if not executable or not isinstance(executable, str):
        raise ValueError("executable must be a non-empty string")
    if not executable_hash or not isinstance(executable_hash, str):
        raise ValueError("executable_hash must be a non-empty string")
    if not version or not isinstance(version, str):
        raise ValueError("version must be a non-empty string")
    if not isinstance(capabilities, list):
        raise ValueError("capabilities must be a list")
    if not isinstance(contract_version, int) or isinstance(contract_version, bool):
        raise ValueError("contract_version must be an integer")
    if not workspace_adapter or not isinstance(workspace_adapter, str):
        raise ValueError("workspace_adapter must be a non-empty string")

    # Serialize the entire load-validate-save critical section so that
    # competing registration writes cannot overwrite a stale approval.
    acquire_store_lock()
    try:
        # Reload from disk AT the commit boundary — this is the durable
        # re-read that detects a re-registration that won the lock first.
        entry = get_adapter(adapter_id)
        if entry is None:
            raise ValueError(
                f"Unknown adapter {adapter_id!r}. Register the adapter first; "
                f"it must be in PENDING state before approval."
            )

        # Normalize deps for comparison (None vs [] are equivalent for legacy)
        _req_deps = dependencies or []
        _entry_deps = entry.dependencies or []

        # Exact-idempotence: if already APPROVED with identical facts, return as-is
        if entry.status == "approved":
            if (entry.executable == executable and
                    entry.executable_hash == executable_hash and
                    entry.version == version and
                    entry.capabilities == capabilities and
                    entry.contract_version == contract_version and
                    entry.workspace_adapter == workspace_adapter and
                    entry.dependency_manifest_version == dependency_manifest_version and
                    _entry_deps == _req_deps):
                logger.info(
                    "approve_adapter: adapter %r already approved with identical "
                    "facts — idempotent no-op", adapter_id
                )
                return entry
            # Already approved but facts differ → reject
            raise ValueError(
                f"Adapter {adapter_id!r} is already APPROVED with different "
                f"immutable facts. Re-register (which resets to PENDING), then "
                f"re-approve with the new snapshot. Current stored hash: "
                f"{entry.executable_hash[:12]}..., current version: {entry.version}."
            )

        # Must be PENDING — reject any other state
        if entry.status != "pending":
            raise ValueError(
                f"Adapter {adapter_id!r} is status={entry.status!r}, not PENDING. "
                f"Only PENDING adapters may be approved."
            )

        # Verify EVERY material identity fact matches the durable store
        if entry.executable != executable:
            raise ValueError(
                f"executable mismatch for {adapter_id!r}: "
                f"store has {entry.executable!r}, approval request has {executable!r}"
            )
        if entry.executable_hash != executable_hash:
            raise ValueError(
                f"executable_hash mismatch for {adapter_id!r}: "
                f"store has {entry.executable_hash[:12]}..., "
                f"approval request has {executable_hash[:12]}..."
            )
        if entry.version != version:
            raise ValueError(
                f"version mismatch for {adapter_id!r}: "
                f"store has {entry.version!r}, approval request has {version!r}"
            )
        if entry.capabilities != capabilities:
            raise ValueError(
                f"capabilities mismatch for {adapter_id!r}: "
                f"store has {entry.capabilities!r}, "
                f"approval request has {capabilities!r}"
            )
        if entry.contract_version != contract_version:
            raise ValueError(
                f"contract_version mismatch for {adapter_id!r}: "
                f"store has {entry.contract_version}, "
                f"approval request has {contract_version}"
            )
        if entry.workspace_adapter != workspace_adapter:
            raise ValueError(
                f"workspace_adapter mismatch for {adapter_id!r}: "
                f"store has {entry.workspace_adapter!r}, "
                f"approval request has {workspace_adapter!r}"
            )
        if entry.dependency_manifest_version != dependency_manifest_version:
            raise ValueError(
                f"dependency_manifest_version mismatch for {adapter_id!r}: "
                f"store has {entry.dependency_manifest_version!r}, "
                f"approval request has {dependency_manifest_version!r}"
            )
        if _entry_deps != _req_deps:
            raise ValueError(
                f"dependencies mismatch for {adapter_id!r}: "
                f"store has {len(_entry_deps)} record(s), "
                f"approval request has {len(_req_deps)} record(s)"
            )

        # Transition from PENDING → APPROVED
        now = datetime.now(timezone.utc).isoformat()
        approved_entry = AdapterEntry(
            id=entry.id,
            name=entry.name,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            status="approved",
            registered_at=entry.registered_at,
            registered_by=entry.registered_by,
            approved_at=now,
            approved_by=approved_by,
            intended_profile_name=entry.intended_profile_name,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        _save_adapter_locked(approved_entry)
        logger.info(
            "approve_adapter: adapter %r approved at %s by %s",
            adapter_id, now, approved_by,
        )

        # THR-107 seq237: atomically bind profile when requested
        # and the adapter has an intended_profile_name
        profile_bind_result = None
        if auto_bind_profile and entry.intended_profile_name:
            try:
                profile_bind_result = _perform_adapter_profile_binding(
                    adapter_id=adapter_id,
                    profile_name=entry.intended_profile_name,
                    workspace_adapter=entry.workspace_adapter,
                )
                logger.info(
                    "approve_adapter: adapter %r profile %r bound in same transaction",
                    adapter_id, entry.intended_profile_name,
                )
            except ValueError:
                # Rollback: restore adapter to PENDING, preserving every
                # durable identity fact including the seq244 dependency manifest.
                rolled_back = AdapterEntry(
                    id=entry.id,
                    name=entry.name,
                    executable=entry.executable,
                    executable_hash=entry.executable_hash,
                    version=entry.version,
                    capabilities=entry.capabilities,
                    contract_version=entry.contract_version,
                    workspace_adapter=entry.workspace_adapter,
                    status="pending",
                    registered_at=entry.registered_at,
                    registered_by=entry.registered_by,
                    approved_at=None,
                    approved_by=None,
                    intended_profile_name=entry.intended_profile_name,
                    dependency_manifest_version=entry.dependency_manifest_version,
                    dependencies=entry.dependencies,
                )
                _save_adapter_locked(rolled_back)
                logger.warning(
                    "approve_adapter: profile binding failed for %r — "
                    "rolled back approval to PENDING", adapter_id,
                )
                raise

        # Attach binding result for the route to include in response
        approved_entry.profile_bound = profile_bind_result  # type: ignore[attr-defined]
        return approved_entry
    finally:
        release_store_lock()


def resolve_adapter(adapter_id: str) -> AdapterEntry | None:
    """Resolve an adapter by id for launch/binding.

    D4: gated on APPROVED status + on-disk hash verification.

    For APPROVED adapters, the on-disk executable MUST still:
      - Exist at its pinned absolute path
      - Be a regular file
      - Be executable by the current user
      - Have the stored SHA-256 hash

    A tampered, removed, non-regular, or unexecutable artifact does NOT
    resolve — returns None with actionable re-registration guidance.
    The daemon NEVER silently updates the stored hash or status.

    Use ``get_adapter`` for read-only inspection (e.g. GET route).
    """
    entry = get_adapter(adapter_id)
    if entry is None:
        return None
    if entry.status != "approved":
        logger.warning(
            "resolve_adapter: adapter %r is status=%r (not approved) — "
            "refusing to resolve for launch/binding. D4 approval is required.",
            adapter_id,
            entry.status,
        )
        return None

    # D4: verify on-disk executable integrity for APPROVED adapters
    import os as _os
    from pathlib import Path as _Path

    exe_path = _Path(entry.executable)
    if not exe_path.exists():
        logger.warning(
            "resolve_adapter: approved adapter %r executable %r no longer exists. "
            "Re-register the adapter to restore.",
            adapter_id, entry.executable,
        )
        return None
    if not exe_path.is_file():
        logger.warning(
            "resolve_adapter: approved adapter %r path %r is not a regular file. "
            "Re-register the adapter with a valid executable.",
            adapter_id, entry.executable,
        )
        return None
    if not _os.access(exe_path, _os.X_OK):
        logger.warning(
            "resolve_adapter: approved adapter %r executable %r is not executable. "
            "Re-register the adapter with a valid executable.",
            adapter_id, entry.executable,
        )
        return None

    # Hash verification
    current_hash = compute_sha256(str(exe_path))
    if current_hash != entry.executable_hash:
        logger.warning(
            "resolve_adapter: approved adapter %r hash mismatch — "
            "stored=%s, current=%s. "
            "The executable has been modified since approval. "
            "Re-register to update the hash and re-approve.",
            adapter_id,
            entry.executable_hash[:12],
            current_hash[:12],
        )
        return None

    return entry
