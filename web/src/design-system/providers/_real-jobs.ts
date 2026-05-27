import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { jobs as jobsApi } from '@/lib/api';
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
import type { JobListResponse, JobOutput, JobRecord } from '@/lib/api/types';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useJobsList(params?: {
  status?: string;
  agent?: string;
  task_id?: string;
  review_required?: string;
  persistent?: string;
  limit?: number;
}) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['jobs', slug, params],
    queryFn: () => jobsApi.listJobs(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<JobListResponse>;
}

function useJob(jobId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['job', slug, jobId],
    queryFn: () => jobsApi.getJob(slug, jobId as string),
    enabled: !!slug && !!jobId,
  }) as QueryLike<JobRecord>;
}

function useRejectJob(): MutationLike<
  { jobId: string; body: RejectJobArgs },
  RejectJobResult
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: string; body: RejectJobArgs }) =>
      jobsApi.rejectJob(slug, jobId, body),
    onSuccess: (_d, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', slug, jobId] });
      qc.invalidateQueries({ queryKey: ['jobs', slug] });
    },
  });
}

function useRunJob(): MutationLike<
  { jobId: string; body: RunJobArgs },
  RunJobResult
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: string; body: RunJobArgs }) =>
      jobsApi.runJob(slug, jobId, body),
    onSuccess: (_d, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', slug, jobId] });
      qc.invalidateQueries({ queryKey: ['jobs', slug] });
    },
  });
}

function useStopJob(): MutationLike<{ jobId: string }, StopJobResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId }: { jobId: string }) => jobsApi.stopJob(slug, jobId),
    onSuccess: (_d, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', slug, jobId] });
      qc.invalidateQueries({ queryKey: ['jobs', slug] });
    },
  });
}

function useJobOutput(jobId: string | undefined): QueryLike<JobOutputResult> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['job-output', slug, jobId],
    queryFn: () => jobsApi.getJobOutput(slug, jobId as string),
    enabled: !!slug && !!jobId,
  }) as QueryLike<JobOutput>;
}

export const realJobsApi: JobsApi = {
  useJobsList,
  useJob,
  useJobOutput,
  useRejectJob,
  useRunJob,
  useStopJob,
};
