/**
 * Native recovery states and compatibility page (THR-387 S1b; handoff v2.4
 * §2 and §5).
 *
 * Rendered by the AppShell in place of the routed page while a native
 * recovery state is active. The routed page stays mounted (hidden), so drafts
 * and page state survive; the Sidebar and AppBar (including Language) stay
 * reachable. Settings fences only its target-backed sections, so Settings ->
 * Preferences stays usable. Relaunch is available only on the app-incompatible
 * needs-update screen when native explicitly advertises support.
 */
import { CircleHelp, CircleArrowUp, Hourglass, Loader, RefreshCw, Unplug, WifiOff } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useNativeAppStatus } from '@/lib/native/hooks';
import { nativeBridge } from '@/lib/native/target';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTranslation } from '@/hooks/i18n';
import { useNativeRecovery, useRetryNativeRecovery } from '@/hooks/nativeRecovery';
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
  const app = useNativeAppStatus();
  const [relaunching, setRelaunching] = useState(false);
  const [relaunchFailed, setRelaunchFailed] = useState(false);
  const canRelaunch = state.code === 'incompatible' && state.side === 'app'
    && app?.state === 'incompatible' && app.incompatible?.side === 'app' && app.app_relaunch === true;
  const relaunch = async (): Promise<void> => {
    const bridge = nativeBridge();
    if (!bridge || !canRelaunch || relaunching) return;
    setRelaunching(true);
    setRelaunchFailed(false);
    try {
      await bridge.call('app.relaunch', '{}');
    } catch {
      setRelaunchFailed(true);
    } finally {
      setRelaunching(false);
    }
  };
  return (
    <section
      role="status"
      aria-live="polite"
      aria-label={t(copy.title)}
      data-native-recovery={state.code}
      className="flex h-full flex-col overflow-auto"
    >
      {/* my-auto centers when there is room; when content is taller than the
          panel (short viewports / page zoom) it starts at the scroll origin, so
          the heading stays reachable and the action stays scrollable. */}
      <div className="my-auto">
        <EmptyState
          icon={copy.icon}
          title={t(copy.title)}
          body={
            <div className="flex flex-col gap-2">
              <p>{t(copy.body)}</p>
              {state.code === 'incompatible' && state.side === 'app' && !canRelaunch && <p>{t('recovery.incompatible.app.reopen')}</p>}
              {copy.otherComputer && <p>{t('recovery.incompatible.otherComputer')}</p>}
              <p>{t('recovery.kept')}</p>
              {canRelaunch && (
                <Button disabled={relaunching} className="whitespace-normal" onClick={() => { void relaunch(); }}>
                  {t('recovery.relaunch')}
                </Button>
              )}
              {canRelaunch && relaunchFailed && <p role="alert">{t('recovery.relaunchFailed')}</p>}
              <p className="text-xs break-words">{t('recovery.details', { code: diagnostic(state) })}</p>
            </div>
          }
          cta={{ label: t(copy.action), onClick: onRetry }}
        />
      </div>
    </section>
  );
}

/**
 * Fence runtime content that needs the target: while a recovery state is
 * active, show the panel and keep `children` mounted but hidden (drafts and
 * page state survive). Used by the AppShell for routed pages and by Settings
 * for its target-backed sections, so local Settings (Preferences) stay usable.
 */
export function NativeRecoveryFence({ children, bypass = false }: { children: ReactNode; bypass?: boolean }): JSX.Element {
  const observed = useNativeRecovery();
  const retry = useRetryNativeRecovery();
  // `bypass` keeps the same element tree (no remount) when an inner owner
  // fences instead (Settings).
  const recovery = bypass ? null : observed;
  return (
    <>
      {recovery && <NativeRecoveryPanel state={recovery} onRetry={retry} />}
      <div hidden={recovery !== null} className="contents">
        {children}
      </div>
    </>
  );
}
