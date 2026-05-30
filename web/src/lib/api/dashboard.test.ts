import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as dashboard from './dashboard';
import * as clientModule from './client';

describe('dashboard api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getDashboardSummary calls the per-org summary route', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ heartbeat: [], escalations: [] });
    await dashboard.getDashboardSummary('demo');
    expect(spy).toHaveBeenCalledWith('/orgs/demo/dashboard/summary');
  });
});
