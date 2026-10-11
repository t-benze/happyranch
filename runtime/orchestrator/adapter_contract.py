"""Versioned adapter input/output contract (THR-107 D3/D7B/D12).

Defines the canonical ``AdapterInput`` and ``AdapterOutput`` Pydantic models
that form the stdin/stdout contract between the daemon and custom adapter
executables. These models are the authoritative shape for both the conformance
probe run at registration time (D3) AND the runtime launch path (D7B) where
``CustomAdapterExecutor`` maps the adapter's AdapterOutput into the existing
``ExecutorResult`` lifecycle.

The contract is additive — optional fields may be mapped into
``ExecutorResult`` without changing any existing audit shape or SQLite column.

D3: conformance-probe validation at registration time.
D7B: runtime enforcement — every custom-adapter launch requires a valid v1
AdapterOutput; ``CustomAdapterExecutor`` rejects mismatched adapter identity,
adapter version, contract version, and session-id-echo before mapping any
result or accounting data. The exact approved artifact SHA-256 is verified
on EVERY launch attempt (inside the per-attempt launch closure, so throttle
retries after a rate-limited response re-verify the artifact before the next
Popen — D4 fail-closed trust boundary).
D12: finalized stable external contract — this module is the authoritative
code-level reference. Current optional input-v2 prose is in
docs/superpowers/specs/2026-10-11-agent-reasoning-effort.md; original
unified-adapter and Protocol references remain historical.

THR-107 seq244: dependency-manifest extension — adds ``DependencyManifest``
and ``DependencyRecord`` models for declared child executable dependencies.
This is an independently versioned registration extension that does NOT
change the AdapterInput/AdapterOutput contract version.  The
``dependency_manifest_version`` field is separate from ``contract_version``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, ConfigDict, StrictStr, model_validator


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
    input_contract_version: int | None = Field(None, description="Input-v2 adapters must acknowledge integer 2; absent for legacy input v1")
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
    session_status: Literal["fresh", "resumed", "not_found"] | None = Field(
        None,
        description=(
            "Optional provider-session outcome: fresh means a new session was "
            "created, resumed means the supplied session continued, and "
            "not_found means the supplied session did not exist. Omission is "
            "valid only when AdapterInput.session was omitted."
        ),
    )
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


class AdapterInputOptions(BaseModel):
    """Explicit input-v2 opt-in. Output contract remains v1. Literal argv templates."""
    model_config = ConfigDict(extra="forbid")
    version: Annotated[Literal[2], BeforeValidator(_strict_int_for_manifest)]
    model_arg_template: list[StrictStr] | None = None
    effort_arg_template: list[StrictStr] | None = None
    reasoning_effort_values: list[StrictStr] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_templates(self):
        import re
        for template, placeholder in ((self.model_arg_template, "model"), (self.effort_arg_template, "effort")):
            if template is not None:
                if not template or len(template) > 16 or any(not p or len(p) > 512 for p in template):
                    raise ValueError("Arg template must contain 1..16 nonempty literal argv elements")
                fields = [field for p in template for field in re.findall(r"\{([^{}]*)\}", p)]
                if fields != [placeholder] or any("{" in p.replace("{" + placeholder + "}", "") or "}" in p.replace("{" + placeholder + "}", "") for p in template):
                    raise ValueError(f"Arg template requires exactly one {{{placeholder}}} and no other placeholders")
        values = self.reasoning_effort_values
        if len(values) != len(set(values)) or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v) for v in values):
            raise ValueError("Effort values must be unique bounded lowercase identifiers")
        if bool(values) != bool(self.effort_arg_template):
            raise ValueError("Effort template and allowed values must be declared together")
        return self


class EffortInfo(BaseModel):
    reasoning_effort: StrictStr
    effort_arg_template: list[str]


class AdapterInputV2(AdapterInput):
    contract_version: Literal[2] = 2
    reasoning_effort: EffortInfo | None = None


def opt_in_adapter_input(
    legacy: AdapterInput, options: dict | None, model: str | None, effort: str | None,
) -> AdapterInput:
    if options is None:
        if effort is not None:
            raise ValueError("This custom adapter has not opted into reasoning_effort")
        return legacy
    declaration = AdapterInputOptions.model_validate(options)
    if effort is not None and effort not in declaration.reasoning_effort_values:
        raise ValueError("Unsupported custom adapter reasoning_effort")
    data = legacy.model_dump()
    data["contract_version"] = 2
    data["model"] = ModelInfo(model_id=model, model_arg_template=declaration.model_arg_template) if model is not None else None
    data["reasoning_effort"] = EffortInfo(reasoning_effort=effort, effort_arg_template=declaration.effort_arg_template) if effort is not None else None
    return AdapterInputV2.model_validate(data)
