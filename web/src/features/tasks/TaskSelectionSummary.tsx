/**
 * Task detail: the model/effort that applies to this task, who selected each
 * field, and what the latest session actually started with (THR-356).
 *
 * `effective_*` and `*_source` come straight from Task GET and resolve the
 * CURRENT agent default. A null effective value is only called the CLI
 * default when the assigned agent is still in the roster; otherwise the
 * default cannot be read and the row says so instead of guessing. The
 * "last started with" row is the existing `session_start` audit row.
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

/** The latest `session_start` audit row's recorded launch settings. */
export function latestSessionSelection(auditLog: unknown[] | undefined): LastRunSelection | null {
  if (!Array.isArray(auditLog)) return null;
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

export function TaskSelectionSummary({
  task,
  lastRun,
}: {
  task: TaskRecord;
  lastRun: LastRunSelection | null;
}): JSX.Element | null {
  const { t } = useTranslation();
  const agents = useAgentsList();
  // Daemons that predate THR-356 return none of these fields: render nothing
  // rather than an invented default.
  if (task.model_source === undefined && task.reasoning_effort_source === undefined) return null;
  const agentKnown = !!task.assigned_agent
    && (agents.data?.agents ?? []).some((agent) => agent.name === task.assigned_agent);

  const row = (
    name: 'model' | 'effort',
    value: string | null | undefined,
    source: TaskSelectionSource | null | undefined,
  ): JSX.Element => {
    const unavailable = value == null && !agentKnown;
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
              {t(unavailable ? 'tasks.selection.unavailable' : 'tasks.selection.cliDefault')}
            </span>
          )}
          <span className="text-text-muted block text-xs">
            {unavailable
              ? t('tasks.selection.unavailableHelp')
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
            {lastRun === null ? t('tasks.selection.notStarted') : t('tasks.selection.lastRunValue', {
              executor: lastRun.executor ?? t('tasks.selection.notRecorded'),
              model: lastRun.model ?? t('tasks.selection.cliDefault'),
              effort: lastRun.effort ?? t('tasks.selection.cliDefault'),
            })}
          </dd>
        </div>
      </dl>
      <p className="text-text-muted mt-3 text-xs">{t('tasks.selection.currentNote')}</p>
    </section>
  );
}
