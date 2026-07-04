/**
 * ConversationSwitcher — the conversation list surfaced inside the assistant
 * dock (THR-056 STEP-B).
 *
 * N conversations live UNDER the one runtime-global assistant (single-assistant
 * framing). This panel lists them newest-first, indicates the active one, and
 * offers new / switch / rename / delete — modelled on the Threads inbox row
 * idiom (same surface/border/text tokens, `bg-accent-soft` for the active row).
 *
 * It is presentational: every side effect is a prop callback owned by
 * AssistantDockHost, which drives the mutations and reconnects the A-mode WS so
 * the transcript replays the resulting active conversation's history.
 */
import { useState } from 'react';
import { Check, Pencil, Plus, Trash2, X } from 'lucide-react';
import type { ConversationSummary } from '@/hooks/assistant';

/**
 * Order conversations newest-first. The backend already returns this order;
 * we sort defensively (by `created_at` descending) so the dock never depends
 * on wire order. Nulls sort last; ties preserve incoming order (stable).
 */
export function sortConversationsNewestFirst(
  conversations: ConversationSummary[],
): ConversationSummary[] {
  return conversations
    .map((c, i) => ({ c, i }))
    .sort((a, b) => {
      const ta = a.c.created_at ? Date.parse(a.c.created_at) : Number.NEGATIVE_INFINITY;
      const tb = b.c.created_at ? Date.parse(b.c.created_at) : Number.NEGATIVE_INFINITY;
      if (tb !== ta) return tb - ta;
      return a.i - b.i;
    })
    .map(({ c }) => c);
}

interface ConversationSwitcherProps {
  conversations: ConversationSummary[];
  loading: boolean;
  error: string | null;
  busy: boolean;
  onNew: () => void;
  onSwitch: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

export function ConversationSwitcher({
  conversations,
  loading,
  error,
  busy,
  onNew,
  onSwitch,
  onRename,
  onDelete,
  onClose,
}: ConversationSwitcherProps): JSX.Element {
  const ordered = sortConversationsNewestFirst(conversations);

  return (
    <div
      className="border-border-default bg-surface-raised absolute inset-0 z-10 flex flex-col"
      role="region"
      aria-label="Conversations"
    >
      <div className="border-border-default flex shrink-0 items-center justify-between border-b px-4 py-3">
        <span className="text-text-primary font-display text-sm">Conversations</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close conversations"
          className="text-text-secondary hover:text-text-primary hover:bg-surface-hover inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="shrink-0 px-3 pt-3">
        <button
          type="button"
          onClick={onNew}
          disabled={busy}
          className="border-border-default text-text-secondary hover:text-text-primary hover:bg-surface-hover flex w-full items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Plus size={16} aria-hidden="true" />
          <span>New conversation</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-text-muted px-1 py-2 text-sm">Loading conversations…</p>
        ) : error ? (
          <p role="alert" className="text-feedback-danger px-1 py-2 text-sm">
            {error}
          </p>
        ) : ordered.length === 0 ? (
          <p className="text-text-muted px-1 py-2 text-sm">No conversations yet.</p>
        ) : (
          <ul className="flex flex-col gap-1" aria-label="Conversation list">
            {ordered.map((conv) => (
              <ConversationRow
                key={conv.id}
                conv={conv}
                busy={busy}
                onSwitch={onSwitch}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ConversationRow({
  conv,
  busy,
  onSwitch,
  onRename,
  onDelete,
}: {
  conv: ConversationSummary;
  busy: boolean;
  onSwitch: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}): JSX.Element {
  const [mode, setMode] = useState<'idle' | 'rename' | 'confirm-delete'>('idle');
  const [draft, setDraft] = useState(conv.title);

  const commitRename = () => {
    const next = draft.trim();
    if (next && next !== conv.title) onRename(conv.id, next);
    setMode('idle');
  };

  if (mode === 'rename') {
    return (
      <li>
        <div className="border-border-default bg-surface-sunken flex items-center gap-1 rounded-lg border p-1">
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitRename();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setDraft(conv.title);
                setMode('idle');
              }
            }}
            aria-label="Conversation title"
            className="text-text-primary min-w-0 flex-1 bg-transparent px-2 py-1 text-sm focus:outline-none"
          />
          <button
            type="button"
            onClick={commitRename}
            disabled={busy}
            aria-label="Save title"
            className="text-feedback-success hover:bg-surface-hover inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors disabled:opacity-40"
          >
            <Check size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => {
              setDraft(conv.title);
              setMode('idle');
            }}
            aria-label="Cancel rename"
            className="text-text-secondary hover:bg-surface-hover inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors"
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>
      </li>
    );
  }

  if (mode === 'confirm-delete') {
    return (
      <li>
        <div className="border-border-default bg-surface-sunken flex items-center gap-2 rounded-lg border px-3 py-2">
          <span className="text-text-secondary min-w-0 flex-1 truncate text-sm">
            Delete “{conv.title}”?
          </span>
          <button
            type="button"
            onClick={() => onDelete(conv.id)}
            disabled={busy}
            className="text-feedback-danger hover:bg-surface-hover rounded-md px-2 py-1 text-xs font-medium transition-colors disabled:opacity-40"
          >
            Delete
          </button>
          <button
            type="button"
            onClick={() => setMode('idle')}
            className="text-text-secondary hover:bg-surface-hover rounded-md px-2 py-1 text-xs transition-colors"
          >
            Cancel
          </button>
        </div>
      </li>
    );
  }

  return (
    <li>
      <div
        className={[
          'group flex items-center gap-1 rounded-lg px-1 transition-colors',
          conv.active ? 'bg-accent-soft' : 'hover:bg-surface-hover',
        ].join(' ')}
      >
        <button
          type="button"
          onClick={() => onSwitch(conv.id)}
          disabled={busy}
          aria-current={conv.active ? 'true' : undefined}
          className={[
            'min-w-0 flex-1 truncate px-2 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed',
            conv.active ? 'text-accent-text font-medium' : 'text-text-primary',
          ].join(' ')}
        >
          {conv.title}
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(conv.title);
            setMode('rename');
          }}
          aria-label={`Rename ${conv.title}`}
          className="text-text-muted hover:text-text-primary hover:bg-surface-hover inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors"
        >
          <Pencil size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setMode('confirm-delete')}
          aria-label={`Delete ${conv.title}`}
          className="text-text-muted hover:text-feedback-danger hover:bg-surface-hover inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors"
        >
          <Trash2 size={14} aria-hidden="true" />
        </button>
      </div>
    </li>
  );
}
