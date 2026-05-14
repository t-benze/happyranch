/**
 * Hand-mirrored from ``src/models.py``. See
 * ``docs/superpowers/specs/2026-05-14-web-ui-design.md`` §8 (the OpenAPI
 * contract test catches drift).
 *
 * Naming: type names mirror the Pydantic class names exactly; field names
 * mirror the wire shape (JSON keys), which sometimes differs from the Python
 * attribute name (e.g. ``ThreadRecord.id`` is serialized as ``thread_id``).
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'blocked'
  | 'completed'
  | 'failed';

export type BlockKind = 'delegated' | 'escalated';

export type PerformanceTier = 'green' | 'yellow' | 'red';

export type ReviewVerdict =
  | 'accept'
  | 'reject'
  | 'request_changes'
  | 'pending';

export type TalkStatus = 'open' | 'closed' | 'abandoned';

export type ThreadStatus = 'open' | 'archiving' | 'archived' | 'abandoned';

export type ThreadMessageKind = 'message' | 'decline' | 'system';

export type ThreadInvocationStatus =
  | 'pending'
  | 'consumed'
  | 'expired'
  | 'declined';

export type ThreadInvocationPurpose = 'reply' | 'bootstrap' | 'close_out';

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export interface TaskRecord {
  task_id: string;
  team: string;
  brief: string;
  status: TaskStatus;
  block_kind: BlockKind | null;
  parent_task_id: string | null;
  revisit_of_task_id: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  cancelled_at: string | null;
  session_timeout_seconds: number | null;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------------------
// Talks
// ---------------------------------------------------------------------------

export interface TalkRecord {
  talk_id: string;
  agent: string;
  status: TalkStatus;
  started_at: string;
  ended_at: string | null;
  abandoned_at: string | null;
  reason: string | null;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------------------
// Threads
// ---------------------------------------------------------------------------

export interface ThreadRecord {
  thread_id: string;
  subject: string;
  status: ThreadStatus;
  started_at: string;
  archived_at: string | null;
  forwarded_from_id: string | null;
  forwarded_from_kind: 'thread' | 'talk' | null;
  turn_cap: number;
  turns_used: number;
  summary: string | null;
  new_kb_slugs: string[] | null;
  transcript_path: string | null;
}

export interface ThreadDetailResponse extends ThreadRecord {
  participants: string[];
  messages: ThreadMessage[];
}

export interface ThreadMessage {
  seq: number;
  speaker: string; // "founder" | <agent_name>
  kind: ThreadMessageKind;
  body_markdown: string | null;
  addressed_to: string[] | null;
  decline_reason: string | null;
  system_payload: Record<string, unknown> | null;
  created_at: string;
}

export interface ThreadInboxEvent {
  thread_id: string;
  event_kind: string; // ThreadMessageKind ∪ {"compose", "invite", "archive_request", ...}
  status: string;
}

export interface ThreadTailEvent {
  thread_id: string;
  seq: number | null;
  speaker: string;
  kind: string;
  preview: string;
}

// ---------------------------------------------------------------------------
// Orgs / runtime / agents (minimal shapes used by the UI today; expand later)
// ---------------------------------------------------------------------------

export interface OrgsListResponse {
  orgs: { slug: string; root: string }[];
}

export interface HealthResponse {
  status: string;
  active_runtime: string | null;
}
