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
    description: 'Owns the engineering team.',
  },
  {
    name: 'content_writer',
    team: 'content',
    role: 'worker',
    executor: 'claude',
    description: 'Drafts posts and marketing copy.',
  },
  {
    name: 'support_agent',
    team: 'cx',
    role: 'worker',
    executor: 'codex',
    description: 'Handles first-line customer support.',
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
