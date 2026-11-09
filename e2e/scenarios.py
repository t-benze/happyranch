"""Eight accepted cases, with every negative/positive variant attributable."""
from __future__ import annotations

import http.client
import json
import time
import uuid
from typing import TYPE_CHECKING

from browser import Browser
from oracles import require

if TYPE_CHECKING:
    from controller import Controller


def done(summary: str) -> dict:
    return {"action": "done", "summary": summary}


def protected(c: Controller) -> dict:
    return dict(stores={org: store.snapshot() for org, store in c.stores.items()}, starts=list(c.starts))


def unchanged(c: Controller, before: dict) -> None:
    require(protected(c) == before, "denied/replayed request changed protected state", protected(c))


def deny(c: Controller, variant: str, org: str, payload: dict, status: int, detail: dict) -> None:
    before = protected(c)
    with c.case(variant, lambda: unchanged(c, before)):
        record = c.callback(org, payload, status, detail)
        unchanged(c, before)
        c.mark(variant, record)



def compare_public(c: Controller, org: str, root: str, payloads: list[dict], starts: list[dict], child: str | None = None) -> None:
    store = c.stores[org]
    snapshot = store.finished(root, payloads, starts, child=child)
    public = c.get(f"/api/v1/orgs/{org}/tasks/{root}")
    recall = c.get(f"/api/v1/orgs/{org}/tasks/{root}/recall?tree=true")
    require(public["task"]["status"] == recall["status"] == "completed", "public terminal agreement", (public, recall))
    require(recall["output_summary"] == payloads[-1]["summary"], "public original final outcome", recall)
    require(len(recall["children"]) == (1 if child else 0), "public exact child count", recall)
    if child:
        require(recall["children"][0]["task_id"] == child and recall["children"][0]["status"] == "completed"
                and recall["children"][0]["output_summary"] == payloads[1]["summary"], "recursive public child outcome", recall)
    expected_results = [row for row in snapshot["results"] if row["task_id"] == root]
    require({row["id"] for row in public["results"]} == {row["id"] for row in expected_results}, "public/durable result IDs", public)
    c.durable.append(dict(org=org, root=root, snapshot=snapshot, public=public, recall=recall))


def collisions(c: Controller) -> tuple[str, str]:
    alpha = c.create("alpha", "ALPHA_COLLISION")
    a = c.start("alpha", alpha, "case_manager")
    beta = c.create("beta", "BETA_COLLISION")
    b = c.start("beta", beta, "case_manager")
    require(alpha == beta and a["session"] != b["session"], "genuine org-local collision with distinct sessions", (a, b))
    for variant, target, own, foreign in (
        ("E03.cross-alpha-beta", "beta", b, a),
        ("E03.cross-beta-alpha", "alpha", a, b),
    ):
        payload = c.payload(own, "CROSSED_ATTACK", done("CROSSED_ATTACK"))
        payload["session_id"] = foreign["session"]
        deny(c, variant, target, payload, 409,
             dict(code="session_mismatch", active=own["session"], got=foreign["session"]))
    # Complete serially so protected-state comparisons never race the other org.
    for org, start, sentinel in (("alpha", a, "ALPHA_COLLISION_OUTCOME"), ("beta", b, "BETA_COLLISION_OUTCOME")):
        payload = c.payload(start, sentinel, done(sentinel))
        record = c.callback(org, payload)
        c.stores[org].result(payload)
        c.release(start)
        c.finish(org, start["task"])
        compare_public(c, org, start["task"], [payload], [start])
        c.mark(f"E03.genuine-{org}", record)
    return alpha, beta


def bearer(c: Controller, task: str) -> None:
    for method, name in (("GET", "get"), ("POST", "post")):
        for token, label, detail in ((None, "absent", "missing bearer token"), ("definitely-wrong", "wrong", "bad token")):
            path = f"/api/v1/orgs/alpha/tasks" + (f"/{task}" if method == "GET" else "")
            before = protected(c)
            with c.case(f"E03.bearer-{name}-{label}", lambda: unchanged(c, before)):
                status, response = c.api(method, path, {"team": "engineering", "brief": "DENIED_NEW_TASK"} if method == "POST" else None, token)
                require((status, response) == (401, {"detail": detail}), "exact bearer denial", (status, response))
                unchanged(c, before)
                c.mark(f"E03.bearer-{name}-{label}", dict(status=status, response=response))
    response = c.get(f"/api/v1/orgs/alpha/tasks/{task}")
    require(response["task"]["task_id"] == task, "legitimate GET identity", response)
    c.mark("E03.bearer-get-valid")


