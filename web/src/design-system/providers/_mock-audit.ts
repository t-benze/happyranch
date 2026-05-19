import type { AuditApi, QueryLike } from './DataContext';
import type { AuditEntry } from '@/lib/api/types';

const empty: QueryLike<{ entries: AuditEntry[] }> = {
  data: { entries: [] },
  isLoading: false,
  isError: false,
  error: null,
};

export const mockAuditApi: AuditApi = {
  useAuditList: () => empty,
};
