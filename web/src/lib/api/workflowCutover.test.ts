import { beforeEach, expect, test, vi } from 'vitest';
vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';
import {
  getWorkflowCutover, requestWorkflowCutover, getWorkflowCutoverDowngradePreflight,
} from './workflowCutover';
import type { WorkflowCutoverRequestInput } from './types';

beforeEach(() => {
  vi.mocked(request).mockReset();
});

test('mirrors all three cutover methods and forwards the strict request unchanged', async () => {
  const pending = { state: 'enable_requested', reconciliation_required: true, request_event_id: 'cutover-event-2' };
  vi.mocked(request).mockResolvedValue(pending);
  const body: WorkflowCutoverRequestInput = { action: 'enable', operation_key: 'one', expected_generation: 1 };
  expect(await getWorkflowCutover('alpha')).toBe(pending);
  expect(await requestWorkflowCutover('alpha', body)).toBe(pending);
  expect(await getWorkflowCutoverDowngradePreflight('alpha')).toBe(pending);
  expect(request).toHaveBeenNthCalledWith(1, '/orgs/alpha/workflows/cutover');
  expect(request).toHaveBeenNthCalledWith(2, '/orgs/alpha/workflows/cutover/requests', { method: 'POST', body });
  expect(request).toHaveBeenNthCalledWith(3, '/orgs/alpha/workflows/cutover/downgrade-preflight');
});

test('keeps conflict and transport failures visible without creating a new operation key', async () => {
  const error = new Error('cutover_operation_conflict');
  vi.mocked(request).mockRejectedValue(error);
  const body: WorkflowCutoverRequestInput = { action: 'disable', operation_key: 'same', expected_generation: 4 };
  await expect(requestWorkflowCutover('alpha', body)).rejects.toBe(error);
  expect(request).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledWith('/orgs/alpha/workflows/cutover/requests', { method: 'POST', body });
});
