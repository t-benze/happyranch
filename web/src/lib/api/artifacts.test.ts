import { afterEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from './client';
import { deleteArtifact, downloadArtifact, listArtifacts, uploadArtifact } from './artifacts';

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

  test('listArtifacts GETs the collection with the bearer token', async () => {
    seedToken();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          artifacts: [
            { name: 'a.pdf', size_bytes: 3, modified_at: '2026-06-09T00:00:00Z' },
            { name: 'b.csv', size_bytes: 9, modified_at: '2026-06-10T00:00:00Z' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const result = await listArtifacts(SLUG);

    expect(result.artifacts).toHaveLength(2);
    expect(result.artifacts[0]?.name).toBe('a.pdf');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/orgs/${SLUG}/artifacts`);
    expect(init.method).toBe('GET');
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer tok',
      Accept: 'application/json',
    });
  });

  test('listArtifacts drops a stale token and retries once on 401', async () => {
    seedToken();
    // First GET 401s; listArtifacts clears the token, which forces getToken()
    // to re-bootstrap, then the GET is retried once and succeeds.
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'tok2' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ artifacts: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    const result = await listArtifacts(SLUG);

    expect(result.artifacts).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/auth/bootstrap');
  });

  test('deleteArtifact DELETEs with the agent query param and bearer token', async () => {
    seedToken();
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ name: 'a b.pdf', deleted: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    await expect(deleteArtifact(SLUG, 'a b.pdf')).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/api/v1/orgs/${SLUG}/artifacts/a%20b.pdf`);
    expect(url).toContain('agent=founder');
    expect(init.method).toBe('DELETE');
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer tok',
      Accept: 'application/json',
    });
  });

  test('deleteArtifact maps a 404 to ApiError with artifact_not_found', async () => {
    seedToken();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { code: 'artifact_not_found', name: 'gone.pdf' } }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const err = await deleteArtifact(SLUG, 'gone.pdf').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: 'artifact_not_found' });
  });

  test('deleteArtifact drops a stale token and retries once on 401', async () => {
    seedToken();
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'tok2' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ name: 'x.pdf', deleted: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    await expect(deleteArtifact(SLUG, 'x.pdf')).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/auth/bootstrap');
  });

  test('downloadArtifact fetches with the bearer token and triggers a download', async () => {
    seedToken();
    const blobContent = new Blob(['file contents'], { type: 'application/pdf' });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(blobContent, {
        status: 200,
        headers: { 'Content-Type': 'application/pdf' },
      }),
    );

    // jsdom doesn't ship createObjectURL / revokeObjectURL — stub them.
    const objectUrl = 'blob:http://localhost/fake-url';
    const createObjectUrlSpy = vi.fn().mockReturnValue(objectUrl);
    const revokeObjectUrlSpy = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: createObjectUrlSpy,
      revokeObjectURL: revokeObjectUrlSpy,
    });

    // Spy on the anchor click so we can assert it fires.
    const clickSpy = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') {
        const el = originalCreateElement('a');
        el.click = clickSpy;
        // Stub setAttribute so the download attribute is recorded.
        const originalSetAttr = el.setAttribute.bind(el);
        el.setAttribute = vi.fn((name: string, value: string) => {
          originalSetAttr(name, value);
        }) as typeof el.setAttribute;
        return el;
      }
      return originalCreateElement(tag) as HTMLElement;
    }) as unknown as typeof document.createElement;

    await downloadArtifact(SLUG, 'report.pdf');

    // Assert the fetch carries the Authorization header.
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/orgs/${SLUG}/artifacts/report.pdf`);
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer tok',
    });

    // Assert the download plumbing fired.
    const blobArg = createObjectUrlSpy.mock.calls[0]?.[0] as Blob;
    expect(blobArg.type).toBe('application/pdf');
    expect(blobArg.size).toBeGreaterThan(0);
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith(objectUrl);
  });

  test('downloadArtifact drops a stale token and retries once on 401', async () => {
    seedToken();
    const blobContent = new Blob(['file contents']);
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue('blob:fake'),
      revokeObjectURL: vi.fn(),
    });
    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = origCreateElement(tag);
      if (tag === 'a') el.click = vi.fn();
      return el;
    }) as unknown as typeof document.createElement;

    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'tok2' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(blobContent, {
          status: 200,
          headers: { 'Content-Type': 'application/pdf' },
        }),
      );

    await downloadArtifact(SLUG, 'x.pdf');

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/auth/bootstrap');
  });

  test('downloadArtifact revokes the object URL even when the DOM click throws', async () => {
    seedToken();
    const blobContent = new Blob(['file contents'], { type: 'application/pdf' });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(blobContent, {
        status: 200,
        headers: { 'Content-Type': 'application/pdf' },
      }),
    );

    const objectUrl = 'blob:http://localhost/leaky-url';
    const createObjectUrlSpy = vi.fn().mockReturnValue(objectUrl);
    const revokeObjectUrlSpy = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: createObjectUrlSpy,
      revokeObjectURL: revokeObjectUrlSpy,
    });

    // Simulate a click that throws (e.g., a CSP violation or detached DOM).
    const clickError = new Error('simulated click failure');
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') {
        const el = originalCreateElement('a');
        el.click = () => {
          throw clickError;
        };
        return el;
      }
      return originalCreateElement(tag) as HTMLElement;
    }) as unknown as typeof document.createElement;

    await expect(downloadArtifact(SLUG, 'report.pdf')).rejects.toThrow(clickError);

    // The object URL must be revoked even though the click threw.
    const blobArg = createObjectUrlSpy.mock.calls[0]?.[0] as Blob;
    expect(blobArg.type).toBe('application/pdf');
    expect(blobArg.size).toBeGreaterThan(0);
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith(objectUrl);
  });

  test('downloadArtifact throws ApiError on non-ok non-401 response', async () => {
    seedToken();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { code: 'artifact_not_found', name: 'gone.pdf' } }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const err = await downloadArtifact(SLUG, 'gone.pdf').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: 'artifact_not_found' });
  });
});
