# Top-level skills

`happyranch/` is the founder-facing CLI skill. Its `SKILL.md` documents the CLI,
and `scripts/happyranch` is the skill-local shim that resolves the project root
and invokes the command. `pyproject.toml` force-includes the skill instructions
as system knowledge for packaged system-assistant use.

This top-level skill is separate from the release-owned bundled session
instructions, managed `skill.yaml` catalog packages, and custom/catalog
machinery under [runtime/skills](../runtime/skills/README.md). It is not a
managed catalog package merely because it is named a skill.
