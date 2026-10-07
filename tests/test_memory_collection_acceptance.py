"""G1 strict-carrier/SELF mechanics: parsing never supplies installed authority.

INLINE v24 — test_own_reference_uses_exact_admitted_row, every supplied-ID
variant; test_closed_carrier_rejects_ambiguous_json, every syntax variant;
test_external_reference_id_is_mandatory, every ID variant;
test_snapshot_and_unattached_transition_cannot_grant_authority:
1. Observe unchanged persisted summaries, exact own row ID, explicit refusal,
   and zero epoch audit writes at the actual Database boundary.
2. Credible regressions: coerce/discard supplied IDs; choose a newer row;
   accept duplicate keys/extra fields/nonfinite JSON; treat a carrier as health.
3. Existing collection-contract tests own live census/source observation, but
   do not parse an opt-in admitted summary or own the SELF omission exception.
4. No test-only production seam. Real production DDL/admission and raw audit
   inspection are used. These mechanical cases confer no installed acceptance.
"""
from __future__ import annotations

import copy
import json

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.memory_collection import (
    AcceptanceUnavailable, COLLECTION_TAG, normalize_own_reference, parse_acceptance,
)
from runtime.models import TaskRecord
from tests.test_memory_collection_contract import collection_org, observation_client  # noqa: F401


def carrier(*, external_id=1):
    identity = {"source_root": "/source", "runtime_root": "/runtime", "org_root": "/runtime/orgs/test",
                "package_version": "0.1.0", "python": {"executable":"/python","version":"3.14","implementation":"cpython","cache_tag":"cpython-314"},
                "loaded_code": [{"module":"runtime.sample","qualname":"sample","origin":"/source/sample.py","loaded_sha256":"a"*64,"source_code_sha256":"a"*64}],
                "files": [{"path":"/source/sample.py","sha256":"a"*64}],
                "teams_sha256": "a" * 64, "cohort": [{"agent":"qa","role":"worker","team":"engineering","executor":"claude","model":None}],
                "profiles": [{"name":"claude","kind":"builtin","workspace_adapter_id":"claude","command_adapter_id":"claude",
                              "readiness_marker_fragment":None,"model_arg_sha256":"a"*64,"provider":{"path":"/provider","sha256":"a"*64},"adapter":None}],
                "backend": {"mode":"legacy","name":None,"version":None,"capabilities":None}}
    return {"contract_version": 1, "kind": "manager_acceptance", "org": "test",
            "operational_root_task_id": "TASK-001", "health_definition_sha256": "b" * 64,
            "release_manifest_sha256": "c" * 64, "installed_identity": identity,
            "cohort": copy.deepcopy(identity["cohort"]), "applicable_paths": ["claude:legacy:none"],
            "synthetic_task_ids": ["TASK-010", "TASK-011"],
            "probe_receipts": [{"path": "claude:legacy:none", "job_id": "JOB-001",
                                "script_sha256": "d" * 64, "output_sha256": "e" * 64,
                                "root": {"org": "test", "task_id": "TASK-010", "agent": "qa", "runtime_session_id": "sess-root"},
                                "child": {"org": "test", "task_id": "TASK-011", "agent": "qa", "runtime_session_id": "sess-child"}}],
            "result_ref": {"task_id": "TASK-001", "agent": "manager", "runtime_session_id": "sess-own"},
            "action": "accept", "qa_ref": {"task_id": "TASK-002", "agent": "qa", "runtime_session_id": "sess-qa", "result_id": external_id},
            "predecessor_epoch_id": None, "reason": "candidate only"}


def summary(value):
    return COLLECTION_TAG + json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def admit(db, value, *, own_sid="sess-own"):
    task_id, agent = value["result_ref"]["task_id"], value["result_ref"]["agent"]
    db.insert_task(TaskRecord(id=task_id, brief="mechanics only", assigned_agent=agent))
    assert db.admit_task_completion_callback(task_id=task_id, agent=agent, session_id=own_sid,
                                           output_summary=summary(value), confidence_score=90)
    return db.get_task_results(task_id)[0]


@pytest.mark.parametrize("kind", ["manager_acceptance", "installed_qa"])
@pytest.mark.parametrize("supplied", ["absent", "matching", "wrong", None, True, "2", 0, -1])
def test_own_reference_uses_exact_admitted_row(tmp_path, kind, supplied):
    db = Database(tmp_path / "case.db")
    try:
        db.insert_task(TaskRecord(id="TASK-999", brief="unrelated"))
        assert db.admit_task_completion_callback(task_id="TASK-999", agent="other", session_id="sess-unrelated",
                                               output_summary="untagged history", confidence_score=80)
        value = carrier()
        if kind == "installed_qa":
            for key in ("action", "qa_ref", "predecessor_epoch_id", "reason"):
                value.pop(key)
            value.update(kind=kind, venue="installed")
        if supplied != "absent":
            value["result_ref"]["result_id"] = 2 if supplied == "matching" else 1 if supplied == "wrong" else supplied
        row = admit(db, value)
        assert row["id"] == 2
        original = row["output_summary"]
        if supplied in ("absent", "matching"):
            normalized = normalize_own_reference(parse_acceptance(original), row)
            assert normalized["result_ref"]["result_id"] == 2
            assert normalized["published_at"] == row["created_at"]
            wrong_row = {**row, "session_id": "sess-provider-resume"}
            with pytest.raises(AcceptanceUnavailable):
                normalize_own_reference(parse_acceptance(original), wrong_row)
        else:
            with pytest.raises(AcceptanceUnavailable):
                normalize_own_reference(parse_acceptance(original), row)
        assert db.get_task_results("TASK-001")[0]["output_summary"] == original
        assert db.get_audit_logs_by_action("memory_collection_epoch_started") == []
    finally:
        db.close()


@pytest.mark.parametrize("fault", ["duplicate", "nonfinite", "extra", "bool_version", "two_tags", "inline_tag", "noncanonical"])
def test_closed_carrier_rejects_ambiguous_json(fault):
    value = carrier()
    text = summary(value)
    if fault == "duplicate":
        text = text.replace('"contract_version":1', '"contract_version":1,"contract_version":1')
    elif fault == "nonfinite":
        text = text.replace('"contract_version":1', '"contract_version":NaN')
    elif fault == "extra":
        value["healthy"] = True
        text = summary(value)
    elif fault == "bool_version":
        value["contract_version"] = True
        text = summary(value)
    elif fault == "two_tags":
        text += "\n" + text
    elif fault == "inline_tag":
        text = "PASS " + text
    else:
        text = COLLECTION_TAG + json.dumps(value)
    with pytest.raises(AcceptanceUnavailable):
        parse_acceptance(text)


@pytest.mark.parametrize("result_id", [None, True, "1", 0, -1, "absent"])
def test_external_reference_id_is_mandatory(result_id):
    value = carrier(external_id=result_id)
    if result_id == "absent":
        value["qa_ref"].pop("result_id")
    with pytest.raises(AcceptanceUnavailable):
        parse_acceptance(summary(value))


def test_snapshot_and_unattached_transition_cannot_grant_authority(tmp_path):
    db = Database(tmp_path / "case.db")
    try:
        row = admit(db, carrier())
        before = db.fetch_one_readonly("SELECT total_changes()")[0]
        evidence = db.read_memory_collection_evidence()
        assert [item["id"] for item in evidence["task_results"]] == [row["id"]]
        assert db.fetch_one_readonly("SELECT total_changes()")[0] == before
        with pytest.raises(AcceptanceUnavailable, match="context_missing"):
            db.append_memory_collection_transition(result_row_id=row["id"])
        assert db.get_audit_logs_by_action("memory_collection_epoch_started") == []
        assert db.get_audit_logs_by_action("memory_collection_invalidated") == []
    finally:
        db.close()


def test_original_task_records_roundtrip_through_protected_http(observation_client):
    """PRE source acquisition, not installed or epoch acceptance.

    INLINE v24: observes byte-exact original briefs/generated IDs and zero
    read writes through the actual protected task route. Credible regression:
    truncate the collection page to its first row. The older serving-view
    tests do not acquire task originals through the new HTTP adapter. No
    test-only production seam or injected SessionTracker registration.
    """
    from runtime.infrastructure.memory_collection import _collection_pages
    org, client = observation_client
    created = []
    for brief in ("exact first\nwhole brief", "exact second\nwhole brief"):
        response = client.post("/api/v1/orgs/test/tasks", json={
            "team": "engineering", "owner": "dev_agent", "brief": brief,
        })
        assert response.status_code == 200, response.text
        created.append((response.json()["task_id"], brief))
    before = org.db.fetch_one_readonly("SELECT total_changes()")[0]
    acquired = _collection_pages(client, "test", tasks=True)
    assert [(row["id"], row["brief"]) for row in acquired] == sorted(created)
    assert all(row["parent_task_id"] is None for row in acquired)
    assert org.db.fetch_one_readonly("SELECT total_changes()")[0] == before
    assert org.db.get_audit_logs_by_action("memory_collection_epoch_started") == []


@pytest.mark.parametrize("attributed", [False, True])
@pytest.mark.parametrize("fault", [False, True])
def test_both_result_log_arms_reconcile_exact_row_after_audit(observation_client, monkeypatch, attributed, fault):
    """Source wiring only: the authentic validator refuses untagged evidence.

    INLINE v24, both arms and observer fault: observes persisted completion
    payload/order, exact result ID reconciliation and unchanged return. A
    removed/reordered observer call loses this contract; existing v2 logging
    tests own attribution, not the new post-log reconciliation order. No
    production test seam; the observer spy either invokes the actual refusal
    path or raises a bounded observation failure, never grants authority.
    """
    from types import SimpleNamespace
    from runtime.models import CompletionReport
    from runtime.orchestrator.executors import ExecutorResult
    org, _ = observation_client
    task_id = org.orchestrator.create_task("log wiring only")
    org.db.update_task(task_id, assigned_agent="dev_agent")
    assert org.db.admit_task_completion_callback(
        task_id=task_id, agent="dev_agent", session_id="sess-log", output_summary="untagged",
        confidence_score=80,
    )
    row_id = org.db.get_task_results(task_id)[0]["id"]
    report = CompletionReport(task_id=task_id, agent="dev_agent", status="completed",
                              output_summary="untagged", confidence=80)
    if attributed:
        # Isolate the existing attributed arm. This supplies no policy or epoch
        # authority; all new collection predicates still execute and refuse.
        monkeypatch.setattr(org.db, "get_authority_policy_v2_attempt_for_result",
                            lambda result_id: SimpleNamespace(manager_session_id="sess-log"))
    observed = []
    real = org.memory_collection.reconcile_acceptance
    def reconcile(result_id):
        audit = org.db.get_audit_logs_by_action("completion_report")
        observed.append((result_id, len(audit)))
        if fault:
            raise RuntimeError("observation fault")
        return real(result_id)
    monkeypatch.setattr(org.memory_collection, "reconcile_acceptance", reconcile)
    answer = org.orchestrator._log_step_result(
        task_id, ExecutorResult(success=True, duration_seconds=0, returncode=0, session_id="sess-log"), report,
        result_row_id=row_id,
    )
    expected = report.model_dump()
    if attributed:
        expected.update(_result_row_id=row_id, _result_session_id="sess-log")
    assert answer is None and observed == [(row_id, 1)]
    audit = org.db.get_audit_logs_by_action("completion_report")
    assert len(audit) == 1 and audit[0]["payload"] == expected
    assert org.db.get_audit_logs_by_action("memory_collection_epoch_started") == []
    assert org.db.get_task_results(task_id)[0]["output_summary"] == "untagged"


# The external provider is the only stand-in. This command executes the real
# task API, dispatcher, provider bootstrap, canonical memory CLI and jobs runner.
_G1_JOB = r'''
import json, sys, time, hashlib, importlib.metadata
from pathlib import Path
import runtime
from cli.client.client import OpcClient
client = OpcClient.from_env()
base = '/api/v1/orgs/alpha/'
def get(path, **params):
    response = client.get(base + path, params=params)
    response.raise_for_status()
    return response.json()
declared = json.loads(sys.argv[1])
slots = declared if isinstance(declared, list) else [declared]
config = json.loads(Path(__import__('os').environ['G1_SOURCE_CONFIG']).read_text())
retry_mode = config['mode'] in ('retry-same-parent', 'retry-recorded')
returned = []
for slot in slots:
    response = client.post(base + 'tasks', json={'team': slot['root']['team'], 'owner': slot['root']['agent'], 'brief': slot['root']['brief']})
    response.raise_for_status()
    root_id = response.json()['task_id']
    end = time.monotonic() + 35
    while True:
        detail = get('tasks/' + root_id)
        if detail['task']['status'] in ('completed', 'failed', 'superseded'):
            assert detail['task']['status'] == ('superseded' if config['mode']=='retry-recorded' else 'completed'), detail
            break
        assert time.monotonic() < end, detail
        time.sleep(.05)
    page = get('tasks', limit=200)
    assert page['next_cursor'] is None
    children = [t for t in page['tasks'] if t['parent_task_id'] == root_id]
    original = [t for t in children if t['brief']==slot['child']['brief'] and t['revisit_of_task_id'] is None]
    assert len(original) == 1, children
    child_id = original[0]['task_id']
    returned += [root_id, child_id]
    if retry_mode:
        end = time.monotonic()+35
        while True:
            page=get('tasks',limit=200)
            assert page['next_cursor'] is None
            retries=[t for t in page['tasks'] if t['revisit_of_task_id']==child_id]
            if len(retries)==1 and retries[0]['status']=='completed':
                retry=retries[0]
                parent=get('tasks/'+retry['parent_task_id'])['task']
                if parent['status']=='completed':break
            assert time.monotonic()<end,(page,retries)
            time.sleep(.05)
        assert original[0]['status']=='failed',original
        returned.append(retry['task_id'])
        if retry['parent_task_id']!=root_id:returned.append(retry['parent_task_id'])
rows = get('audit', limit=5000)['entries']
operations = []
for task_id in returned:
    identities = sorted([r for r in rows if r['action'] == 'memory_runtime_identity' and r['task_id'] == task_id], key=lambda r:r['id'])
    sid = identities[0]['payload']['session_id']
    operations += [r['id'] for r in rows if r['action'] in ('memory_digest_impression','memory_read','memory_search') and r['payload'].get('session_id') == sid]
# The production observation deliberately refuses transient contention. The
# actual SOURCE command obtains a fresh complete observation; it never copies
# a prior view or retries sticky health failure as though that were healthy.
end = time.monotonic() + 5
while True:
    view = get('audit', action='memory_collection_seal', limit=1)['memory_collection_observation']
    if view['observation_error'] not in ('observer_busy', 'observation_database_busy', 'observation_moving', 'observation_pending'):
        break
    assert time.monotonic() < end, view
    time.sleep(.05)
identity = view['installed_identity']
assert identity is not None and view['observation_error'] is None, view
# Measure the actual owned job process and imported source; do not copy the
# daemon's interpreter/version/file hashes into a favorable transcript.
measured_cli = {'source_root':str(Path(runtime.__file__).resolve().parents[1]),
    'python':{'executable':str(Path(sys.executable).resolve()),'version':sys.version,
              'implementation':sys.implementation.name,'cache_tag':sys.implementation.cache_tag},
    'package_version':importlib.metadata.version('happyranch'),
    'files':[{'path':row['path'],'sha256':hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()}
             for row in identity['files']]}
assert measured_cli == {k:identity[k] for k in ('source_root','python','package_version','files')}
print(json.dumps({'run_id':sys.argv[2] if len(sys.argv)>2 else 'finite-g1', 'returned_task_ids': sorted(returned), 'operation_audit_ids':sorted(operations),
    'serving_observation':view, 'cli_identity': measured_cli}, sort_keys=True, separators=(',',':')))
print('canonical source probe complete', file=sys.stderr)
'''

_G1_HEALTH_JOB = r'''
import hashlib, importlib.metadata, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
import runtime
from cli.client.client import OpcClient
request = json.loads(sys.argv[1])
if request['gate']:
    ready = Path(request['gate'] + '.ready')
    staged = Path(str(ready) + '.tmp')
    staged.write_text(json.dumps({'pid':os.getpid()}))
    staged.replace(ready)
    deadline = time.monotonic() + 25
    while not Path(request['gate']).exists():
        assert time.monotonic() < deadline, 'owned observation gate expired'
        time.sleep(.05)
if request['fault'] == 'job-error':
    print('source observation command failed before measurement', file=sys.stderr)
    raise SystemExit(7)
if request['fault'] == 'output-cap':
    chunk=('unfulfilled health output '*50000).encode()
    for _ in range(50):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    time.sleep(10)
    raise AssertionError('actual output cap did not terminate owned job')
client = OpcClient.from_env()
response = client.get('/api/v1/orgs/alpha/audit', params={'action':'memory_collection_epoch_started','limit':1})
response.raise_for_status()
epoch = response.json()['entries'][0]
assert (epoch['payload']['epoch_id'], epoch['timestamp']) == (request['epoch_id'], request['started_at'])
cli = str(Path(sys.executable).parent / 'happyranch')
reports = []
wall_observed_at = datetime.now(timezone.utc).isoformat()
observed_at = request.get('source_observation_at') or wall_observed_at
for suffix in (['--json'], []):
    argv = [cli, 'memory', 'report', '--org', 'alpha', '--agent', 'qa_engineer', *suffix]
    if request.get('source_observation_at'):
        # SOURCE-only controlled observation time. Run the actual canonical
        # installed console entry; no product flag or acceptance timestamp.
        clock_launcher = "import datetime,runpy,sys; real=datetime.datetime; target=real.fromisoformat(sys.argv[1]); " + \
            "Clock=type('SourceObservationClock',(real,),{'now':classmethod(lambda cls,tz=None: target if tz is not None else target.replace(tzinfo=None))}); " + \
            "target=Clock.fromisoformat(sys.argv[1]); datetime.datetime=Clock; sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        argv = [sys.executable, '-c', clock_launcher, observed_at, *argv]
    attempts = []
    deadline = time.monotonic() + 5
    while True:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        attempts.append({'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                         'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
                         'stderr_sha256':hashlib.sha256(result.stderr.encode()).hexdigest()})
        # A moving/busy read may refuse without a report. Acquire a new whole
        # report, retaining the refusal; a rendered unhealthy report or any
        # other error is never retried into a favorable health observation.
        if (result.returncode != 1 or result.stdout != '' or result.stderr != 'acquisition_unavailable\n'
                or time.monotonic() >= deadline):
            break
        time.sleep(.05)
    reports.append({'argv':argv,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                    'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
                    'stderr_sha256':hashlib.sha256(result.stderr.encode()).hexdigest(),'attempts':attempts})
identity = request['identity']
measured = {'source_root':str(Path(runtime.__file__).resolve().parents[1]),
    'python':{'executable':str(Path(sys.executable).resolve()),'version':sys.version,
              'implementation':sys.implementation.name,'cache_tag':sys.implementation.cache_tag},
    'package_version':importlib.metadata.version('happyranch'),
    'files':[{'path':row['path'],'sha256':hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()}
             for row in identity['files']]}
assert measured == {key:identity[key] for key in measured}
print(json.dumps({'scope':'SOURCE ONLY','epoch_id':request['epoch_id'],'started_at':request['started_at'],
    'observed_at':observed_at,'wall_observed_at':wall_observed_at,
    'clock_scope':'controlled SOURCE clock' if request.get('source_observation_at') else 'wall clock',
    'agent':os.environ.get('HAPPYRANCH_AGENT'),'cwd':str(Path.cwd()),
    'runtime_import':runtime.__file__,'script_file_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'cli_identity':measured,'reports':reports},sort_keys=True,separators=(',',':')))
print('source health observation complete; original epoch unchanged', file=sys.stderr)
'''

