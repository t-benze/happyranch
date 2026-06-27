/**
 * Public, provider-aware settings hooks.
 *
 * One-liner forwarders into `useData().settings` so compositions never
 * reach into `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useSettings: ReturnType<
  typeof useData
>['settings']['useSettings'] = () => useData().settings.useSettings();

export const useUpdateOrgSettings: ReturnType<
  typeof useData
>['settings']['useUpdateOrgSettings'] = () =>
  useData().settings.useUpdateOrgSettings();

export const useNextWakes: ReturnType<
  typeof useData
>['settings']['useNextWakes'] = (agent, count) =>
  useData().settings.useNextWakes(agent, count);
