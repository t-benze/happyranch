import { describe, expect, test } from 'vitest';
import {
  deriveArtifactType,
  deriveTitle,
  formatProvenanceDate,
  parseProvenance,
} from './artifact-meta';

describe('deriveArtifactType', () => {
  test('classifies docs by extension', () => {
    expect(deriveArtifactType('product-update.md')).toBe('doc');
    expect(deriveArtifactType('notes.txt')).toBe('doc');
    expect(deriveArtifactType('report.pdf')).toBe('doc');
    expect(deriveArtifactType('READOUT.MARKDOWN')).toBe('doc');
  });

  test('classifies designs/images by extension', () => {
    expect(deriveArtifactType('dev_agent-2026-06-10-TASK-081-label.png')).toBe('design');
    expect(deriveArtifactType('shot.JPG')).toBe('design');
    expect(deriveArtifactType('spend-surface-v2.fig')).toBe('design');
    expect(deriveArtifactType('logo.svg')).toBe('design');
  });

  test('classifies patches by extension', () => {
    expect(deriveArtifactType('audit-dedupe.patch')).toBe('patch');
    expect(deriveArtifactType('fix.diff')).toBe('patch');
  });

  test('classifies pull requests by an explicit PR/number token, beating the extension', () => {
    expect(deriveArtifactType('dev_agent-2026-06-12-PR-101-summary.md')).toBe('pull-request');
    expect(deriveArtifactType('pr-104-artifacts.md')).toBe('pull-request');
    expect(deriveArtifactType('pull-request-104.txt')).toBe('pull-request');
    expect(deriveArtifactType('PR#88.diff')).toBe('pull-request');
  });

  test('does NOT mistake words that merely contain "pr" for pull requests', () => {
    // No digit token after "pr" -> these stay classified by extension.
    expect(deriveArtifactType('product-roadmap.md')).toBe('doc');
    expect(deriveArtifactType('approve-flow.md')).toBe('doc');
    expect(deriveArtifactType('sprint-notes.txt')).toBe('doc');
  });

  test('falls back to "file" for unknown/other extensions', () => {
    expect(deriveArtifactType('export.csv')).toBe('file');
    expect(deriveArtifactType('bundle.tar.gz')).toBe('file');
    expect(deriveArtifactType('noextension')).toBe('file');
  });
});

describe('parseProvenance', () => {
  test('parses the <agent>-<YYYY-MM-DD>-<slug> convention', () => {
    expect(parseProvenance('dev_agent-2026-06-11-design-handoff-product-update.md')).toEqual({
      agent: 'dev_agent',
      date: '2026-06-11',
      threadId: null,
    });
    expect(parseProvenance('code_reviewer-2026-06-01-project-structure-review.md')).toEqual({
      agent: 'code_reviewer',
      date: '2026-06-01',
      threadId: null,
    });
  });

  test('surfaces an embedded THR-NNN token (normalised upper-case) alongside agent/date', () => {
    expect(parseProvenance('engineering_manager-2026-06-16-THR-030-artifacts.png')).toEqual({
      agent: 'engineering_manager',
      date: '2026-06-16',
      threadId: 'THR-030',
    });
    expect(parseProvenance('dev_agent-2026-06-16-thr-7-note.md').threadId).toBe('THR-7');
  });

  test('returns a neutral all-null shape for names that do not match the convention — never fabricates', () => {
    expect(parseProvenance('report.pdf')).toEqual({ agent: null, date: null, threadId: null });
    expect(parseProvenance('export.csv')).toEqual({ agent: null, date: null, threadId: null });
  });

  test('surfaces a standalone THR token even when the agent/date prefix is absent', () => {
    expect(parseProvenance('THR-007-standalone-note.txt')).toEqual({
      agent: null,
      date: null,
      threadId: 'THR-007',
    });
  });
});

describe('deriveTitle', () => {
  test('strips the <agent>-<date>- prefix to a clean slug title', () => {
    expect(deriveTitle('dev_agent-2026-06-11-design-handoff-product-update.md')).toBe(
      'design-handoff-product-update.md',
    );
    expect(deriveTitle('engineering_manager-2026-06-16-THR-030-artifacts.png')).toBe(
      'THR-030-artifacts.png',
    );
  });

  test('keeps the full name when the convention does not match', () => {
    expect(deriveTitle('report.pdf')).toBe('report.pdf');
    expect(deriveTitle('export.csv')).toBe('export.csv');
  });
});

describe('formatProvenanceDate', () => {
  test('formats a YYYY-MM-DD string without timezone drift', () => {
    expect(formatProvenanceDate('2026-06-16')).toBe('Jun 16, 2026');
    expect(formatProvenanceDate('2026-01-01')).toBe('Jan 1, 2026');
    expect(formatProvenanceDate('2026-12-31')).toBe('Dec 31, 2026');
  });
});
