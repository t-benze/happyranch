"""Pure family discrimination over a complete same-snapshot sequential inventory.

This recognizes saved callback syntax, never current authority or admission.
The caller alone supplies authenticated context and its original native budget.
No database acquisition, producer, cache or replacement reader belongs here.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, get_args, get_origin

from pydantic import BaseModel

from runtime.workflows._read_exhaustion import check as _budget_check
from runtime.workflows._read_exhaustion import exhausted

from runtime.workflows.sequential_contracts import StartCommand

if TYPE_CHECKING:
    from runtime.workflows.coding_authority_history import _Budget


# Includes complete and truncated quoted spans, matching the old scanner's
# quote/backslash state even on invalid JSON. json.loads validates full syntax.
_LEXICAL = re.compile(r'"(?:[^"\\]++|\\[\s\S])*+(?:"|\\?\Z)|[{}\[\]]')


class _Unrecognized(ValueError):
    pass


def _require(value: bool) -> None:
    if not value:
        raise _Unrecognized('not a closed sequential callback')


def _step(budget: _Budget) -> None:
    # Decline BEFORE performing another operation if its original allowance
    # cannot admit it. The caller retains and raises its original first error.
    limit = budget.traversal_limit
    _budget_check(budget.traversed < limit, _require, budget, 'work', 1, limit - budget.traversed)
    budget.step()


def _reserve(budget: _Budget, size: int) -> None:
    _require(size >= 0)
    _budget_check(budget.remaining_bytes >= size, _require, budget, 'bytes', size, budget.remaining_bytes)
    _step(budget)
    budget.consumed += size


def _same(left: object, right: object, budget: _Budget) -> bool:
    _step(budget)
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[k], right[k], budget) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b, budget) for a, b in zip(left, right, strict=True))
    return left == right


def _json(raw: object, budget: _Budget) -> dict:
    _require(type(raw) in (str, bytes) and 0 < len(raw) <= 1048576)
    # Reserve UTF8, decoded objects, model and serialization intermediates BEFORE
    # constructing them. Failed parses and comparisons retain all their charges.
    _reserve(budget, 20 * len(raw) + 1024)
    try:
        text = raw.decode('utf8') if type(raw) is bytes else raw
        _require(len(text.encode('utf8')) <= 1048576)
    except UnicodeError as exc:
        raise _Unrecognized() from exc
    depth, token_count = 0, 0
    tokens = _LEXICAL.finditer(text)
    while True:
        # Prepay token/end/matcher bookkeeping before constructing a match.
        _reserve(budget, 256)
        token = next(tokens, None)
        if token is None:
            break
        token_count += 1
        char = token.group(0)[0]
        if char in '[{':
            _reserve(budget, 4096)
            depth += 1
            _require(depth <= 24)
        elif char in ']}':
            depth -= 1
            _require(depth >= 0)

    # Separate closed-model/error/intermediate workspace per lexical token.
    _reserve(budget, 512 * (token_count + 1))

    def pairs(items: list[tuple[str, object]]) -> dict:
        _step(budget)
        result = {}
        for key, value in items:
            _step(budget)
            _require(key not in result)
            result[key] = value
        return result

    def integer(value: str) -> int:
        _require(len(value.lstrip('-')) <= 19)
        return int(value)

    def floating(value: str) -> float:
        # The native v1 assessment carrier has finite confidence. StartCommand
        # has no float fields and still rejects them through its strict model.
        _require(len(value) <= 64)
        result = float(value)
        _require(math.isfinite(result))
        return result

    def constant(value: str) -> object:
        raise _Unrecognized()

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                           parse_float=floating, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise _Unrecognized() from exc
    _require(type(value) is dict)
    return value


def _carrier_shape(value: object, annotation: object, budget: _Budget) -> bool:
    # Charge each visited schema/value node BEFORE inspection. Native carrier
    # schemas are closed, scalar-only except for literal lists and two v2
    # assessment objects. No user container is recursed into without its
    # declared annotation; malformed primitive lists never reach Pydantic.
    _reserve(budget, 512)
    origin = get_origin(annotation)
    if origin is Literal:
        return any(type(value) is type(item) and value == item for item in get_args(annotation))
    if origin is list:
        if type(value) is not list:
            return False
        member, = get_args(annotation)
        return all(_carrier_shape(item, member, budget) for item in value)
    if annotation is str:
        return type(value) is str
    if annotation is int:
        return type(value) is int
    if annotation is float:
        # Native strict floats accept integers; unchanged round-trip equality
        # still decides whether that normalization is an exact saved shape.
        return type(value) in (int, float)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _reserve(budget, 4096)
        fields = annotation.model_fields
        # Optional defaults must be present too: model_dump would otherwise
        # add a member and the existing exact round trip would refuse it.
        if type(value) is not dict or value.keys() != fields.keys():
            return False
        return all(_carrier_shape(value[name], field.annotation, budget)
                   for name, field in fields.items())
    return False


def _carrier(value: object, budget: _Budget) -> None:
    _require(type(value) is dict)
    if set(value) == {'_error_code'}:
        _require(value['_error_code'] in ('missing_assessment', 'null_assessment'))
    elif set(value) == {'_error_code', 'payload_digest'}:
        digest = value['payload_digest']
        _require(value['_error_code'] in ('malformed_output', 'binding_mismatch')
                 and type(digest) is str and len(digest) == 64
                 and all(c in '0123456789abcdef' for c in digest))
    else:
        # Existing native shapes only; no NextStep or producer is imported.
        from runtime.models import AuthorityPolicyV2ManagerSelfEvaluation, ManagerSelfEvaluation
        for model in (ManagerSelfEvaluation, AuthorityPolicyV2ManagerSelfEvaluation):
            if not _carrier_shape(value, model, budget):
                continue
            try:
                normalized = model.model_validate(value).model_dump(mode='json')
            except ValueError:
                continue
            if _same(value, normalized, budget):
                break
        else:
            raise _Unrecognized()


def _residual(value: object, budget: _Budget, reserved: Callable[[str, object], bool],
              path: tuple[str, ...] = ()) -> bool:
    _step(budget)
    if type(value) is dict:
        clean = True
        for key, item in value.items():
            _step(budget)
            selected = path + (key,)
            exempt = (selected == ('action',) and item == 'workflow_start') or (
                selected == ('workflow', 'definition', 'identity') and item == 'release/happyranch/coding')
            # Scan every remaining member, including the native carrier. Do not
            # stop traversal when an earlier member already supplied a marker.
            marker = reserved(key, item) if not exempt else False
            nested = _residual(item, budget, reserved, selected)
            clean = clean and not marker and nested
        return clean
    if type(value) is list:
        clean = True
        for item in value:
            nested = _residual(item, budget, reserved, path + ('[]',))
            clean = clean and nested
        return clean
    return True


def _original(command: dict, raw: object, source_size: int, budget: _Budget) -> bool:
    # Existing complete sequential validation requires canonical original bytes.
    # Exact canonical equality avoids a second parser/model tree. Noncanonical
    # contexts still take the full strict parse, with every cost retained.
    _reserve(budget, 12 * source_size + 4096)
    canonical = json.dumps(command, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False).encode('utf8')
    _step(budget)
    if type(raw) is bytes and raw == canonical:
        return True
    return _same(command, _json(raw, budget), budget)


def recognized_start(raw: object, *, location: str, result_id: int | None,
                     sequential: dict | None, budget: _Budget | None,
                     reserved: Callable[[str, object], bool]) -> bool:
    """Classify only a fully matched callback with a clean residual inventory.

    Call only after retaining the original Coding refusal. Every reservation is
    prepaid on its original object. Insufficient room declines BEFORE new work;
    the caller re-raises that original error, never returns a clean record.
    """
    if (location != 'task_results.decision_json' or not sequential or budget is None
            or type(result_id) is not int or result_id <= 0):
        return False
    original_error = sys.exception()
    try:
        value = _json(raw, budget)
        if 'workflows' in value:
            return _recognized_plural(value, raw, result_id, sequential, budget, reserved)
        _require(set(value) in ({'action', 'workflow'}, {'action', 'workflow', '_manager_self_evaluation'})
                 and value['action'] == 'workflow_start')
        if '_manager_self_evaluation' in value:
            _carrier(value['_manager_self_evaluation'], budget)
        try:
            command = StartCommand.model_validate(value['workflow']).model_dump(mode='json')
        except (ValueError, TypeError, UnicodeError) as exc:
            raise _Unrecognized() from exc
        _require(_same(value['workflow'], command, budget))
        pin = command['definition']
        matches = []
        for row in sequential.get('workflow_seq_definitions', ()):
            _step(budget)
            _reserve(budget, 4096)
            fields = dict(identity=row['identity'], version=row['version'],
                definition_digest=row['definition_digest'], source_digest=row['source_digest'],
                compiler=row['compiler_pin'], validator=row['validator_pin'],
                interpreter=row['interpreter_pin'], capabilities=row['capability_pin'])
            if _same(pin, fields, budget):
                matches.append(row)
        _require(len(matches) == 1)
        settings = []
        for row in sequential.get('workflow_seq_settings', ()):
            _step(budget)
            if (row['definition_id'] == matches[0]['id'] and row['definition_identity'] == pin['identity']
                    and type(row['generation']) is int and row['generation'] == command['settings']['generation']
                    and row['snapshot_digest'] == command['settings']['snapshot_digest']):
                snapshot = _json(row['snapshot_bytes'], budget)
                _require(_same(pin, snapshot['definition'], budget)
                         and _same(command['bindings'], snapshot['bindings'], budget))
                settings.append(row)
        _require(len(settings) == 1)
        referenced = []
        for row in sequential.get('workflow_seq_runs', ()):
            _step(budget)
            if type(row['source_result_id']) is int and row['source_result_id'] == result_id:
                referenced.append(row)
                _require(row['definition_id'] == matches[0]['id'] and row['settings_id'] == settings[0]['id']
                         and _original(command, row['start_bytes'], len(raw), budget))
        _require(len(referenced) <= 1)
        clean = _residual(value, budget, reserved)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        # Both callers rethrow their retained first error after False. Keep the
        # actual failed reservation reachable through that original exception.
        if original_error is not None and exhausted(exc):
            original_error.__cause__ = exc
        return False
    return clean


def _recognized_plural(value: dict, raw: object, result_id: int, sequential: dict,
                       budget: _Budget, reserved: Callable[[str, object], bool]) -> bool:
    """Closed @2 family branch on the caller's original finite budget.

    No Coding parser or special bundle branch is used. Only the exact outer
    action and already-validated definition-pin identity paths are exempt;
    every other residual scalar is scanned on the original budget.
    """
    from runtime.workflows.sequential_assignments import validate_assignment_snapshot
    from runtime.workflows.sequential_definition import compile_generic_definition, parse_generic_start, validate_generic_start
    from runtime.workflows.sequential_history import GenericStorageSettings
    _require(set(value) in ({'action', 'workflows'}, {'action', 'workflows', 'consume_notifications'},
        {'action', 'workflows', '_manager_self_evaluation'},
        {'action', 'workflows', 'consume_notifications', '_manager_self_evaluation'})
        and value['action'] == 'workflow_start' and type(value['workflows']) is list
        and 1 <= len(value['workflows']) <= 8)
    if '_manager_self_evaluation' in value:
        _carrier(value['_manager_self_evaluation'], budget)
    ids = value.get('consume_notifications', [])
    _require(type(ids) is list and len(ids) <= 32 and all(type(key) is str and key and '\x00' not in key for key in ids)
        and len(set(ids)) == len(ids))
    commands, keys = [], set()
    for item in value['workflows']:
        _reserve(budget, 12 * len(raw) + 4096)
        command_raw = json.dumps(item, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')
        command = parse_generic_start(command_raw)
        _require(command.operation_key not in keys)
        keys.add(command.operation_key)
        pin = item['definition']
        definitions = []
        for row in sequential.get('workflow_seq_definitions', ()):
            _step(budget)
            _reserve(budget, 4096)
            fields = dict(identity=row['identity'], version=row['version'], definition_digest=row['definition_digest'],
                source_digest=row['source_digest'], compiler=row['compiler_pin'], validator=row['validator_pin'],
                interpreter=row['interpreter_pin'], capabilities=row['capability_pin'])
            if _same(pin, fields, budget):
                definitions.append(row)
        _require(len(definitions) == 1)
        definition = definitions[0]
        _reserve(budget, 48 * len(definition['definition_bytes']) + 512)
        compiled = compile_generic_definition(definition['definition_bytes'], source_digest=definition['source_digest'])
        _require(command.definition == compiled.pin)
        settings = []
        for row in sequential.get('workflow_seq_settings', ()):
            _step(budget)
            if (row['definition_id'] == definition['id'] and row['definition_identity'] == pin['identity']
                    and type(row['generation']) is int and row['generation'] == command.settings.generation
                    and row['snapshot_digest'] == command.settings.snapshot_digest):
                saved = _json(row['snapshot_bytes'], budget)
                snapshot = GenericStorageSettings.model_validate(saved)
                _require(_same(pin, saved['definition'], budget) and snapshot.admission == 'Allowed')
                validate_generic_start(command, compiled, original_limits=snapshot.limits)
                settings.append((row, snapshot))
        _require(len(settings) == 1)
        commands.append((item, command, definition, compiled, settings[0]))
    referenced = []
    for row in sequential.get('workflow_seq_runs', ()):
        _step(budget)
        if type(row['source_result_id']) is int and row['source_result_id'] == result_id:
            referenced.append(row)
    _require(len(referenced) in (0, len(commands)))
    matched = set()
    for row in referenced:
        _reserve(budget, 4096)
        candidates = [item for item in commands if item[1].operation_key == row['start_operation_key']]
        _require(len(candidates) == 1 and row['engine_version'] == 2 and row['start_operation_key'] not in matched)
        matched.add(row['start_operation_key'])
        item, command, definition, compiled, (setting, snapshot) = candidates[0]
        _require(row['definition_id'] == definition['id'] and row['settings_id'] == setting['id']
            and _original(item, row['start_bytes'], len(raw), budget))
        resolved = _json(row['assignment_snapshot_bytes'], budget)
        validate_assignment_snapshot(resolved, compiled, command, defaults=snapshot.bindings, org_slug=row['org_slug'])
        members = []
        for member in sequential.get('workflow_seq_source_memberships', ()):
            _step(budget)
            if member['run_id'] == row['id']:
                members.append(member)
        _require(len(members) == 1 and (members[0]['source_task_id'], members[0]['source_result_id'],
            members[0]['start_operation_key']) == (row['source_task_id'], result_id, command.operation_key))
    def residual(item: object, path: tuple[str, ...] = ()) -> bool:
        _step(budget)
        if type(item) is dict:
            clean = True
            for key, child in item.items():
                _step(budget)
                exempt = (path == () and key == 'action' and child == 'workflow_start') or (
                    path == ('workflows', '[]', 'definition') and key == 'identity')
                marker = False if exempt else reserved(key, child)
                nested = residual(child, path + (key,))
                clean = clean and not marker and nested
            return clean
        if type(item) is list:
            clean = True
            for child in item:
                nested = residual(child, path + ('[]',))
                clean = clean and nested
            return clean
        return True
    return residual(value)
