/**
 * Public, provider-aware settings hooks.
 *
 * One-liner forwarders into `useData().settings` so compositions never
 * reach into `design-system/providers/` directly.
 */
import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useData } from '@/design-system/providers/DataContext';
import { getSettings } from '@/lib/api/settings';
import type { SettingsSnapshot } from '@/lib/api/types';

// Re-export the mint helper so compositions never import from @/lib/api directly.
export { mintRegistrationToken } from '@/lib/api/settings';
export type {
  RegistrationTokenMintRequest,
  RegistrationTokenMintResponse,
} from '@/lib/api/settings';

export const useSettings: ReturnType<
  typeof useData
>['settings']['useSettings'] = () => useData().settings.useSettings();

export const useUpdateOrgSettings: ReturnType<
  typeof useData
>['settings']['useUpdateOrgSettings'] = () =>
  useData().settings.useUpdateOrgSettings();

export const useDaemonCapacity = () => useData().settings.useDaemonCapacity();
export const useUpdateDaemonCapacity = () => useData().settings.useUpdateDaemonCapacity();

export const useCodingSettings = () => useData().settings.useCodingSettings();
export const useCodingSettingsOptions = () => useData().settings.useCodingSettingsOptions();
export const useCodingSettingsActions = () => useData().settings.useCodingSettingsActions();
export const useCodingSendFence = () => useData().settings.useCodingSendFence();
export type { CodingSendFence } from '@/design-system/providers/DataContext';
export type {
  CodingSettingsCommand,
  CodingSettingsOptions,
  CodingSettingsCurrent,
  CodingSettingsReceipt,
  CodingSettingsSave,
  CodingSettingsView,
  CodingConfiguration,
  CodingRoles,
  CodingAgentBinding,
} from '@/lib/api/types';

export const useNextWakes:ReturnType<
  typeof useData
>['settings']['useNextWakes'] = (agent, count) =>
  useData().settings.useNextWakes(agent, count);

/**
 * THR-387 S6-W2a: fresh same-org read of the org settings for Check result
 * (owner reconciliation of an unknown save). Bypasses the 30s staleTime and
 * writes the ['settings', slug] cache like the ordinary read.
 */
export function useReadOrgSettingsNow(): () => Promise<SettingsSnapshot> {
  const { slug = '' } = useParams<{ slug: string }>();
  const qc = useQueryClient();
  return useCallback(
    () =>
      qc.fetchQuery({
        queryKey: ['settings', slug],
        queryFn: () => getSettings(slug),
        staleTime: 0,
      }),
    [qc, slug],
  );
}
