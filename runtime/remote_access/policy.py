"""Versioned route-policy artifact consumer (contract §6.1 step 4, §14).

The policy artifact is the Unit-A route-policy fixture wrapped in an envelope
carrying schema version, issued-at time, freshness window, and a monotonic
revision. Structural drift (schema/version/unknown or missing keys, pinned
digest mismatch) fails closed at load; operational drift (empty/malformed/
compiler/apply state, revision rollback, staleness, future issuance) fails
closed at ``require_current``. Unknown routes and methods are denied by
default; the remote surface is the explicit allow-list only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from runtime.remote_access.allowlist import AllowEntry, AllowList, template_matches
from runtime.remote_access.audit import detail_for
from runtime.remote_access.models import DeniedOutcome

SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_ARTIFACT_VERSION = 1
MAX_CLOCK_SKEW_SECONDS = 300

# Locked Unit-A security schema: the consumer trusts the complete normative
# contract. Any drift in the locked nine-step decision order, the default
# behavior, nested security-relevant controls, the artifact status, or the
# operational state fails closed at load (policy_malformed) — reversed,
# missing, or duplicated order, flipped controls, and unknown states are
# never treated as active.
LOCKED_DECISION_ORDER: tuple[str, ...] = (
    "authenticate",
    "bind",
    "proof",
    "policy",
    "normalize",
    "allowlist",
    "strip",
    "bearer",
    "redact",
)
LOCKED_DEFAULT_BEHAVIOR = "deny_unclassified"
LOCKED_ARTIFACT_STATUS = "normative-contract"
LOCKED_BEARER_INJECTION_HOP = "connector_to_127.0.0.1_only"
LOCKED_NORMALIZATION_KEYS = frozenset(
    {
        "normalize_once",
        "percent_encoding",
        "dot_segments",
        "duplicate_slashes",
        "query_separation",
        "unicode_control_bytes",
        "absolute_form_authority",
        "ambiguity_denied",
    }
)
LOCKED_HEADER_STRIPPING_KEYS = frozenset(
    {
        "strip_authorization",
        "strip_forwarding",
        "strip_hop_by_hop",
        "strip_cookies",
        "strip_host",
        "daemon_bearer_injection_hop",
        "inject_only_on_loopback",
        "reject_duplicate_critical_headers",
        "reject_smuggling",
    }
)
LOCKED_UPGRADE_KEYS = frozenset(
    {
        "allowed",
        "sse",
        "websocket",
        "unsupported_upgrades_denied",
        "unsupported_bodies_denied",
    }
)
LOCKED_FORBIDDEN_CLASS_IDS = frozenset(
    {
        "auth_bootstrap_registration",
        "agent_callbacks",
        "management",
        "founder_as_founder",
        "memory_learning_writes",
        "artifact_upload_agent_only",
        "adapter_administration",
        "executor_registration",
        "portability_reconciliation",
        "unclassified_default_deny",
    }
)
KNOWN_POLICY_STATES = frozenset(
    {"active", "empty", "malformed", "compiler_failed", "apply_failed"}
)

# Flags that must be enabled for the Unit-A security contract to hold.
_LOCKED_ENABLED_FLAGS = frozenset(
    {
        "normalize_once",
        "ambiguity_denied",
        "strip_authorization",
        "strip_forwarding",
        "strip_hop_by_hop",
        "strip_cookies",
        "strip_host",
        "inject_only_on_loopback",
        "reject_duplicate_critical_headers",
        "reject_smuggling",
        "unsupported_upgrades_denied",
        "unsupported_bodies_denied",
    }
)

_REQUIRED_ARTIFACT_KEYS = frozenset(
    {
        "version",
        "name",
        "status",
        "description",
        "decision_order",
        "default_behavior",
        "normalization",
        "header_stripping",
        "upgrade_semantics",
        "forbidden_classes",
        "allow",
    }
)


class PolicyEnvelope(BaseModel):
    """The operational wrapper around a route-policy artifact."""

    schema_version: int
    artifact: Any = Field(default=None)  # validated structurally in from_envelope
    artifact_version: int
    issued_at: datetime
    max_age_seconds: int = 3600
    revision: int = 1
    state: str = "active"


class PolicyError(Exception):
    """Carries the fail-closed denial produced by the policy check."""

    def __init__(self, outcome: DeniedOutcome) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome


def canonical_json(payload: dict) -> bytes:
    """Deterministic canonical serialization for digest pinning."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class RoutePolicyConsumer:
    """Validated, current-checked route policy."""

    # Mutation-test seam: forcing an allow-unclassified default behavior must
    # be detected by the invariant battery.
    DEFAULT_BEHAVIOR_OVERRIDE: str | None = None

    def __init__(
        self,
        *,
        artifact: dict,
        decision_order: tuple[str, ...],
        default_behavior: str,
        normalization: dict,
        header_stripping: dict,
        upgrade_semantics: dict,
        forbidden_classes: list[dict],
        allow: tuple[AllowEntry, ...],
        issued_at: datetime,
        max_age_seconds: int,
        revision: int,
        last_revision: int | None,
        state: str,
        digest: str,
    ) -> None:
        self.artifact = artifact
        self.decision_order = decision_order
        self._default_behavior = default_behavior
        self.normalization = normalization
        self.header_stripping = header_stripping
        self.upgrade_semantics = upgrade_semantics
        self.forbidden_classes = forbidden_classes
        self.allowlist = AllowList(allow)
        self.issued_at = issued_at
        self.max_age_seconds = max_age_seconds
        self.revision = revision
        self._last_revision = last_revision
        self._state = state
        self.digest = digest

    @classmethod
    def from_envelope(
        cls,
        envelope: PolicyEnvelope,
        *,
        pinned_digest: str | None = None,
        last_revision: int | None = None,
        now: datetime | None = None,
    ) -> "RoutePolicyConsumer":
        """Validate the artifact structurally and build the consumer.

        Structural failures (schema/version drift, unknown or missing keys,
        non-dict artifact, pinned-digest mismatch) raise ``PolicyError``
        fail-closed. Operational conditions are checked by
        ``require_current``.
        """
        del now  # reserved for future structural checks

        def malformed() -> PolicyError:
            return PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_malformed",
                    detail=detail_for("policy", "policy_malformed"),
                    reason="malformed",
                )
            )

        if envelope.schema_version != SUPPORTED_SCHEMA_VERSION:
            raise malformed()
        artifact = envelope.artifact
        if not isinstance(artifact, dict):
            raise malformed()
        unknown = set(artifact) - _REQUIRED_ARTIFACT_KEYS
        if unknown:
            raise malformed()
        missing = _REQUIRED_ARTIFACT_KEYS - set(artifact)
        if missing:
            raise malformed()
        if envelope.artifact_version != SUPPORTED_ARTIFACT_VERSION:
            raise malformed()

        # Locked security schema: the complete Unit-A contract is trusted only
        # when every security-relevant value matches the normative fixture.
        # Reversed/missing/duplicated decision order, altered defaults, flipped
        # nested controls, unknown states, and unknown/duplicate forbidden
        # classes all fail closed as policy_malformed — never treated as active.
        decision_order = tuple(str(x) for x in artifact["decision_order"])
        if decision_order != LOCKED_DECISION_ORDER:
            raise malformed()
        if str(artifact["default_behavior"]) != LOCKED_DEFAULT_BEHAVIOR:
            raise malformed()
        if str(artifact["status"]) != LOCKED_ARTIFACT_STATUS:
            raise malformed()
        if not isinstance(envelope.state, str) or envelope.state not in KNOWN_POLICY_STATES:
            raise malformed()
        normalization = artifact["normalization"]
        if not isinstance(normalization, dict) or set(normalization) != LOCKED_NORMALIZATION_KEYS:
            raise malformed()
        header_stripping = artifact["header_stripping"]
        if not isinstance(header_stripping, dict) or set(header_stripping) != LOCKED_HEADER_STRIPPING_KEYS:
            raise malformed()
        if str(header_stripping["daemon_bearer_injection_hop"]) != LOCKED_BEARER_INJECTION_HOP:
            raise malformed()
        upgrade_semantics = artifact["upgrade_semantics"]
        if not isinstance(upgrade_semantics, dict) or set(upgrade_semantics) != LOCKED_UPGRADE_KEYS:
            raise malformed()
        if not isinstance(upgrade_semantics.get("allowed"), list) or not upgrade_semantics["allowed"]:
            raise malformed()
        for section, flags in (
            (normalization, _LOCKED_ENABLED_FLAGS & LOCKED_NORMALIZATION_KEYS),
            (header_stripping, _LOCKED_ENABLED_FLAGS & LOCKED_HEADER_STRIPPING_KEYS),
            (upgrade_semantics, _LOCKED_ENABLED_FLAGS & LOCKED_UPGRADE_KEYS),
        ):
            for flag in flags:
                if section.get(flag) is not True:
                    raise malformed()
        # Prose semantics must be present (non-empty strings) — an emptied or
        # missing normative rule is drift, not documentation.
        for prose_key in LOCKED_NORMALIZATION_KEYS - _LOCKED_ENABLED_FLAGS:
            value = normalization.get(prose_key)
            if not isinstance(value, str) or not value.strip():
                raise malformed()
        if set(upgrade_semantics.get("allowed")) != {"sse", "websocket"}:
            raise malformed()
        for surface in ("sse", "websocket"):
            surface_cfg = upgrade_semantics.get(surface)
            if not isinstance(surface_cfg, dict) or not isinstance(
                surface_cfg.get("allowed_templates"), list
            ):
                raise malformed()
        forbidden_classes = artifact["forbidden_classes"]
        if not isinstance(forbidden_classes, list) or not forbidden_classes:
            raise malformed()
        class_ids = [str(c["id"]) if isinstance(c, dict) else "" for c in forbidden_classes]
        if set(class_ids) != LOCKED_FORBIDDEN_CLASS_IDS or len(class_ids) != len(set(class_ids)):
            raise malformed()

        digest = hashlib.sha256(canonical_json(artifact)).hexdigest()
        if pinned_digest is not None and digest != pinned_digest:
            raise malformed()

        default_behavior = str(artifact["default_behavior"])
        if cls.DEFAULT_BEHAVIOR_OVERRIDE is not None:
            default_behavior = cls.DEFAULT_BEHAVIOR_OVERRIDE

        allow = tuple(
            AllowEntry(str(e["method"]), str(e["path_template"]))
            for e in artifact["allow"]
        )

        return cls(
            artifact=artifact,
            decision_order=tuple(str(x) for x in artifact["decision_order"]),
            default_behavior=default_behavior,
            normalization=dict(artifact["normalization"]),
            header_stripping=dict(artifact["header_stripping"]),
            upgrade_semantics=dict(artifact["upgrade_semantics"]),
            forbidden_classes=list(artifact["forbidden_classes"]),
            allow=allow,
            issued_at=envelope.issued_at,
            max_age_seconds=envelope.max_age_seconds,
            revision=envelope.revision,
            last_revision=last_revision,
            state=envelope.state,
            digest=digest,
        )

    # ── runtime current-ness (fail closed) ───────────────────────────────

    def require_current(self, now: datetime) -> None:
        """Require present, well-formed, current policy/revocation state."""
        if not isinstance(self._state, str) or self._state not in KNOWN_POLICY_STATES:
            # Unknown operational states are never treated as active: an
            # unrecognized state fails closed as malformed.
            raise PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_malformed",
                    detail=detail_for("policy", "policy_malformed"),
                    reason="malformed",
                )
            )
        state_outcome = {
            "empty": ("policy", "policy_missing"),
            "malformed": ("policy", "policy_malformed"),
            "compiler_failed": ("policy", "policy_compile_failed"),
            "apply_failed": ("policy", "policy_apply_failed"),
        }.get(self._state)
        if state_outcome is not None:
            deny, audit = state_outcome
            raise PolicyError(
                DeniedOutcome(
                    deny_category=deny,
                    audit_category=audit,
                    detail=detail_for(deny, audit),
                    reason=self._state,
                )
            )
        if not self.allowlist_entries:
            raise PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_missing",
                    detail=detail_for("policy", "policy_missing"),
                    reason="empty",
                )
            )
        if self._last_revision is not None and self.revision < self._last_revision:
            raise PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_rollback",
                    detail=detail_for("policy", "policy_rollback"),
                    reason="rollback",
                )
            )
        if now > self.issued_at + __import__("datetime").timedelta(seconds=self.max_age_seconds):
            raise PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_stale",
                    detail=detail_for("policy", "policy_stale"),
                    reason="stale",
                )
            )
        if now < self.issued_at - __import__("datetime").timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
            raise PolicyError(
                DeniedOutcome(
                    deny_category="policy",
                    audit_category="policy_future",
                    detail=detail_for("policy", "policy_future"),
                    reason="future",
                )
            )

    # ── surface properties ───────────────────────────────────────────────

    @property
    def allowlist_entries(self) -> tuple[AllowEntry, ...]:
        return self.allowlist.entries

    @property
    def default_behavior(self) -> str:
        override = type(self).DEFAULT_BEHAVIOR_OVERRIDE
        if override is not None:
            return override
        return self._default_behavior

    @property
    def sse_allowed_templates(self) -> tuple[str, ...]:
        """SSE-allowed templates, normalized to path-only (the fixture encodes
        them as ``METHOD /path``)."""
        return self._paths_only(self.upgrade_semantics.get("sse", {}).get("allowed_templates", []))

    @property
    def websocket_allowed_templates(self) -> tuple[str, ...]:
        """WebSocket-allowed templates, normalized to path-only."""
        return self._paths_only(self.upgrade_semantics.get("websocket", {}).get("allowed_templates", []))

    @staticmethod
    def _paths_only(templates: list) -> tuple[str, ...]:
        paths: list[str] = []
        for template in templates:
            text = str(template)
            if " " in text:
                _, _, path = text.partition(" ")
            else:
                path = text
            paths.append(path)
        return tuple(paths)

    def is_forbidden(self, method: str, path: str) -> str | None:
        """Return the forbidden-class id when method+path matches a forbidden
        example, else None. Used to classify denials as route-denied vs
        unclassified-denied."""
        for cls in self.forbidden_classes:
            for example in cls.get("examples", []):
                if not isinstance(example, str):
                    continue
                if " " in example:
                    ex_method, _, template = example.partition(" ")
                else:
                    ex_method, template = "ANY", example
                if ex_method != "ANY" and ex_method != method:
                    continue
                if template_matches(path, template):
                    return str(cls["id"])
        return None
