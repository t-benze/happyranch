import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { dreams as dreamsApi } from '@/lib/api';
import type { DreamsApi } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useDreamsList(params?: { agent?: string; limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['dreams-list', slug, params],
    queryFn: () => dreamsApi.listDreams(slug, params),
    enabled: !!slug,
  });
}

function useDream(dreamId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['dream', slug, dreamId],
    queryFn: () => dreamsApi.getDream(slug, dreamId as string),
    enabled: !!slug && !!dreamId,
  });
}

function useAcceptCandidate() {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (candidateId: number) => dreamsApi.acceptDreamKbCandidate(slug, candidateId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dream', slug] });
      qc.invalidateQueries({ queryKey: ['dreams-list', slug] });
    },
  });
}

function useDismissCandidate() {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (candidateId: number) => dreamsApi.dismissDreamKbCandidate(slug, candidateId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dream', slug] });
      qc.invalidateQueries({ queryKey: ['dreams-list', slug] });
    },
  });
}

export const realDreamsApi: DreamsApi = {
  useDreamsList: useDreamsList as DreamsApi['useDreamsList'],
  useDream: useDream as DreamsApi['useDream'],
  useAcceptCandidate: useAcceptCandidate as DreamsApi['useAcceptCandidate'],
  useDismissCandidate: useDismissCandidate as DreamsApi['useDismissCandidate'],
};
