import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { audit as auditApi } from '@/lib/api';
import type { AuditApi, QueryLike } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useAuditList(params?: {
  task_id?: string | null;
  agent?: string | null;
  action?: string | null;
  since?: string | null;
  limit?: number;
}) {
  const slug = useRealOrgSlug();
  const clean = Object.fromEntries(
    Object.entries(params ?? {}).filter(([, v]) => v != null && v !== ''),
  ) as Record<string, string | number>;
  return useQuery({
    queryKey: ['audit', slug, clean],
    queryFn: () => auditApi.listAudit(slug, { ...clean, include_thread_origin: true }),
    enabled: !!slug,
    refetchInterval: 60_000,
  }) as QueryLike<Awaited<ReturnType<typeof auditApi.listAudit>>>;
}

export const realAuditApi: AuditApi = {
  useAuditList,
};
