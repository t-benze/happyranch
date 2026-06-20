import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { workHours as workHoursApi } from '@/lib/api';
import type { WorkHoursApi, QueryLike } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useWorkHoursList(params?: { agent?: string; limit?: number }) {
  const slug = useRealOrgSlug();
  // Build a stable query key: drop undefined params.
  const qp: Record<string, string | number> = {};
  if (params?.agent) qp.agent = params.agent;
  if (params?.limit != null) qp.limit = params.limit;
  return useQuery({
    queryKey: ['work-hours-list', slug, qp],
    queryFn: () => workHoursApi.listWorkHours(slug, qp),
    enabled: !!slug,
    refetchInterval: 60_000,
  }) as QueryLike<Awaited<ReturnType<typeof workHoursApi.listWorkHours>>>;
}

export const realWorkHoursApi: WorkHoursApi = {
  useWorkHoursList,
};
