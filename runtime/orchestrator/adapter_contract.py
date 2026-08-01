"""Versioned adapter input/output contract (THR-107 D3/D7B/D12).

Defines the canonical ``AdapterInput`` and ``AdapterOutput`` Pydantic models
that form the stdin/stdout contract between the daemon and custom adapter
executables. These models are the authoritative shape for both the conformance
probe run at registration time (D3) AND the runtime launch path (D7B) where
``CustomAdapterExecutor`` maps the adapter's AdapterOutput into the existing
``ExecutorResult`` lifecycle.

The contract is additive — it does not alter ``ExecutorResult``, any
existing audit shape, or any SQLite column.

D3: conformance-probe validation at registration time.
D7B: runtime enforcement — every custom-adapter launch requires a valid v1
AdapterOutput; ``CustomAdapterExecutor`` rejects mismatched adapter identity,
adapter version, contract version, and session-id-echo before mapping any
result or accounting data. The exact approved artifact SHA-256 is verified
on EVERY launch attempt (inside the per-attempt launch closure, so throttle
retries after a rate-limited response re-verify the artifact before the next
Popen — D4 fail-closed trust boundary).
D12: finalized stable external contract — this module is the authoritative
code-level reference; the unified-adapter architecture spec §2 is the
normative prose. Protocol/05b, 05c, executor guide, and envelope design spec
parity shipped in the same PR.

THR-107 seq244: dependency-manifest extension — adds ``DependencyManifest``
and ``DependencyRecord`` models for declared child executable dependencies.
This is an independently versioned registration extension that does NOT
change the AdapterInput/AdapterOutput contract version.  The
``dependency_manifest_version`` field is separate from ``contract_version``.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


# ---------------------------------------------------------------------------
# Adapter Input Contract (§2.1)
# ---------------------------------------------------------------------------


class InvocationInfo(BaseModel):
    """Identifies the invocation for the adapter."""
    invocation_id: str = Field(..., description="HappyRanch session id (sess-<uuid>)")
    task_id: str | None = Field(None, description="Owning task id (null for threads/dreams/wakes)")
    agent: str = Field(..., description="Agent name")
    org: str = Field(..., description="Org slug")
    invocation_kind: str = Field(..., description="task | thread | wake | dream | schedule")


class TimeoutInfo(BaseModel):
    """Wall-clock and subprocess timeout parameters."""
    deadline_seconds: int = Field(..., description="Wall-clock deadline")
    max_runtime_seconds: int = Field(..., description="Subprocess communicate() timeout")


class ModelInfo(BaseModel):
    """Model selection parameters (null → adapter default)."""
    model_id: str = Field(..., description="Model identifier string")
    model_arg_template: list[str] | None = Field(None, description="How to splice into argv, e.g. ['--model', '{model}']")


class SessionInfo(BaseModel):
    """Resume-capable session info."""
    resume_session_id: str = Field(..., description="The agent CLI's own session id to resume")


class ExecutorContext(BaseModel):
    """Immutable per-invocation executor context."""
    provider: str = Field(..., description="Throttle key (profile name)")
    adapter_id: str = Field(..., description="Workspace adapter id (D6: workspace_adapter_id)")
    adapter_version: str = Field(..., description="Adapter implementation version")
    permission_mode: str | None = Field(None, description="Provider-specific permission posture")


class AdapterInput(BaseModel):
    """The contract the daemon passes to EVERY adapter via stdin.

    This is the normative type from §2.1 of the unified adapter architecture.
    """

    contract_version: int = Field(..., ge=1, description="Version of THIS input contract")
    invocation: InvocationInfo
    prompt: str = Field(..., description="Full prompt text (includes session-lifetime preamble)")
    workspace: str = Field(..., description="Absolute path to prepared workspace")
    timeout: TimeoutInfo
    model: ModelInfo | None = Field(None, description="Null → adapter uses its own default")
    session: SessionInfo | None = Field(None, description="Present for resume-capable invocations")
    executor_context: ExecutorContext


# ---------------------------------------------------------------------------
# Adapter Output Contract (§2.2)
# ---------------------------------------------------------------------------


class TokenUsageInfo(BaseModel):
    """Token usage reporting (maps to existing TokenUsage model)."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    usage_raw_json: str | None = None


