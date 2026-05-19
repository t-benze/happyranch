import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { HelpSheet, type ShortcutSection } from './HelpSheet';

const SECTIONS: ShortcutSection[] = [
  {
    label: 'Global',
    shortcuts: [
      { keys: ['?'], description: 'Show this help' },
      { keys: ['Cmd', 'K'], description: 'Open command palette' },
    ],
  },
  {
    label: 'Threads',
    shortcuts: [{ keys: ['N'], description: 'New thread' }],
  },
];

describe('HelpSheet', () => {
  it('renders the flat list when `shortcuts` is passed', () => {
    render(
      <HelpSheet
        open
        onClose={() => {}}
        shortcuts={[{ keys: ['N'], description: 'New thread' }]}
      />,
    );
    expect(screen.getByText('New thread')).toBeInTheDocument();
  });

  it('renders tabs when `sections` is passed', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    expect(screen.getByRole('tab', { name: 'Global' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Threads' })).toBeInTheDocument();
  });

  it('defaults to the first section', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    expect(screen.getByText('Show this help')).toBeInTheDocument();
    expect(screen.queryByText('New thread')).toBeNull();
  });

  it('honors `defaultTab`', () => {
    render(
      <HelpSheet open onClose={() => {}} sections={SECTIONS} defaultTab="Threads" />,
    );
    expect(screen.getByText('New thread')).toBeInTheDocument();
    expect(screen.queryByText('Show this help')).toBeNull();
  });

  it('switches tab on click', async () => {
    const user = userEvent.setup();
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    await user.click(screen.getByRole('tab', { name: 'Threads' }));
    expect(screen.getByText('New thread')).toBeInTheDocument();
  });

  it('skips empty sections when in tabbed mode', () => {
    render(
      <HelpSheet
        open
        onClose={() => {}}
        sections={[
          { label: 'Global', shortcuts: SECTIONS[0].shortcuts },
          { label: 'Empty', shortcuts: [] },
        ]}
      />,
    );
    expect(screen.queryByRole('tab', { name: 'Empty' })).toBeNull();
  });
});
