/**
 * TierBadge — green/yellow/red performance-tier pill. Per DESIGN.md
 * `components.badge.variants.tier_*`. Reserved for the Agents page; not
 * used by Threads in v0.1. Shipped now so it lives next to its siblings.
 */

interface TierBadgeProps {
  tier: 'green' | 'yellow' | 'red';
}

const TIER_CLASS: Record<TierBadgeProps['tier'], string> = {
  green: 'bg-tier-green-tint text-tier-green border-tier-green',
  yellow: 'bg-tier-yellow-tint text-tier-yellow border-tier-yellow',
  red: 'bg-tier-red-tint text-tier-red border-tier-red',
};

export function TierBadge({ tier }: TierBadgeProps): JSX.Element {
  return (
    <span
      className={`text-mono-sm inline-flex items-center rounded-sm border px-2 py-px font-mono font-semibold ${TIER_CLASS[tier]}`}
    >
      {tier}
    </span>
  );
}

export const meta = {
  name: "TierBadge",
  layer: "pattern",
  import: "@/design-system/patterns/TierBadge",
  variants: { tier: ["green", "yellow", "red"] },
  consumes: ["components.badge"],
  example: "<TierBadge tier='green' />",
} as const;
