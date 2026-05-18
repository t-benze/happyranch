export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  return <aside data-testid="task-detail" data-task={taskId} />;
}
