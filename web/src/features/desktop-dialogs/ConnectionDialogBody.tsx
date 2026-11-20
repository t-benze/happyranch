/**
 * Connection dialog — placeholder shell (THR-387 S6-W1).
 *
 * S6-W3 moves the existing `web/connection` screens in here. Until then this
 * names the destination, keeps the cross-action to Settings and claims
 * nothing about connection state.
 */
import { Settings } from 'lucide-react';
import type { RefObject } from 'react';
import { DialogTitle } from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';

export function ConnectionDialogBody({
  headingRef,
  onOpenSettings,
}: {
  headingRef: RefObject<HTMLHeadingElement>;
  onOpenSettings: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-border-default shrink-0 border-b py-3 pr-14 pl-4 md:pl-6">
        <DialogTitle
          ref={headingRef}
          tabIndex={-1}
          className="font-display focus-visible:ring-accent-default w-fit max-w-full rounded-sm text-lg break-words focus:outline-none focus-visible:ring-2"
        >
          {t('desktopDialogs.connection.title')}
        </DialogTitle>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <p className="text-text-secondary max-w-prose text-sm break-words">
          {t('desktopDialogs.connection.placeholder')}
        </p>
        <Button
          type="button"
          variant="outline"
          className="mt-4 h-auto whitespace-normal"
          onClick={onOpenSettings}
        >
          <Settings aria-hidden="true" />
          {t('desktopDialogs.connection.openSettings')}
        </Button>
      </div>
    </div>
  );
}
