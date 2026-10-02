/**
 * Task detail — Direction-A Pasture, full-page surface.
 *
 * Reached via the `tasks/:task_id` route (mirrors the Jobs surface's
 * `jobs/:job_id -> JobDetailPage`). A '‹ All tasks' back-nav returns to the
 * roots list; the Tasks list navigates here rather than opening an overlay.
 *
 * Shows the task identity card (Pasture card style), the chain timeline
 * (current/done/blocked nodes, blocked node names its blocker), the brief,
 * execution subtasks (from recall), live events, and jobs cross-link.
 *
 * Uses ds.css .card styling (bg-surface, rounded-lg, shadow-pasture-sm).
 * Title in serif font-display.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/design-system/primitives/Button';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { Markdown } from '@/design-system/patterns/Markdown';
import { useTask, useTaskRecall, useTasksRoutes } from '@/hooks/tasks';
import { useJobsList } from '@/hooks/jobs';
import { useTranslation } from '@/hooks/i18n';
import type { Locale, MessageKey, MessageParams } from '@/lib/i18n';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes getTask (useTask deliberately drops active_chain); routed direct per THR-011 founder ruling (option 3), pending a future hook
import { getTask } from '@/lib/api/tasks';
import type {
  ActiveChainResponse,
  EscalationReason,
  JobRecord,
  TaskRecallNode,
  TaskRecord,
  WorkStatusResponse,
} from '@/lib/api/types';
import { TaskRecallTree } from './TaskRecallTree';
import { TaskEventsLog } from './TaskEventsLog';
import { FanoutBand, type FanoutMode } from './FanoutBand';
import {
  parseActiveFanout,
  latestFanoutJoin,
  summarizeChildStatuses,
  snippet,
  type ActiveFanout,
  type ChildStatusCounts,
  type FanoutPlannedChild,
  type JoinedFanout,
} from './fanout';
import { CancelTaskDialog } from './CancelTaskDialog';
import { RevisitTaskDialog } from './RevisitTaskDialog';
import { ResolveEscalationDialog } from './ResolveEscalationDialog';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/* ================================================================
 * Timeline node — ds.css chain styling for the workflow strip
 * ================================================================ */

/** A single node in the chain timeline. */
interface TimelineNodeProps {
  label: string;
  detail?: string;
  state: 'done' | 'current' | 'blocked' | 'pending';
  blockerName?: string;
}

function dotColor(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
      return 'bg-status-open';
    case 'current':
      return 'bg-accent-default';
    case 'blocked':
      return 'bg-status-escalated';
    case 'pending':
      return 'bg-border-strong';
  }
}

function dotRing(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
    case 'current':
    case 'blocked':
      return 'ring-2 ring-offset-1 ring-offset-surface';
    case 'pending':
      return '';
  }
}

function textClass(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
      return 'text-text-secondary';
    case 'current':
      return 'text-text-primary font-semibold';
    case 'blocked':
      return 'text-status-escalated font-semibold';
    case 'pending':
      return 'text-text-muted';
  }
}

