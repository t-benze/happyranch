/**
 * Desktop window header (THR-387 seq146).
 *
 * In the desktop app the whole window is the web view: there is no native
 * header bar or toolbar. This web header replaces the GTK `MainHeader`
 * (Linux) and carries the same entries and copy:
 *
 * 1. Connection and Settings buttons. They open the EXISTING web dialogs
 *    through `useDesktopDialogs().openDesktopDialog`, so the single-dialog
 *    host and its contextual owner gate decide every transition (a Keep
 *    editing choice keeps the current presentation; that is not a failure).
 * 2. The target/status pill, derived only from native `app.status` /
 *    `app.stateChanged` (`useNativeAppStatus`), plus the candidate line while
 *    a connection attempt exists. No status → "Interface unavailable".
 * 3. The persistent "Access wasn't saved" warning while `access_unsaved`;
 *    it opens Connection.
 * 4. "<Connection|Settings> unavailable · Retry" after a failed open. Retry
 *    is an explicit user action only: nothing is ever replayed automatically.
 *
 * What "fails" means here (stated assumption, THR-387 seq146 brief): an open
 * request fails when the web could not hand it to the single-dialog host or
 * the host did not present anything for it. Concretely, either
 *   (a) `openDesktopDialog` throws (host missing/unmounted, or its transition
 *       handler errors), or
 *   (b) after the request is committed (next animation frame) the document
 *       shows neither the requested dialog (`[data-desktop-dialog=<name>]`)
 *       nor the owner gate (`[data-owner-gate]`, prompt or hold).
 * The owner gate asking the user (and the user choosing Keep editing) is a
 * decision, not unavailability, so it never shows Retry. A dialog whose own
 * data cannot load stays open and shows its own in-dialog error state; that
 * is not an open failure either.
 *
 * Rendered only behind the native v2 bridge (`isNativeTarget()`: document at
 * `happyranch-app://app` with the `happyranchConnection` facade). Browser
 * mode renders nothing, so it is unchanged. No bridge command is called.
 */
import { useCallback, useId, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Cable, Settings, TriangleAlert, RotateCw } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/design-system/primitives/Tooltip';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { appEntryName, type AppCandidate, type AppState } from '@/lib/native/appStatus';
import { useNativeAppStatus } from '@/lib/native/hooks';
import { isNativeTarget } from '@/lib/native/target';
import { useDesktopDialogs } from './DesktopDialogs';
import { nativeDialogRequest } from './dialogModel';

type HeaderDialog = 'connection' | 'settings';

const STATE_DETAIL: Record<Exclude<AppState['state'], 'incompatible'>, MessageKey> = {
  no_target: 'desktopHeader.state.noTarget',
  target_starting: 'desktopHeader.state.starting',
  ready: 'desktopHeader.state.ready',
  unreachable: 'desktopHeader.state.unreachable',
  busy: 'desktopHeader.state.busy',
};

const INCOMPATIBLE_DETAIL: Record<'host' | 'app' | 'unknown', MessageKey> = {
  host: 'desktopHeader.state.updateHost',
  app: 'desktopHeader.state.updateApp',
  unknown: 'desktopHeader.state.checkCompatibility',
};

const PHASE: Record<AppCandidate['phase'], MessageKey> = {
  signing_in: 'desktopHeader.candidate.signingIn',
  awaiting_approval: 'desktopHeader.candidate.awaitingApproval',
  ready_to_switch: 'desktopHeader.candidate.readyToSwitch',
  failed: 'desktopHeader.candidate.failed',
  cancelled: 'desktopHeader.candidate.cancelled',
};

const STATE_TONE: Record<AppState['state'], Tone> = {
  no_target: 'neutral',
  target_starting: 'info',
  ready: 'positive',
  unreachable: 'danger',
  incompatible: 'attention',
  busy: 'attention',
};

const RETRY_LABEL: Record<HeaderDialog, MessageKey> = {
  connection: 'desktopHeader.retry.connection',
  settings: 'desktopHeader.retry.settings',
};

/** Long labels wrap at 760x560 / 200% text instead of overflowing. */
const WRAP = 'h-auto min-h-8 max-w-full whitespace-normal break-words text-left';

