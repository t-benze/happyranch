/**
 * THR-118 W3a — NewThreadDialog i18n.
 *
 * Renders the real shared dialog under the real I18nProvider and proves:
 *  - product copy (title, labels, attach control, remove-attachment aria,
 *    buttons, validation) is localized in zh-CN;
 *  - a locale switch in either direction keeps the dialog open, keeps the SAME
 *    subject/recipients/body/file-input nodes with identical values, keeps focus,
 *    keeps the attachment chip node (no regenerated identity), and issues zero
 *    requests — then one real create posts exactly once;
 *  - a mapped validation/daemon error re-translates in place without a
 *    resubmit, while raw diagnostics (unmapped code => `HTTP 500`, raw text
 *    byte-equal to an English catalog value, and empty) stay byte-exact.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { OrgProvider } from '@/lib/orgSlug';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { NewThreadDialog } from './NewThreadDialog';

const SLUG = 'alpha';

const EN = {
  title: 'New thread',
  subject: 'Subject',
  recipients: 'Recipients (comma-separated agent names)',
  body: 'Body (Markdown)',
  attach: 'Attach files',
  remove: 'Remove attachment',
  send: 'Send',
  required: 'Subject, recipients, and a body or attachment are required.',
  unknownAgent: "That agent doesn't exist in this org.",
};
const ZH = {
  title: '新建会话',
  subject: '主题',
  recipients: '收件人（以逗号分隔的智能体名称）',
  body: '正文（Markdown）',
  attach: '添加附件',
  remove: '移除附件',
  send: '发送',
  required: '主题和收件人为必填项，并且需要填写正文或添加附件。',
  unknownAgent: '该智能体在此组织中不存在。',
};

function Harness() {
  const [created, setCreated] = useState<string[]>([]);
  return (
    <>
      <NewThreadDialog
        open
        onClose={() => {}}
        onCreated={(id) => setCreated((c) => [...c, id])}
      />
      <span data-testid="created">{created.join(',')}</span>
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>
  );
}

function renderDialog(locale: 'en' | 'zh-CN' = 'en') {
  return renderWithProviders(
    <Routes>
      <Route
        path="/orgs/:slug/*"
        element={
          <OrgProvider>
            <Harness />
          </OrgProvider>
        }
      />
    </Routes>,
    { route: `/orgs/${SLUG}/threads`, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

/** Ledger of every request issued; sliced per switch window. */
function recordRequests(): string[] {
  const ledger: string[] = [];
  server.events.on('request:start', ({ request }) => {
    ledger.push(`${request.method} ${new URL(request.url).pathname}`);
  });
  return ledger;
}

async function switchTo(locale: 'en' | 'zh-CN') {
  // Programmatic click: the modal dialog makes outside content pointer-inert,
  // and a programmatic click never moves focus.
  act(() => screen.getByTestId(`test-set-locale-${locale}`).click());
  // Let any (forbidden) effect-driven request get a chance to start.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

let composeBodies: unknown[];
let uploads: string[];

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  composeBodies = [];
  uploads = [];
  server.use(
    http.post(`/api/v1/orgs/${SLUG}/artifacts`, ({ request }) => {
      const name = new URL(request.url).searchParams.get('name') ?? '';
      uploads.push(name);
      return HttpResponse.json({ name, size: 3, content_type: 'text/plain' });
    }),
    http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
      composeBodies.push(await request.json());
      return HttpResponse.json({ thread_id: 'THR-900' });
    }),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.restoreAllMocks();
});

