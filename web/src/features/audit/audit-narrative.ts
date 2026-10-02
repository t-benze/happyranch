/**
 * audit-narrative — pure event → human-readable narrative transform.
 *
 * AUDIT-01 (THR-030): audit rows must read as narrative sentences with entity
 * links + a mono secondary detail line, NOT raw event-type codes
 * (`thread_dispatch`, `completion_report`, …).
 *
 * This module is a CLIENT-SIDE presentation transform over data the audit
 * query ALREADY returns (`action` + `agent` + `task_id` scope + `payload` +
 * `timestamp`). It adds NO fetch and invents NO field — every value rendered
 * is read straight off the AuditEntry. Event types without a clean mapping
 * fall back to a humanized phrase (snake_case → words); the raw code is never
 * surfaced verbatim.
 *
 * The per-action mapping mirrors runtime/infrastructure/audit_logger.py —
 * the single writer of audit rows — so payload keys here match what is stored.
 * (The GH-688 Phase 1 ``thread_reply_wake_*`` actions are emitted directly by
 * the runtime/infrastructure/database.py reply-delivery store transitions
 * with the same payload shapes; see the Slice C comment there.)
 *
 * Kept free of React so it is unit-testable in isolation; AuditTimeline maps
 * the returned segments to <Link>s (entity refs) and styled text.
 *
 * THR-118 W4b: every sentence and detail label is a catalog template
 * (`audit.n.*` / `audit.detail.*`) rendered in an explicit locale; agent
 * names, ids, action names and payload values stay verbatim in the slots.
 */
import type { AuditEntry } from '@/lib/api/types';
import {
  formatTokensFor,
  lookupMessage,
  translate,
  type Locale,
  type MessageKey,
  type MessageParams,
} from '@/lib/i18n';

/** A clickable entity. Only types that have an EXISTING client route are
 *  emitted as refs (task/thread/job/agent); everything else stays plain text. */
export interface EntityRef {
  type: 'agent' | 'task' | 'thread' | 'job';
  id: string;
  label: string;
}

export type NarrativeSegment =
  /** The acting agent — rendered bold, not a link. */
  | { kind: 'subject'; text: string }
  /** Connective prose. */
  | { kind: 'text'; text: string }
  /** A linked entity. */
  | { kind: 'ref'; ref: EntityRef };

export interface AuditNarrative {
  /** The narrative sentence, subject first. */
  segments: NarrativeSegment[];
  /** Optional mono secondary detail line; null when there is nothing honest
   *  and useful to show. */
  detail: string | null;
}

/* ------------------------------------------------------------------ */
/*  Segment + ref builders                                            */
/* ------------------------------------------------------------------ */

const sub = (text: string): NarrativeSegment => ({ kind: 'subject', text });
const tx = (text: string): NarrativeSegment => ({ kind: 'text', text });
const rf = (ref: EntityRef): NarrativeSegment => ({ kind: 'ref', ref });

const taskRef = (id: string): EntityRef => ({ type: 'task', id, label: id });
const threadRef = (id: string): EntityRef => ({ type: 'thread', id, label: id });
const jobRef = (id: string): EntityRef => ({ type: 'job', id, label: id });
const agentRef = (id: string): EntityRef => ({ type: 'agent', id, label: id });

/** Classify the generic `task_id` scope column into a routable ref.
 *  TASK-/THR- are linkable; dream/workhour/`artifact:`/`AGENT-` scopes are not. */
function scopeRef(id: string | null): EntityRef | null {
  if (!id) return null;
  if (id.startsWith('TASK-')) return taskRef(id);
  if (id.startsWith('THR-')) return threadRef(id);
  return null;
}

/* ------------------------------------------------------------------ */
/*  Payload accessors (honest — undefined when absent)                */
/* ------------------------------------------------------------------ */

function str(p: Record<string, unknown>, key: string): string | undefined {
  const v = p[key];
  return typeof v === 'string' ? v : undefined;
}

function numOf(p: Record<string, unknown>, key: string): number | undefined {
  const v = p[key];
  return typeof v === 'number' ? v : undefined;
}

