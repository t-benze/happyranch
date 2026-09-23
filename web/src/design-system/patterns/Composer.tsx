/**
 * Composer — compact single-line 18px-rounded input with an INLINE attach icon and
 * a circular send button (THR-061 a-thread-detail). Per DESIGN.md
 * `components.textarea` + `components.button.primary`. Used at the foot of the
 * threads detail pane.
 *
 * The broadcast helper copy lives in the PLACEHOLDER ("Message the thread —
 * all participants see it"), so there is no separate helper line under the
 * input; only a send-error surfaces below the pill. The attach affordance is a
 * small inline paperclip icon (still labelled "Attach files") — the capability
 * is preserved, only the chrome is compacted.
 *
 * Draft persistence: useThreadDraft stores partial messages in localStorage
 * keyed by (orgSlug, threadId) with a 300ms debounce. Drafts survive
 * navigation and are cleared on successful send.
 *
 * Auto-grow, @-mention autocomplete, Enter-to-send (Shift+Enter for new
 * line) live in MentionTextarea so the same typing experience is reused
 * by other surfaces (e.g. NewThreadDialog). The optional abort-reply control
 * sits INSIDE the input pill as an icon-only trailing action next to the
 * circular send button (THR-099 Phase A, founder seq57 — reversing the earlier
 * "moved OUT" decision): a compact neutral/light-fill, thin-outline square
 * button with a centered red stop-square icon. It renders only while replies
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
import type { AgentSummary } from '@/lib/api/agents';

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
  /** Helper text shown below the textarea when no error is set. */
  helper?: string;
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
  /** Lets a parent focus the textarea (e.g. the R keyboard shortcut). */
  registerFocus?: (focus: () => void) => void;

  // Required
  agents: AgentSummary[];
  threadId: string;
  /**
   * Active org slug — keys the localStorage draft alongside threadId. Passed
   * down from the rendering feature (patterns stay pure props-in/JSX-out and
   * must not call @/lib/orgSlug directly).
   */
  orgSlug: string;
  /**
   * Optional abort-reply trailing action rendered INSIDE the input pill, to the
   * left of the circular send button (THR-099 Phase A) — an icon-only compact
   * square button (red stop-square). It renders ONLY while `active` (replies in
   * flight); it is disabled and its accessible name/tooltip read "Aborting…"
   * while `isPending`, otherwise "Abort reply" (no visible text either way).
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
  placeholder,
  registerFocus,
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
    <div className="flex flex-col gap-2">
      {/* Pending attachment chips — above the pill so the input stays compact. */}
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
                className="text-text-muted hover:text-text"
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

      {/* Compact rounded input: inline attach icon + textarea + circular send.
          Founder-approved 18px local extension — reads as a single-line input and
          grows gracefully for the rare Shift+Enter multi-line draft. */}
      <div className="border-border-default bg-surface-raised focus-within:border-accent-default flex items-end gap-1 rounded-lg border py-1 pr-1 pl-2 transition-colors">
        <label
          className="text-text-muted hover:text-text-secondary hover:bg-surface-hover mb-0.5 inline-flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full transition-colors"
          title={L.attachFiles}
        >
          <Paperclip className="h-4 w-4" aria-hidden="true" />
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
          registerFocus={registerFocus}
          className="text-body text-text-primary placeholder:text-text-muted w-full resize-none bg-transparent py-1.5 focus:outline-none disabled:opacity-50"
        />
        {/* Abort reply — icon-only trailing action INSIDE the pill, left of the
            circular send (THR-099 abort-reply correction). Renders only while
            replies are in flight. A compact neutral/light-fill, thin-outline
            square button (matching the send button's h-9 footprint) whose
            centered icon is a red stop-square; it carries NO visible text (the
            "Abort reply" / "Aborting…" copy lives in the accessible name +
            tooltip, not on-screen). One click still aborts EVERY in-flight
            reply — the parent owns the thread-level mutation. */}
        {abortReplies?.active && (
          <button
            type="button"
            onClick={abortReplies.onAbort}
            disabled={abortReplies.isPending}
            aria-label={abortReplies.isPending ? L.aborting : L.abortReply}
            title={abortReplies.isPending ? L.aborting : L.abortReply}
            className="border-border-default bg-surface-raised text-feedback-danger hover:border-feedback-danger hover:bg-danger-soft mb-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-colors disabled:opacity-50"
          >
            <Square className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !canSend || pending}
          aria-label={L.send}
          title={L.sendTitle}
          className="bg-accent text-accent-fg hover:bg-accent-hover disabled:bg-surface-hover disabled:text-text-muted mb-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors"
        >
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      {/* Send error surfaces below the pill; the broadcast copy is the placeholder.
          Rendered whenever an error is SET (non-null), not by text truthiness,
          so a raw diagnostic that is the empty string still owns its slot. */}
      {errorMessage != null && (
        <span data-testid="composer-error" className="text-caption text-feedback-danger">
          {errorMessage}
        </span>
      )}
    </div>
  );
}
