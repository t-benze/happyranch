"""Private literal refusal identity. Never serialize exception text or operands."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
import sys

PRIVATE_DIAGNOSTIC = ContextVar('private_package_loss_writer', default=False)


def diagnostic_loss(site: str, category: str, error: BaseException) -> None:
    """Observe a suppressed error without changing its caller's disposition."""
    diagnostic_error(site, category, error)
    module = sys.modules.get('runtime.remote_access.linux_package')
    owner = getattr(module, '_MANAGED_PACKAGE_DIAGNOSTIC', None)
    record = owner.get() if owner is not None else None
    if record is not None:
        sites = record.setdefault('loss_sites', [])
        if site not in sites and len(sites) < 4:
            sites.append(site)
        record['reason'], record['diagnostic_state'] = None, 'unavailable'
    if PRIVATE_DIAGNOSTIC.get():
        try:
            # One bounded fixed frame. Lost/short writes cannot certify delivery.
            os.write(2, b'managed_diagnostic_loss:' + site.encode('ascii') + b'\n')
        except OSError:
            pass


def exception_guard(error: BaseException) -> object:
    seen = set()
    for _ in range(16):
        if id(error) in seen:
            break
        seen.add(id(error))
        if hasattr(error, '_managed_package_guard'):
            return error._managed_package_guard
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        if error is None:
            break
    return None


def exception_refusal(error: BaseException) -> tuple[str, str] | None:
    """Retain the deepest explicitly chained marked origin, with finite traversal."""
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
        cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
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
def diagnostic_scope(site: str, category: str):
    """Attribute a reached operational exception at its literal source boundary."""
    try:
        yield
    except Exception as error:
        diagnostic_error(site, category, error)
        raise


def diagnostic_propagate(value: object, error: BaseException) -> None:
    """Carry an already validated native origin across the Python guard wrapper."""
    from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
    if type(value) is not dict or set(value) != {'category', 'site'}:
        return
    category, site = value['category'], value['site']
    if type(site) is not str or SITE_CATEGORIES.get(site) != category:
        return
    prior = getattr(error, '_managed_package_refusal', None)
    if prior is not None and prior != (category, site):
        error._managed_package_boundary = prior
    error._managed_package_refusal = (category, site)
    error._managed_package_propagated_refusal = True
