/** Mirror of src/daemon/routes/health.py */
import { request } from './client';
import type { HealthResponse, PrereqsResponse } from './types';

export const getHealth = (): Promise<HealthResponse> => request('/health');

export const getPrereqs = (): Promise<PrereqsResponse> => request('/health/prereqs');
