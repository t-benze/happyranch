/**
 * Hand-off to the desktop app's existing Connection dialog (THR-402 v1.1).
 *
 * Features may not import `features/desktop-dialogs`, so the desktop dialog
 * host publishes its one open request here. The value is null in browser
 * mode (no Connection dialog exists), which callers use to hide any
 * Connection entry point. Opening it is presentation only: every target
 * change still goes through the Connection dialog's own owner gate, receipt
 * and commit path; nothing here switches a computer.
 */
import { createContext, useContext, type ReactNode } from 'react';

/** Open the existing Connection dialog; `invoker` receives focus when it closes. */
export type OpenConnection = (invoker?: HTMLElement | null) => void;

const OpenConnectionContext = createContext<OpenConnection | null>(null);

export function OpenConnectionProvider({ value, children }: { value: OpenConnection | null; children: ReactNode }): JSX.Element {
  return <OpenConnectionContext.Provider value={value}>{children}</OpenConnectionContext.Provider>;
}

/** The desktop Connection opener, or null in browser mode. */
export function useOpenConnection(): OpenConnection | null {
  return useContext(OpenConnectionContext);
}
