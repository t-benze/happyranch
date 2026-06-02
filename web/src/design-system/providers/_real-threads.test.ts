import { describe, it, expect } from 'vitest';
import { classifyTailEvent } from './_real-threads';

// Pins the routing the live "agent working on a reply" indicator depends on:
// the runner publishes seq-bearing invocation_started/settled events on the
// thread tail, and the consumer must invalidate the messages query so
// responder_status (queued/working/…) refetches. See thread_runner.py
// _publish_invocation_event + the spec (issue #53 follow-up).
describe('classifyTailEvent', () => {
  it("appends a full ThreadMessage (carries body_markdown, even when null)", () => {
    expect(classifyTailEvent({ seq: 3, body_markdown: 'hi' })).toBe('append');
    expect(classifyTailEvent({ seq: 3, body_markdown: null })).toBe('append');
  });

  it('invalidates for seq-bearing invocation lifecycle events', () => {
    expect(
      classifyTailEvent({ seq: 12, kind: 'invocation_started' } as never),
    ).toBe('invalidate');
    expect(
      classifyTailEvent({ seq: 12, kind: 'invocation_settled' } as never),
    ).toBe('invalidate');
  });

  it('ignores events without a seq (e.g. decline_status seq=null)', () => {
    expect(classifyTailEvent({ seq: null })).toBe('ignore');
    expect(classifyTailEvent({})).toBe('ignore');
  });
});
