import { describe, expectTypeOf, it } from 'vitest';

import type { RuntimeRegistrationTokenMintRequest } from './settings';
import type { OrgSettings } from './types';

describe('RuntimeRegistrationTokenMintRequest', () => {
  it('accepts the optional Slice-1A direct-authority workspace adapter', () => {
    const request: RuntimeRegistrationTokenMintRequest = {
      name: 'custom-cli',
      purpose: 'adapter',
      intended_profile_name: 'custom-profile',
      workspace_adapter_id: 'codex',
    };

    expectTypeOf(request.workspace_adapter_id).toEqualTypeOf<
      'claude' | 'codex' | 'opencode' | 'pi' | undefined
    >();
  });
});

describe('OrgSettings response contract', () => {
  it('accepts the server-returned reviewer_agents field without creating UI logic', () => {
    expectTypeOf<OrgSettings['reviewer_agents']>().toEqualTypeOf<string[]>();
  });
});
