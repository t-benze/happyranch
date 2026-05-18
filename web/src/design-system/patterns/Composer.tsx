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
 *
 * Mentions: typing @<query> opens MentionAutocomplete anchored below the
 * textarea. Selecting an agent inserts @<name> and positions the caret
 * right after it. On send, resolveAddressedTo scans the final body for
 * @<name> mentions and computes addressed_to.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useOrgSlug } from '@/lib/orgSlug';
import { MentionAutocomplete } from './MentionAutocomplete';
import type { AgentSummary } from '@/lib/api/agents';

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

const MENTION_TOKEN_RE = /@([A-Za-z0-9_-]+)/g;

function detectOpenMention(text: string, caret: number):
  | { query: string; tokenStart: number }
  | null {
  for (let i = caret - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === '@') return { query: text.slice(i + 1, caret), tokenStart: i };
    if (/\s/.test(ch)) return null;
  }
  return null;
}

function resolveAddressedTo(body: string, agents: AgentSummary[] = []): string[] {
  const byName = new Set(agents.map((a) => a.name));
  const out = new Set<string>();
  for (const m of body.matchAll(MENTION_TOKEN_RE)) {
    const token = m[1];
    if (token === 'all') { out.add('@all'); continue; }
    if (byName.has(token)) out.add(token);
  }
  return out.size > 0 ? Array.from(out) : ['@all'];
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
  agents = [],
  threadId = '',
}: ComposerProps): JSX.Element {
  const orgSlug = useOrgSlug();
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(textareaRef, draft);

  const [mention, setMention] = useState<
    | { query: string; tokenStart: number; anchor: { x: number; y: number; width: number; height: number } }
    | null
  >(null);

  // Compute filtered matches here so we can gate Cmd+Enter on actual matches,
  // not just on whether a @-token is open. This avoids blocking submit when the
  // user types @all with an empty agents list (no matches → popup renders null
  // → Enter must fall through to submit).
  const mentionMatches = useMemo(() => {
    if (!mention) return [];
    const q = mention.query.toLowerCase();
    return agents.filter((a) => a.name.toLowerCase().startsWith(q)).slice(0, 8);
  }, [mention, agents]);

  const popupOpen = mentionMatches.length > 0;

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  const refreshMention = useCallback(() => {
    const el = textareaRef.current;
    if (!el || disabled) { setMention(null); return; }
    const caret = el.selectionStart ?? 0;
    const m = detectOpenMention(draft, caret);
    if (!m) { setMention(null); return; }
    const rect = el.getBoundingClientRect();
    // v1: anchor the popup to the textarea's bottom-left corner.
    setMention({ query: m.query, tokenStart: m.tokenStart, anchor: { x: rect.left, y: rect.top, width: rect.width, height: rect.height } });
  }, [draft, disabled]);

  useEffect(() => { refreshMention(); }, [refreshMention]);

  const acceptMention = useCallback((agent: AgentSummary) => {
    if (!mention) return;
    const el = textareaRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? 0;
    const before = draft.slice(0, mention.tokenStart);
    const after = draft.slice(caret);
    const inserted = `@${agent.name} `;
    const next = before + inserted + after;
    setDraft(next);
    setMention(null);
    // Restore caret position right after the inserted token.
    // Use queueMicrotask so this runs after React flushes the state update
    // but before the next userEvent action in tests.
    queueMicrotask(() => {
      const newCaret = (before + inserted).length;
      el.setSelectionRange(newCaret, newCaret);
      el.focus();
    });
  }, [draft, mention, setDraft]);

  const submit = async () => {
    if (!draft.trim() || disabled || pending) return;
    const addressedTo = resolveAddressedTo(draft, agents);
    try {
      await onSend(draft, addressedTo);
      clearDraft();
      setMention(null);
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
        onKeyUp={refreshMention}
        onClick={refreshMention}
        onKeyDown={(e) => {
          // Cmd/Ctrl+Enter submits IF the mention popup is closed (no matches);
          // otherwise the popup handles Enter via its own document-level keydown.
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !popupOpen) {
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
      {mention && (
        <MentionAutocomplete
          anchor={mention.anchor}
          matches={mentionMatches}
          onSelect={acceptMention}
          onDismiss={() => setMention(null)}
        />
      )}
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
