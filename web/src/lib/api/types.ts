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
// During the Path B transition, a legacy DB row may still carry `blocked`
// paired with block_kind='escalated'. The TaskDetailPage dual-read handles
// this; the union keeps it so tsc won't reject the comparison. Remove after
// the legacy-row soak period ends.
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
  | 'superseded'
  // DEPRECATED (Path B transition). Retained so legacy rows that still carry
  // status='blocked' + block_kind='escalated' don't cause type errors in
  // dual-read transition sites. Remove in a later cleanup phase.
  | 'blocked';

// What an `in_progress` task is internally waiting on (escalated left the
// discriminant and became a top-level status under Path B).
// 'escalated' retained for legacy transition: rows that haven't migrated
// yet may carry block_kind='escalated' alongside status='blocked'.
export type BlockKind = 'delegated' | 'blocked_on_job' | 'escalated';

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

export interface CleanupActivity {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  result_status: TaskStatus | null;
  output_summary: string | null;
}

// ---------------------------------------------------------------------------
// Task attachments (THR-109)
// ---------------------------------------------------------------------------

export interface TaskAttachmentRef {
  storage_key: string;
  display_name?: string;
}

export interface TaskAttachmentRecord {
  storage_key: string;
  task_id: string;
  ordinal: number;
  display_name: string;
  size_bytes: number | null;
  content_type: string | null;
  uploaded_by: string;
  created_at: string;
  legacy_status: string | null;
}

