import { beforeEach, expect, test, vi } from 'vitest';
vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';
import { activateWorkflow, getWorkflowActivation, listWorkflowActivations } from './workflowActivations';
import type { WorkflowActivationRequest } from './types';

const body: WorkflowActivationRequest = {
  operation_key: 'same-key', instance_id: 'design-one', expected_activation_revision: 0,
  template: { identity_id: 'template:one', version: 1, definition_digest: 'd'.repeat(64) },
  authority: { namespace: 'org/alpha', generation: 1, snapshot_digest: 'e'.repeat(64) },
  scope: { brief: 'bounded initial draft' },
  bindings: {
    'product-lead': { kind: 'agent', principal: 'product_lead', team: 'product' },
    founder: { kind: 'human', principal: 'founder', team: null },
    implementer: { kind: 'agent', principal: 'dev_agent', team: 'engineering' },
    tester: { kind: 'agent', principal: 'qa_engineer', team: 'engineering' },
  },
  eligible_replacements: { 'product-lead': [], founder: [], implementer: [], tester: [] },
  allowed_actions: ['draft-document'], inputs: [],
};

beforeEach(() => { vi.mocked(request).mockReset(); });

test('activation client forwards exact request and reads original pending identity', async () => {
  const pending = { activation_id: 'activation:one', root_task_id: 'TASK-001', intent_id: 'original',
    pending: true, execution_started: false, reconciliation_required: false };
  vi.mocked(request).mockResolvedValueOnce(pending).mockResolvedValueOnce([pending]).mockResolvedValueOnce(pending);
  expect(await activateWorkflow('alpha', body)).toBe(pending);
  expect(await listWorkflowActivations('alpha')).toEqual([pending]);
  expect(await getWorkflowActivation('alpha', 'activation:one')).toBe(pending);
  expect(request).toHaveBeenNthCalledWith(1, '/orgs/alpha/workflows/activations', { method: 'POST', body });
  expect(request).toHaveBeenNthCalledWith(2, '/orgs/alpha/workflows/activations');
  expect(request).toHaveBeenNthCalledWith(3, '/orgs/alpha/workflows/activations/activation%3Aone');
  expect(request).toHaveBeenCalledTimes(3);
});

test('activation client keeps rejected promises and never remints an operation key', async () => {
  const error = new Error('workflow_activation_operation_conflict');
  vi.mocked(request).mockRejectedValue(error);
  await expect(activateWorkflow('alpha', body)).rejects.toBe(error);
  await expect(listWorkflowActivations('alpha')).rejects.toBe(error);
  await expect(getWorkflowActivation('alpha', 'activation:one')).rejects.toBe(error);
  expect(request).toHaveBeenNthCalledWith(1, '/orgs/alpha/workflows/activations', { method: 'POST', body });
  expect(request).toHaveBeenCalledTimes(3);
});
