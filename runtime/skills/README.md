# Runtime skills

This directory contains three distinct skill surfaces:

- `bundled/` is the release-owned source for bundled instructions and their
  supporting assets. `system_contracts.py` selects the mandatory,
  context-specific session contracts from that source. System contracts are
  outside the toggleable managed catalog; do not infer that every bundled
  package is toggleable.
- Direct child packages such as `manage-agent/`, `manage-repo/`, and
  `reflection/` contain `skill.yaml` plus `SKILL.md`. `registry.py` loads these
  as managed catalog entries and excludes packages classified as system
  contracts.
- `custom/` and the adjacent Python modules implement custom/catalog
  resolution, persistence, validation, eligibility, exposure, canonical-store
  publication, and workspace materialization. They are runtime machinery, not
  another bundle of agent instructions.

The separate [top-level skills directory](../../skills/README.md) documents the
founder-facing CLI skill.
