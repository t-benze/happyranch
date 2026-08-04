/**
 * Pure, provider-agnostic mapping helpers for THR-055 Proposal Detail (Slice 2B).
 *
 * All display derivation lives here — no React, no fetch. The ProposalDetailPage
 * consumes these functions to render immutable facts from the API response.
 *
 * Copy discipline:
 *   - NEVER synthesize SKILL.md, hash, validation pass, claimant, audit row,
 *     assignment, materialization, or permitted action.
 *   - Assignment/materialization are SEPARATE from package decision status.
 *   - Publication = catalog-only, projections = version-pinned, visibility =
 *     guidance only (never permissions).
 *   - Rejected is terminal/view-only — no action/reopen affordance.
 *   - permitted_next_action is informational context only, never clickable.
 */
import type { ProposalDetailResponse } from '@/hooks/skills';

// ── Status label maps ───────────────────────────────────────────────────

export type ProposalStatusLabel =
  | 'Proposed'
  | 'Draft'
  | 'Validation failed'
  | 'Validated'
  | 'In review'
  | 'Approved'
  | 'Published'
  | 'Rejected'
  | 'Legacy quarantined';

export const STATUS_LABEL: Record<string, ProposalStatusLabel> = {
  proposed: 'Proposed',
  draft: 'Draft',
  validation_failed: 'Validation failed',
  validated: 'Validated',
  in_review: 'In review',
  approved: 'Approved',
  published: 'Published',
  rejected: 'Rejected',
  legacy_quarantined: 'Legacy quarantined',
};

export type StatusTone = 'pending' | 'active' | 'done' | 'failed' | 'unknown';

export const STATUS_TONE: Record<string, StatusTone> = {
  proposed: 'pending',
  draft: 'active',
  validation_failed: 'failed',
  validated: 'pending',
  in_review: 'active',
  approved: 'pending',
  published: 'done',
  rejected: 'failed',
  legacy_quarantined: 'unknown',
};

export function statusLabel(status: string): ProposalStatusLabel {
  return STATUS_LABEL[status] ?? (status.charAt(0).toUpperCase() + status.slice(1) as ProposalStatusLabel);
}

export function statusTone(status: string): StatusTone {
  return STATUS_TONE[status] ?? 'unknown';
}

/** True when rejected — terminal, view-only, no action/reopen affordance. */
export function isRejected(status: string): boolean {
  return status === 'rejected';
}

/** True when published — package lifecycle complete, visible in catalog. */
export function isPublished(status: string): boolean {
  return status === 'published';
}

/** True when terminal decision status (published or rejected). */
export function isTerminal(status: string): boolean {
  return isPublished(status) || isRejected(status);
}

// ── Founder review actions (THR-136) ─────────────────────────────────────

export type ProposalReviewAction =
  | 'claim'
  | 'validate'
  | 'submit-review'
  | 'approve'
  | 'reject';

/**
 * Map a server-returned status to the single next Founder review action.
 * Returns `null` for terminal, unknown, or out-of-scope statuses so the UI
 * hides controls instead of speculating about transitions.
 */
export function availableReviewAction(status: string): ProposalReviewAction | null {
  switch (status) {
    case 'proposed':
      return 'claim';
    case 'draft':
    case 'validation_failed':
      return 'validate';
    case 'validated':
      return 'submit-review';
    case 'in_review':
      return 'approve'; // rendered alongside reject in this state
    default:
      return null;
  }
}

// ── Tone styling helpers ─────────────────────────────────────────────────

export const TONE_CHIP: Record<StatusTone, string> = {
  pending: 'text-fg-muted border border-border-default bg-transparent',
  active: 'text-accent-text bg-accent-soft',
  done: 'text-status-open bg-tier-green-tint',
  failed: 'text-attention-text bg-attention-soft',
  unknown: 'text-fg-muted border border-border-default bg-transparent',
};

// ── Hash display ─────────────────────────────────────────────────────────

export interface HashDisplay {
  full: string;
  short: string;
  label: string;
}

/**
 * Format the content_hash for display. Returns a short prefix + tail for
 * readability, and the full hash for the copy control.
 */
