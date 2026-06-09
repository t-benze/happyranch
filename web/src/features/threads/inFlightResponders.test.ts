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

const entry = (
  agent_name: string,
  status: ResponderStatusEntry['status'],
  started_at: string | null = null,
): ResponderStatusEntry => ({ agent_name, status, responded_at: null, started_at });

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
});
