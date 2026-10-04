"""Finite whole-report regressions over the production SQLite/report boundary."""
from datetime import datetime, timezone
from pathlib import Path
import json

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def error_oracle(code, count=1, now=NOW):
    return {
        "data_through": now.isoformat(), "measurement_timezone": "UTC",
        "acquisition_complete": True,
        "epoch": {"status": "unversioned", "id": None,
                  "collection_started": False, "started_at": None},
        "observation_period": {
            "first_impression_at": None, "days_elapsed": 0, "required_days": 14,
            "total_correlated_sessions": 0, "required_sessions": 500,
            "days_met": False, "sessions_met": False, "thresholds_met": False,
            "diagnostics_valid_for_collection": False,
            "status": "insufficient_instrumentation", "reason_code": code,
        },
        "instrumentation_health": {
            "status": "unhealthy", "malformed_records": count, "reason_code": code,
        },
        "aggregate": {}, "by_agent": {}, "by_role": {}, "read_counts": {},
        "excluded": {}, "diagnostic_errors": {code: count},
        "decision": "insufficient_instrumentation", "evaluation_candidate": False,
        "decision_detail": f"Malformed {code.removeprefix('malformed_').replace('_', ' ')} evidence; collection is not eligible.",
    }


def test_r02_exact_task7799_short_malformed_search(tmp_path):
    db = Database(tmp_path / "report.db")
    try:
        logger = AuditLogger(db)
        logger.log_memory_digest_impression(agent="dev_agent", task_id="TASK-0",
                                           session_id="sess0", digest_ids=["MEM-001"], budget=1500)
        logger.log_memory_read(agent="dev_agent", id="MEM-001", slug="x",
                               session_id="sess500", task_id="TASK-500", source="digest")
        db.insert_audit_log(task_id="TASK-500", agent="dev_agent", action="memory_search", payload=[])
        db._conn.execute("UPDATE audit_log SET timestamp = '2026-01-01T00:00:00+00:00'")
        db._conn.commit()
        assert logger.compute_memory_telemetry_report(current_time=NOW) == error_oracle("malformed_search")
    finally:
        db.close()


import copy
from collections import Counter
from datetime import timedelta
import pytest

from cli.client.client import OpcClient
from cli.main import build_parser
from runtime.infrastructure.memory_telemetry_report import ACTIONS, ReportAcquisitionUnavailable

OCT = datetime(2026, 10, 15, tzinfo=timezone.utc)
FIRST = datetime(2026, 10, 1, tzinfo=timezone.utc)
ROLES = {"engineering_head": "manager", "dev_agent": "worker", "qa_engineer": "worker"}


def stamp_new(db, before, timestamp):
    db.execute("UPDATE audit_log SET timestamp=? WHERE id>?", (timestamp.isoformat(), before))
    db._conn.commit()


def emit(db, index=0, agent="dev_agent", *, mode="pointer", read=False, search=False, legacy=False):
    """Production DDL and all four real audit writers, explicit fixture times."""
    logger = AuditLogger(db)
    before = db.fetch_one_readonly("SELECT COALESCE(MAX(id),0) AS n FROM audit_log")["n"]
    sid, task = f"sid{index:04d}", f"TASK-{index:04d}"
    ids = [f"MEM-{i:03d}" for i in range(1, 11)]
    if mode in ("fit", "fallback"):
        ids = ["MEM-001"]
    if mode == "legacy-duplicates":
        ids = ["MEM-001"] * 3
    metadata = {} if legacy else {
        "memory_telemetry_version": 1,
        "pointer_ids": [] if mode in ("body", "fit") else ids,
        "full_body_ids": ids if mode in ("body", "fit") else [],
    }
    logger.log_memory_digest_impression(agent=agent, task_id=task, session_id=sid,
                                       digest_ids=ids, budget=255 if mode == "fit" else 172 if mode == "fallback" else 1500,
                                       **metadata)
    logger.log_session_start(task, agent, "/disposable", session_id=sid, invocation_purpose="worker_execution")
    if read:
        logger.log_memory_read(agent=agent, id="MEM-001", slug="x", session_id=sid, task_id=task, source="digest")
    if search:
        logger.log_memory_search(agent=agent, session_id=sid, task_id=task, memory_ids=["MEM-011"], hit_count=1, kb_hit_count=0)
    stamp_new(db, before, FIRST + timedelta(minutes=index))
    return (agent, task, sid)


def numeric_metrics(q=0, p=0, b=0, x=0, r=0, ops=0, s=0, n=0, activated=0, ps=0):
    # Oracle arithmetic is a declared numerical ledger, never report output.
    return {
        "correlated_sessions": q, "pointer_opportunities": p, "full_body_exposures": b,
        "pointer_pairs_read": x, "digest_pull_through": x / p if p else None,
        "search_sourced_reads": s, "search_sourced_absent_from_digest": n,
        "search_absent_fraction": n / s if s else None, "distinct_valid_read_pairs": r,
        "read_operations": ops, "pointer_sessions_activated": activated, "pointer_sessions": ps,
        "session_activation": activated / ps if ps else None,
    }


