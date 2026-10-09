"""Ephemeral current-name presentation; canonical membership and proofs are external."""
from __future__ import annotations


def identity_display(metadata, canonical_id: str, *, kind: str = "agent") -> str:
    subject = (metadata or {}).get((kind, canonical_id))
    label = subject.current_label if subject is not None else None
    return f"{label} · {canonical_id}" if label else canonical_id


def prompt_name_context(metadata, agent_id: str, roster_ids=()) -> str:
    """Render one already validated read, never load files/DB for each label.

    Founder is a separate human, never a roster agent. No historical transcript
    is rewritten, and this current-turn block is neither a persisted snapshot
    nor authority. Callers select members by their existing canonical ID rules.
    """
    names = metadata or {}
    lines = ["## Current naming context (this turn)",
             f"Self: {identity_display(names, agent_id)}.",
             f"Founder (human): {identity_display(names, 'founder', kind='founder')}; "
             "recipient transport remains @founder, never an agent participant."]
    others = list(dict.fromkeys(value for value in roster_ids if value != "founder"))
    if others:
        lines.append("Current ID-selected participants: " + ", ".join(
            identity_display(names, value) for value in others) + ".")
    if not names:
        lines.append("Naming metadata unavailable; this turn uses permanent IDs only.")
    lines.append("Human destination arguments and @mentions may use current ASCII names or permanent IDs. "
                 "Former names do not forward; use the reported current name or ID. "
                 "Current names supersede remembered labels on resumed turns. "
                 "Automation is ID-only: callback actor/composer/speaker, task/session/invocation proofs, "
                 "delegate/then/fanout targets and configuration identities must remain permanent IDs. "
                 "Names confer no actor authority or permission.")
    return "\n".join(lines) + "\n\n"
