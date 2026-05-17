/**
 * KbdChip — small keycap-style chip used in HelpSheet and inline hints.
 * Per DESIGN.md `components.kbd_chip`: subtle bottom-inset shadow gives
 * the keycap depth without any drop shadow.
 *
 * Renders one or more keys as side-by-side <kbd> elements.
 */

interface KbdChipProps {
  keys: string[];
}

export function KbdChip({ keys }: KbdChipProps): JSX.Element {
  return (
    <span className="inline-flex items-center gap-1">
      {keys.map((k) => (
        <kbd
          key={k}
          className="inline-flex min-w-[1.5rem] items-center justify-center rounded-sm border border-border-default bg-surface-raised px-2 py-px font-mono text-mono-sm text-text-primary shadow-[inset_0_-1px_0_rgba(0,0,0,0.4)]"
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

export const meta = {
  name: "KbdChip",
  layer: "pattern",
  import: "@/design-system/patterns/KbdChip",
  variants: {},
  consumes: ["components.kbd_chip"],
  example: "<KbdChip keys={['Ctrl', 'Enter']} />",
} as const;