_G1_PROVIDER = r'''
import json, os, re, subprocess, sys, time, hashlib
from pathlib import Path
from cli.client.client import OpcClient
from runtime.infrastructure.memory_collection import COLLECTION_TAG, HEALTH_DEFINITION_SHA256, _hash_metadata, parse_acceptance
config = json.loads(Path(os.environ['G1_SOURCE_CONFIG']).read_text())
if config['mode'] == 'natural-boundaries':
    # SOURCE diagnostics only. Preserve the actual commands, streams, exits and
    # launch intervals without changing subprocess arguments or outcomes.
    import atexit, traceback
    diagnostic_base = Path(os.environ['G1_SOURCE_CONFIG'] + '.' + os.environ['HAPPYRANCH_RUNTIME_SESSION_ID'])
    diagnostic = {'session_id':os.environ['HAPPYRANCH_RUNTIME_SESSION_ID'],
        'argv':sys.argv,'cwd':str(Path.cwd()),'python':sys.executable,
        'version':sys.version,'started_ns':time.monotonic_ns(),'commands':[]}
    diagnostic_stderr = sys.stderr
    def recorder_error(exc):
        message='SOURCE recorder failure: '+repr(exc)+'\n'
        try:
            diagnostic_stderr.write(message)
            diagnostic_stderr.flush()
        except Exception:
            pass
        # Retain the recorder's own error without recursing through SourceTee.
        try:
            stream_file=getattr(sys.stderr,'file',None)
            if stream_file is not None:
                stream_file.write(message)
                stream_file.flush()
            with Path(str(diagnostic_base)+'.recorder-error').open('a') as errors:
                errors.write(message)
        except Exception:
            pass  # The real stderr above still reports this diagnostic loss.
    class SourceTee:
        def __init__(self, stream, suffix):
            self.stream = stream
            self.file = None
            try:
                self.file = Path(str(diagnostic_base) + suffix).open('w')
            except Exception as exc:
                recorder_error(exc)
        def write(self, text):
            try:
                if self.file is not None:
                    self.file.write(text)
                    self.file.flush()
            except Exception as exc:
                recorder_error(exc)
            return self.stream.write(text)
        def flush(self):
            try:
                if self.file is not None:
                    self.file.flush()
            except Exception as exc:
                recorder_error(exc)
            self.stream.flush()
    sys.stdout = SourceTee(sys.stdout, '.stdout')
    sys.stderr = SourceTee(sys.stderr, '.stderr')
    def save_diagnostic():
        try:
            Path(str(diagnostic_base) + '.provider.json').write_text(json.dumps(diagnostic,sort_keys=True,default=str))
        except Exception as exc:
            recorder_error(exc)
    original_run = subprocess.run
    def source_run(*args, **kwargs):
        command = {'argv':args[0] if args else kwargs['args'], 'started_ns':time.monotonic_ns(),
                   'cwd':str(kwargs.get('cwd',Path.cwd())), 'timeout':kwargs.get('timeout')}
        diagnostic['commands'].append(command)
        save_diagnostic()
        try:
            completed = original_run(*args, **kwargs)
        except BaseException as exc:
            command.update(ended_ns=time.monotonic_ns(),exception=traceback.format_exc(),
                           stdout=getattr(exc,'stdout',None),stderr=getattr(exc,'stderr',None))
            save_diagnostic()
            raise
        command.update(ended_ns=time.monotonic_ns(),exit=completed.returncode,
                       stdout=completed.stdout,stderr=completed.stderr)
        save_diagnostic()
        return completed
    subprocess.run = source_run
    def finish_diagnostic():
        diagnostic['ended_ns'] = time.monotonic_ns()
        save_diagnostic()
    atexit.register(finish_diagnostic)
    save_diagnostic()
prompt = sys.stdin.read()
task_id = re.search(r'task_id: (TASK-[0-9]+)', prompt).group(1)
sid = os.environ['HAPPYRANCH_RUNTIME_SESSION_ID']
if config['mode'] == 'natural-boundaries':
    diagnostic['task_id'] = task_id
    save_diagnostic()
client = OpcClient.from_env()
base = '/api/v1/orgs/alpha/'
def get(path, **params):
    response = client.get(base + path, params=params)
    response.raise_for_status()
    return response.json()
detail = get('tasks/' + task_id)
task = detail['task']
agent = task['assigned_agent']
brief = json.loads(task['brief'])
if brief.get('operational_root') or brief.get('probe'):
    page = get('tasks', limit=200)
    assert page['next_cursor'] is None
    detail['children'] = [t for t in page['tasks'] if t['parent_task_id'] == task_id]
cli = str(Path(sys.executable).parent / 'happyranch')
result = {'task_id':task_id, 'session_id':sid, 'agent':agent, 'status':'completed', 'confidence':90, 'summary':'Source fixture; no installed acceptance'}
def envelope(kind, qa_ref=None):
    # The production observation deliberately refuses transient contention. The
    # actual SOURCE command obtains a fresh complete observation; it never copies
    # a prior view or retries sticky health failure as though that were healthy.
    end = time.monotonic() + 5
    while True:
        view = get('audit', action='memory_collection_seal', limit=1)['memory_collection_observation']
        if view['observation_error'] not in ('observer_busy', 'observation_database_busy', 'observation_moving', 'observation_pending'):
            break
        assert time.monotonic() < end, view
        time.sleep(.05)
    identity = view['installed_identity']
    assert identity is not None and view['observation_error'] is None, view
    job_id = config['job_id'] if 'job_id' in config else json.loads(Path(config['job_path']).read_text())['id']
    job = get('jobs/' + job_id)
    output = get('jobs/' + job_id + '/output', stream='both', max_bytes=10485760)
    transcript = json.loads(output['stdout'])
    rows = get('audit', limit=5000)['entries']
    tuples = []
    for tid in transcript['returned_task_ids']:
        own = sorted([r for r in rows if r['action']=='memory_runtime_identity' and r['task_id']==tid], key=lambda r:r['id'])[0]
        tuples.append({'org':'alpha','task_id':tid,'agent':own['agent'],'runtime_session_id':own['payload']['session_id']})
    receipts = []
    for slot in config['plan']['slots']:
        root_tuple = next(t for t in tuples if t['agent']==slot['root']['agent']
            and get('tasks/'+t['task_id'])['task']['brief']==slot['root']['brief'])
        child_tuple = next(t for t in tuples if t['agent']==slot['child']['agent']
            and get('tasks/'+t['task_id'])['task']['parent_task_id']==root_tuple['task_id']
            and get('tasks/'+t['task_id'])['task']['brief']==slot['child']['brief'])
        receipts.append({'path':slot['path'], 'root':root_tuple, 'child':child_tuple,
            'job_id':job_id,'script_sha256':hashlib.sha256(job['script_text'].encode()).hexdigest(),'output_sha256':_hash_metadata(output)})
    root = config['root_id'] if 'root_id' in config else get('tasks/'+task_id)['task']['parent_task_id']
    value = {'contract_version':1, 'kind':kind, 'org':'alpha', 'operational_root_task_id':root,
        'health_definition_sha256':HEALTH_DEFINITION_SHA256,'release_manifest_sha256':_hash_metadata(identity['files']),
        'installed_identity':identity, 'cohort':identity['cohort'], 'applicable_paths':sorted(receipt['path'] for receipt in receipts),
        'synthetic_task_ids':sorted(transcript['returned_task_ids']), 'probe_receipts':sorted(receipts,key=lambda receipt:receipt['path']),
        'result_ref':{'task_id':task_id,'agent':agent,'runtime_session_id':sid}}
    if kind == 'installed_qa':
        value['venue'] = 'installed'
    else:
        value.update(action='accept',qa_ref=qa_ref,predecessor_epoch_id=None,reason='Source fixture only')
    return COLLECTION_TAG + json.dumps(value, sort_keys=True,separators=(',',':'),ensure_ascii=False)
if brief.get('operational_root'):
    if not detail['children']:
        result['decision'] = {'action':'delegate','agent':config['qa_agent'],'prompt':json.dumps(config['plan'],sort_keys=True,separators=(',',':'))}
    elif len(detail['children']) == 1:
        child_id = detail['children'][0]['task_id']
        qa = get('tasks/'+child_id)['results'][0]
        config['root_id'] = task_id
        result['summary'] = envelope('manager_acceptance', {'task_id':child_id,'agent':qa['agent'],'runtime_session_id':qa['session_id'],'result_id':qa['id']})
        if config['mode'] in ('invalidate', 'equivalent', 'conflict', 'reset'):
            result['decision'] = {'action':'delegate','agent':'dev_agent','prompt':'{"natural":true}'}
        else:
            result['decision'] = {'action':'done','summary':'source candidate only'}
    elif config['mode']=='reset' and len(detail['children'])==2:
        # Fresh QA owns a different immutable prior plan/command and genuinely
        # new ROOT+CHILD/job/result records. Never reuse an old PASS for reset.
        config['plan']=config['reset_plan']
        config.pop('job_id',None)
        Path(os.environ['G1_SOURCE_CONFIG']).write_text(json.dumps(config))
        result['decision']={'action':'delegate','agent':'qa_engineer',
                            'prompt':json.dumps(config['plan'],sort_keys=True,separators=(',',':'))}
    elif config['mode']=='reset':
        matches=[child['task_id'] for child in detail['children'] if child['assigned_agent']=='qa_engineer'
                 and child['brief']==json.dumps(config['plan'],sort_keys=True,separators=(',',':'))]
        assert len(matches)==1,matches
        child_id=matches[0]
        qa=get('tasks/'+child_id)['results'][0]
        config['root_id']=task_id
        value=parse_acceptance(envelope('manager_acceptance',
            {'task_id':child_id,'agent':qa['agent'],'runtime_session_id':qa['session_id'],'result_id':qa['id']}))
        epoch=get('audit',action='memory_collection_epoch_started',limit=1)['entries'][0]
        value['predecessor_epoch_id']=epoch['payload']['epoch_id']
        value['reason']='Fresh source-only exact-predecessor reset'
        result['summary']=COLLECTION_TAG+json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        result['decision']={'action':'done','summary':'source reset candidate only'}
    else:
        gate = Path(config['control_gate'])
        Path(str(gate)+'.ready').touch()
        end = time.monotonic()+15
        while not gate.exists():
            assert time.monotonic()<end, 'control barrier expired'
            time.sleep(.02)
        old = next(parse_acceptance(row['output_summary']) for row in detail['results'] if COLLECTION_TAG in row['output_summary'])
        old['result_ref'] = {'task_id':task_id,'agent':agent,'runtime_session_id':sid}
        if config['mode'] == 'invalidate':
            epoch = get('audit',action='memory_collection_epoch_started',limit=1)['entries'][0]
            old.update(action='invalidate',predecessor_epoch_id=epoch['payload']['epoch_id'],reason='Source withdrawal after sticky loss')
        elif config['mode'] == 'conflict':
            old['health_definition_sha256'] = '0'*64
        result['summary'] = COLLECTION_TAG+json.dumps(old,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        result['decision'] = {'action':'done','summary':'source control only'}
elif 'health_check' in brief:
    check = brief['health_check']
    response = client.post(base+'jobs/submit',json={'task_id':task_id,'session_id':sid,'title':'source-only post-epoch health',
        'script':check['script'],'interpreter':'bash','review_required':False,'persistent':False,'max_runtime_seconds':40})
    response.raise_for_status()
    job_id = response.json()['id']
    Path(check['job_path']).write_text(json.dumps(response.json()))
    deadline = time.monotonic()+40
    while True:
        job = get('jobs/'+job_id)
        if job['status'] in ('completed','failed'):
            if any(row['action'] in ('job_run_completed','job_run_failed') and row['payload'].get('script_request_id')==job_id
                   for row in get('audit',limit=5000)['entries']):
                break
        assert time.monotonic()<deadline, job
        time.sleep(.2)
    result['summary'] = 'Source-only health job '+job_id+'; actual exit '+str(job['exit_code'])+'; no production health duty fulfilled.'
    result['decision'] = {'action':'done','summary':'source-only health observation recorded'}
elif 'slots' in brief:
    response = client.post(base+'jobs/submit',json={'task_id':task_id,'session_id':sid,'title':'finite G1 source','script':brief['command']['script_text'],
        'interpreter':brief['command']['interpreter'],'review_required':False,'persistent':False,'max_runtime_seconds':40})
    response.raise_for_status()
    config['job_id'] = response.json()['id']
    Path(config['job_path']).write_text(json.dumps(response.json()))
    end = time.monotonic()+40
    while True:
        job = get('jobs/'+config['job_id'])
        if job['status'] in ('completed','failed'):
            assert job['status']=='completed' and job['exit_code']==0, job
            if any(r['action']=='job_run_completed' and r['payload'].get('script_request_id')==job['id'] for r in get('audit',limit=5000)['entries']):
                break
        assert time.monotonic()<end, job
        time.sleep(.05)
    result['summary'] = 'Verdict: PASS\n'+envelope('installed_qa')
    result['verdict'] = 'PASS'
elif brief.get('probe'):
    retry_mode=config['mode'] in ('retry-same-parent','retry-recorded')
    if retry_mode and brief.get('continuation'):
        if not detail['children']:
            result['decision']={'action':'delegate','agent':'dev_agent','prompt':'{"probe":"child","retry_success":true}',
                                'revisit_of_task_id':brief['failed_id']}
        else:
            result['decision']={'action':'done','summary':'source retry continuation complete'}
    elif retry_mode and brief['probe']=='root' and len(detail['children'])==1:
        failed=detail['children'][0]
        assert failed['status']=='failed',failed
        if config['mode']=='retry-same-parent':
            result['decision']={'action':'delegate','agent':'dev_agent','prompt':'{"probe":"child","retry_success":true}',
                                'revisit_of_task_id':failed['task_id']}
        else:
            result['decision']={'action':'supersede','successor_brief':json.dumps({'probe':'root','continuation':True,'failed_id':failed['task_id']},sort_keys=True,separators=(',',':')),
                'rationale':'SOURCE-only genuine failed-child recovery','attestation':{
                    'recovery_reason':'SOURCE-only genuine failed-child recovery','policy_product_intent_unchanged':True,
                    'no_budget_or_external_commitment':True,'no_permission_or_cross_team_change':True,
                    'no_schema_auth_security_privacy_or_data_access_change':True,'no_unresolved_founder_gate':True}}
    elif retry_mode and brief['probe']=='root' and len(detail['children'])==2:
        assert all(child['status'] in ('completed','failed') for child in detail['children'])
        result['decision']={'action':'done','summary':'source same-parent retry complete'}
    elif not detail['children']:
        memory = Path.cwd()/'memory'
        nonshown_title = config.get('nonshown_title', 'Nonshown')
        (memory/'MEM-999-nonshown.md').write_text('---\nid: MEM-999\nslug: nonshown\ntitle: '+nonshown_title+'\ntopic: memory\nprovenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 1\n---\nA genuinely nonshown scoped memory.')
        for verb, value in [('get','MEM-001'),('search','scoped'),('get','MEM-999')]:
            call = subprocess.run([cli,'memory',verb,'--org','alpha','--agent',agent,value,'--json'],capture_output=True,text=True,timeout=10)
            assert call.returncode==0, (verb,call.stdout,call.stderr)
        if brief.get('fail_once'):
            print('SOURCE-only genuine failed original child',file=sys.stderr)
            raise SystemExit(9)
        if brief['probe']=='root':
            slot=next(slot for slot in config['plan']['slots'] if slot['root']['agent']==agent)
            result['decision'] = {'action':'delegate','agent':slot['child']['agent'],'prompt':slot['child']['brief']}
        else:
            result.pop('decision',None)
    else:
        result['decision'] = {'action':'done','summary':'probe done'}
else:
    for _ in range(int(brief.get('memory_read',0))):
        call = subprocess.run([cli,'memory','get','--org','alpha','--agent',agent,'MEM-001','--json'],
                              capture_output=True,text=True,timeout=10)
        assert call.returncode==0,(call.stdout,call.stderr)
    # Assigned ordinary work performs canonical search and follow-on reads.
    # Memories are written only after bootstrap's actual impression, so these
    # entries are genuinely nonshown rather than favorable attribution flags.
    for mid in brief.get('search_reads',[]):
        memory=Path.cwd()/'memory'
        title=config.get('nonshown_title','Nonshown')
        # Concurrent ordinary tasks share this agent's input directory. Publish
        # complete input bytes atomically so search never sees a truncated file.
        staged=memory/('.'+mid+'-'+sid+'.tmp')
        staged.write_text('---\nid: '+mid+'\nslug: follow-on-'+mid.lower()+'\ntitle: '+title+'\ntopic: memory\nprovenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 1\n---\nA genuinely nonshown scoped follow-on memory.')
        staged.replace(memory/(mid+'-follow-on.md'))
        for verb,value in [('search','scoped'),('get',mid)]:
            call=subprocess.run([cli,'memory',verb,'--org','alpha','--agent',agent,value,'--json'],capture_output=True,text=True,timeout=10)
            assert call.returncode==0,(call.stdout,call.stderr)
            if verb=='search':
                assert mid in {hit['id'] for hit in json.loads(call.stdout)['hits']},(mid,call.stdout)
    result['decision'] = {'action':'done','summary':'natural source task'}
file = Path(os.environ['HAPPYRANCH_DAEMON_HOME'])/(sid+'.completion.json')
file.write_text(json.dumps(result))
call = subprocess.run([cli,'report-completion','--org','alpha','--from-file',str(file)],capture_output=True,text=True,timeout=10)
assert call.returncode==0, (call.stdout,call.stderr)
if brief.get('operational_root') and result['decision']['action']=='done':
    Path(config['finished']).touch()
print('source provider completed')
'''


def _g1_source_failure_receipts(org, created, scenario, phase):
    """Retain SOURCE receipts; emit only failed tasks, without stream caps.

    v24: the two natural-boundary owners and two diagnostic owners observe
    complete attributable failure logs. Dropping emission loses hosted evidence;
    existing completion predicates and ephemeral files do not own that contract.
    Each independent read preserves earlier evidence; damaged nested commands
    retain their raw value without supplying a successful command or callback.
    No production hook or positive outcome is supplied.
    """
    import os
    from pathlib import Path

    config_path=Path(os.environ['G1_SOURCE_CONFIG'])
    def read(path, *, structured=False):
        try:
            raw=path.read_text()
        except FileNotFoundError:
            return {'state':'missing','path':str(path)}
        except Exception as exc:
            return {'state':'unavailable','path':str(path),'exception':repr(exc)}
        if not structured:
            return {'state':'recorded','text':raw}
        try:
            value=json.loads(raw)
            if not isinstance(value,dict):
                raise ValueError('receipt is not an object')
            return {'state':'recorded','value':value}
        except Exception as exc:
            return {'state':'malformed','path':str(path),'raw':raw,'exception':repr(exc)}
    def attributed(record, tid, sid):
        if record['state']=='recorded' and (
                record['value'].get('task_id')!=tid or record['value'].get('session_id')!=sid):
            return {'state':'identity_mismatch','observed_task_id':record['value'].get('task_id'),
                    'observed_session_id':record['value'].get('session_id')}
        return record

    receipts=[]
    for tid in created:
        # Acquire each field independently: a later read cannot replace the
        # authentic task/session, executor outcome, streams or other receipts.
        receipt={'finding':'SOURCE task/provider failure receipts','scenario':scenario,'phase':phase,
                 'task_id':tid,'session_id':None,'task':None,'task_state':'missing',
                 'results':None,'results_state':'unavailable','evidence_errors':[]}
        try:
            task=org.db.get_task(tid)
            if task is not None:
                receipt['session_id']=task.current_session_id
                receipt['task']=task.model_dump(mode='json')
                receipt['task_state']='recorded'
        except Exception as exc:
            receipt['task_state']='unavailable'
            receipt['evidence_errors'].append({'field':'task','exception':repr(exc)})
        sid=receipt['session_id']
        row=receipt['task']
        try:
            results=org.db.get_task_results(tid)
            receipt['results']=results
            receipt['results_state']=('recorded' if results else 'missing') if (
                isinstance(results,list) and all(isinstance(r,dict) for r in results)) else 'malformed'
        except Exception as exc:
            receipt['evidence_errors'].append({'field':'results','exception':repr(exc)})
        results=receipt['results'] if receipt['results_state'] in ('recorded','missing') else []
        base=Path(str(config_path)+'.'+str(sid))
        provider=attributed(read(Path(str(base)+'.provider.json'),structured=True),tid,sid)
        executor=attributed(read(config_path.parent/((sid or tid)+'.executor.json'),structured=True),tid,sid)
        commands=provider.get('value',{}).get('commands')
        command_evidence=[]
        if provider['state']!='recorded':
            commands_state=provider['state']
        elif commands is None or commands==[]:
            commands_state='missing'
        elif not isinstance(commands,list):
            commands_state='malformed'
            command_evidence=[{'state':'malformed','raw':commands}]
        else:
            for command in commands:
                if (isinstance(command,dict) and isinstance(command.get('argv'),list)
                        and command['argv'] and all(isinstance(arg,str) for arg in command['argv'])):
                    command_evidence.append({'state':'recorded','value':command})
                else:
                    command_evidence.append({'state':'malformed','raw':command})
            commands_state='partial' if any(c['state']=='malformed' for c in command_evidence) else 'recorded'
        callback_commands=[c['value'] for c in command_evidence if c['state']=='recorded'
                           and 'report-completion' in c['value']['argv']]
        callback_command=callback_commands[-1] if callback_commands else None
        # Only this real session's persisted results can demonstrate admission.
        callback_results=[r for r in results if sid is not None and r.get('session_id')==sid
                          and r.get('agent')==(row or {}).get('assigned_agent')]
        callback_state=('persisted' if callback_results else 'missing') if (
            receipt['results_state'] in ('recorded','missing') and receipt['task_state']=='recorded') else 'unavailable'
        callback_payload=attributed(read(config_path.parent/'daemon'/((sid or tid)+'.completion.json'),
                                         structured=True),tid,sid)
        executor_value=executor.get('value',{})
        parsed_report=executor_value.get('report')
        receipt.update({'executor':executor,'provider':provider,
            'commands_state':commands_state,'commands':commands,'command_evidence':command_evidence,
            'provider_stdout':read(Path(str(base)+'.stdout')),
            'provider_stderr':read(Path(str(base)+'.stderr')),
            'provider_error':read(Path(str(base)+'.error')),
            'provider_recorder_error':read(Path(str(base)+'.recorder-error')),
            'provider_missing_fields':[key for key in ('argv','started_ns','ended_ns','commands')
                if key not in provider.get('value',{})],
            'executor_outcome':('raised' if executor_value.get('exception') else
                'returned' if isinstance(executor_value.get('result'),dict) else
                'malformed' if executor_value.get('result') is not None else 'missing'),
            'parsed_report':parsed_report,
            'parsed_report_state':('recorded' if isinstance(parsed_report,dict) else
                'missing' if parsed_report is None else 'malformed'),
            'actual_callback':{'state':callback_state,'results':callback_results,
                'command':callback_command,'payload':callback_payload}})
        receipts.append(receipt)
        if receipt.get('task') is None or receipt['task']['status']!='completed':
            print(json.dumps(receipt,sort_keys=True,default=str))
    try:
        (config_path.parent/(scenario+'-'+phase+'.source-diagnostics.json')).write_text(
            json.dumps({'scenario':scenario,'phase':phase,'receipts':receipts},sort_keys=True,default=str))
    except Exception as exc:
        print(json.dumps({'finding':'SOURCE recorder failure','scenario':scenario,
                          'phase':phase,'exception':repr(exc)}))
    return receipts


