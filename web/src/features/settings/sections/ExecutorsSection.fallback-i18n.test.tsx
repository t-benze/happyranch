/**
 * THR-118 W2c (TASK-8791) — state-held executor failure messages.
 *
 * CustomProfilesSection (remove) and ExecutorBinariesSection (validate /
 * register) hold a failure message in component state. Two producers feed it:
 *
 * - a product-owned generic fallback (a thrown value that carries no
 *   diagnostic) — must be held as a catalog key and translated at render, so
 *   an already-visible message follows a locale switch in both directions;
 * - a raw daemon/API diagnostic — must stay byte-for-byte in every locale,
 *   even when its bytes happen to equal a catalog string or are empty.
 *
 * The generic fallback is only reachable when the API layer rejects with a
 * non-Error value, so the API modules are wrapped with pass-through spies
 * that a test can make reject with a bare string.
 */
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { ExecutorsSection } from './ExecutorsSection';

const fail = vi.hoisted(() => ({
  remove: null as unknown,
  validate: null as unknown,
  register: null as unknown,
}));

vi.mock('@/lib/api/runtime-executors', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/runtime-executors')>();
  return {
    ...actual,
    removeRuntimeProfile: (...args: Parameters<typeof actual.removeRuntimeProfile>) =>
      fail.remove !== null ? Promise.reject(fail.remove) : actual.removeRuntimeProfile(...args),
  };
});

vi.mock('@/lib/api/executor-binaries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/executor-binaries')>();
  return {
    ...actual,
    validateExecutorBinary: (...args: Parameters<typeof actual.validateExecutorBinary>) =>
      fail.validate !== null ? Promise.reject(fail.validate) : actual.validateExecutorBinary(...args),
    registerExecutorBinary: (...args: Parameters<typeof actual.registerExecutorBinary>) =>
      fail.register !== null ? Promise.reject(fail.register) : actual.registerExecutorBinary(...args),
  };
});

const PROFILE = {
  name: 'my-runner',
  adapter: 'claude',
  workspace_adapter_id: 'claude',
  command_adapter_id: null,
  present: true,
  path: '/usr/local/bin/my-runner-cli',
};

const REMOVE_EN = 'Could not remove this profile.';
const REMOVE_ZH = '无法移除此配置。';
const VALIDATE_EN = 'Validation failed.';
const VALIDATE_ZH = '验证失败。';
const REGISTER_EN = 'Could not register this path.';
const REGISTER_ZH = '无法注册此路径。';

let ledger: string[];

