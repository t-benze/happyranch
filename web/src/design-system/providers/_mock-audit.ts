import type { AuditApi, AuditListPage, InfiniteQueryLike } from './DataContext';

const empty: InfiniteQueryLike<AuditListPage> = {
  data: { pages: [{ entries: [], next_cursor: null }] },
  isLoading: false,
  isError: false,
  error: null,
  fetchNextPage: () => {
    /* no-op: prototype fixtures fit in a single page */
  },
  hasNextPage: false,
  isFetchingNextPage: false,
};

export const mockAuditApi: AuditApi = {
  useAuditList: () => empty,
};
