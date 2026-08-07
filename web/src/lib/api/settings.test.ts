import { describe, expectTypeOf, it } from 'vitest';

import type { RuntimeRegistrationTokenMintRequest } from './settings';

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
