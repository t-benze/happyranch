"""Semantic reader for the merged unit-A normative contract fixtures.

Merge unit B (THR-097, TASK-5792). The harness consumes the normative contract
*by reading the fixtures* (read-only) at runtime:

- deny/audit category taxonomies and the existence-guard rule
  (``failure-categories.json``);
- the full hostile/positive threat matrix (``threat-cases.json``);
- the seven credential classes (``credential-taxonomy.json``);
- the route-policy decision order and allow-list semantics
  (``route-policy.json``).

Expected deny/audit categories are never hard-coded here: they come from the
fixtures. The harness maps each threat case to an executable probe recipe
(``probes.py``) and asserts the *live* observation against the fixture's
expected outcome/categories. Fixtures are never written; digest provenance is
reported in every run summary so contract drift is detectable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CONTRACT_FILES = (
    "route-policy.json",
    "credential-taxonomy.json",
    "failure-categories.json",
    "threat-cases.json",
)


class ContractLoadError(RuntimeError):
    """Raised when a normative fixture is missing or unreadable."""


class Contract:
    """Read-only, semantic view over the normative managed-remote-access contract."""

    def __init__(self, contract_dir: Path) -> None:
        self.contract_dir = Path(contract_dir)
        self._docs: dict[str, dict] = {}
        for name in CONTRACT_FILES:
            path = self.contract_dir / name
            if not path.is_file():
                raise ContractLoadError(f"missing normative fixture: {name}")
            try:
                self._docs[name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ContractLoadError(f"unreadable normative fixture {name}: {exc}") from exc
        self._validate_top_level()

    # -- provenance ---------------------------------------------------------

    def fixture_digests(self) -> dict[str, str]:
        """sha256 digests of the four normative fixtures (mutation detection)."""
        out: dict[str, str] = {}
        for name in CONTRACT_FILES:
            raw = (self.contract_dir / name).read_bytes()
            out[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
        return out

    # -- top-level ----------------------------------------------------------

    @property
    def version(self) -> int:
        return int(self._docs["threat-cases.json"].get("version", 0))

    @property
    def status(self) -> str:
        return str(self._docs["threat-cases.json"].get("status", ""))

    # -- failure/audit taxonomy --------------------------------------------

    @property
    def deny_categories(self) -> set[str]:
        return {c["id"] for c in self._docs["failure-categories.json"]["deny_categories"]}

    @property
    def audit_categories(self) -> set[str]:
        return {c["id"] for c in self._docs["failure-categories.json"]["audit_categories"]}

    @property
    def existence_guard(self) -> dict:
        return self._docs["failure-categories.json"]["existence_guard"]

    # -- threat matrix ------------------------------------------------------

    @property
    def threat_cases(self) -> list[dict]:
        return list(self._docs["threat-cases.json"]["cases"])

    def hostile_cases(self) -> list[dict]:
        return [c for c in self.threat_cases if c["class"] == "hostile"]

    def positive_cases(self) -> list[dict]:
        return [c for c in self.threat_cases if c["class"] == "positive_control"]

    def cases_by_category(self, category: str) -> list[dict]:
        return [c for c in self.threat_cases if c["category"] == category]

    def existence_pairs(self) -> dict[str, list[dict]]:
        pairs: dict[str, list[dict]] = {}
        for case in self.threat_cases:
            pair = case.get("existence_pair")
            if pair:
                pairs.setdefault(pair, []).append(case)
        return pairs

    # -- credential taxonomy / route policy ---------------------------------

    @property
    def credential_classes(self) -> list[dict]:
        return list(self._docs["credential-taxonomy.json"]["classes"])

    @property
    def decision_order(self) -> list[str]:
        return list(self._docs["route-policy.json"]["decision_order"])

    # -- validation ---------------------------------------------------------

    def _validate_top_level(self) -> None:
        for name in CONTRACT_FILES:
            doc = self._docs[name]
            if doc.get("status") != "normative-contract":
                raise ContractLoadError(f"{name}: status must be 'normative-contract'")
            if not doc.get("version"):
                raise ContractLoadError(f"{name}: version must be non-empty")
