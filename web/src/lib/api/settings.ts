import { request } from './client';
import type { SettingsSnapshot } from './types';

export const getSettings = (slug: string): Promise<SettingsSnapshot> =>
  request(`/orgs/${slug}/settings`);