beforeEach(() => {
  fail.remove = null;
  fail.validate = null;
  fail.register = null;
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/executor-binaries', () =>
      HttpResponse.json({ entries: [{ kind: 'claude', path: null, valid: false }] }),
    ),
    http.get('/api/v1/executors/runtime/profiles', () => HttpResponse.json({ profiles: [PROFILE] })),
    http.get('/api/v1/runtime/adapters', () => HttpResponse.json([])),
  );
  ledger = [];
  server.events.on('request:start', ({ request }) => {
    ledger.push(`${request.method} ${new URL(request.url).pathname}`);
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

function renderSection(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <ExecutorsSection />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

function switchTo(locale: 'en' | 'zh-CN') {
  act(() => screen.getByTestId(`test-set-locale-${locale}`).click());
}

/** Arm and confirm the remove of `my-runner`; returns the row. */
async function removeProfile(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  const row = await screen.findByTestId('profile-row-my-runner');
  await user.click(within(row).getByTestId('profile-remove-my-runner'));
  await user.click(within(row).getByTestId('profile-confirm-remove-my-runner'));
  return row;
}

/** Open the claude row's manual-entry disclosure and type a path. */
async function typePath(
  user: ReturnType<typeof userEvent.setup>,
  locale: 'en' | 'zh-CN',
): Promise<{ row: HTMLElement; input: HTMLElement; details: HTMLDetailsElement }> {
  const row = await screen.findByTestId('binary-row-claude');
  await user.click(
    within(row).getByText(locale === 'en' ? 'Advanced: enter path manually' : '高级：手动输入路径'),
  );
  const input = within(row).getByLabelText(
    locale === 'en' ? 'Register binary path' : '注册二进制路径',
  );
  await user.type(input, '/opt/claude');
  const details = within(row).getByTestId('binary-manual-claude') as HTMLDetailsElement;
  expect(details.open).toBe(true);
  return { row, input, details };
}

describe('CustomProfilesSection remove failure (TASK-8791)', () => {
  test('generic fallback re-translates en → zh-CN → en with the same node, no retry, no request', async () => {
    fail.remove = 'non-error rejection';
    const user = userEvent.setup();
    renderSection('en');
    const row = await removeProfile(user);

    const alert = await within(row).findByTestId('profile-remove-error-my-runner');
    expect(alert).toHaveTextContent(REMOVE_EN);
    const confirm = within(row).getByTestId('profile-confirm-remove-my-runner');
    const before = ledger.length;

    switchTo('zh-CN');
    await waitFor(() => expect(alert).toHaveTextContent(REMOVE_ZH));
    expect(within(row).getByTestId('profile-remove-error-my-runner')).toBe(alert);
    expect(within(row).getByTestId('profile-confirm-remove-my-runner')).toBe(confirm);
    expect(alert).not.toHaveTextContent(REMOVE_EN);

    switchTo('en');
    await waitFor(() => expect(alert).toHaveTextContent(REMOVE_EN));
    expect(within(row).getByTestId('profile-remove-error-my-runner')).toBe(alert);
    expect(within(row).getByTestId('profile-confirm-remove-my-runner')).toBe(confirm);
    expect(ledger.slice(before)).toEqual([]);
  });

  test('generic fallback re-translates zh-CN → en → zh-CN', async () => {
    fail.remove = 'non-error rejection';
    const user = userEvent.setup();
    renderSection('zh-CN');
    const row = await removeProfile(user);

    const alert = await within(row).findByTestId('profile-remove-error-my-runner');
    expect(alert).toHaveTextContent(REMOVE_ZH);
    const before = ledger.length;

    switchTo('en');
    await waitFor(() => expect(alert).toHaveTextContent(REMOVE_EN));
    switchTo('zh-CN');
    await waitFor(() => expect(alert).toHaveTextContent(REMOVE_ZH));
    expect(within(row).getByTestId('profile-remove-error-my-runner')).toBe(alert);
    expect(ledger.slice(before)).toEqual([]);
  });

  test.each([
    ['a daemon diagnostic', 'profile busy: my-runner (session sess-42)'],
    // Byte-equal to the English catalog fallback: a string-lookup shape would
    // wrongly translate it; it is a daemon payload and must stay verbatim.
    ['a diagnostic byte-equal to the English fallback', REMOVE_EN],
  ])('raw %s stays byte-for-byte across both switches', async (_label, raw) => {
    const deletes: string[] = [];
    server.use(
      http.delete('/api/v1/executors/runtime/profiles/:name', ({ params }) => {
        deletes.push(String(params.name));
        return HttpResponse.json({ detail: raw }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    renderSection('en');
    const row = await removeProfile(user);

    const alert = await within(row).findByTestId('profile-remove-error-my-runner');
    expect(alert.textContent).toBe(raw);
    switchTo('zh-CN');
    await waitFor(() => expect(screen.getByTestId('test-set-locale-zh-CN')).toBeInTheDocument());
    await waitFor(() => expect(within(row).getByRole('button', { name: '取消' })).toBeInTheDocument());
    expect(alert.textContent).toBe(raw);
    switchTo('en');
    await waitFor(() => expect(within(row).getByRole('button', { name: 'Cancel' })).toBeInTheDocument());
    expect(alert.textContent).toBe(raw);
    expect(deletes).toEqual(['my-runner']);
  });
});

describe('ExecutorBinariesSection validate/register failures (TASK-8791)', () => {
  test('validate generic fallback re-translates en → zh-CN → en; disclosure, value and node retained', async () => {
    fail.validate = 'non-error rejection';
    const user = userEvent.setup();
    renderSection('en');
    const { row, input, details } = await typePath(user, 'en');
    await user.click(within(row).getByRole('button', { name: 'Validate' }));

    const status = await within(row).findByTestId('binary-check-claude');
    expect(status).toHaveTextContent(VALIDATE_EN);
    const before = ledger.length;

    switchTo('zh-CN');
    await waitFor(() => expect(status).toHaveTextContent(VALIDATE_ZH));
    expect(within(row).getByTestId('binary-check-claude')).toBe(status);
    expect(within(row).getByLabelText('注册二进制路径')).toBe(input);
    expect(input).toHaveValue('/opt/claude');
    expect(details.open).toBe(true);

    switchTo('en');
    await waitFor(() => expect(status).toHaveTextContent(VALIDATE_EN));
    expect(within(row).getByTestId('binary-check-claude')).toBe(status);
    expect(input).toHaveValue('/opt/claude');
    expect(details.open).toBe(true);
    expect(ledger.slice(before)).toEqual([]);
  });

  test('register generic fallback re-translates zh-CN → en → zh-CN; focus retained', async () => {
    fail.register = 'non-error rejection';
    const user = userEvent.setup();
    renderSection('zh-CN');
    const { row, input, details } = await typePath(user, 'zh-CN');
    await user.click(within(row).getByRole('button', { name: '注册' }));

    const alert = await within(row).findByTestId('binary-register-error-claude');
    expect(alert).toHaveTextContent(REGISTER_ZH);
    // Put focus back in the field so retention is observable.
    act(() => input.focus());
    const before = ledger.length;

    switchTo('en');
    await waitFor(() => expect(alert).toHaveTextContent(REGISTER_EN));
    expect(within(row).getByTestId('binary-register-error-claude')).toBe(alert);
    expect(input).toHaveFocus();
    expect(details.open).toBe(true);

    switchTo('zh-CN');
    await waitFor(() => expect(alert).toHaveTextContent(REGISTER_ZH));
    expect(within(row).getByTestId('binary-register-error-claude')).toBe(alert);
    expect(input).toHaveFocus();
    expect(input).toHaveValue('/opt/claude');
    expect(ledger.slice(before)).toEqual([]);
  });

  test.each([
    ['a daemon 422 reason', 'not executable: /opt/claude'],
    ['a reason byte-equal to the English fallback', REGISTER_EN],
  ])('register raw %s stays byte-for-byte across both switches', async (_label, raw) => {
    server.use(
      http.post('/api/v1/executor-binaries/register', () =>
        HttpResponse.json({ detail: raw }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderSection('en');
    const { row } = await typePath(user, 'en');
    await user.click(within(row).getByRole('button', { name: 'Register' }));

    const alert = await within(row).findByTestId('binary-register-error-claude');
    expect(alert.textContent).toBe(raw);
    switchTo('zh-CN');
    await within(row).findByRole('button', { name: '注册' });
    expect(alert.textContent).toBe(raw);
    switchTo('en');
    await within(row).findByRole('button', { name: 'Register' });
    expect(alert.textContent).toBe(raw);
  });

  test('validate raw ApiError detail (incl. empty) is never replaced by fallback copy', async () => {
    const raws = ['stat failed: /opt/claude: EACCES', ''];
    for (const raw of raws) {
      server.use(
        http.post('/api/v1/executor-binaries/validate', () =>
          HttpResponse.json({ detail: raw }, { status: 400 }),
        ),
      );
      const user = userEvent.setup();
      const { unmount } = renderSection('en');
      const { row } = await typePath(user, 'en');
      await user.click(within(row).getByRole('button', { name: 'Validate' }));

      const status = await within(row).findByTestId('binary-check-claude');
      expect(status.textContent).toBe(raw);
      switchTo('zh-CN');
      await within(row).findByRole('button', { name: '验证' });
      expect(status.textContent).toBe(raw);
      expect(status).not.toHaveTextContent(VALIDATE_ZH);
      expect(status).not.toHaveTextContent('无效');
      unmount();
    }
  });
});
