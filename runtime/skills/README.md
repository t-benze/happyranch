# Runtime skills

This directory contains three distinct skill surfaces:

- `bundled/` is the release-owned source for bundled instructions and their
  supporting assets. `system_contracts.py` selects the mandatory,
  context-specific session contracts from that source. System contracts are
  outside the toggleable managed catalog; do not infer that every bundled
  package is toggleable.
- Direct child directories `manage-agent/`, `manage-repo/`, and `reflection/`
  contain catalog metadata in `skill.yaml`. Their `source` values select the
  corresponding `bundled/<slug>/` instruction packages. Registry validation,
  workspace publication, and release recovery use that same bundled content;
  the manifest is not part of its content hash. There is no sibling `SKILL.md`
  fallback. `registry.py` excludes packages classified as system contracts.
  Generic fixture and user-authored packages retain their content beside the
  manifest; arbitrary `source` strings are not filesystem redirects.
- `custom/` and the adjacent Python modules implement custom/catalog
  resolution, persistence, validation, eligibility, exposure, canonical-store
  publication, and workspace materialization. They are runtime machinery, not
  another bundle of agent instructions.

The separate [top-level skills directory](../../skills/README.md) documents the
founder-facing CLI skill.
