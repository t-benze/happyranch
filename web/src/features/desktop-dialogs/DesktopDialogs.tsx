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
 *   on every switch, and returns to the invoker on close. A Main link that
 *   opened Settings (e.g. the Sidebar Settings link) is the invoker while it
 *   is still in the document. Without one (cold deep link, native entry, a
 *   removed invoker) focus returns to the Main recovery control when one is
 *   shown, otherwise to the Main content landmark.
 * - A same-document link inside a dialog pane (e.g. Executors > Agents)
 *   goes through the same close request with that destination: the
 *   dialog closes, Main shows the destination and gets focus.
 * - Native `app.dialogRequested` (bridge spec "v2 dialog request event")
 *   opens the named dialog through the same open request, with no invoker.
 *   Invalid payloads are ignored.
 *
 * - THR-387 S6-W2a: every close request, dialog switch and Settings page
 *   change asks the contextual owner gate (`@/shared/owners`) first, with the
 *   owners it would hide: close / Open Connection → every Settings owner; a
 *   page change → the owners of the page being left; an in-pane link →
 *   Settings owners plus Main owners when Main's page changes. Main
 *   navigation to a Settings route while some owner is not clean opens the
 *   dialog in place (Main is not unmounted), so a Main draft is not lost.
 *
 * The internal API is `useDesktopDialogs()` →
 * `{ openDesktopDialog, requestDesktopDialogClose }`.
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
  dialogLinkTarget,
  isSettingsPath,
  nativeDialogRequest,
  parseDialogRequested,
  settingsRequestFromPath,
  type DesktopDialogRequest,
  type DesktopDialogState,
  type DesktopSettingsSection,
} from './dialogModel';
import { isDesktopShell } from './desktopShell';
import { features } from '@/config/features';
import { orgOfPath, useOwnerGate, type OwnerGateEffect } from '@/shared/owners';
import type { OwnerRecord } from '@/lib/owners/ownerRegistry';

/** Optional Settings pages this build shows (build-time UI flags, THR-390 PR6). */
const ENABLED_OPTIONAL_SECTIONS: readonly DesktopSettingsSection[] = features.task_attributes_ui ? ['task-attributes'] : [];

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
   * The ONE close request (close button, Escape, an in-pane link, future
   * native close). With `navigateTo`, Main then shows that in-app path and
   * gets focus. The contextual owner gate decides first (S6-W2a): a kept
   * owner cancels both the close and the navigation.
   */
  requestDesktopDialogClose: (options?: { navigateTo?: string }) => void;
}

interface DesktopDialogsContextValue extends DesktopDialogsApi {
  /** Last Main location outside Settings (background for a Settings route). */
  lastMainLocation: RefObject<Location | null>;
  /**
   * The Main link the user just activated to reach `pathname`, if it is still
   * in the document; null for a deep link or any other entry. One-shot.
   */
  takeLinkInvoker: (pathname: string) => HTMLElement | null;
}

/** `window.happyranchConnection` as far as this host uses it (`on` only). */
interface DialogEventFacade {
  on?: (event: string, handler: (data: string) => void) => unknown;
}

/** What the host gives the Settings dialog body it is composed with. */
export interface DesktopSettingsBodySlot {
  headingRef: RefObject<HTMLHeadingElement>;
  section: DesktopSettingsSection;
  onSelectSection: (section: DesktopSettingsSection) => void;
  onOpenConnection: () => void;
}

