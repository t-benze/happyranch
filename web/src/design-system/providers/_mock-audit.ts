import type { AuditApi, QueryLike } from './DataContext';
import type { AuditEntry } from '@/lib/api/audit';

const empty: QueryLike<{ entries: AuditEntry[] }> = {
  data: { entries: [] },
  isLoading: false,
  isError: false,
  error: null,
};

export const mockAuditApi: AuditApi = {
  useAuditList: () => empty,
};
