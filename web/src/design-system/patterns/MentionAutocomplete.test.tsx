import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { AgentSummary } from '@/lib/api/agents';
import { MentionAutocomplete } from './MentionAutocomplete';

const AGENTS: AgentSummary[] = [
  { name: 'engineering_head', team: 'engineering', role: 'manager', executor: 'claude', description: null },
  { name: 'content_writer',   team: 'content',     role: 'worker',  executor: 'claude', description: null },
  { name: 'design_lead',      team: 'design',      role: 'manager', executor: 'claude', description: null },
];

const ANCHOR = { x: 100, y: 100, width: 200, height: 24 };

describe('MentionAutocomplete', () => {
  it('renders the provided matches', () => {
    const onSelect = vi.fn();
    const onDismiss = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        matches={[AGENTS[0]]}
        onSelect={onSelect}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
    expect(screen.queryByText('content_writer')).toBeNull();
  });

  it('Esc fires onDismiss', async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        matches={AGENTS}
        onSelect={vi.fn()}
        onDismiss={onDismiss}
      />,
    );
    await user.keyboard('{Escape}');
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('Enter selects the active item', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        matches={AGENTS}
        onSelect={onSelect}
        onDismiss={vi.fn()}
      />,
    );
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith(AGENTS[0]);
  });

  it('ArrowDown moves the active item then Enter selects it', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        matches={AGENTS}
        onSelect={onSelect}
        onDismiss={vi.fn()}
      />,
    );
    await user.keyboard('{ArrowDown}{Enter}');
    expect(onSelect).toHaveBeenCalledWith(AGENTS[1]);
  });
});
