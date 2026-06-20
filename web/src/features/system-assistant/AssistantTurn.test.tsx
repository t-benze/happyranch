/**
 * Unit tests for AssistantTurn — ran: card detection and stripping.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { AssistantTurn, type AssistantMessage } from './AssistantTurn';

function makeMsg(text: string, role: 'assistant' | 'user' = 'assistant'): AssistantMessage {
  return { role, text, timestamp: '2026-06-19T12:00:00Z' };
}

describe('AssistantTurn — ran: cards', () => {
  test('single ran: line is detected and stripped', () => {
    const msg = makeMsg('ran: echo hello\nSome output here');
    render(<AssistantTurn message={msg} />);

    // ran: card is rendered with its aria-label.
    expect(screen.getByLabelText('ran: echo hello')).toBeInTheDocument();
    // The message body should contain the output but not the ran: line.
    // (Markdown renderer uses its own container, so check for the output text.)
    expect(screen.getByText(/Some output here/)).toBeInTheDocument();
  });

  test('multiple ran: lines are all detected and stripped', () => {
    const msg = makeMsg(
      'ran: cmd1 --flag\nSome output\nran: cmd2 --other-flag\nMore output\nran: cmd3'
    );
    render(<AssistantTurn message={msg} />);

    // All three ran: cards are rendered.
    expect(screen.getByLabelText('ran: cmd1 --flag')).toBeInTheDocument();
    expect(screen.getByLabelText('ran: cmd2 --other-flag')).toBeInTheDocument();
    expect(screen.getByLabelText('ran: cmd3')).toBeInTheDocument();

    // The non-ran text should appear in the body (Markdown splits newlines).
    expect(screen.getByText(/Some output/)).toBeInTheDocument();
    expect(screen.getByText(/More output/)).toBeInTheDocument();
  });

  test('no ran: lines — all text is in the message body', () => {
    const msg = makeMsg('Just some output\nWith multiple lines');
    render(<AssistantTurn message={msg} />);

    expect(screen.getByText(/Just some output/)).toBeInTheDocument();
    expect(screen.getByText(/With multiple lines/)).toBeInTheDocument();
    // No ran: cards.
    expect(screen.queryByLabelText(/^ran:/)).toBeNull();
  });

  test('user messages never show ran: cards', () => {
    const msg = makeMsg('ran: something', 'user');
    render(<AssistantTurn message={msg} />);

    // User messages don't scan for ran:.
    expect(screen.queryByLabelText(/^ran:/)).toBeNull();
  });
});
