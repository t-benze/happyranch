/**
 * Composer — sticky-bottom textarea + helper line + Send button. Per
 * DESIGN.md `components.textarea` + `components.button.primary`. Used at
 * the foot of the threads detail pane.
 *
 * Draft persistence: useThreadDraft stores partial messages in localStorage
 * keyed by (orgSlug, threadId) with a 300ms debounce. Drafts survive
 * navigation and are cleared on successful send.
 *
 * Auto-grow: useAutoGrow resizes the textarea up to MAX_TEXTAREA_PX px.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useOrgSlug } from '@/lib/orgSlug';

const MAX_TEXTAREA_PX = 240;
const DRAFT_CAP_CHARS = 65_536;
const DRAFT_DEBOUNCE_MS = 300;

function useAutoGrow(
  ref: React.RefObject<HTMLTextAreaElement>,
  value: string,
  maxPx = MAX_TEXTAREA_PX,
): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, maxPx) + 'px';
  }, [ref, value, maxPx]);
}

interface DraftHandle {
  draft: string;
  setDraft: (next: string) => void;
  clearDraft: () => void;
}

function useThreadDraft(orgSlug: string, threadId: string): DraftHandle {
  const key = `grassland:draft:${orgSlug}:${threadId}`;
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
   * Called with the markdown and addressedTo when the user presses Send or
   * Cmd/Ctrl+Enter. May return a Promise; if it rejects (or a sync impl
   * throws), the draft is preserved so the user can retry without retyping.
   */
  onSend: (markdown: string, addressedTo: string[]) => unknown | Promise<unknown>;
  /** Lets a parent focus the textarea (e.g. the R keyboard shortcut). */
  registerFocus?: (focus: () => void) => void;

  // NEW (required)
  agents: import('@/lib/api/agents').AgentSummary[];
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
  agents,           // wired in Task 10
  threadId,
}: ComposerProps): JSX.Element {
  const orgSlug = useOrgSlug();
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(textareaRef, draft);

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  // agents is wired in Task 10; suppress unused-var lint for now.
  void agents;

  const submit = async () => {
    if (!draft.trim() || disabled || pending) return;
    try {
      // addressedTo is wired in Task 10; for now always @all.
      await onSend(draft, ['@all']);
      clearDraft();
    } catch {
      // Composition surfaces via errorMessage; draft is preserved for retry.
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={
          placeholder ?? (disabled ? 'Thread is closed.' : 'Write a message… Cmd/Ctrl+Enter to send.')
        }
        disabled={disabled || pending}
        rows={3}
        aria-label="Compose follow-up"
        className="border-border-default bg-surface-raised text-body-lg text-text-primary placeholder:text-text-muted focus:border-accent-default w-full resize-none rounded-md border px-3 py-2 focus:outline-none disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-2">
        {errorMessage ? (
          <span className="text-caption text-feedback-danger">{errorMessage}</span>
        ) : (
          <span className="text-caption text-text-muted">{helper ?? ''}</span>
        )}
        <Button onClick={submit} disabled={disabled || !draft.trim() || pending}>
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
  example: "<Composer onSend={(md, to) => {}} helper='Cmd/Ctrl+Enter to send' agents={[]} threadId='THR-001' />",
} as const;
