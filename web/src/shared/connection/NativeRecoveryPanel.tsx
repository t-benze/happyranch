/**
 * Native recovery states and compatibility page (THR-387 S1b; handoff v2.4
 * §2 and §5).
 *
 * Rendered by the AppShell in place of the routed page while a native
 * recovery state is active. The routed page stays mounted (hidden), so drafts
 * and page state survive; the Sidebar and AppBar (including Language) stay
 * reachable. The Connection/Settings dialogs are S6 and do not exist yet, so
 * this panel names Connection in copy but offers only Retry/Recheck.
 */
import { CircleHelp, CircleArrowUp, Hourglass, Loader, RefreshCw, Unplug, WifiOff } from 'lucide-react';
import type { ReactNode } from 'react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { NativeRecoveryState } from '@/lib/nativeRecovery';

interface PanelCopy {
  icon: ReactNode;
  title: MessageKey;
  body: MessageKey;
  action: MessageKey;
  otherComputer: boolean;
}

function copyFor(state: NativeRecoveryState): PanelCopy {
  switch (state.code) {
    case 'no_target':
      return { icon: <Unplug />, title: 'recovery.noTarget.title', body: 'recovery.noTarget.body', action: 'recovery.retry', otherComputer: false };
    case 'target_starting':
      return { icon: <Loader />, title: 'recovery.targetStarting.title', body: 'recovery.targetStarting.body', action: 'recovery.retry', otherComputer: false };
    case 'target_unreachable':
      return { icon: <WifiOff />, title: 'recovery.targetUnreachable.title', body: 'recovery.targetUnreachable.body', action: 'recovery.retry', otherComputer: false };
    case 'busy':
      return { icon: <Hourglass />, title: 'recovery.busy.title', body: 'recovery.busy.body', action: 'recovery.retry', otherComputer: false };
    case 'target_changed':
      return { icon: <RefreshCw />, title: 'recovery.targetChanged.title', body: 'recovery.targetChanged.body', action: 'recovery.retry', otherComputer: false };
    case 'incompatible':
      if (state.side === 'host') {
        return { icon: <CircleArrowUp />, title: 'recovery.incompatible.host.title', body: 'recovery.incompatible.host.body', action: 'recovery.recheck', otherComputer: true };
      }
      if (state.side === 'app') {
        return { icon: <CircleArrowUp />, title: 'recovery.incompatible.app.title', body: 'recovery.incompatible.app.body', action: 'recovery.recheck', otherComputer: true };
      }
      // Unknown side: compatibility unavailable — no guessed component to update.
      return { icon: <CircleHelp />, title: 'recovery.incompatible.unknown.title', body: 'recovery.incompatible.unknown.body', action: 'recovery.recheck', otherComputer: false };
  }
}

/** Machine diagnostic, kept verbatim (not translated). */
function diagnostic(state: NativeRecoveryState): string {
  return state.code === 'incompatible' ? `incompatible · side=${state.side}` : state.code;
}

export interface NativeRecoveryPanelProps {
  state: NativeRecoveryState;
  onRetry: () => void;
}

export function NativeRecoveryPanel({ state, onRetry }: NativeRecoveryPanelProps): JSX.Element {
  const { t } = useTranslation();
  const copy = copyFor(state);
  return (
    <section
      role="status"
      aria-live="polite"
      aria-label={t(copy.title)}
      data-native-recovery={state.code}
      className="h-full overflow-auto"
    >
      <EmptyState
        icon={copy.icon}
        title={t(copy.title)}
        body={
          <div className="flex flex-col gap-2">
            <p>{t(copy.body)}</p>
            {copy.otherComputer && <p>{t('recovery.incompatible.otherComputer')}</p>}
            <p>{t('recovery.kept')}</p>
            <p className="text-xs break-words">{t('recovery.details', { code: diagnostic(state) })}</p>
          </div>
        }
        cta={{ label: t(copy.action), onClick: onRetry }}
      />
    </section>
  );
}