def observation_oracle(agent_metrics=None, *, now=OCT, first=FIRST, starts=None, digest=0, explicit=0,
                       read_counts=None, errors=None, excluded=None, duplicates=0, repeated=0, unknown=0, roles=ROLES):
    agent_metrics = agent_metrics or {}
    sums = {k: sum(m[k] or 0 for m in agent_metrics.values()) for k in (
        "correlated_sessions", "pointer_opportunities", "full_body_exposures", "pointer_pairs_read",
        "search_sourced_reads", "search_sourced_absent_from_digest", "distinct_valid_read_pairs",
        "read_operations", "pointer_sessions_activated", "pointer_sessions")}
    # Missing exposure metrics are explicitly unavailable, never inferred.
    if any(m["pointer_opportunities"] is None for m in agent_metrics.values()):
        for key in ("pointer_opportunities", "full_body_exposures", "pointer_pairs_read", "pointer_sessions_activated", "pointer_sessions"):
            sums[key] = None
    q = sums["correlated_sessions"]
    all_metrics = {**sums,
        "digest_pull_through": sums["pointer_pairs_read"] / sums["pointer_opportunities"] if sums["pointer_opportunities"] else None,
        "search_absent_fraction": sums["search_sourced_absent_from_digest"] / sums["search_sourced_reads"] if sums["search_sourced_reads"] else None,
        "session_activation": sums["pointer_sessions_activated"] / sums["pointer_sessions"] if sums["pointer_sessions"] else None,
    }
    by_role = {}
    for role in sorted(set((roles or {}).values())):
        members = [m for a, m in agent_metrics.items() if (roles or {}).get(a) == role]
        if members:
            vals = {k: sum(m[k] for m in members) for k in sums if all(m[k] is not None for m in members)}
            data = numeric_metrics(vals["correlated_sessions"], vals.get("pointer_opportunities", 0), vals.get("full_body_exposures", 0),
                                   vals.get("pointer_pairs_read", 0), vals["distinct_valid_read_pairs"], vals["read_operations"],
                                   vals["search_sourced_reads"], vals["search_sourced_absent_from_digest"],
                                   vals.get("pointer_sessions_activated", 0), vals.get("pointer_sessions", 0))
            for k in sums:
                if k not in vals:
                    data[k] = None
            by_role[role] = {**data, "retrieval_corroboration_eligible": False, "descriptive_only_for_activation_majority": True}
    anchor = first.replace(hour=0, minute=0, second=0, microsecond=0) if first and q else None
    if anchor is not None and first != anchor:
        anchor += timedelta(days=1)
    days = max(0, (now.replace(hour=0, minute=0, second=0, microsecond=0) - anchor).days) if anchor else 0
    return {
        "data_through": now.isoformat(), "measurement_timezone": "UTC", "acquisition_complete": True,
        "epoch": {"status": "unversioned", "id": None, "collection_started": False, "started_at": None},
        "observation_period": {"first_impression_at": first.isoformat() if first and q else None,
            "days_elapsed": days, "required_days": 14, "total_correlated_sessions": q, "required_sessions": 500,
            "days_met": days >= 14, "sessions_met": q >= 500, "thresholds_met": False,
            "diagnostics_valid_for_collection": False, "status": "insufficient_instrumentation", "reason_code": "missing_authority"},
        "instrumentation_health": {"status": "unhealthy" if errors else "unavailable", "population": "audited intended task-session invocations",
            "audited_task_starts": q if starts is None else starts, "intended_task_launches": None,
            "expected_nonempty_launches": None, "matching_exposures": q, "unknown_exposures": unknown,
            "validated_read_operations": sums["read_operations"], "rejected_task_attribution": sum((errors or {}).values()),
            "malformed_records": 0, "complete_launch_census": "UNKNOWN", "probe_health": "UNKNOWN", "epoch_health": "UNKNOWN",
            "roles_available": roles is not None, "reason_code": "missing_authority", "duplicate_impressions": duplicates,
            "repeated_read_pairs": repeated},
        "aggregate": {**all_metrics, "digest_sourced_read_pairs": digest, "explicit_read_pairs": explicit,
            "untrusted_task_reads": sum((errors or {}).values()), "eligible_functional_agents": 0,
            "eligible_pointer_agents": 0, "eligible_agents_below_10_percent": 0},
        "by_agent": {a: {"role": (roles or {}).get(a), "eligible": False, "activation_vote_eligible": False, **m}
                     for a, m in sorted(agent_metrics.items())},
        "by_role": by_role, "read_counts": read_counts or {},
        "excluded": excluded or {"manual": 0, "thread": 0, "dream": 0, "recovery": 0, "legacy": 0},
        "diagnostic_errors": errors or {}, "decision": "insufficient_instrumentation", "evaluation_candidate": False,
        "decision_detail": "Missing trusted epoch/canary authority and independently complete launch/expectation census; counts are observation-only.",
    }


def assert_text_report(rendered, expected):
    """Independent CLI text contract over the declared numeric/error oracle."""
    obs = expected["observation_period"]
    summary = [
        "=== THR-091 Memory Telemetry Report (observation-only) ===",
        "Status: insufficient_instrumentation",
        "Canary-gated collection has NOT started; epoch is unversioned and invalid.",
        f"Data through: {expected['data_through']}; timezone UTC",
        f"First event: {obs['first_impression_at']}",
        f"Days elapsed: {obs['days_elapsed']} / 14",
        f"Sessions: {obs['total_correlated_sessions']} / 500",
        "Thresholds:    NOT MET",
        "Population: audited intended task-session invocations; complete launch census UNKNOWN",
    ]
    lines = rendered.splitlines()
    assert lines[:9] == summary
    remaining = iter(lines[9:])
    # Each complete section is checked independently; no report/formatter call
    # supplies expected values, and extra/missing metrics cannot hide in text.
    for heading, groups in [("AGGREGATE", {"all": expected["aggregate"]}),
                            ("BY AGENT", expected["by_agent"]), ("BY ROLE", expected["by_role"])]:
        assert next(remaining) == heading
        for name in sorted(groups):
            assert next(remaining) == f"  [{name}]"
            actual = {}
            for _ in groups[name]:
                label, value = next(remaining).strip().split(": ", 1)
                assert label not in actual
                actual[label] = value
            assert actual == {key: "unknown" if value is None else str(value)
                              for key, value in groups[name].items()}
    for label in ["read_counts", "excluded", "diagnostic_errors", "instrumentation_health"]:
        name, encoded = next(remaining).split(": ", 1)
        assert name == label
        assert json.loads(encoded) == expected[label]
    assert list(remaining) == ["DECISION: insufficient_instrumentation", expected["decision_detail"]]
    assert rendered.endswith("\n")


def cli_report(client, monkeypatch, capsys, now, expected):
    # Run the actual canonical parser, command, OpcClient and authenticated routes.
    import datetime as clock_module
    real_datetime = clock_module.datetime
    class Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(clock_module, "datetime", Fixed)
    opc = OpcClient("http://testserver", "disposable")
    opc._client.close()
    opc._client = client
    monkeypatch.setattr(OpcClient, "from_env", classmethod(lambda cls: opc))
    for json_mode in (True, False):
        args = build_parser().parse_args(["memory", "report", "--org", "alpha", "--agent", "dev_agent"] + (["--json"] if json_mode else []))
        args.func(args)
        result = capsys.readouterr()
        assert result.err == ""
        if json_mode:
            assert json.loads(result.out) == expected
            assert "NaN" not in result.out and "Infinity" not in result.out
        else:
            assert_text_report(result.out, expected)
    monkeypatch.setattr(clock_module, "datetime", real_datetime)


def assert_venues(client, org, monkeypatch, capsys, expected, now=OCT, roles=ROLES):
    before = [tuple(r) for r in org.db.fetch_all_readonly("SELECT * FROM audit_log ORDER BY id")]
    # /agents is a real roster projection; fixtures declare role fields explicitly.
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    for name, role in (roles or {}).items():
        path = org.root / "org" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_agent_text(AgentDef(name=name, team="engineering", role=role, executor="claude",
                                                allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None, enrolled_at=None, system_prompt="fixture", description="")))
    backend = AuditLogger(org.db).compute_memory_telemetry_report(agent_role_map=roles, current_time=now)
    assert backend == expected
    cli_report(client, monkeypatch, capsys, now, expected)
    after = [tuple(r) for r in org.db.fetch_all_readonly("SELECT * FROM audit_log ORDER BY id")]
    assert before == after


