/**
 * FilterSidebar — reusable 240px left rail with grouped filter chips.
 * Per DESIGN.md `components.filter_sidebar`. Grouped options with optional
 * counts. Pure prop-driven; filters via onChange callback.
 */
import { cn } from '@/lib/utils';

export interface FilterGroup {
  key: string;
  label: string;
  options: { value: string; label: string; count?: number }[];
}

export interface FilterSidebarProps {
  groups: FilterGroup[];
  value: Record<string, string | null>;
  onChange: (next: Record<string, string | null>) => void;
}

export function FilterSidebar({ groups, value, onChange }: FilterSidebarProps): JSX.Element {
  return (
    <aside className="border-border-subtle bg-surface-sunken w-60 shrink-0 overflow-y-auto border-r p-3">
      {groups.map((g) => (
        <section key={g.key} className="mb-4">
          <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
            {g.label}
          </h3>
          <ul className="space-y-0.5">
            <li>
              <button
                type="button"
                onClick={() => onChange({ ...value, [g.key]: null })}
                className={cn(
                  'w-full rounded px-2 py-1 text-left text-sm',
                  value[g.key] == null
                    ? 'bg-accent-muted text-fg'
                    : 'text-fg-muted hover:bg-surface-raised',
                )}
              >
                All
              </button>
            </li>
            {g.options.map((o) => (
              <li key={o.value}>
                <button
                  type="button"
                  onClick={() => onChange({ ...value, [g.key]: o.value })}
                  className={cn(
                    'flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm',
                    value[g.key] === o.value
                      ? 'bg-accent-muted text-fg'
                      : 'text-fg-muted hover:bg-surface-raised',
                  )}
                >
                  <span>{o.label}</span>
                  {o.count != null && (
                    <span className="font-mono text-xs">{o.count}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </aside>
  );
}

export const meta = {
  name: 'FilterSidebar',
  layer: 'pattern',
  import: '@/design-system/patterns/FilterSidebar',
  variants: {},
  consumes: ['components.filter_sidebar'],
  example: "<FilterSidebar groups={[]} value={{}} onChange={() => {}} />",
} as const;
