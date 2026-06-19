/**
 * CrescentMoonBadge — shared SVG crescent-moon badge for dream-originated items (A4).
 *
 * Used consistently across Threads list/detail, Audit timeline, Dreams
 * feed, and Home dashboard wherever composed_from_dream_id is populated.
 * Per the honesty lens (P1): render ONLY when the backing field is present;
 * never show a placeholder crescent.
 */
import { cn } from '@/lib/utils';

interface CrescentMoonBadgeProps {
  className?: string;
}

export function CrescentMoonBadge({ className }: CrescentMoonBadgeProps): JSX.Element {
  return (
    <svg
      className={cn('text-accent inline-block shrink-0', className)}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-label="Dream-originated"
      role="img"
    >
      <path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a6.4 6.4 0 0 1-4.54 1.86c-3.53 0-6.4-2.87-6.4-6.4 0-1.62.6-3.1 1.6-4.24A9 9 0 0 0 12 3Z" />
    </svg>
  );
}

export const meta = {
  name: "CrescentMoonBadge",
  layer: "pattern",
  import: "@/design-system/patterns/CrescentMoonBadge",
  variants: {},
  consumes: ["components.badge"],
  example: "<CrescentMoonBadge />",
} as const;
