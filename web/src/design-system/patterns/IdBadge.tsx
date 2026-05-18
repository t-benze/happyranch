/**
 * IdBadge — monospace THR-NNN / TASK-NNN, color-tinted text, no fill.
 * Per DESIGN.md `components.badge.variants.id_thread` / `id_task`.
 *
 * When `to` is provided, the badge wraps itself in a react-router `<Link>`
 * to enable cross-surface deep-links (e.g., a TASK-NNN reference inside a
 * recall tree opens the detail Drawer).
 *
 * Pure prop-driven.
 */
import { Link } from 'react-router-dom';

interface IdBadgeProps {
  id: string;
  kind: 'thread' | 'task';
  /** Optional react-router target. When set, the badge becomes a Link. */
  to?: string;
}

const TEXT_COLOR: Record<IdBadgeProps['kind'], string> = {
  thread: 'text-id-thread',
  task: 'text-id-task',
};

export function IdBadge({ id, kind, to }: IdBadgeProps): JSX.Element {
  const inner = (
    <span className={`font-mono text-xs ${TEXT_COLOR[kind]}`}>{id}</span>
  );
  return to ? (
    <Link to={to} className="hover:underline">
      {inner}
    </Link>
  ) : (
    inner
  );
}

export const meta = {
  name: "IdBadge",
  layer: "pattern",
  import: "@/design-system/patterns/IdBadge",
  variants: { kind: ["thread", "task"] },
  consumes: ["components.badge"],
  example: "<IdBadge id='THR-042' kind='thread' />",
} as const;
