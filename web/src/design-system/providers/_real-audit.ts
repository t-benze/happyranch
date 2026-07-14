import { useInfiniteQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { audit as auditApi } from '@/lib/api';
import type { AuditApi, AuditListPage, InfiniteQueryLike } from './DataContext';

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
}): InfiniteQueryLike<AuditListPage> {
  const slug = useRealOrgSlug();
  const clean = Object.fromEntries(
    Object.entries(params ?? {}).filter(([, v]) => v != null && v !== ''),
  ) as Record<string, string | number>;
  const q = useInfiniteQuery<AuditListPage>({
    queryKey: ['audit', slug, clean],
    initialPageParam: undefined,
    // Keyset pagination: each page after the first sends the prior page's
    // opaque `next_cursor`. All active filters (task_id/agent/action/since)
    // AND-compose with the cursor server-side, so pagination stays scoped to
    // the current filter + since window.
    queryFn: ({ pageParam }) =>
      auditApi.listAudit(slug, {
        ...clean,
        include_thread_origin: true,
        ...(pageParam ? { cursor: pageParam as string } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!slug,
    // Preserve the surface's 60s auto-refresh. TanStack re-walks the page
    // chain from the top on interval refetch, so the keyset cursors stay
    // coherent even as new most-recent-first rows arrive.
    refetchInterval: 60_000,
  });
  return {
    data: q.data ? { pages: q.data.pages } : undefined,
    isLoading: q.isLoading,
    isError: q.isError,
    error: (q.error as Error | null) ?? null,
    fetchNextPage: () => q.fetchNextPage(),
    hasNextPage: !!q.hasNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
  };
}

export const realAuditApi: AuditApi = {
  useAuditList,
};
