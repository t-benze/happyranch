/**
 * Real (daemon-backed) implementation of `SettingsApi`.
 *
 * Phase 1: read-only `useSettings` hook backed by GET /settings.
 * Phase 2: editable `useUpdateOrgSettings` mutation backed by PUT /settings/org.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { settings as settingsApi } from '@/lib/api';
import type {
  NextWakesResponse,
  OrgSettingsPatch,
  SettingsSnapshot,
} from '@/lib/api/types';
import type { SettingsApi, QueryLike } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useSettings(): QueryLike<SettingsSnapshot> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['settings', slug],
    queryFn: () => settingsApi.getSettings(slug),
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<SettingsSnapshot>;
}

function useUpdateOrgSettings() {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: OrgSettingsPatch) =>
      settingsApi.putOrgSettings(slug, patch),
    onSuccess: (data: SettingsSnapshot) => {
      qc.setQueryData(['settings', slug], data);
    },
  });
}

function useNextWakes(
  agent: string | undefined,
  count = 5,
): QueryLike<NextWakesResponse> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['work-hours-next-wakes', slug, agent, count],
    queryFn: () => settingsApi.getNextWakes(slug, agent as string, count),
    enabled: !!slug && !!agent,
    staleTime: 30_000,
  }) as QueryLike<NextWakesResponse>;
}

export const realSettingsApi: SettingsApi = {
  useSettings,
  useUpdateOrgSettings,
  useNextWakes,
};