/** What the host gives the Connection dialog body it is composed with (S6-W3). */
export interface DesktopConnectionBodySlot {
  headingRef: RefObject<HTMLHeadingElement>;
  onOpenSettings: () => void;
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
  renderConnectionBody,
}: {
  children: ReactNode;
  /** The Settings dialog body (features/settings), composed by routes.tsx. */
  renderSettingsBody: (slot: DesktopSettingsBodySlot) => ReactNode;
  /** The Connection dialog body (features/connection), composed by routes.tsx. */
  renderConnectionBody: (slot: DesktopConnectionBodySlot) => ReactNode;
}): JSX.Element {
  const [state, setState] = useState<DesktopDialogState>(CLOSED);
  const returnTarget = useRef<HTMLElement | null>(null);
  const focusMainOnClose = useRef(false);
  const isOpen = useRef(false);
  const lastLinkActivation = useRef<HTMLAnchorElement | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const lastMainLocation = useRef<Location | null>(null);

  useEffect(() => {
    if (!isSettingsPath(location.pathname)) lastMainLocation.current = location;
  }, [location]);

  // Remember the Main link a click (mouse or keyboard) activates, so a
  // Settings route it leads to can return focus to it. Capture phase runs
  // before the router handles the click.
  useEffect(() => {
    const onClick = (event: MouseEvent): void => {
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
      lastLinkActivation.current =
        link instanceof HTMLAnchorElement && !link.closest('[data-desktop-dialog]') ? link : null;
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  const takeLinkInvoker = useCallback((pathname: string): HTMLElement | null => {
    const link = lastLinkActivation.current;
    lastLinkActivation.current = null;
    if (!link || !link.isConnected) return null;
    try {
      return new URL(link.href).pathname === pathname ? link : null;
    } catch {
      return null;
    }
  }, []);

  const { requestOwnerTransition, setNavigationInterceptor } = useOwnerGate();
  const stateRef = useRef<DesktopDialogState>(CLOSED);
  stateRef.current = state;

  const openDesktopDialog = useCallback(
    (request: DesktopDialogRequest, invoker?: HTMLElement | null) => {
      const current = stateRef.current;
      const next = applyOpenRequest(current, request);
      const run = (): void => {
        if (!isOpen.current) returnTarget.current = invoker ?? null;
        isOpen.current = true;
        stateRef.current = next;
        setState(next);
      };
      // Owners this switch would hide: all of Settings for Connection, the
      // page being left for a page change.
      let affects: ((owner: OwnerRecord) => boolean) | null = null;
      let effect: OwnerGateEffect = 'changePage';
      if (current.dialog === 'settings' && next.dialog === 'connection') {
        affects = (owner) => owner.scope === 'settings';
        effect = 'openConnection';
      } else if (current.dialog === 'settings' && next.dialog === 'settings' && next.section !== current.section) {
        const left = current.section;
        affects = (owner) => owner.scope === 'settings' && owner.section === left;
      }
      if (affects === null) run();
      else requestOwnerTransition({ effect, affects, proceed: run });
    },
    [requestOwnerTransition],
  );

  const requestDesktopDialogClose = useCallback(
    (options?: { navigateTo?: string }) => {
      const navigateTo = options?.navigateTo;
      let effect: OwnerGateEffect = 'close';
      let mainAffected = false;
      if (navigateTo !== undefined) {
        const target = new URL(navigateTo, window.location.href);
        const orgChange = orgOfPath(target.pathname) !== orgOfPath(window.location.pathname);
        mainAffected =
          target.pathname !== window.location.pathname || target.search !== window.location.search;
        effect = orgChange ? 'changeOrg' : 'leave';
      }
      requestOwnerTransition({
        effect,
        affects: (owner) => owner.scope === 'settings' || (mainAffected && owner.scope === 'main'),
        proceed: () => {
          isOpen.current = false;
          focusMainOnClose.current = navigateTo !== undefined;
          stateRef.current = CLOSED;
          setState(CLOSED);
          if (navigateTo !== undefined) navigate(navigateTo);
        },
      });
    },
    [navigate, requestOwnerTransition],
  );

  // While an owner is not clean, Main navigation to a Settings route opens
  // the dialog in place instead of routing (which would unmount Main's page
  // and lose its draft). Only same-org requests: another org goes through the
  // org-change gate as a normal navigation.
  useEffect(() => {
    if (!isDesktopShell()) return;
    setNavigationInterceptor((next) => {
      const request = settingsRequestFromPath(next.pathname, ENABLED_OPTIONAL_SECTIONS);
      if (!request) return false;
      const org = orgOfPath(next.pathname);
      if (org !== null && org !== orgOfPath(window.location.pathname)) return false;
      openDesktopDialog(request, takeLinkInvoker(next.pathname));
      return true;
    });
    return () => setNavigationInterceptor(null);
  }, [setNavigationInterceptor, openDesktopDialog, takeLinkInvoker]);

  // Native entry (bridge spec "v2 dialog request event"): exactly
  // {"schema":2,"dialog":"settings"|"connection"}; anything else is ignored.
  // No invoker: focus returns to the Main fallback. A shell without this
  // event (on() throws or is absent) simply has no native entry.
  useEffect(() => {
    if (!isDesktopShell()) return;
    const facade = (window as { happyranchConnection?: DialogEventFacade }).happyranchConnection;
    if (typeof facade?.on !== 'function') return;
    let unsubscribe: unknown;
    try {
      unsubscribe = facade.on('app.dialogRequested', (data) => {
        const dialog = parseDialogRequested(data);
        if (!dialog) return;
        openDesktopDialog(nativeDialogRequest(dialog, window.location.pathname), null);
      });
    } catch {
      return;
    }
    return () => {
      if (typeof unsubscribe === 'function') {
        try {
          (unsubscribe as () => void)();
        } catch {
          // Teardown of an already retired subscription.
        }
      }
    };
  }, [openDesktopDialog]);

  const value = useMemo(
    () => ({ openDesktopDialog, requestDesktopDialogClose, lastMainLocation, takeLinkInvoker }),
    [openDesktopDialog, requestDesktopDialogClose, takeLinkInvoker],
  );

  return (
    <DesktopDialogsContext.Provider value={value}>
      {children}
      <DesktopDialogHost
        state={state}
        returnTarget={returnTarget}
        focusMainOnClose={focusMainOnClose}
        onSwitch={openDesktopDialog}
        onCloseRequest={requestDesktopDialogClose}
        renderSettingsBody={renderSettingsBody}
        renderConnectionBody={renderConnectionBody}
      />
    </DesktopDialogsContext.Provider>
  );
}

function DesktopDialogHost({
  state,
  returnTarget,
  focusMainOnClose,
  onSwitch,
  onCloseRequest,
  renderSettingsBody,
  renderConnectionBody,
}: {
  renderSettingsBody: (slot: DesktopSettingsBodySlot) => ReactNode;
  renderConnectionBody: (slot: DesktopConnectionBodySlot) => ReactNode;
  state: DesktopDialogState;
  returnTarget: RefObject<HTMLElement | null>;
  focusMainOnClose: { current: boolean };
  onSwitch: (request: DesktopDialogRequest) => void;
  onCloseRequest: (options?: { navigateTo?: string }) => void;
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
          // After an in-pane link, Main shows a new page: focus Main itself.
          const toMain = focusMainOnClose.current;
          focusMainOnClose.current = false;
          restoreFocus(toMain ? null : returnTarget.current);
        }}
        onClickCapture={(event) => {
          // In-pane links (Executors > Agents, Organization >
          // Work Hours, ...) leave Settings for a Main page: route them
          // through the one close request instead of navigating behind the
          // modal. Runs before the router's own link handler.
          const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
          if (!(link instanceof HTMLAnchorElement)) return;
          const path = dialogLinkTarget(
            { href: link.href, target: link.target, hasDownload: link.hasAttribute('download') },
            {
              button: event.button,
              modified: event.metaKey || event.ctrlKey || event.shiftKey || event.altKey,
            },
            window.location,
          );
          if (path === null) return;
          event.preventDefault();
          onCloseRequest({ navigateTo: path });
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
        {state.dialog === 'connection' &&
          renderConnectionBody({
            headingRef,
            onOpenSettings: () => onSwitch({ dialog: 'settings' }),
          })}
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
  const { openDesktopDialog, lastMainLocation, takeLinkInvoker } = context;
  const slug = orgMatch?.params.slug;

  useEffect(() => {
    const request = settingsRequestFromPath(location.pathname, ENABLED_OPTIONAL_SECTIONS);
    // The Main link that led here (Sidebar Settings) is the invoker; a cold
    // deep link has none and uses the Main fallback on close.
    if (request) openDesktopDialog(request, takeLinkInvoker(location.pathname));
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
