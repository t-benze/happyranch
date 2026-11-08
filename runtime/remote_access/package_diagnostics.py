"""Private literal refusal identity. Never serialize exception text or operands."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
import json
import re
import sys

PRIVATE_DIAGNOSTIC = ContextVar('private_package_loss_writer', default=False)


def diagnostic_loss(site: str, category: str, error: BaseException) -> None:
    """Observe a suppressed error without changing its caller's disposition."""
    origin = exception_refusal(error)
    diagnostic_error(site, category, error)
    observed = [origin[1], site] if origin is not None and origin[1] != site else [site]
    module = sys.modules.get('runtime.remote_access.linux_package')
    owner = getattr(module, '_MANAGED_PACKAGE_DIAGNOSTIC', None)
    record = owner.get() if owner is not None else None
    if record is not None:
        sites = record.setdefault('loss_sites', [])
        for value in observed:
            if value not in sites and len(sites) < 4:
                sites.append(value)
        record['reason'], record['diagnostic_state'] = None, 'unavailable'
    if PRIVATE_DIAGNOSTIC.get():
        try:
            # One bounded fixed frame. Lost/short writes cannot certify delivery.
            os.write(2, b'managed_diagnostic_loss:' + ':'.join(observed).encode('ascii') + b'\n')
        except OSError:
            pass


def exception_guard(error: BaseException) -> object:
    # Only this exception or an explicit producer cause owns these guard facts.
    # A handled error's implicit context can belong to an earlier operation.
    seen = set()
    for _ in range(16):
        if id(error) in seen:
            break
        seen.add(id(error))
        if hasattr(error, '_managed_package_guard'):
            return error._managed_package_guard
        error = error.__cause__
        if error is None:
            break
    return None


def exception_refusal(error: BaseException) -> tuple[str, str] | None:
    """Read validated SAME-exception/private-transfer or explicit-cause origins.

    Nested scopes rethrow the same exception, so its own mark survives. Returned
    validator errors and native guards use diagnostic_propagate to transfer a
    validated origin onto the actual caller exception. An explicit cause also
    identifies a producer relationship. Implicit context alone does not: fresh
    recovery I/O errors inherit the already handled initiating failure there.
    Never change those public chains to select a private diagnostic identity.
    """
    result = None
    seen = set()
    for _ in range(16):
        if id(error) in seen:
            return None
        seen.add(id(error))
        value = getattr(error, '_managed_package_refusal', None)
        if type(value) is tuple and len(value) == 2:
            from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
            category, site = value
            if type(site) is str and SITE_CATEGORIES.get(site) == category:
                result = (category, site)
                if getattr(error, '_managed_package_propagated_refusal', False) is True:
                    return result
        cause = error.__cause__
        if cause is None:
            return result
        error = cause
    return None


def diagnostic_error(site: str, category: str, error: BaseException) -> BaseException:
    # Preserve exact exception type/message/cause and public error behavior.
    # The only callers supply source literals; no operand enters the attribute.
    if isinstance(error, type) and issubclass(error, BaseException):
        error = error()
    if exception_refusal(error) is None:
        error._managed_package_refusal = (category, site)
    return error


@contextmanager
def diagnostic_scope(site: str, category: str, *, boundary: bool = False):
    """Attribute a reached operational exception at its literal source boundary."""
    try:
        yield
    except Exception as error:
        if boundary and exception_refusal(error) is not None:
            error._managed_package_boundary = (category, site)
        diagnostic_error(site, category, error)
        raise


def diagnostic_propagate(value: object, error: BaseException) -> None:
    """Carry an already validated native origin across the Python guard wrapper."""
    from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
    if type(value) is not dict or set(value) not in ({'category', 'site'}, {'category', 'site', 'loaded'}):
        return
    category, site = value['category'], value['site']
    if type(site) is not str or SITE_CATEGORIES.get(site) != category:
        return
    prior = getattr(error, '_managed_package_refusal', None)
    if prior is not None and prior != (category, site):
        error._managed_package_boundary = prior
    error._managed_package_refusal = (category, site)
    error._managed_package_propagated_refusal = True
    if 'loaded' in value:
        error._managed_package_loaded = loaded_projection(value['loaded'])


# Private loaded-property facts, positional to fit the EXISTING frame budgets.
# [unit index, property, relation, expected, observed, Wants, Requires, After,
#  Before, PartOf, Conflicts, RequiresMountsFor, BindsTo]. Values are tagged:
# '=' complete, '~' truncated prefix, '!' unsafe/sensitive withheld, '?' missing.
# Version2: [2, unit, property, relation, missing, unexpected, expected, observed,
# *contexts]. Deltas are tagged count:sorted-members. Counts survive reduction;
# context prefixes, then expected/observed, give way before decisive deltas.
LOADED_CONTEXT = ('Wants', 'Requires', 'After', 'Before', 'PartOf', 'Conflicts', 'RequiresMountsFor', 'BindsTo')
LOADED_LIMIT = 240
_LOADED_SAFE = re.compile(r"[A-Za-z0-9_./:@ \\\"'~-]*")
_LOADED_SENSITIVE = frozenset(('Environment', 'EnvironmentFiles', 'PassEnvironment', 'LoadCredential',
                             'LoadCredentialEncrypted', 'ExecStartEx', 'ExecStartPreEx', 'ExecStartPostEx',
                             'ExecStop', 'ExecStopPost'))


