import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ThreadActivityFooter } from './ThreadActivityFooter';
import type { ThreadMessage } from '@/lib/api/types';

const now = 1_000_000_000_000;
const ago = (s: number) => new Date(now - s * 1000).toISOString();

function msg(responders: ThreadMessage['responder_status']): ThreadMessage {
  return {
    seq: 1, speaker: 'founder', kind: 'message', body_markdown: 'hi',
    decline_reason: null, system_payload: null, created_at: ago(60),
    responder_status: responders,
  };
}

describe('ThreadActivityFooter', () => {
  it('renders nothing when no one is working', () => {
    const { container } = render(
      <ThreadActivityFooter
        messages={[msg([{ agent_name: 'a', status: 'queued', responded_at: null, started_at: null }])]}
        nowMs={now}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('lists working participants with elapsed', () => {
    render(
      <ThreadActivityFooter
        messages={[msg([{ agent_name: 'alpha', status: 'working', responded_at: null, started_at: ago(45) }])]}
        nowMs={now}
      />,
    );
    expect(screen.getByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/working on a reply/i)).toBeInTheDocument();
    expect(screen.getByText(/45s/)).toBeInTheDocument();
  });
});
