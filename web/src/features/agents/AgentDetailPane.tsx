/**
 * AgentDetailPane — inline right detail/edit pane (Direction-A Pasture).
 *
 * Direction-A Pasture styling: font-display for agent name / section headings,
 * cards with shadow-pasture-sm + rounded-lg (18px), tag pills (rounded-full,
 * led dot), tabular-nums for counts/IDs, executor as segmented control.
 *
 * Sections: Header (agent identity), executor segmented control, repo/tool
 * chips, system prompt collapsible, accountability metrics, recent
 * tasks/memory/jobs. Sticky save bar at bottom.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Plus, X, AlertCircle } from 'lucide-react';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  useAgentLearnings,
  useAgentsList,
  useAgentTasks,
  useManageAgentRepo,
  useSetAgentExecutor,
} from '@/hooks/agents';
import { useTasksRoutes } from '@/hooks/tasks';
import { useJobsList } from '@/hooks/jobs';
import { useDensity } from '@/hooks/density';

const EXECUTOR_OPTIONS = [
  { value: 'claude', label: 'claude' },
  { value: 'codex', label: 'codex' },
  { value: 'opencode', label: 'opencode' },
  { value: 'pi', label: 'pi' },
] as const;

interface AgentDetailPaneProps {
  agentName: string;
  onClose: () => void;
}

/** Sparse dirty-state tracker: only keys that differ from the last-saved snapshot. */
interface DirtyState {
  executor?: string;
  /** Repos as a whole-dict replace: the current (possibly edited) map. */
  repos?: Record<string, string>;
  /** Names of repos removed since last save. */
  removedRepos?: Set<string>;
}

function useAccountabilityMetrics(agentName: string) {
  const tasksQuery = useAgentTasks(agentName);
  const tasks = tasksQuery.data?.tasks ?? [];
  const done = tasks.filter((t) => t.status === 'completed' || t.status === 'resolved_superseded').length;
  const total = tasks.length;
  // Acceptance rate = (APPROVE+PASS verdicts) / reviewed tasks.
  // review_verdict is not a first-class TaskRecord field (it's on the audit log);
  // the DERIVE route (D-2) that would compute it from the audit_log is not yet
  // built. v1 renders only task counts — real derived counts, never estimates.
  return { tasksQuery, done, total };
}