/** Inclusive range detail "messages F–T" from a reply-wake payload. */
function rangeOf(
  p: Record<string, unknown>,
  t: (key: MessageKey, params?: MessageParams) => string,
): string | undefined {
  const from = numOf(p, 'from_seq');
  const to = numOf(p, 'through_seq');
  if (from == null || to == null) return undefined;
  return t('audit.detail.messages', { from, to });
}

/** "exchange #N" detail from an exchange payload. */
function exchangeOf(
  p: Record<string, unknown>,
  t: (key: MessageKey, params?: MessageParams) => string,
): string | null {
  const id = numOf(p, 'exchange_id');
  return id != null ? t('audit.detail.exchange', { id }) : null;
}

function tokensOf(p: Record<string, unknown>): number | undefined {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total' in tu) {
    const t = (tu as Record<string, unknown>).total;
    if (typeof t === 'number') return t;
  }
  return numOf(p, 'token_count');
}

/* ------------------------------------------------------------------ */
/*  Formatters                                                        */
/* ------------------------------------------------------------------ */

// Token counts use the explicit-locale `formatTokensFor` (THR-118), whose
// English path delegates to the canonical `@/lib/format` `formatTokens`.

type Translate = (key: MessageKey, params?: MessageParams) => string;

function formatDuration(seconds: number, t: Translate): string {
  if (seconds < 60) return t('audit.detail.seconds', { s: seconds });
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return t('audit.detail.minutesSeconds', { m, s });
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Join honest detail parts; null when nothing to show. */
function detailOf(...parts: (string | undefined | null)[]): string | null {
  const kept = parts.filter((p): p is string => !!p);
  return kept.length ? kept.join(' · ') : null;
}

/** snake_case → "snake case" for the unknown-event fallback. */
function humanize(action: string): string {
  return action.replace(/_/g, ' ');
}

/* ------------------------------------------------------------------ */
/*  Catalog sentence templates                                        */
/* ------------------------------------------------------------------ */

const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;

/**
 * Build segments from a catalog sentence template (THR-118 W4b). `{agent}` is
 * the subject slot; every other named slot is a linked entity, a verbatim
 * daemon value, or a translated fallback noun. Slots may move freely between
 * locales. Adjacent prose is merged so a sentence without refs stays one text
 * segment after the subject.
 */
function sentence(
  locale: Locale,
  key: MessageKey,
  slots: Record<string, NarrativeSegment>,
): NarrativeSegment[] {
  const template = lookupMessage(locale, key);
  const raw = typeof template === 'string' ? template : '';
  const out: NarrativeSegment[] = [];
  const pushText = (text: string): void => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.kind === 'text') out[out.length - 1] = tx(last.text + text);
    else out.push(tx(text));
  };
  let cursor = 0;
  for (const match of raw.matchAll(PLACEHOLDER)) {
    const at = match.index ?? 0;
    pushText(raw.slice(cursor, at));
    const slot = slots[match[1]];
    if (slot?.kind === 'text') pushText(slot.text);
    else if (slot) out.push(slot);
    cursor = at + match[0].length;
  }
  pushText(raw.slice(cursor));
  return out;
}

type NounKey =
  | 'audit.n.noun.task'
  | 'audit.n.noun.thread'
  | 'audit.n.noun.job'
  | 'audit.n.noun.artifact'
  | 'audit.n.noun.agent'
  | 'audit.n.noun.participant'
  | 'audit.n.noun.blockedTask';

