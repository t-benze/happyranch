/**
 * SegmentedControl — single-select value toggle (THR-376 Phase 3c).
 *
 * Use it to pick ONE value from a small, fixed set of options that filter or
 * re-group the content already on the page (time window, group-by, status
 * bucket, current/removed view). It has no panels. Use `Tabs` when each option
 * owns its own panel of content (`TabsContent`); use independent toggle
 * buttons / chips when several options can be on at once or the active option
 * can be cleared by clicking it again.
 *
 * Accessibility: a WAI-ARIA radio group. The container is `role="radiogroup"`
 * (always give it `aria-label` or `aria-labelledby`); each option is a
 * `role="radio"` button with `aria-checked`. Roving tabindex: only the checked
 * option (or the first enabled option when none is checked) is in the tab
 * order. Arrow Left/Up and Right/Down move to the previous/next enabled option
 * and select it (selection follows focus); Home/End jump to the first/last.
 * Disabled options are skipped. A click always calls `onValueChange`, also
 * on the already-checked option (as the hand-rolled button groups it replaced
 * did); guard at the call site if a repeat must be a no-op.
 *
 * Pasture `web/design/pasture-ds.css` has no `.seg` rule. The composed values
 * reuse ds.css tokens only:
 *   container — 1px var(--border) (`border-border-default`), var(--surface-2)
 *               fill (`bg-surface-sunken`), var(--radius-pill)
 *               (`rounded-full`), 4px padding and 4px gap (`p-1 gap-1`).
 *   option    — 12px / 600 label (`text-caption font-semibold`, the ds.css
 *               `.hr-chip` / `.btn-sm` label size and weight), pill radius,
 *               ink-3 (`text-text-muted`) at rest, ink on hover.
 *   active    — var(--accent-soft) fill + var(--accent-text) label
 *               (`bg-accent-soft text-accent-text`), the ds.css `.tag.ok` /
 *               Direction-A `.nav-item.active` pairing.
 *   sizes     — default `px-3 py-1` (12/4px); sm `px-2.5 py-0.5` (10/2px) for
 *               compact card headers.
 *   focus     — the shared `focus-visible:ring-2 ring-ring` accent ring.
 *
 * `Tabs variant="segmented"` reads the same class constants so the two look
 * identical. Controlled only: pass `value` and `onValueChange`.
 *
 *   <SegmentedControl
 *     aria-label={t('tasks.page.groupBy')}
 *     value={groupBy}
 *     onValueChange={setGroupBy}
 *     options={[{ value: 'status', label: t('…') }, …]}
 *   />
 */
import { useRef, type KeyboardEvent, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

/** Container classes shared with `Tabs variant="segmented"`. */
export const SEGMENTED_LIST_CLASS =
  'border-border-default bg-surface-sunken inline-flex max-w-full flex-wrap items-center gap-1 rounded-full border p-1';

/** Option classes (state-independent) shared with `Tabs variant="segmented"`. */
export const SEGMENTED_ITEM_CLASS =
  'text-text-muted hover:text-text-primary focus-visible:ring-ring inline-flex items-center justify-center rounded-full font-semibold whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50';

/** Active-option fill + label colour (accent-soft / accent-text). */
export const SEGMENTED_ACTIVE_CLASS = 'bg-accent-soft text-accent-text hover:text-accent-text';

/**
 * Option label size. Kept OUT of `cn()`: tailwind-merge reads the custom
 * `text-caption` size as a text colour and would drop it next to
 * `text-text-muted` / `text-accent-text`. Prepend it as a plain string.
 */
export const SEGMENTED_TEXT_CLASS = 'text-caption';

export const SEGMENTED_SIZE_CLASS = {
  default: 'px-3 py-1',
  sm: 'px-2.5 py-0.5',
} as const;

export type SegmentedControlSize = keyof typeof SEGMENTED_SIZE_CLASS;

export interface SegmentedControlOption<T extends string> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  options: ReadonlyArray<SegmentedControlOption<T>>;
  size?: SegmentedControlSize;
  'aria-label'?: string;
  'aria-labelledby'?: string;
  className?: string;
  /** Extra classes for every option button. */
  itemClassName?: string;
}

export function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  size = 'default',
  className,
  itemClassName,
  ...aria
}: SegmentedControlProps<T>): JSX.Element {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const checkedIdx = options.findIndex((o) => o.value === value && !o.disabled);
  const tabStop = checkedIdx >= 0 ? checkedIdx : options.findIndex((o) => !o.disabled);

  const move = (from: number, step: 1 | -1 | 'first' | 'last'): void => {
    const n = options.length;
    let i: number;
    if (step === 'first' || step === 'last') {
      i = step === 'first' ? 0 : n - 1;
      const dir = step === 'first' ? 1 : -1;
      while (i >= 0 && i < n && options[i]!.disabled) i += dir;
      if (i < 0 || i >= n) return;
    } else {
      i = from;
      for (let k = 0; k < n; k++) {
        i = (i + step + n) % n;
        if (!options[i]!.disabled) break;
      }
      if (options[i]!.disabled || i === from) return;
    }
    refs.current[i]?.focus();
    onValueChange(options[i]!.value);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number): void => {
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        move(idx, 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault();
        move(idx, -1);
        break;
      case 'Home':
        e.preventDefault();
        move(idx, 'first');
        break;
      case 'End':
        e.preventDefault();
        move(idx, 'last');
        break;
      default:
    }
  };

  return (
    <div role="radiogroup" {...aria} className={cn(SEGMENTED_LIST_CLASS, className)}>
      {options.map((opt, idx) => {
        const checked = idx === checkedIdx;
        return (
          <button
            key={opt.value}
            ref={(el) => {
              refs.current[idx] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            data-state={checked ? 'on' : 'off'}
            disabled={opt.disabled}
            tabIndex={idx === tabStop ? 0 : -1}
            onClick={() => onValueChange(opt.value)}
            onKeyDown={(e) => onKeyDown(e, idx)}
            className={`${SEGMENTED_TEXT_CLASS} ${cn(
              SEGMENTED_ITEM_CLASS,
              SEGMENTED_SIZE_CLASS[size],
              checked && SEGMENTED_ACTIVE_CLASS,
              itemClassName,
            )}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