class ResultText(BaseModel):
    """Additive result text field (§2.2)."""
    text: str | None = Field(None, description="Agent's final response text (trimmed)")


class AdapterMetadata(BaseModel):
    """Provenance metadata from the adapter (§2.2)."""
    adapter: str = Field(..., description="REQUIRED to exactly equal the stable server-derived canonical_adapter_id from the contract-reference / submitted adapter ID. Never a display name, provider, or arbitrary implementation identity. A mismatch fails the conformance probe at registration AND blocks every launch at runtime.")
    adapter_version: str = Field(..., description="Version of the adapter implementation")
    contract_version: int = Field(..., description="Version of THIS result contract")


class AdapterOutput(BaseModel):
    """The contract EVERY adapter returns via stdout.

    This is the normative type from §2.2 of the unified adapter architecture.

    ``stdout_tail`` and ``stderr_tail`` are TOP-LEVEL fields (per §2.5) —
    consumed directly by ``run_step`` and ``thread_runner`` for failure
    forensics. They MUST NOT be nested.
    """

    success: bool = Field(..., description="Did the subprocess exit 0?")
    duration_seconds: int = Field(..., description="Wall-clock duration")
    session_id: str = Field(..., description="Echo back the invocation id")
    returncode: int | None = Field(None, description="Subprocess exit code (null on timeout)")
    stdout_tail: str = Field(..., description="Last ~2000 bytes of stdout")
    stderr_tail: str = Field(..., description="Last ~2000 bytes of stderr")
    result: ResultText | None = Field(None, description="Additive result text")
    token_usage: TokenUsageInfo | None = Field(None, description="Token usage (maps to TokenUsage model)")
    error: str | None = Field(None, description="Human-readable error")
    agent_session_id: str | None = Field(None, description="Agent CLI's own session id (for resume)")
    rate_limited: bool = Field(False, description="Did the provider rate-limit this attempt?")
    adapter_metadata: AdapterMetadata = Field(..., description="Provenance metadata from the adapter")
    child_session_id: str | None = Field(None, description="Future: spawned child session id")
    raw_forensics_ref: str | None = Field(None, description="Path/ref to raw forensic capture")


# ---------------------------------------------------------------------------
# THR-107 seq244: Dependency Manifest Extension
# ---------------------------------------------------------------------------


class DependencyRecord(BaseModel):
    """A single declared child executable dependency.

    Each record binds an absolute path to a SHA-256 hex digest.
    The executable must be an absolute path, must exist, must be a
    regular file, must be executable, and must match the declared hash.
    """
    executable: str = Field(..., description="Absolute path to the child executable")
    sha256: str = Field(..., description="SHA-256 hex digest of the executable", min_length=64, max_length=64)


def _strict_int_for_manifest(v: Any) -> int:
    """Reject non-strict integers (float, string, bool) at the boundary.

    Pydantic int coercion silently accepts JSON 1.0, ``"1"``, and ``true``.
    This validator runs in ``mode='before'`` so it sees the raw JSON-decoded
    Python value and rejects anything that is not exactly ``int`` (excluding
    ``bool``, which is a subclass of ``int`` in Python).
    """
    if isinstance(v, bool):
        raise ValueError(
            "dependency_manifest_version must be an integer, not a boolean"
        )
    if not isinstance(v, int):
        raise ValueError(
            f"dependency_manifest_version must be an integer, got {type(v).__name__}"
        )
    return v


class DependencyManifest(BaseModel):
    """Independently versioned dependency manifest extension.

    This is a SEPARATE versioning space from AdapterInput/AdapterOutput
    ``contract_version``.  The ``dependency_manifest_version`` field can
    evolve independently.

    A non-empty ``dependencies`` list is REQUIRED for new submissions.
    Legacy entries (those without this extension) retain their exact
    current launch behavior and are never auto-mutated.
    """
    dependency_manifest_version: Annotated[int, BeforeValidator(_strict_int_for_manifest), Field(ge=1, le=1, description="Version of the dependency manifest contract (must be exactly 1)")]
    dependencies: list[DependencyRecord] = Field(..., min_length=1, description="Non-empty list of declared child executable dependencies")
