/**
 * Public, provider-aware settings hook.
 *
 * One-liner that reads `useData().settings` and forwards.
 * Compositions import from this file — they never reach into
 * `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useSettings: ReturnType<
  typeof useData
>['settings']['useSettings'] = () => useData().settings.useSettings();
