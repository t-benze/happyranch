/**
 * Mock organizations for the prototype harness.
 *
 * One canonical demo org keeps URLs stable in `/__prototypes/*` and lets
 * compositions that build paths like `/orgs/${slug}/...` work unmodified.
 */
import type { OrgsListResponse } from '@/lib/api/types';

export const MOCK_ORG_SLUG = 'demo-org';

export const MOCK_ORGS: OrgsListResponse['orgs'] = [
  { slug: MOCK_ORG_SLUG, root: '/var/grassland/demo-org' },
];
