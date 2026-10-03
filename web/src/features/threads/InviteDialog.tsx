import { useEffect, useId, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { FormField } from '@/design-system/patterns/FormField';
import { useInviteAgent } from '@/hooks/threads';
import { RecipientsInput } from './RecipientsInput';
import { useTranslation } from '@/hooks/i18n';
import { classifyThreadError, renderThreadError, type ThreadErrorView } from '@/lib/threadErrors';
import type { AgentSummary } from '@/lib/api/types';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
  agents?: AgentSummary[];
}

export function InviteDialog({ threadId, open, onClose, agents = [] }: Props): JSX.Element {
  const { t } = useTranslation();
  const invite = useInviteAgent(threadId);
  const [recipientsRaw, setRecipientsRaw] = useState('');
  // Locale-neutral error descriptor rendered at render time (see ArchiveDialog).
  const [errorView, setErrorView] = useState<ThreadErrorView | null>(null);
  const nameId = useId();

  useEffect(() => {
    if (!open) return;
    setRecipientsRaw('');
    setErrorView(null);
  }, [open]);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErrorView(null);
    // Parse comma-separated tokens, trim, discard empties, deduplicate
    // preserving selection order — RecipientsInput builds comma-separated
    // selected names, and the backend POST /threads/{id}/invite accepts
    // one { agent_name } request per participant. Submit every selected
    // name through that unchanged single-agent API sequentially.
    const names = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (names.length === 0) {
      setErrorView({ detail: { kind: 'mapped', key: 'threads.dialog.invite.required' } });
      return;
    }
    const uniqueNames = [...new Set(names)];
    try {
      // Sequential awaits — deterministic order, matches the non-batch
      // server contract one-agent-per-request.
      for (const name of uniqueNames) {
        await invite.mutateAsync({ agent_name: name });
      }
      onClose();
    } catch (err) {
      // Honest partial failure: any succeeding invite mutated before the
      // failure landed — the dialog stays open so the user can retry or
      // close. useInviteAgent onSuccess invalidates ['thread', slug, threadId]
      // on every individual success, so successful invites are reflected.
      setErrorView({ detail: classifyThreadError(err) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('threads.dialog.invite.title')}</DialogTitle>
          <DialogDescription className="sr-only">
            {t('threads.dialog.invite.description')}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <FormField label={t('threads.dialog.invite.agentLabel')} htmlFor={nameId}>
            <RecipientsInput
              id={nameId}
              value={recipientsRaw}
              onChange={setRecipientsRaw}
              agents={agents}
              placeholder="agent_a, agent_b"
            />
            {/* FormField's error node, gated on the descriptor so an empty raw
                diagnostic still renders byte-for-byte. */}
            {errorView !== null && (
              <p className="text-caption text-feedback-danger" role="alert">
                {renderThreadError(errorView, t)}
              </p>
            )}
          </FormField>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={invite.isPending}>
              {invite.isPending ? t('threads.dialog.invite.pending') : t('threads.dialog.invite.confirm')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
