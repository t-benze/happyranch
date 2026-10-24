import { useAgentsList } from '@/hooks/agents';
import { useIdentityOptions, useIdentityPresentation, usePreflightAddresses, namingAddressError } from '@/hooks/identities';
import { RecipientsInput } from '@/design-system/patterns/RecipientsInput';
import { renderThreadErrorDetail, type ThreadErrorDetail } from '@/lib/threadErrors';
/**
 * Tasks list — Direction-A Pasture, roots-only dense list.
 *
 * Group by Status / Agent / Thread. Each group renders a lightweight sans
 * label (dot + name + count) above a rounded bordered Pasture rows-card
 * (a-tasks mockup). Only superseded rows are visually dimmed.
 * Status pills follow ds.css .tag (rounded-pill, led dot).
 *
 * Per founder ruling: NO in-list 'show subtasks' toggle. The list is
 * roots-only; execution subtasks live on the Task detail surface.
 *
 * Driven by GET /tasks/roots (roots-only invariant). Cursor pagination
 * via next_cursor with IntersectionObserver sentinel.
 *
 * THR-046 msg-11: wider full-width layout, cream canvas, STATUS/TASK split
 * columns, rounded column-header bar, rounded bordered group-section cards,
 * right-aligned group-by segmented control.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Filter, RefreshCw } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { Button } from '@/design-system/primitives/Button';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import {
  TaskListColumnHeader,
  TaskListRow,
  severityRollupStatus,
} from './TaskListRow';
import { TASK_LIST_STATUSES, useTasksListStreams, useTasksRoutes, type TaskListStream } from '@/hooks/tasks';
import type { TaskRecord } from '@/lib/api/types';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';

type GroupBy = 'status' | 'agent' | 'thread';
type Translate = (key: MessageKey, params?: MessageParams) => string;

const GROUP_BY_OPTIONS: { value: GroupBy; label: MessageKey }[] = [
  { value: 'status', label: 'tasks.page.groupBy.status' },
  { value: 'agent', label: 'tasks.page.groupBy.agent' },
  { value: 'thread', label: 'tasks.page.groupBy.thread' },
];

/**
 * Colored status dot per group. Status groups map to the same semantic tokens
 * StatusBadge uses; non-status groups (agent / thread) carry a neutral dot —
 * we never claim a status color for a dimension that has no single status.
 */
type GroupDot =
  | 'in_progress'
  | 'pending'
  | 'escalated'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'superseded'
  | 'neutral';

const DOT_COLOR: Record<GroupDot, string> = {
  in_progress: 'text-info',
  pending: 'text-status-archiving',
  escalated: 'text-attention-text',
  completed: 'text-status-open',
  failed: 'text-status-abandoned',
  cancelled: 'text-status-archived',
  superseded: 'text-status-archived',
  neutral: 'text-text-muted',
};

const STATUS_DOT_KEYS = new Set<string>([
  'in_progress',
  'pending',
  'escalated',
  'completed',
  'failed',
  'cancelled',
  'superseded',
]);

function groupDot(key: string, by: GroupBy): GroupDot {
  if (by === 'status' && STATUS_DOT_KEYS.has(key)) return key as GroupDot;
  return 'neutral';
}

/**
 * Pasture group section label (a-tasks mockup .grp-head) — a lightweight
 * sans heading that sits ABOVE its rows-card: colored status dot + name +
 * a plain muted count (TASKS-04). Both derivations are pure client-side reads
 * of the already-loaded roots payload — no fetch, no fabrication.
 */
function GroupHeading({
  label,
  count,
  dot,
  id,
}: {
  label: string;
  count?: number;
  dot: GroupDot;
  /** Optional id so a rows section can name this heading via aria-labelledby. */
  id?: string;
}): JSX.Element {
  return (
    <h2 id={id} className="tasks-group-heading mb-2 flex items-center px-0.5">
      <span
        aria-hidden
        className={`inline-block h-2 w-2 shrink-0 rounded-full bg-current ${DOT_COLOR[dot]}`}
      />
      <span
        className="text-task-group text-text-primary font-semibold tracking-tight"
      >
        {label}
      </span>
      {count !== undefined && (
        <span className="text-text-muted text-xs tabular-nums">{count}</span>
      )}
    </h2>
  );
}

