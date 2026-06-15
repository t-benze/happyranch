/**
 * Real (daemon-backed) implementation of `SettingsApi`.
 *
 * Phase 1: read-only `useSettings` hook backed by GET /settings.
 * Phase 2: editable `useUpdateOrgSettings` mutation backed by PUT /settings/org.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { settings as settingsApi } from '@/lib/api';
import type { OrgSettingsPatch, SettingsSnapshot } from '@/lib/api/types';
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

export const realSettingsApi: SettingsApi = {
  useSettings,
  useUpdateOrgSettings,
};