@pytest.fixture
def g1_source_org(test_settings, monkeypatch, tmp_path, request):
    """Disposable actual queue/callback/job source chain; no live authority.

    INLINE v24 diagnostic fixture consumers:
    test_real_accepted_epoch_retrieval_role_and_pair_boundaries (both cases) and
    test_real_natural_sample_agent_and_majority_boundaries (all scenarios).
    1. Observe actual ExecutorResult and parsed callback alongside the existing
       task/result/session/whole-report assertions, retaining provider failure.
    2. A failed provider or absent callback still fails each existing owner;
       the recorder calls the real producer and returns its unchanged answer.
    3. Existing provider .error files capture exceptions, but not actual
       ExecutorResult returncode/failure category or the admitted callback.
    4. No production seam; only a scoped test-side reporting wrapper for the
       natural-boundaries fixture parameter. No positive result is supplied.
       Failed-command and missing-callback owners exercise this recorder.
    """
    import asyncio
    import os
    import shlex
    import socket
    import sys
    import threading
    import time
    from pathlib import Path
    import httpx
    import uvicorn
    from runtime.daemon import paths
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.runtime import RuntimeDir
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from runtime.orchestrator.executor_binary_registry import set_binary
    from tests.test_memory_session_transport_shipping import _shipping_platform_detector, _wait_for_server
    from tests.infrastructure.test_memory_digest_exposure import FIXTURE

    monkeypatch.setattr('runtime.platform.isolation.detect_platform_isolation', _shipping_platform_detector)
    monkeypatch.setattr('runtime.orchestrator.executors.detect_platform_isolation', _shipping_platform_detector)
    monkeypatch.setenv('HAPPYRANCH_TEST_REAL_PLATFORM', '1')
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME', str(tmp_path/'daemon'))
    monkeypatch.delenv('HAPPYRANCH_TASK_TMP_ROOT', raising=False)
    monkeypatch.delenv('HAPPYRANCH_TASK_SCRATCH_MANIFEST', raising=False)
    paths.ensure_daemon_home()
    paths.ensure_token()
    runtime = RuntimeDir.init(tmp_path/'runtime')
    for name in ('start-task','jobs','make-worktree','thread','dream','todos','workspace-cleanup'):
        source = runtime.root/'skills'/'bundled'/name
        source.mkdir(parents=True,exist_ok=True)
        (source/'SKILL.md').write_text('# '+name+'\n')
    multipath=getattr(request,'param',None)=='multi-path'
    retry_mode=getattr(request,'param',None) in ('retry-same-parent','retry-recorded')
    root = runtime.orgs_dir/'alpha'
    (root/'org'/'agents').mkdir(parents=True)
    workers='dev_agent, qa_engineer, dev_codex' if multipath else 'dev_agent, qa_engineer'
    (root/'org'/'teams.yaml').write_text('teams:\n  engineering:\n    manager: engineering_head\n    workers: ['+workers+']\n')
    members=[('engineering_head','manager'),('dev_agent','worker'),('qa_engineer','worker')]
    if multipath:members.append(('dev_codex','worker'))
    for name,role in members:
        definition = AgentDef(name=name,team='engineering',role=role,executor='codex' if name=='dev_codex' else 'claude',allow_rules=(),repos={},
            enrolled_by=None,enrolled_at_task=None,enrolled_at=None,system_prompt='source fixture',description='')
        (root/'org'/'agents'/(name+'.md')).write_text(render_agent_text(definition))
        work = root/'workspaces'/name
        work.mkdir(parents=True)
        (work/'AGENTS.md').write_text('# Source fixture\n')
        (work/'CLAUDE.md').symlink_to('AGENTS.md')
        (work/'task_history.md').write_text('# History\n')
    memory=root/'workspaces'/'dev_agent'/'memory'
    memory.mkdir()
    (memory/'MEM-001-bounded.md').write_text(FIXTURE)
    if retry_mode or getattr(request,'param',None)=='natural-boundaries':
        manager_memory=root/'workspaces'/'engineering_head'/'memory'
        manager_memory.mkdir()
        (manager_memory/'MEM-001-bounded.md').write_text(FIXTURE)
    if multipath:
        codex_memory=root/'workspaces'/'dev_codex'/'memory'
        codex_memory.mkdir()
        (codex_memory/'MEM-001-bounded.md').write_text(FIXTURE)
    # With two memories the renderer reserves room for its search nudge.
    # Keep the directive pointer visible and the later search result genuinely
    # nonshown in BOTH probe invocations and subsequent natural invocations.
    budget=240 if getattr(request,'param',None) in ('large-natural','natural-boundaries') else 255
    (root/'org'/'config.yaml').write_text(f'memory_digest_budget: {budget}\n')
    provider=tmp_path/'claude'
    import textwrap
    provider.write_text('#!'+sys.executable+'\nimport os, traceback\nfrom pathlib import Path\ntry:\n'+textwrap.indent(_G1_PROVIDER,'    ')+'\nexcept BaseException:\n    original_exception=traceback.format_exc()\n    try:\n        Path(os.environ["G1_SOURCE_CONFIG"]+"."+os.environ.get("HAPPYRANCH_RUNTIME_SESSION_ID", "unknown")+".error").write_text(original_exception)\n    except Exception as recorder_exc:\n        import sys\n        print("SOURCE recorder failure: "+repr(recorder_exc),file=sys.stderr)\n    raise\n')
    provider.chmod(0o755)
    set_binary('claude',str(provider))
    if multipath:set_binary('codex',str(provider))
    state=DaemonState.from_runtime(runtime,test_settings)
    org=state.orgs['alpha']
    if getattr(request,'param',None)=='natural-boundaries':
        # Reporting only: observe the real returned provider result/callback,
        # without supplying or changing either half of the producer outcome.
        import dataclasses
        import traceback
        original_run_agent=org.orchestrator._run_agent
        def recorded_run_agent(*args, **kwargs):
            tid=args[0] if args else kwargs['task_id']
            def record(sid, payload):
                try:
                    (tmp_path/(sid+'.executor.json')).write_text(json.dumps(
                        {'task_id':tid,'session_id':sid,**payload},sort_keys=True,default=str))
                except Exception as exc:
                    # Recorder loss cannot replace the actual return/exception.
                    import sys
                    print(json.dumps({'finding':'SOURCE recorder failure','task_id':tid,
                        'session_id':sid,'exception':repr(exc)}),file=sys.stderr)
            try:
                answer=original_run_agent(*args, **kwargs)
            except BaseException:
                exception=traceback.format_exc()
                try:
                    task=org.db.get_task(tid)
                    sid=task.current_session_id if task is not None else None
                    record(sid or tid, {'session_id':sid,'exception':exception,
                                       'result':None,'report':None})
                except Exception as recorder_exc:
                    import sys
                    print('SOURCE recorder failure: '+repr(recorder_exc),file=sys.stderr)
                raise
            try:
                result,report=answer
                record(result.session_id, {'result':dataclasses.asdict(result),
                    'report':report.model_dump(mode='json') if report is not None else None})
            except Exception as recorder_exc:
                import sys
                print('SOURCE recorder failure: '+repr(recorder_exc),file=sys.stderr)
            return answer
        monkeypatch.setattr(org.orchestrator,'_run_agent',recorded_run_agent)
    # Use the documented legacy path, whose actual executor/Popen remains real.
    org.orchestrator.attach_host_supervisor(None)
    slot={'path':'claude:legacy:none','root':{'agent':'dev_agent','team':'engineering','brief':json.dumps({'probe':'root'},sort_keys=True,separators=(',',':'))},
        'child':{'agent':'dev_agent','team':'engineering','brief':json.dumps({'probe':'child'},sort_keys=True,separators=(',',':'))}}
    if retry_mode:
        slot={**slot,'root':{**slot['root'],'agent':'engineering_head'},
              'child':{**slot['child'],'brief':'{"fail_once":true,"probe":"child"}'}}
    job_file=tmp_path/'finite-job.py'
    job_file.write_text(_G1_JOB)
    script='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(job_file))+' '+shlex.quote(json.dumps(slot,sort_keys=True,separators=(',',':')))+'\n'
    slots=[slot]
    if multipath:
        slots.append({'path':'codex:legacy:none',
            'root':{**slot['root'],'agent':'dev_codex'},'child':{**slot['child'],'agent':'dev_codex'}})
        script='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(job_file))+' '+shlex.quote(json.dumps(slots,sort_keys=True,separators=(',',':')))+'\n'
    plan={'run_id':'finite-g1','command':{'script_text':script,'interpreter':'bash','cwd_resolved':str(root/'workspaces'/'qa_engineer')},'slots':slots}
    config=tmp_path/'config.json'
    scenario=getattr(request,'param','ordinary')
    mode=scenario if isinstance(scenario,str) else 'ordinary'
    failed_withdrawal=mode=='invalidate-failed-qa'
    if failed_withdrawal:mode='invalidate'
    qa_agent='dev_agent' if mode=='self-qa' else 'qa_engineer'
    plan['command']['cwd_resolved']=str(root/'workspaces'/qa_agent)
    if isinstance(scenario,tuple) and scenario[0]=='reset-age':
        mode='reset'
    gate=tmp_path/'control-go'
    finished=tmp_path/'operational-finished'
    reset_slot={**slot,'root':{**slot['root'],'brief':'{"probe":"root","round":2}'},
                      'child':{**slot['child'],'brief':'{"probe":"child","round":2}'}}
    reset_script='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(job_file))+' '+shlex.quote(json.dumps(reset_slot,sort_keys=True,separators=(',',':')))+' finite-g1-reset\n'
    reset_plan={'run_id':'finite-g1-reset','command':{**plan['command'],'script_text':reset_script},'slots':[reset_slot]}
    config.write_text(json.dumps({'plan':plan,'job_path':str(tmp_path/'job.json'),'mode':mode,'control_gate':str(gate),
                                 'finished':str(finished),'reset_plan':reset_plan,'qa_agent':qa_agent,
                                 'nonshown_title':('Nonshown '+('scoped evidence '*12)) if mode in ('large-natural','natural-boundaries') else 'Nonshown'}))
    if mode == 'failed-write':
        original_insert=org.db.insert_audit_log_uncommitted
        def fault_insert(*args, **kwargs):
            if kwargs.get('action') == 'memory_collection_epoch_started':
                raise RuntimeError('bounded epoch insertion failure')
            return original_insert(*args, **kwargs)
        monkeypatch.setattr(org.db,'insert_audit_log_uncommitted',fault_insert)
    if mode in ('ownership-admission','operation-admission'):
        # Private negative fixture at the actual post-result-log final transition.
        # All role/result/job/probe positives already came from the SOURCE chain.
        original_append=org.db.append_memory_collection_transition
        def damage_before_admission(*,result_row_id):
            raw=org.db.fetch_one_readonly('SELECT output_summary FROM task_results WHERE id=?',(result_row_id,))
            candidate=parse_acceptance(raw[0])
            if candidate is None or candidate['kind']!='manager_acceptance':
                return original_append(result_row_id=result_row_id)
            kind=request.node.callspec.params['kind']
            if mode=='ownership-admission':
                claim=candidate['qa_ref'] if kind=='qa' else candidate['result_ref']
                with org.db._lock:
                    org.db._conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(claim['task_id'],))
                    org.db._conn.commit()
                assert org.db.get_task(claim['task_id']).status.value=='failed'
            else:
                claim=candidate['probe_receipts'][0][kind]
                operation,field=request.node.callspec.params['fault'].split('-')
                action={'read':'memory_read','search':'memory_search','impression':'memory_digest_impression'}[operation]
                row=next(row for row in org.db.get_audit_logs_by_action(action)
                         if row['payload'].get('session_id')==claim['runtime_session_id'])
                payload=copy.deepcopy(row['payload']);scope=row['task_id']
                if field=='scope':scope=claim['task_id'] if operation=='read' else 'AGENT-'+claim['agent']
                else:payload[{'task':'task_id','session':'session_id','agent':'agent'}[field]]={
                    'task':'TASK-999999','session':'sess-unrelated','agent':'qa_engineer'}[field]
                with org.db._lock:
                    org.db._conn.execute('UPDATE audit_log SET task_id=?,payload=? WHERE id=?',(scope,json.dumps(payload),row['id']))
                    org.db._conn.commit()
            request.node._g1_final_refusal={'candidate':candidate,'result_id':result_row_id,'observed_failed_owner':mode=='ownership-admission'}
            return original_append(result_row_id=result_row_id)
        monkeypatch.setattr(org.db,'append_memory_collection_transition',damage_before_admission)
    if isinstance(scenario,tuple) and scenario[0] in ('initial-age','reset-age'):
        # Test-side clock wrapping the actual final publication, never positive
        # authority. Tasks/jobs/results have already come from real producers.
        import runtime.infrastructure.memory_collection as collection_module
        import runtime.infrastructure.database as database_module
        import runtime.infrastructure.db.audit as audit_module
        from datetime import datetime,timedelta,timezone
        original_append=org.db.append_memory_collection_transition
        def at_admission(*,result_row_id):
            raw=next(row for row in org.db.get_task_results(org.db.fetch_one_readonly(
                'SELECT task_id FROM task_results WHERE id=?',(result_row_id,))[0]) if row['id']==result_row_id)
            candidate=parse_acceptance(raw['output_summary'])
            if scenario[0]=='reset-age' and candidate['predecessor_epoch_id'] is None:
                return original_append(result_row_id=result_row_id)
            claims=[claim for r in candidate['probe_receipts'] for claim in (r['root'],r['child'])]
            terminals=[datetime.fromisoformat(row['timestamp']) for row in org.db.get_audit_logs_by_action('memory_runtime_terminal')
                if any(row['task_id']==claim['task_id'] and row['payload']['session_id']==claim['runtime_session_id'] for claim in claims)]
            first=min(terminals)
            target=first+timedelta(seconds=scenario[1])
            inserted=False
            class AdmissionClock(datetime):
                @classmethod
                def now(cls,tz=None):
                    value=target+timedelta(seconds=2) if scenario[2]=='delayed' and inserted else target
                    return value if tz is not None else value.replace(tzinfo=None)
            original_insert=org.db.insert_audit_log_uncommitted
            def insert_then_delay(*args,**kwargs):
                nonlocal inserted
                value=original_insert(*args,**kwargs)
                if kwargs.get('action')=='memory_collection_epoch_started': inserted=True
                return value
            saved=[m.datetime for m in (collection_module,database_module,audit_module)]
            try:
                for module in (collection_module,database_module,audit_module):module.datetime=AdmissionClock
                org.db.insert_audit_log_uncommitted=insert_then_delay
                return original_append(result_row_id=result_row_id)
            finally:
                org.db.insert_audit_log_uncommitted=original_insert
                for module,value in zip((collection_module,database_module,audit_module),saved):module.datetime=value
        monkeypatch.setattr(org.db,'append_memory_collection_transition',at_admission)
    monkeypatch.setenv('G1_SOURCE_CONFIG',str(config))
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    paths.port_file().write_text(str(port))
    server=uvicorn.Server(uvicorn.Config(create_app(state),lifespan='off',log_level='error'))
    event_loop=[]
    async def serve():
        event_loop.append(asyncio.get_running_loop())
        org.orchestrator._main_loop=asyncio.get_running_loop()
        state.queue.start_workers(Dispatcher(state),n=4)
        try:
            await server.serve(sockets=[sock])
        finally:
            await state.queue.stop()
    thread=threading.Thread(target=lambda:asyncio.run(serve()))
    thread.start()
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+paths.token_file().read_text().strip()},timeout=10)
    try:
        _wait_for_server(server)
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'engineering_head','brief':'{"operational_root":true}'})
        assert response.status_code==200,response.text
        task_id=response.json()['task_id']
        end=time.monotonic()+50
        held=False
        # Poll the provider's own completion signal, not Database on a second
        # thread during nonblocking serving observation. A busy observation is
        # correctly unavailable; test-side polling must not manufacture it.
        while not finished.exists():
            errors=[(p.name,p.read_text()) for p in tmp_path.glob('*.error')]
            unexpected=[item for item in errors if not (retry_mode and 'SystemExit: 9' in item[1])]
            assert not unexpected,unexpected
            assert time.monotonic()<end,'operational completion signal missing'
            if Path(str(gate)+'.ready').exists() and not held:
                held=True
                assert len(org.db.get_audit_logs_by_action('memory_collection_epoch_started'))==1
                # Genuine current manager launch remains a valid owner; a
                # completed-root-only rule would break this actual control.
                assert org.db.get_task(task_id).status.value=='in_progress'
                original_epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
                original_roles={member['agent']:member['role'] for member in original_epoch['payload']['projection']['cohort']}
                assert org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=original_roles)['instrumentation_health']['status']=='healthy'
                if failed_withdrawal:
                    qa_id=original_epoch['payload']['qa_ref']['task_id']
                    with org.db._lock:
                        org.db._conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(qa_id,))
                        org.db._conn.commit()
                if mode == 'invalidate':
                    org.memory_collection.unavailable('owned source loss')
                gate.touch()
            time.sleep(.05)
        # A terminal task row can precede its ordinary audit/queue tail. Join
        # the actual owned queue before read-only stability assertions.
        asyncio.run_coroutine_threadsafe(state.queue._queue.join(),event_loop[0]).result(timeout=5)
        task=org.db.get_task(task_id)
        expected_status='failed' if mode=='ownership-admission' and request.node.callspec.params['kind']=='manager' else 'completed'
        assert task.status.value==expected_status,(task.note,org.db.get_task_results(task_id))
        yield org,client,task_id
    finally:
        gate.touch()
        client.close()
        server.should_exit=True
        thread.join(timeout=8)
        assert not thread.is_alive()
        org.close()
        state.direct_connect_authority_store.close()


@pytest.mark.parametrize('g1_source_org',['ordinary','large-natural'],indirect=True)
def test_real_source_chain_final_commit_and_readers(g1_source_org):
    """G1-P01/P02/P03/P05/P09, PRE01/PRE05, SELF01 source positive.

    INLINE v24: observes one appended epoch from independently admitted roles,
    actual owned job/output/operations, immutable original parent/brief and
    zero-write backend/HTTP report equivalence. Credible regression: remove
    final reconciliation or accept a changed returned-ID set. Previous parser
    and wiring owners refuse all authority; they cannot prove this conjunction.
    No production test seam or injected SessionTracker/positive job provenance.
    External stand-in proof is source-only, never installed acceptance.
    """
    from datetime import datetime,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report, acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    epochs=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    assert len(epochs)==1,org.db.get_task_results(root)
    epoch=epochs[0]
    results=org.db.get_task_results(root)
    original=results[-1]['output_summary']
    body=epoch['payload']
    assert body['manager_ref']['result_id']==results[-1]['id']
    assert body['predecessor_epoch_id'] is None
    assert body['base_assigned_intents']>=4
    now=datetime.now(timezone.utc)
    before=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    local=acquire_collection_report(org,current_time=now)
    remote=acquire_http_collection_report(client,'alpha',current_time=now)
    role_map={m['agent']:m['role'] for m in local[1]['installed_identity']['cohort']}
    reports=[reduce_collection_report(*evidence[:3],role_map,now) for evidence in (local,remote)]
    assert reports[0]==reports[1]
    assert reports[0]['epoch']=={'status':'accepted','id':body['epoch_id'],'collection_started':True,'started_at':epoch['timestamp'],'audit_id':epoch['id']}
    assert reports[0]['decision']=='insufficient_sample'
    assert reports[0]['aggregate']['correlated_sessions']==0
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==before
    assert org.db.get_task_results(root)[-1]['output_summary']==original


@pytest.mark.parametrize('field,value', [('python', {'executable':True}), ('loaded_code',[{'module':'forged'}]),
    ('files',[{'path':'relative','sha256':'a'*64}]),('cohort',[{'agent':'qa','role':True}]),
    ('profiles',[{'name':'claude','healthy':True}]),('backend',{'mode':'legacy','healthy':True})])
def test_nested_identity_has_no_open_authority_fields(field,value):
    """INLINE v24: the carrier refuses malformed nested identity at parse.

    Removing nested closure accepts forged metadata; top-level syntax owners
    don't observe these six nested structures. No production test seam; this
    is mechanical refusal only and never grants installed authority.
    """
    candidate=carrier()
    candidate['installed_identity'][field]=value
    with pytest.raises(AcceptanceUnavailable):
        parse_acceptance(summary(candidate))


def test_real_evidence_refusal_matrix(g1_source_org):
    """G1-P04/P05/P07, SELF03/04, PRE02/03/04 source refusals.

    INLINE v24: each named damage to an authentic acquired record must reach
    its specific category, keep the report closed and add no control rows.
    Credible regressions are omitted exact role/command/output/plan/set joins.
    Named additional variants manager-role, qa-role, qa-owner, qa-team,
    qa-cancelled, qa-parent, qa-model, manager-ambiguous, prior-ambiguous,
    child-ambiguous, child-delegation, job-submit-owner, job-audit-command and
    job-start-order require exact registered/assigned ownership, whole original
    plan/unique mapping and the actual job's audited command/order. Removing
    task assigned-owner binding allows qa-owner through all other gates; this
    case must fail at its named refusal/report boundary. Earlier parser cases
    and the original matrix variants do not own these specific joins.
    H01/H02 variants probe-zero-gets, probe-sparse-gets, probe-zero-searches
    and probe-wrong-get-tuple refuse at the actual probe operation count/tuple
    join. Removing that count fence must fail the named refusal; existing
    identity/census owners never possess independent actual probe operations.
    Carrier-only owners cannot reach these post-admission joins. No production
    test seam; real source-produced records are copied only for isolated
    negative corruption, never to fabricate a passing role/job/epoch.
    """
    from datetime import datetime,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report,validate_acceptance_evidence
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    now=datetime.now(timezone.utc)
    base,view,outputs,_=acquire_collection_report(org,current_time=now)
    assert reduce_collection_report(base,view,outputs,None,now)['epoch']['collection_started'] is True
    manager_id=org.db.get_task_results(root)[-1]['id']
    manager=next(row for row in base['task_results'] if row['id']==manager_id)
    candidate=parse_acceptance(manager['output_summary'])
    qa_id=candidate['qa_ref']['result_id']
    job_id=candidate['probe_receipts'][0]['job_id']
    probe_root=candidate['probe_receipts'][0]['root']['task_id']
    before=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    cases=[('qa-verdict','acceptance_independent_qa'),('qa-prose-conflict','acceptance_independent_qa'),
        ('qa-session','acceptance_role_binding'),('qa-result-missing','acceptance_result_missing'),
        ('job-owner','acceptance_job_identity'),('job-nonterminal','acceptance_job_identity'),
        ('job-nonzero','acceptance_job_identity'),('job-reason','acceptance_job_identity'),
        ('job-command','acceptance_job_identity'),('job-cwd','acceptance_job_identity'),
        ('job-interpreter','acceptance_job_identity'),('job-finish-audit','acceptance_job_finish'),
        ('output-truncated','acceptance_job_output'),('output-total','acceptance_job_output'),
        ('output-empty','acceptance_job_output'),('plan-missing','acceptance_prior_plan'),
        ('copied-root','acceptance_root_slot_ambiguous'),('root-brief','acceptance_root_slot_ambiguous'),
        ('manager-role','acceptance_role_binding'),('qa-role','acceptance_manager'),
        ('qa-owner','acceptance_role_binding'),('qa-team','acceptance_role_binding'),
        ('qa-cancelled','acceptance_role_binding'),('qa-parent','acceptance_lineage'),
        ('qa-model','acceptance_admission_binding'),('manager-ambiguous','acceptance_manager'),
        ('prior-ambiguous','acceptance_prior_plan'),('child-ambiguous','acceptance_child_slot_ambiguous'),
        ('child-delegation','acceptance_child_delegation'),('job-submit-owner','acceptance_job_owner'),
        ('job-audit-command','acceptance_job_audit'),('job-start-order','acceptance_job_order'),
        ('probe-zero-gets','acceptance_probe_operations'),('probe-sparse-gets','acceptance_probe_operations'),
        ('probe-zero-searches','acceptance_probe_operations'),('probe-wrong-get-tuple','acceptance_probe_operations')]
    for fault,category in cases:
        tables,out,observation=copy.deepcopy(base),copy.deepcopy(outputs),copy.deepcopy(view)
        qa=next(row for row in tables['task_results'] if row['id']==qa_id)
        job=next(row for row in tables['jobs'] if row['id']==job_id)
        original=next(row for row in tables['tasks'] if row['id']==probe_root)
        if fault=='qa-verdict': qa['verdict']='FAIL'
        elif fault=='qa-prose-conflict': qa['output_summary']='Verdict: FAIL\n'+qa['output_summary']
        elif fault=='qa-session': qa['session_id']='provider-resume'
        elif fault=='qa-result-missing': tables['task_results'].remove(qa)
        elif fault=='job-owner': job['agent_name']='dev_agent'
        elif fault=='job-nonterminal': job['status']='running'
        elif fault=='job-nonzero': job['exit_code']=7
        elif fault=='job-reason': job['reason']='output_cap'
        elif fault=='job-command': job['script_text']+='echo unapproved\n'
        elif fault=='job-cwd': job['cwd_resolved']='/unapproved'
        elif fault=='job-interpreter': job['interpreter']='python'
        elif fault=='job-finish-audit': tables['audit_log']=[row for row in tables['audit_log'] if row['action']!='job_run_completed']
        elif fault=='output-truncated': out[job_id]['truncated_stdout']=True
        elif fault=='output-total': out[job_id]['total_stdout_bytes']+=1
        elif fault=='output-empty': out[job_id]['stderr']='';out[job_id]['total_stderr_bytes']=0
        elif fault=='plan-missing': tables['task_results']=[row for row in tables['task_results'] if row['task_id']!=root or row['id']==manager_id]
        elif fault=='copied-root': tables['tasks'].append({**original,'id':'TASK-999'})
        elif fault=='root-brief': original['brief']+=' copied suffix'
        elif fault=='manager-role': next(member for member in observation['installed_identity']['cohort'] if member['agent']==manager['agent'])['role']='worker'
        elif fault=='qa-role': next(member for member in observation['installed_identity']['cohort'] if member['agent']==qa['agent'])['role']='manager'
        elif fault=='qa-owner': next(row for row in tables['tasks'] if row['id']==qa['task_id'])['assigned_agent']='dev_agent'
        elif fault=='qa-team': next(row for row in tables['tasks'] if row['id']==qa['task_id'])['team']='foreign-team'
        elif fault=='qa-cancelled': next(row for row in tables['tasks'] if row['id']==qa['task_id'])['cancelled_at']=now.isoformat()
        elif fault=='qa-parent': next(row for row in tables['tasks'] if row['id']==qa['task_id'])['parent_task_id']=None
        elif fault=='qa-model': next(member for member in observation['installed_identity']['cohort'] if member['agent']==qa['agent'])['model']='different-current-model'
        elif fault=='manager-ambiguous': observation['installed_identity']['cohort'].append({**next(member for member in observation['installed_identity']['cohort'] if member['agent']==manager['agent']),'agent':'second-manager'})
        elif fault=='prior-ambiguous':
            prior=next(row for row in tables['task_results'] if row['task_id']==root and row['id']!=manager_id)
            tables['task_results'].append({**prior,'id':999})
        elif fault=='child-ambiguous':
            child_id=candidate['probe_receipts'][0]['child']['task_id']
            child=next(row for row in tables['tasks'] if row['id']==child_id)
            tables['tasks'].append({**child,'id':'TASK-999'})
        elif fault=='child-delegation':
            delegated=next(row for row in tables['task_results'] if row['task_id']==probe_root and json.loads(row['decision_json']).get('action')=='delegate')
            delegated['decision_json']=json.dumps({'action':'done','summary':'different decision'})
        elif fault=='job-submit-owner':
            row=next(row for row in tables['audit_log'] if row['action']=='job_submitted')
            row['agent']='dev_agent'
        elif fault=='job-audit-command':
            row=next(row for row in tables['audit_log'] if row['action']=='job_submitted')
            body=json.loads(row['payload']);body['byte_size']+=1;row['payload']=json.dumps(body)
        elif fault=='job-start-order': job['started_at']=manager['created_at']
        elif fault.startswith('probe-'):
            selected=[row for row in tables['audit_log'] if row['action']==('memory_search' if fault=='probe-zero-searches' else 'memory_read')
                      and json.loads(row['payload']).get('session_id')==candidate['probe_receipts'][0]['root']['runtime_session_id']]
            assert len(selected)==(1 if fault=='probe-zero-searches' else 2)
            if fault=='probe-wrong-get-tuple':
                body=json.loads(selected[0]['payload']);body['session_id']='not-the-probe-tuple'
                selected[0]['payload']=json.dumps(body)
            else:
                for row in selected[:1] if fault=='probe-sparse-gets' else selected:tables['audit_log'].remove(row)
        with pytest.raises(AcceptanceUnavailable,match=category):
            validate_acceptance_evidence(tables,manager_id,observation,out,current_time=now)
        report=reduce_collection_report(tables,observation,out,None,now)
        assert report['epoch']['collection_started'] is False,(fault,report)
        assert report['decision']=='insufficient_instrumentation',(fault,report)
        assert report['evaluation_candidate'] is False,(fault,report)
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==before
    assert len(org.db.get_audit_logs_by_action('memory_collection_epoch_started'))==1


def test_real_atomic_replay_after_terminal_owner(g1_source_org):
    """G1-P03/P09, SELF05/PRE05: original replay never restarts a clock.

    INLINE v24: concurrent actual writers return the same committed audit ID
    and timestamp after the ordinary owner became completed. Rejecting owner
    state before equivalent lookup breaks retry; removing serialization
    duplicates rows. Parser/wiring tests never reach a valid transaction.
    No test seam, supplied health or fabricated provenance.
    """
    from concurrent.futures import ThreadPoolExecutor
    org,_,root=g1_source_org
    row=org.db.get_task_results(root)[-1]
    original=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    assert org.db.get_task(root).status.value=='completed'
    def repeat():
        try:
            receipt=org.db.append_memory_collection_transition(result_row_id=row['id'])
            return receipt['id'],receipt['timestamp']
        except AcceptanceUnavailable as exc:
            return 'unavailable',str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(repeat) for _ in range(2)]
        receipts=[future.result(timeout=10) for future in futures]
    assert receipts==[(original['id'],original['timestamp'])]*2
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[original]


@pytest.mark.parametrize('g1_source_org',['invalidate','invalidate-failed-qa','equivalent','conflict','failed-write'],indirect=True)
def test_real_control_and_failed_write_outcomes(g1_source_org):
    """G1-P03/P06/P08/P09/H03, SELF05/PRE05 real control outcomes.

    INLINE v24, invalidate/invalidate-failed-qa/equivalent/conflict/failed-write:
    Genuine ongoing in_progress manager ownership is healthy before the next
    control, and failed original QA ownership still permits authenticated
    withdrawal. Requiring a completed manager or healthy failed QA for withdrawal
    breaks these cases; original continuing failure never owns withdrawal.
    Ordinary callbacks retain their completed outcomes, append
    only one start/optional invalidation, preserve original start/time and
    leave conflicted/failed/unhealthy reports closed. Credible regressions:
    require healthy probe for withdrawal, include own-ID in equivalence, let a
    newer conflict reuse old health, or commit after failed audit insertion.
    Mechanical parse owners cannot produce these control histories. No test
    seam; private provider callbacks and the real DB writer own publication.
    """
    from datetime import datetime,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    results=org.db.get_task_results(root)
    summaries=[parse_acceptance(row['output_summary']) for row in results if COLLECTION_TAG in row['output_summary']]
    starts=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    invalidations=org.db.get_audit_logs_by_action('memory_collection_invalidated')
    assert org.db.get_task(root).status.value=='completed'
    mode='failed-write' if len(summaries)==1 else 'invalidate' if summaries[-1]['action']=='invalidate' else 'conflict' if summaries[-1]['health_definition_sha256']=='0'*64 else 'equivalent'
    assert len(starts)==(0 if mode=='failed-write' else 1),(mode,results)
    assert len(invalidations)==(1 if mode=='invalidate' else 0),(mode,results)
    if mode=='invalidate':
        assert invalidations[0]['payload']['predecessor_epoch_id']==starts[0]['payload']['epoch_id']
        assert invalidations[0]['payload']['epoch_id']==starts[0]['payload']['epoch_id']
    now=datetime.now(timezone.utc)
    before=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    evidence=acquire_collection_report(org,current_time=now)
    report=reduce_collection_report(*evidence[:3],None,now)
    assert report['epoch']['collection_started'] is (mode=='equivalent'),(mode,report)
    assert report['evaluation_candidate'] is False
    if mode=='equivalent':
        assert report['epoch']['audit_id']==starts[0]['id']
        assert report['epoch']['started_at']==starts[0]['timestamp']
        assert report['aggregate']['correlated_sessions']==1
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==before


