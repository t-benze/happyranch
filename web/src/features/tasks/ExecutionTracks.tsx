/**
 * Execution subtasks with fan-out tracks (THR-358, founder design
 * `THR-358-20261118T040019Z-task-detail-fanout-tracks.html`).
 *
 * A fan-out pipeline carrier (a child whose recall node carries `track`) is
 * drawn as a lighter group header with a step strip; its legs sit inside as
 * ordinary step rows labelled "Step N · agent". The carrier ID stays on the
 * header and the full title is its tooltip. Ordinary children keep the
 * caller's existing row unchanged. Display-only: no request or mutation.
 */
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '@/design-system/primitives/Card';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { STATUS_TONE, StatusBadge, type TaskStatus } from '@/design-system/patterns/StatusBadge';
import { useTasksRoutes } from '@/hooks/tasks';
import { useTranslation } from '@/hooks/i18n';
import { IdentityName } from '@/shared/identities/IdentityName';
import type { TaskRecallNode } from '@/lib/api/types';
import type { MessageKey, MessageParams } from '@/lib/i18n';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/* ================================================================
 * Derivation (pure)
 * ================================================================ */

export type StepState = 'done' | 'run' | 'wait' | 'fail' | 'pend' | 'cancel' | 'none';

export interface TrackStep {
  agent: string | null;
  node: TaskRecallNode | null;
  state: StepState;
  /** First job a waiting step is blocked on (state `wait`). */
  job: string | null;
  /** The carrier ended (failed) at this step while the step itself
   * completed, e.g. a verdict mismatch: the row keeps the child's real
   * status and shows its verdict. */
  endedHere: boolean;
}

export interface TrackHeaderStatus {
  status: TaskStatus;
  qualifier: string;
  /** 1-based step at which a failed/cancelled track ended. */
  endedAt: number | null;
}

/** Carrier lifecycle that ends a track even when every created step completed. */
function carrierEnd(status: TaskStatus): 'fail' | 'cancel' | null {
  if (status === 'failed') return 'fail';
  if (status === 'cancelled' || status === 'superseded') return 'cancel';
  return null;
}

/** True when the carrier ended and its remaining-step plan is not recorded. */
export function planUnavailable(node: TaskRecallNode): boolean {
  return node.status !== 'completed' && (node.track?.planned_agents ?? null) == null;
}

export function isTrackNode(node: TaskRecallNode): boolean {
  return node.track != null && typeof node.track === 'object';
}

function stepState(node: TaskRecallNode): StepState {
  if (node.status === 'in_progress' && node.block_kind === 'blocked_on_job') return 'wait';
  switch (node.status) {
    case 'completed':
      return 'done';
    case 'failed':
      return 'fail';
    case 'cancelled':
    case 'superseded':
      return 'cancel';
    case 'pending':
      return 'pend';
    default:
      return 'run';
  }
}

/**
 * Created legs in order, then not-yet-created legs from the carrier's plan.
 * A retry child (revisit of an earlier leg) takes that leg's place.
 */
export function trackSteps(node: TaskRecallNode): TrackStep[] {
  const created: TaskRecallNode[] = [];
  for (const child of node.children ?? []) {
    const replaced = child.revisit_of_task_id
      ? created.findIndex((c) => c.task_id === child.revisit_of_task_id)
      : -1;
    if (replaced >= 0) created[replaced] = child;
    else created.push(child);
  }
  const steps: TrackStep[] = created.map((c) => ({
    agent: c.assigned_agent ?? null,
    node: c,
    state: stepState(c),
    job: c.blocked_on_job_ids?.[0] ?? null,
    endedHere: false,
  }));
  // A failed/cancelled carrier wins over its steps' lifecycle: a completed
  // reviewer with REQUEST_CHANGES (verdict mismatch) or a failed next-leg
  // admission ends the track at the last created step.
  const end = carrierEnd(node.status);
  const last = steps[steps.length - 1];
  if (end && last && !steps.some((s) => s.state === 'fail' || s.state === 'cancel')) {
    if (end === 'fail') {
      last.state = 'fail';
      last.endedHere = last.node?.status === 'completed';
    } else if (last.state !== 'done') {
      last.state = 'cancel';
    }
  }
  const planned = node.track?.planned_agents ?? null;
  if (planned) {
    for (let i = steps.length; i < planned.length; i += 1) {
      steps.push({ agent: planned[i] ?? null, node: null, state: 'none', job: null, endedHere: false });
    }
  }
  return steps;
}

