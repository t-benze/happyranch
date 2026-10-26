import { Button } from '@/design-system/primitives/Button';
import { useResumeThread } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';
import { useStartupStatus } from '@/hooks/health';

interface Props {
  threadId: string;
}

export function ResumeButton({ threadId }: Props): JSX.Element {
  const { t } = useTranslation();
  const resume = useResumeThread(threadId);
  const { admitted } = useStartupStatus();
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => resume.mutateAsync()}
      disabled={resume.isPending || !admitted}
      title={admitted ? t('threads.resume.label') : t('startup.action.paused')}
    >
      {resume.isPending ? t('threads.resume.pending') : t('threads.resume.label')}
    </Button>
  );
}