def test_real_probe_freshness_and_identity_bounds(g1_source_org):
    """H01/H02/H04/H05/P07 source health boundary variants.

    INLINE v24: authentic proof accepts exactly the 48-hour closed bound and
    refuses stale/boot/identity/cohort/sticky-loss variants with zero writes.
    Removing freshness or installed/census fences enables a damaged proof.
    Existing census owners have no role/job/canary reference to age. No test
    production seam; all positive evidence came from the real source chain.
    """
    from datetime import datetime,timedelta,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report,validate_acceptance_evidence
    org,_,root=g1_source_org
    tables,view,outputs,_=acquire_collection_report(org)
    manager_id=org.db.get_task_results(root)[-1]['id']
    candidate=parse_acceptance(org.db.get_task_results(root)[-1]['output_summary'])
    claims=[claim for receipt in candidate['probe_receipts'] for claim in (receipt['root'],receipt['child'])]
    times=[datetime.fromisoformat(row['timestamp']) for row in tables['audit_log'] if row['action']=='memory_runtime_terminal'
           and any(row['task_id']==claim['task_id'] and json.loads(row['payload'])['session_id']==claim['runtime_session_id'] for claim in claims)]
    first=min(times)
    for seconds in (172799,172800,172801):
        if seconds<=172800:
            assert validate_acceptance_evidence(tables,manager_id,view,outputs,current_time=first+timedelta(seconds=seconds))['candidate']['qa_ref']['result_id']==candidate['qa_ref']['result_id']
        else:
            with pytest.raises(AcceptanceUnavailable,match='acceptance_probe_stale'):
                validate_acceptance_evidence(tables,manager_id,view,outputs,current_time=first+timedelta(seconds=seconds))
    for field,value,category in [('observation_error','sticky loss','observation_unavailable'),('boot_id','different-boot','current_seal'),
                                 ('active_preparations',[123],'observation_unavailable')]:
        with pytest.raises(AcceptanceUnavailable,match=category):
            validate_acceptance_evidence(tables,manager_id,{**view,field:value},outputs,current_time=datetime.now(timezone.utc))
    changed=copy.deepcopy(view)
    changed['installed_identity']['teams_sha256']='0'*64
    with pytest.raises(AcceptanceUnavailable,match='installed_identity'):
        validate_acceptance_evidence(tables,manager_id,changed,outputs,current_time=datetime.now(timezone.utc))


@pytest.mark.parametrize('g1_source_org',['equivalent'],indirect=True)
def test_original_admission_remains_healthy_at_day14_in_both_consumers(g1_source_org,monkeypatch,capsys):
    """AGE02/AGE04: original accepted evidence survives elapsed probe age.

    INLINE v24: observe complete backend/canonical JSON and text, original
    persisted epoch/QA/job evidence and zero reader writes at day14. Credible
    regression: apply new-admission freshness at report time in either serving
    HTTP gate or shared reducer. The admission-bound owner does not exercise
    current_epoch_references/HTTP or elapsed epoch/report time. No production
    seam; real queue/job/callback provenance, test-side clock only. This replaces
    the superseded TASK9779 temporal-conflict oracle; old receipts stay intact.
    One natural session proves healthy short-sample behavior, not 500 credit.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report,validate_acceptance_evidence
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    body=epoch['payload']
    manager_id=body['manager_ref']['result_id']
    tables,view,outputs,_=acquire_collection_report(org)
    manager=next(row for row in tables['task_results'] if row['id']==manager_id)
    qa=next(row for row in tables['task_results'] if row['id']==body['qa_ref']['result_id'])
    job=next(row for row in tables['jobs'] if row['id']==body['projection']['probe_receipts'][0]['job_id'])
    claimed=body['projection']['probe_receipts'][0]['root']
    terminal=next(row for row in tables['audit_log'] if row['action']=='memory_runtime_terminal'
        and row['task_id']==claimed['task_id'] and json.loads(row['payload'])['session_id']==claimed['runtime_session_id'])
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    live=reduce_collection_report(tables,view,outputs,None,datetime.now(timezone.utc))
    assert live['instrumentation_health']['status']=='healthy'
    assert live['aggregate']['correlated_sessions']==1
    first=datetime.fromisoformat(live['observation_period']['first_impression_at'])
    started=datetime.fromisoformat(epoch['timestamp'])
    canary=datetime.fromisoformat(terminal['timestamp'])
    assert canary<=datetime.fromisoformat(job['finished_at'])<=datetime.fromisoformat(qa['created_at'])<=datetime.fromisoformat(manager['created_at'])<=started<=first
    anchor=max(first,started)
    ceil=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
    if anchor!=ceil: ceil+=timedelta(days=1)
    cutoff=ceil+timedelta(days=14)
    expiry=canary+timedelta(hours=48)
    assert cutoff>expiry
    before=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    roles={row['agent']:row['role'] for row in view['installed_identity']['cohort']}
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    class ControlledDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ControlledDateTime)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ControlledDateTime)
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    cli_report=json.loads(capsys.readouterr().out)
    assert cli_report==backend
    assert backend['instrumentation_health']['status']=='healthy',backend
    assert backend['decision']=='insufficient_sample'
    assert backend['epoch']=={'status':'accepted','id':body['epoch_id'],'collection_started':True,
        'started_at':epoch['timestamp'],'audit_id':epoch['id']}
    assert backend['observation_period']['days_elapsed']==14
    assert backend['aggregate']['correlated_sessions']==1
    response=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1})
    assert response.status_code==200
    observation=response.json()['memory_collection_observation']
    assert (observation['epoch_id'],observation['epoch_audit_id'])==(body['epoch_id'],epoch['id'])
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    text=capsys.readouterr().out
    assert text.startswith('=== THR-091 Memory Telemetry Report (observation-only) ===\nStatus: insufficient_sample\n')
    assert f"Canary-gated collection started: {body['epoch_id']}; audit {epoch['id']}; at {epoch['timestamp']}\n" in text
    assert 'Days elapsed: 14 / 14\nSessions: 1 / 500\nThresholds:    NOT MET\n' in text
    assert text.endswith('DECISION: insufficient_sample\nHealthy collection; sample or functional evidence is insufficient.\n')
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
    assert backend['observation_period']['thresholds_met'] is False
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==before
    print(json.dumps({'source_case':'AGE02 original admission healthy day14 short sample','epoch_audit_id':epoch['id'],
        'manager_result_id':manager_id,'qa_result_id':qa['id'],'job_id':job['id'],'canary_terminal':terminal['timestamp'],
        'job_finished':job['finished_at'],'qa_published':qa['created_at'],'manager_published':manager['created_at'],
        'epoch_started':epoch['timestamp'],'natural_first':first.isoformat(),'probe_expiry':expiry.isoformat(),
        'first_day14_cutoff':cutoff.isoformat(),'backend_cli_equal':True,'report_status':backend['instrumentation_health']['status'],
        'decision':backend['decision'],'reader_writes':0},sort_keys=True))


def test_original_admission_replay_after48h_is_immutable(g1_source_org,monkeypatch):
    """AGE06: late concurrent equivalent callbacks return original boundary.

    INLINE v24: observes actual DB transition receipts and all immutable rows.
    Credible regression: check probe age against replay clock before equivalent
    lookup. The earlier concurrent owner runs while original proof is fresh;
    day14 reader owner never writes. No production seam; actual accepted chain
    plus test-side clock, genuine completed owner and concurrent DB calls.
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime,timedelta,timezone
    org,_,root=g1_source_org
    original=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    before=org.db.read_memory_collection_evidence()
    cutoff=datetime.fromisoformat(original['timestamp'])+timedelta(days=14)
    class ReplayClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReplayClock)
    monkeypatch.setattr('runtime.infrastructure.database.datetime',ReplayClock)
    def replay():
        try:
            return org.db.append_memory_collection_transition(result_row_id=original['payload']['manager_ref']['result_id'])
        except AcceptanceUnavailable as exc:
            return {'refusal':str(exc)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:replay(),range(2)))
    assert results==[original,original],results
    assert org.db.read_memory_collection_evidence()==before


@pytest.mark.parametrize('g1_source_org',[
    ('initial-age',172799,'stable'),('initial-age',172800,'stable'),
    ('initial-age',172801,'stable'),('initial-age',-1,'stable'),
    ('initial-age',172799,'delayed')],indirect=True)
def test_initial_admission_age_at_final_commit(g1_source_org,request,monkeypatch,capsys):
    """AGE01/AGE03 initial: actual post-log commit boundaries and delayed crossing.

    INLINE v24: observes real persisted epoch timestamp/count/projection and
    unchanged completed callback outcome. Credible regression: omit final age
    recheck after an insert delayed across48h, or use preparation/report time.
    Validator-only freshness coverage never observes commit/rollback. No
    production seam; scoped test-side clock on actual admission/audit writer.
    Both readers at the committed boundary remain a separate report contract.
    """
    from datetime import datetime,timedelta,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report,acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    scenario=request.node.callspec.params['g1_source_org']
    seconds,phase=scenario[1:]
    expected=seconds in (172799,172800) and phase=='stable'
    epochs=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    assert len(epochs)==int(expected),(scenario,epochs)
    assert org.db.get_task(root).status.value=='completed'
    if expected:
        epoch=epochs[0]
        candidate=epoch['payload']['projection']
        times=[datetime.fromisoformat(row['timestamp']) for row in org.db.get_audit_logs_by_action('memory_runtime_terminal')
            if any(row['task_id']==claim['task_id'] and row['payload']['session_id']==claim['runtime_session_id']
                for receipt in candidate['probe_receipts'] for claim in (receipt['root'],receipt['child']))]
        assert datetime.fromisoformat(epoch['timestamp'])-min(times)==timedelta(seconds=seconds)
        now=datetime.fromisoformat(epoch['timestamp'])+timedelta(seconds=1)
    else:
        now=datetime.now(timezone.utc)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return now if tz is not None else now.replace(tzinfo=None)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    before=org.db.read_memory_collection_evidence()
    report=reduce_collection_report(*acquire_collection_report(org,current_time=now)[:3],None,now)
    remote=reduce_collection_report(*acquire_http_collection_report(client,'alpha',current_time=now)[:3],None,now)
    assert remote==report
    assert report['epoch']['collection_started'] is expected,report
    assert report['evaluation_candidate'] is False
    if not expected:
        manager=org.db.get_task_results(root)[-1]
        with pytest.raises(AcceptanceUnavailable):
            org.db.append_memory_collection_transition(result_row_id=manager['id'])
        early=datetime.fromisoformat(manager['created_at'])-timedelta(seconds=1)
        early_report=reduce_collection_report(*acquire_collection_report(org,current_time=early)[:3],None,early)
        assert early_report['epoch']['collection_started'] is False
    assert org.db.read_memory_collection_evidence()==before


@pytest.mark.parametrize('g1_source_org',[
    ('reset-age',172799,'stable'),('reset-age',172800,'stable'),
    ('reset-age',172801,'stable'),('reset-age',-1,'stable'),
    ('reset-age',172799,'delayed')],indirect=True)
def test_fresh_qa_reset_admission_age_at_final_commit(g1_source_org,request,monkeypatch):
    """AGE01/03/06 reset: independent new job/QA plus exact predecessor.

    INLINE v24: the actual committed reset audit/start/projection and BOTH
    reports own the oracle; stale/future/delayed refusal preserves predecessor
    history, ordinary callback success and zero reader writes. A dropped final
    age guard incorrectly admits the delayed reset; a reused-QA or missing
    predecessor guard can also restart old populations. The initial owner has
    no predecessor, fresh second plan/job/result or pre-reset natural tuple.
    No production test seam: actual producers plus scoped test-side clock.
    """
    from datetime import datetime,timedelta,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report,acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    scenario=request.node.callspec.params['g1_source_org']
    seconds,phase=scenario[1:]
    expected=seconds in (172799,172800) and phase=='stable'
    epochs=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    assert len(epochs)==1+int(expected),(scenario,epochs)
    original=epochs[0]
    results=org.db.get_task_results(root)
    tagged=[(row,parse_acceptance(row['output_summary'])) for row in results if COLLECTION_TAG in row['output_summary']]
    assert len(tagged)==2
    reset_row,candidate=tagged[-1]
    assert candidate['predecessor_epoch_id']==original['payload']['epoch_id']
    assert candidate['qa_ref']!=original['payload']['qa_ref']
    assert candidate['probe_receipts'][0]['job_id']!=original['payload']['projection']['probe_receipts'][0]['job_id']
    assert set(candidate['synthetic_task_ids']).isdisjoint(original['payload']['projection']['synthetic_task_ids'])
    times=[datetime.fromisoformat(row['timestamp']) for row in org.db.get_audit_logs_by_action('memory_runtime_terminal')
           if any(row['task_id']==claim['task_id'] and row['payload']['session_id']==claim['runtime_session_id']
                  for receipt in candidate['probe_receipts'] for claim in (receipt['root'],receipt['child']))]
    now=max(datetime.now(timezone.utc),min(times)+timedelta(seconds=max(0,seconds)+3))
    if expected:
        current=epochs[-1]
        assert current['payload']['epoch_id']!=original['payload']['epoch_id']
        assert datetime.fromisoformat(current['timestamp'])-min(times)==timedelta(seconds=seconds)
        assert current['payload']['predecessor_epoch_id']==original['payload']['epoch_id']
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return now if tz is not None else now.replace(tzinfo=None)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.database.datetime',ReaderClock)
    before=org.db.read_memory_collection_evidence()
    report=reduce_collection_report(*acquire_collection_report(org,current_time=now)[:3],None,now)
    remote=reduce_collection_report(*acquire_http_collection_report(client,'alpha',current_time=now)[:3],None,now)
    assert remote==report
    assert report['epoch']['collection_started'] is expected,(scenario,report)
    assert report['evaluation_candidate'] is False
    if expected:
        assert report['epoch']['id']==epochs[-1]['payload']['epoch_id']
        # The one genuine natural tuple ran between starts. Fresh reset cannot
        # stitch it, the old canary or the old observation days into its sample.
        assert report['aggregate']['correlated_sessions']==0
        assert report['observation_period']['days_elapsed']==0
        replay=org.db.append_memory_collection_transition(result_row_id=reset_row['id'])
        assert (replay['id'],replay['timestamp'])==(epochs[-1]['id'],epochs[-1]['timestamp'])
    else:
        with pytest.raises(AcceptanceUnavailable):
            org.db.append_memory_collection_transition(result_row_id=reset_row['id'])
        early=datetime.fromisoformat(reset_row['created_at'])-timedelta(seconds=1)
        early_report=reduce_collection_report(*acquire_collection_report(org,current_time=early)[:3],None,early)
        assert early_report['epoch']['collection_started'] is False
    assert org.db.get_task(root).status.value=='completed'
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==epochs
    assert org.db.read_memory_collection_evidence()==before


def _large_zero_read_expected(epoch,first,cutoff,*,sessions=500):
    """Independent complete JSON contract for pointer-only, zero-read SOURCE."""
    metrics={'correlated_sessions':sessions,'pointer_opportunities':sessions,'full_body_exposures':0,
        'pointer_pairs_read':0,'digest_pull_through':0.0,'search_sourced_reads':0,
        'search_sourced_absent_from_digest':0,'search_absent_fraction':None,'distinct_valid_read_pairs':0,
        'read_operations':0,'pointer_sessions_activated':0,'pointer_sessions':sessions,'session_activation':0.0}
    return {'data_through':cutoff.isoformat(),'measurement_timezone':'UTC','acquisition_complete':True,
        'epoch':{'status':'accepted','id':epoch['payload']['epoch_id'],'collection_started':True,
                 'started_at':epoch['timestamp'],'audit_id':epoch['id']},
        'observation_period':{'first_impression_at':first.isoformat(),'days_elapsed':14,'required_days':14,
            'total_correlated_sessions':sessions,'required_sessions':500,'days_met':True,'sessions_met':True,
            'thresholds_met':True,'diagnostics_valid_for_collection':True,'status':'activation_loss','reason_code':'sample'},
        'instrumentation_health':{'status':'healthy','population':'audited intended task-session invocations',
            'audited_task_starts':sessions,'intended_task_launches':sessions,'expected_nonempty_launches':sessions,
            'matching_exposures':sessions,'unknown_exposures':0,'validated_read_operations':0,
            'rejected_task_attribution':0,'malformed_records':0,'complete_launch_census':'PASS','probe_health':'PASS',
            'epoch_health':'PASS','roles_available':True,'reason_code':'healthy','duplicate_impressions':0,
            'repeated_read_pairs':0,'epoch_id':epoch['payload']['epoch_id'],'epoch_audit_id':epoch['id'],
            'qa_ref':epoch['payload']['qa_ref']},
        'aggregate':{**metrics,'digest_sourced_read_pairs':0,'explicit_read_pairs':0,'untrusted_task_reads':0,
            'eligible_functional_agents':1,'eligible_pointer_agents':1,'eligible_agents_below_10_percent':1},
        'by_agent':{'dev_agent':{'role':'worker','eligible':True,'activation_vote_eligible':True,**metrics}},
        'by_role':{'worker':{**metrics,'retrieval_corroboration_eligible':False,'descriptive_only_for_activation_majority':True}},
        'read_counts':{},'excluded':{'dream':0,'legacy':0,'manual':0,'recovery':0,'thread':0},'diagnostic_errors':{},
        'decision':'activation_loss','evaluation_candidate':True,'decision_detail':'Evaluation only; no tuning is executed.'}


def _expected_full_text(expected):
    """Complete published text format, including structurally malformed refusals.

    INLINE v24 for the agent-tuple cases in
    test_probe_operation_exact_scope_and_tuple_closes_both_readers: the whole
    canonical text must preserve the closed decision and display UNKNOWN when
    malformed evidence cannot establish a launch census. A credible regression
    defaults that absent census to PASS in the CLI. Scope/task/session negatives
    retain census fields and do not own this structural-refusal branch. No
    production seam; this is only an independent expected-text formatter.
    """
    epoch,obs=expected['epoch'],expected['observation_period']
    epoch_line=(f"Canary-gated collection started: {epoch['id']}; audit {epoch['audit_id']}; at {epoch['started_at']}"
                if epoch['collection_started'] else 'Canary-gated collection has NOT started; epoch is unversioned and invalid.')
    census=expected['instrumentation_health'].get('complete_launch_census','UNKNOWN')
    lines=['=== THR-091 Memory Telemetry Report (observation-only) ===','Status: '+obs['status'],epoch_line,
        f"Data through: {expected['data_through']}; timezone UTC",f"First event: {obs['first_impression_at']}",
        f"Days elapsed: {obs['days_elapsed']} / 14",f"Sessions: {obs['total_correlated_sessions']} / 500",
        'Thresholds:    '+('MET' if obs['thresholds_met'] else 'NOT MET'),
        'Population: audited intended task-session invocations; complete launch census '+str(census)]
    for title,rows in [('AGGREGATE',{'all':expected['aggregate']}),('BY AGENT',expected['by_agent']),('BY ROLE',expected['by_role'])]:
        lines.append(title)
        for name,values in sorted(rows.items()):
            lines.append('  ['+name+']')
            lines.extend(f"    {key}: {'unknown' if value is None else value}" for key,value in sorted(values.items()))
    lines.extend(f'{key}: '+json.dumps(expected[key],sort_keys=True,allow_nan=False)
                 for key in ('read_counts','excluded','diagnostic_errors','instrumentation_health'))
    lines.extend(['DECISION: '+expected['decision'],expected['decision_detail']])
    return '\n'.join(lines)+'\n'


def _empty_source_health_expected(epoch,cutoff):
    """Independent empty-memory QA invocation contract, with one real intent."""
    expected=_large_zero_read_expected(epoch,cutoff,cutoff,sessions=0)
    metrics=expected['by_agent'].pop('dev_agent')
    metrics.update(eligible=False,activation_vote_eligible=False,digest_pull_through=None,session_activation=None)
    expected['by_agent']['qa_engineer']=metrics
    for aggregate in (expected['aggregate'],expected['by_role']['worker']):
        aggregate.update(digest_pull_through=None,session_activation=None)
    expected['aggregate'].update(eligible_functional_agents=0,eligible_pointer_agents=0,eligible_agents_below_10_percent=0)
    expected['instrumentation_health'].update(audited_task_starts=1,intended_task_launches=1)
    expected['observation_period'].update(first_impression_at=None,days_elapsed=0,days_met=False,
        sessions_met=False,thresholds_met=False,status='insufficient_sample')
    expected.update(decision='insufficient_sample',evaluation_candidate=False,
        decision_detail='Healthy collection; sample or functional evidence is insufficient.')
    return expected


@pytest.mark.parametrize('g1_source_org',['large-natural'],indirect=True)
def test_original_epoch_day14_500_real_natural_sessions_full_reports(g1_source_org,monkeypatch,capsys):
    """AGE02: real large population, original authority, complete JSON/text.

    INLINE v24: independently specified whole JSON and every text byte from
    BOTH consumers must show the unchanged14-day/500/30 activation gate and
    original epoch, with zero writes. Credible regressions: expire original
    proof at report time, drop a later audit/task page, include synthetic or
    pre-start tuples, change500 to501, or flush partial CLI output. Short-sample
    and arithmetic-only owners never run500 admitted real bootstrap tuples or
    a multi-page protected HTTP report. No production seam or positive inserted
    authority/session rows; supported tasks/real queue/Popen/callback only.
    External provider is SOURCE-only, never installed/tuning acceptance.
    """
    import argparse
    import asyncio
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    accepted_evidence=org.db.read_memory_collection_evidence()
    created=[]
    for index in range(500):
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'dev_agent',
            'brief':json.dumps({'ordinary_work_item':index},sort_keys=True,separators=(',',':'))})
        assert response.status_code==200,response.text
        created.append(response.json()['task_id'])
    # Join the actual queue; never simulate natural credit with purpose flags,
    # manually admitted sessions, raw row insertion or favorable counters.
    asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=1500)
    tables,view,outputs,_=acquire_collection_report(org)
    task_rows={row['id']:row for row in tables['tasks']}
    assert len(set(created))==500
    assert all(task_rows[tid]['status']=='completed' for tid in created),[(tid,task_rows[tid]['note']) for tid in created if task_rows[tid]['status']!='completed']
    identities={row['task_id']:json.loads(row['payload']) for row in tables['audit_log']
                if row['action']=='memory_runtime_identity' and row['task_id'] in created}
    assert set(identities)==set(created)
    assert all(body['population']=='root' and body['parent_known'] is True and body['parent_task_id'] is None
               for body in identities.values())
    impressions=[row for row in tables['audit_log'] if row['action']=='memory_digest_impression' and row['task_id'] in created]
    assert len(impressions)==500
    assert all(json.loads(row['payload'])['pointer_ids']==['MEM-001'] and json.loads(row['payload'])['full_body_ids']==[]
               and json.loads(row['payload'])['session_id']==identities[row['task_id']]['session_id'] for row in impressions)
    assert len(tables['audit_log'])>5000  # Actual later-page acquisition is required.
    first=min(datetime.fromisoformat(row['timestamp']) for row in impressions)
    anchor=max(first,datetime.fromisoformat(epoch['timestamp']))
    ceil=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
    if anchor!=ceil:ceil+=timedelta(days=1)
    cutoff=ceil+timedelta(days=14)
    expected=_large_zero_read_expected(epoch,first,cutoff)
    roles={member['agent']:member['role'] for member in view['installed_identity']['cohort']}
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend['observation_period']['sessions_met'] is True
    assert backend==expected
    short=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff-timedelta(microseconds=1))
    short_expected=copy.deepcopy(expected)
    short_expected['data_through']=(cutoff-timedelta(microseconds=1)).isoformat()
    short_expected['observation_period'].update(days_elapsed=13,days_met=False,thresholds_met=False,status='insufficient_sample')
    short_expected.update(decision='insufficient_sample',evaluation_candidate=False,
        decision_detail='Healthy collection; sample or functional evidence is insufficient.')
    assert short==short_expected
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    captured=capsys.readouterr()
    assert captured.err==''
    assert json.loads(captured.out)==expected
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    captured=capsys.readouterr()
    assert captured.err==''
    assert captured.out==_expected_full_text(expected)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
    # Original acceptance/QA/job/control records are unchanged; added natural
    # work is real history, never substituted as newer canary evidence.
    for table in ('task_results','jobs'):
        current={row['id']:row for row in tables[table]}
        assert all(current[row['id']]==row for row in accepted_evidence[table])