def identity_denials(c: Controller, m1: dict, m2: dict) -> None:
    for label, session in (("stale", m1["session"]), ("fabricated", str(uuid.uuid4()))):
        payload = c.payload(m2, "IDENTITY_ATTACK", done("IDENTITY_ATTACK"))
        payload["session_id"] = session
        deny(c, f"E03.{label}", "alpha", payload, 409,
             dict(code="session_mismatch", active=m2["session"], got=session))
    payload = c.payload(m2, "NO_SLOT_ATTACK")
    payload.update(agent="code_reviewer", session_id=str(uuid.uuid4()))
    deny(c, "E03.no-slot", "alpha", payload, 409,
         dict(code="unknown_session", task_id=m2["task"], agent="code_reviewer"))
    payload = c.payload(m2, "UNKNOWN_TASK_ATTACK", done("UNKNOWN_TASK_ATTACK"))
    payload["task_id"] = "TASK-DOES-NOT-EXIST-" + uuid.uuid4().hex
    deny(c, "E03.unknown-task", "alpha", payload, 404,
         dict(code="unknown_task", task_id=payload["task_id"]))


def journey(c: Controller, *, lost: bool) -> None:
    prefix = "LOSS" if lost else "ALPHA"
    task = c.create("alpha", prefix + "_ROOT")
    m1 = c.start("alpha", task, "case_manager")
    if not lost:
        roots_expected = dict(org="alpha", task=dict(task_id=task, brief="ALPHA_ROOT", team="engineering",
            status="in_progress", parent_task_id=None, assigned_agent="case_manager", block_kind=None,
            current_session_id=m1["session"], note=None), severity_rollup="in_progress",
            forbidden=["ALPHA_ROOT_SENTINEL", "ALPHA_CHILD_SENTINEL", "ALPHA_CHILD_OUTCOME"])
        c.browser.open_task("alpha", task, "ALPHA_ROOT", roots_expected=roots_expected, expected=dict(
            task=dict(task_id=task, brief="ALPHA_ROOT", team="engineering", status="in_progress",
                      parent_task_id=None, assigned_agent="case_manager", block_kind=None,
                      current_session_id=m1["session"]),
            absent=["ALPHA_ROOT_SENTINEL", "ALPHA_CHILD_SENTINEL", "ALPHA_CHILD_OUTCOME"]))
    delegate = {"action": "delegate", "agent": "case_worker", "prompt": prefix + "_CHILD_SENTINEL"}
    first = c.payload(m1, prefix + "_DELEGATE_RESULT", delegate)
    if lost:
        c.relay.armed = (f"/api/v1/orgs/alpha/tasks/{task}/completion", m1["session"])
    first_record = c.callback("alpha", first, lost=lost)
    original = c.stores["alpha"].result(first)
    before = protected(c)
    replay = c.callback("alpha", first)
    unchanged(c, before)
    require(replay["body_sha256"] == first_record["body_sha256"], "literal CLI request bytes on replay", replay)
    require(c.stores["alpha"].result(first) == original, "literal replay preserves exact original row")
    require(len(c.stores["alpha"].snapshot(task)["tasks"]) == 1, "held provider has no consumed delegation")
    if not lost:
        c.mark("E06.literal-active", replay)
        changed = dict(first, summary="MUTATED_SUMMARY", decision=done("MUTATED_DECISION"))
        changed_record = c.callback("alpha", changed)
        unchanged(c, before)
        require(c.stores["alpha"].result(first) == original, "changed replay retains first result")
        c.mark("E06.changed-active", changed_record)
        bearer(c, task)
    c.release(m1)
    worker = c.start("alpha", None, "case_worker")
    require(worker["task"] != task, "real delegated child")
    child = c.get(f"/api/v1/orgs/alpha/tasks/{worker['task']}")["task"]
    require(child["parent_task_id"] == task and child["brief"] == delegate["prompt"], "delegated child public linkage", child)
    worker_payload = c.payload(worker, prefix + "_CHILD_OUTCOME")
    c.callback("alpha", worker_payload)
    c.stores["alpha"].result(worker_payload)
    c.release(worker)
    m2 = c.start("alpha", task, "case_manager")
    require(m2["session"] != m1["session"], "natural manager generation changed")
    if not lost:
        identity_denials(c, m1, m2)
    final = c.payload(m2, prefix + "_ROOT_SENTINEL", done(prefix + "_ROOT_SENTINEL"))
    c.callback("alpha", final)
    c.stores["alpha"].result(final)
    c.release(m2)
    c.finish("alpha", task)
    require(c.stores["alpha"].result(first) == original, "original M1 row retained through terminal consumption")
    compare_public(c, "alpha", task, [first, worker_payload, final], [m1, worker, m2], worker["task"])
    closure = c.durable[-1]
    expected = dict(task={key: closure["public"]["task"][key] for key in (
        "task_id", "brief", "team", "status", "parent_task_id", "assigned_agent", "block_kind", "current_session_id", "note")},
        recall=closure["recall"], result_ids=[row["id"] for row in closure["public"]["results"]],
        detail=dict(blocked_on_jobs=None))
    if not lost:
        c.browser.completed("alpha", task, "ALPHA_ROOT", "ALPHA_ROOT_SENTINEL", child=worker["task"],
                            child_summary="ALPHA_CHILD_OUTCOME", shot="lifecycle", refresh=True, expected=expected)
        c.mark("E02.lifecycle-browser")
        terminal = dict(code="task_not_active", task_id=task, status="completed", cancelled=False)
        deny(c, "E06.literal-terminal", "alpha", final, 409, terminal)
        deny(c, "E06.changed-terminal", "alpha", changed, 409, terminal)
    else:
        roots_expected = dict(org="alpha", task=dict(task_id=task, brief="LOSS_ROOT", team="engineering",
            status="completed", parent_task_id=None, assigned_agent="case_manager", block_kind=None,
            current_session_id=m2["session"], note="LOSS_ROOT_SENTINEL"), severity_rollup="completed")
        c.browser.open_task("alpha", task, "LOSS_ROOT", expected=expected, roots_expected=roots_expected, ready=lambda:
            c.browser.completed("alpha", task, "LOSS_ROOT", "LOSS_ROOT_SENTINEL", child=worker["task"],
                                child_summary="LOSS_CHILD_OUTCOME", shot="response-loss"))
        c.mark("E06.committed-response-lost", dict(first=first_record, replay=replay))