/** Actions rendered "<subject> <verb> <scope ref | generic noun>." */
const SCOPED: Record<string, [MessageKey, NounKey]> = {
  session_start: ['audit.n.session_start', 'audit.n.noun.task'],
  session_end: ['audit.n.session_end', 'audit.n.noun.task'],
  completion_report: ['audit.n.completion_report', 'audit.n.noun.task'],
  review_verdict: ['audit.n.review_verdict', 'audit.n.noun.task'],
  escalation: ['audit.n.escalation', 'audit.n.noun.task'],
  daemon_restart_failure: ['audit.n.daemon_restart_failure', 'audit.n.noun.task'],
  escalation_resolved: ['audit.n.escalation_resolved', 'audit.n.noun.task'],
  task_cancelled: ['audit.n.task_cancelled', 'audit.n.noun.task'],
  progress: ['audit.n.progress', 'audit.n.noun.task'],
  task_resume_skipped: ['audit.n.task_resume_skipped', 'audit.n.noun.task'],
  orchestration_step: ['audit.n.orchestration_step', 'audit.n.noun.task'],
  revisit_spawned: ['audit.n.revisit_spawned', 'audit.n.noun.task'],
  thread_started: ['audit.n.thread_started', 'audit.n.noun.thread'],
  thread_message_sent: ['audit.n.thread_message_sent', 'audit.n.noun.thread'],
  thread_decline_consumed: ['audit.n.thread_decline_consumed', 'audit.n.noun.thread'],
  agent_session_reused: ['audit.n.agent_session_reused', 'audit.n.noun.thread'],
  agent_session_evicted_fallback: ['audit.n.agent_session_evicted_fallback', 'audit.n.noun.thread'],
  thread_task_followup_enqueued: ['audit.n.thread_task_followup_enqueued', 'audit.n.noun.task'],
  thread_followup_skipped: ['audit.n.thread_followup_skipped', 'audit.n.noun.task'],
  thread_turn_cap_auto_extended: ['audit.n.thread_turn_cap_auto_extended', 'audit.n.noun.task'],
  thread_archived: ['audit.n.thread_archived', 'audit.n.noun.thread'],
  thread_resumed: ['audit.n.thread_resumed', 'audit.n.noun.thread'],
  thread_renamed: ['audit.n.thread_renamed', 'audit.n.noun.thread'],
  thread_pinned: ['audit.n.thread_pinned', 'audit.n.noun.thread'],
  thread_unpinned: ['audit.n.thread_unpinned', 'audit.n.noun.thread'],
};

/** Actions rendered "<subject> <phrase>[ in <scope ref>]." — [plain, with-scope]. */
const OPTIONAL_SCOPE: Record<string, [MessageKey, MessageKey]> = {
  thread_invocation_failed: ['audit.n.thread_invocation_failed', 'audit.n.thread_invocation_failed.in'],
  thread_reply_wake_created: ['audit.n.thread_reply_wake_created', 'audit.n.thread_reply_wake_created.in'],
  thread_reply_wake_coalesced: ['audit.n.thread_reply_wake_coalesced', 'audit.n.thread_reply_wake_coalesced.in'],
  thread_reply_wake_claimed: ['audit.n.thread_reply_wake_claimed', 'audit.n.thread_reply_wake_claimed.in'],
  thread_reply_wake_settled: ['audit.n.thread_reply_wake_settled', 'audit.n.thread_reply_wake_settled.in'],
  thread_reply_wake_cancelled: ['audit.n.thread_reply_wake_cancelled', 'audit.n.thread_reply_wake_cancelled.in'],
  thread_reply_wake_recovered: ['audit.n.thread_reply_wake_recovered', 'audit.n.thread_reply_wake_recovered.in'],
  thread_exchange_opened: ['audit.n.thread_exchange_opened', 'audit.n.thread_exchange_opened.in'],
  thread_exchange_closed: ['audit.n.thread_exchange_closed', 'audit.n.thread_exchange_closed.in'],
  thread_exchange_corrupt: ['audit.n.thread_exchange_corrupt', 'audit.n.thread_exchange_corrupt.in'],
  thread_deferral_held: ['audit.n.thread_deferral_held', 'audit.n.thread_deferral_held.in'],
  thread_deferral_released: ['audit.n.thread_deferral_released', 'audit.n.thread_deferral_released.in'],
  thread_deferral_coalesced: ['audit.n.thread_deferral_coalesced', 'audit.n.thread_deferral_coalesced.in'],
  thread_deferral_catchup_pending: [
    'audit.n.thread_deferral_catchup_pending',
    'audit.n.thread_deferral_catchup_pending.in',
  ],
  thread_deferral_catchup_minted: [
    'audit.n.thread_deferral_catchup_minted',
    'audit.n.thread_deferral_catchup_minted.in',
  ],
  thread_deferral_suppressed: ['audit.n.thread_deferral_suppressed', 'audit.n.thread_deferral_suppressed.in'],
  learning_added: ['audit.n.learning_added', 'audit.n.learning_added.scoped'],
  task_blocked_on_jobs: ['audit.n.task_blocked_on_jobs', 'audit.n.task_blocked_on_jobs.scoped'],
  task_resumed_from_jobs: ['audit.n.task_resumed_from_jobs', 'audit.n.task_resumed_from_jobs.scoped'],
};

