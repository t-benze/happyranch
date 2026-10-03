/**
 * AgentDetailPane — inline right detail/edit pane (Direction-A Pasture).
 *
 * Direction-A Pasture styling: font-display for agent name / section headings,
 * cards with shadow-pasture-sm + rounded-lg (18px), tag pills (rounded-full,
 * led dot), tabular-nums for counts/IDs.
 *
 * Sections: Header (agent identity), executor dropdown (live-derived from
 * useExecutorOptions — same source as AddAgentDialog, no hard-coded list),
 * repo/tool chips, system prompt collapsible, accountability metrics,
 * recent tasks/memory/jobs. Sticky save bar at bottom.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, MessageCircle, Plus, X, AlertCircle } from 'lucide-react';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  useAgentLearnings,
  useAgentsList,
  useAgentTasks,
  useCleanupActivity,
  useManageAgentRepo,
  useSetAgentExecutor,
  useSetAgentModel,
} from '@/hooks/agents';
import { useTasksRoutes } from '@/hooks/tasks';
import { useJobsList } from '@/hooks/jobs';
import { useDensity } from '@/hooks/density';
import { useTeamsList } from '@/hooks/teams';
import { isEligiblePolicyManager } from '@/hooks/authorityPolicy';
import { AgentAvatar } from './AgentAvatar';
import { useExecutorOptions } from './useExecutorOptions';
import { TeamEscalationPolicyEntryCard } from './TeamEscalationPolicyCard';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n/format';
import { classifyAgentError, renderAgentError, type AgentErrorView, type Translate } from './strings';

interface AgentDetailPaneProps {
  agentName: string;
  onClose: () => void;
  /** Called when the user clicks Start Thread for this agent. */
  onStartThread?: () => void;
}

/** Sparse dirty-state tracker: only keys that differ from the last-saved snapshot. */
interface DirtyState {
  executor?: string;
  /** Per-agent model string; empty string = unset/default (matches CLI clear semantics). */
  model?: string;
  /** Repos as a whole-dict replace: the current (possibly edited) map. */
  repos?: Record<string, string>;
  /** Names of repos removed since last save. */
  removedRepos?: Set<string>;
}

/**
 * One locale-neutral save-error entry. Held in state and rendered through `t`
 * on every render, so a locale switch re-translates the copy while daemon
 * diagnostics (via `classifyAgentError`) stay verbatim.
 */
type SaveErrorItem =
  | { kind: 'executorUnavailable'; name: string }
  | { kind: 'executor' | 'model'; view: AgentErrorView }
  | { kind: 'repo'; name: string; view: AgentErrorView };

function renderSaveError(item: SaveErrorItem, t: Translate): string {
  switch (item.kind) {
    case 'executorUnavailable':
      return t('agents.detail.saveErr.executorUnavailable', { name: item.name });
    case 'executor':
      return t('agents.detail.saveErr.executor', { message: renderAgentError(item.view, t) });
    case 'model':
      return t('agents.detail.saveErr.model', { message: renderAgentError(item.view, t) });
    case 'repo':
      return t('agents.detail.saveErr.repo', { name: item.name, message: renderAgentError(item.view, t) });
  }
}

function useAccountabilityMetrics(agentName: string) {
  const tasksQuery = useAgentTasks(agentName);
  const tasks = tasksQuery.data?.tasks ?? [];
  const done = tasks.filter((t) => t.status === 'completed' || t.status === 'superseded').length;
  const total = tasks.length;
  // Acceptance rate = (APPROVE+PASS verdicts) / reviewed tasks.
  // review_verdict is not a first-class TaskRecord field (it's on the audit log);
  // the DERIVE route (D-2) that would compute it from the audit_log is not yet
  // built. v1 renders only task counts — real derived counts, never estimates.
  return { tasksQuery, done, total };
}