@pytest.mark.parametrize('g1_source_org',['large-natural'],indirect=True)
def test_copied_probe_briefs_after_job_are_natural_and_manual_reads_are_excluded(g1_source_org,monkeypatch,capsys):
    """PRE04/AGE06: copied briefs cannot retrospectively designate natural work.

    INLINE v24:
    1. The actual after-job ROOT copy, its real delegated CHILD and parent
       continuation are three natural invocations. Their two real shown gets,
       two searches and two NONshown gets remain credited; the separate actual
       manual HTTP get/search contribute only excluded manual diagnostics. BOTH
       entire independently specified JSON/full text reports preserve the
       original epoch, with zero reader writes.
    2. Excluding every byte-matching probe brief without its original job/plan
       interval loses these three tuples. Dropping manual get/search traffic from the
       reduction loses its explicit diagnostics; crediting it adds task work.
       The accepted-epoch manual search omission must reach whole-report RED;
       the existing manual-get keeper cannot detect this separate search arm.
    3. real_evidence_refusal_matrix copied-root owns an ambiguous in-interval
       source snapshot. It never executes an unrelated after-job root or a
       manual read against an accepted natural window. The large zero-read
       owner has neither copied briefs nor manual traffic.
    4. No production test seam or positive session/PASS/job injection. Actual
       task POST/queue/bootstrap/delegation/callback and memory HTTP writers
       supply all events; the external provider is SOURCE only.
    """
    import argparse
    import asyncio
    import datetime as datetime_module
    from datetime import datetime,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report,acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    receipt=epoch['payload']['projection']['probe_receipts'][0]
    original=org.db.get_task(receipt['root']['task_id'])
    response=client.post('/api/v1/orgs/alpha/tasks',json={'team':original.team,
        'owner':original.assigned_agent,'brief':original.brief})
    assert response.status_code==200,response.text
    copied=response.json()['task_id']
    asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=45)
    copy_row=org.db.get_task(copied)
    assert copy_row.status.value=='completed' and copy_row.brief==original.brief
    assert copy_row.id not in epoch['payload']['projection']['synthetic_task_ids']
    probe_job=client.get('/api/v1/orgs/alpha/jobs/'+receipt['job_id']).json()
    assert copy_row.created_at>datetime.fromisoformat(probe_job['finished_at'])
    children=[org.db.get_task(task_id) for task_id in org.db.get_children(copied)]
    assert len(children)==1 and children[0].status.value=='completed'
    natural={copied,children[0].id}
    identities=[row for row in org.db.get_audit_logs_by_action('memory_runtime_identity') if row['task_id'] in natural]
    assert len(identities)==3 and len({row['payload']['session_id'] for row in identities})==3
    manual=client.get('/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001')
    assert manual.status_code==200,manual.text
    manual_row=org.db.get_audit_logs_by_action('memory_read')[-1]
    assert 'session_id' not in manual_row['payload'] and 'task_id' not in manual_row['payload']
    assert manual_row['task_id']=='AGENT-dev_agent'
    assert manual_row['payload']=={'id':'MEM-001','slug':manual.json()['slug']}
    manual_search=client.post('/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search',
        json={'query':'scoped','include_kb':False})
    assert manual_search.status_code==200,manual_search.text
    search_row=org.db.get_audit_logs_by_action('memory_search')[-1]
    assert search_row['task_id']=='AGENT-dev_agent'
    assert 'session_id' not in search_row['payload'] and 'task_id' not in search_row['payload']
    assert search_row['payload']['memory_ids']==['MEM-999','MEM-001']
    impressions=[row for row in org.db.get_audit_logs_by_action('memory_digest_impression') if row['task_id'] in natural]
    assert len(impressions)==3
    assert all(row['payload']['pointer_ids']==['MEM-001'] and row['payload']['full_body_ids']==[] for row in impressions)
    first=min(datetime.fromisoformat(row['timestamp']) for row in impressions)
    cutoff=datetime.now(timezone.utc)
    expected=_large_zero_read_expected(epoch,first,cutoff,sessions=3)
    metrics={'pointer_pairs_read':2,'digest_pull_through':2/3,'search_sourced_reads':2,
        'search_sourced_absent_from_digest':2,'search_absent_fraction':1.0,'distinct_valid_read_pairs':4,
        'read_operations':4,'pointer_sessions_activated':2,'session_activation':2/3}
    for values in (expected['aggregate'],expected['by_agent']['dev_agent'],expected['by_role']['worker']):
        values.update(metrics)
    expected['aggregate'].update(digest_sourced_read_pairs=2,eligible_functional_agents=0,
        eligible_pointer_agents=0,eligible_agents_below_10_percent=0)
    expected['by_agent']['dev_agent'].update(eligible=False,activation_vote_eligible=False)
    expected['instrumentation_health']['validated_read_operations']=4
    expected['observation_period'].update(days_elapsed=0,days_met=False,sessions_met=False,
        thresholds_met=False,status='insufficient_sample')
    expected.update(decision='insufficient_sample',evaluation_candidate=False,
        decision_detail='Healthy collection; sample or functional evidence is insufficient.')
    expected['read_counts']={'dev_agent':{mid:{'distinct_pairs':2,'operations':2} for mid in ('MEM-001','MEM-999')}}
    expected['excluded'].update(manual=2,manual_read=1,manual_search=1)
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    roles={member['agent']:member['role'] for member in epoch['payload']['projection']['cohort']}
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend==expected
    remote=acquire_http_collection_report(client,'alpha',current_time=cutoff)
    assert reduce_collection_report(*remote[:3],roles,cutoff)==expected
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    output=capsys.readouterr()
    assert output.err=='' and json.loads(output.out)==expected
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    output=capsys.readouterr()
    assert output.err=='' and output.out==_expected_full_text(expected)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]


@pytest.mark.parametrize('g1_source_org',['natural-boundaries'],indirect=True)
@pytest.mark.parametrize('scenario',['exact-rate','tied-vote'])
def test_real_natural_sample_agent_and_majority_boundaries(g1_source_org,monkeypatch,capsys,scenario):
    """AGE02/G1-P09: real499/500, strict10%, agent29/30 and tied votes.

    INLINE v24, exact-rate and tied-vote:
    1. Independently specified whole backend/canonical HTTP CLI JSON and text
       observe499 insufficient_sample then500 no_demonstrated_problem. Exact
       rate is50/500; tied vote is470 zero-read worker and30 all-read manager,
       with the manager still ineligible at29. Original epoch stays unchanged;
       both readers write zero. All sessions/reads come from real ordinary
       tasks, bootstrap/Popen/callback and canonical memory get, not intent flags.
    2. Credible regressions: aggregate<10% becomes<=10%, agent>=30 becomes>30,
       or strict majority counts a tie. Corresponding complete report oracles
       fail; sample499 also prevents an OR/499 admission regression.
    3. The500 zero-read owner has one eligible voter and zero pull-through,
       so cannot distinguish exact10%, a tied vote or29/30 eligibility. Old
       unversioned arithmetic owners cannot exercise accepted epoch authority.
    4. No production export/hook/flag, fake positive session/PASS/job or natural
       label supplies credit. SOURCE provider performs requested actual work;
       expected values below use only independently specified population/read
       counts and original boundary timestamps, never the report under test.
    """
    import argparse
    import asyncio
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    original=org.db.read_memory_collection_evidence()
    created=[]
    cutoff=None
    for population in (499,500):
        for index in range(len(created),population):
            manager=scenario=='tied-vote' and index>=470
            read=manager if scenario=='tied-vote' else index<49 or index==499
            response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering',
                'owner':'engineering_head' if manager else 'dev_agent',
                'brief':json.dumps({'ordinary_work_item':index,'memory_read':read},sort_keys=True,separators=(',',':'))})
            assert response.status_code==200,response.text
            created.append(response.json()['task_id'])
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=1500)
        assert len(set(created))==population
        _g1_source_failure_receipts(org,created,scenario,str(population))
        assert all(org.db.get_task(tid).status.value=='completed' for tid in created)
        impressions=[row for row in org.db.get_audit_logs_by_action('memory_digest_impression') if row['task_id'] in created]
        assert len(impressions)==population
        assert all(row['payload']['pointer_ids']==['MEM-001'] and row['payload']['full_body_ids']==[] for row in impressions)
        first=min(datetime.fromisoformat(row['timestamp']) for row in impressions)
        if cutoff is None:
            anchor=max(first,datetime.fromisoformat(epoch['timestamp']))
            cutoff=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
            if anchor!=cutoff:cutoff+=timedelta(days=1)
            cutoff+=timedelta(days=14)
        expected=_large_zero_read_expected(epoch,first,cutoff,sessions=population)
        cohorts=({'dev_agent':('worker',population,49 if population==499 else 50)} if scenario=='exact-rate'
                 else {'dev_agent':('worker',470,0),'engineering_head':('manager',population-470,population-470)})
        expected['by_agent']={}
        expected['by_role']={}
        expected['read_counts']={}
        for agent,(role,count,reads) in cohorts.items():
            metrics={'correlated_sessions':count,'pointer_opportunities':count,'full_body_exposures':0,
                'pointer_pairs_read':reads,'digest_pull_through':reads/count,'search_sourced_reads':0,
                'search_sourced_absent_from_digest':0,'search_absent_fraction':None,'distinct_valid_read_pairs':reads,
                'read_operations':reads,'pointer_sessions_activated':reads,'pointer_sessions':count,'session_activation':reads/count}
            expected['by_agent'][agent]={'role':role,'eligible':count>=30,'activation_vote_eligible':count>=30,**metrics}
            expected['by_role'][role]={**metrics,'retrieval_corroboration_eligible':False,'descriptive_only_for_activation_majority':True}
            if reads:expected['read_counts'][agent]={'MEM-001':{'distinct_pairs':reads,'operations':reads}}
        reads=sum(values[2] for values in cohorts.values())
        eligible=1 if population==499 or scenario=='exact-rate' else 2
        low=(1 if scenario=='tied-vote' or population==499 else 0)
        expected['aggregate'].update(pointer_pairs_read=reads,digest_pull_through=reads/population,
            digest_sourced_read_pairs=reads,distinct_valid_read_pairs=reads,read_operations=reads,
            pointer_sessions_activated=reads,session_activation=reads/population,
            eligible_functional_agents=eligible,eligible_pointer_agents=eligible,eligible_agents_below_10_percent=low)
        expected['instrumentation_health']['validated_read_operations']=reads
        decision='insufficient_sample' if population==499 else 'no_demonstrated_problem'
        expected['observation_period'].update(sessions_met=population==500,thresholds_met=population==500,status=decision)
        expected.update(decision=decision,evaluation_candidate=False,decision_detail=(
            'Healthy collection; sample or functional evidence is insufficient.' if population==499 else 'No demonstrated memory problem.'))
        roles={member['agent']:member['role'] for member in epoch['payload']['projection']['installed_identity']['cohort']}
        before=org.db.read_memory_collection_evidence()
        writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
        backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
        assert backend==expected
        class ReaderClock(datetime):
            @classmethod
            def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
        with monkeypatch.context() as clock:
            clock.setattr(datetime_module,'datetime',ReaderClock)
            clock.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
            cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
            output=capsys.readouterr()
            assert output.err=='' and json.loads(output.out)==expected
            cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
            output=capsys.readouterr()
            assert output.err=='' and output.out==_expected_full_text(expected)
        assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
        assert org.db.read_memory_collection_evidence()==before
        assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
        for table in ('task_results','jobs'):
            current={row['id']:row for row in before[table]}
            assert all(current[row['id']]==row for row in original[table])


@pytest.mark.parametrize('fault',['success','measured-loss','job-error','in-flight','output-cap','missing-output','late'])
def test_post_epoch_independent_health_job_truthful_receipts(g1_source_org,tmp_path,monkeypatch,capsys,fault):
    """AGE05: independent actual job execution never renews an accepted epoch.

    INLINE v24, success/measured-loss/job-error/in-flight/output-cap/missing-output/late:
    observe actual QA task/job ownership,
    command/cwd/process/import/source, complete audited outputs, original epoch
    and BOTH full JSON/text readers. Success requires measured healthy output
    after the original start; actual sticky loss closes the readers; nonzero
    or running jobs leave the duty unfulfilled without fabricating health loss.
    The ordinary runner must kill capped output with its actual failed receipt;
    removal of this independently executed job's output leaves missing evidence,
    while neither category damages original epoch instrumentation. Whole healthy
    reports and retained original rows distinguish duty from measured health.
    After each job outcome and any output damage, the canonical HTTP CLI's
    entire JSON and text must equal the backend at one fixed cutoff, with
    empty stderr and zero reader writes. The earlier successful job's own
    reports precede output damage and do not own this final consumer boundary.
    The in-flight variant independently reads the runner-owned PID's OS command,
    executable and cwd while the real process is held at its observation gate.
    This binds the job to execution rather than trusting its own output claim;
    the success variant observes only a completed transcript and cannot own
    this live-process boundary. A wrong executable or job cwd would fail these
    exact OS observations. No runner or process-registration seam is added.
    Credible regressions include treating any unrelated capped job as
    instrumentation failure or acquiring unreferenced health-job output as
    original acceptance evidence; the healthy original report then fails.
    Another is forgetting the observer's sticky error category
    or dropping empty-memory task intents from the report's launch count. The
    measured-loss/whole-success keepers then fail their exact status/count
    oracles. Pre-admission job and AGE04 damage owners
    never execute a post-epoch independent job or own its in-flight/nonzero
    distinction. No production seam, fake PASS/job/session or new health
    envelope: real task/queue/provider callback/job runner and SOURCE-only CLI.
    Every independent canonical command attempt is retained with exact streams,
    exits and hashes. Only an empty-output acquisition_unavailable refusal is
    reacquired within five seconds; rendered unhealthy reports and other errors
    end observation. Dropping these attempt receipts would lose actual failed
    commands; prior whole-report tests run the CLI directly after queue completion
    and do not own observation by an actual running independent job.
    F3 missing-output also adds ordinary untagged JSON job-ID example/prose to
    only the later result. Substring output selection must not make it authority;
    original-output loss and malformed tagged controls remain owned by AGE04/06.
    The late variant pins a test-side subprocess clock to original start+48h+1s
    and keeps the actual wall observation separately. Its real independent
    runner executes the canonical CLI JSON/text at that controlled late cutoff;
    this simulated deadline is unfulfilled, never a physically late production
    receipt. Expiring original admission at report time fails the whole healthy
    report oracle. The success variant cannot own this late job/report boundary.
    No production clock hook, renewed envelope or historical-row edit is used.
    """
    import asyncio
    import hashlib
    import shlex
    import sys
    import time
    from datetime import datetime,timedelta,timezone
    from pathlib import Path
    from runtime.infrastructure.memory_collection import acquire_collection_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    original=org.db.read_memory_collection_evidence()
    identity=epoch['payload']['projection']['installed_identity']
    gate=tmp_path/'health-go'
    job_path=tmp_path/'health-job.json'
    process_measurement=None
    script_file=tmp_path/'health-observe.py'
    script_file.write_text(_G1_HEALTH_JOB)
    request={'epoch_id':epoch['payload']['epoch_id'],'started_at':epoch['timestamp'],
        'identity':identity,'fault':fault,'gate':str(gate) if fault=='in-flight' else None}
    if fault=='late':
        request['source_observation_at']=(datetime.fromisoformat(epoch['timestamp'])+timedelta(hours=48,seconds=1)).isoformat()
    script='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script_file))+' '+shlex.quote(json.dumps(request,sort_keys=True,separators=(',',':')))+'\n'
    brief=json.dumps({'health_check':{'script':script,'job_path':str(job_path)}},sort_keys=True,separators=(',',':'))
    if fault=='measured-loss':
        org.memory_collection.unavailable('measured source-only census loss before independent check')
    response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'qa_engineer','brief':brief})
    assert response.status_code==200,response.text
    health_task=response.json()['task_id']
    try:
        if fault=='in-flight':
            deadline=time.monotonic()+15
            while not Path(str(gate)+'.ready').exists():
                assert time.monotonic()<deadline,'real health job did not enter observation gate'
                time.sleep(.05)
            assert job_path.exists()
            live_job=client.get('/api/v1/orgs/alpha/jobs/'+json.loads(job_path.read_text())['id']).json()
            assert live_job['status']=='running' and live_job['exit_code'] is None
            assert live_job['finished_at'] is None
            # The registry belongs to the ordinary production runner. The
            # stand-in supplies only a PID; independent OS reads establish
            # the actual executable, complete argv and cwd of that handle.
            import os
            import subprocess
            from runtime.daemon.jobs_runner import _INFLIGHT
            proc=_INFLIGHT[live_job['id']]
            announced=json.loads(Path(str(gate)+'.ready').read_text())
            assert announced=={'pid':proc.pid}
            assert proc.returncode is None
            expected_argv=[sys.executable,str(script_file),json.dumps(request,sort_keys=True,separators=(',',':'))]
            if sys.platform=='linux':
                owned_process=Path('/proc')/str(proc.pid)
                measured_argv=owned_process.joinpath('cmdline').read_bytes().removesuffix(b'\0').decode().split('\0')
                measured_executable=str(owned_process.joinpath('exe').resolve(strict=True))
                measured_cwd=str(owned_process.joinpath('cwd').resolve(strict=True))
                assert measured_argv==expected_argv
                assert measured_executable==str(Path(sys.executable).resolve())
            else:
                command=subprocess.check_output(['ps','-ww','-p',str(proc.pid),'-o','command='],text=True).strip()
                assert command==' '.join(expected_argv)
                cwd_rows=subprocess.check_output(['lsof','-a','-p',str(proc.pid),'-d','cwd','-Fn'],text=True).splitlines()
                measured_cwd=next(row[1:] for row in cwd_rows if row.startswith('n'))
                measured_executable=expected_argv[0]
                measured_argv=expected_argv
            assert Path(measured_cwd).resolve()==Path(live_job['cwd_resolved']).resolve()
            assert live_job['cwd_resolved']==str(org.root/'workspaces'/'qa_engineer')
            assert Path(script_file).read_text()==_G1_HEALTH_JOB
            assert proc.returncode is None and _INFLIGHT[live_job['id']] is proc
            process_measurement={'scope':'SOURCE ONLY independent live job-process observation',
                'job_id':live_job['id'],'pid':proc.pid,'observer_pid':os.getpid(),
                'executable':measured_executable,'argv':measured_argv,'cwd':measured_cwd,
                'script_file_sha256':hashlib.sha256(script_file.read_bytes()).hexdigest()}
            assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
            gate.touch()
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=45)
    finally:
        gate.touch()
    assert job_path.exists()
    job_id=json.loads(job_path.read_text())['id']
    job=client.get('/api/v1/orgs/alpha/jobs/'+job_id).json()
    output=client.get('/api/v1/orgs/alpha/jobs/'+job_id+'/output',params={'stream':'both','max_bytes':10485760}).json()
    task=org.db.get_task(health_task)
    result=org.db.get_task_results(health_task)[0]
    assert task.status.value=='completed' and task.assigned_agent=='qa_engineer'
    assert task.brief==brief and result['agent']=='qa_engineer'
    assert result['session_id']!=epoch['payload']['projection']['probe_receipts'][0]['root']['runtime_session_id']
    assert result['verdict'] is None and parse_acceptance(result['output_summary']) is None
    assert job['task_id']==health_task and job['agent_name']=='qa_engineer'
    if fault=='output-cap':
        assert job['status']=='failed' and job['reason']=='output_cap'
        assert job['exit_code']!=0
    else:
        assert job['status']=='completed' and job['exit_code']==(7 if fault=='job-error' else 0)
        assert job['reason'] is None
    assert job['script_text']==script and job['interpreter']=='bash'
    assert job['cwd_resolved']==str(org.root/'workspaces'/'qa_engineer')
    assert output['truncated_stdout'] is (fault=='output-cap')
    assert not output['truncated_stderr']
    if fault!='output-cap':
        assert output['total_stdout_bytes']==len(output['stdout'].encode())
    assert output['total_stderr_bytes']==len(output['stderr'].encode())
    terminal_action='job_run_failed' if fault=='output-cap' else 'job_run_completed'
    audits=[row for row in org.db.get_audit_logs_by_action('job_submitted')+
        org.db.get_audit_logs_by_action('job_auto_started')+org.db.get_audit_logs_by_action(terminal_action)
        if row['payload'].get('script_request_id')==job_id]
    assert {row['action'] for row in audits}=={'job_submitted','job_auto_started',terminal_action}
    finished=next(row['payload'] for row in audits if row['action']==terminal_action)
    assert finished['exit_code']==job['exit_code']
    if fault!='output-cap':
        assert finished['stdout_bytes']==output['total_stdout_bytes'] and finished['stderr_bytes']==output['total_stderr_bytes']
    assert datetime.fromisoformat(epoch['timestamp'])<=datetime.fromisoformat(job['created_at'])<=datetime.fromisoformat(job['started_at'])<=datetime.fromisoformat(job['finished_at'])
    roles={member['agent']:member['role'] for member in identity['cohort']}
    if fault=='job-error':
        assert output['stdout']==''
        assert output['stderr']=='source observation command failed before measurement\n'
    elif fault=='output-cap':
        assert finished['reason']=='output_cap'
        assert '[truncated; see file]' in job['stdout_head']
        assert output['stdout'].startswith('unfulfilled health output ')
        assert job['max_output_bytes']==52428800
        assert job['max_output_bytes']<=output['total_stdout_bytes']<=job['max_output_bytes']+65536
        assert len(output['stdout'].encode())==10485760
        assert output['total_stdout_bytes']<len(('unfulfilled health output '*50000).encode())*50
    else:
        receipt=json.loads(output['stdout'])
        assert receipt['scope']=='SOURCE ONLY' and receipt['epoch_id']==epoch['payload']['epoch_id']
        assert receipt['started_at']==epoch['timestamp']
        original_start=datetime.fromisoformat(epoch['timestamp'])
        observed=datetime.fromisoformat(receipt['observed_at'])
        wall_observed=datetime.fromisoformat(receipt['wall_observed_at'])
        assert original_start<=wall_observed<=datetime.fromisoformat(job['finished_at'])
        if fault=='late':
            assert receipt['clock_scope']=='controlled SOURCE clock'
            assert observed==original_start+timedelta(hours=48,seconds=1)
            assert not (original_start<=observed<=original_start+timedelta(hours=48))
        else:
            assert receipt['clock_scope']=='wall clock' and observed==wall_observed
            assert original_start<=observed<=original_start+timedelta(hours=48)
        assert receipt['script_file_sha256']==hashlib.sha256(script_file.read_bytes()).hexdigest()
        assert receipt['cwd']==job['cwd_resolved']
        assert receipt['runtime_import']==str(Path(identity['source_root'])/'runtime'/'__init__.py')
        assert receipt['cli_identity']=={key:identity[key] for key in ('source_root','python','package_version','files')}
        assert output['stderr']=='source health observation complete; original epoch unchanged\n'
        json_output,text_output=receipt['reports']
        for row in receipt['reports']:
            assert row['exit_code']==0 and row['stderr']=='', row
            assert row['attempts'][-1]=={key:row[key] for key in
                ('exit_code','stdout','stderr','stdout_sha256','stderr_sha256')}
            for attempt in row['attempts']:
                assert attempt['stdout_sha256']==hashlib.sha256(attempt['stdout'].encode()).hexdigest()
                assert attempt['stderr_sha256']==hashlib.sha256(attempt['stderr'].encode()).hexdigest()
            assert all(attempt['exit_code']==1 and attempt['stdout']==''
                and attempt['stderr']=='acquisition_unavailable\n' for attempt in row['attempts'][:-1])
        report=json.loads(json_output['stdout'])
        assert report['instrumentation_health']['status']==('unavailable' if fault=='measured-loss' else 'healthy')
        assert report['epoch']['collection_started'] is (fault!='measured-loss')
        cutoff=datetime.fromisoformat(report['data_through'])
        if fault!='measured-loss':
            assert report['instrumentation_health']['intended_task_launches']==1
            assert report==_empty_source_health_expected(epoch,cutoff)
        assert report==org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
        text=text_output['stdout']
        text_time=next(line.removeprefix('Data through: ').removesuffix('; timezone UTC') for line in text.splitlines() if line.startswith('Data through: '))
        expected_text=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=datetime.fromisoformat(text_time))
        if fault!='measured-loss':
            assert expected_text==_empty_source_health_expected(epoch,datetime.fromisoformat(text_time))
        assert text==_expected_full_text(expected_text)
        if fault=='missing-output':
            # A real successful observation existed. Damage only this later
            # health job stream, preserving its audited byte count and original
            # acceptance job. Missing evidence is an unfulfilled duty.
            Path(job['stdout_path']).unlink()
            missing=client.get('/api/v1/orgs/alpha/jobs/'+job_id+'/output',
                params={'stream':'both','max_bytes':10485760}).json()
            assert missing['stdout']=='' and missing['total_stdout_bytes']==0
            assert finished['stdout_bytes']>0
            assert missing['total_stdout_bytes']!=finished['stdout_bytes']
            # F3: ordinary prose/JSON examples are not tagged evidence. Only
            # this later independent result changes; original authority stays exact.
            later=org.db.get_task_results(health_task)[-1]
            untagged=later['output_summary']+'\nExample reference: '+json.dumps({'job_id':job_id},separators=(',',':'))+'\nOrdinary health summary mentions '+job_id
            assert parse_acceptance(untagged) is None
            with org.db._lock:
                org.db._conn.execute('UPDATE task_results SET output_summary=? WHERE id=?',(untagged,later['id']))
                org.db._conn.commit()
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    now=datetime.now(timezone.utc)
    if fault=='late':
        now=datetime.fromisoformat(request['source_observation_at'])
    from runtime.infrastructure.memory_telemetry_report import ReportAcquisitionUnavailable
    try:
        backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=now)
    except ReportAcquisitionUnavailable as exc:
        pytest.fail(f'Original epoch report must remain readable after {fault}: expected a complete report; observed {exc}')
    assert backend['instrumentation_health']['status']==('unavailable' if fault=='measured-loss' else 'healthy')
    observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert observation['epoch_id']==(None if fault=='measured-loss' else epoch['payload']['epoch_id'])
    import argparse
    import datetime as datetime_module
    from cli.commands.learning import cmd_memory_report
    class FinalReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return now if tz is not None else now.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',FinalReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',FinalReaderClock)
    cmd_memory_report(argparse.Namespace(org='alpha',agent='qa_engineer',json=True))
    final_json=capsys.readouterr()
    assert final_json.err==''
    assert json.loads(final_json.out)==backend
    cmd_memory_report(argparse.Namespace(org='alpha',agent='qa_engineer',json=False))
    final_text=capsys.readouterr()
    assert final_text.err==''
    assert final_text.out==_expected_full_text(backend)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
    assert org.db.get_audit_logs_by_action('memory_collection_invalidated')==[]
    for table in ('task_results','jobs'):
        current={row['id']:row for row in before[table]}
        assert all(current[row['id']]==row for row in original[table])
    print(json.dumps({'case':'AGE05 SOURCE ONLY','variant':fault,'epoch_id':epoch['payload']['epoch_id'],
        'started_at':epoch['timestamp'],'health_task_id':health_task,'job_id':job_id,
        'job_created_at':job['created_at'],'job_started_at':job['started_at'],'job_finished_at':job['finished_at'],
        'exit_code':job['exit_code'],'owner':job['agent_name'],'cwd':job['cwd_resolved'],
        'script_sha256':hashlib.sha256(script.encode()).hexdigest(),
        'stdout_sha256':hashlib.sha256(output['stdout'].encode()).hexdigest(),
        'stderr_sha256':hashlib.sha256(output['stderr'].encode()).hexdigest(),
        'independent_process_measurement':process_measurement,
        'final_json_sha256':hashlib.sha256(final_json.out.encode()).hexdigest(),
        'final_text_sha256':hashlib.sha256(final_text.out.encode()).hexdigest(),
        'measured_health':backend['instrumentation_health']['status'],
        'duty':'unfulfilled SOURCE missed-deadline simulation' if fault=='late' else 'SOURCE ONLY, no production duty fulfilled',
        'original_epoch_unchanged':True,'reader_writes':0},sort_keys=True))


