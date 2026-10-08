import { beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('./client', () => ({ request: vi.fn() }));

import { request } from './client';
import type { WorkflowDocumentReviewDefinition } from './types';
import {
  getWorkflowTemplate,
  listWorkflowTemplates,
  publishWorkflowTemplate,
} from './workflowTemplates';

describe('workflow template API mirror', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  const proposal: WorkflowDocumentReviewDefinition = {
    kind: 'document-review', schema_version: 2,
    description: 'Prepare a bounded written proposal for one human review.',
    author: { role: 'proposal-writer', kind: 'agent' },
    output: { primitive: 'immutable-document-revision', description: 'Written proposal' },
    reviewers: [{ role: 'sponsor', kind: 'human' }],
    outcomes: ['approved', 'changes_requested'],
    approval: { mode: 'all', revision: 'current', required_roles: ['sponsor'] },
    request_changes: { action: 'return-to-author', revision: 'new', invalidate: 'all-prior-receipts' },
    submission: { timing: 'on-completion' },
  };
  const approvalOnly: WorkflowDocumentReviewDefinition = { ...proposal,
    outcomes: ['approved'], request_changes: null };
  const agentOnly: WorkflowDocumentReviewDefinition = { ...proposal,
    reviewers: [{ role: 'sponsor', kind: 'agent' }] };
  test.each([
    ['legacy', { kind: 'product-design' }], ['proposal', proposal],
    ['A', approvalOnly], ['Z', agentOnly],
  ])('mirrors publish/list/get routes exactly (%s)', async (_name, definition) => {
    vi.mocked(request).mockResolvedValue({});
    await publishWorkflowTemplate('alpha', {
      team_slug: 'engineering', operation_key: 'op-1', template_name: 'product-design',
      expected_current_version: 0, definition,
    });
    await listWorkflowTemplates('alpha', 'engineering');
    await getWorkflowTemplate('alpha', 'engineering', 'product-design', 1);

    expect(request).toHaveBeenNthCalledWith(
      1, '/orgs/alpha/workflows/templates/publish', {
        method: 'POST',
        body: {
          team_slug: 'engineering', operation_key: 'op-1', template_name: 'product-design',
          expected_current_version: 0, definition,
        },
      },
    );
    expect(request).toHaveBeenNthCalledWith(
      2, '/orgs/alpha/workflows/templates', { params: { team_slug: 'engineering' } },
    );
    expect(request).toHaveBeenNthCalledWith(
      3, '/orgs/alpha/workflows/templates/engineering/product-design/1',
    );
  });
});
