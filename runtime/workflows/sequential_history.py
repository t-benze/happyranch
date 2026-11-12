"""Standalone U2a complete sequential-history and combined-structure reader.

Selected startup readers compose this complete reader. The result is storage
integrity, not current authorization, native settlement or engine availability.
Retained family data readers compose through an explicit scope; U2b installation
and U3 adapter authentication remain separate.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Literal, TypeVar, TYPE_CHECKING

from pydantic import BeforeValidator, Field

from runtime.workflows._read_exhaustion import ReadExhaustion, check as _budget_check

from runtime.infrastructure.workflow_schema import (
    _candidate_structure, _canonical_connection, _execute_ddl, _read_workflow_schema,
    _sequential_legacy_read,
    _schema_snapshot, _structure_cells,
)
from runtime.infrastructure.workflow_sequential_ownership import (
    SEQUENTIAL_OWNERSHIP_DDL, sequential_binding_view_ddl,
    validate_sequential_ownership, validate_sequential_ownership_guards,
)
from runtime.infrastructure.workflow_sequential_schema import (
    CANONICAL_WORKFLOW_SEQUENTIAL_DDL, SEQUENTIAL_TABLES,
)
from runtime.infrastructure.workflow_task_ownership import (
    OWNERSHIP_DDL, binding_view_ddl,
)
from runtime.workflows.coding_authority_history import _Budget, _CALL_ROW_LIMIT, _bounded_rows, _decoded
from runtime.workflows.sequential_contracts import (
    DEFINITION_LIMIT, RESULT_LIMIT, BindingClaim, Closed, Counters, DefinitionPin,
    Limits, NextEdge, OutputRef, ResultEnvelope, StartCommand, TerminalEdge, _array,
)
from runtime.workflows.sequential_definition import (
    CompiledDefinition, _json, _record as _closed_record, compile_definition, parse_result, parse_start,
    parse_transition, validate_start,
)
from runtime.workflows.sequential_reducer import (
    ActiveVisit, AuthenticatedResult, Decision, RunState, reduce_result,
)

if TYPE_CHECKING:
    from runtime.infrastructure.workflow_coding_schema import _CodingInventory


class StorageSettings(Closed):
    """Internal snapshot encoding, not the future public settings command."""

    format: Literal['workflow-sequential-settings-storage@1']
    definition: DefinitionPin
    bindings: Annotated[tuple[BindingClaim, ...], BeforeValidator(_array), Field(min_length=1, max_length=16)]
    limits: Limits
    admission: Literal['Disabled', 'Allowed']


@dataclass(frozen=True)
class SequentialLayout:
    document: str | None
    coding: bool
    owner_indexed: bool
    ownership: int
    sequential: bool


@dataclass(frozen=True)
class SequentialHistory:
    layout: SequentialLayout
    status: Literal['new-engine unavailable', 'storage readable']
    definition_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    retained: tuple[str, _CodingInventory | None] | None = None


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('workflow_seq_' + code)


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf8')


def _hashed(raw: object, digest: object) -> bytes:
    _require(type(raw) is bytes and 0 < len(raw) <= 1048576, 'bytes_invalid')
    _require(type(digest) is str and hashlib.sha256(raw).hexdigest() == digest, 'digest_invalid')
    return raw


def _record(raw: object, digest: object, keys: set[str], budget: _ReadBudget) -> dict:
    value = _decode(_hashed(raw, digest), budget)
    _require(value.keys() == keys, 'record_fields_invalid')
    return value


@dataclass
class _ReadBudget(_Budget):
    """Extend the retained finite reader's ONE row/byte/traversal allowance.

    Copy reservations cover decoded/model containers before parsing. Deadline
    and SQLite progress are cooperative, not hard RSS/OS-I/O guarantees. The
    caller must own the connection and its progress hook throughout this call.
    """

    deadline: float = field(default_factory=lambda: time.monotonic() + 5.0)
    sql_steps: int = 0
    sql_limit: int = 2000000
    _sql_exhaustion: ClassVar[ReadExhaustion | None] = None

    @property
    def remaining_bytes(self) -> int:
        # Complete candidate AND independent reference metadata/copy work share
        # this finite ceiling; retained row/value caps still apply.
        return 64 * 1048576 - self.consumed

    def check(self) -> None:
        now = time.monotonic()
        _budget_check(now < self.deadline, lambda ok: _require(ok, 'read_deadline_exhausted'),
                      self, 'deadline', 0, self.deadline - now)

    def step(self) -> None:
        self.check()
        self.traversed += 1
        _budget_check(self.traversed <= 65536, lambda ok: _require(ok, 'read_work_exhausted'),
                      self, 'work', 1, 65536 - self.traversed + 1)

    def candidate(self) -> None:
        self.check()
        super().candidate()

    def reserve(self, size: int) -> None:
        self.step()
        self.consumed += size
        _budget_check(self.remaining_bytes >= 0, lambda ok: _require(ok, 'read_bytes_exhausted'),
                      self, 'bytes', size, self.remaining_bytes + size)

    def work(self, count: int) -> None:
        for _ in range(count):
            self.step()

    def progress(self) -> int:
        self.sql_steps += 1000
        if self.sql_steps > self.sql_limit:
            self._sql_exhaustion = ReadExhaustion('interrupted', self, 'sql', 1000,
                                                self.sql_limit - self.sql_steps + 1000)
            return 1
        now = time.monotonic()
        if now >= self.deadline:
            self._sql_exhaustion = ReadExhaustion('interrupted', self, 'deadline', 0, self.deadline - now)
            return 1
        return 0


@contextmanager
def _sql_budget(conn: sqlite3.Connection, budget: _ReadBudget) -> Iterator[None]:
    budget.check()
    conn.set_progress_handler(budget.progress, 1000)
    try:
        yield
        budget.check()
    except (ValueError, sqlite3.Error) as exc:
        if budget._sql_exhaustion is not None:
            raise exc from budget._sql_exhaustion
        raise
    finally:
        conn.set_progress_handler(None, 0)


def _rows(conn: sqlite3.Connection, table: str, metadata: tuple[tuple, ...],
          budget: _ReadBudget) -> Iterator[dict]:
    # Complete schema was independently matched; names/types are trusted only
    # after that match. Reuse pre-fetch typeof/length gating, never SELECT *.
    # These exact columns were acquired and matched inside this snapshot by
    # _read_layout. Reacquisition would charge the same metadata rows twice.
    names = tuple(row[1] for row in metadata)
    kinds = {'TEXT': 'text', 'INTEGER': 'integer', 'BLOB': 'blob'}
    allowed = tuple((kinds[row[2]],) + (() if row[3] else ('null',)) for row in metadata)
    for row in _bounded_rows(conn, table, names, allowed, budget):
        values = row[:len(names)]
        budget.reserve(512 + 128 * len(names))
        yield dict(zip(names, values, strict=True))


def _native_rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
                 allowed: tuple[tuple[str, ...], ...], identity: object,
                 budget: _ReadBudget) -> list[tuple]:
    result = []
    for row in _bounded_rows(conn, table, columns, allowed, budget,
                             where='id=?', args=(identity,), maximum=1):
        budget.reserve(512 + 24 * sum(len(value.encode('utf8')) for value in row[:len(columns)]
                                    if type(value) is str))
        result.append(row[:len(columns)])
    return result


_Parsed = TypeVar('_Parsed')


def _decode(raw: bytes, budget: _ReadBudget) -> dict:
    budget.reserve(24 * len(raw) + 512)
    value = _json(raw, 1048576)
    _decoded(value, budget)
    return value


_OUTPUT_STRING_STRUCTURE = re.compile(rb'(?:[^"]++|"(?:[^"\\]++|\\[\s\S])*+")*+')
_OUTPUT_STRINGS = re.compile(rb'"(?:[^"\\]++|\\[\s\S])*+"')
_OUTPUT_LEAVES = re.compile(rb'\[[^][{}]*+\]|\{[^][{}]*+\}')
_OUTPUT_DELIMITERS = re.compile(rb'[][{}]')
_OUTPUT_LONG_INTEGER = re.compile(rb'[0-9]{20}')
_OUTPUT_FLOAT = re.compile(rb'[0-9][.eE]')


def _output_json(raw: bytes, budget: _ReadBudget) -> None:
    """Prove output integrity without a discarded Python node/byte walk.

    _outputs has already gated length/type/SHA. The original caller allowance
    (complete history or selected accept before COMMIT) covers native JSON
    decoding/canonical encoding plus all transient topology copies. Compiled
    scans mask strings and remove innermost containers in at most 24 rounds;
    Python work is bounded by depth, independently of scalar/member density.
    CPython's accelerated JSON path performs the actual parse/encode work.
    Suspect input uses the strict old decoder on this SAME budget, preserving
    refusal categories without making a digest an attestation of valid JSON.
    """
    budget.reserve(24 * len(raw) + 512)
    budget.reserve(4 * len(raw) + 512)
    budget.step()
    # Anchored, disjoint, possessive branches visit each byte at most once.
    # Only proven strings reach sub: every candidate then consumes a complete
    # string, so it cannot restart at escaped quotes in an unterminated suffix.
    if _OUTPUT_STRING_STRUCTURE.fullmatch(raw) is None:
        _decode(raw, budget)
        return
    budget.step()
    tokens = _OUTPUT_STRINGS.sub(b'""', raw)
    budget.step()
    if _OUTPUT_LONG_INTEGER.search(tokens):
        _decode(raw, budget)
        return
    topology = tokens
    # Each leaf candidate stops at the next delimiter: candidates cannot scan
    # overlapping interiors, even on malformed brackets. Each round is O(n),
    # at most 24 rounds. Integer/float/delimiter searches have fixed width.
    for _ in range(24):
        budget.step()
        reduced = _OUTPUT_LEAVES.sub(b'', topology)
        if reduced == topology:
            break
        topology = reduced
    budget.step()
    if _OUTPUT_DELIMITERS.search(topology):
        _decode(raw, budget)
        return
    budget.step()
    try:
        value = json.loads(raw.decode('utf8'))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _decode(raw, budget)
        return
    budget.step()
    try:
        canonical = _bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _decode(raw, budget)
        return
    budget.step()
    if type(value) is not dict or raw != canonical or _OUTPUT_FLOAT.search(tokens):
        _decode(raw, budget)
    budget.check()


def _parse(raw: bytes, parser: Callable[..., _Parsed], budget: _ReadBudget,
           **kwargs: object) -> _Parsed:
    # Reserve repeated JSON/model/copy work on every parse, including replay;
    # a retained row does not buy an unlimited number of decoded copies.
    budget.reserve(48 * len(raw) + 512)
    value = parser(raw, **kwargs)
    model = value.definition if isinstance(value, CompiledDefinition) else value
    _decoded(model.model_dump(mode='json'), budget)
    return value


def read_sequential_layout(conn: sqlite3.Connection) -> SequentialLayout:
    """Read complete dimensions with separate finite allowances in one snapshot."""
    observed = _ReadBudget()
    reference = _ReadBudget(deadline=observed.deadline)
    data = _ReadBudget(deadline=observed.deadline)
    with _schema_snapshot(conn):
        layout, _ = _read_layout(conn, observed, reference, data)
        data.check()
        return layout


def _read_layout(conn: sqlite3.Connection, observed: _ReadBudget,
                 reference: _ReadBudget, data: _ReadBudget) -> tuple[SequentialLayout, dict[str, tuple[tuple, ...]]]:
    """Compare the ENTIRE owned schema against independently built references.

    Observation selects only finite dimensions. No candidate DDL, object filter,
    baseline derivation, table stripping or database mutation is used. Existing
    literal reference construction retains its complete F/E/G/H/I/J semantics.
    """
    with _sql_budget(conn, observed):
        _require(not any(str(value).casefold().startswith('workflow_')
                         or str(value).casefold() in ('tasks', 'task_results')
                         for row in _structure_cells(conn, 'SELECT rowid,name,tbl_name FROM sqlite_temp_schema',
                             ('name', 'tbl_name'), ('rowid',), observed) for value in row), 'temporary_shadow_refused')
        actual = _candidate_structure(conn, observed)[0]
    names = {row[1] for row in actual[0]}
    sequential = any(str(name).casefold().startswith('workflow_seq_')
                     for row in actual[0] for name in row[1:3])
    if not names:
        return SequentialLayout(None, False, False, 0, False), {}
    coding = any(str(name).casefold().startswith('workflow_coding_')
                 for row in actual[0] for name in row[1:3])
    document = ('I' if 'workflow_document_revision_operations' in names else
                'H' if 'workflow_author_handoffs' in names else
                'G' if 'workflow_submission_schema_versions' in names else
                'E' if 'workflow_draft_adapter_versions' in names else 'F')
    if document == 'I' and coding:
        document = 'J'
    indexed = any(str(name).casefold().startswith('workflow_owner_') for name in names)
    marked = bool(names & {'workflow_task_ownership', 'workflow_task_owner_bindings'})
    # Select a finite literal dimension, then compare the entire independent
    # reference. Candidate SQL never becomes reference DDL. This also avoids
    # rebuilding both ownership references for an empty companion.
    companion = ' '.join(SEQUENTIAL_OWNERSHIP_DDL.strip().removesuffix(';').split())
    version2 = any(row[1] == 'workflow_task_ownership' and row[3] == companion for row in actual[0])
    versions = ((2,) if version2 and sequential else (1,)) if marked else (0,)
    for ownership in versions:
        reference.step()
        with _canonical_connection(document, coding_extension=coding,
                                   owner_indexed=indexed) as expected:
            # Fixed literal construction before yield is deadline-bracketed only;
            # subsequent metadata and comparison consume reference allowance B.
            with _sql_budget(expected, reference):
                if sequential:
                    _execute_ddl(expected, CANONICAL_WORKFLOW_SEQUENTIAL_DDL)
                if ownership:
                    expected.execute(SEQUENTIAL_OWNERSHIP_DDL if ownership == 2 else OWNERSHIP_DDL)
                    expected.execute(sequential_binding_view_ddl(document, coding_extension=coding)
                                     if ownership == 2 else binding_view_ddl(document, coding_extension=coding))
                expected_structure = _candidate_structure(expected, reference)[0]
                reference.work(sum(len(part) for part in actual) + sum(len(part) for part in expected_structure))
                if actual == expected_structure:
                    # Retain the already acquired, fully matched column tuples
                    # for row reads in the SAME snapshot; the map consumes C.
                    data.reserve(128 * len(actual[1]))
                    metadata = {table: columns for table, columns, _ in actual[1]}
                    return SequentialLayout(document, coding, indexed, ownership, sequential), metadata
    raise ValueError('workflow_seq_complete_layout_mismatch')


def _native_contract(conn: sqlite3.Connection, budget: _ReadBudget) -> None:
    for table, key_type, columns in (
        ('tasks', 'TEXT', {'id', 'parent_task_id', 'assigned_agent', 'current_session_id'}),
        ('task_results', 'INTEGER', {'id', 'task_id', 'agent', 'session_id'}),
    ):
        found = list(_bounded_rows(conn, 'sqlite_schema', ('type', 'sql'), (('text',), ('text',)),
                                   budget, where='name=?', args=(table,), maximum=1))
        found = found[0] if found else None
        metadata = {row[1]: row for row in _structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
            ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), budget, (table,))}
        _require(found is not None and found[0] == 'table' and columns <= metadata.keys(), 'native_schema_invalid')
        _require(metadata['id'][2].upper() == key_type and metadata['id'][5] == 1
                 and sum(bool(row[5]) for row in metadata.values()) == 1, 'native_key_invalid')
        if table == 'task_results':
            _require('AUTOINCREMENT' in found[1].upper(), 'native_integer_identity_required')


def _result(conn: sqlite3.Connection, result_id: object, task: str, principal: str, session: str,
            budget: _ReadBudget) -> None:
    _require(type(result_id) is int and result_id > 0, 'result_identity_invalid')
    rows = _native_rows(conn, 'task_results', ('id', 'task_id', 'agent', 'session_id'),
                        (('integer',), ('text',), ('text',), ('text',)), result_id, budget)
    _require(rows == [(result_id, task, principal, session)], 'native_result_conflict')


def _definitions(data: dict[str, list[dict]], budget: _ReadBudget) -> dict[str, CompiledDefinition]:
    result = {}
    for row in data['workflow_seq_definitions']:
        budget.step()
        compiled = _parse(_hashed(row['definition_bytes'], row['definition_digest']),
                          compile_definition, budget, source_digest=row['source_digest'])
        pin = compiled.pin
        _require((row['identity'], row['version'], row['compiler_pin'], row['validator_pin'],
                  row['interpreter_pin'], row['capability_pin']) ==
                 (pin.identity, pin.version, pin.compiler, pin.validator, pin.interpreter, pin.capabilities),
                 'definition_pin_conflict')
        result[row['id']] = compiled
    return result


def _settings(data: dict[str, list[dict]], definitions: dict[str, CompiledDefinition],
              org_slug: str, budget: _ReadBudget) -> tuple[dict[str, dict], dict[str, StorageSettings]]:
    rows = {row['id']: row for row in data['workflow_seq_settings']}
    snapshots = {}
    generations: dict[str, list[int]] = {}
    for key, row in rows.items():
        budget.step()
        _require(row['org_slug'] == org_slug and row['definition_id'] in definitions, 'settings_owner_invalid')
        snapshot = StorageSettings.model_validate(_decode(_hashed(row['snapshot_bytes'], row['snapshot_digest']), budget))
        definition = definitions[row['definition_id']]
        bindings = {item.role: item.principal for item in snapshot.bindings}
        _require(snapshot.definition == definition.pin and row['definition_identity'] == definition.pin.identity
                 and len(bindings) == len(snapshot.bindings)
                 and bindings.keys() == dict(definition.definition.roles).keys(), 'settings_pin_conflict')
        for role, requirement in definition.definition.roles:
            _require(all(bindings[role] != bindings[other] for other in requirement.independent_of),
                     'settings_independence_conflict')
        generations.setdefault(row['definition_identity'], []).append(row['generation'])
        snapshots[key] = snapshot
    for values in generations.values():
        _require(sorted(values) == list(range(1, len(values) + 1)), 'settings_generation_gap')
    seen = set()
    for row in data['workflow_seq_settings_operations']:
        budget.step()
        target = rows.get(row['settings_id'])
        _require(target is not None and target['id'] not in seen and row['org_slug'] == org_slug,
                 'settings_operation_conflict')
        request = _record(row['request_bytes'], row['request_digest'], {
            'format', 'operation_key', 'principal', 'settings_id', 'expected_generation', 'snapshot_digest'}, budget)
        receipt = _record(row['receipt_bytes'], row['receipt_digest'], {
            'format', 'operation_id', 'settings_id', 'generation', 'snapshot_digest'}, budget)
        _require(_bytes(request) == _bytes(dict(format='workflow-sequential-settings-operation-storage@1',
                 operation_key=row['operation_key'], principal=row['principal'], settings_id=target['id'],
                 expected_generation=target['generation'] - 1, snapshot_digest=target['snapshot_digest'])),
                 'settings_request_conflict')
        _require(_bytes(receipt) == _bytes(dict(format='workflow-sequential-settings-receipt-storage@1',
                 operation_id=row['id'], settings_id=target['id'], generation=target['generation'],
                 snapshot_digest=target['snapshot_digest'])), 'settings_receipt_conflict')
        seen.add(target['id'])
    _require(seen == rows.keys(), 'settings_operation_missing')
    return rows, snapshots


def _outputs(data: dict[str, list[dict]], visits: dict[str, dict], runs: dict[str, dict],
             definitions: dict[str, CompiledDefinition], budget: _ReadBudget) -> dict[str, list[dict]]:
    selected: dict[str, list[dict]] = {}
    for row in data['workflow_seq_outputs']:
        budget.step()
        visit = visits.get(row['visit_id'])
        _require(visit is not None and visit['run_id'] == row['run_id'] and row['run_id'] in runs,
                 'output_owner_conflict')
        run = runs[row['run_id']]
        definition = definitions[run['definition_id']].definition
        recipients = _decode(row['recipients_bytes'], budget)
        _require(set(recipients) == {'roles'} and type(recipients['roles']) is list
                 and bool(recipients['roles']) and all(type(role) is str for role in recipients['roles'])
                 and len(set(recipients['roles'])) == len(recipients['roles'])
                 and set(recipients['roles']) <= dict(definition.roles).keys(), 'output_recipients_invalid')
        _hashed(row['content_bytes'], row['content_digest'])
        _require((row['task_id'], row['session_id'], row['result_id'], row['epoch']) ==
                 (visit['task_id'], visit['session_id'], visit['result_id'], visit['epoch']),
                 'output_result_conflict')
        if row['contract'] == 'immutable-document@1':
            row['content_bytes'].decode('utf8')
        else:
            # Domain content integrity only; capability and repository semantics
            # are checked by the future trusted U5 adapter, not unsigned bytes.
            _output_json(row['content_bytes'], budget)
        selected.setdefault(visit['id'], []).append(dict(slot=row['slot'], contract=row['contract'],
            epoch=row['epoch'], producer_visit=visit['id'], task_id=row['task_id'], session_id=row['session_id'],
            result_id=row['result_id'], digest=row['content_digest']))
    return selected


def _event_payload(row: dict, budget: _ReadBudget) -> None:
    payload = _record(row['event_bytes'], row['event_digest'], {
        'format', 'kind', 'run_id', 'sequence', 'operation_key', 'prior_digest'}, budget)
    _require(_bytes(payload) == _bytes(dict(format='workflow-sequential-storage-event@1', kind=row['kind'],
             run_id=row['run_id'], sequence=row['sequence'], operation_key=row['operation_key'],
             prior_digest=row['prior_digest'])), 'event_payload_conflict')


def _selection(visit: dict, budget: _ReadBudget) -> tuple[OutputRef, ...]:
    value = _record(visit['input_selection_bytes'], visit['input_selection_digest'], {'outputs'}, budget)
    _require(type(value['outputs']) is list and len(value['outputs']) <= 32, 'input_selection_invalid')
    result = tuple(OutputRef.model_validate(item) for item in value['outputs'])
    _require(len({item.slot for item in result}) == len(result), 'input_selection_duplicate')
    return result


_WIRE_TOKENS = re.compile(rb'"(?:[^"\\]++|\\[\s\S])*+"|[][{},:]')


_WireRecord = TypeVar('_WireRecord', StartCommand, ResultEnvelope)


def _wire_record(raw: bytes, parser: Callable[[bytes], _WireRecord],
                 model: type[_WireRecord], maximum: int, budget: _ReadBudget) -> _WireRecord:
    # No speculative scan on a nearly spent original allowance. 1024 bounds
    # the decoded walk of either VALID closed model; <=2048 tokens follow.
    # Malformed inputs may have any density, so the planner itself stops at
    # 2048 tokens and invokes the original strict parser on this SAME budget.
    original = 48 * len(raw) + 512
    buffers = 20 * len(raw) + 512
    planning = 256 * 2049
    if (65536 - budget.traversed < 4096
            or budget.remaining_bytes < original + buffers + planning):
        return _parse(raw, parser, budget)
    # Charge buffers/matcher before the first native scan, and every token
    # before creating its match. Nothing is reset, refunded or scanned free.
    budget.reserve(buffers)
    if _OUTPUT_STRING_STRUCTURE.fullmatch(raw) is None:
        return _parse(raw, parser, budget)
    depth = tokens = containers = 0
    matches = _WIRE_TOKENS.finditer(raw)
    while True:
        budget.reserve(256)
        match = next(matches, None)
        if match is None:
            break
        tokens += 1
        char = raw[match.start()]
        if char in (91, 123):
            depth += 1
            containers += 1
        elif char in (93, 125):
            depth -= 1
        if (depth < 0 or depth > 24 or tokens >= 2048
                or 20 * len(raw) + 4096 * containers + 256 * tokens > 48 * len(raw)):
            return _parse(raw, parser, budget)
    budget.reserve(4096 * containers + 512)
    value = _json(raw, maximum)
    result = _closed_record(model, value)
    _decoded(value, budget)
    return result


def _run_history(conn: sqlite3.Connection, run: dict, compiled: CompiledDefinition,
                 settings: dict, snapshot: StorageSettings, visits: list[dict],
                 events: list[dict], outputs: dict[str, list[dict]], budget: _ReadBudget,
                 observations: list[dict]) -> None:
    from runtime.workflows.sequential_business_hold import validate_business_holds
    budget.reserve(512 + 128 * len(visits))
    hold = validate_business_holds(conn, rows=observations, run=run,
        visits={visit['id']: visit for visit in visits}, events=events, compiled=compiled, budget=budget)
    start = _wire_record(_hashed(run['start_bytes'], run['start_digest']),
                         parse_start, StartCommand, DEFINITION_LIMIT, budget)
    original = Limits.model_validate(_decode(run['original_limits_bytes'], budget))
    limits = validate_start(start, compiled, original_limits=original)
    _require(start.operation_key == run['start_operation_key']
             and start.settings.generation == settings['generation']
             and start.settings.snapshot_digest == settings['snapshot_digest']
             and start.bindings == snapshot.bindings and snapshot.admission == 'Allowed'
             and all(getattr(start.lower_limits, key) <= getattr(snapshot.limits, key)
                     for key in Limits.model_fields), 'admission_pin_conflict')
    _result(conn, run['source_result_id'], run['source_task_id'], run['source_principal'], run['source_session_id'], budget)
    source = _native_rows(conn, 'tasks', ('assigned_agent',), (('text',),), run['source_task_id'], budget)
    carrier = _native_rows(conn, 'tasks', ('parent_task_id', 'current_session_id'),
        (('text', 'null'), ('text', 'null')), run['carrier_task_id'], budget)
    _require(source == [(run['source_principal'],)] and carrier == [(run['source_task_id'], None)],
             'source_carrier_conflict')
    _require([row['ordinal'] for row in visits] == list(range(1, len(visits) + 1))
             and 0 < len(visits) <= limits.max_visits, 'visit_order_invalid')
    _require([row['sequence'] for row in events] == list(range(1, len(events) + 1))
             and 0 < len(events) <= limits.max_events and events[0]['kind'] == 'admitted', 'event_order_invalid')
    nodes = {node.id: node for node in compiled.definition.nodes}
    bindings = {binding.role: binding.principal for binding in start.bindings}
    by_id = {visit['id']: visit for visit in visits}
    authenticated: dict[str, AuthenticatedResult] = {}
    budget.reserve(512 + 128 * len(visits))
    for visit in visits:
        budget.step()
        node = nodes.get(visit['node_id'])
        _require(node is not None and visit['principal'] == bindings[node.role], 'visit_binding_conflict')
        task = _native_rows(conn, 'tasks', ('parent_task_id', 'assigned_agent'),
            (('text', 'null'), ('text',)), visit['task_id'], budget)
        _require(task == [(run['carrier_task_id'], visit['principal'])], 'visit_task_conflict')
        _selection(visit, budget)
        if visit['result_id'] is not None:
            _result(conn, visit['result_id'], visit['task_id'], visit['principal'], visit['session_id'], budget)
            envelope = _wire_record(_hashed(visit['result_bytes'], visit['result_digest']),
                                    parse_result, ResultEnvelope, RESULT_LIMIT, budget)
            checks = _decode(visit['verified_checks_bytes'], budget)
            _require(set(checks) == {'checks'}, 'verified_checks_invalid')
            authenticated[visit['id']] = AuthenticatedResult(task_id=visit['task_id'], session_id=visit['session_id'],
                result_id=visit['result_id'], envelope=envelope, verified_checks=checks['checks'])
            actual_outputs = sorted(outputs.get(visit['id'], []), key=lambda item: item['slot'])
            _require(actual_outputs == sorted((item.model_dump() for item in envelope.outputs),
                                              key=lambda item: item['slot']), 'output_envelope_conflict')
            _require(envelope.run_id == run['id'] and envelope.visit_id == visit['id']
                     and envelope.definition_digest == compiled.pin.definition_digest
                     and envelope.input_selection_digest == visit['input_selection_digest'], 'result_envelope_conflict')
            if node.output_contract.type == 'guarded-repository-merge@1' and envelope.outputs:
                from runtime.workflows.sequential_coding_final_history import validate_final_output
                validate_final_output(conn, run=run, visit=visit, compiled=compiled, start=start,
                    visits=visits, events=events, outputs=outputs, original=original,
                    envelope=envelope, verified_checks=checks['checks'], budget=budget)
        else:
            _require(not outputs.get(visit['id']) and visit['state'] not in ('completed', 'failed'), 'visit_result_missing')
    first = visits[0]
    _require(first['node_id'] == compiled.definition.entry_node and first['epoch'] == 1 and not _selection(first, budget)
             and first['predecessor_event_id'] is None, 'entry_visit_invalid')
    counters = Counters(epoch=1, content_returns=0, visits=1, events=0)
    selections, findings, receipts = (), (), ()
    active, state, prior = first, 'active', '0' * 64
    allocated = {first['id']}
    for event in events:
        budget.step()
        _require(event['prior_digest'] == prior, 'event_chain_invalid')
        _hashed(event['event_bytes'], event['event_digest'])
        if event['kind'] == 'transition':
            _require(state == 'active' and active is not None and active['result_id'] is not None
                     and event['source_visit_id'] == active['id']
                     and event['source_result_id'] == active['result_id'], 'transition_source_invalid')
            transition = _parse(event['event_bytes'], parse_transition, budget)
            result = authenticated[active['id']]
            envelope = result.envelope
            current = ActiveVisit(node=active['node_id'], visit_id=active['id'], task_id=active['task_id'],
                session_id=active['session_id'], epoch=active['epoch'], input_selection_digest=active['input_selection_digest'])
            budget.work(len(receipts) + len(selections) + len(findings))
            budget.reserve(256 * (len(receipts) + len(selections) + len(findings)))
            decision: Decision = reduce_result(compiled, RunState(run_id=run['id'],
                definition_digest=compiled.pin.definition_digest, original_limits=original,
                lower_limits=start.lower_limits, counters=counters, active_visit=current,
                selections=selections, findings=findings, receipts=receipts), result)
            _require((transition.run_id, transition.definition_digest, transition.source_visit,
                      transition.source_result, transition.result_digest, transition.node, transition.outcome,
                      transition.prior_cas, transition.prior_event_digest, transition.before, transition.after,
                      transition.invalidated, transition.edge) ==
                     (run['id'], compiled.pin.definition_digest, active['id'], active['result_id'],
                      decision.result_digest, active['node_id'], decision.outcome, event['sequence'] - 2,
                      prior, decision.before, decision.after, decision.invalidated, decision.edge),
                     'transition_reducer_conflict')
            digest = envelope.outputs[0].digest if envelope.outputs else None
            _require(transition.output_digest == digest, 'transition_output_conflict')
            counters, selections, findings = decision.after, decision.selections, decision.findings
            receipts += (decision,)
            if isinstance(decision.edge, NextEdge):
                successor = transition.successor
                next_visit = None if successor is None else by_id.get(successor.visit_id)
                _require(next_visit is not None and next_visit['id'] not in allocated
                         and (next_visit['node_id'], next_visit['task_id'], next_visit['epoch'], next_visit['ordinal'],
                              next_visit['predecessor_event_id']) ==
                         (successor.node, successor.task_id, counters.epoch, counters.visits, event['id']),
                         'successor_conflict')
                _require(decision.activation is not None
                         and _selection(next_visit, budget) == decision.activation.inputs, 'successor_input_conflict')
                _require(active['state'] == 'completed' and active['selected'] == 0, 'retired_visit_invalid')
                allocated.add(next_visit['id'])
                active = next_visit
            elif isinstance(decision.edge, TerminalEdge):
                _require(active['state'] == 'completed' and active['selected'] == 0, 'terminal_visit_invalid')
                active, state = None, 'completed'
            elif decision.edge.action == 'fail':
                _require(active['state'] == 'failed' and active['selected'] == 0, 'failed_visit_invalid')
                active, state = None, 'failed'
            else:
                state = 'held'
        else:
            _event_payload(event, budget)
            if event['kind'] == 'admitted':
                _require(event['sequence'] == 1, 'duplicate_admission')
            elif event['kind'] == 'cancelled':
                _require(state in ('active', 'held') and active is not None
                         and active['state'] == 'cancelled' and active['selected'] == 0, 'cancellation_conflict')
                active, state = None, 'cancelled'
            else:
                _require(event['kind'] == 'returned' and state == 'completed'
                         and event is events[-1], 'return_conflict')
        prior = event['event_digest']
        if hold is not None and event['id'] == hold['last_event_id']:
            _require(state == 'active' and active is not None and active['id'] == hold['visit_id']
                     and active['result_id'] is None, 'business_hold_prefix_conflict')
            state = 'held'
    _require(allocated == by_id.keys() and run['state'] == state and run['cas'] == len(events) - 1
             and run['event_count'] == len(events) and run['last_event_id'] == events[-1]['id']
             and (run['epoch'], run['content_returns'], run['visit_count']) ==
             (counters.epoch, counters.content_returns, counters.visits), 'run_history_conflict')
    selected = [visit['id'] for visit in visits if visit['selected']]
    _require(run['active_visit_id'] == (None if active is None else active['id'])
             and selected == ([] if active is None else [active['id']]), 'active_pointer_conflict')
    if state == 'held':
        _require(active is not None and active['state'] == 'held', 'held_visit_invalid')
    elif state == 'active':
        _require(active is not None and active['state'] in ('queued', 'running', 'waiting')
                 and (active['state'] != 'queued' or (active['session_id'] is None and active['result_id'] is None)),
                 'active_visit_invalid')


def _auxiliary(conn: sqlite3.Connection, data: dict[str, list[dict]], runs: dict[str, dict], visits: dict[str, dict],
               events: dict[str, dict], budget: _ReadBudget,
               definitions: dict[str, CompiledDefinition]) -> None:
    from runtime.workflows.sequential_repository_subject import validate_subject
    subjects: dict[str, list[dict]] = {}
    for row in data['workflow_seq_evidence']:
        budget.step()
        _require(row['kind'] in ('preflight', 'review', 'qa', 'intent', 'action', 'observation', 'terminal', 'outer'),
                 'evidence_kind_invalid')
        _require(row['run_id'] in runs and (row['visit_id'] is None or
                 (row['visit_id'] in visits and visits[row['visit_id']]['run_id'] == row['run_id'])),
                 'evidence_owner_conflict')
        if row['kind'] == 'preflight':
            _require(type(row['evidence_bytes']) is bytes and 0 < len(row['evidence_bytes']) <= 16384,
                     'subject_bytes_invalid')
            subjects.setdefault(row['run_id'], []).append(row)
        _decode(_hashed(row['evidence_bytes'], row['evidence_digest']), budget)
        if row['result_id'] is not None:
            _result(conn, row['result_id'], row['task_id'], row['principal'], row['session_id'], budget)
            run = runs[row['run_id']]
            visit = visits.get(row['visit_id'])
            _require((visit is not None and row['task_id'] == visit['task_id']) or
                     (row['visit_id'] is None and row['task_id'] == run['source_task_id']), 'evidence_task_conflict')
    for run_id, rows in subjects.items():
        run = runs[run_id]
        budget.work(len(visits))
        validate_subject(conn, rows=rows, run=run,
            visits={key: value for key, value in visits.items() if value['run_id'] == run_id},
            compiled=definitions[run['definition_id']], budget=budget)
    notifications: dict[str, dict] = {}
    for row in data['workflow_seq_outbox']:
        budget.step()
        event = events.get(row['event_id'])
        run = runs.get(row['run_id'])
        _require(event is not None and run is not None and event['run_id'] == run['id'], 'outbox_event_conflict')
        payload = _record(row['notification_bytes'], row['notification_digest'], {
            'format', 'notification_id', 'event_id', 'run_id', 'visit_id', 'task_id', 'kind'}, budget)
        _require(_bytes(payload) == _bytes(dict(format='workflow-sequential-notification-storage@1', notification_id=row['id'],
                 event_id=event['id'], run_id=run['id'], visit_id=row['visit_id'], task_id=row['task_id'], kind=row['kind'])),
                 'outbox_payload_conflict')
        if row['kind'] == 'visit_queued':
            visit = visits.get(row['visit_id'])
            _require(visit is not None and visit['run_id'] == run['id'] and row['task_id'] == visit['task_id']
                     and ((visit['ordinal'] == 1 and event['kind'] == 'admitted')
                          or visit['predecessor_event_id'] == event['id'])
                     and visit['id'] not in notifications, 'outbox_visit_conflict')
            notifications[visit['id']] = row
        else:
            _require(row['kind'] == 'run_return' and event['kind'] == 'returned'
                     and row['visit_id'] is None and row['task_id'] == run['source_task_id'], 'outbox_return_conflict')
    _require(notifications.keys() == visits.keys(), 'outbox_visit_missing')
    returned = {row['id'] for row in events.values() if row['kind'] == 'returned'}
    actual_returns = {row['event_id'] for row in data['workflow_seq_outbox'] if row['kind'] == 'run_return'}
    _require(returned == actual_returns, 'outbox_return_missing')


def _legacy_history(conn: sqlite3.Connection, org_slug: str, budget: _ReadBudget,
                    sequential_rows: dict[str, list[dict]] | None = None, *,
                    retained_budget: _Budget | None = None,
                    ) -> tuple[str, _CodingInventory | None]:
    # Exact retained budget/native connection and original per-call caps. The
    # new reader's schema/copy work has wider limits, so it cannot be seeded
    # into the retained narrower counters. Keep the outer totals intact and
    # charge this phase's consumption on both success and refusal below.
    legacy_budget = _Budget() if retained_budget is None else retained_budget
    opening = legacy_budget.consumed, legacy_budget.accepted_rows, legacy_budget.traversed
    try:
        if sequential_rows is None:
            retained = _read_workflow_schema(conn, expected_org_slug=org_slug, budget=legacy_budget)
        else:
            # Same one-read context as workflow_schema:863-878, with the actual
            # caller budget preserved.
            if not conn.in_transaction or _sequential_legacy_read.get() is not None:
                raise ValueError('workflow_seq_legacy_reader_requires_snapshot')
            token = _sequential_legacy_read.set((conn, sequential_rows))
            try:
                retained = _read_workflow_schema(conn, expected_org_slug=org_slug, budget=legacy_budget)
            finally:
                _sequential_legacy_read.reset(token)
    finally:
        # Charge only this call's deltas, even on refusal. Source and changed
        # target keep the SAME cumulative retained budget, without double charge.
        budget.consumed += legacy_budget.consumed - opening[0]
        budget.accepted_rows += legacy_budget.accepted_rows - opening[1]
        budget.traversed += legacy_budget.traversed - opening[2]
    _budget_check(budget.accepted_rows <= _CALL_ROW_LIMIT, lambda ok: _require(ok, 'read_rows_exhausted'),
                  budget, 'rows', budget.accepted_rows, _CALL_ROW_LIMIT)
    budget.reserve(0)
    return retained


def read_sequential_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                            retained_budget: _Budget | None = None,
                            deadline: float | None = None,
                            ownership_sql_limit: int = 32000000,
                            task_id: str | None = None) -> SequentialHistory:
    """Validate all sequential rows and complete combined schema in one snapshot.

    Empty/absent storage is explicitly unavailable. A readable inventory is not
    an installed engine, admission, authorization or safe-to-launch disposition.
    Original complete document/Coding readers validate retained data on both
    legacy-only and combined layouts. Their recursive structure checks select
    complete combined references only inside the explicit new-reader scope.
    An explicit task_id adds the inert U6a native/ownership closure after that
    oracle, under the same snapshot, original data allowance and deadline.
    Default callers retain the original result type and validation path.
    The explicit offline Coding operator may size the SQL-only ordinary-history
    guards up to 640M steps. Every predicate and acquired workflow-data allowance
    remains unchanged; installer/runtime callers retain the original 32M limit.
    """
    _require(type(expected_org_slug) is str and bool(expected_org_slug), 'org_required')
    _require(type(ownership_sql_limit) is int and 32000000 <= ownership_sql_limit <= 640000000,
             'ownership_sql_limit_invalid')
    _require(task_id is None or (type(task_id) is str and bool(task_id) and '\x00' not in task_id),
             'mixed_task_identity_invalid')
    observed = _ReadBudget() if deadline is None else _ReadBudget(deadline=deadline)
    reference = _ReadBudget(deadline=observed.deadline)
    budget = _ReadBudget(deadline=observed.deadline)
    legacy = _ReadBudget(deadline=observed.deadline, sql_limit=ownership_sql_limit)
    with _schema_snapshot(conn):
        layout, metadata = _read_layout(conn, observed, reference, budget)
        if not layout.sequential:
            _require(task_id is None, 'mixed_history_unavailable')
            retained = None
            if layout.document is not None:
                with _sql_budget(conn, legacy):
                    retained = _legacy_history(conn, expected_org_slug, budget, retained_budget=retained_budget)
            budget.check()
            return SequentialHistory(layout, 'new-engine unavailable', (), (), retained)
        with _sql_budget(conn, observed):
            _native_contract(conn, observed)
        with _sql_budget(conn, budget):
            data = {table: list(_rows(conn, table, metadata[table], budget)) for table in SEQUENTIAL_TABLES}
            _require([tuple(row.values()) for row in data['workflow_seq_schema_versions']] == [(1,)],
                     'schema_marker_invalid')
            for table in SEQUENTIAL_TABLES:
                _require(conn.execute('PRAGMA foreign_key_check(' + table + ')').fetchone() is None, 'foreign_key_invalid')
                for row in data[table]:
                    budget.step()
                    for name, value in row.items():
                        if name in ('version', 'generation', 'epoch', 'content_returns', 'visit_count',
                                    'event_count', 'cas', 'ordinal', 'sequence', 'selected'):
                            _require(type(value) is int, 'integer_storage_required')
                        elif name.endswith('result_id'):
                            _require(value is None or (type(value) is int and value > 0), 'result_identity_invalid')
                        elif name.endswith(('_id', '_principal')) or name in ('id', 'org_slug', 'principal', 'operation_key', 'start_operation_key'):
                            _require(value is None or (type(value) is str and bool(value) and '\x00' not in value), 'identity_invalid')
            definitions = _definitions(data, budget)
            settings, snapshots = _settings(data, definitions, expected_org_slug, budget)
            runs = {row['id']: row for row in data['workflow_seq_runs']}
            visits = {row['id']: row for row in data['workflow_seq_visits']}
            events = {row['id']: row for row in data['workflow_seq_events']}
            outputs = _outputs(data, visits, runs, definitions, budget)
            _require(not runs or layout.ownership == 2, 'companion_ownership_required')
            by_run_visits: dict[str, list[dict]] = {}
            by_run_events: dict[str, list[dict]] = {}
            observations: dict[str, list[dict]] = {}
            for row in data['workflow_seq_evidence']:
                budget.step()
                if row['kind'] == 'observation':
                    _require(row['run_id'] in runs, 'evidence_owner_conflict')
                    observations.setdefault(row['run_id'], []).append(row)
            for rows, grouped in ((visits.values(), by_run_visits), (events.values(), by_run_events)):
                for row in rows:
                    budget.step()
                    _require(row['run_id'] in runs, 'orphan_history')
                    grouped.setdefault(row['run_id'], []).append(row)
            start_keys = set()
            for run in runs.values():
                budget.step()
                key = (run['org_slug'], run['source_principal'], run['start_operation_key'])
                _require(key not in start_keys, 'start_operation_conflict')
                start_keys.add(key)
                setting = settings.get(run['settings_id'])
                _require(run['org_slug'] == expected_org_slug and run['definition_id'] in definitions
                         and setting is not None and setting['definition_id'] == run['definition_id'], 'run_pin_invalid')
                run_visits, run_events = by_run_visits.get(run['id'], []), by_run_events.get(run['id'], [])
                for rows in (run_visits, run_events):
                    budget.work(len(rows) * max(1, len(rows).bit_length()))
                _run_history(conn, run, definitions[run['definition_id']], setting, snapshots[setting['id']],
                    sorted(run_visits, key=lambda row: row['ordinal']),
                    sorted(run_events, key=lambda row: row['sequence']), outputs, budget,
                    observations.get(run['id'], []))
            _auxiliary(conn, data, runs, visits, events, budget, definitions)
            if layout.ownership:
                validate_sequential_ownership(conn, org_slug=expected_org_slug, budget=budget, runs=runs, visits=visits)
        # Enroll only fully authenticated new rows; recursive old readers still
        # compare complete combined references and every retained data family.
        with _sql_budget(conn, legacy):
            if layout.ownership:
                validate_sequential_ownership_guards(conn, budget=budget)
            retained = _legacy_history(conn, expected_org_slug, budget, data,
                                       retained_budget=retained_budget)
        budget.check()
        ready = bool(definitions and settings and layout.ownership == 2)
        result = SequentialHistory(layout, 'storage readable' if ready else 'new-engine unavailable',
                                   tuple(sorted(definitions)), tuple(sorted(runs)), retained)
        if task_id is not None:
            from runtime.workflows.sequential_mixed_history import (
                MixedSequentialHistory, close_mixed_history_uncommitted,
            )
            _require(layout.ownership == 2, 'companion_ownership_required')
            with _sql_budget(conn, budget):
                closure = close_mixed_history_uncommitted(conn, org_slug=expected_org_slug,
                                                          task_id=task_id, budget=budget)
            result = MixedSequentialHistory(result.layout, result.status, result.definition_ids,
                                            result.run_ids, result.retained, closure)
    budget.check()
    return result
