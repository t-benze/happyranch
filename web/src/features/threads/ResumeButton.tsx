import { Button } from '@/design-system/primitives/Button';
import { useResumeThread } from '@/hooks/threads';

interface Props {
  threadId: string;
}

export function ResumeButton({ threadId }: Props): JSX.Element {
  const resume = useResumeThread(threadId);
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => resume.mutateAsync()}
      disabled={resume.isPending}
      title="Resume thread"
    >
      {resume.isPending ? 'Resuming…' : 'Resume thread'}
    </Button>
  );
}
