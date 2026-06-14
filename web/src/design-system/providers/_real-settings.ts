/**
 * Real (daemon-backed) implementation of `SettingsApi`.
 *
 * Single `useSettings` hook backed by GET /settings. staleTime 30s
 * keeps the settings panel warm during quick open/close cycles.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { settings as settingsApi } from '@/lib/api';
import type { SettingsSnapshot } from '@/lib/api/types';
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

export const realSettingsApi: SettingsApi = {
  useSettings,
};
