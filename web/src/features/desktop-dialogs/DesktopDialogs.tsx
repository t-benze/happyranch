/**
 * Desktop dialog host (THR-387 S6-W1; handoff v2.4 §2-§3).
 *
 * In the desktop app, Settings and Connection are web dialogs inside Main,
 * in this same document. The host shows ONE top-level dialog at a time:
 * opening the other replaces it (same Radix root, no new window).
 *
 * - The close button and Escape both send the same close request,
 *   `requestDesktopDialogClose`. Backdrop clicks do not dismiss.
 * - Focus is contained (Radix modal), moves to the dialog heading on open and
 *   on every switch, and returns to the invoker on close. Without an invoker
 *   (route/deep link, future native entry) it returns to the Main recovery
 *   control when one is shown, otherwise to the Main content landmark.
 *
 * The internal API is `useDesktopDialogs()` →
 * `{ openDesktopDialog, requestDesktopDialogClose }`. Native
 * `app.dialogRequested` is NOT wired here: its protocol (S5-0b) was not
 * merged when this landed, so that listener is a follow-up.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { useLocation, useNavigate, useMatch, type Location } from 'react-router-dom';
import { Dialog, DialogContent } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
import {
  CLOSED,
  applyOpenRequest,
  isSettingsPath,
  settingsRequestFromPath,
  type DesktopDialogRequest,
  type DesktopDialogState,
  type DesktopSettingsSection,
} from './dialogModel';
import { ConnectionDialogBody } from './ConnectionDialogBody';

/** Id of the Main content landmark: the focus-return fallback. */
export const MAIN_CONTENT_ID = 'happyranch-main-content';

export interface DesktopDialogsApi {
  /**
   * Open or switch to a dialog. `invoker` is where focus returns on the final
   * close; it is recorded only when no dialog is open yet, so a switch keeps
   * the original return destination.
   */
  openDesktopDialog: (request: DesktopDialogRequest, invoker?: HTMLElement | null) => void;
  /**
   * The ONE close request (close button, Escape, future native close).
   * S6-W2 seam: the owner registry / keep-discard gate decides here, before
   * the dialog is closed. Today it closes immediately.
   */
  requestDesktopDialogClose: () => void;
}

interface DesktopDialogsContextValue extends DesktopDialogsApi {
  /** Last Main location outside Settings (background for a Settings route). */
  lastMainLocation: RefObject<Location | null>;
}

/** What the host gives the Settings dialog body it is composed with. */
export interface DesktopSettingsBodySlot {
  headingRef: RefObject<HTMLHeadingElement>;
  section: DesktopSettingsSection;
  onSelectSection: (section: DesktopSettingsSection) => void;
  onOpenConnection: () => void;
}

const DesktopDialogsContext = createContext<DesktopDialogsContextValue | null>(null);

export function useDesktopDialogs(): DesktopDialogsApi {
  const value = useContext(DesktopDialogsContext);
  if (!value) throw new Error('useDesktopDialogs must be used inside <DesktopDialogsProvider>');
  return value;
}

function restoreFocus(target: HTMLElement | null): void {
  if (target && target.isConnected) {
    target.focus();
    return;
  }
  const recoveryControl = document.querySelector<HTMLElement>('[data-native-recovery] button');
  if (recoveryControl) {
    recoveryControl.focus();
    return;
  }
  document.getElementById(MAIN_CONTENT_ID)?.focus();
}

export function DesktopDialogsProvider({
  children,
  renderSettingsBody,
}: {
  children: ReactNode;
  /** The Settings dialog body (features/settings), composed by routes.tsx. */
  renderSettingsBody: (slot: DesktopSettingsBodySlot) => ReactNode;
}): JSX.Element {
  const [state, setState] = useState<DesktopDialogState>(CLOSED);
  const returnTarget = useRef<HTMLElement | null>(null);
  const isOpen = useRef(false);
  const location = useLocation();
  const lastMainLocation = useRef<Location | null>(null);

  useEffect(() => {
    if (!isSettingsPath(location.pathname)) lastMainLocation.current = location;
  }, [location]);

  const openDesktopDialog = useCallback(
    (request: DesktopDialogRequest, invoker?: HTMLElement | null) => {
      if (!isOpen.current) returnTarget.current = invoker ?? null;
      isOpen.current = true;
      setState((current) => applyOpenRequest(current, request));
    },
    [],
  );

  const requestDesktopDialogClose = useCallback(() => {
    // S6-W2 seam: consult affected owners here before closing.
    isOpen.current = false;
    setState(CLOSED);
  }, []);

  const value = useMemo(
    () => ({ openDesktopDialog, requestDesktopDialogClose, lastMainLocation }),
    [openDesktopDialog, requestDesktopDialogClose],
  );

  return (
    <DesktopDialogsContext.Provider value={value}>
      {children}
      <DesktopDialogHost
        state={state}
        returnTarget={returnTarget}
        onSwitch={openDesktopDialog}
        onCloseRequest={requestDesktopDialogClose}
        renderSettingsBody={renderSettingsBody}
      />
    </DesktopDialogsContext.Provider>
  );
}

