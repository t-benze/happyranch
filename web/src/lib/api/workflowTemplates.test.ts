import { beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('./client', () => ({ request: vi.fn() }));

import { request } from './client';
import {
  getWorkflowTemplate,
  listWorkflowTemplates,
  publishWorkflowTemplate,
} from './workflowTemplates';

describe('workflow template API mirror', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  test('mirrors publish/list/get routes exactly', async () => {
    vi.mocked(request).mockResolvedValue({});
    await publishWorkflowTemplate('alpha', {
      team_slug: 'engineering', operation_key: 'op-1', template_name: 'product-design',
      expected_current_version: 0, definition: { kind: 'product-design' },
    });
    await listWorkflowTemplates('alpha', 'engineering');
    await getWorkflowTemplate('alpha', 'engineering', 'product-design', 1);

    expect(request).toHaveBeenNthCalledWith(
      1, '/orgs/alpha/workflows/templates/publish', {
        method: 'POST',
        body: {
          team_slug: 'engineering', operation_key: 'op-1', template_name: 'product-design',
          expected_current_version: 0, definition: { kind: 'product-design' },
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
