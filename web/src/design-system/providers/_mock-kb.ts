import { MOCK_KB_ENTRIES } from '@/mocks/kb';
import type { KBEntry } from '@/lib/api/kb';
import type {
  AddKBEntryArgs,
  AddKBEntryResult,
  KbApi,
  KbRoutes,
  MutationLike,
  QueryLike,
} from './DataContext';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function noopMutation<TArgs, TResult>(): MutationLike<TArgs, TResult> {
  return {
    mutateAsync: async () => ({}) as TResult,
    isPending: false,
  };
}

export const mockKbApi: KbApi = {
  useKBList: (params) =>
    ok({
      entries: params?.type
        ? MOCK_KB_ENTRIES.filter((e) => e.type === params.type)
        : MOCK_KB_ENTRIES,
    }),
  useKBSearch: (q) =>
    ok({
      entries: q
        ? MOCK_KB_ENTRIES.filter(
            (e) =>
              e.title.toLowerCase().includes(q.toLowerCase()) ||
              e.body.toLowerCase().includes(q.toLowerCase()),
          )
        : MOCK_KB_ENTRIES,
    }),
  useKBEntry: (entrySlug) =>
    ok(
      MOCK_KB_ENTRIES.find((e) => e.slug === entrySlug) ?? MOCK_KB_ENTRIES[0],
    ) as QueryLike<KBEntry>,
  useAddKBEntry: () => noopMutation<AddKBEntryArgs, AddKBEntryResult>(),
};

export function useMockKbRoutes(): KbRoutes {
  return {
    inbox: () => '/__prototypes/kb',
    detail: (entrySlug: string) => `/__prototypes/kb/${entrySlug}`,
    inboxForOrg: () => '/__prototypes/kb',
  };
}
