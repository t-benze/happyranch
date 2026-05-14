import { NavLink } from 'react-router-dom';
import type { ThreadRecord } from '@/lib/api/types';

interface Props {
  slug: string;
  thread: ThreadRecord;
  active: boolean;
}

const STATUS_STYLE: Record<string, string> = {
  open: 'bg-tier-green/10 text-tier-green',
  archiving: 'bg-tier-yellow/10 text-tier-yellow',
  archived: 'bg-fg-subtle/10 text-fg-subtle',
  abandoned: 'bg-tier-red/10 text-tier-red',
};

export function InboxRow({ slug, thread, active }: Props): JSX.Element {
  const statusClass = STATUS_STYLE[thread.status] ?? STATUS_STYLE.open;
  return (
    <NavLink
      to={`/orgs/${slug}/threads/${thread.thread_id}`}
      className={`block rounded border px-3 py-2 transition ${
        active
          ? 'border-accent bg-bg-raised'
          : 'border-transparent hover:border-border hover:bg-bg-raised'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-sm font-medium text-fg">{thread.subject}</div>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${statusClass}`}>
          {thread.status}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between text-xs text-fg-muted">
        <span className="font-mono">{thread.thread_id}</span>
        <span>
          {thread.turns_used}/{thread.turn_cap} turns
        </span>
      </div>
    </NavLink>
  );
}
