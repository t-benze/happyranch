/**
 * ThreadHeader — the band above the message transcript. Per UI_SPEC §3.
 * Title (h2) + meta line (status pill + IdBadge + count) + actions.
 * This is a detail-pane header under the page's
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
 * THR-376 spec §2 (header): the title owns the row (22px display, clamped
 * to two lines, full subject always in the `title` tooltip); the status pill
 * moves to the meta line (pill → id → count → "Pinned"); actions are a 32px
 * Pin icon toggle (`aria-pressed`) and a 32px ⋯ menu (Rename, Pin/Unpin when
 * narrow, Copy thread ID, divider, Archive). When the HEADER container is
 * ≤ 520px wide the title drops to 19px and the Pin toggle moves into the
 * menu. The title size is a CSS container query; the pin placement is
 * measured on the header (the menu renders in a portal, outside the
 * container, so CSS alone cannot drive the menu item).
 *
 * Pure prop-driven. Pin/Rename/Archive are callbacks wired by the page;
 * Copy thread ID is client-only (navigator.clipboard). Any extra actions
 * (e.g. Resume on archived threads) are handed in via the `actions` slot.
 */
import type { KeyboardEvent, ReactNode } from 'react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/design-system/primitives/DropdownMenu';
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
  /** Meta-line marker shown when the thread is pinned. */
  pinned?: string;
  /** Pin icon toggle aria-label/tooltip (unpinned / pinned state). */
  pinThread?: string;
  unpinThread?: string;
  /** ⋯ menu items. */
  moreActions?: string;
  rename?: string;
  pin?: string;
  unpin?: string;
  copyId?: string;
  archive?: string;
}

/** Header container width (px) at or below which the narrow layout applies
 *  (spec §2 item 6). Keep in sync with the `@max-[521px]` title variant. */
const NARROW_MAX_PX = 520;

const PIN_PATH = 'M15 3l6 6-3 1-4 4 1 5-2 2-4-4-5 5-1-1 5-5-4-4 2-2 5 1 4-4z';

function PinIcon({ filled = false, className }: { filled?: boolean; className?: string }): JSX.Element {
  return filled ? (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
      <path d={PIN_PATH} />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={PIN_PATH} />
    </svg>
  );
}

const iconBtnCls =
  'text-text-secondary hover:bg-surface-sunken hover:border hover:border-border-default hover:text-text-primary focus-visible:outline-info aria-pressed:bg-accent-soft aria-pressed:text-accent-text grid size-8 shrink-0 cursor-pointer place-items-center rounded-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 disabled:cursor-default disabled:opacity-50';

const menuItemCls =
  'text-body-sm text-text-primary focus:bg-surface-sunken gap-2.25 px-2.5 py-1.75 [&_svg]:text-text-secondary [&_svg]:size-3.5 [&_svg]:shrink-0';

