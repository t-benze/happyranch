import { describe, it, expect } from 'vitest';
import { selectInFlightResponders } from './inFlightResponders';
import type { ResponderStatusEntry, ThreadMessage } from '@/lib/api/types';

function msg(seq: number, responders: ResponderStatusEntry[]): ThreadMessage {
  return {
    seq,
    speaker: 'founder',
    kind: 'message',
    body_markdown: 'hi',
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: '2026-06-03T10:00:00Z',
    responder_status: responders,
  };
}

// A system-row message (task terminal / resumed divider) — the row a
// TASK_FOLLOWUP wake OR a system-row-anchored coalesced REPLY wake hangs off.
function systemMsg(seq: number, responders: ResponderStatusEntry[]): ThreadMessage {
  return {
    seq,
    speaker: 'agent_x',
    kind: 'system',
    body_markdown: null,
    decline_reason: null,
    system_payload: { kind_tag: 'task_completed', task_id: 'TASK-9' },
    attachments: [],
    created_at: '2026-06-03T10:00:00Z',
    responder_status: responders,
  };
}

const entry = (
  agent_name: string,
  status: ResponderStatusEntry['status'],
  purpose: ResponderStatusEntry['purpose'] = 'reply',
  started_at: string | null = null,
): ResponderStatusEntry => ({
  agent_name,
  purpose,
  status,
  responded_at: null,
  started_at,
  decline_reason: null,
  category: null,
});

describe('selectInFlightResponders', () => {
  it('returns only queued/working entries, deduped by (agent, purpose)', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', 'reply', '2026-06-03T10:00:00Z'), entry('bravo', 'replied')]),
      msg(2, [entry('charlie', 'queued')]),
    ]);
    expect(result.map((s) => s.agent_name).sort()).toEqual(['alpha', 'charlie']);
  });

  it('keeps a working turn even when a later message queues the same agent', () => {
    // alpha is working on seq 1; bravo posts seq 2, queuing alpha again.
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', 'reply', '2026-06-03T10:00:00Z')]),
      msg(2, [entry('alpha', 'queued', 'reply')]),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ agent_name: 'alpha', status: 'working' });
  });

  it('upgrades a queued agent to working when a later message reports working', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'queued', 'reply')]),
      msg(2, [entry('alpha', 'working', 'reply', '2026-06-03T10:01:00Z')]),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ agent_name: 'alpha', status: 'working' });
  });

  it('returns empty when nothing is in flight', () => {
    expect(
      selectInFlightResponders([msg(1, [entry('alpha', 'replied'), entry('bravo', 'declined')])]),
    ).toEqual([]);
  });

  it('classifies by WIRE purpose, never the triggering row kind', () => {
    // The SAME system row carries a REPLY wake (purpose='reply', the founder's
    // system-row-anchored coalesced range) AND a TASK_FOLLOWUP wake for the
    // same agent — the row kind cannot distinguish them, the wire purpose can.
    const result = selectInFlightResponders([
      systemMsg(2, [
        entry('alpha', 'working', 'reply', '2026-06-03T10:00:00Z'),
        entry('alpha', 'queued', 'task_followup'),
      ]),
    ]);
    expect(result).toHaveLength(2);
    expect(result.find((s) => s.purpose === 'reply')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
    expect(result.find((s) => s.purpose === 'task_followup')).toMatchObject({
      agent_name: 'alpha',
      status: 'queued',
    });
  });

  it('a message-row TASK_FOLLOWUP stays special despite the message kind', () => {
    // Regression inverse: a TASK_FOLLOWUP that happens to hang off a MESSAGE
    // row must still classify as task_followup (wire purpose wins over kind).
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', 'task_followup', '2026-06-03T10:00:00Z')]),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      agent_name: 'alpha',
      purpose: 'task_followup',
      status: 'working',
    });
  });

  it('dedupes within a purpose but keeps same-agent different-purpose rows separate', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', 'reply'), entry('alpha', 'queued', 'reply')]), // reply: working wins
      systemMsg(2, [entry('alpha', 'working', 'task_followup')]), // followup
      systemMsg(3, [entry('alpha', 'queued', 'task_followup')]), // followup, later queued — masked by working
    ]);
    expect(result).toHaveLength(2);
    expect(result.find((s) => s.purpose === 'reply')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
    expect(result.find((s) => s.purpose === 'task_followup')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
  });
});
