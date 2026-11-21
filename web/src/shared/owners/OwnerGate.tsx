/**
 * Contextual owner gate (THR-387 S6-W2a; handoff v2.4 §3, C1/C2).
 *
 * `OwnerGateProvider` owns the app document's ONE owner registry
 * (`@/lib/owners/ownerRegistry`) and the ONE gate prompt. Every transition
 * that can hide or retire an editor asks the gate first:
 * - the desktop dialog close request (`requestDesktopDialogClose`, incl.
 *   in-pane links), a dialog switch and a Settings page change
 *   (`features/desktop-dialogs` calls `requestOwnerTransition`);
 * - in-app navigation (a router blocker, mounted only while some owner is
 *   not clean), including org switches;
 * - S6-W2b adds native close/Quit/target commit via `selectOwnerBlocking`.
 *
 * Gate outcome for the AFFECTED owners only (an editor the transition leaves
 * mounted is untouched; opening a dialog over a Main editor is not consent):
 * - none affected/clean → the transition runs at once;
 * - any pending/unknown → `hold`: a waiting panel names each owner; unknown
 *   owners offer Check result. Escape does nothing, there is no Discard and
 *   no close button; "Back to {page}" cancels the transition. When every
 *   result arrives the gate re-evaluates: clean → the transition runs;
 *   unsent edits left → the keep/discard prompt.
 * - else dirty/failed → ONE prompt naming every affected owner (Main and
 *   Settings together, page · computer · org), "Keep editing" (default focus,
 *   Escape, close button) or "Discard changes and <effect>". Discard rechecks
 *   all owners first and only discards owners with unsent edits.
 * While the gate is open a repeated request focuses it; no second prompt.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { useBlocker, type Location } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import {
  createOwnerRegistry,
  selectOwnerBlocking,
  sharedRouterGuardNeeded,
  type OwnerRecord,
  type OwnerRegistry,
} from '@/lib/owners/ownerRegistry';
import { navigationTransition, type OwnerGateEffect } from '@/lib/owners/ownerTransitions';
import { ownerContextParams } from './ownerCopy';

export { navigationTransition, orgOfPath, type OwnerGateEffect } from '@/lib/owners/ownerTransitions';

export interface OwnerTransition {
  effect: OwnerGateEffect;
  /** Owners this transition would hide or retire. */
  affects: (owner: OwnerRecord) => boolean;
  /** Run the transition (after consent, or at once when nothing blocks). */
  proceed: () => void;
  /** Called when the transition is cancelled (Keep editing / Back). */
  cancel?: () => void;
}

/** Editor-provided callbacks, keyed by owner id. */
export interface OwnerHandlers {
  /** Reset the editor's unsent draft after Discard consent. */
  discard?: () => void;
  /** Same-context reconciliation of an unknown result (Check result). */
  check?: () => Promise<void>;
}

/** Optional navigation interceptor (desktop: Settings routes open the dialog). */
export type NavigationInterceptor = (next: Location) => boolean;

interface OwnerGateContextValue {
  registry: OwnerRegistry;
  handlers: Map<string, OwnerHandlers>;
  /** Ask the gate; returns true when the transition ran immediately. */
  requestOwnerTransition: (transition: OwnerTransition) => boolean;
  setNavigationInterceptor: (interceptor: NavigationInterceptor | null) => void;
}

const OwnerGateContext = createContext<OwnerGateContextValue | null>(null);

export function useOwnerGateContext(): OwnerGateContextValue {
  const value = useContext(OwnerGateContext);
  if (!value) throw new Error('owner gate hooks must be used inside <OwnerGateProvider>');
  return value;
}

/** Live owner records (re-renders on every registry change). */
export function useOwnerRecords(): readonly OwnerRecord[] {
  const { registry } = useOwnerGateContext();
  return useSyncExternalStore(registry.subscribe, registry.getSnapshot, registry.getSnapshot);
}

/** The gate API for transition owners (dialog host, org switcher, ...). */
export function useOwnerGate(): Pick<OwnerGateContextValue, 'requestOwnerTransition' | 'setNavigationInterceptor'> {
  const { requestOwnerTransition, setNavigationInterceptor } = useOwnerGateContext();
  return { requestOwnerTransition, setNavigationInterceptor };
}

/** Long actions wrap at compact sizes / 200% text instead of overflowing. */
const WRAP = 'h-auto max-w-full whitespace-normal text-left break-words';

