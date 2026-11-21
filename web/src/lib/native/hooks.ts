/**
 * React accessors for the native bridge v2 stores (S5-W1).
 */
import { useMemo, useSyncExternalStore } from 'react';
import { deriveConnectionMenuState, type AppState, type ConnectionMenuState } from './appStatus';
import { getNativeNotice, subscribeNativeNotice, type NativeNoticeKind } from './notices';
import { currentGeneration, getAppStatus, getPromptBaseUrl, subscribeAppStatus } from './target';

/** Latest native `app.status` (desktop), or null (browser / not yet known). */
export function useNativeAppStatus(): AppState | null {
  return useSyncExternalStore(subscribeAppStatus, getAppStatus, getAppStatus);
}

/** Current generation: `browser` without a bridge, null while unknown. */
export function useTargetGeneration(): string | null {
  return useSyncExternalStore(subscribeAppStatus, currentGeneration, currentGeneration);
}

/** app.status-derived connection/settings menu state (desktop only). */
export function useConnectionMenuState(): ConnectionMenuState | null {
  const status = useNativeAppStatus();
  return useMemo(() => (status ? deriveConnectionMenuState(status) : null), [status]);
}

/** Base URL for copy-paste CLI prompts; see `getPromptBaseUrl`. */
export function usePromptBaseUrl(): string | null {
  return useSyncExternalStore(subscribeAppStatus, getPromptBaseUrl, getPromptBaseUrl);
}

export function useNativeNotice(): NativeNoticeKind | null {
  return useSyncExternalStore(subscribeNativeNotice, getNativeNotice, getNativeNotice);
}
