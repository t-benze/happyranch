/**
 * RecipientsInput — single-line input for comma-separated agent names
 * with prefix-match autocomplete on the current token. Reuses the
 * MentionAutocomplete popup so the keyboard model (ArrowUp/Down/Enter/Tab/
 * Esc, mouse hover/click) matches @-mention in the body field.
 *
 * Pure prop-driven. Value is the raw comma-separated string the caller
 * tokenises at submit time.
 *
 * When `restrictToOptions` is true, only agent names present in the
 * `agents` prop may be committed as tokens. Non-roster tokens are stripped
 * from committed positions on every keystroke. The current (unterminated)
 * token is always preserved so autocomplete suggestions remain available.
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { MentionAutocomplete } from '@/design-system/patterns/MentionAutocomplete';
import type { AgentSummary } from '@/lib/api/types';

interface Props {
  id?: string;
  value: string;
  onChange: (next: string) => void;
  agents: AgentSummary[];
  placeholder?: string;
  className?: string;
  /** When true, strips non-roster tokens from committed positions. */
  restrictToOptions?: boolean;
}

/** Slice of `value` from the last comma before `caret` to `caret`, trimmed. */
function tokenAtCaret(value: string, caret: number): { start: number; query: string } {
  const before = value.slice(0, caret);
  const lastComma = before.lastIndexOf(',');
  const start = lastComma === -1 ? 0 : lastComma + 1;
  const raw = value.slice(start, caret);
  return { start, query: raw.trimStart() };
}

/**
 * Strip tokens that are not in the roster from committed positions
 * (every token except the last, which is the one the user is actively
 * typing). Empty tokens are preserved as separators.
 */
function filterNonRoster(value: string, rosterNames: Set<string>): string {
  const tokens = value.split(',');
  if (tokens.length <= 1) return value;
  const result: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    if (i === tokens.length - 1) {
      result.push(tokens[i]);
    } else {
      const trimmed = tokens[i].trim();
      if (trimmed === '' || rosterNames.has(trimmed)) {
        result.push(tokens[i]);
      }
      // else: non-roster committed token — omit
    }
  }
  return result.join(',');
}

export function RecipientsInput({
  id,
  value,
  onChange,
  agents,
  placeholder,
  className,
  restrictToOptions,
}: Props): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [popup, setPopup] = useState<
    | { query: string; tokenStart: number; anchor: { x: number; y: number; width: number; height: number } }
    | null
  >(null);

  const rosterNames = useMemo(
    () => new Set(agents.map((a) => a.name)),
    [agents],
  );

  const matches = useMemo(() => {
    if (!popup) return [];
    const q = popup.query.toLowerCase();
    // Empty query (e.g. caret right after a comma+space) shows all agents.
    const taken = new Set(
      value.split(',').map((s) => s.trim()).filter(Boolean),
    );
    return agents
      .filter((a) => !taken.has(a.name) || a.name.toLowerCase().startsWith(q))
      .filter((a) => a.name.toLowerCase().startsWith(q))
      .slice(0, 8);
  }, [popup, agents, value]);

  const popupOpen = matches.length > 0;

  const refresh = useCallback(() => {
    const el = inputRef.current;
    if (!el) { setPopup(null); return; }
    const caret = el.selectionStart ?? 0;
    const { start, query } = tokenAtCaret(value, caret);
    const rect = el.getBoundingClientRect();
    setPopup({
      query,
      tokenStart: start,
      anchor: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
    });
  }, [value]);

  const accept = useCallback(
    (agent: AgentSummary) => {
      if (!popup) return;
      const el = inputRef.current;
      if (!el) return;
      const caret = el.selectionStart ?? 0;
      const before = value.slice(0, popup.tokenStart);
      const after = value.slice(caret);
      // Preserve leading whitespace from the token slice (so "a, b" stays nice).
      const leading = value.slice(popup.tokenStart, caret).match(/^\s*/)?.[0] ?? '';
      // If there's more text after the caret (e.g. trailing partial recipient),
      // don't append a separator. Otherwise add ", " so the next token can be
      // typed immediately.
      const sep = after.trim().length === 0 ? ', ' : '';
      const inserted = `${leading}${agent.name}${sep}`;
      const next = before + inserted + after;
      onChange(next);
      setPopup(null);
      queueMicrotask(() => {
        const newCaret = (before + inserted).length;
        el.setSelectionRange(newCaret, newCaret);
        el.focus();
      });
    },
    [value, popup, onChange],
  );

  return (
    <>
      <input
        ref={inputRef}
        id={id}
        type="text"
        value={value}
        onChange={(e) => {
          const next = restrictToOptions
            ? filterNonRoster(e.target.value, rosterNames)
            : e.target.value;
          onChange(next);
          // Defer to next tick so the controlled value has updated before we
          // read selectionStart — React 18 batches the state flush.
          queueMicrotask(refresh);
        }}
        onFocus={refresh}
        onKeyUp={refresh}
        onClick={refresh}
        onBlur={() => setPopup(null)}
        onKeyDown={(e) => {
          // Comma commits the current token — close popup so it doesn't
          // sit open while typing the next recipient.
          if (e.key === ',') setPopup(null);
          // Backspace at the start of the current token reopens with the
          // preceding token; let refresh() handle that on keyUp.
          if ((e.key === 'Enter' || e.key === 'Tab') && popupOpen) {
            // MentionAutocomplete's document-level keydown handles selection;
            // we just need to not submit the form on Enter while the popup is
            // open. Dialog parent listens for Enter on Send button — preventing
            // default here keeps focus in the input.
            e.preventDefault();
          }
        }}
        placeholder={placeholder}
        className={className ?? 'input'}
        autoComplete="off"
      />
      {popup && (
        <MentionAutocomplete
          anchor={popup.anchor}
          matches={matches}
          onSelect={accept}
          onDismiss={() => setPopup(null)}
        />
      )}
    </>
  );
}

export const meta = {
  name: 'RecipientsInput',
  layer: 'pattern',
  import: '@/design-system/patterns/RecipientsInput',
  variants: {},
  consumes: ['MentionAutocomplete'],
  example: '<RecipientsInput value="" onChange={() => {}} agents={[]} />',
} as const;