function DesktopDialogHost({
  state,
  returnTarget,
  onSwitch,
  onCloseRequest,
  renderSettingsBody,
}: {
  renderSettingsBody: (slot: DesktopSettingsBodySlot) => ReactNode;
  state: DesktopDialogState;
  returnTarget: RefObject<HTMLElement | null>;
  onSwitch: (request: DesktopDialogRequest) => void;
  onCloseRequest: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const shown = useRef<DesktopDialogState['dialog']>(null);

  // Switching keeps the same Radix root mounted, so its open auto-focus does
  // not run again: move focus to the destination heading here.
  useEffect(() => {
    const previous = shown.current;
    shown.current = state.dialog;
    if (previous !== null && state.dialog !== null && previous !== state.dialog) {
      headingRef.current?.focus();
    }
  }, [state.dialog]);

  return (
    <Dialog
      open={state.dialog !== null}
      onOpenChange={(open) => {
        if (!open) onCloseRequest();
      }}
    >
      <DialogContent
        data-desktop-dialog={state.dialog ?? undefined}
        closeLabel={t('desktopDialogs.close')}
        aria-describedby={undefined}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          headingRef.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          restoreFocus(returnTarget.current);
        }}
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        className={
          // Compact (below md, e.g. 760x560 at 200%): fill the whole Main
          // content area. md and wider: a large centered dialog.
          'inset-0 top-0 left-0 flex h-dvh max-h-dvh w-full max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-none p-0 ' +
          'md:inset-auto md:top-1/2 md:left-1/2 md:h-[min(46rem,calc(100dvh-4rem))] md:max-h-[calc(100dvh-4rem)] md:w-[calc(100%-4rem)] md:max-w-5xl md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-lg ' +
          '[&>button:last-child]:top-3.5 [&>button:last-child]:right-3.5 [&>button:last-child]:p-1.5 [&>button:last-child]:opacity-100'
        }
      >
        {state.dialog === 'settings' &&
          renderSettingsBody({
            headingRef,
            section: state.section,
            onSelectSection: (section) => onSwitch({ dialog: 'settings', section }),
            onOpenConnection: () => onSwitch({ dialog: 'connection' }),
          })}
        {state.dialog === 'connection' && (
          <ConnectionDialogBody
            headingRef={headingRef}
            onOpenSettings={() => onSwitch({ dialog: 'settings' })}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Desktop element for Main `/orgs/:slug/settings/*` and the org-less
 * recovery `/settings/*`: open the Settings dialog at exactly the requested
 * page, then put Main back on the page it showed before (or the org home /
 * app root for a cold deep link). Browser mode never renders this.
 */
export function DesktopSettingsRoute(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const orgMatch = useMatch('/orgs/:slug/*');
  const context = useContext(DesktopDialogsContext);
  if (!context) throw new Error('DesktopSettingsRoute must be used inside <DesktopDialogsProvider>');
  const { openDesktopDialog, lastMainLocation } = context;
  const slug = orgMatch?.params.slug;

  useEffect(() => {
    const request = settingsRequestFromPath(location.pathname);
    if (request) openDesktopDialog(request, null);
    // Keep the previous Main page only when it shows the same org, so the
    // dialog's runtime pages name the org the request asked for.
    const background = lastMainLocation.current;
    const sameOrg = !slug || background?.pathname.startsWith(`/orgs/${slug}/`) === true;
    if (background && sameOrg) navigate(background, { replace: true });
    else navigate(slug ? `/orgs/${slug}/dashboard` : '/', { replace: true });
    // Run once per Settings navigation (location.key changes per entry).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key]);

  return <></>;
}
