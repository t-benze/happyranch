/**
 * AgentDetailPane — inline right detail/edit pane (replaces AgentDetailDrawer).
 *
 * Two-pane Agents surface: this is the detail/edit pane, rendered inline
 * alongside the roster list. The PRD calls for two-pane; the drawer was the
 * pre-reshape IA-shell pattern.
 *
 * Sections:
 *   1. Header — AgentChip, role + team + status dot.
 *   2. Editable fields — executor segmented, repo chips with add/remove.
 *      System prompt + description are READ-ONLY (gap: no founder-facing
 *      route for POST /agents/manage action=update — that route requires
 *      task_id+session_id agent auth). Honest-gap label rendered.
 *   3. Sticky save bar — appears when executor or repos are dirty.
 *      Save → real PUT /agents/{name}/executor + POST /agents/{name}/repos.
 *   4. Accountability metrics — DERIVE: real task counts, acceptance rate.
 *   5. Object-ID click-through — recent tasks, threads, jobs.
 *   6. Learnings — read-only list.
 *
 * States: Clean ⇄ Dirty (save bar hidden/shown); Saving → Saved / Error.
 * Renders a calm empty-state when no agent is selected.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Plus, X, AlertCircle } from 'lucide-react';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
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
      <header className="border-border-subtle flex items-start justify-between gap-3 border-b p-4">
        <div>
          <div className="flex items-center gap-3">
            <AgentChip name={agentName} role={agent?.role ?? 'worker'} />
          </div>
          <p className="text-fg-muted mt-1 text-xs">
            {agent ? (
              <>
                <span>team: {agent.team ?? '—'}</span>
                {agent.executor && <span> · executor: {agent.executor}</span>}
              </>
            ) : agentsQuery.isLoading ? (
              'Loading…'
            ) : (
              'Agent not found'
            )}
          </p>
          {agent?.description && (
            <p className="text-fg mt-2 text-sm">{agent.description}</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <X size={16} />
        </Button>
      </header>

      {/* --- Editable fields --- */}
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {/* Executor switch */}
        <div>
          <label className="text-fg-muted mb-1 block text-xs font-medium tracking-wider uppercase">
            Executor
          </label>
          <Select value={displayExecutor} onValueChange={onExecutorChange}>
            <SelectTrigger>
              <SelectValue placeholder="Select executor…" />
            </SelectTrigger>
            <SelectContent>
              {EXECUTOR_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-fg-muted mt-1 text-xs">
            Edits take effect on this agent's next task.
          </p>
        </div>

        {/* System prompt — READ-ONLY with gap notice */}
        {agent?.system_prompt && (
          <div>
            <button
              type="button"
              onClick={() => setShowPrompt(!showPrompt)}
              className="text-fg-muted hover:text-fg flex w-full items-center gap-1 text-xs font-medium tracking-wider uppercase transition-colors"
            >
              {showPrompt ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              System prompt
            </button>
            {showPrompt && (
              <>
                <pre className="bg-bg-raised border-border mt-2 max-h-48 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap">
                  {agent.system_prompt}
                </pre>
                <div className="text-fg-muted mt-1 flex items-center gap-1 text-xs">
                  <AlertCircle size={12} />
                  <span>
                    Read-only. Updating system prompt from the web UI requires a
                    founder-facing PUT route (the daemon's POST /agents/manage
                    action=update needs task_id+session_id agent auth).
                  </span>
                </div>
              </>
            )}
          </div>
        )}

        {/* Description — READ-ONLY */}
        {agent?.description && (
          <div>
            <span className="text-fg-muted mb-1 block text-xs font-medium tracking-wider uppercase">
              Description
            </span>
            <p className="text-fg text-sm">{agent.description}</p>
            <div className="text-fg-muted mt-1 flex items-center gap-1 text-xs">
              <AlertCircle size={12} />
              <span>
                Read-only. Same gap as system prompt — no founder-facing update route
                for agent description.
              </span>
            </div>
          </div>
        )}

        {/* Repo chips */}
        <div>
          <label className="text-fg-muted mb-1 block text-xs font-medium tracking-wider uppercase">
            Repositories
          </label>
          <div className="mb-2 flex flex-wrap gap-1">
            {Object.entries(displayRepos).map(([key, url]) => (
              <span
                key={key}
                className="bg-bg-raised border-border text-fg-muted group inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs"
              >
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-accent transition-colors"
                >
                  {key}
                </a>
                <button
                  type="button"
                  onClick={() => onRepoRemove(key)}
                  className="text-fg-muted hover:text-tier-red ml-1 transition-colors"
                  aria-label={`Remove ${key}`}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {Object.keys(displayRepos).length === 0 && (
              <span className="text-fg-muted text-xs">None</span>
            )}
          </div>
          {showRepoAdd ? (
            <div className="bg-surface-raised border-border space-y-2 rounded-md border p-2">
              <input
                className="border-border-subtle bg-bg-subtle w-full rounded border px-2 py-1 text-xs"
                placeholder="Repo name (e.g. happyranch)"
                value={repoAddName}
                onChange={(e) => setRepoAddName(e.target.value)}
              />
              <input
                className="border-border-subtle bg-bg-subtle w-full rounded border px-2 py-1 text-xs"
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
        </div>

        {/* Accountability metrics */}
        <div className="border-border-subtle rounded-lg border p-3">
          <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Accountability
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-fg-muted text-xs">Loading…</p>
          ) : tasksQuery.isError ? (
            <p className="text-tier-red text-xs">
              Failed to load task counts.
            </p>
          ) : (
            <div className="text-sm">
              <span className="text-fg">
                {done} done · {total} total tasks
              </span>
            </div>
          )}
        </div>

        {/* Recent tasks */}
        <div>
          <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Recent tasks
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-fg-muted text-xs">Loading tasks…</p>
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
            <p className="text-fg-muted text-xs">
              No tasks where this agent was the assigned manager.
            </p>
          )}
        </div>

        {/* Learnings */}
        <div>
          <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Learnings
          </h3>
          {learningsQuery.isLoading ? (
            <p className="text-fg-muted text-xs">Loading learnings…</p>
          ) : learningsError?.status === 412 ? (
            <p className="text-fg-muted text-xs">
              This workspace hasn't been migrated to the per-entry learnings
              layout yet. Run <code>happyranch learning reindex</code> from the
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
                  className="border-border-subtle bg-surface-raised rounded-md border p-2"
                >
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-fg-muted font-mono">{e.id}</span>
                    <span className="text-fg-muted">·</span>
                    <span className="text-fg-muted">{e.topic}</span>
                  </div>
                  <p className="text-fg mt-1 text-sm">{e.title}</p>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No learnings"
              body="This agent has not filed any learnings yet."
            />
          )}
        </div>

        {/* Recent jobs — object-ID click-through */}
        {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
          <div>
            <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
              Recent jobs
            </h3>
            <ul className="space-y-1 text-sm">
              {jobsQuery.data.jobs.map((j) => (
                <li key={j.id}>
                  {slug ? (
                    <Link
                      to={`/orgs/${slug}/jobs/${j.id}`}
                      className="text-accent font-mono hover:underline"
                    >
                      {j.id}
                    </Link>
                  ) : (
                    <span className="font-mono">{j.id}</span>
                  )}
                  {' — '}
                  {j.title}{' '}
                  <span className="text-fg-muted">({j.status})</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* --- Sticky save bar --- */}
      {isDirty && (
        <footer className="border-border-subtle bg-surface-sunken flex items-center justify-between gap-3 border-t p-3">
          <div className="flex items-center gap-2">
            {saveError && (
              <p className="text-tier-red text-xs">Save error: {saveError}</p>
            )}
            {!saveError && (
              <p className="text-fg-muted text-xs">
                You have unsaved changes. ⌘S to save.
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