// Locale-neutral group keys: they are React keys and the sort key, so a
// locale switch re-labels the same group nodes instead of remounting them.
const UNASSIGNED_GROUP = 'Unassigned';
const NO_THREAD_GROUP = 'No thread';

function groupKey(task: TaskRecord, by: GroupBy): string {
  switch (by) {
    case 'status':
      return task.status;
    case 'agent':
      return task.assigned_agent || UNASSIGNED_GROUP;
    case 'thread': {
      const threadId = task.originating_thread_id ?? task.dispatched_from_thread_id;
      if (threadId && typeof threadId === 'string' && threadId.length > 0) {
        return threadId;
      }
      return NO_THREAD_GROUP;
    }
  }
}

const STATUS_GROUP_LABEL_KEY: Record<string, MessageKey> = {
  pending: 'tasks.group.status.pending',
  in_progress: 'tasks.group.status.inProgress',
  escalated: 'tasks.group.status.escalated',
  completed: 'tasks.group.status.completed',
  failed: 'tasks.group.status.failed',
  cancelled: 'tasks.group.status.cancelled',
  superseded: 'tasks.group.status.superseded',
};

function groupLabel(key: string, by: GroupBy, t: Translate): string {
  if (by === 'status') {
    return Object.prototype.hasOwnProperty.call(STATUS_GROUP_LABEL_KEY, key)
      ? t(STATUS_GROUP_LABEL_KEY[key])
      : key;
  }
  if (by === 'agent' && key === UNASSIGNED_GROUP) return t('tasks.group.unassigned');
  if (by === 'thread' && key === NO_THREAD_GROUP) return t('tasks.group.noThread');
  return key;
}

const GROUP_ORDER_STATUS: Record<string, number> = {
  escalated: 0,
  in_progress: 1,
  pending: 2,
  completed: 4,
  failed: 3,
  cancelled: 5,
  superseded: 6,
};

type TaskFilters = { status?: string; assigned_agent?: string };

const PARTITION_PRIORITY = new Map<string, number>(TASK_LIST_STATUSES.map((status, index) => [status, index]));

function timeOf(value: string): number {
  const time = Date.parse(value);
  return Number.isNaN(time) ? Number.NEGATIVE_INFINITY : time;
}

/**
 * One deterministic ordinary projection per root id across the loaded owners.
 * Each owner already holds only rows matching its exact status/agent filter.
 * The greatest updated_at wins; equal timestamps use the fixed partition
 * order, then the owner's own page position. This is a loaded-projection rule,
 * independent of which owner's response arrived first, not a server snapshot.
 * The result is merged by created_at descending, preserving page order on ties.
 */
function projectOrdinaryTasks(owners: TaskListStream[]): TaskRecord[] {
  const chosen = new Map<string, { task: TaskRecord; rank: number }>();
  for (const owner of owners) {
    const rank = PARTITION_PRIORITY.get(owner.status) ?? TASK_LIST_STATUSES.length;
    for (const task of owner.tasks) {
      const previous = chosen.get(task.task_id);
      if (previous) {
        const time = timeOf(task.updated_at);
        const previousTime = timeOf(previous.task.updated_at);
        if (time < previousTime || (time === previousTime && rank >= previous.rank)) continue;
      }
      chosen.set(task.task_id, { task, rank });
    }
  }
  // Stable: equal created_at keeps partition order, then each owner's own
  // server page order (the server's id tie-break is not reconstructed here).
  return [...chosen.values()].map(({ task }) => task).sort((a, b) => {
    const created = timeOf(b.created_at) - timeOf(a.created_at);
    return Number.isNaN(created) ? 0 : created;
  });
}

export function TasksPage(): JSX.Element {
  const [groupBy, setGroupBy] = useState<GroupBy>('status');
  const [filters, setFilters] = useState<TaskFilters>();
  const orgSlug = useOrgSlugOptional();
  // A new context mounts new local recovery owners. Old promises can settle,
  // but cannot update a later context, including an A → B → A return.
  return <TasksList key={JSON.stringify([orgSlug, filters])} {...{ groupBy, setGroupBy, filters, setFilters }} />;
}

