"""Fresh-only bundled selection; package availability is not admission.

Public manifest selection and the legacy fresh-start fence are mounted together.
This owner does not write historical replay, settings, storage or startup state.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from typing import Literal, NoReturn

from runtime.workflows.sequential_contracts import DEFINITION_LIMIT, DefinitionPin
from runtime.workflows.sequential_definition import CompiledDefinition, compile_definition


CODING_SOURCE_DIGEST = '5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065'
CODING_DEFINITION_DIGEST = 'a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c'


@dataclass(frozen=True)
class BundledDefinition:
    package: str
    resource: tuple[str, ...]
    size: int
    pin: DefinitionPin


# Reviewed package data only; neither discovery nor a caller path adds entries.
MANIFEST = {
    ('release/happyranch/coding', 2): BundledDefinition(
        package='runtime', resource=('workflows', 'builtin_templates', 'coding.v2.json'), size=3795,
        pin=DefinitionPin(identity='release/happyranch/coding', version=2,
            source_digest=CODING_SOURCE_DIGEST, definition_digest=CODING_DEFINITION_DIGEST,
            compiler='workflow-sequential-compiler@1', validator='workflow-sequential-validator@1',
            interpreter='workflow-sequential-interpreter@1', capabilities='workflow-sequential-capabilities@1')),
}
_RETIRED = frozenset({('release/happyranch/coding', 1)})


class SequentialReleaseUnavailable(ValueError):
    """Categorical source refusal, with no resource path or raw loader detail."""

    def __init__(self, code: Literal['sequential_release_unavailable',
                 'sequential_release_corrupt', 'sequential_release_pin_mismatch',
                 'coding_fresh_start_retired']) -> None:
        self.code = code
        super().__init__(code)


def is_bundled_definition(pin: DefinitionPin) -> bool:
    """Classify fresh pins; known releases cannot divert into the fixture lane."""
    key = (pin.identity, pin.version)
    if key in _RETIRED:
        refuse_legacy_fresh_start()
    if key not in MANIFEST and any(identity == pin.identity for identity, _ in MANIFEST):
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    return key in MANIFEST


def manifest_entry(pin: DefinitionPin) -> BundledDefinition:
    """Validate literal package provenance and the complete pin without I/O."""
    if type(pin) is not DefinitionPin or not is_bundled_definition(pin):
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    entry = MANIFEST[(pin.identity, pin.version)]
    if (entry.package != 'runtime' or len(entry.resource) != 3
            or entry.resource[:2] != ('workflows', 'builtin_templates')
            or not entry.resource[2].endswith('.json')
            or any(part in ('', '.', '..') or '/' in part or '\\' in part for part in entry.resource)
            or type(entry.size) is not int or not 0 < entry.size <= DEFINITION_LIMIT + 1
            or entry.pin != pin):
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    return entry


def select_bundled_release(pin: DefinitionPin) -> CompiledDefinition:
    """Re-read a reviewed resource; package availability grants no admission.

    Historical readers retain their original pins and never use this selector.
    One presentation LF is removed; no path override or filesystem fallback exists.
    """
    entry = manifest_entry(pin)
    try:
        resource = resources.files(entry.package).joinpath(*entry.resource)
        with resource.open('rb') as stream:
            raw = stream.read(entry.size + 1)
    except (OSError, ImportError, NotImplementedError, TypeError, ValueError):
        raise SequentialReleaseUnavailable('sequential_release_unavailable') from None
    if (type(raw) is not bytes or len(raw) != entry.size
            or hashlib.sha256(raw).hexdigest() != entry.pin.source_digest
            or raw[-1:] != b'\n'):
        raise SequentialReleaseUnavailable('sequential_release_corrupt')
    try:
        compiled = compile_definition(raw[:-1], source_digest=entry.pin.source_digest)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SequentialReleaseUnavailable('sequential_release_corrupt') from None
    if compiled.pin != entry.pin:
        raise SequentialReleaseUnavailable('sequential_release_corrupt')
    return compiled


def read_coding_release() -> CompiledDefinition:
    """Compatibility accessor for existing Coding setup callers."""
    return select_bundled_release(MANIFEST[('release/happyranch/coding', 2)].pin)


def select_coding_release(pin: DefinitionPin) -> CompiledDefinition:
    """Compatibility accessor; public selection uses the generic manifest."""
    return select_bundled_release(pin)


def refuse_legacy_fresh_start() -> NoReturn:
    """Fresh-only fence, AFTER authenticated admitted replay selection.

    U9b mounts this at callback intake and consumption/inner admission before
    callback writes, native spending or graph allocation. It must never fence an
    admitted callback, settlement, cancellation or LOCAL Final under old pins.
    """
    raise SequentialReleaseUnavailable('coding_fresh_start_retired')
