/**
 * Composer — THR-376 thread-detail `.composer-box`: a 12px-radius (`rounded-md`)
 * input box with a 30px inline attach button, an auto-growing textarea, an
 * optional "Stop" button and a 32px SQUARE primary send button. Focus anywhere
 * in the box draws the accent border plus a 3px accent-soft ring. Used at the
 * foot of the threads detail pane.
 *
 * The broadcast copy lives in the PLACEHOLDER ("Message the thread · @ to
 * address an agent"); an optional `hint` line ("Every participant sees
 * messages in this thread.") sits under the box and hides when the composer
 * itself is narrower than the `@md` container size. A send error surfaces
 * below the box. The attach affordance keeps its "Attach files" label.
 *
 * Draft persistence: useThreadDraft stores partial messages in localStorage
 * keyed by (orgSlug, threadId) with a 300ms debounce. Drafts survive
 * navigation and are cleared on successful send.
 *
 * Auto-grow, @-mention autocomplete, Enter-to-send (Shift+Enter for new
 * line) live in MentionTextarea so the same typing experience is reused
 * by other surfaces (e.g. NewThreadDialog). The optional abort-reply control
 * sits INSIDE the input box next to the send button (THR-099 Phase A, founder
 * seq57): a 30px outlined danger button with a filled stop-square icon and
 * visible "Stop" text (THR-376 `.stop-btn`). It renders only while replies
 * are in flight and is owned entirely by props (the parent holds the
 * thread-level abort mutation).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, Paperclip, Square, X } from 'lucide-react';
import {
  MAX_THREAD_ATTACHMENTS,
  REMOVE_ATTACHMENT_LABEL,
  createSelectionIdFactory,
} from '@/lib/threadAttachments';
import { MentionTextarea } from './MentionTextarea';
import type { AddressOption } from './MentionAutocomplete';

const DRAFT_CAP_CHARS = 65_536;
const DRAFT_DEBOUNCE_MS = 300;

interface DraftHandle {
  draft: string;
  setDraft: (next: string) => void;
  clearDraft: () => void;
}

/**
 * Optional product-copy overrides (THR-118 W3a). Every field defaults to the
 * historical English copy, so callers that omit `labels` render unchanged.
 * The pattern stays hook-free: the owning feature resolves translations.
 */
export interface ComposerLabels {
  /** Placeholder shown while the composer is disabled (thread closed). */
  closedPlaceholder?: string;
  /** Default placeholder when neither `placeholder` nor `helper` is given. */
  defaultPlaceholder?: string;
  attachFiles?: string;
  textareaAria?: string;
  send?: string;
  sendTitle?: string;
  /** Visible text on the abort-replies button (accessible name). */
  stop?: string;
  /** Tooltip on the abort-replies button. */
  abortReply?: string;
  aborting?: string;
  removeAttachment?: string;
  /** Label for the @-mention suggestion list. */
  mentionList?: string;
}

export interface PendingAttachment {
  id: string;
  file: File;
}

function useThreadDraft(orgSlug: string, threadId: string): DraftHandle {
  const key = `happyranch:draft:${orgSlug}:${threadId}`;
  const [draft, setDraftState] = useState<string>(() => {
    try { return localStorage.getItem(key) ?? ''; } catch { return ''; }
  });
  const timer = useRef<number | null>(null);

  // Re-read when key changes (org/thread switch).
  useEffect(() => {
    try { setDraftState(localStorage.getItem(key) ?? ''); } catch { setDraftState(''); }
  }, [key]);

  // Cancel any pending debounce write on unmount.
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  const setDraft = useCallback((next: string) => {
    setDraftState(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      try {
        if (next.length > DRAFT_CAP_CHARS) { console.debug('draft cap exceeded; skipping persist'); return; }
        if (next === '') localStorage.removeItem(key);
        else localStorage.setItem(key, next);
      } catch (e) {
        console.debug('draft persist failed', e);
      }
    }, DRAFT_DEBOUNCE_MS);
  }, [key]);

  const clearDraft = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    try { localStorage.removeItem(key); } catch { /* ignore */ }
    setDraftState('');
  }, [key]);

  return { draft, setDraft, clearDraft };
}

interface ComposerProps {
  disabled?: boolean;
  pending?: boolean;
  /**
   * Optional error message (typically from a failed send). Rendered whenever it
   * is non-null/undefined — including the empty string (a raw diagnostic is
   * shown byte-for-byte). Pass `null`/omit to render no error slot.
   */
  errorMessage?: string | null;
  /**
   * Broadcast copy used as the placeholder when no explicit `placeholder` is
   * given (the historical name; it is not rendered as a separate line).
   */
  helper?: string;
  /**
   * Optional one-line hint rendered under the input box (THR-376
   * `.composer-hint`); hidden when the composer is narrower than `@md`.
   */
  hint?: string;
  /** Placeholder for the textarea. Defaults to a reasonable copy. */
  placeholder?: string;
  /**
   * Called with the markdown when the user presses Send or Enter (Shift+Enter
   * inserts a newline instead). May return a Promise; if it rejects (or a sync
   * impl throws), the draft is preserved so the user can retry without retyping.
   *
   * Broadcast model: the send is always delivered to all thread participants;
   * no per-message addressing is needed.
   */
  onSend: (markdown: string, attachments: PendingAttachment[]) => unknown | Promise<unknown>;
  attachments?: PendingAttachment[];
  onAttachmentsChange?: (attachments: PendingAttachment[]) => void;

