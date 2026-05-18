/**
 * Mock agent roster. Populated with two entries so the prototype's
 * Composer mention popup has something to render.
 */
import type { AgentSummary } from '@/lib/api/agents';

export const MOCK_AGENTS: AgentSummary[] = [
  {
    name: 'engineering_head',
    team: 'engineering',
    role: 'manager',
    executor: 'claude',
    tier: 'green',
    description: 'Owns the engineering team.',
  },
  {
    name: 'content_writer',
    team: 'content',
    role: 'worker',
    executor: 'claude',
    tier: 'green',
    description: 'Drafts posts and marketing copy.',
  },
];
