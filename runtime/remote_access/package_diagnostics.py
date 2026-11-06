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
LOADED_CONTEXT = ('Wants', 'Requires', 'After', 'Before', 'PartOf', 'Conflicts', 'RequiresMountsFor', 'BindsTo')
LOADED_LIMIT = 240
_LOADED_SAFE = re.compile(r"[A-Za-z0-9_./:@ \\\"'~-]*")
_LOADED_SENSITIVE = frozenset(('Environment', 'EnvironmentFiles', 'PassEnvironment', 'LoadCredential',
                             'LoadCredentialEncrypted', 'ExecStartEx', 'ExecStartPreEx', 'ExecStartPostEx',
                             'ExecStop', 'ExecStopPost'))


def loaded_projection(value: object) -> list:
    if (type(value) is not list or len(value) != 13 or type(value[0]) is not int or not 0 <= value[0] <= 2
            or type(value[1]) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}', value[1]) is None
            or value[2] not in ('equal', 'subset', 'after', 'peers', 'query')):
        raise ValueError('private_loaded_invalid')
    for item in value[3:]:
        if (type(item) is not str or not item or item[0] not in '=~!?'
                or item[0] in '!?' and len(item) != 1
                or _LOADED_SAFE.fullmatch(item[1:]) is None):
            raise ValueError('private_loaded_invalid')
    if value[1] in _LOADED_SENSITIVE and value[3:5] != ['!', '!']:
        raise ValueError('private_loaded_invalid')
    if len(json.dumps(value, ensure_ascii=True, separators=(',', ':'))) > LOADED_LIMIT:
        raise ValueError('private_loaded_invalid')
    return list(value)


def loaded_observation(unit: int, prop: str, relation: str, expected: str | None,
                       observed: str | None, context: dict[str, str]) -> list:
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
    # Drop longest retained prefixes first, marking EVERY truncated value.
    # Unit/property/relation and missing/withheld states cannot be evicted.
    while len(json.dumps(result, ensure_ascii=True, separators=(',', ':'))) > LOADED_LIMIT:
        index = max(range(3, 13), key=lambda i: len(result[i]))
        result[index] = '~' + result[index][1:-1]
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
