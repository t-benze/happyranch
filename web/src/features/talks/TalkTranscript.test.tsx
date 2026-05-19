import { describe, expect, test } from 'vitest';
import { splitTranscript } from './TalkTranscript';

describe('splitTranscript', () => {
  test('falls back to a single section when no speaker markers', () => {
    const sections = splitTranscript('Just some prose.\nNo markers.');
    expect(sections).toEqual([
      { speaker: null, body: 'Just some prose.\nNo markers.' },
    ]);
  });

  test('splits on ## founder / ## agent headings', () => {
    const md = [
      '## founder',
      'Q: how do we handle the visa update?',
      '## agent',
      'A: we cross-check the immigration feed daily.',
    ].join('\n');
    const sections = splitTranscript(md);
    expect(sections.map((s) => s.speaker)).toEqual(['founder', 'agent']);
    expect(sections[0].body).toContain('visa update');
    expect(sections[1].body).toContain('immigration feed');
  });

  test('splits on **founder:** / **agent_x:** bold markers', () => {
    const md = '**founder:**\nhi\n**agent_x:**\nhello back';
    const sections = splitTranscript(md);
    expect(sections.map((s) => s.speaker)).toEqual(['founder', 'agent']);
  });

  test('drops empty sections', () => {
    const md = '## founder\n\n## agent\nactual content';
    const sections = splitTranscript(md);
    expect(sections).toEqual([{ speaker: 'agent', body: 'actual content' }]);
  });
});
