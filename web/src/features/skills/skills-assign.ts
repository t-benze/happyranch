/**
 * Pure, provider-agnostic model for the per-agent ASSIGNMENT + config-review
 * surface on a CUSTOM (user-authored) skill's detail page (THR-092 Slice 5).
 *
 * This is the PENDING-CHANGE layer that sits on top of the committed per-agent
 * assignment facts. The committed → product-language derivation (Effective /
 * Takes effect next session / Not assigned + the "why" reason) is REUSED from
 * `skills-detail.ts` (`agentProvenance`) — this module does NOT author a
 * parallel copy set. It only adds:
 *
 *   - a queued set of not-yet-committed per-agent changes (the operator toggles
 *     agents before committing);
 *   - the OPTIMISTIC previewed state for each agent under the queue, so a
 *     newly-assigned agent immediately reads "Takes effect next session" and a
 *     newly-unassigned agent reads "Not assigned";
 *   - the config-review summary — the review-before-commit list of what will
 *     change, in guidance-visibility language.
 *
 * COPY DISCIPLINE (hard): guidance-visibility language only. The api request
 * verb is literally 'allow' / 'remove' but that is the REQUEST BODY value only
 * — it is NEVER surfaced. Visible labels are 'Assign' / 'Unassign' and the
 * commit reads 'Review & apply'. No permission / approve / admit / grant /
 * materialize / "pending" / "active" wording anywhere. This module is
 * unit-tested for that.
 */
import {
  agentProvenance,
  type AgentAssignmentFacts,
  type AgentProvenance,
} from './skills-detail';

/** The api request-body verb for the assign route. REQUEST-ONLY — never
 *  rendered. Toggling a not-assigned agent ON queues 'allow'; toggling an
 *  assigned agent OFF queues 'remove'. */
export type AssignAction = 'allow' | 'remove';

/** The queue of not-yet-committed changes: agent → desired assigned-state.
 *  Only agents whose desired state DIFFERS from the committed state appear
 *  here; a toggle back to the committed state drops the entry. */
export type PendingAssignments = Record<string, boolean>;

/**
 * Build the FULL candidate roster of per-agent assignment rows the panel
 * renders, by unioning the real agents roster with the skill's status response.
 *
 * The status endpoint (`getSkillStatus().assignments`) returns ONLY agents that
 * are already assigned the skill (the daemon skips unassigned agents) — so it is
 * NOT the candidate roster. To let the operator assign a custom skill to an as-
 * yet-unassigned agent, the candidate list is derived from the real agents
 * roster (`useAgentsList`): for each roster agent, its committed row from the
 * status response is used if present, otherwise a synthesized `not_assigned`
 * row (`assigned:false, effective:false`) is emitted — which `agentProvenance`
 * (REUSED, no parallel copy) then renders as "Not assigned" with an Assign
 * control. Roster order is preserved; any assigned agent NOT in the roster (a
 * lingering assignment after a roster change) is appended so it is never lost.
 */
export function rosterAssignments(
  rosterAgents: string[],
  assignments: AgentAssignmentFacts[],
): AgentAssignmentFacts[] {
  const byAgent = new Map(assignments.map((a) => [a.agent, a]));
  const seen = new Set<string>();
  const rows: AgentAssignmentFacts[] = [];
  for (const agent of rosterAgents) {
    if (seen.has(agent)) continue;
    seen.add(agent);
    rows.push(
      byAgent.get(agent) ?? { agent, assigned: false, effective: false },
    );
  }
  for (const a of assignments) {
    if (!seen.has(a.agent)) {
      seen.add(a.agent);
      rows.push(a);
    }
  }
  return rows;
}

/** Desired assigned-state under the queue (committed when not queued). */
export function desiredAssigned(
  a: AgentAssignmentFacts,
  queue: PendingAssignments,
): boolean {
  return a.agent in queue ? queue[a.agent] : a.assigned;
}

/** Whether the agent has a queued (not-yet-committed) change. */
export function isChanged(
  a: AgentAssignmentFacts,
  queue: PendingAssignments,
): boolean {
  return desiredAssigned(a, queue) !== a.assigned;
}

