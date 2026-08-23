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

// A system-row message (task terminal) — the row a TASK_FOLLOWUP wake hangs
// off (GH-688 Phase 1: REPLY invocations hang off MESSAGE rows; TASK_FOLLOWUP
// hang off SYSTEM rows; disjoint by triggering-row kind).
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
  started_at: string | null = null,
): ResponderStatusEntry => ({
  agent_name,
  status,
  responded_at: null,
  started_at,
  decline_reason: null,
  category: null,
});

describe('selectInFlightResponders', () => {
  it('returns only queued/working entries, deduped by agent', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', '2026-06-03T10:00:00Z'), entry('bravo', 'replied')]),
      msg(2, [entry('charlie', 'queued')]),
    ]);
    expect(result.map((s) => s.agent_name).sort()).toEqual(['alpha', 'charlie']);
  });

  it('keeps a working turn even when a later message queues the same agent', () => {
    // alpha is working on seq 1; bravo posts seq 2, queuing alpha again.
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working', '2026-06-03T10:00:00Z')]),
      msg(2, [entry('alpha', 'queued')]),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ agent_name: 'alpha', status: 'working' });
  });

  it('upgrades a queued agent to working when a later message reports working', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'queued')]),
      msg(2, [entry('alpha', 'working', '2026-06-03T10:01:00Z')]),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ agent_name: 'alpha', status: 'working' });
  });

  it('returns empty when nothing is in flight', () => {
    expect(
      selectInFlightResponders([msg(1, [entry('alpha', 'replied'), entry('bravo', 'declined')])]),
    ).toEqual([]);
  });

  it('classifies a message-row wake as reply and a system-row wake as special', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working')]),
      systemMsg(2, [entry('alpha', 'queued')]),
    ]);
    // Same agent, DIFFERENT purposes — kept as separate rows (coexistence):
    // the conversational REPLY is owned by the store projection while the
    // special-purpose wake stays inferred.
    expect(result).toHaveLength(2);
    expect(result.find((s) => s.purpose === 'reply')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
    expect(result.find((s) => s.purpose === 'special')).toMatchObject({
      agent_name: 'alpha',
      status: 'queued',
    });
  });

  it('dedupes within a purpose but keeps same-agent different-purpose rows separate', () => {
    const result = selectInFlightResponders([
      msg(1, [entry('alpha', 'working'), entry('alpha', 'queued')]), // reply: working wins
      systemMsg(2, [entry('alpha', 'working')]), // special
      systemMsg(3, [entry('alpha', 'queued')]), // special, later queued — masked by working
    ]);
    expect(result).toHaveLength(2);
    expect(result.find((s) => s.purpose === 'reply')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
    expect(result.find((s) => s.purpose === 'special')).toMatchObject({
      agent_name: 'alpha',
      status: 'working',
    });
  });
});
