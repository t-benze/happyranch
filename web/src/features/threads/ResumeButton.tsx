import { Button } from '@/design-system/primitives/Button';
import { useResumeThread } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';

interface Props {
  threadId: string;
}

export function ResumeButton({ threadId }: Props): JSX.Element {
  const { t } = useTranslation();
  const resume = useResumeThread(threadId);
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => resume.mutateAsync()}
      disabled={resume.isPending}
      title={t('threads.resume.label')}
    >
      {resume.isPending ? t('threads.resume.pending') : t('threads.resume.label')}
    </Button>
  );
}
