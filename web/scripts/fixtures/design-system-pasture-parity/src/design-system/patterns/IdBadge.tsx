interface IdBadgeProps {
  id: string;
  kind: 'thread' | 'task' | 'job';
}

const TEXT_COLOR: Record<IdBadgeProps['kind'], string> = {
  thread: 'text-id-thread',
  task: 'text-id-task',
  job: 'text-info',
};

export function IdBadge({ id, kind }: IdBadgeProps): JSX.Element {
  return <span className={`font-mono text-xs font-medium ${TEXT_COLOR[kind]}`}>{id}</span>;
}
