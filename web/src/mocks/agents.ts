/**
 * Mock agent roster. Used by the prototype Composer (mention popup) and
 * the Agents page prototype.
 */
import type { AgentSummary, AgentEnrollment } from '@/lib/api/agents';

export const MOCK_AGENTS: AgentSummary[] = [
  {
    name: 'engineering_head',
    team: 'engineering',
    role: 'manager',
    executor: 'claude',
    model: 'claude-sonnet-4-20250514',
    description: 'Owns the engineering team.',
    repos: { happyranch: 'https://github.com/org/happyranch' },
    system_prompt: 'You are the engineering head.',
  },
  {
    name: 'content_writer',
    team: 'content',
    role: 'worker',
    executor: 'claude',
    description: 'Drafts posts and marketing copy.',
    repos: {},
    system_prompt: '',
  },
  {
    name: 'support_agent',
    team: 'cx',
    role: 'worker',
    executor: 'codex',
    description: 'Handles first-line customer support.',
    repos: {},
    system_prompt: '',
  },
];

export const MOCK_ENROLLMENTS: AgentEnrollment[] = [
  {
    name: 'new_writer',
    team: 'content',
    role: 'worker',
    executor: 'codex',
    description: 'Drafts long-form blog posts.',
    status: 'pending',
    enrolled_by: 'content_manager',
    created_at: '2026-05-18T19:00:00Z',
  },
];
