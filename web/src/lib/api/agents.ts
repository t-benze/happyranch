/** Mirror of src/daemon/routes/agents.py — founder-facing read + enrollment subset.
 *
 * Agent-subprocess-only routes (task_id/session_id auth) excluded:
 * POST /agents/manage (enroll/update/terminate via team-manager session),
 * POST /agents/{a}/memory (legacy + entries add/update/promote, reindex).
 *
 * Founder-facing write routes included for the Agents surface reshape:
 * PUT /agents/{name}/executor, POST /agents/{name}/repos.
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
  request(`/orgs/${slug}/agents/${agentName}/memory/entries/`, { params });

export const getLearning = (
  slug: string,
  agentName: string,
  idOrSlug: string,
): Promise<LearningEntry> =>
  request(`/orgs/${slug}/agents/${agentName}/memory/entries/${idOrSlug}`);

export const searchLearnings = (
  slug: string,
  agentName: string,
  body: { query: string; limit?: number; include_promoted?: boolean },
): Promise<{ hits: { id: string; slug: string; title: string; snippet: string; score: number }[] }> =>
  request(`/orgs/${slug}/agents/${agentName}/memory/entries/search`, {
    method: 'POST',
    body,
  });

// ---------------------------------------------------------------------------
// Founder-facing agent write routes (Agents surface reshape, design-overhaul)
// ---------------------------------------------------------------------------

/** Switch an agent's executor end-to-end (org .md + workspace agent.yaml). */
export const setAgentExecutor = (
  slug: string,
  agentName: string,
  body: { executor: string; clean?: boolean },
): Promise<{
  agent: string;
  before: { org_executor: string | null; workspace_executor: string | null };
  after: { org_executor: string; workspace_executor: string };
  stale_files: string[];
}> =>
  request(`/orgs/${slug}/agents/${agentName}/executor`, {
    method: 'PUT',
    body,
  });

/** Add, remove, or update an agent's repo binding. */
export interface ManageAgentRepoBody {
  action: 'add' | 'remove' | 'update';
  repo_name: string;
  url?: string;
}

export const manageAgentRepo = (
  slug: string,
  agentName: string,
  body: ManageAgentRepoBody,
): Promise<{ ok: true }> =>
  request(`/orgs/${slug}/agents/${agentName}/repos`, {
    method: 'POST',
    body,
  });

// ---------------------------------------------------------------------------
// GAP (surfaced per brief): no founder-facing route to update system_prompt
// or description. The daemon's POST /agents/manage (action=update) requires
// task_id + session_id (team-manager agent session). A founder-facing PUT
// /agents/{name} or PUT /orgs/{slug}/agents/{name} would be needed. Until
// then, system_prompt and description render as read-only in the detail pane.
// ---------------------------------------------------------------------------
