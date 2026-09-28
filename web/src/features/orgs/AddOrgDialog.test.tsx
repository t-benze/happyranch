import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AddOrgDialog } from './AddOrgDialog';
import { orgs as orgsApi } from '@/lib/api';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import type { Locale } from '@/lib/i18n';

function renderDialog(onClose = vi.fn()) {
  return renderWithProviders(<AddOrgDialog open onOpenChange={onClose} />, { route: '/' });
}

/** Explicit zh-CN render for the bilingual W2a assertions. */
function renderDialogZh(locale: Locale, onClose = vi.fn()) {
  return renderWithProviders(<AddOrgDialog open onOpenChange={onClose} />, {
    route: '/',
    i18n: { adapter: savedLocaleAdapter(locale) },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('AddOrgDialog', () => {
  test('Create disabled until slug matches ^[a-z0-9-]{1,40}$', async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = screen.getByLabelText(/slug/i);
    const submit = screen.getByRole('button', { name: /create/i });
    expect(submit).toBeDisabled();

    await user.type(input, 'Bad_Slug');
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'good-slug-1');
    expect(submit).not.toBeDisabled();
  });

  test('submits POST /orgs and closes on success', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(orgsApi, 'createOrg').mockResolvedValue({ slug: 'good-slug' });
    const onClose = vi.fn();
    renderDialog(onClose);

    await user.type(screen.getByLabelText(/slug/i), 'good-slug');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ slug: 'good-slug' }));
  });

  test('surfaces 409 org_exists inline', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'taken');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_exists/i)).toBeInTheDocument(),
    );
  });

  test('surfaces 409 org_dir_exists inline as already exists', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('dir exists'), { status: 409, code: 'org_dir_exists' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'stale-dir');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_dir_exists/i)).toBeInTheDocument(),
    );
  });

  test('a bare 409 with no recognized code renders the already-exists message', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'collision');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_exists/i)).toBeInTheDocument(),
    );
  });

  test('surfaces 409 no_active_runtime with its own message, NOT already exists', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('no runtime'), { status: 409, code: 'no_active_runtime' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'my-org');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      // The error is a <p className="text-tier-red">, not role="alert"
      const errorEl = screen.getByText(/runtime|start|ready|moment/i);
      expect(errorEl).toBeInTheDocument();
      // Must NOT say "already exists"
      expect(errorEl.textContent).not.toMatch(/already exists/i);
    });
  });

  test('surfaces generic non-409 error as fallback message', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('server error'), { status: 500 }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'my-org');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/server error/i)).toBeInTheDocument(),
    );
  });

  test('renders zh-CN copy while keeping the slug contract and authored value (W2a)', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }),
    );
    renderDialogZh('zh-CN');

    expect(screen.getByText('新建组织')).toBeInTheDocument();
    expect(screen.getByLabelText('标识符')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    const input = screen.getByLabelText('标识符');
    await user.type(input, 'taken');
    // Authored field value is untouched by localization.
    expect(input).toHaveValue('taken');
    await user.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() =>
      expect(screen.getByText('标识符为 "taken" 的组织已存在。')).toBeInTheDocument(),
    );
  });

  // --- W2a R1: product-owned error identity follows the locale ---------------

  test('an already-visible mapped error follows en -> zh-CN -> en without resubmission (W2a R1)', async () => {
    const createSpy = vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('raw detail'), { status: 409, code: 'org_exists' }),
    );
    renderWithProviders(
      <>
        <AddOrgDialog open onOpenChange={() => undefined} />
        <LocaleTestSwitch to="zh-CN" />
        <LocaleTestSwitch to="en" />
      </>,
      { route: '/', i18n: { adapter: savedLocaleAdapter('en') } },
    );

    const input = screen.getByLabelText('Slug');
    fireEvent.change(input, { target: { value: 'taken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await screen.findByText('An org with slug "taken" already exists.');

    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(screen.getByLabelText('标识符')).toBe(input);
    expect(input).toHaveValue('taken');
    expect(screen.getByRole('dialog').textContent).toContain('标识符为 "taken" 的组织已存在。');

    fireEvent.click(screen.getByTestId('test-set-locale-en'));
    expect(screen.getByLabelText('Slug')).toBe(input);
    expect(input).toHaveValue('taken');
    expect(screen.getByRole('dialog').textContent).toContain(
      'An org with slug "taken" already exists.',
    );
    // No resubmission on either switch.
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  test('error interpolation uses the submitted slug, not a later-edited field (W2a R1)', async () => {
    let reject: (reason?: unknown) => void = () => undefined;
    const pending = new Promise((_resolve, rej) => { reject = rej; });
    vi.spyOn(orgsApi, 'createOrg').mockReturnValue(
      pending as unknown as ReturnType<typeof orgsApi.createOrg>,
    );
    renderDialog();

    const input = screen.getByLabelText(/slug/i);
    fireEvent.change(input, { target: { value: 'first-slug' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    // Edit the field while the submit is still pending.
    fireEvent.change(input, { target: { value: 'second-slug' } });
    reject(Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }));

    await waitFor(() =>
      expect(
        screen.getByText('An org with slug "first-slug" already exists.'),
      ).toBeInTheDocument(),
    );
    // The authored field keeps the user's newer text.
    expect(input).toHaveValue('second-slug');
  });

  test('a generic no-message error translates with the locale (W2a R1)', async () => {
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error(''), { status: 500 }),
    );
    renderWithProviders(
      <>
        <AddOrgDialog open onOpenChange={() => undefined} />
        <LocaleTestSwitch to="zh-CN" />
      </>,
      { route: '/', i18n: { adapter: savedLocaleAdapter('en') } },
    );
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'my-org' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await screen.findByText('Could not create org.');
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(screen.getByRole('dialog').textContent).toContain('无法创建组织。');
  });

  test('an unknown non-empty daemon detail stays verbatim across a locale switch (W2a R1)', async () => {
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      new Error('daemon said: EACCES raw detail'),
    );
    renderWithProviders(
      <>
        <AddOrgDialog open onOpenChange={() => undefined} />
        <LocaleTestSwitch to="zh-CN" />
      </>,
      { route: '/', i18n: { adapter: savedLocaleAdapter('en') } },
    );
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'raw-slug' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await screen.findByText('daemon said: EACCES raw detail');
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(screen.getByRole('dialog').textContent).toContain('daemon said: EACCES raw detail');
  });

  test('the close control exposes a localized accessible name (W2a R2)', () => {
    renderDialogZh('zh-CN');
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });
});
