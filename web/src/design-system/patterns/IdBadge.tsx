/**
 * IdBadge — monospace THR-NNN / TASK-NNN, color-tinted text, no fill.
 * Per DESIGN.md `components.badge.variants.id_thread` / `id_task`.
 *
 * Pure prop-driven.
 */

interface IdBadgeProps {
  id: string;
  kind: 'thread' | 'task';
}

const TEXT_COLOR: Record<IdBadgeProps['kind'], string> = {
  thread: 'text-id-thread',
  task: 'text-id-task',
};

export function IdBadge({ id, kind }: IdBadgeProps): JSX.Element {
  return (
    <span className={`font-mono text-xs ${TEXT_COLOR[kind]}`}>{id}</span>
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
