import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * TopbarSlots — lets a routed page place content into the persistent AppBar
 * (THR-376 r2 PR-S1, rows A1b/A2).
 *
 * `AppShell` wraps the AppBar and the routed `<main>` in
 * `<TopbarSlotsProvider>`. The AppBar registers two DOM slots with
 * `useTopbarSlotRefs()`; a page renders
 *
 *   <TopbarStatus>…page-status chip…</TopbarStatus>
 *   <TopbarActions><Button size="sm">…primary action…</Button></TopbarActions>
 *
 * and the children are portalled into the AppBar. Portals keep the page's
 * React tree, so handlers, dialog state and context stay with the page; when
 * the page unmounts its topbar content goes with it.
 *
 * Outside a provider (isolated page tests, stories, prototype routes) the
 * children render inline where they are written, so a page stays usable
 * without the shell. Inside a provider whose slot has not attached yet they
 * render nothing until it does.
 */

interface TopbarSlotsValue {
  statusEl: HTMLElement | null;
  actionsEl: HTMLElement | null;
  setStatusEl: (el: HTMLElement | null) => void;
  setActionsEl: (el: HTMLElement | null) => void;
}

const TopbarSlotsContext = createContext<TopbarSlotsValue | null>(null);

export function TopbarSlotsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [statusEl, setStatusEl] = useState<HTMLElement | null>(null);
  const [actionsEl, setActionsEl] = useState<HTMLElement | null>(null);
  const value = useMemo(
    () => ({ statusEl, actionsEl, setStatusEl, setActionsEl }),
    [statusEl, actionsEl],
  );
  return <TopbarSlotsContext.Provider value={value}>{children}</TopbarSlotsContext.Provider>;
}

/** AppBar-only: ref callbacks for the status and actions slot elements. */
export function useTopbarSlotRefs(): {
  statusRef: (el: HTMLElement | null) => void;
  actionsRef: (el: HTMLElement | null) => void;
} {
  const ctx = useContext(TopbarSlotsContext);
  return {
    statusRef: ctx?.setStatusEl ?? noop,
    actionsRef: ctx?.setActionsEl ?? noop,
  };
}

function noop(): void {}

function Slot({ children, which }: { children: ReactNode; which: 'status' | 'actions' }): JSX.Element | null {
  const ctx = useContext(TopbarSlotsContext);
  if (!ctx) return <>{children}</>;
  const target = which === 'status' ? ctx.statusEl : ctx.actionsEl;
  return target ? createPortal(children, target) : null;
}

/** Page-status chip(s), shown right after the topbar page title. */
export function TopbarStatus({ children }: { children: ReactNode }): JSX.Element | null {
  return <Slot which="status">{children}</Slot>;
}

/** The page's primary action(s), shown before the language/theme controls. */
export function TopbarActions({ children }: { children: ReactNode }): JSX.Element | null {
  return <Slot which="actions">{children}</Slot>;
}
