import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { AgentSummary } from '@/lib/api/agents';
import { RecipientsInput } from './RecipientsInput';

const AGENTS: AgentSummary[] = [
  { name: 'dev_agent',        team: 'engineering', role: 'worker',  executor: 'claude', description: null, repos: {}, system_prompt: '' },
  { name: 'qa_engineer',      team: 'engineering', role: 'worker',  executor: 'claude', description: null, repos: {}, system_prompt: '' },
  { name: 'engineering_head', team: 'engineering', role: 'manager', executor: 'claude', description: null, repos: {}, system_prompt: '' },
];

/** Controlled wrapper so typing actually updates the value prop. */
function ControlledInput({
  initialValue = '',
  ...rest
}: Omit<React.ComponentProps<typeof RecipientsInput>, 'value' | 'onChange'> & { initialValue?: string }) {
  const [value, setValue] = useState(initialValue);
  return <RecipientsInput value={value} onChange={setValue} {...rest} />;
}

describe('RecipientsInput', () => {
  it('renders an input with the provided value', () => {
    render(
      <ControlledInput
        initialValue="dev_agent"
        agents={AGENTS}
        placeholder="add agents…"
      />,
    );
    const input = screen.getByPlaceholderText('add agents…');
    expect(input).toHaveValue('dev_agent');
  });

  it('does NOT show autocomplete popup when input is focused with empty value', async () => {
    const user = userEvent.setup();
    render(
      <ControlledInput
        agents={AGENTS}
        placeholder="add agents…"
      />,
    );
    const input = screen.getByPlaceholderText('add agents…');
    await user.click(input);
    // Wait a tick for the state update — the popup should NOT appear
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).toBeNull();
    });
  });

  it('shows autocomplete popup when typing a non-empty query', async () => {
    const user = userEvent.setup();
    render(
      <ControlledInput
        agents={AGENTS}
        placeholder="add agents…"
      />,
    );
    const input = screen.getByPlaceholderText('add agents…');
    await user.click(input);
    await user.type(input, 'dev');
    await waitFor(() => {
      expect(screen.getByRole('listbox')).toBeInTheDocument();
    });
    // The matching agent should be visible in the popup
    expect(screen.getByText('dev_agent')).toBeInTheDocument();
  });

  it('does NOT show popup after a comma when the new token is empty', async () => {
    const user = userEvent.setup();
    render(
      <ControlledInput
        agents={AGENTS}
        placeholder="add agents…"
      />,
    );
    const input = screen.getByPlaceholderText('add agents…');
    await user.click(input);
    await user.type(input, 'dev_agent,');
    // After the comma, the new token query is empty — no popup
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).toBeNull();
    });
  });
});
