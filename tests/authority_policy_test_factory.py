import hashlib
import json

from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


def activate_test_policy(db, *, version: int = 1, epoch: int = 1):
    values = {
        "team": "engineering",
        "policy_id": "engineering/pre-escalation-authority",
        "version": version,
        "title": f"Policy v{version}",
        "normative_text": "Escalate protected work.",
        "clauses_json": '[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]',
        "continuation_phrase": "routine same-root follow-through of the already-completed slice",
        "actor_kind": "shared_local_operator_credential",
    }
    payload = json.dumps({
        "clauses": json.loads(values["clauses_json"]),
        "continuation_phrase": values["continuation_phrase"],
        "normative_text": values["normative_text"],
        "policy_id": values["policy_id"], "team": values["team"],
        "title": values["title"], "version": version,
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    release = AuthorityPolicyRelease(
        **values, id=f"APR-{digest}", canonical_payload_json=payload,
        policy_digest=digest,
    )
    store = AuthorityPolicyStore(db)
    release = store.create_release(release)
    activation = store.activate(AuthorityPolicyActivation.create(
        id=f"APA-{epoch}", team="engineering", epoch=epoch,
        release_id=release.id, action="bootstrap" if epoch == 1 else "activate",
        actor_kind="shared_local_operator_credential",
        request_id=f"REQ-{epoch}", request_digest=str(epoch) * 64,
    ))
    return release, activation