def dependency_refusal_stderr(code: str, unit: str, prop: str, expected: str | None,
                              observed: str | None, context: dict[str, str], *,
                              missing: set[str] | None, unexpected: set[str] | None) -> None:
    """Add full SAME-observation dependency facts; keep bounded receipts unchanged."""
    # Only these refusal sites and dependency properties can disclose values.
    # ascii() escapes control characters without clipping any unit/property text.
    if code not in ("P000261", "P000262") or prop not in LOADED_CONTEXT + (
            "DefaultDependencies", "StopWhenUnneeded", "RefuseManualStart", "RefuseManualStop"):
        return
    facts = {"expected": expected, "observed": observed,
             "missing": None if missing is None else " ".join(sorted(missing)),
             "unexpected": None if unexpected is None else " ".join(sorted(unexpected))}
    facts.update((name, context[name]) for name in LOADED_CONTEXT if name in context)
    try:
        for name, value in facts.items():
            sys.stderr.write(f"{code} {unit} {prop} {name}={ascii(value)}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        # A diagnostic sink failure must not replace the original refusal.
        pass


def loaded_projection(value: object) -> list:
    if type(value) is not list or len(value) not in (13, 16):
        raise ValueError('private_loaded_invalid')
    offset = 1 if len(value) == 16 else 0
    if (offset and (type(value[0]) is not int or value[0] != 2)
            or type(value[offset]) is not int or not 0 <= value[offset] <= 2
            or type(value[offset + 1]) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}', value[offset + 1]) is None
            or type(value[offset + 2]) is not str or value[offset + 2] not in ('equal', 'subset', 'after', 'peers', 'query')):
        raise ValueError('private_loaded_invalid')
    for item in value[offset + 3:]:
        if (type(item) is not str or not item or item[0] not in '=~!?'
                or item[0] in '!?' and len(item) != 1
                or _LOADED_SAFE.fullmatch(item[1:]) is None):
            raise ValueError('private_loaded_invalid')
    if offset:
        if value[2] not in LOADED_CONTEXT or value[3] == 'query':
            raise ValueError('private_loaded_invalid')
        for item in value[4:6]:
            if item not in ('!', '?') and re.fullmatch(r'[=~](0|[1-9][0-9]{0,4}):[A-Za-z0-9_./:@ \\\"\'~-]*', item) is None:
                raise ValueError('private_loaded_invalid')
    if value[offset + 1] in _LOADED_SENSITIVE and value[3:5] != ['!', '!']:
        raise ValueError('private_loaded_invalid')
    if len(json.dumps(value, ensure_ascii=True, separators=(',', ':'))) > LOADED_LIMIT:
        raise ValueError('private_loaded_invalid')
    return list(value)


def loaded_shrink(value: list) -> bool:
    # Called only on constructed/validated facts. Keep the same priority in Go.
    groups = (range(8, 16), range(6, 8), range(4, 6)) if len(value) == 16 else (range(3, 13),)
    for group in groups:
        candidates = [i for i in group if value[i][0] in '=~' and len(value[i]) >
                      (value[i].index(':') + 1 if len(value) == 16 and i in (4, 5) else 1)]
        if candidates:
            index = max(candidates, key=lambda i: len(value[i]))
            value[index] = '~' + value[index][1:-1]
            return True
    return False


def loaded_observation(unit: int, prop: str, relation: str, expected: str | None,
                       observed: str | None, context: dict[str, str], *,
                       missing: set[str] | None = None, unexpected: set[str] | None = None) -> list:
    def safe(value):
        if value is None:
            return '?'
        if type(value) is not str or _LOADED_SAFE.fullmatch(value) is None:
            return '!'
        return ('=' if len(value) <= 96 else '~') + value[:96]
    values = [safe(expected), safe(observed), *[safe(context.get(k)) for k in LOADED_CONTEXT]]
    if prop in _LOADED_SENSITIVE:
        values[:2] = ['!', '!']
    result = [unit, prop, relation, *values]
    if missing is not None and unexpected is not None:
        def delta(members):
            return safe(str(len(members)) + ':' + ' '.join(sorted(members)))
        result = [2, unit, prop, relation, delta(missing), delta(unexpected), *values]
    while len(json.dumps(result, ensure_ascii=True, separators=(',', ':'))) > LOADED_LIMIT:
        if not loaded_shrink(result):
            raise ValueError('private_loaded_invalid')
    return loaded_projection(result)


def exception_loaded(error: BaseException) -> list | None:
    seen = set()
    for _ in range(16):
        if id(error) in seen:
            break
        seen.add(id(error))
        value = getattr(error, '_managed_package_loaded', None)
        if value is not None:
            return loaded_projection(value)
        error = error.__cause__
        if error is None:
            break
    return None
