import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { kb as kbApi } from '@/lib/api';
import type {
  AddKBEntryArgs,
  AddKBEntryResult,
  KbApi,
  MutationLike,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useKBList(params?: { type?: string }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-list', slug, params],
    queryFn: () => kbApi.listKB(slug, params),
    enabled: !!slug,
  });
}

function useKBSearch(q: string, params?: { limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-search', slug, q, params],
    queryFn: () => kbApi.searchKB(slug, { q, limit: params?.limit ?? 50 }),
    enabled: !!slug && q.trim().length > 0,
  });
}

function useKBEntry(entrySlug: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-entry', slug, entrySlug],
    queryFn: () => kbApi.getKBEntry(slug, entrySlug as string),
    enabled: !!slug && !!entrySlug,
  });
}

function useAddKBEntry(): MutationLike<AddKBEntryArgs, AddKBEntryResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AddKBEntryArgs) => kbApi.addKBEntry(slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kb-list', slug] });
      qc.invalidateQueries({ queryKey: ['kb-search', slug] });
    },
  });
}

export const realKbApi: KbApi = {
  useKBList: useKBList as KbApi['useKBList'],
  useKBSearch: useKBSearch as KbApi['useKBSearch'],
  useKBEntry: useKBEntry as KbApi['useKBEntry'],
  useAddKBEntry,
};
