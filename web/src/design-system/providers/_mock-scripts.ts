import type { ScriptListResponse, ScriptRequest } from '@/lib/api/types';
import type {
  MutationLike,
  QueryLike,
  RejectScriptArgs,
  RejectScriptResult,
  RunScriptArgs,
  RunScriptResult,
  ScriptsApi,
} from './DataContext';

export const mockScriptsApi: ScriptsApi = {
  useScriptsList: (): QueryLike<ScriptListResponse> => ({
    data: { scripts: [] },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useScript: (_srId: string | undefined): QueryLike<ScriptRequest> => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useRejectScript: (): MutationLike<
    { srId: string; body: RejectScriptArgs },
    RejectScriptResult
  > => ({
    mutateAsync: async () => { throw new Error('Mock: not implemented'); },
    isPending: false,
  }),
  useRunScript: (): MutationLike<
    { srId: string; body: RunScriptArgs },
    RunScriptResult
  > => ({
    mutateAsync: async () => { throw new Error('Mock: not implemented'); },
    isPending: false,
  }),
};