/** Content-box inline size of the header (what the CSS container query sees). */
function contentWidth(el: HTMLElement): number {
  const cs = getComputedStyle(el);
  return el.clientWidth - parseFloat(cs.paddingLeft || '0') - parseFloat(cs.paddingRight || '0');
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
  /* ---- THR-376 §2 header actions (all optional; menu renders when any
   *      of onRenameStart / onTogglePin / onArchive is passed) ---- */
  /** Current pinned state: drives the Pin toggle and the meta "Pinned". */
  pinned?: boolean;
  /** Called with the requested next pinned state. */
  onTogglePin?: (next: boolean) => void;
  pinPending?: boolean;
  onRenameStart?: () => void;
  onArchive?: () => void;
  /** Archive menu item disabled (e.g. thread not open). */
  archiveDisabled?: boolean;
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
  pinned = false,
  onTogglePin,
  pinPending = false,
  onRenameStart,
  onArchive,
  archiveDisabled = false,
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
    pinned: labels?.pinned ?? 'Pinned',
    pinThread: labels?.pinThread ?? 'Pin thread',
    unpinThread: labels?.unpinThread ?? 'Unpin thread',
    moreActions: labels?.moreActions ?? 'More actions',
    rename: labels?.rename ?? 'Rename',
    pin: labels?.pin ?? 'Pin',
    unpin: labels?.unpin ?? 'Unpin',
    copyId: labels?.copyId ?? 'Copy thread ID',
    archive: labels?.archive ?? 'Archive',
  };
  const statusPillCls =
    status === 'open'
      ? 'bg-accent-soft text-accent-text border-accent-soft'
      : 'bg-surface-sunken border-border-default text-text-muted';
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (renaming) inputRef.current?.focus();
  }, [renaming]);

  // Narrow = header container ≤ 520px (not the viewport), so it also holds
  // with the rail open on a small window.
  const headerRef = useRef<HTMLElement>(null);
  const [narrow, setNarrow] = useState(false);
  useLayoutEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const update = () => setNarrow(contentWidth(el) <= NARROW_MAX_PX);
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Rename hands focus to the title input: the open menu's focus trap
  // swallows the effect above, so focus the input as the menu closes
  // instead of returning focus to the ⋯ trigger.
  const skipMenuFocusReturn = useRef(false);

  const hasMenu = !!(onRenameStart || onTogglePin || onArchive);
  const pinLabel = pinned ? L.unpinThread : L.pinThread;

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
    <h2
      title={subject}
      className="font-display text-h1 text-text-primary m-0 line-clamp-2 min-w-0 leading-[1.25] font-medium tracking-[-0.3px] wrap-anywhere @max-[521px]:text-[1.1875rem]"
    >
      {subject}
    </h2>
  );

  return (
    <header ref={headerRef} className="border-border-default @container shrink-0 border-b px-3.5 pt-4 pb-3.5 sm:px-7">
      {/* Spec §2 back link: 12.5px (no type token at that size). */}
      {crumb && <div className="mb-2.5 text-[0.78125rem]">{crumb}</div>}
      {/* Spec §2 .th-row: the title owns the row; the actions stay beside it
          at every width (two 32px icon buttons, one when narrow). */}
      <div className="flex items-start gap-3.5">
        <div className="min-w-0 flex-1">
          {renaming ? (
            title
          ) : (
            <div className="flex min-w-0 items-start gap-2.5">
              {dreamOriginated && <CrescentMoonBadge label={labels?.dreamBadge} className="mt-1.5" />}
              {title}
            </div>
          )}
          <div className="text-text-secondary mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.78125rem]">
            <span
              className={`text-overline inline-flex shrink-0 items-center rounded-full border px-2.25 py-0.5 font-semibold ${statusPillCls}`}
            >
              {status === 'open' ? L.statusOpen : L.statusArchived}
            </span>
            <IdBadge id={threadId} kind="thread" />
            {messageCount !== null && (
              <>
                <span aria-hidden="true" className="text-text-muted">·</span>
                <span>{messageCount ?? (participantLabels ?? (participants.join(', ') || L.noParticipants))}</span>
              </>
            )}
            {pinned && (
              <span className="inline-flex items-center gap-2">
                <span aria-hidden="true" className="text-text-muted">·</span>
                <span className="inline-flex items-center gap-1">
                  <PinIcon filled className="size-3" />
                  {L.pinned}
                </span>
              </span>
            )}
          </div>
        </div>
        {(actions || hasMenu) && (
          <div className="-mt-px flex shrink-0 flex-wrap items-center justify-end gap-0.5">
            {actions}
            {onTogglePin && !narrow && (
              <button
                type="button"
                className={iconBtnCls}
                aria-pressed={pinned}
                aria-label={pinLabel}
                title={pinLabel}
                disabled={pinPending}
                onClick={() => onTogglePin(!pinned)}
              >
                <PinIcon className="size-4" />
              </button>
            )}
            {hasMenu && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button type="button" className={iconBtnCls} aria-label={L.moreActions} title={L.moreActions}>
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className="size-4">
                      <circle cx="5" cy="12" r="1.8" />
                      <circle cx="12" cy="12" r="1.8" />
                      <circle cx="19" cy="12" r="1.8" />
                    </svg>
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="border-border-strong min-w-44"
                  onCloseAutoFocus={(e) => {
                    if (skipMenuFocusReturn.current) {
                      skipMenuFocusReturn.current = false;
                      e.preventDefault();
                      inputRef.current?.focus();
                    }
                  }}
                >
                  {onRenameStart && (
                    <DropdownMenuItem
                      className={menuItemCls}
                      disabled={renaming}
                      onSelect={() => {
                        skipMenuFocusReturn.current = true;
                        onRenameStart();
                      }}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true">
                        <path d="M4 20h4L19 9l-4-4L4 16z" />
                        <path d="M13.5 6.5l4 4" />
                      </svg>
                      {L.rename}
                    </DropdownMenuItem>
                  )}
                  {onTogglePin && narrow && (
                    <DropdownMenuItem
                      className={menuItemCls}
                      disabled={pinPending}
                      onSelect={() => onTogglePin(!pinned)}
                    >
                      <PinIcon />
                      {pinned ? L.unpin : L.pin}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    className={menuItemCls}
                    onSelect={() => {
                      void navigator.clipboard?.writeText(threadId).catch(() => undefined);
                    }}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true">
                      <rect x="8" y="8" width="12" height="12" rx="2" />
                      <path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3" />
                    </svg>
                    {L.copyId}
                  </DropdownMenuItem>
                  {onArchive && (
                    <>
                      <DropdownMenuSeparator className="bg-border-default mx-0.5" />
                      <DropdownMenuItem className={menuItemCls} disabled={archiveDisabled} onSelect={onArchive}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true">
                          <path d="M3 5h18v4H3zM5 9v10h14V9M10 13h4" />
                        </svg>
                        {L.archive}
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
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
