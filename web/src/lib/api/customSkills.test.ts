import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import { __resetTokenCacheForTests } from '../auth';
import { saveCustomSkillEligibility } from './customSkills';

describe('custom skills API client', () => {
  test('commits eligibility with the server revision in If-Match', async () => {
    __resetTokenCacheForTests();
    sessionStorage.setItem('happyranch.token', 'founder-token');
    let ifMatch: string | null = null;
    server.use(http.put('/api/v1/orgs/ranch/custom-skills/custom:one/eligibility', async ({ request }) => {
      ifMatch = request.headers.get('if-match');
      expect(await request.json()).toEqual([{ scope_type: 'org', scope_target: null, effect: 'allow' }]);
      return HttpResponse.json({ newly_visible: ['ada'], newly_hidden: [], unchanged: [], revision: 42 });
    }));
    await expect(saveCustomSkillEligibility('ranch', 'custom:one', [{ scope_type: 'org', scope_target: null, effect: 'allow' }], 42)).resolves.toMatchObject({ revision: 42 });
    expect(ifMatch).toBe('42');
  });
});
