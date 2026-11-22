"""Fresh-only bundled selection; package availability is not admission.

Public manifest selection and the legacy fresh-start fence are mounted together.
This owner does not write historical replay, settings, storage or startup state.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from typing import Literal, NoReturn

from runtime.workflows.sequential_contracts import DEFINITION_LIMIT, DefinitionPin, GenericDefinitionPin
from runtime.workflows.sequential_definition import (
    CompiledDefinition, GenericCompiledDefinition, compile_definition, compile_generic_definition,
)


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


# Literal reviewed generic catalog: registration identity/pins only.
# Public Start@1 discovery and the legacy compatibility view remain separate.
@dataclass(frozen=True)
class StagedGenericDefinition:
    package: str
    resource: tuple[str, ...]
    size: int
    pin: GenericDefinitionPin


STAGED_GENERIC = {
    ('release/happyranch/coding', 3): StagedGenericDefinition(
        package='runtime', resource=('workflows', 'builtin_templates', 'coding.v3.json'), size=5327,
        pin=GenericDefinitionPin(identity='release/happyranch/coding', version=3,
            source_digest='ee1e8f6d3d8117c8c0f9b1b551c166e58944ae3ef137e89b68b30f6fd08f7c36',
            definition_digest='65b1045adf4f204f918a70a281b5a3c8cae7eaa7d9b603a3e7f3bd6242581dae',
            compiler='workflow-sequential-compiler@2', validator='workflow-sequential-validator@2',
            interpreter='workflow-sequential-interpreter@2', capabilities='workflow-sequential-capabilities@2')),
    ('release/happyranch/draft-review', 1): StagedGenericDefinition(
        package='runtime', resource=('workflows', 'builtin_templates', 'draft-review.v1.json'), size=2471,
        pin=GenericDefinitionPin(identity='release/happyranch/draft-review', version=1,
            source_digest='7e89d8ce22c1b7a8e462911fabce17845f67b298a7288b81c94b02831c7afcf2',
            definition_digest='f56b57b3ef688a2e1130cf970c98757033bcb2230dc2885e633f1e43ac7c1a65',
            compiler='workflow-sequential-compiler@2', validator='workflow-sequential-validator@2',
            interpreter='workflow-sequential-interpreter@2', capabilities='workflow-sequential-capabilities@2')),
}


# Keep the S8a registrar import stable; neither name grants public Start@2.
GENERIC_CATALOG = STAGED_GENERIC


def generic_start_catalog(selector: str | None = None) -> tuple[GenericCompiledDefinition, ...]:
    """Reviewed @2 discovery/composer data, without granting public admission.

    The registrar and composer share exact package/pin verification. Keep the
    original @1 manifest and selector independent of this additive catalog.
    """
    if not 0 < len(GENERIC_CATALOG) <= 16:
        raise SequentialReleaseUnavailable('sequential_release_unavailable')
    entries = tuple(entry for entry in GENERIC_CATALOG.values()
        if selector is None or selector == f'{entry.pin.identity}@{entry.pin.version}')
    if not entries:
        raise SequentialReleaseUnavailable('sequential_release_unavailable')
    return tuple(select_staged_release(entry.pin) for entry in entries)


class SequentialReleaseUnavailable(ValueError):
    """Categorical source refusal, with no resource path or raw loader detail."""

    def __init__(self, code: Literal['sequential_release_unavailable',
                 'sequential_release_corrupt', 'sequential_release_pin_mismatch',
                 'coding_fresh_start_retired', 'sequential_domain_transport_unavailable']) -> None:
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


def staged_entry(pin: GenericDefinitionPin) -> StagedGenericDefinition:
    """Validate staged @2 provenance without widening the public @1 manifest."""
    if type(pin) is not GenericDefinitionPin or (pin.identity, pin.version) not in STAGED_GENERIC:
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    entry = STAGED_GENERIC[(pin.identity, pin.version)]
    if (entry.package != 'runtime' or len(entry.resource) != 3
            or entry.resource[:2] != ('workflows', 'builtin_templates')
            or not entry.resource[2].endswith('.json')
            or any(part in ('', '.', '..') or '/' in part or '\\' in part for part in entry.resource)
            or type(entry.size) is not int or not 0 < entry.size <= DEFINITION_LIMIT + 1
            or entry.pin != pin):
        raise SequentialReleaseUnavailable('sequential_release_pin_mismatch')
    return entry


def select_staged_release(pin: GenericDefinitionPin) -> GenericCompiledDefinition:
    """Read/compile reviewed @2 registration data; grants no fresh-start route."""
    entry = staged_entry(pin)
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
        compiled = compile_generic_definition(raw[:-1], source_digest=entry.pin.source_digest)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SequentialReleaseUnavailable('sequential_release_corrupt') from None
    if compiled.pin != entry.pin:
        raise SequentialReleaseUnavailable('sequential_release_corrupt')
    return compiled


def read_coding_release() -> CompiledDefinition:
    """Compatibility accessor for existing Coding setup callers."""
    return select_bundled_release(MANIFEST[('release/happyranch/coding', 2)].pin)


def read_generic_coding_release() -> GenericCompiledDefinition:
    """Coding's generic registration pin; never a public start selector."""
    return select_staged_release(GENERIC_CATALOG[('release/happyranch/coding', 3)].pin)


def require_legacy_domain_selection(definition_digest: str) -> None:
    """Refuse a generic catalog pin before new local engineering effects.

    Original @1 records keep their parsers and uncertain journal bytes. The
    daemon independently classifies every selected run by engine version.
    """
    if any(entry.pin.definition_digest == definition_digest for entry in GENERIC_CATALOG.values()):
        raise SequentialReleaseUnavailable('sequential_domain_transport_unavailable')


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
