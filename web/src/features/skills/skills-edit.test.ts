import { describe, expect, test } from 'vitest';
import type { AgentAssignmentFacts } from './skills-detail';
import { agentProvenanceList } from './skills-detail';
import {
  buildEditSkillRequest,
  editFormErrors,
  editSuccessHeadline,
  effectiveAfterEdit,
  isEditFormSubmittable,
  isVersionBumped,
  prefillEditForm,
  type EditSkillFormValues,
} from './skills-edit';

function form(over: Partial<EditSkillFormValues> = {}): EditSkillFormValues {
  return {
    name: 'Incident postmortem',
    version: 'v1',
    summary: 'What this is for.',
    skillMd: '',
    references: [],
    assets: [],
    ...over,
  };
}

describe('skills-edit — prefill', () => {
  test('seeds name/summary/version from the detail facts; leaves body + maps empty', () => {
    expect(
      prefillEditForm({ name: 'N', summary: 'S', version: '2.0.0' }),
    ).toEqual({
      name: 'N',
      summary: 'S',
      version: '2.0.0',
      skillMd: '',
      references: [],
      assets: [],
    });
  });

  test('missing detail fields fall back to empty strings, never undefined', () => {
    expect(prefillEditForm({})).toEqual({
      name: '',
      summary: '',
      version: '',
      skillMd: '',
      references: [],
      assets: [],
    });
  });
});

describe('skills-edit — build PATCH request', () => {
  test('sends name + summary trimmed, and version when present', () => {
    const body = buildEditSkillRequest(
      form({ name: '  Triage  ', summary: '  brief ', version: '  v2 ' }),
    );
    expect(body).toEqual({ name: 'Triage', summary: 'brief', version: 'v2' });
  });

  test('NEVER emits policy_class — an edit cannot mint or alter a system contract', () => {
    const body = buildEditSkillRequest(form({ skillMd: '# x' }));
    expect(body).not.toHaveProperty('policy_class');
  });

  test('blank SKILL.md is omitted so the daemon keeps the current guidance', () => {
    const body = buildEditSkillRequest(form({ skillMd: '   ' }));
    expect(body).not.toHaveProperty('skill_md');
  });

  test('a non-blank SKILL.md is sent verbatim (untrimmed) to replace the guidance', () => {
    const body = buildEditSkillRequest(form({ skillMd: '# New\n\nbody\n' }));
    expect(body.skill_md).toBe('# New\n\nbody\n');
  });

  test('blank version is omitted so the stored version is preserved', () => {
    const body = buildEditSkillRequest(form({ version: '   ' }));
    expect(body).not.toHaveProperty('version');
  });

  test('references/assets are sent only when at least one named file is present', () => {
    const empty = buildEditSkillRequest(
      form({
        references: [{ name: '  ', content: 'orphan' }],
        assets: [],
      }),
    );
    expect(empty).not.toHaveProperty('references');
    expect(empty).not.toHaveProperty('assets');

    const withFiles = buildEditSkillRequest(
      form({
        references: [{ name: 'notes.md', content: 'hi' }],
        assets: [{ name: 'd.svg', content: '<svg/>' }],
      }),
    );
    expect(withFiles.references).toEqual({ 'notes.md': 'hi' });
    expect(withFiles.assets).toEqual({ 'd.svg': '<svg/>' });
  });
});

describe('skills-edit — form guard', () => {
  test('name is required; version + body are not', () => {
    expect(editFormErrors(form({ name: '   ' }))).toEqual(['Add a name.']);
    expect(editFormErrors(form({ version: '', skillMd: '' }))).toEqual([]);
    expect(isEditFormSubmittable(form())).toBe(true);
    expect(isEditFormSubmittable(form({ name: '' }))).toBe(false);
  });
});

describe('skills-edit — version bump detection', () => {
  test('detects a changed version, ignores whitespace, and treats a blank before as no-bump', () => {
    expect(isVersionBumped('1.0.0', '1.1.0')).toBe(true);
    expect(isVersionBumped(' 1.0.0 ', '1.0.0')).toBe(false);
    expect(isVersionBumped('', '1.0.0')).toBe(false);
  });
});

describe('skills-edit — edited-effective model', () => {
  const assignments: AgentAssignmentFacts[] = [
    { agent: 'a', assigned: true, effective: true, state: 'effective' },
    {
      agent: 'b',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    },
    { agent: 'c', assigned: false, effective: false, state: 'effective' },
  ];

  test('a version bump moves every already-effective agent to not-yet-effective', () => {
    const next = effectiveAfterEdit(assignments, true);
    expect(next[0]).toEqual({
      agent: 'a',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    });
    // Already not-yet-effective + not-assigned rows are untouched.
    expect(next[1]).toEqual(assignments[1]);
    expect(next[2]).toEqual(assignments[2]);
  });

  test('without a version bump the facts pass through unchanged', () => {
    expect(effectiveAfterEdit(assignments, false)).toBe(assignments);
  });

  test('reuses the Slice-2 provenance vocabulary — the bumped agent reads "Takes effect next session", never "active"', () => {
    const prov = agentProvenanceList(effectiveAfterEdit(assignments, true));
    const a = prov.find((p) => p.agent === 'a')!;
    expect(a.status).toBe('not_yet_effective');
    expect(a.statusLabel).toBe('Takes effect next session');
    expect(a.takesEffectNextSession).toBe(true);
    expect(a.reason).toMatch(/takes effect at this agent’s next session/i);
    expect(a.statusLabel.toLowerCase()).not.toContain('active');
  });
});

describe('skills-edit — success headline', () => {
  test('names the next-session effect only when the version was bumped', () => {
    expect(editSuccessHeadline(true)).toMatch(/next session/i);
    expect(editSuccessHeadline(false)).not.toMatch(/next session/i);
    // Guidance-visibility language only — never permission / approval wording.
    expect(editSuccessHeadline(true)).not.toMatch(
      /materializ|admit|permission|approve|grant|\bpending\b|\bactive\b/i,
    );
  });
});
