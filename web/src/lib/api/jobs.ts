/** Mirror of src/daemon/routes/jobs.py — founder-facing surface only.
 *
 * Excluded (agent callback): POST /jobs/submit.
 */
import { request } from './client';
import type {
  JobRecord,
  JobOutput,
  JobRunResponse,
  JobListResponse,
  JobTailResponse,
  JobStopResponse,
  JobWaitResponse,
} from './types';

export const listJobs = (
  slug: string,
  params?: {
    status?: string;
    agent?: string;
    task_id?: string;
    review_required?: string;
    persistent?: string;
    limit?: number;
  },
): Promise<JobListResponse> =>
  request(`/orgs/${slug}/jobs/`, { params });

export const getJob = (slug: string, job_id: string): Promise<JobRecord> =>
  request(`/orgs/${slug}/jobs/${job_id}`);

export const runJob = (
  slug: string,
  job_id: string,
  body: { cwd_override?: string; timeout_seconds?: number },
): Promise<JobRunResponse> =>
  request(`/orgs/${slug}/jobs/${job_id}/run`, { method: 'POST', body });

export const rejectJob = (
  slug: string,
  job_id: string,
  body: { reason: string },
): Promise<JobRecord> =>
  request(`/orgs/${slug}/jobs/${job_id}/reject`, { method: 'POST', body });

export const getJobOutput = (
  slug: string,
  job_id: string,
  params?: { stream?: 'stdout' | 'stderr' | 'both'; max_bytes?: number },
): Promise<JobOutput> =>
  request(`/orgs/${slug}/jobs/${job_id}/output`, { params });

export const jobEventsPath = (slug: string, job_id: string): string =>
  `/orgs/${slug}/jobs/${job_id}/events`;

export const tailJob = (
  slug: string,
  job_id: string,
  opts: { stream?: 'stdout' | 'stderr'; lines?: number } = {},
): Promise<JobTailResponse> =>
  request(`/orgs/${slug}/jobs/${job_id}/tail`, {
    params: {
      stream: opts.stream ?? 'stdout',
      lines: opts.lines ?? 50,
    },
  });

export const waitJob = (
  slug: string,
  job_id: string,
  timeout_seconds = 30,
): Promise<JobWaitResponse> =>
  request(`/orgs/${slug}/jobs/${job_id}/wait`, {
    method: 'POST',
    params: { timeout_seconds },
  });

export const stopJob = (
  slug: string,
  job_id: string,
): Promise<JobStopResponse> =>
  request(`/orgs/${slug}/jobs/${job_id}/stop`, { method: 'POST' });
