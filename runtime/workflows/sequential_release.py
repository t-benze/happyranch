"""Unmounted U9 release selection; package availability is not admission.

U9b must attach this selector and the legacy fresh-start fence atomically.
Neither function owns historical replay, settings, storage or startup writes.
"""
from __future__ import annotations

import hashlib
from importlib import resources
from typing import Literal, NoReturn

from runtime.workflows.sequential_contracts import DefinitionPin
from runtime.workflows.sequential_definition import CompiledDefinition, compile_definition


CODING_SOURCE_DIGEST = '5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065'
CODING_DEFINITION_DIGEST = 'a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c'
_SOURCE_SIZE = 3795


class SequentialReleaseUnavailable(ValueError):
    """Categorical source refusal, with no resource path or raw loader detail."""

    def __init__(self, code: Literal['sequential_release_unavailable',
                 'sequential_release_corrupt', 'sequential_release_pin_mismatch',
                 'coding_fresh_start_retired']) -> None:
        self.code = code
        super().__init__(code)


def read_coding_release() -> CompiledDefinition:
    """Read only the fixed reviewed v2 resource, in source/wheel/frozen loaders.

    The one presentation LF is removed explicitly. There is no path override,
    normalization, legacy release lookup, filesystem fallback or registration.
    """
    try:
        resource = resources.files('runtime').joinpath('workflows', 'builtin_templates', 'coding.v2.json')
        with resource.open('rb') as stream:
            raw = stream.read(_SOURCE_SIZE + 1)
    except (OSError, ImportError, NotImplementedError, TypeError, ValueError):
        raise SequentialReleaseUnavailable('sequential_release_unavailable') from None
    if (type(raw) is not bytes or len(raw) != _SOURCE_SIZE
            or hashlib.sha256(raw).hexdigest() != CODING_SOURCE_DIGEST
            or raw[-1:] != b'\n'):
        raise SequentialReleaseUnavailable('sequential_release_corrupt')
    try:
        compiled = compile_definition(raw[:-1], source_digest=CODING_SOURCE_DIGEST)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SequentialReleaseUnavailable('sequential_release_corrupt') from None
    if (compiled.pin.identity != 'release/happyranch/coding' or compiled.pin.version != 2
            or compiled.pin.definition_digest != CODING_DEFINITION_DIGEST):
        raise SequentialReleaseUnavailable('sequential_release_corrupt')
    return compiled


def select_coding_release(pin: DefinitionPin) -> CompiledDefinition:
    """Select the entire current v2/ABI pin, without admitting or loading a run.

    Historical readers must keep their original saved pins and never call this
    fresh-only selector. Storage, defaults, Allowed admission and mounted native
    execution remain separate requirements for the future public attachment.
    """
    compiled = read_coding_release()
    if type(pin) is not DefinitionPin or pin != compiled.pin:
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    return compiled


def refuse_legacy_fresh_start() -> NoReturn:
    """Future fresh-only fence, AFTER authenticated admitted replay selection.

    U9b must mount this at callback intake and consumption/inner admission before
    callback writes, native spending or graph allocation. It must never fence an
    admitted callback, settlement, cancellation or LOCAL Final under old pins.
    """
    raise SequentialReleaseUnavailable('coding_fresh_start_retired')
