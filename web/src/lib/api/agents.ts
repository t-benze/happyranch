/** Mirror of src/daemon/routes/agents.py — founder-facing read + enrollment subset.
 *
 * Excluded (agent-subprocess-only): POST /agents/{a}/repos,
 * POST /agents/manage, POST /agents/{a}/learnings (legacy + entries
 * add/update/promote, reindex). See spec §2.
 *
 * Founder-facing learnings READS (list/get/search) are exposed because the
 * web UI may eventually render an agent profile that shows their learnings.
 */
import { request } from './client';
import type { PerformanceTier } from './types';

export interface AgentSummary {
  name: string;
  team: string;
  role: 'manager' | 'worker';
  executor: 'claude' | 'codex' | 'opencode';
  tier: PerformanceTier | null;
  description: string | null;
}

export interface AgentEnrollment {
  name: string;
  team: string;
  role: 'manager' | 'worker';
  enrolled_by: string;
  enrolled_at: string;
  enrolled_at_task: string | null;
  description: string;
}

export const listAgents = (
  slug: string,
  params?: { detail?: boolean },
): Promise<{ agents: AgentSummary[] }> =>
  request(`/orgs/${slug}/agents`, { params });

export const initAgents = (
  slug: string,
  body?: { agent?: string },
): Promise<{ initialized: string[] }> =>
  request(`/orgs/${slug}/agents/init`, { method: 'POST', body });

export const listEnrollments = (
  slug: string,
  params?: { status?: 'pending' | 'active' | 'rejected' },
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

export const backfillEnrollments = (
  slug: string,
): Promise<{ backfilled: number }> =>
  request(`/orgs/${slug}/agents/backfill-enrollments`, { method: 'POST' });

// ---------------------------------------------------------------------------
// Per-agent learnings — READ ONLY (writes are agent-subprocess only)
// ---------------------------------------------------------------------------

export interface LearningEntry {
  id: string;
  slug: string;
  title: string;
  topic: string;
  tags: string[];
  body: string;
  related_to: string[];
  supersedes: string | null;
  promoted_to: string | null;
  authored_by: string;
  authored_at: string;
  updated_at: string;
}

export const listLearnings = (
  slug: string,
  agentName: string,
  params?: {
    topic?: string;
    tag?: string;
    promoted?: boolean;
    not_promoted?: boolean;
  },
): Promise<{ entries: LearningEntry[] }> =>
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
  body: { q: string; limit?: number; include_promoted?: boolean },
): Promise<{ entries: LearningEntry[] }> =>
  request(`/orgs/${slug}/agents/${agentName}/learnings/entries/search`, {
    method: 'POST',
    body,
  });