/**
 * Header status: any failed step → failed · at Step N; else any cancelled →
 * cancelled · at Step N; else a failed/cancelled carrier → that status · at
 * its last created step; else all done → completed · N/N steps; else the last
 * created step decides (waiting → in_progress · Step K waiting on JOB-…,
 * queued → pending · Step K queued, otherwise in_progress · Step K of N).
 */
export function trackHeaderStatus(
  steps: TrackStep[],
  t: Translate,
  carrierStatus?: TaskStatus,
): TrackHeaderStatus {
  const n = steps.length;
  const fi = steps.findIndex((s) => s.state === 'fail');
  if (fi >= 0) return { status: 'failed', qualifier: t('tasks.detail.tracks.atStep', { k: fi + 1 }), endedAt: fi + 1 };
  const ci = steps.findIndex((s) => s.state === 'cancel');
  if (ci >= 0) return { status: 'cancelled', qualifier: t('tasks.detail.tracks.atStep', { k: ci + 1 }), endedAt: ci + 1 };
  const createdCount = steps.filter((s) => s.state !== 'none').length;
  const end = carrierStatus ? carrierEnd(carrierStatus) : null;
  if (end) {
    const k = Math.max(createdCount, 1);
    return {
      status: end === 'fail' ? 'failed' : 'cancelled',
      qualifier: t('tasks.detail.tracks.atStep', { k }),
      endedAt: k,
    };
  }
  if (n > 0 && steps.every((s) => s.state === 'done')) {
    return { status: 'completed', qualifier: t('tasks.detail.tracks.allDone', { n }), endedAt: null };
  }
  const k = steps.filter((s) => s.state !== 'none').length;
  const cur = steps[k - 1];
  if (cur?.state === 'wait' && cur.job) {
    return { status: 'in_progress', qualifier: t('tasks.detail.tracks.waitingOn', { k, job: cur.job }), endedAt: null };
  }
  if (cur?.state === 'pend') {
    return { status: 'pending', qualifier: t('tasks.detail.tracks.queued', { k }), endedAt: null };
  }
  return { status: 'in_progress', qualifier: t('tasks.detail.tracks.stepOf', { k: Math.max(k, 1), n: Math.max(n, 1) }), endedAt: null };
}

function firstLine(text: string | null | undefined): string {
  if (!text) return '';
  const line = text.split('\n').find((l) => l.trim() !== '') ?? '';
  return line.replace(/\s+/g, ' ').trim();
}

/**
 * Prefix shared by every sibling track title, cut back to the last `:` `·`
 * `—` `-` separator; only kept when 16+ characters and 2+ tracks.
 */
export function sharedPrefix(titles: string[]): string {
  if (titles.length < 2) return '';
  let p = titles[0] ?? '';
  for (const title of titles) {
    while (p && !title.startsWith(p)) p = p.slice(0, -1);
  }
  const m = p.match(/^(.*[:·—-]\s)/);
  const cut = m?.[1] ?? '';
  return cut.length >= 16 ? cut : '';
}

const ROLE_KEY: Record<string, MessageKey> = {
  dev_agent: 'tasks.detail.tracks.role.dev',
  code_reviewer: 'tasks.detail.tracks.role.review',
  qa_engineer: 'tasks.detail.tracks.role.qa',
};

/* ================================================================
 * Icons (decorative; state is also carried by text/aria)
 * ================================================================ */

function StepIcon({ state }: { state: StepState }): JSX.Element {
  const common = { viewBox: '0 0 16 16', className: 'h-3 w-3 shrink-0', 'aria-hidden': true } as const;
  switch (state) {
    case 'done':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3.5 8.5l3 3 6-7" />
        </svg>
      );
    case 'run':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth={1.6} opacity={0.45} />
          <circle cx="8" cy="8" r="3.4" fill="currentColor" />
        </svg>
      );
    case 'wait':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round">
          <circle cx="8" cy="8" r="6" />
          <path d="M8 4.8V8l2.2 1.6" />
        </svg>
      );
    case 'fail':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
          <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
        </svg>
      );
    case 'pend':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7}>
          <circle cx="8" cy="8" r="5.5" />
        </svg>
      );
    case 'cancel':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round">
          <circle cx="8" cy="8" r="5.5" />
          <path d="M4.2 11.8l7.6-7.6" />
        </svg>
      );
    default:
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.6} strokeDasharray="2.4 2.2">
          <circle cx="8" cy="8" r="5.5" />
        </svg>
      );
  }
}

