/**
 * ConfirmDialog — reusable confirmation surface for pause/cancel.
 * Uses the existing Dialog primitive from the design system.
 *
 * v1 has no Resume endpoint — the dialog text must never promise or
 * imply a resume path.
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/design-system/primitives/Dialog'
import { Button } from '@/design-system/primitives/Button'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  confirmVariant?: 'default' | 'destructive'
  loading?: boolean
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  confirmVariant = 'default',
  loading = false,
  onConfirm,
}: ConfirmDialogProps): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
        <div className="mt-5 flex justify-end gap-3">
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={loading}>
            {loading ? '…' : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