export function AgentDetailPane({ agentName, onClose, onStartThread }: AgentDetailPaneProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const { t, locale, render } = useTranslation();
  const agentsQuery = useAgentsList();
  const teamsQuery = useTeamsList();
  const { density } = useDensity();
  const taskRoutes = useTasksRoutes();
  const learningsQuery = useAgentLearnings(agentName);
  const jobsQuery = useJobsList({ agent: agentName, status: 'all', limit: 10 });
  const { done, total, tasksQuery } = useAccountabilityMetrics(agentName);
  const cleanupQuery = useCleanupActivity(agentName);

  const setExecutor = useSetAgentExecutor();
  const setModel = useSetAgentModel();
  const manageRepo = useManageAgentRepo();
  const executorOptions = useExecutorOptions();

  const agent = agentsQuery.data?.agents.find((a) => a.name === agentName);
  const policyAgent = agent?.team && agent.role
    ? { name: agent.name, team: agent.team, role: agent.role }
    : undefined;
  const repos = useMemo(() => agent?.repos ?? {}, [agent?.repos]);

  // --- Dirty state ---
  const [dirty, setDirty] = useState<DirtyState>({});
  const [saveError, setSaveError] = useState<SaveErrorItem[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [repoAddName, setRepoAddName] = useState('');
  const [repoAddUrl, setRepoAddUrl] = useState('');
  const [showRepoAdd, setShowRepoAdd] = useState(false);

  // Reset dirty state when agent changes
  useEffect(() => {
    setDirty({});
    setSaveError(null);
    setSaving(false);
    setShowRepoAdd(false);
    setRepoAddName('');
    setRepoAddUrl('');
  }, [agentName]);

  const isDirty = dirty.executor !== undefined || dirty.model !== undefined || dirty.repos !== undefined;

  const displayExecutor = dirty.executor ?? agent?.executor ?? '—';
  const displayModel = dirty.model !== undefined ? dirty.model : (agent?.model ?? '');
  const displayRepos = dirty.repos ?? repos;

  // Is the agent's current executor (or the dirty value) available as a
  // selectable live option? Used to guard save + display.
  const currentExecutorName = agent?.executor ?? '';
  const liveSelectableNames = useMemo(
    () => new Set(executorOptions.selectable.map((o) => o.name)),
    [executorOptions.selectable],
  );
  const allLiveNames = useMemo(
    () => new Set([
      ...executorOptions.selectable.map((o) => o.name),
      ...executorOptions.unavailable.map((o) => o.name),
    ]),
    [executorOptions.selectable, executorOptions.unavailable],
  );
  // The current executor is "stale" when it is neither selectable nor
  // listed as unavailable — it has completely disappeared from the live
  // data (e.g., custom profile was removed). It must still be shown but
  // cannot be assigned.
  const currentExecutorIsStale =
    currentExecutorName !== '' && !allLiveNames.has(currentExecutorName);

  const onExecutorChange = useCallback((val: string) => {
    // Prevent selecting an unavailable option.
    if (!liveSelectableNames.has(val)) return;
    if (val === currentExecutorName) {
      setDirty((prev) => {
        const next = { ...prev };
        delete next.executor;
        return next;
      });
    } else {
      setDirty((prev) => ({ ...prev, executor: val }));
    }
    setSaveError(null);
  }, [currentExecutorName, liveSelectableNames]);

  const onModelChange = useCallback((val: string) => {
    const trimmed = val.trim();
    const currentAgentModel = agent?.model ?? '';
    if (trimmed === (currentAgentModel || '')) {
      setDirty((prev) => {
        const next = { ...prev };
        delete next.model;
        return next;
      });
    } else {
      setDirty((prev) => ({ ...prev, model: trimmed }));
    }
    setSaveError(null);
  }, [agent?.model]);

  const onRepoRemove = useCallback((key: string) => {
    setDirty((prev) => {
      const current = prev.repos ?? { ...repos };
      const next = { ...current };
      delete next[key];
      const removed = new Set(prev.removedRepos ?? []);
      removed.add(key);
      return { ...prev, repos: next, removedRepos: removed };
    });
    setSaveError(null);
  }, [repos]);

  const onRepoAdd = useCallback(() => {
    const name = repoAddName.trim();
    const url = repoAddUrl.trim();
    if (!name || !url) return;
    setDirty((prev) => {
      const current = prev.repos ?? { ...repos };
      return { ...prev, repos: { ...current, [name]: url } };
    });
    setRepoAddName('');
    setRepoAddUrl('');
    setShowRepoAdd(false);
    setSaveError(null);
  }, [repoAddName, repoAddUrl, repos]);

  const onSave = useCallback(async () => {
    if (!slug) return;
    setSaving(true);
    setSaveError(null);
    const errors: SaveErrorItem[] = [];

    // Save executor if dirty — guard: don't send an executor that is no
    // longer selectable (e.g., refetched away or unavailable).
    if (dirty.executor && dirty.executor !== agent?.executor) {
      if (!liveSelectableNames.has(dirty.executor)) {
        errors.push({ kind: 'executorUnavailable', name: dirty.executor });
      } else {
        try {
          await setExecutor.mutateAsync({
            agentName,
            body: { executor: dirty.executor },
          });
        } catch (err: unknown) {
          errors.push({ kind: 'executor', view: classifyAgentError(err, 'agents.error.saveFailed') });
          // Preserve dirty state on error — user can retry.
          setSaving(false);
          setSaveError(errors);
          return;
        }
      }
    }

    // Save model if dirty
    if (dirty.model !== undefined) {
      const targetModel = dirty.model || null;
      const currentModel = agent?.model ?? null;
      if (targetModel !== currentModel) {
        try {
          await setModel.mutateAsync({
            agentName,
            body: { model: targetModel },
          });
        } catch (err: unknown) {
          errors.push({ kind: 'model', view: classifyAgentError(err, 'agents.error.saveFailed') });
        }
      }
    }

    // Save repo changes if dirty
    if (dirty.repos) {
      const original = agent?.repos ?? {};
      const current = dirty.repos;
      // Removals
      for (const key of dirty.removedRepos ?? []) {
        if (!(key in current) && key in original) {
          try {
            await manageRepo.mutateAsync({
              agentName,
              body: { action: 'remove', repo_name: key },
            });
          } catch (err: unknown) {
            errors.push({ kind: 'repo', name: key, view: classifyAgentError(err, 'agents.error.removeFailed') });
          }
        }
      }
      // Adds
      for (const [key, url] of Object.entries(current)) {
        if (!(key in original)) {
          try {
            await manageRepo.mutateAsync({
              agentName,
              body: { action: 'add', repo_name: key, url },
            });
          } catch (err: unknown) {
            errors.push({ kind: 'repo', name: key, view: classifyAgentError(err, 'agents.error.addFailed') });
          }
        }
      }
      // Updates (repo existed before but URL changed)
      for (const [key, url] of Object.entries(current)) {
        if (key in original && original[key] !== url) {
          try {
            await manageRepo.mutateAsync({
              agentName,
              body: { action: 'update', repo_name: key, url },
            });
          } catch (err: unknown) {
            errors.push({ kind: 'repo', name: key, view: classifyAgentError(err, 'agents.error.updateFailed') });
          }
        }
      }
    }

    if (errors.length > 0) {
      setSaveError(errors);
    } else {
      setDirty({});
    }
    setSaving(false);
  }, [slug, dirty, agentName, agent, setExecutor, setModel, manageRepo, liveSelectableNames]);

  const onReset = useCallback(() => {
    setDirty({});
    setSaveError(null);
  }, []);

  // ⌘S keyboard shortcut
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        void onSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isDirty, onSave]);

  const learningsError =
    learningsQuery.isError && learningsQuery.error instanceof ApiError
      ? learningsQuery.error
      : null;

  return (
    <section className="flex h-full flex-col">
      {/* --- Header --- */}
      <header className="border-border-default flex items-start gap-3.5 border-b px-5 py-4">
        {/* AGENTS-04: detail-hero avatar anchor (Direction-A `a-agents`),
            reusing the roster's role-colored initial chip. */}
        <AgentAvatar name={agentName} role={agent?.role ?? null} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-text-primary truncate text-xl font-medium">
              {agentName}
            </h2>
            <span
              aria-hidden="true"
              className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                agent?.role === 'manager'
                  ? 'bg-agent-manager'
                  : 'bg-agent-worker'
              }`}
            />
          </div>
          <div className="text-text-muted mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
            <span className="bg-surface-sunken border-border-default rounded-full border px-2 py-px text-xs font-medium">
              {agent?.role ?? '…'}
            </span>
            <span className="tabular-nums">
              {agent?.team ?? '—'}
            </span>
            {agent?.executor && (
              <>
                <span aria-hidden="true" className="text-text-muted">·</span>
                <span className="bg-accent-soft text-accent-text rounded-full px-2 py-px text-xs font-medium">
                  {agent.executor}
                </span>
              </>
            )}
            {agent?.model && (
              <>
                <span aria-hidden="true" className="text-text-muted">·</span>
                <span className="bg-surface-sunken border-border-default rounded-full border px-2 py-px text-xs font-medium">
                  {agent.model}
                </span>
              </>
            )}
          </div>
          {agent?.description && (
            <p className="text-text-secondary mt-2 text-sm leading-relaxed">{agent.description}</p>
          )}
        </div>
        {onStartThread ? (
          <Button size="sm" className="shrink-0" onClick={onStartThread}>
            <MessageCircle size={14} className="mr-1" aria-hidden="true" />
            {t('agents.detail.startThread')}
          </Button>
        ) : (
          <Button variant="ghost" size="sm" className="shrink-0" onClick={onClose} aria-label={t('common.close')}>
            <X size={16} />
          </Button>
        )}
      </header>

      {/* --- Editable fields — Pasture card sections --- */}
      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {isEligiblePolicyManager(
          policyAgent,
          teamsQuery.data?.teams,
        ) && policyAgent && (
          <TeamEscalationPolicyEntryCard agent={policyAgent} />
        )}
        {/* Executor — live-derived dropdown (same source as AddAgentDialog) */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.executor.label')}
          </h3>
          {executorOptions.state === 'loading' ? (
            <p className="text-text-muted text-sm">{t('agents.executor.loading')}</p>
          ) : executorOptions.state === 'error' ? (
            <p className="text-tier-red text-xs">
              {t('agents.detail.executorError')}
            </p>
          ) : (
            <select
              value={displayExecutor}
              onChange={(e) => onExecutorChange(e.target.value)}
              className="border-border-subtle bg-surface w-full max-w-xs rounded border p-2 text-sm"
              aria-label={t('agents.executor.label')}
            >
              {/* Stale current executor — visible but not assignable */}
              {currentExecutorIsStale && (
                <option key={currentExecutorName} value={currentExecutorName} disabled>
                  {t('agents.detail.staleOption', { name: currentExecutorName })}
                </option>
              )}
              {/* Unavailable current executor that IS known but not launchable */}
              {!currentExecutorIsStale &&
                executorOptions.unavailable.some((o) => o.name === displayExecutor) && (
                <option key={displayExecutor} value={displayExecutor} disabled>
                  {t('agents.detail.unavailableCurrentOption', { name: displayExecutor })}
                </option>
              )}
              {executorOptions.selectable.map((opt) => (
                <option key={opt.name} value={opt.name}>
                  {opt.kind === 'custom' ? t('agents.executor.customOption', { name: opt.name }) : opt.name}
                </option>
              ))}
              {executorOptions.unavailable
                .filter((o) => o.name !== displayExecutor)
                .length > 0 && (
                <>
                  <option disabled>{t('agents.executor.unavailableHeader')}</option>
                  {executorOptions.unavailable
                    .filter((o) => o.name !== displayExecutor)
                    .map((opt) => (
                      <option key={opt.name} value={opt.name} disabled>
                        {opt.kind === 'custom'
                          ? t('agents.detail.customUnavailableOption', { name: opt.name })
                          : t('agents.detail.notRegisteredOption', { name: opt.name })}
                      </option>
                    ))}
                </>
              )}
            </select>
          )}
          <p className="text-text-muted mt-2 text-xs">
            {t('agents.detail.nextTask')}
            {currentExecutorIsStale && (
              <>
                {' '}
                {t('agents.detail.staleNote', { name: currentExecutorName })}
              </>
            )}
          </p>
          {/* Settings → Executors navigation link for stale/unavailable executors */}
          {slug && (currentExecutorIsStale || executorOptions.unavailable.length > 0) && (
            <p className="text-text-muted mt-1 text-xs">
              {currentExecutorIsStale
                ? t('agents.detail.staleLink')
                : t('agents.executor.needRegister')}{' '}
              <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
                {t('agents.executor.settingsLink')}
              </Link>
            </p>
          )}
        </section>

        {/* Model — freeform text input (executor-dependent, not a fixed enum) */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.detail.model')}
          </h3>
          <input
            type="text"
            value={displayModel}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder={agent?.executor ? t('agents.detail.modelDefault', { executor: agent.executor }) : t('agents.detail.modelUnset')}
            className="border-border-subtle bg-surface w-full max-w-xs rounded-md border px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent-soft"
            aria-label={t('agents.detail.model')}
          />
          <p className="text-text-muted mt-2 text-xs">
            {t('agents.detail.modelHelp')}
          </p>
        </section>

        {/* System prompt — READ-ONLY card */}
        {agent?.system_prompt && (
          <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border">
            <button
              type="button"
              onClick={() => setShowPrompt(!showPrompt)}
              className="text-text-secondary hover:text-text-primary flex w-full items-center gap-2 px-4 py-3 text-xs font-medium tracking-wider uppercase transition-colors"
            >
              {showPrompt ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              {t('agents.field.systemPrompt')}
            </button>
            {showPrompt && (
              <div className="border-border-default border-t px-4 pb-4">
                <pre className="bg-surface-sunken border-border-subtle mt-3 max-h-48 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {agent.system_prompt}
                </pre>
                <div className="text-text-muted mt-2 flex items-center gap-1.5 text-xs">
                  <AlertCircle size={12} />
                  <span>{t('agents.detail.systemPromptReadOnly')}</span>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Description — READ-ONLY */}
        {agent?.description && (
          <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
            <h3 className="text-overline text-text-muted mb-2 tracking-wider uppercase">
              {t('agents.field.description')}
            </h3>
            <p className="text-text-secondary text-sm leading-relaxed">{agent.description}</p>
            <div className="text-text-muted mt-2 flex items-center gap-1.5 text-xs">
              <AlertCircle size={12} />
              <span>{t('agents.detail.descriptionReadOnly')}</span>
            </div>
          </section>
        )}

        {/* Repo chips — rounded-full tag pattern */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.detail.repos')}
          </h3>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {Object.entries(displayRepos).map(([key, url]) => (
              <span
                key={key}
                className="bg-surface-sunken border-border-default text-text-secondary inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium"
              >
                <span className="bg-agent-worker inline-block h-1.5 w-1.5 shrink-0 rounded-full" />
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-accent-text transition-colors"
                >
                  {key}
                </a>
                <button
                  type="button"
                  onClick={() => onRepoRemove(key)}
                  className="text-text-muted hover:text-tier-red ml-0.5 transition-colors"
                  aria-label={t('agents.detail.removeRepo', { name: key })}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {Object.keys(displayRepos).length === 0 && (
              <span className="text-text-muted text-xs">{t('agents.detail.noRepos')}</span>
            )}
          </div>
          {showRepoAdd ? (
            <div className="bg-surface-sunken border-border-default space-y-2 rounded-lg border p-3">
              <input
                className="border-border-subtle bg-surface w-full rounded-md border px-2.5 py-1.5 text-xs"
                placeholder={t('agents.detail.repoNamePlaceholder')}
                value={repoAddName}
                onChange={(e) => setRepoAddName(e.target.value)}
              />
              <input
                className="border-border-subtle bg-surface w-full rounded-md border px-2.5 py-1.5 text-xs"
                placeholder={t('agents.detail.repoUrlPlaceholder')}
                value={repoAddUrl}
                onChange={(e) => setRepoAddUrl(e.target.value)}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={onRepoAdd} disabled={!repoAddName.trim() || !repoAddUrl.trim()}>
                  {t('agents.detail.repoAdd')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setShowRepoAdd(false)}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowRepoAdd(true)}
            >
              <Plus size={14} className="mr-1" />
              {t('agents.detail.addRepo')}
            </Button>
          )}
        </section>

        {/* Accountability metrics — display font, card */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.detail.accountability')}
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-text-muted text-xs">{t('agents.common.loading')}</p>
          ) : tasksQuery.isError ? (
            <p className="text-tier-red text-xs">
              {renderAgentError(classifyAgentError(tasksQuery.error, 'agents.detail.taskCountsError'), t)}
            </p>
          ) : (
            <div className="flex items-baseline gap-3">
              <span className="font-display text-text-primary text-2xl font-medium tabular-nums">
                {total}
              </span>
              <span className="text-text-secondary text-sm tabular-nums">
                {t('agents.detail.tasksUnit', { count: total })}
              </span>
              <span aria-hidden="true" className="text-text-muted">·</span>
              <span className="font-display text-text-primary text-2xl font-medium tabular-nums">
                {done}
              </span>
              <span className="text-text-secondary text-sm tabular-nums">
                {t('agents.detail.doneUnit', { count: done })}
              </span>
            </div>
          )}
        </section>

        {/* Recent tasks */}
        <section>
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.detail.recentTasks')}
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-text-muted text-xs">{t('agents.detail.loadingTasks')}</p>
          ) : tasksQuery.data && tasksQuery.data.tasks.length > 0 ? (
            <ul className="space-y-2">
              {tasksQuery.data.tasks.map((t) => (
                <li key={t.task_id}>
                  <TaskCard
                    task={t}
                    to={taskRoutes.detail(t.task_id)}
                    density={density}
                    taskRoutes={taskRoutes}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-text-muted text-xs">
              {t('agents.detail.noTasks')}
            </p>
          )}
        </section>

        <section>
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">{t('agents.detail.cleanup')}</h3>
          {cleanupQuery.isLoading ? <p className="text-text-muted text-xs">{t('agents.detail.cleanupLoading')}</p>
            : cleanupQuery.isError ? <div><p className="text-tier-red text-xs">{renderAgentError(classifyAgentError(cleanupQuery.error, 'agents.detail.cleanupError'), t)}</p><Button size="sm" variant="ghost" onClick={() => cleanupQuery.refetch()}>{t('common.retry')}</Button></div>
            : cleanupQuery.data?.activities.length ? <ul className="space-y-2">{cleanupQuery.data.activities.map((activity) => {
              const summary = activity.output_summary?.trim() || t('agents.detail.summaryUnavailable');
              return <li key={activity.task_id} className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-3"><Link to={taskRoutes.detail(activity.task_id)} className="text-accent-text break-all text-sm hover:underline">{activity.task_id}</Link><p className="text-text-muted mt-1 text-xs">{t('agents.detail.cleanupRun', { date: formatDateShapeFor(locale, new Date(activity.created_at), 'monthDayYear'), status: activity.status })}{activity.result_status ? ` · ${t('agents.detail.cleanupResult', { result: activity.result_status })}` : ''}</p><p className="text-text-primary mt-2 break-words text-sm whitespace-pre-wrap">{summary}</p></li>;
            })}</ul> : <p className="text-text-muted text-xs">{t('agents.detail.noCleanup')}</p>}
        </section>

        {/* Learnings */}
        <section>
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            {t('agents.detail.learnings')}
          </h3>
          {learningsQuery.isLoading ? (
            <p className="text-text-muted text-xs">{t('agents.detail.learningsLoading')}</p>
          ) : learningsError?.status === 412 ? (
            <p className="text-text-muted text-xs">
              {render('agents.detail.learningsNotMigrated', { command: <code>happyranch memory reindex</code> })}
            </p>
          ) : learningsError ? (
            <p className="text-tier-red text-xs">
              {renderAgentError(classifyAgentError(learningsError, 'agents.detail.learningsError'), t)}
            </p>
          ) : learningsQuery.data && learningsQuery.data.entries.length > 0 ? (
            <ul className="space-y-2">
              {learningsQuery.data.entries.map((e) => (
                <li
                  key={e.id}
                  className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-3"
                >
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-text-muted font-mono tabular-nums">{e.id}</span>
                    <span className="text-text-muted">·</span>
                    <span className="text-text-muted">{e.topic}</span>
                  </div>
                  <p className="text-text-primary mt-1 text-sm font-medium">{e.title}</p>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title={t('agents.detail.noLearningsTitle')}
              body={t('agents.detail.noLearningsBody')}
            />
          )}
        </section>

        {/* Recent jobs — object-ID click-through */}
        {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
          <section>
            <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
              {t('agents.detail.recentJobs')}
            </h3>
            <ul className="space-y-1.5 text-sm">
              {jobsQuery.data.jobs.map((j) => (
                <li
                  key={j.id}
                  className="border-border-default bg-surface shadow-pasture-sm rounded-lg border px-3 py-2"
                >
                  {slug ? (
                    <Link
                      to={`/orgs/${slug}/jobs/${j.id}`}
                      className="text-accent-text font-mono text-xs tabular-nums hover:underline"
                    >
                      {j.id}
                    </Link>
                  ) : (
                    <span className="font-mono text-xs tabular-nums">{j.id}</span>
                  )}
                  <span className="text-text-primary ml-2">{j.title}</span>
                  <span className="text-text-muted ml-2 text-xs">
                    <span className="bg-surface-sunken border-border-default rounded-full border px-1.5 py-px text-xs font-medium">
                      {j.status}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      {/* --- Sticky save bar — Pasture border/background --- */}
      {isDirty && (
        <footer className="border-border-default bg-surface-sunken flex items-center justify-between gap-3 border-t px-4 py-3">
          <div className="flex items-center gap-2">
            {saveError && (
              <div className="text-tier-red flex items-center gap-1.5 text-xs">
                <AlertCircle size={12} />
                <span>{t('agents.detail.saveError', { message: saveError.map((item) => renderSaveError(item, t)).join('; ') })}</span>
              </div>
            )}
            {!saveError && (
              <p className="text-text-muted text-xs">
                {render('agents.detail.unsaved', {
                  shortcut: <kbd className="bg-surface border-border-default rounded border px-1.5 py-px font-mono text-xs">⌘S</kbd>,
                })}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={onReset}>
              {t('agents.detail.reset')}
            </Button>
            <Button size="sm" onClick={onSave} disabled={saving}>
              {saving ? t('agents.detail.saving') : t('agents.detail.save')}
            </Button>
          </div>
        </footer>
      )}
    </section>
  );
}
