/**
 * StatusBadge — pill for thread status. Per DESIGN.md
 * `components.badge.variants.status_*`. Tier-tint fill, status-color text,
 * transparent border (archived uses a subtle border to read as "outline").
 *
 * Pure prop-driven.
 */

interface StatusBadgeProps {
  status: 'open' | 'archiving' | 'archived' | 'abandoned';
}

const STATUS_CLASS: Record<StatusBadgeProps['status'], string> = {
  open: 'bg-tier-green-tint text-status-open',
  archiving: 'bg-tier-yellow-tint text-status-archiving',
  archived: 'border border-border-subtle bg-transparent text-status-archived',
  abandoned: 'bg-tier-red-tint text-status-abandoned',
};

export function StatusBadge({ status }: StatusBadgeProps): JSX.Element {
  return (
    <span
      className={`text-mono-sm inline-flex items-center rounded-sm px-2 py-px font-mono font-semibold ${STATUS_CLASS[status]}`}
    >
      {status}
    </span>
  );
}

export const meta = {
  name: "StatusBadge",
  layer: "pattern",
  import: "@/design-system/patterns/StatusBadge",
  variants: { status: ["open", "archiving", "archived", "abandoned"] },
  consumes: ["components.badge"],
  example: "<StatusBadge status='open' />",
} as const;
