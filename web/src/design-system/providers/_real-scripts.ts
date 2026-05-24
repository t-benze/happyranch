import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { scripts as scriptsApi } from '@/lib/api';
import type {
  MutationLike,
  QueryLike,
  RejectScriptArgs,
  RejectScriptResult,
  RunScriptArgs,
  RunScriptResult,
  ScriptsApi,
} from './DataContext';
import type { ScriptListResponse, ScriptRequest } from '@/lib/api/types';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useScriptsList(params?: {
  status?: string;
  agent?: string;
  task_id?: string;
  limit?: number;
}) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['scripts', slug, params],
    queryFn: () => scriptsApi.listScripts(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<ScriptListResponse>;
}

function useScript(srId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['script', slug, srId],
    queryFn: () => scriptsApi.getScript(slug, srId as string),
    enabled: !!slug && !!srId,
  }) as QueryLike<ScriptRequest>;
}

function useRejectScript(): MutationLike<
  { srId: string; body: RejectScriptArgs },
  RejectScriptResult
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ srId, body }: { srId: string; body: RejectScriptArgs }) =>
      scriptsApi.rejectScript(slug, srId, body),
    onSuccess: (_d, { srId }) => {
      qc.invalidateQueries({ queryKey: ['script', slug, srId] });
      qc.invalidateQueries({ queryKey: ['scripts', slug] });
    },
  });
}

function useRunScript(): MutationLike<
  { srId: string; body: RunScriptArgs },
  RunScriptResult
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ srId, body }: { srId: string; body: RunScriptArgs }) =>
      scriptsApi.runScript(slug, srId, body),
    onSuccess: (_d, { srId }) => {
      qc.invalidateQueries({ queryKey: ['script', slug, srId] });
      qc.invalidateQueries({ queryKey: ['scripts', slug] });
    },
  });
}

export const realScriptsApi: ScriptsApi = {
  useScriptsList,
  useScript,
  useRejectScript,
  useRunScript,
};