export interface TaskAttachmentUploadResponse {
  storage_key: string;
  display_name: string;
  size_bytes: number;
  content_type: string | null;
  uploaded_by: string;
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

export interface EscalationReason {
  primary: string | null;
  refusal_code: string | null;
  secondary: string | null;
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
  /** DERIVE from escalation_superseded audit: successor task_id when this
   *  task was auto-resolved to SUPERSEDED. Null otherwise. */
  superseded_by_task_id: string | null;
  /** TASK-5522: read-only derived work-status summary. Present on every
   *  envelope; the server always derives it from the task record + audit
   *  rows (session_start / progress). Absent only from legacy-daemon or
   *  stubbed fixtures. */
  work_status: WorkStatusResponse | null;
  /** Current-episode-only read projection. Existing task.note is unchanged. */
  escalation_reason: EscalationReason | null;
  [extra: string]: unknown;
}

/** Machine state of the derived work-status summary (TASK-5522).
 *
 * (a)-(d) apply only to the live-task shape (in_progress, no block_kind)
 * with a FRESH observed heartbeat; every other shape is explicitly
 * not_applicable, and stale/absent heartbeat liveness is its own honest
 * state rather than being papered over with a progress label.
 */
export type WorkStatusState =
  | 'newly_started'          // (a) no receipt yet, session start < 5m
  | 'recent_progress'        // (b) latest current-session receipt < 5m
  | 'stale_no_receipt'       // (c) no receipt, session start >= 5m
  | 'stale_old_receipt'      // (d) latest current-session receipt >= 5m
  | 'heartbeat_stale'        // live shape but heartbeat >= 60s old
  | 'heartbeat_unavailable'  // live shape, no heartbeat observed
  | 'unavailable'            // cannot derive (absent/malformed audit data)
  | 'not_applicable';        // terminal / pending / escalated / parked-on-block

/** Read-only derived work-status summary for the task-detail envelope. */
export interface WorkStatusResponse {
  applicable: boolean;
  state: WorkStatusState;
  /** Human phrase — says what is OBSERVED; never claims execution progress
   *  from a heartbeat and never claims a receipt when only liveness exists. */
  label: string;
  /** not_applicable / unavailable discriminator: terminal | pending |
   *  escalated | blocked | no_session_start | unassigned. Null otherwise. */
  reason: string | null;
  /** Latest assigned-agent session_start timestamp (current-session lower
   *  boundary). Null when not derivable. */
  session_start_ts: string | null;
  heartbeat: {
    timestamp: string | null;
    /** Existing 60-second heartbeat freshness semantics (2 missed 30s
     *  intervals) — never a new monitor/reaper threshold. */
    freshness: 'fresh' | 'stale' | 'unavailable';
  };
  /** Latest current-session progress receipt; `message` is null when the
   *  stored content is absent/malformed (unavailable, never fabricated). */
  latest_progress: {
    timestamp: string;
    message: string | null;
    agent: string;
  } | null;
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
  /** Founder-workspace pin state (THR-209). Wire is derived from the durable
   *  ``pinned_at`` column: false when never pinned, true while pinned. */
  pinned: boolean;
  pinned_at: string | null;
  /** Most recent message created_at (derived server-side). Informational on
   *  the wire (THR-209 msg 9: pinned ranking uses the immutable numeric
   *  thread ID, not activity). */
  last_activity_at: string | null;
  /** Current participant names, projected only on bounded list rows. */
  participants: string[];
}

export interface ThreadDetailResponse extends ThreadRecord {
  participants: string[];
  messages: ThreadMessage[];
  /** Pair-level reply-delivery projection (GH-688 Phase 1, Slice B wire).
   *  Present on both GET /threads/{id} and GET /threads/{id}/messages.
   *  Empty when every pair is fully settled — no live obligation exists. */
  reply_delivery: ReplyDeliveryEntry[];
}

export type ReplyDeliveryState =
  | 'queued'
  | 'running'
  | 'held'
  | 'retry_required';

/** Pair-level reply-delivery projection (GH-688 Phase 1).
 *
 *  Mirrors runtime/models.py ReplyDeliveryProjection. Derived from the
 *  durable ``thread_reply_delivery_state`` table — never fabricated from
 *  per-message invocation rows. ``state`` is truthful about the live
 *  obligation: queued = one unstarted coalesced wake (NO subprocess), running
 *  = one claimed in-flight reply (started_at set), held = an obligation
 *  intentionally deferred by an open exchange plus matching held row,
 *  retry_required = an
 *  unacknowledged range with no active wake (diagnostic; the next
 *  conversational arrival mints the single covering retry).
 *  ``coalesced_message_count`` is the number of transcript rows the wake's
 *  inclusive range covers (computed in the store, not inferred from
 *  seq subtraction). */
export interface ReplyDeliveryEntry {
  agent_name: string;
  state: ReplyDeliveryState;
  from_seq: number;
  through_seq: number;
  coalesced_message_count: number;
  started_at: string | null;
  updated_at: string | null;
  last_terminal_reason: string | null;
  current_failure_category: 'no_callback' | 'no_callback_after_reprompt' | 'infra_fail' | null;
}

export type ResponderStatus =
  | 'queued'
  | 'working'
  | 'replied'
  | 'declined'
  | 'failed';

export interface ResponderStatusEntry {
  agent_name: string;
  /** Authoritative wake purpose from thread_invocations (TASK-5553).
   *  Classification/dedup uses THIS, never the triggering row's kind — a
   *  coalesced conversational REPLY range can anchor on a SYSTEM row, and a
   *  same-agent TASK_FOLLOWUP can coexist on the same transcript. */
  purpose: 'reply' | 'bootstrap' | 'task_followup';
  status: ResponderStatus;
  responded_at: string | null;
  started_at: string | null;
  decline_reason: string | null;
  category:
    | 'declined'
    | 'no_callback'
    | 'no_callback_after_reprompt'
    | 'infra_fail'
    | null;
}

export interface ThreadAttachment {
  artifact_name: string;
  display_name: string;
  size_bytes: number | null;
  content_type: string | null;
  uploaded_by: string;
  /** Non-null for thread-scoped attachments (uploaded via the composer).
   *  When present, artifact_name is empty and the download targets the
   *  thread-scoped route. */
  thread_attachment_id?: string | null;
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

export interface ThreadMessagesPage {
  messages: ThreadMessage[];
  has_more: boolean;
  next_since_seq: number;
  /** Pair-level reply-delivery projection — same shape as the thread-detail
   *  response so both surfaces stay in lockstep (GH-688 Phase 1 Slice B). */
  reply_delivery: ReplyDeliveryEntry[];
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
  /** Bounded, non-sensitive host-session health block (THR-207). Present only
   *  when the daemon-wide HostSessionSupervisor is wired (runtime-backed
   *  states); idle states keep the two-key contract. Public variant drops the
   *  per-receipt recent window, censused survivor identities, and backend
   *  probe evidence. */
  host_sessions?: HostSessionPublicBlock;
}

/** Mirror of the public (unauthenticated) `host_sessions` block on /health.
 *  Counts, aggregates, and the stable backend classification yes; per-receipt
 *  detail, survivor identities, and probe evidence no.
 *  Full block: web/src/lib/api/metrics.ts HostSessionBlock. */
export interface HostSessionPublicBlock {
  wired: boolean;
  backend: {
    name: string | null;
    version: string | null;
    healthy: boolean;
    probed_at: number;
    capabilities: Record<string, string>;
  };
  admission: {
    cap: number | null;
    active: number;
    queue_depth: number;
    oldest_wait_seconds: number;
    head_stall_reason: string | null;
    shutdown: boolean;
    admitted_total: number;
    released_total: number;
    cancelled_queued_total: number;
  };
  residue: {
    admission_blocked: boolean;
    block_reason: string | null;
    survivors_count: number;
  };
  receipts: {
    published_total: number;
    window_size: number;
    by_terminal_reason: Record<string, number>;
    by_cleanup_status: Record<string, number>;
    quiescent_count: number;
    with_residue_count: number;
    cleanup_duration_seconds: { max: number | null; last: number | null };
    peaks: {
      memory_peak_bytes: { kernel: { max: number | null; count: number }; sampled: { max: number | null; count: number }; unavailable_count: number };
      cpu_total_seconds: { kernel: { max: number | null; count: number }; sampled: { max: number | null; count: number }; unavailable_count: number };
      process_peak: { kernel: { max: number | null; count: number }; sampled: { max: number | null; count: number }; unavailable_count: number };
    };
  };
}

/** Mirror of runtime/daemon/routes/health.py::ExecutorPrereq. */
export interface ExecutorPrereq {
  tool: string;
  present: boolean;
  path: string | null;
  hint: string;
}

/** Mirror of runtime/daemon/routes/health.py::PrereqsResponse. */
export interface PrereqsResponse {
  prereqs: ExecutorPrereq[];
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export interface IdentityNameMetadata {
  addressable_name?: string | null;
  name_revision?: number | null;
  naming_status?: 'ready' | 'unavailable';
}

export interface AgentSummary extends IdentityNameMetadata {
  name: string;
  team: string | null;
  role: 'manager' | 'worker' | null;
  executor: string | null;
  model?: string | null;
  description: string | null;
  // Phase 2: additive read-only fields (D6)
  repos: Record<string, string>;
  system_prompt: string;
  /** Exact-content revision for a whole-definition update. */
  revision?: string | null;
}

export interface SystemPromptBody {
  system_prompt: string;
  expected_revision: string;
}

export interface SystemPromptReceipt {
  agent: string;
  system_prompt: string;
  revision: string;
}

export type SystemPromptCompensation = 'restored' | 'not_owned' | 'failed' | 'not_required';
export type SystemPromptErrorDetail =
  | { code: 'expected_revision_required' | 'agent_not_found' }
  | { code: 'stale_agent_revision'; current_revision: string }
  | { code: 'system_prompt_reconciliation_failed'; error: string;
      compensation: { canonical: SystemPromptCompensation; workspace: SystemPromptCompensation } }
  | { code: 'system_prompt_audit_failed'; commit_state: 'possibly_committed' };

export interface AgentEnrollment extends IdentityNameMetadata {
  name: string;
  team: string;
  role: 'manager' | 'worker';
  executor: string;
  description: string;
  status: 'pending' | 'approved';
  enrolled_by: string | null;
  created_at: string | null;
}

/** Summary shape returned by the memory list endpoint. */
export interface MemoryEntrySummary {
  id: string;
  slug: string;
  title: string;
  topic: string;
  tags: string[];
  promoted_to: string | null;
  updated_at: string;
}

/** Full entry as returned by the memory get / search endpoints. */
export interface MemoryEntry extends MemoryEntrySummary {
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
  escalation_reason?: EscalationReason | null;
}

export interface DashboardPendingReviewJobRow {
  id: string;
  task_id: string;
  agent_name: string;
  title: string;
  created_at: string;
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
  pending_review_jobs: DashboardPendingReviewJobRow[];
  active_by_team: ActiveByTeamRow[];
  recent_activity: DashboardActivityRow[];
  updates_this_week: DashboardUpdateRow[];
  org_pulse: TeamPulseRow[];
  org_age_days: number;
  server_now: string;
  generated_at: string | null;
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
  queue_workers: SystemSettingEntry;
  host_global_session_cap: SystemSettingEntry;
  protocol_dir: SystemSettingEntry;
}

export interface DaemonCapacitySnapshot {
  running_at_daemon_start: { queue_workers: number; host_global_session_cap: number };
  running_provenance: string;
  persisted_yaml: { queue_workers: number | null; host_global_session_cap: number | null };
  next_start: { queue_workers: number; host_global_session_cap: number };
  environment_shadowed: string[];
  environment_warning: string | null;
  producer_envelope: number;
  producer_components: { task_workers: number; thread_workers: number; dream_workers: number; wake_workers: number; schedule_workers: number };
  effective_admission_cap: number | null;
  effective_admission_reason: string;
  warnings: string[];
  revision: string;
  restart_required: boolean;
  restart_pending: boolean;
  guidance: { queue_workers: string; host_global_session_cap: string; enforced: boolean };
  authorization: string;
  message?: string;
}

export interface DaemonCapacityWrite {
  revision: string;
  queue_workers: number;
  host_global_session_cap: number;
  rationale: string;
  confirm_environment_shadow: boolean;
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

// ---------------------------------------------------------------------------
// Work-hours config (schedule surface) — RAW per-tier blocks.
//
// Mirror of the daemon `WorkingHoursSettingsView` (routes/settings.py). The
// client derives per-leaf provenance + the effective schedule from these raw
// tiers (THR-035 §4.3 reconciliation view) — a `null` leaf is unset at that
// tier and inherits from a lower-precedence tier.
// ---------------------------------------------------------------------------

export interface WorkHoursWindow {
  start: string | null;
  end: string | null;
  timezone: string | null;
}

export interface WorkHoursLayer {
  mode: string | null;
  window: WorkHoursWindow;
  interval: string | null;
  days: string[] | null;
  catch_up_on_startup: boolean | null;
}

/** Single org-level eligibility gate (not per-tier). */
export interface WorkHoursAgents {
  mode: string; // 'all' | 'whitelist'
  include: string[];
  exclude: string[];
}

export interface WorkingHoursSettings {
  /** Single feature-level on/off switch (NOT a per-tier leaf). */
  enabled: boolean;
  agents: WorkHoursAgents;
  default: WorkHoursLayer;
  teams: Record<string, WorkHoursLayer>;
  overrides: Record<string, WorkHoursLayer>;
}

export interface OrgSettings {
  session_timeout_seconds: number | null;
  /** Server-returned operator setting; intentionally has no browser control. */
  reviewer_agents: string[];
  dreaming: DreamingSettings;
  threads: ThreadsSettings;
  working_hours: WorkingHoursSettings;
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

// ---------------------------------------------------------------------------
// Work-hours config PATCH — partial; an explicit `null` leaf clears the
// override (reset-to-inherited). teams/overrides are keyed dicts: sending one
// key deep-merges without dropping siblings.
// ---------------------------------------------------------------------------

export interface WorkHoursWindowPatch {
  start?: string | null;
  end?: string | null;
  timezone?: string | null;
}

export interface WorkHoursLayerPatch {
  mode?: string | null;
  window?: WorkHoursWindowPatch;
  interval?: string | null;
  days?: string[] | null;
  catch_up_on_startup?: boolean | null;
}

export interface WorkHoursAgentsPatch {
  mode?: string | null;
  include?: string[] | null;
  exclude?: string[] | null;
}

export interface WorkingHoursPatch {
  enabled?: boolean;
  agents?: WorkHoursAgentsPatch;
  default?: WorkHoursLayerPatch;
  teams?: Record<string, WorkHoursLayerPatch>;
  overrides?: Record<string, WorkHoursLayerPatch>;
}

export interface OrgSettingsPatch {
  session_timeout_seconds?: number | null;
  dreaming?: DreamingPatch;
  threads?: ThreadsPatch;
  working_hours?: WorkingHoursPatch;
}

// ---------------------------------------------------------------------------
// Next-wakes preview (GET /work-hours/next-wakes)
// ---------------------------------------------------------------------------

export interface NextWakesResponse {
  agent: string;
  enabled: boolean;
  timezone: string | null;
  mode: string | null;
  next_wakes: string[]; // ISO-8601
  error: string | null;
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

// ---------------------------------------------------------------------------
// Schedules (Agent Todos) — THR-105 Phase 3
// ---------------------------------------------------------------------------

export type ScheduleKind = 'one_shot' | 'weekly' | 'recurring';

export interface ScheduleRecurrence {
  day?: string;
  time?: string;
  tz?: string;
  [key: string]: string | number | string[] | null | undefined;
}

export type ScheduleStatus =
  | 'armed'
  | 'firing'
  | 'fired'
  | 'paused'
  | 'cancelled'
  | 'expired'
  | 'failed'
  | 'timeout';

export interface ScheduleRecord {
  schedule_id: string;
  agent_name: string;
  team: string;
  kind: ScheduleKind;
  fire_at: string;
  recurrence: ScheduleRecurrence | null;
  timezone: string;
  normalized_brief: string;
  source_instruction: string;
  status: ScheduleStatus;
  active: number;
  expires_at: string | null;
  indefinite: number;
  spawned_task_ids: string[];
  last_fired_at: string | null;
  fire_count: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleListResponse {
  schedules: ScheduleRecord[];
}

export interface ScheduleEditFields {
  fire_at?: string;
  recurrence?: ScheduleRecurrence;
  timezone?: string;
  /** Optional local YYYY-MM-DD native-recurring phase; server derives fire_at. */
  start_date?: string;
}

export interface ScheduleRenewBody {
  indefinite?: boolean;
}

// Existing-schema THR-139 cutover. No activation, dispatch or launch receipt.
export type WorkflowCutoverState = 'installed_legacy_only' | 'enable_requested'
  | 'compatibility_verified' | 'enabled' | 'disable_requested' | 'draining' | 'drained';
export interface WorkflowCutoverEvent {
  id: string;
  event_seq: number;
  state_before: WorkflowCutoverState | null;
  state_after: WorkflowCutoverState;
  operation_key: string | null;
  event_digest: string;
  created_at: string;
}
export interface WorkflowCutoverBlocker {
  code: string;
  record_id?: string | null;
  state?: string | null;
  owner: string;
  required_action: string;
  deferred_to?: string | null;
}
export interface WorkflowCutoverProjection {
  org_slug: string;
  schema_version: number;
  state: WorkflowCutoverState;
  recovery_owner: 'workflow_cutover_reconciler';
  generation: number;
  operation_key: string | null;
  disable_reason: 'founder_disable_requested' | null;
  updated_at: string;
  events: WorkflowCutoverEvent[];
  allowed_actions: ('enable' | 'disable')[];
  blockers: WorkflowCutoverBlocker[];
  reconciliation_required: boolean;
  verification: { event_id: string; policy: 'workflow-cutover-verifier@1' } | null;
}
export interface WorkflowCutoverRequestInput {
  action: 'enable' | 'disable';
  operation_key: string;
  expected_generation: number;
}
export interface WorkflowCutoverRequestResponse extends WorkflowCutoverProjection {
  request_event_id: string;
  request_generation: number;
  request_action: 'enable' | 'disable';
  replayed: boolean;
}
export interface WorkflowCutoverDowngradePreflight {
  eligible: boolean;
  blockers: WorkflowCutoverBlocker[];
  projection: WorkflowCutoverProjection;
}

/** Exact-version Founder activation; no caller actor/task/session/result claims. */
export type WorkflowActivationRole = 'product-lead' | 'founder' | 'implementer' | 'tester';
export type WorkflowActivationAction = 'draft-document' | 'submit-immutable-document' | 'collect-review' |
  'approve-planning-input' | 'return-to-author';
export interface WorkflowActivationRoleBinding {
  kind: 'agent' | 'human';
  principal: string;
  team: string | null;
}
export type WorkflowActivationBindings = Record<WorkflowActivationRole, WorkflowActivationRoleBinding>;
export type WorkflowActivationReplacements = Record<WorkflowActivationRole, WorkflowActivationRoleBinding[]>;
export interface WorkflowActivationAuthority {
  namespace: string;
  generation: number;
  snapshot_digest: string;
}
export interface WorkflowActivationTemplatePin {
  identity_id: string;
  version: number;
  definition_digest: string;
}
export type WorkflowActivationInput = {
  kind: 'task-attachment'; task_id: string; storage_key: string;
  sha256: string; recipients: WorkflowActivationRole[];
} | {
  kind: 'thread-attachment'; thread_id: string; attachment_id: string;
  sha256: string; recipients: WorkflowActivationRole[];
};
export interface LegacyWorkflowActivationRequest {
  operation_key: string;
  instance_id: string;
  expected_activation_revision: 0;
  template: WorkflowActivationTemplatePin;
  authority: WorkflowActivationAuthority;
  scope: { brief: string };
  bindings: WorkflowActivationBindings;
  eligible_replacements: WorkflowActivationReplacements;
  allowed_actions: WorkflowActivationAction[];
  inputs: WorkflowActivationInput[];
}
export interface LegacyWorkflowActivationReceipt {
  activation_id: string;
  instance_id: string;
  instance_reference: string;
  activation_revision: number;
  root_task_id: string;
  intent_id: string;
  template: WorkflowActivationTemplatePin & {
    version_id: string; compiler_pin: string; validator_pin: string; source_pin: string;
  };
  authority: WorkflowActivationAuthority;
  bindings: WorkflowActivationBindings;
  eligible_replacements: WorkflowActivationReplacements;
  allowed_actions: WorkflowActivationAction[];
  scope_digest: string;
  context_digest: string;
  activated_by: { principal_kind: 'human'; principal_id: 'founder'; proof_kind: 'founder_bearer' };
  created_at: string;
  original_request_digest: string;
  replayed: boolean;
  state: 'queued' | 'claimed' | 'running' | 'uncertain' | 'cancelled' | 'failed' | 'completed';
  execution_started: boolean;
  pending: boolean;
  reconciliation_required: boolean;
  cancellation_requested: boolean;
  current_eligibility: { eligible: boolean; blockers: string[] };
  responsible_owner: string;
}

export type WorkflowDocumentReturnRule = {
  action: 'return-to-author'; revision: 'new'; invalidate: 'all-prior-receipts';
};
export type WorkflowDocumentReviewDefinition = {
  kind: 'document-review'; schema_version: 2; description: string;
  author: { role: string; kind: 'agent' };
  output: { primitive: 'immutable-document-revision'; description: string };
  reviewers: { role: string; kind: 'agent' | 'human' }[];
  approval: { mode: 'all'; revision: 'current'; required_roles: string[] };
  submission: { timing: 'on-completion' | 'while-active-or-completed' };
} & ({ outcomes: ['approved']; request_changes: null } | {
  outcomes: ['approved', 'changes_requested'] | ['changes_requested', 'approved'];
  request_changes: WorkflowDocumentReturnRule;
});
export type DocumentWorkflowActivationInput =
  Omit<Extract<WorkflowActivationInput, { kind: 'task-attachment' }>, 'recipients'> & { recipients: string[] }
  | Omit<Extract<WorkflowActivationInput, { kind: 'thread-attachment' }>, 'recipients'> & { recipients: string[] };
export interface DocumentWorkflowActivationRequest extends Omit<LegacyWorkflowActivationRequest,
  'bindings' | 'eligible_replacements' | 'inputs'> {
  format: 'workflow-activation-request@2';
  bindings: Record<string, WorkflowActivationRoleBinding>;
  eligible_replacements: Record<string, WorkflowActivationRoleBinding[]>;
  inputs: DocumentWorkflowActivationInput[];
}
export interface DocumentWorkflowActivationReceipt extends Omit<LegacyWorkflowActivationReceipt,
  'bindings' | 'eligible_replacements'> {
  format: 'workflow-activation-receipt@2';
  bindings: Record<string, WorkflowActivationRoleBinding>;
  eligible_replacements: Record<string, WorkflowActivationRoleBinding[]>;
}
export type WorkflowActivationRequest = LegacyWorkflowActivationRequest | DocumentWorkflowActivationRequest;
export type WorkflowActivationReceipt = LegacyWorkflowActivationReceipt | DocumentWorkflowActivationReceipt;

// Naming v1 — separate metadata; AgentSummary.name remains canonical ID.
export interface IdentityView {
  canonical_id: string;
  kind: 'agent' | 'founder';
  lifecycle: 'active' | 'pending' | 'terminated' | 'absent' | 'founder';
  addressable_name: string | null;
  name_revision: number | null;
  canonical_definition_revision: string | null;
  naming_status: 'ready' | 'unavailable';
}
export interface IdentityList { identities: IdentityView[] }
export interface RenameBody { addressable_name: string; expected_name_revision: number }
export interface ResolveBody {
  addresses: string[];
  context: 'thread_recipient' | 'task_owner' | 'lookup';
  thread_id?: string | null;
}
export interface Resolution {
  address: string;
  status: 'resolved' | 'former_name' | 'unknown_identity' | 'invalid_identity_address' | 'ineligible_identity' | 'naming_unavailable';
  identity: IdentityView | null;
  eligible: boolean;
}
export interface ResolveResponse { resolutions: Resolution[] }
