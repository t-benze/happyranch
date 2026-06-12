import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { cn } from '@/lib/utils';
import { useJobsList, useJobsRoutes } from '@/hooks/jobs';
import type { JobRecord, JobStatus } from '@/lib/api/types';
import { JobDetailPane } from './JobDetailPane';

const STATUSES: FilterGroup['options'] = [
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
];

const REVIEW_REQUIRED: FilterGroup['options'] = [
  { value: 'true', label: 'Review required' },
  { value: 'false', label: 'Auto-run' },
];

const PERSISTENT: FilterGroup['options'] = [
  { value: 'true', label: 'Persistent' },
  { value: 'false', label: 'Bounded' },
];

// Job-specific status colours (extends design-system tokens where possible)
const STATUS_CLASS: Record<JobStatus, string> = {
  pending: 'bg-tier-yellow-tint text-status-archiving',
  running: 'bg-tier-green-tint text-status-open',
  completed: 'border border-border-subtle bg-transparent text-status-archived',
  failed: 'bg-tier-red-tint text-status-abandoned',
  rejected: 'border border-border-subtle bg-transparent text-fg-muted',
};

function JobStatusBadge({ status }: { status: JobStatus }): JSX.Element {
  return (
    <span
      className={cn(
        'text-mono-sm inline-flex items-center rounded-sm px-2 py-px font-mono text-xs font-semibold',
        STATUS_CLASS[status],
      )}
    >
      {status}
    </span>
  );
}

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

interface JobCardProps {
  job: JobRecord;
  to: string;
  active?: boolean;
}

function JobCard({ job, to, active }: JobCardProps): JSX.Element {
  return (
    <Link
      to={to}
      className={cn(
        'border-border-subtle bg-surface-raised block rounded-lg border p-3',
        active && 'ring-accent ring-2',
        'hover:bg-surface-raised/80',
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <span className="text-id-task font-mono">{job.id}</span>
        <JobStatusBadge status={job.status} />
        {job.persistent && (
          <span className="text-mono-sm border-border-subtle rounded-sm border px-1 py-px font-mono uppercase tracking-wider text-fg-muted">
            persistent
          </span>
        )}
        {job.review_required && (
          <span className="text-mono-sm border-border-subtle rounded-sm border px-1 py-px font-mono uppercase tracking-wider text-fg-muted">
            review
          </span>
        )}
        <span className="text-fg-muted">{job.agent_name}</span>
        <span className="text-fg-muted">· {job.task_id}</span>
        <span className="text-fg-muted ml-auto">{relativeAge(job.created_at)}</span>
      </div>
      <p className="text-fg mt-1 line-clamp-1 text-sm font-medium">{job.title}</p>
      {job.rationale && (
        <p className="text-fg-muted mt-0.5 line-clamp-2 text-xs">{job.rationale}</p>
      )}
    </Link>
  );
}

export function JobsPage(): JSX.Element {
  const { job_id: openJobId } = useParams<{ job_id: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    status: null,
    review_required: null,
    persistent: null,
  });
  const routes = useJobsRoutes();
  const jobsQuery = useJobsList({
    status: filters.status ?? 'all',
    review_required: filters.review_required ?? undefined,
    persistent: filters.persistent ?? undefined,
  });

  const jobs = jobsQuery.data?.jobs ?? [];

  const groups: FilterGroup[] = [
    { key: 'status', label: 'Status', options: STATUSES },
    { key: 'review_required', label: 'Review', options: REVIEW_REQUIRED },
    { key: 'persistent', label: 'Persistence', options: PERSISTENT },
  ];

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        {jobsQuery.isLoading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : jobs.length === 0 ? (
          <EmptyState
            title="No jobs"
            body="Background jobs submitted by agents will appear here."
          />
        ) : (
          <ul className="space-y-2">
            {jobs.map((job) => (
              <li key={job.id}>
                <JobCard
                  job={job}
                  to={routes.detail(job.id)}
                  active={openJobId === job.id}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openJobId && <JobDetailPane jobId={openJobId} />}
    </div>
  );
}