/** Job actions "<subject> <verb> <JOB>[ <prep> <parent task>]." — [plain, with-scope]. */
const JOB: Record<string, [MessageKey, MessageKey]> = {
  job_submitted: ['audit.n.job_submitted', 'audit.n.job_submitted.on'],
  job_rejected: ['audit.n.job_rejected', 'audit.n.job_rejected.on'],
  job_run_started: ['audit.n.job_run_started', 'audit.n.job_run_started.on'],
  job_auto_started: ['audit.n.job_auto_started', 'audit.n.job_auto_started.on'],
  job_run_completed: ['audit.n.job_run_completed', 'audit.n.job_run_completed.on'],
  job_run_failed: ['audit.n.job_run_failed', 'audit.n.job_run_failed.on'],
  job_stopped: ['audit.n.job_stopped', 'audit.n.job_stopped.on'],
};

/** Subject-only sentences with no entity slot. */
const PLAIN: Record<string, MessageKey> = {
  learning_updated: 'audit.n.learning_updated',
  learning_promoted: 'audit.n.learning_promoted',
  dream_scheduled: 'audit.n.dream_scheduled',
  dream_started: 'audit.n.dream_started',
  dream_completed: 'audit.n.dream_completed',
  dream_failed: 'audit.n.dream_failed',
  dream_timeout: 'audit.n.dream_timeout',
  work_hour_scheduled: 'audit.n.work_hour_scheduled',
  work_hour_started: 'audit.n.work_hour_started',
  work_hour_spawned: 'audit.n.work_hour_spawned',
  work_hour_completed: 'audit.n.work_hour_completed',
  work_hour_failed: 'audit.n.work_hour_failed',
  work_hour_timeout: 'audit.n.work_hour_timeout',
};

const AGENT_MANAGED: Record<string, MessageKey> = {
  enroll: 'audit.n.agent_managed.enroll',
  update: 'audit.n.agent_managed.update',
  terminate: 'audit.n.agent_managed.terminate',
};

const has = (map: object, key: string): boolean => Object.prototype.hasOwnProperty.call(map, key);

/* ------------------------------------------------------------------ */
/*  Main transform                                                    */
/* ------------------------------------------------------------------ */

/**
 * Describe one audit row in `locale`. Sentence structure and connective prose
 * come from `audit.n.*` catalog templates; agent names, ids, action names and
 * payload values are inserted verbatim into named slots.
 */
