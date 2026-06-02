/** Mirror of src/daemon/routes/agents.py — founder-facing read + enrollment subset.
 *
 * Excluded (agent-subprocess-only): POST /agents/{a}/repos,
 * POST /agents/manage, POST /agents/{a}/learnings (legacy + entries
 * add/update/promote, reindex). See spec §2.
 *
 * Type definitions live in `./types.ts` so feature compositions can import
 * them without violating the no-restricted-imports rule that forbids
 * `@/lib/api/<X>` deep imports from `src/features/`.
 */
import { request } from './client';
import type {
  AgentEnrollment,
  AgentSummary,
  LearningEntry,
  LearningEntrySummary,
} from './types';

// Re-export for callers that import from this module by name (the
// providers layer; never feature compositions).
export type {
  AgentEnrollment,
  AgentSummary,
  LearningEntry,
  LearningEntrySummary,
} from './types';

export const listAgents = (
  slug: string,
): Promise<{ agents: AgentSummary[] }> =>
  request(`/orgs/${slug}/agents`);

export interface CreateAgentBody {
  name: string;
  role: 'worker' | 'manager';
  team?: string;
  new_team?: string;
  executor: 'claude' | 'codex' | 'opencode' | 'pi';
  description: string;
  system_prompt: string;
  allow_rules?: string[];
  repos?: Record<string, string>;
}

export const createAgent = (
  slug: string,
  body: CreateAgentBody,
): Promise<{ name: string; team: string; role: 'worker' | 'manager' }> =>
  request(`/orgs/${slug}/agents`, { method: 'POST', body });

export const initAgents = (
  slug: string,
  body?: { agent?: string },
): Promise<{ initialized: string[] }> =>
  request(`/orgs/${slug}/agents/init`, { method: 'POST', body });

export const listEnrollments = (
  slug: string,
  params?: { status?: 'pending' | 'approved'; team?: string },
): Promise<{ enrollments: AgentEnrollment[] }> =>
  request(`/orgs/${slug}/agents/enrollments`, { params });

export const approveAgent = (
  slug: string,
  agentName: string,
): Promise<{ name: string }> =>
  request(`/orgs/${slug}/agents/${agentName}/approve`, { method: 'POST' });

export const rejectAgent = (
  slug: string,
  agentName: string,
  body?: { reason?: string },
): Promise<{ name: string }> =>
  request(`/orgs/${slug}/agents/${agentName}/reject`, { method: 'POST', body });

// ---------------------------------------------------------------------------
// Per-agent learnings — READ ONLY (writes are agent-subprocess only)
// ---------------------------------------------------------------------------

export const listLearnings = (
  slug: string,
  agentName: string,
  params?: {
    topic?: string;
    tag?: string;
    promoted?: boolean;
  },
): Promise<{ entries: LearningEntrySummary[] }> =>
  request(`/orgs/${slug}/agents/${agentName}/learnings/entries/`, { params });

export const getLearning = (
  slug: string,
  agentName: string,
  idOrSlug: string,
): Promise<LearningEntry> =>
  request(`/orgs/${slug}/agents/${agentName}/learnings/entries/${idOrSlug}`);

export const searchLearnings = (
  slug: string,
  agentName: string,
  body: { query: string; limit?: number; include_promoted?: boolean },
): Promise<{ hits: { id: string; slug: string; title: string; snippet: string; score: number }[] }> =>
  request(`/orgs/${slug}/agents/${agentName}/learnings/entries/search`, {
    method: 'POST',
    body,
  });