@pytest.mark.parametrize("case", ["R01-empty", "R01-intended-only", "R01-manual", "R02", "R03", "R04"])
def test_r01_r04_whole_empty_and_corrupt_reports(client_with_runtime, monkeypatch, capsys, case):
    client, org = client_with_runtime
    db = org.db
    if case == "R01-empty":
        expected = observation_oracle(now=NOW, first=None)
    elif case == "R01-intended-only":
        AuditLogger(db).log_session_start("TASK-intended", "dev_agent", "/fixture", session_id="intended", invocation_purpose="worker_execution")
        db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")
        expected = observation_oracle({"dev_agent": numeric_metrics()}, now=NOW, first=None, starts=1, unknown=1)
    elif case == "R01-manual":
        logger = AuditLogger(db)
        logger.log_memory_read(agent="dev_agent", id="MEM-001", slug="x")
        logger.log_memory_search(agent="dev_agent", session_id=None, memory_ids=[], hit_count=0, kb_hit_count=0)
        db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")
        expected = observation_oracle(now=NOW, first=None, excluded={"manual": 2, "manual_read": 1, "manual_search": 1,
                                  "thread": 0, "dream": 0, "recovery": 0, "legacy": 0})
    else:
        if case != "R04":
            AuditLogger(db).log_memory_digest_impression(agent="dev_agent", task_id="TASK-0", session_id="sess0", digest_ids=["MEM-001"], budget=1500)
        if case == "R02":
            # Preserve the exact TASK7799 short-malformed-search read tuple,
            # in addition to its legacy impression and malformed search.
            AuditLogger(db).log_memory_read(agent="dev_agent", id="MEM-001", slug="x",
                                           session_id="sess500", task_id="TASK-500", source="digest")
        action = "memory_read" if case == "R03" else "memory_search"
        db.insert_audit_log(task_id="TASK-500", agent="dev_agent", action=action,
                            payload={"id": "MEM-001", "source": "digest", "session_id": "sess500", "task_id": "TASK-500"} if case == "R03" else [])
        db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")
        if case == "R03":
            db.execute("UPDATE audit_log SET timestamp='not-a-date' WHERE action='memory_read'")
        expected = error_oracle("malformed_read_timestamp" if case == "R03" else "malformed_search")
    assert_venues(client, org, monkeypatch, capsys, expected, NOW)


@pytest.mark.parametrize("mode", ["pointer", "fit", "fallback", "body", "legacy", "legacy-duplicates"])
def test_r05_r11_observed_modes_and_legacy_refusal(client_with_runtime, monkeypatch, capsys, mode):
    client, org = client_with_runtime
    emit(org.db, mode=mode, read=True, legacy=mode.startswith("legacy"))
    body = mode in ("fit", "body")
    count = 1 if mode in ("fit", "fallback") else 10
    m = numeric_metrics(1, 0 if body else count, count if body else 0, 0 if body else 1, 1, 1,
                        activated=0 if body else 1, ps=0 if body else 1)
    if mode.startswith("legacy"):
        for k in ("pointer_opportunities", "full_body_exposures", "pointer_pairs_read", "digest_pull_through", "pointer_sessions_activated", "pointer_sessions", "session_activation"):
            m[k] = None
    expected = observation_oracle({"dev_agent": m}, digest=1, unknown=int(mode.startswith("legacy")),
                                 read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}}})
    assert_venues(client, org, monkeypatch, capsys, expected)


@pytest.mark.parametrize("population", [0, 1, 501])
@pytest.mark.parametrize("variant", ["json", "array", "null", "ids-scalar", "ids-null", "blank-sid", "agent", "read-id", "source", "search-ids", "hits-negative", "hits-bool", "date", "naive", "source-null"])
def test_r13_full_structural_matrix(client_with_runtime, monkeypatch, capsys, population, variant):
    client, org = client_with_runtime
    for i in range(population):
        emit(org.db, i)
    # Q0 still has genuine production task-start provenance for source validators.
    logger = AuditLogger(org.db)
    logger.log_session_start("TASK-bad", "dev_agent", "/disposable", session_id="bad", invocation_purpose="worker_execution")
    payloads = {
        "memory_digest_impression": {"agent": "dev_agent", "session_id": "bad", "digest_ids": ["MEM-001"]},
        "memory_read": {"id": "MEM-001", "source": "explicit_or_other", "session_id": "bad", "task_id": "TASK-bad"},
        "memory_search": {"session_id": "bad", "task_id": "TASK-bad", "memory_ids": ["MEM-001"], "hit_count": 1, "kb_hit_count": 0},
    }
    action = "memory_digest_impression"
    if variant in ("read-id", "source", "source-null"):
        action = "memory_read"
    if variant in ("json", "array", "null", "search-ids", "hits-negative", "hits-bool"):
        action = "memory_search"
    p = payloads[action]
    code = "malformed_" + action.removeprefix("memory_digest_").removeprefix("memory_")
    mutations = {"ids-scalar": ("digest_ids", "MEM-001"), "ids-null": ("digest_ids", [None]),
        "blank-sid": ("session_id", ""), "agent": ("agent", "wrong"), "read-id": ("id", None),
        "source": ("source", "future-source"), "source-null": ("source", None),
        "search-ids": ("memory_ids", "MEM-001"), "hits-negative": ("hit_count", -1), "hits-bool": ("hit_count", True)}
    if variant in mutations:
        k, v = mutations[variant]
        p[k] = v
    if variant == "array":
        p = []
    if variant == "null":
        p = None
    row_id = org.db.insert_audit_log(task_id="AGENT-dev_agent" if action == "memory_read" else "TASK-bad", agent="dev_agent", action=action, payload=p)
    org.db.execute("UPDATE audit_log SET timestamp=? WHERE id=?", (FIRST.isoformat(), row_id))
    org.db.execute("UPDATE audit_log SET timestamp=? WHERE action='session_start' AND task_id='TASK-bad'", (FIRST.isoformat(),))
    if variant == "json":
        org.db.execute("UPDATE audit_log SET payload='{' WHERE id=?", (row_id,))
        # Actual route decoder raises: backend sees structural returned bytes,
        # HTTP acquisition instead fails before any report exists (R24).
        assert AuditLogger(org.db).compute_memory_telemetry_report(current_time=OCT) == error_oracle(code, now=OCT)
        with pytest.raises(SystemExit) as exc:
            cli_report(client, monkeypatch, capsys, OCT, {})
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == "acquisition_unavailable\n"
        return
    if variant in ("date", "naive"):
        org.db.execute("UPDATE audit_log SET timestamp=? WHERE id=?", ("not-a-date" if variant == "date" else "2026-10-01T00:00:00", row_id))
        code += "_timestamp"
    if variant == "source":
        code = "invalid_source"
    if variant == "source-null":
        code = "malformed_read_source_type"
    assert_venues(client, org, monkeypatch, capsys, error_oracle(code, now=OCT))


def append_read(db, key, mid="MEM-001", source="digest", timestamp=FIRST + timedelta(seconds=1)):
    a, task, sid = key
    before = db.fetch_one_readonly("SELECT COALESCE(MAX(id),0) n FROM audit_log")["n"]
    AuditLogger(db).log_memory_read(agent=a, id=mid, slug="x", task_id=task, session_id=sid, source=source)
    stamp_new(db, before, timestamp)


@pytest.mark.parametrize("variant", ["duplicates", "search-dedup", "wrong-agent", "wrong-task", "unknown-sid", "wrong-scope",
    "missing-search", "late-search", "early-read", "wrong-search-task", "shown-search", "body-search"])
