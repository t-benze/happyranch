import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { formatProvenanceDate, formatArtifactModifiedAt } from './artifact-meta';

const name = 'Raw_Agent-2026-06-16-THR-042-Raw_Title.pdf';
function base() {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/x' }] })), http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 12 })), http.get('/api/v1/orgs/alpha/artifacts', () => HttpResponse.json({ artifacts: [{ name, size_bytes: 1536, modified_at: '2026-06-20T14:30:00Z' }, { name: 'Raw_Folder/raw.txt', size_bytes: 10, modified_at: '' }] })));
}
function switchLocale(locale: string) { act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: locale }))); }

describe('Artifacts bilingual display', () => {
  test.each(['en', 'zh-CN'] as const)('list/folder/type chrome and filename bytes in %s', async locale => {
    base(); renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText('THR-042-Raw_Title.pdf');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(locale === 'en' ? 'Everything the org has produced' : '组织产出的所有文件');
    expect(screen.getByText(locale === 'en' ? 'document' : '文档', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('Raw_Agent').textContent).toBe('Raw_Agent');
    expect(screen.getByText(locale === 'en' ? '2 artifacts · produced by 1 thread' : '2 个共享文件 · 由 1 个讨论串产出')).toBeInTheDocument();
    expect(screen.getByText(locale === 'en' ? 'From filename' : '来自文件名')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Raw_Folder/ })).toHaveTextContent(locale === 'en' ? '1 file' : '1 个文件');
    await userEvent.click(screen.getByRole('button', { name: /Raw_Folder/ }));
    expect(screen.getByRole('button', { name: locale === 'en' ? 'Artifacts' : '共享文件' })).toBeInTheDocument();
    expect(screen.getByText(locale === 'en' ? 'Modified time unavailable' : '修改时间不可用')).toBeInTheDocument();
  });
  test.each(['en', 'zh-CN'] as const)('delete confirmation/error and upload validation in %s', async locale => {
    base(); let deletes = 0; let uploads = 0;
    server.use(http.delete('/api/v1/orgs/alpha/artifacts/:name', () => { deletes++; return HttpResponse.json({ detail: { code: 'artifact_not_found' } }, { status: 404 }); }), http.post('/api/v1/orgs/alpha/artifacts', () => { uploads++; return HttpResponse.json({}); }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText('THR-042-Raw_Title.pdf');
    await userEvent.click(screen.getByRole('button', { name: (locale === 'en' ? 'Delete ' : '删除 ') + name }));
    expect(confirm).toHaveBeenCalledWith(locale === 'en' ? `Delete "${name}"? This cannot be undone.` : `删除“${name}”？此操作无法撤销。`);
    await screen.findByText(locale === 'en' ? 'That artifact no longer exists.' : '该共享文件已不存在。');
    await userEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Upload' : '上传' }));
    await userEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Upload' : '上传' }));
    expect(screen.getByText(locale === 'en' ? 'Select a file to upload.' : '请选择要上传的文件。')).toBeInTheDocument();
    expect(deletes).toBe(1); expect(uploads).toBe(0); confirm.mockRestore();
  });
  test('upload switch retains selected File, filename, focus, nodes and zero requests in both directions', async () => {
    base(); let requests = 0; server.events.on('request:start', () => { requests++; });
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter('en') } });
    await screen.findByText('THR-042-Raw_Title.pdf'); await userEvent.click(screen.getByRole('button', { name: 'Upload' }));
    const fileInput = screen.getByLabelText('File') as HTMLInputElement;
    const file = new File(['exact bytes'], 'Raw_File.pdf', { type: 'application/pdf' });
    await userEvent.upload(fileInput, file);
    const input = screen.getByLabelText('Name (optional — defaults to the file name)');
    await userEvent.type(input, 'Raw_Draft.pdf'); const upload = screen.getByRole('button', { name: 'Upload' }); input.focus();
    for (const locale of ['zh-CN', 'en']) {
      const before = requests; switchLocale(locale);
      expect(screen.getByLabelText(locale === 'en' ? 'Name (optional — defaults to the file name)' : '名称（可选，默认为文件名）')).toBe(input);
      expect(input).toHaveValue('Raw_Draft.pdf'); expect(input).toHaveFocus(); expect(fileInput.files?.[0]).toBe(file);
      expect(screen.getByRole('button', { name: locale === 'en' ? 'Upload' : '上传' })).toBe(upload); expect(requests).toBe(before);
    }
    server.events.removeAllListeners('request:start');
  });
  test('plain filename date stays calendar-only and mtime display uses explicit Chinese locale', () => {
    expect(formatProvenanceDate('2026-06-16', 'zh-CN')).toBe('2026年6月16日');
    const viewerTime = new Date('2026-06-20T14:30:00Z');
    const hour = String(viewerTime.getHours()).padStart(2, '0');
    const minute = String(viewerTime.getMinutes()).padStart(2, '0');
    expect(formatArtifactModifiedAt('2026-06-20T14:30:00Z', 'zh-CN')).toBe(
      `${viewerTime.getFullYear()}年${viewerTime.getMonth() + 1}月${viewerTime.getDate()}日 ${hour}:${minute}`,
    );
    expect(formatProvenanceDate('2026-02-30', 'zh-CN')).toBeNull();
    expect(formatProvenanceDate('not-a-date', 'en')).toBeNull();
    expect(formatArtifactModifiedAt('not-a-date', 'zh-CN')).toBeNull();
  });
  test.each([
    ['UTC', '2026年6月20日 14:30'],
    ['Pacific/Kiritimati', '2026年6月21日 04:30'],
  ])('plain filename date stays June 16 while viewer-local Chinese mtime in %s is %s', (timeZone, expectedMtime) => {
    // Simulate the viewer default only; preserve explicit UTC for filename dates.
    const NativeDateTimeFormat = Intl.DateTimeFormat;
    const viewerDefault = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation((locales, options) =>
      new NativeDateTimeFormat(locales, { ...options, timeZone: options?.timeZone ?? timeZone }),
    );
    try {
      expect(formatProvenanceDate('2026-06-16', 'zh-CN')).toBe('2026年6月16日');
      expect(formatArtifactModifiedAt('2026-06-20T14:30:00Z', 'zh-CN')).toBe(expectedMtime);
      expect(formatProvenanceDate('2026-02-30', 'zh-CN')).toBeNull();
      expect(formatProvenanceDate('not-a-date', 'zh-CN')).toBeNull();
      expect(formatArtifactModifiedAt('not-a-date', 'zh-CN')).toBeNull();
    } finally {
      viewerDefault.mockRestore();
    }
  });
  test.each(['en', 'zh-CN'] as const)('empty/error + Retry chrome in %s', async locale => {
    base();
    server.use(http.get('/api/v1/orgs/alpha/artifacts', () => HttpResponse.json({ artifacts: [] })));
    const mounted = renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText(locale === 'en' ? 'No artifacts yet' : '暂无共享文件');
    mounted.unmount();
    server.use(http.get('/api/v1/orgs/alpha/artifacts', () => HttpResponse.json({ detail: 'RAW_HTTP' }, { status: 500 })));
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText(locale === 'en' ? /Could not load artifacts[.]/ : /无法加载共享文件。/);
    expect(screen.getByRole('button', { name: locale === 'en' ? 'Retry' : '重试' })).toBeInTheDocument();
  });

  test.each(['en', 'zh-CN'] as const)('loading accessibility chrome in %s', async locale => {
    base(); server.use(http.get('/api/v1/orgs/alpha/artifacts', async () => { await delay('infinite'); return HttpResponse.json({}); }));
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter(locale) } });
    expect(await screen.findByLabelText(locale === 'en' ? 'Loading artifacts' : '正在加载共享文件')).toBeInTheDocument();
  });

  test.each(['upload', 'download', 'delete'] as const)('%s failure retranslates while visible without another request', async action => {
    base(); let requests = 0;
    const english = action === 'upload' ? 'File exceeds the 10 MB limit.' : action === 'delete' ? 'That artifact no longer exists.' : 'Download failed.';
    const chinese = action === 'upload' ? '文件超过 10 MB 限制。' : action === 'delete' ? '该共享文件已不存在。' : '下载失败。';
    const response = () => { requests++; return HttpResponse.json(action === 'download' ? {} : { detail: { code: action === 'upload' ? 'artifact_too_large' : 'artifact_not_found' } }, { status: 500 }); };
    server.use(http.post('/api/v1/orgs/alpha/artifacts', response), http.get('/api/v1/orgs/alpha/artifacts/:name', response), http.delete('/api/v1/orgs/alpha/artifacts/:name', response));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/artifacts', i18n: { adapter: savedLocaleAdapter('en') } });
    await screen.findByText('THR-042-Raw_Title.pdf');
    if (action === 'upload') {
      await userEvent.click(screen.getByRole('button', { name: 'Upload' }));
      await userEvent.upload(screen.getByLabelText('File'), new File(['raw'], 'Raw_File.pdf'));
      await userEvent.click(screen.getByRole('button', { name: 'Upload' }));
    } else {
      await userEvent.click(screen.getByRole('button', { name: action === 'delete' ? 'Delete ' + name : 'Download' }));
    }
    await screen.findByText(english);
    switchLocale('zh-CN'); expect(screen.getByText(chinese)).toBeInTheDocument();
    switchLocale('en'); expect(screen.getByText(english)).toBeInTheDocument();
    expect(requests).toBe(1); confirm.mockRestore();
  });

});
