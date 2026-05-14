/** Mirror of src/daemon/routes/health.py */
import { request } from './client';
import type { HealthResponse } from './types';

export const getHealth = (): Promise<HealthResponse> => request('/health');