def test_r07_r08_r11_r12_d08_d09_pair_mechanics(client_with_runtime, monkeypatch, capsys, variant):
    client, org = client_with_runtime
    db = org.db
    key = emit(db, read=True, search=True, mode="body" if variant == "body-search" else "pointer")
    expected = observation_oracle({"dev_agent": numeric_metrics(1, 0 if variant == "body-search" else 10,
                        10 if variant == "body-search" else 0, 0 if variant == "body-search" else 1,
                        1, 1, activated=0 if variant == "body-search" else 1, ps=0 if variant == "body-search" else 1)},
        digest=1, read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}}})
    if variant == "duplicates":
        originals = [dict(r) for r in db.fetch_all_readonly("SELECT * FROM audit_log WHERE action IN ('memory_read', 'memory_digest_impression')")]
        for _ in range(2):
            for row in originals:
                new_id = db.insert_audit_log(task_id=row["task_id"], agent=row["agent"], action=row["action"], payload=json.loads(row["payload"]))
                db.execute("UPDATE audit_log SET timestamp=? WHERE id=?", (row["timestamp"], new_id))
        expected["aggregate"]["read_operations"] = 3
        expected["by_agent"]["dev_agent"]["read_operations"] = 3
        expected["by_role"]["worker"]["read_operations"] = 3
        expected["read_counts"]["dev_agent"]["MEM-001"]["operations"] = 3
        expected["instrumentation_health"].update(duplicate_impressions=2, repeated_read_pairs=2, validated_read_operations=3)
    elif variant == "search-dedup":
        for _ in range(3):
            append_read(db, key, "MEM-011", "search")
        expected = observation_oracle({"dev_agent": numeric_metrics(1, 10, 0, 1, 2, 4, 1, 1, 1, 1)},
            digest=1, repeated=2, read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}, "MEM-011": {"distinct_pairs": 1, "operations": 3}}})
    else:
        if variant in ("wrong-agent", "wrong-task", "unknown-sid", "wrong-scope"):
            append_read(db, key)
            row = db.fetch_one_readonly("SELECT MAX(id) n FROM audit_log")["n"]
            p = json.loads(db.fetch_one_readonly("SELECT payload FROM audit_log WHERE id=?", (row,))["payload"])
            if variant == "wrong-agent":
                db.execute("UPDATE audit_log SET agent='qa_engineer',task_id='AGENT-qa_engineer' WHERE id=?", (row,))
            if variant == "wrong-task":
                p["task_id"] = "TASK-wrong"
            if variant == "unknown-sid":
                p["session_id"] = "unknown"
            if variant == "wrong-scope":
                db.execute("UPDATE audit_log SET task_id='TASK-0000' WHERE id=?", (row,))
            db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(p), row))
            code = "rejected_attribution"
        else:
            mid = "MEM-001" if variant in ("shown-search", "body-search") else "MEM-011"
            if variant in ("shown-search", "body-search"):
                search_row = db.fetch_one_readonly("SELECT id,payload FROM audit_log WHERE action='memory_search'")
                search_payload = json.loads(search_row["payload"])
                search_payload.update(memory_ids=["MEM-001", "MEM-011"], hit_count=2)
                db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(search_payload), search_row["id"]))
            if variant == "missing-search":
                db.execute("DELETE FROM audit_log WHERE action='memory_search'")
            if variant == "late-search":
                db.execute("UPDATE audit_log SET timestamp=? WHERE action='memory_search'", ((FIRST + timedelta(seconds=2)).isoformat(),))
            if variant == "wrong-search-task":
                db.execute("UPDATE audit_log SET task_id='TASK-other' WHERE action='memory_search'")
            if variant == "early-read":
                db.execute("UPDATE audit_log SET timestamp=? WHERE action='session_start'", ((FIRST - timedelta(seconds=2)).isoformat(),))
                append_read(db, key, timestamp=FIRST - timedelta(seconds=1))
            else:
                append_read(db, key, mid, "search")
            code = "source_contradiction"
        count = 2 if variant == "wrong-search-task" else 1
        expected["diagnostic_errors"] = {code: 1}
        if count == 2:
            expected["diagnostic_errors"]["rejected_attribution"] = 1
        expected["aggregate"]["untrusted_task_reads"] = 1
        expected["instrumentation_health"]["status"] = "unhealthy"
        expected["instrumentation_health"]["rejected_task_attribution"] = count
    assert_venues(client, org, monkeypatch, capsys, expected)


@pytest.mark.parametrize("source", ["explicit_or_other", "future-source", None])
def test_r09_exclusion_before_source_validation(client_with_runtime, monkeypatch, capsys, source):
    client, org = client_with_runtime
    logger, db = AuditLogger(org.db), org.db
    counts = Counter({"manual": 0, "thread": 0, "dream": 0, "recovery": 0, "legacy": 0})
    # Two manual, two thread, two dream, one recovery, one legacy record.
    for i, purpose in enumerate([None, None, "thread_reply", "thread_followup", "dream", "dream", "unattributed", "legacy"]):
        sid, task = f"excluded-{i}", f"TASK-client-claim-{i}"
        if purpose is not None:
            logger.log_session_start(task, "dev_agent", "/fixture", session_id=sid, invocation_purpose=purpose)
        db.insert_audit_log(task_id="AGENT-dev_agent", agent="dev_agent", action="memory_read",
            payload={"id": "MEM-001", "source": source, **({"task_id": task, "session_id": sid} if purpose is not None else {"task_id": task})})
        category = {"thread_reply": "thread", "thread_followup": "thread", "dream": "dream", "unattributed": "recovery", "legacy": "legacy"}.get(purpose, "manual")
        counts[category] += 1
        counts[f"{category}_read"] += 1
    db.execute("UPDATE audit_log SET timestamp=?", (FIRST.isoformat(),))
    assert_venues(client, org, monkeypatch, capsys, observation_oracle(first=None, excluded=dict(counts)))


def test_r10_sid_collision_has_no_last_row_wins_credit(client_with_runtime, monkeypatch, capsys):
    client, org = client_with_runtime
    emit(org.db)
    AuditLogger(org.db).log_session_start("TASK-other", "dev_agent", "/fixture", session_id="sid0000", invocation_purpose="worker_execution")
    org.db.execute("UPDATE audit_log SET timestamp=?", (FIRST.isoformat(),))
    assert_venues(client, org, monkeypatch, capsys, error_oracle("ambiguous_session_binding", now=OCT))


@pytest.mark.parametrize("order", ["ascending", "descending", "offset", "ties"])
def test_r14_minimum_order_ties_aware_offsets(client_with_runtime, monkeypatch, capsys, order):
    client, org = client_with_runtime
    for i in (range(3) if order == "ascending" else reversed(range(3))):
        emit(org.db, i)
        timestamp = (FIRST + timedelta(days=i if order != "ties" else 0)).isoformat()
        if order == "offset":
            timestamp = (FIRST + timedelta(days=i)).astimezone(timezone(timedelta(hours=8))).isoformat()
        org.db.execute("UPDATE audit_log SET timestamp=? WHERE task_id=?", (timestamp, f"TASK-{i:04d}"))
    assert_venues(client, org, monkeypatch, capsys, observation_oracle({"dev_agent": numeric_metrics(3, 30, ps=3)}))


@pytest.mark.parametrize("cutoff,first,count", [
    ("2026-10-14T23:59:59+00:00", "2026-10-01T00:00:00+00:00", 499),
    ("2026-10-15T00:00:00+00:00", "2026-10-01T00:00:00+00:00", 500),
    ("2026-10-15T12:00:00+00:00", "2026-10-01T12:00:00+00:00", 501),
    ("2026-10-16T08:00:00+08:00", "2026-10-02T00:00:00+12:00", 500),
])
def test_r15_r17_complete_days_and_volume_remain_raw(client_with_runtime, monkeypatch, capsys, cutoff, first, count):
    client, org = client_with_runtime
    anchor = datetime.fromisoformat(first).astimezone(timezone.utc)
    now = datetime.fromisoformat(cutoff).astimezone(timezone.utc)
    for i in range(count):
        emit(org.db, i)
    org.db.execute("UPDATE audit_log SET timestamp=?", (anchor.isoformat(),))
    expected = observation_oracle({"dev_agent": numeric_metrics(count, 10 * count, ps=count)}, now=now, first=anchor)
    assert_venues(client, org, monkeypatch, capsys, expected, now)


