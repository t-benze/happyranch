"""Release-controlled consumer for separately generated S7 readiness evidence."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONTRACT_ID="engineering-manager-self-evaluation-readiness"; CONTRACT_VERSION="s7-v3"; PROOF_VERSION=2
PINNED_ARTIFACT_DIGEST="964bb5a6bf89441a60e020eaee32565c75e4fd2720c0fc38293e8d0f18652c18"
NEGATIVE_CONTROLS=("absent","malformed","extra_field","stale","mismatched","replay","ambiguous","low_confidence","cancellation","budget","protected_fence","mechanical_fence","startup_recovery","zombie_recovery")
MUST_ESCALATE_EXPECTED=len(NEGATIVE_CONTROLS)
_SOURCE_FILES=("runtime/orchestrator/active_authority_policy.py","runtime/orchestrator/authority.py","runtime/orchestrator/run_step.py","runtime/daemon/__main__.py","runtime/daemon/zombie_reaper.py")
_GENERATOR="scripts/generate_authority_readiness_evidence.py"
_ARTIFACT=Path(__file__).with_name("authority_activation_readiness_proof.json")
def _canonical(v:Any)->bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
CONTRACT_DIGEST=hashlib.sha256(_canonical({"id":CONTRACT_ID,"version":CONTRACT_VERSION,"proof_version":PROOF_VERSION,"negative_controls":NEGATIVE_CONTROLS,"source_files":_SOURCE_FILES,"generator":_GENERATOR})).hexdigest()
def _closed(reason:str)->dict[str,Any]: return {"ready":False,"reason":reason,"contract_id":CONTRACT_ID,"contract_version":CONTRACT_VERSION,"contract_digest":CONTRACT_DIGEST,"must_escalate_observed":0,"must_escalate_expected":MUST_ESCALATE_EXPECTED,"must_escalate_recall":0.0}

def verify_readiness_artifact(artifact:object,*,source_root:Path)->dict[str,Any]:
 if not isinstance(artifact,dict) or set(artifact)!={"proof_version","contract_id","contract_version","shipping_sources","generator","receipts","artifact_digest"}: return _closed("readiness proof malformed")
 unsigned={k:v for k,v in artifact.items() if k!="artifact_digest"}; computed=hashlib.sha256(_canonical(unsigned)).hexdigest()
 if artifact.get("artifact_digest")!=computed: return _closed("readiness proof digest mismatch")
 if computed!=PINNED_ARTIFACT_DIGEST: return _closed("readiness proof is not the release-pinned artifact")
 if (artifact.get("proof_version"),artifact.get("contract_id"),artifact.get("contract_version"))!=(PROOF_VERSION,CONTRACT_ID,CONTRACT_VERSION): return _closed("readiness proof version mismatch")
 sources=artifact.get("shipping_sources")
 if not isinstance(sources,dict) or tuple(sources)!=_SOURCE_FILES: return _closed("readiness proof source set mismatch")
 for relative in _SOURCE_FILES:
  try:
   if sources[relative]!=hashlib.sha256((source_root/relative).read_bytes()).hexdigest(): return _closed("readiness proof stale for shipping implementation")
  except (KeyError,OSError,TypeError): return _closed("readiness proof source unavailable")
 generator=artifact.get("generator")
 if not isinstance(generator,dict) or set(generator)!={"path","digest","observed_at"}: return _closed("readiness proof provenance malformed")
 try: observed=datetime.fromisoformat(generator["observed_at"].replace("Z","+00:00")); generator_digest=hashlib.sha256((source_root/_GENERATOR).read_bytes()).hexdigest()
 except (AttributeError,OSError,TypeError,ValueError): return _closed("readiness proof provenance malformed")
 if observed.tzinfo is None or datetime.now(timezone.utc)-observed>timedelta(days=30): return _closed("readiness proof stale")
 if generator["path"]!=_GENERATOR or generator["digest"]!=generator_digest: return _closed("readiness proof generator mismatch")
 receipts=artifact.get("receipts"); expected=("positive",)+NEGATIVE_CONTROLS
 if not isinstance(receipts,list) or len(receipts)!=len(expected): return _closed("readiness proof receipt corpus incomplete")
 observed_cases=[]; source_set_digest=hashlib.sha256(_canonical(sources)).hexdigest()
 for receipt in receipts:
  if not isinstance(receipt,dict) or set(receipt)!={"case","nodeid","test_source_digest","shipping_source_digest","result","receipt_digest"}: return _closed("readiness proof receipt malformed")
  unsigned_receipt={k:v for k,v in receipt.items() if k!="receipt_digest"}
  if receipt["receipt_digest"]!=hashlib.sha256(_canonical(unsigned_receipt)).hexdigest(): return _closed("readiness proof receipt digest mismatch")
  nodeid=receipt.get("nodeid")
  try: test_digest=hashlib.sha256((source_root/nodeid.split("::",1)[0]).read_bytes()).hexdigest()
  except (AttributeError,OSError): return _closed("readiness proof receipt source unavailable")
  result=receipt.get("result")
  if receipt.get("test_source_digest")!=test_digest or receipt.get("shipping_source_digest")!=source_set_digest or not isinstance(result,dict) or set(result)!={"exit_code","pytest_status","stdout_digest","stderr_digest"} or result["exit_code"]!=0 or result["pytest_status"]!="passed" or not all(isinstance(result.get(k),str) and len(result[k])==64 for k in ("stdout_digest","stderr_digest")): return _closed("readiness proof receipt execution mismatch")
  observed_cases.append(receipt.get("case"))
 if tuple(observed_cases)!=expected or len(set(observed_cases))!=len(observed_cases): return _closed("readiness proof receipt corpus mismatch")
 return {"ready":True,"reason":"verified generated shipping-path readiness evidence","contract_id":CONTRACT_ID,"contract_version":CONTRACT_VERSION,"contract_digest":CONTRACT_DIGEST,"must_escalate_observed":MUST_ESCALATE_EXPECTED,"must_escalate_expected":MUST_ESCALATE_EXPECTED,"must_escalate_recall":1.0}

def activation_readiness(*,artifact_path:Path|None=None)->dict[str,Any]:
 try: artifact=json.loads((artifact_path or _ARTIFACT).read_text(encoding="utf-8"))
 except (OSError,UnicodeError,json.JSONDecodeError): return _closed("readiness proof absent or malformed")
 return verify_readiness_artifact(artifact,source_root=Path(__file__).parents[2])
