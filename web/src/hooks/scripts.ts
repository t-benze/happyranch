/**
 * Public, provider-aware scripts hooks.
 *
 * Compositions in `features/` import from this file — they never reach into
 * `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useScriptsRoutes = () => useData().useScriptsRoutes();

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const useScriptsList: ReturnType<typeof useData>['scripts']['useScriptsList'] = (
  params,
) => useData().scripts.useScriptsList(params);

export const useScript: ReturnType<typeof useData>['scripts']['useScript'] = (
  srId,
) => useData().scripts.useScript(srId);

export const useScriptOutput: ReturnType<typeof useData>['scripts']['useScriptOutput'] = (
  srId,
) => useData().scripts.useScriptOutput(srId);

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useRejectScript: ReturnType<typeof useData>['scripts']['useRejectScript'] = () =>
  useData().scripts.useRejectScript();

export const useRunScript: ReturnType<typeof useData>['scripts']['useRunScript'] = () =>
  useData().scripts.useRunScript();
