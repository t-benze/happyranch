"""Working-hours wake invocations.

Leg A provides the pure, unit-testable prompt composition (``build_wake_prompt``),
mirroring ``dream_runner.build_dream_prompt``. The wake prompt is composed HERE
in the daemon runner — no ``protocol/`` edit is needed to ship the mechanism.
The executor invocation, token-usage recording (``scope_type="work_hour"``),
status transitions, and the ``wake_worker_loop`` are wired in leg B.
"""
from __future__ import annotations


def build_wake_prompt(
    *,
    org_slug: str,
    work_hour_id: str,
    agent_name: str,
    role: str,
    team: str,
    local_date: str,
    slot: str,
    mode: str,
    preamble: str,
    routines: list[str],
) -> str:
    """Compose the wake-session prompt.

    The wake is a TRIGGER, not the work: the session's only job is to translate
    each routine list item into one concrete root-task brief and submit them in
    a SINGLE ``work-hours spawn --from-file`` callback. The parsed
    ``## Routine Tasks`` section (preamble + list) is injected verbatim, and the
    cadence (local_date, slot, mode) is stated so briefs can be phrased relative
    to the last wake.
    """
    routine_block = "\n".join(routines) if routines else "(none)"
    preamble_block = f"{preamble}\n\n" if preamble else ""
    return f"""# Working-Hours Wake

You are {agent_name} ({role}) on the {team} team in HappyRanch org `{org_slug}`.
This is a WORKING-HOURS WAKE: a scheduled trigger to launch your standing
routines. It is NOT the work itself, and it is NOT a reflection. The real work
happens in the root tasks you spawn — do not perform the routines here.

Cadence: local_date {local_date}, slot {slot}, mode {mode}.

Turn EACH routine below into ONE concrete root-task brief (phrased for the work
due since the last wake at this cadence), then submit them ALL in a SINGLE
callback:

happyranch work-hours spawn --org {org_slug} --work-hour-id {work_hour_id} --from-file /tmp/wake-{work_hour_id}.json

Do not call create_task directly and do not dispatch other agents: the spawn
endpoint creates the root tasks on your own team, targeted to you as executor.

## Routine Tasks (verbatim from your agent file)

{preamble_block}{routine_block}
"""
