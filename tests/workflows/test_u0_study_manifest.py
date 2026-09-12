from __future__ import annotations

import json
from pathlib import Path

from tests.workflows.u0_evidence_helpers import source_manifest, verify_detached_lock


def test_preregistered_three_arm_sources_have_separate_truth_projection_and_no_maker_lock() -> None:
    root = Path(__file__).parents[1] / "fixtures" / "workflow_u0"
    manifest = source_manifest([root / "clean_approval.json", root / "findings_revision.json", root / "reassignment.json"], source_pin="cb920574a810f19cceddf415747d3220361fcdb3")
    assert manifest["truth"] != manifest["projection"]
    protocol = json.loads(manifest["protocol"])
    assert protocol["arms"] == ["A", "B", "C"] and protocol["denominator"] == 18
    assert not verify_detached_lock(manifest, {"reviewer": "maker", "locked_manifest_sha256": manifest["raw_manifest_sha256"]})
    assert verify_detached_lock(manifest, {"reviewer": "code_reviewer", "locked_manifest_sha256": manifest["raw_manifest_sha256"]})