export function OwnerGateProvider({ children }: { children: ReactNode }): JSX.Element {
  const [registry] = useState(createOwnerRegistry);
  const handlers = useRef(new Map<string, OwnerHandlers>()).current;
  const [active, setActive] = useState<OwnerTransition | null>(null);
  const activeRef = useRef<OwnerTransition | null>(null);
  const gateRef = useRef<HTMLDivElement | null>(null);
  const interceptor = useRef<NavigationInterceptor | null>(null);

  const focusGate = useCallback(() => {
    const target = gateRef.current?.querySelector<HTMLElement>('[data-owner-gate-default]');
    target?.focus({ preventScroll: true });
  }, []);

  const requestOwnerTransition = useCallback(
    (transition: OwnerTransition): boolean => {
      if (activeRef.current) {
        // One gate at a time: a repeated request focuses the open gate.
        transition.cancel?.();
        focusGate();
        return false;
      }
      const blocking = selectOwnerBlocking(registry.getSnapshot(), transition.affects);
      if (blocking.level === 'none') {
        transition.proceed();
        return true;
      }
      activeRef.current = transition;
      setActive(transition);
      return false;
    },
    [registry, focusGate],
  );

  const finish = useCallback((run: boolean) => {
    const transition = activeRef.current;
    activeRef.current = null;
    setActive(null);
    if (!transition) return;
    if (run) transition.proceed();
    else transition.cancel?.();
  }, []);

  // Forget the callbacks of owners the registry dropped (detached owners
  // keep theirs until their save resolves).
  useEffect(
    () =>
      registry.subscribe(() => {
        for (const id of [...handlers.keys()]) if (!registry.has(id)) handlers.delete(id);
      }),
    [registry, handlers],
  );

  const setNavigationInterceptor = useCallback((next: NavigationInterceptor | null) => {
    interceptor.current = next;
  }, []);

  const value = useMemo(
    () => ({ registry, handlers, requestOwnerTransition, setNavigationInterceptor }),
    [registry, handlers, requestOwnerTransition, setNavigationInterceptor],
  );

  return (
    <OwnerGateContext.Provider value={value}>
      {children}
      <OwnerRouterGuard interceptor={interceptor} holding={active !== null} />
      {active && (
        <OwnerGatePrompt
          transition={active}
          registry={registry}
          handlers={handlers}
          gateRef={gateRef}
          onFinish={finish}
        />
      )}
    </OwnerGateContext.Provider>
  );
}

/**
 * Mounts the router blocker while `sharedRouterGuardNeeded`. React Router
 * honours only the LAST registered blocker, so the shared one is
 * re-registered (keyed remount) whenever a self-guarded owner mounts; while
 * mounted it evaluates every owner, self-guarded ones too.
 *
 * While a gate transition is open (`holding`) a mounted blocker stays mounted
 * under the same key, even when the last owner resolves: the gate's
 * `proceed()`/`reset()` must reach the SAME blocker that blocked the
 * navigation (an unmounted blocker is deleted from the router, and a later
 * proceed is an invalid blocker transition).
 */
function OwnerRouterGuard({
  interceptor,
  holding,
}: {
  interceptor: { current: NavigationInterceptor | null };
  holding: boolean;
}): JSX.Element | null {
  const owners = useOwnerRecords();
  const mounted = useRef<string | null>(null);
  const needed = sharedRouterGuardNeeded(owners);
  const selfGuarded = owners
    .filter((owner) => owner.ownNavigationGuard && !owner.detached)
    .map((owner) => owner.id)
    .join(' ');
  let key: string | null;
  if (holding && mounted.current !== null) key = mounted.current;
  else key = needed ? selfGuarded : null;
  useEffect(() => {
    mounted.current = key;
  });
  return key === null ? null : <OwnerRouterBlocker key={key} interceptor={interceptor} />;
}

function OwnerRouterBlocker({ interceptor }: { interceptor: { current: NavigationInterceptor | null } }): null {
  const { registry, requestOwnerTransition } = useOwnerGateContext();
  const pending = useRef<ReturnType<typeof navigationTransition> | null>(null);
  const intercepted = useRef(false);
  const shouldBlock = useCallback(
    ({ currentLocation, nextLocation }: { currentLocation: Location; nextLocation: Location }) => {
      intercepted.current = false;
      if (interceptor.current?.(nextLocation)) {
        intercepted.current = true;
        return true;
      }
      const transition = navigationTransition(currentLocation, nextLocation);
      if (selectOwnerBlocking(registry.getSnapshot(), transition.affects).level === 'none') return false;
      pending.current = transition;
      return true;
    },
    [registry, interceptor],
  );
  const blocker = useBlocker(shouldBlock);
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  // Once per blocked navigation (keyed on the blocker state, not its object).
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    const current = blockerRef.current;
    if (intercepted.current) {
      intercepted.current = false;
      current.reset?.();
      return;
    }
    const transition = pending.current;
    pending.current = null;
    if (!transition) {
      current.reset?.();
      return;
    }
    requestOwnerTransition({
      ...transition,
      proceed: () => blockerRef.current.proceed?.(),
      cancel: () => blockerRef.current.reset?.(),
    });
  }, [blocker.state, requestOwnerTransition]);

  return null;
}