/** True when the host presented the dialog or the owner gate for a request. */
function presented(dialog: HeaderDialog): boolean {
  return (
    document.querySelector(`[data-desktop-dialog="${dialog}"]`) !== null ||
    document.querySelector('[data-owner-gate]') !== null
  );
}

export function DesktopHeader(): JSX.Element | null {
  if (!isNativeTarget()) return null;
  return <DesktopHeaderBar />;
}

function DesktopHeaderBar(): JSX.Element {
  const { t } = useTranslation();
  const status = useNativeAppStatus();
  const { openDesktopDialog } = useDesktopDialogs();
  const location = useLocation();
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;
  const [failed, setFailed] = useState<HeaderDialog | null>(null);
  const warningDescriptionId = useId();

  const open = useCallback(
    (dialog: HeaderDialog, invoker: HTMLElement | null) => {
      try {
        openDesktopDialog(nativeDialogRequest(dialog, pathRef.current), invoker);
      } catch {
        setFailed(dialog);
        return;
      }
      // Check once, after React commits the request (see "fails" above).
      requestAnimationFrame(() => setFailed(presented(dialog) ? null : dialog));
    },
    [openDesktopDialog],
  );

  const localName = t('desktopHeader.thisComputer');
  let pillText: string;
  let tone: Tone = 'neutral';
  let candidateText: string | null = null;
  if (status === null) {
    pillText = t('desktopHeader.interfaceUnavailable');
  } else {
    const detail =
      status.state === 'incompatible'
        ? t(INCOMPATIBLE_DETAIL[status.incompatible?.side ?? 'unknown'])
        : t(STATE_DETAIL[status.state]);
    const name = status.target ? appEntryName(status.target, localName) : '';
    pillText = name ? `${name} · ${detail}` : detail;
    tone = STATE_TONE[status.state];
    if (status.candidate) {
      candidateText = `${appEntryName(status.candidate, localName)} · ${t(PHASE[status.candidate.phase])}`;
    }
  }

  return (
    <header
      data-desktop-header=""
      aria-label={t('desktopHeader.label')}
      // Wrapped status/candidate copy must leave room for Main navigation at
      // 760×560 / 200% text. All header content stays reachable by scrolling.
      className="border-border-default bg-surface-canvas flex max-h-1/4 shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 overflow-y-auto border-b px-3 py-1.5"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          className={WRAP}
          data-desktop-header-open="connection"
          onClick={(event) => open('connection', event.currentTarget)}
        >
          <Cable aria-hidden="true" />
          {t('desktopHeader.connection')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className={WRAP}
          data-desktop-header-open="settings"
          onClick={(event) => open('settings', event.currentTarget)}
        >
          <Settings aria-hidden="true" />
          {t('desktopHeader.settings')}
        </Button>
      </div>
      <div
        role="status"
        aria-live="polite"
        data-desktop-header-status={status?.state ?? 'unavailable'}
        className="flex min-w-0 grow basis-48 flex-col items-start gap-0.5"
      >
        <span
          className={`${TONE_CLASS[tone]} text-caption max-w-full rounded-full px-2.5 py-0.5 font-semibold break-words`}
        >
          {pillText}
        </span>
        {candidateText !== null && (
          <span data-desktop-header-candidate="" className="text-text-secondary text-caption max-w-full px-2.5 break-words">
            {candidateText}
          </span>
        )}
      </div>
      {(status?.access_unsaved === true || failed !== null) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {status?.access_unsaved === true && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className={`${WRAP} text-attention-text`}
                  data-desktop-header-access-unsaved=""
                  aria-describedby={warningDescriptionId}
                  onClick={(event) => open('connection', event.currentTarget)}
                >
                  <TriangleAlert aria-hidden="true" />
                  {t('desktopHeader.accessUnsaved')}
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">{t('desktopHeader.accessUnsaved.description')}</TooltipContent>
            </Tooltip>
          )}
          {status?.access_unsaved === true && (
            <span id={warningDescriptionId} className="sr-only">
              {t('desktopHeader.accessUnsaved.description')}
            </span>
          )}
          {failed !== null && (
            <Button
              variant="outline"
              size="sm"
              className={WRAP}
              data-desktop-header-retry={failed}
              onClick={(event) => open(failed, event.currentTarget)}
            >
              <RotateCw aria-hidden="true" />
              {t(RETRY_LABEL[failed])}
            </Button>
          )}
        </div>
      )}
    </header>
  );
}
