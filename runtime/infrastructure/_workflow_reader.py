"""Private accounting for existing workflow readers, never semantic evidence.

The caller supplies its actual remaining allowance and owns the connection and
unchanged snapshot. No transaction, validation result, cache or allowance is
created here. SQLite preparation, sorting and engine allocations are outside
the cooperative Python acquisition limits.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import base64
from copy import deepcopy
from enum import Enum
import sqlite3
from collections.abc import Iterator
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from runtime.workflows.coding_authority_history import _Budget


class _ReaderBudgetExhausted(ValueError):
    """Resource refusal; semantic fallback readers must never turn it absent."""


def _reserve(budget: _Budget | None, size: int) -> None:
    if budget is None:
        return
    if type(size) is not int or size < 0:
        raise ValueError('workflow_reader_invalid_reservation')
    # The original owner retains its native refusal category as well as the
    # private resource discriminator used by semantic fallback readers.
    budget.reserve(size)


def _work(value: object, budget: _Budget | None, *, copies: int = 1) -> None:
    """Reserve a pure routine's input traversal/copies before invoking it.

    Container/string upper bounds cover Python containers and JSON escaping;
    this is accounting, not a hard allocator or wall-time guarantee. Each call
    charges again. Retained object identity supplies no exemption.
    """
    if budget is None:
        return
    if type(copies) is not int or copies < 1:
        raise ValueError('workflow_reader_invalid_reservation')

    def visit(item: object, depth: int) -> None:
        if depth > 64:
            raise _ReaderBudgetExhausted('workflow_reader_depth_exhausted')
        if type(item) is str:
            # Six ASCII bytes per code point bounds JSON escaping, including
            # non-ASCII UTF8. Reserve before constructing any encoded copy.
            _reserve(budget, copies * (6 * len(item) + 64))
        elif type(item) is bytes:
            _reserve(budget, copies * (2 * len(item) + 64))
        elif type(item) is type and item in (str, bytes, int, float, bool, type(None)):
            # Original native locator equality includes literal storage-type
            # identities. Hashing/copying those fixed references allocates no
            # model and grants no type/row validity or execution authority.
            _reserve(budget, copies * 64)
        elif isinstance(item, Enum):
            budget.step()
            visit(item.value, depth + 1)
        elif type(item) in (dict, list, tuple, set, frozenset):
            _reserve(budget, copies * (64 + 64 * len(item)))
            if type(item) is dict:
                for key, member in item.items():
                    visit(key, depth + 1)
                    visit(member, depth + 1)
            else:
                for member in item:
                    visit(member, depth + 1)
        elif item is None or type(item) in (bool, int, float):
            _reserve(budget, copies * 64)
        elif isinstance(item, BaseModel):
            _reserve(budget, copies * (64 + 64 * len(type(item).model_fields)))
            for name in type(item).model_fields:
                visit(getattr(item, name), depth + 1)
        elif isinstance(item, sqlite3.Row):
            _reserve(budget, copies * (64 + 64 * len(item)))
            for index in range(len(item)):
                visit(item[index], depth + 1)
        elif is_dataclass(item) and not isinstance(item, type):
            _reserve(budget, copies * (64 + 64 * len(type(item).__dataclass_fields__)))
            for field in fields(item):
                visit(getattr(item, field.name), depth + 1)
        else:
            raise ValueError('workflow_reader_invalid_value')

    visit(value, 0)


def _json_work(raw: object, budget: _Budget | None) -> None:
    """Preserve json.loads semantics after charging its original input.

    The semantic owner still checks canonical bytes, duplicates and hashes.
    A lexical depth/size gate precedes parser allocation. It is no substitute
    for those original validators.
    """
    if budget is None:
        return
    if type(raw) not in (bytes, str) or len(raw) > 16777216:
        raise ValueError('workflow_reader_invalid_json')
    # Text may require four UTF8 bytes per code point. Reserve decoding and
    # parser containers before encode or loads, then charge reconstructed data
    # separately when its owner actually copies/canonicalizes it.
    _reserve(budget, len(raw) * (24 if type(raw) is str else 12))
    # Inspect decoded code points: a byte scanner would misread UTF16/32 or
    # non-ASCII continuation bytes as JSON quotes/brackets. Reserve BOTH this
    # decode and the original owner's later parse before either allocation.
    data = raw if type(raw) is str else raw.decode(json.detect_encoding(raw))
    depth = 0
    quoted = escaped = False
    for offset, char in enumerate(data):
        byte = ord(char)
        if offset % 256 == 0:
            budget.step()
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            _reserve(budget, 128)
            if depth > 64:
                raise _ReaderBudgetExhausted('workflow_reader_depth_exhausted')
        elif byte in (93, 125):
            depth -= 1
        elif byte in (44, 58):
            _reserve(budget, 128)




def _json(raw: object, budget: _Budget | None, **options) -> object:
    _json_work(raw, budget)
    return json.loads(raw, **options)


def _json_dumps(value: object, *, budget: _Budget | None, **options) -> str:
    _work(value, budget, copies=2)
    if budget is not None and options.get('sort_keys'):
        def sorting(item):
            budget.step()
            if isinstance(item, dict):
                count = len(item)
                _reserve(budget, 128 + 32 * count)
                for _ in range(count * max(1, count.bit_length())):
                    budget.step()
                for member in item.values():
                    sorting(member)
            elif isinstance(item, (list, tuple)):
                for member in item:
                    sorting(member)
        sorting(value)
    return json.dumps(value, **options)


def _sha256(raw: bytes, *, budget: _Budget | None):
    _work(raw, budget)
    # Include the fixed hash object and its later digest/hex result.
    _reserve(budget, 256)
    return hashlib.sha256(raw)


def _fromhex(raw: str, *, budget: _Budget | None) -> bytes:
    _work(raw, budget)
    return bytes.fromhex(raw)


def _b64decode(raw: object, *, budget: _Budget | None, **options) -> bytes:
    _work(raw, budget)
    return base64.b64decode(raw, **options)


def _b64encode(raw: bytes, *, budget: _Budget | None) -> bytes:
    _work(raw, budget)
    return base64.b64encode(raw)


def _copy(value: object, *, budget: _Budget | None):
    _work(value, budget)
    return deepcopy(value)


def _validate(model: type[BaseModel], value: object, *, budget: _Budget | None, **options):
    # Preserve the selected original model's validators and options. This
    # accounts their input/copy envelope before entering the original owner.
    _work(value, budget, copies=2)
    return model.model_validate(value, **options)


def _dump(value: BaseModel, *, budget: _Budget | None, **options):
    _work(value, budget)
    return value.model_dump(**options)


def _encode(value: str, *args, budget: _Budget | None, **options) -> bytes:
    _work(value, budget)
    return value.encode(*args, **options)


def _decode(value: bytes, *args, budget: _Budget | None, **options) -> str:
    _work(value, budget)
    return value.decode(*args, **options)


def _hex(value: bytes, *args, budget: _Budget | None, **options) -> str:
    _work(value, budget)
    return value.hex(*args, **options)


def _list(values, *, budget: _Budget | None) -> list:
    if budget is None:
        return list(values)
    _reserve(budget, 64)
    result = []
    iterator = iter(values)
    primary = None
    try:
        while True:
            # Iterator acquisition and a reference slot precede next/append.
            # The yielding owner separately charges its actual payload.
            _reserve(budget, 128)
            try:
                value = next(iterator)
            except StopIteration:
                return result
            result.append(value)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        # These callers pass their owned ephemeral row iterators. Exhaustion
        # before next must still run that original cursor's lexical cleanup.
        try:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader iterator close failed: ' + type(exc).__name__)


def _equal(left, right, *, budget: _Budget | None) -> bool:
    if budget is None:
        return left == right

    def comparison_work(value, depth=0):
        # Structural metadata consists of nested collections of fixed-width
        # scalar rows. Charge each row together, avoiding an artificial parser/
        # deepcopy traversal per scalar when equality makes neither copy.
        # This measures actual comparison work, never a validation certificate.
        if depth > 64:
            raise _ReaderBudgetExhausted('workflow_reader_depth_exhausted')
        if type(value) in (tuple, list):
            _reserve(budget, 128 + 128 * len(value))
            if len(value) <= 16 and all(type(item) in (str, bytes, int, float, bool, type(None)) for item in value):
                size = 0
                for item in value:
                    if type(item) is str:
                        size += 6 * len(item) + 64
                    elif type(item) is bytes:
                        size += 2 * len(item) + 64
                    else:
                        size += 64
                _reserve(budget, size)
            else:
                for item in value:
                    comparison_work(item, depth + 1)
        else:
            _work(value, budget)

    comparison_work(left)
    comparison_work(right)
    # Keep Python's original equality, including identity/NaN, container-kind,
    # key-collision and Boolean/integer semantics. Accounting is the only change.
    return left == right


def _iter(values, *, budget: _Budget | None):
    """Charge an owned ephemeral traversal before each underlying next()."""
    _reserve(budget, 64)
    iterator = iter(values)
    primary = None
    try:
        while True:
            _reserve(budget, 256)
            try:
                value = next(iterator)
            except StopIteration:
                return
            yield value
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader traversal close failed: ' + type(exc).__name__)
            if isinstance(primary, GeneratorExit):
                raise BaseExceptionGroup('workflow_reader_traversal_close_failed', [primary]) from None


def _all_true(values, *, budget: _Budget | None) -> bool:
    if budget is None:
        return all(values)
    items = _iter(values, budget=budget)
    primary = None
    try:
        return all(items)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            items.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader all traversal close failed: ' + type(exc).__name__)


def _any_true(values, *, budget: _Budget | None) -> bool:
    if budget is None:
        return any(values)
    items = _iter(values, budget=budget)
    primary = None
    try:
        return any(items)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            items.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader any traversal close failed: ' + type(exc).__name__)


def _set(values=(), *, budget: _Budget | None) -> set:
    if budget is None:
        return set(values)
    _reserve(budget, 64)
    result = set()
    items = _iter(values, budget=budget)
    primary = None
    try:
        for value in items:
            _work(value, budget)
            result.add(value)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            items.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader set traversal close failed: ' + type(exc).__name__)
    return result


def _tuple(values=(), *, budget: _Budget | None) -> tuple:
    if budget is None:
        return tuple(values)
    if type(values) is tuple:
        # Like tuple(existing_tuple), retain the immutable references only.
        _reserve(budget, 64)
        return values
    copied = _list(values, budget=budget)
    _reserve(budget, 64 + 16 * len(copied))
    return tuple(copied)


def _append(target: list, value, *, budget: _Budget | None) -> None:
    # Only the new reference slot is copied; the value's constructing owner
    # accounts its payload before supplying it here.
    _reserve(budget, 128)
    target.append(value)


def _extend(target: list, values, *, budget: _Budget | None) -> None:
    if budget is None:
        target.extend(values)
        return
    items = _iter(values, budget=budget)
    primary = None
    try:
        for value in items:
            _append(target, value, budget=budget)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            items.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader extend close failed: ' + type(exc).__name__)


def _add(target: set, value, *, budget: _Budget | None) -> None:
    # Hashing/equality may traverse strings or composite keys even for a
    # duplicate. A retained identity never exempts that actual work.
    _work(value, budget)
    _reserve(budget, 128)
    target.add(value)


def _put(value, target: dict, key, *, budget: _Budget | None) -> None:
    # Assignment evaluates its RHS before its target and key. Callers retain
    # that order by passing value first, including nested target expressions.
    _work(key, budget)
    _reserve(budget, 128)
    target[key] = value


def _setdefault(target: dict, key, default=None, *, budget: _Budget | None):
    _work(key, budget)
    _reserve(budget, 128)
    return target.setdefault(key, default)


def _update(target: dict | set, values, *, budget: _Budget | None) -> None:
    if budget is None:
        target.update(values)
        return
    # Preserve the existing builtin's mapping-versus-pairs interpretation.
    # The temporary dictionary and iterator are separately accounted.
    if isinstance(target, dict):
        values = _dict(values, budget=budget)
        for key in values:
            _put(values[key], target, key, budget=budget)
    else:
        values = _set(values, budget=budget)
        for value in values:
            _add(target, value, budget=budget)


def _dict_pairs(values, *, budget: _Budget | None) -> dict:
    if budget is None:
        return dict(values)
    _reserve(budget, 64)
    result = {}
    items = _iter(values, budget=budget)
    primary = None
    try:
        for key, value in items:
            _work(key, budget)
            result[key] = value
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            items.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader mapping traversal close failed: ' + type(exc).__name__)
    return result


def _dict(values, *, budget: _Budget | None) -> dict:
    if budget is None:
        return dict(values)
    if not hasattr(values, 'keys'):
        return _dict_pairs(values, budget=budget)
    # sqlite3.Row.keys() allocates its own name list; normal dict keys retain
    # their view. Reserve that representation before asking its original owner.
    _reserve(budget, 64 + 128 * len(values))
    keys = values.keys()
    _reserve(budget, 64)
    result = {}
    for key in keys:
        _reserve(budget, 128)
        _work(key, budget)
        result[key] = values[key]
    return result


def _sorted(values, *, budget: _Budget | None, key=None, reverse=False) -> list:
    if budget is None:
        return sorted(values, key=key, reverse=reverse)
    values = _list(values, budget=budget)
    count = len(values)
    height = max(1, count.bit_length())
    _reserve(budget, 128 + 64 * count)
    # Reserve a conservative comparison/traversal envelope before sorting.
    for _ in range(2 * count * height):
        budget.step()

    def charged_key(value):
        _reserve(budget, 128)
        selected = value if key is None else key(value)
        _work(selected, budget, copies=2 * height)
        return selected

    return sorted(values, key=charged_key, reverse=reverse)


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = (), *,
          budget: _Budget | None, names_out: list[str] | None = None,
          _factory=...) -> Iterator:
    """Charge complete acquisition without pairing independent cursor orders.

    The scalar stream establishes a conservative maximum for ANY selected row.
    Each payload fetch reserves that maximum first. The payload statement also
    gates its own actual row, so neither a different traversal order nor an SQL
    expression change can fetch an oversized cell under another row's charge.
    The original descriptor/factory and original selected order are preserved.
    Engine evaluation/materialization is outside these cooperative Python caps.
    """
    if _factory is not None and _factory is not ...:
        raise ValueError('workflow_reader_invalid_factory')
    # Account this actual statement/argument traversal and the cursor/list
    # envelopes before creating either. The semantic caller still owns SQL.
    if budget is not None:
        budget.step()
        if budget.accepted_rows >= 8192:
            budget.candidate()  # original owner's native resource refusal
    _reserve(budget, 96)
    _work((sql, args), budget)
    _reserve(budget, 512)
    scalar = payload = None
    primary = None
    collected = []
    descriptor = conn.cursor()
    try:
        if _factory is None:
            # The native history owners already select raw cursor-local tuples.
            # Never change the connection or a different caller's row factory.
            descriptor.row_factory = None
        if budget is None:
            descriptor.execute(sql, args)
            if names_out is not None:
                names_out.extend(column[0] for column in descriptor.description)
            yield from descriptor
            return
        if not conn.in_transaction:
            raise ValueError('workflow_reader_snapshot_required')
        # Existing private schema readers also use these read-only PRAGMAs.
        # Table-valued forms preserve the selected column order while allowing
        # the same scalar/cell gates; arbitrary PRAGMA/write text is rejected.
        # lstrip/Unicode uppercase can create two separate representations.
        _reserve(budget, 128 + 16 * len(sql))
        if sql.lstrip().upper().startswith('PRAGMA'):
            _reserve(budget, 256 + 8 * len(sql))  # match/group/strip envelopes
            bare = re.fullmatch(r'\s*PRAGMA\s+(database_list|foreign_key_check|integrity_check)\s*', sql, re.I)
            if bare is not None and not args:
                _reserve(budget, 192 + 8 * (len('SELECT * FROM pragma_') + len(bare[1])))
                sql = 'SELECT * FROM pragma_' + bare[1].lower()
            else:
                _reserve(budget, 256 + 8 * len(sql))
                pragma = re.fullmatch(r'\s*PRAGMA\s+(table_info|table_xinfo|foreign_key_list|index_list|index_xinfo|foreign_key_check)\s*\((.+)\)\s*', sql, re.I)
                if pragma is None or args:
                    raise ValueError('workflow_reader_unsupported_pragma')
                table = pragma[2].strip()
                if table.startswith('"') and table.endswith('"'):
                    _reserve(budget, 128 + 8 * len(table))
                    table = table[1:-1].replace('""', '"')
                elif not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
                    raise ValueError('workflow_reader_unsupported_pragma')
                _reserve(budget, 352 + 12 * (len('SELECT * FROM pragma_(?)') + len(pragma[1])))
                sql, args = 'SELECT * FROM pragma_' + pragma[1].lower() + '(?)', (table,)
        budget.step()
        # execute prepares/steps SQLite, but no Python payload row is fetched.
        # This cursor retains the exact original names, including duplicates.
        descriptor.execute(sql, args)
        factory = descriptor.row_factory
        _reserve(budget, 64 + 128 * len(descriptor.description))
        original_names = tuple(column[0] for column in descriptor.description)
        if names_out is not None:
            names_out.extend(original_names)
        if not original_names:
            raise ValueError('workflow_reader_empty_projection')
        # Private positional CTE columns preserve duplicates and expression
        # names through the original descriptor without copying those long
        # names into every gate. They grant no validation or cache authority.
        # Generated positional names are ASCII and no longer than the bit
        # length of the column count plus their prefix. Charge each actual
        # string/tuple construction before it happens; an empty result must
        # not pay a fictitious 8 KiB per projected column.
        count = len(original_names)
        name_width = 1 + max(1, count.bit_length())
        _reserve(budget, 64 + count * (128 + 4 * name_width))
        columns = tuple('c' + str(index) for index in range(len(original_names)))
        _reserve(budget, 128 + 8 * (len(sql) + count * (name_width + 1) + 48))
        source = f"WITH _reader_source({','.join(columns)}) AS ({sql})"
        _reserve(budget, 256)
        scalar = conn.cursor()
        scalar.row_factory = None
        _reserve(budget, 64 + count * (128 + 8 * (40 + 4 * name_width)))
        projection = ','.join(f'typeof({name}),length(CAST({name} AS BLOB))'
                              for name in columns)
        budget.step()
        _reserve(budget, 128 + 4 * (len(source) + len(projection) + 48))
        scalar.execute(f'{source} SELECT {projection} FROM _reader_source', args)
        names = columns
        maximum = 0
        while True:
            budget.step()
            if budget.accepted_rows >= 8192:
                budget.candidate()
            _reserve(budget, 128 * len(names))
            shape = scalar.fetchone()
            if shape is None:
                break
            budget.candidate()
            size = 0
            for offset in range(0, len(shape), 2):
                budget.step()
                storage, length = shape[offset], shape[offset + 1]
                if (storage not in ('null', 'text', 'blob', 'integer', 'real')
                        or (length is not None and (type(length) is not int
                            or length < 0 or length > 16777216))):
                    raise ValueError('workflow_reader_invalid_cell')
                if storage in ('text', 'blob'):
                    size += length
            maximum = max(maximum, size)
        _reserve(budget, 64 + count * (128 + 8 * (240 + 8 * name_width)))
        gates = ' AND '.join(
            f"(typeof({name}) IN ('null','text','blob','integer','real') "
            f"AND (typeof({name}) NOT IN ('text','blob') OR "
            f"length(CAST({name} AS BLOB))<=16777216))" for name in columns)
        _reserve(budget, 64 + count * (128 + 8 * (120 + 3 * name_width)))
        size_sql = '+'.join(f"CASE WHEN typeof({name}) IN ('text','blob') "
                            f"THEN length(CAST({name} AS BLOB)) ELSE 0 END"
                            for name in columns)
        _reserve(budget, 128 + 4 * (len(gates) + len(size_sql) + 48))
        valid = f'({gates}) AND ({size_sql})<={maximum}'
        # Select the original result once per actual payload statement. CASE
        # withholds every cell if ANY actual cell or the whole row is invalid.
        _reserve(budget, 64 + count * (128 + 8 * (80 + 2 * name_width)))
        body = ','.join(f'CASE WHEN _reader_valid THEN {name} ELSE NULL END'
                        for name in columns)
        _reserve(budget, 256)
        payload = conn.cursor()
        payload.row_factory = None
        budget.step()
        _reserve(budget, 128 + 4 * (len(source) + len(valid) + len(body) + 160))
        payload.execute(f'{source},_reader_gated AS (SELECT *,({valid}) '
            f'AS _reader_valid FROM _reader_source) SELECT {body}'
            ',_reader_valid FROM _reader_gated', args)
        while True:
            budget.step()
            if budget.accepted_rows >= 8192:
                budget.candidate()
            # UTF8->str and the raw tuple, reconstructed tuple, Row/dict and
            # their containers are all reserved before fetching/copying.
            _reserve(budget, maximum * 4 + 256 * len(names))
            row = payload.fetchone()
            if row is None:
                break
            budget.candidate()
            if len(row) != len(names) + 1 or row[-1] != 1:
                raise ValueError('workflow_reader_invalid_cell')
            for offset in range(len(names)):
                value = row[offset]
                if (type(value) not in (type(None), str, bytes, int, float)
                        or (type(value) is float and not math.isfinite(value))):
                    raise ValueError('workflow_reader_invalid_cell')
            values = row[:-1]
            _reserve(budget, 16)
            collected.append(values if factory is None else factory(descriptor, values))
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup = None
        close_failed = False
        for cursor in (payload, scalar, descriptor):
            if cursor is None:
                continue
            try:
                cursor.close()
            except BaseException as exc:
                close_failed = True
                if primary is not None:
                    primary.add_note('workflow reader cursor close failed: ' + type(exc).__name__)
                elif cleanup is None:
                    cleanup = exc
                else:
                    cleanup.add_note('workflow reader cursor close also failed: ' + type(exc).__name__)
        if cleanup is not None:
            raise cleanup
        if close_failed and isinstance(primary, GeneratorExit):
            # Do not let generator.close() suppress secondary cleanup failure.
            # Retain the actual pending primary, with only safe type notes.
            raise BaseExceptionGroup('workflow_reader_cursor_close_failed', [primary]) from None
    # No cursor remains open while a semantic consumer traverses these rows.
    # This collection grants no proof; every payload/container was charged above.
    yield from collected


def _one(conn: sqlite3.Connection, sql: str, args: tuple = (), *,
         budget: _Budget | None):
    rows = _rows(conn, sql, args, budget=budget)
    primary = None
    try:
        return next(rows, None)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            rows.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader single-row close failed: ' + type(exc).__name__)


def _all(conn: sqlite3.Connection, sql: str, args: tuple = (), *,
         budget: _Budget | None) -> list:
    rows = _rows(conn, sql, args, budget=budget)
    primary = None
    try:
        _reserve(budget, 64)
        result = []
        for row in rows:
            _reserve(budget, 16)
            result.append(row)
        return result
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            rows.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader collection close failed: ' + type(exc).__name__)


def _raw_rows(conn: sqlite3.Connection, sql: str, args: tuple = (), *,
              budget: _Budget) -> Iterator[tuple]:
    """Existing raw native representation, with the same acquisition gates."""
    yield from _rows(conn, sql, args, budget=budget, _factory=None)


def _raw_one(conn: sqlite3.Connection, sql: str, args: tuple = (), *,
             budget: _Budget):
    rows = _raw_rows(conn, sql, args, budget=budget)
    primary = None
    try:
        return next(rows, None)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            rows.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('workflow reader raw single-row close failed: ' + type(exc).__name__)
