/**
 * ThreadHeader — the band above the message transcript. Per UI_SPEC §3.
 * Title (h2) + meta line (IdBadge + participant list) + actions, with an
 * inline Pasture status pill. This is a detail-pane header under the page's
 * own <h1>, so it keeps an h2; top-level page titles use PageTitle.
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
 * THR-376 thread-detail (mockup .td-head): an optional `crumb` slot renders
 * the back link inside the header block; the title uses the mockup .h1 size
 * (22px display, weight 500); and when `messageCount` is passed the sub-line
 * reads `thread id · N messages` instead of the participant list (the
 * participants live in the page's rail). Passing `messageCount={null}` means
 * the count is still loading: the sub-line shows the thread id alone and
 * never falls back to raw participant ids.
 *
 * Pure prop-driven. Actions are handed in as a slot so the composition can
 * compose Button primitives with its own onClick wiring.
 */
import type { KeyboardEvent, ReactNode } from 'react';
import { useEffect, useRef } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from './CrescentMoonBadge';
import { IdBadge } from './IdBadge';

/**
 * Optional product-copy overrides (THR-118 W3a). Omitted fields keep the
 * historical English copy so other callers render unchanged.
 */
export interface ThreadHeaderLabels {
  statusOpen?: string;
  statusArchived?: string;
  noParticipants?: string;
  archiveSummary?: string;
  titleInput?: string;
  save?: string;
  cancel?: string;
  /** Accessible name of the dream-origin badge; omitted keeps English. */
  dreamBadge?: string;
}

interface ThreadHeaderProps {
  threadId: string;
  subject: string;
  status: 'open' | 'archived';
  participants: string[];
  participantLabels?: ReactNode;
  archiveSummary?: string | null;
  /** When true, renders a crescent-moon badge (dream-originated marker, A4). */
  dreamOriginated?: boolean;
  actions?: ReactNode;
  /** Back link rendered at the top of the header block (mockup .crumb). */
  crumb?: ReactNode;
  /** Localized "N messages" sub-line text; when set it replaces the
   *  participant list in the sub-line. `null` = count still loading: the
   *  sub-line shows only the thread id (no participant fallback). Omitted
   *  (undefined) keeps the participant list. */
  messageCount?: string | null;
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
  /** Optional localized product copy (display only; `status` stays machine). */
  labels?: ThreadHeaderLabels;
}

export function ThreadHeader({
  threadId,
  subject,
  status,
  participants,
  participantLabels,
  archiveSummary,
  dreamOriginated,
  actions,
  crumb,
  messageCount,
  renaming = false,
  renameDraft = '',
  onRenameDraftChange,
  onRenameSave,
  onRenameCancel,
  renameError,
  renameSaving = false,
  labels,
}: ThreadHeaderProps): JSX.Element {
  const L = {
    statusOpen: labels?.statusOpen ?? 'open',
    statusArchived: labels?.statusArchived ?? 'archived',
    noParticipants: labels?.noParticipants ?? 'no participants',
    archiveSummary: labels?.archiveSummary ?? 'Archive summary:',
    titleInput: labels?.titleInput ?? 'Thread title',
    save: labels?.save ?? 'Save',
    cancel: labels?.cancel ?? 'Cancel',
  };
  const statusPillCls =
    status === 'open'
      ? 'bg-accent-soft text-accent-text border-accent-soft'
      : 'bg-surface-sunken border-border-default text-text-muted';
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
        aria-label={L.titleInput}
        className="border-border-default bg-surface-raised text-body-sm text-text-primary min-w-0 flex-1 rounded-md border px-2 py-1"
      />
      <Button size="sm" onClick={onRenameSave} disabled={renameSaving || !renameDraft.trim()}>
        {L.save}
      </Button>
      <Button size="sm" variant="ghost" onClick={onRenameCancel} disabled={renameSaving}>
        {L.cancel}
      </Button>
      {renameError && (
        <span role="alert" className="text-feedback-danger text-xs">
          {renameError}
        </span>
      )}
    </div>
  ) : (
    <span className="inline-flex max-w-full items-center gap-2.5">
      {dreamOriginated && <CrescentMoonBadge label={labels?.dreamBadge} />}
      <span className="font-display text-h1 truncate font-medium tracking-tight">{subject}</span>
      <span
        className={`text-overline inline-flex shrink-0 items-center rounded-full border px-2.25 py-0.5 font-semibold ${statusPillCls}`}
      >
        {status === 'open' ? L.statusOpen : L.statusArchived}
      </span>
    </span>
  );

  return (
    <header className="border-border-default @container shrink-0 border-b px-3.5 pt-4 pb-3.5 sm:px-7">
      {crumb && <div className="mb-2.5">{crumb}</div>}
      {/* Actions stack under the title until the HEADER itself is >= 28rem
          wide (container query, not viewport): beside the 244px app rail and
          the 300px properties rail the conversation column can be ~224px at a
          768px viewport, where a viewport `sm:flex-row` squeezed the title to
          0px. Stacked, the title keeps the full column width (truncates). */}
      <div className="flex flex-col items-start gap-3 @md:flex-row @md:items-center @md:justify-between">
        <div className="w-full min-w-0 flex-1">
          <h2 className="text-text-primary truncate">{title}</h2>
          <div className="text-caption text-text-secondary mt-1 flex flex-wrap items-center gap-2">
            <IdBadge id={threadId} kind="thread" />
            {messageCount !== null && (
              <>
                <span aria-hidden="true" className="text-text-muted">·</span>
                <span>{messageCount ?? (participantLabels ?? (participants.join(', ') || L.noParticipants))}</span>
              </>
            )}
          </div>
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-1">{actions}</div>
        )}
      </div>
      {archiveSummary && (
        <p className="border-border-default bg-surface-raised text-caption text-text-muted mt-2 rounded-md border p-2">
          <strong className="text-text-primary">{L.archiveSummary}</strong>{' '}
          {archiveSummary}
        </p>
      )}
    </header>
  );
}
