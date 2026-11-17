"""Focused founder initial-name observations; see docs/operations/local-ci.md. No launches."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_agent_retirement import fixture, files, PREFIX, rows
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes import agents
from runtime.identities import registry as names
from runtime.orchestrator.context_builder import ContextBuilder


def body(label: str, **kwargs: Any) -> dict[str, Any]:
    return {"display_name": label, "role": "worker", "team": "retiring",
            "description": "Synthetic description", "system_prompt": "Synthetic prompt", **kwargs}


def snapshot(org: OrgState) -> tuple:
    return (files(org.root / "org/agents"), (org.root / "org/teams.yaml").read_bytes(),
            [(t, org.teams.manager_for_team(t)) for t in org.teams.teams()],
            rows(org, "identity_name_owners"), rows(org, "identity_name_claims"))


async def create(client: httpx.AsyncClient, payload: dict) -> httpx.Response:
    return await client.post(f"{PREFIX}/agents", json=payload)


async def success(org: OrgState, client: httpx.AsyncClient, *, manager: bool = False, label: str = "Consultant-Claude") -> None:
    payload = body(label)
    if manager:
        del payload["team"]
        payload.update(role="manager", new_team="consulting")
    before = set(files(org.root / "org/agents"))
    # Observe committed rows through an independent connection during each insert.
    # Neither a default-name owner nor a label-only claim may escape the transaction.
    original = names.transaction
    observed = []

    @contextmanager
    def observe(db: Any) -> Any:
        with original(db) as conn:
            class Connection:
                def __getattr__(self, key: str) -> Any:
                    return getattr(conn, key)

                def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
                    result = conn.execute(sql, parameters)
                    if sql.startswith("INSERT INTO identity_name_"):
                        with sqlite3.connect(db.db_path) as reader:
                            actual = reader.execute("SELECT current_label FROM identity_name_owners WHERE canonical_id='consultant_claude'").fetchall()
                            assert actual == [], actual
                        observed.append(sql)
                    return result
            yield Connection()

    with patch.object(names, "transaction", observe), patch.object(names, "rename_owner", side_effect=AssertionError("second rename")):
        response = await create(client, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["name"] == result["canonical_id"] == "consultant_claude", result
    assert result["addressable_name"] == label, result
    assert set(files(org.root / "org/agents")) - before == {"consultant_claude.md"}
    owner = names.classify(org, "consultant_claude")
    assert (owner.current_label, owner.revision, owner.lifecycle) == (label, 1, "active"), owner
    assert names.classify(org, label).canonical_id == "consultant_claude"
    assert names.refresh_names(org)
    assert names.classify(org, label).current_label == label
    claim = org.db.execute("SELECT permanent FROM identity_name_claims WHERE normalized_name=?", (label.lower(),)).fetchone()
    assert claim[0] == 1
    assert observed, "initial owner/claim insertion not observed"
    listing = await client.get(f"{PREFIX}/identities")
    assert listing.status_code == 200, listing.text
    matches = [r for r in listing.json()["identities"] if r["canonical_id"] == "consultant_claude"]
    assert len(matches) == 1 and matches[0]["addressable_name"] == label, matches
    if manager:
        assert org.teams.manager_for_team("consulting").name == "consultant_claude"


async def refusal(org: OrgState, client: httpx.AsyncClient, label: str, code: str, status: int, **kwargs: Any) -> None:
    before = snapshot(org)
    response = await create(client, body(label, **kwargs))
    assert response.status_code == status, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code") == code, (code, response.text)
    assert snapshot(org) == before, "refused creation changed files/teams/names"


async def conflict(org: OrgState, client: httpx.AsyncClient, *, former: bool = False) -> None:
    await names.rename_owner(org, kind="agent", canonical_id="worker", label="Consultant-Claude", expected_revision=1)
    if former:
        await names.rename_owner(org, kind="agent", canonical_id="worker", label="Advisor", expected_revision=2)
    await refusal(org, client, "cOnSuLtAnT-cLaUdE", "identity_name_unavailable", 409)


async def collision(org: OrgState, client: httpx.AsyncClient, *, terminated: bool = False, claim: bool = False) -> None:
    if claim:
        await names.rename_owner(org, kind="agent", canonical_id="worker", label="consultant_claude", expected_revision=1)
    else:
        response = await create(client, {**body("ignored"), "display_name": None, "name": "consultant_claude"})
        assert response.status_code == 200, response.text
        if terminated:
            response = await client.post(f"{PREFIX}/agents/consultant_claude/retire")
            assert response.status_code == 200, response.text
    response = await create(client, body("Consultant-Claude"))
    assert response.status_code == 200, response.text
    assert response.json()["canonical_id"] == "consultant_claude_2", response.text
    assert names.classify(org, "Consultant-Claude").canonical_id == "consultant_claude_2"
    if terminated:
        assert (org.root / "org/agents/_terminated/consultant_claude.md").exists()


async def legacy(org: OrgState, client: httpx.AsyncClient) -> None:
    response = await create(client, {"name": "legacy_worker", "role": "worker", "team": "retiring",
                                     "description": "Synthetic", "system_prompt": "Synthetic"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["name"] == "legacy_worker", result
    assert result["canonical_id"] == result["addressable_name"] == "legacy_worker", result


async def rollback(org: OrgState, client: httpx.AsyncClient, *, manager: bool = False, stage: str = "sql") -> None:
    before = snapshot(org)
    payload = body("Consultant-Claude")
    if manager:
        del payload["team"]
        payload.update(role="manager", new_team="consulting")
    original = names.transaction
    insert_reached = []

    @contextmanager
    def fail_insert(db: Any) -> Any:
        with original(db) as conn:
            class Connection:
                def __getattr__(self, key: str) -> Any:
                    return getattr(conn, key)

                def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
                    result = conn.execute(sql, parameters)
                    if sql.startswith("INSERT INTO identity_name_claims") and parameters[0] == "consultant-claude":
                        insert_reached.append(True)
                        raise OSError("synthetic initial claim failure")
                    return result
            yield Connection()

    original_replace = agents.os.replace
    def fail_file(src: str, dst: str | Path) -> None:
        if Path(dst).name == "consultant_claude.md":
            insert_reached.append(True)
            raise OSError("synthetic file failure")
        return original_replace(src, dst)

    target = patch.object(names, "transaction", fail_insert) if stage == "sql" else patch.object(agents.os, "replace", fail_file)
    with target:
        try:
            response = await create(client, payload)
        except OSError:
            pass
        else:
            assert response.status_code >= 500, response.text
    assert insert_reached, "fault did not reach intended write"
    assert snapshot(org) == before, "rollback left agent/teams/owner/claim residue"
    assert names.refresh_names(org)
    assert snapshot(org) == before


async def exhausted(org: OrgState, client: httpx.AsyncClient) -> None:
    # Valid former claims belonging to an existing owner occupy every candidate.
    with names.transaction(org.db) as conn:
        for token in ['consultant_claude', *(f'consultant_claude_{i}' for i in range(2, 1001))]:
            conn.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)',
                         (token, 'agent', 'worker', 0, 1))
    await refusal(org, client, 'Consultant-Claude', 'agent_id_exhausted', 409)


async def long_collision(org: OrgState, client: httpx.AsyncClient) -> None:
    response = await create(client, {**body('ignored'), 'display_name': None, 'name': 'a' * 63 + '_'})
    assert response.status_code == 200, response.text
    response = await create(client, body('A' * 63 + '-'))
    assert response.status_code == 200, response.text
    assert response.json()['canonical_id'] == 'a' * 62 + '_2', response.text
    assert names.classify(org, 'a' * 62 + '_2').current_label == 'A' * 63 + '-'


async def contention(org: OrgState, client: httpx.AsyncClient) -> None:
    # Both real HTTP requests propose the same ID before either owns the writer.
    async with org.workflow_authority._async_writer_lock:
        first = asyncio.create_task(create(client, body('A-B_C')))
        second = asyncio.create_task(create(client, body('A_B-C')))
        await asyncio.sleep(0)
    responses = await asyncio.gather(first, second)
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert {r.json()['canonical_id'] for r in responses} == {'a_b_c', 'a_b_c_2'}
    for label in ('A-B_C', 'A_B-C'):
        subject = names.classify(org, label)
        assert subject.current_label == label and subject.revision == 1, subject


async def team_rollback(org: OrgState, client: httpx.AsyncClient, *, manager: bool = False) -> None:
    before = snapshot(org)
    payload = body('Consultant-Claude')
    method = 'add_worker'
    if manager:
        del payload['team']
        payload.update(role='manager', new_team='consulting')
        method = 'add_team'
    original = getattr(org.teams, method)
    reached = []
    def fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        reached.append(True)
        raise OSError('synthetic post-team-save failure')
    with patch.object(org.teams, method, fail):
        try:
            response = await create(client, payload)
        except OSError:
            pass
        else:
            assert response.status_code >= 500, response.text
    assert reached
    assert snapshot(org) == before, 'team write failure left partial enrollment'


async def openapi(org: OrgState, client: httpx.AsyncClient) -> None:
    schema = client._transport.app.openapi()
    request = schema['components']['schemas']['FounderCreateAgentBody']
    assert {'name', 'display_name'} <= set(request['properties'])
    assert 'name' not in request['required']
    receipt = schema['components']['schemas']['FounderCreateAgentReceipt']
    assert {'name', 'canonical_id', 'addressable_name', 'team', 'role'} == set(receipt['required'])


async def bootstrap_failure(org: OrgState, client: httpx.AsyncClient) -> None:
    with patch.object(ContextBuilder, 'ensure_workspace_ready', side_effect=OSError('synthetic bootstrap failure')):
        try:
            response = await create(client, body('Consultant-Claude'))
        except OSError:
            pass
        else:
            assert response.status_code >= 500, response.text
    assert (org.root / 'org/agents/consultant_claude.md').is_file()
    subject = names.classify(org, 'Consultant-Claude')
    assert (subject.canonical_id, subject.current_label, subject.revision) == ('consultant_claude', 'Consultant-Claude', 1)
    assert 'consultant_claude' in org.teams.manager_for_team('retiring').workers


async def run() -> None:
    cases = [
        ("openapi-additive-fields", lambda o,c: openapi(o,c)),
        ("bootstrap-retains-both", lambda o,c: bootstrap_failure(o,c)),
        ("readable-worker-atomic", lambda o,c: success(o,c)),
        ("readable-manager-atomic", lambda o,c: success(o,c,manager=True)),
        ("id-shaped-label-permanent", lambda o,c: success(o,c,label="consultant_claude")),
        ("case-insensitive-current-conflict", lambda o,c: conflict(o,c)),
        ("case-insensitive-former-conflict", lambda o,c: conflict(o,c,former=True)),
        ("reserved-founder", lambda o,c: refusal(o,c,"Founder","identity_name_reserved",409)),
        ("reserved-agent-id", lambda o,c: refusal(o,c,"WORKER","identity_name_reserved",409)),
        *[(f"invalid-{i}", lambda o,c,label=label: refusal(o,c,label,"invalid_identity_label",422))
          for i,label in enumerate(("", "Two Words", "中文", "_leading", "9leading", "A"*65, " trailing", "trailing "))],
        ("ambiguous-fields", lambda o,c: refusal(o,c,"Consultant-Claude","agent_name_source_required",422,name="explicit")),
        ("id-file-collision", lambda o,c: collision(o,c)),
        ("id-claim-collision", lambda o,c: collision(o,c,claim=True)),
        ("terminated-id-never-reused", lambda o,c: collision(o,c,terminated=True)),
        ("bounded-id-exhaustion", lambda o,c: exhausted(o,c)),
        ("long-id-suffix-bound", lambda o,c: long_collision(o,c)),
        ("concurrent-id-recheck", lambda o,c: contention(o,c)),
        ("worker-team-rollback", lambda o,c: team_rollback(o,c)),
        ("manager-team-rollback", lambda o,c: team_rollback(o,c,manager=True)),
        ("legacy-name-only", lambda o,c: legacy(o,c)),
        ("worker-sql-rollback", lambda o,c: rollback(o,c)),
        ("manager-sql-rollback", lambda o,c: rollback(o,c,manager=True)),
        ("worker-file-rollback", lambda o,c: rollback(o,c,stage="file")),
        ("manager-file-rollback", lambda o,c: rollback(o,c,manager=True,stage="file")),
    ]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', choices=[case[0] for case in cases])
    selected = parser.parse_args().case
    if selected:
        cases = [case for case in cases if case[0] in selected]
    failed = []
    with tempfile.TemporaryDirectory(prefix="founder-name-") as scratch, \
            patch.object(ContextBuilder, "ensure_workspace_ready"), \
            patch.object(ContextBuilder, "create_agent_dirs"):
        for index, (case, check) in enumerate(cases):
            try:
                async with fixture(Path(scratch) / str(index)) as (org, client):
                    await check(org, client)
            except Exception as exc:
                failed.append(case)
                print(f"FAIL {case}: {type(exc).__name__}: {exc}", flush=True)
            else:
                print(f"PASS {case}", flush=True)
    assert not failed, f"{len(failed)} failed cases: {failed}"
    print(f"PASS {len(cases)} founder creation cases; real ASGI/SQLite, no daemon lifespan/providers")


if __name__ == "__main__":
    asyncio.run(run())
