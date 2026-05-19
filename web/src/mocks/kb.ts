import type { KBEntry } from '@/lib/api/kb';

export const MOCK_KB_ENTRIES: KBEntry[] = [
  {
    slug: 'policy/refund-thresholds',
    title: 'Refund authority by tier',
    type: 'precedent',
    topic: 'finance',
    tags: ['policy', 'finance', 'customer-care'],
    body:
      '# Refund authority\n\nThe CX Manager may approve refunds up to **$150**.' +
      ' Beyond that, escalate to the founder.\n',
    updated_at: '2026-05-16T09:00:00Z',
    authored_by: 'founder',
    source_task: 'TASK-0042',
    related_entries: ['intake/spanish-walk-ins'],
  },
  {
    slug: 'intake/spanish-walk-ins',
    title: 'Spanish-speaking walk-in flow',
    type: 'sop',
    topic: 'intake',
    tags: ['intake', 'language-spanish'],
    body: '# Spanish walk-ins\n\nGreet in Spanish, hand off to translator.\n',
    updated_at: '2026-05-12T11:00:00Z',
    authored_by: 'intake_manager',
    source_task: null,
    related_entries: [],
  },
  {
    slug: 'routing/macau-after-hours',
    title: 'Macau after-hours routing',
    type: 'guide',
    topic: 'routing',
    tags: ['routing', 'macau'],
    body: '# After-hours\n\nRoute to the partner concierge.\n',
    updated_at: '2026-05-08T22:00:00Z',
    authored_by: 'ops_manager',
    source_task: null,
    related_entries: [],
  },
];
