/**
 * ThreadHeader — the band above the message transcript. Per UI_SPEC §3.
 * Composes PageHeader + inline Pasture status pill + IdBadge + participant
 * list + actions.
 *
 * Direction-A Pasture: subject title uses --font-display (Newsreader serif),
 * status pill matches ds.css .tag pattern.
 *
 * THR-209 rename: when the page passes the rename props, the header renders
 * an inline rename affordance — a "Rename" action plus, while `renaming`, a
 * prefilled title input with Save/Cancel, Enter-to-save, Escape-to-cancel,
 * and an inline error slot (the typed value is retained on failure by the
 * caller keeping `renaming` true). The header stays presentational: edit
 * state lives in the page so the overflow-menu Rename item shares it.
 *
 * Pure prop-driven. Actions are handed in as a slot so the composition can
 * compose Button primitives with its own onClick wiring.
 */
import type { KeyboardEvent, ReactNode } from 'react';
import { useEffect, useRef } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from './CrescentMoonBadge';
import { IdBadge } from './IdBadge';
import { PageHeader } from './PageHeader';

interface ThreadHeaderProps {
  threadId: string;
  subject: string;
  status: 'open' | 'archived';
  participants: string[];
  archiveSummary?: string | null;
  /** When true, renders a crescent-moon badge (dream-originated marker, A4). */
  dreamOriginated?: boolean;
  actions?: ReactNode;
  /* ---- THR-209 inline rename (controlled by the page) ---- */
  /** When true the subject renders as a prefilled input with Save/Cancel. */
  renaming?: boolean;
  renameDraft?: string;
  onRenameDraftChange?: (value: string) => void;
  onRenameSave?: () => void;
  onRenameCancel?: () => void;
  /** Inline error shown while the last rename save failed; the caller keeps
   *  `renaming` true so the typed value stays for retry. */
  renameError?: string | null;
  renameSaving?: boolean;
}

export function ThreadHeader({
  threadId,
  subject,
  status,
  participants,
  archiveSummary,
  dreamOriginated,
  actions,
  renaming = false,
  renameDraft = '',
  onRenameDraftChange,
  onRenameSave,
  onRenameCancel,
  renameError,
  renameSaving = false,
}: ThreadHeaderProps): JSX.Element {
  const statusPillCls =
    status === 'open'
      ? 'bg-accent-soft text-accent-text'
      : 'bg-surface-sunken border border-border-default text-text-muted';
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (renaming) inputRef.current?.focus();
  }, [renaming]);

  const title = renaming ? (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <input
        ref={inputRef}
        type="text"
        value={renameDraft}
        onChange={(e) => onRenameDraftChange?.(e.target.value)}
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onRenameSave?.();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            onRenameCancel?.();
          }
        }}
        maxLength={120}
        aria-label="Thread title"
        className="border-border-default bg-surface text-body-sm text-text-primary min-w-0 flex-1 rounded-md border px-2 py-1"
      />
      <Button size="sm" onClick={onRenameSave} disabled={renameSaving || !renameDraft.trim()}>
        Save
      </Button>
      <Button size="sm" variant="ghost" onClick={onRenameCancel} disabled={renameSaving}>
        Cancel
      </Button>
      {renameError && (
        <span role="alert" className="text-feedback-danger text-xs">
          {renameError}
        </span>
      )}
    </div>
  ) : (
    <span className="inline-flex items-center gap-2">
      {dreamOriginated && <CrescentMoonBadge />}
      <span className="font-display truncate font-medium tracking-tight">{subject}</span>
      <span
        className={`inline-flex items-center rounded-full px-2 py-px text-xs leading-relaxed font-semibold ${statusPillCls}`}
      >
        {status === 'open' ? 'active' : 'archived'}
      </span>
    </span>
  );

  return (
    <header className="border-border-default bg-surface-sunken border-b px-4 py-2">
      <PageHeader
        title={title}
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <IdBadge id={threadId} kind="thread" />
            <span aria-hidden="true">·</span>
            <span>{participants.join(', ') || 'no participants'}</span>
          </div>
        }
        actions={actions}
      />
      {archiveSummary && (
        <p className="border-border-default bg-surface-raised text-caption text-text-muted mt-2 rounded-md border p-2">
          <strong className="text-text-primary">Archive summary:</strong>{' '}
          {archiveSummary}
        </p>
      )}
    </header>
  );
}
