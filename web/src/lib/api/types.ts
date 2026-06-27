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

// THR-037 Change B (Path B, stored source-of-truth): `blocked` is gone from
// the surfaced vocabulary. A parent waiting on its own children/jobs is
// `in_progress` with a `block_kind` discriminant; an agent waiting on the
// founder is the top-level `escalated`; a founder-cancelled task is the
// terminal `cancelled` (distinct from `failed`).
export type TaskStatus =
  | 'pending'
  | 'in_progress'
  // Non-terminal: an agent (root) needs a founder decision (was blocked+escalated).
  | 'escalated'
  | 'completed'
  | 'failed'
  // Terminal: founder-initiated stop, distinct from failed.
  | 'cancelled'
  // Terminal: task closed because its follow-up moved to a human-authorized
  // continuation (revisit / thread-dispatch).
  | 'resolved_superseded';

// What an `in_progress` task is internally waiting on (escalated left the
// discriminant and became a top-level status under Path B).
export type BlockKind = 'delegated' | 'blocked_on_job';

export type ReviewVerdict =
  | 'accept'
  | 'reject'
  | 'request_changes'
  | 'pending';

export type ThreadStatus = 'open' | 'archived';

export type ThreadMessageKind = 'message' | 'decline' | 'system';

export type ThreadInvocationStatus =
  | 'pending'
  | 'consumed'
  | 'expired'
  | 'declined';

