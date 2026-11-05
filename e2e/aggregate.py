"""Event health: missing cells/variants, skips and unknown cleanup always fail."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import signal
from datetime import datetime, timedelta

from cancellation import validate as validate_cancellation
from run import BASELINE_ARTIFACTS, daily_admission, validate_admission, validate_timing, scenario_fields
from pathlib import Path
import completed


MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text())


def validate(rows: list[dict], expected: list[str], source: str, matrix_result: str) -> None:
    if matrix_result != "success":
        raise ValueError(f"matrix result is {matrix_result}")
    if len(rows) != len(expected) or sorted(r["python"] for r in rows) != sorted(expected):
        raise ValueError("missing, duplicate or unexpected matrix cell")
    for row in rows:
        validate_timing(row)
        if row["source_sha"] != source or row["status"] != "PASS" or row["skipped"] != 0:
            raise ValueError("source/result/skip mismatch")
        if set(row["variants"]) != set(MANIFEST["variants"]):
            raise ValueError("missing or unexpected variant")
        if any(v["status"] != "PASS" for v in row["variants"].values()):
            raise ValueError("failed or skipped variant")
        if row["cleanup"]["ok"] is not True or row["cleanup"]["data_absent"] is not True:
            raise ValueError("unsuccessful or unknown cleanup")
        if not row["cleanup"]["ports_absent"] or not all(value is True for value in row["cleanup"]["ports_absent"].values()):
            raise ValueError("missing port absence")
        if row["total_seconds"] > MANIFEST["hard_seconds"]:
            raise ValueError("hard deadline exceeded")
        required = {"dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios"}
        if {p["name"] for p in row["phases"]} != required or any(p["exit"] != 0 for p in row["phases"]):
            raise ValueError("missing or failed execution phase")


def controls() -> dict:
    """Bounded validator controls only; never product-behavior evidence."""
    source = "1" * 40
    phases = [dict(name=n, exit=0, entered=0, finished=1,
                   cap_seconds=480 if n == "scenarios" else 240,
                   allowance_seconds=480 if n == "scenarios" else 240,
                   deadline=480 if n == "scenarios" else 240)
              for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios")]
    timing = dict(started=0, action_deadline=720, hard_deadline=900,
                  action=dict(finished=1, deadline=720, allowance_seconds=720, elapsed_seconds=1),
                  cleanup=dict(entered=1, finished=2, deadline=61, allowance_seconds=60, elapsed_seconds=1, ok=True),
                  export=dict(entered=2, finished=3, deadline=122, allowance_seconds=120, elapsed_seconds=1, ok=True))
    valid = [dict(python=p, source_sha=source, status="PASS", skipped=0, total_seconds=3, evidence_seconds=1,
                  exercise="baseline", timing=copy.deepcopy(timing),
                  variants={v: {"status": "PASS"} for v in MANIFEST["variants"]},
                  cleanup=dict(ok=True, data_absent=True, ports_absent={"1234": True}),
                  phases=copy.deepcopy(phases))
             for p in MANIFEST["daily_python"]]
    validate(valid, MANIFEST["daily_python"], source, "success")
    results = {}
    for name in ("missing-cell", "skip", "failed-variant", "missing-variant", "wrong-sha", "cleanup", "failed-job", "failed-export", "wrong-scenario-deadline"):
        rows = copy.deepcopy(valid)
        state = "success"
        if name == "missing-cell":
            rows.pop()
        elif name == "skip":
            rows[0]["skipped"] = 1
        elif name == "failed-variant":
            rows[0]["variants"][MANIFEST["variants"][0]]["status"] = "FAIL"
        elif name == "missing-variant":
            rows[0]["variants"].pop(MANIFEST["variants"][0])
        elif name == "wrong-sha":
            rows[0]["source_sha"] = "2" * 40
        elif name == "cleanup":
            rows[0]["cleanup"]["ok"] = False
        elif name == "failed-export":
            rows[0]["timing"]["export"]["ok"] = False
        elif name == "wrong-scenario-deadline":
            rows[0]["phases"][-1]["deadline"] = 720
        else:
            state = "failure"
        try:
            validate(rows, MANIFEST["daily_python"], source, state)
        except ValueError as exc:
            results[name] = str(exc)
        else:
            raise AssertionError(f"gate incorrectly accepted {name}")
    validate(valid, MANIFEST["daily_python"], source, "success")
    results.update(cancellation_controls())
    results.update(admission_controls())
    results.update(completed_controls())
    results.update(original_controls())
    results.update(failure_result_controls())
    return results


def failure_result_controls() -> dict:
    """Projection and real disposable-file shipping seams; native behavior NOT RUN."""
    secret = 'PRIVATE_SENTINEL"\n'
    original = dict(variants={'E05.compose-race':dict(status='FAIL',error=secret)},
        scenario_phases=[], e07=None,
        groups={'E05':dict(state='unknown',first_error_ref=0),
                'E07':dict(state='refused',first_error_ref=1)},
        e07_admission=dict(state='refused',refusal='missing original owner'),
        failures=[dict(stage='controller-action',error='ValueError',at=1.),
                  dict(stage='controller-action',error='AssertionError',at=2.)],
        finalization=[dict(stage='browser-close',error_ref=2)],
        ignored=secret)
    expected = dict(variants={'E05.compose-race':dict(status='FAIL',error='[REDACTED]')},
        scenario_phases=[], e07=None,
        groups={'E05':dict(state='unknown',first_error_ref=0),
                'E07':dict(state='refused',first_error_ref=1)},
        e07_admission=dict(state='refused',refusal='missing original owner'),
        failures=[dict(stage='controller-action',error='ValueError',at=1.),
                  dict(stage='controller-action',error='AssertionError',at=2.)],
        finalization=[dict(stage='browser-close',error_ref=2)])
    safe = scenario_fields(original, lambda text:text.replace(secret,'[REDACTED]'), context=[secret])
    assert safe == expected and 'PRIVATE_SENTINEL' not in json.dumps(safe)
    original['groups']['E05']['state'] = 'reported_pass'
    original['failures'].clear()
    assert safe == expected  # Original producer mutations cannot rewrite retained state.
    assert scenario_fields({'variants':{}},lambda text:text, context=[secret]) == {'variants':{}}
    results = {'B09-B12-detached-failure-fields':'actual bounded redaction projection; native fallback NOT RUN'}
    for name,result in (('absent',None),('missing-variants',{}),('non-object',[]),
                         ('nonfinite',dict(variants={},failures=[float('nan')])),
                         ('unsupported',dict(variants={},groups=object())),('byte-cap',dict(variants={},groups='x'*(8*1024*1024+1))),
                         ('fact-cap',dict(variants={},groups=[None]*200001))):
        try:
            scenario_fields(result,lambda text:text, context=[secret])
        except ValueError:
            results['B09-B12-projection-'+name] = 'refused; no raw fallback'
        else:
            raise AssertionError('unsafe scenario projection accepted: '+name)
    nested = None
    for _ in range(65):
        nested = [nested]
    try:
        scenario_fields(dict(variants={},groups=nested),lambda text:text, context=[secret])
    except ValueError as exc:
        assert 'capacity refused' in str(exc)
    else:
        raise AssertionError('deep scenario projection accepted')
    def unavailable(_text):
        raise ValueError('complete redaction context unavailable')
    try:
        scenario_fields(dict(variants={},groups={'E05':{}}),unavailable, context=[secret])
    except ValueError as exc:
        assert 'redaction context unavailable' in str(exc)
    else:
        raise AssertionError('scenario retained without redaction')
    # The already returned object stays detached; this is not proof that a
    # native final failure actually reached failure_receipt or was published.
    assert safe == expected
    results['B09-B12-redaction-refused'] = 'projection refuses; previous detached bytes unchanged'
    # SAME C1/C2/F3 object keeper: no file/parser guard can own this debit.
    borrowed = {'variants':{}, 'groups':{'value':'a'*4194304}}
    try:
        scenario_fields(borrowed, lambda text:text, context=[secret])
    except ValueError as exc:
        assert str(exc) == 'evidence capacity refused'
    else:
        raise AssertionError('F3 borrowed original-plus-safe projection accepted')
    assert safe == expected
    results['F3-projection-original-plus-safe'] = '4194304-byte borrowed scalar refuses at safe debit; earlier full tree unchanged'

    repeated = {'z':[True,False,None,7,1.5,'public\nline','中']}
    borrowed = {'variants':{},'groups':{'b':repeated,'a':repeated}}
    detached = scenario_fields(borrowed, lambda text:text, context=[secret])
    literal = {'variants':{},'groups':{'b':{'z':[True,False,None,7,1.5,'public\nline','中']},
                                     'a':{'z':[True,False,None,7,1.5,'public\nline','中']}}}

    def exact(actual: object, wanted: object) -> None:
        assert type(actual) is type(wanted), (type(actual),type(wanted))
        if type(wanted) is dict:
            assert list(actual) == list(wanted)
            for key in wanted: exact(actual[key],wanted[key])
        elif type(wanted) is list:
            assert len(actual) == len(wanted)
            for item,other in zip(actual,wanted): exact(item,other)
        else:
            assert actual == wanted

    exact(detached,literal)
    repeated['z'].clear()
    exact(detached,literal)
    assert detached['groups']['b'] is not detached['groups']['a']
    results['F3-projection-alias-order-types'] = 'full independently literal detached tree/types/order and repeated copies retained'

    # Real writer -> literal files -> shipping collection/publication -> FAIL.
    # These incomplete synthetic claims are never native/browser receipts.
    import tempfile
    import time
    import shutil
    import threading
    import traceback as traceback_module
    from typing import Iterator
    from contextlib import contextmanager
    from controller import Controller
    from run import (collect_evidence, publish_evidence, failure_receipt, publish_failure,
                     _result_json, _collection_line, _ordinary_evidence_error, _failure_error,
                     _EvidenceCapacity)

    @contextmanager
    def fixture(*, additional_secret: str | None = None) -> Iterator[tuple]:
        with tempfile.TemporaryDirectory(prefix="e2e-failure-result-") as temporary:
            base = Path(temporary)
            root, output = base / "source", base / "output"
            root.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            for name in ("raw", "coord", "daemon"):
                (root / name).mkdir(mode=0o700)
            token = 'SYNTHETIC_TOKEN'
            context = [token, secret] + ([additional_secret] if additional_secret is not None else [])
            (root / 'daemon/daemon.token').write_text(token)
            (root / 'coord/redaction-context.json').write_text(json.dumps(context))
            controller = Controller.__new__(Controller)
            controller.root, controller.token, controller.secrets = root, token, set(context[1:])
            controller.threads_active = False
            controller.deadline = time.monotonic() + 30
            controller.local_bounds, controller.inherited_alarm = threading.local(), float('inf')
            controller.secret_lock = threading.RLock()
            controller._evidence_fatal, controller._evidence_context, controller._evidence_budget = None, None, None
            controller._evidence_preparing, controller._evidence_root = False, None
            controller._evidence_replacements = []
            controller._failure_writer_owner = threading.get_ident()
            controller._failure_raw, controller._failure_slots = {}, {}
            controller._diagnostic_snapshot, controller._diagnostic_recorder_active = {}, False
            controller._diagnostic_outer_end = controller.deadline
            controller._diagnostic_alarm_code = None
            controller._final_attempt = None
            controller.failures, controller.primary_error, controller.primary_traceback = [], None, None
            controller.prepare_redaction()  # Never manually assert readiness.
            controller.results = {'E05.compose-race':dict(status='FAIL',error=secret)}
            controller.phases, controller.failures = [], copy.deepcopy(expected['failures'])
            controller.groups = copy.deepcopy(expected['groups'])
            controller.e07_admission = copy.deepcopy(expected['e07_admission'])
            controller.finalization = copy.deepcopy(expected['finalization'])
            controller.browser, controller.primary_error = None, None
            controller.stage_small_evidence()
            assert (root / 'scenario.json').is_file(), 'real stage_small_evidence did not write scenario'
            controller._atomic_text(root / 'raw/failure.txt', 'safe before ' + secret + ' after\n')
            controller._atomic_json(root / 'raw/safe.json', {'sample':'public'})
            info, output_info = root.stat(), output.stat()
            state = dict(token='',context=[],safe_scenario=dict(variants={}),fatal=False)
            staged, published, failed_members, failures, export_errors = {}, {}, set(), [], []
            row = dict(source_sha='1'*40,python='3.14',cleanup=dict(ok=True),
                       signals=[],phases=[],timing=dict(cleanup=dict(ok=True)))
            end = time.monotonic() + 30

            def check() -> None:
                if time.monotonic() >= end:
                    raise TimeoutError('control original allowance exhausted')

            def failed(error: BaseException | None, *, category: str, annotation: tuple) -> None:
                failures.append((error, error.__traceback__ if error else None, category, state['window'], annotation))

            def acquire() -> tuple:
                return collect_evidence(root,(info.st_dev,info.st_ino),row,'baseline',staged,state,check,failed)

            def drain() -> None:
                publish_evidence(output,(output_info.st_dev,output_info.st_ino),staged,published,failed_members,
                                 state,check,export_errors.append)

            def commit() -> dict:
                value = failure_receipt(row,state['safe_scenario'],{},None,None,'baseline',time.monotonic())
                publish_failure(output,(output_info.st_dev,output_info.st_ino),value,check)
                return json.loads((output / 'result.json').read_text())

            yield (controller,root,output,state,staged,published,failed_members,failures,export_errors,
                   row,check,acquire,drain,commit)

    # C1-C3/C6-C7: result shipping, separate from scenario/input predicates.
    # Entire ordered envelope/bytes below are independent literal expectations.
    for status in ('FAIL','PASS'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            acquire(); drain(); commit()
            prior_tree = copy.deepcopy(state['safe_scenario'])
            prior_member = (output/'failure.txt').read_bytes()
            source_bytes = (root/'scenario.json').read_bytes()
            repeated = {'b':2,'a':1}
            envelope = {'schema':1,'status':status,'variants':{'one':{'status':'FAIL'}},
                        'first':[True,False,None,7,1.5], 'sample':'public\nline','quote':'"','unicode':'中',
                        'copies':[repeated,repeated], 'completed_cases':None,
                        'failure_categories':{'schema':1,'controller':{'availability':'unavailable','snapshot':None}},
                        'ownership_evidence':{'availability':'unavailable','reason':'not_frozen','data':None},
                        'timing':{'export':{'ok':False}},'total_seconds':2.5,'target_exceeded':False}
            wanted = {'schema':1,'status':status,'variants':{'one':{'status':'FAIL'}},
                      'first':[True,False,None,7,1.5],'sample':'public\nline','quote':'"','unicode':'中',
                      'copies':[{'b':2,'a':1},{'b':2,'a':1}], 'completed_cases':None,
                      'failure_categories':{'schema':1,'controller':{'availability':'unavailable','snapshot':None}},
                      'ownership_evidence':{'availability':'unavailable','reason':'not_frozen','data':None},
                      'timing':{'export':{'ok':False}},'total_seconds':2.5,'target_exceeded':False}
            literal_bytes = (b'{"schema":1,"status":"'+(b'FAIL' if status == 'FAIL' else b'PASS')+
                b'","variants":{"one":{"status":"FAIL"}},"first":[true,false,null,7,1.5],"sample":"public\\nline","quote":"\\\"","unicode":"\xe4\xb8\xad","copies":[{"b":2,"a":1},{"b":2,"a":1}],"completed_cases":null,"failure_categories":{"schema":1,"controller":{"availability":"unavailable","snapshot":null}},"ownership_evidence":{"availability":"unavailable","reason":"not_frozen","data":null},"timing":{"export":{"ok":false}},"total_seconds":2.5,"target_exceeded":false}')
            publish_failure(output,(output.stat().st_dev,output.stat().st_ino),envelope,check)
            assert (output/'result.json').read_bytes() == literal_bytes, status
            detailed = _result_json(envelope,check,token=state['token'],context=state['context'])
            assert type(detailed) is bytearray and detailed == literal_bytes, status
            exact(json.loads((output/'result.json').read_bytes()),wanted)
            exact(envelope,wanted); exact(state['safe_scenario'],prior_tree)
            assert (output/'failure.txt').read_bytes() == prior_member and (root/'scenario.json').read_bytes() == source_bytes
            assert not (output/'result.tmp').exists()
            results['C3-result-compact-'+status] = 'whole literal file and shared final encoder buffer; every type/order/metadata/repetition exact; whole-main NOT RUN'

    class ResultString(str): pass
    class ResultDict(dict): pass
    cycle = []; cycle.append(cycle)
    depth = None
    for _ in range(65): depth = [depth]
    result_refusals = {
        'unsupported': (object(),'evidence value unavailable'),
        'subclass-string': (ResultString('public'),'evidence value unavailable'),
        'subclass-dict': (ResultDict(x=1),'evidence value unavailable'),
        'nonfinite': (float('inf'),'evidence number refused'),
        'nan': (float('nan'),'evidence number refused'),
        'nonstring-key': ({1:'public'},'evidence key unavailable'),
        'cycle': (cycle,'evidence cycle refused'),
        'surrogate': ('\ud800','evidence surrogate refused'),
        'backslash': ('public\\path','evidence representation withheld'),
        'nested-json': ('{"script":"public\\nline"}','evidence representation withheld'),
        'encoded-metadata': ('\0'*1500000,'evidence encoded capacity refused'),
        'aggregate-metadata': ('a'*2800000,'evidence capacity refused'),
        'facts': ([None]*18000,'evidence capacity refused'),
        'depth': (depth,'evidence depth capacity refused'),
    }
    for case,(metadata,message) in result_refusals.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            acquire(); drain(); commit()
            earlier = {path.name:path.read_bytes() for path in output.iterdir()}
            prior_tree = copy.deepcopy(state['safe_scenario'])
            source_bytes = (root/'scenario.json').read_bytes()
            # Scenario is small and already admitted; only final metadata changes.
            envelope = {'variants':state['safe_scenario']['variants'],'metadata':metadata}
            try: publish_failure(output,(output.stat().st_dev,output.stat().st_ino),envelope,check)
            except ValueError as exc: assert str(exc) == message, (case,str(exc))
            else: raise AssertionError('new result accepted: '+case)
            assert {path.name:path.read_bytes() for path in output.iterdir()} == earlier, case
            assert not (output/'result.tmp').exists()
            exact(state['safe_scenario'],prior_tree)
            assert (root/'scenario.json').read_bytes() == source_bytes
            results['C1-C2-C6-C7-result-'+case] = 'actual result refusal before pending; entire prior files/FAIL/tree/source exact; named guard distinct from scenario/input'

    for case in ('empty-context','missing-token','marker-context','key-collision','authenticated-secret','longest-first','literal-secret','closed-unavailable'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            acquire(); drain(); commit()
            earlier = {path.name:path.read_bytes() for path in output.iterdir()}
            prior_tree = copy.deepcopy(state['safe_scenario'])
            context,token = state['context'],state['token']
            envelope = {'sample':'public'}
            if case == 'empty-context': context = []
            elif case == 'missing-token': token = 'OTHER_TOKEN'
            elif case == 'marker-context': context = [token,'REDACTED']
            elif case == 'key-collision': envelope = {secret:1,'[REDACTED]':2}
            elif case == 'authenticated-secret': envelope = {'sample':secret,'byte_length':7}
            elif case == 'longest-first': context = [token,'cat','xcat']; envelope = {'sample':'xcat'}
            elif case == 'literal-secret': envelope = {'sample':secret}
            if case == 'closed-unavailable':
                # Real closed selection excludes raw failure/traceback/error.
                row.update(failure=secret,traceback=secret)
                row['cleanup']['error'] = secret
                state.update(token='',context=[])
                value = commit()
                assert value['failure'] == 'incomplete detailed evidence' and 'traceback' not in value
                assert 'error' not in value['cleanup'] and b'PRIVATE_SENTINEL' not in (output/'result.json').read_bytes()
            else:
                try: candidate = _result_json(envelope,check,token=token,context=context)
                except ValueError as exc:
                    assert case not in ('longest-first','literal-secret')
                    assert str(exc) == ('evidence redacted key collision' if case == 'key-collision' else
                                        'authenticated evidence requires original bytes' if case == 'authenticated-secret' else
                                        'result original context unavailable'), (case,str(exc))
                else:
                    assert case in ('longest-first','literal-secret'), case
                    assert candidate == b'{"sample":"[REDACTED]"}'
                assert {path.name:path.read_bytes() for path in output.iterdir()} == earlier
            exact(state['safe_scenario'],prior_tree)
            assert not (output/'result.tmp').exists()
            results['C3-C6-C7-result-'+case] = 'shared detailed original-context admission or real closed FAIL selection; no raw rescue; final-main chronology NOT RUN'

    # Strict ORIGINAL scenario JSON through actual file/collector/detachment.
    json_cases = {
        'positive': b'{"variants":{},"groups":{"first":[true,false,null,7,1.5],"sample":"public\\nline","quote":"\\\"","unicode":"\xe4\xb8\xad","pair":"\\ud83d\\ude00","order":{"b":2,"a":1}}}',
        'byte-bound-positive': b'{\r\n"variants":{},"groups":{"mixed":"'+b'a\xe4\xb8\xad\xf0\x9f\x98\x80\\u00e9'*140+b'","order":{"b":2,"a":1}}\r\n}',
        'bounded-construction-positive': b'{"variants":{},"groups":{"value":"'+b'a'*16384+b'"}}',
        'carry-capacity': b'{"variants":{},"groups":{"value":"'+b'\\u0061'*300000+b'"}}',
        'numeric-positive': b'{"variants":{},"groups":[18446744073709551615,-18446744073709551615,-0.0,0.'+b'1'*126+b']}',
        'duplicates': b'{"variants":{},"groups":{"x":1,"x":2}}',
        'escaped-equal': b'{"variants":{},"groups":{"x":1,"\\u0078":2}}',
        'bad-escape': b'{"variants":{},"groups":"\\q"}',
        'trailing': b'{"variants":{}} null',
        'trailing-comma': b'{"variants":{},}',
        'unclosed': b'{"variants":{},"groups":[1}',
        'nan': b'{"variants":{},"groups":NaN}',
        'infinity': b'{"variants":{},"groups":Infinity}',
        'nonfinite-float': b'{"variants":{},"groups":1e999}',
        'high-surrogate': b'{"variants":{},"groups":"\\ud800"}',
        'low-surrogate': b'{"variants":{},"groups":"\\udc00"}',
        'bad-pair': b'{"variants":{},"groups":"\\ud800\\u0041"}',
        'int-value': b'{"variants":{},"groups":18446744073709551616}',
        'int-spelling': b'{"variants":{},"groups":-1234567890123456789012}',
        'float-spelling': b'{"variants":{},"groups":0.'+b'1'*127+b'}',
        'leading-zero': b'{"variants":{},"groups":01}',
        'fraction': b'{"variants":{},"groups":1.}',
        'exponent': b'{"variants":{},"groups":1e+}',
        'decoded-backslash': b'{"variants":{},"groups":"public\\\\path"}',
        'parser-storage': b'{"variants":{},"groups":{"value":"'+b'a'*4194304+b'"}}',
    }
    json_expected = {'variants':{},'groups':{'first':[True,False,None,7,1.5],
                     'sample':'public\nline','quote':'"','unicode':'中','pair':'😀','order':{'b':2,'a':1}}}
    for case,source_bytes in json_cases.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            (root/'scenario.json').write_bytes(source_bytes)
            complete,error = acquire(); drain(); value = commit()
            assert not complete and not state['fatal'] and not errors
            assert (output/'failure.txt').read_bytes() == b'safe before [REDACTED] after\n'
            assert not (output/'result.tmp').exists()
            if case in ('positive','numeric-positive','byte-bound-positive','bounded-construction-positive'):
                assert error is None
                wanted = (json_expected if case == 'positive' else
                          {'variants':{},'groups':{'mixed':'a中😀é'*140,'order':{'b':2,'a':1}}}
                          if case == 'byte-bound-positive' else {'variants':{},'groups':{'value':'a'*16384}}
                          if case == 'bounded-construction-positive' else {'variants':{},'groups':[
                              18446744073709551615,-18446744073709551615,-0.0,0.1111111111111111]})
                exact(state['safe_scenario'],wanted)
                for key in wanted: exact(value[key],wanted[key])
                assert (root/'scenario.json').read_bytes() == source_bytes
                if case == 'byte-bound-positive':
                    assert (state['chars'],state['utf8_bytes']) == (1323,2023)
            else:
                assert error is not None and failures[0][0] is error[0], case
                assert state['safe_scenario'] == {'variants':{}}
                assert value['variants'] == {} and 'groups' not in value
                expected_op = ('SCENARIO_PROJECT' if case == 'decoded-backslash' else
                               'SCENARIO_READ' if case == 'parser-storage' else 'SCENARIO_DECODE')
                assert failures[0][4][0] == expected_op, (case,failures[0][4])
                if case in ('parser-storage','carry-capacity','int-spelling','float-spelling'):
                    assert failures[0][4][1] == 'CAPACITY', case
                if case == 'parser-storage':
                    assert failures[0][4][2:] == ('U','U')
            results['F3-json-'+case] = 'actual full scenario file/tree/type/order or whole-claim refusal; eligible sibling exact'

    # C1/C3/C5-C7/F3/R1: registered RAW scans, independently literal bytes.
    # Scan positives preserve noncompact framing, unlike the separate C3 writer.
    raw_object = b' { "first" : [true, false, null, 7, 1.5], "sample":"public\\nline", "quote":"\\\"", "slash":"a\\/b", "unicode":"\xe4\xb8\xad", "pair":"\\ud83d\\ude00", "copies":[{"b":2,"a":1},{"b":2,"a":1}] }\n'
    raw_array = b' [ {"sample":"public\\nline", "quote":"\\\"", "slash":"a\\/b", "unicode":"\xe4\xb8\xad"}, [true, false, null, 7, 1.5], [true, false, null, 7, 1.5] ] \n'
    raw_lines = b' {"index":1, "sample":"public\\nline", "slash":"a\\/b"} \n{"index":2,"unicode":"\xe4\xb8\xad","quote":"\\\""}\n'
    object_value = {'first':[True,False,None,7,1.5],'sample':'public\nline','quote':'"',
                    'slash':'a/b','unicode':'中','pair':'😀','copies':[{'b':2,'a':1},{'b':2,'a':1}]}
    array_value = [{'sample':'public\nline','quote':'"','slash':'a/b','unicode':'中'},
                   [True,False,None,7,1.5],[True,False,None,7,1.5]]
    line_values = [{'index':1,'sample':'public\nline','slash':'a/b'}, {'index':2,'unicode':'中','quote':'"'}]
    raw_positives = {
        'observations.json': (raw_object,object_value),
        'e07.json': (raw_object,object_value),
        'e07-prefix.json': (raw_object,object_value),
        'browser-events.json': (raw_array,array_value),
        'e05-browser-events.json': (raw_array,array_value),
        'launch-witnesses.jsonl': (raw_lines,line_values),
    }
    for name,(source_bytes,wanted) in raw_positives.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            (root/'raw'/name).write_bytes(source_bytes)
            complete,error = acquire(); drain(); commit()
            assert error is None and not state['fatal'] and not errors, (name,failures)
            assert (root/'raw'/name).read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == source_bytes, name
            assert published[name] is staged[name]  # Direct candidate ownership transfer.
            assert hashlib.sha256((output/name).read_bytes()).digest() == hashlib.sha256(source_bytes).digest()
            decoded = ([json.loads(part) for part in source_bytes.split(b'\n')[:-1]]
                       if name.endswith('.jsonl') else json.loads((output/name).read_bytes()))
            exact(decoded,wanted)
            assert not (output/(name+'.tmp')).exists()
            results['C3-C5-raw-positive-'+name] = 'entire literal original source/staged/published bytes, independent full type/key/list order; no raw tree'

    # Review13872 F1: ordinary full-image matching, no selected captured edge.
    # Literal originals/typed inputs precede execution; one table attributes ALL
    # missed file boundaries on the pre-fix source, rather than stopping at one.
    syntax_secret = '1,"b"'
    conversion_secret = '中' * 1023 + '","b"'
    conversion_image = (b'{"a":"' + ('中' * 1023).encode('utf8')
                        + b'","c":"' + ('中' * 1023).encode('utf8') + b'","b":2}')
    whole_images = [
        (name, name, b'[{"a":1,"b":2}]' if name in ('browser-events.json','e05-browser-events.json')
         else b'{"a":1,"b":2}\n' if name.endswith('.jsonl') else b'{"a":1,"b":2}', syntax_secret)
        for name in raw_positives
    ]
    whole_images.extend([
        ('jsonl-cross-LF','launch-witnesses.jsonl',b'{"a":1}\n{"b":2}\n','1}\n{"b"'),
        ('raw-conversion','e07.json',conversion_image,conversion_secret),
    ])
    unsafe_admissions = []
    for label,name,image,original_secret in whole_images:
        with fixture(additional_secret=original_secret) as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            prior = b'{"prior":"safe"}\n' if name.endswith('.jsonl') else b'[]' if name in ('browser-events.json','e05-browser-events.json') else b'{"prior":"safe"}'
            destination = root/'raw'/name
            destination.write_bytes(prior)
            acquire(); drain(); commit()
            old_files = {p.name:p.read_bytes() for p in output.iterdir()}
            old_tree, old_buffer, first = copy.deepcopy(state['safe_scenario']), staged[name], failures[0]
            scenario_image = (root/'scenario.json').read_bytes()
            destination.write_bytes(image)
            (root/'raw/eligible.txt').write_bytes(b'eligible ordinary sibling\n')
            complete,error = acquire(); drain()
            if name not in state['refused_sources']:
                unsafe_admissions.append('raw/'+label)
                continue  # Aggregate the actual missed boundaries for ONE proof.
            assert not complete and error is not None and not state['fatal'] and not errors, label
            assert str(error[0]) == 'original evidence content withheld', (label,error[0])
            refusal = next(f for f in failures if f[0] is error[0])
            assert refusal[4][0:2] == ('REDACT','VALUE') and failures[0] is first, (label,refusal[4])
            assert staged[name] is old_buffer and published[name] is old_buffer
            assert {p.name:p.read_bytes() for p in output.iterdir()} == old_files | {'eligible.txt':b'eligible ordinary sibling\n'}
            exact(state['safe_scenario'],old_tree)
            assert destination.read_bytes() == image and (root/'scenario.json').read_bytes() == scenario_image
            assert not list((root/'raw').glob('*.tmp')) and not list(output.glob('*.tmp'))
            results['C5-F1-whole-raw-'+label] = 'ordinary complete original syntax/LF secret refused; entire prior files/tree/FAIL/first cause exact; eligible sibling retained'
    for label,value,original_secret in (
        ('syntax',{'a':1,'b':2},syntax_secret),
        ('conversion',{'a':'中'*1023,'c':'中'*1023,'b':2},conversion_secret),
    ):
        with fixture(additional_secret=original_secret) as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            destination = root/'raw/e07.json'
            writer._atomic_json(destination,{'prior':'safe'})
            acquire(); drain(); commit()
            old_files = {p.name:p.read_bytes() for p in output.iterdir()}
            old_raw = {p.name:p.read_bytes() for p in (root/'raw').iterdir()}
            old_tree, original_value = copy.deepcopy(state['safe_scenario']), copy.deepcopy(value)
            scenario_image = (root/'scenario.json').read_bytes()
            try:
                writer._atomic_json(destination,value)
            except ValueError as error:
                assert str(error) == 'original literal content withheld', (label,error)
            else:
                unsafe_admissions.append('writer/'+label)
                continue
            assert writer._evidence_fatal is None
            assert {p.name:p.read_bytes() for p in (root/'raw').iterdir()} == old_raw
            assert {p.name:p.read_bytes() for p in output.iterdir()} == old_files
            exact(state['safe_scenario'],old_tree); exact(value,original_value)
            assert (root/'scenario.json').read_bytes() == scenario_image
            assert not list((root/'raw').glob('*.tmp')) and not list(output.glob('*.tmp'))
            results['C5-F1-whole-writer-'+label] = 'ordinary entire encoded candidate secret refused before pending; complete prior source/output/tree/FAIL and original ordered typed input exact'
    # A prefix match alone grants no refusal: all original bytes must match.
    with fixture(additional_secret=conversion_secret) as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        acquire(); drain(); commit()
        old_files = {p.name:p.read_bytes() for p in output.iterdir()}
        old_tree = copy.deepcopy(state['safe_scenario'])
        value = {'a':'中'*1023,'c':2}
        wanted = b'{"a":"' + ('中'*1023).encode('utf8') + b'","c":2}'
        try:
            writer._atomic_json(root/'raw/e07.json',value)
        except ValueError as error:
            raise AssertionError('ordinary encoded prefix-only wrongly refused') from error
        assert (root/'raw/e07.json').read_bytes() == wanted
        complete,error = acquire(); drain()
        assert not complete and error is None and not state['fatal'] and not errors
        assert {p.name:p.read_bytes() for p in output.iterdir()} == old_files | {'e07.json':wanted}
        exact(json.loads((output/'e07.json').read_bytes()),{'a':'中'*1023,'c':2})
        exact(state['safe_scenario'],old_tree)
        assert not list((root/'raw').glob('*.tmp')) and not list(output.glob('*.tmp'))
        results['C5-F1-whole-writer-prefix-only'] = 'partial encoded context prefix remains safe; entire independent literal bytes/types/order and prior files/tree/FAIL retained'
    assert not unsafe_admissions, ('ordinary whole-image unsafe admissions',unsafe_admissions)

    # SAME C5/C6/C7 table: full accepted E05 P/S/Q, canonical Relay edge only.
    # S and Q are copied literal bytes from the accepted subject, not a writer
    # roundtrip. P's entire ordered typed content is independently literal here.
    relay_s = b'{\n  "thread_id": "THR-701",\n  "subject": "E05 compose race",\n  "status": "open",\n  "started_at": "2026-01-01T00:00:00+00:00",\n  "archived_at": null,\n  "forwarded_from_id": null,\n  "forwarded_from_kind": null,\n  "turn_cap": 500,\n  "turns_used": 2,\n  "summary": null,\n  "transcript_path": "threads/THR-701.md",\n  "composed_by": "founder",\n  "composed_from_task_id": null,\n  "composed_from_dream_id": null,\n  "last_speaker": "case_manager",\n  "pinned": false,\n  "pinned_at": null,\n  "last_activity_at": "2026-01-01T00:00:03+00:00",\n  "participants": [\n    "case_manager",\n    "case_worker"\n  ],\n  "messages": [\n    {\n      "seq": 1,\n      "speaker": "founder",\n      "kind": "message",\n      "body_markdown": "E05 opening\\nliteral beta",\n      "decline_reason": null,\n      "system_payload": null,\n      "attachments": [],\n      "created_at": "2026-01-01T00:00:00+00:00",\n      "responder_status": [\n        {\n          "agent_name": "case_manager",\n          "purpose": "reply",\n          "status": "replied",\n          "responded_at": "2026-01-01T00:00:02+00:00",\n          "started_at": "2026-01-01T00:00:01+00:00",\n          "decline_reason": null,\n          "category": null\n        },\n        {\n          "agent_name": "case_worker",\n          "purpose": "reply",\n          "status": "declined",\n          "responded_at": "2026-01-01T00:00:02+00:00",\n          "started_at": "2026-01-01T00:00:01+00:00",\n          "decline_reason": "E05 initial observed",\n          "category": "declined"\n        }\n      ]\n    },\n    {\n      "seq": 2,\n      "speaker": "case_manager",\n      "kind": "message",\n      "body_markdown": "E05 reply\\n@case_worker received",\n      "decline_reason": null,\n      "system_payload": null,\n      "attachments": [],\n      "created_at": "2026-01-01T00:00:02+00:00",\n      "responder_status": [\n        {\n          "agent_name": "case_worker",\n          "purpose": "reply",\n          "status": "declined",\n          "responded_at": "2026-01-01T00:00:03+00:00",\n          "started_at": "2026-01-01T00:00:02+00:00",\n          "decline_reason": "E05 reply observed",\n          "category": "declined"\n        }\n      ]\n    }\n  ],\n  "reply_delivery": []\n}\n'
    relay_q = b'"{\\n  \\"thread_id\\": \\"THR-701\\",\\n  \\"subject\\": \\"E05 compose race\\",\\n  \\"status\\": \\"open\\",\\n  \\"started_at\\": \\"2026-01-01T00:00:00+00:00\\",\\n  \\"archived_at\\": null,\\n  \\"forwarded_from_id\\": null,\\n  \\"forwarded_from_kind\\": null,\\n  \\"turn_cap\\": 500,\\n  \\"turns_used\\": 2,\\n  \\"summary\\": null,\\n  \\"transcript_path\\": \\"threads/THR-701.md\\",\\n  \\"composed_by\\": \\"founder\\",\\n  \\"composed_from_task_id\\": null,\\n  \\"composed_from_dream_id\\": null,\\n  \\"last_speaker\\": \\"case_manager\\",\\n  \\"pinned\\": false,\\n  \\"pinned_at\\": null,\\n  \\"last_activity_at\\": \\"2026-01-01T00:00:03+00:00\\",\\n  \\"participants\\": [\\n    \\"case_manager\\",\\n    \\"case_worker\\"\\n  ],\\n  \\"messages\\": [\\n    {\\n      \\"seq\\": 1,\\n      \\"speaker\\": \\"founder\\",\\n      \\"kind\\": \\"message\\",\\n      \\"body_markdown\\": \\"E05 opening\\\\nliteral beta\\",\\n      \\"decline_reason\\": null,\\n      \\"system_payload\\": null,\\n      \\"attachments\\": [],\\n      \\"created_at\\": \\"2026-01-01T00:00:00+00:00\\",\\n      \\"responder_status\\": [\\n        {\\n          \\"agent_name\\": \\"case_manager\\",\\n          \\"purpose\\": \\"reply\\",\\n          \\"status\\": \\"replied\\",\\n          \\"responded_at\\": \\"2026-01-01T00:00:02+00:00\\",\\n          \\"started_at\\": \\"2026-01-01T00:00:01+00:00\\",\\n          \\"decline_reason\\": null,\\n          \\"category\\": null\\n        },\\n        {\\n          \\"agent_name\\": \\"case_worker\\",\\n          \\"purpose\\": \\"reply\\",\\n          \\"status\\": \\"declined\\",\\n          \\"responded_at\\": \\"2026-01-01T00:00:02+00:00\\",\\n          \\"started_at\\": \\"2026-01-01T00:00:01+00:00\\",\\n          \\"decline_reason\\": \\"E05 initial observed\\",\\n          \\"category\\": \\"declined\\"\\n        }\\n      ]\\n    },\\n    {\\n      \\"seq\\": 2,\\n      \\"speaker\\": \\"case_manager\\",\\n      \\"kind\\": \\"message\\",\\n      \\"body_markdown\\": \\"E05 reply\\\\n@case_worker received\\",\\n      \\"decline_reason\\": null,\\n      \\"system_payload\\": null,\\n      \\"attachments\\": [],\\n      \\"created_at\\": \\"2026-01-01T00:00:02+00:00\\",\\n      \\"responder_status\\": [\\n        {\\n          \\"agent_name\\": \\"case_worker\\",\\n          \\"purpose\\": \\"reply\\",\\n          \\"status\\": \\"declined\\",\\n          \\"responded_at\\": \\"2026-01-01T00:00:03+00:00\\",\\n          \\"started_at\\": \\"2026-01-01T00:00:02+00:00\\",\\n          \\"decline_reason\\": \\"E05 reply observed\\",\\n          \\"category\\": \\"declined\\"\\n        }\\n      ]\\n    }\\n  ],\\n  \\"reply_delivery\\": []\\n}\\n"\n'
    relay_p_bytes = b'{"thread_id":"THR-701","subject":"E05 compose race","status":"open","started_at":"2026-01-01T00:00:00+00:00","archived_at":null,"forwarded_from_id":null,"forwarded_from_kind":null,"turn_cap":500,"turns_used":2,"summary":null,"transcript_path":"threads/THR-701.md","composed_by":"founder","composed_from_task_id":null,"composed_from_dream_id":null,"last_speaker":"case_manager","pinned":false,"pinned_at":null,"last_activity_at":"2026-01-01T00:00:03+00:00","participants":["case_manager","case_worker"],"messages":[{"seq":1,"speaker":"founder","kind":"message","body_markdown":"E05 opening\\nliteral beta","decline_reason":null,"system_payload":null,"attachments":[],"created_at":"2026-01-01T00:00:00+00:00","responder_status":[{"agent_name":"case_manager","purpose":"reply","status":"replied","responded_at":"2026-01-01T00:00:02+00:00","started_at":"2026-01-01T00:00:01+00:00","decline_reason":null,"category":null},{"agent_name":"case_worker","purpose":"reply","status":"declined","responded_at":"2026-01-01T00:00:02+00:00","started_at":"2026-01-01T00:00:01+00:00","decline_reason":"E05 initial observed","category":"declined"}]},{"seq":2,"speaker":"case_manager","kind":"message","body_markdown":"E05 reply\\n@case_worker received","decline_reason":null,"system_payload":null,"attachments":[],"created_at":"2026-01-01T00:00:02+00:00","responder_status":[{"agent_name":"case_worker","purpose":"reply","status":"declined","responded_at":"2026-01-01T00:00:03+00:00","started_at":"2026-01-01T00:00:02+00:00","decline_reason":"E05 reply observed","category":"declined"}]}],"reply_delivery":[]}'
    relay_p = json.loads(relay_p_bytes)  # Independent literal oracle reader only.
    assert len(relay_s) == 2174 and hashlib.sha256(relay_s).hexdigest() == '61e8793704c04efdcb865f6a3a91c3ab41fa0cd2d67839ba91d2e6b0f209c06c'
    assert len(relay_q) == 2451 and hashlib.sha256(relay_q).hexdigest() == '903972eece33feb898150c343c17ced7144d22888087411db71d45c773d1f4f1'
    exact(json.loads(relay_s),relay_p)
    empty_body = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    for form in ('original','quote-LF-unicode'):
        s, q, p_bytes, p = relay_s, relay_q[:-1], relay_p_bytes, copy.deepcopy(relay_p)
        if form != 'original':
            s = s.replace(b'"summary": null', '"summary": "public \\"quote\\"\\n中"'.encode())
            q = q.replace(b'\\"summary\\": null', '\\"summary\\": \\"public \\\\\\"quote\\\\\\"\\\\n中\\"'.encode())
            p_bytes = p_bytes.replace(b'"summary":null', '"summary":"public \\"quote\\"\\n中"'.encode())
            p['summary'] = 'public "quote"\n中'
        # Complete independent original and compact fragments, not candidate bytes.
        exact(json.loads(s),p)
        assert json.loads(q) == s.decode()
        fields = {'path':b'"/api/v1/orgs/alpha/threads/THR-701"','method':b'"GET"',
            'body_sha256':b'"'+empty_body.encode()+b'"','status':b'200', 'response':s,
            'dropped':b'false','request':b'null','response_text':q,'entered':b'10.02'}
        values = dict(path='/api/v1/orgs/alpha/threads/THR-701',method='GET',body_sha256=empty_body,
            status=200,response=p,dropped=False,request=None,response_text=s.decode(),entered=10.02)
        orders = (tuple(fields), ('response_text','response','entered','path','request','status','method','dropped','body_sha256'),
                  ('response','response_text','request','body_sha256','method','path','status','entered','dropped'))
        for order_index,order in enumerate(orders):
            record = {key:values[key] for key in order}
            original_image = b' { "relay" : [ {'+b', '.join(b'"'+key.encode()+b'":'+fields[key] for key in order)+b'} ] }\r\n'
            compact_image = b'{"relay":[{'+b','.join(b'"'+key.encode()+b'":'+(p_bytes if key == 'response' else fields[key]) for key in order)+b'}]}'
            wanted = {'relay':[copy.deepcopy(record)]}
            for boundary in ('writer','raw','raw-prefix','prefix'):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name
                    if boundary == 'writer':
                        writer._atomic_json(destination,{'relay':[record]})
                        assert destination.read_bytes() == compact_image, (form,order_index)
                        expected_image = compact_image
                    elif boundary == 'prefix':
                        source = root/'raw/observations.json'; source.write_bytes(original_image)
                        candidate,parsed = writer._prepare_original_evidence(destination,source)
                        assert candidate == original_image and not destination.exists() and not destination.with_suffix('.json.tmp').exists()
                        exact(parsed,wanted)
                        writer._replace_evidence(destination,candidate)
                        assert destination.read_bytes() == original_image and source.read_bytes() == original_image
                        expected_image = original_image
                    else:
                        destination.write_bytes(original_image)
                        expected_image = original_image
                    complete,error = acquire(); drain(); commit()
                    assert error is None and not complete and not state['fatal'] and not errors, (boundary,failures)
                    assert staged[name] == published[name] == (output/name).read_bytes() == expected_image
                    exact(json.loads((output/name).read_bytes()),wanted)
                    assert not destination.with_name(name+'.tmp').exists()
                    results[f'C5-Relay-full-{form}-{order_index}-{boundary}'] = 'full independent P/S/Q/order/types/framing retained at shipping file boundary; native NOT RUN'

    # Finite negative inputs; source construction is independent of admission.
    base_record = dict(path='/api/v1/orgs/alpha/threads/THR-701',method='GET',body_sha256=empty_body,
        status=200,response=relay_p,dropped=False,request=None,response_text=relay_s.decode(),entered=10.02)
    relay_refusals = {}
    for case,text in {
        'duplicate':relay_s.replace(b'"thread_id": "THR-701",',b'"thread_id": "THR-701", "thread_id": "THR-701",',1),
        'escaped-equal':relay_s.replace(b'"thread_id": "THR-701",',b'"thread_id": "THR-701", "\\u0074hread_id": "THR-701",',1),
        'malformed':relay_s[:-2], 'trailing':relay_s+b'x', 'wrong-top':b'['+relay_s+b']',
        'nonfinite':relay_s.replace(b'"summary": null',b'"summary": NaN'),
        'surrogate':relay_s.replace(b'"summary": null',b'"summary": "\\ud800"'),
        'escaped-secret':relay_s.replace(b'"summary": null',b'"summary": "\\u0053YNTHETIC_TOKEN"'),
        'escaped-secret-key':relay_s.replace(b'"summary": null',b'"\\u0053YNTHETIC_TOKEN": null'),
        'literal-secret':relay_s.replace(b'"summary": null',b'"summary": "SYNTHETIC_TOKEN"'),
        'residual-backslash':relay_s.replace(b'"summary": null',b'"summary": "public\\\\nline"'),
        'second-json':relay_s.replace(b'"summary": null',b'"summary": "{\\"x\\":\\"public\\\\nline\\"}"'),
    }.items():
        record = copy.deepcopy(base_record); record['response_text'] = text.decode(); relay_refusals[case] = {'relay':[record]}
    for case in ('changed','missing','extra','reordered','coerced','repeat'):
        record = copy.deepcopy(base_record)
        if case == 'changed': record['response']['subject'] = 'changed'
        elif case == 'missing': del record['response']['summary']
        elif case == 'extra': record['response']['extra'] = None
        elif case == 'reordered': record['response'] = dict(reversed(tuple(record['response'].items())))
        elif case == 'coerced': record['response']['pinned'] = 0
        else: record['response']['messages'].append(copy.deepcopy(record['response']['messages'][0]))
        relay_refusals[case] = {'relay':[record]}
    for key,value in (('path','/api/v1/orgs/alpha/jobs/JOB-701'),('method','PUT'),('status',True),
                      ('status',403),('request',{}),('body_sha256','0'*64),('dropped',True),('entered',False)):
        record = copy.deepcopy(base_record); record[key] = value
        relay_refusals['identity-'+key+'-'+str(value)] = {'relay':[record]}
    for key in ('response','entered','request','path'):
        record = copy.deepcopy(base_record); del record[key]; relay_refusals['missing-'+key] = {'relay':[record]}
    record = copy.deepcopy(base_record); record['unexpected'] = None; relay_refusals['extra-schema'] = {'relay':[record]}
    relay_refusals['wrong-holder'] = {'holder':{'relay':[copy.deepcopy(base_record)]}}
    relay_refusals['wrong-relay-type'] = {'relay':copy.deepcopy(base_record)}
    record = copy.deepcopy(base_record); record['response']['summary'] = 'a'*550000
    relay_refusals['shared-work'] = {'relay':[record]}
    record = copy.deepcopy(base_record); record['response']['summary'] = [None]*70000
    relay_refusals['shared-facts'] = {'relay':[record]}
    depth = None
    for _ in range(61): depth = [depth]
    record = copy.deepcopy(base_record); record['response']['summary'] = depth
    relay_refusals['shared-depth'] = {'relay':[record]}
    for case,image in relay_refusals.items():
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name
                prior = b'{"prior":"safe"}'
                destination.write_bytes(prior)
                acquire(); drain(); commit()
                earlier = {p.name:p.read_bytes() for p in output.iterdir()}
                old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                if boundary in ('writer','prefix'):
                    if boundary == 'prefix':
                        source = root/'raw/observations.json'; source.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Relay unsafe member admitted: '+case+'/'+boundary)
                    assert destination.read_bytes() == prior
                    assert not destination.with_name(name+'.tmp').exists()
                    assert staged[name] is old_buffer and not state['fatal']
                else:
                    destination.write_bytes(original_image)
                    (root/'raw/eligible-relay.txt').write_bytes(b'eligible ordinary sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case,boundary,failures)
                    assert staged[name] is old_buffer and published[name] is old_buffer
                    assert (output/name).read_bytes() == prior and name in state['refused_sources']
                    if case not in ('shared-work','shared-facts'):
                        assert (output/'eligible-relay.txt').read_bytes() == b'eligible ordinary sibling\n'
                exact(state['safe_scenario'],old_tree)
                for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                results['C5-Relay-refusal-'+case+'-'+boundary] = 'whole prior final/member/FAIL/full safe tree preserved; no new member/pending; overlapping guards stated in case record'

    # Real compose request encoding is HTTPX's compact UTF8 JSON, in source order.
    post_request_bytes = b'{"subject":"E05 denial venue","recipients":["case_worker"],"body_markdown":"E05 denial anchor"}'
    post_response_bytes = b'{"thread_id":"THR-701","started_at":"2026-01-01T00:00:00+00:00","pending_replies":["case_worker"]}'
    post_text = b'{"thread_id":"\\u0054HR-701","started_at":"2026-01-01T00:00:00+00:00","pending_replies":["case_worker"]}\n'
    post_token = b'"{\\"thread_id\\":\\"\\\\u0054HR-701\\",\\"started_at\\":\\"2026-01-01T00:00:00+00:00\\",\\"pending_replies\\":[\\"case_worker\\"]}\\n"'
    post_hash = hashlib.sha256(post_request_bytes).hexdigest()
    post_record = dict(path='/api/v1/orgs/alpha/threads',method='POST',body_sha256=post_hash,status=200,
        response={'thread_id':'THR-701','started_at':'2026-01-01T00:00:00+00:00','pending_replies':['case_worker']},
        dropped=False,request={'subject':'E05 denial venue','recipients':['case_worker'],'body_markdown':'E05 denial anchor'},
        response_text=post_text.decode(),entered=10.02)
    post_image = (b'{"relay":[{"path":"/api/v1/orgs/alpha/threads","method":"POST","body_sha256":"'+post_hash.encode()+
        b'","status":200,"response":'+post_response_bytes+b',"dropped":false,"request":'+post_request_bytes+
        b',"response_text":'+post_token+b',"entered":10.02}]}')
    exact(json.loads(post_image),{'relay':[post_record]})
    for case in ('post','post-hash','post-request','legacy'):
        image = copy.deepcopy({'relay':[post_record]}); original_image = post_image
        if case == 'post-hash': image['relay'][0]['body_sha256'] = '0'*64
        elif case == 'post-request': image['relay'][0]['request']['subject'] = 'changed'
        elif case == 'legacy':
            image = {'relay':[dict(path='/api/v1/orgs/alpha/tasks/TASK-701/completion',method='POST',
                body_sha256='5'*64,status=200,response={'ok':True},dropped=False)]}
            original_image = b'{"relay":[{"path":"/api/v1/orgs/alpha/tasks/TASK-701/completion","method":"POST","body_sha256":"'+b'5'*64+b'","status":200,"response":{"ok":true},"dropped":false}]}'
        if case in ('post-hash','post-request'): original_image = json.dumps(image,separators=(',',':')).encode()
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                wanted = case in ('post','legacy')
                try:
                    if boundary == 'writer': writer._atomic_json(destination,image)
                    elif boundary == 'prefix':
                        source = root/'raw/observations.json'; source.write_bytes(original_image)
                        candidate,parsed = writer._prepare_original_evidence(destination,source)
                        assert not destination.with_name(name+'.tmp').exists()
                        exact(parsed,image)
                        writer._replace_evidence(destination,candidate)
                    else:
                        destination.write_bytes(original_image)
                        complete,error = acquire(); drain()
                        if error is not None: raise error[0]
                except ValueError:
                    assert not wanted, (case,boundary)
                    if boundary in ('writer','prefix'): assert destination.read_bytes() == prior
                    assert staged[name] is old_buffer
                    exact(state['safe_scenario'],old_tree)
                    for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                else:
                    assert wanted, 'Relay request hash not checked: '+case+'/'+boundary
                    assert destination.read_bytes() == original_image
                    if boundary in ('raw','raw-prefix'): assert staged[name] == published[name] == (output/name).read_bytes() == original_image
                    exact(json.loads(destination.read_bytes()),image)
                assert not destination.with_name(name+'.tmp').exists() and not (output/'result.tmp').exists()
                results['C5-Relay-'+case+'-'+boundary] = 'complete POST body/digest/response or unchanged legacy record; whole prior file/tree/FAIL preserved on refusal; native NOT RUN'

    # Literal cross-token original secret: no individual decoded scalar owns it.
    for name in ('observations.json','e07-prefix.json'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            cross = '\"GET\",\"body_sha256\":'
            (root/'coord/redaction-context.json').write_text(json.dumps(['SYNTHETIC_TOKEN',secret,cross]))
            destination = root/'raw'/name; destination.write_bytes(b'{"prior":"safe"}')
            acquire(); drain(); commit()
            earlier = {p.name:p.read_bytes() for p in output.iterdir()}
            old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
            original_image = json.dumps({'relay':[base_record]},separators=(',',':')).encode()
            assert cross.encode() in original_image
            destination.write_bytes(original_image)
            (root/'raw/eligible-relay.txt').write_bytes(b'eligible ordinary sibling\n')
            complete,error = acquire(); drain()
            assert not complete and error is not None and str(error[0]) == 'original evidence content withheld' and not state['fatal']
            assert staged[name] is old_buffer and published[name] is old_buffer
            exact(state['safe_scenario'],old_tree)
            for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
            assert (output/'eligible-relay.txt').read_bytes() == b'eligible ordinary sibling\n'
            assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
            results['C5-Relay-cross-token-'+name] = 'whole literal original secret across syntax refuses after complete nested closure; prior entire files/tree/FAIL exact'

    # No extension of canonical owner to the same spelling at other filenames.
    for name in ('e07.json','browser-events.json'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root/'raw'/name; prior = b'[]' if name == 'browser-events.json' else b'{"prior":"safe"}'
            path.write_bytes(prior); acquire(); drain(); commit()
            earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario'])
            image = {'relay':[base_record]}
            if name == 'browser-events.json': image = [image]
            try: writer._atomic_json(path,image)
            except ValueError: pass
            else: raise AssertionError('Relay grant escaped its owner: '+name)
            assert path.read_bytes() == prior and not path.with_name(name+'.tmp').exists()
            path.write_text(json.dumps(image))
            complete,error = acquire(); drain()
            assert not complete and error is not None and not state['fatal']
            exact(state['safe_scenario'],old_tree)
            for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
            results['C5-Relay-wrong-owner-'+name] = 'same spelling at unselected real writer/raw owner refuses wholly; earlier complete state exact'

    # Canonical Store.snapshot final-org records ONLY. The complete literal SQL
    # rows below are independent inputs/oracles, not product DB observations.
    sql_rows = {
        'tasks': [dict(id='TASK-701', status='completed', assigned_agent='case_manager', team='engineering',
            brief='literal public task', task_type='task', revision_count=0, created_at='2026-01-01', updated_at='2026-01-02',
            completed_at='2026-01-02', parent_task_id=None, final_output_summary='ordinary summary', final_output_dir=None,
            executor_pid=101, block_kind=None, note='ordinary note', orchestration_step_count=1, cancelled_at=None,
            revisit_of_task_id=None, last_heartbeat=None, session_timeout_seconds=None, blocked_on_job_ids='["JOB-701","quote\\\" LF\\n \\u4e2d"]',
            dispatched_from_thread_id=None, active_chain='{"legs":[],"text":"quote\\\" LF\\n \\u4e2d"}',
            active_fanout='{"children":[],"text":"quote\\\" LF\\n \\u4e2d"}', current_session_id='sess-sql', zombie_flagged_at=None,
            future_scalar='public 中 extension', future_integer=0, future_real=1.5)],
        'results': [dict(id=701, task_id='TASK-701', agent='case_manager', session_id='sess-sql', status='completed',
            output_summary='ordinary result', decision_json='{"action":"done","text":"quote\\\" LF\\n \\u4e2d"}', confidence_score=90,
            learnings=None, risks_flagged='["quote\\\" LF\\n \\u4e2d"]', duration_seconds=1, token_count=None, estimated_cost=0.5,
            output_dir=None, created_at='2026-01-02', waiting_on_job_ids='["JOB-701","quote\\\" LF\\n \\u4e2d"]', verdict=None,
            local_ci='{"exit_code":0,"text":"quote\\\" LF\\n \\u4e2d"}', future_scalar=None)],
        'audit': [dict(id=702, task_id='TASK-701', agent='case_manager', action='completion_report',
            payload=' {"ok":true,"empty":null,"n":7,"f":1.5,"text":"quote\\\" LF\\n \\u4e2d","literal":"中"} \n', timestamp='2026-01-02', future_scalar='ordinary')],
    }
    sql_columns = (('audit','payload','{'), ('results','decision_json','{'), ('results','local_ci','{'),
        ('results','risks_flagged','['), ('results','waiting_on_job_ids','['), ('tasks','blocked_on_job_ids','['),
        ('tasks','active_chain','{'), ('tasks','active_fanout','{'))

    def sql_input(root: Path, tag: str = 'jobs', org: str = 'alpha', *, late: bool = False, null: bool = False) -> dict:
        snapshot = copy.deepcopy(sql_rows)
        if null:
            for table,column,_ in sql_columns:
                snapshot[table][0][column] = None
        entry = {tag+'_final_org':org, 'snapshot':snapshot}
        manifest = dict(root=str(root), source='/original/source-fixture', revision='1'*40,
            python=str(root/'venv/bin/python'), callback=str(root/'bin/happyranch'), callback_sha256='a'*64,
            stubs={p:dict(path=str(root/'bin'/p),sha256='b'*64) for p in ('claude','codex','opencode')})
        if late:
            for table,rows in snapshot.items():
                snapshot[table] = [{key:r[key] for key in reversed(r)} for r in rows]
            manifest = {key:manifest[key] for key in reversed(manifest)}
            entry = {'snapshot':snapshot, tag+'_final_org':org}
            return {'durable':[entry, {'executable_manifest':manifest}]}
        return {'durable':[{'executable_manifest':manifest},entry]}

    for tag in ('jobs','threads'):
        for org in ('alpha','beta'):
            for late in (False,True):
                for null in (False,True):
                    for boundary in ('writer','raw','raw-prefix','prefix'):
                        label = f'C5-SQL-full-{tag}-{org}-'+('late' if late else 'early')+'-'+('null' if null else 'encoded')+'-'+boundary
                        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                            writer.revision = row['source_sha']
                            acquire(); drain(); commit()
                            previous = {p.name:p.read_bytes() for p in output.iterdir()}
                            tree = copy.deepcopy(state['safe_scenario']); scenario = (root/'scenario.json').read_bytes()
                            image = sql_input(root,tag,org,late=late,null=null)
                            wanted = copy.deepcopy(image)
                            original_image = b' '+json.dumps(image,ensure_ascii=False,indent=2).encode()+b'\r\n'
                            compact = json.dumps(wanted,ensure_ascii=False,separators=(',',':')).encode()
                            name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                            destination = root/'raw'/name
                            try:
                                if boundary == 'writer':
                                    writer._atomic_json(destination,image); expected_image = compact
                                elif boundary == 'prefix':
                                    source = root/'raw/observations.json'; source.write_bytes(original_image)
                                    content,parsed = writer._prepare_original_evidence(destination,source)
                                    assert content == source.read_bytes() == original_image
                                    exact(parsed,wanted)
                                    assert not destination.exists() and not destination.with_name(name+'.tmp').exists()
                                    writer._replace_evidence(destination,content); expected_image = original_image
                                else:
                                    destination.write_bytes(original_image); expected_image = original_image
                                complete,error = acquire(); drain()
                                # SAME fixture omits real baseline artifacts; this
                                # member must ship while baseline stays incomplete.
                                assert not complete and error is None and not state['fatal'] and not errors
                                assert len(failures) == 2 and all(cause is None and traceback is None
                                    and category == 'other' and annotation[:2] == ('REQUIRED','MISSING')
                                    for cause,traceback,category,window,annotation in failures)
                            except (ValueError,AssertionError) as error:
                                raise AssertionError('SQL canonical full member failed: '+label) from error
                            assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
                            exact(json.loads(expected_image),wanted); exact(image,wanted); exact(state['safe_scenario'],tree)
                            assert (root/'scenario.json').read_bytes() == scenario
                            for prior_name,prior_bytes in previous.items(): assert (output/prior_name).read_bytes() == prior_bytes
                            assert not destination.with_name(name+'.tmp').exists() and not (output/(name+'.tmp')).exists()
                            results[label] = 'entire independent full SQL rows/nulls/column text/types/order and exact compact or original outer bytes; prior full files/tree/FAIL retained; SQL/native NOT RUN'
    for boundary in ('writer','raw','raw-prefix','prefix'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            writer.revision = row['source_sha']; image = sql_input(root,late=True)
            acquire(); drain(); commit()
            previous = {p.name:p.read_bytes() for p in output.iterdir()}
            tree = copy.deepcopy(state['safe_scenario']); scenario = (root/'scenario.json').read_bytes()
            image['durable'].insert(1,image['durable'][0])  # Same object, TWO original occurrences.
            wanted = copy.deepcopy(image)
            original_image = json.dumps(wanted,ensure_ascii=False,separators=(',',':')).encode()
            name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'; destination = root/'raw'/name
            if boundary == 'writer': writer._atomic_json(destination,image)
            elif boundary == 'prefix':
                source = root/'raw/observations.json'; source.write_bytes(original_image)
                content,parsed = writer._prepare_original_evidence(destination,source); exact(parsed,wanted)
                assert content == original_image and not destination.exists()
                writer._replace_evidence(destination,content)
            else: destination.write_bytes(original_image)
            complete,error = acquire(); drain()
            assert not complete and error is None and not state['fatal'] and not errors
            assert len(failures) == 2 and all(cause is None and traceback is None
                and category == 'other' and annotation[:2] == ('REQUIRED','MISSING')
                for cause,traceback,category,window,annotation in failures)
            exact(image,wanted); exact(json.loads((output/name).read_bytes()),wanted)
            assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == original_image
            exact(state['safe_scenario'],tree)
            assert (root/'scenario.json').read_bytes() == scenario
            for prior_name,prior_bytes in previous.items(): assert (output/prior_name).read_bytes() == prior_bytes
            assert not destination.with_name(name+'.tmp').exists() and not (output/(name+'.tmp')).exists()
            results['C5-SQL-full-shared-occurrences-'+boundary] = 'full repeated original occurrences/aliases preserved; each traversal/decode charged; isolated debit sensitivity NOT RUN'

    sql_bad = {'malformed':'{"x":', 'duplicate':'{"x":0,"x":1}', 'escaped-equal':'{"x":0,"\\u0078":1}',
        'trailing':'{} []', 'nonfinite':'{"x":1e999}', 'surrogate':'{"x":"\\ud800"}',
        'second-layer':'{"x":"second\\\\nlayer"}', 'secret-value':'{"x":"SYNTHETIC_TOKEN"}',
        'secret-key':'{"SYNTHETIC_TOKEN":0}', 'escaped-secret-value':'{"x":"\\u0053YNTHETIC_TOKEN"}',
        'escaped-secret-key':'{"\\u0053YNTHETIC_TOKEN":0}', 'secret-original':'{"x":"'+secret.replace('"','\\"').replace('\n','\\n')+'"}',
        'primitive':'7', 'null-text':'null', 'empty-text':''}
    sql_refusals = [f'{table}.{column}-{form}' for table,column,_ in sql_columns
        for form in ('wrong-top','sql-bool','sql-number','sql-container','missing-column')]
    sql_refusals += list(sql_bad) + ['missing-tag','foreign-tag','contradictory-tag','foreign-org','boolean-org','extra-entry',
        'missing-snapshot','snapshot-type','missing-table','foreign-table','table-type','row-type','row-id-bool','row-id-missing',
        'row-id-text','result-owner-bool','audit-scope-bool','ordinary-nested','ordinary-bool','missing-manifest','duplicate-manifest',
        'foreign-root','foreign-source','foreign-revision','foreign-python','foreign-callback','callback-hash','manifest-shape',
        'lookalike-column','output-summary','final-output-summary','lookalike-root','case-snapshot','decoded-json-lookalike','wrong-owner',
        'shared-work','shared-facts','shared-depth']
    for case in sql_refusals:
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha']; image = sql_input(root,late=True)
                entry = image['durable'][0]; snapshot = entry['snapshot']; manifest = image['durable'][1]['executable_manifest']
                column_case = next(((t,c,top,case[len(t+'.'+c+'-'):]) for t,c,top in sql_columns if case.startswith(t+'.'+c+'-')),None)
                if column_case:
                    table,column,top,form = column_case
                    if form == 'missing-column': del snapshot[table][0][column]
                    else: snapshot[table][0][column] = {'wrong-top':'[]' if top == '{' else '{}', 'sql-bool':True, 'sql-number':0, 'sql-container':{}}[form]
                elif case in sql_bad: snapshot['audit'][0]['payload'] = sql_bad[case]
                elif case == 'missing-tag': del entry['jobs_final_org']
                elif case == 'foreign-tag': entry['final_org'] = entry.pop('jobs_final_org')
                elif case == 'contradictory-tag': entry['threads_final_org'] = 'beta'
                elif case == 'foreign-org': entry['jobs_final_org'] = 'foreign'
                elif case == 'boolean-org': entry['jobs_final_org'] = True
                elif case == 'extra-entry': entry['extra'] = 'public'
                elif case == 'missing-snapshot': del entry['snapshot']
                elif case == 'snapshot-type': entry['snapshot'] = []
                elif case == 'missing-table': del snapshot['tasks']
                elif case == 'foreign-table': snapshot['other'] = snapshot.pop('tasks')
                elif case == 'table-type': snapshot['audit'] = {}
                elif case == 'row-type': snapshot['audit'] = [0]
                elif case == 'row-id-bool': snapshot['audit'][0]['id'] = True
                elif case == 'row-id-missing': del snapshot['audit'][0]['id']
                elif case == 'row-id-text': snapshot['results'][0]['id'] = '701'
                elif case == 'result-owner-bool': snapshot['results'][0]['task_id'] = True
                elif case == 'audit-scope-bool': snapshot['audit'][0]['task_id'] = True
                elif case == 'ordinary-nested': snapshot['audit'][0]['future_scalar'] = {'x':0}
                elif case == 'ordinary-bool': snapshot['tasks'][0]['future_integer'] = True
                elif case == 'missing-manifest': image['durable'].pop()
                elif case == 'duplicate-manifest': image['durable'].append(copy.deepcopy(image['durable'][1]))
                elif case == 'foreign-root': manifest['root'] += '/foreign'
                elif case == 'foreign-source': manifest['source'] = '/original/../foreign'
                elif case == 'foreign-revision': manifest['revision'] = '2'*40
                elif case == 'foreign-python': manifest['python'] += '-foreign'
                elif case == 'foreign-callback': manifest['callback'] += '-foreign'
                elif case == 'callback-hash': manifest['callback_sha256'] = True
                elif case == 'manifest-shape': del manifest['stubs']
                elif case == 'lookalike-column': snapshot['audit'][0]['payload_lookalike'] = snapshot['audit'][0]['payload']
                elif case == 'output-summary': snapshot['results'][0]['output_summary'] = snapshot['results'][0]['decision_json']
                elif case == 'final-output-summary': snapshot['tasks'][0]['final_output_summary'] = snapshot['tasks'][0]['active_chain']
                elif case == 'lookalike-root': image['lookalike'] = image.pop('durable')
                elif case == 'case-snapshot': image['durable'][0] = {'case':{'final_snapshot':snapshot}}
                elif case == 'decoded-json-lookalike': snapshot['audit'][0]['payload'] = json.dumps(
                    {'snapshot':{'tasks':[{'active_chain':'{"x":"second\\nlayer"}'}]}})
                elif case == 'wrong-owner': pass
                elif case == 'shared-work': snapshot['audit'][0]['payload'] = '{"value":"'+'p'*2800000+'"}'
                elif case == 'shared-facts': snapshot['audit'][0]['payload'] = '{"values":['+','.join('0' for _ in range(70000))+']}'
                elif case == 'shared-depth': snapshot['audit'][0]['payload'] = '{"value":'+'['*60+'0'+']'*60+'}'
                else: raise AssertionError('unhandled SQL case '+case)
                wanted = copy.deepcopy(image)
                original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                name = 'e07.json' if case == 'wrong-owner' else 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                previous = {p.name:p.read_bytes() for p in output.iterdir()}; tree = copy.deepcopy(state['safe_scenario'])
                scenario = (root/'scenario.json').read_bytes(); old_buffer = staged[name]
                if boundary in ('writer','prefix'):
                    if boundary == 'prefix':
                        source = root/'raw/observations.json'; source.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source)
                    except (ValueError,AssertionError): pass
                    else: raise AssertionError('SQL unsafe member admitted: '+case+'/'+boundary)
                    assert destination.read_bytes() == prior and staged[name] is old_buffer and not state['fatal']
                    if boundary == 'prefix': assert source.read_bytes() == original_image
                else:
                    destination.write_bytes(original_image)
                    (root/'raw/eligible-sql.txt').write_bytes(b'eligible ordinary SQL sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case,boundary,failures)
                    assert destination.read_bytes() == original_image and staged[name] is old_buffer and published[name] is old_buffer
                    assert name in state['refused_sources'] and (output/name).read_bytes() == prior
                    if case not in ('shared-work','shared-facts'): assert (output/'eligible-sql.txt').read_bytes() == b'eligible ordinary SQL sibling\n'
                exact(image,wanted); exact(state['safe_scenario'],tree)
                assert (root/'scenario.json').read_bytes() == scenario
                for prior_name,prior_bytes in previous.items(): assert (output/prior_name).read_bytes() == prior_bytes
                assert not destination.with_name(name+'.tmp').exists() and not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                results['C5-SQL-refusal-'+case+'-'+boundary] = 'whole original input/prior member/output/result/FAIL/tree exact; selected pending/staging withheld; overlapping guards and resource/venue limits in case record'

    # SAME fixture/table, canonical submission edges. Original SOURCE strings
    # are copied literally; their source/SID/token identity is never rebound.
    submission_sources = {'exit0': 'import json, os, signal, socket, sys, time\nfrom pathlib import Path\nif False:\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\nPath(\'/tmp/e04-show/coord/e08eba4596159ebe63677c14.started\').write_text(json.dumps(dict(token=\'e08eba4596159ebe63677c14\', pid=os.getpid())))\nsys.stdout.write(\'E04_OK_OUT\\n\'); sys.stdout.flush()\nsys.stderr.write(\'E04_OK_ERR\\n\'); sys.stderr.flush()\nend = 40.0\ndef remaining():\n    value = end - time.monotonic()\n    if value <= 0:\n        raise TimeoutError("E04 script absolute deadline")\n    return min(20, value)\nwith socket.socket(socket.AF_UNIX) as peer:\n    peer.settimeout(remaining())\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\n    peer.settimeout(remaining())\n    peer.sendall((json.dumps(dict(token=\'e08eba4596159ebe63677c14\', pid=os.getpid())) + "\\n").encode())\n    remaining()\n    if False:\n        while True:\n            signal.pause()\n    peer.settimeout(remaining())\n    released = peer.recv(1)\n    remaining()\n    if released != b"R":\n        raise RuntimeError("missing external release")\nraise SystemExit(0)\n', 'exit7': 'import json, os, signal, socket, sys, time\nfrom pathlib import Path\nif False:\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\nPath(\'/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started\').write_text(json.dumps(dict(token=\'df5f1abd9fb56d8c669f17e2\', pid=os.getpid())))\nsys.stdout.write(\'E04_EXIT7_OUT\\n\'); sys.stdout.flush()\nsys.stderr.write(\'E04_EXIT7_ERR\\n\'); sys.stderr.flush()\nend = 40.0\ndef remaining():\n    value = end - time.monotonic()\n    if value <= 0:\n        raise TimeoutError("E04 script absolute deadline")\n    return min(20, value)\nwith socket.socket(socket.AF_UNIX) as peer:\n    peer.settimeout(remaining())\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\n    peer.settimeout(remaining())\n    peer.sendall((json.dumps(dict(token=\'df5f1abd9fb56d8c669f17e2\', pid=os.getpid())) + "\\n").encode())\n    remaining()\n    if False:\n        while True:\n            signal.pause()\n    peer.settimeout(remaining())\n    released = peer.recv(1)\n    remaining()\n    if released != b"R":\n        raise RuntimeError("missing external release")\nraise SystemExit(7)\n', 'timeout': 'import json, os, signal, socket, sys, time\nfrom pathlib import Path\nif True:\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\nPath(\'/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started\').write_text(json.dumps(dict(token=\'effc58d0f834d9ba4fc7c83e\', pid=os.getpid())))\nsys.stdout.write(\'E04_TIMEOUT_OUT\\n\'); sys.stdout.flush()\nsys.stderr.write(\'E04_TIMEOUT_ERR\\n\'); sys.stderr.flush()\nend = 40.0\ndef remaining():\n    value = end - time.monotonic()\n    if value <= 0:\n        raise TimeoutError("E04 script absolute deadline")\n    return min(20, value)\nwith socket.socket(socket.AF_UNIX) as peer:\n    peer.settimeout(remaining())\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\n    peer.settimeout(remaining())\n    peer.sendall((json.dumps(dict(token=\'effc58d0f834d9ba4fc7c83e\', pid=os.getpid())) + "\\n").encode())\n    remaining()\n    if True:\n        while True:\n            signal.pause()\n    peer.settimeout(remaining())\n    released = peer.recv(1)\n    remaining()\n    if released != b"R":\n        raise RuntimeError("missing external release")\nraise SystemExit(-9)\n'}
    submission_source_tokens = {'exit0': b'"import json, os, signal, socket, sys, time\\nfrom pathlib import Path\\nif False:\\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\\nPath(\'/tmp/e04-show/coord/e08eba4596159ebe63677c14.started\').write_text(json.dumps(dict(token=\'e08eba4596159ebe63677c14\', pid=os.getpid())))\\nsys.stdout.write(\'E04_OK_OUT\\\\n\'); sys.stdout.flush()\\nsys.stderr.write(\'E04_OK_ERR\\\\n\'); sys.stderr.flush()\\nend = 40.0\\ndef remaining():\\n    value = end - time.monotonic()\\n    if value <= 0:\\n        raise TimeoutError(\\"E04 script absolute deadline\\")\\n    return min(20, value)\\nwith socket.socket(socket.AF_UNIX) as peer:\\n    peer.settimeout(remaining())\\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\\n    peer.settimeout(remaining())\\n    peer.sendall((json.dumps(dict(token=\'e08eba4596159ebe63677c14\', pid=os.getpid())) + \\"\\\\n\\").encode())\\n    remaining()\\n    if False:\\n        while True:\\n            signal.pause()\\n    peer.settimeout(remaining())\\n    released = peer.recv(1)\\n    remaining()\\n    if released != b\\"R\\":\\n        raise RuntimeError(\\"missing external release\\")\\nraise SystemExit(0)\\n"', 'exit7': b'"import json, os, signal, socket, sys, time\\nfrom pathlib import Path\\nif False:\\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\\nPath(\'/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started\').write_text(json.dumps(dict(token=\'df5f1abd9fb56d8c669f17e2\', pid=os.getpid())))\\nsys.stdout.write(\'E04_EXIT7_OUT\\\\n\'); sys.stdout.flush()\\nsys.stderr.write(\'E04_EXIT7_ERR\\\\n\'); sys.stderr.flush()\\nend = 40.0\\ndef remaining():\\n    value = end - time.monotonic()\\n    if value <= 0:\\n        raise TimeoutError(\\"E04 script absolute deadline\\")\\n    return min(20, value)\\nwith socket.socket(socket.AF_UNIX) as peer:\\n    peer.settimeout(remaining())\\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\\n    peer.settimeout(remaining())\\n    peer.sendall((json.dumps(dict(token=\'df5f1abd9fb56d8c669f17e2\', pid=os.getpid())) + \\"\\\\n\\").encode())\\n    remaining()\\n    if False:\\n        while True:\\n            signal.pause()\\n    peer.settimeout(remaining())\\n    released = peer.recv(1)\\n    remaining()\\n    if released != b\\"R\\":\\n        raise RuntimeError(\\"missing external release\\")\\nraise SystemExit(7)\\n"', 'timeout': b'"import json, os, signal, socket, sys, time\\nfrom pathlib import Path\\nif True:\\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\\nPath(\'/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started\').write_text(json.dumps(dict(token=\'effc58d0f834d9ba4fc7c83e\', pid=os.getpid())))\\nsys.stdout.write(\'E04_TIMEOUT_OUT\\\\n\'); sys.stdout.flush()\\nsys.stderr.write(\'E04_TIMEOUT_ERR\\\\n\'); sys.stderr.flush()\\nend = 40.0\\ndef remaining():\\n    value = end - time.monotonic()\\n    if value <= 0:\\n        raise TimeoutError(\\"E04 script absolute deadline\\")\\n    return min(20, value)\\nwith socket.socket(socket.AF_UNIX) as peer:\\n    peer.settimeout(remaining())\\n    peer.connect(\'/tmp/e04-show/coord/jobs.sock\')\\n    peer.settimeout(remaining())\\n    peer.sendall((json.dumps(dict(token=\'effc58d0f834d9ba4fc7c83e\', pid=os.getpid())) + \\"\\\\n\\").encode())\\n    remaining()\\n    if True:\\n        while True:\\n            signal.pause()\\n    peer.settimeout(remaining())\\n    released = peer.recv(1)\\n    remaining()\\n    if released != b\\"R\\":\\n        raise RuntimeError(\\"missing external release\\")\\nraise SystemExit(-9)\\n"', 'F4-one-backslash': b'"print(\\"public\\\\nline\\")"', 'F4-two-backslash': b'"print(\\"public\\\\\\\\nline\\")"'}
    old_source = 'd6a605d8a22610b0074d87c9523b9b87742513ae'
    def submission_input(root: Path, source: str, *, refusal: bool = False, late: bool = False) -> dict:
        # Independent source-shaped input. stdlib test-side JSON specifies the
        # actual producer's inner sorted presentation and compact HTTPX body;
        # no shipping parser/scanner/sanitizer/renderer constructs expectations.
        provider = dict(task='TASK-701',session='sess-e04-first',agent='case_manager',org='alpha',pid=101)
        payload = dict(title='E04 source fixture',rationale='Full original submission',script=source,interpreter='python3',
            cwd_hint=None,review_required=refusal,persistent=False,max_runtime_seconds=30,
            task_id=provider['task'],session_id=provider['session'])
        text = json.dumps(payload,sort_keys=True)
        digest = hashlib.sha256(text.encode()).hexdigest()
        file = dict(path=str(root/'coord'/(digest+'.json')),text=text,bytes=len(text.encode()),sha256=digest)
        response = dict(id='JOB-701',status='running',created_at='2026-01-01T00:00:00+00:00',
            started_at='2026-01-01T00:00:01+00:00',cwd_resolved=str(root/'runtime/orgs/alpha/workspaces/case_manager'),
            timeout_seconds=30,events_url='/api/v1/orgs/alpha/jobs/JOB-701/events',
            authentication=dict(task_id=provider['task'],session_id=provider['session']))
        wire = dict(path='/api/v1/orgs/alpha/jobs/submit',method='POST',
            body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
            status=201,response=response,dropped=False)
        record = dict(action='jobs-refuse-review' if refusal else 'jobs-submit',operation=None,
            argv=[str(root/'bin/happyranch'),'jobs','submit','--org','alpha','--from-file',file['path'],'--json'],
            exit=2 if refusal else 0,stdout='' if refusal else json.dumps(response,sort_keys=True)+'\n',
            stderr=('Error reading scripts-submit file '+file['path']+': review_required_removed: obtain any required authorization in a thread before submitting; '
                'upgrade old clients and omit review_required or set it to false. This request was not submitted.\n') if refusal else '',
            payload=payload,payload_file=file,entered=10.,finished=10.5,timed_out=False,execution_deadline=19.,provider=provider,
            child_pid=202,child_start='456',child_parent=101,child_stat='202 (python) S 101 '+'0 '*17+'456\n',
            python=str(root/'venv/bin/python'),source_sha=old_source,callback_sha256='a'*64,wire=[] if refusal else [wire])
        manifest = dict(root=str(root),source='/original/source-fixture',revision=old_source,
            python=str(root/'venv/bin/python'),callback=str(root/'bin/happyranch'),callback_sha256='a'*64,
            stubs={p:dict(path=str(root/'bin'/p),sha256='b'*64) for p in ('claude','codex','opencode')})
        starts = [dict(provider,pid_start='123',pgid=101)]
        if late:
            record = {k:record[k] for k in ('stdout','payload_file','payload','wire','provider','argv','stderr','exit','child_stat',
                'child_start','child_parent','child_pid','python','callback_sha256','source_sha','finished','entered','execution_deadline','timed_out','operation','action')}
            return dict(provider_commands=[record],durable=[dict(executable_manifest=manifest)],starts=starts)
        return dict(starts=starts,durable=[dict(executable_manifest=manifest)],provider_commands=[record])

    def submission_reseal(image: dict) -> None:
        # Finite negative-input construction only. Keeps every unrelated
        # identity correct so JSON/lexical negatives reach their own layer.
        record = image['provider_commands'][0]; file = record['payload_file']
        data = file['text'].encode(); file['bytes'] = len(data); file['sha256'] = hashlib.sha256(data).hexdigest()
        file['path'] = str(Path(file['path']).parent/(file['sha256']+'.json'))
        record['argv'][-2] = file['path']
        if record['wire']:
            record['wire'][0]['body_sha256'] = hashlib.sha256(json.dumps(record['payload'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

    source_forms = dict(submission_sources)
    source_forms.update({
        'F4-one-backslash': 'print("public\\nline")',
        'quotes-unicode': "x = '中'; y = u'\\u03b2'; z = \\\"public\\\"\n".replace('\\"','"'),
        'bytes-UTF8': 'x = b"\\xe4\\xb8\\xad"\n',
        'raw-no-backslash': 'x=r"public"; y=BR"ASCII"; z=rb\'public\'\n',
        'triple': 'x="""public\nline 中"""\n',
        'triple-CRLF': 'x="""public\r\nline"""\r\n',
        'continuation': 'x = (1 + \\\n 2); y="public\\\nline"\n',
        'escaped-quote-controls': 'x="public\\"quote\\n\\t\\a\\b\\f\\r\\v\\101\\x42\\u03b2\\U0001f600"\n',
        'cross-chunk-literal': 'x="'+('p'*1020)+'\\n'+('q'*1050)+'"\n',
    })
    for ending,cost in (('LF',1),('CRLF',2),('EOF',0)):
        source_forms['physical-'+ending+'-inclusive'] = 'x=0'+' '*(65536-3-cost)+{'LF':'\n','CRLF':'\r\n','EOF':''}[ending]
    for form,source in source_forms.items():
        for boundary in ('writer','raw','raw-prefix','prefix'):
            for late in (False,True) if form in ('exit0','exit7','timeout') else (False,):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    writer.revision = row['source_sha'] = old_source
                    image = submission_input(root,source,late=late)
                    if form in submission_source_tokens:
                        literal_token = submission_source_tokens[form]
                        assert json.loads(literal_token) == source
                        assert b'\"script\": '+literal_token in image['provider_commands'][0]['payload_file']['text'].encode()
                    # Independent exact expected full outer framing only.
                    compact = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                    original_image = b' '+json.dumps(image,ensure_ascii=False,indent=2).encode()+b'\r\n'
                    name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name
                    if boundary == 'writer':
                        writer._atomic_json(destination,image)
                        expected_image = compact
                    elif boundary == 'prefix':
                        source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                        data,parsed = writer._prepare_original_evidence(destination,source_file)
                        assert data == original_image and not destination.exists() and not destination.with_name(name+'.tmp').exists()
                        exact(parsed,image); writer._replace_evidence(destination,data)
                        expected_image = original_image
                    else:
                        destination.write_bytes(original_image); expected_image = original_image
                    assert destination.read_bytes() == expected_image
                    complete,error = acquire(); drain(); commit()
                    assert not complete and error is None and not state['fatal'] and not errors, (form,boundary,failures)
                    assert staged[name] == published[name] == (output/name).read_bytes() == expected_image
                    exact(json.loads(expected_image),image)
                    for record in json.loads(expected_image)['provider_commands']:
                        assert record['payload']['script'] == source
                        decoded_file = json.loads(record['payload_file']['text'])
                        assert set(decoded_file) == set(record['payload'])
                        for key in decoded_file: exact(decoded_file[key],record['payload'][key])
                        assert list(json.loads(record['payload_file']['text'])) == sorted(record['payload'])
                        assert record['payload_file']['sha256'] == hashlib.sha256(record['payload_file']['text'].encode()).hexdigest()
                    results[f'C5-Submit-full-{form}-{int(late)}-{boundary}'] = 'entire original SOURCE/file/public/wire/typed trees and independent compact or original framing; native NOT RUN'
    for boundary in ('writer','raw','raw-prefix','prefix'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            writer.revision = row['source_sha'] = old_source
            image = submission_input(root,submission_sources['exit0'],refusal=True,late=True)
            name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
            destination = root/'raw'/name
            expected_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
            if boundary == 'prefix':
                source_file = root/'raw/observations.json'; source_file.write_bytes(expected_image)
                data,parsed = writer._prepare_original_evidence(destination,source_file)
                exact(parsed,image); writer._replace_evidence(destination,data)
            elif boundary == 'writer': writer._atomic_json(destination,image)
            else: destination.write_bytes(expected_image)
            complete,error = acquire(); drain(); commit()
            assert error is None and not complete and not state['fatal'] and not errors, failures
            assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
            exact(json.loads(expected_image),image)
            results['C5-Submit-refusal-original-'+boundary] = 'genuine local exit2/empty stdout/exact error/true review mode/no wire retained; no legacy pending success'

    for form in ('deferred-retained','repeated-occurrences'):
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                image = submission_input(root,submission_sources['exit0'],late=True)
                record = image['provider_commands'][0]
                if form == 'deferred-retained':
                    r = record['wire'][0]['response']; r['status'] = 'pending'
                    for key in ('started_at','cwd_resolved','timeout_seconds','events_url'): r[key] = None
                    r.update(deferred=True,code='root_paused')
                    record['stdout'] = json.dumps(r,sort_keys=True)+'\n'
                else:
                    second = copy.deepcopy(record)
                    # Reuse the actual original payload/file scalar aliases;
                    # every occurrence must still pay its own admission work.
                    second['payload'],second['payload_file'] = record['payload'],record['payload_file']
                    second['child_pid'] = 203; second['child_stat'] = second['child_stat'].replace('202 (','203 (',1)
                    r = second['wire'][0]['response']; r['id'] = 'JOB-702'; r['events_url'] = '/api/v1/orgs/alpha/jobs/JOB-702/events'
                    second['stdout'] = json.dumps(r,sort_keys=True)+'\n'
                    image['provider_commands'].append(second)
                name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name
                expected_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                if boundary == 'writer': writer._atomic_json(destination,image)
                elif boundary == 'prefix':
                    source_file = root/'raw/observations.json'; source_file.write_bytes(expected_image)
                    data,parsed = writer._prepare_original_evidence(destination,source_file)
                    exact(parsed,image); writer._replace_evidence(destination,data)
                else: destination.write_bytes(expected_image)
                complete,error = acquire(); drain(); commit()
                assert not complete and error is None and not state['fatal'] and not errors, failures
                assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
                exact(json.loads(expected_image),image)
                results['C5-Submit-'+form+'-'+boundary] = 'whole original receipt retained with actual deferred null/code branch or independently charged copied occurrences; no legacy/native success claim'

    lexical_refusals = {
        'F4-two-backslash':'print("public\\\\nline")', 'f-prefix':'x=f"public"', 't-prefix':'x=t"public"',
        'bad-prefix':'x=ur"public"', 'raw-layer':'x=r"public\\nline"', 'unknown-escape':'x="public\\q"',
        'named-escape':'x="\\N{LATIN CAPITAL LETTER A}"', 'unfinished':'x="public',
        'unfinished-delimiter':'x=(1', 'wrong-delimiter':'x=(1]', 'lone-CR':'x=0\r',
        'nonUTF8-bytes':'x=b"\\xff"', 'bytes-unicode':'x=b"\\u0061"', 'bytes-nonASCII':'x=b"中"',
        'surrogate-literal':'x="\\ud800"', 'beyond-unicode':'x="\\U00110000"', 'bad-hex':'x="\\x0z"',
        'literal-secret':'x="SYNTHETIC_TOKEN"', 'decoded-secret':'x="\\x53YNTHETIC_TOKEN"',
        'escape-boundary-secret':'x="SYNTHETIC_\\x54OKEN"',
        'continuation-secret':'x="SYNTHETIC_\\\nTOKEN"',
        'chunk-secret':'x="'+('p'*1020)+'SYNTHETIC_\\x54OKEN"',
        'comment-backslash':'# public\\n\nx=0', 'unsupported-character':'x=$public',
        'source-total-over': ('x=0\n'*131073),
        'literal-spelling-over': 'x="'+('p\\\n'*90000)+'"',
    }
    for ending,cost in (('LF',1),('CRLF',2),('EOF',0)):
        lexical_refusals['physical-'+ending+'-over'] = 'x=0'+' '*(65537-3-cost)+{'LF':'\n','CRLF':'\r\n','EOF':''}[ending]
    for case in (list(lexical_refusals)+[
            'file-duplicate','file-escaped-equal','file-malformed','file-trailing','file-nonfinite','file-surrogate','file-wrong-top',
            'file-unsupported-layer','file-equality','file-sorted-order','file-hash','file-count','filename','source-copy',
            'stdout-duplicate','stdout-escaped-equal','stdout-malformed','stdout-trailing','stdout-nonfinite','stdout-surrogate',
            'stdout-wrong-top','stdout-unsupported-layer','stdout-equality','stdout-no-LF',
            'missing-action','foreign-action','operation','provider','task','SID','org','source','callback','python','argv',
            'exit-bool','timed-out','time-bool','child-parent','child-start','missing-payload','missing-file',
            'wire-job','wire-auth','wire-status','wire-hash','wire-extra','wire-captured-unsupported',
            'payload-interpreter','payload-review-bool','payload-persistent-bool','payload-extra','wrong-root','wrong-owner',
            'missing-start','foreign-start','duplicate-start','missing-manifest','foreign-manifest','duplicate-record',
            'plain-stdout','shared-work','shared-facts','shared-depth']):
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                image = submission_input(root,lexical_refusals.get(case,submission_sources['exit0']),late=True)
                record = image['provider_commands'][0]; file = record['payload_file']; response = record['wire'][0]['response']
                if case.startswith('file-'):
                    changes = {
                        'duplicate':lambda x:x.replace('"title":','"title":"other", "title":',1),
                        'escaped-equal':lambda x:x.replace('"title":','"\\u0074itle":"other", "title":',1),
                        'malformed':lambda x:x[:-1]+',}', 'trailing':lambda x:x+' {}',
                        'nonfinite':lambda x:x.replace('"cwd_hint": null','"cwd_hint": NaN'),
                        'surrogate':lambda x:x.replace('"cwd_hint": null','"cwd_hint": "\\ud800"'),
                        'wrong-top':lambda x:'['+x+']', 'unsupported-layer':lambda x:json.dumps(x),
                        'equality':lambda x:x.replace('Full original submission','different original submission'),
                        'sorted-order':lambda x:json.dumps(record['payload']),
                    }
                    suffix = case[5:]
                    if suffix in changes: file['text'] = changes[suffix](file['text']); submission_reseal(image)
                    elif suffix == 'hash': file['sha256'] = '0'*64
                    elif suffix == 'count': file['bytes'] += 1
                elif case.startswith('stdout-'):
                    changes = {
                        'duplicate':lambda x:x.replace('"id":','"id":"JOB-999", "id":',1),
                        'escaped-equal':lambda x:x.replace('"id":','"\\u0069d":"JOB-999", "id":',1),
                        'malformed':lambda x:x[:-2]+',}\n', 'trailing':lambda x:x+'{}',
                        'nonfinite':lambda x:x.replace('"timeout_seconds": 30','"timeout_seconds": Infinity'),
                        'surrogate':lambda x:x.replace('JOB-701','\\ud800'), 'wrong-top':lambda x:'['+x+']',
                        'unsupported-layer':lambda x:json.dumps(x), 'equality':lambda x:x.replace('JOB-701','JOB-999'),
                        'no-LF':lambda x:x[:-1],
                    }
                    record['stdout'] = changes[case[7:]](record['stdout'])
                elif case == 'filename': file['path'] += '.json'; record['argv'][-2] = file['path']
                elif case == 'source-copy': record['payload']['script'] += '# different\n'; submission_reseal(image)
                elif case == 'missing-action': del record['action']
                elif case == 'foreign-action': record['action'] = 'jobs-read'
                elif case == 'operation': record['operation'] = 'receipt'
                elif case == 'provider': record['provider']['agent'] = 'case_worker'
                elif case == 'task': record['provider']['task'] = 'TASK-999'
                elif case == 'SID': record['provider']['session'] = 'foreign-sid'
                elif case == 'org': record['provider']['org'] = 'beta'
                elif case == 'source': record['source_sha'] = 'f'*40
                elif case == 'callback': record['callback_sha256'] = 'c'*64
                elif case == 'python': record['python'] = '/foreign/python'
                elif case == 'argv': record['argv'].append('--extra')
                elif case == 'exit-bool': record['exit'] = False
                elif case == 'timed-out': record['timed_out'] = True
                elif case == 'time-bool': record['entered'] = True
                elif case == 'child-parent': record['child_parent'] = 999
                elif case == 'child-start': record['child_start'] = '999'
                elif case == 'missing-payload': del record['payload']
                elif case == 'missing-file': del record['payload_file']
                elif case == 'wire-job': response['id'] = 'JOB-999'
                elif case == 'wire-auth': response['authentication']['session_id'] = 'foreign-sid'
                elif case == 'wire-status': record['wire'][0]['status'] = True
                elif case == 'wire-hash': record['wire'][0]['body_sha256'] = '0'*64
                elif case == 'wire-extra': record['wire'][0]['unexpected'] = None
                elif case == 'wire-captured-unsupported':
                    record['wire'][0].update(request=record['payload'],response_text=record['stdout'],entered=10.1)
                elif case == 'payload-interpreter': record['payload']['interpreter'] = 'bash'; submission_reseal(image)
                elif case == 'payload-review-bool': record['payload']['review_required'] = 0; submission_reseal(image)
                elif case == 'payload-persistent-bool': record['payload']['persistent'] = 0; submission_reseal(image)
                elif case == 'payload-extra': record['payload']['unexpected'] = None; submission_reseal(image)
                elif case == 'wrong-root': image = {'holder':image}
                elif case == 'wrong-owner': pass
                elif case == 'missing-start': image['starts'] = []
                elif case == 'foreign-start': image['starts'][0]['task'] = 'TASK-999'
                elif case == 'duplicate-start': image['starts'] *= 2
                elif case == 'missing-manifest': image['durable'] = []
                elif case == 'foreign-manifest': image['durable'][0]['executable_manifest']['revision'] = 'f'*40
                elif case == 'duplicate-record': image['provider_commands'] *= 2
                elif case == 'plain-stdout': record['stdout'] = 'print("public\\nline")'
                elif case == 'shared-work': record['payload']['title'] = 'p'*550000; submission_reseal(image)
                elif case == 'shared-facts': image['starts'] = [copy.deepcopy(image['starts'][0]) for _ in range(10000)]
                elif case == 'shared-depth':
                    nested = None
                    for _ in range(62): nested = [nested]
                    record['payload']['extra'] = nested
                name = ('e07.json' if case == 'wrong-owner' else 'e07-prefix.json'
                        if boundary in ('raw-prefix','prefix') else 'observations.json')
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                if boundary in ('writer','prefix'):
                    if boundary == 'prefix':
                        source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source_file)
                    except (ValueError,AssertionError):
                        pass
                    else:
                        raise AssertionError('Submission unsafe member admitted: '+case+'/'+boundary)
                    assert destination.read_bytes() == prior and not destination.with_name(name+'.tmp').exists()
                    assert staged[name] is old_buffer and not state['fatal']
                else:
                    destination.write_bytes(original_image)
                    (root/'raw/eligible-submit.txt').write_bytes(b'eligible ordinary sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case,boundary,failures)
                    assert staged[name] is old_buffer and published[name] is old_buffer
                    assert (output/name).read_bytes() == prior and name in state['refused_sources']
                    if case not in ('shared-work','shared-facts'):
                        assert (output/'eligible-submit.txt').read_bytes() == b'eligible ordinary sibling\n'
                exact(state['safe_scenario'],old_tree)
                for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                results['C5-Submit-refusal-'+case+'-'+boundary] = 'whole prior file/member/full safe tree/FAIL preserved; no selected pending/output; overlapping guards documented'

    # SAME controls: complete closed receipt16/authentication/output6. These
    # literal values come from the accepted independent three-form subjects;
    # SOURCE remains the original submission_sources, never candidate output.
    receipt_jobs = {'exit0': {'id': 'JOB-701', 'task_id': 'TASK-701', 'agent_name': 'case_manager', 'title': 'E04.approve-exit0/e08eba4596159ebe63677c14', 'rationale': 'Disposable owned deterministic job', 'script_text': None, 'interpreter': 'python3', 'cwd_hint': None, 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager', 'status': 'completed', 'exit_code': 0, 'reason': None, 'duration_ms': 100, 'created_at': '2026-01-01T00:00:00+00:00', 'started_at': '2026-01-01T00:00:01+00:00', 'finished_at': '2026-01-01T00:00:01.100000+00:00'}, 'exit7': {'id': 'JOB-703', 'task_id': 'TASK-703', 'agent_name': 'case_manager', 'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2', 'rationale': 'Disposable owned deterministic job', 'script_text': None, 'interpreter': 'python3', 'cwd_hint': None, 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager', 'status': 'completed', 'exit_code': 7, 'reason': None, 'duration_ms': 100, 'created_at': '2026-01-01T00:00:00+00:00', 'started_at': '2026-01-01T00:00:01+00:00', 'finished_at': '2026-01-01T00:00:01.100000+00:00'}, 'timeout': {'id': 'JOB-702', 'task_id': 'TASK-702', 'agent_name': 'case_manager', 'title': 'E04.failed-timeout/effc58d0f834d9ba4fc7c83e', 'rationale': 'Disposable owned deterministic job', 'script_text': None, 'interpreter': 'python3', 'cwd_hint': None, 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager', 'status': 'failed', 'exit_code': -9, 'reason': 'timeout', 'duration_ms': 5001, 'created_at': '2026-01-01T00:00:00+00:00', 'started_at': '2026-01-01T00:00:01+00:00', 'finished_at': '2026-01-01T00:00:06.001000+00:00'}}
    receipt_streams = {'exit0': ('E04_OK_OUT\n', 'E04_OK_ERR\n'),
                       'exit7': ('E04_EXIT7_OUT\n', 'E04_EXIT7_ERR\n'),
                       'timeout': ('E04_TIMEOUT_OUT\n', 'E04_TIMEOUT_ERR\n')}
    def receipt_input(root: Path, form: str = 'exit0', *, late: bool = False, source: str | None = None) -> dict:
        image = submission_input(root,submission_sources[form],late=late)
        template = image['provider_commands'][0]
        job = dict(receipt_jobs[form]); job['script_text'] = submission_sources[form] if source is None else source
        first = dict(task=job['task_id'],session='sess-e04-first',agent='case_manager',org='alpha',pid=101)
        second = dict(task=job['task_id'],session='sess-e04-second',agent='case_manager',org='alpha',pid=303)
        image['starts'] = [dict(first,pid_start='123',pgid=101),dict(second,pid_start='789',pgid=303)]
        commands = []
        for number,operation in enumerate(('receipt','output')):
            out,err = receipt_streams[form]
            response = dict(authentication=dict(task_id=job['task_id'],session_id=second['session']),job=dict(job),
                output=dict(stdout=out,stderr=err,truncated_stdout=False,truncated_stderr=False,
                            total_stdout_bytes=len(out.encode()),total_stderr_bytes=len(err.encode())))
            query = 'task_id='+job['task_id']+'&session_id='+second['session']
            if operation == 'output': query += '&stream=both&max_bytes=1048576'
            discovery = dict(path='/api/v1/orgs',method='GET',body_sha256=hashlib.sha256(b'').hexdigest(),status=200,
                response=dict(orgs=[dict(slug='alpha',root=str(root/'runtime/orgs/alpha')),
                                    dict(slug='beta',root=str(root/'runtime/orgs/beta'))],broken=[]),dropped=False)
            wire = dict(path='/api/v1/orgs/alpha/jobs/'+job['id']+'/receipt?'+query,method='GET',
                body_sha256=hashlib.sha256(b'').hexdigest(),status=200,response=response,dropped=False)
            command = dict(template)
            child = 404+number
            command.update(action='jobs-read',operation=operation,
                argv=[str(root/'bin/happyranch'),'jobs','show' if operation == 'receipt' else 'output',job['id'],
                      '--org','alpha','--json','--task-id',job['task_id'],'--session-id',second['session']],
                payload=None,payload_file=None,stdout=json.dumps(response,sort_keys=True)+'\n',stderr='',provider=dict(second),
                entered=32.+number,finished=32.5+number,execution_deadline=39.,child_pid=child,child_start=str(900+number),
                child_parent=303,child_stat=str(child)+' (python) S 303 '+'0 '*17+str(900+number)+'\n',wire=[discovery,wire])
            if late:
                command['wire'][1] = {k:wire[k] for k in ('response','dropped','status','body_sha256','method','path')}
            commands.append(command)
        image['provider_commands'] = commands
        return image

    receipt_positive_forms = ('exit0','exit7','timeout','F4-one-backslash','quotes-output','JSON-output','rejected-null','failed-null','repeated-alias')
    for form in receipt_positive_forms:
        for late in (False,True) if form in ('exit0','exit7','timeout') else (False,):
            for boundary in ('writer','raw','raw-prefix','prefix'):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    writer.revision = row['source_sha'] = old_source
                    image = receipt_input(root,form if form in receipt_jobs else 'exit0',late=late,
                        source=source_forms['F4-one-backslash'] if form == 'F4-one-backslash' else None)
                    for command in image['provider_commands']:
                        response = command['wire'][1]['response']
                        if form in ('quotes-output','JSON-output'):
                            response['output']['stdout'] = 'full "quote" 中\nlast\n' if form == 'quotes-output' else '{"plain":"JSON resembling output"}\n'
                            response['output']['total_stdout_bytes'] = len(response['output']['stdout'].encode())
                        if form in ('rejected-null','failed-null'):
                            response['job'].update(status='rejected' if form == 'rejected-null' else 'failed',exit_code=None,
                                duration_ms=None,started_at=None,finished_at=None,cwd_resolved=None,reason=None)
                        command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                    if form == 'repeated-alias':
                        duplicate = dict(image['provider_commands'][0]); duplicate.update(child_pid=406,child_start='902',child_stat='406 (python) S 303 '+'0 '*17+'902\n')
                        image['provider_commands'].append(duplicate)
                    compact = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                    original_image = b' '+json.dumps(image,ensure_ascii=False,indent=2).encode()+b'\r\n'
                    name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name
                    if boundary == 'writer': writer._atomic_json(destination,image); expected_image = compact
                    elif boundary == 'prefix':
                        source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                        data,parsed = writer._prepare_original_evidence(destination,source_file)
                        assert data == original_image and not destination.exists() and not destination.with_name(name+'.tmp').exists()
                        exact(parsed,image); writer._replace_evidence(destination,data); expected_image = original_image
                    else: destination.write_bytes(original_image); expected_image = original_image
                    assert destination.read_bytes() == expected_image
                    complete,error = acquire(); drain(); commit()
                    assert not complete and error is None and not state['fatal'] and not errors, (form,boundary,failures)
                    assert staged[name] == published[name] == (output/name).read_bytes() == expected_image
                    exact(json.loads(expected_image),image)
                    for command in image['provider_commands']:
                        assert len(command['wire']) == 2 and command['wire'][0]['path'] == '/api/v1/orgs'
                        response = command['wire'][1]['response']; decoded = json.loads(command['stdout'])
                        assert list(decoded) == sorted(response) and command['stdout'].endswith('}\n')
                        for holder in ('authentication','job','output'):
                            assert list(decoded[holder]) == sorted(response[holder])
                            for key in response[holder]: exact(decoded[holder][key],response[holder][key])
                        assert list(response['job']) == list(receipt_jobs['exit0'])
                    results[f'C5-Receipt-full-{form}-{int(late)}-{boundary}'] = 'entire independent closed16/auth/output6, discovery, full SOURCE/output and actual sorted stdout LF preserved; same-job distinct S2 reads; native NOT RUN'

    receipt_refusals = (['stdout-'+holder+'-'+key for holder,keys in (
        ('authentication',('task_id','session_id')),('job',tuple(receipt_jobs['exit0'])),
        ('output',('stdout','stderr','truncated_stdout','truncated_stderr','total_stdout_bytes','total_stderr_bytes'))) for key in keys] + [
        'duplicate','escaped-equal','malformed','trailing','nonfinite','surrogate','wrong-top','unsupported-layer','inner-order','no-LF','extra-LF','output-LF',
        'F4-two-backslash','decoded-secret','chunk-secret','output-backslash','output-secret',
        'missing-action','foreign-action','operation','provider','task','SID','S1-as-S2','org','source','callback','python','argv','payload','payload-file',
        'exit-bool','timed-out','time-bool','child-parent','child-start','duplicate-child','missing-start','foreign-start','duplicate-start','missing-manifest','foreign-manifest',
        'job-id','job-task','job-agent','job-interpreter','job-exit-bool','job-duration-bool','job-timeout','job-missing','job-extra','job-order',
        'wire-missing','wire-extra','discovery-missing','discovery-masquerade','discovery-source','wire-status','wire-drop','wire-hash','wire-method','wire-query','wire-extra-query','wire-body','wire-response-text','wire-auth',
        'output-bool','output-count','output-truncated','output-negative','wrong-root','wrong-owner','plain-stdout','shared-work','shared-facts','shared-depth'])
    for case in receipt_refusals:
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                image = receipt_input(root,late=True)
                command = image['provider_commands'][0]; wire = command['wire'][1]; response = wire['response']
                if case.startswith('stdout-'):
                    _,holder,key = case.split('-',2)
                    changed = copy.deepcopy(response); value = changed[holder][key]
                    changed[holder][key] = value+' changed' if type(value) is str else not value if type(value) is bool else value+1 if type(value) is int else ''
                    command['stdout'] = json.dumps(changed,sort_keys=True)+'\n'
                elif case in ('duplicate','escaped-equal','malformed','trailing','nonfinite','surrogate','wrong-top','unsupported-layer','inner-order','no-LF','extra-LF','output-LF'):
                    changes = {
                        'duplicate':lambda x:x.replace('"title":','"title":"other","title":',1),
                        'escaped-equal':lambda x:x.replace('"title":','"\\u0074itle":"other","title":',1),
                        'malformed':lambda x:x[:-2]+',}\n','trailing':lambda x:x+'{}',
                        'nonfinite':lambda x:x.replace('"duration_ms": 100','"duration_ms": NaN'),
                        'surrogate':lambda x:x.replace('JOB-701','\\ud800'),'wrong-top':lambda x:'['+x+']',
                        'unsupported-layer':lambda x:json.dumps(x),'inner-order':lambda x:json.dumps(response)+'\n',
                        'no-LF':lambda x:x[:-1],'extra-LF':lambda x:x+'\n',
                        'output-LF':lambda x:x.replace('E04_OK_OUT\\n','E04_OK_OUT'),
                    }
                    command['stdout'] = changes[case](command['stdout'])
                elif case in ('F4-two-backslash','decoded-secret','chunk-secret'):
                    response['job']['script_text'] = lexical_refusals[case]
                    command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                elif case in ('output-backslash','output-secret'):
                    response['output']['stdout'] = 'ordinary\\noutput' if case == 'output-backslash' else 'SYNTHETIC_TOKEN\n'
                    response['output']['total_stdout_bytes'] = len(response['output']['stdout'].encode())
                    command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                elif case == 'missing-action': del command['action']
                elif case == 'foreign-action': command['action'] = 'jobs-final'
                elif case == 'operation': command['operation'] = 'show'
                elif case == 'provider': command['provider']['agent'] = 'case_worker'
                elif case == 'task': command['provider']['task'] = 'TASK-999'
                elif case == 'SID': command['provider']['session'] = 'foreign-sid'
                elif case == 'S1-as-S2': command['provider']['session'] = 'sess-e04-first'
                elif case == 'org': command['provider']['org'] = 'beta'
                elif case == 'source': command['source_sha'] = 'f'*40
                elif case == 'callback': command['callback_sha256'] = 'c'*64
                elif case == 'python': command['python'] = '/foreign/python'
                elif case == 'argv': command['argv'].append('--extra')
                elif case == 'payload': command['payload'] = {}
                elif case == 'payload-file': command['payload_file'] = {}
                elif case == 'exit-bool': command['exit'] = False
                elif case == 'timed-out': command['timed_out'] = True
                elif case == 'time-bool': command['entered'] = True
                elif case == 'child-parent': command['child_parent'] = 999
                elif case == 'child-start': command['child_start'] = '999'
                elif case == 'duplicate-child': image['provider_commands'][1]['child_pid'] = command['child_pid']
                elif case == 'missing-start': image['starts'] = image['starts'][:1]
                elif case == 'foreign-start': image['starts'][1]['task'] = 'TASK-999'
                elif case == 'duplicate-start': image['starts'].append(dict(image['starts'][1]))
                elif case == 'missing-manifest': image['durable'] = []
                elif case == 'foreign-manifest': image['durable'][0]['executable_manifest']['revision'] = 'f'*40
                elif case.startswith('job-'):
                    key = case[4:]; job = response['job']
                    if key == 'id': job['id'] = 'JOB-999'
                    elif key == 'task': job['task_id'] = 'TASK-999'
                    elif key == 'agent': job['agent_name'] = 'case_worker'
                    elif key == 'interpreter': job['interpreter'] = 'bash'
                    elif key == 'exit-bool': job['exit_code'] = False
                    elif key == 'duration-bool': job['duration_ms'] = False
                    elif key == 'timeout': job['reason'] = 'timeout'
                    elif key == 'missing': del job['finished_at']
                    elif key == 'extra': job['review_required'] = False
                    elif key == 'order': response['job'] = dict(reversed(list(job.items())))
                    command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                elif case == 'wire-missing': command['wire'].pop()
                elif case == 'wire-extra': command['wire'].append(copy.deepcopy(wire))
                elif case == 'discovery-missing': command['wire'].pop(0)
                elif case == 'discovery-masquerade': command['wire'][0]['response'] = copy.deepcopy(response)
                elif case == 'discovery-source': command['wire'][0]['response']['job'] = copy.deepcopy(response['job'])
                elif case == 'wire-status': wire['status'] = True
                elif case == 'wire-drop': wire['dropped'] = True
                elif case == 'wire-hash': wire['body_sha256'] = '0'*64
                elif case == 'wire-method': wire['method'] = 'POST'
                elif case == 'wire-query': wire['path'] = wire['path'].replace('sess-e04-second','sess-e04-first')
                elif case == 'wire-extra-query': wire['path'] += '&stream=both'
                elif case == 'wire-body': wire['request'] = None
                elif case == 'wire-response-text': wire['response_text'] = command['stdout']
                elif case == 'wire-auth': response['authentication']['session_id'] = 'sess-e04-first'
                elif case.startswith('output-'):
                    out = response['output']
                    if case == 'output-bool': out['truncated_stdout'] = 0
                    elif case == 'output-count': out['total_stdout_bytes'] += 1
                    elif case == 'output-truncated': out['truncated_stdout'] = True
                    elif case == 'output-negative': out['total_stdout_bytes'] = -1
                    command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                elif case == 'wrong-root': image = dict(holder=image)
                elif case == 'wrong-owner': pass
                elif case == 'plain-stdout': command['stdout'] = 'print("public\\nline")'
                elif case == 'shared-work': response['job']['title'] = 'p'*700000; command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
                elif case == 'shared-facts': image['starts'] *= 5000
                elif case == 'shared-depth':
                    nested = None
                    for _ in range(62): nested = [nested]
                    response['job']['extra'] = nested
                name = ('e07.json' if case == 'wrong-owner' else 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json')
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                if boundary in ('writer','prefix'):
                    if boundary == 'prefix': source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source_file)
                    except (ValueError,AssertionError): pass
                    else: raise AssertionError('Receipt unsafe member admitted: '+case+'/'+boundary)
                    assert destination.read_bytes() == prior and not destination.with_name(name+'.tmp').exists()
                    assert staged[name] is old_buffer and not state['fatal']
                else:
                    destination.write_bytes(original_image); (root/'raw/eligible-receipt.txt').write_bytes(b'eligible ordinary sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case,boundary,failures)
                    assert staged[name] is old_buffer and published[name] is old_buffer
                    assert (output/name).read_bytes() == prior and name in state['refused_sources']
                    if case not in ('shared-work','shared-facts'):
                        assert (output/'eligible-receipt.txt').read_bytes() == b'eligible ordinary sibling\n'
                exact(state['safe_scenario'],old_tree)
                for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                results['C5-Receipt-refusal-'+case+'-'+boundary] = 'whole prior file/member/full safe tree/FAIL preserved; no selected pending/output; no generic read grant; overlapping guards documented'

    # SAME C5 fixture: the original source-bearing attempt is a separately
    # owned occurrence, joined to its exact canonical command and full start.
    def attempt_input(root: Path, form: str = 'exit0', *, late: bool = False, refusal: bool = False) -> dict:
        image = submission_input(root,submission_sources[form],refusal=refusal,late=late)
        command = image['provider_commands'][0]
        payload = {k:v for k,v in command['payload'].items() if k not in ('task_id','session_id')}
        instruction = dict(action=command['action'],deadline=20.,execution_deadline=19.,payload=payload)
        attempt = dict(provider=dict(image['starts'][0]),instruction=instruction,entered=9.5,wire_begin=4,
                       wire_end=4+len(command['wire']),finished=11.,command_index=0)
        if late:
            attempt['instruction'] = {k:instruction[k] for k in ('payload','execution_deadline','deadline','action')}
            attempt = {k:attempt[k] for k in ('instruction','command_index','finished','wire_end','wire_begin','entered','provider')}
            return dict(provider_attempts=[attempt],**image)
        image['provider_attempts'] = [attempt]
        return image

    for form in ('exit0','exit7','timeout','refusal','repeated','ordinary-unfinished'):
        for late in (False,True) if form in ('exit0','exit7','timeout') else (False,):
            for boundary in ('writer','raw','raw-prefix','prefix'):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    writer.revision = row['source_sha'] = old_source
                    image = attempt_input(root,form if form in submission_sources else 'exit0',late=late,refusal=form == 'refusal')
                    if form == 'repeated':
                        command = copy.deepcopy(image['provider_commands'][0])
                        command.update(child_pid=203,child_stat=command['child_stat'].replace('202 (','203 (',1))
                        command['wire'][0]['response'].update(id='JOB-702',events_url='/api/v1/orgs/alpha/jobs/JOB-702/events')
                        command['stdout'] = json.dumps(command['wire'][0]['response'],sort_keys=True)+'\n'
                        image['provider_commands'].append(command)
                        attempt = copy.deepcopy(image['provider_attempts'][0])
                        attempt.update(command_index=1,wire_begin=5,wire_end=6)
                        image['provider_attempts'].append(attempt)
                    if form == 'ordinary-unfinished':
                        image['provider_attempts'].append(dict(provider=dict(image['starts'][0]),
                            instruction=dict(action='jobs-final',deadline=20.,execution_deadline=19.,
                                             payload=dict(status='completed',confidence=90,summary='ordinary unfinished')),
                            entered=12.,wire_begin=5))
                    compact = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                    original_image = b' '+json.dumps(image,ensure_ascii=False,indent=2).encode()+b'\r\n'
                    name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name
                    if boundary == 'writer': writer._atomic_json(destination,image); expected_image = compact
                    elif boundary == 'prefix':
                        source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                        data,parsed = writer._prepare_original_evidence(destination,source_file)
                        assert data == original_image and not destination.exists() and not destination.with_name(name+'.tmp').exists()
                        exact(parsed,image); writer._replace_evidence(destination,data); expected_image = original_image
                    else: destination.write_bytes(original_image); expected_image = original_image
                    complete,error = acquire(); drain(); commit()
                    assert not complete and error is None and not state['fatal'] and not errors, (form,boundary,failures)
                    assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
                    exact(json.loads(expected_image),image)
                    for attempt in image['provider_attempts']:
                        if 'command_index' in attempt:
                            command = image['provider_commands'][attempt['command_index']]
                            assert attempt['provider'] == image['starts'][0]
                            for key,value in attempt['instruction']['payload'].items(): exact(value,command['payload'][key])
                            assert attempt['instruction']['payload']['script'] == submission_sources[form if form in submission_sources else 'exit0']
                    results[f'C5-Attempt-full-{form}-{int(late)}-{boundary}'] = 'entire original plan/command/start/SOURCE/type/order and outer bytes; original unavailable ordinary attempt retained; native NOT RUN'

    attempt_refusals = ('payload-title','payload-rationale','payload-script','payload-interpreter','payload-cwd_hint',
        'payload-review_required','payload-persistent','payload-max_runtime_seconds','payload-extra','payload-missing',
        'missing-command','wrong-index','index-bool','duplicate-index','foreign-action','unsupported-action','missing-action',
        'provider-task','provider-session','provider-agent','provider-org','provider-pid','provider-start','provider-pgid','provider-extra',
        'missing-start','missing-manifest','foreign-manifest','deadline','execution-deadline','deadline-bool','entered-bool','late-finished',
        'wire-begin','wire-end','wire-bool','outer-extra','outer-missing','instruction-extra','wrong-root','wrong-owner',
        'partial-source','decoded-secret','residual-layer','shared-work','shared-facts','shared-depth')
    for case in attempt_refusals:
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                image = attempt_input(root,late=True)
                attempt = image['provider_attempts'][0]; instruction = attempt['instruction']; payload = instruction['payload']
                if case.startswith('payload-') and case[8:] in payload:
                    key = case[8:]; value = payload[key]
                    payload[key] = (value+'changed' if type(value) is str else 0 if type(value) is bool else True
                                    if type(value) is int else 'foreign-cwd')
                elif case == 'payload-extra': payload['extra'] = None
                elif case == 'payload-missing': del payload['title']
                elif case == 'missing-command': image['provider_commands'] = []
                elif case == 'wrong-index': attempt['command_index'] = 1
                elif case == 'index-bool': attempt['command_index'] = False
                elif case == 'duplicate-index': image['provider_attempts'] *= 2
                elif case == 'foreign-action': instruction['action'] = 'jobs-refuse-review'
                elif case == 'unsupported-action': instruction['action'] = 'other'
                elif case == 'missing-action': del instruction['action']
                elif case.startswith('provider-'):
                    key = {'start':'pid_start'}.get(case[9:],case[9:])
                    attempt['provider'][key] = 999 if key in ('pid','pgid') else 'foreign'
                elif case == 'missing-start': image['starts'] = []
                elif case == 'missing-manifest': image['durable'] = []
                elif case == 'foreign-manifest': image['durable'][0]['executable_manifest']['revision'] = 'f'*40
                elif case == 'deadline': instruction['deadline'] = 21.
                elif case == 'execution-deadline': instruction['execution_deadline'] = 18.
                elif case == 'deadline-bool': instruction['deadline'] = True
                elif case == 'entered-bool': attempt['entered'] = True
                elif case == 'late-finished': attempt['finished'] = 20.
                elif case == 'wire-begin': attempt['wire_begin'] = -1
                elif case == 'wire-end': attempt['wire_end'] = 6
                elif case == 'wire-bool': attempt['wire_begin'] = True
                elif case == 'outer-extra': attempt['extra'] = None
                elif case == 'outer-missing': del attempt['wire_end']
                elif case == 'instruction-extra': instruction['extra'] = None
                elif case == 'wrong-root': image = {'holder':image}
                elif case == 'wrong-owner': pass
                elif case == 'partial-source':
                    for key in ('finished','wire_end','command_index'): del attempt[key]
                elif case == 'decoded-secret': payload['script'] = 'x="\\x53YNTHETIC_TOKEN"'
                elif case == 'residual-layer': payload['script'] = 'print("public\\\\nline")'
                elif case == 'shared-work': payload['title'] = 'p'*550000
                elif case == 'shared-facts': image['provider_attempts'] = [copy.deepcopy(attempt) for _ in range(10000)]
                elif case == 'shared-depth':
                    nested = None
                    for _ in range(62): nested = [nested]
                    payload['extra'] = nested
                name = 'e07.json' if case == 'wrong-owner' else 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                if boundary in ('writer','prefix'):
                    if boundary == 'prefix':
                        source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source_file)
                    except ValueError: pass
                    except AssertionError as exc:
                        assert case == 'wrong-owner' and boundary == 'prefix'
                        assert str(exc) == 'original evidence owner/path unavailable; observed=None'
                    else: raise AssertionError('Attempt unsafe member admitted: '+case+'/'+boundary)
                    assert destination.read_bytes() == prior and not destination.with_name(name+'.tmp').exists()
                    assert staged[name] is old_buffer and not state['fatal']
                else:
                    destination.write_bytes(original_image); (root/'raw/eligible-attempt.txt').write_bytes(b'eligible ordinary sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case,boundary,failures)
                    assert staged[name] is old_buffer and published[name] is old_buffer
                    assert (output/name).read_bytes() == prior and name in state['refused_sources']
                    if case not in ('shared-work','shared-facts'):
                        assert (output/'eligible-attempt.txt').read_bytes() == b'eligible ordinary sibling\n'
                exact(state['safe_scenario'],old_tree)
                for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                results['C5-Attempt-refusal-'+case+'-'+boundary] = 'whole prior member/output/tree/FAIL exact; no selected pending/publication; identity/safety/capacity guards may overlap'

    # C5 completion/wait/tail primitives. The independently authored payload
    # and wire trees below extend this fixture, never execute a producer.
    completion_summary = 'public "quote"\n中 repeated repeated'
    completion_texts = {
        'block': r'{"agent": "case_manager", "confidence": 90, "session_id": "sess-e04-first", "status": "blocked", "summary": "public \"quote\"\n\u4e2d repeated repeated", "task_id": "TASK-701", "waiting_on_job_ids": ["JOB-702", "JOB-701"]}',
        'final': r'{"agent": "case_manager", "confidence": 90, "decision": {"action": "done", "summary": "public \"quote\"\n\u4e2d repeated repeated"}, "session_id": "sess-e04-first", "status": "completed", "summary": "public \"quote\"\n\u4e2d repeated repeated", "task_id": "TASK-701"}',
    }

    def finite_input(root: Path, family: str, form: str, *, late: bool) -> dict:
        if family == 'completion':
            image = submission_input(root, submission_sources['exit0'], late=late)
            command = image['provider_commands'][0]
            blocked = form.startswith('block')
            plan = dict(status='blocked' if blocked else 'completed', confidence=90, summary=completion_summary)
            if blocked: plan['waiting_on_job_ids'] = ['JOB-702','JOB-701']
            else: plan['decision'] = dict(action='done', summary=completion_summary)
            if form.endswith('nulls'):
                plan['verdict'] = None
                if blocked: plan['decision'] = None
            payload = dict(plan, task_id='TASK-701', session_id='sess-e04-first', agent='case_manager')
            text = json.dumps(payload, sort_keys=True) if form.endswith('nulls') else completion_texts['block' if blocked else 'final']
            assert text == json.dumps(payload, sort_keys=True)  # Literal bytes independently own this producer spelling.
            digest = hashlib.sha256(text.encode()).hexdigest()
            path = str(root/'coord'/(digest+'.json'))
            body = dict(session_id='sess-e04-first',agent='case_manager',status='blocked' if blocked else 'completed',
                confidence=90,output_summary=completion_summary,risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
            if blocked: body['waiting_on_job_ids'] = ['JOB-702','JOB-701']
            else: body['decision'] = dict(action='done',summary=completion_summary)
            wire = dict(path='/api/v1/orgs/alpha/tasks/TASK-701/completion',method='POST',
                body_sha256=hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                status=200,response=dict(ok=True),dropped=False)
            command.update(action='jobs-block' if blocked else 'jobs-final',operation=None,
                argv=[str(root/'bin/happyranch'),'report-completion','--org','alpha','--from-file',path],
                payload=payload,payload_file=dict(path=path,text=text,bytes=len(text.encode()),sha256=digest),
                stdout='',stderr='',wire=[wire])
            instruction = dict(action=command['action'],deadline=20.,execution_deadline=19.,payload=copy.deepcopy(plan))
            attempt = dict(provider=dict(image['starts'][0]),instruction=instruction,entered=9.5,wire_begin=4,wire_end=5,finished=11.,command_index=0)
        else:
            nonterminal = form in ('running','pending')
            image = (submission_input(root, source_forms['F4-one-backslash'], late=late) if nonterminal
                     else receipt_input(root, form if form in receipt_jobs else 'exit0', late=late,
                         source=source_forms['F4-one-backslash']))
            template = image['provider_commands'][-1]
            provider = dict(template['provider'])
            job = dict(receipt_jobs[form if form in receipt_jobs else 'exit0'])
            job['script_text'] = source_forms['F4-one-backslash']
            if form == 'rejected':
                job.update(status='rejected',exit_code=None,duration_ms=None,started_at=None,cwd_resolved=None)
                for prior in image['provider_commands']:
                    prior['wire'][1]['response']['job'].update(job)
                    prior['stdout'] = json.dumps(prior['wire'][1]['response'],sort_keys=True)+'\n'
            if nonterminal:
                job.update(title='E04 source fixture',rationale='Full original submission',status=form,exit_code=None,
                    duration_ms=None,started_at=None if form == 'pending' else '2026-01-01T00:00:01+00:00',finished_at=None,
                    cwd_resolved=None if form == 'pending' else str(root/'runtime/orgs/alpha/workspaces/case_manager'))
            detail = dict(id=job['id'],task_id=job['task_id'],agent_name='case_manager',title=job['title'],rationale=job['rationale'],
                script_text=job['script_text'],interpreter='python3',cwd_hint=None,status=job['status'],exit_code=job['exit_code'],
                stdout_head=None,stderr_head=None,stdout_path=None,stderr_path=None,duration_ms=job['duration_ms'],
                started_at=job['started_at'],finished_at=job['finished_at'],reviewed_at=None,reviewed_by=None,reject_reason=None,
                cwd_resolved=job['cwd_resolved'],max_runtime_seconds=5 if form == 'timeout' else 30,max_output_bytes=52428800,
                review_required=form == 'rejected',persistent=False,reason=job['reason'],created_at=job['created_at'],timed_out=nonterminal)
            operation = 'tail' if family == 'tail' else 'wait'
            response = dict(stream='stdout',lines=['public "quote"','中 repeated repeated']) if operation == 'tail' else detail
            stdout = 'public "quote"\n中 repeated repeated\n' if operation == 'tail' else (
                '{"status": "'+job['status']+'", "timed_out": '+('true' if nonterminal else 'false')+'}\n')
            discovery = dict(path='/api/v1/orgs',method='GET',body_sha256=hashlib.sha256(b'').hexdigest(),status=200,
                response=dict(orgs=[dict(slug='alpha',root=str(root/'runtime/orgs/alpha')),
                                    dict(slug='beta',root=str(root/'runtime/orgs/beta'))],broken=[]),dropped=False)
            query = ('stream=stdout&lines=50' if operation == 'tail' else 'timeout_seconds=1')+'&task_id='+provider['task']+'&session_id='+provider['session']
            wire = dict(path='/api/v1/orgs/alpha/jobs/'+job['id']+'/'+operation+'?'+query,method='GET' if operation == 'tail' else 'POST',
                body_sha256=hashlib.sha256(b'').hexdigest(),status=200,response=response,dropped=False)
            argv = [str(root/'bin/happyranch'),'jobs',operation,job['id'],'--org','alpha']
            if operation == 'wait': argv += ['--timeout-seconds','1']
            argv += ['--task-id',provider['task'],'--session-id',provider['session']]
            command = dict(template)
            command.update(action='jobs-read',operation=operation,argv=argv,payload=None,payload_file=None,stdout=stdout,stderr='',
                wire=[discovery,wire],entered=35.,finished=35.5,execution_deadline=39.,child_pid=406,
                child_start='902',child_parent=provider['pid'],child_stat='406 (python) S '+str(provider['pid'])+' '+'0 '*17+'902\n')
            index = len(image['provider_commands']); image['provider_commands'].append(command)
            instruction = dict(action='jobs-read',deadline=40.,execution_deadline=39.,operation=operation,job=job['id'])
            start = next(s for s in image['starts'] if s['session'] == provider['session'])
            attempt = dict(provider=dict(start),instruction=instruction,entered=34.5,wire_begin=8,wire_end=10,finished=36.,command_index=index)
        if late:
            command_order = ('stdout','payload_file','payload','wire','provider','argv','stderr','exit','child_stat','child_start','child_parent',
                'child_pid','python','callback_sha256','source_sha','finished','entered','execution_deadline','timed_out','operation','action')
            image['provider_commands'][-1] = {k:command[k] for k in command_order}
            attempt['instruction'] = dict(reversed(list(instruction.items())))
            attempt = dict(reversed(list(attempt.items())))
            image = dict(provider_attempts=[attempt],provider_commands=image['provider_commands'],durable=image['durable'],starts=image['starts'])
        else: image['provider_attempts'] = [attempt]
        if form == 'repeated':
            # Same borrowed wire/line/argv references, independently owned CLI.
            second = dict(image['provider_commands'][-1])
            second.update(child_pid=407,child_start='903',child_stat='407 (python) S '+str(second['provider']['pid'])+' '+'0 '*17+'903\n',
                entered=36.,finished=36.5)
            another = dict(attempt,command_index=len(image['provider_commands']),entered=35.9,finished=37.,wire_begin=10,wire_end=12)
            image['provider_commands'].append(second); image['provider_attempts'].append(another)
        return image

    finite_forms = [('completion',f) for f in ('block','final','block-nulls','final-nulls')]
    finite_forms += [('wait',f) for f in ('exit0','exit7','timeout','rejected','running','pending')]
    finite_forms += [('tail','exit0'),('tail','repeated')]
    for family,form in finite_forms:
        for late in (False,True):
            for boundary in ('writer','raw','raw-prefix','prefix'):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    writer.revision = row['source_sha'] = old_source
                    image = finite_input(root,family,form,late=late)
                    wanted = copy.deepcopy(image)
                    original_image = b' '+json.dumps(image,ensure_ascii=False,indent=2).encode()+b'\r\n'
                    compact = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                    name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image); expected_image = compact
                        elif boundary == 'prefix':
                            source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                            data,parsed = writer._prepare_original_evidence(destination,source_file)
                            assert data == original_image and not destination.exists() and not destination.with_name(name+'.tmp').exists()
                            exact(parsed,wanted); writer._replace_evidence(destination,data); expected_image = original_image
                        else: destination.write_bytes(original_image); expected_image = original_image
                        complete,error = acquire(); drain(); commit()
                        assert not complete and error is None and not state['fatal'] and not errors, (family,form,late,boundary,failures)
                        assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
                        exact(json.loads(expected_image),wanted); exact(image,wanted)
                    except Exception as exc:
                        raise AssertionError(f'C5-Finite-full-{family}-{form}-{int(late)}-{boundary}') from exc
                    results[f'C5-Finite-full-{family}-{form}-{int(late)}-{boundary}'] = 'complete literal payload/wire/plan trees, bytes/types/order, original ownership and publication preserved; Case/native NOT RUN'

    finite_refusals = ('action','operation','argv','argv-job','argv-task','argv-session','exit-bool','timed-out','child','child-start','child-parent',
        'provider-task','provider-session','provider-agent','provider-org','provider-pid','source','python','callback','missing-start','duplicate-start',
        'missing-manifest','foreign-manifest','wrong-root','wrong-owner','command-extra','command-missing','wire-status','wire-status-bool','wire-path',
        'wire-method','wire-body','wire-dropped','wire-extra','wire-duplicate','wire-missing','response-extra','response-bool',
        'attempt-index','attempt-index-bool','attempt-duplicate','attempt-provider','attempt-start','attempt-operation','attempt-job',
        'attempt-deadline','attempt-wire','attempt-extra','attempt-missing','attempt-index-missing','stdout-layer','stderr-layer','literal-secret','escaped-secret',
        'shared-work','shared-facts','shared-depth','unsupported-wire-script')
    completion_refusals = ('file-bytes','file-hash','file-path','file-disagreement','file-duplicate','file-extra','file-missing','file-reordered',
        'file-trailing','file-second-JSON','payload-type','payload-waits','payload-wait-order','payload-wait-bool','payload-decision','payload-extra',
        'attempt-payload','attempt-payload-type','unsupported-script','done-action','done-summary','done-type','done-extra','done-missing')
    read_refusals = ('missing-discovery','discovery-path','response-missing','response-reordered','job-identity','job-proof','job-script',
        'job-bool','wait-stdout-disagreement','wait-stdout-duplicate','wait-stdout-reordered','wait-stdout-null','unknown-stdout-slot','tail-disagreement')
    for family in ('completion','wait','tail'):
        cases = finite_refusals
        if family == 'completion':
            # Completion carries org and payload-file argv values, not job/task/session slots.
            cases = tuple('argv-org' if case == 'argv-job' else 'argv-payload-file' if case == 'argv-session' else case
                          for case in cases if case != 'argv-task')
        cases += completion_refusals if family == 'completion' else read_refusals
        for case in cases:
            for boundary in ('writer','raw','raw-prefix','prefix'):
                with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                    writer.revision = row['source_sha'] = old_source
                    image = finite_input(root,family,('final' if case.startswith('done-') else 'block') if family == 'completion' else 'exit0',late=True)
                    command = image['provider_commands'][-1]; payload = command['payload']; file = command['payload_file']
                    attempt = image['provider_attempts'][0]; instruction = attempt['instruction']; wire = command['wire'][-1]; response = wire['response']
                    if case == 'action': command['action'] = 'other'
                    elif case == 'operation': command['operation'] = 'output'
                    elif case.startswith('argv'):
                        position = {'argv':0,'argv-org':3,'argv-payload-file':-1,'argv-job':3,'argv-task':-3,'argv-session':-1}[case]; command['argv'][position] = 'foreign'
                    elif case == 'exit-bool': command['exit'] = False
                    elif case == 'timed-out': command['timed_out'] = True
                    elif case == 'child': command['child_pid'] = True
                    elif case == 'child-start': command['child_start'] = 'wrong'
                    elif case == 'child-parent': command['child_parent'] = 999
                    elif case.startswith('provider-'): command['provider'][case[9:]] = 999 if case == 'provider-pid' else 'foreign'
                    elif case == 'source': command['source_sha'] = 'f'*40
                    elif case == 'python': command['python'] += 'foreign'
                    elif case == 'callback': command['callback_sha256'] = 'f'*64
                    elif case == 'missing-start': image['starts'] = []
                    elif case == 'duplicate-start': image['starts'] *= 2
                    elif case == 'missing-manifest': image['durable'] = []
                    elif case == 'foreign-manifest': image['durable'][0]['executable_manifest']['revision'] = 'f'*40
                    elif case == 'wrong-root': image = dict(holder=image)
                    elif case == 'wrong-owner': pass
                    elif case == 'command-extra': command['extra'] = None
                    elif case == 'command-missing': del command['child_start']
                    elif case == 'wire-status': wire['status'] = 201
                    elif case == 'wire-status-bool': wire['status'] = True
                    elif case == 'wire-path': wire['path'] += 'foreign'
                    elif case == 'wire-method': wire['method'] = 'DELETE'
                    elif case == 'wire-body': wire['body_sha256'] = 'f'*64
                    elif case == 'wire-dropped': wire['dropped'] = True
                    elif case == 'wire-extra': wire['extra'] = None
                    elif case == 'wire-duplicate': command['wire'].append(copy.deepcopy(wire))
                    elif case == 'wire-missing': command['wire'].pop()
                    elif case == 'response-extra': response['extra'] = None
                    elif case == 'response-bool': response['ok' if family == 'completion' else 'timed_out' if family == 'wait' else 'stream'] = 1
                    elif case == 'attempt-index': attempt['command_index'] = 999
                    elif case == 'attempt-index-bool': attempt['command_index'] = True
                    elif case == 'attempt-duplicate': image['provider_attempts'] *= 2
                    elif case == 'attempt-provider': attempt['provider']['session'] = 'foreign'
                    elif case == 'attempt-start': attempt['provider']['pid_start'] = 'foreign'
                    elif case == 'attempt-operation': instruction['operation'] = 'other'
                    elif case == 'attempt-job': instruction['job'] = 'JOB-999'
                    elif case == 'attempt-deadline': instruction['deadline'] = True
                    elif case == 'attempt-wire': attempt['wire_end'] += 1
                    elif case == 'attempt-extra': attempt['extra'] = None
                    elif case == 'attempt-missing': del attempt['wire_end']
                    elif case == 'attempt-index-missing': del attempt['command_index']
                    elif case in ('stdout-layer','stderr-layer'): command[case.split('-')[0]] = r'{"public":"second\nlayer"}'
                    elif case == 'literal-secret': command['stderr'] = 'SYNTHETIC_TOKEN'
                    elif case == 'escaped-secret': command['stdout'] = r'{"status":"\u0053YNTHETIC_TOKEN","timed_out":false}'+'\n'
                    elif case == 'shared-work': command['stderr'] = 'p'*3000000
                    elif case == 'shared-facts': image['provider_attempts'] = [copy.deepcopy(attempt) for _ in range(10000)]
                    elif case == 'shared-depth':
                        nested = None
                        for _ in range(65): nested = [nested]
                        command['extra'] = nested
                    elif case == 'unsupported-wire-script':
                        command.update(action='other',operation=None,payload=None,payload_file=None,stdout='',stderr='')
                        for entry in command['wire']: entry['response'] = dict(sample='public')
                        while len(command['wire']) < 3:
                            command['wire'].append(dict(path='/ordinary',method='GET',body_sha256=hashlib.sha256(b'').hexdigest(),
                                status=200,response=dict(sample='public'),dropped=False))
                        command['wire'][2]['response'] = dict(script_text=source_forms['F4-one-backslash'])
                        image['provider_attempts'] = []
                    elif case.startswith('file-'):
                        if case == 'file-bytes': file['bytes'] += 1
                        elif case == 'file-hash': file['sha256'] = 'f'*64
                        elif case == 'file-path': file['path'] += 'foreign'
                        elif case == 'file-disagreement': file['text'] = file['text'].replace('repeated repeated','changed changed')
                        elif case == 'file-duplicate': file['text'] = file['text'][:-1]+',"confidence":90}'
                        elif case == 'file-extra': file['text'] = file['text'][:-1]+',"extra":null}'
                        elif case == 'file-missing': file['text'] = file['text'].replace('"confidence": 90, ','')
                        elif case == 'file-reordered': file['text'] = json.dumps(dict(reversed(list(payload.items()))))
                        elif case == 'file-trailing': file['text'] += '\n'
                        elif case == 'file-second-JSON': file['text'] = json.dumps(file['text'])
                        if case not in ('file-bytes','file-hash','file-path'):
                            file['bytes'] = len(file['text'].encode()); file['sha256'] = hashlib.sha256(file['text'].encode()).hexdigest()
                            file['path'] = str(root/'coord'/(file['sha256']+'.json')); command['argv'][-1] = file['path']
                    elif case == 'payload-type': payload['confidence'] = True
                    elif case == 'payload-waits': payload['waiting_on_job_ids'] = None
                    elif case == 'payload-wait-order': payload['waiting_on_job_ids'].reverse()
                    elif case == 'payload-wait-bool': payload['waiting_on_job_ids'][0] = True
                    elif case == 'payload-decision': payload['decision'] = dict(action='done',summary=completion_summary)
                    elif case == 'payload-extra': payload['extra'] = None
                    elif case == 'attempt-payload': instruction['payload']['summary'] += 'foreign'
                    elif case == 'attempt-payload-type': instruction['payload']['confidence'] = True
                    elif case == 'unsupported-script': payload['script'] = source_forms['F4-one-backslash']
                    elif case == 'done-action': payload['decision']['action'] = 'delegate'
                    elif case == 'done-summary': payload['decision']['summary'] += 'foreign'
                    elif case == 'done-type': payload['decision'] = []
                    elif case == 'done-extra': payload['decision']['extra'] = None
                    elif case == 'done-missing': del payload['decision']['action']
                    elif case == 'missing-discovery': command['wire'].pop(0)
                    elif case == 'discovery-path': command['wire'][0]['path'] = '/api/v1/orgs/foreign'
                    elif case == 'response-missing': del response[next(iter(response))]
                    elif case == 'response-reordered': wire['response'] = dict(reversed(list(response.items())))
                    elif case == 'job-identity': response['id'] = 'JOB-999'
                    elif case == 'job-proof': image['provider_commands'] = [command]; attempt['command_index'] = 0
                    elif case == 'job-script': response['script_text'] = 'print("changed")'
                    elif case == 'job-bool': response['exit_code'] = False
                    elif case == 'wait-stdout-disagreement': command['stdout'] = '{"status":"failed","timed_out":false}\n'
                    elif case == 'wait-stdout-duplicate': command['stdout'] = '{"status":"completed","status":"completed","timed_out":false}\n'
                    elif case == 'wait-stdout-reordered': command['stdout'] = '{"timed_out":false,"status":"completed"}\n'
                    elif case == 'wait-stdout-null': command['stdout'] = '{"status":null,"timed_out":false}\n'
                    elif case == 'unknown-stdout-slot': command.update(action='jobs-read',operation='show',stdout=r'{"script_text":"print(\"public\\nline\")"}')
                    elif case == 'tail-disagreement': command['stdout'] += 'foreign\n'
                    else: raise AssertionError(case)
                    name = 'e07.json' if case == 'wrong-owner' else 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                    destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                    acquire(); drain(); commit()
                    earlier = {p.name:p.read_bytes() for p in output.iterdir()}; old_tree = copy.deepcopy(state['safe_scenario']); old_buffer = staged[name]
                    original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                    if boundary in ('writer','prefix'):
                        if boundary == 'prefix':
                            source_file = root/'raw/observations.json'; source_file.write_bytes(original_image)
                        try:
                            if boundary == 'writer': writer._atomic_json(destination,image)
                            else: writer._prepare_original_evidence(destination,source_file)
                        except ValueError: pass
                        except AssertionError as exc:
                            assert case == 'wrong-owner' and boundary == 'prefix'
                            assert str(exc) == 'original evidence owner/path unavailable; observed=None'
                        else: raise AssertionError('finite unsafe member admitted: '+family+'/'+case+'/'+boundary)
                        assert destination.read_bytes() == prior and not destination.with_name(name+'.tmp').exists()
                        assert staged[name] is old_buffer and not state['fatal']
                    else:
                        destination.write_bytes(original_image); (root/'raw/eligible-finite.txt').write_bytes(b'eligible ordinary sibling\n')
                        complete,error = acquire(); drain()
                        assert not complete and error is not None and not state['fatal'] and not errors, (family,case,boundary,failures)
                        assert staged[name] is old_buffer and published[name] is old_buffer and (output/name).read_bytes() == prior
                        assert name in state['refused_sources']
                        if case not in ('shared-work','shared-facts'):
                            assert (output/'eligible-finite.txt').read_bytes() == b'eligible ordinary sibling\n'
                    exact(state['safe_scenario'],old_tree)
                    for old_name,old_bytes in earlier.items(): assert (output/old_name).read_bytes() == old_bytes
                    assert not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                    results[f'C5-Finite-refusal-{family}-{case}-{boundary}'] = 'entire original candidate withheld; all prior member/result/FAIL/tree bytes exact; ordinary sibling eligibility and shared accounting retained; overlapping guards limit proof'

    # C5/C6: required original trees at the two shipping preparation/file
    # boundaries. Full Controller.evidence / E07 admission are NOT RUN here.
    originals = {
        'witness': ('launch-witnesses.jsonl', 'witness/identities.jsonl', raw_lines, line_values,
                    96, 'dda9febd81e71b7b1d4903835e0ef7d062087d856275692ce4d1193eab7ee060'),
        'prefix': ('e07-prefix.json', 'raw/observations.json', raw_object, object_value,
                   178, 'b61b97cce1e792f408ad6e40ea4b7310a6b20de88dcd899da2997c95bb975131'),
    }
    for kind,(name,relative,image,wanted,length,digest) in originals.items():
        for framing in ('LF','CRLF'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                source, destination = root/relative, root/'raw'/name
                source.parent.mkdir(exist_ok=True)
                source_bytes = image if framing == 'LF' else image.replace(b'\n',b'\r\n')
                source.write_bytes(source_bytes)
                witness_rows = {}
                candidate,parsed = writer._prepare_original_evidence(destination,source,witness_rows=witness_rows)
                exact(parsed,wanted)
                assert candidate == source_bytes and not destination.exists(), 'original preparation published before closure'
                assert not destination.with_name(name+'.tmp').exists()
                writer._replace_evidence(destination,candidate)
                assert source.read_bytes() == destination.read_bytes() == source_bytes
                if framing == 'LF':
                    assert len(candidate) == length and hashlib.sha256(candidate).hexdigest() == digest
                    if kind == 'witness':
                        assert hashlib.sha256(candidate[:55]).hexdigest() == '97be040eeac0844d1e9a6de06299099667c925cb5897731a32ef085df6b30577'
                assert witness_rows == ({'witness_incomplete_line':False} if kind == 'witness' else {})
                acquire(); drain(); commit()
                assert (output/name).read_bytes() == source_bytes and not errors
                exact(parsed,wanted)
                results['C5-C6-original-'+kind+'-'+framing] = 'one required full typed ordered tree and exact original source/prepared/file/collector bytes; original digest/framing'

    raw_refusals = {
        'wrong-object': ('observations.json',b'[]'),
        'wrong-e07': ('e07.json',b'null'),
        'wrong-prefix': ('e07-prefix.json',b'[]'),
        'wrong-array': ('browser-events.json',b'{}'),
        'wrong-e05-array': ('e05-browser-events.json',b'{}'),
        'empty': ('observations.json',b''),
        'truncated': ('observations.json',b'{"x":['),
        'trailing': ('observations.json',b'{} null'),
        'comma': ('observations.json',b'{"x":1,}'),
        'duplicate': ('observations.json',b'{"x":1,"\\u0078":"SYNTHETIC_TOKEN"}'),
        'escape': ('observations.json',b'{"x":"\\q"}'),
        'surrogate-high': ('observations.json',b'{"x":"\\ud800"}'),
        'surrogate-low': ('observations.json',b'{"x":"\\udc00"}'),
        'surrogate-pair': ('observations.json',b'{"x":"\\ud800\\u0041"}'),
        'utf8': ('observations.json',b'{"x":"\xff"}'),
        'control': ('observations.json',b'{"x":"raw\nline"}'),
        'nan': ('observations.json',b'{"x":NaN}'),
        'infinity': ('observations.json',b'{"x":Infinity}'),
        'nonfinite': ('observations.json',b'{"x":1e999}'),
        'integer': ('observations.json',b'{"x":18446744073709551616}'),
        'int-spelling': ('observations.json',b'{"x":-1234567890123456789012}'),
        'float-spelling': ('observations.json',b'{"x":0.'+b'1'*127+b'}'),
        'zero': ('observations.json',b'{"x":01}'),
        'fraction': ('observations.json',b'{"x":1.}'),
        'exponent': ('observations.json',b'{"x":1e+}'),
        'depth': ('observations.json',b'{"x":'+b'['*65+b'null'+b']'*65+b'}'),
        'facts': ('observations.json',b'{"x":['+b'null,'*100001+b'null]}'),
        'secret-value': ('observations.json',b'{"x":"SYNTHETIC_TOKEN"}'),
        'secret-key': ('observations.json',b'{"SYNTHETIC_TOKEN":1}'),
        'escaped-secret-value': ('observations.json',b'{"x":"PRIVATE_SENTINEL\\\"\\n"}'),
        'escaped-secret-key': ('observations.json',b'{"PRIVATE_SENTINEL\\\"\\n":1}'),
        'backslash': ('observations.json',b'{"x":"public\\\\path"}'),
        'nested-json': ('observations.json',b'{"payload_file":{"text":"{\\\"x\\\":\\\"public\\\\nline\\\"}"}}'),
        'unregistered-json': ('familiar.json',raw_object),
        'unregistered-jsonl': ('familiar.jsonl',raw_lines),
        'line-incomplete': ('launch-witnesses.jsonl',b'{"index":1}\n{"index":2}'),
        'line-malformed': ('launch-witnesses.jsonl',b'{"index":1}\n{"index":}\n'),
        'line-duplicate': ('launch-witnesses.jsonl',b'{"index":1}\n{"x":1,"\\u0078":2}\n'),
        'line-type': ('launch-witnesses.jsonl',b'{"index":1}\n[]\n'),
        'line-secret': ('launch-witnesses.jsonl',b'{"index":1}\n{"x":"SYNTHETIC_TOKEN"}\n'),
        'line-backslash': ('launch-witnesses.jsonl',b'{"index":1}\n{"x":"public\\\\path"}\n'),
        'line-empty': ('launch-witnesses.jsonl',b''),
        'line-blank': ('launch-witnesses.jsonl',b'{"index":1}\n\n'),
        'line-facts': ('launch-witnesses.jsonl',(b'{"x":['+b'null,'*50001+b'null]}\n')*2),
        'scalar-work': ('observations.json',b'{"x":"'+b'a'*1700000+b'"}'),
        'candidate-work': ('observations.json',b'{}'+b' '*4200000),
    }
    for case,(name,source_bytes) in raw_refusals.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            # Establish real prior full file/tree/FAIL commits, then refuse a
            # changed image at the second EXISTING collection opportunity.
            prior = b'{"prior":"safe"}\n' if name.endswith('.jsonl') else (b'[]' if name in ('browser-events.json','e05-browser-events.json') else b'{"prior":"safe"}')
            (root/'raw'/name).write_bytes(prior)
            acquire(); drain(); commit()
            old_tree, old_result = copy.deepcopy(state['safe_scenario']), (output/'result.json').read_bytes()
            old_buffer, first = staged[name], failures[0]
            (root/'raw'/name).write_bytes(source_bytes)
            (root/'raw/eligible.txt').write_bytes(b'eligible ordinary sibling\n')
            complete,error = acquire(); drain()
            assert not complete and error is not None and not state['fatal'] and not errors, case
            refusal = next(f for f in failures if f[0] is error[0])
            assert refusal[4][0] == ('TEXT_READ' if case in ('utf8','candidate-work') else 'REDACT'), (case,refusal[4])
            if case == 'candidate-work':
                # Both original byte/decode images refuse before acquisition;
                # annotations still describe the last returned scenario.
                exact(list(refusal[4]),['TEXT_READ','CAPACITY',500,500])
            if case in ('int-spelling','float-spelling','depth','facts','line-facts','scalar-work','candidate-work'):
                assert refusal[4][1] == 'CAPACITY', (case,refusal[4])
            if case == 'duplicate':
                assert isinstance(error[0],json.JSONDecodeError), 'duplicate must refuse BEFORE its sensitive value'
            assert name in state['refused_sources'] and failures[0] is first
            assert staged[name] is old_buffer and published[name] is old_buffer
            assert (output/name).read_bytes() == prior and not (output/(name+'.tmp')).exists(), case
            exact(state['safe_scenario'],old_tree)
            assert (output/'result.json').read_bytes() == old_result
            assert (root/'raw'/name).read_bytes() == source_bytes
            if case not in ('facts','line-facts','scalar-work','candidate-work'):
                assert (output/'eligible.txt').read_bytes() == b'eligible ordinary sibling\n', case
            # Guard exhaustion may also withhold the later sibling; no refund.
            results['C1-C5-C7-raw-refusal-'+case] = 'whole new image withheld; prior full bytes/tree/FAIL and first cause retained; ordinary/fact/work boundary attributed'

    # Reuse the SAME strict-format negatives at the required-tree owner. The
    # existing scan rows above own a different collector boundary.
    original_refusals = {case:image for case,(name,image) in raw_refusals.items()
                         if name == 'observations.json' and case not in ('scalar-work','candidate-work')}
    original_refusals['duplicate'] = b'{"x":1,"\\u0078":2}'
    original_refusals['literal-across-tokens'] = b'{"a":1,"b":2}'
    original_refusals['output-work'] = b'{"a":"'+b'a'*550000+b'","b":"'+b'b'*550000+b'"}'
    for kind,(name,relative,prior,wanted,length,digest) in originals.items():
        cases = dict(original_refusals)
        if kind == 'witness':
            cases = {case:b'{"index":1}\n'+image+b'\n' for case,image in cases.items()}
            cases.update({case:image for case,(raw_name,image) in raw_refusals.items()
                          if raw_name == name and case != 'line-facts'})
            cases['output-work'] = (b'{"x":"'+b'a'*550000+b'"}\n')*2
        for case,image in cases.items():
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                source, destination = root/relative, root/'raw'/name
                source.parent.mkdir(exist_ok=True)
                source.write_bytes(prior)
                candidate,parsed = writer._prepare_original_evidence(destination,source)
                writer._replace_evidence(destination,candidate)
                acquire(); drain(); commit()
                old_files = {p.name:p.read_bytes() for p in output.iterdir()}
                old_tree = copy.deepcopy(state['safe_scenario'])
                old_work,old_facts = writer._evidence_budget.work,writer._evidence_budget.facts
                witness_rows = {}
                if case == 'literal-across-tokens':
                    # A literal original secret spans JSON syntax, not a decoded
                    # key/value; the whole original image still must refuse.
                    writer.secrets.add('1,"b"')
                    writer.prepare_redaction()
                source.write_bytes(image)
                try:
                    candidate,parsed = writer._prepare_original_evidence(destination,source,witness_rows=witness_rows)
                    writer._replace_evidence(destination,candidate)
                except (ValueError,UnicodeError) as error:
                    assert writer._evidence_fatal is None, (kind,case,error)
                    if case == 'utf8':
                        assert isinstance(error,UnicodeDecodeError) and error.object == b''
                    if case == 'duplicate':
                        assert isinstance(error,json.JSONDecodeError), (kind,case,error)
                    if case in ('output-work','facts','depth','int-spelling','float-spelling'):
                        assert type(error).__name__ == '_EvidenceCapacity', (kind,case,error)
                else:
                    raise AssertionError('original image admitted: '+kind+'/'+case)
                assert source.read_bytes() == image
                assert destination.read_bytes() == prior, (kind,case,'prior original replaced')
                assert len(prior) == length and hashlib.sha256(destination.read_bytes()).hexdigest() == digest
                assert not destination.with_name(name+'.tmp').exists(), (kind,case)
                assert {p.name:p.read_bytes() for p in output.iterdir()} == old_files
                exact(state['safe_scenario'],old_tree)
                assert writer._evidence_budget.work > old_work and writer._evidence_budget.facts > old_facts
                if kind == 'witness' and case == 'line-incomplete':
                    assert witness_rows == {'witness_incomplete_line':True}
                if case not in ('output-work','facts','depth','int-spelling','float-spelling'):
                    writer._atomic_text(root/'raw/ordinary-after.txt','eligible ordinary continuation\n')
                    assert (root/'raw/ordinary-after.txt').read_bytes() == b'eligible ordinary continuation\n'
                results['C5-C6-C7-original-refusal-'+kind+'-'+case] = 'whole required image refused before pending/assignment; complete prior original/hash/length/tree/member/FAIL retained; no work refund'

    # A changed public image is NOT a checksum violation at these producers.
    # Independent full readers retain their separate byte/hash equality duty.
    for kind,(name,relative,image,wanted,length,digest) in originals.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            source = root/relative
            source.parent.mkdir(exist_ok=True)
            changed = image.replace(b'public',b'PUBLIC')
            source.write_bytes(changed)
            candidate,parsed = writer._prepare_original_evidence(root/'raw'/name,source)
            writer._replace_evidence(root/'raw'/name,candidate)
            changed_tree = copy.deepcopy(wanted)
            (changed_tree[0] if kind == 'witness' else changed_tree)['sample'] = 'PUBLIC\nline'
            exact(parsed,changed_tree)
            assert source.read_bytes() == (root/'raw'/name).read_bytes() == changed
            assert hashlib.sha256(candidate).hexdigest() != digest
            results['C5-C6-original-changed-public-'+kind] = 'new safe original image retained exactly; producer has no external expected checksum assertion'

    for kind,(name,relative,image,wanted,length,digest) in originals.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            source, destination = root/relative, root/'raw'/name
            source.parent.mkdir(exist_ok=True)
            source.write_bytes(image)
            candidate,parsed = writer._prepare_original_evidence(destination,source)
            writer._replace_evidence(destination,candidate)
            acquire(); drain(); commit()
            prior_files = {p.name:p.read_bytes() for p in output.iterdir()}
            writer.token = 'CHANGED_ORIGINAL_TOKEN'
            try:
                writer._prepare_original_evidence(destination,source)
            except AssertionError as error:
                assert writer._evidence_fatal[0] is error
            else:
                raise AssertionError('original context drift admitted: '+kind)
            original_fatal = writer._evidence_fatal
            try:
                writer._atomic_text(root/'raw/later.txt','later ordinary member')
            except AssertionError as error:
                # Existing readiness guard emits a fresh refusal, while the
                # original fatal cause/traceback remains latched unchanged.
                assert writer._evidence_fatal is original_fatal
            else:
                raise AssertionError('fatal original context allowed continuation: '+kind)
            assert not (root/'raw/later.txt').exists()
            assert not destination.with_name(name+'.tmp').exists() and destination.read_bytes() == image
            assert {p.name:p.read_bytes() for p in output.iterdir()} == prior_files
            results['C5-C6-original-fatal-context-'+kind] = 'original context drift stops detailed work; entire earlier file/FAIL and actual fatal cause retained'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        for name in ('launch-witnesses.jsonl','e07-prefix.json','ordinary.txt'):
            try:
                writer._atomic_text(root/'raw'/name,raw_object.decode('utf-8'))
            except ValueError:
                pass
            else:
                raise AssertionError('ordinary text gained structured permission: '+name)
            assert not (root/'raw'/name).exists() and not (root/'raw'/(name+'.tmp')).exists()
        results['C5-C6-original-no-text-exemption'] = 'ordinary atomic text retains whole-backslash refusal at every filename'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        (root/'raw/observations.json').write_bytes(b'{"x":"public\\\\path"}')
        acquire(); drain(); commit()
        first,prior_result = failures[0],(output/'result.json').read_bytes()
        assert 'observations.json' not in staged and not (output/'observations.json').exists()
        (root/'raw/observations.json').write_bytes(raw_object)
        (root/'raw/eligible.txt').write_bytes(b'eligible after refusal\n')
        acquire(); drain()
        assert 'observations.json' not in staged and not (output/'observations.json').exists()
        assert not (output/'observations.json.tmp').exists() and failures[0] is first
        assert (output/'result.json').read_bytes() == prior_result
        assert (output/'eligible.txt').read_bytes() == b'eligible after refusal\n'
        results['C6-C7-raw-no-retry'] = 'new refused member never staged, second opportunity never retries repaired source; first cause and eligible sibling preserved'

    # SAME remaining scenario owner across registered raw siblings. Each 1M
    # scalar costs byte read/decode2M + decoded/chunk/join2M + two safe
    # passes2M + candidate1M plus positive facts/keys. Later sources cannot
    # reserve their read/decode images after the first entire sibling stages.
    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        acquire(); drain(); commit()
        prior_result = (output/'result.json').read_bytes()
        (root/'scenario.json').write_bytes(b'{"variants":{},"groups":"'+b'a'*100000+b'"}')
        images = {'e07.json': b'{"x":"'+b'b'*1000000+b'"}',
                  'e07-prefix.json': b'{"x":"'+b'c'*1000000+b'"}',
                  'e05-browser-events.json': b'["'+b'd'*1000000+b'"]'}
        for name,image in images.items(): (root/'raw'/name).write_bytes(image)
        complete,error = acquire(); drain()
        chosen = [name for name in images if name in staged]
        assert len(chosen) == 1 and not complete and error is not None and not state['fatal'] and not errors, chosen
        for name,image in images.items():
            if name in chosen:
                assert staged[name] == (output/name).read_bytes() == image
            else:
                assert not (output/name).exists() and not (output/(name+'.tmp')).exists()
        exact([list(f[4]) for f in failures if f[4][1] == 'CAPACITY'],
              [['TEXT_READ','CAPACITY',100027,100027],['TEXT_READ','CAPACITY',100027,100027]])
        assert (output/'result.json').read_bytes() == prior_result
        exact(state['safe_scenario'],{'variants':{},'groups':'a'*100000})
        results['C1-C6-C7-raw-shared-owner'] = 'one exact full registered sibling, shared original scenario work and no refund; later parser capacity preserves prior FAIL'

    # One owner's nonrefundable scenario work continues into the SAME raw
    # enumeration. Each raw scalar fits alone; together only one can stage.
    # Byte read plus strict decode is reserved before either later acquisition.
    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        acquire(); drain(); commit()
        prior_result = (output/'result.json').read_bytes()
        (root/'scenario.json').write_bytes(b'{"variants":{},"groups":{"value":"'+b'a'*600000+b'"}}')
        (root/'raw/large-a.txt').write_bytes(b'b'*700000)
        (root/'raw/large-b.txt').write_bytes(b'c'*700000)
        (root/'raw/large-c.txt').write_bytes(b'd'*700000)
        complete,error = acquire(); drain()
        assert not complete and error is not None and not state['fatal'] and not errors, 'F3 shared owner unexpectedly renewed'
        chosen = [name for name in ('large-a.txt','large-b.txt','large-c.txt') if name in staged]
        assert len(chosen) == 1, chosen
        for name,content in (('large-a.txt',b'b'*700000),('large-b.txt',b'c'*700000),('large-c.txt',b'd'*700000)):
            if name in chosen: assert (output/name).read_bytes() == content
            else: assert not (output/name).exists() and not (output/(name+'.tmp')).exists()
        exact(state['safe_scenario'],{'variants':{},'groups':{'value':'a'*600000}})
        assert (output/'result.json').read_bytes() == prior_result
        exact([list(f[4]) for f in failures if f[4][1] == 'CAPACITY'],
              [['TEXT_READ','CAPACITY',600037,600037],['TEXT_READ','CAPACITY',600037,600037]])
        results['F3-shared-nonrefundable-siblings'] = 'same scenario/raw owner exhausts; one full sibling and prior complete result retained'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        (root/'scenario.json').write_bytes(b'{"variants":{},"groups":"\\q"}')
        (root/'raw/withheld.txt').write_bytes(b'public\\path')
        complete,error = acquire(); drain(); commit()
        first = failures[0]
        before = (output/'result.json').read_bytes()
        (root/'scenario.json').write_bytes(b'{"variants":{},"groups":{"fresh":"public"}}')
        (root/'raw/withheld.txt').write_bytes(b'public repaired')
        (root/'raw/fresh.txt').write_bytes(b'eligible safe sibling\n')
        complete,error = acquire(); drain()
        assert not complete and error is None and failures[0] is first
        assert not state['fatal'] and not errors
        assert state['safe_scenario'] == {'variants':{}}
        assert (output/'result.json').read_bytes() == before
        assert (output/'fresh.txt').read_bytes() == b'eligible safe sibling\n'
        assert 'withheld.txt' not in staged and not (output/'withheld.txt').exists()
        results['F3-no-failed-source-retry'] = 'second existing opportunity preserves first cause/prior result and admits only eligible sibling'

    # SAME F2/C3/C6/C7 table: actual save and the existing bare-controller venue.
    # The actual existing save now uses typed compact bytes, no final LF.
    from e07 import save as e07_save
    compact_input = {'first':[True,False,None,7,1.5], 'sample':'public\nline', 'quote':'"', 'unicode':'中'}
    compact_bytes = b'{"first":[true,false,null,7,1.5],"sample":"public\\nline","quote":"\\\"","unicode":"\xe4\xb8\xad"}'
    for case in ('save', 'capture', 'missing-token', 'changed-context', 'changed-token',
                 'marker-overlap', 'admission', 'pending'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root / 'raw/e07.json'
            prior = b'{"previous":"complete"}'
            path.write_bytes(prior)
            writer.e07 = copy.deepcopy(compact_input)
            budget = writer._evidence_budget
            spent = budget.work
            prepared = writer._evidence_context
            if case == 'capture':
                # Actual capture -> remember -> context publication, no mocked capture.
                writer.stores, writer.threads_active = {}, True
                writer.secrets.add(writer.token)
            elif case == 'missing-token': writer.token = ''
            elif case == 'changed-context': writer.secrets.add('NEW_PRIVATE_SENTINEL')
            elif case == 'changed-token': writer.token = 'CHANGED_TOKEN'
            elif case == 'marker-overlap': writer.secrets.add('REDACTED')
            elif case == 'admission': writer.e07 = {'unsupported':object()}
            elif case == 'pending': path.with_name('e07.json.tmp').write_bytes(b'prior pending')
            try:
                if case in ('changed-context', 'changed-token'):
                    # Same prepared opportunity: mutation cannot rescue its writer.
                    writer._atomic_json(path, writer.e07)
                else:
                    e07_save(writer)
            except (AssertionError, ValueError, FileExistsError) as exc:
                assert case not in ('save', 'capture'), (case, type(exc).__name__)
                assert path.read_bytes() == prior, case
                pending = path.with_name('e07.json.tmp')
                assert pending.read_bytes() == b'prior pending' if case == 'pending' else not pending.exists()
                if case in ('missing-token', 'changed-context', 'changed-token', 'marker-overlap'):
                    assert not writer.redaction_ready and writer._evidence_fatal is not None
                    original = writer._evidence_fatal
                    try: e07_save(writer)
                    except BaseException: pass
                    else: raise AssertionError('fatal finalizer wrote evidence')
                    assert writer._evidence_fatal is original and path.read_bytes() == prior and not pending.exists()
                if case in ('missing-token', 'marker-overlap', 'admission', 'pending'):
                    assert writer.primary_error is exc and writer.primary_traceback is not None
            else:
                assert case in ('save', 'capture'), 'unsafe save admitted: '+case
                assert path.read_bytes() == compact_bytes
                exact(json.loads(path.read_bytes()), compact_input)
                assert not path.with_name('e07.json.tmp').exists()
                assert writer._evidence_replacements[-1]['path'] == path
                assert writer._evidence_replacements[-1]['replaced'] is True
                if case == 'capture':
                    assert json.loads((root/'coord/redaction-context.json').read_text()) == sorted([writer.token,secret])
            assert writer._evidence_budget is budget and budget.work >= spent
            assert prepared[-2] == (secret,), 'later preparation mutated original snapshot'
            results['F2-save-'+case] = 'actual save/prepared-writer whole bytes or refusal; prior bytes retained; native chronology NOT RUN'

    # C3/C4/C5/F3 at the actual writer/file and selected-seven boundaries.
    # No registered nested-format positive or collector roundtrip is implied.
    class StringSubclass(str): pass
    class IntegerSubclass(int): pass
    class FloatSubclass(float): pass
    class ListSubclass(list): pass
    class DictSubclass(dict): pass
    cycle = []; cycle.append(cycle)
    alias = {'value':'a'*1400000}
    deep = None
    for _ in range(65): deep = [deep]
    identity_digest = hashlib.sha256(secret.encode('utf8')).hexdigest()
    invalid = {
        'string-subclass': {'sample':StringSubclass('public')},
        'integer-subclass': {'sample':IntegerSubclass(7)},
        'float-subclass': {'sample':FloatSubclass(1.5)},
        'list-subclass': ListSubclass([None]), 'dict-subclass': DictSubclass(sample='public'),
        'key-subclass': {StringSubclass('sample'):'public'}, 'nonstring-key': {7:'public'},
        'unsupported': {'sample':object()}, 'nan': {'sample':float('nan')},
        'infinity': {'sample':float('inf')}, 'surrogate': {'sample':'\ud800'},
        'key-surrogate': {'\udfff':'public'}, 'integer-overflow': {'sample':1<<64},
        'cycle': cycle, 'depth': deep, 'facts': [None]*200001,
        'backslash': {'sample':'public\\path'}, 'key-backslash': {'public\\key':'public'},
        'collision': {secret:'first','[REDACTED]':'second'},
        'identity-after': {'sample':secret,'byte_length':len(secret.encode())},
        'identity-before': {'sha256':identity_digest,'sample':secret},
        'identity-bytes': {'sample':secret,'bytes':len(secret.encode('utf8'))},
        'identity-nested-after': {'sample':secret,'later':{'entity_copy':None}},
        'identity-key-change': {secret:'public','original_byte_check':True},
        'identity-suffix': {'sample':secret,'later':{'body_bytes':17}},
        'identity-hash-suffix': {'sample':secret,'later':{'body_sha256':identity_digest}},
        'encoded-size': {'sample':'\0'*1500000},
        'candidate-reservation': {'sample':'a'*2200000},
        'alias-work': {'a':alias,'b':alias},
    }
    for case,original in invalid.items():
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root/'raw/diagnostic.json'
            prior = b'{"previous":[true,null,7,"complete"]}'
            path.write_bytes(prior)
            acquire(); drain(); commit()
            old_result = (output/'result.json').read_bytes()
            old_tree = copy.deepcopy(state['safe_scenario'])
            spent, owner, context = writer._evidence_budget.work, writer._evidence_budget, writer._evidence_context
            original_identity = copy.deepcopy(original) if case.startswith('identity-') else None
            try:
                writer._atomic_json(path,original)
            except ValueError as exc:
                if case == 'encoded-size': assert str(exc) == 'evidence encoded capacity refused'
                elif case == 'candidate-reservation': assert str(exc) == 'evidence capacity refused'
                elif case == 'depth': assert str(exc) == 'evidence depth capacity refused'
                elif case.startswith('identity-'): assert str(exc) == 'authenticated evidence requires original bytes'
                elif case == 'collision': assert str(exc) == 'evidence redacted key collision'
            else:
                raise AssertionError('typed writer admitted '+case)
            assert path.read_bytes() == prior and not path.with_name(path.name+'.tmp').exists(), case
            assert writer._evidence_budget is owner and owner.work >= spent and owner.reserved == 0
            assert writer._evidence_context is context and writer._evidence_fatal is None
            exact(state['safe_scenario'],old_tree)
            assert (output/'result.json').read_bytes() == old_result
            assert (output/'safe.json').read_bytes() == b'{"sample":"public"}'
            assert (output/'failure.txt').read_bytes() == b'safe before [REDACTED] after\n'
            if case.startswith('identity-'):
                assert original.get('sample') == secret or secret in original, 'negative lost ORIGINAL secret'
                exact(original,original_identity)
                writer._atomic_text(root/'raw/eligible-sibling.txt','eligible public sibling\n')
                assert (root/'raw/eligible-sibling.txt').read_bytes() == b'eligible public sibling\n'
            results['C4-C5-F3-writer-'+case] = 'whole original candidate refused before pending; prior complete file/tree/result/sibling exact; reached guard attributed where stated'

    for case in ('cross-span-replacement','postcondition'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root/'raw/e07.json'
            prior = b'{"previous":"complete"}'
            path.write_bytes(prior)
            # Long original context exercises multiple <=4096B matcher chunks;
            # the independent full safe oracle is still literal, never rendered.
            writer.secrets.update({'A'*700,'z'+'p'*600+'[RE'} if case == 'cross-span-replacement' else {'x[RE','cat'})
            writer.e07 = {'sample':'z'+'p'*600+'A'*700 if case == 'cross-span-replacement' else 'xcat'}
            if case == 'cross-span-replacement':
                e07_save(writer)
                assert path.read_bytes() == b'{"sample":"[REDACTED]DACTED]"}'
            else:
                previous_ref = writer.groups['E07']['first_error_ref']
                try: e07_save(writer)
                except ValueError as exc:
                    assert str(exc) == 'unsafe evidence sanitation postcondition'
                    assert writer.primary_error is exc and writer.primary_traceback is not None
                    assert writer._evidence_fatal[0] is exc and not writer.redaction_ready
                else: raise AssertionError('writer sanitation postcondition ignored')
                fatal = writer._evidence_fatal
                try: e07_save(writer)
                except ValueError: pass
                else: raise AssertionError('fatal save performed another publication')
                assert writer._evidence_fatal is fatal and writer.groups['E07']['first_error_ref'] == previous_ref
                assert path.read_bytes() == prior and not path.with_name(path.name+'.tmp').exists()
            results['C4-C6-C7-writer-'+case] = 'actual full virtual-span sanitation or fatal original first-error/refusal facts; prior bytes retained'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        path = root/'raw/diagnostic.json'
        writer._atomic_json(path,{'sample':secret})
        assert path.read_bytes() == b'{"sample":"[REDACTED]"}'
        writer._atomic_json(path,{secret:secret})
        assert path.read_bytes() == b'{"[REDACTED]":"[REDACTED]"}'
        repeated = {'z':[True,False,None,7,1.5,'public\nline','中']}
        original = {'b':repeated,'a':repeated}
        literal = {'b':{'z':[True,False,None,7,1.5,'public\nline','中']},
                   'a':{'z':[True,False,None,7,1.5,'public\nline','中']}}
        writer._atomic_json(path,original)
        assert path.read_bytes() == b'{"b":{"z":[true,false,null,7,1.5,"public\\nline","\xe4\xb8\xad"]},"a":{"z":[true,false,null,7,1.5,"public\\nline","\xe4\xb8\xad"]}}'
        exact(json.loads(path.read_bytes()),literal)
        exact(original,literal)
        writer._atomic_json(path,{'first':[18446744073709551615,-18446744073709551615,-0.0,1e308], 'byte_length':7})
        assert path.read_bytes() == b'{"first":[18446744073709551615,-18446744073709551615,-0.0,1e+308],"byte_length":7}'
        results['C3-C4-writer-literal-types-alias-key-redaction'] = 'independent complete compact bytes; exact original/repeated type/order; real original secret redacted before escaping; safe identity metadata unchanged'

    for case in ('encoded-predicate','nonrefundable-writer','plain-text-backslash'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root/'raw/diagnostic.json'
            acquire(); drain(); commit()
            old_result = (output/'result.json').read_bytes()
            old_tree = copy.deepcopy(state['safe_scenario'])
            if case == 'encoded-predicate':
                try:
                    scenario_fields({'variants':{},'groups':{'value':'\0'*1500000}}, lambda text:text, context=[secret])
                except ValueError as exc: assert str(exc) == 'evidence encoded capacity refused'
                else: raise AssertionError('selected-seven encoded predicate admitted >C')
            elif case == 'nonrefundable-writer':
                original = {'sample':'a'*1060000}
                writer._atomic_json(path,original)
                prior = b'{"sample":"'+b'a'*1060000+b'"}'
                assert path.read_bytes() == prior
                owner, spent = writer._evidence_budget, writer._evidence_budget.work
                try: writer._atomic_json(path,original)
                except ValueError as exc: assert str(exc) == 'evidence capacity refused'
                else: raise AssertionError('writer renewed original allowance')
                assert writer._evidence_budget is owner and owner.work > spent and owner.reserved == 0
                assert path.read_bytes() == prior and not path.with_name(path.name+'.tmp').exists()
            else:
                prior = b'complete previous text\n'
                path.write_bytes(prior)
                try: writer._atomic_text(path,'public\\path\n')
                except ValueError as exc: assert str(exc) == 'evidence text representation withheld'
                else: raise AssertionError('plain text got structured permission')
                assert path.read_bytes() == prior and not path.with_name(path.name+'.tmp').exists()
            exact(state['safe_scenario'],old_tree)
            assert (output/'result.json').read_bytes() == old_result
            results['C1-C2-C6-C7-'+case] = 'independent encoded/no-refund/literal-text guard; prior full detached tree and committed bytes survive'

    for case in ('R1a','R1b-missing','R1b-decode','R1c-txt','R1c-jsonl','R1c-malformed'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            writer._atomic_json(root / 'raw/observations.json', {'sample':secret})
            assert (root / 'raw/observations.json').read_bytes() == b'{"sample":"[REDACTED]"}'
            # ORIGINAL escaped secret injection exercises the independent raw
            # collector guard; the redacting writer is never its negative oracle.
            (root / 'raw/observations.json').write_bytes(b'{"sample":"PRIVATE_SENTINEL\\"\\n"}')
            if case == 'R1b-missing':
                (root / 'scenario.json').unlink()
            elif case == 'R1b-decode':
                (root / 'scenario.json').write_text('{')
            if case.startswith('R1c'):
                name = 'unsupported.jsonl' if case == 'R1c-jsonl' else 'unsupported.txt'
                original = '{\\' if case == 'R1c-malformed' else 'public\\path\n'
                try: writer._atomic_text(root / 'raw' / name, original)
                except ValueError: pass
                else: raise AssertionError('plain backslash writer admitted')
                assert not (root/'raw'/name).exists() and not (root/'raw'/(name+'.tmp')).exists()
                (root/'raw'/name).write_text(original)
            complete, error = acquire()
            drain()
            value = commit()
            assert not (output / 'observations.json').exists(), 'escaped raw observations unexpectedly published'
            assert not errors and 'observations.json' not in staged
            assert not (output / 'observations.json.tmp').exists()
            assert (output / 'failure.txt').is_file(), 'ordinary refusal suppressed safe raw sibling'
            assert not complete and not state['fatal'] and error is not None
            assert (output / 'failure.txt').read_bytes() == b'safe before [REDACTED] after\n'
            assert (output / 'safe.json').read_bytes() == b'{"sample":"public"}'
            assert value['status'] == 'FAIL' and value['cleanup']['ok'] is False and value['timing']['cleanup']['ok'] is False
            if case.startswith('R1b'):
                assert value['variants'] == {} and 'groups' not in value
                first = failures[0]
                assert first[3] == ('SCENARIO_PRESENT' if case.endswith('missing') else 'SCENARIO_DECODE')
                assert (first[0] is None if case.endswith('missing') else isinstance(first[0],json.JSONDecodeError))
                assert first[1] is None if first[0] is None else first[1] is not None
            else:
                assert value['variants'] == {'E05.compose-race':{'status':'FAIL','error':'[REDACTED]'}}
                for key in ('groups','e07_admission','failures','finalization','scenario_phases'):
                    assert value[key] == expected[key]
                assert failures[0][3] == 'REDACT'
                assert failures[0][4][:2] == ('REDACT','VALUE')
            if case.startswith('R1c'):
                assert name not in staged and not (output/name).exists()
            assert 'PRIVATE_SENTINEL' not in (output / 'result.json').read_text()
            assert _collection_line('cleanup',failures[0][3],'F' if failures[0][0] else 'U',failures[0][4]).startswith(b'HRD1:C:CLEANUP:')
            results['B09-B12-'+case] = 'literal source proven; unsafe raw withheld; safe siblings and actual FAIL committed'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        writer._atomic_json(root / 'raw/observations.json', {'sample':'public-v1'})
        acquire(); drain(); commit()
        earlier = (output / 'result.json').read_bytes()
        assert (output / 'observations.json').read_bytes() == b'{"sample":"public-v1"}'
        snapshot = copy.deepcopy(state['safe_scenario'])
        writer._atomic_json(root / 'raw/observations.json', {'sample':secret})
        assert (root / 'raw/observations.json').read_bytes() == b'{"sample":"[REDACTED]"}'
        (root / 'raw/observations.json').write_bytes(b'{"sample":"PRIVATE_SENTINEL\\"\\n"}')
        (root / 'scenario.json').write_text('{')
        writer._atomic_text(root / 'raw/new.txt','new safe sibling\n')
        complete, error = acquire()  # Only the second original opportunity.
        drain()
        assert not complete and error is not None and state['safe_scenario'] == snapshot
        assert (output / 'observations.json').read_bytes() == b'{"sample":"public-v1"}'
        assert not (output / 'observations.json.tmp').exists()
        assert (output / 'new.txt').read_bytes() == b'new safe sibling\n'
        # Genuine later serialization refusal leaves the initial actual result.
        try:
            publish_failure(output,(output.stat().st_dev,output.stat().st_ino),dict(bad=object()),check)
        except ValueError as exc:
            assert str(exc) == 'evidence value unavailable'  # Typed encoder taxonomy.
        else:
            raise AssertionError('unserializable later result accepted')
        assert (output / 'result.json').read_bytes() == earlier
        assert not (output/'result.tmp').exists()
        results['B09-B12-R1d'] = 'old commit/snapshot exact; new safe sibling drained after later refusal; earlier FAIL exact'

    # Complete-safe presence positive and empty-required negatives are distinct
    # from the escaped source. None supplies native/content acceptance.
    for empty in (None, 'observations.json', 'lifecycle.png'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            for name in BASELINE_ARTIFACTS:
                if name.endswith('.png'):
                    (root/'raw'/name).write_bytes(b'unchanged synthetic image bytes')
                elif name.endswith('.jsonl'):
                    writer._atomic_text(root/'raw'/name,'{}\n')
                elif name.endswith('.json'):
                    writer._atomic_json(root/'raw'/name,[] if name in ('browser-events.json','e05-browser-events.json') else {'sample':'public'})
                else:
                    writer._atomic_text(root/'raw'/name,'safe log\n')
            if empty is not None:
                (root/'raw'/empty).write_bytes(b'')
            complete, error = acquire(); drain(); value = commit()
            assert complete is (empty is None) and (error is not None if empty == 'observations.json' else error is None) and not errors
            assert failures == [] if empty is None else failures[0][3] == ('REDACT' if empty == 'observations.json' else 'REQUIRED')
            assert value['variants'] == {'E05.compose-race':{'status':'FAIL','error':'[REDACTED]'}}
            if empty != 'observations.json':
                assert (output/'observations.json').read_bytes() == b'{"sample":"public"}'
            assert value['status'] == 'FAIL'
            results['B09-B11-complete-safe-file' if empty is None else 'B09-B11-empty-required-'+empty] = 'actual safe presence/required emptiness and decoded FAIL publication; no native acceptance'

    for case in ('valid-empty','zero-byte','absent','open-directory','invalid-utf8','truncated-utf8','missing-variants','non-object',
                 'nonfinite','key-collision','deep','exact-cap','over-cap','multibyte-cap','long-prefix','raw-directory'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            path = root / 'scenario.json'
            if case == 'valid-empty': path.write_text('{"variants":{}}')
            elif case == 'zero-byte': path.write_bytes(b'')
            elif case == 'absent': path.unlink()
            elif case == 'open-directory': path.unlink(); path.mkdir()
            elif case == 'invalid-utf8': path.write_bytes(b'\xff')
            elif case == 'truncated-utf8': path.write_bytes(b'{"variants":{},"groups":"\xf0\x9f\x98')
            elif case == 'missing-variants': path.write_text('{}')
            elif case == 'non-object': path.write_text('[]')
            elif case == 'nonfinite': path.write_text('{"variants":{},"failures":[NaN]}')
            elif case == 'key-collision': path.write_text('{"variants":{},"groups":{"SYNTHETIC_TOKEN":1,"[REDACTED]":2}}')
            elif case == 'deep': path.write_text('{"variants":{},"groups":' + '['*65 + 'null' + ']'*65 + '}')
            elif case == 'exact-cap': path.write_text('{"variants":{}}' + ' '*(8388608-15))
            elif case == 'over-cap': path.write_text('x'*8388609)
            elif case == 'multibyte-cap': path.write_text('é'*4194305)
            elif case == 'long-prefix': path.write_text('x'*8388619)
            elif case == 'raw-directory': (root/'raw/broken.txt').mkdir()
            (root/'raw/empty.txt').write_bytes(b'')
            complete, error = acquire(); drain(); value = commit()
            assert not complete and not state['fatal'] and not errors
            assert (output/'failure.txt').is_file(), case
            assert (output/'failure.txt').read_bytes() == b'safe before [REDACTED] after\n'
            assert (output/'empty.txt').read_bytes() == b''
            assert value['status'] == 'FAIL' and value['cleanup']['ok'] is False
            first = failures[0]
            if case in ('valid-empty','raw-directory'):
                assert value['variants'] == ({} if case != 'raw-directory' else {'E05.compose-race':{'status':'FAIL','error':'[REDACTED]'}})
                assert first[3] == ('TEXT_READ' if case == 'raw-directory' else 'REQUIRED')
            elif case in ('exact-cap','over-cap','multibyte-cap','long-prefix'):
                # Original context already spent work: the input reservation
                # refuses before read. This is NOT a C+1 returned-slice witness.
                assert first[4] == ('SCENARIO_READ','CAPACITY','U','U')
                assert value['variants'] == {}
            elif case in ('open-directory','invalid-utf8','truncated-utf8'):
                assert first[4] == (('SCENARIO_OPEN','IO','U','U') if case == 'open-directory' else ('SCENARIO_READ','UNICODE','U','U'))
            assert first[0] is error[0] if error is not None else first[0] is None
            results['B09-B12-file-'+case] = 'real ordinary refusal/presence with independent sibling output and sticky FAIL'

    for case in ('missing-token','empty-token','token-directory','missing-context','context-directory','malformed-context','not-list','incomplete-context',
                 'changed-context','root-drift','marker-overlap','deadline','interruption'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            if case == 'missing-token': (root/'daemon/daemon.token').unlink()
            elif case == 'empty-token': (root/'daemon/daemon.token').write_text('')
            elif case == 'token-directory': (root/'daemon/daemon.token').unlink(); (root/'daemon/daemon.token').mkdir()
            elif case == 'missing-context': (root/'coord/redaction-context.json').unlink()
            elif case == 'context-directory': (root/'coord/redaction-context.json').unlink(); (root/'coord/redaction-context.json').mkdir()
            elif case == 'malformed-context': (root/'coord/redaction-context.json').write_text('{')
            elif case == 'not-list': (root/'coord/redaction-context.json').write_text('{}')
            elif case == 'incomplete-context': (root/'coord/redaction-context.json').write_text('["wrong"]')
            elif case == 'changed-context':
                acquire()  # Retain the actual first complete context, not invented readiness.
                (root/'coord/redaction-context.json').write_text('["SYNTHETIC_TOKEN","changed"]')
            elif case == 'root-drift':
                root.rename(root.with_name('original'))
                shutil.copytree(root.with_name('original'),root)  # Valid context; only identity differs.
            elif case == 'marker-overlap':
                (root/'coord/redaction-context.json').write_text('["SYNTHETIC_TOKEN","REDACTED"]')
                writer._atomic_text(root/'raw/safe.json','REDACTED')
            try:
                if case in ('deadline','interruption'):
                    def stopped() -> None:
                        raise TimeoutError('original allowance') if case == 'deadline' else InterruptedError('original interruption')
                    collect_evidence(root,(root.stat().st_dev,root.stat().st_ino),row,'baseline',staged,state,stopped,
                                     lambda error,**kw: failures.append((error,error.__traceback__,kw['category'],state['window'],kw['annotation'])))
                else:
                    acquire()
            except BaseException as exc:
                assert state['fatal'] and failures
                if case == 'root-drift':
                    assert str(exc) == 'owned evidence root identity changed'
                assert _failure_error(exc) == ('timeout' if case == 'deadline' else 'interruption') if case in ('deadline','interruption') else True
            else:
                raise AssertionError('unsafe detailed collection admitted: '+case)
            before = dict(staged)
            try: drain()
            except ValueError: pass
            else: raise AssertionError('fatal state drained')
            assert staged == before and not list(output.iterdir())
            results['B10-B12-fatal-'+case] = 'actual safety/check stop; detailed publisher refused'

    # Ordinary replace errors and actual result writes, without permission/fault knobs.
    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        staged.update({'blocked.txt':b'safe blocked\n','sibling.txt':b'safe sibling\n'})
        (output/'blocked.txt').mkdir()
        drain()
        assert len(errors) == 1 and failed_members == {'blocked.txt'}
        assert (output/'sibling.txt').read_bytes() == b'safe sibling\n'
        drain()
        assert len(errors) == 1, 'second pass retried failed output member'
        assert (output/'blocked.txt.tmp').read_bytes() == b'safe blocked\n'
        results['B10-B11-output-member'] = 'ordinary replace refusal; safe sibling committed; failed member not retried'

    for case in ('initial-failed-final-committed','both-results-unavailable'):
        with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
            (output/'result.json').mkdir()
            try: commit()
            except IsADirectoryError: pass
            else: raise AssertionError('initial result falsely committed')
            staged['safe.txt'] = b'independent safe bytes\n'
            drain()
            assert (output/'safe.txt').read_bytes() == b'independent safe bytes\n'
            if case == 'initial-failed-final-committed':
                (output/'result.json').rmdir()
                assert commit()['status'] == 'FAIL'
            else:
                try: commit()
                except IsADirectoryError: pass
                else: raise AssertionError('unavailable result falsely committed')
                assert not (output/'result.json').is_file()
            results['B10-B11-'+case] = 'only actual result replacement counts; independent raw bytes committed'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        writer._atomic_json(root/'raw/observations.json', {'sample':'public-v1'})
        acquire(); drain()
        assert (output/'observations.json').read_bytes() == b'{"sample":"public-v1"}'
        writer._atomic_json(root/'raw/observations.json', {'sample':'public-v2'})
        acquire()  # Original second acquisition, no extra source.
        (output/'observations.json.tmp').mkdir()
        drain()
        assert len(errors) == 1 and 'observations.json' in failed_members
        assert (output/'observations.json').read_bytes() == b'{"sample":"public-v1"}'
        assert published['observations.json'] == b'{"sample":"public-v1"}'
        results['B10-B11-prior-member-precommit'] = 'actual pending-open refusal preserves old commit without a retry'

    with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
        acquire(); drain()
        old = (output/'safe.json').read_bytes()
        output.rename(output.with_name('original-output'))
        output.symlink_to(output.with_name('original-output'), target_is_directory=True)
        staged['new.txt'] = b'never publish after destination drift'
        try: drain()
        except ValueError: pass
        else: raise AssertionError('unsafe destination admitted')
        assert state['fatal'] and not (output/'new.txt').exists()
        assert (output/'safe.json').read_bytes() == old
        results['B10-B12-fatal-destination'] = 'literal destination alias refused; old safe output unchanged'

    for error in (TimeoutError(),InterruptedError(),KeyboardInterrupt(),SystemExit(),GeneratorExit(),MemoryError(),RecursionError()):
        assert not _ordinary_evidence_error(error)
    assert _ordinary_evidence_error(UnicodeError()) and _failure_error(UnicodeError()) == 'unicode'
    assert _collection_line('cleanup','UNKNOWN','F',('UNKNOWN','UNKNOWN','U','U')) == b'HRD1:C:CLEANUP:UNKNOWN:F:D1:UNKNOWN:UNKNOWN:U:U\n'
    assert _collection_line('cleanup','SCENARIO_READ','F',('SCENARIO_CAPACITY','CAPACITY',8388609,33554436)) == b'HRD1:C:CLEANUP:SCENARIO_READ:F:D1:SCENARIO_CAPACITY:CAPACITY:8388609:33554436\n'
    results['B10-B12-diagnostic-grammar'] = 'closed literal D1 and subclass precedence; native one-shot fd2 delivery NOT RUN'
    # Complete accepted literal SQL/SOURCE subjects (TASK13807 D2-D5).
    # These are independent INPUT/oracles, never candidate-generated rows.
    case_literals = {'0': {'index': 0,
           'variant': 'E04.approve-exit0',
           'source_sha': 'd6a605d8a22610b0074d87c9523b9b87742513ae',
           's1': 'sess-e04-first',
           'jobs': [{'id': 'JOB-701',
                     'code': 0,
                     'label': 'OK',
                     'token': 'e08eba4596159ebe63677c14',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(0)\n',
                     'script_sha256': '4a07fb8db4cd3501f9c80553cc89170cd1446e3e7d5839a282d9585b03ee62ef',
                     'running': {'id': 'JOB-701',
                                 'task_id': 'TASK-701',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.approve-exit0/e08eba4596159ebe63677c14',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(0)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-701',
                                  'task_id': 'TASK-701',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.approve-exit0/e08eba4596159ebe63677c14',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 0,
                                  'reason': None,
                                  'stdout_head': 'E04_OK_OUT\n',
                                  'stderr_head': 'E04_OK_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-701',
                                  'task_id': 'TASK-701',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.approve-exit0/e08eba4596159ebe63677c14',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}}]},
     '2': {'index': 2,
           'variant': 'E04.completed-exit7',
           'source_sha': 'd6a605d8a22610b0074d87c9523b9b87742513ae',
           's1': 'sess-e04-first',
           'jobs': [{'id': 'JOB-703',
                     'code': 7,
                     'label': 'EXIT7',
                     'token': 'df5f1abd9fb56d8c669f17e2',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(7)\n',
                     'script_sha256': 'afb8c94d6ba24e17e17263e30dd4dc200948616e7fb82e50bac59e19e2f55a00',
                     'running': {'id': 'JOB-703',
                                 'task_id': 'TASK-703',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(7)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-703',
                                  'task_id': 'TASK-703',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 7,
                                  'reason': None,
                                  'stdout_head': 'E04_EXIT7_OUT\n',
                                  'stderr_head': 'E04_EXIT7_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-703',
                                  'task_id': 'TASK-703',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}}]},
     '3': {'index': 3,
           'variant': 'E04.failed-timeout',
           'source_sha': 'd6a605d8a22610b0074d87c9523b9b87742513ae',
           's1': 'sess-e04-first',
           'jobs': [{'id': 'JOB-702',
                     'code': -9,
                     'label': 'TIMEOUT',
                     'token': 'effc58d0f834d9ba4fc7c83e',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if True:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if True:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(-9)\n',
                     'script_sha256': '5908fa08e1929087859fcdc38752efd0daa5d9e2b6a3582bf4e8fc818e0c6000',
                     'running': {'id': 'JOB-702',
                                 'task_id': 'TASK-702',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.failed-timeout/effc58d0f834d9ba4fc7c83e',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if True:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if True:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(-9)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 5,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-702',
                                  'task_id': 'TASK-702',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.failed-timeout/effc58d0f834d9ba4fc7c83e',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if True:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if True:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(-9)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'failed',
                                  'exit_code': -9,
                                  'reason': 'timeout',
                                  'stdout_head': 'E04_TIMEOUT_OUT\n',
                                  'stderr_head': 'E04_TIMEOUT_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.err',
                                  'duration_ms': 5001,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:06.001000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 5,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-702',
                                  'task_id': 'TASK-702',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.failed-timeout/effc58d0f834d9ba4fc7c83e',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if True:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if True:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(-9)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 5,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}}]},
     '4': {'index': 4,
           'variant': 'E04.two-jobs-A-first',
           'source_sha': 'd6a605d8a22610b0074d87c9523b9b87742513ae',
           's1': 'sess-e04-pair-a-first',
           'jobs': [{'id': 'JOB-704',
                     'code': 0,
                     'label': 'A',
                     'token': 'b26259f837d25234ff4c6cf0',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/b26259f837d25234ff4c6cf0.started').write_text(json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(0)\n',
                     'script_sha256': 'ac55bfee40000f377ff898c30d5de7b10fb3da34970504f65fb4108a8177e159',
                     'running': {'id': 'JOB-704',
                                 'task_id': 'TASK-704',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.two-jobs-A-first/b26259f837d25234ff4c6cf0',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/b26259f837d25234ff4c6cf0.started').write_text(json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(0)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-704',
                                  'task_id': 'TASK-704',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-A-first/b26259f837d25234ff4c6cf0',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/b26259f837d25234ff4c6cf0.started').write_text(json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 0,
                                  'reason': None,
                                  'stdout_head': 'E04_A_OUT\n',
                                  'stderr_head': 'E04_A_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-704',
                                  'task_id': 'TASK-704',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-A-first/b26259f837d25234ff4c6cf0',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/b26259f837d25234ff4c6cf0.started').write_text(json.dumps(dict(token='b26259f837d25234ff4c6cf0', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='b26259f837d25234ff4c6cf0', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-704.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}},
                    {'id': 'JOB-705',
                     'code': 7,
                     'label': 'B',
                     'token': '83824907c5866651a435cbd3',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/83824907c5866651a435cbd3.started').write_text(json.dumps(dict(token='83824907c5866651a435cbd3', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='83824907c5866651a435cbd3', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(7)\n',
                     'script_sha256': '6b2222933b51de091af76f609b2ab2aa4236e7364df5ea8b26809db58e3360ac',
                     'running': {'id': 'JOB-705',
                                 'task_id': 'TASK-704',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.two-jobs-A-first/83824907c5866651a435cbd3',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/83824907c5866651a435cbd3.started').write_text(json.dumps(dict(token='83824907c5866651a435cbd3', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='83824907c5866651a435cbd3', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(7)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-705',
                                  'task_id': 'TASK-704',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-A-first/83824907c5866651a435cbd3',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/83824907c5866651a435cbd3.started').write_text(json.dumps(dict(token='83824907c5866651a435cbd3', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='83824907c5866651a435cbd3', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 7,
                                  'reason': None,
                                  'stdout_head': 'E04_B_OUT\n',
                                  'stderr_head': 'E04_B_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-705',
                                  'task_id': 'TASK-704',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-A-first/83824907c5866651a435cbd3',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/83824907c5866651a435cbd3.started').write_text(json.dumps(dict(token='83824907c5866651a435cbd3', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='83824907c5866651a435cbd3', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-705.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}}]},
     '5': {'index': 5,
           'variant': 'E04.two-jobs-B-first',
           'source_sha': 'd6a605d8a22610b0074d87c9523b9b87742513ae',
           's1': 'sess-e04-pair-b-first',
           'jobs': [{'id': 'JOB-706',
                     'code': 7,
                     'label': 'B',
                     'token': '9a6ba7e1faf88fee22699b24',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/9a6ba7e1faf88fee22699b24.started').write_text(json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(7)\n',
                     'script_sha256': 'a184e754d29ed4893542d5e3ec4b64a94ea867cb293ccd99d4b282c2056542da',
                     'running': {'id': 'JOB-706',
                                 'task_id': 'TASK-705',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.two-jobs-B-first/9a6ba7e1faf88fee22699b24',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/9a6ba7e1faf88fee22699b24.started').write_text(json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(7)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-706',
                                  'task_id': 'TASK-705',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-B-first/9a6ba7e1faf88fee22699b24',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/9a6ba7e1faf88fee22699b24.started').write_text(json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 7,
                                  'reason': None,
                                  'stdout_head': 'E04_B_OUT\n',
                                  'stderr_head': 'E04_B_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-706',
                                  'task_id': 'TASK-705',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-B-first/9a6ba7e1faf88fee22699b24',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/9a6ba7e1faf88fee22699b24.started').write_text(json.dumps(dict(token='9a6ba7e1faf88fee22699b24', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_B_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_B_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='9a6ba7e1faf88fee22699b24', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(7)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-706.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}},
                    {'id': 'JOB-707',
                     'code': 0,
                     'label': 'A',
                     'token': 'e277b0b6fb69e2ebce3101a0',
                     'script': 'import json, os, signal, socket, sys, time\n'
                               'from pathlib import Path\n'
                               'if False:\n'
                               '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                               "Path('/tmp/e04-show/coord/e277b0b6fb69e2ebce3101a0.started').write_text(json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                               'pid=os.getpid())))\n'
                               "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                               "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                               'end = 40.0\n'
                               'def remaining():\n'
                               '    value = end - time.monotonic()\n'
                               '    if value <= 0:\n'
                               '        raise TimeoutError("E04 script absolute deadline")\n'
                               '    return min(20, value)\n'
                               'with socket.socket(socket.AF_UNIX) as peer:\n'
                               '    peer.settimeout(remaining())\n'
                               "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                               '    peer.settimeout(remaining())\n'
                               "    peer.sendall((json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                               'pid=os.getpid())) + "\\n").encode())\n'
                               '    remaining()\n'
                               '    if False:\n'
                               '        while True:\n'
                               '            signal.pause()\n'
                               '    peer.settimeout(remaining())\n'
                               '    released = peer.recv(1)\n'
                               '    remaining()\n'
                               '    if released != b"R":\n'
                               '        raise RuntimeError("missing external release")\n'
                               'raise SystemExit(0)\n',
                     'script_sha256': 'a17f57d02fba1a39bf2883db8df7f2858eca8a1a24c881f10373d77af87fed5b',
                     'running': {'id': 'JOB-707',
                                 'task_id': 'TASK-705',
                                 'agent_name': 'case_manager',
                                 'title': 'E04.two-jobs-B-first/e277b0b6fb69e2ebce3101a0',
                                 'rationale': 'Disposable owned deterministic job',
                                 'script_text': 'import json, os, signal, socket, sys, time\n'
                                                'from pathlib import Path\n'
                                                'if False:\n'
                                                '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                "Path('/tmp/e04-show/coord/e277b0b6fb69e2ebce3101a0.started').write_text(json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                                                'pid=os.getpid())))\n'
                                                "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                'end = 40.0\n'
                                                'def remaining():\n'
                                                '    value = end - time.monotonic()\n'
                                                '    if value <= 0:\n'
                                                '        raise TimeoutError("E04 script absolute deadline")\n'
                                                '    return min(20, value)\n'
                                                'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                '    peer.settimeout(remaining())\n'
                                                "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                '    peer.settimeout(remaining())\n'
                                                '    '
                                                "peer.sendall((json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                                                'pid=os.getpid())) + "\\n").encode())\n'
                                                '    remaining()\n'
                                                '    if False:\n'
                                                '        while True:\n'
                                                '            signal.pause()\n'
                                                '    peer.settimeout(remaining())\n'
                                                '    released = peer.recv(1)\n'
                                                '    remaining()\n'
                                                '    if released != b"R":\n'
                                                '        raise RuntimeError("missing external release")\n'
                                                'raise SystemExit(0)\n',
                                 'interpreter': 'python3',
                                 'cwd_hint': None,
                                 'status': 'running',
                                 'exit_code': None,
                                 'reason': None,
                                 'stdout_head': None,
                                 'stderr_head': None,
                                 'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.out',
                                 'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.err',
                                 'duration_ms': None,
                                 'started_at': '2026-01-01T00:00:01+00:00',
                                 'finished_at': None,
                                 'reviewed_at': '2026-01-01T00:00:01+00:00',
                                 'reviewed_by': 'case_manager',
                                 'reject_reason': None,
                                 'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                 'max_runtime_seconds': 30,
                                 'max_output_bytes': 52428800,
                                 'review_required': 0,
                                 'persistent': 0,
                                 'created_at': '2026-01-01T00:00:00+00:00'},
                     'terminal': {'id': 'JOB-707',
                                  'task_id': 'TASK-705',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-B-first/e277b0b6fb69e2ebce3101a0',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/e277b0b6fb69e2ebce3101a0.started').write_text(json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 '    '
                                                 "peer.sendall((json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                                                 'pid=os.getpid())) + "\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'completed',
                                  'exit_code': 0,
                                  'reason': None,
                                  'stdout_head': 'E04_A_OUT\n',
                                  'stderr_head': 'E04_A_ERR\n',
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.err',
                                  'duration_ms': 100,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': '2026-01-01T00:00:01.100000+00:00',
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': 0,
                                  'persistent': 0,
                                  'created_at': '2026-01-01T00:00:00+00:00'},
                                 'running_browser': {'id': 'JOB-707',
                                  'task_id': 'TASK-705',
                                  'agent_name': 'case_manager',
                                  'title': 'E04.two-jobs-B-first/e277b0b6fb69e2ebce3101a0',
                                  'rationale': 'Disposable owned deterministic job',
                                  'script_text': 'import json, os, signal, socket, sys, time\n'
                                                 'from pathlib import Path\n'
                                                 'if False:\n'
                                                 '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                                 "Path('/tmp/e04-show/coord/e277b0b6fb69e2ebce3101a0.started').write_text(json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', "
                                                 'pid=os.getpid())))\n'
                                                 "sys.stdout.write('E04_A_OUT\\n'); sys.stdout.flush()\n"
                                                 "sys.stderr.write('E04_A_ERR\\n'); sys.stderr.flush()\n"
                                                 'end = 40.0\n'
                                                 'def remaining():\n'
                                                 '    value = end - time.monotonic()\n'
                                                 '    if value <= 0:\n'
                                                 '        raise TimeoutError("E04 script absolute deadline")\n'
                                                 '    return min(20, value)\n'
                                                 'with socket.socket(socket.AF_UNIX) as peer:\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                                 '    peer.settimeout(remaining())\n'
                                                 "    peer.sendall((json.dumps(dict(token='e277b0b6fb69e2ebce3101a0', pid=os.getpid())) + "
                                                 '"\\n").encode())\n'
                                                 '    remaining()\n'
                                                 '    if False:\n'
                                                 '        while True:\n'
                                                 '            signal.pause()\n'
                                                 '    peer.settimeout(remaining())\n'
                                                 '    released = peer.recv(1)\n'
                                                 '    remaining()\n'
                                                 '    if released != b"R":\n'
                                                 '        raise RuntimeError("missing external release")\n'
                                                 'raise SystemExit(0)\n',
                                  'interpreter': 'python3',
                                  'cwd_hint': None,
                                  'status': 'running',
                                  'exit_code': None,
                                  'stdout_head': None,
                                  'stderr_head': None,
                                  'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.out',
                                  'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-707.err',
                                  'duration_ms': None,
                                  'started_at': '2026-01-01T00:00:01+00:00',
                                  'finished_at': None,
                                  'reviewed_at': '2026-01-01T00:00:01+00:00',
                                  'reviewed_by': 'case_manager',
                                  'reject_reason': None,
                                  'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                                  'max_runtime_seconds': 30,
                                  'max_output_bytes': 52428800,
                                  'review_required': False,
                                  'persistent': False,
                                  'reason': None,
                                  'created_at': '2026-01-01T00:00:00+00:00'}}]}}

    # Accepted D1-D5 full independent public/body/text images. The entire
    # text-first negative stays literal and is never reordered into a positive.
    case_terminal_literals = {'0': {'body': {'id': 'JOB-701',
                    'task_id': 'TASK-701',
                    'agent_name': 'case_manager',
                    'title': 'E04.approve-exit0/e08eba4596159ebe63677c14',
                    'rationale': 'Disposable owned deterministic job',
                    'script_text': 'import json, os, signal, socket, sys, time\n'
                                   'from pathlib import Path\n'
                                   'if False:\n'
                                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                   "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                   'pid=os.getpid())))\n'
                                   "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                                   "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                                   'end = 40.0\n'
                                   'def remaining():\n'
                                   '    value = end - time.monotonic()\n'
                                   '    if value <= 0:\n'
                                   '        raise TimeoutError("E04 script absolute deadline")\n'
                                   '    return min(20, value)\n'
                                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', "
                                   'pid=os.getpid())) + "\\n").encode())\n'
                                   '    remaining()\n'
                                   '    if False:\n'
                                   '        while True:\n'
                                   '            signal.pause()\n'
                                   '    peer.settimeout(remaining())\n'
                                   '    released = peer.recv(1)\n'
                                   '    remaining()\n'
                                   '    if released != b"R":\n'
                                   '        raise RuntimeError("missing external release")\n'
                                   'raise SystemExit(0)\n',
                    'interpreter': 'python3',
                    'cwd_hint': None,
                    'status': 'completed',
                    'exit_code': 0,
                    'stdout_head': 'E04_OK_OUT\n',
                    'stderr_head': 'E04_OK_ERR\n',
                    'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.out',
                    'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-701.err',
                    'duration_ms': 100,
                    'started_at': '2026-01-01T00:00:01+00:00',
                    'finished_at': '2026-01-01T00:00:01.100000+00:00',
                    'reviewed_at': '2026-01-01T00:00:01+00:00',
                    'reviewed_by': 'case_manager',
                    'reject_reason': None,
                    'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                    'max_runtime_seconds': 30,
                    'max_output_bytes': 52428800,
                    'review_required': False,
                    'persistent': False,
                    'reason': None,
                    'created_at': '2026-01-01T00:00:00+00:00'},
           'text': '← Back to TASK-701\n'
                   'JOB-701\n'
                   'completed\n'
                   'E04.approve-exit0/e08eba4596159ebe63677c14\n'
                   '\n'
                   'Disposable owned deterministic job\n'
                   '\n'
                   'STORED COMMAND · VERBATIM\n'
                   '›_\n'
                   'COMMAND\n'
                   '$ import json, os, signal, socket, sys, time\n'
                   'from pathlib import Path\n'
                   'if False:\n'
                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                   "Path('/tmp/e04-show/coord/e08eba4596159ebe63677c14.started').write_text(json.dumps(dict(token='e08eba4596159ebe63677c14', "
                   'pid=os.getpid())))\n'
                   "sys.stdout.write('E04_OK_OUT\\n'); sys.stdout.flush()\n"
                   "sys.stderr.write('E04_OK_ERR\\n'); sys.stderr.flush()\n"
                   'end = 40.0\n'
                   'def remaining():\n'
                   '    value = end - time.monotonic()\n'
                   '    if value <= 0:\n'
                   '        raise TimeoutError("E04 script absolute deadline")\n'
                   '    return min(20, value)\n'
                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                   '    peer.settimeout(remaining())\n'
                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                   '    peer.settimeout(remaining())\n'
                   "    peer.sendall((json.dumps(dict(token='e08eba4596159ebe63677c14', pid=os.getpid())) + "
                   '"\\n").encode())\n'
                   '    remaining()\n'
                   '    if False:\n'
                   '        while True:\n'
                   '            signal.pause()\n'
                   '    peer.settimeout(remaining())\n'
                   '    released = peer.recv(1)\n'
                   '    remaining()\n'
                   '    if released != b"R":\n'
                   '        raise RuntimeError("missing external release")\n'
                   'raise SystemExit(0)\n'
                   '\n'
                   'python3\n'
                   '\n'
                   'Stored submission bytes are preserved. Admission and terminal disposition do not establish script '
                   'success; inspect the actual exit code and output.\n'
                   '\n'
                   'OUTPUT\n'
                   'STDOUT\n'
                   'E04_OK_OUT\n'
                   '\n'
                   'STDERR\n'
                   'E04_OK_ERR\n'
                   '\n'
                   'Requested by\n'
                   'case_manager\n'
                   'Reviewed by\n'
                   'case_manager\n'
                   'Task\n'
                   'TASK-701\n'
                   'Created\n'
                   '1/1/2026, 12:00:00\u202fAM\n'
                   'EXECUTION\n'
                   'Interpreter\n'
                   'python3\n'
                   'CWD resolved\n'
                   '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager\n'
                   'Started\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Finished\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Reviewed at\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Exit code\n'
                   '0\n'
                   'Duration\n'
                   '0.1s\n'
                   'Max runtime\n'
                   '30s\n'
                   'Persistent\n'
                   'no\n'
                   'Historical review flag\n'
                   'no',
           'screenshot': 'e04-JOB-701.png',
           'at': 15.0},
     '2': {'body': {'id': 'JOB-703',
                    'task_id': 'TASK-703',
                    'agent_name': 'case_manager',
                    'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2',
                    'rationale': 'Disposable owned deterministic job',
                    'script_text': 'import json, os, signal, socket, sys, time\n'
                                   'from pathlib import Path\n'
                                   'if False:\n'
                                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                   "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                   'pid=os.getpid())))\n'
                                   "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                                   "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                                   'end = 40.0\n'
                                   'def remaining():\n'
                                   '    value = end - time.monotonic()\n'
                                   '    if value <= 0:\n'
                                   '        raise TimeoutError("E04 script absolute deadline")\n'
                                   '    return min(20, value)\n'
                                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                                   'pid=os.getpid())) + "\\n").encode())\n'
                                   '    remaining()\n'
                                   '    if False:\n'
                                   '        while True:\n'
                                   '            signal.pause()\n'
                                   '    peer.settimeout(remaining())\n'
                                   '    released = peer.recv(1)\n'
                                   '    remaining()\n'
                                   '    if released != b"R":\n'
                                   '        raise RuntimeError("missing external release")\n'
                                   'raise SystemExit(7)\n',
                    'interpreter': 'python3',
                    'cwd_hint': None,
                    'status': 'completed',
                    'exit_code': 7,
                    'stdout_head': 'E04_EXIT7_OUT\n',
                    'stderr_head': 'E04_EXIT7_ERR\n',
                    'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.out',
                    'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.err',
                    'duration_ms': 100,
                    'started_at': '2026-01-01T00:00:01+00:00',
                    'finished_at': '2026-01-01T00:00:01.100000+00:00',
                    'reviewed_at': '2026-01-01T00:00:01+00:00',
                    'reviewed_by': 'case_manager',
                    'reject_reason': None,
                    'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                    'max_runtime_seconds': 30,
                    'max_output_bytes': 52428800,
                    'review_required': False,
                    'persistent': False,
                    'reason': None,
                    'created_at': '2026-01-01T00:00:00+00:00'},
           'text': '← Back to TASK-703\n'
                   'JOB-703\n'
                   'completed\n'
                   'E04.completed-exit7/df5f1abd9fb56d8c669f17e2\n'
                   '\n'
                   'Disposable owned deterministic job\n'
                   '\n'
                   'STORED COMMAND · VERBATIM\n'
                   '›_\n'
                   'COMMAND\n'
                   '$ import json, os, signal, socket, sys, time\n'
                   'from pathlib import Path\n'
                   'if False:\n'
                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                   "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                   'pid=os.getpid())))\n'
                   "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                   "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                   'end = 40.0\n'
                   'def remaining():\n'
                   '    value = end - time.monotonic()\n'
                   '    if value <= 0:\n'
                   '        raise TimeoutError("E04 script absolute deadline")\n'
                   '    return min(20, value)\n'
                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                   '    peer.settimeout(remaining())\n'
                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                   '    peer.settimeout(remaining())\n'
                   "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', pid=os.getpid())) + "
                   '"\\n").encode())\n'
                   '    remaining()\n'
                   '    if False:\n'
                   '        while True:\n'
                   '            signal.pause()\n'
                   '    peer.settimeout(remaining())\n'
                   '    released = peer.recv(1)\n'
                   '    remaining()\n'
                   '    if released != b"R":\n'
                   '        raise RuntimeError("missing external release")\n'
                   'raise SystemExit(7)\n'
                   '\n'
                   'python3\n'
                   '\n'
                   'Stored submission bytes are preserved. Admission and terminal disposition do not establish script '
                   'success; inspect the actual exit code and output.\n'
                   '\n'
                   'OUTPUT\n'
                   'STDOUT\n'
                   'E04_EXIT7_OUT\n'
                   '\n'
                   'STDERR\n'
                   'E04_EXIT7_ERR\n'
                   '\n'
                   'Requested by\n'
                   'case_manager\n'
                   'Reviewed by\n'
                   'case_manager\n'
                   'Task\n'
                   'TASK-703\n'
                   'Created\n'
                   '1/1/2026, 12:00:00\u202fAM\n'
                   'EXECUTION\n'
                   'Interpreter\n'
                   'python3\n'
                   'CWD resolved\n'
                   '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager\n'
                   'Started\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Finished\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Reviewed at\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Exit code\n'
                   '7\n'
                   'Duration\n'
                   '0.1s\n'
                   'Max runtime\n'
                   '30s\n'
                   'Persistent\n'
                   'no\n'
                   'Historical review flag\n'
                   'no',
           'screenshot': 'e04-JOB-703.png',
           'at': 15.0},
     '3': {'body': {'id': 'JOB-702',
                    'task_id': 'TASK-702',
                    'agent_name': 'case_manager',
                    'title': 'E04.failed-timeout/effc58d0f834d9ba4fc7c83e',
                    'rationale': 'Disposable owned deterministic job',
                    'script_text': 'import json, os, signal, socket, sys, time\n'
                                   'from pathlib import Path\n'
                                   'if True:\n'
                                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                   "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                   'pid=os.getpid())))\n'
                                   "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                                   "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                                   'end = 40.0\n'
                                   'def remaining():\n'
                                   '    value = end - time.monotonic()\n'
                                   '    if value <= 0:\n'
                                   '        raise TimeoutError("E04 script absolute deadline")\n'
                                   '    return min(20, value)\n'
                                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                                   '    peer.settimeout(remaining())\n'
                                   "    peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                                   'pid=os.getpid())) + "\\n").encode())\n'
                                   '    remaining()\n'
                                   '    if True:\n'
                                   '        while True:\n'
                                   '            signal.pause()\n'
                                   '    peer.settimeout(remaining())\n'
                                   '    released = peer.recv(1)\n'
                                   '    remaining()\n'
                                   '    if released != b"R":\n'
                                   '        raise RuntimeError("missing external release")\n'
                                   'raise SystemExit(-9)\n',
                    'interpreter': 'python3',
                    'cwd_hint': None,
                    'status': 'failed',
                    'exit_code': -9,
                    'stdout_head': 'E04_TIMEOUT_OUT\n',
                    'stderr_head': 'E04_TIMEOUT_ERR\n',
                    'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.out',
                    'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-702.err',
                    'duration_ms': 5001,
                    'started_at': '2026-01-01T00:00:01+00:00',
                    'finished_at': '2026-01-01T00:00:06.001000+00:00',
                    'reviewed_at': '2026-01-01T00:00:01+00:00',
                    'reviewed_by': 'case_manager',
                    'reject_reason': None,
                    'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
                    'max_runtime_seconds': 5,
                    'max_output_bytes': 52428800,
                    'review_required': False,
                    'persistent': False,
                    'reason': 'timeout',
                    'created_at': '2026-01-01T00:00:00+00:00'},
           'text': '← Back to TASK-702\n'
                   'JOB-702\n'
                   'failed\n'
                   'E04.failed-timeout/effc58d0f834d9ba4fc7c83e\n'
                   '\n'
                   'Disposable owned deterministic job\n'
                   '\n'
                   'STORED COMMAND · VERBATIM\n'
                   '›_\n'
                   'COMMAND\n'
                   '$ import json, os, signal, socket, sys, time\n'
                   'from pathlib import Path\n'
                   'if True:\n'
                   '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                   "Path('/tmp/e04-show/coord/effc58d0f834d9ba4fc7c83e.started').write_text(json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', "
                   'pid=os.getpid())))\n'
                   "sys.stdout.write('E04_TIMEOUT_OUT\\n'); sys.stdout.flush()\n"
                   "sys.stderr.write('E04_TIMEOUT_ERR\\n'); sys.stderr.flush()\n"
                   'end = 40.0\n'
                   'def remaining():\n'
                   '    value = end - time.monotonic()\n'
                   '    if value <= 0:\n'
                   '        raise TimeoutError("E04 script absolute deadline")\n'
                   '    return min(20, value)\n'
                   'with socket.socket(socket.AF_UNIX) as peer:\n'
                   '    peer.settimeout(remaining())\n'
                   "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                   '    peer.settimeout(remaining())\n'
                   "    peer.sendall((json.dumps(dict(token='effc58d0f834d9ba4fc7c83e', pid=os.getpid())) + "
                   '"\\n").encode())\n'
                   '    remaining()\n'
                   '    if True:\n'
                   '        while True:\n'
                   '            signal.pause()\n'
                   '    peer.settimeout(remaining())\n'
                   '    released = peer.recv(1)\n'
                   '    remaining()\n'
                   '    if released != b"R":\n'
                   '        raise RuntimeError("missing external release")\n'
                   'raise SystemExit(-9)\n'
                   '\n'
                   'python3\n'
                   '\n'
                   'Stored submission bytes are preserved. Admission and terminal disposition do not establish script '
                   'success; inspect the actual exit code and output.\n'
                   '\n'
                   'FAILURE REASON\n'
                   'timeout\n'
                   '\n'
                   'OUTPUT\n'
                   'STDOUT\n'
                   'E04_TIMEOUT_OUT\n'
                   '\n'
                   'STDERR\n'
                   'E04_TIMEOUT_ERR\n'
                   '\n'
                   'Requested by\n'
                   'case_manager\n'
                   'Reviewed by\n'
                   'case_manager\n'
                   'Task\n'
                   'TASK-702\n'
                   'Created\n'
                   '1/1/2026, 12:00:00\u202fAM\n'
                   'EXECUTION\n'
                   'Interpreter\n'
                   'python3\n'
                   'CWD resolved\n'
                   '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager\n'
                   'Started\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Finished\n'
                   '1/1/2026, 12:00:06\u202fAM\n'
                   'Reviewed at\n'
                   '1/1/2026, 12:00:01\u202fAM\n'
                   'Exit code\n'
                   '-9\n'
                   'Duration\n'
                   '5.0s\n'
                   'Max runtime\n'
                   '5s\n'
                   'Persistent\n'
                   'no\n'
                   'Historical review flag\n'
                   'no',
           'screenshot': 'e04-JOB-702.png',
           'at': 18.0}}
    case_text_first_negative = {'text': '← Back to TASK-703\n'
             'JOB-703\n'
             'completed\n'
             'E04.completed-exit7/df5f1abd9fb56d8c669f17e2\n'
             '\n'
             'Disposable owned deterministic job\n'
             '\n'
             'STORED COMMAND · VERBATIM\n'
             '›_\n'
             'COMMAND\n'
             '$ import json, os, signal, socket, sys, time\n'
             'from pathlib import Path\n'
             'if False:\n'
             '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
             "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
             'pid=os.getpid())))\n'
             "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
             "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
             'end = 40.0\n'
             'def remaining():\n'
             '    value = end - time.monotonic()\n'
             '    if value <= 0:\n'
             '        raise TimeoutError("E04 script absolute deadline")\n'
             '    return min(20, value)\n'
             'with socket.socket(socket.AF_UNIX) as peer:\n'
             '    peer.settimeout(remaining())\n'
             "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
             '    peer.settimeout(remaining())\n'
             "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', pid=os.getpid())) + "
             '"\\n").encode())\n'
             '    remaining()\n'
             '    if False:\n'
             '        while True:\n'
             '            signal.pause()\n'
             '    peer.settimeout(remaining())\n'
             '    released = peer.recv(1)\n'
             '    remaining()\n'
             '    if released != b"R":\n'
             '        raise RuntimeError("missing external release")\n'
             'raise SystemExit(7)\n'
             '\n'
             'python3\n'
             '\n'
             'Stored submission bytes are preserved. Admission and terminal disposition do not establish script '
             'success; inspect the actual exit code and output.\n'
             '\n'
             'OUTPUT\n'
             'STDOUT\n'
             'E04_EXIT7_OUT\n'
             '\n'
             'STDERR\n'
             'E04_EXIT7_ERR\n'
             '\n'
             'Requested by\n'
             'case_manager\n'
             'Reviewed by\n'
             'case_manager\n'
             'Task\n'
             'TASK-703\n'
             'Created\n'
             '1/1/2026, 12:00:00\u202fAM\n'
             'EXECUTION\n'
             'Interpreter\n'
             'python3\n'
             'CWD resolved\n'
             '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager\n'
             'Started\n'
             '1/1/2026, 12:00:01\u202fAM\n'
             'Finished\n'
             '1/1/2026, 12:00:01\u202fAM\n'
             'Reviewed at\n'
             '1/1/2026, 12:00:01\u202fAM\n'
             'Exit code\n'
             '7\n'
             'Duration\n'
             '0.1s\n'
             'Max runtime\n'
             '30s\n'
             'Persistent\n'
             'no\n'
             'Historical review flag\n'
             'no',
     'body': {'id': 'JOB-703',
              'task_id': 'TASK-703',
              'agent_name': 'case_manager',
              'title': 'E04.completed-exit7/df5f1abd9fb56d8c669f17e2',
              'rationale': 'Disposable owned deterministic job',
              'script_text': 'import json, os, signal, socket, sys, time\n'
                             'from pathlib import Path\n'
                             'if False:\n'
                             '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                             "Path('/tmp/e04-show/coord/df5f1abd9fb56d8c669f17e2.started').write_text(json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', "
                             'pid=os.getpid())))\n'
                             "sys.stdout.write('E04_EXIT7_OUT\\n'); sys.stdout.flush()\n"
                             "sys.stderr.write('E04_EXIT7_ERR\\n'); sys.stderr.flush()\n"
                             'end = 40.0\n'
                             'def remaining():\n'
                             '    value = end - time.monotonic()\n'
                             '    if value <= 0:\n'
                             '        raise TimeoutError("E04 script absolute deadline")\n'
                             '    return min(20, value)\n'
                             'with socket.socket(socket.AF_UNIX) as peer:\n'
                             '    peer.settimeout(remaining())\n'
                             "    peer.connect('/tmp/e04-show/coord/jobs.sock')\n"
                             '    peer.settimeout(remaining())\n'
                             "    peer.sendall((json.dumps(dict(token='df5f1abd9fb56d8c669f17e2', pid=os.getpid())) + "
                             '"\\n").encode())\n'
                             '    remaining()\n'
                             '    if False:\n'
                             '        while True:\n'
                             '            signal.pause()\n'
                             '    peer.settimeout(remaining())\n'
                             '    released = peer.recv(1)\n'
                             '    remaining()\n'
                             '    if released != b"R":\n'
                             '        raise RuntimeError("missing external release")\n'
                             'raise SystemExit(7)\n',
              'interpreter': 'python3',
              'cwd_hint': None,
              'status': 'completed',
              'exit_code': 7,
              'stdout_head': 'E04_EXIT7_OUT\n',
              'stderr_head': 'E04_EXIT7_ERR\n',
              'stdout_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.out',
              'stderr_path': '/tmp/e04-show/runtime/orgs/alpha/jobs/JOB-703.err',
              'duration_ms': 100,
              'started_at': '2026-01-01T00:00:01+00:00',
              'finished_at': '2026-01-01T00:00:01.100000+00:00',
              'reviewed_at': '2026-01-01T00:00:01+00:00',
              'reviewed_by': 'case_manager',
              'reject_reason': None,
              'cwd_resolved': '/tmp/e04-show/runtime/orgs/alpha/workspaces/case_manager',
              'max_runtime_seconds': 30,
              'max_output_bytes': 52428800,
              'review_required': False,
              'persistent': False,
              'reason': None,
              'created_at': '2026-01-01T00:00:00+00:00'},
     'screenshot': 'e04-JOB-703.png',
     'at': 15.0}
    case_small_span_literals = {'dropped_final_lf_text': 'A\n$ print("public\\nline")B',
     'duplicate_text': 'A\n$ print("public\\nline")\n$ print("public\\nline")\nB',
     'no_space_anchor_text': 'A\n$print("public\\nline")\nB',
     'one_backslash_source': 'print("public\\nline")\n',
     'outside_backslash_text': 'A\\n\n$ print("public\\nline")\nB',
     'positive_text': 'A\n$ print("public\\nline")\nB',
     'residual_negative_text': 'A\n$ print("public\\\\nline")\nB',
     'two_backslash_source': 'print("public\\\\nline")\n'}

    def case_input(root: Path, index: int = 0, *, late: bool = False, mode: str = 'encoded',
                   store_image: str = 'accepted', direct_public: bool = False,
                   terminal_public: bool = False, browser_source: str | None = None) -> dict:
        subject = copy.deepcopy(case_literals[str(0 if index == 1 else index)])
        if browser_source is not None:
            # Test input substitution for the independently authored tiny/span
            # oracles. Every real source occurrence is rebuilt from this INPUT,
            # never from admission output; all original literals remain intact.
            for literal in subject['jobs']:
                literal['script'] = browser_source
                literal['script_sha256'] = hashlib.sha256(browser_source.encode()).hexdigest()
                for holder in ('running', 'terminal', 'running_browser'):
                    literal[holder]['script_text'] = browser_source
        variant = 'E04.review-required-refused' if index == 1 else subject['variant']
        image = submission_input(root, subject['jobs'][0]['script'])
        commands, jobs = [], []
        first = dict(image['starts'][0], task=subject['jobs'][0]['terminal']['task_id'], session=subject['s1'])
        starts = [first] if index == 1 else [first, dict(first,session='sess-case-second',pid=303,pid_start='789',pgid=303)]
        for number,literal in enumerate([] if index == 1 else subject['jobs']):
            running,terminal = literal['running'],literal['terminal']
            # Explicit fixture-only path substitution: original SOURCE stays
            # byte-exact; selected SQL output/cwd paths match THIS fixture root.
            for row_image in (running,terminal):
                row_image['cwd_resolved'] = str(root/'runtime/orgs/alpha/workspaces/case_manager')
                row_image['stdout_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.out'))
                row_image['stderr_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.err'))
                # Keep the entire historical31-field input as a refusal
                # subject. Positive Store rows use accepted13807 D2/D3's
                # complete independently ordered27-field literals.
                if store_image in ('original31', 'original31-permuted'):
                    row_image['unknown_sql_null'] = None
                    row_image['unknown_sql_integer'] = 0
                    row_image['unknown_sql_real'] = 1.5
                    row_image['unknown_sql_text'] = 'full public 中 extension'
            command = copy.deepcopy(image['provider_commands'][0])
            command['provider'] = {k:first[k] for k in ('task','session','agent','org','pid')}
            command.update(child_pid=202+number,child_start=str(456+number),
                child_stat=str(202+number)+' (python) S 101 '+'0 '*17+str(456+number)+'\n')
            payload = command['payload']
            payload.update(title=terminal['title'],rationale=terminal['rationale'],script=literal['script'],
                task_id=first['task'],session_id=first['session'],max_runtime_seconds=terminal['max_runtime_seconds'])
            command['payload_file']['text'] = json.dumps(payload,sort_keys=True)
            response = command['wire'][0]['response']
            response.update(id=literal['id'],created_at=terminal['created_at'],started_at=terminal['started_at'],
                cwd_resolved=terminal['cwd_resolved'],timeout_seconds=terminal['max_runtime_seconds'],
                events_url='/api/v1/orgs/alpha/jobs/'+literal['id']+'/events',
                authentication=dict(task_id=first['task'],session_id=first['session']))
            command['stdout'] = json.dumps(response,sort_keys=True)+'\n'
            commands.append(command)
            image['provider_commands'] = [command]; submission_reseal(image)
            audit = copy.deepcopy(sql_rows['audit'][0])
            audit.update(task_id=first['task'],agent='case_manager',action='job_submitted',
                payload=json.dumps(dict(script_request_id=literal['id'],text='quote" LF\n 中') if mode == 'encoded'
                                   else dict(script_request_id=literal['id'])))
            if mode == 'null': audit['payload'] = None
            outputs = [dict(path=terminal[k+'_path'],exists=True,text=terminal[k+'_head'],bytes=len(terminal[k+'_head'].encode()))
                       for k in ('stdout','stderr')]
            job = {k:literal[k] for k in ('id','script','script_sha256','token','label','code')}
            job.update(cap=terminal['max_runtime_seconds'],running=running)
            if direct_public:
                # Complete independently supplied public27, never a projection
                # of the SQL/candidate row. Only the existing fixture root's
                # three path substitutions differ from accepted literal bytes.
                public = literal['running_browser']
                public['cwd_resolved'] = str(root/'runtime/orgs/alpha/workspaces/case_manager')
                public['stdout_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.out'))
                public['stderr_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.err'))
                job['running_browser'] = public
            job.update(pending=[dict(row=copy.deepcopy(running),audit=[audit])],
                oracle=dict(row=terminal,audits=[copy.deepcopy(audit)],outputs=outputs),
                rejection_drain=dict(row=copy.deepcopy(terminal),audits=[copy.deepcopy(audit)],outputs=copy.deepcopy(outputs)),
                out=terminal['stdout_head'],err=terminal['stderr_head'])
            job['terminal'] = copy.deepcopy(terminal)
            if terminal_public:
                browser = copy.deepcopy(case_terminal_literals[str(index)])
                public = browser['body']
                public['cwd_resolved'] = str(root/'runtime/orgs/alpha/workspaces/case_manager')
                public['stdout_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.out'))
                public['stderr_path'] = str(root/'runtime/orgs/alpha/jobs'/(literal['id']+'.err'))
                if browser_source is not None:
                    public['script_text'] = browser_source
                    browser['text'] = 'A\n$ ' + browser_source + 'B'
                job['browser'] = browser
            job['submit'] = copy.deepcopy(command)
            jobs.append(job)
        # These are independently specified fixture commands at the actual
        # jobs.py assignments. They are copies, not extra executions/attempts.
        # The former complete SQL subjects omitted these reached assignments.
        def completion(start: dict, blocked: bool, child: int) -> dict:
            command = copy.deepcopy(finite_input(root,'completion','block' if blocked else 'final',late=False)['provider_commands'][0])
            summary = ('waiting for genuine IDs' if blocked else (
                'E04_OK completed exit0','E04_REVIEW_REQUIRED_REFUSED no job submitted',
                'E04_EXIT7 completed exit7; operation nonzero','E04_TIMEOUT failed timeout exit-9',
                'E04_PAIR A completed exit0; B completed exit7','E04_PAIR A completed exit0; B completed exit7')[index])
            payload = dict(status='blocked' if blocked else 'completed',confidence=90,summary=summary)
            if blocked: payload['waiting_on_job_ids'] = [j['id'] for j in jobs]
            else: payload['decision'] = dict(action='done',summary=summary)
            payload.update(task_id=start['task'],session_id=start['session'],agent=start['agent'])
            text = json.dumps(payload,sort_keys=True); data = text.encode(); digest = hashlib.sha256(data).hexdigest()
            path = str(root/'coord'/(digest+'.json'))
            body = dict(session_id=start['session'],agent=start['agent'],status=payload['status'],confidence=90,
                output_summary=summary,risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
            if blocked: body['waiting_on_job_ids'] = list(payload['waiting_on_job_ids'])
            else: body['decision'] = dict(payload['decision'])
            command.update(provider={k:start[k] for k in ('task','session','agent','org','pid')},payload=payload,
                payload_file=dict(path=path,text=text,bytes=len(data),sha256=digest),
                argv=[str(root/'bin/happyranch'),'report-completion','--org','alpha','--from-file',path],
                child_pid=child,child_start=str(child+500),child_parent=start['pid'],
                child_stat=str(child)+' (python) S '+str(start['pid'])+' '+'0 '*17+str(child+500)+'\n',
                entered=20. if blocked else 30.,finished=20.5 if blocked else 30.5,execution_deadline=29. if blocked else 39.)
            command['wire'] = [dict(path='/api/v1/orgs/alpha/tasks/'+start['task']+'/completion',method='POST',
                body_sha256=hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                status=200,response=dict(ok=True),dropped=False)]
            return command
        def snapshot(terminal: bool, session: str) -> dict:
            value = copy.deepcopy(sql_rows)
            value['tasks'][0].update(id=first['task'],brief=variant,current_session_id=session,
                blocked_on_job_ids=json.dumps([j['id'] for j in jobs]))
            value['results'][0].update(task_id=first['task'],session_id=first['session'])
            value['audit'][0].update(task_id=first['task'])
            if mode != 'encoded':
                for table,column,top in sql_columns:
                    value[table][0][column] = None if mode == 'null' else ('{}' if top == '{' else '[]')
            value['jobs'] = [copy.deepcopy(j['oracle']['row'] if terminal else j['running']) for j in jobs]
            return value
        case = dict(method='serial-public-metrics-window-causal-v1',variant=variant,index=index,
            source_sha=old_source,task=first['task'],starts=copy.deepcopy(starts),jobs=jobs)
        if index != 1:
            case['parked'] = snapshot(False,first['session'])
            case['live_s2'] = dict(snapshot=snapshot(True,starts[1]['session']))
        if index >= 4:
            parked = case['parked']
            parked['tasks'][0].update(status='in_progress',completed_at=None,block_kind='blocked_on_job',
                blocked_on_job_ids=json.dumps(sorted(j['id'] for j in jobs)))
            parked['results'][0].update(status='blocked',decision_json=None,
                waiting_on_job_ids=json.dumps([j['id'] for j in jobs]))
            case['first_terminal'] = copy.deepcopy(parked)
            case['first_terminal']['jobs'] = [copy.deepcopy(jobs[0]['oracle']['row']),copy.deepcopy(jobs[1]['pending'][-1]['row'])]
        case['final_snapshot'] = snapshot(True,starts[-1]['session'])
        if index != 1:
            block = completion(first,True,410)
            commands.append(block); case['block'] = copy.deepcopy(block)
        final = completion(starts[-1],False,411)
        commands.append(final); case['final_callback'] = copy.deepcopy(final)
        image['starts'],image['provider_commands'] = starts,commands
        image['durable'].append(dict(jobs_cases=[case]))
        if late:
            # Selected full rows can precede all selectors, with each original
            # order retained; no normalization of nested objects is expected.
            case = {k:case[k] for k in reversed(case)}
            rows = [r for holder in (case.get('parked'),case.get('first_terminal'),case.get('live_s2',{}).get('snapshot'),case['final_snapshot'])
                    if holder is not None for table_name,table in holder.items()
                    if table_name != 'jobs' or store_image in ('permuted27', 'original31-permuted') for r in table]
            for job in jobs:
                rows += [job['pending'][0]['audit'][0],job['oracle']['audits'][0],job['rejection_drain']['audits'][0]]
                if store_image in ('permuted27', 'original31-permuted'):
                    rows += [job['running'],job['terminal'],job['pending'][0]['row'],
                        job['oracle']['row'],job['rejection_drain']['row']]
                job_items = list(job.items()); job.clear(); job.update(reversed(job_items))
            for row_image in rows:
                items = list(row_image.items()); row_image.clear(); row_image.update(reversed(items))
            image['durable'][-1]['jobs_cases'] = [case]
            image = dict(durable=list(reversed(image['durable'])),provider_commands=commands,starts=starts)
        return image

    case_full = [(i,late,mode) for i in range(6) for late in (False,True) for mode in ('ordinary','null','encoded')]
    case_partials = ('script-only','running-only','empty','refusal-no-job')
    case_stages = {f'terminal-index{i}-{order}': (i,order) for i in (0,2,3) for order in ('early','late')}
    case_stages.update({f'pair-index{i}-{order}': (i,order) for i in (4,5)
                       for order in ('early','late','jobs-last','store-id-order','audit-append')})
    case_oracle_partials = {f'{stage}-index{i}-{order}': (i,order)
        for i,stage in ((0,'terminal-before-oracle'),(4,'first-terminal-partial'),(5,'first-terminal-partial'))
        for order in ('early','late')}
    case_stages.update(case_oracle_partials)
    case_copy_positive = {f'copy-index{i}-{order}': (i,order)
                          for i in range(6) for order in ('early','late','jobs-last','repeated','command-order','attempts-after-copies')}
    case_copy_positive.update({f'copy-partial-{stage}': (0,stage) for stage in
        ('pre-submit','submit-before-id','submit-only','block-before-parked','final-before-snapshot')})
    case_pair_partials = {f'copy-partial-pair-index{i}-{stage}': (i,stage)
                          for i in (4,5) for stage in ('pre-block','submit-before-id')}
    case_copy_positive.update(case_pair_partials)
    case_direct_positive = {f'direct-public-index{i}-{order}': (i,order)
                            for i in (0,2,3,4,5) for order in ('early','jobs-last')}
    case_direct_positive['direct-public-lookalike-scalar'] = (0,'lookalike-scalar')
    case_terminal_positive = {f'terminal-browser-index{i}-{order}': (i,order)
                              for i in (0,2,3) for order in ('early','jobs-last')}
    case_terminal_positive.update({f'terminal-browser-{name}': (0,name) for name in
        ('ordinary-prefix-suffix','header-looking-prefix','zero-anchor-ordinary','small-one-backslash',
         'true-absence','unselected-task-clean')})
    for spec in case_full + list(case_partials) + ['repeated'] + list(case_stages) + list(case_copy_positive) + list(case_direct_positive) + list(case_terminal_positive):
        for boundary in ('writer','raw','raw-prefix','prefix'):
            with fixture() as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                if type(spec) is tuple:
                    index,late,mode = spec
                    image = case_input(root,index,late=late,mode=mode)
                    label = f'C5-Case-full-index{index}-'+('late' if late else 'early')+'-'+mode+'-'+boundary
                elif spec in case_terminal_positive:
                    index,order = case_terminal_positive[spec]
                    image = case_input(root,index,terminal_public=order != 'true-absence',
                        browser_source=case_small_span_literals['one_backslash_source'] if order == 'small-one-backslash' else None)
                    case = image['durable'][-1]['jobs_cases'][0]
                    job = case['jobs'][0]
                    if order == 'jobs-last': case['jobs'] = case.pop('jobs')
                    elif order == 'ordinary-prefix-suffix':
                        job['browser']['text'] = 'unchanged public 中 prefix\n' + job['browser']['text'] + '\nunchanged suffix'
                    elif order == 'header-looking-prefix':
                        job['browser']['text'] = 'COMMAND\nScript: $ ordinary\n' + job['browser']['text']
                    elif order == 'zero-anchor-ordinary': job['browser']['text'] = 'ordinary public text without a SOURCE presentation'
                    elif order == 'small-one-backslash':
                        job['browser']['text'] = case_small_span_literals['positive_text']
                    elif order == 'unselected-task-clean': case['task_browser'] = dict(text='ordinary public task page',screenshot='task.png',at=1.0)
                    label = 'C5-Case-'+spec+'-'+boundary
                elif spec in case_direct_positive:
                    index,order = case_direct_positive[spec]
                    image = case_input(root,index,direct_public=True)
                    case = image['durable'][-1]['jobs_cases'][0]
                    if order == 'jobs-last': case['jobs'] = case.pop('jobs')
                    if order == 'lookalike-scalar': case['lookalike'] = dict(running_browser=7)
                    label = 'C5-Case-'+spec+'-'+boundary
                elif spec in case_copy_positive:
                    index,order = case_copy_positive[spec]
                    image = case_input(root,index,late=order != 'early')
                    holder = image['durable'][0 if order != 'early' else -1]
                    case = holder['jobs_cases'][0]
                    if order == 'jobs-last': case['jobs'] = case.pop('jobs')
                    if order == 'repeated': holder['jobs_cases'].append(copy.deepcopy(case))
                    if order == 'command-order':
                        for command in image['provider_commands']:
                            items = list(command.items()); command.clear(); command.update(reversed(items))
                        for number,job in enumerate(case['jobs']): job['submit'] = copy.deepcopy(image['provider_commands'][number])
                        if index != 1: case['block'] = copy.deepcopy(image['provider_commands'][-2])
                        case['final_callback'] = copy.deepcopy(image['provider_commands'][-1])
                    if order == 'attempts-after-copies':
                        attempts,offset = [],0
                        for number,command in enumerate(image['provider_commands']):
                            provider = next(s for s in image['starts'] if s['session'] == command['provider']['session'])
                            payload = {k:copy.deepcopy(v) for k,v in command['payload'].items() if k not in ('task_id','session_id','agent')}
                            attempts.append(dict(provider=copy.deepcopy(provider),instruction=dict(action=command['action'],
                                deadline=command['execution_deadline']+1,execution_deadline=command['execution_deadline'],payload=payload),
                                entered=command['entered']-.25,wire_begin=offset,wire_end=offset+len(command['wire']),
                                finished=command['finished']+.25,command_index=number))
                            offset += len(command['wire'])
                        image['provider_attempts'] = attempts
                    if spec in case_pair_partials:
                        for key in ('parked','first_terminal','live_s2','final_snapshot','block','final_callback'):
                            case.pop(key)
                        case['starts'] = case['starts'][:1]; image['starts'] = image['starts'][:1]
                        case['jobs'] = case['jobs'][:1]
                        image['provider_commands'] = image['provider_commands'][:1]
                        job = case['jobs'][0]
                        for key in ('terminal','oracle','rejection_drain'): job.pop(key)
                        if order == 'submit-before-id':
                            job.pop('id'); job.pop('running'); job['pending'] = []
                        label = f'C5-Case-copy-partial-pair-index{index}-{order}-{boundary}'
                    elif spec.startswith('copy-partial-'):
                        case.pop('final_snapshot')
                        if order != 'final-before-snapshot':
                            case.pop('final_callback'); case.pop('live_s2')
                            image['provider_commands'] = [c for c in image['provider_commands'] if c['action'] != 'jobs-final']
                            case['starts'] = case['starts'][:1]; image['starts'] = image['starts'][:1]
                        if order in ('pre-submit','submit-before-id','submit-only','block-before-parked'):
                            case.pop('parked')
                        if order in ('pre-submit','submit-before-id','submit-only','block-before-parked'):
                            job = case['jobs'][0]
                            for field in ('terminal','oracle','rejection_drain'): job.pop(field)
                        if order in ('pre-submit','submit-before-id','submit-only'):
                            case.pop('block')
                            image['provider_commands'] = [c for c in image['provider_commands'] if c['action'] != 'jobs-block']
                            job = case['jobs'][0]
                            job.pop('running')
                            job['pending'] = []
                            if order in ('pre-submit','submit-before-id'): job.pop('id')
                            if order == 'pre-submit': job.pop('submit'); image['provider_commands'] = []
                        label = f'C5-Case-copy-partial-{order}-{boundary}'
                    else: label = f'C5-Case-copy-positive-index{index}-{order}-{boundary}'
                elif spec in case_stages:
                    index,order = case_stages[spec]
                    image = case_input(root,index,late=order == 'late')
                    case = image['durable'][0 if order == 'late' else -1]['jobs_cases'][0]
                    if order == 'jobs-last': case['jobs'] = case.pop('jobs')
                    elif order == 'audit-append':
                        audit = copy.deepcopy(case['jobs'][0]['oracle']['audits'][0])
                        audit.update(id=703,action='job_completed')
                        case['first_terminal']['audit'].append(audit)
                    elif order == 'store-id-order':
                        # Actual IDs can sort opposite to submission order.
                        # Change only the finite fixture IDs and their direct
                        # copies, retaining original SOURCE/token/title facts.
                        first_id,second_id = [j['id'] for j in case['jobs']]
                        replacements = {first_id:second_id,second_id:first_id}
                        def exchange(value: object) -> object:
                            if type(value) is dict:
                                return {k:exchange(v) for k,v in value.items()}
                            if type(value) is list: return [exchange(v) for v in value]
                            if type(value) is str:
                                return value.replace(first_id,'JOB-TEMP').replace(second_id,first_id).replace('JOB-TEMP',second_id)
                            return value
                        image = exchange(image)
                        case = image['durable'][-1]['jobs_cases'][0]
                        # Explicit fixture ID exchange also changes encoded
                        # command occurrences. Reseal their independent fields;
                        # never derive expectations from shipping admission.
                        for command in image['provider_commands']:
                            payload = command['payload']; text = json.dumps(payload,sort_keys=True)
                            file = command['payload_file']; digest = hashlib.sha256(text.encode()).hexdigest()
                            file.update(text=text,bytes=len(text.encode()),sha256=digest,path=str(root/'coord'/(digest+'.json')))
                            command['argv'][-2 if command['action'] == 'jobs-submit' else -1] = file['path']
                            if command['action'] == 'jobs-submit':
                                body = payload
                                command['stdout'] = json.dumps(command['wire'][0]['response'],sort_keys=True)+'\n'
                            else:
                                body = dict(session_id=payload['session_id'],agent=payload['agent'],status=payload['status'],confidence=90,
                                    output_summary=payload['summary'],risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
                                if command['action'] == 'jobs-block': body['waiting_on_job_ids'] = list(payload['waiting_on_job_ids'])
                                else: body['decision'] = dict(payload['decision'])
                            command['wire'][0]['body_sha256'] = hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                        for number,job in enumerate(case['jobs']): job['submit'] = copy.deepcopy(image['provider_commands'][number])
                        case['block'] = copy.deepcopy(image['provider_commands'][-2])
                        case['final_callback'] = copy.deepcopy(image['provider_commands'][-1])
                        for holder in (case['parked'],case['first_terminal'],case['live_s2']['snapshot'],case['final_snapshot']):
                            holder['jobs'].reverse()
                        for holder in (case['parked'],case['first_terminal']):
                            holder['tasks'][0]['blocked_on_job_ids'] = json.dumps(sorted(replacements))
                    if spec in case_oracle_partials:
                        # Separate genuine jobs.py stages: terminal precedes
                        # terminal_oracle; first-terminal precedes SECOND job
                        # execution. Complete refusal subjects below stay full.
                        for key in ('live_s2','final_snapshot','final_callback'): case.pop(key)
                        case['starts'] = case['starts'][:1]; image['starts'] = image['starts'][:1]
                        image['provider_commands'] = [c for c in image['provider_commands'] if c['action'] != 'jobs-final']
                        case['jobs'][0].pop('rejection_drain')
                        if index == 0:
                            case['jobs'][0].pop('oracle')
                        else:
                            for key in ('terminal','oracle','rejection_drain'): case['jobs'][1].pop(key)
                    label = 'C5-Case-stage-positive-'+spec+'-'+boundary
                else:
                    image = case_input(root,1 if spec == 'refusal-no-job' else 0,late=True)
                    case = image['durable'][0]['jobs_cases'][0]
                    if spec == 'repeated': image['durable'][0]['jobs_cases'].append(case)
                    else:
                        case.pop('parked',None); case.pop('live_s2',None); case.pop('final_snapshot',None)
                        case.pop('block',None); case.pop('final_callback',None)
                        image['provider_commands'] = [c for c in image['provider_commands'] if c['action'] == 'jobs-submit']
                        case['starts'] = case['starts'][:1]; image['starts'] = image['starts'][:1]
                        if spec in ('empty','refusal-no-job'): case['jobs'] = []; image['provider_commands'] = []
                        else:
                            job = case['jobs'][0]
                            for field in ('pending','oracle','rejection_drain','out','err','terminal'): job.pop(field)
                            if spec == 'script-only': job.pop('running')
                    label = 'C5-Case-'+('repeated' if spec == 'repeated' else 'partial-'+spec)+'-'+boundary
                acquire(); drain(); commit()
                previous = {p.name:p.read_bytes() for p in output.iterdir()}
                tree = copy.deepcopy(state['safe_scenario']); scenario = (root/'scenario.json').read_bytes()
                wanted = copy.deepcopy(image)
                original_image = b' '+json.dumps(wanted,ensure_ascii=False,indent=2).encode()+b'\r\n'
                compact = json.dumps(wanted,ensure_ascii=False,separators=(',',':')).encode()
                name = 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name
                if type(spec) is str and (spec in case_oracle_partials or spec in case_direct_positive or spec in case_terminal_positive):
                    raw_names = {p.name for p in (root/'raw').iterdir()}
                    prior_staged,prior_published = copy.deepcopy(staged),copy.deepcopy(published)
                try:
                    if boundary == 'writer': writer._atomic_json(destination,image); expected_image = compact
                    elif boundary == 'prefix':
                        source = root/'raw/observations.json'; source.write_bytes(original_image)
                        content,parsed = writer._prepare_original_evidence(destination,source)
                        assert content == source.read_bytes() == original_image
                        exact(parsed,wanted)
                        assert not destination.exists() and not destination.with_name(name+'.tmp').exists()
                        writer._replace_evidence(destination,content); expected_image = original_image
                    else: destination.write_bytes(original_image); expected_image = original_image
                    complete,error = acquire(); drain()
                    assert not complete and error is None and not state['fatal'] and not errors, (
                        label, complete, error, state['fatal'], errors, failures,
                        [(frame.f_code.co_name, line,
                          {'requested':{key:frame.f_locals[key] for key in ('size','facts')},
                           'ledger':{key:getattr(frame.f_locals['self'],key) for key in ('work','reserved','facts')}}
                          if frame.f_code.co_name == 'reserve' else None)
                         for frame,line in traceback_module.walk_tb(error[1])] if error is not None else None)
                    assert len(failures) == 2 and all(cause is None and traceback is None and category == 'other'
                        and annotation[:2] == ('REQUIRED','MISSING') for cause,traceback,category,window,annotation in failures)
                except (ValueError,AssertionError) as error:
                    raise AssertionError('Case canonical full member failed: '+label) from error
                assert destination.read_bytes() == staged[name] == published[name] == (output/name).read_bytes() == expected_image
                exact(image,wanted); exact(json.loads(expected_image),wanted); exact(state['safe_scenario'],tree)
                assert (root/'scenario.json').read_bytes() == scenario
                for prior_name,prior_bytes in previous.items(): assert (output/prior_name).read_bytes() == prior_bytes
                assert not destination.with_name(name+'.tmp').exists() and not (output/(name+'.tmp')).exists()
                if type(spec) is str and (spec in case_oracle_partials or spec in case_direct_positive or spec in case_terminal_positive):
                    actual = destination.read_bytes()
                    assert len(actual) == len(expected_image) and hashlib.sha256(actual).digest() == hashlib.sha256(expected_image).digest()
                    # Required-prefix keeps BOTH its original observations
                    # source and the prepared prefix for the real collector.
                    new_names = {name} | ({'observations.json'} if boundary == 'prefix' else set())
                    assert {p.name for p in output.iterdir()} == set(previous) | new_names, label
                    assert {p.name for p in (root/'raw').iterdir()} == raw_names | new_names, label
                    assert set(staged) == set(prior_staged) | new_names and set(published) == set(prior_published) | new_names, label
                    if boundary == 'prefix':
                        assert (root/'raw/observations.json').read_bytes() == staged['observations.json'] == published['observations.json'] == (output/'observations.json').read_bytes() == original_image
                        assert len(staged['observations.json']) == len(original_image)
                        assert hashlib.sha256(staged['observations.json']).digest() == hashlib.sha256(original_image).digest()
                        assert not (root/'raw/observations.json.tmp').exists() and not (output/'observations.json.tmp').exists()
                    for prior_name,prior_bytes in prior_staged.items(): assert staged[prior_name] == prior_bytes
                    for prior_name,prior_bytes in prior_published.items(): assert published[prior_name] == prior_bytes
                    assert not (output/'result.tmp').exists()
                results[label] = 'entire independent selected Case SOURCE/full SQL rows/types/order/nulls/original framing retained; prior files/tree/result/FAIL exact; incomplete baseline remains TWO REQUIRED-MISSING; native NOT RUN'

    case_holders = ('parked','live_s2','final_snapshot','running','pending','oracle','rejection_drain')
    case_missing_jobs = {f'{holder}-missing-job-index{index}': (holder,index)
                         for holder in ('parked','live_s2','final_snapshot') for index in (0,4,5)}
    # Omitted membership reaches R1 first on the original predicate; after R1
    # alone is repaired, omitted per-job audit identity reaches R2 independently.
    case_refusals = list(case_missing_jobs) + [holder+'-audit-missing-job'
                        for holder in ('pending','oracle','rejection_drain')]
    case_refusals += ['missing-'+k for k in ('method','index','variant','source_sha','task','starts','jobs','manifest','submit','script','script_sha256','job-id')]
    case_refusals += ['wrong-'+k for k in ('method','index','variant','source_sha','task','start','job-id','root','script','script_sha256')]
    case_refusals += ['index-bool','index1-job','start-copy','start-missing-fact','source-hash-only','row-script-content',
        'script-type','script-second-layer','script-secret','script-hash-type','row-flag-bool','row-review-bool','row-max-bytes-bool','row-exit-bool','row-missing-field',
        'row-interpreter','row-start','row-created','row-type','row-nested',
        'snapshot-type','snapshot-missing-table','snapshot-table-type','snapshot-empty-task','snapshot-duplicate-job',
        'snapshot-row-field','running-row-copy','pending-audit-type','oracle-audit-type','drain-audit-type','row-null-source','unknown-encoded','output-json-lookalike',
        'output-second-layer','job-out-second-layer','output-text-type','job-out-type','wrong-owner','lookalike-root','lookalike-column','first-terminal','terminal','refusal-script',
        'shared-work','shared-facts','shared-depth']
    case_refusals += [holder+'-owner' for holder in case_holders]
    case_refusals += [f'{table}.{column}-{form}' for table,column,_ in sql_columns
                     for form in ('bool','number','container','wrong-top','missing')]
    case_refusals += ['sql-'+k for k in sql_bad]
    case_refusals += ['task-owner','result-owner','audit-owner','result-session','audit-job','pending-audit-job','oracle-audit-job','drain-audit-job']
    case_stage_terminal = ('owner','source','hash-only','flag-bool','exit-bool','state','reason','head','path','time',
                          'unknown-content','unknown-type','unknown-missing','unknown-extra','missing')
    case_stage_pair = ('missing-job','extra-job','duplicate-job','foreign-job','reverse-order','first-running',
        'second-terminal','second-last-pending','job-source','job-flag-bool','missing-task','extra-task',
        'task-owner','task-session','task-cleared','task-status','task-step-bool','task-step','task-block-ids',
        'task-full-content','task-full-type','missing-result','extra-result','result-content','result-s2',
        'result-state','result-full-type','resumed-audit','missing-stage','missing-parked','missing-pending')
    case_stage_refusals = {f'stage-terminal-{key}-index{i}': (i,'terminal',key)
                           for key in case_stage_terminal for i in (0,2,3)}
    case_stage_refusals.update({f'stage-pair-{key}-index{i}': (i,'pair',key)
                               for key in case_stage_pair for i in (4,5)})
    case_refusals += list(case_stage_refusals)
    copy_shared = ('outer-order','payload-order','provider-order','wire-order','entered','stdout','stderr',
        'file-text','wire-body','response','argv-org','argv-payload-file',
        'canonical-missing','canonical-foreign','canonical-ambiguous','required-copy','early-contradiction')
    case_copy_refusals = {f'copy-{family}-{key}-index{index}': (index,family,key)
        for index,family in ((0,'submit'),(0,'block'),(0,'final'),(1,'final')) for key in copy_shared}
    case_copy_refusals.update({f'copy-submit-{key}-index{i}': (i,'submit',key)
        for i in (0,4,5) for key in ('script-hash-only','job-swap','task-swap','sid-swap','child-swap',
            'action','operation','missing-field','field-type','python','source','file-count')})
    case_copy_refusals.update({f'copy-{family}-{key}-index{i}': (i,family,key)
        for i in (4,5) for family,key in (('block','waiting-order'),('block','waiting-foreign'),
            ('final','done-summary'),('final','sid-swap'),('block','canonical-waiting-order'),
            ('block','canonical-waiting-foreign'),('final','canonical-done-summary'))})
    case_refusals += list(case_copy_refusals)
    case_shape_refusals = {name:0 for name in (
        'top-starts-object','top-starts-trailing-array','top-starts-trailing-scalar','stubs-true',
        'case-starts-trailing-array','case-starts-trailing-scalar','peer-row-key-order',
        'store27-coordinated-order','store27-coordinated-extra','store27-coordinated-missing',
        'store-original31','store-original31-permuted')}
    case_shape_refusals.update({f'parked-{table}-peer-key-order-index{i}':i
                                for table in ('tasks','results') for i in (4,5)})
    case_shape_refusals.update({f'pair-one-submit-block-index{i}':i for i in (4,5)})
    case_shape_refusals.update({f'reached-running-missing-index{i}-job{j}':i
                                for i in (0,2,3,4,5) for j in range(2 if i >= 4 else 1)})
    case_shape_refusals.update({f'case-start-field-array-start{s}':0 for s in (0,1)})
    case_shape_refusals['case-start-field-nested-array-start0'] = 0
    case_shape_refusals.update({f'oracle-missing-peer-contradiction-index{i}-job{j}':i
                                for i,j in ((0,0),(4,1),(5,1))})
    case_shape_refusals.update({f'oracle-{missing}-missing-index{i}-job{j}':i
                                for missing in ('row','holder') for i in (0,2,3,4,5)
                                for j in range(2 if i >= 4 else 1)})
    case_direct_refusals = {'direct-public-wrapper-index0-job0': (0, 0, 'wrapper'),
     'direct-public-store-flags-index0-job0': (0, 0, 'store-flags'),
     'direct-public-id-index0-job0': (0, 0, 'id'),
     'direct-public-task-index0-job0': (0, 0, 'task'),
     'direct-public-agent-index0-job0': (0, 0, 'agent'),
     'direct-public-interpreter-index0-job0': (0, 0, 'interpreter'),
     'direct-public-script-byte-index0-job0': (0, 0, 'script-byte'),
     'direct-public-script-secret-index0-job0': (0, 0, 'script-secret'),
     'direct-public-script-second-layer-index0-job0': (0, 0, 'script-second-layer'),
     'direct-public-script-residual-index0-job0': (0, 0, 'script-residual'),
     'direct-public-script-type-index0-job0': (0, 0, 'script-type'),
     'direct-public-script-null-index0-job0': (0, 0, 'script-null'),
     'direct-public-status-completed-index0-job0': (0, 0, 'status-completed'),
     'direct-public-status-pending-index0-job0': (0, 0, 'status-pending'),
     'direct-public-exit-bool-index0-job0': (0, 0, 'exit-bool'),
     'direct-public-exit-int-index0-job0': (0, 0, 'exit-int'),
     'direct-public-reason-index0-job0': (0, 0, 'reason'),
     'direct-public-stdout-head-index0-job0': (0, 0, 'stdout-head'),
     'direct-public-stderr-head-index0-job0': (0, 0, 'stderr-head'),
     'direct-public-duration-int-index0-job0': (0, 0, 'duration-int'),
     'direct-public-duration-bool-index0-job0': (0, 0, 'duration-bool'),
     'direct-public-finished-index0-job0': (0, 0, 'finished'),
     'direct-public-flag-review-int-index0-job0': (0, 0, 'flag-review-int'),
     'direct-public-flag-persistent-int-index0-job0': (0, 0, 'flag-persistent-int'),
     'direct-public-flag-review-null-index0-job0': (0, 0, 'flag-review-null'),
     'direct-public-flag-persistent-null-index0-job0': (0, 0, 'flag-persistent-null'),
     'direct-public-max-bytes-bool-index0-job0': (0, 0, 'max-bytes-bool'),
     'direct-public-max-runtime-bool-index0-job0': (0, 0, 'max-runtime-bool'),
     'direct-public-cwd-int-index0-job0': (0, 0, 'cwd-int'),
     'direct-public-created-null-index0-job0': (0, 0, 'created-null'),
     'direct-public-missing-index0-job0': (0, 0, 'missing'),
     'direct-public-extra-index0-job0': (0, 0, 'extra'),
     'direct-public-reordered-index0-job0': (0, 0, 'reordered'),
     'direct-public-missing-running-index0-job0': (0, 0, 'missing-running'),
     'direct-public-unselected-source-index0-job0': (0, 0, 'unselected-source'),
     'direct-public-id-index4-job0': (4, 0, 'id'),
     'direct-public-script-byte-index4-job0': (4, 0, 'script-byte'),
     'direct-public-missing-running-index4-job0': (4, 0, 'missing-running'),
     'direct-public-id-index4-job1': (4, 1, 'id'),
     'direct-public-script-byte-index4-job1': (4, 1, 'script-byte'),
     'direct-public-missing-running-index4-job1': (4, 1, 'missing-running'),
     'direct-public-id-index5-job0': (5, 0, 'id'),
     'direct-public-script-byte-index5-job0': (5, 0, 'script-byte'),
     'direct-public-missing-running-index5-job0': (5, 0, 'missing-running'),
     'direct-public-id-index5-job1': (5, 1, 'id'),
     'direct-public-script-byte-index5-job1': (5, 1, 'script-byte'),
     'direct-public-missing-running-index5-job1': (5, 1, 'missing-running')}
    case_terminal_refusals = {f'terminal-browser-{change}-index0': (0,change) for change in
        ('holder-text-first', 'holder-missing', 'holder-extra', 'holder-array', 'holder-null', 'body-array',
         'body-missing', 'body-extra', 'body-order', 'body-bool-flags', 'body-null-flag', 'body-id',
         'body-task', 'body-agent', 'body-interpreter', 'body-script-byte', 'body-script-secret',
         'body-script-decoded-secret', 'body-script-residual', 'body-script-second-layer',
         'body-script-type', 'body-status', 'body-exit-bool', 'body-reason', 'body-head', 'body-path',
         'body-duration-bool', 'body-time', 'body-cap-bool', 'body-output-cap', 'body-created',
         'missing-oracle', 'screenshot', 'screenshot-type', 'at-bool', 'at-string', 'text-type',
         'anchor-none', 'anchor-no-space', 'anchor-tab', 'anchor-duplicate', 'anchor-overlap',
         'text-dropped-LF', 'text-CRLF', 'source-dropped-LF', 'source-CRLF', 'source-tab',
         'source-nonascii', 'outside-prefix-backslash', 'outside-suffix-backslash', 'header-only',
         'original-secret', 'prefix-span-secret', 'span-suffix-secret', 'chunk-secret',
         'small-two-backslash', 'small-no-space', 'small-dropped-LF', 'small-outside-backslash',
         'unselected-task-source')}
    case_terminal_refusals.update({f'terminal-browser-{change}-index{i}': (i,change) for i in (2,3)
                                  for change in ('holder-text-first','body-id','body-exit-bool','body-script-byte','anchor-duplicate')})
    case_shape_refusals.update({name:spec[0] for name,spec in case_terminal_refusals.items()})
    case_shape_refusals.update({name:spec[0] for name,spec in case_direct_refusals.items()})
    case_refusals += list(case_shape_refusals)
    for case_name in case_refusals:
        for boundary in ('writer','raw','raw-prefix','prefix'):
            terminal_change = case_terminal_refusals[case_name][1] if case_name in case_terminal_refusals else None
            extra_secret = ('prefix-marker$ import' if terminal_change == 'prefix-span-secret' else
                            '\nSUFFIX_MARKER' if terminal_change == 'span-suffix-secret' else
                            'boundary-marker' if terminal_change == 'chunk-secret' else None)
            with fixture(additional_secret=extra_secret) as (writer,root,output,state,staged,published,failed_members,failures,errors,row,check,acquire,drain,commit):
                writer.revision = row['source_sha'] = old_source
                browser_source = (case_small_span_literals['two_backslash_source'] if terminal_change == 'small-two-backslash' else
                    case_small_span_literals['one_backslash_source'] if terminal_change in ('small-no-space','small-dropped-LF','small-outside-backslash') else
                    '$ $ \n' if terminal_change == 'anchor-overlap' else
                    case_literals['0']['jobs'][0]['script'].rstrip('\n') if terminal_change == 'source-dropped-LF' else
                    case_literals['0']['jobs'][0]['script'].replace('\n','\r\n') if terminal_change == 'source-CRLF' else
                    case_literals['0']['jobs'][0]['script'] + '# \t\n' if terminal_change == 'source-tab' else
                    case_literals['0']['jobs'][0]['script'] + '# 中\n' if terminal_change == 'source-nonascii' else None)
                image = case_input(root,case_stage_refusals[case_name][0] if case_name in case_stage_refusals else
                                   case_copy_refusals[case_name][0] if case_name in case_copy_refusals else
                                   case_missing_jobs[case_name][1] if case_name in case_missing_jobs else
                                   case_shape_refusals.get(case_name,0),late=case_name != 'store-original31',
                                   store_image='original31' if case_name == 'store-original31' else
                                   'original31-permuted' if case_name == 'store-original31-permuted'
                                       or case_name.startswith('stage-terminal-unknown-') else
                                   'permuted27' if case_name == 'store27-coordinated-order' else 'accepted',
                                   direct_public=case_name in case_direct_refusals,
                                   terminal_public=case_name in case_terminal_refusals,browser_source=browser_source)
                case = image['durable'][-1 if case_name == 'store-original31' else 0]['jobs_cases'][0]
                job = case['jobs'][0] if case['jobs'] else None
                snapshot = case['final_snapshot']; sql_job = job['oracle']['row'] if job is not None else None
                column_case = next(((t,c,top,case_name[len(t+'.'+c+'-'):]) for t,c,top in sql_columns if case_name.startswith(t+'.'+c+'-')),None)
                if case_name in case_shape_refusals:
                    if case_name in case_terminal_refusals:
                        _,change = case_terminal_refusals[case_name]
                        browser = job['browser']; body = browser['body']; source_script = body['script_text']
                        if change == 'holder-text-first':
                            if case_terminal_refusals[case_name][0] == 2:
                                # ENTIRE accepted negative, including all original
                                # body paths, SOURCE and text, without normalization.
                                job['browser'] = copy.deepcopy(case_text_first_negative)
                            else: job['browser'] = {k:browser[k] for k in ('text','body','screenshot','at')}
                        elif change == 'holder-missing': del browser['at']
                        elif change == 'holder-extra': browser['wire'] = None
                        elif change == 'holder-array': job['browser'] = []
                        elif change == 'holder-null': job['browser'] = None
                        elif change == 'body-array': browser['body'] = []
                        elif change == 'body-missing': del body['reviewed_at']
                        elif change == 'body-extra': body['unknown'] = None
                        elif change == 'body-order':
                            items = list(body.items()); items[0],items[1] = items[1],items[0]
                            body.clear(); body.update(items)
                        elif change == 'body-bool-flags': body.update(review_required=0,persistent=0)
                        elif change == 'body-null-flag': body['persistent'] = None
                        elif change == 'body-id': body['id'] = 'JOB-foreign'
                        elif change == 'body-task': body['task_id'] = 'TASK-foreign'
                        elif change == 'body-agent': body['agent_name'] = 'foreign_agent'
                        elif change == 'body-interpreter': body['interpreter'] = 'bash'
                        elif change == 'body-script-byte': body['script_text'] += '# changed original SOURCE\n'
                        elif change == 'body-script-secret': body['script_text'] += '# '+secret+'\n'
                        elif change == 'body-script-decoded-secret': body['script_text'] += "x='"+chr(92)+"x50RIVATE_SENTINEL"+chr(92)+"x22"+chr(92)+"n'\n"
                        elif change == 'body-script-residual': body['script_text'] += "x='"+chr(92)+"x5cn'\n"
                        elif change == 'body-script-second-layer': body['script_text'] += "x='"+chr(92)+chr(92)+"n'\n"
                        elif change == 'body-script-type': body['script_text'] = 7
                        elif change == 'body-status': body['status'] = 'running'
                        elif change == 'body-exit-bool': body['exit_code'] = False
                        elif change == 'body-reason': body['reason'] = 'foreign reason'
                        elif change == 'body-head': body['stdout_head'] = 'changed full output\n'
                        elif change == 'body-path': body['stdout_path'] += '.foreign'
                        elif change == 'body-duration-bool': body['duration_ms'] = False
                        elif change == 'body-time': body['finished_at'] = None
                        elif change == 'body-cap-bool': body['max_runtime_seconds'] = True
                        elif change == 'body-output-cap': body['max_output_bytes'] = 0
                        elif change == 'body-created': body['created_at'] = None
                        elif change == 'missing-oracle': del job['oracle']
                        elif change == 'screenshot': browser['screenshot'] = 'e04-JOB-foreign.png'
                        elif change == 'screenshot-type': browser['screenshot'] = 7
                        elif change == 'at-bool': browser['at'] = True
                        elif change == 'at-string': browser['at'] = '15.0'
                        elif change == 'text-type': browser['text'] = []
                        elif change == 'anchor-none': browser['text'] = browser['text'].replace('$ ','',1)
                        elif change == 'anchor-no-space': browser['text'] = browser['text'].replace('$ ','$',1)
                        elif change == 'anchor-tab': browser['text'] = browser['text'].replace('$ ','$\t',1)
                        elif change == 'anchor-duplicate': browser['text'] += '\n$ '+source_script
                        elif change == 'anchor-overlap': browser['text'] = '$ $ $ \n'
                        elif change == 'text-dropped-LF':
                            # The original suffix starts LF. Deleting only the
                            # SOURCE's final LF leaves an exact span using that
                            # suffix byte, which the accepted contract permits.
                            # Keep the whole prefix/suffix; mark the missing LF
                            # boundary so this input actually has no full SOURCE.
                            browser['text'] = browser['text'].replace(
                                source_script, source_script[:-1] + '[SOURCE final LF absent]', 1)
                            assert source_script not in browser['text'] and chr(92) in browser['text']
                        elif change == 'text-CRLF': browser['text'] = browser['text'].replace(source_script,source_script.replace('\n','\r\n'),1)
                        elif change in ('source-dropped-LF','source-CRLF','source-tab','source-nonascii'): pass
                        elif change == 'outside-prefix-backslash': browser['text'] = chr(92)+'n'+browser['text']
                        elif change == 'outside-suffix-backslash': browser['text'] += chr(92)+'n'
                        elif change == 'header-only': browser['text'] = 'COMMAND\n$ header\n'+source_script
                        elif change == 'original-secret': browser['text'] += secret
                        elif change == 'prefix-span-secret': browser['text'] = 'prefix-marker$ '+source_script
                        elif change == 'span-suffix-secret': browser['text'] = '$ '+source_script+'SUFFIX_MARKER'
                        elif change == 'chunk-secret': browser['text'] = 'x'*1020+'boundary-marker\n$ '+source_script
                        elif change == 'small-two-backslash': browser['text'] = case_small_span_literals['residual_negative_text']
                        elif change == 'small-no-space': browser['text'] = case_small_span_literals['no_space_anchor_text']
                        elif change == 'small-dropped-LF': browser['text'] = case_small_span_literals['dropped_final_lf_text']
                        elif change == 'small-outside-backslash': browser['text'] = case_small_span_literals['outside_backslash_text']
                        elif change == 'unselected-task-source': case['waiting_browser'] = dict(text=browser['text'],body={},at=1.0)
                        else: raise AssertionError('unhandled terminal browser refusal '+change)
                    elif case_name in case_direct_refusals:
                        _,number,change = case_direct_refusals[case_name]
                        selected_job = case['jobs'][number]
                        public = selected_job['running_browser']
                        if change == 'wrapper': selected_job['running_browser'] = dict(body=public)
                        elif change == 'store-flags': public.update(review_required=0,persistent=0)
                        elif change == 'id': public['id'] = 'JOB-foreign'
                        elif change == 'task': public['task_id'] = 'TASK-foreign'
                        elif change == 'agent': public['agent_name'] = 'foreign_agent'
                        elif change == 'interpreter': public['interpreter'] = 'bash'
                        elif change == 'script-byte': public['script_text'] = public['script_text'].replace('import json,','import jsom,',1)
                        elif change == 'script-secret': public['script_text'] += '# '+secret+'\n'
                        elif change == 'script-second-layer': public['script_text'] += "x='"+chr(92)+chr(92)+"n'\n"
                        elif change == 'script-residual': public['script_text'] += "x='"+chr(92)+"x5cn'\n"
                        elif change == 'script-type': public['script_text'] = 7
                        elif change == 'script-null': public['script_text'] = None
                        elif change == 'status-completed': public['status'] = 'completed'
                        elif change == 'status-pending': public['status'] = 'pending'
                        elif change == 'exit-bool': public['exit_code'] = True
                        elif change == 'exit-int': public['exit_code'] = 0
                        elif change == 'reason': public['reason'] = 'timeout'
                        elif change == 'stdout-head': public['stdout_head'] = 'finished\n'
                        elif change == 'stderr-head': public['stderr_head'] = ''
                        elif change == 'duration-int': public['duration_ms'] = 0
                        elif change == 'duration-bool': public['duration_ms'] = False
                        elif change == 'finished': public['finished_at'] = public['started_at']
                        elif change == 'flag-review-int': public['review_required'] = 0
                        elif change == 'flag-persistent-int': public['persistent'] = 0
                        elif change == 'flag-review-null': public['review_required'] = None
                        elif change == 'flag-persistent-null': public['persistent'] = None
                        elif change == 'max-bytes-bool': public['max_output_bytes'] = True
                        elif change == 'max-runtime-bool': public['max_runtime_seconds'] = True
                        elif change == 'cwd-int': public['cwd_hint'] = 0
                        elif change == 'created-null': public['created_at'] = None
                        elif change == 'missing': del public['reviewed_at']
                        elif change == 'extra': public['unknown_public'] = None
                        elif change == 'reordered':
                            items = list(public.items()); items[0],items[1] = items[1],items[0]
                            public.clear(); public.update(items)
                        elif change == 'missing-running': del selected_job['running']
                        elif change == 'unselected-source':
                            del selected_job['running_browser']
                            case['lookalike'] = dict(running_browser=public)
                        else: raise AssertionError('unhandled direct public refusal '+change)
                    elif case_name == 'top-starts-object': image['starts'] = {'only':image['starts'][0], 'second':image['starts'][1]}
                    elif case_name == 'top-starts-trailing-array': image['starts'].append([])
                    elif case_name == 'top-starts-trailing-scalar': image['starts'].append(7)
                    elif case_name == 'stubs-true': image['durable'][1]['executable_manifest']['stubs'] = True
                    elif case_name == 'case-starts-trailing-array': case['starts'].append([])
                    elif case_name == 'case-starts-trailing-scalar': case['starts'].append(7)
                    elif case_name == 'peer-row-key-order':
                        selected = job['terminal']; items = list(selected.items())
                        items[0],items[1] = items[1],items[0]; selected.clear(); selected.update(items)
                    elif case_name.startswith('parked-'):
                        table = 'tasks' if case_name.startswith('parked-tasks-') else 'results'
                        selected = case['first_terminal'][table][0]; items = list(selected.items())
                        items[0],items[1] = items[1],items[0]; selected.clear(); selected.update(items)
                    elif case_name in ('store27-coordinated-extra','store27-coordinated-missing'):
                        selected_rows = [r for holder in (case['parked'],case['live_s2']['snapshot'],case['final_snapshot']) for r in holder['jobs']]
                        for selected_job in case['jobs']:
                            selected_rows += [selected_job['running'],selected_job['terminal'],selected_job['pending'][0]['row'],
                                selected_job['oracle']['row'],selected_job['rejection_drain']['row']]
                        for selected in selected_rows:
                            if case_name == 'store27-coordinated-extra': selected['extra_original_scalar'] = None
                            else: del selected['created_at']
                    elif case_name.startswith('pair-one-submit-block-'):
                        for key in ('parked','first_terminal','live_s2','final_snapshot','final_callback'): case.pop(key)
                        case['starts'] = case['starts'][:1]; image['starts'] = image['starts'][:1]
                        case['jobs'] = case['jobs'][:1]
                        for key in ('terminal','oracle','rejection_drain'): job.pop(key)
                        image['provider_commands'] = [image['provider_commands'][0],image['provider_commands'][-2]]
                        command = image['provider_commands'][-1]
                        command['payload']['waiting_on_job_ids'] = [job['id']]
                        payload = command['payload']; text = json.dumps(payload,sort_keys=True)
                        digest = hashlib.sha256(text.encode()).hexdigest()
                        command['payload_file'].update(text=text,bytes=len(text.encode()),sha256=digest,path=str(root/'coord'/(digest+'.json')))
                        command['argv'][-1] = command['payload_file']['path']
                        body = dict(session_id=payload['session_id'],agent=payload['agent'],status=payload['status'],confidence=90,
                            output_summary=payload['summary'],risks_flagged=[],dependencies=[],suggested_reviewer_focus=[],
                            waiting_on_job_ids=[job['id']])
                        command['wire'][0]['body_sha256'] = hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                        case['block'] = copy.deepcopy(command)
                    elif case_name.startswith('reached-running-missing-'):
                        case['jobs'][int(case_name[-1])].pop('running')
                    elif case_name.startswith('case-start-field-'):
                        case['starts'][int(case_name[-1])]['extra_original_fact'] = ([[]]
                            if case_name == 'case-start-field-nested-array-start0' else [])
                    elif case_name.startswith('oracle-'):
                        selected = case['jobs'][int(case_name[-1])]
                        if case_name.startswith('oracle-holder-missing-'): selected.pop('oracle')
                        else: selected['oracle'].pop('row')
                        if case_name.startswith('oracle-missing-peer-contradiction-'):
                            selected['terminal']['finished_at'] += '-changed'
                    elif case_name not in ('store-original31','store-original31-permuted','store27-coordinated-order'):
                        raise AssertionError(case_name)
                elif case_name in case_copy_refusals:
                    _,family,key = case_copy_refusals[case_name]
                    command = job['submit'] if family == 'submit' else case['block' if family == 'block' else 'final_callback']
                    action = 'jobs-'+family; position = next(n for n,c in enumerate(image['provider_commands']) if c['action'] == action)
                    reseal = False
                    if key == 'outer-order':
                        items = list(command.items()); command.clear(); command.update(reversed(items))
                    elif key == 'payload-order': command['payload'] = dict(reversed(list(command['payload'].items()))); reseal = True
                    elif key == 'provider-order': command['provider'] = dict(reversed(list(command['provider'].items())))
                    elif key == 'wire-order': command['wire'][0] = dict(reversed(list(command['wire'][0].items())))
                    elif key == 'entered': command['entered'] += .125
                    elif key in ('stdout','stderr'): command[key] += 'different full original output\n'
                    elif key == 'file-text': command['payload_file']['text'] += ' '
                    elif key == 'file-count': command['payload_file']['bytes'] += 1
                    elif key == 'wire-body': command['wire'][0]['body_sha256'] = '0'*64
                    elif key == 'response': command['wire'][0]['response']['id' if family == 'submit' else 'ok'] = 'JOB-999' if family == 'submit' else False
                    elif key == 'argv-org': command['argv'][4 if family == 'submit' else 3] = 'beta'
                    elif key == 'argv-payload-file': command['argv'][-2 if family == 'submit' else -1] += '.foreign'
                    elif key == 'canonical-missing': image['provider_commands'].pop(position)
                    elif key == 'canonical-foreign': image['provider_commands'][position]['provider']['task'] = 'TASK-999'
                    elif key == 'canonical-ambiguous': image['provider_commands'].insert(position,copy.deepcopy(image['provider_commands'][position]))
                    elif key == 'required-copy':
                        if family == 'submit': del job['submit']
                        else: del case['block' if family == 'block' else 'final_callback']
                    elif key == 'early-contradiction':
                        image['durable'][0]['jobs_cases'].append(copy.deepcopy(case))
                        command['entered'] += .125
                    elif key == 'script-hash-only': command['payload']['script'] += '# changed full copy with unchanged claimed job SOURCE hash\n'; reseal = True
                    elif key == 'job-swap':
                        if len(case['jobs']) == 2: job['submit'] = copy.deepcopy(case['jobs'][1]['submit'])
                        else:
                            command['wire'][0]['response']['id'] = 'JOB-999'
                            command['wire'][0]['response']['events_url'] = '/api/v1/orgs/alpha/jobs/JOB-999/events'
                            command['stdout'] = json.dumps(command['wire'][0]['response'],sort_keys=True)+'\n'
                    elif key == 'task-swap': command['provider']['task'] = 'TASK-999'
                    elif key == 'sid-swap': command['provider']['session'] = case['starts'][0]['session'] if family == 'final' else 'sess-foreign'
                    elif key == 'child-swap': command['child_pid'] = 999
                    elif key == 'action': command['action'] = 'jobs-read'
                    elif key == 'operation': command['operation'] = 'receipt'
                    elif key == 'missing-field': del command['execution_deadline']
                    elif key == 'field-type': command['exit'] = False
                    elif key == 'python': command['python'] += '.foreign'
                    elif key == 'source': command['source_sha'] = '0'*40
                    elif key in ('waiting-order','canonical-waiting-order'): command['payload']['waiting_on_job_ids'].reverse(); reseal = True
                    elif key in ('waiting-foreign','canonical-waiting-foreign'): command['payload']['waiting_on_job_ids'][-1] = 'JOB-999'; reseal = True
                    elif key in ('done-summary','canonical-done-summary'):
                        command['payload']['summary'] += ' altered'
                        command['payload']['decision']['summary'] = command['payload']['summary']; reseal = True
                    else: raise AssertionError(key)
                    if reseal:
                        payload = command['payload']; text = json.dumps(payload,sort_keys=True); digest = hashlib.sha256(text.encode()).hexdigest()
                        file = command['payload_file']; file.update(text=text,bytes=len(text.encode()),sha256=digest,path=str(root/'coord'/(digest+'.json')))
                        command['argv'][-2 if family == 'submit' else -1] = file['path']
                        if family == 'submit': body = payload
                        else:
                            body = dict(session_id=payload['session_id'],agent=payload['agent'],status=payload['status'],confidence=90,
                                output_summary=payload['summary'],risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
                            if family == 'block': body['waiting_on_job_ids'] = list(payload['waiting_on_job_ids'])
                            else: body['decision'] = dict(payload['decision'])
                        command['wire'][0]['body_sha256'] = hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                        if key.startswith('canonical-'): image['provider_commands'][position] = copy.deepcopy(command)
                elif case_name in case_stage_refusals:
                    _,family,key = case_stage_refusals[case_name]
                    if family == 'terminal':
                        selected = job['terminal']
                        if key == 'owner': selected['task_id'] = 'TASK-foreign'
                        elif key in ('source','hash-only'):
                            selected['script_text'] += '# contradictory full terminal SOURCE\n'
                            if key == 'hash-only': job['script_sha256'] = hashlib.sha256(selected['script_text'].encode()).hexdigest()
                        elif key == 'flag-bool': selected['persistent'] = False
                        elif key == 'exit-bool': selected['exit_code'] = False
                        elif key == 'state': selected['status'] = 'running'
                        elif key == 'reason': selected['reason'] = None if job['code'] == -9 else 'timeout'
                        elif key == 'head': selected['stdout_head'] += 'changed'
                        elif key == 'path': selected['stdout_path'] += '.changed'
                        elif key == 'time': selected['finished_at'] += '-changed'
                        elif key == 'unknown-content': selected['unknown_sql_text'] = 'changed full original scalar'
                        elif key == 'unknown-type': selected['unknown_sql_integer'] = 0.0
                        elif key == 'unknown-missing': del selected['unknown_sql_null']
                        elif key == 'unknown-extra': selected['another_original_column'] = None
                        elif key == 'missing': del job['terminal']
                        else: raise AssertionError(key)
                    else:
                        early = case['first_terminal']; task_row = early['tasks'][0]; result_row = early['results'][0]
                        if key == 'missing-job': early['jobs'].pop()
                        elif key == 'extra-job': early['jobs'].append(copy.deepcopy(early['jobs'][1]))
                        elif key == 'duplicate-job': early['jobs'][1] = copy.deepcopy(early['jobs'][0])
                        elif key == 'foreign-job': early['jobs'][1]['id'] = 'JOB-foreign'
                        elif key == 'reverse-order': early['jobs'].reverse()
                        elif key == 'first-running': early['jobs'][0] = copy.deepcopy(job['running'])
                        elif key == 'second-terminal': early['jobs'][1] = copy.deepcopy(case['jobs'][1]['oracle']['row'])
                        elif key == 'second-last-pending':
                            later = copy.deepcopy(case['jobs'][1]['pending'][-1])
                            later['row']['unknown_sql_text'] = 'different actual last pending row'
                            case['jobs'][1]['pending'].append(later)
                        elif key == 'job-source': early['jobs'][1]['script_text'] += '# contradictory pair source\n'
                        elif key == 'job-flag-bool': early['jobs'][1]['review_required'] = False
                        elif key == 'missing-task': early['tasks'] = []
                        elif key == 'extra-task': early['tasks'].append(copy.deepcopy(task_row))
                        elif key == 'task-owner': task_row['id'] = 'TASK-foreign'
                        elif key == 'task-session': task_row['current_session_id'] = case['starts'][1]['session']
                        elif key == 'task-cleared': task_row['block_kind'] = None
                        elif key == 'task-status': task_row['status'] = 'completed'
                        elif key == 'task-step-bool': task_row['orchestration_step_count'] = True
                        elif key == 'task-step': task_row['orchestration_step_count'] = 2
                        elif key == 'task-block-ids': task_row['blocked_on_job_ids'] = json.dumps([job['id']])
                        elif key == 'task-full-content': task_row['note'] = 'changed original parked field'
                        elif key == 'task-full-type': task_row['future_integer'] = 0.0
                        elif key == 'missing-result': early['results'] = []
                        elif key == 'extra-result': early['results'].append(copy.deepcopy(result_row))
                        elif key == 'result-content': result_row['output_summary'] = 'changed full parked result'
                        elif key == 'result-s2': result_row['session_id'] = case['starts'][1]['session']
                        elif key == 'result-state': result_row['status'] = 'completed'
                        elif key == 'result-full-type': result_row['duration_seconds'] = 1.0
                        elif key == 'resumed-audit': early['audit'][0]['action'] = 'task_resumed_from_jobs'
                        elif key == 'missing-stage': del case['first_terminal']
                        elif key == 'missing-parked': del case['parked']
                        elif key == 'missing-pending': case['jobs'][1]['pending'][-1].pop('row')
                        else: raise AssertionError(key)
                elif case_name in case_missing_jobs:
                    holder,_ = case_missing_jobs[case_name]
                    selected = case['live_s2']['snapshot'] if holder == 'live_s2' else case[holder]
                    selected['jobs'].pop()
                elif case_name in ('pending-audit-missing-job','oracle-audit-missing-job','rejection_drain-audit-missing-job'):
                    holder = case_name.removesuffix('-audit-missing-job')
                    audit = job['pending'][0]['audit'][0] if holder == 'pending' else job[holder]['audits'][0]
                    audit['payload'] = json.dumps(dict(message='quote" LF\n 中'))
                elif column_case:
                    table,column,top,form = column_case
                    if form == 'missing': del snapshot[table][0][column]
                    else: snapshot[table][0][column] = {'bool':True,'number':0,'container':{},'wrong-top':'[]' if top == '{' else '{}'}[form]
                elif case_name.startswith('sql-'): snapshot['audit'][0]['payload'] = sql_bad[case_name[4:]]
                elif case_name.startswith('missing-'):
                    key = case_name[8:]
                    if key == 'manifest': image['durable'].pop()
                    elif key == 'submit': image['provider_commands'] = []
                    elif key in ('script','script_sha256'): del job[key]
                    elif key == 'job-id': del job['id']
                    else: del case[key]
                elif case_name.startswith('wrong-') and case_name != 'wrong-owner':
                    key = case_name[6:]
                    if key == 'root': image['durable'][1]['executable_manifest']['root'] += '/foreign'
                    elif key == 'start': case['starts'][0]['session'] = 'foreign-start'
                    elif key == 'job-id': job['id'] = 'JOB-999'
                    elif key == 'index': case[key] = 6
                    elif key == 'script': job['script'] += '# changed complete SOURCE\n'
                    elif key == 'script_sha256': job[key] = '0'*64
                    else: case[key] = 'foreign'
                elif case_name == 'index-bool': case['index'] = True
                elif case_name == 'index1-job': case.update(index=1,variant='E04.review-required-refused')
                elif case_name == 'start-copy': case['starts'][0]['pid'] = 999
                elif case_name == 'start-missing-fact': del case['starts'][0]['pid_start']
                elif case_name == 'source-hash-only':
                    job['script'] += '# altered full source\n'; job['script_sha256'] = hashlib.sha256(job['script'].encode()).hexdigest()
                elif case_name == 'row-script-content': sql_job['script_text'] += '# changed row source\n'
                elif case_name == 'script-type': job['script'] = {}
                elif case_name == 'script-second-layer': job['script'] = 'x="second\\\\nlayer"\n'
                elif case_name == 'script-secret': job['script'] = 'print("SYNTHETIC_TOKEN")\n'
                elif case_name == 'script-hash-type': job['script_sha256'] = True
                elif case_name == 'row-flag-bool': sql_job['persistent'] = False
                elif case_name == 'row-review-bool': sql_job['review_required'] = False
                elif case_name == 'row-max-bytes-bool': sql_job['max_output_bytes'] = True
                elif case_name == 'row-exit-bool': sql_job['exit_code'] = False
                elif case_name == 'row-missing-field': del sql_job['created_at']
                elif case_name == 'row-interpreter': sql_job['interpreter'] = 'bash'
                elif case_name == 'row-start': sql_job['started_at'] += '-foreign'
                elif case_name == 'row-created': sql_job['created_at'] += '-foreign'
                elif case_name == 'row-type': job['oracle']['row'] = []
                elif case_name == 'row-nested': sql_job['unknown_nested'] = {}
                elif case_name == 'snapshot-type': case['final_snapshot'] = []
                elif case_name == 'snapshot-missing-table': del snapshot['jobs']
                elif case_name == 'snapshot-table-type': snapshot['jobs'] = {}
                elif case_name == 'snapshot-empty-task': snapshot['tasks'] = []
                elif case_name == 'snapshot-duplicate-job': snapshot['jobs'].append(copy.deepcopy(snapshot['jobs'][0]))
                elif case_name == 'snapshot-row-field': snapshot['jobs'][0]['unknown_sql_text'] = 'changed full original scalar'
                elif case_name == 'running-row-copy': job['pending'][0]['row']['unknown_sql_text'] = 'changed full original scalar'
                elif case_name == 'pending-audit-type': job['pending'][0]['audit'] = {}
                elif case_name == 'oracle-audit-type': job['oracle']['audits'] = {}
                elif case_name == 'drain-audit-type': job['rejection_drain']['audits'] = {}
                elif case_name == 'row-null-source': sql_job['script_text'] = None
                elif case_name == 'unknown-encoded': sql_job['unknown_sql_text'] = 'public\\nvalue'
                elif case_name == 'lookalike-column': sql_job['script_text_copy'] = job['script']
                elif case_name == 'output-json-lookalike': job['oracle']['outputs'][0]['text'] = '{"value":"second\\nlayer"}'
                elif case_name == 'output-second-layer': job['oracle']['outputs'][0]['text'] = 'second\\nlayer'
                elif case_name == 'job-out-second-layer': job['out'] = 'second\\nlayer'
                elif case_name == 'output-text-type': job['oracle']['outputs'][0]['text'] = 7
                elif case_name == 'job-out-type': job['out'] = 7
                elif case_name in (holder+'-owner' for holder in case_holders):
                    holder = case_name[:-6]
                    changed = (case[holder]['jobs'][0] if holder in ('parked','final_snapshot') else
                        case['live_s2']['snapshot']['jobs'][0] if holder == 'live_s2' else job['running'] if holder == 'running' else
                        job['pending'][0]['row'] if holder == 'pending' else job[holder]['row'])
                    changed['task_id'] = 'TASK-foreign'
                elif case_name == 'lookalike-root': image['durable'][0] = dict(cases=[case])
                elif case_name == 'first-terminal': case['first_terminal'] = copy.deepcopy(case['parked'])
                elif case_name == 'terminal': job['terminal']['script_text'] += '# contradictory earlier terminal copy\n'
                elif case_name == 'refusal-script': case['refusal'] = dict(script=job['script'])
                elif case_name == 'task-owner': snapshot['tasks'][0]['id'] = 'TASK-foreign'
                elif case_name == 'result-owner': snapshot['results'][0]['task_id'] = 'TASK-foreign'
                elif case_name == 'audit-owner': snapshot['audit'][0]['task_id'] = 'TASK-foreign'
                elif case_name == 'result-session': snapshot['results'][0]['session_id'] = 'foreign-start'
                elif case_name in ('audit-job','pending-audit-job','oracle-audit-job','drain-audit-job'):
                    audit = (snapshot['audit'][0] if case_name == 'audit-job' else job['pending'][0]['audit'][0]
                        if case_name == 'pending-audit-job' else job['oracle']['audits'][0] if case_name == 'oracle-audit-job' else job['rejection_drain']['audits'][0])
                    audit['payload'] = '{"script_request_id":"JOB-foreign"}'
                elif case_name == 'shared-work': snapshot['audit'][0]['payload'] = '{"value":"'+'p'*2800000+'"}'
                elif case_name == 'shared-facts': snapshot['audit'][0]['payload'] = '{"values":['+','.join('0' for _ in range(70000))+']}'
                elif case_name == 'shared-depth': snapshot['audit'][0]['payload'] = '{"value":'+'['*60+'0'+']'*60+'}'
                elif case_name == 'wrong-owner': pass
                else: raise AssertionError('unhandled Case refusal '+case_name)
                wanted = copy.deepcopy(image); original_image = json.dumps(image,ensure_ascii=False,separators=(',',':')).encode()
                name = 'e07.json' if case_name == 'wrong-owner' else 'e07-prefix.json' if boundary in ('raw-prefix','prefix') else 'observations.json'
                destination = root/'raw'/name; prior = b'{"prior":"safe"}'; destination.write_bytes(prior)
                acquire(); drain(); commit()
                previous = {p.name:p.read_bytes() for p in output.iterdir()}; tree = copy.deepcopy(state['safe_scenario'])
                scenario = (root/'scenario.json').read_bytes(); old_buffer = staged[name]
                if case_name in case_shape_refusals:
                    prior_staged,prior_published = copy.deepcopy(staged),copy.deepcopy(published)
                    raw_names = {p.name for p in (root/'raw').iterdir()}
                if boundary in ('writer','prefix'):
                    fatal_owner_loss = case_name == 'wrong-owner' and boundary == 'prefix'
                    exhausted_budget = case_name == 'shared-facts'
                    original_fatal = None
                    initial_capacity_error = initial_capacity_tb = None
                    if fatal_owner_loss or exhausted_budget:
                        assert writer.redaction_ready and writer._evidence_fatal is None
                        writer._atomic_text(root/'raw/eligible-case.txt','eligible ordinary Case sibling\n')
                        assert (root/'raw/eligible-case.txt').read_bytes() == b'eligible ordinary Case sibling\n'
                    if boundary == 'prefix': source = root/'raw/observations.json'; source.write_bytes(original_image)
                    try:
                        if boundary == 'writer': writer._atomic_json(destination,image)
                        else: writer._prepare_original_evidence(destination,source)
                    except (ValueError,AssertionError) as exc:
                        if writer._evidence_fatal is not None:
                            error, tb = writer._evidence_fatal
                            if (not fatal_owner_loss or type(exc) is not AssertionError
                                    or str(exc) != 'original evidence owner/path unavailable; observed=None'):
                                # Unexpected fatal causes must remain visible.
                                error.add_note('Case refusal: '+case_name+'/'+boundary)
                                raise error.with_traceback(tb)
                            original_fatal = writer._evidence_fatal
                            assert error is exc and tb is not None and not writer.redaction_ready
                        elif exhausted_budget:
                            if (type(exc) is not _EvidenceCapacity
                                    or str(exc) != 'evidence capacity refused'
                                    or not _ordinary_evidence_error(exc)):
                                exc.add_note('Case refusal: '+case_name+'/'+boundary)
                                raise
                            initial_capacity_error, initial_capacity_tb = exc, exc.__traceback__
                            assert initial_capacity_tb is not None and writer.redaction_ready
                            assert writer._evidence_fatal is None
                        elif fatal_owner_loss:
                            raise
                    else: raise AssertionError('Case unsafe member admitted: '+case_name+'/'+boundary)
                    assert destination.read_bytes() == prior and staged[name] is old_buffer and not state['fatal']
                    if boundary == 'prefix': assert source.read_bytes() == original_image
                    if fatal_owner_loss:
                        assert original_fatal is not None
                        try: writer._atomic_text(root/'raw/eligible-case.txt','eligible ordinary Case sibling\n')
                        except AssertionError as later:
                            assert str(later) == 'unsafe evidence withheld without full redaction context; observed=None'
                            assert writer._evidence_fatal is original_fatal
                            assert original_fatal[0] is error and original_fatal[1] is tb and not writer.redaction_ready
                        else: raise AssertionError('fatal Case owner/path allowed continuation: '+case_name+'/'+boundary)
                        assert (root/'raw/eligible-case.txt').read_bytes() == b'eligible ordinary Case sibling\n'
                        assert not (root/'raw/eligible-case.txt.tmp').exists()
                    elif exhausted_budget:
                        assert initial_capacity_error is not None and initial_capacity_tb is not None
                        try: writer._atomic_text(root/'raw/eligible-case.txt','eligible ordinary Case sibling\n')
                        except _EvidenceCapacity as later:
                            assert type(later) is _EvidenceCapacity and str(later) == 'evidence capacity refused'
                            assert _ordinary_evidence_error(later) and later.__traceback__ is not None
                            assert writer.redaction_ready and writer._evidence_fatal is None
                            assert initial_capacity_error.__traceback__ is initial_capacity_tb
                        else: raise AssertionError('exhausted Case budget allowed continuation: '+case_name+'/'+boundary)
                        assert (root/'raw/eligible-case.txt').read_bytes() == b'eligible ordinary Case sibling\n'
                        assert not (root/'raw/eligible-case.txt.tmp').exists()
                        assert destination.read_bytes() == prior and staged[name] is old_buffer and not state['fatal']
                        if boundary == 'prefix': assert source.read_bytes() == original_image
                    else:
                        writer._atomic_text(root/'raw/eligible-case.txt','eligible ordinary Case sibling\n')
                        assert (root/'raw/eligible-case.txt').read_bytes() == b'eligible ordinary Case sibling\n'
                else:
                    destination.write_bytes(original_image); (root/'raw/eligible-case.txt').write_bytes(b'eligible ordinary Case sibling\n')
                    complete,error = acquire(); drain()
                    assert not complete and error is not None and not state['fatal'] and not errors, (case_name,boundary,failures)
                    assert destination.read_bytes() == original_image and staged[name] is old_buffer and published[name] is old_buffer
                    assert name in state['refused_sources'] and (output/name).read_bytes() == prior
                    if case_name not in ('shared-work','shared-facts'): assert (output/'eligible-case.txt').read_bytes() == b'eligible ordinary Case sibling\n'
                exact(image,wanted); exact(state['safe_scenario'],tree)
                assert (root/'scenario.json').read_bytes() == scenario
                for prior_name,prior_bytes in previous.items(): assert (output/prior_name).read_bytes() == prior_bytes
                assert not destination.with_name(name+'.tmp').exists() and not (output/(name+'.tmp')).exists() and not (output/'result.tmp').exists()
                if case_name in case_shape_refusals:
                    assert {p.name for p in output.iterdir()} == set(previous) | ({'eligible-case.txt'} if boundary in ('raw','raw-prefix') else set())
                    assert {p.name for p in (root/'raw').iterdir()} == raw_names | {'eligible-case.txt'} | ({'observations.json'} if boundary == 'prefix' else set())
                    for prior_name,prior_bytes in prior_staged.items(): assert staged[prior_name] == prior_bytes
                    for prior_name,prior_bytes in prior_published.items(): assert published[prior_name] == prior_bytes
                    assert set(staged) == set(prior_staged) | ({'eligible-case.txt'} if boundary in ('raw','raw-prefix') else set())
                    assert set(published) == set(prior_published) | ({'eligible-case.txt'} if boundary in ('raw','raw-prefix') else set())
                result_name = ('C5-Case-stage-refusal-'+case_name.removeprefix('stage-') if case_name in case_stage_refusals
                               else 'C5-Case-refusal-'+case_name)
                results[result_name+'-'+boundary] = 'whole original input/prior member/output/result/FAIL/tree exact; no selected pending/publication; overlapping owner/strict/R1/capacity guards; specific sensitivity PROOF NOT RUN'

    return results


def original_controls() -> dict:
    """Extend the existing raw-reader controls; no native/browser fixture runs."""
    import tempfile
    import threading
    from transport import BrowserRelay
    results = {}
    text = '{"thread_id":"THR-007","pending_replies":["case_worker"]}'
    raw = text.encode()
    digest = hashlib.sha256(raw).hexdigest()
    record = dict(relay_id=0, method='POST', target='/api/v1/orgs/alpha/threads',
        path='/api/v1/orgs/alpha/threads', ordinal=1, status=200, entered=2., delivered=6., closed=7.,
        original=dict(schema=1,relay_id=0,required_kind='compose',started=2.,status=200,
            framing=dict(content_type=['application/json'],content_encoding=[],content_length=[str(len(raw))],transfer_encoding=[],bodyless=False),
            header_bytes=64,finished=3.,outcome='complete',byte_length=len(raw),sha256=digest,
            first_error=None,capture_state='sealed',capture_refusal=None,
            entity_copy=dict(representation='unchanged_utf8',text=text,copy_bytes=len(raw),copy_sha256=digest,original_byte_check='available')),
        forwarding=dict(segments=[dict(offset=0,length=len(raw),sha256=digest,write_started=4.,write_returned=5.,flush_returned=5.5,written=len(raw))],
                        byte_length=len(raw),sha256=digest,finished=5.7))
    expected = {'thread_id':'THR-007','pending_replies':['case_worker']}
    assert validate_original_member(record) == expected
    # Actual capture/export methods on a bare test-harness owner: no constructor,
    # server, socket, browser, HTTP parser or native ownership fixture is run.
    # Literal bytes are independent finite scenario inputs to the real exporter.
    def captured(data, outcome='complete', *, status=200, encoding=None):
        relay = BrowserRelay.__new__(BrowserRelay)
        relay.lock = threading.Lock()
        relay.records = [dict(copy.deepcopy(record), method='GET',
                             path='/api/v1/orgs/alpha/threads/THR-007',
                             target='/api/v1/orgs/alpha/threads/THR-007',ordinal=None)]
        relay.browser_requests, relay.originals, relay.capture_refusals = [], {}, []
        relay.capture_bytes = relay.capture_facts = 0
        relay.capture_closed = None
        source = relay.records[0]
        relay._capture_begin(source, False)
        source['status'] = status
        source['original'].update(status=status, framing=dict(content_type=['application/json'],
            content_encoding=encoding or [], content_length=[str(len(data))],transfer_encoding=[],bodyless=outcome=='bodyless'),
            header_bytes=64)
        relay._capture_chunk(source,data)
        if outcome in {'complete','empty','bodyless'}:
            relay._capture_seal(source)
        elif outcome in {'partial','error'}:
            relay._capture_error(source,'read',OSError())
        return relay
    relay = captured(raw)
    try:
        relay.original_for(99)
    except ValueError as exc:
        assert 'Request identity unavailable' in str(exc)
    else:
        raise AssertionError('missing native Request accepted')
    published = relay.snapshot(lambda value:value)[0]
    entity = published['original']['entity_copy']
    assert entity == dict(representation='unchanged_utf8',text=text,copy_bytes=len(raw),
                          copy_sha256=digest,original_byte_check='available')
    assert published['original']['sha256'] == digest and published['original']['byte_length'] == len(raw)
    assert relay.browser_requests == []  # Export did not invent a browser join.
    results['A03-A13-sealed-unpaired-export'] = 'actual capture/snapshot JSON seam; native forwarding NOT RUN'
    safe_copy = captured(raw).snapshot(lambda value:value.replace('THR-007','THR-008'))[0]['original']['entity_copy']
    assert safe_copy['representation'] == 'sanitized_utf8' and safe_copy['original_byte_check'] == 'unavailable'
    assert safe_copy['copy_sha256'] == hashlib.sha256(safe_copy['text'].encode()).hexdigest() != digest
    results['A13-sanitized-original-unavailable'] = 'sanitized copy cannot establish original equality'
    for name,data,outcome in (('empty',b'','empty'),('bodyless',b'','bodyless'),
                              ('partial',b'{','partial'),('error',b'','error'),('pending',b'','pending')):
        relay = captured(data,outcome)
        published = relay.snapshot(lambda value:value)[0]['original']
        assert published['outcome'] == outcome
        if outcome in {'empty','bodyless'}:
            assert published['entity_copy']['text'] == '' and published['entity_copy']['copy_bytes'] == 0
        else:
            assert published['entity_copy']['representation'] == 'unavailable'
        results['A03-A08-export-'+name] = 'explicit original outcome; never required JSON success'
    for name,data,encoding in (('invalid-json',b'{',None),('invalid-utf8',b'\xff',None),
                               ('duplicate-json',b'{"status":"open","status":"failed"}',None),
                               ('nonfinite',b'{"seq":NaN}',None),('encoding',raw,['gzip']),
                               ('sensitive',b'{"credential":"PRIVATE_SENTINEL"}',None)):
        relay = captured(data,encoding=encoding)
        assert relay.snapshot(lambda value:value)[0]['original']['entity_copy']['representation'] == 'unavailable'
        before = relay.capture_bytes
        assert relay.snapshot(lambda value:value)[0]['original']['entity_copy']['text'] is None
        assert relay.capture_bytes == before  # Invalid parse never consumes a new copy allowance.
        if relay.originals[0]['parse_error'] is not None:
            counters = relay.capture_bytes,relay.capture_facts
            for _ in range(2):
                try:
                    relay._parse_original(relay.records[0])
                except (ValueError,UnicodeError,RecursionError):
                    pass
                else:
                    raise AssertionError('previous parse refusal accepted')
                assert (relay.capture_bytes,relay.capture_facts) == counters
        results['A13-export-'+name] = 'unsafe/unparseable original withheld; parse work not repeated'
    relay = captured(raw)
    relay.capture_bytes = 8*1024*1024
    refused = relay.snapshot(lambda value:value)[0]['original']
    assert refused['capture_state'] == 'refused' and refused['capture_refusal'] is not None
    assert refused['entity_copy']['representation'] == 'unavailable'
    results['A07-A13-export-capacity'] = 'same snapshot retains sticky refusal and unavailable copy'
    relay = captured(b'[' + b'0,'*17000 + b'0]')
    assert relay.snapshot(lambda value:value)[0]['original']['entity_copy']['representation'] == 'unavailable'
    assert relay.records[0]['original']['capture_state'] == 'refused'
    assert relay.capture_refusals[0]['reason'] == 'fact-cap'
    results['A07-A13-parse-fact-cap'] = 'parser facts reserved before retained JSON allocation'
    relay = captured(raw)
    relay.capture_facts = 16384
    assert relay.snapshot(lambda value:value) == []
    assert relay.capture_refusals[0]['reason'] == 'fact-cap'
    assert relay.snapshot(lambda value:value) == [] and len(relay.capture_refusals) == 1
    results['A07-A13-export-fact-cap'] = 'incomplete census; one sticky refusal'
    relay = captured(b'{"status":"failed"}',status=503)
    published = relay.snapshot(lambda value:value)[0]
    assert published['original']['status'] == 503
    assert published['original']['entity_copy']['text'] == '{"status":"failed"}'
    try:
        validate_original_member(published)
    except ValueError as exc:
        assert 'original status/timeline' in str(exc)
    else:
        raise AssertionError('non200 original became required success')
    results['A03-A08-non200-original'] = 'safe original published; required success still refuses'
    for outcome in ('partial','error','pending'):
        relay = captured(b'{' if outcome == 'partial' else b'',outcome)
        source = copy.deepcopy(relay.records[0]['original'])
        published = relay.snapshot(lambda value:value)[0]['original']
        assert all(published[k] == v for k,v in source.items())
        assert published['entity_copy']['representation'] == 'unavailable'
    results['A03-A08-prefix-identities'] = 'first error/status/framing/hash/length/outcome unchanged'

    results['A01-original-reader-consistency'] = 'accepted original only; browser evidence unavailable'
    mutations = {
        'unknown-field':('closed original grammar',lambda r:r['original'].update(extra=True)),
        'wrong-relay':('original identity',lambda r:r['original'].update(relay_id=1)),
        'partial':('complete required original',lambda r:r['original'].update(outcome='partial')),
        'empty':('complete required original',lambda r:r['original'].update(outcome='empty')),
        'bodyless':('complete required original',lambda r:r['original'].update(outcome='bodyless')),
        'pending':('complete required original',lambda r:r['original'].update(outcome='pending')),
        'error':('complete required original',lambda r:r['original'].update(outcome='error',first_error=dict(stage='read',error='OSError',at=3.))),
        'storage-refusal':('complete required original',lambda r:r['original'].update(capture_state='refused',capture_refusal=dict(reason='entity-cap',at=3.,relay_id=0))),
        'nonidentity':('identity UTF8',lambda r:r['original']['framing'].update(content_encoding=['gzip'])),
        'entity-cap':('entity/header bounds',lambda r:r['original'].update(byte_length=1048577)),
        'header-cap':('entity/header bounds',lambda r:r['original'].update(header_bytes=65537)),
        'sanitized-copy':('unchanged safe original',lambda r:r['original']['entity_copy'].update(representation='sanitized_utf8',original_byte_check='unavailable')),
        'wrong-hash':('original entity bytes/hash',lambda r:r['original'].update(sha256='0'*64)),
        'forward-gap':('contiguous full write',lambda r:r['forwarding']['segments'][0].update(offset=1)),
        'short-write':('contiguous full write',lambda r:r['forwarding']['segments'][0].update(written=1)),
        'wrong-segment':('forwarded original segment',lambda r:r['forwarding']['segments'][0].update(sha256='0'*64)),
        'flush-missing':('separate actual write and flush',lambda r:r['forwarding']['segments'][0].update(flush_returned=None)),
        'incomplete-forwarding':('complete forwarding',lambda r:r['forwarding'].update(byte_length=1)),
    }
    for name,(predicate,mutate) in mutations.items():
        changed = copy.deepcopy(record)
        mutate(changed)
        try:
            validate_original_member(changed)
        except ValueError as exc:
            assert predicate in str(exc), (name,str(exc))
            results['A03-A08-A13-'+name] = str(exc)
        else:
            raise AssertionError('unsafe original accepted: '+name)
    original = dict(body=expected,sha256=digest,bytes=len(raw))
    measured = dict(original,browser_copy='measured-browser')
    require_browser_copy(measured,original)
    for origin in ('UNAVAILABLE','relay-original','sanitized_utf8',None):
        try:
            require_browser_copy(dict(measured,browser_copy=origin),original)
        except ValueError as exc:
            assert 'independent browser copy unavailable' in str(exc)
            results['Q1-'+str(origin)] = str(exc)
        else:
            raise AssertionError('relay self-comparison admitted as browser proof: '+str(origin))
    for key,value in (('body',{}),('sha256','0'*64),('bytes',True)):
        try:
            require_browser_copy(dict(measured,**{key:value}),original)
        except ValueError as exc:
            assert 'full body/hash/length mismatch' in str(exc)
            results['Q1-measured-'+key] = str(exc)
        else:
            raise AssertionError('independent browser mismatch accepted: '+key)
    # A full-window raw census control uses serialized observations only. This
    # does not manufacture a healthy E05/native fixture or run any transport.
    origin = 'http://127.0.0.1:4321'
    events = [dict(event='request',request_id=0,url=origin+'/orgs/alpha/threads',method='GET',resource_type='document',navigation=True,main_frame=True,unredirected=True,document_request_id=None,pending_documents=[],at=0.),
              dict(event='navigation',url=origin+'/orgs/alpha/threads',new_document=True,document_request_id=0,at=0.5),
              dict(event='request',request_id=1,url=origin+record['target'],method='POST',resource_type='fetch',navigation=False,main_frame=True,unredirected=True,document_request_id=0,pending_documents=[],at=1.),
              dict(event='compose-response',request_id=1,response_id=0,url=origin+record['target'],method='POST',status=200,browser_copy='UNAVAILABLE',body=None,sha256=None,bytes=None,at=6.),
              dict(event='request-finished',request_id=1,url=origin+record['target'],method='POST',at=8.)]
    reference = dict(original,origin='relay-original',relay_id=0,request_id=1,response_id=0)
    observations = dict(capture_inventory=dict(refusals=[],closed=9.,retained_bytes=4096,retained_facts=100,requests=[dict(events[0],target='/orgs/alpha/threads'),dict(events[2],target=record['target'],terminals=[events[4]])]),thread_proxy=[record],thread_originals=[reference],
                        durable=[dict(threads_cases=dict(transport=dict(browser_port=4321)))])
    with tempfile.TemporaryDirectory(prefix='aggregate-original-controls-') as tmp:
        directory = Path(tmp)
        def read(obs,ledger):
            (directory/'e05-browser-events.json').write_text(json.dumps(ledger))
            return validate_e05_originals(obs,directory)
        assert read(observations,events)[1] == reference
        results['A04-full-window-reader'] = 'unique complete original join only; native behavior NOT RUN'
        for name,predicate in (('duplicate-response','unique Response'),('boolean-response','unique Response'),('pending-document','pending document snapshot'),
                               ('wrong-query','unique bijection'),('missing-terminal','native terminal'),
                               ('late-member','every automatic'),('conflicting-window','every automatic')):
            obs,ledger = copy.deepcopy(observations),copy.deepcopy(events)
            if name == 'duplicate-response':
                ledger.insert(4,copy.deepcopy(ledger[3]))
            elif name == 'boolean-response':
                ledger[3]['response_id'] = False
            elif name == 'pending-document':
                ledger[2]['pending_documents'] = [2]
            elif name == 'wrong-query':
                ledger[2]['url'] += '?different=1'
                ledger[3]['url'] = ledger[4]['url'] = ledger[2]['url']
            elif name == 'missing-terminal':
                ledger.pop()
            elif name == 'late-member':
                obs['thread_proxy'][0]['closed'] = 10.
            else:
                obs['thread_proxy'].append(dict(copy.deepcopy(record),relay_id=1))
            if name in {'pending-document','wrong-query','missing-terminal'}:
                # Keep the serialized owner inventory coherent so the control
                # reaches its original document/window/terminal predicate.
                retained = obs['capture_inventory']['requests'][1]
                retained.update(ledger[2],target=ledger[2]['url'][len(origin):])
                if name == 'missing-terminal':
                    retained.pop('terminals')
                else:
                    retained['terminals'] = [ledger[-1]]
            try:
                read(obs,ledger)
            except ValueError as exc:
                assert predicate in str(exc), (name,str(exc))
                results['A05-A06-A09-'+name] = str(exc)
            else:
                raise AssertionError('ambiguous original census accepted: '+name)
        for name,predicate in (('missing-request-inventory','complete original Request inventory'),
                               ('missing-required-response','every required native Request'),
                               ('membership-overflow','closed complete inventory'),
                               ('fact-overflow','reserved byte/fact bounds'),('aggregate-overflow','reserved byte/fact bounds')):
            obs,ledger = copy.deepcopy(observations),copy.deepcopy(events)
            if name == 'missing-request-inventory':
                obs['capture_inventory']['requests'] = []
            elif name == 'missing-required-response':
                issued = dict(ledger[2],request_id=2,url=origin+'/api/v1/orgs/alpha/threads/THR-007',method='GET',at=8.1)
                terminal = dict(event='request-finished',request_id=2,url=issued['url'],method='GET',at=8.2)
                ledger.extend([issued,terminal])
                obs['capture_inventory']['requests'].append(dict(issued,target=issued['url'][len(origin):],terminals=[terminal]))
            elif name == 'fact-overflow':
                obs['capture_inventory']['retained_facts'] = 16385
            elif name == 'aggregate-overflow':
                obs['capture_inventory']['retained_bytes'] = 8*1024*1024+1
            else:
                obs['capture_inventory']['refusals'] = [dict(reason='membership-cap',at=8.5,relay_id=None)]
            try:
                read(obs,ledger)
            except ValueError as exc:
                assert predicate in str(exc), (name,str(exc))
                results['R3-R5-'+name] = str(exc)
            else:
                raise AssertionError('incomplete census accepted: '+name)
        def retain_inventory(obs, ledger):
            obs['capture_inventory']['requests'] = []
            for issued in (e for e in ledger if e['event'] == 'request'):
                retained = dict(issued,target=issued['url'][len(origin):])
                terminals = [e for e in ledger if e['event'] in {'request-finished','request-failed'}
                             and e['request_id'] == issued['request_id']]
                if terminals:
                    retained['terminals'] = terminals
                    if terminals[0]['event'] == 'request-failed':
                        retained['failed'] = terminals[0]['at']
                obs['capture_inventory']['requests'].append(retained)
        document_cases = {
            'future-document':'committed issuing document',
            'superseded-document':'committed issuing document',
            'nonGET-document':'GET document commit',
            'missing-commit':'committed issuing document',
            'mismatched-commit':'GET document commit',
            'same-document-lineage':'same-document retains committed lineage',
            'pending-transition':'unambiguous issuing document',
            'pending-snapshot':'pending document snapshot',
            'missing-frame-fact':'provenance available',
            'non-main-frame':'GET document commit',
            'redirected-document':'GET document commit',
        }
        for name,predicate in document_cases.items():
            obs,ledger = copy.deepcopy(observations),copy.deepcopy(events)
            if name == 'future-document':
                ledger[2]['document_request_id'] = 2
                ledger.append(dict(ledger[0],request_id=2,document_request_id=0,at=8.1))
                ledger.append(dict(ledger[1],document_request_id=2,at=8.2))
            elif name in {'superseded-document','pending-transition'}:
                issued = dict(ledger[0],request_id=1,document_request_id=0,at=0.6)
                # Request IDs remain contiguous in chronological issuance order.
                for event in ledger[2:]:
                    event['request_id'] = 2
                obs['thread_originals'][0]['request_id'] = 2
                ledger[2:2] = [issued]
                if name == 'superseded-document':
                    ledger.insert(3,dict(ledger[1],document_request_id=1,at=0.7))
                else:
                    ledger[3]['pending_documents'] = [1]
            elif name == 'nonGET-document':
                ledger[0]['method'] = 'PUT'
            elif name == 'missing-commit':
                ledger.pop(1)
                ledger[1]['pending_documents'] = [0]
            elif name == 'mismatched-commit':
                ledger[1]['url'] += '/wrong'
            elif name == 'same-document-lineage':
                ledger.insert(2,dict(ledger[1],new_document=False,document_request_id=99,at=0.7))
            elif name == 'pending-snapshot':
                ledger[2]['pending_documents'] = [99]
            elif name == 'missing-frame-fact':
                ledger[0].pop('main_frame')
            elif name == 'non-main-frame':
                ledger[0]['main_frame'] = False
            else:
                ledger[0]['unredirected'] = False
            retain_inventory(obs,ledger)
            try:
                read(obs,ledger)
            except ValueError as exc:
                assert predicate in str(exc), (name,str(exc))
                results['F3-document-'+name] = str(exc)
            else:
                raise AssertionError('unsafe committed-document census accepted: '+name)
        same_document = copy.deepcopy(events)
        same_document.insert(2,dict(events[1],new_document=False,at=0.75))
        assert read(observations,same_document)[1] == reference
        results['F3-same-document-positive'] = 'serialized original committed lineage retained'
        # Exercise compose AND thread Responses at this real reader boundary.
        for kind in ('compose','thread'):
            obs,ledger = copy.deepcopy(observations),copy.deepcopy(events)
            if kind == 'thread':
                for event in ledger[2:]:
                    event['method'] = 'GET'
                    event['url'] = origin+'/api/v1/orgs/alpha/threads/THR-007'
                ledger[3]['event'] = 'thread-response'
                wire = obs['thread_proxy'][0]
                wire.update(method='GET',target='/api/v1/orgs/alpha/threads/THR-007',
                            path='/api/v1/orgs/alpha/threads/THR-007',ordinal=None)
                wire['original']['required_kind'] = 'detail'
            retain_inventory(obs,ledger)
            assert read(obs,ledger)[1] == reference
            results['F3-'+kind+'-response-positive'] = 'serialized committed-document/Response interval only'
            for name,at in (('before-request',0.75),('after-terminal',8.5)):
                changed = copy.deepcopy(ledger)
                changed[3]['at'] = at
                changed.sort(key=lambda e:e['at'])
                try:
                    read(obs,changed)
                except ValueError as exc:
                    assert 'Request <= Response <= successful terminal interval' in str(exc), (kind,name,str(exc))
                    results['F3-'+kind+'-'+name] = str(exc)
                else:
                    raise AssertionError('Response outside its original interval accepted: '+kind+' '+name)
        assert read(observations,events)[1] == reference
    # Serialized body-independent owner grammar at the SAME reader boundary.
    # These literal facts establish reader sensitivity only; no native owner is
    # constructed, and these inputs are never Product/native closure receipts.
    header = dict(status=200,transfer_encoding=[],content_length=['0'],content_type=['text/css'],chunked=False,length=0)
    checkpoint = dict(kind='between',received=0,delivered=0,chunk_left=None,delimiter=None,remaining=None)
    names = ['connection','original','duplicate','wake_read','wake_write','selector','raw','buffer','file','response']
    acquired = [2.0,2.1,2.2,2.3,2.3,2.5,2.6,2.7,2.8,3.2]
    owner = dict(runtime=dict(implementation='cpython',version=[3,14,4]),generation=1,end=10.,
        resources=[dict(name=n,acquired=t,closed=4.5) for n,t in zip(names,acquired)],
        registrations=[dict(name=n,registered=2.55,unregistered=4.3) for n in ('duplicate','wake_read')],
        trace=[dict(event='checkpoint',at=2.9,kind='before-response',received=0,delivered=0,chunk_left=None,delimiter=None,remaining=None),
               dict(event='checkpoint',at=3.6,**checkpoint)],
        operations=[dict(id=1,kind='getresponse',read_id=None,started=3.,finished=3.5,outcome='returned',result=header)],
        received=0,delivered=0,forced=None,terminal=None,mode=dict(original_blocking=False,duplicate_blocking=False,at=2.4),errors=[],finished=4.6)
    request = dict(event='request',request_id=0,url=origin+'/asset.css',method='GET',resource_type='stylesheet',navigation=False,main_frame=True,unredirected=True,document_request_id=0,pending_documents=[],at=0.)
    proxy = dict(relay_id=0,method='GET',path='/asset.css',target='/asset.css',ordinal=None,entered=1.,forwarded=2.,closed=5.,delivered=4.7,
                 browser_request_id=0,owner_generation=1,io_owner=owner,response_outcome=header,status=200,streaming=False,upstream_operation=owner['operations'][0])
    lifecycle = [dict(event='context-closing',request_ids=[],at=7.),dict(event='proxy-streams-shutdown',streams=[],at=7.1),
                 dict(event='context-close',at=7.2),dict(event='context-closed',at=7.3)]
    ledger = [request,dict(event='request-finished',request_id=0,url=request['url'],method='GET',at=6.),*lifecycle]
    obs = dict(thread_proxy=[proxy],durable=[dict(threads_cases=dict(transport=dict(browser_port=4321),entered=0.,cleanup_finished=9.)),
        dict(thread_proxy_closure=dict(port=4321,entered=8.,finished=8.5,workers_alive=0,listener_alive=False,connections=0,errors=[]))])
    validate_e05_safety(obs,ledger)
    results['R1-serialized-GET-owner'] = 'complete original-owner grammar accepted; native closure NOT RUN'
    stopped = copy.deepcopy(obs)
    r = stopped['thread_proxy'][0]
    o = r['io_owner']
    abort = dict(event='request-failed',request_id=0,url=request['url'],method='GET',failure='net::ERR_ABORTED',at=3.8)
    o['operations'].append(dict(id=2,kind='stop',read_id=None,started=4.,finished=4.2,outcome='cancelled'))
    r.update(error='Stopped',failure_operation='upstream',failure_at=4.4,browser_abort=dict(abort,target=r['target']),stop_settled=5.1)
    del r['delivered']
    r['upstream_operation'] = o['operations'][-1]
    r['stop_intent'] = dict(kind='navigation',request_id=0,method='GET',target=r['target'],generation=1,event=r['browser_abort'],operation_id=1,enqueued=3.9,end=10.)
    r['stop_ack'] = dict(generation=1,request_id=0,operation_id=2,previous_operation_id=1,read_id=None,previous_read_id=None,
        checkpoint=checkpoint,received=0,delivered=0,buffered=0,since_checkpoint=0,would_block=False,started=4.,finished=4.1,outcome='already-complete')
    stop_ledger = [request,abort,*lifecycle]
    validate_e05_safety(stopped,stop_ledger)
    results['R1-serialized-controlled-stop'] = 'original checkpoint grammar accepted; native cancellation NOT RUN'
    for name,predicate,base,source_events in (
        ('missing-owner','required original GET owner',obs,ledger),('empty-resources','every original/duplicate',obs,ledger),
        ('empty-registrations','selector registrations',obs,ledger),('wrong-generation','positive GET owner',obs,ledger),
        ('wrong-read-accounting','full raw/buffer accounting',obs,ledger),
        ('duplicate-generation','positive GET owner generation',obs,ledger),
        ('stale-stop','acknowledgement matches actual last',stopped,stop_ledger),
        ('buffered-stop','empty entire buffered checkpoint',stopped,stop_ledger),
        ('unsafe-browser-event','closed original browser event grammar',obs,ledger)):
        changed,changed_events = copy.deepcopy(base),copy.deepcopy(source_events)
        r = changed['thread_proxy'][0]
        if name == 'missing-owner': del r['io_owner']
        elif name == 'empty-resources': r['io_owner']['resources'] = []
        elif name == 'empty-registrations': r['io_owner']['registrations'] = []
        elif name == 'wrong-generation': r['owner_generation'] = 2
        elif name == 'wrong-read-accounting': r['io_owner']['received'] = 1
        elif name == 'duplicate-generation':
            duplicate = copy.deepcopy(r)
            duplicate.update(relay_id=1,browser_request_id=1)
            changed['thread_proxy'].append(duplicate)
            changed_events.insert(1,dict(request,request_id=1,at=0.1))
            changed_events.insert(3,dict(ledger[1],request_id=1,at=6.1))
        elif name == 'stale-stop': r['stop_ack']['checkpoint']['kind'] = 'finite'
        elif name == 'buffered-stop': r['stop_ack']['buffered'] = 1
        else: changed_events.insert(-1,dict(event='origin-bypass',url='http://elsewhere/',method='GET',resource_type='fetch',at=7.25))
        try:
            validate_e05_safety(changed,changed_events)
        except ValueError as exc:
            assert predicate in str(exc), (name,str(exc))
            results['R1-R3-'+name] = str(exc)
        else:
            raise AssertionError('unsafe serialized owner accepted: '+name)
    # A pre-E05 checkpoint owns no C/N/D command or closure. Counts cannot
    # manufacture receipts even at this empty selected-operation boundary.
    prefix = dict(starts=[dict(session=str(i)) for i in range(29)],witnesses=[dict(kind='stub') for _ in range(29)],command_count=0,thread_frame={})
    before = dict(durable=[dict(threads_cases={})],thread_commands=[],thread_attempts=[],provider_commands=[],launch_witnesses=prefix['witnesses'])
    validate_e05_operations({},before,prefix,[])
    before['thread_commands'] = [{}]
    try:
        validate_e05_operations({},before,prefix,[])
    except ValueError as exc:
        assert 'finite eleven provider CLI actions' in str(exc)
        results['R2-pre-E05-no-invented-command'] = str(exc)
    else:
        raise AssertionError('pre-E05 invented command accepted')
    groups = {name:dict(state='reported_pass',begin=1.,end=2.,first_error_ref=None,
              variants=MANIFEST['variants'][start:end],checkpoint_ref=None,admission_ref=None)
              for name,start,end in (('E05',48,53),('E07',53,56))}
    group_result = dict(groups=groups)
    group_observations = dict(groups=copy.deepcopy(groups),failures=[])
    group_result['groups']['E05']['state'] = 'invalid'
    validate_groups(group_result,group_observations,'E07')
    try:
        validate_groups(group_result,group_observations)
    except ValueError as exc:
        assert 'group export equality' in str(exc)
        results['B09-E05-metadata-isolation'] = 'E07 own metadata valid; global E05 conflict refused, independent content still required'
    else:
        raise AssertionError('invalid E05 metadata promoted globally')
    group_result['groups']['E07']['variants'] = []
    try:
        validate_groups(group_result,group_observations,'E07')
    except ValueError as exc:
        assert 'selected group export equality' in str(exc)
        results['B09-E07-own-metadata-required'] = str(exc)
    else:
        raise AssertionError('invalid own E07 metadata accepted')
    # Serialized attribution inputs exercise the SAME shipping predicate called
    # by validate_e07. They are not manufactured complete E07/native receipts.
    owner = dict(state='unknown',begin=1.,end=5.,first_error_ref=0,
        variants=MANIFEST['variants'][48:53],checkpoint_ref=None,admission_ref=None)
    later = dict(state='reported_pass',begin=6.,end=9.,first_error_ref=None,
        variants=MANIFEST['variants'][53:],checkpoint_ref='D',admission_ref='e07_admission')
    caught = dict(site='threads.exercise',error_ref=0,caught=3.2,ordinary=True,primary=True)
    e05 = dict(entered=2.,failure=caught,cleanup_finished=4.,cleanup_deadline=4.5,
        case_outcomes={v:dict(status='UNKNOWN',error_ref=None) for v in MANIFEST['variants'][48:53]})
    observed = dict(groups=dict(E05=owner,E07=later),observer_error=None,
        failures=[dict(stage='controller-action',error='ValueError',at=3.1)],
        durable=[dict(threads_cases=e05)],e07=dict(entered=7.,finished=8.),
        e07_admission=dict(entered=6.1,finished=6.3),
        finalization=dict(entered=10.,finished=12.,attempts=[]))
    archived = dict(groups=copy.deepcopy(observed['groups']),failures=copy.deepcopy(observed['failures']),
        durable=copy.deepcopy(observed['durable']),observer_state=dict(cutoff=6.2))
    archived['groups']['E07'].update(state='running',end=None,checkpoint_ref=None,admission_ref=None)
    def attribution_input(earlier: bool, final: bool) -> tuple[dict,dict]:
        o,a = copy.deepcopy(observed),copy.deepcopy(archived)
        if not earlier:
            o['failures'],a['failures'] = [],[]
            o['groups']['E05'].update(state='reported_pass',first_error_ref=None)
            a['groups']['E05'] = copy.deepcopy(o['groups']['E05'])
            del o['durable'][0]['threads_cases']['failure']
            a['durable'] = copy.deepcopy(o['durable'])
        if final:
            ref = len(o['failures'])
            o['failures'].extend([dict(stage='bounded',error='OSError',at=11.4),
                                  dict(stage='daemon-log-close',error='OSError',at=11.5)])
            o['finalization']['attempts'] = [dict(owner='Controller.close',operation='daemon-log-close',
                entered=11.,finished=11.8,failure_refs=[ref,ref+1],same_cause=True,ordinary=True,main_thread=True)]
        return o,a
    for name,earlier,final in (('healthy-no-errors',False,False),('E05-unknown-first-cause',True,False),
                              ('final-log-only',False,True),('E05-plus-final-log',True,True)):
        o,a = attribution_input(earlier,final)
        try:
            validate_e07_failures(copy.deepcopy(o),o,a,[])
        except (ValueError,KeyError,TypeError) as exc:
            raise AssertionError('valid E07 failure attribution rejected: '+name) from exc
        results['B09-B11-attribution-'+name] = 'attribution predicate only; full native/content/completion gates still required'
    o,a = attribution_input(True,False)
    o['groups']['E05']['state'] = a['groups']['E05']['state'] = 'failed'
    validate_e07_failures(copy.deepcopy(o),o,a,[])
    results['B09-attribution-E05-failed-first-cause'] = 'original failed E05 retained'
    counterexamples = {
        'missing-catch':('original E05 catch export',lambda o,a:o['durable'][0]['threads_cases'].pop('failure')),
        'foreign-ref':('original ordinary E05 first cause',lambda o,a:[d[0]['threads_cases']['failure'].update(error_ref=1) for d in (o['durable'],a['durable'])]),
        'false-primary':('original ordinary E05 first cause',lambda o,a:[d[0]['threads_cases']['failure'].update(primary=False) for d in (o['durable'],a['durable'])]),
        'nonordinary':('original ordinary E05 first cause',lambda o,a:[d[0]['threads_cases']['failure'].update(ordinary=False) for d in (o['durable'],a['durable'])]),
        'unknown-stage':('original ordinary E05 first cause',lambda o,a:[f[0].update(stage='unknown') for f in (o['failures'],a['failures'])]),
        'prefix-drift':('original failure prefix',lambda o,a:a['failures'][0].update(error='AssertionError')),
        'nonfinite':('closed finite original failure',lambda o,a:[f[0].update(at=float('inf')) for f in (o['failures'],a['failures'])]),
        'overlap':('E05 catch and cleanup before E07',lambda o,a:[g['E05'].update(end=6.5) for g in (o['groups'],a['groups'])]),
        'reversed-admission':('original admission, prefix',lambda o,a:o['e07_admission'].update(finished=6.15)),
        'E07-error':('unattributed or E07 failure',lambda o,a:o['failures'].append(dict(stage='controller-action',error='AssertionError',at=7.5))),
        'E07-group-error':('original running E07',lambda o,a:o['groups']['E07'].update(first_error_ref=0)),
        'bad-final-ref':('final failure reference',lambda o,a:o['finalization']['attempts'][0].update(failure_refs=[99])),
        'mixed-final-cause':('source-owned final attempt interval',lambda o,a:o['finalization']['attempts'][0].update(same_cause=False)),
        'required-resource-error':('unrelated final log descriptor',lambda o,a:o['finalization']['attempts'][0].update(operation='thread-proxy-close')),
        'required-export-error':('source-owned final attempt interval',lambda o,a:o['finalization']['attempts'][0].update(operation='export-e07')),
        'early-final':('original finalization export',lambda o,a:o['finalization'].update(entered=7.)),
        'final-time-drift':('unrelated final log descriptor',lambda o,a:o['failures'][-1].update(at=11.9)),
        'reversed-failures':('ordered original failures',lambda o,a:o['failures'][-1].update(at=11.3)),
        'interrupted-final':('unrelated final log descriptor',lambda o,a:o['finalization']['attempts'][0].update(ordinary=False)),
        'observer-error':('complete browser stream',lambda o,a:o.update(observer_error='unavailable')),
    }
    final_names = {'bad-final-ref','mixed-final-cause','required-resource-error','required-export-error',
                   'early-final','final-time-drift','reversed-failures','interrupted-final'}
    for name,(reason,change) in counterexamples.items():
        o,a = attribution_input(True,name in final_names)
        change(o,a)
        try:
            validate_e07_failures(copy.deepcopy(o),o,a,[])
        except ValueError as exc:
            assert reason in str(exc), (name,str(exc))
            results['B04-B08-B09-attribution-'+name] = str(exc)
        else:
            raise AssertionError('unsafe E07 failure attribution accepted: '+name)
    o,a = attribution_input(True,False)
    try:
        validate_e07_failures(copy.deepcopy(o),o,a,None)
    except ValueError as exc:
        assert 'complete browser stream' in str(exc)
        results['B09-attribution-missing-browser-stream'] = str(exc)
    else:
        raise AssertionError('missing browser stream accepted')
    return results



def completed_controls() -> dict:
    """Extend the existing reader controls; no Product/daemon execution."""
    import tempfile
    from controller import Controller
    results = {}
    with tempfile.TemporaryDirectory(prefix="e2e-completed-") as temporary:
        root = Path(temporary)
        (root / "coord").mkdir(mode=0o700)
        controller = Controller.__new__(Controller)
        controller.root = root
        controller.results = {}
        controller.completed_identity = dict(source_sha="1" * 40, source_tree="2" * 40,
            run_id="123", run_attempt="1", python="3.12", exercise="baseline")
        controller.completed = dict(schema=1, identity=controller.completed_identity, cases=[])
        controller.completed_files = {}
        controller.http, controller.commands, controller.starts, controller.durable = [], [], [], []
        # Actual publisher accepts only closed fields, even if future-secret raw
        # content is already present in the producer's original result evidence.
        secret = "future-secret-body-must-never-be-published"
        controller.mark(MANIFEST["variants"][0], dict(raw_body=secret))
        original_result = copy.deepcopy(controller.results)
        accepted, bundle = completed.read_bundle(root / "coord", controller.completed_identity)
        assert len(accepted["cases"]) == 1 and all(secret.encode() not in data for data in bundle.values())
        try:
            controller.mark(MANIFEST["variants"][0])
        except AssertionError:
            results["completed-duplicate"] = "refused"
        else:
            raise AssertionError("duplicate completed case accepted")
        # Later arbitrary evidence/export/cleanup failure does not touch the
        # separately committed prefix. It also supplies no independent PASS.
        controller.results.clear()
        again, after = completed.read_bundle(root / "coord", controller.completed_identity)
        assert again == accepted and after == bundle
        for name, data in bundle.items():
            completed.publish(root, name, data)
        copied, _ = completed.read_bundle(root, controller.completed_identity)
        assert copied == accepted
        files = {name: data for name, data in bundle.items() if name != "completed-cases.json"}
        for case in ("duplicate", "source_sha", "source_tree", "run_id", "run_attempt", "python", "exercise",
                     "variant", "sequence", "manifest_index", "unsafe-path", "missing-evidence", "raw-field", "digest"):
            value, evidence = copy.deepcopy(accepted), dict(files)
            if case == "duplicate":
                value["cases"].append(copy.deepcopy(value["cases"][0]))
            elif case in completed.IDENTITY:
                value["identity"][case] = dict(source_sha="3" * 40, source_tree="4" * 40,
                    run_id="124", run_attempt="2", python="3.14", exercise="cancellation")[case]
                # Coherently wrong artifact identity must still refuse the
                # independently expected source/run/lane, not another guard.
                body = completed.decode(evidence["case-01.json"])
                body["identity"] = dict(value["identity"])
                evidence["case-01.json"] = completed.encode(body)
                value["cases"][0]["evidence"].update(bytes=len(evidence["case-01.json"]),
                    sha256=hashlib.sha256(evidence["case-01.json"]).hexdigest())
            elif case in ("variant", "sequence", "manifest_index"):
                value["cases"][0][case] = MANIFEST["variants"][1] if case == "variant" else 2
            elif case == "unsafe-path":
                value["cases"][0]["evidence"]["path"] = "../raw/scenarios.txt"
            elif case == "missing-evidence":
                evidence.clear()
            elif case == "digest":
                value["cases"][0]["evidence"]["sha256"] = "0" * 64
            else:
                value["cases"][0]["raw_log"] = secret
            try:
                completed.validate(value, controller.completed_identity, evidence)
            except (ValueError, KeyError, TypeError):
                results["completed-" + case] = "refused"
            else:
                raise AssertionError("unsafe completed case accepted: " + case)
        try:
            completed.join_results(accepted, dict(controller.completed_identity,
                completed_cases=accepted, variants=controller.results))
        except ValueError:
            results["completed-prefix-only"] = "UNKNOWN; no complete original result/content proof"
        else:
            raise AssertionError("completion marker alone accepted as full results")
        # Continue the actual producer through all original manifest identities.
        # This checks the ledger bound/join, never the Product case assertions.
        controller.results = original_result
        for variant in MANIFEST["variants"][1:]:
            controller.mark(variant)
        full, _ = completed.read_bundle(root / "coord", controller.completed_identity)
        completed.join_results(full, dict(controller.completed_identity,
            completed_cases=full, variants=controller.results))
        assert len(full["cases"]) == 56
        results["completed-all56-consistency"] = "accepted metadata only; full content oracles remain required"
        try:
            completed.publish(root / "coord", "case-01.json", b"{}")
        except ValueError:
            results["completed-conflicting-publication"] = "refused"
        else:
            raise AssertionError("conflicting completed evidence overwritten")
        try:
            completed.decode(b'{"schema":1,"schema":1}')
        except ValueError:
            results["completed-duplicate-json-field"] = "refused"
        else:
            raise AssertionError("duplicate completed JSON field accepted")
        for name in ("case-02.json", "completed-cases.json"):
            target = root / "coord" / name
            if target.exists():
                target.unlink()  # Disposable synthetic fixture only.
            target.symlink_to(root / "outside")
            try:
                completed.publish(root / "coord", name, b"{}")
            except (OSError, ValueError):
                results["completed-symlink-" + name] = "refused"
            else:
                raise AssertionError("unsafe completed path accepted")
        assert not (root / "outside").exists()
        # Actual existing completion publisher, with genuine sparse manifest
        # indices. These are consistency controls, never case/native acceptance.
        sparse = root/'sparse'
        (sparse/'coord').mkdir(parents=True,mode=0o700)
        controller.root = sparse
        controller.results = {}
        controller.completed = dict(schema=1,identity=controller.completed_identity,cases=[])
        controller.completed_files = {}
        for variant in MANIFEST['variants'][:48]+MANIFEST['variants'][53:]:
            controller.mark(variant)
        ledger,_ = completed.read_bundle(sparse/'coord',controller.completed_identity)
        result = dict(controller.completed_identity,completed_cases=ledger,variants=controller.results)
        scoped_completed_join(ledger,result,MANIFEST['variants'][53:])
        assert len(ledger['cases']) == 51 and ledger['cases'][48]['manifest_index'] == 53
        results['B09-B11-sparse-scoped-completion'] = 'E07 consistency join accepted; independent content still required'
        for label,action,predicate in (
            ('missing-E05',lambda:scoped_completed_join(ledger,result,MANIFEST['variants'][48:53]),'scoped completed cases unavailable'),
            ('whole56',lambda:completed.join_results(ledger,result),'complete case/result join unavailable'),
        ):
            try:
                action()
            except ValueError as exc:
                assert predicate in str(exc)
                results['B09-B11-'+label] = str(exc)
            else:
                raise AssertionError('sparse completion falsely admitted: '+label)
        changed = copy.deepcopy(result)
        changed['variants'][MANIFEST['variants'][53]]['at'] += 1
        try:
            scoped_completed_join(ledger,changed,MANIFEST['variants'][53:])
        except ValueError as exc:
            assert 'scoped completed original result conflict' in str(exc)
            results['B09-scoped-time-conflict'] = str(exc)
        else:
            raise AssertionError('scoped original result conflict accepted')
    return results


def admission_fixture(event: str) -> dict:
    """Synthetic identity only; never an execution receipt."""
    return dict(event=event, cron="0 19 * * *" if event == "schedule" else None,
                repository="t-benze/happyranch",
                workflow_ref="t-benze/happyranch/.github/workflows/e2e.yml@refs/heads/main",
                workflow_sha="1" * 40, run_id="123", run_attempt="1",
                source_ref="refs/heads/main", source_sha="1" * 40, source_tree="2" * 40)


def admission_controls() -> dict:
    """Call only shipping admission/receipt predicates, with child launch forbidden.

    Actions-shaped payloads/env and git outputs below are isolated synthetic
    inputs, not authentic Actions evidence or permission to execute scenarios.
    """
    import tempfile
    from unittest.mock import patch

    results = {}
    with tempfile.TemporaryDirectory(prefix="e2e-admission-control-") as scratch:
        payload = Path(scratch) / "event.json"
        for event_name in ("schedule", "workflow_dispatch"):
            expected = admission_fixture(event_name)
            event = dict(repository=dict(full_name="t-benze/happyranch", default_branch="main"))
            if event_name == "schedule":
                event["schedule"] = "0 19 * * *"
            else:
                event.update(ref="refs/heads/main", inputs={})
            env = dict(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=event_name,
                       GITHUB_EVENT_PATH=str(payload), GITHUB_REPOSITORY="t-benze/happyranch",
                       GITHUB_REF="refs/heads/main", GITHUB_SHA="1" * 40,
                       GITHUB_WORKFLOW_SHA="1" * 40, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
                       GITHUB_WORKFLOW_REF="t-benze/happyranch/.github/workflows/e2e.yml@refs/heads/main")

            def admit(environment: dict, body: dict, head: str = "1" * 40, tree: str = "2" * 40) -> dict:
                payload.write_text(json.dumps(body))

                def git_read(command: list, **kwargs: object) -> str:
                    if kwargs != dict(cwd=Path(scratch), text=True):
                        raise AssertionError("unexpected git cwd/options")
                    if command == ["git", "rev-parse", "HEAD"]:
                        return head + "\n"
                    if command == ["git", "rev-parse", "HEAD^{tree}"]:
                        return tree + "\n"
                    raise AssertionError("non-admission command prohibited")

                with patch.dict(os.environ, environment, clear=True), \
                        patch("run.subprocess.check_output", side_effect=git_read), \
                        patch("subprocess.Popen", side_effect=AssertionError("child launch prohibited")):
                    return daily_admission(Path(scratch))

            if admit(env, event) != expected:
                raise AssertionError("wrong admitted event/run/source identity")
            results["admission-" + event_name] = "synthetic identity admitted; no child launch"
            cases = []
            for key in env:
                missing = dict(env)
                missing.pop(key)
                cases.append(("missing-" + key, missing, event, "1" * 40, "2" * 40))
            for key, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_REF", "refs/heads/other"),
                               ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_SHA", "bad"),
                               ("GITHUB_WORKFLOW_SHA", "3" * 40), ("GITHUB_RUN_ID", "0"),
                               ("GITHUB_RUN_ATTEMPT", "-1"), ("GITHUB_RUN_ID", "１２３"),
                               ("GITHUB_WORKFLOW_REF", "t-benze/happyranch/.github/workflows/ci.yml@refs/heads/main")):
                cases.append(("wrong-" + key + "-" + value, dict(env, **{key: value}), event, "1" * 40, "2" * 40))
            for unsupported in ("pull_request", "push", "release", "workflow_call", "repository_dispatch", "unknown"):
                cases.append((unsupported, dict(env, GITHUB_EVENT_NAME=unsupported), event, "1" * 40, "2" * 40))
            cases.extend([("checkout-mismatch", env, event, "3" * 40, "2" * 40),
                          ("invalid-tree", env, event, "1" * 40, "bad"),
                          ("missing-repository", env, {}, "1" * 40, "2" * 40),
                          ("repository-mismatch", env, dict(event, repository=dict(full_name="other/repo", default_branch="main")), "1" * 40, "2" * 40),
                          ("default-mismatch", env, dict(event, repository=dict(full_name="t-benze/happyranch", default_branch="other")), "1" * 40, "2" * 40)])
            if event_name == "schedule":
                missing = dict(event)
                missing.pop("schedule")
                cases.extend([("missing-cron", env, missing, "1" * 40, "2" * 40),
                              ("wrong-cron", env, dict(event, schedule="0 7 * * *"), "1" * 40, "2" * 40)])
            else:
                missing = dict(event)
                missing.pop("ref")
                cases.extend([("missing-payload-ref", env, missing, "1" * 40, "2" * 40),
                              ("non-main-payload", env, dict(event, ref="refs/heads/other"), "1" * 40, "2" * 40),
                              ("invented-schedule", env, dict(event, schedule="0 19 * * *"), "1" * 40, "2" * 40)])
            for name, environment, body, head, tree in cases:
                try:
                    admit(environment, body, head, tree)
                except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                    results["admission-" + event_name + "-" + name] = type(exc).__name__ + ": " + str(exc)
                else:
                    raise AssertionError("admission incorrectly accepted " + name)
            validate_admission(expected, expected)
            for key in expected:
                for missing in (False, True):
                    row = dict(expected)
                    if missing:
                        row.pop(key)
                    else:
                        row[key] = "conflicting-identity"
                    try:
                        validate_admission(row, expected)
                    except ValueError as exc:
                        results[f"receipt-{event_name}-{key}-{'missing' if missing else 'conflict'}"] = str(exc)
                    else:
                        raise AssertionError("receipt incorrectly accepted " + key)
    return results


def cancellation_controls() -> dict:
    """Synthetic refusal controls; these never count as actual signal evidence."""
    source = "1" * 40
    phases = [dict(name=n, exit=0, entered=0, finished=1, deadline=240, cap_seconds=240, allowance_seconds=240)
              for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build")]
    phases.append(dict(name="scenarios", exit=None, interrupted=True,
                       entered=1, finished=3, deadline=241, cap_seconds=240, allowance_seconds=240))
    timing = dict(started=0, action_deadline=720, hard_deadline=900,
                  action=dict(finished=3, deadline=720, allowance_seconds=720, elapsed_seconds=3),
                  cleanup=dict(entered=3, finished=4, deadline=63, allowance_seconds=60, elapsed_seconds=1, ok=True),
                  export=dict(entered=4, finished=5, deadline=124, allowance_seconds=120, elapsed_seconds=1, ok=True))
    fixture = dict(source_sha=source, launcher_exit=1, launcher_absent=True,
                   identities_live_at_signal=True, total_seconds=10,
                   sent=dict(signal=15, at=2, pid=10, start="100"),
                   active=dict(source_sha=source, at=1, launcher=dict(pid=10, start="100"),
                               provider=dict(pid=11, pid_start="101"), browser_started=True,
                               callback=dict(status=200, response={"ok": True}, dropped=False), ports=[1234, 1235],
                               owned=dict(identities={"11": "101"}, units=["happyranch-example.service"], groups=["/example"])),
                   launcher_result=dict(source_sha=source, python="3.12", exercise="cancellation", status="FAIL", skipped=0,
                                        signals=[dict(signal=15, at=3, pid=10, start="100")], total_seconds=5, evidence_seconds=1, phases=phases, timing=timing,
                                        cleanup=dict(ok=True, data_absent=True, sockets_absent=True, watcher_stopped=True,
                                                     survivors={}, errors=[], identities={"11": "101"},
                                                     units={"happyranch-example.service": {"stdout": "LoadState=not-found"}},
                                                     groups={"/example": False}, ports_absent={"1234": True, "1235": True})))
    admission = admission_fixture("schedule")
    fixture.update(admission)
    fixture["launcher_result"].update(admission)
    validate_cancellation(fixture, source, "3.12", 15)
    results = {}
    for name in ("no-delivery", "wrong-signal", "false-success", "not-active", "missing-identity", "missing-unit", "live-cgroup", "missing-port", "data-residue", "setup-failure", "wrong-event", "wrong-run", "wrong-tree", "missing-admission"):
        row = copy.deepcopy(fixture)
        result = row["launcher_result"]
        if name == "no-delivery": result["signals"] = []
        elif name == "wrong-signal": result["signals"][0]["signal"] = 2
        elif name == "false-success": row["launcher_exit"] = 0
        elif name == "not-active": row["identities_live_at_signal"] = False
        elif name == "missing-identity": result["cleanup"]["identities"] = {}
        elif name == "missing-unit": result["cleanup"]["units"] = {}
        elif name == "live-cgroup": result["cleanup"]["groups"]["/example"] = True
        elif name == "missing-port": result["cleanup"]["ports_absent"] = {}
        elif name == "data-residue": result["cleanup"]["data_absent"] = False
        elif name == "setup-failure": result["phases"][0]["exit"] = 1
        elif name == "wrong-event": result["event"] = "workflow_dispatch"
        elif name == "wrong-run": result["run_id"] = "456"
        elif name == "wrong-tree": result["source_tree"] = "3" * 40
        elif name == "missing-admission": result.pop("cron")
        try:
            validate_cancellation(row, source, "3.12", 15)
        except ValueError as exc:
            results["cancellation-" + name] = str(exc)
        else:
            raise AssertionError(f"cancellation validator accepted {name}")
    manual = admission_fixture("workflow_dispatch")
    fixture.update(manual)
    fixture["launcher_result"].update(manual)
    validate_cancellation(fixture, source, "3.12", 15)
    results["cancellation-manual-identity"] = "synthetic wrapper/inner identity matched"
    return results


def validate_review_refusal(row: dict, observations: dict, directory: Path, record: dict, positive: dict) -> None:
    """Independently reconstruct the two refusals and original-SID continuation."""
    import hashlib

    def check(ok: bool, label: str) -> None:
        if not ok:
            raise ValueError("E04 refusal content: " + label)

    start, = record["starts"]
    task, refusal = record["task"], record["refusal"]
    check(start == observations["starts"][20] and start["task"] == task and start["org"] == "alpha"
          and start["agent"] == "case_manager" and start["pid_start"] is not None and record["jobs"] == [], "one actual alpha provider/zero jobs")
    commands = observations["provider_commands"][record["commands_begin"]:record["commands_end"]]
    check([cmd["action"] for cmd in commands] == ["jobs-refuse-review", "jobs-final"]
          and commands == [refusal["cli"], record["final_callback"]]
          and [cmd for cmd in observations["provider_commands"] if cmd["provider"]["session"] == start["session"]] == commands,
          "closed first-refusal/final-only protocol")
    command, final = commands
    check(not any(c.get("provider", {}).get("session") == start["session"] for c in observations["thread_commands"] + observations["e07_commands"]), "no mixed-mode commands")
    manifest = next(d["executable_manifest"] for d in observations["durable"] if "executable_manifest" in d)
    root = Path(manifest["root"])
    check(command["argv"][:6] == [manifest["callback"], "jobs", "submit", "--org", "alpha", "--from-file"]
          and len(command["argv"]) == 8 and command["argv"][-1] == "--json", "real shipping submit argv")
    file = command["payload_file"]
    raw = file["text"].encode("utf-8")
    payload = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    check(file == dict(path=str(root/"coord"/(digest+".json")), text=raw.decode("utf-8"), bytes=len(raw), sha256=digest)
          and file["path"] == command["argv"][-2] and command["payload"] == payload
          and raw == json.dumps(payload,sort_keys=True).encode("utf-8"), "actual UTF8 payload file entity/path/hash")
    token = hashlib.sha256((row["source_sha"]+start["session"]+"REFUSED").encode()).hexdigest()[:24]
    sentinel, socket_path = str(root/"coord"/(token+".started")), str(root/"coord/jobs.sock")
    # The program is independently authored here; no scenario oracle is imported.
    program = f'''import json, os, signal, socket, sys, time
from pathlib import Path
Path({sentinel!r}).write_text(json.dumps(dict(token={token!r}, pid=os.getpid())))
sys.stdout.write('E04_REFUSED_OUT\\n'); sys.stdout.flush()
sys.stderr.write('E04_REFUSED_ERR\\n'); sys.stderr.flush()
end = {refusal["effective_end"]!r}
def remaining():
    value = end - time.monotonic()
    if value <= 0:
        raise TimeoutError("E04 script absolute deadline")
    return min(20, value)
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(remaining())
    peer.connect({socket_path!r})
    peer.settimeout(remaining())
    peer.sendall((json.dumps(dict(token={token!r}, pid=os.getpid())) + "\\n").encode())
    remaining()
    if False:
        while True:
            signal.pause()
    peer.settimeout(remaining())
    released = peer.recv(1)
    remaining()
    if released != b"R":
        raise RuntimeError("missing external release")
raise SystemExit(0)
'''
    check(record["entered"] < refusal["effective_end"] <= record["deadline"] and refusal["token"] == token and refusal["sentinel"] == sentinel and refusal["script"] == program
          and payload == dict(task_id=task, session_id=start["session"], title="E04 review refusal", rationale="Authorization mode removed",
              script=program, interpreter="python3", cwd_hint=None, review_required=True, persistent=False, max_runtime_seconds=30)
          and payload["review_required"] is True and payload["persistent"] is False and type(payload["max_runtime_seconds"]) is int,
          "independent valid sentinel-first bounded true payload")
    expected_stderr = (f"Error reading scripts-submit file {file['path']}: review_required_removed: obtain any required authorization in a thread before submitting; "
        "upgrade old clients and omit review_required or set it to false. This request was not submitted.\n")
    check(command["exit"] == 2 and command["stdout"] == "" and command["stderr"] == expected_stderr
          and command["timed_out"] is False and command["wire"] == [], "exact local refusal before HTTP")
    attempt, = [a for a in observations["provider_attempts"] if a["command_index"] == record["commands_begin"]]
    check(attempt["wire_begin"] == attempt["wire_end"] and "expected_exit" not in attempt["instruction"]
          and attempt["provider"] == start, "unchanged real relay interval/no arbitrary exit")
    check(all("expected_exit" not in a["instruction"] for a in observations["provider_attempts"]), "no caller-selected exceptional exit")
    for cmd in commands:
        check(cmd["provider"] == {k:start[k] for k in ("task","session","agent","org","pid")}
              and cmd["python"] == manifest["python"] and cmd["source_sha"] == row["source_sha"]
              and cmd["callback_sha256"] == manifest["callback_sha256"] and cmd["child_start"] is not None, "actual executable/PID/start/source binding")
        child_fields = cmd["child_stat"].rsplit(")",1)[1].split()
        check(int(cmd["child_stat"].split("(",1)[0]) == cmd["child_pid"] and child_fields[19] == cmd["child_start"]
              and int(child_fields[1]) == cmd["child_parent"] == start["pid"], "raw actual CLI child PID/start/parent")
        argv_hash = hashlib.sha256(json.dumps(cmd["argv"][1:]).encode()).hexdigest()
        witnesses = [w for w in observations["launch_witnesses"] if w["kind"] == "callback" and w["pid"] == cmd["child_pid"]]
        check(len(witnesses) == 1 and witnesses[0]["ppid"] == start["pid"] and witnesses[0]["argv_sha256"] == argv_hash
              and witnesses[0]["source_sha"] == row["source_sha"]
              and witnesses[0]["cli_sha256"] == hashlib.sha256((Path(__file__).resolve().parents[1]/"cli/main.py").read_bytes()).hexdigest(), "real intended CLI child witness")
    frames = refusal["frames"]
    check(len(frames) == 5 and refusal["closure"] == frames[3] and refusal["final_drain"] == frames[4], "complete before/after/closure/drain frames")
    before, after_cli, after_api = frames[:3]
    index = refusal["http_index"]
    http = observations["http"]
    api = http[index]
    check(type(index) is int and sum(h.get("capture") == "E04.review-required-refused" for h in http) == 1
          and api["index"] == index and api["capture"] == "E04.review-required-refused"
          and api["method"] == "POST" and api["path"] == "/api/v1/orgs/alpha/jobs/submit"
          and api["payload_file"] == file and api["request_text"] == raw.decode("utf-8")
          and api["request_bytes"] == len(raw) and api["request_sha256"] == digest and "error" not in api, "unique real identical-entity public POST")
    check(api["source_sha"] == row["source_sha"] and api["boot"] == observations["boots"][1]
          and api["daemon_pid"] == api["boot"]["pid"] and api["daemon_start"] == api["boot"]["start"] is not None
          and api["destination"]["host"] == "127.0.0.1" and api["destination"]["port"] in api["boot"]["discovered_ports"]
          and api["peer"] == ["127.0.0.1",api["destination"]["port"]], "actual loopback peer/daemon/boot source")
    response_raw = api["response_text"].encode("utf-8")
    check(api["status"] == 422 and api["response_bytes"] == len(response_raw)
          and api["response_sha256"] == hashlib.sha256(response_raw).hexdigest()
          and api["response"] == json.loads(response_raw) == {"detail":{"code":"review_required_removed","message":
              "Obtain any required authorization in a thread before submitting; omit review_required or set it to false. This request was not submitted."}}, "exact raw/parsed public refusal, no earlier guard")
    check(record["entered"] <= before["entered"] <= before["finished"] <= command["entered"] <= command["finished"]
          <= after_cli["entered"] <= after_cli["finished"] <= api["entered"] <= api["send"] <= api["response_at"] <= api["finished"]
          <= after_api["entered"] <= after_api["finished"] <= final["entered"] <= final["finished"] <= frames[3]["entered"]
          <= frames[3]["finished"] <= record["anchors"][-1]["entered"] < record["finished"] <= frames[4]["entered"], "monotonic real causal intervals")
    control_sentinel = positive["jobs"][0]["sentinel"]
    for i, frame in enumerate(frames):
        check(frame["coord_entries"] == sorted(set(frame["coord_entries"])) and Path(sentinel).name not in frame["coord_entries"]
              and Path(control_sentinel).name in frame["coord_entries"] and frame["socket"]["path"] == socket_path
              and type(frame["socket"]["fd"]) is int and frame["socket"]["fd"] >= 0 and frame["peer_ready"] == []
              and frame["sentinel_absent"] is True and frame["peer_pending"] is False
              and frame["provider_start"] == (start["pid_start"] if i < 3 else None), "sentinel/socket absence and live-then-gone original SID")
        for org in ("alpha","beta"):
            part, files = frame["sql"][org], frame["job_files"][org]
            check(frame["entered"] <= part["entered"] <= part["finished"] <= files["entered"] <= files["finished"] <= frame["finished"],
                  "coherent reopened SQL/file observation intervals, no claimed atomic files")
            content = part["content"]
            check({"tasks","task_results","audit_log","jobs","task_pause_controls"} <= set(content["tables"]), "complete relevant tables/control journal")
            check(all(r["review_required"] == 0 and r["status"] != "pending" for r in content["tables"]["jobs"]), "global no pending review row")
            check(not any(r["task_id"] == task for r in content["tables"]["jobs"]), "task no job allocation")
            for f in files["files"]:
                data = bytes.fromhex(f["hex"])
                check(f["bytes"] == len(data) and f["sha256"] == hashlib.sha256(data).hexdigest()
                      and dict(path="jobs/"+f["path"], bytes=len(data), sha256=f["sha256"]) in content["files"], "actual full job/output bytes and Store hashes")
            check(len(files["files"]) == sum(f["path"].startswith("jobs/") for f in content["files"]), "whole output file membership")
        procs = frame["processes"]
        check(frame["entered"] <= procs["entered"] <= procs["finished"] <= frame["finished"] and len(procs["rows"]) < 512
              and len({r["pid"] for r in procs["rows"]}) == len(procs["rows"]), "bounded actual descendant census")
        by_pid = {r["pid"]:r for r in procs["rows"]}
        check(api["daemon_pid"] in by_pid and by_pid[api["daemon_pid"]]["start"] == api["daemon_start"], "same daemon process")
        for r in procs["rows"]:
            fields = r["stat"].rsplit(")",1)[1].split()
            check(int(r["stat"].split("(",1)[0]) == r["pid"] and fields[19] == r["start"] and int(fields[1]) == r["ppid"]
                  and r["root_marker"] == f"E2E_ROOT={root}", "raw owned process identity")
            check(r["start"] is not None and all(child in by_pid and by_pid[child]["ppid"] == r["pid"] for child in r["children"]), "closed known descendant ownership")
        check((start["pid"] in by_pid) == (i < 3) and not any(cmd["child_pid"] in by_pid for cmd in commands), "intended CLI children separately accounted and gone")
    def unchanged(a: dict, b: dict) -> None:
        for org in ("alpha","beta"):
            left, right = a["sql"][org]["content"], b["sql"][org]["content"]
            check(left.keys() == right.keys() and left["files"] == right["files"] and left["tables"].keys() == right["tables"].keys()
                  and a["job_files"][org]["files"] == b["job_files"][org]["files"], "no product file or table membership change")
            for table, old_rows in left["tables"].items():
                new_rows = right["tables"][table]
                check(len(old_rows) == len(new_rows), "no row allocation " + table)
                for old,new in zip(old_rows,new_rows):
                    if org == "alpha" and table == "tasks" and old["id"] == task:
                        check(old.keys() == new.keys() and all(old[k] == new[k] for k in old if k not in {"last_heartbeat","updated_at"}), "exact current task unchanged")
                        pairs = [[datetime.fromisoformat(r[k]) for k in ("last_heartbeat","updated_at")] for r in (old,new)]
                        check(all(v.tzinfo is not None and v.utcoffset() is not None for pair in pairs for v in pair)
                              and all(pair[1] >= pair[0] for pair in pairs), "initialized valid aware heartbeat pairs")
                        check((all(old[k] == new[k] for k in ("last_heartbeat","updated_at")))
                              or all(y > x for x,y in zip(*pairs)), "only BOTH timestamps strictly forward")
                    else:
                        check(old == new, "no refusal SQL/audit/control/wait/result side effect " + table)
        keys = ("pid","start","ppid","children","root_marker")
        check([{k:r[k] for k in keys} for r in a["processes"]["rows"]] ==
              [{k:r[k] for k in keys} for r in b["processes"]["rows"]], "no refused descendant launch")
    unchanged(before,after_cli)
    unchanged(after_cli,after_api)
    baseline_tables = before["sql"]["alpha"]["content"]["tables"]
    baseline_task, = [t for t in baseline_tables["tasks"] if t["id"] == task]
    check(baseline_task["status"] == "in_progress" and baseline_task["current_session_id"] == start["session"]
          and baseline_task["assigned_agent"] == "case_manager" and baseline_task["orchestration_step_count"] == 1
          and baseline_task["parent_task_id"] is None and all(baseline_task[k] is None for k in ("block_kind","blocked_on_job_ids","active_chain","active_fanout"))
          and not any(r["task_id"] == task for r in baseline_tables["task_results"]), "valid initialized live ordinary task before refusal")
    check(refusal["positive_case"] == 0 and positive["index"] == 0 and positive["finished"] <= record["entered"]
          and len(positive["jobs"]) == 1, "immediately preceding genuine automatic positive")
    control = positive["jobs"][0]
    check(refusal["positive_job"] == control["id"] and refusal["positive_peer"] == control["peer"]
          and refusal["positive_oracle"] == control["oracle"] and json.loads(refusal["positive_sentinel"]) == control["peer"]["hello"]
          == {"token":control["token"],"pid":control["peer"]["pid"]}
          and control["peer"]["start"] is not None and control["code"] == control["oracle"]["row"]["exit_code"] == 0
          and control["oracle"]["outputs"][0]["text"] == "E04_OK_OUT\n"
          and [a["action"] for a in control["oracle"]["audits"]] == ["job_submitted","job_auto_started","job_run_completed"], "positive script/sentinel/socket/PID/output/audit control")
    summary = "E04_REVIEW_REQUIRED_REFUSED no job submitted"
    done = dict(action="done",summary=summary)
    expected_callback = dict(task_id=task,session_id=start["session"],agent="case_manager",status="completed",confidence=90,summary=summary,decision=done)
    check(final["payload"] == expected_callback and final["exit"] == 0 and not final["timed_out"]
          and final["argv"] == [manifest["callback"],"report-completion","--org","alpha","--from-file",final["payload_file"]["path"]], "genuine last final CLI callback")
    final_wire = [w for w in final["wire"] if w["method"] == "POST" and w["path"] == f"/api/v1/orgs/alpha/tasks/{task}/completion"]
    check(len(final_wire) == 1 and final_wire[0]["status"] == 200 and final_wire[0]["response"] == {"ok":True}
          and final_wire[0]["dropped"] is False and final_wire[0] in observations["relay"], "actual accepted callback200")
    snap = record["final_snapshot"]
    task_row, = snap["tasks"]
    result, = snap["results"]
    check(task_row["id"] == task and task_row["status"] == "completed" and task_row["completed_at"] is not None
          and task_row["parent_task_id"] is None and task_row["assigned_agent"] == "case_manager"
          and task_row["current_session_id"] == start["session"] and task_row["orchestration_step_count"] == 1
          and all(task_row[k] is None for k in ("block_kind","blocked_on_job_ids","active_chain","active_fanout")) and snap["jobs"] == [], "one-generation final no-wait task")
    check(type(result["id"]) is int and result["id"] > 0 and result["session_id"] == start["session"] and result["task_id"] == task
          and result["agent"] == "case_manager" and result["status"] == "completed" and type(result["confidence_score"]) is int
          and result["confidence_score"] == 90 and result["output_summary"] == summary and result["verdict"] is None
          and result["waiting_on_job_ids"] is None and json.loads(result["decision_json"]) == done, "actual positive INTEGER single truthful result")
    kinds = ("session_start","session_end","completion_report","orchestration_step","task_blocked_on_jobs","task_resumed_from_jobs")
    actions = {kind:[a for a in snap["audit"] if a["action"] == kind] for kind in kinds}
    check([len(actions[k]) for k in kinds] == [1,1,1,1,0,0] and len({a["id"] for a in snap["audit"]}) == len(snap["audit"])
          and all(type(a["id"]) is int and a["id"] > 0 for a in snap["audit"]), "one complete lifecycle/no park/wake")
    a,b,d,step = [actions[k][0] for k in kinds[:4]]
    consumed = json.loads(d["payload"])
    expanded = dict(done,then=[],children=[],**{k:None for k in ("agent","prompt","expect_verdict","width_cap_ack","revisit_of_task_id","attachments","join_summary","reason","successor_brief","rationale","attestation")})
    check(a["id"] < b["id"] < d["id"] < step["id"] and all(x["task_id"] == task for x in (a,b,d,step))
          and all(x["agent"] == "case_manager" for x in (a,b,d)) and step["agent"] == "orchestrator"
          and json.loads(a["payload"])["session_id"] == start["session"] and consumed["task_id"] == task and consumed["agent"] == "case_manager"
          and consumed["status"] == "completed" and type(consumed["confidence"]) is int and consumed["confidence"] == 90
          and consumed["output_summary"] == summary and consumed["verdict"] is None and consumed["waiting_on_job_ids"] == []
          and consumed["decision"] == expanded and json.loads(step["payload"]) == dict(step_number=1,decision=dict(done,then=[],children=[])), "ordered full callback consumption and step1")
    final_by_id = {a["id"]:a for a in snap["audit"]}
    check(all(final_by_id[a["id"]] == a for a in baseline_tables["audit_log"] if a["task_id"] == task), "all original lifecycle bytes retained")
    check([r for r in frames[3]["sql"]["alpha"]["content"]["tables"]["task_results"] if r["task_id"] == task] == [result], "closure retains same result")
    rendered = record["task_browser"]
    check(all(t in rendered["text"] for t in (summary,"RECALL TREE","OUTCOME","completed"))
          and rendered["screenshot"] == "e04-task-1.png" and (directory/"e04-task-1.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual final task/list/Recall screenshot")


def validate_e04(row: dict, observations: dict, browser: list[dict], directory: Path) -> None:
    """Recompute actual artifact content independently of the scenario driver.

    This is a daily artifact oracle, not an execution entrypoint or merge test.
    The metadata-only controls above do not establish this oracle's sensitivity.
    """
    import hashlib
    from collections import Counter
    from urllib.parse import parse_qs, urlsplit

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E04 content: " + message)

    check(observations["variants"] == row["variants"] and observations["observer_error"] is None,
          "scenario/observation disagreement or observer failure")
    starts, commands = observations["starts"], observations["provider_commands"]
    durable, observer = observations["durable"], observations["provider_observer"]
    check(len(starts) >= 29 and len({s["session"] for s in starts[:29]}) == 29, "independently owned29 genuine E04 prefix providers")
    check(len(observer) >= 2 and all(0 < b["at"] - a["at"] <= 1 for a, b in zip(observer, observer[1:])),
          "continuous provider observer interval")
    check(all(a["stubs"] <= b["stubs"] and a["calls"] <= b["calls"] for a, b in zip(observer, observer[1:])),
          "observer counter rollback")
    check(any(o['stubs'] == 29 and not o['pending'] for o in observer), 'actual closed E04 observer prefix')
    http = observations["http"]
    anchors = [r["jobs_anchor"] for r in durable if "jobs_anchor" in r and not r["jobs_anchor"]["name"].startswith("E05:")]
    check(len(anchors) == 27 and len({a["name"] for a in anchors}) == 27, "complete unique boot/case/drain anchors")
    by_name = {a["name"]: a for a in anchors}

    def anchor_content(a: dict, total: int, count: int, previous: dict | None = None) -> None:
        check(a["total"] == total and a["source_sha"] == row["source_sha"] and a["provider_count"] == count,
              "anchor source/count")
        check(a["daemon_start"] is not None and a["entered"] <= a["finished"] and len(a["samples"]) >= 2,
              "timed fresh anchor")
        check(0 <= a["observer_begin"] < a["observer_end"] <= len(observer), "actual observer references")
        check(observer[0]["at"] <= a["entered"] <= a["finished"] <= observer[-1]["at"], "observer covers barrier")
        check(len(a["provider_closure"]) == count and a["provider_closure"] == [
            dict(pid=s["pid"], start=s["pid_start"], observed_start=x["observed_start"])
            for s, x in zip(starts[:count], a["provider_closure"])], "actual finished provider references")
        check(all(x["start"] is not None and x["start"] != x["observed_start"] for x in a["provider_closure"]), "provider identity closure")
        projections = []
        for sample in a["samples"]:
            check(a["entered"] <= sample["entered"] <= sample["finished"] <= a["finished"], "sample timing")
            m = sample["metrics"]
            check(any(h["method"] == "GET" and h["path"] == "/api/v1/metrics" and h["status"] == 200 and h["response"] == m for h in http),
                  "actual referenced metrics response")
            host = m["host_sessions"]
            r, admission = host["receipts"], host["admission"]
            check(host["wired"] is True and host["backend"]["healthy"] is True and host["backend"]["name"] != "passthrough"
                  and host["residue"]["admission_blocked"] is False and host["residue"]["survivors_count"] == 0, "host availability/residue")
            check(all(type(admission[k]) is int and 0 <= admission[k] <= total for k in ("admitted_total", "released_total"))
                  and admission["shutdown"] is False and admission["cancelled_queued_total"] == 0, "literal admissions")
            check(type(r["published_total"]) is int and 0 <= r["published_total"] <= total <= 64
                  and r["published_total"] == r["window_size"] == len(r["recent"]) == r["quiescent_count"]
                  and r["with_residue_count"] == 0 and all(x["quiescent"] is True and type(x["survivors_count"]) is int
                                                         and x["survivors_count"] == 0 for x in r["recent"]), "literal complete anonymous window")
            if previous:
                old = previous["projection"]["receipts"]
                d = r["published_total"] - old["published_total"]
                check(d >= 0 and r["recent"][d:] == old["recent"], "transient ordered tail/reset")
            projections.append(dict(admission=admission, receipts=r,
                                    executor_sessions_active=m["executor_sessions_active"], run_step_queue_depth=m["run_step_queue_depth"]))
        check(projections[-1] == projections[-2] == a["projection"], "two fresh settled projections")
        p = projections[-1]
        check(p["executor_sessions_active"] == p["run_step_queue_depth"] == p["admission"]["active"] == p["admission"]["queue_depth"] == 0
              and p["admission"]["admitted_total"] == p["admission"]["released_total"] == p["receipts"]["published_total"] == total,
              "settled publication/release/tracker conjunction")
        if previous:
            check(a["daemon_pid"] == previous["daemon_pid"] and a["daemon_start"] == previous["daemon_start"]
                  and previous["finished"] <= a["entered"], "same-boot serial ordering")
            d = total - previous["total"]
            check(d >= 0 and p["receipts"]["recent"][d:] == previous["projection"]["receipts"]["recent"], "ordered retained tail with duplicates")

    initial, baseline, archive, boot_b, pause = (by_name[n] for n in ("initial-A", "baseline-A", "archive-A", "initial-B", "pause-final-B"))
    anchor_content(initial, 0, 0)
    anchor_content(baseline, 9, 9, initial)
    anchor_content(archive, 12, 12, baseline)
    anchor_content(boot_b, 0, 12)
    anchor_content(pause, 6, 18, boot_b)
    restarts = [d["pause_daemon_restart"] for d in durable if "pause_daemon_restart" in d]
    check(len(restarts) == 1, "only named Pause restart")
    restart = restarts[0]
    check(restart["exit"] == 0 and (restart["old"]["pid"], restart["old"]["start"]) == (archive["daemon_pid"], archive["daemon_start"])
          and (restart["new"]["pid"], restart["new"]["start"]) == (boot_b["daemon_pid"], boot_b["daemon_start"])
          and restart["old"] != restart["new"] and archive["finished"] < boot_b["entered"], "actual old/new boot binding")
    for key, expected in (("final_snapshot", {"alpha": (6, 8), "beta": (1, 1)}),
                          ("pause_final_org", {"alpha": (13, 17), "beta": (1, 1)}),
                          ("jobs_final_org", {"alpha": (19, 28), "beta": (1, 1)})):
        records = [d for d in durable if key in d]
        check(len(records) == 2, "retained full original and final org snapshots")
        for d in records:
            org = d["org"] if key == "final_snapshot" else d[key]
            snap = d[key] if key == "final_snapshot" else d["snapshot"]
            check(org in expected and (len(snap["tasks"]), len(snap["results"])) == expected[org]
                  and all(t["status"] == "completed" for t in snap["tasks"]), "literal org task/result closure")
    case_rows = [d["jobs_cases"] for d in durable if "jobs_cases" in d]
    check(len(case_rows) == 1 and len(case_rows[0]) == 6, "six actual case records")
    expected_variants = ["E04.approve-exit0", "E04.review-required-refused", "E04.completed-exit7", "E04.failed-timeout",
                         "E04.two-jobs-A-first", "E04.two-jobs-B-first"]
    expected_codes = [[0], [], [7], [-9], [0, 7], [7, 0]]
    all_jobs, peer_count, previous = [], 0, pause
    for index, record in enumerate(case_rows[0]):
        variant = expected_variants[index]
        check(record["variant"] == variant and record["index"] == index and record["source_sha"] == row["source_sha"]
              and record["method"] == "serial-public-metrics-window-causal-v1"
              and row["variants"][variant]["evidence"]["host_cleanup_attribution"] == record, "actual variant evidence reference")
        check(record["entered"] <= record["finished"] < record["deadline"]
              and record["deadline"] <= record["entered"] + [20, 15, 20, 25, 25, 25][index], "case bound")
        begin, end = ((18,20),(20,21),(21,23),(23,25),(25,27),(27,29))[index]
        check(record["starts"] == starts[begin:end], "actual sequential provider references")
        if index == 1:
            checkpoints = record["anchors"]
            check([a["name"] for a in checkpoints] == [variant+":pre",variant+":final"]
                  and all(by_name[a["name"]] == a for a in checkpoints), "refusal two settled anchors")
            anchor_content(checkpoints[0], 8, 20, previous)
            anchor_content(checkpoints[1], 9, 21, checkpoints[0])
            validate_review_refusal(row, observations, directory, record, case_rows[0][0])
            previous = checkpoints[1]
            continue
        first, second = record["starts"]
        task = record["task"]
        check(first["task"] == second["task"] == task and first["org"] == second["org"] == "alpha"
              and first["agent"] == second["agent"] == "case_manager", "genuine task/S1/S2 ownership")
        checkpoints = record["anchors"]
        names = [variant + ":pre", variant + ":S1"]
        if index >= 4:
            names += [variant + ":first-terminal", variant + ":pre-second-action"]
        names += [variant + ":final"]
        check([a["name"] for a in checkpoints] == names and all(by_name[a["name"]] == a for a in checkpoints), "complete causal checkpoint references")
        for i, a in enumerate(checkpoints):
            count = begin + (0 if i == 0 else 2 if i == len(checkpoints) - 1 else 1)
            anchor_content(a, count - 12, count, previous)
            previous = a
        jobs = record["jobs"]
        ids = sorted(j["id"] for j in jobs)
        check([j["code"] for j in jobs] == expected_codes[index], "literal case set/order")
        block_payload = dict(record["block"]["payload"])
        for optional in ("verdict", "decision"):
            if optional in block_payload:
                check(block_payload.pop(optional) is None, "declared original block null default")
        check(block_payload == dict(task_id=task, session_id=first["session"], agent="case_manager", status="blocked",
              confidence=90, summary="waiting for genuine IDs", waiting_on_job_ids=[j["id"] for j in jobs]), "real blocked callback payload")
        check(record["parked"]["tasks"][0]["block_kind"] == "blocked_on_job"
              and record["parked"]["results"][0]["status"] == "blocked", "S1 parked durable evidence")
        live = record["live_s2"]
        live_snapshot = live["snapshot"]
        check(live["source_sha"] == row["source_sha"] and live["provider"] == second
              and live["observed_start"] == second["pid_start"] is not None
              and checkpoints[1]["finished"] < live["entered"] <= live["finished"]
              < record["final_callback"]["entered"], "source-bound live S2 frame before callback")
        check(len(live_snapshot["tasks"]) == 1 and len(live_snapshot["results"]) == 1,
              "live S2 one task and one historical result")
        live_task, blocked = live_snapshot["tasks"][0], live_snapshot["results"][0]
        check(live_task["id"] == blocked["task_id"] == task and live_task["status"] == "in_progress"
              and live_task["current_session_id"] == second["session"]
              and live_task["orchestration_step_count"] == 2 and live_task["completed_at"] is None
              and live_task["assigned_agent"] == "case_manager"
              and all(live_task[k] is None for k in ("block_kind", "active_chain", "active_fanout", "parent_task_id"))
              and json.loads(live_task["blocked_on_job_ids"]) == ids
              and blocked["status"] == "blocked" and blocked["session_id"] == first["session"]
              and blocked["agent"] == "case_manager" and live_snapshot["results"] == record["parked"]["results"],
              "live S2 binding with unchanged blocked S1 result")
        check(live_snapshot["jobs"] == sorted([j["oracle"]["row"] for j in jobs], key=lambda j: j["id"]),
              "live S2 durable job outcomes")
        for job in jobs:
            all_jobs.append(job["id"])
            label = (["OK"], ["REJECT"], ["EXIT7"], ["TIMEOUT"], ["A", "B"], ["B", "A"])[index][jobs.index(job)]
            check(job["label"] == label and job["out"] == ("" if index == 1 else f"E04_{label}_OUT\n")
                  and job["err"] == ("" if index == 1 else f"E04_{label}_ERR\n"), "independent literal stream expectations")
            check(job["submit"] in commands and all(r in commands for r in job["reads"].values()), "command content references")
            raw = job["oracle"]["row"]
            code = job["code"]
            status = "rejected" if code is None else "failed" if code == -9 else "completed"
            check((raw["id"], raw["task_id"], raw["agent_name"], raw["status"], raw["exit_code"], raw["reason"])
                  == (job["id"], task, "case_manager", status, code, "timeout" if code == -9 else None), "literal job outcome")
            check(raw["reviewed_by"] == ("founder" if code is None else "case_manager") and raw["reviewed_at"] is not None and raw["max_runtime_seconds"] == (5 if code == -9 else 30)
                  and raw["review_required"] == (1 if code is None else 0) and raw["persistent"] == 0 and raw["script_text"] == job["script"]
                  and hashlib.sha256(job["script"].encode()).hexdigest() == job["script_sha256"], "submitted script/cap/review join")
            submitted = json.loads(job["submit"]["stdout"])
            check(submitted["id"] == job["id"] and submitted["status"] == ("pending" if code is None else "running")
                  and submitted["authentication"] == dict(task_id=task, session_id=first["session"])
                  and job["submit"]["payload"]["script"] == raw["script_text"]
                  and job["submit"]["payload"]["review_required"] is (code is None), "actual returned ID and provider submit")
            submit_wire = [w for w in job["submit"]["wire"] if w["method"] == "POST" and w["path"] == "/api/v1/orgs/alpha/jobs/submit"]
            check(len(submit_wire) == 1 and submit_wire[0]["status"] == 201 and submit_wire[0]["response"] == submitted, "real submit201")
            check(len(job["pending"]) == (4 if index >= 4 and job is jobs[1] else 3) and job["pending"][0]["at"] < checkpoints[1]["entered"]
                  and job["pending"][1]["at"] >= checkpoints[1]["finished"], "pending observations at submit/park/dialog")
            check(job["scenario_deadline"] == next(p["deadline"] for p in row["phases"] if p["name"] == "scenarios")
                  and job["effective_end"] <= job["scenario_deadline"]
                  and job["effective_end"] == record["deadline"]
                  and ("end = " + repr(job["effective_end"])) in job["script"], "actual generated job absolute end")
            for pending in job["pending"]:
                if code is None:
                    check(pending["row"]["status"] == "pending" and pending["sentinel_absent"] is True and pending["peer_pending"] is False
                          and len(pending["files_absent"]) == 2 and all(pending["files_absent"].values())
                          and [a["action"] for a in pending["audit"]] == ["job_submitted"]
                          and all(pending["row"][k] is None for k in ("started_at", "finished_at", "reviewed_at", "exit_code", "stdout_path", "stderr_path")),
                          "historical pending no launch/file/audit evidence")
                else:
                    check(pending["row"]["status"] == "running" and pending["sentinel_absent"] is False
                          and pending["peer_pending"] is False and len(pending["files_absent"]) == 2
                          and not any(pending["files_absent"].values())
                          and pending["row"]["started_at"] and pending["row"]["reviewed_at"]
                          and pending["row"]["reviewed_by"] == "case_manager"
                          and all(pending["row"][k] is None for k in ("finished_at", "exit_code", "reason"))
                          and [a["action"] for a in pending["audit"]] == ["job_submitted", "job_auto_started"]
                          and all(a["agent"] == "case_manager" for a in pending["audit"]),
                          "automatic running witness and genuine agent start, not success")
            action = job["action"]
            check(action["at"] > checkpoints[1]["finished"] and action["no_run_controls"] is True
                  and action["method"] == ("POST" if code is None else "GET")
                  and action["path"] == f"/api/v1/orgs/alpha/jobs/{job['id']}" + ("/reject" if code is None else "")
                  and action["request"] == ({"reason": "E04_REJECTED_BY_REVIEW"} if code is None else None)
                  and action["status"] == 200 and action["response"]["id"] == job["id"]
                  and action["response"]["status"] == ("rejected" if code is None else "running")
                  and dict(event="job-action", **action) in browser, "actual browser no-run observation or historical individual rejection")
            if code is not None:
                check(any(e.get("event") == "job-read" and e["at"] <= action["at"]
                          and all(e[key] == action[key] for key in ("path", "method", "status", "response"))
                          for e in browser), "automatic UI observation joins the actual native read")
            check(job["browser"]["body"]["status"] == status and job["browser"]["body"]["exit_code"] == code, "browser literal outcome")
            image = job["browser"]["screenshot"]
            check(Path(image).name == image and (directory / image).is_file() and (directory / image).stat().st_size > 0, "actual outcome screenshot")
            output = dict(stdout=job["out"], stderr=job["err"], truncated_stdout=False, truncated_stderr=False,
                          total_stdout_bytes=len(job["out"].encode()), total_stderr_bytes=len(job["err"].encode()))
            check([o["text"] for o in job["oracle"]["outputs"]] == [job["out"], job["err"]]
                  and [o["bytes"] for o in job["oracle"]["outputs"]] == [len(job["out"].encode()), len(job["err"].encode())], "literal output file content")
            reads = job["reads"]
            check(set(reads) == {"show", "receipt", "output", "tail", "wait"}, "all five declared read operations")
            # Shipping routes expose the full JobRecord for show/wait, and a
            # closed identity for receipt/output. Build both from the raw row,
            # never from whichever fields the CLI happened to return.
            detail = dict(raw, review_required=bool(raw["review_required"]), persistent=bool(raw["persistent"]))
            receipt = dict(authentication=dict(task_id=task, session_id=second["session"]),
                           job={k: raw[k] for k in ("id", "task_id", "agent_name", "title", "rationale", "script_text",
                                "interpreter", "cwd_hint", "cwd_resolved", "status", "exit_code", "reason", "duration_ms",
                                "created_at", "started_at", "finished_at")}, output=output)
            tail = dict(stream="stdout", lines=job["out"].splitlines())
            expected_bodies = dict(show=detail, receipt=receipt, output=receipt, tail=tail,
                                   wait=dict(detail, timed_out=False))
            previous_read_end = live["finished"]
            for operation in ("receipt", "show", "output", "tail", "wait"):
                command = reads[operation]
                check(command["action"] == "jobs-read" and command["operation"] == operation
                      and command["provider"] == {k: second[k] for k in ("task", "session", "agent", "org", "pid")}
                      and previous_read_end <= command["entered"] <= command["finished"] < record["final_callback"]["entered"],
                      "actual live S2 ordered read identity/time")
                previous_read_end = command["finished"]
                verb = "show" if operation == "receipt" else operation
                argv = ["jobs", verb, job["id"], "--org", "alpha"]
                if operation in {"receipt", "output"}:
                    argv += ["--json", "--task-id", task, "--session-id", second["session"]]
                if operation == "wait":
                    argv += ["--timeout-seconds", "1"]
                if operation in {"tail", "wait"}:
                    argv += ["--task-id", task, "--session-id", second["session"]]
                check(command["argv"][1:] == argv, "shipping read argv")
                suffix = "" if operation == "show" else "/receipt" if operation in {"receipt", "output"} else "/" + operation
                query = {} if operation == "show" else dict(task_id=[task], session_id=[second["session"]])
                if operation == "output":
                    query.update(stream=["both"], max_bytes=["1048576"])
                elif operation == "tail":
                    query.update(stream=["stdout"], lines=["50"])
                elif operation == "wait":
                    query.update(timeout_seconds=["1"])
                # Org discovery stays in wire evidence but cannot satisfy a
                # job read. Count only the job operation, not all requests.
                wire = [w for w in command["wire"] if "/jobs/" in urlsplit(w["path"]).path]
                check(len(wire) == 1, "one actual job operation per read")
                actual = wire[0]
                url = urlsplit(actual["path"])
                check(actual["method"] == ("POST" if operation == "wait" else "GET")
                      and url.path == f"/api/v1/orgs/alpha/jobs/{job['id']}{suffix}"
                      and not url.scheme and not url.netloc and not url.fragment
                      and parse_qs(url.query, keep_blank_values=True, strict_parsing=True) == query
                      and actual["status"] == 200 and actual["dropped"] is False
                      and actual["response"] == expected_bodies[operation], "shipping read HTTP200/query/actual body vs durable row")
                if operation in {"receipt", "output"}:
                    check(json.loads(command["stdout"]) == actual["response"], "CLI JSON equals actual receipt response")
                elif operation == "tail":
                    check(command["stdout"] == "".join(line + "\n" for line in actual["response"]["lines"]) == job["out"],
                          "CLI tail equals actual response and durable output")
                elif operation == "wait":
                    check(json.loads(command["stdout"]) == dict(status=actual["response"]["status"], timed_out=False),
                          "CLI wait equals actual terminal response")
                else:
                    lines = command["stdout"].splitlines()
                    check(bool(lines) and lines[0] == f"{job['id']}   {status}   submitted {raw['created_at']}",
                          "human show first-line ID/status/timestamp, independent of title")
                    if code is not None:
                        check(f"Exit code:    {code}" in lines, "human show literal exit")
                    if code == -9:
                        check("Reason:       timeout" in lines, "human show timeout reason")
                    if code is None:
                        check("Reject reason: E04_REJECTED_BY_REVIEW" in lines, "human show rejection reason")
            audits = job["oracle"]["audits"]
            terminal = "job_rejected" if code is None else "job_run_failed" if code == -9 else "job_run_completed"
            check([a["action"] for a in audits] == (["job_submitted", terminal] if code is None else ["job_submitted", "job_auto_started", terminal])
                  and audits[0]["agent"] == "case_manager" and all(a["agent"] == ("founder" if code is None else "case_manager") for a in audits[1:])
                  and all(json.loads(a["payload"])["script_request_id"] == job["id"] for a in audits), "literal job audit links/actors")
            payload = json.loads(audits[-1]["payload"])
            if code is None:
                check(raw["reject_reason"] == payload["reason"] == "E04_REJECTED_BY_REVIEW" and "peer" not in job
                      and job["rejection_drain"] == job["oracle"] and all(o["exists"] is False for o in job["oracle"]["outputs"])
                      and all(raw[k] is None for k in ("started_at", "finished_at", "duration_ms", "cwd_resolved", "stdout_head", "stderr_head")), "rejection through final drain")
            else:
                peer_count += 1
                peer, closure = job["peer"], job["closure"]
                check(peer["hello"] == dict(token=job["token"], pid=peer["pid"]) and peer["pid"] == peer["pgid"] == closure["pgid"]
                      and peer["start"] == closure["start"] and closure["start"] != closure["observed_start"]
                      and closure["eof"] is True and closure["members"] == [] and payload["exit_code"] == code, "real job process/group/EOF closure")
                check(any(d.get("jobs_peer") == peer for d in durable), "retained owned script peer")
                check(str(peer["pid"]) in row["cleanup"]["identities"]
                      and row["cleanup"]["identities"][str(peer["pid"]) ] == peer["start"], "outer owned census join")
                if code == -9:
                    check(payload["reason"] == "timeout" and 5000 <= raw["duration_ms"] <= 17000 and "released" not in job, "product timeout without controller release")
                else:
                    check(payload["stdout_bytes"] == len(job["out"].encode()) and payload["stderr_bytes"] == len(job["err"].encode())
                          and payload["truncated_stdout"] is False and payload["truncated_stderr"] is False, "terminal audit exact bytes")
        # Independent reconstruction from serialized frames and original
        # commands. Never import the maker oracle or trust its validation flag.
        summary = ["E04_OK completed exit0", "E04_REVIEW_REQUIRED_REFUSED no job submitted",
                   "E04_EXIT7 completed exit7; operation nonzero", "E04_TIMEOUT failed timeout exit-9",
                   "E04_PAIR A completed exit0; B completed exit7", "E04_PAIR A completed exit0; B completed exit7"][index]
        compact_done = {"action": "done", "summary": summary}
        semantic_done = {"action": "done", "summary": summary, "then": [], "children": [],
                         "agent": None, "prompt": None, "expect_verdict": None, "width_cap_ack": None,
                         "revisit_of_task_id": None, "attachments": None, "join_summary": None,
                         "reason": None, "successor_brief": None, "rationale": None, "attestation": None}
        check(all(isinstance(s["session"], str) and s["session"] for s in (first, second))
              and first["session"] != second["session"], "two distinct genuine provider SIDs")

        def completion_content(result: dict, start: dict, a: dict, b: dict, d: dict,
                               command: dict, generation: int) -> None:
            callback = command["payload"]
            semantic = json.loads(d["payload"])
            expected_status = "blocked" if generation == 0 else "completed"
            expected_summary = "waiting for genuine IDs" if generation == 0 else summary
            check(type(result["id"]) is int and result["id"] > 0
                  and result["session_id"] == start["session"] == callback["session_id"]
                  == json.loads(a["payload"])["session_id"], "genuine result/start/callback SID")
            check(a["task_id"] == b["task_id"] == d["task_id"] == result["task_id"]
                  == semantic["task_id"] == callback["task_id"] == start["task"] == task
                  and a["agent"] == b["agent"] == d["agent"] == result["agent"]
                  == semantic["agent"] == callback["agent"] == start["agent"] == "case_manager"
                  and a["id"] < b["id"] < d["id"], "complete ordered task/agent generation slot")
            check(semantic["status"] == result["status"] == callback["status"] == expected_status
                  and type(semantic["confidence"]) is int and type(result["confidence_score"]) is int
                  and type(callback["confidence"]) is int
                  and semantic["confidence"] == result["confidence_score"] == callback["confidence"] == 90
                  and semantic["output_summary"] == result["output_summary"] == callback["summary"] == expected_summary
                  and semantic["verdict"] is None and result["verdict"] is None,
                  "original accepted result vs every emitted completion semantic field")
            accepted = None if result["decision_json"] is None else json.loads(result["decision_json"])
            check(accepted == (None if generation == 0 else compact_done)
                  and semantic["decision"] == (None if generation == 0 else semantic_done)
                  and semantic["waiting_on_job_ids"] == ([j["id"] for j in jobs] if generation == 0 else []),
                  "closed actual NextStep defaults and original waiting list")
            check((json.loads(result["waiting_on_job_ids"]) == ids if generation == 0 else result["waiting_on_job_ids"] is None),
                  "accepted S1 sorted durable waits and S2 NULL")

        # Original public command bodies/actors/paths, including only declared
        # optional nulls. These are source witnesses, not a maker success flag.
        for generation, (start, command) in enumerate(((first, record["block"]), (second, record["final_callback"]))):
            expected_callback = dict(task_id=task, session_id=start["session"], agent="case_manager",
                                     status="blocked" if generation == 0 else "completed", confidence=90,
                                     summary="waiting for genuine IDs" if generation == 0 else summary)
            if generation == 0:
                expected_callback["waiting_on_job_ids"] = [j["id"] for j in jobs]
            else:
                expected_callback["decision"] = compact_done
            supplied = dict(command["payload"])
            if "verdict" in supplied:
                check(supplied.pop("verdict") is None, "original null callback verdict")
            if generation == 0 and "decision" in supplied:
                check(supplied.pop("decision") is None, "original null blocked decision")
            check(supplied == expected_callback and type(command["payload"]["confidence"]) is int
                  and command in commands and command["source_sha"] == row["source_sha"]
                  and command["provider"] == {k: start[k] for k in ("task", "session", "agent", "org", "pid")}
                  and command["action"] == ("jobs-block" if generation == 0 else "jobs-final")
                  and len(command["argv"]) == 6
                  and command["argv"][1:5] == ["report-completion", "--org", "alpha", "--from-file"] and Path(command["argv"][5]).is_absolute(), "original actual callback command identity")
            wire = [w for w in command["wire"] if w["method"] == "POST"
                    and w["path"] == f"/api/v1/orgs/alpha/tasks/{task}/completion"]
            check(len(wire) == 1 and wire[0]["status"] == 200 and wire[0]["response"] == {"ok": True}
                  and wire[0]["dropped"] is False, "genuine shipping accepted completion200")

        snap = record["final_snapshot"]
        # Original history is joined by unique INTEGER identity and full row,
        # including raw payload bytes. A duplicate cannot stand in for a row.
        for frame in (record["parked"], live_snapshot, snap):
            check(all(type(a["id"]) is int and a["id"] > 0 for a in frame["audit"])
                  and len({a["id"] for a in frame["audit"]}) == len(frame["audit"]), "unique positive audit identities")
        parked_audits = {a["id"]: a for a in record["parked"]["audit"]}
        live_audits = {a["id"]: a for a in live_snapshot["audit"]}
        final_audits = {a["id"]: a for a in snap["audit"]}
        check(all(identity in live_audits and live_audits[identity] == original for identity, original in parked_audits.items())
              and all(identity in final_audits and final_audits[identity] == original for identity, original in live_audits.items()),
              "all original parked/live audit rows and payload bytes retained")

        # J1.b: real live S2, before its reads/callback, with S1 consumed once.
        live_actions = {k: [a for a in live_snapshot["audit"] if a["action"] == k] for k in
                        ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")}
        check([len(live_actions[k]) for k in live_actions] == [2, 1, 1, 1, 1, 0], "live exact lifecycle, no S2 end/consume/done")
        check([json.loads(a["payload"])["session_id"] for a in live_actions["session_start"]]
              == [first["session"], second["session"]]
              and all(a["task_id"] == task and a["agent"] == "case_manager" for a in live_actions["session_start"]),
              "live two actual task/agent start bindings")
        completion_content(blocked, first, live_actions["session_start"][0], live_actions["session_end"][0],
                           live_actions["completion_report"][0], record["block"], 0)
        live_block, live_wake = live_actions["task_blocked_on_jobs"][0], live_actions["task_resumed_from_jobs"][0]
        check(live_block["task_id"] == live_wake["task_id"] == task and live_block["agent"] == "case_manager"
              and live_wake["agent"] == "orchestrator"
              and live_actions["completion_report"][0]["id"] < live_block["id"] < live_wake["id"] < live_actions["session_start"][1]["id"],
              "natural live S1 consume/park/wake/S2 start order")
        check(json.loads(live_wake["payload"]) == dict(blocking_job_ids=ids, trigger="job_terminal", triggering_job_id=jobs[-1]["id"],
              job_outcomes={j["id"]: j["oracle"]["row"]["status"] for j in jobs}), "independent live all-terminal wake map")

        # J1.c: complete final generations reconstructed independently of J1.a.
        check(len(snap["tasks"]) == 1 and len(snap["results"]) == 2
              and all(type(r["id"]) is int and r["id"] > 0 for r in snap["results"])
              and len({r["id"] for r in snap["results"]}) == 2
              and snap["results"][0]["id"] < snap["results"][1]["id"]
              and [r["status"] for r in snap["results"]] == ["blocked", "completed"]
              and [r["session_id"] for r in snap["results"]] == [first["session"], second["session"]]
              and snap["results"][:1] == live_snapshot["results"] == record["parked"]["results"], "distinct final results and unchanged complete S1")
        task_row = snap["tasks"][0]
        check(task_row["id"] == task and task_row["assigned_agent"] == "case_manager"
              and task_row["status"] == "completed" and task_row["completed_at"] is not None and task_row["parent_task_id"] is None
              and task_row["orchestration_step_count"] == 2 and task_row["current_session_id"] == second["session"]
              and all(task_row[k] is None for k in ("block_kind", "active_chain", "active_fanout"))
              and json.loads(task_row["blocked_on_job_ids"]) == ids, "terminal task/historical IDs")
        actions = {k: [a for a in snap["audit"] if a["action"] == k] for k in
                   ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")}
        check([len(actions[k]) for k in actions] == [2, 2, 2, 1, 1, 1], "complete final lifecycle cardinalities")
        for generation, start in enumerate((first, second)):
            a, b, d = (actions[k][generation] for k in ("session_start", "session_end", "completion_report"))
            completion_content(snap["results"][generation], start, a, b, d,
                               record["block"] if generation == 0 else record["final_callback"], generation)
        block, wake = actions["task_blocked_on_jobs"][0], actions["task_resumed_from_jobs"][0]
        check(block["task_id"] == wake["task_id"] == task and block["agent"] == "case_manager"
              and wake["agent"] == "orchestrator"
              and actions["completion_report"][0]["id"] < block["id"] < wake["id"] < actions["session_start"][1]["id"], "park before natural wake")
        wp = json.loads(wake["payload"])
        check(wp == dict(blocking_job_ids=ids, trigger="job_terminal", triggering_job_id=jobs[-1]["id"],
              job_outcomes={j["id"]: j["oracle"]["row"]["status"] for j in jobs}), "independent final all-terminal wake map")
        done_audit = actions["orchestration_step"][0]
        check(done_audit["task_id"] == task and done_audit["agent"] == "orchestrator"
              and done_audit["id"] > actions["completion_report"][1]["id"]
              and json.loads(done_audit["payload"]) == dict(step_number=2, decision=dict(compact_done, then=[], children=[])),
              "actual orchestrator final done with exclude_none defaults")
        if index >= 4:
            early = record["first_terminal"]
            check(len(early["jobs"]) == 2 and len(set(ids)) == 2
                  and sorted(j["id"] for j in early["jobs"]) == ids
                  and all(j["task_id"] == task and j["agent_name"] == "case_manager" for j in early["jobs"]),
                  "pair first-terminal exact submitted job identities/owner")
            early_jobs = {j["id"]: j for j in early["jobs"]}
            completed, running = (early_jobs[j["id"]] for j in jobs)
            check(completed == jobs[0]["oracle"]["row"]
                  and type(completed["exit_code"]) is int
                  and (completed["status"], completed["exit_code"], completed["reason"])
                  == ("completed", expected_codes[index][0], None)
                  and running == jobs[1]["pending"][-1]["row"]
                  and running["status"] == "running"
                  and all(running[k] is None for k in ("finished_at", "exit_code", "reason", "duration_ms")),
                  "pair authored first outcome and genuinely running second job")
            check(len(early["tasks"]) == 1 and len(early["results"]) == 1
                  and early["results"] == record["parked"]["results"]
                  and early["tasks"][0]["id"] == task
                  and early["tasks"][0]["assigned_agent"] == "case_manager"
                  and early["tasks"][0]["status"] == "in_progress"
                  and early["tasks"][0]["current_session_id"] == first["session"]
                  and early["tasks"][0]["orchestration_step_count"] == 1
                  and early["tasks"][0]["block_kind"] == "blocked_on_job"
                  and json.loads(early["tasks"][0]["blocked_on_job_ids"]) == ids
                  and not any(a["action"] == "task_resumed_from_jobs" for a in early["audit"])
                  and jobs[1]["action"]["at"] > checkpoints[-2]["finished"], "pair +0 no-early-resume barrier")
        check(snap["results"][1]["output_summary"] == summary, "independent truthful final summary")
        rendered = record["task_browser"]
        check(all(t in rendered["text"] for t in (summary, "RECALL TREE", "OUTCOME", "completed"))
              and rendered["screenshot"] == f"e04-task-{index}.png" and (directory / rendered["screenshot"]).is_file(), "rendered final task/Recall")
        own = commands[record["commands_begin"]:record["commands_end"]]
        check(own and own[-1] == record["final_callback"] and own[-1]["action"] == "jobs-final"
              and own[-1]["payload"]["task_id"] == task and own[-1]["payload"]["session_id"] == second["session"]
              and "waiting_on_job_ids" not in own[-1]["payload"]
              and own[-1]["payload"]["summary"] == snap["results"][1]["output_summary"]
              and json.loads(snap["results"][1]["decision_json"]) == own[-1]["payload"]["decision"], "last real final callback")
        check([x["action"] for x in own if x["provider"]["session"] == first["session"]] == [*["jobs-submit"] * len(jobs), "jobs-block"], "S1 final CLI action")
        check([x["action"] for x in own if x["provider"]["session"] == second["session"]] == [*["jobs-read"] * (5 * len(jobs)), "jobs-final"], "S2 reads before final callback")
    check(len(all_jobs) == len(set(all_jobs)) == 7 and peer_count == 7, "seven jobs/seven scripts")
    anchor_content(by_name["final-drain-B"], 17, 29, previous)
    final_witness = [d["jobs_final_witnesses"] for d in durable if "jobs_final_witnesses" in d]
    check(len(final_witness) == 1, "actual final witness artifact content")
    check(observations["launch_witnesses"][:len(final_witness[0])] == final_witness[0]
          and observations["witness_incomplete_line"] is False, "complete unchanged E04 witness prefix")
    e05 = [d["threads_cases"] for d in durable if "threads_cases" in d]
    check(len(e05) == 1 and e05[0]["entered"] >= by_name["final-drain-B"]["finished"], "E05 only after complete E04")
    prefix_commands = observations["commands"][:e05[0]["prefix_command_count"]]
    attempts = observations["provider_attempts"]
    check(len(attempts) == len(commands) and [a["command_index"] for a in attempts] == list(range(len(commands)))
          and all(a["entered"] <= a["finished"] and a["provider"]["session"] == c["provider"]["session"]
                  and a["instruction"]["action"] == c["action"]
                  and c["execution_deadline"] == a["instruction"]["execution_deadline"] == a["instruction"]["deadline"] - 1
                  and a["entered"] <= c["entered"] <= c["finished"] <= a["finished"] < a["instruction"]["deadline"]
                  <= a["entered"] + 10 for a, c in zip(attempts, commands)), "all CLI attempts accounted")
    check(len(commands) == 54 and Counter(c["action"] for c in commands) ==
          {"jobs-submit":7,"jobs-refuse-review":1,"jobs-block":5,"jobs-final":6,"jobs-read":35}, "exact54 provider command inventory")
    calls = [w for w in final_witness[0] if w["kind"] == "callback"]
    stubs = [w for w in final_witness[0] if w["kind"] == "stub"]
    check(len(calls) == len(prefix_commands) + len(commands) and len(stubs) == 29
          and all(w["source_sha"] == row["source_sha"] for w in calls + stubs), "all authentic source-bound executable witnesses")
    for command in commands:
        refused = command["action"] == "jobs-refuse-review"
        check(command["exit"] == (2 if refused else 0) and command["timed_out"] is False and command["source_sha"] == row["source_sha"]
              and command["entered"] <= command["finished"]
              and (command["wire"] == [] if refused else bool(command["wire"])) and all(w in observations["relay"] and w["dropped"] is False for w in command["wire"]), "actual CLI/wire record")
        if command["action"] in {"jobs-block", "jobs-final"}:
            callbacks = [w for w in command["wire"] if w["method"] == "POST" and w["path"] == f"/api/v1/orgs/alpha/tasks/{command['provider']['task']}/completion"]
            check(len(callbacks) == 1 and callbacks[0]["status"] == 200 and callbacks[0]["response"] == {"ok": True}, "actual callback200")
        digest = hashlib.sha256(json.dumps(command["argv"][1:]).encode()).hexdigest()
        check(len([w for w in calls if w["pid"] == command["child_pid"] and w["ppid"] == command["provider"]["pid"]
                   and w["argv_sha256"] == digest]) == 1, "provider child/PID/argv source witness")


def require_browser_copy(event: dict, original: dict) -> None:
    """A measured independent browser input is mandatory, including full JSON."""
    if event.get('browser_copy') != 'measured-browser':
        raise ValueError('E05 browser evidence UNKNOWN: independent browser copy unavailable')
    if (event.get('body') != original['body'] or event.get('sha256') != original['sha256']
            or type(event.get('bytes')) is not int or event['bytes'] != original['bytes']):
        raise ValueError('E05 browser evidence FAIL: original/browser full body/hash/length mismatch')


def validate_original_member(record: dict) -> object:
    """Independent bounded original/forwarding grammar, not browser receipt."""
    def check(value, reason):
        if not value:
            raise ValueError('E05 original: ' + reason)
    def number(value):
        return type(value) in (int,float) and math.isfinite(value)
    o,f = record['original'], record['forwarding']
    check(set(o) == {'schema','relay_id','required_kind','started','status','framing','header_bytes','finished',
                     'outcome','byte_length','sha256','first_error','capture_state','capture_refusal','entity_copy'}, 'closed original grammar')
    check(type(o['schema']) is int and o['schema'] == 1 and type(o['relay_id']) is int
          and o['relay_id'] == record['relay_id'] and o['required_kind'] in {'compose','detail','messages'}, 'original identity')
    check(o['outcome'] in {'complete','empty','bodyless','partial','error','pending'}
          and o['capture_state'] in {'open','sealed','refused'}, 'distinct original outcome')
    check(o['capture_state'] == 'sealed' and o['capture_refusal'] is None
          and o['first_error'] is None and o['outcome'] == 'complete', 'complete required original, never partial/empty/error/refusal')
    check(type(o['status']) is int and o['status'] == record['status'] == 200
          and number(o['started']) and number(o['finished'])
          and o['started'] == record['entered'] <= o['finished'] <= record['closed'], 'original status/timeline')
    framing = o['framing']
    check(set(framing) == {'content_type','content_encoding','content_length','transfer_encoding','bodyless'}
          and all(type(framing[k]) is list and len(framing[k]) <= 1 and all(type(v) is str for v in framing[k])
                  for k in ('content_type','content_encoding','content_length','transfer_encoding'))
          and type(framing['bodyless']) is bool and not framing['bodyless']
          and framing['content_encoding'] in ([],['identity'])
          and len(framing['content_type']) == 1 and framing['content_type'][0].split(';')[0] == 'application/json', 'identity UTF8 JSON framing')
    check(type(o['header_bytes']) is int and 0 <= o['header_bytes'] <= 65536
          and type(o['byte_length']) is int and 0 < o['byte_length'] <= 1048576, 'entity/header bounds')
    entity = o['entity_copy']
    check(set(entity) == {'representation','text','copy_bytes','copy_sha256','original_byte_check'}
          and entity['representation'] == 'unchanged_utf8' and entity['original_byte_check'] == 'available'
          and type(entity['text']) is str, 'unchanged safe original bytes available; sanitized copy is not original proof')
    raw = entity['text'].encode('utf8')
    check(len(raw) == o['byte_length'] == entity['copy_bytes']
          and hashlib.sha256(raw).hexdigest() == o['sha256'] == entity['copy_sha256'], 'original entity bytes/hash')
    check(not framing['content_length'] or framing['content_length'][0].isascii()
          and framing['content_length'][0].isdecimal() and int(framing['content_length'][0]) == len(raw), 'complete declared entity length')
    check(set(f) == {'segments','byte_length','sha256','finished'} and type(f['segments']) is list
          and len(f['segments']) <= 16384, 'closed forwarding grammar')
    offset = 0
    for segment in f['segments']:
        check(set(segment) == {'offset','length','sha256','write_started','write_returned','flush_returned','written'}
              and type(segment['offset']) is int and type(segment['length']) is int
              and segment['offset'] == offset and segment['length'] > 0
              and type(segment['written']) is int and segment['written'] == segment['length'], 'actual contiguous full write prefix')
        part = raw[offset:offset+segment['length']]
        check(len(part) == segment['length'] and hashlib.sha256(part).hexdigest() == segment['sha256'], 'forwarded original segment digest')
        check(all(number(segment[k]) for k in ('write_started','write_returned','flush_returned'))
              and record['entered'] <= segment['write_started'] <= segment['write_returned'] <= segment['flush_returned'] <= record['delivered'], 'separate actual write and flush returns')
        offset += segment['length']
    check(offset == f['byte_length'] == o['byte_length'] and f['sha256'] == o['sha256']
          and number(f['finished']) and o['finished'] <= f['finished'] <= record['delivered'] <= record['closed'], 'complete forwarding, never browser receipt')
    def pairs(items):
        result = {}
        for k,v in items:
            check(k not in result, 'duplicate original JSON key')
            result[k] = v
        return result
    def constant(_value):
        raise ValueError('E05 original: nonfinite original JSON')
    return json.loads(raw.decode('utf8'), object_pairs_hook=pairs, parse_constant=constant)


def validate_e05_originals(observations: dict, directory: Path) -> dict:
    """Census every required native Response and independently recompute joins."""
    import re
    def check(value, reason):
        if not value:
            raise ValueError('E05 originals: ' + reason)
    events = json.loads((directory/'e05-browser-events.json').read_text())
    inventory = observations['capture_inventory']
    check(set(inventory) == {'refusals','closed','requests','retained_bytes','retained_facts'} and inventory['refusals'] == []
          and type(inventory['closed']) in (int,float) and math.isfinite(inventory['closed']), 'closed complete inventory, no refusal')
    check(type(inventory['retained_bytes']) is int and 0 <= inventory['retained_bytes'] <= 8*1024*1024
          and type(inventory['retained_facts']) is int and 0 <= inventory['retained_facts'] <= 16384,
          'whole-context reserved byte/fact bounds')
    requests = [e for e in events if e['event'] == 'request']
    check([e['request_id'] for e in requests] == list(range(len(requests)))
          and all(type(e['request_id']) is int for e in requests), 'unique complete Request census')
    check(len(requests) <= 512 and len(events) <= 16384 and len(inventory['requests']) == len(requests)
          and all(len(e['url'].encode('utf8')) <= 8192 for e in requests), 'complete original Request inventory')
    case = next(d['threads_cases'] for d in observations['durable'] if 'threads_cases' in d)
    origin = 'http://127.0.0.1:' + str(case['transport']['browser_port'])
    for issued, retained in zip(requests,inventory['requests']):
        check(issued['url'].startswith(origin+'/'), 'native Request origin safety')
        expected = dict(issued,target=issued['url'][len(origin):])
        terminals = [e for e in events if e['event'] in {'request-failed','request-finished'} and e['request_id'] == issued['request_id']]
        check(len(terminals) <= 1, 'unique original Request terminal')
        if terminals:
            expected['terminals'] = terminals
            if terminals[0]['event'] == 'request-failed':
                expected['failed'] = terminals[0]['at']
        check(retained == expected, 'exact original Request/terminal inventory')
    required_requests = [e for e in requests if
        e['method'] == 'POST' and e['url'].split('?')[0] == origin+'/api/v1/orgs/alpha/threads'
        or e['method'] == 'GET' and re.fullmatch(re.escape(origin)+r'/api/v1/orgs/alpha/threads/THR-\d+(?:/messages)?',e['url'].split('?')[0])]
    native = [e for e in events if e['event'] in {'compose-response','thread-response'}]
    check([e['response_id'] for e in native] == list(range(len(native)))
          and all(type(e['response_id']) is int and type(e['request_id']) is int for e in native)
          and len({e['request_id'] for e in native}) == len(native) <= 512, 'exact unique Response objects')
    check({e['request_id'] for e in required_requests} == {e['request_id'] for e in native}
          and len(required_requests) == len(native), 'every required native Request has its own Response')
    # Replay the ORIGINAL order, never search a future Request to establish an
    # issuing document. Main-frame/unredirected facts come from native objects;
    # the pinned main-frame commit callback supplies the commit observation.
    check(all(type(e['at']) in (int,float) and math.isfinite(e['at']) for e in events)
          and all(a['at'] <= b['at'] for a,b in zip(events,events[1:])), 'chronological native document ledger')
    seen, committed, pending = {}, set(), set()
    current_document = None
    for event in events:
        if event['event'] == 'request':
            check(all(type(event.get(k)) is bool for k in ('navigation','main_frame','unredirected')),
                  'native main-frame and redirect provenance available')
            check(type(event['pending_documents']) is list
                  and all(type(i) is int for i in event['pending_documents'])
                  and event['pending_documents'] == sorted(pending), 'consistent original pending document snapshot')
            expected_document = current_document if event['main_frame'] and event['unredirected'] else None
            check(event['document_request_id'] is None if expected_document is None else
                  type(event['document_request_id']) is int and event['document_request_id'] == expected_document,
                  'actual committed issuing document at Request issuance')
            if event in required_requests:
                check(event['main_frame'] and event['unredirected'] and current_document is not None and not pending,
                      'unambiguous issuing document at issue time')
            seen[event['request_id']] = event
            if event['navigation'] and event['main_frame']:
                pending.add(event['request_id'])
        elif event['event'] == 'navigation':
            document_id = event['document_request_id']
            check(type(event['new_document']) is bool and type(document_id) is int
                  and event['url'].startswith(origin+'/'), 'typed same-origin native document commit lineage')
            if event['new_document']:
                document = seen.get(document_id)
                check(document is not None and document_id not in committed and document_id in pending
                      and document['navigation'] and document['main_frame'] and document['unredirected']
                      and document['resource_type'] == 'document' and document['method'] == 'GET'
                      and document['url'] == event['url'] and document['at'] <= event['at'],
                      'actual earlier unredirected main-frame GET document commit')
                committed.add(document_id)
                pending.remove(document_id)
                current_document = document_id
            else:
                check(current_document is not None and document_id == current_document and not pending,
                      'same-document retains committed lineage without pending navigation')
    proxies = observations['thread_proxy']
    check(len(proxies) <= 512 and [r['relay_id'] for r in proxies] == list(range(len(proxies))), 'complete relay membership including late requests')
    def required(r):
        return (r['method'] == 'POST' and r['ordinal'] is not None
                or r['method'] == 'GET' and re.fullmatch(r'/api/v1/orgs/alpha/threads/THR-\d+(?:/messages)?',r['target'].split('?')[0]))
    members = [r for r in proxies if required(r)]
    check(len(members) == len(native) <= 512 and all('original' in r for r in members)
          and all(r['closed'] <= inventory['closed'] for r in members), 'every automatic/view/revisit required original is retained')
    check(sum(r['original']['byte_length']+r['original']['header_bytes'] for r in members) <= 8*1024*1024
          and sum(len(r['forwarding']['segments'])+1 for r in members) <= 16384, 'whole-context original bounds')
    check(sum(r['original']['byte_length']+r['original']['header_bytes'] for r in members) <= inventory['retained_bytes']
          and len(proxies)+len(requests)+sum(len(r['forwarding']['segments'])+4 for r in members) <= inventory['retained_facts'],
          'complete retained membership has original byte/fact reservations')
    case = next(d['threads_cases'] for d in observations['durable'] if 'threads_cases' in d)
    origin = 'http://127.0.0.1:' + str(case['transport']['browser_port'])
    def candidates(e, response):
        terminal = [t for t in events if t['event'] in {'request-finished','request-failed'} and t['request_id'] == e['request_id']]
        check(len(terminal) == 1 and terminal[0]['event'] == 'request-finished'
              and terminal[0]['url'] == e['url'] and terminal[0]['method'] == e['method'], 'exact successful original native terminal')
        check(e['at'] <= response['at'] <= terminal[0]['at']
              and events.index(e) <= events.index(response) <= events.index(terminal[0]),
              'exact Request <= Response <= successful terminal interval')
        return [r for r in members if r['method'] == e['method'] and origin+r['target'] == e['url']
                and r['entered'] <= terminal[0]['at'] and r['closed'] >= e['at']]
    matches = {}
    for response in native:
        issued = [e for e in requests if e['request_id'] == response['request_id']]
        check(len(issued) == 1 and issued[0]['url'] == response['url'] and issued[0]['method'] == response['method'], 'exact Response.request identity')
        issued = issued[0]
        check(type(issued.get('document_request_id')) is int and issued.get('pending_documents') == [], 'unambiguous issuing document at issue time')
        options = candidates(issued, response)
        check(len(options) == 1 and options[0]['relay_id'] not in {r['relay_id'] for r in matches.values()}, 'full-window unique bijection; no URL/ordinal fallback')
        wire = options[0]
        check(response['status'] == wire['status'] == 200 and wire['original']['required_kind'] ==
              ('compose' if response['event'] == 'compose-response' else 'messages' if wire['path'].endswith('/messages') else 'detail'), 'required response kind/status')
        matches[response['request_id']] = wire
    check(len({r['relay_id'] for r in matches.values()}) == len(members), 'no omitted or reused required member')
    originals = observations['thread_originals']
    check(len(originals) == len(native), 'final original observations include every response')
    bodies = {}
    for response, original in zip(native, originals):
        wire = matches[response['request_id']]
        body = validate_original_member(wire)
        check(set(original) == {'relay_id','request_id','origin','body','bytes','sha256','response_id'}
              and original['origin'] == 'relay-original' and original['relay_id'] == wire['relay_id']
              and original['request_id'] == response['request_id'] and original['response_id'] == response['response_id']
              and original['body'] == body and original['bytes'] == wire['original']['byte_length']
              and original['sha256'] == wire['original']['sha256'], 'full original object/content reference')
        bodies[response['request_id']] = original
    return bodies


def validate_e05_safety(observations: dict, events: list[dict]) -> None:
    """Body-independent native census and COMPLETE original GET/stop proof."""
    def check(value, reason):
        if not value:
            raise ValueError('E05 safety: ' + reason)
    durable = observations['durable']
    case = next(d['threads_cases'] for d in durable if 'threads_cases' in d)
    case_end = case.get('finished', case['cleanup_finished'])
    transport = case['transport']
    origin = f"http://127.0.0.1:{transport['browser_port']}"
    def number(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(value)

    def natural(value: object) -> bool:
        return type(value) is int and value >= 0

    def same(left: object, right: object) -> bool:
        # JSON equality must preserve bool/int/float distinctions in snapshots.
        return json.dumps(left,sort_keys=True,allow_nan=False) == json.dumps(right,sort_keys=True,allow_nan=False)

    event_fields = {
        'request': 'request_id url method resource_type navigation main_frame unredirected document_request_id pending_documents',
        'request-failed': 'request_id url method failure', 'request-finished': 'request_id url method',
        'stream-response': 'request_id url method status',
        'context-closing': 'request_ids', 'proxy-streams-shutdown': 'streams',
        'context-close': '', 'context-closed': '',
        'compose-request': 'url method sha256 bytes payload', 'compose-response': 'url method status request_id response_id browser_copy sha256 bytes body',
        'compose-created': 'thread dialog_count list_rows url list_url',
        'navigation': 'url new_document document_request_id', 'deliberate-revisit': 'url',
        'snapshot': 'name', 'event-batch': 'rows guard_sensitivity', 'thread-response': 'url method status request_id response_id browser_copy sha256 bytes body',
        'thread-view': 'thread body original_body original_ref messages_original_ref request_id response_id messages_request_id messages_response_id browser_copy text screenshot url response_url method status messages_body original_messages_body messages_url messages_status messages_method rendered_messages',
    }
    check(all(e.get('event') in event_fields and set(e) == {'event', 'at', *event_fields[e['event']].split()}
              for e in events), 'R2 closed original browser event grammar')
    check(all(type(e['at']) in (int,float) and math.isfinite(e['at']) for e in events)
          and [e['at'] for e in events] == sorted(e['at'] for e in events),
          'R2 complete chronological native browser ledger')
    native_requests = [e for e in events if e['event'] == 'request']
    check([e['request_id'] for e in native_requests] == list(range(len(native_requests)))
          and all(type(e['request_id']) is int for e in native_requests), 'R2 unique native Request ledger')
    for event in events:
        if event['event'] in {'request-failed', 'request-finished', 'stream-response'}:
            issued = [e for e in native_requests if e['request_id'] == event['request_id']]
            check(type(event['request_id']) is int and len(issued) == 1 and issued[0]['url'] == event['url']
                  and issued[0]['method'] == event['method'] and issued[0]['at'] <= event['at'],
                  'R2 native response/terminal event belongs to original Request')
    for issued in native_requests:
        terminals = [e for e in events if e['event'] in {'request-failed', 'request-finished'}
                     and e['request_id'] == issued['request_id']]
        check(len(terminals) <= 1, 'R2 no duplicate or contradictory native Request terminal')
        check(all(r['at'] <= t['at'] for r in events if r['event'] == 'stream-response'
                  and r['request_id'] == issued['request_id'] for t in terminals),
              'R2 native response precedes its exact Request terminal')
    check(events and events[-1]['event'] == 'context-closed', 'real browser origin/closure')
    closure = [d["thread_proxy_closure"] for d in durable if "thread_proxy_closure" in d]
    check(len(closure) == 1 and closure[0]["port"] == transport["browser_port"] and not closure[0]["errors"]
          and closure[0]["workers_alive"] == closure[0]["connections"] == 0 and closure[0]["listener_alive"] is False, "bounded proxy/socket/listener closure")
    check(set(closure[0]) == {'port','entered','finished','workers_alive','listener_alive','connections','errors'}
          and type(closure[0]['port']) is int and type(closure[0]['workers_alive']) is int
          and type(closure[0]['connections']) is int and closure[0]['errors'] == []
          and number(closure[0]['entered']) and number(closure[0]['finished']),
          'R2 closed typed original proxy closure')
    check(case.get('final_entered',case['entered']) <= events[-1]['at'] <= closure[0]['entered'] <= closure[0]['finished'] <= case_end,
          'R2 browser and proxy close within original E05 final allowance')
    lifecycle = [e for e in events if e['event'] in {'context-closing', 'proxy-streams-shutdown', 'context-close', 'context-closed'}]
    check([e['event'] for e in lifecycle] == ['context-closing', 'proxy-streams-shutdown', 'context-close', 'context-closed']
          and case.get('final_entered',case['entered']) <= lifecycle[0]['at'] <= lifecycle[1]['at'] <= lifecycle[2]['at']
          <= lifecycle[3]['at'] <= closure[0]['entered'], 'R2 initiating owner, native context close and actual return')
    closing, shutdown_event, native_close, returned = lifecycle
    streams = [e for e in events if e['event'] == 'stream-response']
    check(len({e['request_id'] for e in streams}) == len(streams)
          and all(e['method'] == 'GET' and type(e['status']) is int and e['status'] == 200
                  and e['url'].startswith(origin+'/') for e in streams), 'R2 unique native same-origin SSE responses')
    live = sorted(e['request_id'] for e in streams if e['at'] <= closing['at']
                  and not any(t['event'] in {'request-failed','request-finished'} and t['request_id'] == e['request_id']
                              and t['at'] <= closing['at'] for t in events))
    check(closing['request_ids'] == live and all(type(i) is int for i in closing['request_ids'])
          and not any(e['at'] > closing['at'] for e in streams), 'R2 exact still-live native SSE set before owner shutdown')
    shutdowns = shutdown_event['streams']
    check(isinstance(shutdowns, list) and len({s['intent']['request_id'] for s in shutdowns}) == len(shutdowns)
          and sorted(s['intent']['request_id'] for s in shutdowns) == live, 'R2 every original live native stream shutdown accounted')
    for shutdown in shutdowns:
        check(type(shutdown) is dict and set(shutdown) == {'intent','ack','settled'},
              'R2 closed worker stream acknowledgement')
        owners = [p for p in observations['thread_proxy']
                  if same(p.get('stop_intent'), shutdown['intent']) and same(p.get('stop_ack'), shutdown['ack'])
                  and same(p.get('stop_settled'), shutdown['settled'])]
        check(len(owners) == 1 and shutdown['intent']['kind'] == 'context'
              and same(shutdown['intent']['event'], closing) and shutdown['ack']['outcome'] == 'shutdown'
              and shutdown['settled'] <= shutdown_event['at'], 'R2 exact complete stream worker acknowledgement join')
    def validate_io_owner(proxied: dict) -> None:
        owner = proxied['io_owner']
        generation = proxied['owner_generation']
        check(type(generation) is int and generation > 0 and generation not in owner_generations
              and type(owner) is dict
              and set(owner) == {'runtime','generation','end','resources','registrations','trace','operations','received','delivered','forced','terminal','mode','errors','finished'}
              and type(owner['generation']) is int and owner['generation'] == generation and owner['forced'] is None and owner['errors'] == []
              and number(owner['end']) and number(owner['finished'])
              and proxied['entered'] <= owner['finished'] <= proxied['closed'] <= owner['end']
              and proxied['method'] == 'GET' and proxied['ordinal'] is None,
              'R2 original positive GET owner generation, deadline and sticky closure')
        check(all(number(proxied[k]) for k in ('entered','forwarded','closed')),
              'R2 original owner times have strict finite numeric types')
        owner_generations.add(generation)
        runtime = owner['runtime']
        check(type(runtime) is dict and set(runtime) == {'implementation','version'}
              and runtime['implementation'] == 'cpython' and type(runtime['version']) is list
              and len(runtime['version']) == 3 and all(natural(v) for v in runtime['version'])
              and runtime['version'][:2] in ([3,12],[3,14]),
              'R2 supported exact-minor private parser owner')
        mode = owner['mode']
        check(type(mode) is dict and set(mode) == {'original_blocking','duplicate_blocking','at'}
              and mode['original_blocking'] is False and mode['duplicate_blocking'] is False
              and number(mode['at']) and proxied['forwarded'] <= mode['at'] <= owner['finished'],
              'R2 actual shared original/duplicate nonblocking descriptor modes')
        resources = owner['resources']
        check(type(resources) is list and len(resources) == 10
              and {r.get('name') for r in resources}
              == {'connection','original','duplicate','wake_read','wake_write','selector','raw','buffer','file','response'},
              'R2 every original/duplicate/parser/buffer/selector/wake owner inventoried')
        check(all(set(r) == {'name','acquired','closed'} and number(r['acquired']) and number(r['closed'])
                  and proxied['entered'] <= r['acquired'] <= r['closed'] <= owner['finished'] for r in resources)
              and [r['acquired'] for r in resources] == sorted(r['acquired'] for r in resources),
              'R2 complete actual per-handle closure, never workers-zero inference')
        by_name = {r['name']:r for r in resources}
        check(by_name['connection']['acquired'] <= by_name['original']['acquired']
              <= by_name['duplicate']['acquired'] <= mode['at']
              and by_name['raw']['acquired'] <= by_name['buffer']['acquired']
              <= by_name['file']['acquired'] <= by_name['response']['acquired'],
              'R2 mode and parser wrappers belong to their already-acquired underlying handles')
        registrations = owner['registrations']
        check(type(registrations) is list and len(registrations) == 2
              and {r.get('name') for r in registrations} == {'duplicate','wake_read'}
              and all(set(r) == {'name','registered','unregistered'} and number(r['registered'])
                      and number(r['unregistered'])
                      and max(by_name['selector']['acquired'],by_name[r['name']]['acquired'],mode['at'])
                      <= r['registered'] <= by_name['file']['acquired']
                      and r['registered'] <= r['unregistered']
                      <= min(h['closed'] for h in resources) for r in registrations),
              'R2 actual selector registrations removed before any acquired handle close')
        trace = owner['trace']
        check(type(trace) is list and 0 < len(trace) <= 16384
              and all(type(t) is dict and number(t.get('at')) for t in trace)
              and [t['at'] for t in trace] == sorted(t['at'] for t in trace)
              and proxied['forwarded'] <= trace[0]['at'] <= trace[-1]['at'] <= owner['finished'],
              'R2 chronological raw original reader accounting')
        received = delivered = 0
        checkpoints = []
        last_file = None
        chunk_remaining = None
        size_pending = False
        eof_seen = False
        chunk_terminal_seen = False
        trailer_ended = False
        for fact in trace:
            if fact['event'] == 'receive':
                check(set(fact) == {'event','at','bytes','received'} and natural(fact['bytes']) and fact['bytes'] <= 65536, 'R2 original socket receive including actual EOF')
                check(not eof_seen and not trailer_ended, 'R2 no receive after native EOF or complete chunk terminal')
                eof_seen = fact['bytes'] == 0
                received += fact['bytes']
                check(fact['received'] == received and type(fact['received']) is int,
                      'R2 actual cumulative socket receive count')
            elif fact['event'] == 'would-block':
                check(set(fact) == {'event','at','received','delivered'}
                      and natural(fact['received']) and natural(fact['delivered'])
                      and fact['received'] == received and fact['delivered'] == delivered,
                      'R2 actual would-block observation preserves buffered progress')
            elif fact['event'] in {'chunk-terminal','trailer-end'}:
                extra = 'size' if fact['event'] == 'chunk-terminal' else 'delimiter'
                check(set(fact) == {'event','at','received','delivered',extra}
                      and natural(fact['received']) and natural(fact['delivered'])
                      and fact['received'] == received and fact['delivered'] == delivered
                      and last_file is not None and last_file['method'] == 'readline',
                      'R2 original terminal chunk/trailer counters')
                if fact['event'] == 'chunk-terminal':
                    check(not chunk_terminal_seen and type(fact['size']) is int and fact['size'] == 0 and size_pending
                          and last_file['bytes'] >= 3, 'R2 actual parsed terminal zero chunk')
                    chunk_terminal_seen = True
                    size_pending = False
                    chunk_remaining = 0
                else:
                    check(chunk_terminal_seen and not trailer_ended and fact['delimiter'] == '0d0a'
                          and chunk_remaining == 0 and last_file['bytes'] == 2,
                          'R2 actual complete trailer terminator')
                    trailer_ended = True
            elif fact['event'] == 'file-return':
                check(not trailer_ended and (not chunk_terminal_seen or fact['method'] == 'readline'),
                      'R2 terminal chunk permits only its original trailer reads')
                check(set(fact) == {'event','at','method','bytes','delivered'}
                      and fact['method'] in {'read','read1','readline'} and natural(fact['bytes']),
                      'R2 closed parser file-return counters')
                delivered += fact['bytes']
                check(type(fact['delivered']) is int and fact['delivered'] == delivered <= received,
                      'R2 buffered and pending file bytes never vanish')
                if fact['method'] == 'read':
                    check(fact['bytes'] == 2, 'R2 original parser consumes exactly the chunk CRLF')
                if fact['method'] == 'read1' and proxied.get('response_outcome', {}).get('chunked') is True:
                    check(type(chunk_remaining) is int and 0 < fact['bytes'] <= chunk_remaining,
                          'R2 body return consumes only known current chunk payload')
                    chunk_remaining -= fact['bytes']
                last_file = fact
            elif fact['event'] == 'checkpoint':
                check(set(fact) == {'event','at','kind','received','delivered','chunk_left','delimiter','remaining'}
                      and fact['kind'] in {'before-response','between','chunk-size','payload','finite'}
                      and natural(fact['received']) and natural(fact['delivered'])
                      and fact['received'] == received and fact['delivered'] == delivered,
                      'R2 raw semantic checkpoint counters and closed grammar')
                kind = fact['kind']
                check(not chunk_terminal_seen or trailer_ended and kind == 'between',
                      'R2 terminal chunk cannot reopen a cancellable parser checkpoint')
                check((fact['chunk_left'] is None or type(fact['chunk_left']) is int and fact['chunk_left'] > 0)
                      and (fact['remaining'] is None or type(fact['remaining']) is int and fact['remaining'] > 0)
                      and (kind == 'payload') == (fact['chunk_left'] is not None)
                      and (kind == 'finite') == (fact['remaining'] is not None)
                      and (kind == 'chunk-size' or fact['delimiter'] is None),
                      'R2 checkpoint-specific framing/finite shape')
                if kind == 'before-response':
                    check(not checkpoints and received == delivered == 0, 'R2 entire-response byte-zero checkpoint')
                if kind == 'chunk-size':
                    check(fact['delimiter'] is None and chunk_remaining is None
                          or fact['delimiter'] == '0d0a' and chunk_remaining == 0 and last_file is not None
                          and last_file['method'] == 'read' and last_file['bytes'] == 2,
                          'R2 fresh chunk-size waits follow actual validated CRLF')
                    size_pending = True
                if kind == 'payload':
                    check((size_pending and last_file is not None and last_file['method'] == 'readline'
                           and last_file['bytes'] >= 3)
                          or (not size_pending and chunk_remaining is not None
                              and fact['chunk_left'] == chunk_remaining),
                          'R2 fresh parsed positive size or exact retained chunk remainder')
                    chunk_remaining = fact['chunk_left']
                    size_pending = False
                checkpoints.append(fact)
            else:
                check(False, 'R2 unknown or sticky failed raw reader event')
        check(natural(owner['received']) and natural(owner['delivered'])
              and owner['received'] == received and owner['delivered'] == delivered,
              'R2 full raw/buffer accounting retained through closure')
        operations = owner['operations']
        check(type(operations) is list and operations
              and [o.get('id') for o in operations] == list(range(1,len(operations)+1))
              and same(proxied.get('upstream_operation'), operations[-1]),
              'R2 complete unique current/prior operation identities')
        reads = []
        previous = None
        for op in operations:
            check(type(op['id']) is int and set(op) in (
                      {'id','kind','read_id','started','finished','outcome','result'},
                      {'id','kind','read_id','started','finished','outcome'})
                  and op['kind'] in {'getresponse','read','stop'}
                  and number(op['started']) and number(op['finished'])
                  and proxied['forwarded'] <= op['started'] <= op['finished'] <= owner['finished']
                  and (previous is None or previous['finished'] <= op['started'])
                  and op.get('outcome') in {'returned','cancelled'},
                  'R2 complete chronological operations; earlier error is never laundered')
            if op['kind'] == 'read':
                check(type(op['read_id']) is int and op['read_id'] == len(reads)+1,
                      'R2 positive distinct actual read identity')
                reads.append(op)
            else:
                check(op['read_id'] is None, 'R2 only actual body read owns a read ID')
            if op['outcome'] == 'cancelled':
                check(op is operations[-1] and set(op) == {'id','kind','read_id','started','finished','outcome'},
                      'R2 explicit current cancellation never invents returned bytes')
            elif op['kind'] == 'getresponse':
                check(op is operations[0] and same(op['result'], proxied.get('response_outcome')),
                      'R2 original returned headers retained verbatim')
            else:
                check(op['kind'] == 'read' and previous is not None and previous['outcome'] == 'returned',
                      'R2 returned body follows complete validated headers')
                result = op['result']
                check(set(result) == {'bytes','remaining_before','remaining_after','closed','eof'}
                      and natural(result['bytes']) and result['bytes'] <= 65536 and type(result['closed']) is bool and type(result['eof']) is bool
                      and result['eof'] == (result['bytes'] == 0)
                      and all(v is None or natural(v) for v in (result['remaining_before'],result['remaining_after'])),
                      'R2 original returned bytes/EOF/length facts')
                before, after = result['remaining_before'], result['remaining_after']
                expected = (proxied['response_outcome']['length'] if previous['kind'] == 'getresponse'
                            else previous['result']['remaining_after'])
                check(before == expected and (before is None and after is None or before is not None
                      and after == before-result['bytes'])
                      and (not (result['closed'] or result['eof']) or after in (None,0))
                      and (after != 0 or result['closed']) and (not result['eof'] or result['closed'])
                      and (not proxied['streaming'] or not result['closed'] and not result['eof'])
                      and (previous['kind'] != 'read' or not previous['result']['closed']),
                      'R2 every prior finite/stream outcome validates before any stop')
            body_returns = [t for t in trace if t['event'] == 'file-return' and t['method'] == 'read1'
                            and op['started'] <= t['at'] <= op['finished']]
            if op['outcome'] == 'cancelled':
                check(not body_returns and not any(t['event'] in {'chunk-terminal','trailer-end'}
                      and op['started'] <= t['at'] <= op['finished'] for t in trace),
                      'R2 no returned body or terminal chunk outcome is discarded by cancellation')
            elif op['kind'] == 'read':
                terminal_facts = [t for t in trace if op['started'] <= t['at'] <= op['finished']
                                  and t['event'] in {'chunk-terminal','trailer-end'}]
                check((len(body_returns) == 1 and body_returns[0]['bytes'] == op['result']['bytes'])
                      or (not body_returns and op['result']['bytes'] == 0 and op['result']['closed'] is True
                          and op['result']['eof'] is True and not proxied['streaming']
                          and proxied['response_outcome']['chunked'] is True
                          and [t['event'] for t in terminal_facts] == ['chunk-terminal','trailer-end']),
                      'R2 actual parser body return equals retained operation bytes')
            else:
                check(not body_returns, 'R2 header operation cannot consume body payload')
            previous = op
        # These are final explicit successful close acknowledgements, rather
        # than HTTPConnection detach or HTTPResponse's automatic file close.
        # Every inventoried owner and registration is retained until finally,
        # after the last operation (including any shutdown ack) and checkpoint.
        last_use = max(operations[-1]['finished'], trace[-1]['at'])
        check(all(last_use <= r['closed'] for r in resources)
              and all(last_use <= r['unregistered'] for r in registrations),
              'R2 final handle closure and unregister follow all worker I/O, validation and shutdown')
        first = operations[0]
        check(first['kind'] == 'getresponse' and trace[0]['event'] == 'checkpoint'
              and trace[0]['kind'] == 'before-response'
              and all(r['acquired'] <= trace[0]['at'] for r in resources if r['name'] != 'response')
              and trace[0]['at'] <= first['started'] <= by_name['response']['acquired'] <= first['finished']
              and all(by_name['response']['acquired'] <= op['started'] for op in operations[1:]),
              'R2 initialized reader precedes first call and response is acquired inside getresponse before later uses')
        for index, op in enumerate(operations):
            completed = [c for c in checkpoints if c['kind'] == 'between'
                         and op['finished'] <= c['at']
                         and (index+1 == len(operations) or c['at'] <= operations[index+1]['started'])]
            check(len(completed) == (1 if op['outcome'] == 'returned' else 0),
                  'R2 each validated return establishes its own between checkpoint before the next operation')
        if proxied.get('streaming') and reads:
            read = proxied.get('stream_read')
            last = reads[-1]
            check(type(read) is dict and read['id'] == last['read_id']
                  and type(read['id']) is int and number(read['started']) and number(read['finished'])
                  and read['started'] == last['started'] and read['finished'] == last['finished']
                  and (set(read) == {'id','started','finished','bytes'} and last['outcome'] == 'returned'
                       and natural(read['bytes']) and read['bytes'] == last['result']['bytes']
                       or set(read) == {'id','started','finished','outcome'} and read['outcome'] == last['outcome'] == 'cancelled'),
                  'R2 current/prior stream read remains validated even with null stop read ID')
        else:
            check('stream_read' not in proxied, 'R2 genuine no-prior-stream-read state')
        for index, fact in enumerate(trace):
            if fact['event'] != 'checkpoint':
                check(len([op for op in operations if op['kind'] != 'stop'
                           and op['started'] <= fact['at'] <= op['finished']]) == 1
                      and by_name['response']['acquired'] <= fact['at'],
                      'R2 raw I/O belongs to exactly one actual parser operation')
            elif fact['kind'] == 'between':
                establishing = [op for i, op in enumerate(operations) if op['outcome'] == 'returned'
                                and op['finished'] <= fact['at']
                                and (i+1 == len(operations) or fact['at'] <= operations[i+1]['started'])]
                check(len(establishing) == 1,
                      'R2 between checkpoint follows its exact completed validation, outside later operations')
            elif fact['kind'] != 'before-response':
                establishing = [op for op in reads if op['started'] <= fact['at'] <= op['finished']]
                check(len(establishing) == 1 and establishing[0]['id'] > 1,
                      'R2 framing checkpoint belongs to one actual body read after headers')
                current_read = establishing[0]
                preceding = operations[current_read['id']-2]
                check(preceding['outcome'] == 'returned' and preceding['finished'] <= current_read['started'],
                      'R2 framing checkpoint preserves the distinct prior validated operation')
                raw_before = [t for t in trace[:index] if t['event'] != 'checkpoint'
                              and current_read['started'] <= t['at']]
                files_before = [t for t in raw_before if t['event'] == 'file-return']
                if fact['kind'] == 'finite':
                    remaining = (preceding['result']['remaining_after'] if preceding['kind'] == 'read'
                                 else proxied['response_outcome']['length'])
                    check(proxied['response_outcome']['chunked'] is False and not raw_before
                          and type(remaining) is int and remaining > 0 and fact['remaining'] == remaining,
                          'R2 finite checkpoint precedes raw work in its exact remaining-length read')
                else:
                    check(proxied['response_outcome']['chunked'] is True,
                          'R2 chunk framing checkpoints require actual chunked headers')
                    if fact['kind'] == 'chunk-size':
                        check(not raw_before if fact['delimiter'] is None else
                              bool(files_before) and files_before[-1]['method'] == 'read'
                              and files_before[-1]['bytes'] == 2,
                              'R2 size checkpoint follows its own read delimiter validation or first chunk entry')
                    else:
                        check(not raw_before or bool(files_before) and files_before[-1]['method'] == 'readline',
                              'R2 payload checkpoint follows its own parsed size or retained chunk entry')
        eof_facts = [t for t in trace if t['event'] == 'receive' and t['bytes'] == 0]
        last_return = next((o for o in reversed(operations) if o.get('outcome') == 'returned'),None)
        check((owner['terminal'] is None and not eof_facts)
              or (owner['terminal'] == 'EOF' and len(eof_facts) == 1
                  and last_return is not None and last_return['kind'] == 'read'
                  and last_return['result']['eof'] is True and not proxied['streaming']
                  and proxied['response_outcome']['length'] is None
                  and (operations[-1] is last_return or operations[-1]['kind'] == 'stop')),
              'R2 native EOF/error stays sticky; only real complete finite EOF can settle')
        intent, ack = proxied.get('stop_intent'), proxied.get('stop_ack')
        if intent is None:
            check(ack is None and 'stop_settled' not in proxied and 'browser_abort' not in proxied
                  and operations[-1]['outcome'] == 'returned' and received == delivered, 'R2 no detached stop facts or discarded buffer')
            return
        check(type(intent) is dict and set(intent) == {'kind','request_id','method','target','generation','event','operation_id','enqueued','end'}
              and intent['kind'] in {'navigation','context'} and intent['method'] == 'GET'
              and type(intent['request_id']) is int and intent['request_id'] == proxied.get('browser_request_id')
              and intent['target'] == proxied['target'] and type(intent['generation']) is int
              and intent['generation'] == generation and number(intent['enqueued']) and number(intent['end'])
              and proxied['entered'] <= intent['enqueued'] <= intent['end'] <= owner['end']
              and intent['event']['at'] <= intent['enqueued'],
              'R2 exact original Request/target/generation stop intent')
        observed_operations = [op for op in operations if op['started'] <= intent['enqueued']]
        check(intent['operation_id'] == (observed_operations[-1]['id'] if observed_operations else None)
              and (intent['operation_id'] is None or type(intent['operation_id']) is int),
              'R2 original operation identity at intent enqueue, distinct from later stop')
        check(type(ack) is dict and set(ack) == {'generation','request_id','operation_id','previous_operation_id','read_id',
              'previous_read_id','checkpoint','received','delivered','buffered','since_checkpoint','would_block','started','finished','outcome'}
              and type(ack['generation']) is int and ack['generation'] == generation
              and type(ack['request_id']) is int and ack['request_id'] == intent['request_id']
              and number(ack['started']) and number(ack['finished'])
              and intent['enqueued'] <= ack['started'] <= ack['finished'] <= operations[-1]['finished']
              <= proxied.get('failure_at',-1) <= owner['finished'] <= proxied['closed'] <= proxied.get('stop_settled',-1)
              <= intent['end'] and proxied.get('error') == 'Stopped' and 'delivered' not in proxied
              and proxied.get('failure_operation') in {'upstream','downstream'}
              and (operations[-1]['kind'] == 'stop' or proxied['failure_operation'] == 'upstream'),
              'R2 actual original worker stop and complete owner acknowledgement under original end')
        current = operations[-1]
        prior = operations[-2] if len(operations) > 1 else None
        check(type(ack['operation_id']) is int and ack['operation_id'] == current['id'] and current['outcome'] == 'cancelled'
              and ack['previous_operation_id'] == (prior['id'] if prior else None)
              and ack['read_id'] == current['read_id'] and ack['previous_read_id'] == (prior['read_id'] if prior else None)
              and all(v is None or type(v) is int and v > 0 for v in
                      (ack['previous_operation_id'],ack['read_id'],ack['previous_read_id']))
              and all(natural(ack[k]) for k in ('received','delivered','buffered','since_checkpoint'))
              and ack['received'] == received == ack['delivered'] == delivered
              and ack['buffered'] == ack['since_checkpoint'] == 0 and type(ack['would_block']) is bool,
              'R2 distinct real current/prior IDs and empty entire buffered checkpoint')
        candidates = [c for c in checkpoints if c['at'] <= ack['started']]
        check(candidates and same(ack['checkpoint'], {k:v for k,v in candidates[-1].items() if k not in {'event','at'}})
              and candidates[-1]['received'] == received and candidates[-1]['delivered'] == delivered
              and not any(t['at'] > ack['started'] for t in trace),
              'R2 acknowledgement matches actual last validated checkpoint, with no hidden progress')
        cp = ack['checkpoint']
        check(ack['outcome'] in {'shutdown','already-complete'}
              and (cp['kind'] == 'between') == (current['kind'] == 'stop' and ack['would_block'] is False),
              'R2 wait boundary requires actual would-block; completed boundary stays separate')
        if cp['kind'] != 'between':
            check(ack['would_block'] is True and trace[-1]['event'] == 'would-block'
                  and current['started'] <= trace[-1]['at'] <= ack['started']
                  and (cp['kind'] == 'before-response' or current['started'] <= candidates[-1]['at']), 'R2 actual nonblocking wait observation')
        if cp['kind'] == 'before-response':
            check(len(operations) == 1 and current['kind'] == 'getresponse' and received == delivered == 0
                  and intent['kind'] == 'navigation' and 'response_outcome' not in proxied
                  and not any(k in proxied for k in ('status','streaming','stream_read')),
                  'R2 genuine cancelled-before-response, no fabricated status/read/header outcome')
        else:
            check(prior is not None and prior['outcome'] == 'returned' and 'response_outcome' in proxied,
                  'R2 actual prior validated outcome survives cancellation')
            if cp['kind'] in {'chunk-size','payload'}:
                check(current['kind'] == 'read' and proxied['response_outcome']['chunked'] is True,
                      'R2 fresh chunk boundary belongs to actual chunked read')
            if cp['kind'] == 'finite':
                expected = (prior['result']['remaining_after'] if prior['kind'] == 'read'
                            else proxied['response_outcome']['length'])
                check(current['kind'] == 'read' and proxied['response_outcome']['chunked'] is False
                      and type(expected) is int and expected > 0 and cp['remaining'] == expected,
                      'R2 fresh finite-body wait preserves known remaining length')
        complete = (prior is not None and cp['kind'] == 'between'
                    and (prior['kind'] == 'getresponse' and proxied['response_outcome']['length'] == 0
                         or prior['kind'] == 'read' and prior['result']['closed']))
        check((ack['outcome'] == 'already-complete') == complete,
              'R2 complete finite/bodyless outcome survives separately from delivery')
        issued = [r for r in native_requests if r['request_id'] == intent['request_id']]
        terminals = [t for t in events if t['event'] in {'request-failed','request-finished'}
                     and t['request_id'] == intent['request_id']]
        check(len(issued) == 1 and issued[0]['method'] == 'GET' and issued[0]['url'] == origin+proxied['target']
              and issued[0]['at'] <= intent['enqueued'], 'R2 original native owner still joins its full target')
        if intent['kind'] == 'context':
            check(same(intent['event'], closing) and intent['request_id'] in live
                  and not any(t['at'] <= ack['started'] for t in terminals)
                  and proxied.get('status') == 200 and proxied.get('streaming') is True
                  and ack['outcome'] == 'shutdown' and proxied['stop_settled'] <= shutdown_event['at']
                  and any(same(dict(intent=intent,ack=ack,settled=proxied['stop_settled']),s) for s in shutdowns)
                  and 'browser_abort' not in proxied, 'R2 native stream stays live until its OWN worker initiation')
        else:
            abort = proxied.get('browser_abort')
            check(len(terminals) == 1 and same(abort,dict(terminals[0],target=proxied['target'])) and same(abort,intent['event'])
                  and abort['failure'] == 'net::ERR_ABORTED' and abort['method'] == 'GET'
                  and proxied['entered'] <= abort['at'] <= intent['enqueued']
                  and intent['request_id'] not in used_aborts, 'R2 one genuine native navigation abort, never timeout/error retagging')
            used_aborts.add(intent['request_id'])

    used_aborts = set()
    paired_requests = set()
    owner_generations = set()
    for proxied in observations['thread_proxy']:
        check(set(proxied) <= set(('relay_id original forwarding method path target ordinal entered browser_request_id forwarded status streaming delivered closed '
                                  'request_sha256 request_bytes upstream_complete response_sha256 response_bytes content_type request '
                                  'invalid_request_json response commit committed held release_observed error client_disconnected failure_operation '
                                  'failure_at browser_abort abort_shutdown_error '
                                  'stream_read upstream_operation response_outcome '
                                  'response_close_error connection_close_error owner_generation io_owner stop_intent stop_ack stop_settled').split())
              and not any(k in proxied for k in ('response_close_error','connection_close_error','abort_shutdown_error')),
              'R2 closed proxy evidence grammar; failed ownership never exempt')
        check(proxied['method'] != 'GET' or type(proxied.get('io_owner')) is dict,
              'R2 required original GET owner is missing')
        check(proxied['entered'] <= proxied['closed'] <= closure[0]['finished'], 'R2 every actual browser request settled')
        if 'stream_read' in proxied and 'io_owner' not in proxied:
            read = proxied['stream_read']
            check(type(read.get('id')) is int and read['id'] > 0
                  and set(read) in ({'id','started','finished','error'}, {'id','started','finished','bytes'})
                  and all(type(read[k]) in (int, float) for k in ('started','finished'))
                  and proxied.get('streaming') is True and proxied['forwarded'] <= read['started'] <= read['finished'] <= proxied['closed']
                  and ('error' in read and type(read['error']) is str
                       or type(read.get('bytes')) is int and 0 <= read['bytes'] <= 65536),
                  'R2 closed original upstream stream read evidence')
        operation = proxied.get('upstream_operation')
        response = proxied.get('response_outcome')
        if response is not None:
            check(type(response) is dict
                  and set(response) == {'status','transfer_encoding','content_length','content_type','chunked','length'}
                  and type(response['status']) is int and 200 <= response['status'] <= 599
                  and type(response['chunked']) is bool
                  and all(type(response[k]) is list and len(response[k]) <= 1
                          and all(type(v) is str for v in response[k])
                          for k in ('transfer_encoding','content_length','content_type')),
                  'R2 original safe response status/header facts, not a validation Boolean')
            transfer, lengths = response['transfer_encoding'], response['content_length']
            check((not transfer or transfer[0].strip().lower() == 'chunked' and not lengths)
                  and (not lengths or lengths[0].isascii() and lengths[0].isdecimal())
                  and response['chunked'] == bool(transfer), 'R2 original unambiguous supported transfer framing')
            length = (0 if proxied['method'] == 'HEAD' or response['status'] in {204,304}
                      else int(lengths[0]) if lengths else None)
            check((response['length'] is None if length is None else
                   type(response['length']) is int and response['length'] == length)
                  and type(proxied.get('status')) is int and proxied['status'] == response['status']
                  and proxied.get('streaming') is ((response['content_type'][0] if response['content_type'] else '')
                                                 .split(';')[0] == 'text/event-stream'),
                  'R2 native response parser and retained status/content/length agree')
        boundary = False
        if operation is not None and 'io_owner' not in proxied:
            check(set(operation) in ({'kind','read_id','started','finished','outcome','result'},
                                     {'kind','read_id','started','finished','error'},
                                     {'kind','read_id','started','finished','error','result'})
                  and operation['kind'] in {'getresponse', 'read'}
                  and all(type(operation[k]) in (int, float) for k in ('started', 'finished'))
                  and proxied['forwarded'] <= operation['started'] <= operation['finished'] <= proxied['closed']
                  and (operation['read_id'] is None or type(operation['read_id']) is int and operation['read_id'] > 0)
                  and (operation['read_id'] is None or 'stream_read' in proxied)
                  and (operation['kind'] != 'getresponse' or operation['read_id'] is None)
                  and (operation.get('outcome') == 'returned' or type(operation.get('error')) is str),
                  'R2 complete original upstream operation outcome, not later handler publication')
            if operation.get('outcome') == 'returned':
                check(response is not None and type(operation['result']) is dict,
                      'R2 successful publication requires original validated response facts')
                result = operation['result']
                if operation['kind'] == 'getresponse':
                    check(json.dumps(result, sort_keys=True, allow_nan=False)
                          == json.dumps(response, sort_keys=True, allow_nan=False) and 'stream_read' not in proxied,
                          'R2 no-first-read outcome retains actual status and framing')
                    boundary = True
                else:
                    check(set(result) == {'bytes','remaining_before','remaining_after','closed','eof'}
                          and type(result['bytes']) is int and result['bytes'] >= 0
                          and type(result['closed']) is bool and type(result['eof']) is bool
                          and result['eof'] == (result['bytes'] == 0)
                          and all(v is None or type(v) is int and v >= 0
                                  for v in (result['remaining_before'],result['remaining_after'])),
                          'R2 original finite/stream read byte/EOF/remaining-length facts')
                    before, after = result['remaining_before'], result['remaining_after']
                    check((before is None and after is None if response['length'] is None else
                           before is not None and after is not None and before <= response['length']
                           and after == before-result['bytes'])
                          and (not (result['closed'] or result['eof']) or after in (None,0))
                          and (after != 0 or result['closed'])
                          and (not result['eof'] or result['closed'])
                          and (not proxied['streaming'] or not result['eof'] and not result['closed']),
                          'R2 premature finite EOF and streaming EOF refuse before cancellation')
                    if 'stream_read' in proxied:
                        check(result['bytes'] == proxied['stream_read'].get('bytes'),
                              'R2 retained stream bytes equal original operation outcome')
                    boundary = result['bytes'] > 0 and not result['closed']
            if 'stream_read' in proxied:
                read = proxied['stream_read']
                check(operation['kind'] == 'read' and operation['read_id'] == read['id']
                      and operation['started'] == read['started'] and operation['finished'] == read['finished']
                      and (operation.get('error') == read.get('error') and 'error' in read
                           or operation.get('outcome') == 'returned' and 'bytes' in read),
                      'R2 exact native upstream operation/read outcome identity')
        if 'browser_request_id' in proxied:
            request_id = proxied['browser_request_id']
            issued = [e for e in events if e['event'] == 'request' and e['request_id'] == request_id]
            check(type(request_id) is int and request_id not in paired_requests and len(issued) == 1
                  and issued[0]['url'] == origin + proxied['target'] and issued[0]['method'] == proxied['method'],
                  'R2 one actual native Request per paired original proxy request')
            paired_requests.add(request_id)
        if 'io_owner' in proxied:
            validate_io_owner(proxied)
        else:
            check(proxied['method'] != 'GET'
                  and not any(k in proxied for k in ('owner_generation','stop_intent','stop_ack','stop_settled')),
                  'R2 only GET owns the nonblocking reader and stop protocol')
        failure = proxied.get('error') or proxied.get('client_disconnected')
        if not failure:
            check('delivered' in proxied and 'stop_intent' not in proxied and 'browser_abort' not in proxied,
                  'R2 complete original response delivery without contradictory cancellation')
            check(response is not None and not proxied['streaming'] and operation is not None
                  and operation.get('outcome') == 'returned'
                  and (operation['kind'] == 'getresponse' and response['length'] == 0
                       or operation['kind'] == 'read' and operation['result']['closed']
                       and operation['result']['remaining_after'] in (None,0)),
                  'R2 finite delivery requires completed body or original bodyless response, never progress alone')
            if proxied['ordinal'] is not None:
                check(operation['kind'] == 'read' and type(proxied.get('response_bytes')) is int
                      and operation['result']['bytes'] == proxied['response_bytes']
                      and operation['result']['remaining_before'] == response['length'],
                      'R2 selected compose delivery retains original complete read outcome and byte count')
            continue
        check(('error' in proxied) != ('client_disconnected' in proxied)
              and ('client_disconnected' in proxied) == (failure in {'BrokenPipeError','ConnectionResetError','RemoteDisconnected'})
              and type(proxied.get('failure_at')) in (int, float)
              and proxied['entered'] <= proxied.get('failure_at', -1) <= proxied['closed'],
              'R2 literal original failure/time, never a later cleanup timestamp')
        check('io_owner' in proxied and failure == 'Stopped' and 'stop_intent' in proxied,
              'R2 only original worker-acknowledged controlled stop is exempt')
    check(paired_requests == {e['request_id'] for e in native_requests},
          'R2 every native Request has an original paired relay owner')


def validate_e05_operations(row: dict, observations: dict, point: dict, groups: list[dict]) -> None:
    """Independently authenticate the selected original C/N/D command closure."""
    def check(value, reason):
        if not value:
            raise ValueError('E05 operations: ' + reason)
    durable = observations['durable']
    case = next(d['threads_cases'] for d in durable if 'threads_cases' in d)
    check([g['name'] for g in groups] == ['C','N','D'][:len(groups)], 'finite selected operation order')
    C = groups[0] if groups else None
    N = groups[1] if len(groups) > 1 else None
    D = groups[2] if len(groups) > 2 else None
    expected_count = (0,4,5,11)[len(groups)]
    expected_starts = (29,32,33,36)[len(groups)]
    starts = point['starts']
    witnesses = point['witnesses']
    providers = [p for g in groups for p in g['providers']]
    all_providers = providers + ([D['task']] if D is not None else [])
    check(len(starts) == expected_starts and len({p['session'] for p in starts}) == expected_starts
          and len(all_providers) == expected_starts-29
          and {p['session'] for p in all_providers} == {p['session'] for p in starts[29:]}
          and all(p in starts and p['org'] == 'alpha' and p['pid_start'] for p in all_providers),
          'complete actual selected providers and unique sessions')
    final = point['thread_frame']
    expected_messages = [[(1,'founder','message','E05 opening\nliteral beta'),(2,'case_manager','message','E05 reply\n@case_worker received')]]
    T, sid = (D['task']['task'],D['task']['session']) if D is not None else (None,None)
    def identity(value: dict, name: str) -> None:
        digest = value.get('canonical_definition_revision')
        check(type(digest) is str and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
              'R1 canonical definition digest')
        check(value == dict(canonical_id=name, kind='agent', lifecycle='active', addressable_name=name,
                            name_revision=1, canonical_definition_revision=digest, naming_status='ready')
              and type(value['name_revision']) is int, 'R1 complete current agent identity')

    def resolution(wire: dict, name: str, context: str) -> None:
        check(wire['request'] == dict(addresses=[name], context=context)
              and set(wire['response']) == {'resolutions'} and len(wire['response']['resolutions']) == 1,
              'R1 exact naming preflight request/response')
        resolved = wire['response']['resolutions'][0]
        identity(resolved['identity'], name)
        check(resolved == dict(address=name, status='resolved', identity=resolved['identity'], eligible=True)
              and resolved['eligible'] is True, 'R1 actual eligible current canonical resolution')

    def wire_window(wires: list[dict], expected: list[tuple], entered: float, finished: float) -> None:
        check([(w['method'], w['path']) for w in wires] == expected
              and all(w in observations['relay'] and w['dropped'] is False
                      and entered <= w['entered'] <= finished
                      and json.loads(w['response_text']) == w['response'] for w in wires)
              and all(a['entered'] <= b['entered'] for a, b in zip(wires, wires[1:])),
              'R1 complete exact ordered shipping wire window')
        for wire in wires:
            if wire['path'] == '/api/v1/orgs':
                manifest = next(d['executable_manifest'] for d in observations['durable'] if 'executable_manifest' in d)
                check(wire['request'] is None and wire['status'] == 200
                      and wire['response'] == dict(orgs=[dict(slug=org, root=str(Path(manifest['root']) / 'runtime/orgs' / org))
                                                        for org in ('alpha', 'beta')], broken=[]), 'R1 exact org discovery')

    expected_messages = [
        [(1,"founder","message","E05 opening\nliteral beta"),(2,"case_manager","message","E05 reply\n@case_worker received")],
        [(1,"founder","message","E05 denial anchor")],
        [(1,"founder","message","E05 do one task"),(2,"case_manager","system",None),(3,"case_manager","message","E05 task accepted"),
         (4,"case_manager","system",None),(5,"case_manager","message","E05 task complete")]]
    for group, expected, subject, turns, names in zip(groups,expected_messages,("E05 compose race","E05 denial venue","E05 dispatch"),
                                                    (2,1,3), ({"case_manager","case_worker"},{"case_worker"},{"case_manager"})):
        tid = group["thread"]
        thread = [t for t in final["threads"] if t["id"] == tid]
        check(len(thread) == 1 and thread[0]["subject"] == subject and thread[0]["status"] == "open"
              and thread[0]["turns_used"] == turns and thread[0]["turn_cap"] == 500, "literal thread fields")
        messages = [m for m in final["thread_messages"] if m["thread_id"] == tid]
        check([(m["seq"],m["speaker"],m["kind"],m["body_markdown"]) for m in messages] == expected, "literal message timeline")
        participants = [p for p in final["thread_participants"] if p["thread_id"] == tid]
        check({p["agent_name"] for p in participants} == names and len(participants) == len(names), "literal participants")
        invs = [i for i in final["thread_invocations"] if i["thread_id"] == tid]
        public = group["settled"]["public"]
        check(any(h["method"] == "GET" and h["path"] == f"/api/v1/orgs/alpha/threads/{tid}" and h["status"] == 200
                  and h["response"] == public for h in observations["http"]), "actual public response reference")
        check(public["thread_id"] == tid and public["subject"] == subject and public["turns_used"] == turns
              and not public["reply_delivery"] and set(public["participants"]) == names and len(public["messages"]) == len(messages), "actual public thread projection")
        check(json.loads(group["settled"]["cli"]["stdout"]) == public and group["settled"]["cli"] in observations["commands"]
              and group["settled"]["cli"]["exit"] == 0, "actual CLI readback")
        check(group["settled"]["messages_page"]["messages"] == public["messages"] and group["settled"]["messages_page"]["has_more"] is False,
              "actual complete message page")
        for message, projected in zip(messages,public["messages"]):
            check(all(message[k] == projected[k] for k in ("seq","speaker","kind","body_markdown"))
                  and projected["attachments"] == [], "public literal message content")
            responders = [i for i in invs if i["triggering_seq"] == message["seq"]]
            check(sorted((r["agent_name"],r["purpose"],r["status"]) for r in projected["responder_status"])
                  == sorted((i["agent_name"],i["purpose"],"replied" if i["status"] == "consumed" else "declined") for i in responders),
                  "public authentic responder purpose/outcome; no invented SID")
        for provider in group["providers"]:
            matches = [i for i in invs if i["invocation_token"] == "sha256:" + provider["token_sha256"]]
            check(len(matches) == 1, "genuine delivered token digest")
            i = matches[0]
            check(i["session_id"] == provider["session"] and i["agent_name"] == provider["agent"] and i["purpose"] == provider["purpose"]
                  and i["started_at"] and i["consumed_at"], "distinct genuine runtime identity")
            expected_reply = (2 if tid == C["thread"] else 5 if i["purpose"] == "task_followup" else 3) if i["agent_name"] == "case_manager" else None
            check(i["reply_message_seq"] == expected_reply and i["status"] == ("consumed" if expected_reply else "declined"), "exact reply linkage and decline outcomes")
            if i["purpose"] == "reply":
                claims = [json.loads(a["payload"]) for a in final["audit_log"] if a["action"] == "thread_reply_wake_claimed"
                          and a["task_id"] == tid and json.loads(a["payload"]).get("token_prefix") == i["token_prefix"]]
                seq = 2 if tid == C["thread"] and i["agent_name"] == "case_worker" and i["triggering_seq"] == 2 else 1
                check(len(claims) == 1 and claims[0]["agent_name"] == i["agent_name"]
                      and claims[0]["from_seq"] == claims[0]["through_seq"] == seq, "exact admitted delivery range")
        for delivery in [r for r in final["thread_reply_delivery_state"] if r["thread_id"] == tid]:
            watermark = 2 if tid == C["thread"] and delivery["agent_name"] == "case_worker" else 1
            check(delivery["acknowledged_through_seq"] == delivery["required_through_seq"] == watermark
                  and all(delivery[k] is None for k in ("queued_invocation_token","running_invocation_token","running_from_seq","running_through_seq")), "exact settled delivery watermarks")

    if D is not None:
        T, sid = D["task"]["task"], D["task"]["session"]
        dispatched = [t for t in final["tasks"] if t["dispatched_from_thread_id"] is not None]
        check(len(dispatched) == 1, "one sourced task only")
        t = dispatched[0]
        check(t["id"] == T and t["dispatched_from_thread_id"] == D["thread"] and t["assigned_agent"] == "case_manager"
              and t["team"] == "engineering" and t["brief"] == "E05 one owned task" and t["status"] == "completed"
              and t["completed_at"] and t["current_session_id"] == sid and t["orchestration_step_count"] == 1
              and t["note"] == "E05 task complete" and all(t[k] is None for k in ("parent_task_id","block_kind","active_chain","active_fanout","final_output_dir")), "actual single completed root")
        result = [r for r in final["task_results"] if r["task_id"] == T]
        check(len(result) == 1 and type(result[0]["id"]) is int and result[0]["session_id"] == sid
              and result[0]["status"] == "completed" and result[0]["output_summary"] == "E05 task complete"
              and json.loads(result[0]["decision_json"]) == {"action":"done","summary":"E05 task complete"}, "one genuine accepted task result")
        task_audit = [a for a in final["audit_log"] if a["task_id"] == T]
        required = [next((a for a in task_audit if a["action"] == name),None) for name in ("session_start","session_end","completion_report","orchestration_step")]
        check(all(required) and all(sum(a["action"] == name for a in task_audit) == 1 for name in ("session_start","session_end","completion_report","orchestration_step"))
              and [a["id"] for a in required] == sorted(a["id"] for a in required), "one ordered task lifecycle/consumption")
        systems = [json.loads(m["system_payload_json"]) for m in final["thread_messages"] if m["thread_id"] == D["thread"] and m["kind"] == "system"]
        dispatch_audit = [a for a in final["audit_log"] if a["action"] == "thread_dispatch"]
        check(len(dispatch_audit) == 1 and dispatch_audit[0]["task_id"] == D["thread"]
              and json.loads(dispatch_audit[0]["payload"])["task_id"] == T
              and json.loads(dispatch_audit[0]["payload"])["dispatcher"] == "case_manager", "real original dispatch audit ownership")
        check([i["dispatched_task_id"] for i in final["thread_invocations"] if i["dispatched_task_id"]] == [T]
              and not any(a["action"] in {"thread_followup_skipped","thread_invocation_failed"} for a in final["audit_log"]), "no duplicate/recursive dispatch or lost followup")
        check(systems == [dict(kind_tag="task_dispatched",task_id=T,dispatcher="case_manager",target_agent="case_manager",team="engineering",brief_preview="E05 one owned task"),
                          dict(kind_tag="task_completed",task_id=T,original_task_id=T,root_task_id=T,status="completed",final_output_summary="E05 task complete",final_output_dir=None,cancelled=False,revisit_chain_length=1,revisit_task_id=None)], "both literal system payloads")
        check(len(D["tasks_projection"]) == 1 and D["tasks_projection"][0]["id"] == T, "actual thread task link")

    negatives = []
    if C is not None:
        negatives.append((C['replay'],409,dict(code='invocation_token_consumed',status='consumed')))
    if D is not None:
        negatives.append((D['denial'],403,dict(code='not_a_participant',composer='case_manager')))
    for negative, status, detail in negatives:
        before, after, cmd = negative["before"], negative["after"], negative["command"]
        check(before["content"] == after["content"] and before["samples"][-1]["at"] <= cmd["entered"] <= cmd["finished"] <= after["samples"][0]["at"], "coherent negative causal window")
        for f in (before,after):
            check(len(f["samples"]) >= 2 and f["samples"][-1]["content"] == f["samples"][-2]["content"] == f["content"], "two stable full frames")
        check(cmd["exit"] == 1 and cmd["selected_response"] == {"detail":detail}
              and len([w for w in cmd["wire"] if w["method"] == "POST" and w["status"] == status and w["response"] == {"detail":detail}]) == 1, "literal409/403 real CLI denial")
    if C is not None:
        check(C["reply"]["argv"] == C["replay"]["command"]["argv"] and C["reply"]["payload_sha256"] == C["replay"]["command"]["payload_sha256"], "exact same retained live reply file")

    commands, attempts = observations["thread_commands"], observations["thread_attempts"]
    check(len(commands) == len(attempts) == expected_count and [a["command_index"] for a in attempts] == list(range(expected_count)), "finite eleven provider CLI actions")
    boundary = dict(witness_records=len(witnesses),
                    command_count=point['command_count'])
    check(witnesses == observations["launch_witnesses"][:len(witnesses)] and len([w for w in witnesses if w["kind"] == "stub"]) == expected_starts, "complete actual executable witnesses")
    calls = [w for w in witnesses if w["kind"] == "callback"]
    check(len(calls) == boundary["command_count"]+len(observations["provider_commands"])+len(commands), "every CLI accounted")
    closures = [d["thread_process_closed"] for d in durable if "thread_process_closed" in d]
    check(len(closures) == len(all_providers) and {r["session"] for r in closures} == {p["session"] for p in all_providers}, "seven actual natural process closures")
    for provider in all_providers:
        closure = next(r for r in closures if r["session"] == provider["session"])
        key = "token_sha256" if "thread" in provider else "session"
        own = [cmd for cmd in commands if cmd["provider"].get(key) == provider[key]]
        check(closure["pid"] == provider["pid"] and closure["start"] == provider["pid_start"] != closure["observed_start"]
              and len(closure["children"]) == len(own) and all(ch["pid"] == cmd["child_pid"] and ch["start"] == cmd["child_start"] != ch["observed_start"]
              for ch,cmd in zip(closure["children"],own)), "actual provider and CLI child PID/start closure")
        paths = [cmd["argv"][cmd["argv"].index("--from-file")+1] for cmd in own if "--from-file" in cmd["argv"]]
        check(closure["private_files"] == [dict(path=path,exists=False) for path in paths], "actual named private payload absence")
    barriers = []
    if C is not None:
        barriers.append((C['worker_closed'],19,18,C['reply']['entered']))
    if D is not None:
        barriers.append((D['dispatcher_closed'],23,22,D['denial']['command']['entered']))
    for barrier, admitted, released, command_start in barriers:
        check(barrier in [d["thread_live_boundary"] for d in durable if "thread_live_boundary" in d]
              and barrier["finished"] <= command_start and len(barrier["samples"]) >= 2, "actual intermediate host barrier")
        for sample in barrier["samples"][-2:]:
            m = sample["metrics"]
            a,r = m["host_sessions"]["admission"],m["host_sessions"]["receipts"]
            check((a["admitted_total"],a["released_total"],r["published_total"],a["active"],a["queue_depth"]) == (admitted,released,released,1,0)
                  and any(h["path"] == "/api/v1/metrics" and h["response"] == m for h in observations["http"]), "two actual release/publication/live samples")
    if C is not None:
        check(C["held_worker"]["provider"] == C["providers"][2] and C["held_worker"]["before"] == C["held_worker"]["after"] == C["providers"][2]["pid_start"], "W2 held across same-process replay")
    for cmd,a in zip(commands,attempts):
        keys = ("thread","token_sha256","agent","org","purpose","pid") if "thread" in a["provider"] else ("task","session","agent","org","pid")
        check(cmd["provider"] == {k:a["provider"][k] for k in keys} and a["provider"] in starts, "actual provider receipt identity")
        check(cmd["source_sha"] == row["source_sha"] and cmd["timed_out"] is False and cmd["output_truncated"] is False and a["observed_start"] == a["provider"]["pid_start"]
              and a["entered"] <= cmd["entered"] <= cmd["finished"] <= a["finished"] < a["instruction"]["deadline"]
              <= a["entered"] + 10 and cmd["execution_deadline"] == a["instruction"]["deadline"] - 1, "original live owner/deadline")
        check(all(w in observations["relay"] and w["dropped"] is False for w in cmd["wire"]) and cmd["wire"], "actual complete relay references")
        check(all(type(cmd[k + '_bytes']) is int and cmd[k + '_bytes'] >= 0
                  and cmd[k + '_truncated'] == (cmd[k + '_bytes'] > 16384)
                  and len(cmd[k].encode()) <= 16384 for k in ('stdout','stderr'))
              and cmd['output_truncated'] == (cmd['stdout_truncated'] or cmd['stderr_truncated']),
              'R3 original byte counts and per-stream truncation diagnostics')
        manifest = next(d['executable_manifest'] for d in durable if 'executable_manifest' in d)
        check(cmd['argv'][0] == manifest['callback'] and cmd['callback_sha256'] == manifest['callback_sha256']
              and cmd['python'] == manifest['python']
              and type(cmd['child_pid']) is int and type(cmd['child_start']) is str and cmd['child_start'].isdigit(),
              'actual original executable/interpreter/child generation binding')
        digest = hashlib.sha256(json.dumps(cmd["argv"][1:]).encode()).hexdigest()
        check(len([w for w in calls if w["pid"] == cmd["child_pid"] and w["ppid"] == cmd["provider"]["pid"]
                   and w["argv_sha256"] == digest and w["source_sha"] == row["source_sha"]
                   and w["cli_sha256"] == hashlib.sha256((Path(__file__).resolve().parents[1]/"cli/main.py").read_bytes()).hexdigest()]) == 1, "independent actual CLI child/source join")
    expected_actions = [["thread-reply","thread-reply"],["thread-decline"],["thread-decline"],["thread-decline"],
                        ["thread-dispatch","thread-reply"],["thread-task-read","thread-reply"],["thread-task-send","thread-task-final"]]
    for provider, actions in zip(all_providers,expected_actions):
        key = "token_sha256" if "thread" in provider else "session"
        own = [cmd for cmd in commands if cmd["provider"].get(key) == provider[key]]
        check([cmd["action"] for cmd in own] == actions, "finite per-provider action order/final callback last")
    if D is not None:
        check(D["callback"]["selected_response"] == {"ok":True} and D["callback"]["exit"] == 0
              and D["callback"] in commands and D["followup"] in commands and D["read"] in commands
              and D["callback"]["finished"] <= D["providers"][1]["at"] <= D["read"]["entered"] <= D["followup"]["entered"], "task callback to natural D2 read/reply")
    # Fixed case receipts must be the actual eleven ledger entries, each joined
    # to its original finite instruction. Durable acceptance alone is not CLI success.
    specifications = []
    if C is not None:
        specifications += [
            (C['initial_decline'], C['providers'][1], 'thread-decline', dict(reason='E05 initial observed', in_response_to_seq=1), 200, None),
            (C['reply'], C['providers'][0], 'thread-reply', dict(body_markdown='E05 reply\n@case_worker received', in_response_to_seq=1), 200, 2),
            (C['replay']['command'], C['providers'][0], 'thread-reply', dict(body_markdown='E05 reply\n@case_worker received', in_response_to_seq=1), 409, None),
            (C['last_decline'], C['providers'][2], 'thread-decline', dict(reason='E05 reply observed', in_response_to_seq=2), 200, None),
        ]
    if N is not None:
        specifications += [
            (N['decline'], N['providers'][0], 'thread-decline', dict(reason='E05 denial venue observed', in_response_to_seq=1), 200, None),
        ]
    if D is not None:
        specifications += [
            (D['dispatch'], D['providers'][0], 'thread-dispatch', dict(brief='E05 one owned task', target_agent='case_manager', team='engineering'), 200, None),
            (D['reply'], D['providers'][0], 'thread-reply', dict(body_markdown='E05 task accepted', in_response_to_seq=1), 200, 3),
            (D['denial']['command'], D['task'], 'thread-task-send', dict(body_markdown='E05 forbidden post'), 403, None),
            (D['callback'], D['task'], 'thread-task-final', dict(status='completed', confidence=90, summary='E05 task complete', decision=dict(action='done', summary='E05 task complete')), 200, None),
            (D['read'], D['providers'][1], 'thread-task-read', None, 200, None),
            (D['followup'], D['providers'][1], 'thread-reply', dict(body_markdown='E05 task complete', in_response_to_seq=4), 200, 5),
        ]
    check([s[0] for s in specifications] == commands, 'R1 every named receipt equals canonical ordered command ledger')
    for index, (cmd, provider, action, supplied, status, seq) in enumerate(specifications):
        attempt = attempts[index]
        expected_instruction = dict(action=action, deadline=attempt['instruction']['deadline'], execution_deadline=cmd['execution_deadline'])
        if supplied is not None:
            expected_instruction['payload'] = supplied
        if action == 'thread-task-send':
            expected_instruction['thread'] = N['thread']
        if action == 'thread-task-read':
            expected_instruction['task'] = T
        check(attempt['provider'] == provider and attempt['instruction'] == expected_instruction and cmd['action'] == action,
              'R1 named owner/action/original instruction equality')
        tid = provider.get('thread')
        expected_payload = None
        if action in {'thread-reply', 'thread-decline', 'thread-dispatch'}:
            operation = action.removeprefix('thread-')
            expected_payload = dict(supplied, thread_id=tid, invocation_token='[REDACTED]')
            expected_payload['dispatcher' if operation == 'dispatch' else 'speaker'] = provider['agent']
            args = ['threads', operation, '--org', 'alpha', '--thread-id', tid]
            method, path = 'POST', f'/api/v1/orgs/alpha/threads/{tid}/{operation}'
            request_body = expected_payload
        elif action == 'thread-task-send':
            expected_payload = dict(supplied, composer='case_manager')
            args = ['threads', 'send', '--thread-id', N['thread'], '--task-id', T, '--session-id', sid, '--org', 'alpha']
            method, path = 'POST', f"/api/v1/orgs/alpha/threads/{N['thread']}/send"
            request_body = dict(expected_payload, task_id=T, session_id=sid)
        elif action == 'thread-task-final':
            expected_payload = dict(supplied, task_id=T, session_id=sid, agent='case_manager')
            args = ['report-completion', '--org', 'alpha']
            method, path = 'POST', f'/api/v1/orgs/alpha/tasks/{T}/completion'
            request_body = dict(session_id=sid, agent='case_manager', status='completed', confidence=90,
                                output_summary='E05 task complete', risks_flagged=[], dependencies=[],
                                suggested_reviewer_focus=[], decision=dict(action='done', summary='E05 task complete'))
        else:
            args = ['details', '--org', 'alpha', T]
            method, path, request_body = 'GET', f'/api/v1/orgs/alpha/tasks/{T}', None
        if expected_payload is not None:
            private_path = cmd['argv'][-1]
            check(Path(private_path).is_absolute() and Path(private_path).parent == Path(cmd['argv'][0]).parent.parent / 'coord'
                  and Path(private_path).name.startswith('e05-private-'), 'R1 actual private payload argv scope')
            args += ['--from-file', private_path]
        check(cmd['argv'][1:] == args and cmd['payload'] == expected_payload and cmd['exit'] == (0 if status == 200 else 1)
              and cmd['stderr'] == '', 'R1 exact CLI argv/payload/success or denial exit and stderr')
        selected = [w for w in cmd['wire'] if w['method'] == method and w['path'] == path]
        check(len(selected) == 1, 'R1 exact selected method and endpoint')
        wire = selected[0]
        check(wire['status'] == status and wire['request'] == request_body and wire['response'] == cmd['selected_response']
              and json.loads(wire['response_text']) == wire['response'], 'R1 request/status/selected actual response equality')
        expected_window = [] if action == 'thread-task-final' else [('GET', '/api/v1/orgs')]
        if action == 'thread-reply' and supplied['body_markdown'] == 'E05 reply\n@case_worker received':
            expected_window += [('POST', '/api/v1/orgs/alpha/identities/resolve'),
                                ('GET', f'/api/v1/orgs/alpha/threads/{tid}')]
        expected_window.append((method, path))
        if action == 'thread-task-read':
            expected_window.append(('GET', '/api/v1/orgs/alpha/identities'))
        wire_window(cmd['wire'], expected_window, cmd['entered'], cmd['finished'])
        if action == 'thread-reply' and len(expected_window) == 4:
            naming, preview = cmd['wire'][1:3]
            check(naming['status'] == preview['status'] == 200 and preview['request'] is None,
                  'R1 successful naming and roster preflight')
            resolution(naming, 'case_worker', 'lookup')
            check(preview['response']['thread_id'] == tid and preview['response']['subject'] == 'E05 compose race'
                  and preview['response']['participants'] == ['case_manager', 'case_worker']
                  and preview['response']['turns_used'] == (1 if status == 200 else 2)
                  and [(m['seq'], m['speaker'], m['body_markdown']) for m in preview['response']['messages']]
                  == [(seq, speaker, body) for seq, speaker, _, body in expected_messages[0]][:1 if status == 200 else 2],
                  'R1 actual current thread roster and complete pre-action messages')
        if action == 'thread-task-read':
            naming = cmd['wire'][-1]
            check(naming['status'] == 200 and naming['request'] is None
                  and set(naming['response']) == {'identities', 'naming_diagnostic'}
                  and naming['response']['naming_diagnostic'] is None,
                  'R1 exact task-details presentation lookup')
            identities = naming['response']['identities']
            check([i['canonical_id'] for i in identities] == ['founder', 'case_manager', 'case_worker', 'code_reviewer'],
                  'R1 complete current presentation roster')
            check(identities[0] == dict(canonical_id='founder', kind='founder', lifecycle='founder', addressable_name='founder',
                                       name_revision=1, canonical_definition_revision=None, naming_status='ready')
                  and type(identities[0]['name_revision']) is int, 'R1 exact founder presentation')
            for item in identities[1:]:
                identity(item, item['canonical_id'])
            for receipt in commands:
                for prior in receipt['wire']:
                    if prior['path'] == '/api/v1/orgs/alpha/identities/resolve':
                        for resolved in prior['response']['resolutions']:
                            check(resolved['identity'] in identities, 'R1 resolution agrees with independent presentation read')
        if status != 200:
            expected = {'detail': {'code':'invocation_token_consumed','status':'consumed'} if status == 409 else {'code':'not_a_participant','composer':'case_manager'}}
            stdout = f"Error ({status}): {wire['response_text']}\n"
        elif action == 'thread-decline':
            expected, stdout = dict(thread_id=tid, status='declined'), f'ok: declined on {tid}\n'
        elif action == 'thread-reply':
            expected, stdout = dict(thread_id=tid, seq=seq, kind='message'), f'ok: reply seq={seq} on {tid}\n'
        elif action == 'thread-dispatch':
            expected = dict(task_id=T, team='engineering', assigned_agent='case_manager', dispatched_from_thread_id=D['thread'], system_message_seq=2, superseded_task_id=None)
            stdout = f"ok: dispatched {T} from {D['thread']}\n"
        elif action == 'thread-task-final':
            expected, stdout = {'ok':True}, ''
        else:
            details = wire['response']
            actual_task, results = details['task'], details['results']
            check(all(actual_task[k] == v for k,v in dict(task_id=T, status='completed', assigned_agent='case_manager',
                  team='engineering', brief='E05 one owned task', dispatched_from_thread_id=D['thread'], parent_task_id=None).items())
                  and len(results) == 1 and all(results[0][k] == v for k,v in dict(task_id=T, session_id=sid, agent='case_manager',
                  status='completed', confidence_score=90, output_summary='E05 task complete').items()), 'R1 D2 actual dispatched completed task/result summary')
            lines = cmd['stdout'].splitlines()
            check(all(line in lines for line in (f'Task:       {T}', 'Team:       engineering', 'Status:     completed',
                  'Agent:      case_manager · case_manager', 'Brief:      E05 one owned task', 'Results (1):',
                  '  - [case_manager · case_manager] confidence=90  E05 task complete')), 'R1 D2 shipping literal details lines')
            expected, stdout = details, cmd['stdout']
        check(wire['response'] == expected and cmd['stdout'] == stdout, 'R1 literal selected outcome and shipping stdout')

    for group in groups:
        settled = group['settled']
        cmd, wires = settled['cli'], settled['wire_records']
        check(observations['commands'][settled['command_index']] == cmd and observations['commands'].count(cmd) == 1
              and cmd['argv'][1:] == ['threads','show','--org','alpha',group['thread'],'--json']
              and cmd['exit'] == 0 and cmd['stderr'] == '', 'R1 named settled read canonical ledger and literal CLI')
        check(len(wires) == 2 and [(w['method'],w['path']) for w in wires] == [('GET','/api/v1/orgs'),('GET',f"/api/v1/orgs/alpha/threads/{group['thread']}")]
              and all(w in observations['relay'] and w['status'] == 200 and w['request'] is None and w['dropped'] is False
                      and settled['cli_entered'] <= w['entered'] <= settled['cli_finished'] for w in wires)
              and wires[-1]['response'] == settled['public'] == json.loads(cmd['stdout']), 'R1 settled read full canonical actual wire window')

    for group, subject, recipient, body in [(g,subject,recipient,body) for g,subject,recipient,body in
            ((N,'E05 denial venue','case_worker','E05 denial anchor'),
             (D,'E05 dispatch','case_manager','E05 do one task')) if g is not None]:
        compose = group['compose']
        cmd, wire = compose['command'], compose['wire']
        check(observations['commands'].count(cmd) == 1 and wire in observations['relay'] and wire['dropped'] is False,
              'R1 named N/D compose joins canonical command and relay')
        check(cmd['argv'][1:] == ['threads','compose','--org','alpha','--subject',subject,'--recipients',recipient,'--body',body]
              and cmd['exit'] == 0 and cmd['stderr'] == '' and wire['method'] == 'POST' and wire['path'] == '/api/v1/orgs/alpha/threads'
              and wire['status'] == 200 and wire['request'] == dict(subject=subject, recipients=[recipient], body_markdown=body)
              and wire['response'] == compose['response'], 'R1 N/D exact compose command/request/response')
        check(observations['commands'][compose['command_index']] == cmd and len(compose['wire_records']) == 3
              and compose['wire_records'][-1] == wire
              and [(w['method'],w['path']) for w in compose['wire_records']] == [('GET','/api/v1/orgs'),('POST','/api/v1/orgs/alpha/identities/resolve'),('POST','/api/v1/orgs/alpha/threads')]
              and compose['wire_records'][0]['request'] is None
              and all(w in observations['relay'] and w['status'] == 200 and w['dropped'] is False
                      and compose['entered'] <= w['entered'] <= compose['finished'] for w in compose['wire_records']),
              'R1 N/D complete canonical wire and command-index window')
        wire_window(compose['wire_records'], [('GET', '/api/v1/orgs'), ('POST', '/api/v1/orgs/alpha/identities/resolve'),
                                             ('POST', '/api/v1/orgs/alpha/threads')], compose['entered'], compose['finished'])
        resolution(compose['wire_records'][1], recipient, 'thread_recipient')
        response = compose['response']
        stored = next(r for r in final['threads'] if r['id'] == group['thread'])
        check(response == dict(thread_id=group['thread'], started_at=stored['started_at'], pending_replies=[recipient]),
              'R1 N/D original compose identity and literal pending list')
        # Producer records the child-local formatted timestamp; independently
        # validate its UTC offset conversion against the actual stored instant.
        from datetime import datetime, timedelta, timezone
        stamp = datetime.fromisoformat(response['started_at'].replace('Z', '+00:00'))
        offset = compose['utc_offset_seconds']
        check(type(offset) is int and abs(offset) < 86400, 'R1 compose local timezone evidence')
        formatted = stamp.astimezone(timezone(timedelta(seconds=offset))).strftime('%Y-%m-%d %H:%M:%S')
        check(cmd['stdout'] == f"{group['thread']}  started={formatted}  pending={[recipient]}\n",
              'R1 full shipping founder compose stdout')
        digest = hashlib.sha256(json.dumps(cmd['argv'][1:]).encode()).hexdigest()
        check(len([w for w in calls if w['argv_sha256'] == digest and w['source_sha'] == row['source_sha']]) == 1,
              'R1 N/D independent executable source/argv witness')



def validate_e05(row: dict, observations: dict, directory: Path) -> None:
    """Independent literal-content consumer, never import the scenario oracle."""
    originals = validate_e05_originals(observations, directory)
    import hashlib
    from collections import Counter

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E05 content: " + message)

    durable = observations["durable"]
    cases = [d["threads_cases"] for d in durable if "threads_cases" in d]
    check(len(cases) == 1, "one finite C/N/D record")
    case = cases[0]
    variants = ["E05.compose-race", "E05.reply-natural", "E05.self-dispatch-followup", "E05.consumed-token", "E05.nonparticipant-task-send"]
    check(case["source_sha"] == row["source_sha"] and case["variants"] == variants, "five actual-content references")
    case_end = case.get('finished', case['cleanup_finished'])
    check(case["final_entered"] <= case_end <= case["final_deadline"] <= case["final_entered"]+4, "bounded final evidence allowance")
    groups = case["groups"]
    check([g["name"] for g in groups] == ["C", "N", "D"], "exact finite group order")
    C, N, D = groups
    starts = observations["starts"]
    check(len(starts) in {36,38} and len({s["session"] for s in starts[:36]}) == 36, "independently owned36 unique E05 prefix sessions")
    providers = C["providers"] + N["providers"] + D["providers"]
    check(len(providers) == 6 and len({p["token_sha256"] for p in providers}) == 6
          and {s["session"] for s in starts[29:36]} == {p["session"] for p in providers + [D["task"]]}, "six genuine thread tokens and one task")
    check(all(p in starts and p["org"] == "alpha" and p["pid_start"] for p in providers), "actual thread socket identities")
    anchors = [d["jobs_anchor"] for d in durable if "jobs_anchor" in d]
    previous = next(a for a in anchors if a["name"] == "final-drain-B")
    check(len([a for a in anchors if a["name"].startswith("E05:")]) == 4, "four E05 group/drain boundaries")
    for group, total, count, allowance in zip(groups, (20,21,24), (32,33,36), (18,8,25)):
        check(group["entered"] <= group["finished"] <= group["deadline"] <= group["entered"] + allowance,
              "original finite group allowance")
        a = group["anchor"]
        check(a in anchors and a["name"] == "E05:" + group["name"] and a["source_sha"] == row["source_sha"]
              and a["total"] == total and a["provider_count"] == count, "actual anchor joins")
        check((a["daemon_pid"],a["daemon_start"]) == (previous["daemon_pid"],previous["daemon_start"])
              and previous["finished"] <= a["entered"] <= a["finished"], "no reset or replacement")
        samples = a["samples"]
        check(len(samples) >= 2, "two independently read metrics samples")
        projections = []
        for sample in samples:
            m = sample["metrics"]
            check(any(h["method"] == "GET" and h["path"] == "/api/v1/metrics" and h["status"] == 200 and h["response"] == m
                      for h in observations["http"]), "actual metrics response content")
            h = m["host_sessions"]
            r, adm = h["receipts"], h["admission"]
            check(h["wired"] is True and h["backend"]["healthy"] is True and h["backend"]["name"] != "passthrough"
                  and h["residue"]["admission_blocked"] is False and h["residue"]["survivors_count"] == 0, "live host health")
            check(r["published_total"] == r["window_size"] == len(r["recent"]) == r["quiescent_count"] <= total <= 64
                  and r["with_residue_count"] == 0 and all(x["quiescent"] is True and x["survivors_count"] == 0 for x in r["recent"]), "no eviction/residue")
            delta = r["published_total"] - previous["total"]
            check(delta >= 0 and r["recent"][delta:] == previous["projection"]["receipts"]["recent"], "retained ordered duplicate-preserving tail")
            check(adm["cancelled_queued_total"] == 0 and adm["shutdown"] is False, "no cancelled admission")
            projections.append(dict(admission=adm, receipts=r, executor_sessions_active=m["executor_sessions_active"], run_step_queue_depth=m["run_step_queue_depth"]))
        p = a["projection"]
        check(projections[-1] == projections[-2] == p and p["admission"]["admitted_total"] == p["admission"]["released_total"]
              == p["receipts"]["published_total"] == total and p["admission"]["active"] == p["admission"]["queue_depth"]
              == p["executor_sessions_active"] == p["run_step_queue_depth"] == 0, "settled receipt/release/live conjunction")
        check(len(a["provider_closure"]) == count and all(x["pid"] == s["pid"] and x["start"] == s["pid_start"]
              and x["observed_start"] != x["start"] for x,s in zip(a["provider_closure"],starts[:count])), "natural provider closure")
        previous = a
    final_anchor = case["final_anchor"]
    check(final_anchor in anchors and final_anchor["name"] == "E05:final" and final_anchor["projection"] == previous["projection"]
          and final_anchor["provider_count"] == 36 and final_anchor["entered"] >= previous["finished"], "unchanged final drain")
    check(len(final_anchor["samples"]) >= 2 and all(s["metrics"]["host_sessions"]["receipts"] == previous["projection"]["receipts"]
              for s in final_anchor["samples"][-2:]), "actual final rereads")
    check(Counter(x["invocation_kind"] for x in final_anchor["projection"]["receipts"]["recent"]) == {"task":18,"thread":6}, "B24 canonical task/thread mix")

    final = case["final"]["content"]
    expected_sources = ("e2e/threads.py", "e2e/thread_provider.py", "e2e/provider.py", "e2e/controller.py", "e2e/browser.py",
                        "e2e/transport.py", "e2e/oracles.py", "e2e/aggregate.py", "tests/integration/fake_claude.sh", "cli/commands/threads.py",
                        "runtime/daemon/routes/threads.py", "runtime/daemon/thread_runner.py", "web/src/shared/threads/NewThreadDialog.tsx", "cli/_shared.py", "cli/commands/tasks.py",
         "runtime/daemon/routes/tasks.py", "web/src/features/threads/ThreadsPage.tsx",
         "web/src/design-system/patterns/MessageBubble.tsx", "web/src/design-system/patterns/Markdown.tsx",
         "web/src/design-system/patterns/AgentChip.tsx")
    source_root = Path(__file__).resolve().parents[1]
    check(case["source_hashes"] == {name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in expected_sources}, "actual immutable producer/consumer source bytes")
    beta = [d["threads_beta"] for d in durable if "threads_beta" in d]
    check(len(beta) == 1 and not any(beta[0][k] for k in beta[0] if k.startswith("thread") or k in {"jobs","files"}), "beta has no thread/job/file effects")
    check(len(final["jobs"]) == 7 and {j["id"] for j in final["jobs"]} == {
        j["id"] for d in durable if "jobs_cases" in d for r in d["jobs_cases"] for j in r["jobs"]}, "unchanged seven prior jobs")
    check(len(case["final"]["samples"]) >= 2 and case["final"]["samples"][-1]["content"]
          == case["final"]["samples"][-2]["content"] == final, "coherent final rereads")
    check([len(final[k]) for k in ("threads","thread_participants","thread_messages","thread_invocations","thread_reply_delivery_state")]
          == [3,4,8,6,4], "exact3 threads/4 participants/8 messages/6 invocations/4 delivery pairs")
    check(all(not final[k] for k in ("thread_reply_exchange","thread_exchange_deferrals","thread_reply_breaker_episodes",
          "thread_reply_breaker_receipts","thread_message_attachments","thread_scoped_attachments","task_attachments","files")), "no extra effect/attachment/breaker")
    check(Counter(i["status"] for i in final["thread_invocations"]) == {"consumed":3,"declined":3}
          and Counter(i["purpose"] for i in final["thread_invocations"]) == {"reply":5,"task_followup":1}, "terminal invocation outcome/purpose")
    T, sid = D['task']['task'],D['task']['session']
    systems = [json.loads(m['system_payload_json']) for m in final['thread_messages'] if m['thread_id'] == D['thread'] and m['kind'] == 'system']
    validate_e05_operations(row,observations,case['checkpoints'][-1],groups)

    events = json.loads((directory / "e05-browser-events.json").read_text())
    validate_e05_safety(observations, events)
    check(C["created"] in events and C["created"]["dialog_count"] == 0 and C["created"]["list_rows"] == 1, "actual dialog closure and one list row")
    check(events and events[-1]["event"] == "context-closed" and not any(e["event"] in {"pageerror","origin-bypass"} for e in events), "real browser origin/closure")
    requests = [e for e in events if e["event"] == "compose-request"]
    responses = [e for e in events if e["event"] == "compose-response"]
    proxy = [p for p in observations["thread_proxy"] if p["ordinal"] is not None]
    check(len(requests) == len(responses) == len(proxy) == 1, "one page/upstream/original response")
    request, response, wire = requests[0],responses[0],proxy[0]
    transport = case["transport"]
    check(len({transport[k] for k in ("daemon_port","cli_port","browser_port")}) == 3
          and all(transport[k] in transport["ports"] for k in ("daemon_port","cli_port","browser_port")), "registered owned D/L/P")
    origin = f"http://127.0.0.1:{transport['browser_port']}"
    check(request["url"] == response["url"] == origin + "/api/v1/orgs/alpha/threads"
          and wire["target"] == "/api/v1/orgs/alpha/threads" and request["method"] == wire["method"] == "POST"
          and request["payload"] == wire["request"] == {"subject":"E05 compose race","recipients":["case_manager","case_worker"],"body_markdown":"E05 opening\nliteral beta"}
          and request["sha256"] == wire["request_sha256"] and request["bytes"] == wire["request_bytes"], "exact end-to-end compose bytes")
    check(wire["status"] == response["status"] == 200 and wire["response"] == originals[response['request_id']]["body"]
          and wire["response_sha256"] == originals[response['request_id']]["sha256"] and wire["response_bytes"] == originals[response['request_id']]["bytes"]
          and wire["response"]["thread_id"] == C["thread"] and set(wire["response"]["pending_replies"]) == {"case_manager","case_worker"}, "original actual200 identity/payload")
    check(C["armed"]["at"] <= wire["forwarded"] <= wire["upstream_complete"] <= wire["committed"] <= wire["held"] <= C["released"]
          <= wire["delivered"] <= wire["closed"] <= C["armed"]["deadline"] <= C["armed"]["at"]+5, "single bounded hold/release timeline")
    check(len(wire["commit"]["threads"]) == len(wire["commit"]["thread_messages"]) == 1
          and wire["commit"]["thread_messages"][0]["body_markdown"] == "E05 opening\nliteral beta", "actual independent commit content")
    check(all(isinstance(e['at'], (int, float)) and not isinstance(e['at'], bool) for e in events)
          and all(a['at'] <= b['at'] for a,b in zip(events, events[1:])), 'R2 complete browser ledger chronological order')
    for name, snapshot in (('held', C['held_events']), ('released', C['released_events'])):
        indices = [i for i,e in enumerate(events) if e['event'] == 'snapshot' and e['name'] == name]
        check(len(indices) == 1 and snapshot == events[:indices[0]+1], 'R2 actual held/released full ledger prefix equality')
    check([e['name'] for e in events if e['event'] == 'snapshot'] == ['held','released'], 'R2 exactly two ordered snapshot markers')
    check(wire['held'] <= C['held_events'][-1]['at'] <= C['released'] <= wire['release_observed']
          <= response['at'] <= C['released_events'][-1]['at'], 'R2 snapshots and response bind actual proxy release interval')
    detail = origin + f"/orgs/alpha/threads/{C['thread']}"
    inbox = origin + '/orgs/alpha/threads'
    # Classify EVERY navigation, including intentional revisits. An extra event
    # anywhere in the full ledger cannot be hidden by a case snapshot.
    navigation, entries = [], []
    document_id, document_url, current_url, initialized = None, None, None, False
    documents = set()
    for event in events:
        if event['event'] == 'deliberate-revisit':
            navigation.append(('deliberate-revisit', event['url'], None))
        elif event['event'] == 'navigation':
            check(type(event['new_document']) is bool and type(event['document_request_id']) is int,
                  'R2 typed native document lineage')
            if event['new_document']:
                document_id = event['document_request_id']
                issued = [e for e in events if e['event'] == 'request' and e['request_id'] == document_id]
                check(document_id not in documents and len(issued) == 1 and issued[0]['navigation'] is True
                      and issued[0]['resource_type'] == 'document' and issued[0]['method'] == 'GET'
                      and issued[0]['url'] == event['url'] and issued[0]['at'] <= event['at'],
                      'R2 each new document has its own actual unredirected browser GET')
                documents.add(document_id)
                document_url, current_url, initialized = event['url'], event['url'], False
            else:
                check(document_id is not None and event['document_request_id'] == document_id,
                      'R2 same-document event retains actual original Request')
                if event['url'] == current_url:
                    check(not initialized and current_url == document_url,
                          'R2 at most one nondeparting initialization per actual document')
                    initialized = True
                    continue  # Raw event remains in the complete ledger.
                current_url = event['url']
            navigation.append(('navigation', event['url'], event['new_document']))
            entries.append(event)
    expected_navigation = [('navigation',inbox,True), ('navigation',detail,False)]
    for target in (inbox, detail, detail, origin + f"/orgs/alpha/threads/{D['thread']}"):
        expected_navigation += [('deliberate-revisit',target,None), ('navigation',target,True)]
    check(navigation == expected_navigation, 'R2 one automatic detail entry and four explicitly paired later revisits; no extras')
    check(not any(e['at'] < wire['release_observed'] and (e['event'] == 'compose-response'
                  or (e['event'] == 'navigation' and e['url'] != inbox)) for e in events),
          'R2 zero held response/detail navigation recomputed from full ledger')
    auto_entry = entries[1]
    first_revisit = next(e for e in events if e['event'] == 'deliberate-revisit')
    check(response['at'] <= auto_entry['at'] <= C['released_events'][-1]['at'] <= first_revisit['at']
          and C['created']['url'] == detail and C['created']['list_url'] == inbox,
          'R2 original response to automatic entry precedes every intentional revisit')
    batch = C["event_batch"]
    check(batch in events and batch["guard_sensitivity"] == "UNPROVEN"
          and [(e["id"],e["type"]) for e in batch["rows"] if e["phase"] == "before"] == [(1,"keydown"),(2,"click"),(3,"click")], "same synchronous finite input sequence; no causal guard claim")
    check(all(a["at"] <= b["at"] for a,b in zip(batch["rows"],batch["rows"][1:])), "actual DOM event ordering")
    views = (C['initial_view'], C['view'], D['view'])
    check([dict(event='thread-view', **v) for v in views] == [e for e in events if e['event'] == 'thread-view'],
          'R2 all three actual view records in ledger order')
    for view, group, count, name in zip(views, (C,C,D), (1,2,5), ('e05-compose.png','e05-reply.png','e05-dispatch.png')):
        tid = group['thread']
        public = group['settled']['public']
        body = view['original_body']
        check(view['original_ref'] == originals[view['request_id']] and view['messages_original_ref'] == originals[view['messages_request_id']], 'R2 exact view Response object/original references')
        check(view['screenshot'] == name and (directory/name).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),
              'R2 named PNG bytes only; rendered semantics checked separately')
        view_index = events.index(dict(event='thread-view', **view))
        revisit_index = max(i for i,e in enumerate(events[:view_index]) if e['event'] == 'deliberate-revisit')
        response_rows = [e for e in events[revisit_index+1:view_index] if e['event'] == 'thread-response'
                         and e['url'] == origin + f'/api/v1/orgs/alpha/threads/{tid}' and e['request_id'] == view['request_id'] and e['response_id'] == view['response_id'] and originals[e['request_id']]['body'] == body]
        check(view['url'] == origin + f'/orgs/alpha/threads/{tid}' and view['response_url'] == origin + f'/api/v1/orgs/alpha/threads/{tid}'
              and view['method'] == 'GET' and view['status'] == 200 and response_rows
              and response_rows[0]['method'] == 'GET' and response_rows[0]['status'] == 200,
              'R2 rendered view joins exact thread and actual public GET response in its revisit interval')
        check(body['thread_id'] == tid and body['subject'] == public['subject'] and body['status'] == 'open'
              and body['turns_used'] == (1 if count == 1 else public['turns_used'])
              and sorted(body['participants']) == sorted(public['participants']) and len(body['messages']) == count,
              'R2 initial/reply/dispatch public identity and exact projection cardinality')
        for actual, expected in zip(body['messages'], public['messages'][:count]):
            check(all(actual[k] == expected[k] for k in ('seq','speaker','kind','body_markdown','attachments','system_payload')),
                  'R2 rendered response literal message and complete system-payload projection')
        message_rows = [e for e in events[revisit_index+1:view_index] if e['event'] == 'thread-response'
                        and e['url'] == view['messages_url'] and e['request_id'] == view['messages_request_id'] and e['response_id'] == view['messages_response_id'] and originals[e['request_id']]['body'] == view['original_messages_body']]
        check(view['messages_url'].split('?')[0] == origin + f'/api/v1/orgs/alpha/threads/{tid}/messages'
              and view['messages_status'] == 200 and view['messages_method'] == 'GET' and message_rows
              and message_rows[0]['status'] == 200 and message_rows[0]['method'] == 'GET'
              and len(view['original_messages_body']['messages']) == count and view['original_messages_body']['has_more'] is False,
              'R2 actual browser message-page response joins displayed projection')
        for detail_message, page_message in zip(body['messages'], view['original_messages_body']['messages']):
            check(all(detail_message[k] == page_message[k] for k in ('seq','speaker','kind','body_markdown','attachments','system_payload','created_at')),
                  'R2 detail/message-page immutable content equality; initial responder state may advance')
        if count != 1:
            check(view['original_messages_body']['messages'] == public['messages'], 'R2 settled message-page full public projection')
        expected_bubbles = [m for m in body['messages'] if m['kind'] == 'message']
        check(len(view['rendered_messages']) == len(expected_bubbles), 'R2 exact rendered message bubble count')
        for rendered, message in zip(view['rendered_messages'], expected_bubbles):
            check(rendered['visible'] is True and f"#{message['seq']} · " in rendered['header']
                  and message['speaker'] in rendered['header']
                  and ' '.join(rendered['body'].split()) == ' '.join(message['body_markdown'].split()),
                  'R2 each literal reply/opening belongs to its own rendered sequence bubble')
        if count != 1:
            check(body == public, 'R2 settled rendered response equals full observed public projection')
        normalized = ' '.join(view['text'].split())
        literals = [public['subject']] + [m['body_markdown'] for m in public['messages'][:count] if m['kind'] == 'message']
        if group is D:
            literals += [f'dispatched {T}', f'task {T} completed · E05 task complete']
            check([m['system_payload'] for m in body['messages'] if m['kind'] == 'system'] == systems,
                  'R2 full D dispatched/completed payloads and summary')
        check(all(' '.join(literal.split()) in normalized for literal in literals),
              'R2 every full opening/reply/dispatch/followup/summary literal rendered (DOM whitespace normalized)')
    final_orgs = [d for d in durable if "threads_final_org" in d]
    check(len(final_orgs) == 2 and {d["threads_final_org"] for d in final_orgs} == {"alpha","beta"}, "both final orgs")
    for d in final_orgs:
        expected = (20,29) if d["threads_final_org"] == "alpha" else (1,1)
        check((len(d["snapshot"]["tasks"]),len(d["snapshot"]["results"])) == expected
              and all(t["status"] == "completed" for t in d["snapshot"]["tasks"]), "global21tasks30results")

    for event in events:
        if event['event'] in {'compose-response','thread-response'}:
            require_browser_copy(event, originals[event['request_id']])
    for view in views:
        check(view.get('browser_copy') == 'measured-browser', 'mandatory independent browser view JSON unavailable')
        check(view['body'] == view['original_body'] and view['messages_body'] == view['original_messages_body'],
              'original independent full browser detail/message-page projection')
        # Includes created_at, has_more, all system payloads and rendered-response
        # equality already checked above for the full original entity.
    check(all(row['variants'][v]['evidence']['threads'] == case for v in variants), 'five actual completed case references')


def e07_sql_date(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    encoded = parsed.isoformat()
    if parsed.utcoffset() == timedelta(0):
        encoded = encoded.removesuffix("+00:00")+"Z"
    return encoded


def e07_cancellation_transition(live: dict, frozen: dict, parent: str, worker: str) -> None:
    """Reconstruct lifecycle bounds from raw rows/public responses/audits only."""
    def instant(value: str) -> datetime:
        if type(value) is not str or "T" not in value:
            raise ValueError("E07 serialized lifecycle timestamp string")
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError("E07 serialized lifecycle timestamp aware UTC")
        return result

    before = {row["id"]: row for row in live["sql"]["alpha"]["tables"]["tasks"]}
    after = {row["id"]: row for row in frozen["sql"]["alpha"]["tables"]["tasks"]}
    events = [row for row in frozen["sql"]["alpha"]["tables"]["audit_log"]
              if row["task_id"] in (parent, worker) and row["action"] == "task_cancelled"]
    if [row["task_id"] for row in events] != [parent, worker]:
        raise ValueError("E07 serialized lifecycle cancellation audit order")
    cancelled = after[parent]["cancelled_at"]
    allowed = {"status", "block_kind", "note", "cancelled_at", "completed_at", "updated_at"}
    for index, task in enumerate((parent, worker)):
        old, new = before[task], after[task]
        if (set(old) != set(new) or old["status"] != "in_progress"
                or old["cancelled_at"] is not None or old["completed_at"] is not None
                or json.dumps({k:v for k,v in old.items() if k not in allowed}, sort_keys=True)
                != json.dumps({k:v for k,v in new.items() if k not in allowed}, sort_keys=True)
                or new["status"] != "cancelled" or new["block_kind"] is not None
                or new["note"] != "cancelled by founder: E07_CANCEL_TREE"
                or new["cancelled_at"] != cancelled or new["completed_at"] != cancelled):
            raise ValueError("E07 serialized exact cancellation field/key delta")
        updated = instant(new["updated_at"])
        if not (instant(old["created_at"]) <= instant(old["updated_at"]) < instant(cancelled)
                <= updated <= instant(events[index]["timestamp"])):
            raise ValueError("E07 serialized positive cancellation update/audit bounds")
        if index and instant(events[index-1]["timestamp"]) > updated:
            raise ValueError("E07 serialized parent audit before worker update")
        for sample, row in ((live, old), (frozen, new)):
            listed = [t for t in sample["public"]["alpha"]["tasks"] if t["task_id"] == task]
            if (len(listed) != 1 or listed[0]["updated_at"] != e07_sql_date(row["updated_at"])
                    or sample["public"][task][""]["task"]["updated_at"] != e07_sql_date(row["updated_at"])):
                raise ValueError("E07 serialized public/SQL lifecycle update mismatch")


def e07_pause_state(raw: dict) -> dict:
    """Independent serialized validator; never consume a producer verdict/copy."""
    if not isinstance(raw, dict) or not isinstance(raw["observed_at"], str):
        raise ValueError("E07 serialized Pause observation missing/string")
    stamp = raw["observed_at"]
    instant = datetime.fromisoformat(stamp)
    if "T" not in stamp or instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError("E07 serialized Pause observation must be timezone-aware UTC")
    if not isinstance(raw["blockers"], list):
        raise ValueError("E07 serialized Pause blockers must be an ordered array")
    for blocker in raw["blockers"]:
        if not isinstance(blocker, dict) or not isinstance(blocker["observed_at"], str) or blocker["observed_at"] != stamp:
            raise ValueError("E07 serialized blocker observation disagrees")
    result = copy.deepcopy(raw)
    del result["observed_at"]
    for blocker in result["blockers"]:
        del blocker["observed_at"]
    return result


def e07_detail_state(raw: dict) -> str:
    value = copy.deepcopy(raw)
    value["pause"] = e07_pause_state(raw["pause"])
    return json.dumps(value, sort_keys=True)


def e07_public_state(sample: dict, http: list[dict]) -> str:
    raw = sample["public"]
    indices = sample["http_indices"]
    if set(indices) != set(raw):
        raise ValueError("E07 serialized HTTP observation keys")
    value = copy.deepcopy(raw)
    for key, body in raw.items():
        entries = [(indices[key], f"/api/v1/orgs/{key}/tasks?limit=100", body)] if key in ("alpha", "beta") else [
            (indices[key][suffix], f"/api/v1/orgs/alpha/tasks/{key}"+suffix, response) for suffix,response in body.items()]
        if key not in ("alpha", "beta") and set(indices[key]) != set(body):
            raise ValueError("E07 serialized detail/recall observation keys")
        for index, path, response in entries:
            if type(index) is not int or not 0 <= index < len(http):
                raise ValueError("E07 serialized HTTP observation index")
            actual = http[index]
            if ((actual["method"], actual["path"], actual["status"]) != ("GET", path, 200)
                    or json.dumps(actual["response"], sort_keys=True) != json.dumps(response, sort_keys=True)):
                raise ValueError("E07 raw same-observation HTTP response mismatch")
        # Authenticated RAW response above; only then construct comparison state.
        if key in ("alpha", "beta"):
            if not isinstance(body["tasks"], list):
                raise ValueError("E07 serialized task page array")
            for task in value[key]["tasks"]:
                task["pause"] = e07_pause_state(task["pause"])
        else:
            value[key][""] = json.loads(e07_detail_state(body[""]))
    return json.dumps(value, sort_keys=True)


def validate_e07_admission(row: dict, observations: dict, directory: Path) -> tuple[int,int,dict]:
    """Reconstruct finite operation ownership from full raw source/action joins."""
    from collections import Counter
    def check(value, reason):
        if not value:
            raise ValueError('E07 admission content: ' + reason)
    def same(a,b):
        return json.dumps(a,sort_keys=True,allow_nan=False) == json.dumps(b,sort_keys=True,allow_nan=False)
    # Original first48 are a real dependency of these finite checkpoints, not
    # invented prefix receipts. Reuse their complete existing independent reader.
    validate_e04(row,observations,json.loads((directory/'browser-events.json').read_text()),directory)
    a = observations['e07_admission']
    check(a['state'] == 'accepted' and a['refusal'] is None and a == observations['e07']['admission'] == row['e07_admission'], 'actual admission export equality')
    check(a['checkpoint'] in {'pre-E05','C','N','D'}, 'finite genuine checkpoint')
    name = a['checkpoint']
    S,H = {'pre-E05':(29,17),'C':(32,20),'N':(33,21),'D':(36,24)}[name]
    check(type(a['starts']) is int and type(a['host']) is int and (a['starts'],a['host']) == (S,H), 'source-selected checkpoint totals')
    case = next(d['threads_cases'] for d in observations['durable'] if 'threads_cases' in d)
    point = case['checkpoints'][-1]
    check(all(type(t) in (int,float) and math.isfinite(t) for t in
              (case['entered'],point['at'],case['cleanup_finished'],case['cleanup_deadline'],
               a['entered'],a['finished'],observations['e07']['scenario_deadline']))
          and same(point,a['point']) and point['name'] == name
          and case['entered'] <= point['at'] <= case['cleanup_finished'] <= a['entered'] < a['finished']
          and case['cleanup_finished'] <= case['cleanup_deadline'], 'original settled operation and original cleanup end')
    check(a['finished']+50 < observations['e07']['scenario_deadline'], 'full50s original work remains after admission')
    check(all(f['stage'] == 'controller-action' for f in observations['failures'] if f['at'] <= a['finished']), 'no pre-admission safety/teardown/export failure')
    boots = observations['boots']
    check(len(boots) == 2 and same(a['boots'],boots) and point['anchor']['daemon_pid'] == boots[1]['pid']
          and point['anchor']['daemon_start'] == boots[1]['start'], 'same two real boots/current index1 generation')
    check(set(case['source_hashes']) == {
        'e2e/threads.py','e2e/thread_provider.py','e2e/provider.py','e2e/controller.py','e2e/browser.py',
        'e2e/transport.py','e2e/oracles.py','e2e/aggregate.py','tests/integration/fake_claude.sh','cli/commands/threads.py',
        'runtime/daemon/routes/threads.py','runtime/daemon/thread_runner.py','web/src/shared/threads/NewThreadDialog.tsx',
        'cli/_shared.py','cli/commands/tasks.py','runtime/daemon/routes/tasks.py','web/src/features/threads/ThreadsPage.tsx',
        'web/src/design-system/patterns/MessageBubble.tsx','web/src/design-system/patterns/Markdown.tsx',
        'web/src/design-system/patterns/AgentChip.tsx'}, 'complete original source membership')
    source = Path(__file__).resolve().parents[1]
    check(a['source_sha'] == row['source_sha'] and a['python'] == observations['e07']['python']
          and all(hashlib.sha256((source/n).read_bytes()).hexdigest() == digest for n,digest in case['source_hashes'].items()), 'original source/interpreter equality')
    transport = case['transport']
    check(len({transport[k] for k in ('daemon_port','cli_port','browser_port')}) == 3
          and a['ports'] == transport['ports'] and all(transport[k] in a['ports'] for k in ('daemon_port','cli_port','browser_port'))
          and (transport['daemon_pid'],transport['daemon_start']) == (boots[1]['pid'],boots[1]['start']), 'original D/L/P registry and daemon identity')
    closures = [d['thread_proxy_closure'] for d in observations['durable'] if 'thread_proxy_closure' in d]
    check(len(closures) == 1 and not any(closures[0][k] for k in ('errors','connections','workers_alive','listener_alive'))
          and closures[0]['finished'] <= case['cleanup_finished'], 'positive proxy listener/worker/socket closure')
    events = json.loads((directory/'e05-browser-events.json').read_text())
    validate_e05_safety(observations, events)
    validate_e05_originals(observations,directory)
    current = a['frame']
    check(same(current['sql'],point['frame']['sql'])
          and e07_public_state(current,observations['http']) == e07_public_state(point['frame'],observations['http']), 'stable full checkpoint SQL/files/public/rawHTTP')
    check(not current['pending'] and not point['frame']['pending'], 'no unknown peer')
    operation_count = {'pre-E05':0,'C':1,'N':2,'D':3}[name]
    check(not any('mutation_entered' in g for g in case['groups'][operation_count:]), 'no later unclassified mutating attempt')
    groups = case['groups'][:operation_count]
    check(not groups or same(point['thread_frame'],groups[-1]['settled']['content']),
          'full checkpoint values equal genuine last settled operation')
    check([g['name'] for g in groups] == ['C','N','D'][:len(groups)], 'complete selected operations in source order')
    validate_e05_operations(row,observations,point,groups)
    providers = [p for g in groups for p in g['providers']+([g['task']] if g['name'] == 'D' else [])]
    check(len(providers) == S-29 and len(point['starts']) == S
          and point['starts'] == observations['starts'][:S]
          and {p['session'] for p in providers} == {p['session'] for p in point['starts'][29:]}, 'full task/thread provider membership')
    check(len(point['processes']) == S-29
          and {(p['pid'],p['start']) for p in point['processes']} == {(p['pid'],p['pid_start']) for p in point['starts'][29:]},
          'complete original PID-generation/process membership')
    group_paths = set()
    for process in point['processes']:
        check(set(process) == {'pid','start','state','ppid','argv','cgroup','at'}
              and type(process['pid']) is int and type(process['ppid']) is int
              and type(process['start']) is str and process['start'].isdigit()
              and process['at'] <= point['at'], 'actual bound provider process grammar')
        paths = [line[3:] for line in process['cgroup'].splitlines() if line.startswith('0::')]
        check(len(paths) == 1 and paths[0].rsplit('/',1)[-1].startswith('happyranch-')
              and paths[0].endswith(('.service','.scope')), 'actual pinned provider containment group')
        group_paths.add(paths[0])
    check(set(point['provider_groups']) == set(a['provider_groups']) == group_paths
          and all(not point['provider_groups'][path]['pids'] and not a['provider_groups'][path]['pids']
                  and point['provider_groups'][path]['at'] <= point['at'] <= a['provider_groups'][path]['at'] <= a['finished']
                  for path in group_paths), 'positive pinned groups empty at both checkpoint and admission')
    check(point['thread_command_count'] == point['thread_attempt_count'] == len(observations['thread_commands']) == {29:0,32:4,33:5,36:11}[S]
          and all(a.get('command_index') == i and 'finished' in a for i,a in enumerate(observations['thread_attempts'])), 'exact complete source command and attempt inventory')
    closed_providers = [d['thread_process_closed'] for d in observations['durable'] if 'thread_process_closed' in d]
    check(len(closed_providers) == S-29 and {r['session'] for r in closed_providers} == {p['session'] for p in providers}
          and all(r['observed_start'] != r['start'] and all(not f['exists'] for f in r['private_files'])
                  and all(ch['observed_start'] != ch['start'] for ch in r['children']) for r in closed_providers), 'every genuine provider and child/private-file closure')
    known = []
    for g,subject,messages,turns,host_total in zip(groups,
            ('E05 compose race','E05 denial venue','E05 dispatch'),
            (((1,'founder','message','E05 opening\nliteral beta'),(2,'case_manager','message','E05 reply\n@case_worker received')),
             ((1,'founder','message','E05 denial anchor'),),
             ((1,'founder','message','E05 do one task'),(2,'case_manager','system',None),(3,'case_manager','message','E05 task accepted'),(4,'case_manager','system',None),(5,'case_manager','message','E05 task complete'))),
            (2,1,3),(20,21,24)):
        settled = g['settled']
        frame = settled['content']
        thread = next(t for t in frame['threads'] if t['id'] == g['thread'])
        check(thread['subject'] == subject and thread['turns_used'] == turns and thread['status'] == 'open'
              and thread['turn_cap'] == 500, 'literal settled operation state')
        actual = [m for m in frame['thread_messages'] if m['thread_id'] == g['thread']]
        check([(m['seq'],m['speaker'],m['kind'],m['body_markdown']) for m in actual] == list(messages), 'full finite source-owned message content/order')
        check(settled['public']['messages'] == settled['messages_page']['messages']
              and settled['messages_page']['has_more'] is False and not settled['public']['reply_delivery']
              and not settled['messages_page']['reply_delivery'] and json.loads(settled['cli']['stdout']) == settled['public']
              and settled['cli']['exit'] == 0, 'full public/messages/actual CLI operation content')
        check(any(h['response'] == settled['public'] and h['path'] == f"/api/v1/orgs/alpha/threads/{g['thread']}" and h['status'] == 200 for h in observations['http']), 'actual public original rawHTTP')
        inv = [r for r in frame['thread_invocations'] if r['thread_id'] == g['thread']]
        check(len(inv) == len(g['providers']) and {r['session_id'] for r in inv} == {p['session'] for p in g['providers']}
              and all(r['started_at'] and r['consumed_at'] and r['status'] in {'consumed','declined'} for r in inv), 'complete genuine terminal invocation membership')
        delivery = [r for r in frame['thread_reply_delivery_state'] if r['thread_id'] == g['thread']]
        check(len(delivery) == len({p['agent'] for p in g['providers']}) and all(
              r['required_through_seq'] == r['acknowledged_through_seq'] and all(r[k] is None for k in
              ('queued_invocation_token','running_invocation_token','running_from_seq','running_through_seq')) for r in delivery)
              and all(not frame[k] for k in ('thread_reply_exchange','thread_exchange_deferrals','thread_reply_breaker_episodes','thread_reply_breaker_receipts')), 'no delivery/exchange/breaker residue')
        anchor = g['anchor']
        check(anchor['total'] == host_total and anchor['provider_count'] == {'C':32,'N':33,'D':36}[g['name']]
              and anchor in [d['jobs_anchor'] for d in observations['durable'] if 'jobs_anchor' in d], 'actual source-owned operation anchor')
        known.append(g['thread'])
    check([t['id'] for t in point['thread_frame']['threads']] == known
          and len(point['thread_frame']['thread_messages']) == {'pre-E05':0,'C':2,'N':3,'D':8}[name]
          and len(point['thread_frame']['thread_invocations']) == {'pre-E05':0,'C':3,'N':4,'D':6}[name], 'entire finite thread/message/invocation inventory')
    for org in ('alpha','beta'):
        old = next(d['snapshot'] for d in observations['durable'] if d.get('jobs_final_org') == org)
        tables = current['sql'][org]['tables']
        extra = groups[-1]['task']['task'] if name == 'D' and org == 'alpha' else None
        check(same([t for t in tables['tasks'] if t['id'] != extra],old['tasks'])
              and same([r for r in tables['task_results'] if r['task_id'] != extra],old['results'])
              and len(tables['tasks']) == len(old['tasks'])+bool(extra)
              and len(tables['task_results']) == len(old['results'])+bool(extra)
              and all(t['status'] == 'completed' for t in tables['tasks']), 'independently reconstructed complete task/result originals ' + org)
        if extra:
            result = next(r for r in tables['task_results'] if r['task_id'] == extra)
            callback = groups[-1]['callback']
            check(result['session_id'] == groups[-1]['task']['session'] and result['status'] == 'completed'
                  and result['output_summary'] == callback['payload']['summary'] == 'E05 task complete'
                  and callback in observations['thread_commands'] and callback['selected_response'] == {'ok':True}, 'one genuine D INTEGER callback result')
    check(point['anchor']['total'] == H and point['anchor']['provider_count'] == S
          and Counter(w['kind'] for w in point['witnesses']) == {'stub':S,'callback':point['command_count']+len(observations['provider_commands'])+len(observations['thread_commands'])}, 'full independent prefix witness inventory')
    return S,H,point


def validate_e07_failures(row: dict, observations: dict, archive: dict, trace: object) -> None:
    """The complete reader's error boundary, using original catch/operation joins.

    This does not establish admission, content, source, export or completion by
    itself. validate_e07 retains all those independent gates around this call.
    """
    def check(value: bool, reason: str) -> None:
        if not value:
            raise ValueError('E07 failure attribution: ' + reason)
    def same(a: object, b: object) -> bool:
        return json.dumps(a,sort_keys=True,allow_nan=False) == json.dumps(b,sort_keys=True,allow_nan=False)
    def number(value: object) -> bool:
        return type(value) in (int,float) and math.isfinite(value) and value >= 0

    check(type(trace) is list and observations['observer_error'] is None,
          'complete browser stream and no observer error')
    failures, original = observations['failures'], archive['failures']
    check(type(failures) is list and type(original) is list, 'original failure lists')
    for failure in failures + original:
        check(type(failure) is dict and set(failure) == {'stage','error','at'}
              and type(failure['stage']) is str and type(failure['error']) is str
              and bool(failure['error']) and number(failure['at']), 'closed finite original failure')
    check(same(row['failures'],failures) and same(original,failures[:len(original)]),
          'original failure prefix and final export equality')
    check(all(a['at'] <= b['at'] for a,b in zip(failures,failures[1:])), 'ordered original failures')
    group = observations['groups']['E07']
    prior_group = archive['groups']['E07']
    case, admission = observations['e07'], observations['e07_admission']
    check(same(row['groups']['E07'],group) and group['state'] == 'reported_pass'
          and group['first_error_ref'] is None and prior_group['state'] == 'running'
          and prior_group['first_error_ref'] is None and prior_group['end'] is None
          and same(prior_group['begin'],group['begin'])
          and prior_group['variants'] == group['variants'] == MANIFEST['variants'][53:],
          'original running E07 and completed error-free group')
    cutoff = archive['observer_state']['cutoff']
    times = (group['begin'], admission['entered'], cutoff, admission['finished'],
             case['entered'], case['finished'], group['end'])
    check(all(number(t) for t in times) and all(a <= b for a,b in zip(times,times[1:])),
          'original admission, prefix and complete E07 chronology')
    if original:
        cases = [d['threads_cases'] for d in observations['durable'] if 'threads_cases' in d]
        archived = [d['threads_cases'] for d in archive['durable'] if 'threads_cases' in d]
        check(len(cases) == len(archived) == 1 and same(cases[0],archived[0]), 'original E05 catch export')
        e05, owner = cases[0], observations['groups']['E05']
        caught = e05['failure']
        check(len(original) == 1 and type(caught) is dict
              and set(caught) == {'site','error_ref','caught','ordinary','primary'}
              and caught['site'] == 'threads.exercise' and type(caught['error_ref']) is int
              and caught['error_ref'] == 0 and caught['ordinary'] is True and caught['primary'] is True
              and original[0]['stage'] == 'controller-action'
              and type(owner['first_error_ref']) is int and owner['first_error_ref'] == 0
              and owner['state'] in {'failed','unknown'}
              and same(owner,archive['groups']['E05']) and same(owner,row['groups']['E05'])
              and owner['variants'] == MANIFEST['variants'][48:53], 'original ordinary E05 first cause')
        times = (owner['begin'], e05['entered'], original[0]['at'], caught['caught'],
                 e05['cleanup_finished'], owner['end'], group['begin'])
        check(all(number(t) for t in (*times,e05['cleanup_deadline']))
              and all(a <= b for a,b in zip(times,times[1:]))
              and e05['cleanup_finished'] <= e05['cleanup_deadline'], 'E05 catch and cleanup before E07')
        for outcome in e05['case_outcomes'].values():
            check(outcome['error_ref'] is None or
                  (outcome['status'] == 'FAIL' and type(outcome['error_ref']) is int
                   and outcome['error_ref'] == 0), 'E05 case error reference')

    final = observations['finalization']
    check(type(final) is dict and set(final) == {'entered','finished','attempts'}
          and same(final,row['finalization']) and number(final['entered']) and number(final['finished'])
          and group['end'] <= final['entered'] <= final['finished']
          and type(final['attempts']) is list, 'original finalization export and interval')
    # This action owns only the controller's log descriptor. Resource, browser,
    # observer, source and evidence writers are not unrelated final operations.
    accounted = list(range(len(original)))
    previous = final['entered']
    operations = {'thread-proxy-release','late-transport-release','provider-observer-join','browser-teardown',
        'thread-proxy-close','late-transport-close','peer-socket-shutdown','peer-stream-close','peer-socket-close',
        'provider-server-close','provider-socket-unlink','cli-relay-close','daemon-term','daemon-wait','daemon-log-close'}
    for attempt in final['attempts']:
        check(type(attempt) is dict and set(attempt) ==
              {'owner','operation','entered','finished','failure_refs','same_cause','ordinary','main_thread'}
              and attempt['owner'] == 'Controller.close' and type(attempt['operation']) is str
              and attempt['operation'] in operations
              and number(attempt['entered']) and number(attempt['finished'])
              and previous <= attempt['entered'] <= attempt['finished'] <= final['finished']
              and attempt['main_thread'] is True and attempt['same_cause'] is True
              and type(attempt['failure_refs']) is list, 'source-owned final attempt interval')
        previous = attempt['finished']
        refs = attempt['failure_refs']
        check(all(type(ref) is int and 0 <= ref < len(failures) for ref in refs), 'final failure reference')
        if not refs:
            check(attempt['ordinary'] is None, 'successful final attempt has no error')
            continue
        check(attempt['operation'] == 'daemon-log-close' and attempt['ordinary'] is True
              and refs == list(range(len(accounted),len(accounted)+len(refs)))
              and [failures[ref]['stage'] for ref in refs] in
                  (['daemon-log-close'],['bounded','daemon-log-close'])
              and all(attempt['entered'] <= failures[ref]['at'] <= attempt['finished'] for ref in refs),
              'unrelated final log descriptor failure and exact cause references')
        accounted.extend(refs)
    check(accounted == list(range(len(failures))), 'unattributed or E07 failure remains')


def validate_e07(row: dict, observations: dict, directory: Path) -> None:
    """Reopen serialized C evidence; no producer oracle or expected helper import."""
    import hashlib
    import socket
    from collections import Counter

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E07 content: " + message)

    S,H,point = validate_e07_admission(row, observations, directory)
    case = json.loads((directory / "e07.json").read_text())
    check(case == observations["e07"] == row["e07"], "actual raw/observations/scenario export equality")
    check(case["launch_record_failures"] == [], "missing/malformed/incomplete/unexpected private launch record")
    variants = ["E07.cancel-cascade-browser","E07.cancel-terminal-unchanged","E07.cancelled-late-cli"]
    check(case["variants"] == variants and case["prefix"]["variants"] in (MANIFEST['variants'][:48],MANIFEST['variants'][:53])
          and list(row["variants"]) == case["prefix"]["variants"] + variants
          and case["design_sha256"] == MANIFEST["e07_c_design_sha256"]
          and case["source_sha"] == row["source_sha"], "exact C manifest/design/source/preserved53")
    for name in variants:
        check(row["variants"][name]["status"] == "PASS" and row["variants"][name]["evidence"] == {"e07_file":"e07.json","source_sha":row["source_sha"]}
              and row["variants"][name]["at"] >= case["finished"], "new marks follow actual case content")
    phase = next(p for p in row["phases"] if p["name"] == "scenarios")
    check(case["end"] == min(case["entered"]+50,case["scenario_deadline"])
          and case["scenario_deadline"] == phase["deadline"] and case["entered"] < case["finished"] <= case["end"], "original C/scenario deadline binding")
    check([p["name"] for p in case["phases"]] == ["capture","cancel-and-close","terminal-ui-and-refusals","forward-and-observe","archive"], "finite C-only phases")
    for p,offset in zip(case["phases"],(16,28,36,42,50)):
        check(p["end"] == min(case["entered"]+offset,case["end"],case["scenario_deadline"]) and p["entered"] < p["end"], "fixed absolute phase ends")
    check(all(a["entered"] < b["entered"] <= a["end"] for a,b in zip(case["phases"],case["phases"][1:])), "no overrun borrows next phase")
    e05 = next(d["threads_cases"] for d in observations["durable"] if "threads_cases" in d)
    boundary = case["prefix"]
    archive_raw = (directory/"e07-prefix.json").read_bytes()
    archive = json.loads(archive_raw)
    check(hashlib.sha256(archive_raw).hexdigest() == boundary["archive_sha256"]
          and archive["starts"] == observations["starts"][:S]
          and archive["commands"] == observations["commands"][:boundary["command_count"]]
          and archive["provider_commands"] == observations["provider_commands"]
          and archive["thread_commands"] == observations["thread_commands"]
          and archive["provider_observer"] == observations["provider_observer"][:boundary["observer_end"]]
          and archive["variants"] == {k:observations["variants"][k] for k in boundary["variants"]},
          "actual pre-C archived original53 outcomes and command/observer prefix")
    check(case["anchors"] == [dict(name='E05-end-B24' if len(case['prefix']['variants']) == 53 else f"E05-checkpoint-{point['name']}-B{H}",samples=[case["baseline"]]),
          dict(name=f"P-success-W-live-B{H+1}",samples=case["live"]),dict(name=f"cancelled-B{H+2}",samples=case["freeze"]),
          dict(name=f"final-B{H+2}",samples=[case["final"]])], "dedicated complete E07 boundary content")
    raw = (directory / "launch-witnesses.jsonl").read_bytes()
    check(raw.endswith(b"\n") and len(raw) == observations["witness_bytes"]
          and hashlib.sha256(raw).hexdigest() == observations["witness_sha256"], "complete actual final append-only bytes")
    all_witness = [json.loads(line) for line in raw.splitlines()]
    prefix_raw = raw[:boundary["witness_bytes"]]
    check(prefix_raw.endswith(b"\n") and hashlib.sha256(prefix_raw).hexdigest() == boundary["witness_sha256"]
          and len(prefix_raw.splitlines()) == boundary["witness_records"]
          and [json.loads(line) for line in prefix_raw.splitlines()] == point["witnesses"]
          and all_witness == observations["launch_witnesses"] and observations["witness_incomplete_line"] is False,
          "genuine E05 byte/record boundary with exact original content")
    check(boundary["e05_finished"] == e05.get("finished")
          and (boundary['e05_finished'] is None or boundary['e05_finished'] <= case['entered'])
          and observations["provider_observer"][boundary["observer_end"]-1]["stubs"] == S,
          "original E05 observer boundary, no final38 substitution")
    starts = observations["starts"]
    parent,worker = case["parent"],case["worker"]
    P,W = parent["task"],worker["task"]
    check(len(starts) == len({s["session"] for s in starts}) == S+2 and starts[S:] == [parent,worker]
          and P != W and parent["agent"] == "case_manager" and worker["agent"] == "case_worker"
          and parent["org"] == worker["org"] == "alpha", "exact two genuine suffix starts")
    check(Counter(w["kind"] for w in all_witness) == {"stub":S+2,"callback":len(observations["commands"])+len(observations["provider_commands"])+len(observations["thread_commands"])+4}, "all old/new executable witnesses accounted")
    commands = observations["e07_commands"]
    check(commands == case["commands"] and [c["action"] for c in commands] == ["e07-parent","e07-held","terminal-cancel","terminal-cancel"], "dedicated exact four-command ledger")
    suffix = all_witness[boundary["witness_records"]:]
    check(Counter(w["kind"] for w in suffix) == {"stub":2,"callback":4}, "every appended record classified")
    callbacks = [w for w in suffix if w["kind"] == "callback"]
    check(all(w["source_sha"] == row["source_sha"] for w in suffix), "new wrapper/stub source equality")
    cli_digest = hashlib.sha256((Path(__file__).resolve().parents[1]/"cli/main.py").read_bytes()).hexdigest()
    check(all(w["cli_sha256"] == cli_digest for w in callbacks), "actual shipping CLI source hash")
    manifest = next(d["executable_manifest"] for d in observations["durable"] if "executable_manifest" in d)
    check(case["python"] == manifest["python"], "bound interpreter")
    # The task templates select Claude with unset model/effort; these are fresh
    # P/W tasks, not resumed sessions. ClaudeAdapter sends seven arguments:
    # -p --permission-mode MODE --allowedTools RULES --output-format json.
    # fake_claude.sh preserves that original argv for the unchanged guard.
    # Recompute the exact task plan bytes independently, without importing the
    # producer or guard's expected-construction helpers.
    import shlex
    wrappers = next(d["wrappers"] for d in observations["durable"] if "executable_manifest" in d)
    source = Path(__file__).resolve().parents[1]
    stub = manifest["stubs"]["claude"]
    stub_sha = hashlib.sha256((source / "tests/integration/fake_claude.sh").read_bytes()).hexdigest()
    plan_path = str(Path(manifest["root"]) / "coord/plan.sh")
    plan_bytes = ("#!/bin/sh\nexec " + shlex.quote(manifest["python"]) + " -I "
                  + shlex.quote(str(Path(manifest["source"]) / "e2e/provider.py")) + ' "$@"\n').encode()
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    check(manifest["revision"] == row["source_sha"]
          and stub["path"] == str(Path(manifest["root"]) / "bin/claude")
          and stub["sha256"] == wrappers[stub["path"]] == stub_sha
          and wrappers[plan_path] == plan_sha
          and wrappers[plan_path + ".sha256"] == hashlib.sha256(plan_sha.encode()).hexdigest(),
          "actual immutable source/stub/task-plan bindings")
    stubs = [w for w in suffix if w["kind"] == "stub"]
    for witness in stubs:
        check(set(witness) == {"kind","provider","stub_sha256","source_sha","plan_sha256","argc","flags","format_json"}
              and witness["format_json"] is False and type(witness["argc"]) is int and witness == dict(
                  kind="stub", provider="claude", stub_sha256=stub_sha, source_sha=row["source_sha"],
                  plan_sha256=plan_sha, argc=7, flags=["-p","--output-format"], format_json=False),
              "complete new stub record and concrete fresh Claude launch contract")
    source_names = ["e2e/owned.py","e2e/transport.py","e2e/jobs.py","e2e/threads.py",
        "e2e/thread_provider.py","e2e/pause_browser.py","runtime/daemon/__main__.py","runtime/daemon/paths.py","e2e/e07.py","e2e/late_transport.py","e2e/provider.py","e2e/controller.py","e2e/oracles.py",
                    "e2e/browser.py","e2e/aggregate.py","e2e/run.py","e2e/manifest.json","cli/commands/tasks.py",
                    "runtime/daemon/routes/tasks.py","runtime/models.py","runtime/infrastructure/db/tasks.py",
        "runtime/infrastructure/task_pause_controls.py","runtime/orchestrator/run_step.py","web/src/features/tasks/CancelTaskDialog.tsx"]
    check(set(case["source_files"]) == set(source_names) and all(
        hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest() == case["source_files"][name]
        for name in source_names), "actual complete harness/shipping seam source hashes")
    check(case["partial_launches"] == [cmd["launch"] for cmd in commands[:2]]
          and all("incomplete" not in launch and launch["provider"] == {
              k:start[k] for k in ("task","session","agent","org","pid")}
              for launch,start in zip(case["partial_launches"],(parent,worker))),
          "both actual complete pre-wait launch records in issued-command order")
    for index,cmd in enumerate(commands):
        launch = cmd["launch"] if index < 2 else cmd
        child = launch["child"]
        argv = launch["argv"]
        match = [w for w in callbacks if w["pid"] == child["pid"] and w["argv_sha256"] == hashlib.sha256(json.dumps(argv[1:]).encode()).hexdigest()]
        check(len(match) == 1 and match[0]["ppid"] == child["ppid"]
              and launch["source_sha"] == row["source_sha"] and launch["python"] == manifest["python"]
              and argv[0] == manifest["callback"] and child["start"], "each genuine CLI authority/source/argv/PID-start")
        if index < 2:
            check(child["ppid"] == (parent if index == 0 else worker)["pid"]
                  and launch["callback_sha256"] == manifest["callback_sha256"]
                  and launch["entered"] <= launch["acknowledged"] <= case["phases"][1]["entered"]
                  and launch["execution_deadline"] == cmd["instruction"]["execution_deadline"], "provider pre-wait acknowledgement")
            expected = dict(task_id=P if index == 0 else W, session_id=parent["session"] if index == 0 else worker["session"],
                            agent="case_manager" if index == 0 else "case_worker",status="completed",confidence=90,
                            summary="E07 parent delegated" if index == 0 else "E07 cancelled child must not complete")
            if index == 0:
                expected["decision"] = {"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"}
            check(launch["payload"] == expected
                  and hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest() == launch["payload_sha256"]
                  and len(json.dumps(expected,sort_keys=True).encode()) == launch["payload_bytes"], "actual provider-bound finite payload")
    check(case["parent_command"] == commands[0] and case["held"] == commands[1] and case["refusals"] == commands[2:], "case references exact ledger")
    parent_command = commands[0]
    ret = parent_command["receipt"]
    check(ret["launch"] == parent_command["launch"] and ret["exit"] == 0 and not ret["timed_out"]
          and all(not ret[k]["truncated"] and not ret[k]["incomplete"] for k in ("stdout","stderr")), "genuine parent success return")
    pw = parent_command["wire"]
    parent_body = dict(session_id=parent["session"],agent="case_manager",status="completed",confidence=90,
                       output_summary="E07 parent delegated",risks_flagged=[],dependencies=[],suggested_reviewer_focus=[],
                       decision={"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"})
    check(len(pw) == 1 and pw[0] in observations["relay"] and pw[0]["path"] == f"/api/v1/orgs/alpha/tasks/{P}/completion"
          and pw[0]["method"] == "POST" and pw[0]["request"] == parent_body and pw[0]["status"] == 200 and pw[0]["response"] == {"ok":True}, "parent accepted actual CLI wire")
    check("receipt" not in commands[1] and case["provider_eof"]["bytes"] == 0, "no posthumous killed CLI return claim")
    transport = case["transport"]
    check(transport["port"] in case["ports"] and transport["upstream"] in case["ports"]
          and transport["port"] != transport["upstream"], "distinct owned registered listener/upstream")
    selected = [r for r in transport["records"] if r["selected"]]
    check(len(selected) == 1 and all(r["selected"] or (r["method"] == "GET" and r["path"] == "/api/v1/orgs") for r in transport["records"]), "one selected original request, no retries/extras")
    wire = selected[0]
    body = dict(session_id=worker["session"],agent="case_worker",status="completed",confidence=90,
                output_summary="E07 cancelled child must not complete",risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
    check(wire["upstream"] == transport["upstream"] and wire["method"] == "POST" and wire["path"] == f"/api/v1/orgs/alpha/tasks/{W}/completion"
          and wire["request"] == json.loads(wire["request_text"]) == body
          and wire["request_bytes"] == len(wire["request_text"].encode())
          and wire["request_sha256"] == hashlib.sha256(wire["request_text"].encode()).hexdigest()
          and wire["request_truncated"] is False and wire["authorization_sha256"] == transport["bearer_sha256"], "actual full body and private bearer equality")
    provenance = case["provenance"]
    child = commands[1]["launch"]["child"]
    check(provenance["process"]["pid"] == child["pid"] and provenance["process"]["start"] == child["start"]
          and provenance["process"]["ppid"] == worker["pid"] and provenance["process"]["state"] != "Z"
          and len(provenance["tcp"]) == len(provenance["fds"]) == 1, "captured live CLI owns exactly one selected socket")
    tcp,fd = provenance["tcp"][0],provenance["fds"][0]
    fields = tcp["raw"].split()
    def endpoint(value: str) -> list:
        host,port = value.split(":")
        return [socket.inet_ntoa(bytes.fromhex(host)[::-1]),int(port,16)]
    check(endpoint(fields[1]) == tcp["local"] == wire["peer"] and endpoint(fields[2]) == tcp["remote"] == wire["local"]
          and fields[3] == tcp["state"] == "01" and fields[9] == tcp["inode"]
          and fd["target"] == "socket:["+tcp["inode"]+"]" and type(fd["fd"]) is int
          and wire["local"] == ["127.0.0.1",transport["port"]]
          and wire["listener_fd"] == transport["listener"]["fd"] and wire["accepted_inode"].startswith("socket:["), "actual raw /proc tuple/inode/FD/listener join")
    restart = [d["pause_daemon_restart"] for d in observations["durable"] if "pause_daemon_restart" in d]
    check(len(restart) == 1 and transport["upstream"] == restart[0]["new"]["port"]
          and (transport["daemon"]["pid"],transport["daemon"]["start"]) == (restart[0]["new"]["pid"],restart[0]["new"]["start"]), "original boot B, no new restart")
    cancel = case["browser_cancel"]
    check(commands[1]["launch"]["acknowledged"] <= wire["captured"] <= provenance["sampled"] <= cancel["entered"]
          <= cancel["finished"] <= case["freeze"][0]["entered"] <= case["freeze"][1]["finished"] <= case["released"]
          <= wire["forwarded"] <= wire["upstream_complete"] <= wire["closed"] <= case["window"]["entered"], "original pre-forward/cancel/closure/late-response order")
    check(len(transport["accepted"]) == len(transport["records"]) and any(
        a["peer"] == wire["peer"] and a["local"] == wire["local"] and a["fd"] == wire["accepted_fd"]
        and a["inode"] == wire["accepted_inode"] and a["at"] <= wire["entered"] for a in transport["accepted"]),
          "all accepted sockets registered before header/body waits")
    check(provenance["process"]["argv"][0] == case["python"]
          and provenance["process"]["argv"][-5:] == commands[1]["launch"]["argv"][1:], "actual live command line belongs to hash-bound CLI")
    check(wire["status"] == 409 and wire["response"] == json.loads(wire["response_stream"]["text"]) == {"detail":{"code":"task_not_active","task_id":W,"status":"cancelled","cancelled":True}}
          and not wire["response_stream"]["truncated"] and not wire["response_stream"]["incomplete"]
          and wire["downstream"] in {"EOF","peer-gone"}, "literal task-active denial, dead downstream separately")

    def projection(frame: dict, admitted: int, released: int, active: int, tail: dict) -> dict:
        m = frame["metrics"]
        check(any(h["path"] == "/api/v1/metrics" and h["response"] == m for h in observations["http"]), "actual public metrics record")
        h = m["host_sessions"]
        a,r = h["admission"],h["receipts"]
        check((a["admitted_total"],a["released_total"],r["published_total"],a["active"],a["queue_depth"],m["executor_sessions_active"],m["run_step_queue_depth"])
              == (admitted,released,released,active,0,active,0) and a["cancelled_queued_total"] == 0 and not a["shutdown"], "exact counters, no queued cancellation")
        check(h["wired"] and h["backend"]["healthy"] and h["backend"]["name"] != "passthrough"
              and not h["residue"]["admission_blocked"] and h["residue"]["survivors_count"] == 0
              and r["published_total"] == r["window_size"] == r["quiescent_count"] == len(r["recent"]) <= 64
              and r["with_residue_count"] == 0 and all(x["quiescent"] and x["survivors_count"] == 0 for x in r["recent"]), "complete settled retained receipt window")
        check(r["recent"][released-tail["published_total"]:] == tail["recent"] and not frame["pending"], "entire ordered duplicate-preserving tail/no peer")
        return {"admission":a,"receipts":r,"executor_sessions_active":active,"run_step_queue_depth":0}

    check(case['baseline']['sql'] == case['admission']['frame']['sql']
          and e07_public_state(case['baseline'],observations['http']) == e07_public_state(case['admission']['frame'],observations['http']),
          'actual baseline full checkpoint content before the exact P/W suffix')
    live = case["live"]
    prior = point["anchor"]["projection"]["receipts"]
    projection(case["baseline"],H,H,0,prior)
    check(len(live) == 2 and projection(live[0],H+2,H+1,1,prior) == projection(live[1],H+2,H+1,1,prior), "two B26/25/25/1 projections")
    check(live[1]["metrics"]["host_sessions"]["receipts"]["recent"][0]["terminal_reason"] == "success", "P success distinct from active cancellation")
    result = case["parent_result"]
    check(type(result["id"]) is int and result["task_id"] == P and result["session_id"] == parent["session"]
          and result["status"] == "completed" and result["confidence_score"] == 90 and result["output_summary"] == "E07 parent delegated"
          and json.loads(result["decision_json"]) == {"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"}, "one real accepted delegation result")
    live_tasks = {t["id"]:t for t in live[1]["sql"]["alpha"]["tables"]["tasks"]}
    check(live_tasks[P]["status"] == live_tasks[W]["status"] == "in_progress" and live_tasks[P]["block_kind"] == "delegated"
          and live_tasks[W]["block_kind"] is None and live_tasks[W]["parent_task_id"] == P, "actual parked/live tree")
    freeze,second = case["freeze"]
    check(projection(freeze,H+2,H+2,0,live[1]["metrics"]["host_sessions"]["receipts"]) == projection(second,H+2,H+2,0,live[1]["metrics"]["host_sessions"]["receipts"]), "two settled B26 projections")
    newest = freeze["metrics"]["host_sessions"]["receipts"]["recent"][0]
    check(newest["terminal_reason"] == "cancelled" and newest["invocation_kind"] == "task", "one W active cancellation receipt")
    tables = freeze["sql"]["alpha"]["tables"]
    tasks = {t["id"]:t for t in tables["tasks"]}
    check(len(tasks) == (22 if point['name'] == 'D' else 21)
          and len(tables["task_results"]) == (30 if point['name'] == 'D' else 29)
          and Counter(t["status"] for t in tasks.values()) == {"completed":20 if point['name'] == 'D' else 19,"cancelled":2}
          and len(freeze["sql"]["beta"]["tables"]["tasks"]) == len(freeze["sql"]["beta"]["tables"]["task_results"]) == 1, "global23/31 and21completed/two cancelled")
    for task,start in ((P,parent),(W,worker)):
        t = tasks[task]
        check(t["status"] == "cancelled" and t["block_kind"] is None and t["note"] == "cancelled by founder: E07_CANCEL_TREE"
              and t["cancelled_at"] is not None and t["cancelled_at"] == t["completed_at"] == tasks[P]["cancelled_at"]
              and t["current_session_id"] == start["session"], "exact shared cancellation stamp and owner")
        check(freeze["public"][task][""]["task"]["task_id"] == task
              and all(freeze["public"][task][""]["task"][k] == (e07_sql_date(t[k]) if k in ("cancelled_at","completed_at") else t[k])
                      for k in ("status","block_kind","note","cancelled_at","completed_at","current_session_id")), "actual detail fields agree with coherent frame")
    e07_cancellation_transition(live[1],freeze,P,W)
    for task in (P,W):
        recall = freeze["public"][task]["/recall"]
        check(recall["task_id"] == task and recall["status"] == "cancelled", "literal public terminal recall")
        for suffix,body in freeze["public"][task].items():
            check(any(h["method"] == "GET" and h["path"] == f"/api/v1/orgs/alpha/tasks/{task}"+suffix
                      and h["status"] == 200 and h["response"] == body for h in observations["http"]), "real public detail/recall/tree response content")
    recall_tree = freeze["public"][P]["/recall?tree=true"]
    check(recall_tree["task_id"] == P and recall_tree["status"] == "cancelled" and len(recall_tree["children"]) == 1
          and recall_tree["children"][0]["task_id"] == W and recall_tree["children"][0]["status"] == "cancelled", "exact public recall lineage")
    check([r for r in tables["task_results"] if r["task_id"] in (P,W)] == [result], "no worker result or replacement")
    audits = [a for a in tables["audit_log"] if a["task_id"] in (P,W)]
    cancels = [a for a in audits if a["action"] == "task_cancelled"]
    check([a["task_id"] for a in cancels] == [P,W] and all(a["agent"] == "founder" and json.loads(a["payload"]) == {"rationale":"E07_CANCEL_TREE","cascade":True} for a in cancels), "exact two founder audits")
    check(sum(a["action"] == "session_end" and a["task_id"] == W for a in audits) == 1
          and not any(a["task_id"] == W and a["action"] in {"completion_report","orchestration_step"} for a in audits), "one W end, no result consumption")
    decisions = [a for a in audits if a["task_id"] == P and a["action"] == "orchestration_step"]
    check(len(decisions) == 1 and json.loads(decisions[0]["payload"])["decision"]["action"] == "delegate", "P INTEGER result consumed once as delegate")
    baseline = case["baseline"]["sql"]
    check(freeze["sql"]["beta"] == baseline["beta"], "complete unchanged beta")
    for name,old in baseline["alpha"]["tables"].items():
        now = tables[name]
        if name in {"tasks","task_results","audit_log"}:
            check([r for r in now if r.get("task_id",r.get("id")) not in (P,W)] == old, "full old task/result/audit prefix " + name)
        elif name.startswith("task_"):
            check([r for r in now if r.get("task_id",r.get("root_task_id")) not in (P,W)] == old, "full old task lineage/control prefix " + name)
        else:
            check(now == old, "unchanged entire jobs/thread/delivery content " + name)
    check(len(tables["jobs"]) == 7 and freeze["sql"]["alpha"]["files"] == baseline["alpha"]["files"], "unchanged7 jobs/seven scripts and product files")
    for sample in [second,case["after_refusals"],*case["window"]["samples"],case["final"]]:
        check(sample["sql"] == freeze["sql"] and e07_public_state(sample, observations["http"]) == e07_public_state(freeze, observations["http"]), "full task/result/audit/lineage/job/thread/public unchanged")
        projection(sample,H+2,H+2,0,freeze["metrics"]["host_sessions"]["receipts"])
    tree = case["tree"]
    check(tree["chain"][0]["pid"] == worker["pid"] and tree["chain"][-1]["pid"] == tree["launch_pid"] == live_tasks[W]["executor_pid"]
          and tree["launch_pid"] != worker["pid"] and all(a["ppid"] == b["pid"] for a,b in zip(tree["chain"],tree["chain"][1:])), "separate real launch root/plan ancestry")
    expected_pids = {(x["pid"],x["start"]) for x in tree["chain"]+[child]}
    for closed in [case["closure_samples"][-1],*[s["closure"] for s in case["window"]["samples"]]]:
        check({(p["pid"],p["start"]) for p in closed["processes"]} == expected_pids
              and all(p["observed_start"] != p["start"] for p in closed["processes"])
              and all(not g["pids"] for g in closed["groups"].values()) and not closed["pending"], "all actual process generations closed before rescue")
    check(case["closure_samples"][-1]["sampled"] <= freeze["entered"], "product closure precedes Fcancel")
    check({u["unit"] for u in case["units"]} == {g.rsplit("/",1)[-1] for g in case["closure_samples"][-1]["groups"]}
          and all("ActiveState=inactive" in u["stdout"]["text"] and not u["stdout"]["truncated"]
                  and u["at"] < freeze["entered"] for u in case["units"]), "every owned product unit inactive before delayed forward")
    for cmd,task,status,rationale in zip(commands[2:],(P,case["completed_target"]["id"]),("cancelled","completed"),("E07_DUPLICATE","E07_TERMINAL")):
        posts = [w for w in cmd["wire"] if w["method"] == "POST"]
        check(len(posts) == 1 and posts[0] in observations["relay"] and posts[0]["path"] == f"/api/v1/orgs/alpha/tasks/{task}/cancel"
              and posts[0]["request"] == {"rationale":rationale,"cascade":True} and posts[0]["status"] == 409
              and posts[0]["response"] == {"detail":{"code":"task_already_terminal","current_status":status}}
              and cmd["exit"] == 1 and cmd["stdout"]["text"] == f"Task {task} is already {status}; nothing to cancel.\n"
              and cmd["stderr"]["text"] == "" and all(not cmd[k]["truncated"] and not cmd[k]["incomplete"] for k in ("stdout","stderr"))
              and cmd["observed_start"] != cmd["child"]["start"] and cmd["entered"] >= second["finished"], "genuine terminal refusals and full human output")
    check(case["completed_target"] in baseline["alpha"]["tables"]["tasks"] and case["completed_target"]["status"] == "completed", "identified completed baseline target")
    window = case["window"]
    samples = window["samples"]
    check(window["end"] == window["entered"]+1 and len(samples) >= 21
          and samples[0]["entered"]-window["entered"] <= .05 and samples[-1]["entered"] >= window["end"]
          and window["finished"] >= samples[-1]["finished"] and window["finished"] <= case["phases"][3]["end"]
          and all(0 < b["entered"]-a["entered"] <= .05 for a,b in zip(samples,samples[1:]))
          and all(s["finished"]-s["entered"] <= .05 and s["witnesses"] == case["after_refusals"]["witnesses"] for s in samples), "entire fresh one-second <=50ms unchanged window")
    check(case["transport_closure"]["forward_count"] == 1 and case["transport_closure"]["port"] == transport["port"]
          and not any(case["transport_closure"][k] for k in ("listener_alive","workers_alive","sockets","errors")), "owned one-shot transport closure")
    events = case["browser_events"]
    check([e["event"] for e in events] == ["detail-reload","detail-reload","cancel","detail-reload","detail-reload","list-reload"], "full finite browser action ledger")
    check(cancel["status"] == 200 and cancel["method"] == "POST" and cancel["request"] == {"rationale":"E07_CANCEL_TREE"}
          and cancel["response"] == {"ok":True,"task_id":P,"cancelled":[P,W],"killed":[{"task_id":W,"agent":"case_worker"}]}
          and len(cancel["requests"]) == 1 and cancel["requests"][0]["body"] == cancel["request"], "one actual dialog cancellation/no invented PID")
    for view, task, qualifier in ((cancel["parent_view"], P, "· Waiting on child work"),
                                   (cancel["child_view"], W, None)):
        public = view["detail"]["task"]
        check(dict(event="detail-reload", **view) in events
              and view["task"] == public["task_id"] == view["recall"]["task_id"] == task
              and view["url"].endswith(f"/orgs/alpha/tasks/{task}")
              and view["detail_url"].endswith(f"/api/v1/orgs/alpha/tasks/{task}")
              and view["recall_url"] == view["detail_url"]+"/recall?tree=true"
              and view["detail_method"] == view["recall_method"] == "GET"
              and view["detail_status"] == view["recall_status"] == 200
              and view["header"] == dict(identity=task, status="in_progress", qualifier=qualifier)
              and public["status"] == view["recall"]["status"] == "in_progress"
              and view["cancel_disabled"] is False, "own rendered live header and actual detail/tree")
        if task == P:
            check(public["block_kind"] == "delegated" and public["child_wait"]["state"] == "waiting_children"
                  and [node["task_id"] for node in view["recall"]["children"]] == [W]
                  and view["recall"]["children"][0]["status"] == "in_progress", "public waiting parent/child tree")
        else:
            check(public["block_kind"] is None and public["child_wait"] is None
                  and public["parent_task_id"] == P and view["recall"]["parent_task_id"] == P,
                  "public running unblocked child")
    for view,task,name in zip(case["views"][:2],(P,W),("e07-parent.png","e07-child.png")):
        check(dict(event="detail-reload",**view) in events and view["url"].endswith(f"/orgs/alpha/tasks/{task}")
              and view["detail_url"].endswith(f"/api/v1/orgs/alpha/tasks/{task}") and view["recall_url"] == view["detail_url"]+"/recall?tree=true"
              and view["detail_method"] == view["recall_method"] == "GET" and view["detail_status"] == view["recall_status"] == 200
              and e07_detail_state(view["detail"]) == e07_detail_state(freeze["public"][task][""]) and view["recall"] == freeze["public"][task]["/recall?tree=true"]
              and view["cancel_disabled"] is True and view["cancel_title"] == "Cannot cancel a cancelled task"
              and view["badges"] and all(x == "cancelled" for x in view["badges"]) and "cancelled" in view["text"]
              and view["screenshot"] == name and (directory/name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual terminal detail/reload/literal badge/disabled control")
    listing = case["views"][2]
    check(dict(event="list-reload",**listing) in events and listing["method"] == "GET" and listing["status"] == 200
          and listing["url"].endswith("/orgs/alpha/tasks") and listing["roots_url"].split("?")[0].endswith("/api/v1/orgs/alpha/tasks/roots")
          and any(t["task_id"] == P and t["status"] == "cancelled" for t in listing["roots"]["tasks"])
          and "Cancelled" in listing["text"] and listing["parent_href"] == f"/orgs/alpha/tasks/{P}"
          and (directory/listing["screenshot"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual list group and task link")

    # ID18 is an independent exported-content consumer. Producer labels and
    # private raw-byte digests cannot stand in for missing original members.
    from urllib.parse import parse_qs, urlsplit
    import re

    def image(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def strict_file(name: str) -> object:
        def pairs(items: list) -> dict:
            value = {}
            for key, item in items:
                check(key not in value, "duplicate exported JSON key")
                value[key] = item
            return value
        def nonfinite(_value: str) -> None:
            raise ValueError("E07 content: nonfinite exported JSON")
        return json.loads((directory/name).read_bytes().decode("utf-8"),
                          object_pairs_hook=pairs, parse_constant=nonfinite)

    check(image(strict_file("e07.json")) == image(case)
          and image(strict_file("observations.json")) == image(observations)
          and image(strict_file("result.json")) == image(row), "strict complete exported copies")
    admission = daily_admission(Path(__file__).resolve().parents[1])
    check(all(row[key] == value for key, value in admission.items()), "independent source/tree/event/run binding")
    check(directory.name == "e2e-"+row["python"] and row["python"] in ("3.12", "3.14")
          and any(d.get("python_version", "").startswith(row["python"]+".")
                  for d in observations["durable"]), "actual cell/interpreter version")
    trace = strict_file("browser-events.json")
    validate_e07_failures(row, observations, strict_file('e07-prefix.json'), trace)
    ref = listing["roots_observation"]
    check(type(ref) is dict and set(ref) == {"group_id", "checkpoint_site", "selected_request_sequence", "query", "accepted_at"}
          and type(ref["group_id"]) is int and ref["group_id"] > 0
          and ref["checkpoint_site"] == "18", "public exact group/checkpoint reference")
    groups = [item for item in trace if item.get("event") == "response-observation"
              and item.get("group_id") == ref["group_id"]]
    check(len(groups) == 1, "one complete ID18 group in this browser stream")
    group = groups[0]
    origin = "http://127.0.0.1:"+str(restart[0]["new"]["port"])
    destination = origin+"/orgs/alpha/tasks"
    boots = observations["boots"]
    check(len(boots) == 2 and type(group["boot_index"]) is int and group["boot_index"] == 1
          and image(group["boot"]) == image(boots[1])
          and (boots[1]["pid"], boots[1]["start"]) == (restart[0]["new"]["pid"], restart[0]["new"]["start"])
          and restart[0]["new"]["port"] in boots[1]["discovered_ports"], "current original post-Pause boot/port")
    check(group["source_sha"] == case["source_sha"] == row["source_sha"]
          and group["python"] == case["python"] == manifest["python"]
          and group["origin"] == origin and group["org"] == "alpha"
          and group["phase"] == "terminal-ui-and-refusals" and group["checkpoint_site"] == "18"
          and group["path"] == "/api/v1/orgs/alpha/tasks/roots" and group["document"] == "/orgs/alpha/tasks"
          and group["source_url"] == listing["url"] == destination
          and group["mode"] == "new-document" and group["tree"] is False
          and group["capture_json"] is True, "exact source/cell/origin/document/phase")
    steps = group["steps"]
    check(type(steps) is list and steps and steps[0]["event"] == "arm", "original arm")
    end = group["deadline"]
    for step in steps:
        check(type(step["sequence"]) is int and step["sequence"] >= 0
              and type(step["at"]) in (int, float) and steps[0]["at"] <= step["at"] < end,
              "all trace work inside original bound")
    check(all(a["sequence"] <= b["sequence"] and a["at"] <= b["at"] for a,b in zip(steps,steps[1:])),
          "ordered native sequence and synchronous step positions")
    def positions(event: str) -> list[int]:
        return [i for i,step in enumerate(steps) if step["event"] == event]
    def only(event: str) -> int:
        found = positions(event)
        check(len(found) == 1, "one "+event)
        return found[0]
    arm,nav,commit,frozen,guarded,cleanup,sealed = (only(name) for name in
        ("arm", "navigation-request", "commit", "checkpoint", "final-guard", "local-cleanup", "seal"))
    check(arm == 0 and arm < nav < commit < frozen < guarded < cleanup < sealed == len(steps)-1
          and steps[nav]["expected"] is True and steps[nav]["main_frame"] is True
          and steps[nav]["method"] == "GET" and steps[nav]["unredirected"] is True
          and steps[nav]["url"] == steps[commit]["url"] == destination
          and steps[commit]["expected"] is True and steps[nav]["sequence"] < steps[commit]["sequence"]
          and steps[commit]["document_sequence"] == steps[nav]["sequence"], "actual unredirected navigation Request/commit")
    source_sequence = group["source_document_sequence"]
    check(type(source_sequence) is int and 0 < source_sequence < steps[arm]["sequence"]
          and group["source_document_url"] == group["source_url"] == destination
          and group["source_navigation_pending"] is False and group["source_unredirected"] is True,
          "armed exact original source document attribution")
    for i, step in enumerate(steps):
        if step["event"] != "same-document":
            continue
        check(arm < i and steps[arm]["sequence"] < step["sequence"]
              and step["expected"] is True and step["url"] == destination,
              "same-document exact nondeparting URL")
        if i < commit:
            check(step["document_sequence"] == source_sequence
                  and step["sequence"] < steps[commit]["sequence"]
                  and (step["sequence"] > steps[nav]["sequence"] if nav < i
                       else step["sequence"] < steps[nav]["sequence"])
                  and step["pending_navigation_sequence"] == (steps[nav]["sequence"] if nav < i else None),
                  "precommit remains old source with only selected pending Request")
        else:
            check(step["document_sequence"] == steps[nav]["sequence"]
                  and step["sequence"] > steps[commit]["sequence"]
                  and step["pending_navigation_sequence"] is None,
                  "postcommit retains new Request document lineage")
    check(steps[guarded]["document_sequence"] == steps[nav]["sequence"]
          and steps[guarded]["boot_index"] == 1 and steps[guarded]["failure"] is False
          and steps[guarded]["poisoned"] is False, "final non-yielding current-document/boot/error guard")
    check(not any(step["event"] in {"request-failed", "ineligible-response", "observation-error"} for step in steps),
          "no member failure or refused ownership")
    terminal_phase = case["phases"][2]
    check(boots[1]["entered"] < terminal_phase["entered"] <= steps[arm]["at"]
          and second["finished"] <= steps[arm]["at"]
          and steps[arm]["at"] <= listing["at"] <= steps[frozen]["at"]
          and steps[sealed]["at"] <= ref["accepted_at"] < end
          and end <= min(steps[arm]["at"]+4, terminal_phase["end"],case["end"],case["scenario_deadline"])
          and ref["accepted_at"] <= case["phases"][3]["entered"], "original4s/phase/arm/DOM/freeze/seal/accept join")
    required = ("pending", "in_progress", "escalated", "completed", "failed", "cancelled", "superseded")
    def query(url: str) -> dict | None:
        parsed = urlsplit(url)
        check((parsed.scheme,parsed.netloc,parsed.path) == ("http",urlsplit(origin).netloc,
              "/api/v1/orgs/alpha/tasks/roots") and not parsed.fragment, "literal roots origin/path/no fragment")
        value = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        return value if value in [{"limit":["50"],"status":[status]} for status in required] else None
    members = {}
    for i,step in enumerate(steps):
        if step["event"] != "request-start":
            continue
        decoded = query(step["url"]) if step["url"] is not None else None
        eligible = (decoded is not None and step["main_frame"] is True and step["method"] == "GET"
                    and step["unredirected"] is True and i > commit
                    and step["commit"] == steps[commit]["sequence"]
                    and step["document_sequence"] == steps[nav]["sequence"])
        check(step["eligible"] is eligible, "independently reconstructed request eligibility")
        if eligible:
            seq = step["request_sequence"]
            check(type(seq) is int and seq == step["sequence"] and seq not in members and i < frozen
                  and image(decoded) == image(step["query"]), "unique original OPEN member/query, no late start")
            members[seq] = (i,step,decoded)
    check({item[2]["status"][0] for item in members.values()} == set(required), "all seven literal queries")
    frozen_members = steps[frozen]["members"]
    check(steps[frozen]["site"] == "18" and frozen_members == list(members)
          and steps[sealed]["members"] == frozen_members, "complete frozen/sealed member equality")
    bodies = group["bodies"]
    check(type(bodies) is list and len(bodies) == len(members)
          and {body["request_sequence"] for body in bodies} == set(members), "every original full body exactly once")
    member_events = {"selected", "body-begin", "body-settled", "body-end", "request-finished"}
    check(all(step["request_sequence"] in members for step in steps if step["event"] in member_events),
          "no unmatched response/body/finish or cross-group stitching")
    images = {}
    def stamp(value: object) -> None:
        check(type(value) is str and "T" in value, "UTC observation stamp shape")
        parsed = datetime.fromisoformat(value)
        check(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "UTC observation stamp")
    def fields(value: object, strings: str = "", nullable: str = "", integers: str = "", booleans: str = "") -> None:
        check(type(value) is dict, "complete object shape")
        for key in strings.split():
            check(key in value and type(value[key]) is str, "string "+key)
        for key in nullable.split():
            check(key in value and (value[key] is None or type(value[key]) is str), "nullable "+key)
        for key in integers.split():
            check(key in value and type(value[key]) is int, "integer "+key)
        for key in booleans.split():
            check(key in value and type(value[key]) is bool, "boolean "+key)
    for seq,(started,request,decoded) in members.items():
        joins = {}
        for event in member_events:
            found = [i for i,step in enumerate(steps) if step["event"] == event and step["request_sequence"] == seq]
            check(len(found) == 1, "one original "+event)
            joins[event] = found[0]
        response = steps[joins["selected"]]
        check(started < joins["selected"] < joins["body-begin"] < joins["body-settled"] < joins["body-end"] < guarded
              and joins["selected"] < joins["request-finished"] < guarded
              and response["status"] == 200 and type(response["status"]) is int
              and response["method"] == "GET" and response["main_frame"] is True and response["unredirected"] is True
              and response["request_url"] == request["url"] == response["response_url"]
              and response["document_sequence"] == steps[nav]["sequence"]
              and image(query(response["response_url"])) == image(decoded)
              and steps[joins["body-settled"]]["outcome"] == "success"
              and steps[joins["body-settled"]]["error"] is None, "own original response/body AND finish")
        raw_body = next(body for body in bodies if body["request_sequence"] == seq)
        check(type(raw_body["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}",raw_body["sha256"]) is not None,
              "diagnostic private raw-byte identity only")
        body = raw_body["body"]
        check("[REDACTED]" not in image(body) and type(body) is dict
              and type(body.get("tasks")) is list and "next_cursor" in body and body["next_cursor"] is None,
              "complete unredacted roots including unknown content")
        compared = copy.deepcopy(body)
        seen = set()
        status = decoded["status"][0]
        for index,task in enumerate(body["tasks"]):
            fields(task, "task_id status team task_type brief created_at updated_at severity_rollup",
                   "assigned_agent parent_task_id revisit_of_task_id dispatched_from_thread_id block_kind "
                   "blocked_on_job_ids active_chain active_fanout note final_output_dir completed_at cancelled_at "
                   "last_heartbeat current_session_id zombie_flagged_at", "orchestration_step_count revision_count")
            check(task["task_id"] not in seen and task["task_id"] != W and task["parent_task_id"] is None
                  and task["status"] == status and "session_timeout_seconds" in task
                  and (task["session_timeout_seconds"] is None or type(task["session_timeout_seconds"]) is int)
                  and type(task.get("direct_revisits")) is list, "unique null-parent requested-status full root")
            seen.add(task["task_id"])
            # ID18 is terminal: an undeclared delegated task cannot borrow a
            # stamp exclusion or a phase from another case/group.
            check("child_wait" in task and task["child_wait"] is None
                  and not (task["status"] == "in_progress" and task["block_kind"] == "delegated"), "terminal/undeclared child-wait refusal")
            hold = task["pause"]
            fields(hold, "org_slug root_task_id lifecycle_status control_state observed_at", "paused_at resumed_at",
                   "generation", "held effective_hold evidence_complete")
            stamp(hold["observed_at"])
            check(hold["org_slug"] == "alpha" and hold["root_task_id"] == task["task_id"]
                  and hold["lifecycle_status"] == task["status"] and type(hold.get("blockers")) is list, "root pause identity")
            for blocker in compared["tasks"][index]["pause"]["blockers"]:
                check(type(blocker) is dict and blocker.get("observed_at") == hold["observed_at"], "exact-slot blocker stamp")
                del blocker["observed_at"]
            del compared["tasks"][index]["pause"]["observed_at"]
            if task["task_id"] == P:
                want = dict(task_id=P, brief="E07_CANCEL_PARENT", team="engineering", status="cancelled",
                            assigned_agent="case_manager", parent_task_id=None, block_kind=None,
                            current_session_id=parent["session"], note="cancelled by founder: E07_CANCEL_TREE",
                            severity_rollup="cancelled")
                check(all(image(task[key]) == image(value) for key,value in want.items())
                      and hold["held"] is False and hold["effective_hold"] is False
                      and hold["control_state"] == "unheld" and hold["generation"] == 0
                      and hold["paused_at"] is None and hold["resumed_at"] is None,
                      "authored cancelled P bound to genuine allocation/session/DB/audit")
        check((P in seen) == (status == "cancelled"), "P exactly once only in cancelled partition; W never root")
        full_image = image(compared)
        check(status not in images or images[status] == full_image, "every same-query full type-sensitive image")
        images[status] = full_image
    for ready_at in positions("ready"):
        ready_members = steps[ready_at]["members"]
        check(ready_at < frozen and len(ready_members) == len(set(ready_members))
              and set(ready_members) <= set(members)
              and {members[seq][2]["status"][0] for seq in ready_members} == set(required)
              and all(any(i < ready_at and step["event"] == "body-end" and step["request_sequence"] == seq
                          for i,step in enumerate(steps)) for seq in ready_members), "OPEN all-query original-body readiness")
    check(positions("ready"), "readiness before DOM/checkpoint/freeze")
    check(steps[positions("ready")[0]]["at"] <= listing["at"], "all-query readiness before DOM snapshot")
    representatives = [dict(query={"limit":["50"],"status":[status]}, request_sequence=min(
        seq for seq,member in members.items() if member[2]["status"] == [status])) for status in required]
    check(image(steps[sealed]["representatives"]) == image(representatives)
          and steps[sealed]["body_tasks_settled"] is True and steps[sealed]["wakes_detached"] is True
          and steps[sealed]["disposals_complete"] is True and steps[cleanup]["aborted"] is False
          and steps[cleanup]["tasks"] == [], "whole frozen group settled/disposed without outstanding owner")
    selected = next(item["request_sequence"] for item in representatives if item["query"]["status"] == ["cancelled"])
    selected_body = next(body["body"] for body in bodies if body["request_sequence"] == selected)
    check(ref["selected_request_sequence"] == selected and type(ref["selected_request_sequence"]) is int
          and image(ref["query"]) == image({"status":["cancelled"],"limit":["50"]})
          and listing["roots_url"] == members[selected][1]["url"]
          and image(query(listing["roots_url"])) == image(ref["query"])
          and image(listing["roots"]) == image(selected_body)
          and image(dict(event="list-reload",**listing)) == image(events[-1])
          and image(case) == image(observations["e07"]) == image(row["e07"]),
          "earliest cancelled original, full selected body and identical public references/copies")

    # Optional diagnostics belong ONLY to complete cases. All original equality,
    # actual-source and behavioral gates above remain mandatory. This reader
    # neither imports the stat parser nor makes a claim that a syscall ran.
    def diag_number(value: object, *, zero: bool = False) -> bool:
        return type(value) is int and (0 if zero else 1) <= value < 10**20

    def diag_ticks(value: object) -> bool:
        return (type(value) is str and 1 <= len(value) <= 20 and value.isascii()
                and value.isdecimal() and int(value) > 0)

    def diag_time(value: object) -> bool:
        return type(value) in (int, float) and 0 <= value < float("inf")

    def read_observation(value: object, pid: int, lower: float, upper: float) -> None:
        check(type(value) is dict and set(value) == {
            "pid", "start", "state", "ppid", "reason", "entered", "finished"}, "diagnostic read keys")
        check(diag_number(value["pid"]) and value["pid"] == pid
              and diag_time(value["entered"]) and diag_time(value["finished"])
              and lower <= value["entered"] <= value["finished"] <= upper, "diagnostic read identity/interval")
        check(type(value["reason"]) is str and value["reason"] in {
            "observed", "absent", "unreadable", "interrupted", "empty", "partial", "oversized", "malformed"},
              "finite diagnostic read reason")
        if value["reason"] == "observed":
            check(diag_ticks(value["start"]) and diag_number(value["ppid"], zero=True)
                  and type(value["state"]) is str and len(value["state"]) == 1
                  and value["state"] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                  "same-buffer diagnostic scalars")
        else:
            check(all(value[k] is None for k in ("start", "state", "ppid")), "UNKNOWN/absence has no scalar fact")

    def association(value: object, pid: int, pin: str | None, pins: dict,
                    lower: float, upper: float) -> int:
        check(type(value) is dict and set(value) == {
            "entered", "finished", "expected_start", "target_a", "parent", "target_b", "parent_pin", "relation"},
              "diagnostic association keys")
        check(diag_time(value["entered"]) and diag_time(value["finished"])
              and lower <= value["entered"] <= value["finished"] <= upper
              and (value["expected_start"] is None if pin is None else
                   diag_ticks(value["expected_start"]) and value["expected_start"] == pin),
              "diagnostic original pin/interval")
        a, p, b = value["target_a"], value["parent"], value["target_b"]
        read_observation(a, pid, value["entered"], value["finished"])
        reads = 1
        if a["reason"] != "observed":
            expected = "target-absent" if a["reason"] == "absent" else "target-unknown"
        elif pin is not None and a["start"] != pin:
            expected = "target-reused"
        elif a["ppid"] == 0:
            expected = "no-parent"
        else:
            reads = 3
            read_observation(p, a["ppid"], a["finished"], value["finished"])
            read_observation(b, pid, p["finished"], value["finished"])
            parent_pin = pins.get(a["ppid"])
            check(value["parent_pin"] is None if parent_pin is None else
                  diag_ticks(value["parent_pin"]) and value["parent_pin"] == parent_pin,
                  "parent pin comes only from existing generation/context")
            if b["reason"] != "observed" or b["start"] != a["start"] or b["ppid"] != a["ppid"]:
                expected = "target-changed"
            elif p["reason"] != "observed":
                expected = "parent-missing" if p["reason"] == "absent" else "parent-unknown"
            elif parent_pin is not None and p["start"] != parent_pin:
                expected = "parent-reused"
            else:
                expected = "bracketed"
        if reads == 1:
            check(p is None and b is None and value["parent_pin"] is None, "unconfirmed target has no parent walk")
        check(type(value["relation"]) is str and value["relation"] == expected,
              "independent sequential relation, never closure/adoption/wait ownership")
        return reads

    def diagnostics(closed: dict, upper: float) -> None:
        processes = closed["processes"]
        present = "controller_diagnostic" in closed or any("diagnostic" in p for p in processes)
        if not present:
            return  # Historical absence is unobserved; no backfill or credit.
        check(type(processes) is list and len(processes) == len(tree["chain"])+1 <= 17
              and [(p["pid"], p["start"]) for p in processes]
              == [(p["pid"], p["start"]) for p in tree["chain"]+[child]],
              "all original targets/order, no diagnostic truncation")
        pins = {}
        for p in [*processes, transport["daemon"]]:
            check(diag_number(p["pid"]) and diag_ticks(p["start"])
                  and (p["pid"] not in pins or pins[p["pid"]] == p["start"]), "unambiguous original diagnostic pins")
            pins[p["pid"]] = p["start"]
        check(diag_time(closed["sampled"]) and case["entered"] <= closed["sampled"] <= upper,
              "original sample time before optional work")
        check("controller_diagnostic" in closed and all("diagnostic" in p for p in processes)
              and diag_number(phase["pid"]) and transport["daemon"]["ppid"] == phase["pid"],
              "complete optional records and controller invocation PID join")
        context = closed["controller_diagnostic"]
        reads = association(context, phase["pid"], None, pins, closed["sampled"], upper)
        # A surviving owner.add receipt can corroborate self generation. It
        # never supplies the missing independent launcher generation/role pin.
        identities = row["cleanup"].get("identities", {})
        check(type(identities) is dict, "optional controller owner identities")
        controller_pin = identities.get(str(phase["pid"]))
        if controller_pin is not None:
            check(diag_ticks(controller_pin), "retained controller generation pin")
            for observation in (context["target_a"], context["target_b"]):
                if observation is not None and observation["reason"] == "observed":
                    check(observation["start"] == controller_pin, "observed self matches retained owner pin")
        if context["relation"] == "bracketed":
            for observation in (context["target_a"], context["parent"]):
                pins.setdefault(observation["pid"], observation["start"])
        last = context["finished"]
        for p in processes:
            check(p["observed_start"] is None or diag_ticks(p["observed_start"]), "original observed start type")
            reads += association(p["diagnostic"], p["pid"], p["start"], pins, last, upper)
            last = p["diagnostic"]["finished"]
        check(reads <= 54, "per-closure finite diagnostic read cardinality")

    for index, closed in enumerate(case["closure_samples"]):
        upper = (case["closure_samples"][index+1]["sampled"]
                 if index+1 < len(case["closure_samples"]) else freeze["entered"])
        diagnostics(closed, min(upper, case["phases"][1]["end"], case["finished"]))
    for index, sample in enumerate(samples):
        upper = samples[index+1]["entered"] if index+1 < len(samples) else window["finished"]
        diagnostics(sample["closure"], min(upper, case["phases"][3]["end"], case["finished"]))


def _decode_diagnostic(path: Path) -> tuple[object, tuple]:
    """One exported-file decode; bound selected duplicate bookkeeping only."""
    vocabulary = ("diagnostic_read_observation", "source_sha", "failure_categories", "schema", "state", "outcome",
                  "controller", "launcher", "availability", "snapshot", "diagnostic_read", "first_observed",
                  "context_capture", "stage", "error", "at", "launcher_result")
    records = []  # Strong references: dictionary identities cannot be recycled.
    overflow = False
    root_mask = 0
    hidden_member = False

    def pairs(items: list[tuple]) -> dict:
        nonlocal overflow, root_mask, hidden_member
        result = {}
        mask = 0
        hidden = False
        for key, value in items:
            if key in result and key in vocabulary:
                mask |= 1 << vocabulary.index(key)
            if (key == "launcher_result" and type(value) is dict
                    and "diagnostic_read_observation" in value):
                hidden = True  # ANY candidate, before a duplicate parent overwrites it.
            result[key] = value  # Preserve unrelated legacy last-key semantics.
        if mask:
            if len(records) < 16:
                records.append((result, mask))
            else:
                overflow = True
        # For a dictionary root, its hook is last; independent of record overflow.
        root_mask, hidden_member = mask, hidden
        return result

    value = json.loads(path.read_text(encoding="utf-8", errors="strict"), object_pairs_hook=pairs)
    return value, (records, overflow, root_mask, hidden_member, vocabulary)


def _diagnostic_duplicates(value: dict, facts: tuple, keys: tuple[str, ...], *, root: bool = False) -> None:
    records, overflow, root_mask, _hidden, vocabulary = facts
    if overflow:
        raise ValueError("invalid diagnostic metadata")
    mask = root_mask if root else next((mask for item, mask in records if item is value), 0)
    if any(mask & (1 << vocabulary.index(key)) for key in keys):
        raise ValueError("invalid diagnostic metadata")


def _diagnostic_source(value: object, source: str) -> None:
    if (type(value) is not str or len(value) != 40 or any(c not in "0123456789abcdef" for c in value)
            or value != source):
        raise ValueError("invalid diagnostic metadata")


def _diagnostic_projection(result: dict, facts: tuple, source: str, *, root: bool) -> tuple | None:
    """Validate only the fixed-depth new member and its necessary relation."""
    if "diagnostic_read_observation" not in result:
        return None  # Legacy/unobserved, not unreached or diagnostic success.

    def closed(value: object, keys: tuple[str, ...]) -> dict:
        if type(value) is not dict or len(value) != len(keys) or any(key not in value for key in keys):
            raise ValueError("invalid diagnostic metadata")
        _diagnostic_duplicates(value, facts, keys)
        return value

    def schema_source(value: dict) -> None:
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise ValueError("invalid diagnostic metadata")
        _diagnostic_source(value["source_sha"], source)

    def slot(value: object, *, read: bool = False) -> tuple | None:
        if value is None:
            return None
        value = closed(value, ("stage", "error", "at"))
        stage, error, at = (value[key] for key in ("stage", "error", "at"))
        if (type(stage) is not str or len(stage) > 8
                or stage not in ("phase", "bounded", "restore", "context", "action", "teardown", "evidence", "browser", "other")
                or (read and stage != "teardown")
                or type(error) is not str or len(error) > 12
                or error not in ("unicode", "timeout", "interruption", "assertion", "value", "io", "runtime", "other")
                or type(at) not in (int, float) or not 0 <= at <= 9007199254740991):
            raise ValueError("invalid diagnostic metadata")
        # The range check also rejects NaN/infinity without coercing large ints.
        return stage, error, type(at), at

    _diagnostic_duplicates(result, facts, ("diagnostic_read_observation", "source_sha", "failure_categories"), root=root)
    _diagnostic_source(result["source_sha"], source)
    observation = closed(result["diagnostic_read_observation"], ("schema", "source_sha", "state", "outcome"))
    schema_source(observation)
    state, outcome = observation["state"], observation["outcome"]
    if (type(state) is not str or len(state) > 9 or state not in ("unknown", "admitted", "unreached", "completed")
            or (outcome is not None and (type(outcome) is not str or len(outcome) > 18))):
        raise ValueError("invalid diagnostic metadata")
    if state == "completed":
        if outcome not in ("root_unset", "root_missing", "coord_missing", "final_missing", "refused", "available"):
            raise ValueError("invalid diagnostic metadata")
    elif outcome is not None:
        raise ValueError("invalid diagnostic metadata")
    # Serialize only the already bounded four validated values (never arbitrary input).
    if len(json.dumps(observation, separators=(",", ":")).encode("utf-8")) > 256:
        raise ValueError("invalid diagnostic metadata")

    categories = closed(result["failure_categories"], ("schema", "source_sha", "controller", "launcher"))
    schema_source(categories)
    controller = closed(categories["controller"], ("availability", "snapshot"))
    availability = controller["availability"]
    if (type(availability) is not str or len(availability) > 11
            or availability not in ("unavailable", "available")):
        raise ValueError("invalid diagnostic metadata")
    snapshot = controller["snapshot"]
    selected_snapshot = None
    if availability == "unavailable":
        if snapshot is not None:
            raise ValueError("invalid diagnostic metadata")
    else:
        snapshot = closed(snapshot, ("schema", "source_sha", "first_observed", "context_capture"))
        schema_source(snapshot)
        selected_snapshot = (slot(snapshot["first_observed"]), slot(snapshot["context_capture"]))
    launcher = categories["launcher"]
    if type(launcher) is not dict or "diagnostic_read" not in launcher:
        raise ValueError("invalid diagnostic metadata")
    _diagnostic_duplicates(launcher, facts, ("diagnostic_read",))
    read_slot = slot(launcher["diagnostic_read"], read=True)
    if ((state == "unreached" and (availability != "unavailable" or read_slot is not None))
            or (state == "completed" and (availability == "available") != (outcome == "available"))):
        raise ValueError("invalid diagnostic metadata")
    # Sources/schemas are independently exact above; numbers retain exact types.
    return state, outcome, availability, selected_snapshot, read_slot


def _diagnostic_cancellation(raw: object, raw_facts: tuple, row: object, row_facts: tuple, source: str) -> None:
    if type(raw) is not dict or type(row) is not dict or type(row["launcher_result"]) is not dict:
        raise ValueError("invalid diagnostic metadata")
    embedded = row["launcher_result"]
    for result in (raw, row, embedded):
        _diagnostic_source(result["source_sha"], source)
    present = "diagnostic_read_observation" in raw or "diagnostic_read_observation" in embedded
    hidden = row_facts[3]
    if present or hidden:
        _diagnostic_duplicates(raw, raw_facts, ("diagnostic_read_observation", "source_sha", "failure_categories"), root=True)
        _diagnostic_duplicates(row, row_facts, ("launcher_result", "source_sha"), root=True)
        if hidden and "diagnostic_read_observation" not in embedded:
            raise ValueError("invalid diagnostic metadata")
        if ("diagnostic_read_observation" in raw) != ("diagnostic_read_observation" in embedded):
            raise ValueError("invalid diagnostic metadata")
        if (_diagnostic_projection(raw, raw_facts, source, root=True)
                != _diagnostic_projection(embedded, row_facts, source, root=False)):
            raise ValueError("invalid diagnostic metadata")


def scoped_completed_join(accepted: dict, result: dict, variants: list[str]) -> None:
    """Consistency of a named scope, AFTER its complete independent content read.

    The existing whole56 completed.join_results remains the overall gate.
    """
    if completed.binding(accepted['identity']) != completed.binding(result) or result.get('completed_cases') != accepted:
        raise ValueError('scoped completed source/run/lane conflict')
    rows = [r for r in accepted['cases'] if r['variant'] in variants]
    if [r['variant'] for r in rows] != variants:
        raise ValueError('scoped completed cases unavailable')
    for observed in rows:
        actual = result['variants'].get(observed['variant'])
        if (actual is None or actual['status'] != 'PASS' or type(actual['at']) is not type(observed['at'])
                or actual['at'] != observed['at']):
            raise ValueError('scoped completed original result conflict')


def validate_groups(result: dict, observations: dict, selected: str | None = None) -> None:
    groups = result['groups']
    if selected is None and (groups != observations['groups'] or set(groups) != {'E05','E07'}):
        raise ValueError('group export equality/identity mismatch')
    for name,start,end in (('E05',48,53),('E07',53,56)):
        if selected is not None and name != selected:
            continue
        group = groups[name]
        if group != observations['groups'][name]:
            raise ValueError('selected group export equality mismatch')
        if (set(group) != {'state','begin','end','first_error_ref','variants','checkpoint_ref','admission_ref'}
                or group['state'] not in {'not_started','running','reported_pass','failed','refused','unknown'}
                or group['variants'] != MANIFEST['variants'][start:end]):
            raise ValueError('closed group state/variant grammar')
        for key in ('begin','end'):
            value = group[key]
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise ValueError('group original time unavailable')
        if group['end'] is not None and (group['begin'] is None or group['end'] < group['begin']):
            raise ValueError('group original interval conflict')
        ref = group['first_error_ref']
        if ref is not None and (type(ref) is not int or not 0 <= ref < len(observations['failures'])):
            raise ValueError('group original error reference unavailable')
        if group['state'] in {'failed','refused','unknown'} and ref is None:
            raise ValueError('group first original error unavailable')
        if group['state'] == 'reported_pass' and (ref is not None or group['begin'] is None or group['end'] is None):
            raise ValueError('group pass conflicts with original error/interval')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--event", choices=["schedule", "workflow_dispatch"])
    parser.add_argument("--matrix-result")
    parser.add_argument("--cancellation-result")
    args = parser.parse_args()
    if args.controls:
        print(json.dumps(controls(), indent=2))
        return 0
    if not all((args.artifacts, args.source, args.event, args.matrix_result, args.cancellation_result)):
        parser.error("artifacts, source, event, matrix-result and cancellation-result are required")
    # Keep literal ancestors for completed.directory's no-follow validation.
    args.artifacts = Path.cwd() / args.artifacts
    expected = MANIFEST["daily_python"]
    names = ["e2e-" + version for version in expected] + ["e2e-cancel-TERM", "e2e-cancel-INT"]
    health = dict(status="FAIL", event=None, source_sha=None,
                  requested_event=args.event, requested_source_sha=args.source,
                  cells=expected, artifacts=names, errors=[], completed_cases={}, groups={}, recorded_failures={})
    try:
        admission = daily_admission(Path(__file__).resolve().parents[1])
        health.update(admission)
        if admission["source_sha"] != args.source or admission["event"] != args.event:
            raise ValueError("requested aggregate event/source differs from admitted Actions identity")
        rows = []
        content_accepted = set()
        required = ["result.json", *BASELINE_ARTIFACTS]
        phase_logs = [name + ".txt" for name in ("dependencies", "browser-dependencies", "browser-install",
                                                "web-dependencies", "web-build", "scenarios")]
        # Missing/empty sanitized artifacts cannot be represented as healthy.
        for version in expected:
            directory = args.artifacts / ("e2e-" + version)
            accepted = None
            health['completed_cases'][version] = [dict(variant=v,status='UNKNOWN') for v in MANIFEST['variants']]
            health['groups'][version] = {n:dict(producer=None,reader='UNKNOWN',error=None) for n in ('E04','E05','E07')}
            try:
                accepted, _ = completed.read_bundle(directory, dict(admission, python=version, exercise="baseline"))
                metadata = {r['variant']:r for r in accepted['cases']}
                health['completed_cases'][version] = [dict(metadata.get(v,dict(variant=v)),status='UNKNOWN') for v in MANIFEST['variants']]
            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                health["errors"].append(f"e2e-{version}: completed case evidence unavailable")
            required_evidence_accepted = True
            for name in required + phase_logs:
                path = directory / name
                if not path.is_file() or (name.endswith((".json", ".png")) and path.stat().st_size == 0):
                    required_evidence_accepted = False
                    health["errors"].append(f"missing/empty artifact: e2e-{version}/{name}")
            if (directory / "result.json").is_file():
                result = None
                diagnostic_accepted = False
                try:
                    result, diagnostic_facts = _decode_diagnostic(directory / "result.json")
                    if type(result) is not dict:
                        result = None
                        raise ValueError("invalid diagnostic metadata")
                except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                    health["errors"].append(f"e2e-{version}: invalid diagnostic metadata")
                else:
                    try:
                        _diagnostic_projection(result, diagnostic_facts, args.source, root=True)
                        diagnostic_accepted = True
                    except (ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError):
                        health["errors"].append(f"e2e-{version}: invalid diagnostic metadata")
                diagnostic_facts = None  # Release this decode's selected duplicate records.
                if result is not None:
                    rows.append(result)
                    if result.get("python") != version or result.get("exercise") != "baseline":
                        health["errors"].append(f"e2e-{version}: directory/interpreter/exercise mismatch")
                    complete_join = False
                    try:
                        if accepted is None:
                            raise ValueError("completed case evidence unavailable")
                        completed.join_results(accepted, result)
                        complete_join = True
                    except (ValueError, KeyError, TypeError, IndexError):
                        health["errors"].append(f"e2e-{version}: complete case/result join unavailable")
                    # Each content reader executes independently. Full56, global
                    # artifact/diagnostic/cleanup gates cannot short-circuit a
                    # separately complete group's own content/source/case joins.
                    try:
                        observations = json.loads((directory/'observations.json').read_text())
                        validate_admission(result, admission)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        health['errors'].append(f'e2e-{version}: scoped source/group evidence unavailable: {exc}')
                        observations = None
                    if observations is not None:
                        health['recorded_failures'][version] = observations['failures']
                        if observations['failures']:
                            health['errors'].append(f'e2e-{version}: original controller failures retained; global Health fails')
                        try:
                            validate_groups(result, observations)
                        except (ValueError,KeyError,TypeError) as exc:
                            health['errors'].append(f'e2e-{version}: group metadata: {exc}')
                        thread_cases = [d['threads_cases'] for d in observations['durable'] if 'threads_cases' in d]
                        if len(thread_cases) == 1:
                            for item in health['completed_cases'][version]:
                                failure = thread_cases[0].get('case_outcomes',{}).get(item['variant'])
                                if failure and failure.get('status') == 'FAIL':
                                    ref = failure.get('error_ref')
                                    if type(ref) is int and 0 <= ref < len(observations['failures']):
                                        item.update(status='FAIL',reported_error=observations['failures'][ref])
                    scoped = set()
                    readers = (
                        ('E04',MANIFEST['variants'][:48],lambda:validate_e04(result,observations,json.loads((directory/'browser-events.json').read_text()),directory)),
                        ('E05',MANIFEST['variants'][48:53],lambda:validate_e05(result,observations,directory)),
                        ('E07',MANIFEST['variants'][53:],lambda:validate_e07(result,observations,directory)),
                    )
                    for group,named,reader in readers:
                        outcome = health['groups'][version][group]
                        outcome['producer'] = result.get('groups',{}).get(group)
                        try:
                            if observations is None or accepted is None:
                                raise ValueError('required scoped source/completion evidence unavailable')
                            if group != 'E04':
                                validate_groups(result,observations,group)
                            reader()
                            scoped_completed_join(accepted,result,named)
                        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                            outcome.update(reader='UNKNOWN' if isinstance(exc,(OSError,KeyError)) or 'UNKNOWN' in str(exc) or 'unavailable' in str(exc) else 'FAIL',error=str(exc))
                            health['errors'].append(f'e2e-{version}: {group}: {exc}')
                        else:
                            outcome['reader'] = 'scoped acceptance'
                            scoped.add(group)
                            for item in health['completed_cases'][version]:
                                if item['variant'] in named:
                                    item['status'] = 'PASS'
                    if scoped == {'E04','E05','E07'} and complete_join and required_evidence_accepted and diagnostic_accepted:
                        content_accepted.add(version)
        unexpected = set(path.name for path in args.artifacts.glob("e2e-*") if path.is_dir()) - set(names)
        if unexpected:
            health["errors"].append("unexpected artifact directories: " + ", ".join(sorted(unexpected)))
        try:
            validate(rows, expected, args.source, args.matrix_result)
            for row in rows:
                validate_admission(row, admission)
            for version in content_accepted:
                for case in health["completed_cases"][version]:
                    case["status"] = "PASS"  # Full original content gates above, never a marker alone.
        except (ValueError, KeyError, TypeError) as exc:
            health["errors"].append(str(exc))
        if args.cancellation_result != "success":
            health["errors"].append("cancellation jobs did not succeed: " + args.cancellation_result)
        for label, version, signum in (("TERM", "3.12", int(signal.SIGTERM)), ("INT", "3.14", int(signal.SIGINT))):
            directory = args.artifacts / ("e2e-cancel-" + label)
            for name in ["cancellation.json", "launcher.log", "launcher/active.json", "launcher/result.json"] + ["launcher/" + name for name in phase_logs]:
                path = directory / name
                if not path.is_file() or (name in {"cancellation.json", "launcher/active.json", "launcher/result.json"} and path.stat().st_size == 0):
                    health["errors"].append(f"missing/empty artifact: e2e-cancel-{label}/{name}")
            raw = row = None
            metadata_invalid = False
            try:
                raw, raw_facts = _decode_diagnostic(directory / "launcher/result.json")
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                metadata_invalid = True
            try:
                row, row_facts = _decode_diagnostic(directory / "cancellation.json")
                if type(row) is not dict:
                    row = None
                    raise ValueError("invalid diagnostic metadata")
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                metadata_invalid = True
            # Independent reads and validation catches: metadata never skips a
            # readable row's original status/admission/signal/timing/closure group.
            if not metadata_invalid:
                try:
                    _diagnostic_cancellation(raw, raw_facts, row, row_facts, args.source)
                except (ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError):
                    metadata_invalid = True
            if metadata_invalid:
                health["errors"].append(f"{label}: invalid diagnostic metadata")
            raw_facts = row_facts = None  # No parser bookkeeping crosses label validation.
            if row is not None:
                try:
                    if row["status"] != "PASS":
                        raise ValueError("failed cancellation receipt")
                    validate_admission(row, admission)
                    validate_admission(row["launcher_result"], admission)
                    validate_cancellation(row, args.source, version, signum)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    health["errors"].append(f"{label}: {exc}")
        if not health["errors"]:
            health["status"] = "PASS"
    except Exception as exc:
        health["errors"].append(str(exc))
    print(json.dumps(health, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        event_label = health["event"] or "UNVERIFIED"
        event_detail = {"schedule": " (19:00 UTC)", "workflow_dispatch": " (manual main dispatch)"}.get(event_label, "")
        lines = ["## Product E2E health: " + health["status"],
                 "", "Default-branch source: `" + str(health["source_sha"] or "UNVERIFIED") + "`",
                 "Event: `" + event_label + "`" + event_detail,
                 "Run: `" + str(health.get("run_id", "UNVERIFIED")) + "`, attempt: `" + str(health.get("run_attempt", "UNVERIFIED")) + "`",
                 "Source tree: `" + str(health.get("source_tree", "UNVERIFIED")) + "`",
                 "", "Expected artifacts (7-day retention): " + ", ".join("`" + name + "`" for name in names),
                 "", "Baseline jobs: " + args.matrix_result + "; launcher diagnostics: " + args.cancellation_result,
                 "", "Engineering_manager owns triage through existing tasks/threads. This health result does not block merging."]
        lines.extend("\n- " + error.replace("\n", " ") for error in health["errors"])
        with Path(summary).open("a") as stream:
            stream.write("\n".join(lines) + "\n")
    return 0 if health["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