@pytest.mark.parametrize('g1_source_org',['self-qa'],indirect=True)
def test_actual_probe_maker_cannot_supply_independent_qa(g1_source_org):
    """G1-P04 independent role joins: real registered worker is its own maker.

    INLINE v24: an actual admitted structured PASS and genuinely successful
    QA-owned job must still yield zero epochs and closed BOTH reports if QA
    owns the predeclared probe maker slots. Removing maker-independence lets
    this whole valid chain accept; wrong-job-owner negatives stop earlier.
    No production seam, fictional sessions/PASS/jobs or name-based trust rule.
    The same provider-boundary source stand-in drives all authentic records.
    """
    from datetime import datetime,timezone
    from runtime.infrastructure.memory_collection import acquire_collection_report,acquire_http_collection_report,validate_acceptance_evidence
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    candidate=parse_acceptance(org.db.get_task_results(root)[-1]['output_summary'])
    assert candidate['qa_ref']['agent']==candidate['probe_receipts'][0]['root']['agent']=='dev_agent'
    assert org.db.get_task(candidate['qa_ref']['task_id']).status.value=='completed'
    before=org.db.read_memory_collection_evidence()
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[]
    now=datetime.now(timezone.utc)
    local=acquire_collection_report(org,current_time=now)
    remote=acquire_http_collection_report(client,'alpha',current_time=now)
    for evidence in (local,remote):
        with pytest.raises(AcceptanceUnavailable,match='acceptance_maker_independence'):
            validate_acceptance_evidence(*[evidence[0],org.db.get_task_results(root)[-1]['id'],evidence[1],evidence[2]],current_time=now)
        report=reduce_collection_report(*evidence[:3],None,now)
        assert report['epoch']['collection_started'] is False
        assert report['decision']=='insufficient_instrumentation'
    assert org.db.get_task(root).status.value=='completed'
    assert org.db.read_memory_collection_evidence()==before


@pytest.mark.parametrize('kind',['root','child','qa','manager'])
def test_failed_probe_task_closes_original_epoch_in_both_readers(g1_source_org,monkeypatch,capsys,kind):
    """G1-P05/PRE04/AGE04: a failed probe cannot retain positive authority.

    INLINE v24, ROOT, CHILD, original QA and manager: damage only the authentic probe's current task
    state in a private negative fixture, retaining its previously successful
    session/operations/job. BOTH persisted-epoch readers must close at day14,
    with complete JSON/text equality and zero reader writes. A credible
    regression checks the successful first ROOT session but omits its final
    failed task state; the prior job/tuple negatives do not own that gap. The
    real production chain provides every positive record; corruption supplies
    only a refusal. No production hook, invented PASS/job/session or new
    lifecycle writer. A source stand-in never confers installed acceptance.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    claim=(epoch['payload']['qa_ref'] if kind=='qa' else epoch['payload']['manager_ref'] if kind=='manager'
           else epoch['payload']['projection']['probe_receipts'][0][kind])
    assert org.db.get_task(claim['task_id']).status.value=='completed'
    cutoff=datetime.fromisoformat(epoch['timestamp'])+timedelta(days=14)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    roles={member['agent']:member['role'] for member in epoch['payload']['projection']['cohort']}
    healthy=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert healthy['epoch']['collection_started'] is True
    with org.db._lock:
        org.db._conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(claim['task_id'],))
        org.db._conn.commit()
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend['epoch']['collection_started'] is False
    assert backend['decision']=='insufficient_instrumentation' and backend['evaluation_candidate'] is False
    observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert observation['epoch_id'] is None and observation['epoch_audit_id'] is None
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    captured=capsys.readouterr()
    assert captured.err=='' and json.loads(captured.out)==backend
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    captured=capsys.readouterr()
    assert captured.err=='' and captured.out==_expected_full_text(backend)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    with pytest.raises(AcceptanceUnavailable):
        org.db.append_memory_collection_transition(result_row_id=epoch['payload']['manager_ref']['result_id'])
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]


@pytest.mark.parametrize('g1_source_org',['ordinary','operation-admission'],indirect=True)
@pytest.mark.parametrize('kind',['root','child'])
@pytest.mark.parametrize('fault',['read-scope','read-task','read-session','read-agent',
    'search-scope','search-task','search-agent','impression-task','impression-agent'])
def test_probe_operation_exact_scope_and_tuple_closes_both_readers(g1_source_org,request,monkeypatch,capsys,kind,fault):
    """F2/AGE04 exact operation scope and redundant binding, at both real readers.

    INLINE v24, every ROOT/CHILD and named operation fault:
    1. Real SOURCE chain reaches final initial admission or a healthy continuing
       epoch. Corrupt only one actual
       operation's canonical scope or redundant task/session/agent field; real
       Database admission/replay refuses, HTTP serving refs are null, whole CLI
       JSON/text match the closed backend, original controls stay exact, zero writes.
    2. Credible regression: OR row/payload task join accepts a task-scoped read,
       or trusts session alone without redundant agent/task validation.
    3. Failed task and probe-source owners do not change scope or redundant tuple;
       normal telemetry negatives exclude canary evidence from their population.
    4. No production seam or fake positive records. Only a private negative audit
       mutation; queue/bootstrap/jobs/callbacks supplied all original positives.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timedelta
    from cli.commands.learning import cmd_memory_report
    org,client,root=g1_source_org
    epochs=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    initial=bool(getattr(request.node,'_g1_final_refusal',None))
    assert len(epochs)==int(not initial)
    candidate=request.node._g1_final_refusal['candidate'] if initial else epochs[0]['payload']['projection']
    claim=candidate['probe_receipts'][0][kind]
    cutoff=datetime.fromisoformat(org.db.get_task_results(root)[-1]['created_at'])+timedelta(days=14)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    roles={member['agent']:member['role'] for member in candidate['cohort']}
    if not initial:
        healthy=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
        assert healthy['epoch']['collection_started'] is True
        operation,field=fault.split('-')
        action={'read':'memory_read','search':'memory_search','impression':'memory_digest_impression'}[operation]
        row=next(row for row in org.db.get_audit_logs_by_action(action)
                 if row['payload'].get('session_id')==claim['runtime_session_id'])
        payload=copy.deepcopy(row['payload'])
        scope=row['task_id']
        if field=='scope':
            assert scope==('AGENT-'+claim['agent'] if operation=='read' else claim['task_id'])
            scope=claim['task_id'] if operation=='read' else 'AGENT-'+claim['agent']
        else:
            key={'task':'task_id','session':'session_id','agent':'agent'}[field]
            payload[key]={'task':'TASK-999999','session':'sess-unrelated','agent':'qa_engineer'}[field]
        with org.db._lock:
            org.db._conn.execute('UPDATE audit_log SET task_id=?,payload=? WHERE id=?',(scope,json.dumps(payload),row['id']))
            org.db._conn.commit()
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend['epoch']['collection_started'] is False,(kind,fault,backend)
    assert backend['decision']=='insufficient_instrumentation' and backend['evaluation_candidate'] is False
    observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert observation['epoch_id'] is None and observation['epoch_audit_id'] is None
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    output=capsys.readouterr()
    assert output.err=='' and json.loads(output.out)==backend
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    output=capsys.readouterr()
    assert output.err=='' and output.out==_expected_full_text(backend)
    if not initial:
        with pytest.raises(AcceptanceUnavailable):
            org.db.append_memory_collection_transition(result_row_id=org.db.get_task_results(root)[-1]['id'])
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==epochs


def test_current_epoch_evidence_movement_is_acquisition_refusal(g1_source_org,monkeypatch,capsys):
    """AGE04: refuse a moving original census before validating its prefix.

    INLINE v24: the actual serving HTTP gate returns observation_moving with
    no epoch refs; backend acquisition refuses and BOTH canonical CLI forms
    report complete insufficient instrumentation with zero
    reader writes. Credible regression: validate an opening snapshot against
    later census rows before checking the evidence bookends. The existing
    moving-observation variant changes the serving view itself, and cannot
    own movement between that view and the epoch's separate evidence SELECT.
    No production seam: omit one real terminal only in an alternating private
    negative snapshot from the existing reader; no positive facts are forged.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report
    from runtime.infrastructure.memory_telemetry_report import ReportAcquisitionUnavailable,reduce_collection_report
    org,client,root=g1_source_org
    cutoff=datetime.now(timezone.utc)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    _,healthy_view,outputs,_=acquire_collection_report(org,current_time=cutoff)
    roles={member['agent']:member['role'] for member in healthy_view['installed_identity']['cohort']}
    original=org.db.read_memory_collection_evidence
    before=original()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    calls=0
    def moving_evidence():
        nonlocal calls
        calls+=1
        tables=original()
        if calls%2:
            terminal=next(row for row in reversed(tables['audit_log']) if row['action']=='memory_runtime_terminal')
            tables['audit_log']=[row for row in tables['audit_log'] if row['id']!=terminal['id']]
        return tables
    monkeypatch.setattr(org.db,'read_memory_collection_evidence',moving_evidence)
    response=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1})
    assert response.status_code==200
    view=response.json()['memory_collection_observation']
    assert view['observation_error']=='observation_moving',view['observation_error']
    assert view['epoch_id'] is None and view['epoch_audit_id'] is None
    assert view['data_through'] is None
    calls=0
    with pytest.raises(ReportAcquisitionUnavailable):
        acquire_collection_report(org)
    expected=reduce_collection_report(before,view,outputs,roles,cutoff)
    assert expected['decision']=='insufficient_instrumentation'
    assert expected['epoch']['collection_started'] is False
    for json_mode in (True,False):
        calls=0
        cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=json_mode))
        captured=capsys.readouterr()
        assert captured.err==''
        if json_mode:
            assert json.loads(captured.out)==expected
        else:
            assert captured.out==_expected_full_text(expected)
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert original()==before


@pytest.mark.parametrize('fault',['original-qa','original-job','original-output','original-job-audit',
    'original-probe','current-cohort','current-boot','current-profile','current-provider',
    'current-loaded-code','moving-observation','sticky-loss'])
@pytest.mark.parametrize('g1_source_org',['equivalent'],indirect=True)
def test_aged_original_authority_still_requires_evidence_and_current_health(g1_source_org,monkeypatch,capsys,fault):
    """AGE04: actual original damage/current drift closes both day14 readers.

    INLINE v24: an accepted epoch first reads healthy past48h, then each
    isolated original QA/job/output/job-audit/probe-source or current
    cohort/boot/profile/provider/loaded-code/moving-observation/sticky-loss mutation
    withholds exact HTTP refs and BOTH canonical reports without any reader
    writes/new control. Ignoring original evidence or serving health enables
    this regression. Dropping the current_epoch_references validator admits
    original-probe corruption at the real HTTP epoch-reference assertion.
    Missing job audit/probe source and all current identity bookends remain
    conjunctive despite original age; complete text and empty stderr are exact.
    Snapshot-only refusal matrices never damage the actual
    protected HTTP/CLI acquisition at aged original authority. No production
    seam; private negative corruption only, never invented positive provenance.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report,ReportAcquisitionUnavailable
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    cutoff=datetime.fromisoformat(epoch['timestamp'])+timedelta(days=14)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    tables,view,outputs,_=acquire_collection_report(org,current_time=cutoff)
    roles={member['agent']:member['role'] for member in view['installed_identity']['cohort']}
    healthy=reduce_collection_report(tables,view,outputs,None,cutoff)
    assert healthy['instrumentation_health']['status']=='healthy'
    assert healthy['epoch']['id']==epoch['payload']['epoch_id']
    if fault=='original-qa':
        with org.db._lock:
            org.db._conn.execute('UPDATE task_results SET verdict=? WHERE id=?',('FAIL',epoch['payload']['qa_ref']['result_id']))
            org.db._conn.commit()
    elif fault=='original-job':
        with org.db._lock:
            org.db._conn.execute('UPDATE jobs SET exit_code=? WHERE id=?',(7,epoch['payload']['projection']['probe_receipts'][0]['job_id']))
            org.db._conn.commit()
    elif fault=='original-output':
        (org.root/'jobs'/(epoch['payload']['projection']['probe_receipts'][0]['job_id']+'.out')).unlink()
    elif fault=='original-job-audit':
        job_id=epoch['payload']['projection']['probe_receipts'][0]['job_id']
        row=next(row for row in org.db.get_audit_logs_by_action('job_run_completed')
                 if row['payload'].get('script_request_id')==job_id)
        with org.db._lock:
            org.db._conn.execute('DELETE FROM audit_log WHERE id=?',(row['id'],))
            org.db._conn.commit()
    elif fault=='original-probe':
        claim=epoch['payload']['projection']['probe_receipts'][0]['child']
        row=next(row for row in org.db.get_audit_logs_by_action('memory_read')
                 if row['payload'].get('session_id')==claim['runtime_session_id']
                 and row['payload'].get('source')=='search')
        payload={**row['payload'],'source':'explicit_or_other'}
        with org.db._lock:
            org.db._conn.execute('UPDATE audit_log SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
            org.db._conn.commit()
    elif fault in ('current-cohort','current-profile'):
        definition=org.root/'org'/'agents'/'dev_agent.md'
        old,new=('model: null','model: changed-after-admission') if fault=='current-cohort' else ('executor: claude','executor: codex')
        definition.write_text(definition.read_text().replace(old,new))
        assert new in definition.read_text()
    elif fault=='current-boot':
        import uuid
        org.memory_collection.boot_id=str(uuid.uuid4())
    elif fault=='current-provider':
        from pathlib import Path
        from runtime.orchestrator.executor_binary_registry import get_binary
        provider=Path(get_binary('claude'))
        provider.write_text(provider.read_text()+'\n# changed actual executable after admission\n')
    elif fault=='current-loaded-code':
        from runtime.infrastructure import memory_collection as collection_module
        original=collection_module._validate_probe_operations
        def different_loaded_implementation(*args,**kwargs):
            return original(*args,**kwargs)
        monkeypatch.setattr(collection_module,'_validate_probe_operations',different_loaded_implementation)
    elif fault=='moving-observation':
        from runtime.infrastructure import memory_collection as collection_module
        original=org.memory_collection._read_revision
        calls=0
        def moving_revision():
            nonlocal calls
            calls+=1
            revision=original()
            return revision[:-1]+(revision[-1]+calls,)
        # Perturb the test-side SELECT observation without replacing a
        # fingerprinted serving function: this reaches actual bookend drift,
        # rather than the separate loaded-code mismatch guard above.
        monkeypatch.setattr(org.memory_collection,'_read_revision',moving_revision)
    else:
        org.memory_collection.unavailable('measured source instrumentation loss')
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert observation['epoch_id'] is None and observation['epoch_audit_id'] is None
    assert observation['observation_error'] is not None
    if fault=='moving-observation':
        assert observation['observation_error']=='observation_moving'
    elif fault=='current-loaded-code':
        assert observation['observation_error']=='epoch_validation_unavailable'
        assert observation['installed_identity']!=epoch['payload']['projection']['installed_identity']
        assert any('different_loaded_implementation' in row['qualname']
                   for row in observation['installed_identity']['loaded_code'])
    if fault=='original-output':
        with pytest.raises(ReportAcquisitionUnavailable):
            acquire_collection_report(org,current_time=cutoff)
        for json_mode in (True,False):
            with pytest.raises(SystemExit) as exit_info:
                cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=json_mode))
            captured=capsys.readouterr()
            assert exit_info.value.code==1
            assert captured.out=='' and captured.err=='acquisition_unavailable\n'
    else:
        backend=reduce_collection_report(*acquire_collection_report(org,current_time=cutoff)[:3],roles,cutoff)
        assert backend['epoch']['collection_started'] is False
        assert backend['decision']=='insufficient_instrumentation'
        assert backend['evaluation_candidate'] is False
        cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
        captured=capsys.readouterr()
        assert captured.err=='' and json.loads(captured.out)==backend
        cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
        captured=capsys.readouterr()
        assert captured.err=='' and captured.out==_expected_full_text(backend)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]


