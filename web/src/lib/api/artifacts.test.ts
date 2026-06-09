import { afterEach, describe, expect, test, vi } from 'vitest';
import { uploadArtifact } from './artifacts';

const SLUG = 'alpha';

const seedToken = () => sessionStorage.setItem('happyranch.token', 'tok');

describe('artifacts api mirror', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('uploadArtifact posts multipart data with agent and name', async () => {
    seedToken();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'THR-001-report.pdf',
          size_bytes: 3,
          modified_at: '2026-06-09T00:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });
    const result = await uploadArtifact(SLUG, {
      file,
      name: 'THR-001-report.pdf',
      agent: 'founder',
    });

    expect(result.name).toBe('THR-001-report.pdf');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/api/v1/orgs/${SLUG}/artifacts`);
    expect(url).toContain('agent=founder');
    expect(url).toContain('name=THR-001-report.pdf');
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer tok',
      Accept: 'application/json',
    });
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
  });
});
