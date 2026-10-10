# Top-level skills

`happyranch/` is the founder-facing CLI skill. Its `SKILL.md` documents the CLI,
and `scripts/happyranch` is the skill-local shim that resolves the project root
and invokes the command. The former packaged System Assistant knowledge copy
is retired under THR-294; this founder-facing CLI skill remains available.

This top-level skill is separate from the release-owned bundled session
instructions, managed `skill.yaml` catalog packages, and custom/catalog
machinery under [runtime/skills](../runtime/skills/README.md). It is not a
managed catalog package merely because it is named a skill.