  // Required
  agents: AddressOption[];
  threadId: string;
  /**
   * Active org slug — keys the localStorage draft alongside threadId. Passed
   * down from the rendering feature (patterns stay pure props-in/JSX-out and
   * must not call @/lib/orgSlug directly).
   */
  orgSlug: string;
  /**
   * Optional abort-reply trailing action rendered INSIDE the input box, to the
   * left of the send button (THR-099 Phase A) — a 30px outlined danger button
   * with a stop-square icon and visible text. It renders ONLY while `active`
   * (replies in flight); it is disabled and reads "Aborting…" while
   * `isPending`, otherwise "Stop" (tooltip "Abort reply").
   * `onAbort` aborts EVERY in-flight reply (the parent owns the thread-level
   * mutation — the pattern stays pure). Other Composer surfaces (NewThreadDialog,
   * the assistant dock) omit this prop.
   */
  abortReplies?: { active: boolean; isPending: boolean; onAbort: () => void };
  /** Optional localized product copy; omitted fields keep the English defaults. */
  labels?: ComposerLabels;
}

export function Composer({
  disabled,
  pending,
  errorMessage,
  helper,
  hint,
  placeholder,
  onSend,
  attachments = [],
  onAttachmentsChange,
  agents = [],
  threadId = '',
  orgSlug,
  abortReplies,
  labels,
}: ComposerProps): JSX.Element {
  const L = {
    closedPlaceholder: labels?.closedPlaceholder ?? 'Thread is closed.',
    defaultPlaceholder: labels?.defaultPlaceholder ?? 'Write a message…',
    attachFiles: labels?.attachFiles ?? 'Attach files',
    textareaAria: labels?.textareaAria ?? 'Compose follow-up',
    send: labels?.send ?? 'Send',
    sendTitle: labels?.sendTitle ?? 'Send (Enter)',
    stop: labels?.stop ?? 'Stop',
    abortReply: labels?.abortReply ?? 'Abort reply',
    aborting: labels?.aborting ?? 'Aborting…',
    removeAttachment: labels?.removeAttachment ?? REMOVE_ATTACHMENT_LABEL,
  };
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const canSend = Boolean(draft.trim() || attachments.length);

  // Synchronous in-flight latch — blocks a second same-tick submit (double
  // click, Enter+Send race) before the async `onSend` can set a re-render
  // driven `pending` prop. Mirrors NewThreadDialog's submittingRef.
  const submittingRef = useRef(false);
  // The destination generation that currently owns the latch (null = none).
  const submittingGenRef = useRef<number | null>(null);
  // Monotonic destination generation. It advances on EVERY thread/org change —
  // including A -> B -> A — so a submission started against a departed view can
  // never look current again merely because the destination string repeats.
  const destGenRef = useRef(0);
  const threadKey = `${orgSlug}:${threadId}`;
  const threadKeyRef = useRef(threadKey);
  if (threadKeyRef.current !== threadKey) {
    threadKeyRef.current = threadKey;
    destGenRef.current += 1;
    // A submission from the previous destination must not block this view's
    // first submit while it is still in flight. Its own `finally` is keyed to
    // its captured generation, so it cannot clear this view's newer latch.
    submittingRef.current = false;
    submittingGenRef.current = null;
  }
  // Stable, non-metadata chip identity (two identical Files stay distinct).
  const selectionIdFactory = useRef<(() => string) | null>(null);
  if (selectionIdFactory.current === null) {
    selectionIdFactory.current = createSelectionIdFactory();
  }
  const nextSelectionId = selectionIdFactory.current;

  // A full unmount is also a destination departure: invalidate any in-flight
  // submission's generation so its late success cannot clear a draft the user
  // retyped after remounting the same thread/org.
  useEffect(() => () => { destGenRef.current += 1; }, []);

  const removeAttachment = (id: string) => {
    onAttachmentsChange?.(attachments.filter((item) => item.id !== id));
  };

  const submit = async () => {
    if (!canSend || disabled || pending || submittingRef.current) return;
    submittingRef.current = true;
    const submitGen = destGenRef.current;
    submittingGenRef.current = submitGen;
    try {
      await onSend(draft, attachments);
      if (destGenRef.current === submitGen) {
        clearDraft();
        onAttachmentsChange?.([]);
      }
    } catch {
      // Composition surfaces via errorMessage; draft is preserved for retry.
    } finally {
      if (submittingGenRef.current === submitGen) {
        submittingRef.current = false;
        submittingGenRef.current = null;
      }
    }
  };

  // Broadcast copy rides in the placeholder (a-thread-detail): the helper prop
  // is used as the placeholder when the caller gave no explicit one, so the
  // compact input carries the broadcast semantics without a separate line.
  const composerPlaceholder =
    placeholder ??
    (disabled ? L.closedPlaceholder : (helper ?? L.defaultPlaceholder));

  return (
    // `@container` lets the hint line respond to the composer's own width (the
    // detail column can be narrow at md while both side rails are visible).
    <div className="@container flex flex-col gap-2">
      {/* Pending attachment chips — above the box so the input stays compact.
          The mockup's .attach-row has no chip styling of its own, so the
          existing token chip is kept. */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {attachments.map((item) => (
            <span
              key={item.id}
              className="border-border-subtle bg-surface-raised text-caption inline-flex max-w-full items-center gap-2 rounded-md border px-2 py-1"
            >
              <span className="max-w-64 truncate">{item.file.name}</span>
              <button
                type="button"
                className="text-text-muted hover:text-text-primary"
                aria-label={L.removeAttachment}
                onClick={() => removeAttachment(item.id)}
                disabled={disabled || pending}
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* THR-376 .composer-box: border-2 (border-strong), --radius (rounded-md
          12px), padding 9/10/9/6, gap 8; focus-within → accent border + 3px
          accent-soft ring. Controls bottom-align so a Shift+Enter multi-line
          draft grows upward while attach/stop/send stay on the last line.
          Below the `@2xs` container size (the md column with both rails is
          ~200px wide) the textarea takes its own full-width first row and the
          controls wrap to a row beneath it, so the input never collapses to a
          few characters wide. */}
      <div className="border-border-strong bg-surface-raised focus-within:border-accent-default focus-within:ring-accent-soft flex flex-wrap items-end gap-2 rounded-md border py-2.25 pr-2.5 pl-1.5 transition-colors focus-within:ring-3">
        <label
          className="text-text-secondary hover:text-text-primary hover:bg-surface-hover mb-0.25 inline-flex h-7.5 w-7.5 shrink-0 cursor-pointer items-center justify-center rounded-sm transition-colors"
          title={L.attachFiles}
        >
          <Paperclip className="h-4.5 w-4.5" aria-hidden="true" />
          <input
            aria-label={L.attachFiles}
            type="file"
            multiple
            className="sr-only"
            disabled={disabled || pending}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files ?? []).slice(
                0,
                MAX_THREAD_ATTACHMENTS,
              );
              onAttachmentsChange?.([
                ...attachments,
                ...files.map((file) => ({
                  id: nextSelectionId(),
                  file,
                })),
              ].slice(0, MAX_THREAD_ATTACHMENTS));
              event.currentTarget.value = '';
            }}
          />
        </label>
        <MentionTextarea
          value={draft}
          onChange={setDraft}
          agents={agents}
          onSubmit={() => { submit(); }}
          disabled={disabled || pending}
          rows={1}
          placeholder={composerPlaceholder}
          ariaLabel={L.textareaAria}
          mentionListLabel={labels?.mentionList}
          className="text-body text-text-primary placeholder:text-text-secondary order-first min-w-0 flex-1 basis-full resize-none @2xs:order-none @2xs:basis-0 bg-transparent py-1.25 focus:outline-none disabled:opacity-50"
        />
        {/* Stop — THR-376 .stop-btn: 30px outlined danger button with a filled
            stop-square and visible "Stop" text, left of send. Renders only while
            replies are in flight; one click still aborts EVERY in-flight reply
            (the parent owns the thread-level mutation). */}
        <div className="ml-auto flex items-end gap-2">
          {abortReplies?.active && (
            <button
              type="button"
              onClick={abortReplies.onAbort}
              disabled={abortReplies.isPending}
              title={abortReplies.isPending ? L.aborting : L.abortReply}
              className="border-border-strong bg-surface-raised text-feedback-danger hover:border-feedback-danger hover:bg-danger-soft text-label mb-0.25 inline-flex h-7.5 shrink-0 items-center gap-1.5 rounded-sm border px-2.5 font-semibold whitespace-nowrap transition-colors disabled:opacity-50"
            >
              <Square className="h-3 w-3" fill="currentColor" aria-hidden="true" />
              {abortReplies.isPending ? L.aborting : L.stop}
            </button>
          )}
          {/* Send — THR-376 .btn.btn-primary.btn-icon: 32px square, --radius-sm. */}
          <button
            type="button"
            onClick={submit}
            disabled={disabled || !canSend || pending}
            aria-label={L.send}
            title={L.sendTitle}
            className="bg-accent-default text-text-inverse hover:bg-accent-hover disabled:bg-surface-hover disabled:text-text-muted inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-sm transition-colors"
          >
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Send error surfaces below the box; the broadcast copy is the placeholder.
          Rendered whenever an error is SET (non-null), not by text truthiness,
          so a raw diagnostic that is the empty string still owns its slot. */}
      {errorMessage != null && (
        <span className="text-caption text-feedback-danger">{errorMessage}</span>
      )}
      {/* THR-376 .composer-hint (11.5px ink-2 → text-caption text-secondary);
          the mockup hides it on narrow layouts. */}
      {hint && (
        <p className="text-caption text-text-secondary hidden @md:block">{hint}</p>
      )}
    </div>
  );
}
