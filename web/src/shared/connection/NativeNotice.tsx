/**
 * Non-blocking native request notice (THR-387 S5-W1; W-1).
 *
 * A slim status bar above the routed page: it never replaces or hides the
 * page, so drafts, focus and selection stay as they are. Busy/throttled clear
 * themselves on the next successful forwarded response; Not sent and Result
 * unknown stay until dismissed. Owner-specific Check result reconciliation is
 * S6-W2; this surface only names the outcome and never resends anything.
 */
import { CircleHelp, Hourglass, Send, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { useNativeNotice } from '@/lib/native/hooks';
import { dismissNativeNotice, type NativeNoticeKind } from '@/lib/native/notices';

const COPY: Record<NativeNoticeKind, { icon: ReactNode; title: MessageKey; body: MessageKey; dismissible: boolean }> = {
  busy: { icon: <Hourglass />, title: 'nativeNotice.busy.title', body: 'nativeNotice.busy.body', dismissible: false },
  throttled: { icon: <Hourglass />, title: 'nativeNotice.throttled.title', body: 'nativeNotice.throttled.body', dismissible: false },
  not_sent: { icon: <Send />, title: 'nativeNotice.notSent.title', body: 'nativeNotice.notSent.body', dismissible: true },
  result_unknown: { icon: <CircleHelp />, title: 'nativeNotice.resultUnknown.title', body: 'nativeNotice.resultUnknown.body', dismissible: true },
};

export function NativeNoticeBar({ kind, onDismiss }: { kind: NativeNoticeKind; onDismiss: () => void }): JSX.Element {
  const { t } = useTranslation();
  const copy = COPY[kind];
  return (
    <div
      role="status"
      aria-live="polite"
      data-native-notice={kind}
      className="border-feedback-warning/40 bg-feedback-warning/5 text-text-primary flex items-start gap-3 border-b px-4 py-2 text-sm"
    >
      <span aria-hidden="true" className="text-feedback-warning mt-0.5 shrink-0 [&>svg]:h-4 [&>svg]:w-4">
        {copy.icon}
      </span>
      <p className="min-w-0 flex-1 break-words">
        <span className="font-medium">{t(copy.title)}</span>{' '}
        <span className="text-text-secondary">{t(copy.body)}</span>
      </p>
      {copy.dismissible && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t('nativeNotice.dismiss')}
          className="text-text-secondary hover:text-text-primary focus-visible:ring-ring shrink-0 rounded-md p-0.5 focus-visible:ring-2 focus-visible:outline-none"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

/** Mounted once by the AppShell above the routed page. */
export function NativeNotice(): JSX.Element | null {
  const kind = useNativeNotice();
  if (!kind) return null;
  return <NativeNoticeBar kind={kind} onDismiss={dismissNativeNotice} />;
}