@pytest.mark.parametrize("claim", ["wrong-org", "installed-sha", "verifier", "early-epoch", "late-acceptance", "revoked", "missing-record", "digest-mismatch"])
def test_r18_r19_forged_authority_does_not_start_collection(client_with_runtime, monkeypatch, capsys, claim):
    client, org = client_with_runtime
    emit(org.db, read=True)
    row = org.db.fetch_one_readonly("SELECT id,payload FROM audit_log WHERE action='memory_digest_impression'")
    payload = json.loads(row["payload"])
    payload.update(epoch="forged", canary_pass=True, synthetic=False, installed_sha=claim, accepted_at="2026-01-01T00:00:00Z")
    org.db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    expected = observation_oracle({"dev_agent": numeric_metrics(1, 10, x=1, r=1, ops=1, activated=1, ps=1)},
                                 digest=1, read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}}})
    assert_venues(client, org, monkeypatch, capsys, expected)


@pytest.fixture(scope="module", params=[551, 5001])
def paged_dataset(request, tmp_path_factory):
    n = request.param
    db = Database(tmp_path_factory.mktemp(f"pages-{n}") / "report.db")
    counts = (201, 175, 175) if n == 551 else (2001, 1500, 1500)
    names = tuple(ROLES)
    ledger = {a: [] for a in ACTIONS}
    original = db.insert_audit_log
    # Capture the actual writer-returned identity at the existing insert seam.
    # Each payload is independently checked below, rather than copied from reads.
    def capture(**kwargs):
        row_id = original(**kwargs)
        ledger[kwargs["action"]].append({"id": row_id, **copy.deepcopy(kwargs)})
        return row_id
    db.insert_audit_log = capture
    try:
        for i in range(n):
            agent = names[0] if i < counts[0] else names[1] if i < sum(counts[:2]) else names[2]
            emit(db, i, agent, read=True, search=True)
        for action, rows in ledger.items():
            assert len(rows) == n
            for i, row in enumerate(rows):
                row["timestamp"] = (FIRST + timedelta(minutes=i)).isoformat()
                p = row["payload"]
                assert p.get("session_id") == f"sid{i:04d}"
                if action == "memory_digest_impression":
                    assert p == {"agent": row["agent"], "session_id": f"sid{i:04d}", "digest_ids": [f"MEM-{j:03d}" for j in range(1, 11)],
                                 "digest_count": 10, "budget": 1500, "memory_telemetry_version": 1,
                                 "pointer_ids": [f"MEM-{j:03d}" for j in range(1, 11)], "full_body_ids": []}
                if action == "memory_read":
                    assert p["task_id"] == f"TASK-{i:04d}" and p["id"] == "MEM-001" and p["source"] == "digest"
                    assert row["task_id"] == f"AGENT-{row['agent']}"
                else:
                    assert row["task_id"] == f"TASK-{i:04d}"
        db._conn.commit()
        yield db, ledger, counts
    finally:
        db.insert_audit_log = original
        db.close()


def page_oracle(counts):
    return observation_oracle({a: numeric_metrics(c, c * 10, 0, c, c, c, activated=c, ps=c)
                              for a, c in zip(ROLES, counts)}, digest=sum(counts),
        read_counts={a: {"MEM-001": {"distinct_pairs": c, "operations": c}} for a, c in zip(ROLES, counts)})


def audit_projection(rows):
    return [{k: r[k] for k in ("id", "timestamp", "agent", "task_id", "action", "payload")} for r in rows]


