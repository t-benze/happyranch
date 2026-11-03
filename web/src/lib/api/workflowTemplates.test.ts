import { beforeEach, describe, expect, expectTypeOf, test, vi } from 'vitest';

vi.mock('./client', () => ({ request: vi.fn() }));

import { request } from './client';
import type { WorkflowDocumentReviewDefinition } from './types';
import {
  getWorkflowTemplate,
  listWorkflowTemplates,
  publishWorkflowTemplate,
  type WorkflowTemplatePublisher,
  type WorkflowTemplateVersion,
} from './workflowTemplates';

describe('workflow template API mirror', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  test('mirrors the exact principal and system publisher response contracts', () => {
    type PrincipalPublisher = {
      principal_kind: 'agent' | 'human';
      principal_id: string;
      proof_kind: 'task_session' | 'founder_bearer';
      task_id?: string;
      session_id?: string;
    };
    type SystemPublisher = {
      kind: 'system';
      component: 'coding-builtin';
      provenance_format: 'workflow-coding-builtin-registration@1';
    };
    type Publisher = PrincipalPublisher | SystemPublisher;

    expectTypeOf<WorkflowTemplatePublisher>().toEqualTypeOf<PrincipalPublisher>();
    expectTypeOf<WorkflowTemplateVersion['publisher']>().toEqualTypeOf<Publisher>();
    expectTypeOf<Awaited<ReturnType<typeof listWorkflowTemplates>>['templates'][number]['publisher']>()
      .toEqualTypeOf<Publisher>();
    expectTypeOf<Awaited<ReturnType<typeof getWorkflowTemplate>>['publisher']>()
      .toEqualTypeOf<Publisher>();
    expectTypeOf<Awaited<ReturnType<typeof publishWorkflowTemplate>>['publisher']>()
      .toEqualTypeOf<Publisher>();
  });

  test.each([
    { principal_kind: 'agent', principal_id: 'dev_agent', proof_kind: 'task_session',
      task_id: 'TASK-1', session_id: 'sess-1' },
    { principal_kind: 'human', principal_id: 'founder', proof_kind: 'founder_bearer' },
    { kind: 'system', component: 'coding-builtin',
      provenance_format: 'workflow-coding-builtin-registration@1' },
  ] as const)('narrows publisher response without synthetic principal fields (%j)', (value) => {
    const narrow = (publisher: WorkflowTemplateVersion['publisher']) => {
      if ('kind' in publisher) {
        expectTypeOf(publisher).toEqualTypeOf<{
          kind: 'system';
          component: 'coding-builtin';
          provenance_format: 'workflow-coding-builtin-registration@1';
        }>();
        expect(publisher.kind).toBe('system');
        expect(publisher.component).toBe('coding-builtin');
        expect(publisher.provenance_format).toBe('workflow-coding-builtin-registration@1');
        expect(publisher).not.toHaveProperty('principal_id');
      } else {
        expectTypeOf(publisher).toEqualTypeOf<WorkflowTemplatePublisher>();
        expectTypeOf(publisher.principal_kind).toEqualTypeOf<'agent' | 'human'>();
        expectTypeOf(publisher.proof_kind).toEqualTypeOf<'task_session' | 'founder_bearer'>();
        expect(publisher.principal_id).toBeDefined();
      }
    };
    narrow(value);
  });

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
