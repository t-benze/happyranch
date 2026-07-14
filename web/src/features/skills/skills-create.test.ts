import { describe, expect, test } from 'vitest';
import {
  buildCreateSkillRequest,
  createFormErrors,
  emptyCreateSkillForm,
  failureHeadline,
  isCreateFormSubmittable,
  isValidationPassed,
  plainValidationError,
  plainValidationErrors,
  successHeadline,
  VALIDATION_CHECKS,
  type CreateSkillFormValues,
} from './skills-create';

function form(over: Partial<CreateSkillFormValues> = {}): CreateSkillFormValues {
  return { ...emptyCreateSkillForm(), ...over };
}

describe('skills-create — build request', () => {
  test('assembles required fields and trims', () => {
    const body = buildCreateSkillRequest(
      form({ slug: '  triage-runbook ', name: ' Triage Runbook ', skillMd: '# Triage\n' }),
    );
    expect(body).toEqual({
      slug: 'triage-runbook',
      name: 'Triage Runbook',
      skill_md: '# Triage\n',
    });
  });

  test('omits empty optional fields rather than sending blanks', () => {
    const body = buildCreateSkillRequest(
      form({ slug: 's', name: 'n', skillMd: 'b', version: '   ', summary: '' }),
    );
    expect(body).not.toHaveProperty('version');
    expect(body).not.toHaveProperty('summary');
    expect(body).not.toHaveProperty('references');
    expect(body).not.toHaveProperty('assets');
  });

  test('includes version/summary when present', () => {
    const body = buildCreateSkillRequest(
      form({ slug: 's', name: 'n', skillMd: 'b', version: 'v1', summary: 'hi' }),
    );
    expect(body.version).toBe('v1');
    expect(body.summary).toBe('hi');
  });

  test('folds references/assets into records, dropping blank-name rows', () => {
    const body = buildCreateSkillRequest(
      form({
        slug: 's',
        name: 'n',
        skillMd: 'b',
        references: [
          { name: ' notes.md ', content: 'a' },
          { name: '', content: 'ignored' },
        ],
        assets: [{ name: 'logo.svg', content: '<svg/>' }],
      }),
    );
    expect(body.references).toEqual({ 'notes.md': 'a' });
    expect(body.assets).toEqual({ 'logo.svg': '<svg/>' });
  });

  test('NEVER emits policy_class — custom skills are user_authored only', () => {
    const body = buildCreateSkillRequest(
      form({ slug: 's', name: 'n', skillMd: 'b' }),
    );
    expect(body).not.toHaveProperty('policy_class');
    // and the wire type has no such key regardless of input
    expect(JSON.stringify(body)).not.toMatch(/policy_class|system_contract/i);
  });
});

describe('skills-create — required-field guard', () => {
  test('flags missing slug / name / skill_md', () => {
    expect(createFormErrors(form())).toHaveLength(3);
    expect(isCreateFormSubmittable(form())).toBe(false);
  });

  test('version is optional', () => {
    const v = form({ slug: 's', name: 'n', skillMd: 'b' });
    expect(createFormErrors(v)).toEqual([]);
    expect(isCreateFormSubmittable(v)).toBe(true);
  });
});

describe('skills-create — validation result state', () => {
  test('validation.ok=true is a pass', () => {
    expect(
      isValidationPassed({
        skill_id: 'x',
        validation_state: 'validated',
        validation: { ok: true, errors: [] },
      }),
    ).toBe(true);
  });

  test('validation.ok=false is a fail even if state lags', () => {
    expect(
      isValidationPassed({
        skill_id: 'x',
        validation_state: 'validated',
        validation: { ok: false, errors: ['x'] },
      }),
    ).toBe(false);
  });

  test('falls back to validation_state when no validation block', () => {
    expect(
      isValidationPassed({ skill_id: 'x', validation_state: 'in_catalog', validation: null }),
    ).toBe(false);
    expect(
      isValidationPassed({ skill_id: 'x', validation_state: 'validated', validation: undefined }),
    ).toBe(true);
  });
});

describe('skills-create — plain-language error mapper', () => {
  test('maps the system-contract family', () => {
    expect(plainValidationError('policy_class system_contract is reserved')).toMatch(
      /can’t declare a system contract/i,
    );
  });

  test('maps a slug collision', () => {
    expect(plainValidationError("slug collides with release skill 'jobs'")).toMatch(
      /already used by a bundled skill/i,
    );
  });

  test('maps a missing SKILL.md heading', () => {
    expect(plainValidationError('missing SKILL.md heading')).toMatch(
      /Add the SKILL\.md guidance body/i,
    );
  });

  test('maps an unresolved reference/asset', () => {
    expect(
      plainValidationError('The references/pricing.md asset could not be resolved.'),
    ).toMatch(/referenced file couldn’t be found/i);
  });

  test('passes unknown errors through, trimmed', () => {
    expect(plainValidationError('  some novel backend note  ')).toBe(
      'some novel backend note',
    );
  });

  test('drops blank lines from the list', () => {
    expect(plainValidationErrors(['missing SKILL.md heading', '', '  '])).toHaveLength(1);
  });
});

describe('skills-create — validation-check explanation covers every check', () => {
  test('one entry per §8.3 check the brief enumerates', () => {
    const keys = VALIDATION_CHECKS.map((c) => c.key);
    expect(keys).toEqual([
      'parses',
      'metadata',
      'skill_md',
      'files_resolve',
      'slug_unique',
      'stays_custom',
      'dry_assembly',
    ]);
  });

  test('every check has a title and a description', () => {
    for (const c of VALIDATION_CHECKS) {
      expect(c.title.trim().length).toBeGreaterThan(0);
      expect(c.description.trim().length).toBeGreaterThan(0);
    }
  });
});

describe('skills-create — copy discipline (guidance-visibility, mirrors skills-detail.test.ts)', () => {
  // Every user-facing string this module PRODUCES must read as guidance
  // visibility, never permission / lifecycle / approval jargon, and never a
  // user-facing "active". Scan the check explanations, the result headlines,
  // and the mapped error messages together.
  const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;

  const mappedErrors = [
    'policy_class system_contract is reserved',
    "slug collides with release skill 'jobs'",
    'missing SKILL.md heading',
    'missing version field',
    'field id is required',
    'The references/pricing.md asset could not be resolved.',
    'skill.yaml failed to parse',
  ].map(plainValidationError);

  const strings = [
    successHeadline(),
    failureHeadline(1),
    failureHeadline(3),
    ...VALIDATION_CHECKS.flatMap((c) => [c.title, c.description]),
    ...mappedErrors,
  ];

  test('no forbidden permission/lifecycle/approval wording', () => {
    for (const s of strings) {
      expect(s).not.toMatch(forbidden);
    }
  });

  test('no user-facing "active"', () => {
    for (const s of strings) {
      expect(s).not.toMatch(/\bactive\b/i);
    }
  });
});