function TasksList({ groupBy, setGroupBy, filters, setFilters }: {
  groupBy: GroupBy;
  setGroupBy: (value: GroupBy) => void;
  filters: TaskFilters | undefined;
  setFilters: (value: TaskFilters | undefined) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const [filterOpen, setFilterOpen] = useState(false);
  const [draftStatus, setDraftStatus] = useState(filters?.status ?? '');
  const [draftAgent, setDraftAgent] = useState(filters?.assigned_agent ?? '');
  const agents = useAgentsList();
  const identityOptions = useIdentityOptions(agents.data?.agents ?? []);
  const presentation = useIdentityPresentation();
  const preflight = usePreflightAddresses();
  const [filterError, setFilterError] = useState<ThreadErrorDetail | null>(null);
  const filterLatch = useRef(false);
  const filterAttempt = useRef(0);
  const abandoned = useRef(false);
  useEffect(() => { abandoned.current = false; return () => { abandoned.current = true; }; }, []);
  const [isRetrying, setIsRetrying] = useState(false);
  const routes = useTasksRoutes();
  const orgSlug = useOrgSlugOptional();
  // One owner per selected ordinary status plus the independent attention
  // traversal. Each owner keeps its own cursor, errors and Retry; the page owns
  // only presentation, grouping and scroll demand.
  const streams = useTasksListStreams(filters);
  const owners = streams.ordinary;
  const attention = streams.attention;

  const allTasks = useMemo(() => projectOrdinaryTasks(owners), [owners]);
  // This is deliberately not derived from the ordinary pages. The roots
  // endpoint has no total, so its independent status traversal is the only
  // truthful source for a Waiting-on-you presentation.
  const attentionTasks = attention.tasks;
  const attentionTaskIds = useMemo(
    () => new Set(attentionTasks.map((task) => task.task_id)),
    [attentionTasks],
  );
  // A waiting root owns its presentation. If it also occurs in an ordinary
  // traversal, keep one row rather than duplicating it.
  const ordinaryTasks = useMemo(
    () => allTasks.filter((task) => !attentionTaskIds.has(task.task_id)),
    [allTasks, attentionTaskIds],
  );

  // Page eyebrow — derived ONLY from already-loaded roots-list fields
  // (no extra fetch, no fabrication). "Failed" uses the same severity rollup
  // the rows display. "Subtasks roll up" is a static, honest descriptor of
  // the roots payload (it carries severity_rollup).
  const eyebrow = useMemo(() => {
    const failed = ordinaryTasks.filter(
      (t) => severityRollupStatus(t) === 'failed',
    ).length;
    return [
      t('tasks.page.eyebrow.loaded', { count: ordinaryTasks.length }),
      t('tasks.page.eyebrow.rollUp'),
      t('tasks.page.eyebrow.failed', { count: failed }),
    ].join(' · ');
  }, [ordinaryTasks, t]);

  // Group tasks by the active dimension, sorted by group priority then recency.
  const groups = useMemo(() => {
    const map = new Map<string, TaskRecord[]>();
    for (const t of ordinaryTasks) {
      const k = groupKey(t, groupBy);
      const list = map.get(k);
      if (list) list.push(t);
      else map.set(k, [t]);
    }
    // Sort within each group by updated_at desc (most recent first).
    for (const [, tasks] of map) {
      tasks.sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
    }
    // Sort groups: by explicit order for status, alpha for others.
    const entries = [...map.entries()];
    if (groupBy === 'status') {
      entries.sort(
        (a, b) =>
          (GROUP_ORDER_STATUS[a[0]] ?? 99) -
          (GROUP_ORDER_STATUS[b[0]] ?? 99),
      );
    } else {
      entries.sort((a, b) => a[0].localeCompare(b[0]));
    }
    return entries;
  }, [ordinaryTasks, groupBy]);

  const hasUsableTasks = allTasks.length > 0;
  const pendingOwners = owners.filter((stream) => stream.isInitialPending);
  const initialErrorOwners = owners.filter((stream) => stream.isInitialError);
  const allInitialFailed = owners.length > 0 && initialErrorOwners.length === owners.length;
  const ordinaryExhausted = owners.every((stream) =>
    !stream.isInitialPending && !stream.isInitialError && !stream.isStale &&
    !stream.isContinuationError && !stream.hasNextPage);
  const multipleOwners = owners.length > 1;

  // Sentinel observer for infinite scroll. Each demand asks every owner that
  // can advance for at most one page with its own cursor; each owner refuses
  // when exhausted, failed or already fetching, so siblings never re-advance.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const ownersRef = useRef(owners);
  ownersRef.current = owners;
  const anyFetchingNextPage = owners.some((stream) => stream.isFetchingNextPage);
  const canAdvance = owners.some((stream) =>
    stream.hasNextPage && !stream.isContinuationError && !stream.isStale &&
    !stream.isInitialError && !stream.isRefreshing);
  const loadNextPages = useCallback(() => {
    for (const stream of ownersRef.current) void stream.fetchNextPage();
  }, []);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !canAdvance || anyFetchingNextPage || isRetrying) return;
    let active = true;
    const obs = new IntersectionObserver(
      (entries) => {
        if (active && entries[0]?.isIntersecting) loadNextPages();
      },
      { rootMargin: '200px' },
    );
    obs.observe(node);
    return () => { active = false; obs.disconnect(); };
  }, [canAdvance, anyFetchingNextPage, isRetrying, loadNextPages]);

  const retryAllFailed = async () => {
    if (isRetrying) return;
    const failed = ownersRef.current.filter((stream) => stream.isInitialError);
    if (failed.length === 0) return;
    setIsRetrying(true);
    try {
      await Promise.all(failed.map((stream) => stream.retry()));
    } finally {
      if (!abandoned.current) setIsRetrying(false);
    }
  };
  const attentionCount = attention.hasNextPage
    ? t('tasks.attention.countMore')
    : t('tasks.attention.count', { count: attentionTasks.length });

  // Presentation split. The escalated attention traversal is independent of the
  // ordinary owners, so its group is decided on its own data: when it has
  // rows it renders FIRST inside the shared list shell (identical markup to the
  // ordinary groups); when it is empty/loading/errored it renders no group.
  // The ordinary initial loading/error/empty states stay truthful even when
  // the escalated group is present, and never hide the shared list shell.
  const attentionLoading = attention.isInitialPending;
  const attentionFailed = attention.isInitialError || attention.isStale || attention.isContinuationError;
  const attentionInitialError = attentionFailed && attentionTasks.length === 0;
  const showEscalatedGroup = attentionTasks.length > 0;
  const ordinaryLoading = !hasUsableTasks && pendingOwners.length > 0 && !isRetrying;
  const ordinaryInitialError = !hasUsableTasks && (allInitialFailed || isRetrying);
  const showOrdinaryGroups = hasUsableTasks;
  const showListShell = showEscalatedGroup || showOrdinaryGroups;
  // Global empty / end-of-list claims need every relevant owner — the selected
  // ordinary owners AND the attention traversal — to have succeeded and been
  // exhausted, with no visible attention rows.
  const allOwnersExhausted = ordinaryExhausted && !attentionLoading && !attentionFailed &&
    !attention.hasNextPage && attentionTasks.length === 0;
  const globalEmpty = allOwnersExhausted && !hasUsableTasks;
  const globalEnd = allOwnersExhausted && hasUsableTasks;
  const statusLabel = (status: string) => groupLabel(status, 'status', t);
  // Per-owner recovery: each failed owner stays visibly incomplete with its own
  // Retry, while successful siblings keep their rows.
  const ownerAlert = (stream: TaskListStream, kind: 'initial' | 'stale' | 'more') => (
    <div key={`${kind}-${stream.owner}`} role="alert"
      className="border-feedback-danger bg-danger-soft flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
      <div className="flex min-w-0 items-center gap-2">
        <AlertCircle className="text-feedback-danger shrink-0" size={18} aria-hidden />
        <div className="min-w-0">
          <p className="text-text-primary text-sm font-medium">
            {t(kind === 'initial' ? 'tasks.list.errorTitle' : kind === 'stale' ? 'tasks.list.staleTitle' : 'tasks.list.moreErrorTitle')}
          </p>
          {multipleOwners && (
            <p className="text-text-secondary text-xs">{t('tasks.list.ownerScope', { status: statusLabel(stream.status) })}</p>
          )}
          <p className="text-text-secondary text-xs">
            {t(kind === 'initial' ? 'tasks.list.errorBody' : kind === 'stale' ? 'tasks.list.staleBody' : 'tasks.list.moreErrorBody')}
          </p>
        </div>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="h-auto max-w-full whitespace-normal break-words"
        aria-label={multipleOwners
          ? t('tasks.list.retryStatus', { status: statusLabel(stream.status) })
          : kind === 'more' ? t('tasks.list.retryMore') : undefined}
        onClick={() => void stream.retry()}
        loading={kind === 'more' ? stream.isFetchingNextPage : stream.isRefreshing}
      >
        <RefreshCw size={14} aria-hidden />
        {t('common.retry')}
      </Button>
    </div>
  );
  const topAlerts = owners.flatMap((stream) =>
    stream.isStale ? [ownerAlert(stream, 'stale')]
      : stream.isInitialError && !allInitialFailed ? [ownerAlert(stream, 'initial')] : []);
  const moreAlerts = owners.flatMap((stream) => stream.isContinuationError ? [ownerAlert(stream, 'more')] : []);
  const partialLoading = pendingOwners.length > 0 && (hasUsableTasks || showEscalatedGroup);

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      <main className="min-h-0 flex-1">
        <ContentWrap>
        {/* Heading, filters and rows share the list scroll owner. */}
        <div data-testid="tasks-page-header" className="tasks-heading flex flex-col items-start justify-between gap-4 sm:flex-row">
          <div className="min-w-0">
            <p className="tasks-eyebrow text-text-muted font-semibold tracking-wide uppercase">
              {eyebrow}
            </p>
            <h1 className="font-display text-text-primary text-task-heading font-medium">
              {t('tasks.page.title')}
            </h1>
          </div>
          <div className="flex shrink-0 items-center gap-2.5">
          <Tabs
            className="shrink-0"
            value={groupBy}
            onValueChange={(v) => setGroupBy(v as GroupBy)}
          >
            <TabsList
              aria-label={t('tasks.page.groupBy')}
              className="border-border-default bg-surface-sunken tasks-segment gap-0 rounded-full border"
            >
              {GROUP_BY_OPTIONS.map((opt) => (
                <TabsTrigger
                  key={opt.value}
                  value={opt.value}
                  className="data-[state=active]:bg-accent-soft data-[state=active]:text-accent-text tasks-segment-button rounded-full"
                >
                  {t(opt.label)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Button variant="ghost" size="sm" aria-expanded={filterOpen} onClick={() => setFilterOpen(!filterOpen)}>
            <Filter size={14} aria-hidden /> {t('tasks.page.filter')}
          </Button>
          </div>
        </div>
        {filterOpen && (
          <form aria-label={t('tasks.filters.label')} className="mb-4 flex flex-wrap items-end gap-3" onSubmit={async (event) => {
            event.preventDefault();
            if (filterLatch.current || !orgSlug) return;
            const attempt = ++filterAttempt.current;
            filterLatch.current = true; setFilterError(null);
            try {
            const resolved = draftAgent ? await preflight.mutateAsync({ slug: orgSlug, context: 'lookup', recipients: [draftAgent], canonicalAgentIds: identityOptions.map((a) => a.name) }) : { recipients: [] };
            if (abandoned.current || attempt !== filterAttempt.current) return;
            const canonical = resolved.recipients[0];
            const next = { ...(draftStatus ? { status: draftStatus } : {}), ...(canonical ? { assigned_agent: canonical } : {}) };
            setFilters(Object.keys(next).length ? next : undefined);
            } catch (error) {
              if (!abandoned.current && attempt === filterAttempt.current) setFilterError(namingAddressError(error) ?? { kind: 'mapped', key: 'identity.unavailable' });
            } finally { if (!abandoned.current && attempt === filterAttempt.current) filterLatch.current = false; }
          }}>
            <label className="text-text-secondary text-sm">{t('tasks.filters.status')}
              <select aria-label={t('tasks.filters.statusSelect')} className="border-border-default bg-surface-raised block rounded-sm border px-3 py-2" value={draftStatus} onChange={(event) => setDraftStatus(event.target.value)}>
                <option value="">{t('tasks.filters.allStatuses')}</option>
                {Object.keys(GROUP_ORDER_STATUS).map((status) => <option key={status} value={status}>{status}</option>)}
              </select>
            </label>
            <label className="text-text-secondary text-sm">{t('tasks.filters.agent')}
              <RecipientsInput multiple={false} value={draftAgent} onChange={setDraftAgent} agents={identityOptions} ariaLabel={t('tasks.filters.agent')} disabled={preflight.isPending} />
            </label>
            <Button type="submit" size="sm" disabled={preflight.isPending}>{t('tasks.filters.apply')}</Button>
            {filterError && <p role="alert">{renderThreadErrorDetail(filterError, t)}</p>}
            <Button type="button" variant="outline" size="sm" onClick={() => { ++filterAttempt.current; filterLatch.current = false; setFilterError(null); setDraftStatus(''); setDraftAgent(''); setFilters(undefined); }}>{t('tasks.filters.clear')}</Button>
          </form>
        )}
        {filters && <p className="text-text-secondary mb-4 text-sm">{t('tasks.filters.applied')} {filters.status && t('tasks.filters.appliedStatus', { status: filters.status })} {filters.assigned_agent && t('tasks.filters.appliedAgent', { agent: presentation.label(filters.assigned_agent) })}</p>}
        {/* The attention traversal owns its own loading/error states. Its rows
            render inside the shared list shell below, as the first group. */}
        {attentionLoading && (
          <p className="text-text-muted px-6 text-sm">{t('tasks.attention.loading')}</p>
        )}
        {attentionInitialError && (
          <div role="alert" className="border-feedback-danger bg-danger-soft mx-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
            <p className="text-text-primary text-sm font-medium">{t('tasks.attention.error')}</p>
            <Button size="sm" variant="outline" className="h-auto w-full max-w-full whitespace-normal break-words sm:w-auto" onClick={() => void attention.retry()}>
              <RefreshCw size={14} aria-hidden /> {t('common.retry')}
            </Button>
          </div>
        )}
        <style data-testid="tasks-responsive-styles">{`@media (max-width: 767px) {
          [data-tasks-responsive-list] > div:first-child { display: none; }
          [data-tasks-responsive-list] section li > div > a {
            display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: .5rem .75rem; align-items: center;
          }
          [data-tasks-responsive-list] section li > div > a > div { width: auto; min-width: 0; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(1) { grid-column: 1; grid-row: 1; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(2) { grid-column: 2; grid-row: 1; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(3) { grid-column: 1 / -1; grid-row: 2; overflow: visible; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(3) > span { white-space: normal; overflow: visible; text-overflow: clip; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(4) { grid-column: 1; grid-row: 3; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(5) { grid-column: 2; grid-row: 3; }
          [data-tasks-responsive-list] section li > div > a > div:nth-child(6) { grid-column: 2; grid-row: 4; justify-self: end; }
        }`}</style>
        {(topAlerts.length > 0 || partialLoading) && (
          <div className="mb-6 space-y-3">
            {topAlerts}
            {partialLoading && (
              <p className="text-text-muted text-sm" role="status">
                {t('tasks.list.partialLoading', { statuses: pendingOwners.map((stream) => statusLabel(stream.status)).join(t('tasks.list.statusSeparator')) })}
              </p>
            )}
          </div>
        )}
        {showListShell ? (
          <div className="space-y-6">
            <div data-testid="tasks-responsive-list" data-tasks-responsive-list>
              <TaskListColumnHeader />
              {showEscalatedGroup && (
                <div className="tasks-group">
                  {/* Same label/count read as every ordinary group. The truthful
                      non-exact count text lives in a count note (not inside the
                      heading) so the accessible heading name stays exactly
                      'Waiting on you'. */}
                  <div className="flex items-center justify-between gap-3">
                    <GroupHeading
                      id="waiting-on-you-heading"
                      label={t('tasks.attention.heading')}
                      dot="escalated"
                    />
                    <span className="text-text-muted mb-2 text-xs tabular-nums">
                      {attentionCount}
                    </span>
                  </div>
                  <section
                    aria-labelledby="waiting-on-you-heading"
                    className="border-border-default bg-surface-raised rounded-xl border shadow-sm"
                  >
                    <ul>
                      {attentionTasks.map((task) => (
                        <li key={task.task_id}>
                          <TaskListRow task={task} to={routes.detail(task.task_id)} taskRoutes={routes} />
                        </li>
                      ))}
                    </ul>
                  </section>
                  {attentionFailed && (
                    <div role="alert" className="border-feedback-danger bg-danger-soft mt-2 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
                      <p className="text-text-primary text-sm font-medium">{t('tasks.attention.partialError')}</p>
                      <Button size="sm" variant="outline" className="h-auto w-full max-w-full whitespace-normal break-words sm:w-auto" onClick={() => void attention.retry()} loading={attention.isContinuationError ? attention.isFetchingNextPage : attention.isRefreshing}>
                        <RefreshCw size={14} aria-hidden /> {attention.isContinuationError ? t('tasks.attention.retryMore') : t('common.retry')}
                      </Button>
                    </div>
                  )}
                  {attention.hasNextPage && !attentionFailed && (
                    <Button size="sm" variant="outline" className="mt-2 h-auto w-full max-w-full whitespace-normal break-words sm:w-auto" onClick={() => void attention.fetchNextPage({ explicit: true })} loading={attention.isFetchingNextPage}>
                      {t('tasks.attention.loadMore')}
                    </Button>
                  )}
                </div>
              )}
            {showOrdinaryGroups && groups.map(([key, tasks]) => {
              return (
                <div key={key} className="tasks-group">
                  <GroupHeading
                    label={groupBy === 'agent' && key !== UNASSIGNED_GROUP ? presentation.label(key) : groupLabel(key, groupBy, t)}
                    count={tasks.length}
                    dot={groupDot(key, groupBy)}
                  />
                  <section className="border-border-default bg-surface-raised rounded-xl border shadow-sm">
                    <ul>
                      {tasks.map((t) => (
                        <li key={t.task_id}>
                          <TaskListRow
                            task={t}
                            to={routes.detail(t.task_id)}
                            taskRoutes={routes}
                          />
                        </li>
                      ))}
                    </ul>
                  </section>
                </div>
              );
            })}
            </div>
            {/* The escalated group can be present while the ordinary traversal
                is still loading, errored, or empty. Keep those ordinary states
                truthful and visible WITHOUT hiding the shared list shell. */}
            {showEscalatedGroup && ordinaryLoading && (
              <p className="text-text-muted py-6 text-center text-sm">{t('tasks.list.loading')}</p>
            )}
            {showEscalatedGroup && ordinaryInitialError && (
              <EmptyState
                icon={<AlertCircle size={32} className="text-feedback-danger" />}
                title={t('tasks.list.errorTitle')}
                body={t('tasks.list.errorBody')}
                cta={{ label: isRetrying ? t('tasks.list.retrying') : t('common.retry'), onClick: () => void retryAllFailed() }}
              />
            )}
            <div ref={sentinelRef} aria-hidden className="h-1" />
            {anyFetchingNextPage && (
              <p className="text-text-muted py-3 text-center text-sm">
                {t('tasks.list.loadingMore')}
              </p>
            )}
            {moreAlerts}
            {globalEnd && (
              <p className="text-text-muted py-4 text-center text-xs">
                {t('tasks.list.endOfList')}
              </p>
            )}
          </div>
        ) : ordinaryLoading ? (
          <p className="text-text-muted py-6 text-center text-sm">{t('tasks.list.loading')}</p>
        ) : ordinaryInitialError ? (
          <EmptyState
            icon={<AlertCircle size={32} className="text-feedback-danger" />}
            title={t('tasks.list.errorTitle')}
            body={t('tasks.list.errorBody')}
            cta={{ label: isRetrying ? t('tasks.list.retrying') : t('common.retry'), onClick: () => void retryAllFailed() }}
          />
        ) : globalEmpty ? (
          <EmptyState title={t('tasks.list.emptyTitle')} body={t('tasks.list.emptyBody')} />
        ) : moreAlerts.length > 0 ? (
          <div className="space-y-6">{moreAlerts}</div>
        ) : null}
        </ContentWrap>
      </main>
    </div>
  );
}
