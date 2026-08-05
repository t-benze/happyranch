// THR-136: lifecycleStatusToValidationState and buildValidationFromLifecycleStatus
// are retired with the Create/Edit/Validate proposal flow. Direct agent submissions
// are synchronously published.
//
// This test file is intentionally empty — the removed functions had no callers
// after the proposal review UI retirement.
import { describe, it, expect } from 'vitest';

describe('_real-skills (THR-136)', () => {
  it('has no retired function tests — Create/Edit/Validate hooks are removed', () => {
    // THR-136: Direct agent submissions are published synchronously.
    // The old validation-pipeline helpers were removed with the proposal
    // review UI retirement.
    expect(true).toBe(true);
  });
});