function TrackGlyph(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="text-text-secondary h-4 w-4 shrink-0 max-sm:mt-0.5"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M6 8.2v3.3a3 3 0 0 0 3 3h6.8" />
      <path d="M15.8 6H8.2" />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`text-text-muted h-3.5 w-3.5 shrink-0 transition-transform max-sm:mt-0.5 ${open ? '' : '-rotate-90'}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/* ================================================================
 * Step strip + step rows
 * ================================================================ */

const CHIP_CLASS: Record<StepState, string> = {
  done: 'text-accent-text bg-accent-soft border-accent-soft',
  run: 'text-info bg-info-soft border-info-soft font-semibold',
  wait: 'text-info bg-info-soft border-info-soft font-semibold',
  fail: 'text-danger bg-danger-soft border-danger-soft font-semibold',
  pend: 'text-text-secondary bg-surface-raised border-border-strong',
  cancel: 'text-text-secondary bg-surface-sunken border-border-default',
  none: 'text-text-secondary border-dashed border-border-strong',
};

const STATE_WORD: Record<StepState, MessageKey> = {
  done: 'tasks.detail.tracks.state.done',
  run: 'tasks.detail.tracks.state.run',
  wait: 'tasks.detail.tracks.state.wait',
  fail: 'tasks.detail.tracks.state.fail',
  pend: 'tasks.detail.tracks.state.pend',
  cancel: 'tasks.detail.tracks.state.cancel',
  none: 'tasks.detail.tracks.state.none',
};

function roleLabel(agent: string | null, t: Translate): ReactNode {
  if (!agent) return '—';
  const key = ROLE_KEY[agent];
  return key ? t(key) : <IdentityName canonicalId={agent} />;
}

function StepStrip({ steps, unavailable }: { steps: TrackStep[]; unavailable: boolean }): JSX.Element {
  const { t } = useTranslation();
  return (
    <ol className="mt-1.5 flex flex-wrap items-center gap-1" aria-label={t('tasks.detail.tracks.stepsAria')}>
      {steps.map((s, i) => {
        const word = t(STATE_WORD[s.state]);
        const step = t('tasks.detail.tracks.step', { n: i + 1 });
        const roleText = s.agent ? (ROLE_KEY[s.agent] ? t(ROLE_KEY[s.agent]!) : s.agent) : '—';
        return (
          <li key={s.node?.task_id ?? `planned-${i}`} className="contents">
            <span
              className={`text-caption inline-flex items-center gap-1 whitespace-nowrap rounded-full border py-px pr-2 pl-1 ${CHIP_CLASS[s.state]}`}
              aria-label={`${step} · ${roleText}: ${word}`}
              title={`${step} · ${s.agent ?? '—'} · ${word}`}
            >
              <StepIcon state={s.state} />
              <span className={s.state === 'cancel' ? 'line-through' : undefined}>{roleLabel(s.agent, t)}</span>
            </span>
            {(i < steps.length - 1 || unavailable) && (
              <span className="text-text-muted text-overline font-normal" aria-hidden>
                →
              </span>
            )}
          </li>
        );
      })}
      {unavailable && (
        <li className="contents">
          <span
            className={`text-caption inline-flex items-center gap-1 whitespace-nowrap rounded-full border py-px pr-2 pl-1 ${CHIP_CLASS.none}`}
            aria-label={t('tasks.detail.tracks.unavailable')}
            title={t('tasks.detail.tracks.unavailable')}
          >
            <StepIcon state="none" />
            <span>?</span>
          </span>
        </li>
      )}
    </ol>
  );
}

const NODE_CLASS: Record<StepState, string> = {
  done: 'bg-accent-default h-2 w-2',
  run: 'bg-info ring-info-soft h-2 w-2 ring-3',
  wait: 'bg-surface-raised border-info ring-info-soft h-2.5 w-2.5 border-2 ring-3',
  fail: 'bg-danger h-2 w-2',
  pend: 'bg-border-strong h-2 w-2',
  cancel: 'border-text-muted h-2.5 w-2.5 border-2',
  none: 'border-text-muted h-2.5 w-2.5 border border-dashed',
};

function StepRow({
  step,
  index,
  endedAt,
}: {
  step: TrackStep;
  index: number;
  endedAt: number | null;
}): JSX.Element {
  const routes = useTasksRoutes();
  const { t } = useTranslation();
  const label = (
    <p className="text-body-sm truncate">
      <span className={`font-semibold ${step.node ? 'text-text-primary' : 'text-text-secondary'}`}>
        {t('tasks.detail.tracks.step', { n: index + 1 })}
      </span>
      <span className="text-text-secondary">
        {' · '}
        {step.agent ? <IdentityName canonicalId={step.agent} /> : '—'}
      </span>
    </p>
  );
  const node = step.node;
  const outcome = node?.output_summary?.replace(/\s+/g, ' ').trim() ?? '';
  const why = node
    ? outcome
    : endedAt != null
      ? t('tasks.detail.tracks.ended', { k: endedAt })
      : t('tasks.detail.tracks.createdWhen', { k: index });
  return (
    <div className="border-border-default flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b px-1.5 py-2 last:border-b-0 sm:flex-nowrap">
      <span className="flex w-4 shrink-0 justify-center" aria-hidden>
        <span className={`inline-block rounded-full ${NODE_CLASS[step.state]}`} />
      </span>
      <span className="min-w-0 flex-1">
        {label}
        {why && (
          <p
            className={`text-caption mt-px truncate ${step.state === 'fail' ? 'text-danger' : 'text-text-secondary'}`}
            title={node ? outcome : undefined}
          >
            {why}
          </p>
        )}
      </span>
      <span className="flex w-full min-w-0 flex-wrap items-center gap-2.5 pl-7 sm:w-auto sm:shrink-0 sm:pl-0">
        {node ? (
          <>
            <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
            {step.state === 'fail' && typeof node.verdict === 'string' && node.verdict && (
              <span className="text-overline text-danger border-danger-soft inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono">
                {t('tasks.detail.tracks.verdict', { verdict: node.verdict })}
              </span>
            )}
            {step.state === 'wait' && step.job ? (
              <HeaderBadge status={node.status} qualifier={t('tasks.detail.tracks.waitShort', { job: step.job })} />
            ) : (
              <StatusBadge status={node.status} />
            )}
          </>
        ) : (
          <span className="text-overline text-text-secondary inline-flex items-center rounded-full border border-dashed border-border-strong px-2.5 py-0.5">
            {t('tasks.detail.tracks.notCreated')}
          </span>
        )}
      </span>
    </div>
  );
}

function UnavailableRow(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="border-border-default flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b px-1.5 py-2 last:border-b-0 sm:flex-nowrap">
      <span className="flex w-4 shrink-0 justify-center" aria-hidden>
        <span className={`inline-block rounded-full ${NODE_CLASS.none}`} />
      </span>
      <span className="min-w-0 flex-1">
        <p className="text-body-sm text-text-secondary truncate font-semibold">{t('tasks.detail.tracks.unavailable')}</p>
        <p className="text-caption text-text-secondary mt-px truncate">{t('tasks.detail.tracks.unavailableWhy')}</p>
      </span>
    </div>
  );
}

/* ================================================================
 * Track group
 * ================================================================ */

function HeaderBadge({ status, qualifier }: { status: TaskStatus; qualifier: string }): JSX.Element {
  const dot = status === 'in_progress' || status === 'completed' || status === 'pending';
  return (
    <StatusBadge
      tone={STATUS_TONE[status]}
      dot={dot}
      label={
        <>
          {status}
          <span className="min-w-0 break-words font-normal opacity-70">{`· ${qualifier}`}</span>
        </>
      }
    />
  );
}

function TrackGroup({
  node,
  prefix,
  open,
  onToggle,
}: {
  node: TaskRecallNode;
  prefix: string;
  open: boolean;
  onToggle: () => void;
}): JSX.Element {
  const routes = useTasksRoutes();
  const { t } = useTranslation();
  const steps = trackSteps(node);
  const header = trackHeaderStatus(steps, t, node.status);
  const unavailable = planUnavailable(node);
  const title = firstLine(node.brief);
  const name = prefix && title.startsWith(prefix) ? title.slice(prefix.length) : title;
  const stepsId = `track-steps-${node.task_id}`;
  const container = t('tasks.detail.tracks.container', { id: node.task_id });
  return (
    <div className="border-border-default border-b last:border-b-0" data-track-state={header.status}>
      <div className="flex flex-col gap-x-3.5 gap-y-1.5 px-1.5 pt-3 pb-2 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            className="text-text-primary focus-visible:outline-info flex max-w-full min-w-0 cursor-pointer items-center gap-2 rounded-sm text-left focus-visible:outline-2 focus-visible:outline-offset-2 max-sm:items-start"
            aria-expanded={open}
            aria-controls={stepsId}
            title={title}
            onClick={onToggle}
          >
            <Chevron open={open} />
            <TrackGlyph />
            <span className="text-2xs text-text-secondary border-border-strong shrink-0 rounded-full border px-1.5 font-semibold tracking-wider uppercase max-sm:mt-0.5">
              {t('tasks.detail.tracks.label')}
            </span>
            <span className="text-nav min-w-0 truncate font-semibold max-sm:line-clamp-2 max-sm:whitespace-normal">
              {name || '—'}
            </span>
          </button>
          <div className="ml-5">
            <StepStrip steps={steps} unavailable={unavailable} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2.5 pt-px max-sm:ml-5 sm:shrink-0 sm:justify-end">
          <HeaderBadge status={header.status} qualifier={header.qualifier} />
          <Link
            to={routes.detail(node.task_id)}
            className="text-2xs text-text-secondary hover:text-info focus-visible:outline-info rounded-sm font-mono whitespace-nowrap hover:underline focus-visible:outline-2 focus-visible:outline-offset-2"
            title={container}
            aria-label={container}
          >
            {node.task_id}
          </Link>
        </div>
      </div>
      <div
        id={stepsId}
        hidden={!open}
        className="mb-2.5 ml-1.5 pl-2.5 sm:ml-3 sm:pl-4"
      >
        {steps.map((s, i) => (
          <StepRow key={s.node?.task_id ?? `planned-${i}`} step={s} index={i} endedAt={header.endedAt} />
        ))}
        {unavailable && <UnavailableRow />}
      </div>
    </div>
  );
}

/* ================================================================
 * List
 * ================================================================ */

export function ExecutionTrackList({
  nodes,
  renderPlain,
}: {
  nodes: TaskRecallNode[];
  renderPlain: (node: TaskRecallNode) => ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const tracks = nodes.filter(isTrackNode);
  const plainCount = nodes.length - tracks.length;
  const prefix = sharedPrefix(tracks.map((n) => firstLine(n.brief)));
  const many = nodes.length >= 6;
  // Completed tracks start collapsed only when the parent has 6+ children.
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () =>
      new Set(
        many
          ? tracks
              .filter((n) => trackHeaderStatus(trackSteps(n), t, n.status).status === 'completed')
              .map((n) => n.task_id)
          : [],
      ),
  );
  const anyCollapsed = tracks.some((n) => collapsed.has(n.task_id));
  const toggle = (id: string): void =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const count = [
    tracks.length ? t('tasks.detail.tracks.countTracks', { count: tracks.length }) : '',
    plainCount ? t('tasks.detail.tracks.countSubtasks', { count: plainCount }) : '',
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <section className="mt-6">
      <div className="mb-2.5 flex flex-wrap items-center gap-2.5">
        <h3 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
          {t('tasks.detail.subtasks.heading')}
        </h3>
        <span className="text-caption text-text-secondary tabular-nums">{count}</span>
        {tracks.length > 1 && (
          <>
            <span className="flex-1" />
            <button
              type="button"
              className="text-caption text-info focus-visible:outline-info cursor-pointer rounded-sm px-1 py-0.5 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2"
              onClick={() =>
                setCollapsed(anyCollapsed ? new Set() : new Set(tracks.map((n) => n.task_id)))
              }
            >
              {anyCollapsed ? t('tasks.detail.tracks.expandAll') : t('tasks.detail.tracks.collapseAll')}
            </button>
          </>
        )}
      </div>
      {prefix && (
        <p className="text-caption text-text-secondary -mt-1 mb-2.5">
          {t('tasks.detail.tracks.prefix')}{' '}
          <q className="text-text-primary font-mono">{prefix.trim()}</q>{' '}
          {t('tasks.detail.tracks.prefixTail')}
        </p>
      )}
      <Card padding="none" className="px-1.5 py-0.5">
        {nodes.map((n) =>
          isTrackNode(n) ? (
            <TrackGroup
              key={n.task_id}
              node={n}
              prefix={prefix}
              open={!collapsed.has(n.task_id)}
              onToggle={() => toggle(n.task_id)}
            />
          ) : (
            <div key={n.task_id} className="border-border-default border-b px-1.5 py-1 last:border-b-0">
              {renderPlain(n)}
            </div>
          ),
        )}
      </Card>
    </section>
  );
}
