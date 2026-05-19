/**
 * Public, provider-aware audit hook. Mirrors `useData().audit` so
 * compositions never reach into `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useAuditList: ReturnType<typeof useData>['audit']['useAuditList'] = (
  params,
) => useData().audit.useAuditList(params);
