/**
 * IdBadge — monospace entity ID (THR-NNN / TASK-NNN / JOB-NNN /
 * SCHEDULE-NNN / workflow run ID), color-tinted text, no fill. Pasture `.id`
 * colours every entity ID with --info at weight 500 (THR-376, PR12 weight);
 * thread/task keep their
 * `--color-id-*` aliases, which now resolve to --color-info.
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
  kind: 'thread' | 'task' | 'job' | 'schedule' | 'run';
  /** Optional react-router target. When set, the badge becomes a Link. */
  to?: string;
}

const TEXT_COLOR: Record<IdBadgeProps['kind'], string> = {
  thread: 'text-id-thread',
  task: 'text-id-task',
  job: 'text-info',
  schedule: 'text-info',
  run: 'text-info',
};

export function IdBadge({ id, kind, to }: IdBadgeProps): JSX.Element {
  const inner = (
    <span className={`font-mono text-xs font-medium ${TEXT_COLOR[kind]}`}>{id}</span>
  );
  return to ? (
    <Link to={to} className="hover:underline">
      {inner}
    </Link>
  ) : (
    inner
  );
}
