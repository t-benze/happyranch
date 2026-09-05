#!/usr/bin/env python3
"""Generate S7 evidence by executing shipping authority/recovery seams."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

CASES = {
 "positive":"tests/test_orchestrator.py::test_manager_policy_shipping_seam_consumes_authenticated_self_evaluation",
 "absent":"tests/daemon/test_startup_recovery.py::test_sweep_orphaned_result_invalid_evidence_fails_closed[absent]",
 "malformed":"tests/daemon/test_startup_recovery.py::test_sweep_orphaned_result_invalid_evidence_fails_closed[malformed]",
 "extra_field":"tests/test_authority_hook.py::test_fail_closed_escalates_on_wrong_action",
 "stale":"tests/test_authority_hook.py::test_cancellation_during_evaluation_wins",
 "mismatched":"tests/daemon/test_startup_recovery.py::test_sweep_orphaned_result_invalid_evidence_fails_closed[mismatch]",
 "replay":"tests/test_authority_hook.py::test_replayed_consume_completion_report_exactly_once",
 "ambiguous":"tests/test_authority_hook.py::test_adversarial_reason_omitted_empty_fails_closed",
 "low_confidence":"tests/test_authority_hook.py::test_fail_closed_escalates_on_low_confidence_continue",
 "cancellation":"tests/test_authority_hook.py::test_cancellation_at_final_cas_wins",
 "budget":"tests/test_authority_hook.py::test_server_fence_budget_exhausted_benign_reason_escalates",
 "protected_fence":"tests/test_authority_hook.py::test_server_gate_permission_surface_change_during_attempt_escalates",
 "mechanical_fence":"tests/test_authority_hook.py::test_consumption_recheck_race_fails_closed[active_work]",
 "startup_recovery":"tests/daemon/test_startup_recovery.py::test_sweep_orphaned_result_replay_cannot_continue_twice",
 "zombie_recovery":"tests/test_zombie_reaper.py::test_consume_zombie_fingerprint_replay_cannot_continue_twice",
}
SOURCES=("runtime/orchestrator/active_authority_policy.py","runtime/orchestrator/authority.py","runtime/orchestrator/run_step.py","runtime/daemon/__main__.py","runtime/daemon/zombie_reaper.py")
def canonical(v): return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def file_digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
 root=Path(__file__).resolve().parents[1]; sources={n:file_digest(root/n) for n in SOURCES}; receipts=[]
 for case,nodeid in CASES.items():
  done=subprocess.run([sys.executable,"-m","pytest","-q",nodeid],cwd=root,text=True,capture_output=True)
  normalized="\n".join(x for x in done.stdout.splitlines() if " passed in " not in x and " failed in " not in x)
  result={"exit_code":done.returncode,"pytest_status":"passed" if done.returncode==0 else "failed","stdout_digest":hashlib.sha256(normalized.encode()).hexdigest(),"stderr_digest":hashlib.sha256(done.stderr.encode()).hexdigest()}
  receipt={"case":case,"nodeid":nodeid,"test_source_digest":file_digest(root/nodeid.split("::",1)[0]),"shipping_source_digest":hashlib.sha256(canonical(sources)).hexdigest(),"result":result}
  receipt["receipt_digest"]=hashlib.sha256(canonical(receipt)).hexdigest(); receipts.append(receipt)
  if done.returncode: sys.stderr.write(done.stdout+done.stderr); return done.returncode
 artifact={"proof_version":2,"contract_id":"engineering-manager-self-evaluation-readiness","contract_version":"s7-v3","shipping_sources":sources,"generator":{"path":"scripts/generate_authority_readiness_evidence.py","digest":file_digest(Path(__file__).resolve()),"observed_at":datetime.now(timezone.utc).isoformat()},"receipts":receipts}
 artifact["artifact_digest"]=hashlib.sha256(canonical(artifact)).hexdigest(); a.output.write_text(json.dumps(artifact,indent=2)+"\n"); return 0
if __name__=="__main__": raise SystemExit(main())
