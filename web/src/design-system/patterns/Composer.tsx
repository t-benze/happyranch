/**
 * Composer — sticky-bottom textarea + helper line + Send button. Per
 * DESIGN.md `components.textarea` + `components.button.primary`. Used at
 * the foot of the threads detail pane.
 *
 * Draft persistence: useThreadDraft stores partial messages in localStorage
 * keyed by (orgSlug, threadId) with a 300ms debounce. Drafts survive
 * navigation and are cleared on successful send.
 *
 * Auto-grow, @-mention autocomplete, Enter-to-send (Shift+Enter for new
 * line) live in MentionTextarea so the same typing experience is reused
 * by other surfaces (e.g. NewThreadDialog).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Paperclip, X } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { useOrgSlug } from '@/lib/orgSlug';
import { MentionTextarea } from './MentionTextarea';
import type { AgentSummary } from '@/lib/api/agents';

const DRAFT_CAP_CHARS = 65_536;
const DRAFT_DEBOUNCE_MS = 300;

interface DraftHandle {
  draft: string;
  setDraft: (next: string) => void;
  clearDraft: () => void;
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
  /** Optional error message (typically from a failed send). */
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
}: ComposerProps): JSX.Element {
  const orgSlug = useOrgSlug();
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const canSend = Boolean(draft.trim() || attachments.length);

  const removeAttachment = (id: string) => {
    onAttachmentsChange?.(attachments.filter((item) => item.id !== id));
  };

  const submit = async () => {
    if (!canSend || disabled || pending) return;
    try {
      await onSend(draft, attachments);
      clearDraft();
      onAttachmentsChange?.([]);
    } catch {
      // Composition surfaces via errorMessage; draft is preserved for retry.
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <MentionTextarea
        value={draft}
        onChange={setDraft}
        agents={agents}
        onSubmit={() => { submit(); }}
        disabled={disabled || pending}
        placeholder={
          placeholder ?? (disabled ? 'Thread is closed.' : 'Write a message… Enter to send, Shift+Enter for new line.')
        }
        ariaLabel="Compose follow-up"
        registerFocus={registerFocus}
      />
      <div className="flex flex-col gap-2">
        <label className="border-border-subtle bg-surface text-caption hover:bg-surface-hover inline-flex w-fit cursor-pointer items-center gap-2 rounded-md border px-2 py-1">
          <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Attach files</span>
          <input
            aria-label="Attach files"
            type="file"
            multiple
            className="sr-only"
            disabled={disabled || pending}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files ?? []).slice(0, 5);
              onAttachmentsChange?.([
                ...attachments,
                ...files.map((file) => ({
                  id: `${file.name}-${file.size}-${file.lastModified}`,
                  file,
                })),
              ].slice(0, 5));
              event.currentTarget.value = '';
            }}
          />
        </label>
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
                  aria-label={`Remove ${item.file.name}`}
                  onClick={() => removeAttachment(item.id)}
                  disabled={disabled || pending}
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-2">
        {errorMessage ? (
          <span className="text-caption text-feedback-danger">{errorMessage}</span>
        ) : (
          <span className="text-caption text-text-muted">{helper ?? ''}</span>
        )}
        <Button onClick={submit} disabled={disabled || !canSend || pending}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}

export const meta = {
  name: "Composer",
  layer: "pattern",
  import: "@/design-system/patterns/Composer",
  variants: {},
  consumes: ["components.textarea", "components.button"],
  example: "<Composer onSend={(md) => {}} helper='Enter to send · Shift+Enter for new line' agents={[]} threadId='THR-001' />",
} as const;
