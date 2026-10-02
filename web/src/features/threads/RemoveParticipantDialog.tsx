import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useRemoveParticipant } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';
import { classifyThreadError, renderThreadError, type ThreadErrorView } from '@/lib/threadErrors';

interface Props {
  threadId: string;
  /** Participant to remove. `null` keeps the dialog closed. */
  agentName: string | null;
  open: boolean;
  onClose: () => void;
}

/**
 * Confirm-then-remove dialog — the destructive mirror of InviteDialog. The
 * dialog itself is the confirm step: it names the participant and requires an
 * explicit Remove click before firing removeParticipantFromThread.
 */
export function RemoveParticipantDialog({ threadId, agentName, open, onClose }: Props): JSX.Element {
  const { t, render } = useTranslation();
  const remove = useRemoveParticipant(threadId);
  // Locale-neutral error descriptor rendered at render time.
  const [errorView, setErrorView] = useState<ThreadErrorView | null>(null);

  useEffect(() => {
    if (!open) return;
    setErrorView(null);
  }, [open]);

  const submit = async () => {
    if (!agentName) return;
    setErrorView(null);
    try {
      await remove.mutateAsync({ agent_name: agentName });
      onClose();
    } catch (err) {
      setErrorView({ detail: classifyThreadError(err) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('threads.dialog.remove.title')}</DialogTitle>
          <DialogDescription>
            {render('threads.dialog.remove.description', {
              name: <span className="font-semibold">{agentName}</span>,
            })}
          </DialogDescription>
        </DialogHeader>
        {errorView !== null && (
          <p className="text-feedback-danger text-body">{renderThreadError(errorView, t)}</p>
        )}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="button" onClick={submit} disabled={remove.isPending}>
            {remove.isPending ? t('threads.dialog.remove.pending') : t('threads.dialog.remove.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
