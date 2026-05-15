import type { ThreadDetailResponse } from '@/lib/api/types';

interface Props {
  thread: ThreadDetailResponse;
  onInvite: () => void;
  onArchive: () => void;
  onAbandon: () => void;
  onExtend: () => void;
}

const STATUS_BADGE: Record<string, string> = {
  open: 'bg-tier-green/10 text-tier-green border-tier-green/30',
  archiving: 'bg-tier-yellow/10 text-tier-yellow border-tier-yellow/30',
  archived: 'bg-fg-subtle/10 text-fg-subtle border-border',
  abandoned: 'bg-tier-red/10 text-tier-red border-tier-red/30',
};

export function ThreadHeader({
  thread,
  onInvite,
  onArchive,
  onAbandon,
  onExtend,
}: Props): JSX.Element {
  const open = thread.status === 'open';
  return (
    <header className="border-b border-border bg-bg-subtle px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-fg">{thread.subject}</h2>
            <span
              className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                STATUS_BADGE[thread.status] ?? STATUS_BADGE.open
              }`}
            >
              {thread.status}
            </span>
          </div>
          <div className="mt-1 text-xs text-fg-muted">
            <span className="font-mono">{thread.thread_id}</span>
            <span className="mx-2">·</span>
            <span>{thread.participants.join(', ') || 'no participants'}</span>
            <span className="mx-2">·</span>
            <span>
              {thread.turns_used}/{thread.turn_cap} turns
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <HeaderAction onClick={onInvite} disabled={!open} title="Invite (I)">
            Invite
          </HeaderAction>
          <HeaderAction onClick={onExtend} disabled={!open} title="Extend turn cap">
            Extend
          </HeaderAction>
          <HeaderAction onClick={onArchive} disabled={!open} title="Archive (A)">
            Archive
          </HeaderAction>
          <HeaderAction
            onClick={onAbandon}
            disabled={!open}
            danger
            title="Abandon (X)"
          >
            Abandon
          </HeaderAction>
        </div>
      </div>
      {thread.summary && (
        <p className="mt-2 rounded border border-border bg-bg-raised p-2 text-xs text-fg-muted">
          <strong className="text-fg">Archive summary:</strong> {thread.summary}
        </p>
      )}
    </header>
  );
}

function HeaderAction({
  children,
  onClick,
  disabled,
  danger,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  title?: string;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded px-2 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        danger
          ? 'text-tier-red hover:bg-tier-red/10'
          : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
      }`}
    >
      {children}
    </button>
  );
}
