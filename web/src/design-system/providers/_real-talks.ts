import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { talks as talksApi } from '@/lib/api';
import type {
  AbandonTalkArgs,
  AbandonTalkResult,
  DispatchFromTalkArgs,
  DispatchFromTalkResult,
  EndTalkArgs,
  EndTalkResult,
  MutationLike,
  QueryLike,
  StartTalkArgs,
  StartTalkResult,
  TalksApi,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useTalksList(params?: { status?: string; agent?: string; limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['talks', slug, params],
    queryFn: () => talksApi.listTalks(slug, params),
    enabled: !!slug,
    refetchInterval: 60_000,
  }) as QueryLike<Awaited<ReturnType<typeof talksApi.listTalks>>>;
}

function useTalk(talkId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['talk', slug, talkId],
    queryFn: () => talksApi.getTalk(slug, talkId as string),
    enabled: !!slug && !!talkId,
    refetchInterval: 60_000,
  });
}

function invalidate(qc: ReturnType<typeof useQueryClient>, slug: string, talkId?: string) {
  qc.invalidateQueries({ queryKey: ['talks', slug] });
  if (talkId) qc.invalidateQueries({ queryKey: ['talk', slug, talkId] });
}

function useStartTalk(): MutationLike<StartTalkArgs, StartTalkResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: StartTalkArgs) => talksApi.startTalk(slug, body),
    onSuccess: (resp) => invalidate(qc, slug, resp.talk_id),
  });
}

function useAbandonTalk(
  talkId: string,
): MutationLike<AbandonTalkArgs, AbandonTalkResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AbandonTalkArgs) => talksApi.abandonTalk(slug, talkId, body),
    onSuccess: () => invalidate(qc, slug, talkId),
  });
}

function useEndTalk(talkId: string): MutationLike<EndTalkArgs, EndTalkResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EndTalkArgs) => talksApi.endTalk(slug, talkId, body),
    onSuccess: () => invalidate(qc, slug, talkId),
  });
}

function useDispatchFromTalk(
  talkId: string,
): MutationLike<DispatchFromTalkArgs, DispatchFromTalkResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DispatchFromTalkArgs) =>
      talksApi.dispatchFromTalk(slug, talkId, body),
    onSuccess: () => invalidate(qc, slug, talkId),
  });
}

export const realTalksApi: TalksApi = {
  useTalksList,
  useTalk,
  useStartTalk,
  useAbandonTalk,
  useEndTalk,
  useDispatchFromTalk,
};