/** Toggle one agent's desired state in the queue. A toggle that returns the
 *  agent to its committed state DROPS the queue entry (a no-op change is never
 *  committed). Pure — returns a new queue. */
export function toggleAssignment(
  a: AgentAssignmentFacts,
  queue: PendingAssignments,
): PendingAssignments {
  const next = { ...queue };
  const desired = !desiredAssigned(a, queue);
  if (desired === a.assigned) {
    delete next[a.agent];
  } else {
    next[a.agent] = desired;
  }
  return next;
}

/** The product-language TOGGLE label for an agent — the action the toggle will
 *  perform given the current desired state. 'Assign' when it will start showing
 *  the skill as guidance; 'Unassign' when it will stop. Never renders the api
 *  verb ('allow'/'remove') or permission wording ('Allow'/'Grant'/'Approve'). */
export function toggleLabel(
  a: AgentAssignmentFacts,
  queue: PendingAssignments,
): 'Assign' | 'Unassign' {
  return desiredAssigned(a, queue) ? 'Unassign' : 'Assign';
}

/** The OPTIMISTIC per-agent provenance under the queue. Feeds the committed
 *  facts through `agentProvenance` (REUSED) after applying the desired assigned
 *  state: a newly-assigned agent becomes assigned-not-yet-effective ("Takes
 *  effect next session"); a newly-unassigned agent becomes not-assigned; an
 *  unchanged agent keeps its committed provenance verbatim. */
export function previewProvenance(
  a: AgentAssignmentFacts,
  queue: PendingAssignments,
): AgentProvenance {
  const desired = desiredAssigned(a, queue);
  if (desired === a.assigned) return agentProvenance(a);
  if (desired) {
    // Newly assigned — the current version is not materialized for this agent
    // yet, so it takes effect at its next session.
    return agentProvenance({ agent: a.agent, assigned: true, effective: false });
  }
  // Newly unassigned — the skill is no longer shown to this agent as guidance.
  return agentProvenance({ agent: a.agent, assigned: false, effective: false });
}

/** One queued change, ready for the config-review summary AND the commit call.
 *  `action` is the REQUEST-ONLY verb; `label` / `summary` are the rendered,
 *  guidance-visibility copy. */
export interface AssignmentChange {
  agent: string;
  /** REQUEST-BODY verb — 'allow' | 'remove'. NEVER rendered. */
  action: AssignAction;
  /** Product-language label for the change — 'Assign' | 'Unassign'. */
  label: 'Assign' | 'Unassign';
  /** One-sentence, guidance-visibility summary of what the change does. */
  summary: string;
}

/** The config-review summary: the review-before-commit list of queued changes,
 *  in guidance-visibility language. Preserves the input agent order so the
 *  review reads the same as the per-agent table. */
export function reviewChanges(
  assignments: AgentAssignmentFacts[],
  queue: PendingAssignments,
): AssignmentChange[] {
  const changes: AssignmentChange[] = [];
  for (const a of assignments) {
    if (!isChanged(a, queue)) continue;
    changes.push(
      desiredAssigned(a, queue)
        ? {
            agent: a.agent,
            action: 'allow',
            label: 'Assign',
            summary: `${a.agent} will be shown this skill as guidance at its next session.`,
          }
        : {
            agent: a.agent,
            action: 'remove',
            label: 'Unassign',
            summary: `${a.agent} will no longer be shown this skill as guidance.`,
          },
    );
  }
  return changes;
}

/** How many agents have a queued change. Drives the "N changes to review"
 *  affordance and gates the commit action. */
export function changeCount(
  assignments: AgentAssignmentFacts[],
  queue: PendingAssignments,
): number {
  return reviewChanges(assignments, queue).length;
}

/** The guidance-visibility-only note shown at the commit action. States the
 *  invariant plainly: assignment changes what an agent is SHOWN as guidance,
 *  not what it can do. Deliberately avoids every forbidden token family
 *  (permission / approve / admit / grant / materialize / pending / active). */
export const CONFIG_REVIEW_NOTE =
  'Assigned skills are shown to this agent as guidance at its next session; they do not change available tools or commands.';
