import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
export function ReviewResponseDialog({ open, outcome, revision, findings, busy, disabled, onClose, onConfirm, onReturnFocus }: {
  open: boolean; outcome: 'approved' | 'changes_requested'; revision: number; findings: string;
  busy: boolean; disabled: boolean; onClose: () => void; onConfirm: () => void; onReturnFocus: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  return <Dialog open={open} onOpenChange={value => { if (!value && !busy) onClose(); }}>
    <DialogContent closeLabel={t('common.close')} onCloseAutoFocus={event => { event.preventDefault(); onReturnFocus(); }}>
      <DialogHeader><DialogTitle>{t('workflows.confirmResponse')}</DialogTitle>
        <DialogDescription>{t('workflows.confirmBody', { revision })}</DialogDescription></DialogHeader>
      <p>{t(outcome === 'approved' ? 'workflows.sign' : 'workflows.changes')}</p>
      <pre className="border-border bg-bg-raised max-h-64 overflow-y-auto rounded border p-3 whitespace-pre-wrap wrap-anywhere">{findings}</pre>
      <DialogFooter className="gap-2"><Button variant="outline" disabled={busy} onClick={onClose}>{t('workflows.cancel')}</Button>
        <Button disabled={disabled || busy} onClick={onConfirm}>{t(busy ? 'workflows.sending' : 'workflows.confirm')}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
