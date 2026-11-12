/**
 * Task detail: the model/effort that applies to this task, who selected each
 * field, and what the latest session actually started with (THR-356).
 *
 * `effective_*` and `*_source` come straight from Task GET and resolve the
 * CURRENT agent default. A null effective value is only called the CLI
 * default when a successful roster read contains the assigned agent, and the
 * agent is only called gone when the task names one and a successful read
 * lacks it; while the roster is loading or unreadable the row says that
 * instead of guessing. A task with no stored assignment is its own state: the
 * daemon resolves the team manager's default for it, so nobody was removed. The
 * "last started with" row is the existing `session_start` audit row, with
 * loading and unreadable audit data kept apart from a confirmed absence.
 */
import { useAgentsList } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import type { TaskRecord, TaskSelectionSource } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';

export interface LastRunSelection {
  executor: string | null;
  model: string | null;
  effort: string | null;
}

/** What is known about the latest launch: `value: null` is a CONFIRMED absence. */
export type LastRunState =
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'ready'; value: LastRunSelection | null };

/**
 * The latest `session_start` audit row's recorded launch settings; null when
 * the audit log has none, undefined when the response carried no audit log.
 */
export function latestSessionSelection(auditLog: unknown[] | undefined): LastRunSelection | null | undefined {
  if (!Array.isArray(auditLog)) return undefined;
  for (let i = auditLog.length - 1; i >= 0; i--) {
    const entry = auditLog[i] as { action?: unknown; payload?: unknown };
    if (entry?.action !== 'session_start') continue;
    const payload = (entry.payload ?? {}) as Record<string, unknown>;
    const text = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null);
    return { executor: text(payload.executor), model: text(payload.model), effort: text(payload.reasoning_effort) };
  }
  return null;
}

const SOURCE_KEYS: Record<TaskSelectionSource, MessageKey> = {
  founder: 'tasks.selection.source.founder',
  manager: 'tasks.selection.source.manager',
  agent_default: 'tasks.selection.source.agentDefault',
};

type AgentState = 'known' | 'gone' | 'loading' | 'unreadable' | 'unassigned';

const NULL_VALUE_KEYS: Record<AgentState, MessageKey> = {
  known: 'tasks.selection.cliDefault',
  gone: 'tasks.selection.unavailable',
  loading: 'tasks.selection.rosterLoading',
  unreadable: 'tasks.selection.unavailable',
  unassigned: 'tasks.selection.unassigned',
};

const NULL_HELP_KEYS: Record<Exclude<AgentState, 'known'>, MessageKey> = {
  gone: 'tasks.selection.unavailableHelp',
  loading: 'tasks.selection.rosterLoadingHelp',
  unreadable: 'tasks.selection.rosterUnreadableHelp',
  unassigned: 'tasks.selection.unassignedHelp',
};

export function TaskSelectionSummary({
  task,
  lastRun,
}: {
  task: TaskRecord;
  lastRun: LastRunState;
}): JSX.Element | null {
  const { t } = useTranslation();
  const agents = useAgentsList();
  // Daemons that predate THR-356 return none of these fields: render nothing
  // rather than an invented default.
  if (task.model_source === undefined && task.reasoning_effort_source === undefined) return null;
  // Only a successful roster read can establish that the agent is present
  // or gone; a pending or failed read establishes neither. No stored
  // assignment is not a removal, whatever the roster read says.
  const roster = agents.data?.agents;
  const agentState: AgentState = !task.assigned_agent
    ? 'unassigned'
    : roster !== undefined
      ? (roster.some((agent) => agent.name === task.assigned_agent) ? 'known' : 'gone')
      : agents.isError ? 'unreadable' : 'loading';

  const row = (
    name: 'model' | 'effort',
    value: string | null | undefined,
    source: TaskSelectionSource | null | undefined,
  ): JSX.Element => {
    const unresolved = value == null && agentState !== 'known' ? agentState : null;
    return (
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1" data-selection-field={name} data-selection-source={source ?? ''}>
        <dt className="text-text-muted w-20 shrink-0 text-xs">
          {t(name === 'model' ? 'tasks.selection.model' : 'tasks.selection.effort')}
        </dt>
        <dd className="min-w-0 flex-1 break-words">
          {value != null ? (
            <span className="text-text-primary font-mono text-xs">{value}</span>
          ) : (
            <span className="text-text-secondary text-xs">
              {t(NULL_VALUE_KEYS[agentState])}
            </span>
          )}
          <span className="text-text-muted block text-xs">
            {unresolved !== null
              ? t(NULL_HELP_KEYS[unresolved])
              : source ? t(SOURCE_KEYS[source] ?? 'tasks.selection.source.agentDefault') : null}
          </span>
        </dd>
      </div>
    );
  };

  return (
    <section aria-label={t('tasks.selection.heading')} className="border-border-default mt-4 min-w-0 border-t pt-4">
      <h3 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        {t('tasks.selection.heading')}
      </h3>
      <dl className="space-y-3 text-sm">
        {row('model', task.effective_model, task.model_source)}
        {row('effort', task.effective_reasoning_effort, task.reasoning_effort_source)}
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1" data-selection-field="last-run">
          <dt className="text-text-muted w-20 shrink-0 text-xs">{t('tasks.selection.lastRun')}</dt>
          <dd className="text-text-secondary min-w-0 flex-1 break-words text-xs">
            {lastRun.status === 'loading' ? t('tasks.selection.lastRunLoading')
              : lastRun.status === 'unavailable' ? t('tasks.selection.lastRunUnavailable')
              : lastRun.value === null ? t('tasks.selection.notStarted')
              : t('tasks.selection.lastRunValue', {
                executor: lastRun.value.executor ?? t('tasks.selection.notRecorded'),
                model: lastRun.value.model ?? t('tasks.selection.cliDefault'),
                effort: lastRun.value.effort ?? t('tasks.selection.cliDefault'),
              })}
          </dd>
        </div>
      </dl>
      <p className="text-text-muted mt-3 text-xs">{t('tasks.selection.currentNote')}</p>
    </section>
  );
}
