import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useOrgSlug } from '@/lib/orgSlug';
import { InboxRow } from './InboxRow';
import { useThreadsList } from './hooks';

const STATUS_TABS = ['open', 'archived', 'abandoned'] as const;
type StatusTab = (typeof STATUS_TABS)[number];

interface Props {
  onCompose: () => void;
}

export function InboxList({ onCompose }: Props): JSX.Element {
  const slug = useOrgSlug();
  const { thread_id: activeId } = useParams<{ thread_id: string }>();
  const [status, setStatus] = useState<StatusTab>('open');
  const [filter, setFilter] = useState('');

  const query = useThreadsList(slug, { status });
  const threads = useMemo(() => {
    const all = query.data?.threads ?? [];
    if (!filter.trim()) return all;
    const needle = filter.toLowerCase();
    return all.filter(
      (t) =>
        t.subject.toLowerCase().includes(needle) ||
        t.thread_id.toLowerCase().includes(needle),
    );
  }, [query.data, filter]);

  return (
    <aside className="flex h-full flex-col border-r border-border bg-bg-subtle">
      <header className="border-b border-border px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-xs uppercase tracking-wide text-fg-muted">Inbox</h2>
          <button
            type="button"
            onClick={onCompose}
            className="rounded bg-accent px-2 py-0.5 text-xs font-medium text-white hover:bg-accent-hover"
            aria-label="New thread"
            title="New thread (N)"
          >
            + New
          </button>
        </div>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="mt-2 w-full rounded border border-border bg-bg-raised px-2 py-1 text-xs text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none"
          aria-label="Filter threads"
        />
        <div className="mt-2 flex gap-1">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              className={`rounded px-2 py-0.5 text-xs ${
                status === s
                  ? 'bg-bg-raised text-fg'
                  : 'text-fg-muted hover:text-fg'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </header>
      <div className="flex-1 overflow-auto p-2">
        {query.isLoading && (
          <p className="px-2 py-4 text-xs text-fg-muted">Loading…</p>
        )}
        {query.isError && (
          <p className="px-2 py-4 text-xs text-tier-red">Failed to load threads.</p>
        )}
        {!query.isLoading && threads.length === 0 && (
          <p className="px-2 py-4 text-xs text-fg-muted">
            {filter
              ? 'No threads match the filter.'
              : 'No threads yet. Press N to compose.'}
          </p>
        )}
        <div className="flex flex-col gap-1">
          {threads.map((t) => (
            <InboxRow
              key={t.thread_id}
              slug={slug}
              thread={t}
              active={t.thread_id === activeId}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}
