import { describe, expect, test } from 'vitest';
import { lifecycleStatusToValidationState } from './_real-skills';

describe('lifecycleStatusToValidationState — TASK-3488 provider-seam regression', () => {
  test('raw current_status=proposed maps to proposed (NOT validated)', () => {
    expect(lifecycleStatusToValidationState('proposed')).toBe('proposed');
  });

  test('raw current_status=validated maps to validated (post-transition)', () => {
    expect(lifecycleStatusToValidationState('validated')).toBe('validated');
  });

  test('raw current_status=approved maps to validated (post-transition)', () => {
    expect(lifecycleStatusToValidationState('approved')).toBe('validated');
  });

  test('raw current_status=published maps to validated (post-transition)', () => {
    expect(lifecycleStatusToValidationState('published')).toBe('validated');
  });

  test('raw current_status=validation_failed maps to failed_validation', () => {
    expect(lifecycleStatusToValidationState('validation_failed')).toBe(
      'failed_validation',
    );
  });

  test('null / unknown status falls back to proposed (fail-closed)', () => {
    expect(lifecycleStatusToValidationState(null)).toBe('proposed');
    expect(lifecycleStatusToValidationState('unknown_status')).toBe('proposed');
  });
});