function TimelineNodeItem({
  label,
  detail,
  state,
  blockerName,
}: TimelineNodeProps): JSX.Element {
  const { render } = useTranslation();
  return (
    <li className="flex gap-3">
      {/* Vertical connector */}
      <div className="flex flex-col items-center">
        <div
          className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${dotColor(state)} ${dotRing(state)}`}
        />
      </div>
      <div className="min-w-0 flex-1 pb-3">
        <span className={`text-sm ${textClass(state)}`}>{label}</span>
        {detail && (
          <span className="text-text-muted ml-2 font-mono text-xs">{detail}</span>
        )}
        {state === 'blocked' && blockerName && (
          <p className="text-status-escalated mt-0.5 text-xs">
            {render('tasks.detail.blockedOn', {
              name: <span className="font-mono">{blockerName}</span>,
            })}
          </p>
        )}
      </div>
    </li>
  );
}

/* ================================================================
 * Chain timeline
 * ================================================================ */

interface ChainTimelineBlockInfo {
  isBlocked: boolean;
  blockerName?: string;
}

type BlockedJobEntry = { job_id: string; status: string };

/** Derive a human-readable blocker name from the task block context.
 *  THR-037 Change B: escalation is a top-level status now (not a block_kind),
 *  so it no longer appears here — only the in_progress waiting reasons do.
 *
 *  When fanout is present:
 *  - `spawned` fan-out replaces bare "delegation" with descriptive
 *    "waiting on N subtasks".
 *  (pending_review removed per THR-012 msg 129/131 — no fan-out review gate.)
 *  Otherwise, ordinary block_kind/blocked_on_jobs display is preserved. */
function deriveBlockerName(
  blockKind: string | null | undefined,
  blockedOnJobs: BlockedJobEntry[] | null | undefined,
  fanout: ActiveFanout | null | undefined,
  t: Translate,
): string | undefined {
  // Active spawned fan-out: children are alive, parent waits for all to
  // become terminal. Show width-aware delegation copy.
  if (fanout?.status === 'spawned' && blockKind === 'delegated') {
    return t('tasks.detail.blocker.fanout', { count: fanout.width });
  }
  if (blockedOnJobs && blockedOnJobs.length > 0) {
    const jobIds = blockedOnJobs.map((e) => e.job_id);
    return t('tasks.detail.blocker.jobs', { ids: jobIds.join(', ') });
  }
  if (blockKind === 'delegated') return t('tasks.detail.blocker.delegation');
  if (blockKind === 'blocked_on_job') return t('tasks.detail.blocker.blockedOnJob');
  // An unknown block_kind is a raw machine value: verbatim.
  return blockKind ?? undefined;
}

/** Localized label for a KNOWN derived flavor; an unknown one stays verbatim. */
const FLAVOR_LABEL_KEY: Record<string, MessageKey> = {
  'needs-decision': 'tasks.detail.flavor.needsDecision',
  'over-budget': 'tasks.detail.flavor.overBudget',
  exhausted: 'tasks.detail.flavor.exhausted',
};

/**
 * THR-037 Change B §G: derive the escalated display flavor from the latest
 * escalation audit reason. Mirrors the daemon-side classifier
 * (dashboard_summary.classify_escalation_flavor). Best-effort: returns null
 * when no reason is recoverable, so the surface shows plain "escalated".
 */
function classifyEscalationFlavor(reason: string | null | undefined): string | null {
  if (!reason) return null;
  const low = reason.toLowerCase();
  if (low.includes('failure-round bound') && low.includes('exhausted')) {
    return 'exhausted';
  }
  if (low.includes('max steps') && low.includes('exceeded')) {
    return 'over-budget';
  }
  return 'needs-decision';
}

/** Pull the most recent escalation reason from an audit-log array (DERIVE). */
function latestEscalationReason(auditLog: unknown[] | undefined): string | null {
  if (!Array.isArray(auditLog)) return null;
  for (let i = auditLog.length - 1; i >= 0; i--) {
    const entry = auditLog[i] as { action?: unknown; payload?: unknown };
    if (entry?.action === 'escalation') {
      const payload = entry.payload as { reason?: unknown } | null | undefined;
      const reason = payload?.reason;
      return typeof reason === 'string' ? reason : null;
    }
  }
  return null;
}

function WorkflowChainTimeline({
  chain,
  blockInfo,
}: {
  chain: ActiveChainResponse;
  blockInfo?: ChainTimelineBlockInfo;
}): JSX.Element {
  const { t } = useTranslation();
  const totalLegs = 1 + chain.legs.length;
  const currentIdx = chain.step_index;

  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.detail.chain.heading', { step: currentIdx + 1, total: totalLegs })}
      </h3>
      <ol>
        {/* First leg */}
        <TimelineNodeItem
          label={t('tasks.detail.chain.firstLeg')}
          detail={chain.first_leg_expect_verdict ?? undefined}
          state={
            blockInfo?.isBlocked && currentIdx === 0
              ? 'blocked'
              : currentIdx === 0
                ? 'current'
                : currentIdx > 0
                  ? 'done'
                  : 'pending'
          }
          blockerName={
            blockInfo?.isBlocked && currentIdx === 0
              ? blockInfo.blockerName
              : undefined
          }
        />
        {/* Subsequent legs */}
        {chain.legs.map((leg, i) => {
          const legNum = i + 2;
          const isCurrentLeg = currentIdx === legNum - 1;
          const legState: TimelineNodeProps['state'] =
            blockInfo?.isBlocked && isCurrentLeg
              ? 'blocked'
              : isCurrentLeg
                ? 'current'
                : currentIdx >= legNum
                  ? 'done'
                  : 'pending';
          return (
            <TimelineNodeItem
              key={legNum}
              label={leg.agent}
              detail={leg.expect_verdict ?? undefined}
              state={legState}
              blockerName={
                blockInfo?.isBlocked && isCurrentLeg
                  ? blockInfo.blockerName
                  : undefined
              }
            />
          );
        })}
      </ol>
    </section>
  );
}

/* ================================================================
 * Execution subtasks — pulled from recall tree
 * ================================================================ */

interface SubtaskRowProps {
  node: TaskRecallNode;
  depth?: number;
}

const DEPTH_PL = [
  'ml-0', 'ml-4', 'ml-8', 'ml-12', 'ml-16',
  'ml-20', 'ml-24', 'ml-28', 'ml-32',
] as const;

function SubtaskRow({ node, depth = 0 }: SubtaskRowProps): JSX.Element {
  const routes = useTasksRoutes();
  const { t } = useTranslation();
  const ml = DEPTH_PL[Math.min(depth, DEPTH_PL.length - 1)];
  // Prompt/summary excerpts — backed excerpts only; omitted when absent.
  const brief = snippet(node.brief, 88);
  const summary = snippet(node.output_summary, 96);
  return (
    <div className={`${ml}`}>
      <div className="border-border-default flex items-start gap-2 border-l-2 py-1.5 pl-3 text-sm">
        <span
          className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${
            node.status === 'completed'
              ? 'bg-status-open'
              : node.status === 'failed'
                ? 'bg-status-abandoned'
                : node.status === 'in_progress'
                  ? 'bg-accent-default'
                  : 'bg-border-strong'
          }`}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          {brief ? (
            <p className="text-text-primary truncate">{brief}</p>
          ) : (
            <p className="text-text-muted italic">{t('tasks.detail.subtasks.noBrief')}</p>
          )}
          {(node.assigned_agent || summary) && (
            <p className="text-text-muted mt-0.5 truncate text-xs">
              {node.assigned_agent}
              {node.assigned_agent && summary && ' · '}
              {summary}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
          <StatusBadge status={node.status} />
        </div>
      </div>
      {node.children.map((c) => (
        <SubtaskRow key={c.task_id} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}

function ExecutionSubtasks({ recall }: { recall: TaskRecallNode }): JSX.Element {
  const { t } = useTranslation();
  const hasSubtasks = recall.children && recall.children.length > 0;
  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.detail.subtasks.heading')}
      </h3>
      {!hasSubtasks ? (
        <p className="text-text-muted text-xs">{t('tasks.detail.subtasks.none')}</p>
      ) : (
        <div className="space-y-0">
          {recall.children.map((c) => (
            <SubtaskRow key={c.task_id} node={c} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ================================================================
 * Brief section
 * ================================================================ */

const BRIEF_COLLAPSE_THRESHOLD = 600;

function BriefSection({ brief }: { brief: string }): JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const shouldCollapse = brief.length > BRIEF_COLLAPSE_THRESHOLD;
  const preview =
    shouldCollapse && !expanded
      ? brief.slice(0, BRIEF_COLLAPSE_THRESHOLD).replace(/\s+\S*$/, '') + '…'
      : brief;

  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.detail.brief.heading')}
      </h3>
      <div className="border-border-default bg-surface-sunken rounded-md border p-3">
        <Markdown body={preview} />
        {shouldCollapse && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-accent-default mt-2 text-xs hover:underline"
          >
            {expanded
              ? t('tasks.detail.brief.showLess')
              : t('tasks.detail.brief.showFull', { count: brief.length })}
          </button>
        )}
      </div>
    </section>
  );
}

/* ================================================================
 * Revisit & dependency chain — predecessor lineage timeline
 * (a-task-detail reference "Revisit & dependency chain" card).
 *
 * PRESENTATIONAL: renders the revisit / parent PREDECESSOR lineage as a
 * vertical timeline of clickable task nodes, entirely from data already on
 * the TaskDetailResponse envelope — `revisit_chain` ([this, predecessor, …,
 * original]), `parent_task_id`, and `direct_revisits` (tasks that revisit
 * THIS one). The current task node is highlighted.
 *
 * Honesty fence: predecessor / revised-by nodes are id-only links — the chain
 * arrays carry ids, not records, so there is no per-node status/agent/title to
 * render without fabricating it. Only the current node carries a real
 * StatusBadge + title (from the fetched TaskRecord). Forward *dependents*
 * traversal (the blocked-dependent graph the mockup hints at) needs backend
 * assembly and is intentionally omitted, never faked. The superseded-by
 * successor stays in the header lineage strip (already shipped + tested) to
 * avoid a duplicate rendering.
 * ================================================================ */

type ChainNodeState = 'done' | 'current' | 'pending';

/** Locale-neutral lineage role: the React key and the catalog lookup. */
type ChainRole = 'parent' | 'revisitOf' | 'thisTask' | 'revisedBy';

const CHAIN_ROLE_KEY: Record<ChainRole, MessageKey> = {
  parent: 'tasks.detail.lineage.parent',
  revisitOf: 'tasks.detail.lineage.revisitOf',
  thisTask: 'tasks.detail.lineage.thisTask',
  revisedBy: 'tasks.detail.lineage.revisedBy',
};

interface ChainCardNode {
  id: string;
  role: ChainRole;
  state: ChainNodeState;
}

function chainDotClass(state: ChainNodeState): string {
  switch (state) {
    case 'current':
      return 'bg-accent-default ring-2 ring-offset-1 ring-offset-surface';
    case 'done':
      return 'bg-status-open';
    case 'pending':
      return 'bg-border-strong';
  }
}

function ChainNodeItem({
  node,
  isLast,
  detailHref,
  current,
}: {
  node: ChainCardNode;
  isLast: boolean;
  detailHref: string;
  current?: { status: TaskRecord['status']; title: string | null };
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <li className="flex gap-3">
      {/* Connector: node dot + vertical line to the next node */}
      <div className="flex flex-col items-center">
        <div
          className={`mt-3 h-2.5 w-2.5 shrink-0 rounded-full ${chainDotClass(node.state)}`}
        />
        {!isLast && <div className="bg-border-default mt-1 w-0.5 flex-1" />}
      </div>
      <div
        className={`mb-2 min-w-0 flex-1 rounded-md border p-3 ${
          node.state === 'current'
            ? 'border-accent-default bg-surface-sunken'
            : 'border-border-default'
        }`}
      >
        <p className="text-text-muted text-xs font-medium tracking-wider uppercase">
          {t(CHAIN_ROLE_KEY[node.role])}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          {node.state === 'current' ? (
            <IdBadge kind="task" id={node.id} />
          ) : (
            <IdBadge kind="task" id={node.id} to={detailHref} />
          )}
          {current && <StatusBadge status={current.status} />}
        </div>
        {current?.title && (
          <p className="text-text-secondary mt-1 truncate text-sm">
            {current.title}
          </p>
        )}
      </div>
    </li>
  );
}

function RevisitDependencyChain({
  task,
  revisitChain,
  directRevisits,
  routes,
}: {
  task: TaskRecord;
  revisitChain: string[];
  directRevisits: string[];
  routes: ReturnType<typeof useTasksRoutes>;
}): JSX.Element | null {
  const { t } = useTranslation();
  const parentId = task.parent_task_id;
  // revisit_chain = [this, predecessor, …, original]; predecessors are
  // slice(1) reversed so they read oldest → newest ahead of the current node.
  const predecessors = revisitChain.slice(1).reverse();
  const hasLineage =
    !!parentId || predecessors.length > 0 || directRevisits.length > 0;
  if (!hasLineage) return null;

  const nodes: ChainCardNode[] = [];
  if (parentId) nodes.push({ id: parentId, role: 'parent', state: 'done' });
  for (const id of predecessors) {
    nodes.push({ id, role: 'revisitOf', state: 'done' });
  }
  nodes.push({ id: task.task_id, role: 'thisTask', state: 'current' });
  for (const id of directRevisits) {
    nodes.push({ id, role: 'revisedBy', state: 'pending' });
  }

  const title = snippet(task.brief, 72);
  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.detail.lineage.heading')}
      </h3>
      <ol>
        {nodes.map((n, i) => (
          <ChainNodeItem
            key={`${n.role}-${n.id}`}
            node={n}
            isLast={i === nodes.length - 1}
            detailHref={routes.detail(n.id)}
            current={
              n.state === 'current' ? { status: task.status, title } : undefined
            }
          />
        ))}
      </ol>
    </section>
  );
}

/* ================================================================
 * Chain + block context query
 * ================================================================ */

interface ChainWithBlock {
  chain: ActiveChainResponse | null;
  blockedOnJobs: BlockedJobEntry[] | null;
  /** THR-037 §G: derived escalated flavor from the latest escalation audit. */
  escalationFlavor: string | null;
  /** DERIVE from escalation_superseded audit: successor task_id when this
   *  task was auto-resolved to SUPERSEDED. Null otherwise. */
  superseded_by_task_id: string | null;
  /** DERIVE from the latest `fanout_join` audit row: joined fan-out context.
   *  Null when the task never joined a fan-out. `active_fanout` is cleared
   *  after join, so this audit row is the only backing for the joined band. */
  joinedFanout: JoinedFanout | null;
  /** Revisit lineage from the envelope: `[this, predecessor, …, original]`.
   *  Length 1 (or empty) means the task was never revisited. Backs the
   *  "Revisit & dependency chain" predecessor timeline. */
  revisitChain: string[];
  /** Task ids that revisit THIS task (forward, single-hop — NOT a dependents
   *  traversal). Rendered as trailing "Revised by" nodes on the chain. */
  directRevisits: string[];
  /** TASK-5522: derived work-status summary from the envelope. Null when the
   *  daemon response lacks it (legacy daemon / stubbed fixture) — the page
   *  then renders no execution-status card (empty behavior preserved). */
  workStatus: WorkStatusResponse | null;
  escalationReason: EscalationReason | null;
}

function parseEscalationReason(rr: Record<string, unknown>): EscalationReason | null {
  const value = rr.escalation_reason;
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Record<string, unknown>;
  const nullableString = (field: unknown) => field === null || typeof field === 'string';
  if (
    !nullableString(candidate.primary) ||
    !nullableString(candidate.refusal_code) ||
    !nullableString(candidate.secondary)
  ) return null;
  return candidate as unknown as EscalationReason;
}

function parseWorkStatus(rr: Record<string, unknown>): WorkStatusResponse | null {
  const ws = rr.work_status;
  if (!ws || typeof ws !== 'object') return null;
  const candidate = ws as WorkStatusResponse;
  // Defensive shape gate: the card renders only when the envelope really
  // carried a usable summary — never fabricate one from partial data.
  if (typeof candidate.state !== 'string' || typeof candidate.applicable !== 'boolean') {
    return null;
  }
  return candidate;
}

function useChainWithBlock(slug: string | undefined, taskId: string | undefined) {
  return useQuery({
    queryKey: ['task-chain-block', slug, taskId],
    queryFn: () => getTask(slug as string, taskId as string),
    select: (r): ChainWithBlock => {
      const rr = r as Record<string, unknown>;
      const bj = rr.blocked_on_jobs;
      const blockedOnJobs: BlockedJobEntry[] | null =
        Array.isArray(bj)
          ? bj.filter(
              (v): v is BlockedJobEntry =>
                typeof v === 'object' &&
                v !== null &&
                typeof (v as Record<string, unknown>).job_id === 'string',
            )
          : null;
      const sbti = rr.superseded_by_task_id;
      const rc = rr.revisit_chain;
      const revisitChain = Array.isArray(rc)
        ? rc.filter((v): v is string => typeof v === 'string')
        : [];
      const dr = rr.direct_revisits;
      const directRevisits = Array.isArray(dr)
        ? dr.filter((v): v is string => typeof v === 'string')
        : [];
      return {
        chain: r.active_chain ?? null,
        blockedOnJobs,
        escalationFlavor: classifyEscalationFlavor(
          latestEscalationReason(r.audit_log),
        ),
        superseded_by_task_id:
          typeof sbti === 'string' && sbti ? sbti : null,
        joinedFanout: latestFanoutJoin(r.audit_log),
        revisitChain,
        directRevisits,
        workStatus: parseWorkStatus(rr),
        escalationReason: parseEscalationReason(rr),
      };
    },
    enabled: !!slug && !!taskId,
  });
}

/* ================================================================
 * Property rail — labeled metadata grid (a-task-detail / TASKDET-03)
 * ================================================================ */

function formatDateTime(iso: string | null | undefined, locale: Locale): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString(locale);
}

/** Localized label for a KNOWN work-status state; an unknown state keeps the
 *  daemon-supplied label verbatim. */
const WORK_STATE_LABEL_KEY: Record<string, MessageKey> = {
  newly_started: 'tasks.exec.label.newlyStarted',
  recent_progress: 'tasks.exec.label.recentProgress',
  stale_no_receipt: 'tasks.exec.label.staleNoReceipt',
  stale_old_receipt: 'tasks.exec.label.staleOldReceipt',
  heartbeat_stale: 'tasks.exec.label.heartbeatStale',
  heartbeat_unavailable: 'tasks.exec.label.heartbeatUnavailable',
  unavailable: 'tasks.exec.label.unavailable',
  not_applicable: 'tasks.exec.label.notApplicable',
};

/** Only the founder is distinguishable; every other agent is a worker. */
function chipRole(name: string): 'worker' | 'founder' {
  return name === 'founder' ? 'founder' : 'worker';
}

/** One label/value row inside the property rail card. */
function RailRow({
  label,
  children,
  valueClassName,
}: {
  label: string;
  children: ReactNode;
  valueClassName?: string;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <dt className="text-text-muted w-20 shrink-0 text-xs">{label}</dt>
      <dd
        className={`${valueClassName ?? 'min-w-0'} flex-1 break-words`}
      >
        {children}
      </dd>
    </div>
  );
}

/**
 * Execution status card — TASK-5522 right-rail summary of observed work
 * activity, rendered from the server-derived `work_status` envelope field
 * (same task-detail fetch — no extra endpoint).
 *
 * Honesty contract: the card visibly separates the heartbeat/liveness
 * observation (its own labeled row with an explicit freshness suffix) from
 * the actual agent-written update row, which is populated ONLY from a real
 * `progress` audit receipt. When no receipt is in scope the card says so
 * explicitly ('No substantive update recorded') — it never implies activity
 * from a fresh heartbeat, and for non-applicable tasks it never implies a
 * live agent at all.
 */
export function ExecutionStatusDetails({ status }: { status: WorkStatusResponse }): JSX.Element {
  const { locale, t } = useTranslation();
  const start = formatDateTime(status.session_start_ts, locale);
  const hbTs = formatDateTime(status.heartbeat?.timestamp ?? null, locale);
  const hbFreshness = status.heartbeat?.freshness;
  const hbSuffix =
    hbFreshness === 'fresh'
      ? t('tasks.exec.fresh')
      : hbFreshness === 'stale'
        ? t('tasks.exec.stale')
        : t('tasks.exec.unavailable');
  const prog = status.latest_progress;
  const progTs = formatDateTime(prog?.timestamp ?? null, locale);
  const stateLabel = Object.prototype.hasOwnProperty.call(WORK_STATE_LABEL_KEY, status.state)
    ? t(WORK_STATE_LABEL_KEY[status.state])
    : status.label;

  return (
    <section aria-label={t('tasks.exec.heading')} className="min-w-0">
      <h3 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.exec.heading')}
      </h3>
      <dl className="min-w-0 space-y-3 text-sm">
          <RailRow label={t('tasks.exec.state')}>
            <span className="text-text-primary font-medium">{stateLabel}</span>
          </RailRow>
          {start && (
            <RailRow label={t('tasks.exec.start')}>
              <span className="text-text-primary font-mono text-xs tabular-nums">
                {start}
              </span>
            </RailRow>
          )}
          <RailRow label={t('tasks.exec.heartbeat')}>
            <span className="text-text-primary font-mono text-xs tabular-nums">
              {hbTs ?? t('tasks.exec.noneObserved')}{' '}
              <span className="text-text-muted">{hbSuffix}</span>
            </span>
          </RailRow>
          {status.applicable ? (
            <RailRow label={t('tasks.exec.update')}>
              {prog ? (
                <span className="text-text-secondary min-w-0">
                  {progTs}
                  {prog.message ? (
                    <span className="text-text-primary"> — {prog.message}</span>
                  ) : (
                    <span className="text-text-muted"> {t('tasks.exec.contentUnavailable')}</span>
                  )}
                </span>
              ) : (
                <span className="text-status-escalated font-medium">
                  {t('tasks.exec.noUpdate')}
                </span>
              )}
            </RailRow>
          ) : (
            <RailRow label={t('tasks.exec.reason')}>
              <span className="text-text-secondary font-mono text-xs">
                {status.reason ?? '—'}
              </span>
            </RailRow>
          )}
      </dl>
    </section>
  );
}

/**
 * Metadata rail — right-rail card styled per the a-task-detail reference
 * (TASKDET-03), mirroring the JobDetailPage PropertyRail idiom (JOBDET-01).
 *
 * Renders ONLY property-grid fields with a real backing value in the
 * TaskRecord payload the page already fetches: Status, Assignee, Thread,
 * Job, Created. The reference's Executor / Churn (token usage) / Priority
 * rows have NO backing field in the task-detail contract, so they are
 * honestly omitted rather than fabricated — surfacing them would need a
 * daemon/data-contract change, out of scope for this presentation-only leg.
 */
export function TaskStatusCard({
  task,
  slug,
  jobs,
  workStatus,
}: {
  task: TaskRecord;
  slug: string | undefined;
  jobs: JobRecord[];
  workStatus: WorkStatusResponse | null;
}): JSX.Element {
  const { locale, t } = useTranslation();
  const threadId = (task as Record<string, unknown>).dispatched_from_thread_id;
  const created = formatDateTime(task.created_at, locale);

  return (
    <aside aria-label={t('tasks.rail.label')} className="min-w-0 lg:w-80 lg:shrink-0">
      <div className="border-border-default bg-surface-raised min-w-0 rounded-xl border p-4">
        <h2 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
          {t('tasks.rail.heading')}
        </h2>
        <dl className="space-y-3 text-sm">
          <RailRow label={t('tasks.rail.status')}>
            <span className="inline-flex max-w-full flex-wrap">
              <StatusBadge
                status={task.status}
                blockKind={task.block_kind}
                waitingLabels={{
                  delegated: t('tasks.waiting.subtasks'),
                  blocked_on_job: t('tasks.waiting.jobs'),
                }}
              />
            </span>
          </RailRow>
          {task.block_kind && (
            <RailRow label={t('tasks.rail.blockKind')}>
              <span className="text-text-secondary break-words font-mono text-xs">
                {task.block_kind}
              </span>
            </RailRow>
          )}
          {task.assigned_agent && (
            <RailRow label={t('tasks.rail.assignee')}>
              <span className="block max-w-full">
                <AgentChip
                  name={task.assigned_agent}
                  role={chipRole(task.assigned_agent)}
                  wrap
                />
              </span>
            </RailRow>
          )}
          {typeof threadId === 'string' && threadId && (
            <RailRow label={t('tasks.rail.thread')}>
              <IdBadge
                id={threadId}
                kind="thread"
                to={slug ? `/orgs/${slug}/threads/${threadId}` : undefined}
              />
            </RailRow>
          )}
          {jobs.length > 0 && (
            <RailRow label={t('tasks.rail.job')} valueClassName="min-w-0">
              <span className="flex flex-wrap gap-x-2 gap-y-1">
                {jobs.map((j) =>
                  slug ? (
                    <Link
                      key={j.id}
                      to={`/orgs/${slug}/jobs/${j.id}`}
                      className="text-accent-default break-all font-mono text-xs hover:underline"
                    >
                      {j.id}
                    </Link>
                  ) : (
                    <span key={j.id} className="break-all font-mono text-xs">
                      {j.id}
                    </span>
                  ),
                )}
              </span>
            </RailRow>
          )}
          {created && (
            <RailRow label={t('tasks.rail.created')}>
              <span className="text-text-primary font-mono text-xs tabular-nums">
                {created}
              </span>
            </RailRow>
          )}
        </dl>
        {workStatus && (
          <div className="border-border-default mt-4 min-w-0 border-t pt-4">
            <ExecutionStatusDetails status={workStatus} />
          </div>
        )}
      </div>
    </aside>
  );
}

/* ================================================================
 * Main pane
 * ================================================================ */

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'failed',
  'completed',
  'cancelled',
  'superseded',
]);

export function TaskDetailPage(): JSX.Element {
  const { slug, task_id: taskIdParam } = useParams<{ slug: string; task_id: string }>();
  const taskId = taskIdParam ?? '';
  const routes = useTasksRoutes();
  const { t, render } = useTranslation();
  const task = useTask(taskId);
  const recall = useTaskRecall(taskId);
  const jobsQuery = useJobsList({ task_id: taskId, status: 'all', limit: 100 });
  const chainQuery = useChainWithBlock(slug, taskId);
  const [dialog, setDialog] = useState<
    null | 'cancel' | 'revisit' | 'resolve-continue'
  >(null);

  // THR-037 Change B dual-read: Path B top-level `escalated` status OR
  // legacy `blocked` + `escalated` block_kind (transition window).
  const isEscalated =
    task.data?.status === 'escalated' ||
    (task.data?.status === 'blocked' && task.data?.block_kind === 'escalated');
  const isTerminal = task.data ? TERMINAL_STATUSES.has(task.data.status) : false;
  const isFailed = task.data?.status === 'failed';
  const note = task.data ? (task.data as { note?: unknown }).note : undefined;
  const failureNote = isFailed && typeof note === 'string' && note ? note : null;
  const escalationNote =
    isEscalated && typeof note === 'string' && note ? note : null;
  const projectedEscalation = chainQuery.data?.escalationReason ?? null;
  const isAuthorityV2Refusal = !!projectedEscalation?.refusal_code;
  const escalationPrimary = isAuthorityV2Refusal
    ? projectedEscalation.primary
    : escalationNote;
  const escalationSecondary = isAuthorityV2Refusal
    ? projectedEscalation.secondary
    : null;
  const recallNode = recall.data && isAuthorityV2Refusal
    ? {
        ...recall.data,
        output_summary: [escalationPrimary, escalationSecondary]
          .filter((value): value is string => !!value)
          .join('\n\n') || null,
      }
    : recall.data;
  const brief = task.data?.brief ?? '';
  // §G derived escalated flavor (graceful: null → plain "escalated").
  const escalationFlavor = isEscalated ? chainQuery.data?.escalationFlavor ?? null : null;

  // Build block info for the chain timeline. The red "blocked" timeline node
  // fires for a genuine escalation OR a parked in_progress task waiting on its
  // children/jobs (THR-037 §F.2) — `blocked` as a status is retired.
  const fanoutCtx = parseActiveFanout(task.data?.active_fanout);
  const blockInfo: ChainTimelineBlockInfo | undefined = useMemo(() => {
    if (!task.data) return undefined;
    const isBlocked =
      task.data.status === 'escalated' ||
      (task.data.status === 'blocked' && task.data.block_kind === 'escalated') ||
      (task.data.status === 'in_progress' && !!task.data.block_kind);
    if (!isBlocked) return { isBlocked: false };
    const blockerName =
      task.data.status === 'escalated' ||
      (task.data.status === 'blocked' && task.data.block_kind === 'escalated')
        ? t('tasks.detail.blocker.escalation')
        : deriveBlockerName(
            task.data.block_kind,
            chainQuery.data?.blockedOnJobs ?? null,
            fanoutCtx,
            t,
          );
    return { isBlocked: true, blockerName };
  }, [task.data, chainQuery.data, fanoutCtx, t]);

  // ── Fan-out status band derivation ──────────────────────────────────────
  // Three lifecycle states, all DERIVED from data already fetched. Regular
  // (non-fan-out) tasks produce `null` and render no band. Honesty rules from
  // the TASK-1696 Step 0 note are enforced in fanout.ts (no fabricated data).
  const recallChildren = recall.data?.children;
  const joinedFanout = chainQuery.data?.joinedFanout ?? null;
  interface FanoutBandData {
    mode: FanoutMode;
    width: number | null;
    counts: ChildStatusCounts | null;
    plannedChildren: FanoutPlannedChild[];
  }
  let fanoutBand: FanoutBandData | null = null;
  if (fanoutCtx?.status === 'spawned') {
    // Running: progress counts from recall children, restricted to this
    // fan-out's own children_ids when the payload records them.
    fanoutBand = {
      mode: 'running',
      width: fanoutCtx.width,
      counts: summarizeChildStatuses(recallChildren, fanoutCtx.childrenIds),
      plannedChildren: [],
    };
  } else if (joinedFanout) {
    // Joined: active_fanout is cleared after join, so counts come from recall
    // children and width from the fanout_join audit row.
    fanoutBand = {
      mode: 'joined',
      width: joinedFanout.width,
      counts: summarizeChildStatuses(recallChildren, joinedFanout.childrenIds),
      plannedChildren: [],
    };
  }

  return (
    <>
      <ContentWrap>
          {/* Back-nav — return to the roots list */}
          <nav className="mb-4">
            <Link
              to={routes.inbox()}
              className="text-text-muted hover:text-text-primary text-xs transition-colors"
            >
              {t('tasks.detail.back')}
            </Link>
          </nav>

          {/* Two-column body: primary content + right-rail property grid (TASKDET-03) */}
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
            <div className="min-w-0 flex-1">
          {/* Header — identity card */}
          <header className="border-border-default border-b pb-4">
            <h1 className="flex items-center gap-2">
              <span className="font-display text-h1 text-text-primary font-medium">
                {taskId}
              </span>
              {task.data && (
                <StatusBadge
                  status={task.data.status}
                  blockKind={task.data.block_kind}
                  waitingLabels={{
                    delegated: t('tasks.waiting.subtasks'),
                    blocked_on_job: t('tasks.waiting.jobs'),
                  }}
                />
              )}
              {escalationFlavor && (
                <span className="text-text-muted text-xs font-medium">
                  ·{' '}
                  {Object.prototype.hasOwnProperty.call(FLAVOR_LABEL_KEY, escalationFlavor)
                    ? t(FLAVOR_LABEL_KEY[escalationFlavor])
                    : escalationFlavor}
                </span>
              )}
            </h1>
            {task.data && (
              <div className="text-text-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs tabular-nums">
                <span>{task.data.team}</span>
                {task.data.parent_task_id && (
                  <span>
                    {render('tasks.detail.parent', {
                      id: (
                        <Link
                          to={routes.detail(task.data.parent_task_id)}
                          className="text-id-task hover:underline"
                        >
                          {task.data.parent_task_id}
                        </Link>
                      ),
                    })}
                  </span>
                )}
                {task.data.revisit_of_task_id && (
                  <span>
                    {render('tasks.detail.revisitOf', {
                      id: (
                        <Link
                          to={routes.detail(task.data.revisit_of_task_id)}
                          className="text-id-task hover:underline"
                        >
                          {task.data.revisit_of_task_id}
                        </Link>
                      ),
                    })}
                  </span>
                )}
                {chainQuery.data?.superseded_by_task_id && (
                  <span>
                    {render('tasks.detail.supersededBy', {
                      id: (
                        <Link
                          to={routes.detail(chainQuery.data.superseded_by_task_id)}
                          className="text-id-task hover:underline"
                        >
                          {chainQuery.data.superseded_by_task_id}
                        </Link>
                      ),
                    })}
                  </span>
                )}
              </div>
            )}
            {failureNote && (
              <div
                role="alert"
                className="bg-tier-red-tint text-status-abandoned mt-3 max-h-32 overflow-y-auto rounded-md px-3 py-2 text-sm"
              >
                <span className="font-semibold">{t('tasks.detail.failureReason')}</span>{' '}
                <span className="font-mono">{failureNote}</span>
              </div>
            )}
            {(escalationPrimary || escalationSecondary) && (
              <div
                className="bg-tier-amber-tint text-status-escalated mt-3 max-h-32 overflow-y-auto rounded-md px-3 py-2 text-sm"
              >
                {escalationPrimary && (
                  <p>
                    <span className="font-semibold">{t('tasks.detail.escalationReason')}</span>{' '}
                    <span className="font-mono">{escalationPrimary}</span>
                  </p>
                )}
                {escalationSecondary && (
                  <p className={escalationPrimary ? 'mt-1' : undefined}>
                    <span className="font-semibold">{t('tasks.detail.automaticEscalation')}</span>{' '}
                    <span>{escalationSecondary}</span>
                  </p>
                )}
              </div>
            )}
            <div className="mt-3 flex gap-2">
              {isEscalated ? (
                <>
                  {/* THR-080: Continue via resolve-escalation; Cancel via
                      generic /cancel route (removed from resolve vocabulary). */}
                  <Button size="sm" onClick={() => setDialog('resolve-continue')}>
                    {t('tasks.detail.continue')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setDialog('cancel')}
                  >
                    {t('tasks.detail.cancel')}
                  </Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="ghost" onClick={() => setDialog('revisit')}>
                    {t('tasks.detail.revisit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setDialog('cancel')}
                    disabled={isTerminal}
                    title={
                      isTerminal
                        ? t('tasks.detail.cannotCancel', { status: task.data?.status ?? '' })
                        : undefined
                    }
                  >
                    {t('tasks.detail.cancel')}
                  </Button>
                </>
              )}
              {slug && (
                <Link
                  to={`/orgs/${slug}/audit?task_id=${taskId}`}
                  className="text-accent-default ml-auto self-center text-xs hover:underline"
                >
                  {t('tasks.detail.viewAudit')}
                </Link>
              )}
            </div>
          </header>

          {/* Fan-out status band — pending / running / joined. Only mounted
              when real fan-out evidence exists; regular tasks render nothing. */}
          {fanoutBand && (
            <FanoutBand
              mode={fanoutBand.mode}
              width={fanoutBand.width}
              counts={fanoutBand.counts}
            />
          )}

          {/* Body */}
          <section className="py-4">
            {task.data?.brief && <BriefSection brief={brief} />}

            {task.data && (
              <RevisitDependencyChain
                task={task.data}
                revisitChain={chainQuery.data?.revisitChain ?? []}
                directRevisits={chainQuery.data?.directRevisits ?? []}
                routes={routes}
              />
            )}

            {chainQuery.data?.chain && (
              <WorkflowChainTimeline
                chain={chainQuery.data.chain}
                blockInfo={blockInfo}
              />
            )}

            {recall.data && <ExecutionSubtasks recall={recall.data} />}

            <section className="mt-5">
              <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                {t('tasks.detail.recall.heading')}
              </h3>
              {recallNode ? (
                <TaskRecallTree node={recallNode} />
              ) : (
                <p className="text-text-muted text-xs">{t('tasks.detail.recall.loading')}</p>
              )}
            </section>

            <section className="mt-5">
              <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                {t('tasks.detail.activity.heading')}
              </h3>
              <TaskEventsLog key={taskId} taskId={taskId} />
            </section>

            {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
              <section className="mt-5">
                <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                  {t('tasks.detail.jobs.heading')}
                </h3>
                <ul className="space-y-1 text-sm">
                  {jobsQuery.data.jobs.map((j) => (
                    <li key={j.id}>
                      {slug ? (
                        <Link
                          to={`/orgs/${slug}/jobs/${j.id}`}
                          className="text-accent-default font-mono hover:underline"
                        >
                          {j.id}
                        </Link>
                      ) : (
                        <span className="font-mono">{j.id}</span>
                      )}
                      {' — '}
                      {j.title}{' '}
                      <span className="text-text-muted">({j.status})</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </section>
            </div>

            {task.data && (
              <>
                <TaskStatusCard
                  task={task.data}
                  slug={slug}
                  jobs={jobsQuery.data?.jobs ?? []}
                  workStatus={chainQuery.data?.workStatus ?? null}
                />
              </>
            )}
          </div>
      </ContentWrap>

      {dialog === 'cancel' && (
        <CancelTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'revisit' && (
        <RevisitTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'resolve-continue' && (
        <ResolveEscalationDialog
          intent="continue"
          taskId={taskId}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}
