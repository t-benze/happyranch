import { describe, expect, test } from 'vitest';
import {
  lifecycleStatusToValidationState,
  buildValidationFromLifecycleStatus,
} from './_real-skills';

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

describe('buildValidationFromLifecycleStatus — TASK-3491 full response-facts seam', () => {
  test('raw current_status=proposed → validation_state=proposed, validation.ok=false', () => {
    const result = buildValidationFromLifecycleStatus({
      current_status: 'proposed',
      skill_id: 'hr:test-skill',
    });
    expect(result.validation_state).toBe('proposed');
    expect(result.validation.ok).toBe(false);
    expect(result.skill_id).toBe('hr:test-skill');
  });

  test('raw current_status=validated → validation_state=validated, validation.ok=true', () => {
    const result = buildValidationFromLifecycleStatus({
      current_status: 'validated',
      skill_id: 'hr:test-skill',
    });
    expect(result.validation_state).toBe('validated');
    expect(result.validation.ok).toBe(true);
  });

  test('raw current_status=approved → validation_state=validated, validation.ok=true', () => {
    const result = buildValidationFromLifecycleStatus({
      current_status: 'approved',
      skill_id: 'hr:test-skill',
    });
    expect(result.validation_state).toBe('validated');
    expect(result.validation.ok).toBe(true);
  });

  test('raw current_status=published → validation_state=validated, validation.ok=true', () => {
    const result = buildValidationFromLifecycleStatus({
      current_status: 'published',
      skill_id: 'hr:test-skill',
    });
    expect(result.validation_state).toBe('validated');
    expect(result.validation.ok).toBe(true);
  });

  test('raw current_status=validation_failed → validation_state=failed_validation, validation.ok=false', () => {
    const result = buildValidationFromLifecycleStatus({
      current_status: 'validation_failed',
      skill_id: 'hr:test-skill',
    });
    expect(result.validation_state).toBe('failed_validation');
    expect(result.validation.ok).toBe(false);
  });

  test('null / unknown status → validation_state=proposed, validation.ok=false (fail-closed)', () => {
    const resultNull = buildValidationFromLifecycleStatus({
      current_status: null,
      skill_id: 'hr:test-skill',
    });
    expect(resultNull.validation_state).toBe('proposed');
    expect(resultNull.validation.ok).toBe(false);

    const resultUnknown = buildValidationFromLifecycleStatus({
      current_status: 'unknown_status',
      skill_id: 'hr:test-skill',
    });
    expect(resultUnknown.validation_state).toBe('proposed');
    expect(resultUnknown.validation.ok).toBe(false);
  });
});