export function describeAuditEntry(e: AuditEntry, locale: Locale): AuditNarrative {
  const t: Translate = (key, params) => translate(locale, key, params);
  const p = e.payload ?? {};
  const agent = sub(e.agent ?? t('audit.n.subject.system'));
  const scope = scopeRef(e.task_id);
  const noun = (key: NounKey): NarrativeSegment => tx(t(key));
  const say = (key: MessageKey, slots: Record<string, NarrativeSegment> = {}): NarrativeSegment[] =>
    sentence(locale, key, { agent, ...slots });
  let segs: NarrativeSegment[];
  let detail: string | null = null;

  if (has(SCOPED, e.action)) {
    const [key, generic] = SCOPED[e.action];
    segs = say(key, { target: scope ? rf(scope) : noun(generic) });
  } else if (has(OPTIONAL_SCOPE, e.action)) {
    const [plain, scoped] = OPTIONAL_SCOPE[e.action];
    segs = scope ? say(scoped, { target: rf(scope) }) : say(plain);
  } else if (has(JOB, e.action)) {
    // The job id lives in payload.script_request_id; task_id is the parent.
    const [plain, scoped] = JOB[e.action];
    const job = str(p, 'script_request_id');
    const jobSeg = job ? rf(jobRef(job)) : noun('audit.n.noun.job');
    segs = scope ? say(scoped, { job: jobSeg, target: rf(scope) }) : say(plain, { job: jobSeg });
  } else if (has(PLAIN, e.action)) {
    segs = say(PLAIN[e.action]);
  } else {
    segs = describeSpecial();
  }

  switch (e.action) {
    /* --- sessions ------------------------------------------------- */
    case 'session_end': {
      const dur = numOf(p, 'duration_seconds');
      const toks = tokensOf(p);
      detail = detailOf(
        dur != null ? formatDuration(dur, t) : null,
        toks != null && toks > 0
          ? t('audit.detail.tokens', { tokens: formatTokensFor(locale, toks) })
          : null,
      );
      break;
    }

    /* --- task lifecycle ------------------------------------------- */
    case 'completion_report': {
      const status = str(p, 'status');
      const conf = numOf(p, 'confidence');
      detail = detailOf(status, conf != null ? t('audit.detail.confidence', { value: conf }) : null);
      break;
    }
    case 'review_verdict':
      detail = detailOf(str(p, 'verdict'));
      break;
    case 'escalation':
    case 'task_resume_skipped':
    case 'thread_followup_skipped':
    case 'thread_invocation_failed':
    case 'thread_exchange_corrupt':
    case 'thread_deferral_suppressed':
    case 'job_rejected':
    case 'job_run_failed':
    case 'dream_failed':
    case 'dream_timeout':
    case 'work_hour_failed':
    case 'work_hour_timeout':
      detail = detailOf(str(p, 'reason'));
      break;
    case 'escalation_resolved':
      detail = detailOf(str(p, 'decision'));
      break;
    case 'task_cancelled':
      detail = detailOf(str(p, 'rationale'));
      break;
    case 'progress':
      detail = detailOf(str(p, 'message'));
      break;

    /* --- revisits / chains ---------------------------------------- */
    case 'orchestration_step': {
      const step = numOf(p, 'step_number');
      const decision = p['decision'];
      const decAction =
        decision && typeof decision === 'object'
          ? str(decision as Record<string, unknown>, 'action')
          : undefined;
      detail = detailOf(step != null ? t('audit.detail.step', { step }) : null, decAction);
      break;
    }
    case 'chain_auto_advance': {
      const leg = numOf(p, 'leg_index');
      detail = detailOf(
        leg != null ? t('audit.detail.leg', { leg }) : null,
        str(p, 'triggering_verdict'),
      );
      break;
    }
    case 'auto_revisit_of': {
      const attempt = numOf(p, 'attempt');
      detail = detailOf(
        str(p, 'failure_kind'),
        attempt != null ? t('audit.detail.attempt', { attempt }) : null,
      );
      break;
    }
    case 'revisit_spawned': {
      const newRoot = str(p, 'new_root');
      detail = newRoot ? `→ ${newRoot}` : null;
      break;
    }

    /* --- artifacts ------------------------------------------------ */
    case 'artifact_put': {
      const size = numOf(p, 'size_bytes');
      detail = size != null ? formatBytes(size) : null;
      break;
    }

    /* --- agents --------------------------------------------------- */
    case 'agent_backfilled':
    case 'agent_session_reused':
    case 'agent_session_evicted_fallback':
      detail = detailOf(str(p, 'executor'));
      break;

    /* --- learnings ------------------------------------------------ */
    case 'learning_added':
      detail = detailOf(str(p, 'topic'));
      break;
    case 'learning_updated':
      detail = detailOf(str(p, 'id'));
      break;
    case 'learning_promoted':
      detail = detailOf(str(p, 'kb_slug'));
      break;

    /* --- threads -------------------------------------------------- */
    case 'thread_started':
      detail = detailOf(str(p, 'subject'));
      break;
    case 'thread_message_sent':
      detail = detailOf(str(p, 'kind'));
      break;
    case 'thread_dispatch': {
      const team = str(p, 'team');
      detail = detailOf(team ? t('audit.detail.team', { team }) : null);
      break;
    }
    case 'thread_turn_cap_auto_extended': {
      const cap = numOf(p, 'new_cap');
      detail = cap != null ? t('audit.detail.newCap', { cap }) : null;
      break;
    }
    case 'thread_renamed': {
      // THR-209: actor renamed a thread; detail carries old → new title.
      const oldS = str(p, 'old_subject');
      const newS = str(p, 'new_subject');
      if (oldS != null && newS != null) detail = t('audit.detail.renamed', { from: oldS, to: newS });
      break;
    }

    /* --- thread reply delivery (GH-688 Phase 1 Slice C) ---------------- */
    // Payload shapes mirror runtime/infrastructure/database.py reply-delivery
    // store transitions (the single writer of these rows): agent_name,
    // inclusive from_seq/through_seq, 8-char token_prefix, outcome / reason /
    // follow-on result. `agent` is the wake owner.
    case 'thread_reply_wake_created':
    case 'thread_reply_wake_coalesced':
    case 'thread_reply_wake_claimed':
    case 'thread_deferral_catchup_pending':
      detail = detailOf(rangeOf(p, t));
      break;
    case 'thread_reply_wake_settled': {
      const ack = numOf(p, 'acknowledged_through_seq');
      const req = numOf(p, 'required_through_seq');
      detail = detailOf(
        str(p, 'outcome'),
        ack != null ? t('audit.detail.acknowledgedThrough', { seq: ack }) : null,
        req != null ? t('audit.detail.requiredThrough', { seq: req }) : null,
        p['retry_required'] === true ? t('audit.detail.retryRequired') : null,
        str(p, 'follow_on_token_prefix') ? t('audit.detail.followOnMinted') : null,
      );
      break;
    }
    case 'thread_reply_wake_cancelled': {
      const boundary = numOf(p, 'boundary_seq');
      const swept = numOf(p, 'swept_count');
      detail = detailOf(
        str(p, 'reason'),
        boundary != null ? t('audit.detail.discardedThrough', { seq: boundary }) : null,
        swept != null ? t('audit.detail.receiptsRetired', { count: swept }) : null,
      );
      break;
    }
    case 'thread_reply_wake_recovered':
      detail = detailOf(str(p, 'kind'), rangeOf(p, t));
      break;

    /* --- strict mention-led exchange (TASK-5966) ----------------------- */
    // Payload shapes mirror runtime/infrastructure/database.py exchange store
    // seams (the single writer): thread_id, exchange_id, open_seq/close_seq,
    // priority/deferred sets, close_reason, mint_token_prefix. `agent` is
    // the exchange owner (founder) or the deferral owner.
    case 'thread_exchange_opened':
      detail = detailOf(exchangeOf(p, t), rangeOf(p, t));
      break;
    case 'thread_exchange_closed':
      detail = detailOf(
        str(p, 'close_reason'),
        p['suppressed'] === true ? t('audit.detail.noCatchup') : null,
      );
      break;
    case 'thread_deferral_held':
      detail = detailOf(exchangeOf(p, t));
      break;
    case 'thread_deferral_released':
      detail = detailOf(str(p, 'mint_token_prefix'));
      break;
    case 'thread_deferral_catchup_minted':
      detail = detailOf(str(p, 'mint_token_prefix'), rangeOf(p, t));
      break;

    /* --- jobs ----------------------------------------------------- */
    case 'job_submitted':
      detail = detailOf(str(p, 'title'));
      break;
    case 'job_run_completed': {
      const exit = numOf(p, 'exit_code');
      const durMs = numOf(p, 'duration_ms');
      detail = detailOf(
        exit != null ? t('audit.detail.exit', { code: exit }) : null,
        durMs != null ? t('audit.detail.seconds', { s: (durMs / 1000).toFixed(1) }) : null,
      );
      break;
    }

    /* --- dreams --------------------------------------------------- */
    case 'dream_scheduled':
      detail = detailOf(str(p, 'local_date'));
      break;
    case 'dream_completed': {
      const learn = numOf(p, 'new_learnings_count');
      const kb = numOf(p, 'kb_candidate_count');
      detail = detailOf(
        learn != null ? t('audit.detail.learnings', { count: learn }) : null,
        kb != null ? t('audit.detail.kbCandidates', { count: kb }) : null,
      );
      break;
    }

    /* --- working hours -------------------------------------------- */
    case 'work_hour_scheduled':
      detail = detailOf(str(p, 'slot'), str(p, 'mode'));
      break;
    case 'work_hour_spawned': {
      const n = numOf(p, 'spawned_task_count');
      detail = n != null ? t('audit.detail.tasks', { count: n }) : null;
      break;
    }
    case 'work_hour_completed': {
      const n = numOf(p, 'spawned_task_count');
      const r = numOf(p, 'routine_count');
      detail = detailOf(
        n != null ? t('audit.detail.tasks', { count: n }) : null,
        r != null ? t('audit.detail.routines', { count: r }) : null,
      );
      break;
    }
    default:
      break;
  }

  return { segments: segs, detail };

  /** Actions whose sentence shape depends on payload refs, plus the fallback. */
  function describeSpecial(): NarrativeSegment[] {
    switch (e.action) {
      case 'chain_auto_advance': {
        const spawned = str(p, 'spawned_child_id');
        const child = spawned ? rf(taskRef(spawned)) : null;
        if (scope && child) return say('audit.n.chain_auto_advance.onSpawning', { target: rf(scope), child });
        if (scope) return say('audit.n.chain_auto_advance.on', { target: rf(scope) });
        if (child) return say('audit.n.chain_auto_advance.spawning', { child });
        return say('audit.n.chain_auto_advance');
      }
      case 'revisit_of':
      case 'auto_revisit_of': {
        const other = str(p, e.action === 'revisit_of' ? 'predecessor_root' : 'failed_task');
        const target = scope ? rf(scope) : noun('audit.n.noun.task');
        const [plain, withOther]: [MessageKey, MessageKey] =
          e.action === 'revisit_of'
            ? ['audit.n.revisit_of', 'audit.n.revisit_of.of']
            : ['audit.n.auto_revisit_of', 'audit.n.auto_revisit_of.of'];
        return other ? say(withOther, { target, other: rf(taskRef(other)) }) : say(plain, { target });
      }
      case 'escalation_superseded': {
        const successor = str(p, 'successor_root');
        const target = scope ? rf(scope) : noun('audit.n.noun.blockedTask');
        return successor
          ? say('audit.n.escalation_superseded.with', { target, other: rf(taskRef(successor)) })
          : say('audit.n.escalation_superseded', { target });
      }
      case 'artifact_put':
      case 'artifact_delete': {
        const name = str(p, 'name');
        return say(e.action === 'artifact_put' ? 'audit.n.artifact_put' : 'audit.n.artifact_delete', {
          name: name ? tx(name) : noun('audit.n.noun.artifact'),
        });
      }
      case 'agent_managed':
      case 'agent_backfilled': {
        const name = str(p, 'name');
        const verb = str(p, 'action') ?? '';
        const key: MessageKey =
          e.action === 'agent_backfilled'
            ? 'audit.n.agent_backfilled'
            : has(AGENT_MANAGED, verb)
              ? AGENT_MANAGED[verb]
              : 'audit.n.agent_managed.other';
        return say(key, { target: name ? rf(agentRef(name)) : noun('audit.n.noun.agent') });
      }
      case 'thread_participant_added': {
        const who = str(p, 'agent_name');
        const whoSeg = who ? rf(agentRef(who)) : noun('audit.n.noun.participant');
        return scope
          ? say('audit.n.thread_participant_added.to', { who: whoSeg, target: rf(scope) })
          : say('audit.n.thread_participant_added', { who: whoSeg });
      }
      case 'thread_dispatch': {
        const task = str(p, 'task_id');
        const target = str(p, 'target_agent');
        const taskSeg = task ? rf(taskRef(task)) : noun('audit.n.noun.task');
        return target
          ? say('audit.n.thread_dispatch.to', { task: taskSeg, who: rf(agentRef(target)) })
          : say('audit.n.thread_dispatch', { task: taskSeg });
      }
      case 'dream_founder_thread_created': {
        const tid = str(p, 'founder_thread_id');
        return tid
          ? say('audit.n.dream_founder_thread_created.thread', { target: rf(threadRef(tid)) })
          : say('audit.n.dream_founder_thread_created');
      }
      default: {
        /* --- unknown: humanized action name, verbatim otherwise ---- */
        const action = tx(humanize(e.action));
        return scope
          ? say('audit.n.unknown.on', { action, target: rf(scope) })
          : say('audit.n.unknown', { action });
      }
    }
  }
}

/** Flatten a narrative to plain text (subject + prose + ref labels). */
export function narrativeText(n: AuditNarrative): string {
  return n.segments
    .map((s) => (s.kind === 'ref' ? s.ref.label : s.text))
    .join('');
}
