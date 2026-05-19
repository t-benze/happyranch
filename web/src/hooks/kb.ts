/**
 * Public, provider-aware KB hooks. Each is a one-liner that reads
 * `useData().kb` and forwards. Compositions in `features/kb/` import
 * only from this file.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useKbRoutes = () => useData().useKbRoutes();

export const useKBList: ReturnType<typeof useData>['kb']['useKBList'] = (
  params,
) => useData().kb.useKBList(params);

export const useKBSearch: ReturnType<typeof useData>['kb']['useKBSearch'] = (
  q,
  params,
) => useData().kb.useKBSearch(q, params);

export const useKBEntry: ReturnType<typeof useData>['kb']['useKBEntry'] = (
  entrySlug,
) => useData().kb.useKBEntry(entrySlug);

export const useAddKBEntry: ReturnType<typeof useData>['kb']['useAddKBEntry'] = () =>
  useData().kb.useAddKBEntry();