describe('NewThreadDialog i18n (THR-118 W3a)', () => {
  test('renders localized product copy in zh-CN', () => {
    renderDialog('zh-CN');
    expect(screen.getByRole('dialog', { name: ZH.title })).toBeInTheDocument();
    expect(screen.getByLabelText(ZH.subject)).toBeInTheDocument();
    expect(screen.getByLabelText(ZH.recipients)).toBeInTheDocument();
    expect(screen.getByLabelText(ZH.body)).toBeInTheDocument();
    expect(screen.getByLabelText(ZH.attach)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: ZH.send })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
  });

  test('locale switch preserves draft, nodes, focus, attachment identity and issues no request; one create posts once', async () => {
    const user = userEvent.setup();
    renderDialog('en');
    const subject = screen.getByLabelText(EN.subject) as HTMLInputElement;
    const recipients = screen.getByLabelText(EN.recipients) as HTMLInputElement;
    const body = screen.getByLabelText(EN.body) as HTMLTextAreaElement;
    const fileInput = screen.getByLabelText(EN.attach) as HTMLInputElement;
    await user.type(subject, 'Quarterly plan');
    await user.type(recipients, 'agent_a');
    await user.type(body, 'Hello **world**');
    await user.upload(fileInput, new File(['abc'], 'report.txt', { type: 'text/plain' }));
    const chipRemove = screen.getByRole('button', { name: EN.remove });
    const chip = chipRemove.closest('span')!;
    expect(chip).toHaveTextContent('report.txt');
    await user.click(body);
    expect(body).toHaveFocus();

    const ledger = recordRequests();
    await switchTo('zh-CN');
    expect(screen.getByRole('dialog', { name: ZH.title })).toBeInTheDocument();
    expect(screen.getByLabelText(ZH.subject)).toBe(subject);
    expect(screen.getByLabelText(ZH.recipients)).toBe(recipients);
    expect(screen.getByLabelText(ZH.body)).toBe(body);
    expect(screen.getByLabelText(ZH.attach)).toBe(fileInput);
    expect(subject.value).toBe('Quarterly plan');
    expect(recipients.value).toBe('agent_a');
    expect(body.value).toBe('Hello **world**');
    expect(body).toHaveFocus();
    const chipRemoveZh = screen.getByRole('button', { name: ZH.remove });
    expect(chipRemoveZh).toBe(chipRemove);
    expect(chipRemoveZh.closest('span')).toBe(chip);
    expect(chip).toHaveTextContent('report.txt');
    expect(ledger).toEqual([]);

    await switchTo('en');
    expect(screen.getByRole('dialog', { name: EN.title })).toBeInTheDocument();
    expect(screen.getByLabelText(EN.subject)).toBe(subject);
    expect(screen.getByLabelText(EN.body)).toBe(body);
    expect(body.value).toBe('Hello **world**');
    expect(subject.value).toBe('Quarterly plan');
    expect(recipients.value).toBe('agent_a');
    expect(body).toHaveFocus();
    expect(screen.getByRole('button', { name: EN.remove })).toBe(chipRemove);
    expect(ledger).toEqual([]);
    expect(composeBodies).toHaveLength(0);
    expect(uploads).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: EN.send }));
    await waitFor(() => expect(screen.getByTestId('created')).toHaveTextContent('THR-900'));
    expect(uploads).toHaveLength(1);
    expect(composeBodies).toHaveLength(1);
    expect(composeBodies[0]).toMatchObject({
      subject: 'Quarterly plan',
      recipients: ['agent_a'],
      body_markdown: 'Hello **world**',
      attachments: [{ display_name: 'report.txt' }],
    });
    expect(ledger.filter((r) => r === `POST /api/v1/orgs/${SLUG}/threads`)).toHaveLength(1);
  });

  test('mapped validation error re-translates in place without resubmitting', async () => {
    const user = userEvent.setup();
    renderDialog('en');
    await user.click(screen.getByRole('button', { name: EN.send }));
    const error = screen.getByText(EN.required);
    const ledger = recordRequests();
    await switchTo('zh-CN');
    expect(error).toHaveTextContent(ZH.required);
    expect(error.textContent).toBe(ZH.required);
    await switchTo('en');
    expect(error.textContent).toBe(EN.required);
    expect(ledger).toEqual([]);
    expect(composeBodies).toHaveLength(0);
  });

  test('mapped daemon error re-translates; unmapped code stays raw HTTP 500', async () => {
    const user = userEvent.setup();
    let mode: 'mapped' | 'unmapped' = 'mapped';
    let posts = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        posts += 1;
        return mode === 'mapped'
          ? HttpResponse.json({ detail: { code: 'unknown_agent' } }, { status: 400 })
          : HttpResponse.json({ detail: { code: 'some_unmapped_code' } }, { status: 500 });
      }),
    );
    renderDialog('en');
    await user.type(screen.getByLabelText(EN.subject), 'Hi');
    await user.type(screen.getByLabelText(EN.recipients), 'ghost');
    await user.type(screen.getByLabelText(EN.body), 'Body');
    await user.click(screen.getByRole('button', { name: EN.send }));
    const mapped = await screen.findByText(EN.unknownAgent);
    expect(posts).toBe(1);
    await switchTo('zh-CN');
    expect(mapped.textContent).toBe(ZH.unknownAgent);
    await switchTo('en');
    expect(mapped.textContent).toBe(EN.unknownAgent);
    expect(posts).toBe(1);

    mode = 'unmapped';
    await user.click(screen.getByRole('button', { name: EN.send }));
    const raw = await screen.findByText('HTTP 500');
    expect(posts).toBe(2);
    await switchTo('zh-CN');
    expect(raw.textContent).toBe('HTTP 500');
    await switchTo('en');
    expect(raw.textContent).toBe('HTTP 500');
    expect(posts).toBe(2);
  });

  test.each([
    ['raw text byte-equal to an English catalog value', 'Subject is required.'],
    ['empty raw text', ''],
  ])('upload failure with %s stays byte-exact; only the file prefix localizes', async (_label, raw) => {
    const user = userEvent.setup();
    const originalSet = FormData.prototype.set;
    vi.spyOn(FormData.prototype, 'set').mockImplementation(function (
      this: FormData,
      field: string,
      value: unknown,
      filename?: string,
    ) {
      if (value instanceof File) throw raw; // a non-Error raw diagnostic
      return originalSet.call(this, field, value as Blob, filename);
    });
    renderDialog('en');
    await user.type(screen.getByLabelText(EN.subject), 'Hi');
    await user.type(screen.getByLabelText(EN.recipients), 'agent_a');
    await user.upload(
      screen.getByLabelText(EN.attach),
      new File(['abc'], 'a.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: EN.send }));
    const expectedEn = `a.txt: ${raw}`;
    const error = await screen.findByText(
      (_content, el) => el?.tagName === 'P' && el.textContent === expectedEn,
    );
    const ledger = recordRequests();
    await switchTo('zh-CN');
    // Prefix is product copy; the raw portion is never translated (the raw
    // 'Subject is required.' must NOT become '主题为必填项。').
    expect(error.textContent).toBe(`a.txt：${raw}`);
    await switchTo('en');
    expect(error.textContent).toBe(expectedEn);
    expect(ledger).toEqual([]);
    expect(uploads).toHaveLength(0);
    expect(composeBodies).toHaveLength(0);
  });
});