def exercise(c: Controller) -> None:
    alpha, beta = collisions(c)  # First task in each org; no counter seeding.
    c.browser = Browser(c)
    journey(c, lost=False)
    journey(c, lost=True)
    control = c.create("alpha", "BEARER_POSITIVE_CONTROL")
    start = c.start("alpha", control, "case_manager")
    payload = c.payload(start, "BEARER_POSITIVE_OUTCOME", done("BEARER_POSITIVE_OUTCOME"))
    c.callback("alpha", payload)
    c.release(start)
    c.finish("alpha", control)
    compare_public(c, "alpha", control, [payload], [start])
    c.mark("E03.bearer-post-valid")
    for index, (org, task) in enumerate((("alpha", alpha), ("beta", beta), ("alpha", alpha))):
        alien = ("BETA" if org == "alpha" else "ALPHA") + "_COLLISION"
        closure = next(row for row in c.durable if row.get("org") == org and row.get("root") == task
                       and "public" in row)
        expected = dict(task={key: closure["public"]["task"][key] for key in (
            "task_id", "brief", "team", "status", "parent_task_id", "assigned_agent", "block_kind", "current_session_id", "note")},
            recall=closure["recall"], result_ids=[r["id"] for r in closure["public"]["results"]], forbidden=[alien])
        own_start = next(row for row in c.starts if row["org"] == org and row["task"] == task)
        roots_expected = dict(org=org, task=dict(task_id=task, brief=org.upper()+"_COLLISION", team="engineering",
            status="completed", parent_task_id=None, assigned_agent="case_manager", block_kind=None,
            current_session_id=own_start["session"], note=org.upper()+"_COLLISION_OUTCOME"),
            severity_rollup="completed", forbidden=[alien])
        c.browser.open_task(org, task, org.upper() + "_COLLISION", roots_expected=roots_expected, switch=index > 0,
                            cached_recall=index == 2, alien=alien, expected=expected, ready=lambda:
            c.browser.completed(org, task, org.upper() + "_COLLISION", org.upper() + "_COLLISION_OUTCOME",
                                alien=alien, shot=f"org-switch-{index}"))
        if index == 2:
            # First prove the populated-cache return, then also preserve the
            # accepted fresh target-org HTTP + rendered-content oracle.
            c.browser.revisit_after_cache_expiry(org, task, expected=expected, roots_expected=roots_expected,
                                                  alien=alien, ready=lambda:
                c.browser.completed(org, task, "ALPHA_COLLISION", "ALPHA_COLLISION_OUTCOME",
                                    alien=alien, shot="org-switch-return-revalidated"))
        c.mark(("E03.browser-alpha", "E03.browser-beta", "E03.browser-alpha-return")[index])
    require(len(c.starts) == 9, "exact whole-run launch count", c.starts)
    for org, tasks, results in (("alpha", 6, 8), ("beta", 1, 1)):
        snapshot = c.stores[org].snapshot()
        require(len(snapshot["tasks"]) == tasks and len(snapshot["results"]) == results,
                "exact whole-org closure, no extra children or reports", snapshot)
        c.durable.append(dict(org=org, final_snapshot=snapshot))
    rows = [json.loads(line) for line in (c.root / "witness" / "identities.jsonl").read_text().splitlines()]
    stubs = [row for row in rows if row["kind"] == "stub"]
    require(len(stubs) == 9 and all(row["provider"] == "claude" and row["source_sha"] == c.revision for row in stubs),
            "actual registered executable witnesses", stubs)
    binding = json.loads((c.root / "coord/manifest.json").read_text())
    require(all(row["stub_sha256"] == binding["stubs"]["claude"]["sha256"]
                and row["plan_sha256"] == (c.root / "coord/plan.sh.sha256").read_text()
                and "-p" in row["flags"] for row in stubs), "exact source/plan/flags witness", stubs)
    from controller import sha
    calls = [row for row in rows if row["kind"] == "callback"]
    require(len(calls) == len(c.commands) and all(row["source_sha"] == c.revision
                and row["cli_sha256"] == sha(c.source / "cli/main.py") for row in calls),
            "actual source-bound CLI witnesses", calls)
    require(any(s["pgid"] != c.daemon.pid for s in c.starts), "cleanup covers providers beyond daemon PGID", c.starts)
    metrics = c.poll(lambda: (m if (m := c.get("/api/v1/metrics"))["host_sessions"]["admission"]["active"] == 0 else None),
                     "host sessions quiescent")
    host = metrics["host_sessions"]
    require(host["admission"]["queue_depth"] == 0 and not host["residue"]["admission_blocked"], "native admission/residue clear", host)
    recent = host["receipts"]["recent"]
    require(len(recent) == 9 and all(row["quiescent"] and row["survivors_count"] == 0 for row in recent),
            "real production session cleanup receipts", host)
    c.durable.append(dict(final_metrics=metrics, launch_witnesses=rows))


