/**
 * MentionAutocomplete — floating popup of matching agents, anchored
 * below a caret rect.
 *
 * Pure props in / events out. No fetching — the caller passes the full
 * agent list and the current query string. Keyboard handling is global
 * (document-level keydown) because the popup is rendered outside the
 * focused textarea and we need ArrowUp/Down/Enter/Esc to interact with
 * it without stealing focus.
 *
 * Filtering: prefix-match on agent.name (case-insensitive).
 */
import { useEffect, useMemo, useState } from 'react';
import type { AgentSummary } from '@/lib/api/agents';

export interface MentionAutocompleteProps {
  anchor: { x: number; y: number; width: number; height: number };
  query: string;
  agents: AgentSummary[];
  onSelect: (agent: AgentSummary) => void;
  onDismiss: () => void;
}

export function MentionAutocomplete({
  anchor,
  query,
  agents,
  onSelect,
  onDismiss,
}: MentionAutocompleteProps): JSX.Element | null {
  const matches = useMemo(() => {
    const q = query.toLowerCase();
    return agents.filter((a) => a.name.toLowerCase().startsWith(q)).slice(0, 8);
  }, [query, agents]);

  const [active, setActive] = useState(0);
  // Reset active index when the query (and therefore matches) changes.
  useEffect(() => { setActive(0); }, [query, agents.length]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onDismiss(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, matches.length - 1)); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); return; }
      if ((e.key === 'Enter' || e.key === 'Tab') && matches[active]) {
        e.preventDefault();
        onSelect(matches[active]);
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [matches, active, onSelect, onDismiss]);

  if (matches.length === 0) return null;

  const style: React.CSSProperties = {
    position: 'fixed',
    left: anchor.x,
    top: anchor.y + anchor.height + 4,
    minWidth: 200,
    maxWidth: 320,
    zIndex: 1000,
  };

  return (
    <div
      role="listbox"
      aria-label="Mention agents"
      style={style}
      className="border-border-default bg-surface-overlay text-text-primary text-caption rounded-md border shadow-lg"
    >
      {matches.map((a, i) => (
        <button
          key={a.name}
          type="button"
          role="option"
          aria-selected={i === active}
          onMouseDown={(e) => { e.preventDefault(); onSelect(a); }}
          onMouseEnter={() => setActive(i)}
          className={`block w-full px-3 py-1.5 text-left ${
            i === active ? 'bg-accent-muted' : 'hover:bg-surface-raised'
          }`}
        >
          <span className="font-medium">{a.name}</span>
          <span className="text-text-muted ml-2">{a.team}</span>
        </button>
      ))}
    </div>
  );
}

export const meta = {
  name: 'MentionAutocomplete',
  layer: 'pattern',
  import: '@/design-system/patterns/MentionAutocomplete',
  variants: {},
  consumes: ['components.popover'],
  example: "<MentionAutocomplete anchor={{x:0,y:0,width:0,height:0}} query='' agents={[]} onSelect={() => {}} onDismiss={() => {}} />",
} as const;
