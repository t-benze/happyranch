import { request } from './client';
import type { DashboardSummaryResponse } from './types';

export const getDashboardSummary = (
  slug: string,
): Promise<DashboardSummaryResponse> =>
  request(`/orgs/${slug}/dashboard/summary`);