export function hashDisplay(contentHash: string): HashDisplay {
  const prefix = 'sha256:';
  const hex = contentHash.startsWith(prefix) ? contentHash.slice(prefix.length) : contentHash;
  return {
    full: contentHash.startsWith(prefix) ? contentHash : `${prefix}${hex}`,
    short: hex.length >= 14 ? `${hex.slice(0, 10)}…${hex.slice(-5)}` : hex,
    label: hex.length >= 14 ? `${hex.slice(0, 7)}…${hex.slice(-5)}` : hex,
  };
}

// ── Readiness facts ──────────────────────────────────────────────────────

export interface ReadinessFact {
  label: string;
  status: 'ok' | 'pending' | 'warning' | 'none';
}

/**
 * Derive readiness facts directly from response facts — events,
 * assignments, materializations, and supplied status/decision fields.
 * NEVER infer catalog membership, assignment state, validation success,
 * claimant, or projection facts from a status enum.
 */
export function readinessFacts(detail: ProposalDetailResponse): ReadinessFact[] {
  const facts: ReadinessFact[] = [];

  // Assignment: derive from actual assignments array, not status
  const assignedCount = detail.assignments?.length ?? 0;
  if (assignedCount > 0) {
    facts.push({ label: `${assignedCount} agent(s) assigned`, status: 'ok' });
  } else {
    facts.push({ label: 'No assignments recorded', status: 'none' });
  }

  // Materialization: derive from actual materializations array
  const matCount = detail.materializations?.length ?? 0;
  if (matCount > 0) {
    const successCount = detail.materializations!.filter((m) => m.success === true).length;
    facts.push({ label: `${successCount}/${matCount} materialization(s) succeeded`, status: successCount === matCount ? 'ok' : 'warning' });
  } else {
    facts.push({ label: 'No materializations recorded', status: 'none' });
  }

  // Decision: only when review_decision is present (backed by real fields)
  if (detail.review_decision) {
    if (detail.review_decision === 'approved') {
      facts.push({ label: 'Approved by reviewer', status: 'ok' });
    } else if (detail.review_decision === 'rejected') {
      facts.push({ label: 'Rejected — terminal', status: 'none' });
    }
  }

  return facts;
}

// ── Event classification ─────────────────────────────────────────────────

export interface TimelineEvent {
  eventType: string;
  actor: string;
  actorRole: string;
  time: string;
  previousStatus: string | null;
  newStatus: string | null;
  contentHash: string | null;
  metadata: Record<string, unknown> | null;
  /** Safely extracted metadata facts for display */
  metadataFacts: MetadataFact[];
  /** Human-readable event label */
  label: string;
  /** Tone for the event row */
  tone: StatusTone;
}

/** A single fact extracted from event metadata, safe for display. */
export interface MetadataFact {
  key: string;
  value: string;
}

export const EVENT_LABELS: Record<string, string> = {
  proposed: 'Proposed',
  drafted: 'Claimed as draft',
  validated: 'Validation passed',
  validation_failed: 'Validation failed',
  submitted_for_review: 'Submitted for review',
  approved: 'Approved',
  rejected: 'Rejected',
  published: 'Published',
  assigned: 'Assigned',
  unassigned: 'Unassigned',
  rolled_back: 'Rolled back',
  retired: 'Retired',
  materialized: 'Materialized',
  materialization_failed: 'Materialization failed',
};

/**
 * Safely extract displayable facts from event metadata. Only surfaces known
 * metadata keys used in audit review — never dumps arbitrary metadata blob.
 */
export function metadataFacts(metadata: Record<string, unknown> | null): MetadataFact[] {
  if (!metadata) return [];
  const facts: MetadataFact[] = [];
  const knownKeys: Array<{ key: string; label: string }> = [
    { key: 'validator_version', label: 'Validator version' },
    { key: 'validator_key', label: 'Validator key' },
    { key: 'run_id', label: 'Run' },
    { key: 'run_identifier', label: 'Run' },
    { key: 'reason', label: 'Reason' },
    { key: 'rationale', label: 'Rationale' },
    { key: 'failure', label: 'Failure' },
    { key: 'error', label: 'Error' },
  ];
  for (const { key, label } of knownKeys) {
    const v = metadata[key];
    if (v != null && v !== '') {
      facts.push({ key: label, value: String(v) });
    }
  }
  return facts;
}

