/**
 * Mock agent roster. Used by the prototype Composer (mention popup) and
 * the Agents page prototype. Three agents give the table a visible mix
 * of tiers + scorecards.
 */
import type { AgentSummary, AgentEnrollment } from '@/lib/api/agents';

const NOW = '2026-05-19T00:00:00Z';

export const MOCK_AGENTS: AgentSummary[] = [
  {
    name: 'engineering_head',
    team: 'engineering',
    role: 'manager',
    executor: 'claude',
    description: 'Owns the engineering team.',
    tier: 'green',
    scorecard: {
      agent: 'engineering_head',
      period_start: '2026-04-19T00:00:00Z',
      period_end: NOW,
      acceptance_rate: 0.94,
      revision_rate: 0.04,
      error_count: 1,
      tier: 'green',
      updated_at: NOW,
    },
    avg_confidence: 88,
  },
  {
    name: 'content_writer',
    team: 'content',
    role: 'worker',
    executor: 'claude',
    description: 'Drafts posts and marketing copy.',
    tier: 'green',
    scorecard: {
      agent: 'content_writer',
      period_start: '2026-04-19T00:00:00Z',
      period_end: NOW,
      acceptance_rate: 0.92,
      revision_rate: 0.06,
      error_count: 2,
      tier: 'green',
      updated_at: NOW,
    },
    avg_confidence: 85,
  },
  {
    name: 'support_agent',
    team: 'cx',
    role: 'worker',
    executor: 'codex',
    description: 'Handles first-line customer support.',
    tier: 'yellow',
    scorecard: {
      agent: 'support_agent',
      period_start: '2026-04-19T00:00:00Z',
      period_end: NOW,
      acceptance_rate: 0.82,
      revision_rate: 0.12,
      error_count: 4,
      tier: 'yellow',
      updated_at: NOW,
    },
    avg_confidence: 78,
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
