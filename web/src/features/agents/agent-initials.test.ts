import { describe, expect, test } from 'vitest';
import { agentInitials } from './agent-initials';

describe('agentInitials', () => {
  test('takes the first letter of the first two underscore segments', () => {
    expect(agentInitials('engineering_manager')).toBe('EM');
    expect(agentInitials('code_reviewer')).toBe('CR');
    expect(agentInitials('dev_agent')).toBe('DA');
    expect(agentInitials('qa_engineer')).toBe('QE');
  });

  test('handles hyphen and whitespace separators', () => {
    expect(agentInitials('content-ops')).toBe('CO');
    expect(agentInitials('support team')).toBe('ST');
  });

  test('falls back to the first two letters of a single segment', () => {
    expect(agentInitials('founder')).toBe('FO');
    expect(agentInitials('qa')).toBe('QA');
  });

  test('ignores empty/separator-only fragments', () => {
    expect(agentInitials('__dev__agent__')).toBe('DA');
  });

  test('degrades gracefully on an empty name', () => {
    expect(agentInitials('')).toBe('?');
  });
});