@pytest.mark.parametrize('g1_source_org',['multi-path'],indirect=True)
def test_two_actual_profiles_require_complete_root_child_receipts(g1_source_org,monkeypatch,capsys):
    """G1-P01/P05/H01: two actual executor paths, one independent QA job.

    INLINE v24: observe one persisted final epoch with complete claude/codex
    ROOT+CHILD tuples, actual2/4/2 per path, independently measured command
    process/source, BOTH whole JSON/full text and zero report writes. Credible
    regression: truncate the required installed profile paths to the first
    path before final validation; the final epoch-count assertion fails.
    test_real_source_chain_final_commit_and_readers uses one profile, so cannot
    own complete multi-path applicability or a shared job's combined operation
    ledger. No production test seam: independent temporary registered agents,
    actual production bootstrap/Popen/HTTP/jobs/callback/result-log only;
    external executables are SOURCE stand-ins, never installed acceptance.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_collection_report,acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    epochs=org.db.get_audit_logs_by_action('memory_collection_epoch_started')
    assert len(epochs)==1,org.db.get_task_results(root)
    epoch=epochs[0]
    candidate=epoch['payload']['projection']
    assert candidate['applicable_paths']==['claude:legacy:none','codex:legacy:none']
    assert [receipt['path'] for receipt in candidate['probe_receipts']]==candidate['applicable_paths']
    assert len(candidate['synthetic_task_ids'])==4
    assert len({receipt['job_id'] for receipt in candidate['probe_receipts']})==1
    tables=org.db.read_memory_collection_evidence()
    for receipt in candidate['probe_receipts']:
        assert receipt['root']['agent']==receipt['child']['agent']
        root_task=next(row for row in tables['tasks'] if row['id']==receipt['root']['task_id'])
        child_task=next(row for row in tables['tasks'] if row['id']==receipt['child']['task_id'])
        assert root_task['parent_task_id'] is None and child_task['parent_task_id']==root_task['id']
        own=[{**row,'payload':json.loads(row['payload'])} for row in tables['audit_log']
             if row['task_id'] in (root_task['id'],child_task['id'])
             or json.loads(row['payload']).get('task_id') in (root_task['id'],child_task['id'])]
        claims=(receipt['root'],receipt['child'])
        first=[row for row in own if any(row['payload'].get('session_id')==claim['runtime_session_id'] for claim in claims)]
        assert [sum(row['action']==action for row in first) for action in ('memory_digest_impression','memory_read','memory_search')]==[2,4,2]
        assert all(row['payload']['executor']==receipt['path'].split(':')[0] for row in first if row['action']=='memory_runtime_identity')
    cutoff=datetime.now(timezone.utc)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    local=acquire_collection_report(org,current_time=cutoff)
    remote=acquire_http_collection_report(client,'alpha',current_time=cutoff)
    roles={member['agent']:member['role'] for member in local[1]['installed_identity']['cohort']}
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend==reduce_collection_report(*remote[:3],roles,cutoff)
    expected=_large_zero_read_expected(epoch,cutoff,cutoff,sessions=0)
    expected['by_agent']={}
    expected['by_role']={}
    expected['aggregate'].update(digest_pull_through=None,session_activation=None,
        eligible_functional_agents=0,eligible_pointer_agents=0,eligible_agents_below_10_percent=0)
    expected['observation_period'].update(first_impression_at=None,days_elapsed=0,days_met=False,
        sessions_met=False,thresholds_met=False,status='insufficient_sample')
    expected.update(decision='insufficient_sample',evaluation_candidate=False,
        decision_detail='Healthy collection; sample or functional evidence is insufficient.')
    assert backend==expected
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    captured=capsys.readouterr()
    assert captured.err=='' and json.loads(captured.out)==expected
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    captured=capsys.readouterr()
    assert captured.err=='' and captured.out==_expected_full_text(expected)
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]


@pytest.mark.parametrize('g1_source_org',['retry-same-parent','retry-recorded'],indirect=True)
def test_real_failed_probe_retry_closure(g1_source_org,monkeypatch,capsys):
    """PRE04/G1-P05: genuine failed history is excluded, never a canary PASS.

    INLINE v24, both named variants:
    1. Actual protected task creation, source-provider nonzero9, ordinary
       admitted parent decisions, real QA-owned job and recorded manager
       supersession produce exactly the original/retry set. Failed child
       stays FAILED under its original parent with its original result/audits.
       BOTH complete reports/HTTP refs remain closed and readers write zero.
    2. Credible regressions: require identical parent for a supported recorded
       supersession retry; omit retry or supporting successor root from the
       exact returned-ID closure; choose copied briefs as retry authority.
       The intended pre-fix RED is acceptance_retry_lineage at the final
       exact-set assertion in the recorded variant, after actual verifier
       acceptance and all real callback/job/consumer assertions.
    3. The failed_probe_task owner corrupts one previously successful task;
       it does not execute a genuine failed provider or create a retry through
       real run_step/recorded supersession. The retry database tests own spawn
       admission, not this source command/closure/BOTH-report boundary.
    4. No production test seam, manually registered session, inserted PASS or
       invented job. The real server verifier is used by admission, locked
       commit and serving validation. The provider supplies SOURCE only.
       Withholding the raw recorded edge at the SELECT boundary with
       intact paired audits refuses; append-only history remains intact.
       Every omitted returned-ID refuses. Additional raw-read negative cases
       failed-state, failed-agent, nested-successor, subtask-successor,
       predecessor-state, predecessor-team, predecessor-manager and missing
       paired-audit own the verifier-to-exact-closure boundary. Bypassing that
       verifier while trusting public audits incorrectly admits these damages;
       existing database owners stop at lineage admission, not probe closure.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.database import VerifiedRetry
    from runtime.infrastructure.memory_collection import (
        _decoded_tables,_resolve_probe_slots,acquire_collection_report,
        acquire_http_collection_report,
    )
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,operational_root=g1_source_org
    result=org.db.get_task_results(operational_root)[-1]
    candidate=parse_acceptance(result['output_summary'])
    qa=org.db.get_task(candidate['qa_ref']['task_id'])
    plan=json.loads(qa.brief)
    receipt=candidate['probe_receipts'][0]
    failed=org.db.get_task(receipt['child']['task_id'])
    original_root=org.db.get_task(receipt['root']['task_id'])
    assert failed.status.value=='failed' and failed.parent_task_id==original_root.id
    assert failed.revisit_of_task_id is None
    retries=org.db.get_direct_revisits(failed.id)
    assert len(retries)==1,retries
    retry=org.db.get_task(retries[0])
    assert retry.status.value=='completed' and retry.assigned_agent==failed.assigned_agent
    path=org.db.verify_retry_link(retry.parent_task_id,retry.assigned_agent,failed.id)
    expected_path=((original_root.id,) if retry.parent_task_id==original_root.id
                   else (retry.parent_task_id,original_root.id))
    assert path==VerifiedRetry(expected_path),path
    if len(expected_path)==2:
        assert original_root.status.value=='superseded'
        records=org.db.fetch_all_readonly('SELECT * FROM manager_supersessions WHERE predecessor_task_id=?',(original_root.id,))
        assert len(records)==1 and records[0]['successor_task_id']==retry.parent_task_id
        paired=[row for row in org.db.get_audit_logs_by_action('manager_supersession')
                if row['task_id'] in expected_path]
        assert len(paired)==2 and {row['task_id'] for row in paired}==set(expected_path)
    job_response=client.get('/api/v1/orgs/alpha/jobs/'+receipt['job_id'])
    job_response.raise_for_status()
    job=job_response.json()
    assert job['status']=='completed' and job['exit_code']==0 and job['agent_name']==candidate['qa_ref']['agent']
    streams=client.get('/api/v1/orgs/alpha/jobs/'+receipt['job_id']+'/output',params={'stream':'both','max_bytes':10485760}).json()
    assert streams['truncated_stdout'] is False and streams['truncated_stderr'] is False
    transcript=json.loads(streams['stdout'])
    expected=set(expected_path)|{failed.id,retry.id}
    assert set(transcript['returned_task_ids'])==expected
    assert set(candidate['synthetic_task_ids'])==expected
    cutoff=datetime.now(timezone.utc)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    local=acquire_collection_report(org,current_time=cutoff)
    remote=acquire_http_collection_report(client,'alpha',current_time=cutoff)
    roles={member['agent']:member['role'] for member in local[1]['installed_identity']['cohort']}
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend==reduce_collection_report(*remote[:3],roles,cutoff)
    assert backend['epoch']['collection_started'] is False and backend['decision']=='insufficient_instrumentation'
    assert backend['evaluation_candidate'] is False
    view=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert view['epoch_id'] is None and view['epoch_audit_id'] is None
    cmd_memory_report(argparse.Namespace(org='alpha',agent='qa_engineer',json=True))
    output=capsys.readouterr()
    assert output.err=='' and json.loads(output.out)==backend
    cmd_memory_report(argparse.Namespace(org='alpha',agent='qa_engineer',json=False))
    output=capsys.readouterr()
    assert output.err=='' and output.out==_expected_full_text(backend)
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[]
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_task(failed.id).status.value=='failed'
    # This is the actual predesignated command/record boundary. A failed
    # original cannot pass canary operations, but its retry closure still has
    # to be derivable exactly without losing or relabeling its history.
    data=_decoded_tables(before)
    assert _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                               {job['id']:job},{job['id']:transcript},
                               retry_verifier=org.db.verify_retry_link)==expected
    from runtime.infrastructure.memory_collection import AcceptanceUnavailable
    if len(expected_path)==2:
        # The complete paired public audits alone cannot authenticate the
        # raw recorded edge. The production verifier, not brief matching,
        # owns that authority both before and inside the final transaction.
        with pytest.raises(AcceptanceUnavailable,match='acceptance_retry_lineage'):
            _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                                 {job['id']:job},{job['id']:transcript})
        connection=org.db._conn
        class MissingRecordedEdge:
            def execute(self,sql,params=()):
                if sql.startswith('SELECT * FROM manager_supersessions WHERE successor_task_id='):
                    return connection.execute('SELECT * FROM manager_supersessions WHERE 0')
                return connection.execute(sql,params)
            def __getattr__(self,name):return getattr(connection,name)
        with monkeypatch.context() as missing_edge:
            missing_edge.setattr(org.db,'_conn',MissingRecordedEdge())
            with pytest.raises(AcceptanceUnavailable,match='acceptance_retry_lineage'):
                _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                                     {job['id']:job},{job['id']:transcript},
                                     retry_verifier=org.db.verify_retry_link)
        assert [dict(row) for row in org.db.fetch_all_readonly(
            'SELECT * FROM manager_supersessions WHERE predecessor_task_id=?',(original_root.id,))]==[dict(row) for row in records]
        # Refuse damaged authority via read-only adapters over the actual
        # production verifier. These never write/replace a persisted record,
        # grant positive evidence or call an invented retry implementation.
        variants=[('failed-state',failed.id,'status','completed'),
                  ('failed-agent',failed.id,'assigned_agent','qa_engineer'),
                  ('nested-successor',retry.parent_task_id,'parent_task_id',original_root.id),
                  ('subtask-successor',retry.parent_task_id,'task_type','subtask'),
                  ('predecessor-state',original_root.id,'status','completed'),
                  ('predecessor-team',original_root.id,'team','different-team'),
                  ('predecessor-manager',original_root.id,'assigned_agent','qa_engineer'),
                  ('missing-paired-audit',None,None,None)]
        for fault,target,field,value in variants:
            class DamagedRetryRead:
                def execute(self,sql,params=()):
                    cursor=connection.execute(sql,params)
                    if sql=='SELECT * FROM tasks WHERE id=?' and params==(target,):
                        row=cursor.fetchone()
                        class ChangedRow:
                            def fetchone(self):return {**dict(row),field:value}
                        return ChangedRow()
                    if fault=='missing-paired-audit' and sql.startswith('SELECT agent, payload FROM audit_log'):
                        return connection.execute('SELECT agent, payload FROM audit_log WHERE 0')
                    return cursor
                def __getattr__(self,name):return getattr(connection,name)
            with monkeypatch.context() as damaged:
                damaged.setattr(org.db,'_conn',DamagedRetryRead())
                with pytest.raises(AcceptanceUnavailable,match='acceptance_retry_lineage'):
                    _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                                         {job['id']:job},{job['id']:transcript},
                                         retry_verifier=org.db.verify_retry_link)
            assert org.db.read_memory_collection_evidence()==before,fault
    assert _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                               {job['id']:job},{job['id']:transcript},
                               retry_verifier=org.db.verify_retry_link)==expected
    for missing in expected:
        incomplete={**transcript,'returned_task_ids':[task for task in transcript['returned_task_ids'] if task!=missing]}
        with pytest.raises(AcceptanceUnavailable,match='acceptance_slot_mapping|acceptance_returned_task_set'):
            _resolve_probe_slots(data,plan,candidate['probe_receipts'],
                                 {job['id']:job},{job['id']:incomplete},
                                 retry_verifier=org.db.verify_retry_link)
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.get_task(failed.id).status.value=='failed'


@pytest.mark.parametrize('g1_source_org',['large-natural'],indirect=True)
@pytest.mark.parametrize('scenario',['fullbody-empty','sparse-duplicates'])
@pytest.mark.parametrize('population',[3,500])
def test_real_fullbody_empty_and_sparse_natural_reports(g1_source_org,monkeypatch,capsys,scenario,population):
    """R26/R27 source cases at actual natural writers and BOTH final consumers.

    INLINE v24, fullbody-empty/sparse-duplicates at population3/500:
    1. Real canonical tasks, bootstrap/renderer/provider/callback produce
       either empty launches plus full-body-only impressions with zero pointer
       voters, or one natural tuple with three genuine CLI gets of one shown
       pointer. Entire independently expected backend/HTTP/CLI JSON/full text
       preserves original epoch and zero reader writes: fullbody Q500/B500/
       P0/L530 stays insufficient_sample; sparse Q500/X1/R1/ops3 is activation
       loss after14 complete days. Small3 remains sample-insufficient.
    2. Dropping full-body cardinality, equating repeat operations with distinct
       pairs, or leaking preliminary output before complete authority acquisition
       fails the whole-report oracles. Corrected acceptance requires actual
       census and canary, not a fabricated healthy population.
    3. The zero-read500 owner has pointers/no bodies or reads. Rate/vote owners
       have distinct one-read tuples and no disabled natural renderer; the
       unversioned R26/R27 arithmetic owners cannot reach accepted source health.
       Short3 variants isolate keeper sensitivity;500 variants own sample gates.
    4. No production flag/export/hook or positive session/result/job injection.
       Fixture config/memory change only renders actual disposable natural work.
       Read-count request controls the SOURCE provider's real CLI work, not
       population intent. All records come from real production writers.
    """
    import argparse
    import asyncio
    import datetime as datetime_module
    from datetime import datetime,timedelta,timezone
    from cli.commands.learning import cmd_memory_report
    from runtime.infrastructure.memory_collection import acquire_http_collection_report
    from runtime.infrastructure.memory_telemetry_report import reduce_collection_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    original=org.db.read_memory_collection_evidence()
    natural=[]
    empty=[]
    def create(index,*,reads=0):
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'dev_agent',
            'brief':json.dumps({'ordinary_work_item':index,'memory_read':reads},sort_keys=True,separators=(',',':'))})
        assert response.status_code==200,response.text
        return response.json()['task_id']
    def finish():
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=1500)
    if scenario=='fullbody-empty':
        org.orchestrator._paths.org_config_path.write_text('memory_digest_budget: 0\n')
        for index in range(30 if population==500 else 2):empty.append(create('empty-'+str(index)))
        finish()
        # Canary operations and original evidence remain in the DB; later
        # fixture memory content is independently rendered, never relabeled.
        (org.root/'workspaces'/'dev_agent'/'memory'/'MEM-999-nonshown.md').unlink()
        org.orchestrator._paths.org_config_path.write_text('memory_digest_budget: 255\n')
    for index in range(population):
        natural.append(create(index,reads=3 if scenario=='sparse-duplicates' and index==0 else 0))
    finish()
    assert len(set(natural+empty))==population+len(empty)
    assert all(org.db.get_task(tid).status.value=='completed' for tid in natural+empty)
    identities=[row for row in org.db.get_audit_logs_by_action('memory_runtime_identity') if row['task_id'] in natural+empty]
    assert len(identities)==population+len(empty)
    assert len({row['payload']['session_id'] for row in identities})==population+len(empty)
    expectations=[row for row in org.db.get_audit_logs_by_action('memory_runtime_expectation') if row['task_id'] in empty]
    assert len(expectations)==len(empty)
    assert all(row['payload']['state']=='disabled' and row['payload']['reason']=='budget_zero' for row in expectations)
    impressions=[row for row in org.db.get_audit_logs_by_action('memory_digest_impression') if row['task_id'] in natural+empty]
    assert len(impressions)==population
    bodies=scenario=='fullbody-empty'
    assert all(row['task_id'] in natural and row['payload']['pointer_ids']==([] if bodies else ['MEM-001'])
               and row['payload']['full_body_ids']==(['MEM-001'] if bodies else []) for row in impressions)
    reads=[row for row in org.db.get_audit_logs_by_action('memory_read') if row['payload'].get('task_id') in natural]
    assert len(reads)==(0 if bodies else 3)
    if reads:
        assert {row['payload']['session_id'] for row in reads}=={row['payload']['session_id'] for row in identities if row['task_id']==natural[0]}
        assert all(row['payload']['id']=='MEM-001' and row['payload']['source']=='digest' for row in reads)
    first=min(datetime.fromisoformat(row['timestamp']) for row in impressions)
    anchor=max(first,datetime.fromisoformat(epoch['timestamp']))
    cutoff=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
    if anchor!=cutoff:cutoff+=timedelta(days=1)
    cutoff+=timedelta(days=14)
    expected=_large_zero_read_expected(epoch,first,cutoff,sessions=population)
    eligible=population>=30
    if bodies:
        for values in (expected['aggregate'],expected['by_agent']['dev_agent'],expected['by_role']['worker']):
            values.update(pointer_opportunities=0,full_body_exposures=population,digest_pull_through=None,
                          pointer_sessions=0,session_activation=None)
        expected['aggregate'].update(eligible_pointer_agents=0,eligible_agents_below_10_percent=0)
        expected['by_agent']['dev_agent']['activation_vote_eligible']=False
        expected['instrumentation_health'].update(audited_task_starts=population+len(empty),intended_task_launches=population+len(empty))
        decision='insufficient_sample'
    else:
        for values in (expected['aggregate'],expected['by_agent']['dev_agent'],expected['by_role']['worker']):
            values.update(pointer_pairs_read=1,digest_pull_through=1/population,distinct_valid_read_pairs=1,
                          read_operations=3,pointer_sessions_activated=1,session_activation=1/population)
        expected['aggregate']['digest_sourced_read_pairs']=1
        expected['instrumentation_health'].update(validated_read_operations=3,repeated_read_pairs=2)
        expected['read_counts']={'dev_agent':{'MEM-001':{'distinct_pairs':1,'operations':3}}}
        expected['by_agent']['dev_agent']['activation_vote_eligible']=eligible
        expected['aggregate'].update(eligible_pointer_agents=int(eligible),eligible_agents_below_10_percent=int(eligible))
        decision='activation_loss' if population>=500 else 'insufficient_sample'
    expected['aggregate']['eligible_functional_agents']=int(eligible)
    expected['by_agent']['dev_agent']['eligible']=eligible
    expected['observation_period'].update(sessions_met=population>=500,thresholds_met=population>=500,status=decision,
        reason_code='functional_population' if bodies and population>=500 else 'sample')
    expected.update(decision=decision,evaluation_candidate=decision=='activation_loss',decision_detail=(
        'Evaluation only; no tuning is executed.' if decision=='activation_loss'
        else 'Healthy collection; sample or functional evidence is insufficient.'))
    before=org.db.read_memory_collection_evidence()
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    roles={member['agent']:member['role'] for member in epoch['payload']['projection']['cohort']}
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
    assert backend==expected
    remote=acquire_http_collection_report(client,'alpha',current_time=cutoff)
    assert reduce_collection_report(*remote[:3],roles,cutoff)==expected
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
    captured=capsys.readouterr()
    assert captured.err=='' and json.loads(captured.out)==expected
    cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
    captured=capsys.readouterr()
    assert captured.err=='' and captured.out==_expected_full_text(expected)
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
    for table in ('task_results','jobs'):
        current={row['id']:row for row in before[table]}
        assert all(current[row['id']]==row for row in original[table])


def test_canonical_epoch_acquisition_error_never_flushes_partial_report(g1_source_org,monkeypatch,capsys):
    """G1-P09/R24: actual complete authority acquisition owns output atomicity.

    INLINE v24:
    1. Both canonical JSON/text commands encounter an actual original job-output
       HTTP read failure after the real four-stream sweeps/task/result reads.
       Each exits1 with exact acquisition_unavailable stderr, empty stdout,
       unchanged epoch/history and zero database writes.
    2. Rendering or flushing preliminary diagnostics before final original-job
       acquisition leaks stdout at this oracle. The keeper inserts premature
       output before acquire_http_collection_report, restores bytes and GREEN.
    3. Existing missing-original-output owner exercises empty persisted files;
       it does not fail an HTTP response after successful source stream reads
       or assert exact no-output behavior for BOTH JSON/text commands.
    4. No production seam/flag: a test-side client adapter delegates every real
       HTTP read and fails only the original output response; authority is
       never supplied. SOURCE fixture establishes the authentic original epoch.
    """
    import argparse
    import httpx
    from cli.commands.learning import cmd_memory_report
    from cli.client.client import OpcClient
    from runtime.infrastructure.memory_telemetry_report import ReportAcquisitionUnavailable
    org,client,root=g1_source_org
    before=org.db.read_memory_collection_evidence()
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    seen=[]
    class FailedOriginalOutput:
        def get(self,path,params=None):
            response=client.get(path,params=params)
            response.raise_for_status()
            seen.append((path,params))
            if path.endswith('/output'):
                return httpx.Response(503,json={'error':'owned source read failure'},request=response.request)
            return response
    monkeypatch.setattr(OpcClient,'from_env',classmethod(lambda cls:FailedOriginalOutput()))
    for as_json in (True,False):
        seen.clear()
        with pytest.raises(SystemExit) as failed:
            cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=as_json))
        assert failed.value.code==1
        captured=capsys.readouterr()
        assert captured.out==''
        assert captured.err==ReportAcquisitionUnavailable.category+'\n'
        assert seen[-1][0].endswith('/output')
        assert any('/tasks/' in path for path,_ in seen)
        assert {params['action'] for path,params in seen if path.endswith('/audit') and params and params.get('action')}.issuperset(
            {'session_start','memory_digest_impression','memory_read','memory_search'})
    assert org.db.read_memory_collection_evidence()==before
    assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]

@pytest.mark.parametrize('g1_source_org',['natural-boundaries'],indirect=True)
@pytest.mark.parametrize('fault',['command-failure','command-exception'])
def test_natural_source_failed_command_receipts(g1_source_org,tmp_path,capsys,fault):
    """v24, both cases: observe full pytest failure stdout for a real failed
    natural-source task/session/result and command. Removing emission loses
    those receipts while completion stays failed. Natural500 owners only assert
    completion and cannot retain a known negative's complete hosted log. No
    production hook: faults change only this disposable external provider/CLI.
    """
    import asyncio
    import os
    import sys
    from pathlib import Path

    org,client,_=g1_source_org
    provider=tmp_path/'claude'
    original=provider.read_bytes()
    config_path=Path(os.environ['G1_SOURCE_CONFIG'])
    real_cli=str(Path(sys.executable).parent/'happyranch')
    negative_cli=tmp_path/'negative-cli'
    actual=tmp_path/'actual-subprocess.json'
    if fault=='command-failure':
        # A real canonical missing-memory failure, with long distinct streams
        # that would be lost by an ExecutorResult tail or an abbreviated log.
        negative_cli.write_text('#!'+sys.executable+'\n'+
            'import json, subprocess, sys\nfrom pathlib import Path\n'+
            'argv='+repr([real_cli])+"+sys.argv[1:]\nargv[-2]='MEM-404'\n"+
            'answer=subprocess.run(argv,capture_output=True,text=True,timeout=10)\n'+
            "out='DIAGNOSTIC-STDOUT-BEGIN\\n'+'o'*12000+'\\n'+answer.stdout\n"+
            "err='DIAGNOSTIC-STDERR-BEGIN\\n'+'e'*12000+'\\n'+answer.stderr\n"+
            'Path('+repr(str(actual))+').write_text(json.dumps({"argv":argv,"stdout":out,"stderr":err,"exit":answer.returncode}))\n'+
            'sys.stdout.write(out)\nsys.stderr.write(err)\nraise SystemExit(answer.returncode)\n')
        negative_cli.chmod(0o755)
    # command-exception deliberately names an absent executable, producing an
    # actual FileNotFoundError rather than a synthesized ExecutorResult.
    provider.write_text(original.decode().replace(
        "cli = str(Path(sys.executable).parent / 'happyranch')",
        'cli = '+repr(str(negative_cli))))
    try:
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'dev_agent',
            'brief':json.dumps({'ordinary_work_item':'diagnostic-negative','memory_read':1})})
        assert response.status_code==200,response.text
        tid=response.json()['task_id']
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=30)
    finally:
        provider.write_bytes(original)
    task=org.db.get_task(tid)
    assert task.status.value=='failed'
    with pytest.raises(AssertionError):
        assert all(org.db.get_task(t).status.value=='completed' for t in [tid])
    capsys.readouterr()
    _g1_source_failure_receipts(org,[tid],fault,'negative')
    output=capsys.readouterr().out
    assert 'SOURCE task/provider failure receipts' in output,('missing attributable failure log',output)
    receipt=json.loads(output)
    assert receipt['task_id']==tid and receipt['session_id']==task.current_session_id
    assert receipt['task']['status']=='failed' and receipt['task']['note']==task.note
    assert receipt['results']==org.db.get_task_results(tid)
    assert receipt['executor']['value']['result']['success'] is False
    assert receipt['executor']['value']['result']['session_id']==task.current_session_id
    assert receipt['actual_callback']['state']=='missing' and receipt['parsed_report'] is None
    command=receipt['commands'][-1]
    assert command['argv']==[str(negative_cli),'memory','get','--org','alpha','--agent','dev_agent','MEM-001','--json']
    if fault=='command-failure':
        actual_receipt=json.loads(actual.read_text())
        assert actual_receipt['exit']!=0
        assert command['exit']==actual_receipt['exit']
        assert command['stdout']==actual_receipt['stdout'] and command['stderr']==actual_receipt['stderr']
        assert 'DIAGNOSTIC-STDOUT-BEGIN' in receipt['provider_error']['text']
        assert 'DIAGNOSTIC-STDERR-BEGIN' in receipt['provider_stderr']['text']
    else:
        assert 'FileNotFoundError' in command['exception']
        assert command['stdout'] is None and command['stderr'] is None and 'exit' not in command
        assert 'FileNotFoundError' in receipt['provider_error']['text']
    assert provider.read_bytes()==original

    # Observe a real successful return/callback through the same recording path;
    # it produces supporting files and no false failure transcript.
    response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'dev_agent',
        'brief':'{"ordinary_work_item":"diagnostic-success","memory_read":1}'})
    assert response.status_code==200,response.text
    success_id=response.json()['task_id']
    asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=30)
    assert org.db.get_task(success_id).status.value=='completed'
    capsys.readouterr()
    success=_g1_source_failure_receipts(org,[success_id],fault,'success')[0]
    assert capsys.readouterr().out==''
    assert success['executor']['value']['result']['success'] is True
    assert success['executor']['value']['result']['returncode']==0
    assert success['parsed_report']['task_id']==success_id
    assert success['actual_callback']['state']=='persisted'
    assert success['actual_callback']['payload']['value']['session_id']==success['session_id']
    assert all(c['exit']==0 for c in success['commands'])
    assert config_path.is_file()


@pytest.mark.parametrize('g1_source_org',['natural-boundaries'],indirect=True)
@pytest.mark.parametrize('fault',['missing-callback','executor-exception','recorder-error',
                                  'nested-commands','evidence-read-error'])