export function AgentDetailPane({ agentName, onClose }: AgentDetailPaneProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const agentsQuery = useAgentsList();
  const { density } = useDensity();
  const taskRoutes = useTasksRoutes();
  const learningsQuery = useAgentLearnings(agentName);
  const jobsQuery = useJobsList({ agent: agentName, status: 'all', limit: 10 });
  const { done, total, tasksQuery } = useAccountabilityMetrics(agentName);

  const setExecutor = useSetAgentExecutor();
  const manageRepo = useManageAgentRepo();

  const agent = agentsQuery.data?.agents.find((a) => a.name === agentName);
  const repos = useMemo(() => agent?.repos ?? {}, [agent?.repos]);

  // --- Dirty state ---
  const [dirty, setDirty] = useState<DirtyState>({});
  const [saveError, setSaveError] = useState<string | null>(null);
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

  const isDirty = dirty.executor !== undefined || dirty.repos !== undefined;

  const displayExecutor = dirty.executor ?? agent?.executor ?? '—';
  const displayRepos = dirty.repos ?? repos;

  const onExecutorChange = useCallback((val: string) => {
    if (val === agent?.executor) {
      setDirty((prev) => {
        const next = { ...prev };
        delete next.executor;
        return next;
      });
    } else {
      setDirty((prev) => ({ ...prev, executor: val }));
    }
    setSaveError(null);
  }, [agent?.executor]);

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
    const errors: string[] = [];

    // Save executor if dirty
    if (dirty.executor && dirty.executor !== agent?.executor) {
      try {
        await setExecutor.mutateAsync({
          agentName,
          body: { executor: dirty.executor },
        });
      } catch (err: unknown) {
        const e = err as { message?: string };
        errors.push(`Executor: ${e.message ?? 'save failed'}`);
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
            const e = err as { message?: string };
            errors.push(`Repo ${key}: ${e.message ?? 'remove failed'}`);
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
            const e = err as { message?: string };
            errors.push(`Repo ${key}: ${e.message ?? 'add failed'}`);
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
            const e = err as { message?: string };
            errors.push(`Repo ${key}: ${e.message ?? 'update failed'}`);
          }
        }
      }
    }

    if (errors.length > 0) {
      setSaveError(errors.join('; '));
    } else {
      setDirty({});
    }
    setSaving(false);
  }, [slug, dirty, agentName, agent, setExecutor, manageRepo]);

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
      <header className="border-border-default flex items-start justify-between gap-3 border-b px-5 py-4">
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
          </div>
          {agent?.description && (
            <p className="text-text-secondary mt-2 text-sm leading-relaxed">{agent.description}</p>
          )}
        </div>
        <Button variant="ghost" size="sm" className="shrink-0" onClick={onClose}>
          <X size={16} />
        </Button>
      </header>

      {/* --- Editable fields — Pasture card sections --- */}
      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {/* Executor — segmented control */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            Executor
          </h3>
          <div className="flex gap-1">
            {EXECUTOR_OPTIONS.map((opt) => {
              const selected = displayExecutor === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => onExecutorChange(opt.value)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    selected
                      ? 'bg-accent-soft text-accent-text border border-transparent'
                      : 'bg-surface-sunken text-text-muted border-border-default hover:border-border-strong border'
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          <p className="text-text-muted mt-2 text-xs">
            Takes effect on this agent's next task.
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
              System prompt
            </button>
            {showPrompt && (
              <div className="border-border-default border-t px-4 pb-4">
                <pre className="bg-surface-sunken border-border-subtle mt-3 max-h-48 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {agent.system_prompt}
                </pre>
                <div className="text-text-muted mt-2 flex items-center gap-1.5 text-xs">
                  <AlertCircle size={12} />
                  <span>
                    Read-only. Updating system prompt from the web UI requires a
                    founder-facing route.
                  </span>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Description — READ-ONLY */}
        {agent?.description && (
          <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
            <h3 className="text-overline text-text-muted mb-2 tracking-wider uppercase">
              Description
            </h3>
            <p className="text-text-secondary text-sm leading-relaxed">{agent.description}</p>
            <div className="text-text-muted mt-2 flex items-center gap-1.5 text-xs">
              <AlertCircle size={12} />
              <span>Read-only — no founder-facing update route for description.</span>
            </div>
          </section>
        )}

        {/* Repo chips — rounded-full tag pattern */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            Repositories
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
                  aria-label={`Remove ${key}`}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {Object.keys(displayRepos).length === 0 && (
              <span className="text-text-muted text-xs">No repositories configured.</span>
            )}
          </div>
          {showRepoAdd ? (
            <div className="bg-surface-sunken border-border-default space-y-2 rounded-lg border p-3">
              <input
                className="border-border-subtle bg-surface w-full rounded-md border px-2.5 py-1.5 text-xs"
                placeholder="Repo name (e.g. happyranch)"
                value={repoAddName}
                onChange={(e) => setRepoAddName(e.target.value)}
              />
              <input
                className="border-border-subtle bg-surface w-full rounded-md border px-2.5 py-1.5 text-xs"
                placeholder="Git URL"
                value={repoAddUrl}
                onChange={(e) => setRepoAddUrl(e.target.value)}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={onRepoAdd} disabled={!repoAddName.trim() || !repoAddUrl.trim()}>
                  Add
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setShowRepoAdd(false)}>
                  Cancel
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
              Add repository
            </Button>
          )}
        </section>

        {/* Accountability metrics — display font, card */}
        <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            Accountability
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-text-muted text-xs">Loading…</p>
          ) : tasksQuery.isError ? (
            <p className="text-tier-red text-xs">
              Failed to load task counts.
            </p>
          ) : (
            <div className="flex items-baseline gap-3">
              <span className="font-display text-text-primary text-2xl font-medium tabular-nums">
                {total}
              </span>
              <span className="text-text-secondary text-sm tabular-nums">
                tasks
              </span>
              <span aria-hidden="true" className="text-text-muted">·</span>
              <span className="font-display text-text-primary text-2xl font-medium tabular-nums">
                {done}
              </span>
              <span className="text-text-secondary text-sm tabular-nums">
                done
              </span>
            </div>
          )}
        </section>

        {/* Recent tasks */}
        <section>
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            Recent tasks
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-text-muted text-xs">Loading tasks…</p>
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
              No tasks where this agent was the assigned manager.
            </p>
          )}
        </section>

        {/* Learnings */}
        <section>
          <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
            Learnings
          </h3>
          {learningsQuery.isLoading ? (
            <p className="text-text-muted text-xs">Loading learnings…</p>
          ) : learningsError?.status === 412 ? (
            <p className="text-text-muted text-xs">
              This workspace hasn't been migrated to the per-entry memory
              layout yet. Run <code>happyranch memory reindex</code> from the
              CLI to upgrade.
            </p>
          ) : learningsError ? (
            <p className="text-tier-red text-xs">
              Failed to load learnings ({learningsError.status}).
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
              title="No learnings"
              body="This agent has not filed any learnings yet."
            />
          )}
        </section>

        {/* Recent jobs — object-ID click-through */}
        {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
          <section>
            <h3 className="text-overline text-text-muted mb-3 tracking-wider uppercase">
              Recent jobs
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
                <span>Save error: {saveError}</span>
              </div>
            )}
            {!saveError && (
              <p className="text-text-muted text-xs">
                You have unsaved changes. <kbd className="bg-surface border-border-default rounded border px-1.5 py-px font-mono text-xs">⌘S</kbd> to save.
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={onReset}>
              Reset
            </Button>
            <Button size="sm" onClick={onSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save agent'}
            </Button>
          </div>
        </footer>
      )}
    </section>
  );
}
