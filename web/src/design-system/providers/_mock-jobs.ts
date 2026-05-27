import type { JobListResponse, JobOutput, JobRecord } from '@/lib/api/types';
import type {
  JobOutputResult,
  JobsApi,
  MutationLike,
  QueryLike,
  RejectJobArgs,
  RejectJobResult,
  RunJobArgs,
  RunJobResult,
  StopJobResult,
} from './DataContext';

export const mockJobsApi: JobsApi = {
  useJobsList: (): QueryLike<JobListResponse> => ({
    data: { jobs: [] },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useJob: (_jobId: string | undefined): QueryLike<JobRecord> => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useJobOutput: (_jobId: string | undefined): QueryLike<JobOutputResult> => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }) as QueryLike<JobOutput>,
  useRejectJob: (): MutationLike<
    { jobId: string; body: RejectJobArgs },
    RejectJobResult
  > => ({
    mutateAsync: async () => { throw new Error('Mock: not implemented'); },
    isPending: false,
  }),
  useRunJob: (): MutationLike<
    { jobId: string; body: RunJobArgs },
    RunJobResult
  > => ({
    mutateAsync: async () => { throw new Error('Mock: not implemented'); },
    isPending: false,
  }),
  useStopJob: (): MutationLike<{ jobId: string }, StopJobResult> => ({
    mutateAsync: async () => { throw new Error('Mock: not implemented'); },
    isPending: false,
  }),
};
