/** Mirror of src/daemon/routes/health.py */
import { request } from './client';
import type { HealthResponse, HealthReadyResponse, PrereqsResponse } from './types';

export const getHealth = (): Promise<HealthResponse> => request('/health');

export const getHealthReady = (): Promise<HealthReadyResponse> => request('/health/ready');

export const getPrereqs = (): Promise<PrereqsResponse> => request('/health/prereqs');
