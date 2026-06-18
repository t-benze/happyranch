/**
 * AssistantSection — System Assistant config in the Settings page.
 *
 * Read-only status display + init/repair actions + link to terminal.
 * Mirrors the existing AssistantSection from SettingsDialog with the page context.
 */
import { Link, useParams } from 'react-router-dom';
import {
  useAssistantStatus,
  useInitAssistant,
  useRepairAssistant,
} from '@/hooks/assistant';
import type { AssistantState } from '@/lib/api/types';

const STATE_LABEL: Record<AssistantState, string> = {
  uninitialized: 'Uninitialized',
  configured: 'Configured',
  stale_or_broken: 'Stale or broken',
};

const STATE_BADGE: Record<AssistantState, string> = {
  uninitialized: 'bg-bg-raised text-fg-muted',
  configured: 'bg-feedback-success/15 text-feedback-success',
  stale_or_broken: 'bg-feedback-danger/15 text-feedback-danger',
};

export function AssistantSection(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const assistantHref = slug ? `/orgs/${slug}/assistant` : '#';
  const statusQuery = useAssistantStatus();
  const initMutation = useInitAssistant();
  const repairMutation = useRepairAssistant();
  const status = statusQuery.data;

  return (
    <section>
      {statusQuery.isLoading ? (
        <p className="text-fg-muted text-sm">Loading…</p>
      ) : statusQuery.isError || !status ? (
        <p className="text-tier-red text-sm">Could not load assistant status.</p>
      ) : (
        <div className="border-border bg-bg-subtle space-y-3 rounded-md border p-4">
          {/* Status badge + executor/workspace */}
          <div className="flex items-center gap-2">
            <span className="text-fg-muted text-sm">State</span>
            <span
              className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_BADGE[status.state]}`}
            >
              {STATE_LABEL[status.state]}
            </span>
          </div>
          <dl className="flex flex-col gap-1 text-sm">
            <div className="flex gap-4">
              <dt className="text-fg-muted w-24 shrink-0">Executor</dt>
              <dd className="text-fg break-all">
                {status.selected_executor ?? '—'}
              </dd>
            </div>
            <div className="flex gap-4">
              <dt className="text-fg-muted w-24 shrink-0">Workspace</dt>
              <dd className="text-fg break-all">
                {status.workspace_path ?? '—'}
              </dd>
            </div>
          </dl>

          {/* Detail / error text */}
          {status.state === 'stale_or_broken' && status.detail && (
            <p className="text-feedback-danger text-sm">{status.detail}</p>
          )}

          {/* Actions */}
          <div className="flex flex-wrap items-center gap-3">
            {status.state === 'uninitialized' && (
              <>
                <button
                  type="button"
                  onClick={() => initMutation.mutateAsync({ reconfigure: false })}
                  disabled={initMutation.isPending}
                  className="bg-accent text-accent-fg hover:bg-accent-hover rounded px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {initMutation.isPending ? 'Initializing…' : 'Initialize workspace'}
                </button>
                <Link
                  to={assistantHref}
                  className="text-accent text-sm hover:underline"
                >
                  Register executor →
                </Link>
              </>
            )}
            {status.state === 'stale_or_broken' && (
              <button
                type="button"
                onClick={() => repairMutation.mutateAsync()}
                disabled={repairMutation.isPending}
                className="bg-accent text-accent-fg hover:bg-accent-hover rounded px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50"
              >
                {repairMutation.isPending ? 'Repairing…' : 'Repair'}
              </button>
            )}
            {status.state === 'configured' && (
              <Link
                to={assistantHref}
                className="text-accent text-sm hover:underline"
                aria-label="Open terminal"
              >
                Open terminal →
              </Link>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
