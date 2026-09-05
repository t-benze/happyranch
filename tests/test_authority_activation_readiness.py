from __future__ import annotations
import copy, hashlib, json
from pathlib import Path
import pytest
import runtime.orchestrator.authority_activation_readiness as module
from runtime.orchestrator.authority_activation_readiness import MUST_ESCALATE_EXPECTED, NEGATIVE_CONTROLS, activation_readiness, verify_readiness_artifact
ROOT=Path(__file__).parents[1]; ARTIFACT=ROOT/"runtime/orchestrator/authority_activation_readiness_proof.json"
def artifact(): return json.loads(ARTIFACT.read_text())
def reseal(value):
 unsigned={k:v for k,v in value.items() if k!="artifact_digest"}; value["artifact_digest"]=hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def test_release_artifact_has_exact_executed_100_percent_recall():
 result=activation_readiness(); assert result["ready"] is True; assert result["must_escalate_observed"]==MUST_ESCALATE_EXPECTED==14; assert result["must_escalate_recall"]==1.0
 assert [r["case"] for r in artifact()["receipts"]]==["positive",*NEGATIVE_CONTROLS]
 assert all(r["result"]["exit_code"]==0 for r in artifact()["receipts"])
@pytest.mark.parametrize("mutation",[
 lambda v:v["receipts"].pop(), lambda v:v["receipts"][1].__setitem__("case","unknown"), lambda v:v["receipts"].__setitem__(2,v["receipts"][1]), lambda v:v["receipts"][1]["result"].__setitem__("exit_code",1), lambda v:v["receipts"][1].__setitem__("nodeid","tests/fabricated.py::test_ok"), lambda v:v["generator"].__setitem__("path","tests/test_orchestrator.py"), lambda v:v["generator"].__setitem__("observed_at","2020-01-01T00:00:00Z"), lambda v:v["shipping_sources"].pop("runtime/orchestrator/run_step.py")])
def test_readiness_fails_closed_for_falsified_receipts(mutation):
 value=copy.deepcopy(artifact()); mutation(value); reseal(value); assert verify_readiness_artifact(value,source_root=ROOT)["ready"] is False
def test_identity_reseal_stays_closed_even_when_release_pin_is_substituted(monkeypatch):
 value=copy.deepcopy(artifact()); value["contract_version"]="fabricated-release"; value["receipts"][0]["case"]="fabricated-positive"; reseal(value); monkeypatch.setattr(module,"PINNED_ARTIFACT_DIGEST",value["artifact_digest"]); assert verify_readiness_artifact(value,source_root=ROOT)["ready"] is False
def test_in_memory_release_pin_alone_cannot_admit_a_fabricated_identity(monkeypatch):
 value=copy.deepcopy(artifact()); value["receipts"][0]["nodeid"]="tests/test_orchestrator.py::fabricated_identity"; reseal(value); monkeypatch.setattr(module,"PINNED_ARTIFACT_DIGEST",value["artifact_digest"]); assert verify_readiness_artifact(value,source_root=ROOT)["ready"] is False
def test_readiness_fails_closed_for_bad_digest_absence_and_malformed(tmp_path):
 value=artifact(); value["artifact_digest"]="0"*64; assert verify_readiness_artifact(value,source_root=ROOT)["ready"] is False; assert activation_readiness(artifact_path=tmp_path/"missing.json")["ready"] is False; bad=tmp_path/"proof.json"; bad.write_text("not-json"); assert activation_readiness(artifact_path=bad)["ready"] is False
