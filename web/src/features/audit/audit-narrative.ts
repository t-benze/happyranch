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
 * The per-action mapping mirrors runtime/infrastructure/audit_logger.py — the
 * single writer of audit rows — so payload keys here match what is stored.
 *
 * Kept free of React so it is unit-testable in isolation; AuditTimeline maps
 * the returned segments to <Link>s (entity refs) and styled text.
 */
import type { AuditEntry } from '@/lib/api/types';
import { formatTokens } from '@/lib/format';

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

// `formatTokens` is the canonical compact display-number formatter. It was
// relocated to the neutral @/lib/format module (THR-099 number-overflow fix);
// this feature now consumes it via the import above and no longer owns the def.

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
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
/*  Main transform                                                    */
/* ------------------------------------------------------------------ */

export function describeAuditEntry(e: AuditEntry): AuditNarrative {
  const p = e.payload ?? {};
  const subject = sub(e.agent ?? 'The system');
  const scope = scopeRef(e.task_id);
  const segs: NarrativeSegment[] = [subject];
  let detail: string | null = null;

  /** Append " <verb> " + the scope ref (or a generic noun when unlinkable). */
  function withScope(verb: string, generic: string): void {
    if (scope) segs.push(tx(` ${verb} `), rf(scope), tx('.'));
    else segs.push(tx(` ${verb} ${generic}.`));
  }

  switch (e.action) {
    /* --- sessions ------------------------------------------------- */
    case 'session_start':
      withScope('started working on', 'a task');
      break;
    case 'session_end': {
      withScope('wrapped up', 'a task');
      const dur = numOf(p, 'duration_seconds');
      const toks = tokensOf(p);
      detail = detailOf(
        dur != null ? formatDuration(dur) : null,
        toks != null && toks > 0 ? `${formatTokens(toks)} tokens` : null,
      );
      break;
    }

    /* --- task lifecycle ------------------------------------------- */
    case 'completion_report': {
      withScope('completed', 'a task');
      const status = str(p, 'status');
      const conf = numOf(p, 'confidence');
      detail = detailOf(status, conf != null ? `confidence ${conf}` : null);
      break;
    }
    case 'review_verdict': {
      withScope('reviewed', 'a task');
      detail = detailOf(str(p, 'verdict'));
      break;
    }
    case 'escalation': {
      withScope('escalated', 'a task');
      detail = detailOf(str(p, 'reason'));
      break;
    }
    case 'daemon_restart_failure':
      if (scope) segs.push(tx(' — '), rf(scope), tx(' failed after a daemon restart.'));
      else segs.push(tx(' — a task failed after a daemon restart.'));
      break;
    case 'escalation_resolved': {
      withScope('resolved an escalation on', 'a task');
      detail = detailOf(str(p, 'decision'));
      break;
    }
    case 'task_cancelled': {
      withScope('cancelled', 'a task');
      detail = detailOf(str(p, 'rationale'));
      break;
    }
    case 'progress': {
      withScope('reported progress on', 'a task');
      detail = detailOf(str(p, 'message'));
      break;
    }
    case 'task_blocked_on_jobs':
      withScope('blocked', 'a task');
      if (scope) segs.splice(segs.length - 1, 1, tx(' on jobs.'));
      break;
    case 'task_resumed_from_jobs':
      withScope('resumed', 'a task');
      if (scope) segs.splice(segs.length - 1, 1, tx(' after its jobs finished.'));
      break;
    case 'task_resume_skipped': {
      withScope('skipped resuming', 'a task');
      detail = detailOf(str(p, 'reason'));
      break;
    }

    /* --- revisits / chains ---------------------------------------- */
    case 'orchestration_step': {
      withScope('advanced', 'a task');
      const step = numOf(p, 'step_number');
      const decision = p['decision'];
      const decAction =
        decision && typeof decision === 'object'
          ? str(decision as Record<string, unknown>, 'action')
          : undefined;
      detail = detailOf(step != null ? `step ${step}` : null, decAction);
      break;
    }
    case 'chain_auto_advance': {
      const spawned = str(p, 'spawned_child_id');
      segs.push(tx(' auto-advanced the chain'));
      if (scope) segs.push(tx(' on '), rf(scope));
      if (spawned) segs.push(tx(', spawning '), rf(taskRef(spawned)));
      segs.push(tx('.'));
      const leg = numOf(p, 'leg_index');
      detail = detailOf(leg != null ? `leg ${leg}` : null, str(p, 'triggering_verdict'));
      break;
    }
    case 'revisit_of': {
      const pred = str(p, 'predecessor_root');
      if (scope) segs.push(tx(' opened '), rf(scope));
      else segs.push(tx(' opened a task'));
      segs.push(tx(' as a revisit'));
      if (pred) segs.push(tx(' of '), rf(taskRef(pred)));
      segs.push(tx('.'));
      break;
    }
    case 'auto_revisit_of': {
      const failed = str(p, 'failed_task');
      if (scope) segs.push(tx(' opened '), rf(scope));
      else segs.push(tx(' opened a task'));
      segs.push(tx(' as an automatic retry'));
      if (failed) segs.push(tx(' of '), rf(taskRef(failed)));
      segs.push(tx('.'));
      const attempt = numOf(p, 'attempt');
      detail = detailOf(str(p, 'failure_kind'), attempt != null ? `attempt ${attempt}` : null);
      break;
    }
    case 'revisit_spawned': {
      const newRoot = str(p, 'new_root');
      withScope('spawned a revisit from', 'a task');
      detail = newRoot ? `→ ${newRoot}` : null;
      break;
    }
    case 'escalation_superseded': {
      const successor = str(p, 'successor_root');
      if (scope) segs.push(tx(' superseded '), rf(scope));
      else segs.push(tx(' superseded a blocked task'));
      if (successor) segs.push(tx(' with '), rf(taskRef(successor)));
      segs.push(tx('.'));
      break;
    }

    /* --- artifacts ------------------------------------------------ */
    case 'artifact_put': {
      const name = str(p, 'name');
      segs.push(tx(` published ${name ?? 'an artifact'}.`));
      const size = numOf(p, 'size_bytes');
      detail = size != null ? formatBytes(size) : null;
      break;
    }
    case 'artifact_delete': {
      const name = str(p, 'name');
      segs.push(tx(` deleted ${name ?? 'an artifact'}.`));
      break;
    }

    /* --- agents --------------------------------------------------- */
    case 'agent_managed': {
      const verbMap: Record<string, string> = {
        enroll: 'enrolled',
        update: 'updated',
        terminate: 'terminated',
      };
      const verb = verbMap[str(p, 'action') ?? ''] ?? 'managed';
      const name = str(p, 'name');
      segs.push(tx(` ${verb} agent `));
      segs.push(name ? rf(agentRef(name)) : tx('an agent'));
      segs.push(tx('.'));
      break;
    }
    case 'agent_backfilled': {
      const name = str(p, 'name');
      segs.push(tx(' backfilled agent '));
      segs.push(name ? rf(agentRef(name)) : tx('an agent'));
      segs.push(tx('.'));
      detail = detailOf(str(p, 'executor'));
      break;
    }

    /* --- learnings ------------------------------------------------ */
    case 'learning_added': {
      if (scope) segs.push(tx(' recorded a learning on '), rf(scope), tx('.'));
      else segs.push(tx(' recorded a learning.'));
      detail = detailOf(str(p, 'topic'));
      break;
    }
    case 'learning_updated':
      segs.push(tx(' updated a learning.'));
      detail = detailOf(str(p, 'id'));
      break;
    case 'learning_promoted':
      segs.push(tx(' promoted a learning to the knowledge base.'));
      detail = detailOf(str(p, 'kb_slug'));
      break;

    /* --- threads -------------------------------------------------- */
    case 'thread_started': {
      withScope('started thread', 'a thread');
      detail = detailOf(str(p, 'subject'));
      break;
    }
    case 'thread_message_sent': {
      withScope('sent a message in', 'a thread');
      detail = detailOf(str(p, 'kind'));
      break;
    }
    case 'thread_decline_consumed':
      withScope('declined to participate in', 'a thread');
      break;
    case 'thread_participant_added': {
      const who = str(p, 'agent_name');
      segs.push(tx(' added '));
      segs.push(who ? rf(agentRef(who)) : tx('a participant'));
      if (scope) segs.push(tx(' to '), rf(scope));
      segs.push(tx('.'));
      break;
    }
    case 'thread_dispatch': {
      const task = str(p, 'task_id');
      const target = str(p, 'target_agent');
      segs.push(tx(' dispatched '));
      segs.push(task ? rf(taskRef(task)) : tx('a task'));
      if (target) segs.push(tx(' to '), rf(agentRef(target)));
      segs.push(tx('.'));
      detail = detailOf(str(p, 'team') ? `team ${str(p, 'team')}` : null);
      break;
    }
    case 'agent_session_reused': {
      withScope('resumed its session in', 'a thread');
      detail = detailOf(str(p, 'executor'));
      break;
    }
    case 'agent_session_evicted_fallback': {
      withScope('rebuilt a fresh session in', 'a thread');
      detail = detailOf(str(p, 'executor'));
      break;
    }
    case 'thread_task_followup_enqueued':
      withScope('enqueued a thread follow-up on', 'a task');
      break;
    case 'thread_followup_skipped': {
      withScope('skipped a thread follow-up on', 'a task');
      detail = detailOf(str(p, 'reason'));
      break;
    }
    case 'thread_turn_cap_auto_extended': {
      withScope('extended the turn cap on', 'a task');
      const cap = numOf(p, 'new_cap');
      detail = cap != null ? `new cap ${cap}` : null;
      break;
    }
    case 'thread_archived':
      withScope('archived', 'a thread');
      break;
    case 'thread_resumed':
      withScope('resumed', 'a thread');
      break;
    case 'thread_invocation_failed': {
      if (scope) segs.push(tx(' — a thread invocation failed in '), rf(scope), tx('.'));
      else segs.push(tx(' — a thread invocation failed.'));
      detail = detailOf(str(p, 'reason'));
      break;
    }

    /* --- jobs ----------------------------------------------------- */
    case 'job_submitted': {
      pushJob(' submitted ', 'on');
      detail = detailOf(str(p, 'title'));
      break;
    }
    case 'job_rejected': {
      pushJob(' rejected ', 'on');
      detail = detailOf(str(p, 'reason'));
      break;
    }
    case 'job_run_started':
      pushJob(' started running ', 'on');
      break;
    case 'job_auto_started':
      pushJob(' auto-started ', 'on');
      break;
    case 'job_run_completed': {
      pushJob(' finished ', 'on');
      const exit = numOf(p, 'exit_code');
      const durMs = numOf(p, 'duration_ms');
      detail = detailOf(
        exit != null ? `exit ${exit}` : null,
        durMs != null ? `${(durMs / 1000).toFixed(1)}s` : null,
      );
      break;
    }
    case 'job_run_failed': {
      pushJob(' — job ', 'on', ' failed.');
      detail = detailOf(str(p, 'reason'));
      break;
    }
    case 'job_stopped':
      pushJob(' stopped ', 'for');
      break;

    /* --- dreams --------------------------------------------------- */
    case 'dream_scheduled': {
      segs.push(tx(' scheduled a dream.'));
      detail = detailOf(str(p, 'local_date'));
      break;
    }
    case 'dream_started':
      segs.push(tx(' started a dream.'));
      break;
    case 'dream_completed': {
      segs.push(tx(' completed a dream.'));
      const learn = numOf(p, 'new_learnings_count');
      const kb = numOf(p, 'kb_candidate_count');
      detail = detailOf(
        learn != null ? `${learn} learnings` : null,
        kb != null ? `${kb} KB candidates` : null,
      );
      break;
    }
    case 'dream_failed':
      segs.push(tx(' — a dream failed.'));
      detail = detailOf(str(p, 'reason'));
      break;
    case 'dream_timeout':
      segs.push(tx(' — a dream timed out.'));
      detail = detailOf(str(p, 'reason'));
      break;
    case 'dream_founder_thread_created': {
      const tid = str(p, 'founder_thread_id');
      segs.push(tx(' opened a founder thread from a dream'));
      if (tid) segs.push(tx(' ('), rf(threadRef(tid)), tx(')'));
      segs.push(tx('.'));
      break;
    }

    /* --- working hours -------------------------------------------- */
    case 'work_hour_scheduled': {
      segs.push(tx(' scheduled a working-hour wake.'));
      detail = detailOf(str(p, 'slot'), str(p, 'mode'));
      break;
    }
    case 'work_hour_started':
      segs.push(tx(' started a working-hour wake.'));
      break;
    case 'work_hour_spawned': {
      segs.push(tx(' spawned working-hour tasks.'));
      const n = numOf(p, 'spawned_task_count');
      detail = n != null ? `${n} tasks` : null;
      break;
    }
    case 'work_hour_completed': {
      segs.push(tx(' completed a working-hour wake.'));
      const n = numOf(p, 'spawned_task_count');
      const r = numOf(p, 'routine_count');
      detail = detailOf(
        n != null ? `${n} tasks` : null,
        r != null ? `${r} routines` : null,
      );
      break;
    }
    case 'work_hour_failed':
      segs.push(tx(' — a working-hour wake failed.'));
      detail = detailOf(str(p, 'reason'));
      break;
    case 'work_hour_timeout':
      segs.push(tx(' — a working-hour wake timed out.'));
      detail = detailOf(str(p, 'reason'));
      break;

    /* --- unknown -------------------------------------------------- */
    default:
      if (scope) segs.push(tx(` ${humanize(e.action)} on `), rf(scope), tx('.'));
      else segs.push(tx(` ${humanize(e.action)}.`));
      break;
  }

  return { segments: segs, detail };

  /** Push a job-id'd predicate: "<verb><JOB> <prep> <parent-task><tail>".
   *  The job id lives in payload.script_request_id; task_id is the parent. */
  function pushJob(verb: string, prep: string, tail = '.'): void {
    const job = str(p, 'script_request_id');
    segs.push(tx(verb));
    segs.push(job ? rf(jobRef(job)) : tx('a job'));
    if (scope) segs.push(tx(` ${prep} `), rf(scope));
    segs.push(tx(tail));
  }
}

/** Flatten a narrative to plain text (subject + prose + ref labels). */
export function narrativeText(n: AuditNarrative): string {
  return n.segments
    .map((s) => (s.kind === 'ref' ? s.ref.label : s.text))
    .join('');
}