export function timelineEvents(events: Array<Record<string, unknown>>): TimelineEvent[] {
  return events
    .map((e) => {
      const eventType = String(e.event_type ?? '');
      const newStatus = e.new_status != null ? String(e.new_status) : null;
      const meta = (e.metadata as Record<string, unknown>) ?? null;
      return {
        eventType,
        actor: String(e.actor ?? ''),
        actorRole: String(e.actor_role ?? ''),
        time: String(e.created_at ?? ''),
        previousStatus: e.previous_status != null ? String(e.previous_status) : null,
        newStatus,
        contentHash: e.content_hash != null ? String(e.content_hash) : null,
        metadata: meta,
        metadataFacts: metadataFacts(meta),
        label: EVENT_LABELS[eventType] ?? eventType,
        tone: newStatus ? statusTone(newStatus) : 'unknown',
      } as TimelineEvent;
    })
    .sort((a, b) => a.time.localeCompare(b.time));
}

// ── Assignment & materialization projection ──────────────────────────────

export interface AssignmentProjection {
  agentName: string;
  assigned: boolean;
  version: string | null;
  assignedBy: string | null;
  assignedAt: string | null;
}

export interface MaterializationAttempt {
  agentName: string;
  success: boolean | null;
  errorMessage: string | null;
  createdAt: string | null;
}

export function assignmentProjection(assignments: Array<Record<string, unknown>>): AssignmentProjection[] {
  return assignments.map((a) => ({
    agentName: String(a.agent_name ?? ''),
    assigned: Boolean(a.active ?? a.assigned ?? true),
    version: a.version != null ? String(a.version) : null,
    assignedBy: a.assigned_by != null ? String(a.assigned_by) : null,
    assignedAt: a.assigned_at != null ? String(a.assigned_at) : null,
  }));
}

/**
 * Map raw materialization records into a narrow projection using actual
 * server fields: success, error_message, created_at.
 * Never reads nonexistent materialized_at or labels a record 'pending'.
 */
export function materializationProjection(
  materializations: Array<Record<string, unknown>>,
): MaterializationAttempt[] {
  return materializations.map((m) => ({
    agentName: String(m.agent_name ?? ''),
    success: typeof m.success === 'boolean' ? m.success : null,
    errorMessage: m.error_message != null ? String(m.error_message) : null,
    createdAt: m.created_at != null ? String(m.created_at) : null,
  }));
}

/** True when there are any assignments or materializations. */
export function hasAssignmentProjection(detail: ProposalDetailResponse): boolean {
  return (
    (detail.assignments?.length ?? 0) > 0 ||
    (detail.materializations?.length ?? 0) > 0
  );
}

// ── Validator facts ─────────────────────────────────────────────────────

export interface ValidatorFacts {
  hasValidation: boolean;
  version: string | null;
  key: string | null;
  result: string | null;
}

/**
 * Extract validation facts from events — the latest validation event determines
 * whether validation has been run and what the result was.
 */
export function validatorFacts(events: Array<Record<string, unknown>>): ValidatorFacts {
  // Find the latest validate/validation_failed event
  let version: string | null = null;
  let key: string | null = null;
  let hasValidation = false;
  let result: string | null = null;

  for (const e of [...events].reverse()) {
    const et = String(e.event_type ?? '');
    if (et === 'validated' || et === 'validation_failed') {
      hasValidation = true;
      result = et === 'validated' ? 'Passed' : 'Failed';
      const meta = e.metadata as Record<string, unknown> | null;
      if (meta) {
        version = meta.validator_version != null ? String(meta.validator_version) : null;
        key = meta.validator_key != null ? String(meta.validator_key) : null;
      }
      break;
    }
  }

  return { hasValidation, version, key, result };
}