def knowledge_read(c: Controller, path: str, bearer: str | None = "valid") -> dict:
    """Observe complete HTTP bytes, including binary artifact responses."""
    headers = {} if bearer is None else {"Authorization": "Bearer " + (c.token if bearer == "valid" else bearer)}
    with c.bounded(c.effective_end()):
        connection = http.client.HTTPConnection("127.0.0.1", c.port, timeout=c.remaining())
        try:
            connection.request("GET", path, headers=headers)
            upstream = connection.sock
            response = connection.getresponse()
            upstream.settimeout(c.remaining())
            raw = response.read()
            c.remaining()
            return dict(path=path, status=response.status, bytes=len(raw), body_hex=raw.hex())
        finally:
            connection.close()


def knowledge(c: Controller) -> None:
    """J1 appends only after the complete original E07 execution prefix."""
    c.j1_prefix = c.observations()
    # E05's JSON-only request capture has finished. Artifact upload is multipart;
    # retain the ordinary relay response/digest witness for this suffix.
    c.relay.capture_e05 = False
    record = c.j1 = dict(entered=time.monotonic(), finished=None, api={}, denials={}, denial_state={})
    slug, name = "j1-agent-knowledge", "j1-artifact.txt"
    body = "J1 knowledge from the real agent CLI.\n\nExact content: alpha → founder; 中文 café.\n"
    content = "J1 artifact from the real agent CLI.\nExact bytes: alpha → founder; 中文 café.\n".encode()
    task = c.create("alpha", "J1 publish knowledge and artifact")
    start = record["start"] = c.start("alpha", task, "case_manager")
    for action in ("j1-kb-add", "j1-artifacts-put"):
        c.provider_cli(start, action)
    summary = "J1 knowledge and artifact published"
    final = c.provider_cli(start, "j1-final", payload=dict(status="completed", confidence=90,
        summary=summary, decision=done(summary)))
    c.stores["alpha"].result(final["payload"])
    c.release(start)
    c.finish("alpha", task)
    record["completion"] = c.stores["alpha"].snapshot(task)
    c.mark("J1.agent-cli-publish")

    kb_path, artifact_path = f"/api/v1/orgs/alpha/kb/{slug}", f"/api/v1/orgs/alpha/artifacts/{name}"
    api = record["api"]
    api["kb"] = c.get(kb_path)
    api["artifacts"] = c.get("/api/v1/orgs/alpha/artifacts")
    api["artifact"] = knowledge_read(c, artifact_path)
    require({k: api["kb"][k] for k in ("slug", "title", "body", "authored_by")} == dict(
        slug=slug, title="J1 agent knowledge", body=body, authored_by=start["agent"]), "J1 exact API KB content")
    info, = [a for a in api["artifacts"]["artifacts"] if a["name"] == name]
    require(info["size_bytes"] == len(content) and info["modified_at"]
            and api["artifact"]["status"] == 200 and bytes.fromhex(api["artifact"]["body_hex"]) == content,
            "J1 exact API artifact metadata and bytes")
    c.mark("J1.api-content")

    record["browser_kb"] = c.browser.knowledge_entry(slug)
    require(record["browser_kb"] == dict(slug=slug, title="J1 agent knowledge", paragraphs=[
        "J1 knowledge from the real agent CLI.", "Exact content: alpha → founder; 中文 café."]), "J1 exact rendered KB")
    c.mark("J1.browser-kb")
    record["browser_artifact"] = c.browser.knowledge_artifact(name, info["modified_at"])
    browser = record["browser_artifact"]
    require(browser["name"] == browser["filename"] == name and browser["size"] == f"{len(content)} B"
            and browser["modified"].startswith("Modified ") and "unavailable" not in browser["modified"].lower()
            and bytes.fromhex(browser["body_hex"]) == content, "J1 page Download exact bytes and metadata", browser)
    c.mark("J1.browser-artifact-download")

    def protected_knowledge() -> dict:
        return dict(stores={org: store.knowledge() for org, store in c.stores.items()}, starts=list(c.starts))

    paths = ["/api/v1/orgs/alpha/kb", kb_path, "/api/v1/orgs/alpha/artifacts", artifact_path]
    for token, label, detail in ((None, "absent", "missing bearer token"), ("j1-wrong-bearer", "wrong", "bad token")):
        before = protected_knowledge()
        def unchanged_knowledge() -> None:
            require(protected_knowledge() == before, "J1 refused read leaves both orgs unchanged")
        with c.case("J1.bearer-" + label, unchanged_knowledge):
            reads = record["denials"][label] = [knowledge_read(c, path, token) for path in paths]
            require(all(r["status"] == 401 and json.loads(bytes.fromhex(r["body_hex"])) == {"detail": detail}
                        for r in reads), "J1 exact KB/artifact bearer refusals", reads)
            unchanged_knowledge()
            record["denial_state"][label] = dict(before=before, after=protected_knowledge())
            c.mark("J1.bearer-" + label)

    before = protected_knowledge()
    beta = record["beta"] = dict(kb=c.get("/api/v1/orgs/beta/kb"), artifacts=c.get("/api/v1/orgs/beta/artifacts"),
        entry=knowledge_read(c, f"/api/v1/orgs/beta/kb/{slug}"), artifact=knowledge_read(c, f"/api/v1/orgs/beta/artifacts/{name}"))
    require(all(e["slug"] != slug for e in beta["kb"]["entries"])
            and all(a["name"] != name for a in beta["artifacts"]["artifacts"]), "J1 no alpha content in beta lists")
    for key, detail in (("entry", dict(code="not_found", slug=slug)), ("artifact", dict(code="artifact_not_found", name=name))):
        require(beta[key]["status"] == 404 and json.loads(bytes.fromhex(beta[key]["body_hex"])) == {"detail": detail},
                "J1 exact org-local missing content", beta[key])
    require(protected_knowledge() == before, "J1 ownership reads are read-only in both orgs")
    record["ownership_state"] = dict(before=before, after=protected_knowledge())
    require(c.get(kb_path) == api["kb"] and knowledge_read(c, artifact_path) == api["artifact"], "J1 alpha content retained")
    c.mark("J1.cross-org-isolation")
    record["finished"] = time.monotonic()