def real_pages(client, action, limit):
    cursor, pages, union = None, [], []
    while True:
        response = client.get("/api/v1/orgs/alpha/audit", params={"action": action, "limit": limit, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200
        page = response.json()
        pages.append(len(page["entries"]))
        union.extend(page["entries"])
        nxt = page["next_cursor"]
        if nxt is None:
            break
        from runtime.infrastructure.database import _decode_cursor
        if cursor is not None:
            assert _decode_cursor(nxt) < _decode_cursor(cursor)
        cursor = nxt
    return pages, union


def test_r21a_actual_query_http250_and_exact_writer_unions(client_with_runtime, paged_dataset, monkeypatch):
    client, org = client_with_runtime
    db, ledger, counts = paged_dataset
    monkeypatch.setattr(org, "db", db)
    n = sum(counts)
    expected_pages = [250] * (n // 250) + ([n % 250] if n % 250 else [])
    for action in ACTIONS:
        pages, union = real_pages(client, action, 250)
        assert pages == expected_pages
        expected = sorted(ledger[action], key=lambda r: r["id"])
        assert sorted(audit_projection(union), key=lambda r: r["id"]) == expected
        assert len({r["id"] for r in union}) == n
        assert union[0]["payload"]["session_id"] == f"sid{n-250:04d}"
        assert union[-1]["payload"]["session_id"] == f"sid{n%250-1:04d}"
        direct = []
        cursor = None
        while True:
            rows, cursor = db.query_audit_logs(action=action, limit=250, cursor=cursor)
            assert rows == sorted(rows, key=lambda r: r["id"])
            direct.extend(rows)
            if cursor is None:
                break
        assert sorted(audit_projection(direct), key=lambda r: r["id"]) == expected


def test_r21b_real_loopback_canonical_cli5000_json_text(client_with_runtime, paged_dataset, monkeypatch, tmp_path):
    import socket
    import subprocess
    import sys
    import threading
    import uvicorn
    from runtime.runtime import port_file
    client, org = client_with_runtime
    db, ledger, counts = paged_dataset
    monkeypatch.setattr(org, "db", db)
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    for name, role in ROLES.items():
        (org.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
        (org.root / "org" / "agents" / f"{name}.md").write_text(render_agent_text(AgentDef(
            name=name, role=role, team="engineering", executor="claude", allow_rules=(), repos={},
            enrolled_by=None, enrolled_at_task=None, enrolled_at=None, system_prompt="fixture")))
    observed = []
    query = db.query_audit_logs
    def capture(**kwargs):
        result = query(**kwargs)
        observed.append((kwargs, copy.deepcopy(result)))
        return result
    monkeypatch.setattr(db, "query_audit_logs", capture)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(client.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    port_file().write_text(str(sock.getsockname()[1]))
    expected = page_oracle(counts)
    script = '''import datetime, sys, runpy
import runtime.infrastructure.memory_telemetry_report
real = datetime.datetime
now = real(2026,10,15,tzinfo=datetime.timezone.utc)
class Fixed(real):
    @classmethod
    def now(cls, tz=None): return now if tz else now.replace(tzinfo=None)
datetime.datetime = Fixed
sys.argv = ['happyranch','memory','report','--org','alpha','--agent','dev_agent'] + sys.argv[1:]
runpy.run_module('cli.main',run_name='__main__')
'''
    try:
        for mode in (["--json"], []):
            result = subprocess.run([sys.executable, "-c", script, *mode], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert result.stderr == ""
            if mode:
                assert json.loads(result.stdout) == expected
            else:
                assert_text_report(result.stdout, expected)
        n = sum(counts)
        pages = [5000, 1] if n == 5001 else [551]
        for action in ACTIONS:
            samples = [(kw, rows) for kw, (rows, nxt) in observed if kw["action"] == action]
            assert [len(rows) for kw, rows in samples] == pages * 4  # JSON/text, two sweeps each
            for start in range(0, len(samples), len(pages)):
                union = [r for kw, rows in samples[start:start+len(pages)] for r in rows]
                assert all(kw["limit"] == 5000 for kw, rows in samples[start:start+len(pages)])
                assert sorted(audit_projection(union), key=lambda r: r["id"]) == sorted(ledger[action], key=lambda r: r["id"])
                assert len({r["id"] for r in union}) == n
                assert min(r["payload"]["session_id"] for r in union) == "sid0000"
                assert max(r["payload"]["session_id"] for r in union) == f"sid{n-1:04d}"
        assert AuditLogger(db).compute_memory_telemetry_report(current_time=OCT, agent_role_map=ROLES) == expected
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def clone_dataset(paged_dataset, tmp_path):
    db, _, _ = paged_dataset
    target = Database(tmp_path / "copy.db")
    db._conn.backup(target._conn)
    return target


@pytest.mark.parametrize("action", ACTIONS)
def test_r22a_last_page_corruption_all_four_streams(client_with_runtime, paged_dataset, tmp_path, monkeypatch, capsys, action):
    client, org = client_with_runtime
    db = clone_dataset(paged_dataset, tmp_path)
    monkeypatch.setattr(org, "db", db)
    bad = {"session_start": [], "memory_digest_impression": {"agent": "engineering_head", "session_id": "sid0000", "digest_ids": "MEM-001"},
           "memory_read": {"id": "MEM-001", "session_id": "sid0000", "task_id": "TASK-0000", "source": None}, "memory_search": []}[action]
    code = {"session_start": "malformed_session_start", "memory_digest_impression": "malformed_impression",
            "memory_read": "malformed_read_source_type", "memory_search": "malformed_search"}[action]
    try:
        row_id = db.fetch_one_readonly("SELECT MIN(id) n FROM audit_log WHERE action=?", (action,))["n"]
        db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(bad), row_id))
        page_counts, raw = real_pages(client, action, 250)
        assert page_counts == ([250, 250, 51] if paged_dataset[2][0] == 201 else [250] * 20 + [1])
        assert row_id in {r["id"] for r in raw[-page_counts[-1]:]}
        assert_venues(client, org, monkeypatch, capsys, error_oracle(code, now=OCT))
    finally:
        db.close()


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("fault", [500, 422, "timeout", "invalid-response"])
def test_r22b_actual_partial_acquisition_refuses(client_with_runtime, paged_dataset, monkeypatch, capsys, action, fault):
    import httpx
    client, org = client_with_runtime
    db, _, counts = paged_dataset
    monkeypatch.setattr(org, "db", db)
    # Both real HTTP page-limit venues are exercised: 250 raw route and 5000
    # actual CLI. The 551 CLI venue is one page, so faults there are injected
    # on the second sweep rather than pretending it has a second page.
    page_counts, union = real_pages(client, action, 250)
    assert page_counts[0] == 250 and len(union) == sum(counts)
    raw_first = client.get("/api/v1/orgs/alpha/audit", params={"action": action, "limit": 250})
    assert raw_first.status_code == 200 and len(raw_first.json()["entries"]) == 250
    raw_cursor = raw_first.json()["next_cursor"]
    assert raw_cursor is not None
    raw_requests = []
    actual_get = client.get
    def raw_failure(path, **kwargs):
        raw_requests.append(kwargs["params"].copy())
        assert kwargs["params"]["limit"] == 250 and kwargs["params"]["cursor"] == raw_cursor
        if fault == "timeout":
            raise httpx.ReadTimeout("fixture")
        if fault == "invalid-response":
            return httpx.Response(200, text="invalid JSON")
        return httpx.Response(fault, json={"detail": "fixture"})
    with monkeypatch.context() as raw_patch:
        raw_patch.setattr(client, "get", raw_failure)
        if fault == "timeout":
            with pytest.raises(httpx.ReadTimeout):
                client.get("/api/v1/orgs/alpha/audit", params={"action": action, "limit": 250, "cursor": raw_cursor})
        else:
            raw_second = client.get("/api/v1/orgs/alpha/audit", params={"action": action, "limit": 250, "cursor": raw_cursor})
            if fault == "invalid-response":
                with pytest.raises(ValueError):
                    raw_second.json()
            else:
                assert raw_second.status_code == fault
    assert len(raw_requests) == 1
    opc = OpcClient("http://testserver", "fixture")
    opc._client.close()
    opc._client = client
    calls, successes = [], []
    original = opc.get
    def broken(path, **kwargs):
        params = kwargs.get("params") or {}
        if path.endswith("/audit") and params.get("action") == action:
            calls.append(params.copy())
            if len(calls) == 2:
                if fault == "timeout":
                    raise httpx.ReadTimeout("fixture")
                if fault == "invalid-response":
                    return httpx.Response(200, text="invalid JSON")
                return httpx.Response(fault, json={"detail": "fixture"})
            response = original(path, **kwargs)
            successes.append(len(response.json()["entries"]))
            return response
        return original(path, **kwargs)
    monkeypatch.setattr(opc, "get", broken)
    monkeypatch.setattr(OpcClient, "from_env", classmethod(lambda cls: opc))
    before = db._conn.total_changes
    args = build_parser().parse_args(["memory", "report", "--org", "alpha", "--agent", "dev_agent", "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    captured = capsys.readouterr()
    assert exc.value.code == 1 and captured.out == "" and captured.err == "acquisition_unavailable\n"
    assert successes == [min(5000, sum(counts))]
    assert len(calls) == 2 and db._conn.total_changes == before
    assert bool(calls[1].get("cursor")) == (sum(counts) == 5001)


@pytest.mark.parametrize("fault", ["repeat", "cycle", "malformed", "entries", "cursor-type"])
def test_r23_cursor_schema_failures_are_bounded(client_with_runtime, paged_dataset, monkeypatch, capsys, fault, tmp_path, request):
    import httpx
    client, org = client_with_runtime
    db, _, counts = paged_dataset
    if sum(counts) == 551 and fault in ("repeat", "cycle"):
        # 551 rows cannot give a second canonical CLI page at limit5000.
        # Add real writer rows in a private DB, never fake a limit250 CLI page
        # or pass this negative through an unrelated malformed-cursor guard.
        db = clone_dataset(paged_dataset, tmp_path)
        request.addfinalizer(db.close)
        for i in range(551, 5001):
            AuditLogger(db).log_session_start(f"TASK-{i:04d}", "dev_agent", "/fixture",
                session_id=f"sid{i:04d}", invocation_purpose="worker_execution")
        db.execute("UPDATE audit_log SET timestamp=? WHERE action='session_start'", (FIRST.isoformat(),))
        db._conn.commit()
    monkeypatch.setattr(org, "db", db)
    opc = OpcClient("http://testserver", "fixture")
    opc._client.close()
    opc._client = client
    original, calls = opc.get, []
    def broken(path, **kwargs):
        response = original(path, **kwargs)
        if path.endswith("/audit"):
            params = kwargs["params"]
            calls.append(params.copy())
            body = response.json()
            if fault == "entries":
                body["entries"] = {}
            elif fault == "cursor-type":
                body["next_cursor"] = 42
            elif fault == "malformed":
                body["next_cursor"] = "not-a-cursor"
            elif len(calls) >= 2:
                if fault == "repeat":
                    body["next_cursor"] = params.get("cursor") or "not-a-cursor"
                elif len(calls) == 2 and body["entries"] and params.get("cursor"):
                    from runtime.infrastructure.database import _encode_cursor
                    oldest = min(body["entries"], key=lambda row: (row["timestamp"], row["id"]))
                    body["next_cursor"] = _encode_cursor(oldest["timestamp"], oldest["id"])
                else:
                    body["next_cursor"] = calls[1].get("cursor") or "not-a-cursor"
            return httpx.Response(200, json=body)
        return response
    monkeypatch.setattr(opc, "get", broken)
    monkeypatch.setattr(OpcClient, "from_env", classmethod(lambda cls: opc))
    args = build_parser().parse_args(["memory", "report", "--org", "alpha", "--agent", "dev_agent", "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    captured = capsys.readouterr()
    assert exc.value.code == 1 and captured.out == "" and captured.err == "acquisition_unavailable\n"
    assert len(calls) <= 3


def test_r24_backend_select_failure_is_typed(tmp_path, monkeypatch):
    import sqlite3
    db = Database(tmp_path / "failure.db")
    def failure(*args):
        raise sqlite3.OperationalError("fixture")
    monkeypatch.setattr(db, "fetch_all_readonly", failure)
    try:
        with pytest.raises(ReportAcquisitionUnavailable, match="acquisition_unavailable"):
            AuditLogger(db).compute_memory_telemetry_report(current_time=OCT)
    finally:
        db.close()


@pytest.mark.parametrize("change", ["at-cutoff", "newer", "backdated", "content", "roles"])
def test_r25_two_sweeps_detect_relevant_drift(client_with_runtime, monkeypatch, capsys, change):
    import httpx
    client, org = client_with_runtime
    emit(org.db)
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    for name, role in ROLES.items():
        path = org.root / "org" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_agent_text(AgentDef(name=name, role=role, team="engineering", executor="claude", allow_rules=(), repos={},
                                                  enrolled_by=None, enrolled_at_task=None, enrolled_at=None, system_prompt="fixture")))
    opc = OpcClient("http://testserver", "fixture")
    opc._client.close()
    opc._client = client
    original, calls = opc.get, []
    def drift(path, **kwargs):
        response = original(path, **kwargs)
        calls.append(path)
        if len(calls) == 5:  # completed first sweep and first role projection
            if change == "roles":
                agent_path = org.root / "org" / "agents" / "dev_agent.md"
                agent_path.write_text(agent_path.read_text().replace("role: worker", "role: manager"))
            elif change == "content":
                row = org.db.fetch_one_readonly("SELECT id,payload FROM audit_log WHERE action='memory_digest_impression'")
                payload = json.loads(row["payload"])
                payload["budget"] = 1
                org.db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
            else:
                # A separate connection is the real concurrent writer.
                org.db._conn.commit()
                other = Database(Path(org.db.fetch_all_readonly("PRAGMA database_list")[0]["file"]))
                try:
                    logger = AuditLogger(other)
                    logger.log_session_start("TASK-new", "dev_agent", "/fixture", session_id="new", invocation_purpose="worker_execution")
                    timestamp = OCT if change == "at-cutoff" else OCT + timedelta(seconds=1) if change == "newer" else FIRST
                    other.execute("UPDATE audit_log SET timestamp=? WHERE task_id='TASK-new'", (timestamp.isoformat(),))
                    other._conn.commit()
                finally:
                    other.close()
        return response
    monkeypatch.setattr(opc, "get", drift)
    monkeypatch.setattr(OpcClient, "from_env", classmethod(lambda cls: opc))
    import datetime as clock_module
    real = clock_module.datetime
    class Fixed(real):
        @classmethod
        def now(cls, tz=None): return OCT
    monkeypatch.setattr(clock_module, "datetime", Fixed)
    args = build_parser().parse_args(["memory", "report", "--org", "alpha", "--agent", "dev_agent", "--json"])
    if change in ("at-cutoff", "newer"):
        args.func(args)
        captured = capsys.readouterr()
        assert captured.err == ""
        assert json.loads(captured.out) == observation_oracle({"dev_agent": numeric_metrics(1, 10, ps=1)})
    else:
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        captured = capsys.readouterr()
        assert exc.value.code == 1 and captured.out == "" and captured.err == "acquisition_unavailable\n"


def test_r11_combined_modes_causal_search_pairs_and_operation_counts(client_with_runtime, monkeypatch, capsys):
    client, org = client_with_runtime
    logger, db = AuditLogger(org.db), org.db
    logger.log_memory_digest_impression(agent="dev_agent", task_id="TASK-0", session_id="r11", digest_ids=["MEM-001", "MEM-002"], budget=1500,
                                       memory_telemetry_version=1, pointer_ids=["MEM-001"], full_body_ids=["MEM-002"])
    logger.log_session_start("TASK-0", "dev_agent", "/fixture", session_id="r11", invocation_purpose="worker_execution")
    logger.log_memory_search(agent="dev_agent", session_id="r11", task_id="TASK-0", memory_ids=["MEM-001", "MEM-002", "MEM-003"], hit_count=4, kb_hit_count=1)
    for mid, source in [("MEM-001", "digest"), ("MEM-002", "digest"), ("MEM-003", "search"), ("MEM-004", "explicit_or_other"), ("MEM-003", "search"), ("MEM-003", "search")]:
        logger.log_memory_read(agent="dev_agent", id=mid, slug="fixture", session_id="r11", task_id="TASK-0", source=source)
    db.execute("UPDATE audit_log SET timestamp=?", (FIRST.isoformat(),))
    expected = observation_oracle({"dev_agent": numeric_metrics(1, 1, 1, 1, 4, 6, 1, 1, 1, 1)}, digest=2, explicit=1,
        repeated=2, read_counts={"dev_agent": {f"MEM-{i:03d}": {"distinct_pairs": 1, "operations": 3 if i == 3 else 1} for i in range(1, 5)}})
    assert_venues(client, org, monkeypatch, capsys, expected)


@pytest.mark.parametrize("variant", ["duplicate", "overlap", "union", "version", "count", "partial", "count-missing"])
def test_r11_r13_exposure_metadata_corruption_is_structural(client_with_runtime, monkeypatch, capsys, variant):
    client, org = client_with_runtime
    emit(org.db)
    row = org.db.fetch_one_readonly("SELECT id,payload FROM audit_log WHERE action='memory_digest_impression'")
    p = json.loads(row["payload"])
    if variant == "duplicate": p["pointer_ids"].append("MEM-001")
    if variant == "overlap": p["full_body_ids"] = ["MEM-001"]
    if variant == "union": p["pointer_ids"].pop()
    if variant == "version": p["memory_telemetry_version"] = True
    if variant == "count": p["digest_count"] = 9
    if variant == "count-missing": del p["digest_count"]
    if variant == "partial": del p["full_body_ids"]
    org.db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(p), row["id"]))
    assert_venues(client, org, monkeypatch, capsys, error_oracle("malformed_impression", now=OCT))


@pytest.mark.parametrize("variant", ["a500", "a501", "sparse", "sparse-duplicates"])
def test_r06_r20_r27_a500_descriptive_ledger_no_ranking_writes(client_with_runtime, monkeypatch, capsys, variant):
    client, org = client_with_runtime
    numeric = {}
    read_counts = {}
    sparse = variant.startswith("sparse")
    for i in range(501 if variant == "a501" else 500):
        agent = "engineering_head" if i < 200 else "dev_agent" if i < 350 else "qa_engineer"
        key = emit(org.db, i, agent)
        active = i == 0 if sparse else i < 100 or 200 <= i < 275 or 350 <= i < 500
        if active:
            append_read(org.db, key, timestamp=FIRST + timedelta(minutes=i, seconds=1))
        if not sparse and i >= 350 and i < 425:
            append_read(org.db, key, "MEM-002", timestamp=FIRST + timedelta(minutes=i, seconds=2))
    for agent, q, x, activated in [("engineering_head", 200, 1 if sparse else 100, 1 if sparse else 100),
                                   ("dev_agent", 150, 0 if sparse else 75, 0 if sparse else 75),
                                   ("qa_engineer", 151 if variant == "a501" else 150, 0 if sparse else 225, 0 if sparse else 150)]:
        numeric[agent] = numeric_metrics(q, q * 10, x=x, r=x, ops=x, activated=activated, ps=q)
        if x:
            read_counts[agent] = {"MEM-001": {"distinct_pairs": activated, "operations": activated}}
            if x > activated:
                read_counts[agent]["MEM-002"] = {"distinct_pairs": x-activated, "operations": x-activated}
    if variant == "sparse-duplicates":
        for _ in range(2):
            append_read(org.db, ("engineering_head", "TASK-0000", "sid0000"))
        numeric["engineering_head"]["read_operations"] = 3
        read_counts["engineering_head"]["MEM-001"]["operations"] = 3
    expected = observation_oracle(numeric, digest=1 if sparse else 400, read_counts=read_counts,
                                 repeated=2 if variant == "sparse-duplicates" else 0)
    memory = org.root / "workspaces" / "dev_agent" / "memory" / "MEM-001-fixture.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("---\nsalience: 50\n---\nKeep observation read-only.\n")
    before = (memory.read_bytes(), memory.stat().st_mtime_ns)
    for _ in range(2):
        assert_venues(client, org, monkeypatch, capsys, expected)
        assert (memory.read_bytes(), memory.stat().st_mtime_ns) == before


def test_r24_roles_unavailable_retains_safe_descriptive_counts(client_with_runtime, monkeypatch, capsys):
    import httpx
    client, org = client_with_runtime
    emit(org.db, read=True)
    original = client.get
    def unavailable(path, **kwargs):
        if path.endswith("/agents"):
            return httpx.Response(500, json={"detail": "fixture"})
        return original(path, **kwargs)
    monkeypatch.setattr(client, "get", unavailable)
    expected = observation_oracle({"dev_agent": numeric_metrics(1, 10, x=1, r=1, ops=1, activated=1, ps=1)},
        digest=1, roles=None, read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}}})
    assert_venues(client, org, monkeypatch, capsys, expected, roles=None)


def test_r25_backend_single_statement_snapshot_two_real_connections(client_with_runtime, monkeypatch, capsys):
    client, org = client_with_runtime
    db = org.db
    db._conn.execute("PRAGMA journal_mode=WAL")
    emit(db, read=True, search=True)
    db._conn.commit()
    other = Database(Path(db.fetch_all_readonly("PRAGMA database_list")[0]["file"]))
    steps, observed, inserted = [0], [], []
    def progress():
        steps[0] += 1
        if steps[0] == 80:
            logger = AuditLogger(other)
            logger.log_session_start("TASK-backdated", "dev_agent", "/fixture", session_id="backdated", invocation_purpose="worker_execution")
            other.execute("UPDATE audit_log SET timestamp=? WHERE task_id='TASK-backdated'", (FIRST.isoformat(),))
            other._conn.commit()
            inserted.append(True)
        return 0
    db._conn.set_trace_callback(observed.append)
    db._conn.set_progress_handler(progress, 1)
    try:
        expected = observation_oracle({"dev_agent": numeric_metrics(1, 10, x=1, r=1, ops=1, activated=1, ps=1)}, digest=1,
                                     read_counts={"dev_agent": {"MEM-001": {"distinct_pairs": 1, "operations": 1}}})
        report = AuditLogger(db).compute_memory_telemetry_report(current_time=OCT, agent_role_map=ROLES)
        assert inserted == [True]
        assert report == expected
        selects = [sql for sql in observed if sql.startswith("SELECT")]
        assert len(selects) == 1
        assert all(action in selects[0] for action in ACTIONS)
    finally:
        db._conn.set_progress_handler(None, 0)
        db._conn.set_trace_callback(None)
        other.close()


def test_r05_exact_short_254_day_read_and_search_ledger(client_with_runtime, monkeypatch, capsys):
    client, org = client_with_runtime
    key = emit(org.db, mode="fallback", read=True)
    a, task, sid = key
    AuditLogger(org.db).log_memory_search(agent=a, task_id=task, session_id=sid, memory_ids=["MEM-002"], hit_count=1, kb_hit_count=0)
    append_read(org.db, key, "MEM-002", "search")
    org.db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = observation_oracle({"dev_agent": numeric_metrics(1, 1, 0, 1, 2, 2, 1, 1, 1, 1)}, now=NOW, first=first, digest=1,
                                 read_counts={"dev_agent": {f"MEM-{i:03d}": {"distinct_pairs": 1, "operations": 1} for i in (1, 2)}})
    assert expected["observation_period"]["days_elapsed"] == 254
    assert_venues(client, org, monkeypatch, capsys, expected, NOW)


def test_r08_r10_selected_org_database_never_reads_beta(client_with_runtime, tmp_path, monkeypatch, capsys):
    client, org = client_with_runtime
    beta = Database(tmp_path / "beta.db")
    try:
        emit(beta, agent="dev_agent", read=True, search=True)
        assert len(beta.fetch_all_readonly("SELECT * FROM audit_log")) == 4
        assert_venues(client, org, monkeypatch, capsys, observation_oracle(first=None))
    finally:
        beta.close()


def test_r24_real_sqlite_row_decoder_failure_is_acquisition_unavailable(tmp_path):
    db = Database(tmp_path / "decoder.db")
    emit(db)
    def bad_decoder(cursor, values):
        raise ValueError("fixture SQLite row decoder failure")
    db._conn.row_factory = bad_decoder
    try:
        with pytest.raises(ReportAcquisitionUnavailable, match="acquisition_unavailable"):
            AuditLogger(db).compute_memory_telemetry_report(current_time=OCT)
    finally:
        db.close()
