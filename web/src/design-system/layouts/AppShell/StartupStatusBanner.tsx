/**
 * Daemon startup status banner (THR-308).
 *
 * Rendered by the app shell above the routed content whenever startup is not
 * positively ready. It reads the shared startup observation only; it never
 * fetches. Copy is translated; the failed state shows the daemon's stable
 * error code verbatim (no raw cause, path or progress guess).
 */
import { useStartupStatus } from '@/hooks/health';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { StartupPhase } from '@/lib/startup';

const MESSAGE: Record<Exclude<StartupPhase, 'ready'>, MessageKey> = {
  checking: 'startup.banner.checking',
  recovering: 'startup.banner.recovering',
  failed: 'startup.banner.failed',
  stopping: 'startup.banner.stopping',
  unknown: 'startup.banner.unknown',
};

export function StartupStatusBanner(): JSX.Element | null {
  const { phase, errorCode } = useStartupStatus();
  const { t, render } = useTranslation();
  if (phase === 'ready') return null;
  const failed = phase === 'failed';
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={t('startup.banner.aria')}
      className={`border-border-default text-caption shrink-0 border-b px-4 py-2 break-words ${
        failed ? 'bg-danger-soft text-feedback-danger' : 'bg-attention-soft text-attention-text'
      }`}
    >
      <p>{t(MESSAGE[phase])}</p>
      {failed && errorCode && (
        <p className="mt-1">
          {render('startup.banner.code', {
            code: <code className="font-mono">{errorCode}</code>,
          })}
        </p>
      )}
    </div>
  );
}
