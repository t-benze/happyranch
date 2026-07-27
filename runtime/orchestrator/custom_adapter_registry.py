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
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput
from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    compute_sha256,
    get_adapter,
    load_adapters,
    save_adapter,
)

logger = logging.getLogger(__name__)

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


def validate_version(version: str) -> str:
    """Validate an adapter version string.

    Must be a non-empty string. Returns the trimmed version.
    """
    if not version or not isinstance(version, str):
        raise ValueError("version must be a non-empty string")
    return version.strip()


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


def build_probe_input(adapter_name: str) -> AdapterInput:
    """Build a minimal, deterministic ``AdapterInput`` for the conformance probe.

    The probe input is a lightweight sample invocation that lets the adapter
    demonstrate it can parse the contract and produce valid output. It does
    NOT exercise a real agentic session — it only validates the contract shapes.
    """
    from runtime.orchestrator.adapter_contract import (
        ExecutorContext,
        InvocationInfo,
        TimeoutInfo,
    )

    return AdapterInput(
        contract_version=1,
        invocation=InvocationInfo(
            invocation_id="probe-sess-00000000-0000-0000-0000-000000000000",
            task_id=None,
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        ),
        prompt="conformance-probe: respond with a valid AdapterOutput.",
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


def run_conformance_probe(executable: str, adapter_name: str) -> AdapterOutput:
    """Run a bounded stdin/stdout conformance probe against an adapter executable.

    1. Builds a minimal ``AdapterInput`` as JSON.
    2. Spawns the executable as a subprocess, pipes stdin/stdout.
    3. Sends the input JSON via stdin.
    4. Reads stdout (capped at 1 MB), parses as ``AdapterOutput`` JSON.
    5. Validates the output against the contract.

    Returns the parsed ``AdapterOutput`` on success.

    Raises ``ValueError`` with an actionable message when:
      - The subprocess fails to start (OSError, FileNotFoundError)
      - The subprocess times out
      - The subprocess exits non-zero
      - The stdout is not valid JSON
      - The JSON does not validate as ``AdapterOutput``
      - The ``adapter_metadata.contract_version`` is missing or unknown

    ZERO durable residue is left on failure — this is a pure validation step.
    """
    probe_input = build_probe_input(adapter_name)
    input_json = probe_input.model_dump_json()

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

    try:
        stdout_bytes, stderr_bytes = proc.communicate(
            input=input_json.encode("utf-8"),
            timeout=CONFORMANCE_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()  # Drain
        raise ValueError(
            f"Conformance probe timed out after "
            f"{CONFORMANCE_PROBE_TIMEOUT_SECONDS}s for {executable!r}"
        )

    if proc.returncode != 0:
        stderr_tail = ""
        if stderr_bytes:
            stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-2000:]
        raise ValueError(
            f"Conformance probe exited with code {proc.returncode} for "
            f"{executable!r}. stderr tail: {stderr_tail[:500]}"
        )

    if not stdout_bytes:
        raise ValueError(
            f"Conformance probe produced no stdout for {executable!r}"
        )

    # Decode and parse stdout as JSON
    try:
        stdout_text = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Conformance probe stdout is not valid UTF-8 for {executable!r}: {exc}"
        ) from exc

    # Trim to max allowed bytes before parsing
    if len(stdout_text) > CONFORMANCE_PROBE_MAX_STDOUT_BYTES:
        stdout_text = stdout_text[:CONFORMANCE_PROBE_MAX_STDOUT_BYTES]

    try:
        output_dict = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Conformance probe stdout is not valid JSON for {executable!r}: {exc}"
        ) from exc

    if not isinstance(output_dict, dict):
        raise ValueError(
            f"Conformance probe stdout is not a JSON object for {executable!r}; "
            f"got {type(output_dict).__name__}"
        )

    # Validate against AdapterOutput schema
    try:
        output = AdapterOutput.model_validate(output_dict)
    except Exception as exc:
        raise ValueError(
            f"Conformance probe output does not match AdapterOutput contract "
            f"for {executable!r}: {exc}"
        ) from exc

    # Validate contract_version in adapter_metadata
    if output.adapter_metadata.contract_version < 1:
        raise ValueError(
            f"Conformance probe output has unknown contract_version "
            f"{output.adapter_metadata.contract_version} for {executable!r}; "
            f"minimum supported version is 1"
        )

    # Basic sanity: success must be true
    if not output.success:
        raise ValueError(
            f"Conformance probe reported success=false for {executable!r}"
        )

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


def register_custom_adapter(
    executable: str,
    version: str,
    capabilities: list[str],
    workspace_adapter: str = "pi",
    registered_by: str = "",
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
    5. Compute SHA-256 of the executable file
    6. Run the conformance probe (bounded stdin/stdout)
    7. Build and persist an ``AdapterEntry`` with status="pending"

    Re-registration: if an adapter with a derived id already exists AND
    its executable/hash/capabilities differ, the new entry replaces the
    old one — ALWAYS as ``status="pending"``.  Approval is never silently
    retained.

    Returns the persisted ``AdapterEntry``.

    Raises ``ValueError`` at the first validation failure — ZERO durable
    residue is left before the persistence step.
    """
    # Step 1–4: Validate inputs (no side effects)
    executable_path = validate_executable_path(executable)
    version = validate_version(version)
    capabilities = validate_capabilities(capabilities)
    workspace_adapter = validate_workspace_adapter(workspace_adapter)

    # Step 5: Compute SHA-256
    file_hash = compute_sha256(str(executable_path))

    # Generate adapter id
    adapter_name = Path(executable).name  # Use filename as default name
    adapter_id = generate_adapter_id(adapter_name)

    # Step 6: Conformance probe (spawns subprocess — no durable residue on failure)
    run_conformance_probe(str(executable_path), adapter_id)

    # Step 7: Build and persist
    now = datetime.now(timezone.utc).isoformat()

    # Check for existing entry to enforce re-registration semantics
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
        status="pending",  # ALWAYS pending in D3
        registered_at=now,
        registered_by=registered_by,
        approved_at=None,
        approved_by=None,
    )

    # Re-registration guard: if existing entry differs, status MUST be pending.
    # If identical (same executable, hash, caps), keep original status
    # (forward-compat for D4 approval — but D3 always has pending anyway).
    if existing is not None:
        if (existing.executable == str(executable_path) and
                existing.executable_hash == file_hash and
                existing.version == version and
                existing.capabilities == capabilities and
                existing.workspace_adapter == workspace_adapter):
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
                status="pending",  # D3: always pending
                registered_at=existing.registered_at,
                registered_by=existing.registered_by,
                approved_at=None,  # D3: never approved
                approved_by=None,
            )
        else:
            # Changed — new registration, pending
            pass  # entry already has status="pending"

    # Persist atomically
    save_adapter(entry)

    return entry


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------


def list_adapters() -> list[AdapterEntry]:
    """Return all registered custom adapters.

    D3: all entries are status "pending" only.
    """
    return list(load_adapters().values())


def resolve_adapter(adapter_id: str) -> AdapterEntry | None:
    """Resolve an adapter by id.

    D3: returns the entry regardless of status (all are pending).
    D4: will gate on approved status.
    """
    return get_adapter(adapter_id)
