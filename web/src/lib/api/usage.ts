/** Mirror of runtime/daemon/routes/usage.py. */
import { request } from './client';

export interface UsageWindow {
  start_utc: string;
  end_utc: string;
  start_local: string;
  end_local: string;
}

export interface UsageDelta {
  kind: 'absolute' | 'percent' | 'new_from_zero' | 'no_change' | 'withheld';
  value: number | null;
  withheld_reason: string | null;
}

export interface RuntimeMetric {
  seconds: number;
  known: number;
  total: number;
}

export interface WorkloadPeriod {
  task_runs: number;
  thread_wakes: number;
  recorded_runtime: RuntimeMetric;
  deliveries: number;
  delivery_unclassified_results: number;
  replies: number;
}

export interface WorkloadAgent {
  agent: string;
  current: WorkloadPeriod;
  previous: WorkloadPeriod | null;
  deltas: Record<string, UsageDelta> | null;
}

export interface WorkloadResponse {
  generated_at: string;
  data_through: string;
  timezone: string;
  current_window: UsageWindow;
  previous_window: UsageWindow | null;
  agents: WorkloadAgent[];
}

export interface UsageCoverage {
  known: number;
  total: number;
  ratio: number | null;
}

export interface TokenMetric {
  value: number | null;
  n_reported: number;
  partial_count: number;
}

export interface KnownTotal {
  value: number | null;
  n_reported: number;
}

export interface DeclineWaste {
  state: 'no_declines' | 'reported';
  declined: number;
  total: number;
  rate: number | null;
  usage_known: number;
  fresh_input: KnownTotal;
  reread: KnownTotal;
  output: KnownTotal;
}

export interface EfficiencyPeriod {
  runs: number;
  usage_coverage: UsageCoverage;
  fresh_input: TokenMetric;
  reread: TokenMetric;
  output: TokenMetric;
  decline_waste: DeclineWaste | null;
}

export type EfficiencyRunType =
  | 'worker_task'
  | 'manager_decision'
  | 'thread_reply'
  | 'thread_followup'
  | 'dream';

export interface EfficiencyRow {
  run_type: EfficiencyRunType;
  current: EfficiencyPeriod;
  previous: EfficiencyPeriod | null;
  deltas: Record<string, UsageDelta> | null;
}

export interface CohortOption {
  executor: string;
  model: string | null;
  model_unpinned: boolean;
  current_runs: number;
  previous_runs: number;
}

export interface UnattributedCounts extends Record<EfficiencyRunType, number> {
  task_unclassified: number;
  recovery: number;
}

export interface EfficiencyResponse {
  generated_at: string;
  data_through: string;
  timezone: string;
  current_window: UsageWindow;
  previous_window: UsageWindow | null;
  cohorts: CohortOption[];
  unattributed: { current: UnattributedCounts; previous: UnattributedCounts | null };
  rows: EfficiencyRow[];
}

export const getWorkload = (
  slug: string,
  compare = false,
): Promise<WorkloadResponse> =>
  request(`/orgs/${slug}/usage/workload`, { params: { compare } });

export interface EfficiencyParams {
  compare?: boolean;
  executor?: string;
  model?: string;
  model_unpinned?: boolean;
}

export const getEfficiency = (
  slug: string,
  params?: EfficiencyParams,
): Promise<EfficiencyResponse> =>
  request(`/orgs/${slug}/usage/efficiency`, { params: params ? { ...params } : undefined });