export type ThreadInvocationPurpose = 'reply' | 'bootstrap';

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export interface TaskRecord {
  task_id: string;
  team: string;
  brief: string;
  status: TaskStatus;
  block_kind: BlockKind | null;
  assigned_agent: string | null;
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
// Task events (SSE tail)
// ---------------------------------------------------------------------------

export interface TaskEvent {
  type: string;
  timestamp: string;
  task_id?: string;
  agent?: string | null;
  payload?: Record<string, unknown> | null;
  [extra: string]: unknown;
}

export interface ChainLegResponse {
  agent: string;
  prompt: string;
  expect_verdict: string | null;
}

export interface ActiveChainResponse {
  step_index: number;
  first_leg_expect_verdict: string | null;
  legs: ChainLegResponse[];
  step_audit_id: number;
}

/** Envelope returned by `GET /api/v1/orgs/{slug}/tasks/{task_id}`. */
export interface TaskDetailResponse {
  task: TaskRecord;
  results: unknown[] | null;
  audit_log: unknown[];
  revisit_chain: string[];
  direct_revisits: unknown[];
  predecessor_prior_status: string | null;
  active_chain: ActiveChainResponse | null;
  [extra: string]: unknown;
}

/** Audit-log entry shape (mirror of `audit_log` table rows). */
export interface AuditEntry {
  id: number;
  task_id: string | null;
  session_id: string | null;
  agent: string | null;
  action: string;
  payload: Record<string, unknown>;
  timestamp: string;
  /** DERIVE enrichment (include_thread_origin=true): the dream that composed
   *  the thread referenced by task_id, when task_id is thread-scoped (THR-*).
   *  Absent for non-thread or non-dream-originated entries. */
  _thread_dream_id?: string | null;
}

/** Recall payload. With `?tree=true`, `children` is recursive; without it,
 * `children` is a list of task-ID strings — UI must request the tree shape. */
export interface TaskRecallNode {
  task_id: string;
  assigned_agent?: string | null;
  brief: string;
  status: TaskStatus;
  output_summary?: string | null;
  children: TaskRecallNode[];
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
  forwarded_from_kind: 'thread' | null;
  turn_cap: number;
  turns_used: number;
  summary: string | null;
  transcript_path: string | null;
  composed_from_dream_id: string | null;
  last_speaker: string | null;
}

export interface ThreadDetailResponse extends ThreadRecord {
  participants: string[];
  messages: ThreadMessage[];
}

export type ResponderStatus =
  | 'queued'
  | 'working'
  | 'replied'
  | 'declined'
  | 'failed';

export interface ResponderStatusEntry {
  agent_name: string;
  status: ResponderStatus;
  responded_at: string | null;
  started_at: string | null;
}

export interface ThreadAttachment {
  artifact_name: string;
  display_name: string;
  size_bytes: number | null;
  content_type: string | null;
  uploaded_by: string;
}

export interface ThreadAttachmentRef {
  artifact_name: string;
  display_name?: string;
  content_type?: string | null;
}

export interface ThreadMessage {
  seq: number;
  speaker: string; // "founder" | <agent_name>
  kind: ThreadMessageKind;
  body_markdown: string | null;
  decline_reason: string | null;
  system_payload: Record<string, unknown> | null;
  attachments: ThreadAttachment[];
  created_at: string;
  responder_status: ResponderStatusEntry[];
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
// Knowledge Base
// ---------------------------------------------------------------------------

export interface KBEntry {
  slug: string;
  title: string;
  type: string;
  topic: string;
  tags: string[];
  body: string;
  updated_at: string;
  authored_by: string;
  source_task: string | null;
  related_entries?: string[];
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

// ---------------------------------------------------------------------------
// System assistant
// ---------------------------------------------------------------------------

/** Mirror of runtime/system_assistant.py::AssistantState. */
export type AssistantState = 'uninitialized' | 'configured' | 'stale_or_broken';

/** Mirror of runtime/system_assistant.py::AssistantStatus. */
export interface AssistantStatus {
  state: AssistantState;
  selected_executor: string | null;
  workspace_path: string | null;
  detail: string | null;
}

/** Body of POST /assistant/register. */
export interface AssistantRegisterBody {
  executor: string;
  command: string;
  argv: string[];
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export interface AgentSummary {
  name: string;
  team: string | null;
  role: 'manager' | 'worker' | null;
  executor: 'claude' | 'codex' | 'opencode' | 'pi' | null;
  description: string | null;
  // Phase 2: additive read-only fields (D6)
  repos: Record<string, string>;
  system_prompt: string;
}

export interface AgentEnrollment {
  name: string;
  team: string;
  role: 'manager' | 'worker';
  executor: 'claude' | 'codex' | 'opencode' | 'pi';
  description: string;
  status: 'pending' | 'approved';
  enrolled_by: string | null;
  created_at: string | null;
}

/** Summary shape returned by the learnings list endpoint. */
export interface LearningEntrySummary {
  id: string;
  slug: string;
  title: string;
  topic: string;
  tags: string[];
  promoted_to: string | null;
  updated_at: string;
}

/** Full entry as returned by the learnings get / search endpoints. */
export interface LearningEntry extends LearningEntrySummary {
  body: string;
  source_task: string | null;
  related_to: string[];
  supersedes: string | null;
  authored_by: string;
  authored_at: string;
  updated_by: string | null;
}

// ---------------------------------------------------------------------------
// Jobs (formerly "script requests")
// ---------------------------------------------------------------------------

export type JobStatus =
  | 'pending'
  | 'rejected'
  | 'running'
  | 'completed'
  | 'failed';

export type JobInterpreter = 'bash' | 'sh' | 'zsh' | 'python3';

export interface JobRecord {
  id: string;
  task_id: string;
  agent_name: string;
  title: string;
  rationale: string;
  script_text: string;
  interpreter: JobInterpreter;
  cwd_hint: string | null;
  status: JobStatus;
  exit_code: number | null;
  stdout_head: string | null;
  stderr_head: string | null;
  stdout_path: string | null;
  stderr_path: string | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  reject_reason: string | null;
  cwd_resolved: string | null;
  max_runtime_seconds: number | null;
  max_output_bytes: number | null;
  review_required: boolean;
  persistent: boolean;
  reason: string | null;
  created_at: string;
}

export interface JobListResponse {
  jobs: JobRecord[];
}

export interface JobRunResponse {
  id: string;
  status: 'running';
  started_at: string;
  cwd_resolved: string;
  timeout_seconds: number;
  events_url: string;
}

export interface JobOutput {
  stdout: string;
  stderr: string;
  truncated_stdout: boolean;
  truncated_stderr: boolean;
  total_stdout_bytes: number;
  total_stderr_bytes: number;
}

export interface JobTailResponse {
  stream: 'stdout' | 'stderr';
  lines: string[];
}

export interface JobStopResponse {
  ok: boolean;
  id: string;
  already_terminal?: boolean;
}

/**
 * Wait response is `JobRecord | {timed_out: true}` — when the timeout fires
 * the daemon returns only `{timed_out: true}`; on terminal transition it
 * returns the full record merged with `{timed_out: false}`.
 */
export type JobWaitResponse =
  | (JobRecord & { timed_out: false })
  | { timed_out: true };

// ---------------------------------------------------------------------------
// Dashboard summary (mirrors src/orchestrator/dashboard_summary.py)
// ---------------------------------------------------------------------------

export type HeartbeatTier = 'ok' | 'warn' | 'bad';

export interface HeartbeatBucket {
  hour: number;
  steps: number;
  failed: number;
  tier: HeartbeatTier;
}

export interface NarrativeCounts {
  completed_today: number;
  failed_today: number;
  escalated_open: number;
  kb_added_today: number;
  agents_active_now: number;
  spend_today_usd: number;
}

export interface DashboardEscalationRow {
  task_id: string;
  agent: string;
  team: string;
  question: string;
  raised_at: string;
  age_seconds: number;
  /** THR-037 Change B §G: DERIVED display flavor for the single stored
   *  `escalated` status ("needs-decision" | "exhausted" | "over-budget"),
   *  or null when the escalation reason is absent/unrecognized. */
  flavor?: string | null;
}

export interface ActiveByTeamRow {
  team: string;
  count: number;
  task_ids: string[];
}

export type ActivityVerdict = 'ok' | 'fail' | 'warn';

export interface DashboardActivityRow {
  timestamp: string;
  who: string;
  event_kind: string;
  task_id: string | null;
  verdict: ActivityVerdict | null;
  /** DERIVE enrichment (A4): the dream that composed the thread
   *  referenced by task_id, when task_id is THR-*. Null otherwise. */
  _thread_dream_id?: string | null;
}

export type UpdateMarker = 'add' | 'warn' | 'info';

export interface DashboardUpdateRow {
  marker: UpdateMarker;
  text: string;
  meta: string;
  timestamp: string;
}

export interface TeamPulseRow {
  team: string;
  acceptance_pct: number;
  trend_delta: number;
  sparkline: number[];
  members: number;
  lead: string;
}

export interface DashboardSummaryResponse {
  heartbeat: HeartbeatBucket[];
  narrative_counts: NarrativeCounts;
  escalations: DashboardEscalationRow[];
  active_by_team: ActiveByTeamRow[];
  recent_activity: DashboardActivityRow[];
  updates_this_week: DashboardUpdateRow[];
  org_pulse: TeamPulseRow[];
  org_age_days: number;
  server_now: string;
}

// ---------------------------------------------------------------------------
// Settings (read-only System + Org)
// ---------------------------------------------------------------------------

export interface SystemSettingEntry {
  value: string | number;
  restart_required: boolean;
}

export interface SystemSettings {
  claude_cli_path: SystemSettingEntry;
  codex_cli_path: SystemSettingEntry;
  opencode_cli_path: SystemSettingEntry;
  pi_cli_path: SystemSettingEntry;
  session_timeout_seconds: SystemSettingEntry;
  max_orchestration_steps: SystemSettingEntry;
  queue_workers: SystemSettingEntry;
  protocol_dir: SystemSettingEntry;
}

export interface DreamingSchedule {
  time: string;
  timezone: string;
}

export interface DreamingAgents {
  mode: string;
  include: string[];
  exclude: string[];
}

export interface DreamingSettings {
  enabled: boolean;
  schedule: DreamingSchedule;
  catch_up_on_startup: boolean;
  agents: DreamingAgents;
}

export interface ThreadsSettings {
  enabled: boolean;
  default_turn_cap: number;
  invocation_timeout_seconds: number | null;
}

export interface OrgSettings {
  session_timeout_seconds: number | null;
  dreaming: DreamingSettings;
  threads: ThreadsSettings;
}

export interface SettingsSnapshot {
  system: SystemSettings;
  org: OrgSettings;
}

// ---------------------------------------------------------------------------
// Settings PATCH — Phase 2 editable org surface
// ---------------------------------------------------------------------------

export interface DreamingSchedulePatch {
  time?: string;
  timezone?: string;
}

export interface DreamingAgentsPatch {
  mode?: string;
  include?: string[];
  exclude?: string[];
}

export interface DreamingPatch {
  enabled?: boolean;
  schedule?: DreamingSchedulePatch;
  catch_up_on_startup?: boolean;
  agents?: DreamingAgentsPatch;
}

export interface ThreadsPatch {
  enabled?: boolean;
  default_turn_cap?: number;
  invocation_timeout_seconds?: number | null;
}

export interface OrgSettingsPatch {
  session_timeout_seconds?: number | null;
  dreaming?: DreamingPatch;
  threads?: ThreadsPatch;
}

// ---------------------------------------------------------------------------
// Work-hours (Schedule surface)
// ---------------------------------------------------------------------------

export interface WorkHourRecord {
  work_hour_id: string;
  agent_name: string;
  local_date: string;
  slot: string;
  mode: string;
  scheduled_for: string;
  started_at: string | null;
  ended_at: string | null;
  status: string;
  routine_count: number;
  spawned_task_ids: string[];
  spawned_task_count: number;
  summary: string | null;
  transcript_path: string | null;
  session_id: string | null;
  error: string | null;
  created_at: string;
}

export interface WorkHourListResponse {
  work_hours: WorkHourRecord[];
}

export interface WorkHourStatusResponse {
  recent: WorkHourRecord[];
}