def test_natural_source_missing_callback_and_exception_receipts(g1_source_org,tmp_path,monkeypatch,capsys,fault):
    """v24, all cases: full failed task/session evidence distinguishes clean
    return without callback, actual producer exception, and recorder loss.
    Removing emission loses that distinction without changing failed tasks.
    The failed-command owner cannot cover clean exit0 or a pre-Popen exception;
    natural500 owners have no deliberate missing/partial/foreign receipt case.
    No production hook: only disposable provider bytes and test-side negatives
    at the existing launch/receipt-write boundary; no positive is fabricated.

    D1013-01 v24, nested-commands and evidence-read-error:
    1. The full emitted JSON retains the real failed task/status/note/results,
       exact session, clean ExecutorResult exit0, parsed report, provider record
       and complete stdout/stderr, with missing callback. Damaged commands and
       independent result/stream read failures stay explicitly unavailable.
    2. The published helper's commands:1 iteration or all-or-nothing reader
       fallback discards the acquired session; RED observes that lost identity.
       Restoring the corrected helper byte-exactly gives GREEN on the same seam.
    3. The existing missing-callback case owns lost/partial/foreign files, but
       not attributable malformed nested data or a later DB/stream-reader error.
       The failed-command owner observes subprocess failures, not recorder loss.
    4. No production seam. Actual queue/provider/session/result writers run;
       only disposable recorded data and independent readers are damaged, with
       finally restoration. No successful task/result/callback is manufactured.
    """
    import asyncio
    import os
    from pathlib import Path

    org,client,_=g1_source_org
    provider=tmp_path/'claude'
    original=provider.read_bytes()
    provider.write_text(original.decode().replace(
        "file = Path(os.environ['HAPPYRANCH_DAEMON_HOME'])/(sid+'.completion.json')",
        "if brief.get('ordinary_work_item')=='diagnostic-negative':\n        raise SystemExit(0)\n    file = Path(os.environ['HAPPYRANCH_DAEMON_HOME'])/(sid+'.completion.json')"))
    original_launch=org.orchestrator._launch_agent_with_scratch
    producer_error=RuntimeError('distinct actual producer launch exception')
    observed=[]
    if fault=='executor-exception':
        def failed_launch(**kwargs):
            observed.append((kwargs['task_id'],kwargs['session_id']))
            raise producer_error
        monkeypatch.setattr(org.orchestrator,'_launch_agent_with_scratch',failed_launch)
        original_recorded=org.orchestrator._run_agent
        def observe_exception(*args,**kwargs):
            try:
                return original_recorded(*args,**kwargs)
            except BaseException as exc:
                assert exc is producer_error
                observed.append(exc)
                raise
        monkeypatch.setattr(org.orchestrator,'_run_agent',observe_exception)
    if fault=='recorder-error':
        original_write=Path.write_text
        def lost_executor_record(path,*args,**kwargs):
            if path.name.endswith('.executor.json'):
                raise OSError('distinct executor recorder loss')
            return original_write(path,*args,**kwargs)
        monkeypatch.setattr(Path,'write_text',lost_executor_record)
        def lost_provider_record(**kwargs):
            Path(os.environ['G1_SOURCE_CONFIG']+'.'+kwargs['session_id']+'.provider.json').mkdir()
            return original_launch(**kwargs)
        monkeypatch.setattr(org.orchestrator,'_launch_agent_with_scratch',lost_provider_record)
    try:
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':'dev_agent',
            'brief':'{"ordinary_work_item":"diagnostic-negative","memory_read":1}'})
        assert response.status_code==200,response.text
        tid=response.json()['task_id']
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=30)
    finally:
        provider.write_bytes(original)
    task=org.db.get_task(tid)
    assert task.status.value=='failed'
    with pytest.raises(AssertionError):
        assert all(org.db.get_task(t).status.value=='completed' for t in [tid])
    recorder_output=capsys.readouterr()
    _g1_source_failure_receipts(org,[tid],fault,'negative')
    output=capsys.readouterr().out
    assert 'SOURCE task/provider failure receipts' in output,('missing attributable failure log',output)
    receipt=json.loads(output)
    assert receipt['task_id']==tid and receipt['session_id']==task.current_session_id
    assert receipt['actual_callback']['state']=='missing'
    assert receipt['actual_callback']['command'] is None
    assert receipt['actual_callback']['payload']['state']=='missing'
    assert receipt['parsed_report'] is None and receipt['results_state']=='missing'
    if fault=='executor-exception':
        assert observed==[(tid,task.current_session_id),producer_error]
        assert 'distinct actual producer launch exception' in receipt['executor']['value']['exception']
        assert receipt['executor']['value']['result'] is None
        assert receipt['provider']['state']=='missing' and receipt['commands_state']=='missing'
    elif fault=='recorder-error':
        assert receipt['executor']['state']=='missing' and receipt['provider']['state']=='unavailable'
        assert 'distinct executor recorder loss' in recorder_output.err
        assert tid in recorder_output.err and task.current_session_id in recorder_output.err
        assert 'SOURCE recorder failure' in receipt['provider_stderr']['text']
    else:
        assert receipt['executor']['value']['result']['success'] is True
        assert receipt['executor']['value']['result']['returncode']==0
        assert all(c['exit']==0 for c in receipt['commands'])
        # Retained files can be lost, partial or foreign: each condition must
        # stay explicit and cannot lend another session's successful receipt.
        config_path=Path(os.environ['G1_SOURCE_CONFIG'])
        provider_record=Path(str(config_path)+'.'+task.current_session_id+'.provider.json')
        executor_record=tmp_path/(task.current_session_id+'.executor.json')
        saved_provider=provider_record.read_bytes();saved_executor=executor_record.read_bytes()
        try:
            provider_record.unlink()
            executor_record.write_text('{"partial":')
            _g1_source_failure_receipts(org,[tid],fault,'lost')
            lost=json.loads(capsys.readouterr().out)
            assert lost['provider']['state']=='missing' and lost['commands_state']=='missing'
            assert lost['executor']['state']=='malformed' and lost['executor']['raw']=='{"partial":'
            provider_record.write_text(json.dumps({'task_id':tid,'session_id':task.current_session_id}))
            executor_record.write_text(json.dumps({'task_id':'TASK-999999','session_id':'sess-foreign','result':{'success':True}}))
            _g1_source_failure_receipts(org,[tid],fault,'partial')
            partial=json.loads(capsys.readouterr().out)
            assert partial['commands_state']=='missing'
            assert partial['executor']['state']=='identity_mismatch' and 'value' not in partial['executor']
        finally:
            provider_record.write_bytes(saved_provider);executor_record.write_bytes(saved_executor)
        assert provider_record.read_bytes()==saved_provider and executor_record.read_bytes()==saved_executor
        if fault in ('nested-commands','evidence-read-error'):
            actual_provider=json.loads(saved_provider)
            actual_executor=json.loads(saved_executor)
            actual_results=org.db.get_task_results(tid)
            actual_stdout=Path(str(config_path)+'.'+task.current_session_id+'.stdout').read_text()
            actual_stderr=Path(str(config_path)+'.'+task.current_session_id+'.stderr').read_text()
            assert task.note=='agent session failed (rc=0; no completion callback)'
            assert actual_results==[] and actual_executor['report'] is None
            assert actual_executor['result']['returncode']==0 and actual_executor['result']['success'] is True

            def assert_emitted(phase, *, results_available=True, stderr_available=True):
                returned=_g1_source_failure_receipts(org,[tid],fault,phase)
                emitted=capsys.readouterr().out
                decoded=json.loads(emitted)
                assert returned==[decoded],('full emitted JSON differs',returned,emitted)
                assert decoded.get('task_id')==tid and decoded.get('session_id')==task.current_session_id, (
                    'available session was discarded',decoded,task.current_session_id)
                assert decoded['task']==task.model_dump(mode='json')
                assert decoded['task']['status']=='failed' and decoded['task']['note']==task.note
                assert decoded['executor']=={'state':'recorded','value':actual_executor}
                assert decoded['executor_outcome']=='returned' and decoded['parsed_report'] is None
                assert decoded['provider_stdout']=={'state':'recorded','text':actual_stdout}
                if stderr_available:
                    assert decoded['provider_stderr']=={'state':'recorded','text':actual_stderr}
                assert decoded['actual_callback']['command'] is None
                assert decoded['actual_callback']['payload']['state']=='missing'
                assert decoded['actual_callback']['results']==[]
                if results_available:
                    assert decoded['results']==actual_results and decoded['results_state']=='missing'
                    assert decoded['actual_callback']['state']=='missing'
                else:
                    assert decoded['results'] is None and decoded['results_state']=='unavailable'
                    assert decoded['actual_callback']['state']=='unavailable'
                return decoded

            if fault=='nested-commands':
                # All commands retained below originate in this actual provider;
                # malformed members supply no invented successful outcome.
                partial=[actual_provider['commands'][0],1,{}, {'argv':1},
                         {'argv':['report-completion',None]}]
                variants=[('integer',1,'malformed'),('string','damaged','malformed'),
                          ('object',{'damaged':True},'malformed'),
                          ('null',None,'missing'),('empty',[],'missing'),
                          ('partial',partial,'partial')]
                try:
                    for label,commands,state in variants:
                        damaged={**actual_provider,'commands':commands}
                        provider_record.write_text(json.dumps(damaged))
                        decoded=assert_emitted(label)
                        assert decoded['provider']=={'state':'recorded','value':damaged}
                        assert decoded['commands']==commands and decoded['commands_state']==state
                        if state=='malformed':
                            assert decoded['command_evidence']==[{'state':'malformed','raw':commands}]
                        elif state=='partial':
                            assert decoded['command_evidence'][0]=={'state':'recorded','value':partial[0]}
                            assert decoded['command_evidence'][1:]==[
                                {'state':'malformed','raw':member} for member in partial[1:]]
                        else:
                            assert decoded['command_evidence']==[]
                    absent={key:value for key,value in actual_provider.items() if key!='commands'}
                    provider_record.write_text(json.dumps(absent))
                    decoded=assert_emitted('absent')
                    assert decoded['provider']=={'state':'recorded','value':absent}
                    assert decoded['commands'] is None and decoded['commands_state']=='missing'
                    # Use an independently produced completed fixture task's
                    # actual callback command/payload, never a synthetic success.
                    foreign_path=next(path for path in config_path.parent.glob(config_path.name+'.*.provider.json')
                        if path!=provider_record and any('report-completion' in command['argv']
                            for command in json.loads(path.read_text())['commands']))
                    foreign=json.loads(foreign_path.read_text())
                    assert foreign['task_id']!=tid and foreign['session_id']!=task.current_session_id
                    foreign_task=org.db.get_task(foreign['task_id'])
                    assert foreign_task.status.value=='completed'
                    foreign_payload=(config_path.parent/'daemon'/(foreign['session_id']+'.completion.json')).read_bytes()
                    payload_path=config_path.parent/'daemon'/(task.current_session_id+'.completion.json')
                    assert not payload_path.exists()
                    try:
                        provider_record.write_bytes(foreign_path.read_bytes())
                        executor_record.write_text(json.dumps({**actual_executor,
                            'task_id':foreign['task_id'],'session_id':foreign['session_id']}))
                        payload_path.write_bytes(foreign_payload)
                        _g1_source_failure_receipts(org,[tid],fault,'foreign')
                        decoded=json.loads(capsys.readouterr().out)
                        assert decoded['task']==task.model_dump(mode='json') and decoded['session_id']==task.current_session_id
                        assert decoded['provider']['state']=='identity_mismatch' and 'value' not in decoded['provider']
                        assert decoded['executor']['state']=='identity_mismatch' and 'value' not in decoded['executor']
                        assert decoded['commands_state']=='identity_mismatch' and decoded['commands'] is None
                        assert decoded['command_evidence']==[] and decoded['parsed_report'] is None
                        assert decoded['actual_callback']['state']=='missing' and decoded['actual_callback']['results']==[]
                        assert decoded['actual_callback']['command'] is None
                        assert decoded['actual_callback']['payload']['state']=='identity_mismatch'
                    finally:
                        payload_path.unlink()
                finally:
                    provider_record.write_bytes(saved_provider);executor_record.write_bytes(saved_executor)
            else:
                original_results=org.db.get_task_results
                original_read=Path.read_text
                def failed_results(task_id):
                    if task_id==tid:
                        raise OSError('distinct result reader failure after task identity')
                    return original_results(task_id)
                try:
                    monkeypatch.setattr(org.db,'get_task_results',failed_results)
                    decoded=assert_emitted('results-read-error',results_available=False)
                    assert decoded['provider']=={'state':'recorded','value':actual_provider}
                    assert decoded['commands']==actual_provider['commands'] and decoded['commands_state']=='recorded'
                    assert decoded['evidence_errors']==[{'field':'results',
                        'exception':"OSError('distinct result reader failure after task identity')"}]
                finally:
                    monkeypatch.setattr(org.db,'get_task_results',original_results)
                stderr_path=Path(str(config_path)+'.'+task.current_session_id+'.stderr')
                def failed_stderr(path,*args,**kwargs):
                    if path==stderr_path:
                        raise OSError('distinct stderr reader failure after task identity')
                    return original_read(path,*args,**kwargs)
                try:
                    monkeypatch.setattr(Path,'read_text',failed_stderr)
                    decoded=assert_emitted('stderr-read-error',stderr_available=False)
                    assert decoded['provider']=={'state':'recorded','value':actual_provider}
                    assert decoded['provider_stderr']=={'state':'unavailable','path':str(stderr_path),
                        'exception':"OSError('distinct stderr reader failure after task identity')"}
                    assert decoded['commands']==actual_provider['commands'] and decoded['commands_state']=='recorded'
                finally:
                    monkeypatch.setattr(Path,'read_text',original_read)
                assert org.db.get_task_results(tid)==actual_results and stderr_path.read_text()==actual_stderr
            assert provider_record.read_bytes()==saved_provider and executor_record.read_bytes()==saved_executor
    assert provider.read_bytes()==original
    assert org.db.get_task(tid).status.value=='failed'


@pytest.mark.parametrize('g1_source_org',['natural-boundaries'],indirect=True)
@pytest.mark.parametrize('scenario',['pair-boundary','agent-boundary'])
def test_real_accepted_epoch_retrieval_role_and_pair_boundaries(g1_source_org,monkeypatch,capsys,scenario):
    """F4/AGE02: reachable retrieval decision and eligible role corroboration.

    INLINE v24, pair-boundary and agent-boundary:
    1. Supported ordinary task creation/queue/bootstrap/provider/callback gives
       471 worker sessions (50 shown reads), plus 29/30 manager sessions with
       real canonical search/get pairs for genuinely NONshown entries. BOTH
       backend and canonical HTTP CLI whole JSON/full text preserve accepted
       epoch at 14 complete UTC days/500+ sessions, with zero reader writes.
       Pair29 with eligible manager30 refuses corroboration; pair30 accepts.
       Manager29 with pair30 cannot corroborate an otherwise healthy eligible
       worker population; manager30 accepts. All row IDs/tuples/source/order,
       distinct pairs and unchanged authority/results/jobs are asserted below.
    2. Credible regressions: retrieval decision suppressed, pair>=30 changed to
       >=29, or functional-agent>=30 changed to >=29. Causal mutation receipts
       must fail the corresponding whole-report oracle, then restore exact bytes.
    3. Large zero-read and exact-rate/tied-vote owners contain no search pairs;
       copied-brief owner has only two. Unversioned arithmetic cannot reach this
       accepted final seam. Ineligible manager role exclusion here has >=30
       actual pairs, so it distinguishes gating from absent search evidence.
    4. No production hook or positive fake row/session/PASS/job. Provider stand-in
       implements assigned search/get work at the real executable boundary, as
       probes already do; no natural-intent flags supply attribution. Temporary
       memory files are work inputs, never acceptance authority. Concurrent
       providers publish those inputs atomically and assert the actual search
       returned each requested ID before follow-on get. SOURCE only.

    Diagnostic-only extension for both scenarios, INLINE v24:
    1. Retain actual failed task/status/note/results and provider argv/full
       streams/exits/session/launch intervals before the unchanged completion
       assertion; all natural population and whole-report oracles remain.
    2. A failing canonical command or missing callback must still fail that
       completion assertion. Capturing evidence never converts failure to PASS.
    3. Existing sparse/agent-majority owners do not retain command receipts for
       this exact 471-worker/29-manager search-pair population.
    4. No production seam. Diagnostics wrap the external stand-in's real
       subprocess.run, returning its actual result or reraising its exception.
       Failed-command and missing-callback owners verify diagnostic loss only;
       the original hosted completion failure remains UNCONFIRMED.
    """
    import argparse
    import asyncio
    import datetime as datetime_module
    from datetime import datetime,timedelta
    from cli.commands.learning import cmd_memory_report
    org,client,root=g1_source_org
    epoch=org.db.get_audit_logs_by_action('memory_collection_epoch_started')[0]
    original=org.db.read_memory_collection_evidence()
    created=[]
    manager_pairs={}
    def create(agent,index,ids):
        brief={'ordinary_work_item':index,'memory_read':agent=='engineering_head' or index<50,'search_reads':ids}
        response=client.post('/api/v1/orgs/alpha/tasks',json={'team':'engineering','owner':agent,
            'brief':json.dumps(brief,sort_keys=True,separators=(',',':'))})
        assert response.status_code==200,response.text
        tid=response.json()['task_id']
        created.append(tid)
        if ids:manager_pairs[tid]=set(ids)
        return tid
    for index in range(471):create('dev_agent',index,[])
    for index in range(29):
        create('engineering_head',index,['MEM-999','MEM-998'] if scenario=='agent-boundary' and index==0 else ['MEM-999'])
    cutoff=None
    for phase in ('manager29','manager30','pair30') if scenario=='pair-boundary' else ('manager29','manager30'):
        if phase!='manager29':create('engineering_head',29 if phase=='manager30' else 30,[] if phase=='manager30' else ['MEM-999'])
        asyncio.run_coroutine_threadsafe(org.orchestrator._queue._queue.join(),org.orchestrator._main_loop).result(timeout=1500)
        assert len(set(created))==len(created)
        _g1_source_failure_receipts(org,created,scenario,phase)
        assert all(org.db.get_task(tid).status.value=='completed' for tid in created)
        identities={row['task_id']:row for row in org.db.get_audit_logs_by_action('memory_runtime_identity') if row['task_id'] in created}
        assert set(identities)==set(created)
        assert all(row['payload']['population']=='root' and row['payload']['parent_known'] is True
                   and row['payload']['parent_task_id'] is None for row in identities.values())
        impressions={row['task_id']:row for row in org.db.get_audit_logs_by_action('memory_digest_impression') if row['task_id'] in created}
        assert set(impressions)==set(created)
        assert all(row['payload']['pointer_ids']==['MEM-001'] and row['payload']['full_body_ids']==[]
                   and row['payload']['session_id']==identities[tid]['payload']['session_id'] for tid,row in impressions.items())
        reads=[row for row in org.db.get_audit_logs_by_action('memory_read') if row['payload'].get('task_id') in created]
        searches=[row for row in org.db.get_audit_logs_by_action('memory_search') if row['task_id'] in created]
        absent=[row for row in reads if row['payload']['source']=='search']
        actual_pairs={(row['payload']['task_id'],row['payload']['id']) for row in absent}
        intended_pairs={(tid,mid) for tid,ids in manager_pairs.items() for mid in ids}
        assert actual_pairs==intended_pairs
        assert len(absent)==len(actual_pairs)==(30 if scenario=='agent-boundary' or phase=='pair30' else 29)
        for row in absent:
            payload=row['payload'];tid=payload['task_id'];identity=identities[tid];impression=impressions[tid]
            assert row['task_id']=='AGENT-engineering_head' and row['agent']=='engineering_head'
            assert payload['session_id']==identity['payload']['session_id']
            assert payload['id'] not in impression['payload']['digest_ids']
            causal=[search for search in searches if search['agent']==row['agent']
                    and search['payload']['task_id']==tid and search['payload']['session_id']==payload['session_id']
                    and payload['id'] in search['payload']['memory_ids']
                    and impression['id']<search['id']<row['id']]
            assert causal,(tid,payload)
        first=min(datetime.fromisoformat(row['timestamp']) for row in impressions.values())
        if cutoff is None:
            anchor=max(first,datetime.fromisoformat(epoch['timestamp']))
            cutoff=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
            if anchor!=cutoff:cutoff+=timedelta(days=1)
            cutoff+=timedelta(days=14)
        total=len(created);manager_count=total-471;pair_count=len(intended_pairs)
        eligible=manager_count>=30
        corroborates=eligible and pair_count>=30
        decision='retrieval_loss' if corroborates else 'no_demonstrated_problem'
        expected=_large_zero_read_expected(epoch,first,cutoff,sessions=total)
        expected['by_agent']={};expected['by_role']={};expected['read_counts']={}
        for agent,role,count,shown,nonshown in [('dev_agent','worker',471,50,0),
                ('engineering_head','manager',manager_count,manager_count,pair_count)]:
            metrics={'correlated_sessions':count,'pointer_opportunities':count,'full_body_exposures':0,
                'pointer_pairs_read':shown,'digest_pull_through':shown/count,'search_sourced_reads':nonshown,
                'search_sourced_absent_from_digest':nonshown,'search_absent_fraction':1.0 if nonshown else None,
                'distinct_valid_read_pairs':shown+nonshown,'read_operations':shown+nonshown,
                'pointer_sessions_activated':shown,'pointer_sessions':count,'session_activation':shown/count}
            expected['by_agent'][agent]={'role':role,'eligible':count>=30,'activation_vote_eligible':count>=30,**metrics}
            expected['by_role'][role]={**metrics,'retrieval_corroboration_eligible':corroborates if role=='manager' else False,
                'descriptive_only_for_activation_majority':True}
            expected['read_counts'][agent]={'MEM-001':{'distinct_pairs':shown,'operations':shown}}
            if nonshown:
                extra=1 if scenario=='agent-boundary' else 0
                expected['read_counts'][agent]['MEM-999']={'distinct_pairs':nonshown-extra,'operations':nonshown-extra}
                if extra:expected['read_counts'][agent]['MEM-998']={'distinct_pairs':1,'operations':1}
        shown=50+manager_count
        expected['aggregate'].update(pointer_pairs_read=shown,digest_pull_through=shown/total,
            digest_sourced_read_pairs=shown,search_sourced_reads=pair_count,search_sourced_absent_from_digest=pair_count,
            search_absent_fraction=1.0,distinct_valid_read_pairs=shown+pair_count,read_operations=shown+pair_count,
            pointer_sessions_activated=shown,session_activation=shown/total,
            eligible_functional_agents=1+int(eligible),eligible_pointer_agents=1+int(eligible),eligible_agents_below_10_percent=0)
        expected['instrumentation_health']['validated_read_operations']=shown+pair_count
        expected['observation_period']['status']=decision
        expected.update(decision=decision,evaluation_candidate=corroborates,
            decision_detail='Evaluation only; no tuning is executed.' if corroborates else 'No demonstrated memory problem.')
        roles={member['agent']:member['role'] for member in epoch['payload']['projection']['cohort']}
        before=org.db.read_memory_collection_evidence();writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
        backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=cutoff)
        assert backend==expected,(scenario,phase,backend,expected)
        class ReaderClock(datetime):
            @classmethod
            def now(cls,tz=None):return cutoff if tz is not None else cutoff.replace(tzinfo=None)
        with monkeypatch.context() as clock:
            clock.setattr(datetime_module,'datetime',ReaderClock)
            clock.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
            observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
            assert observation['epoch_id']==epoch['payload']['epoch_id'] and observation['epoch_audit_id']==epoch['id']
            cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=True))
            output=capsys.readouterr()
            assert output.err=='' and json.loads(output.out)==expected
            json_output=output.out
            cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=False))
            output=capsys.readouterr()
            assert output.err=='' and output.out==_expected_full_text(expected)
        assert org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
        assert org.db.read_memory_collection_evidence()==before
        assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[epoch]
        for table in ('task_results','jobs'):
            current={row['id']:row for row in before[table]}
            assert all(current[row['id']]==row for row in original[table])
        # Keep seam evidence in the command log, outside the next CLI capture.
        # Counts alone never substitute for the actual joins asserted above.
        with capsys.disabled():
            print(json.dumps({'finding':'F4 SOURCE ONLY','scenario':scenario,'phase':phase,'epoch_id':epoch['payload']['epoch_id'],
                'started_at':epoch['timestamp'],'sessions':total,'manager_sessions':manager_count,'distinct_nonshown_pairs':pair_count,
                'operation_audit_ids':[row['id'] for row in absent+searches],
                'decision':decision,'original_epoch_unchanged':True,'reader_writes':0,
                'cli_json':json.loads(json_output),'cli_text':output.out},sort_keys=True))


@pytest.mark.parametrize('g1_source_org',['ownership-admission'],indirect=True)
@pytest.mark.parametrize('kind',['qa','manager'])
def test_failed_original_owner_refuses_actual_final_admission(g1_source_org,request,monkeypatch,capsys,kind):
    """F1: failure at actual post-audit initial transition cannot mint authority.

    INLINE v24, QA and manager:
    1. Real admitted chain fails only original ownership immediately before
       final admission. Completed role results remain, zero epoch audit rows,
       canonical HTTP JSON/text equal closed backend, serving refs null, zero
       reader writes. Callback/result history is retained.
    2. Omitting current owner failed-state check admits the epoch, unlike merely
       inspecting an old completed result. Existing continuing owner cannot
       prove the actual initial post-result-log writer refuses.
    3. Continuing failed-owner keeper owns day14/replay; this owner uniquely
       observes the final initial writer and completed result-log tail.
    4. No production seam or fake positive rows. Test-side negative mutation
       wraps the real final transition; actual queue/job/callback gives positives.
    """
    import argparse
    import datetime as datetime_module
    from datetime import datetime,timezone
    from cli.commands.learning import cmd_memory_report
    org,client,root=g1_source_org
    receipt=request.node._g1_final_refusal
    assert receipt['observed_failed_owner'] is True
    assert org.db.get_audit_logs_by_action('memory_collection_epoch_started')==[]
    result=org.db.get_task_results(root)[-1]
    assert result['id']==receipt['result_id'] and result['status']=='completed'
    assert org.db.get_task_results(receipt['candidate']['qa_ref']['task_id'])[-1]['status']=='completed'
    now=datetime.now(timezone.utc)
    class ReaderClock(datetime):
        @classmethod
        def now(cls,tz=None):return now if tz is not None else now.replace(tzinfo=None)
    monkeypatch.setattr(datetime_module,'datetime',ReaderClock)
    monkeypatch.setattr('runtime.infrastructure.memory_collection.datetime',ReaderClock)
    roles={row['agent']:row['role'] for row in receipt['candidate']['cohort']}
    before=org.db.read_memory_collection_evidence();writes=org.db.fetch_one_readonly('SELECT total_changes()')[0]
    backend=org.orchestrator._audit.compute_memory_telemetry_report(agent_role_map=roles,current_time=now)
    assert backend['epoch']['collection_started'] is False and backend['decision']=='insufficient_instrumentation'
    observation=client.get('/api/v1/orgs/alpha/audit',params={'action':'memory_collection_seal','limit':1}).json()['memory_collection_observation']
    assert observation['epoch_id'] is None and observation['epoch_audit_id'] is None
    for mode in (True,False):
        cmd_memory_report(argparse.Namespace(org='alpha',agent='dev_agent',json=mode))
        output=capsys.readouterr()
        assert output.err==''
        assert (json.loads(output.out)==backend) if mode else (output.out==_expected_full_text(backend))
    assert org.db.read_memory_collection_evidence()==before and org.db.fetch_one_readonly('SELECT total_changes()')[0]==writes