function OwnerGatePrompt({
  transition,
  registry,
  handlers,
  gateRef,
  onFinish,
}: {
  transition: OwnerTransition;
  registry: OwnerRegistry;
  handlers: Map<string, OwnerHandlers>;
  gateRef: { current: HTMLDivElement | null };
  onFinish: (run: boolean) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const owners = useSyncExternalStore(registry.subscribe, registry.getSnapshot, registry.getSnapshot);
  const blocking = selectOwnerBlocking(owners, transition.affects);
  const returnFocus = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  // Revisions the user saw when consenting; a later edit invalidates consent.
  const seen = useRef(new Map<string, number>());
  const [checking, setChecking] = useState<Record<string, 'running' | 'failed' | undefined>>({});

  useEffect(() => {
    for (const owner of blocking.owners) seen.current.set(owner.id, owner.draftRevision);
  }, [blocking.owners]);

  // Re-evaluate whenever an owner changes: every result arrived and nothing
  // unsent is left → run the transition.
  useEffect(() => {
    if (blocking.level === 'none') onFinish(true);
  }, [blocking.level, onFinish]);

  // Focus the default control when the gate opens or changes mode.
  useEffect(() => {
    // preventScroll: at compact sizes the prompt opens at its heading (page ·
    // computer · org first); the default control still holds focus.
    const id = requestAnimationFrame(() =>
      gateRef.current?.querySelector<HTMLElement>('[data-owner-gate-default]')?.focus({ preventScroll: true }),
    );
    return () => cancelAnimationFrame(id);
  }, [blocking.level, gateRef]);

  const cancel = (): void => {
    const target = returnFocus.current;
    onFinish(false);
    if (target?.isConnected) queueMicrotask(() => target.focus());
  };

  const discard = (): void => {
    const current = selectOwnerBlocking(registry.getSnapshot(), transition.affects);
    if (current.level !== 'prompt') return;
    const changed = current.owners.some((owner) => seen.current.get(owner.id) !== owner.draftRevision);
    if (changed) return; // the prompt now shows the newer state; ask again
    for (const owner of current.owners) {
      if (registry.discard(owner.id)) handlers.get(owner.id)?.discard?.();
    }
    if (selectOwnerBlocking(registry.getSnapshot(), transition.affects).level === 'none') onFinish(true);
  };

  const check = async (owner: OwnerRecord): Promise<void> => {
    const run = handlers.get(owner.id)?.check;
    if (!run) return;
    setChecking((prev) => ({ ...prev, [owner.id]: 'running' }));
    try {
      await run();
      setChecking((prev) => ({ ...prev, [owner.id]: undefined }));
    } catch {
      setChecking((prev) => ({ ...prev, [owner.id]: 'failed' }));
    }
  };

  const hold = blocking.level === 'hold';
  const first = blocking.owners[0];
  const firstParams = first ? ownerContextParams(first, t) : { page: '' };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        // Close button (prompt only) == Keep editing.
        if (!open && !hold) cancel();
      }}
    >
      <DialogContent
        ref={gateRef}
        role="alertdialog"
        data-owner-gate={hold ? 'hold' : 'prompt'}
        closeLabel={t('owners.gate.keepEditing')}
        className={`max-w-lg ${hold ? '[&>button:last-child]:hidden' : ''}`}
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => {
          event.preventDefault();
          // Escape == Keep editing in the prompt; a hold cannot be escaped.
          if (!hold) cancel();
        }}
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="pr-8 break-words">
            {t(hold ? 'owners.gate.holdTitle' : 'owners.gate.promptTitle')}
          </DialogTitle>
          <DialogDescription className="break-words">
            {t(hold ? 'owners.gate.holdBody' : 'owners.gate.promptBody')}
          </DialogDescription>
        </DialogHeader>
        <ul className="space-y-2" data-testid="owner-gate-owners">
          {blocking.owners.map((owner) => {
            const params = ownerContextParams(owner, t);
            const key =
              owner.status === 'pending'
                ? 'owners.gate.ownerPending'
                : owner.status === 'unknown'
                  ? 'owners.gate.ownerUnknown'
                  : owner.status === 'failed'
                    ? 'owners.gate.ownerFailed'
                    : 'owners.gate.ownerDirty';
            const state = checking[owner.id];
            return (
              <li
                key={owner.id}
                data-owner-status={owner.status}
                className="border-border-default bg-surface-raised text-text-primary rounded-md border p-3 text-sm break-words"
              >
                <p>{t(key, params)}</p>
                {owner.status === 'unknown' && (
                  <div className="mt-2 space-y-1">
                    {owner.canCheck ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={state === 'running'}
                        onClick={() => void check(owner)}
                      >
                        {t(state === 'running' ? 'owners.save.checking' : 'owners.save.checkResult')}
                      </Button>
                    ) : (
                      <p className="text-text-secondary text-xs">{t('owners.save.checkUnavailable', params)}</p>
                    )}
                    {state === 'failed' && (
                      <p role="alert" className="text-feedback-danger text-xs">
                        {t('owners.save.checkFailed')}
                      </p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <DialogFooter className="flex-wrap">
          {hold ? (
            <Button size="sm" className={WRAP} data-owner-gate-default onClick={cancel}>
              {t('owners.gate.backToOwner', { page: firstParams.page })}
            </Button>
          ) : (
            <>
              <Button size="sm" className={WRAP} data-owner-gate-default onClick={cancel}>
                {t('owners.gate.keepEditing')}
              </Button>
              <Button size="sm" variant="destructiveOutline" className={WRAP} onClick={discard}>
                {t(`owners.gate.discard.${transition.effect}`)}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
