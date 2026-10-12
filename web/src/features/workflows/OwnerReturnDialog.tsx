import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
export function OwnerReturnDialog({ open, busy, disabled, onClose, onConfirm, onReturnFocus }: {
  open: boolean; busy: boolean; disabled: boolean; onClose: () => void; onConfirm: () => void; onReturnFocus: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  return <Dialog open={open} onOpenChange={value => { if (!value && !busy) onClose(); }}>
    <DialogContent closeLabel={t('common.close')} onCloseAutoFocus={event => { event.preventDefault(); onReturnFocus(); }}>
      <DialogHeader><DialogTitle>{t('workflows.returnTitle')}</DialogTitle>
        <DialogDescription>{t('workflows.returnBody')}</DialogDescription></DialogHeader>
      <DialogFooter className="gap-2"><Button variant="outline" disabled={busy} onClick={onClose}>{t('workflows.cancel')}</Button>
        <Button disabled={disabled || busy} onClick={onConfirm}>{t(busy ? 'workflows.sending' : 'workflows.return')}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
