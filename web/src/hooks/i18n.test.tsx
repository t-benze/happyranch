import { StrictMode, useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider, useI18n } from './i18n';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import {
  bootstrapDocumentLocale,
  LOCALE_STORAGE_KEY,
  type Locale,
  type LocalePreferenceAdapter,
  type LocaleSnapshot,
  type LocaleWriteOutcome,
  type LocaleWriteResult,
} from '@/lib/i18n';

afterEach(() => {
  vi.restoreAllMocks();
});

function makeAdapter(
  snapshot: LocaleSnapshot,
  write?: (locale: Locale) => LocaleWriteResult,
): LocalePreferenceAdapter {
  return {
    id: 'test-adapter',
    readSnapshot: () => snapshot,
    write: write ?? (() => ({ status: 'durable' })),
  };
}

/** A native-owned adapter double: the injected snapshot is authoritative. */
function makeNativeAdapter(
  snapshot: LocaleSnapshot,
  write?: (locale: Locale) => LocaleWriteResult,
): LocalePreferenceAdapter {
  return {
    id: 'native-test-adapter',
    authority: 'native',
    readSnapshot: () => snapshot,
    write: write ?? (() => ({ status: 'durable' })),
  };
}

function dispatchStorageEvent(init: StorageEventInit): void {
  act(() => {
    window.dispatchEvent(new StorageEvent('storage', init));
  });
}

function Probe(): JSX.Element {
  const { locale, source, persistence, persistenceReason, setLocale, t } = useI18n();
  const [draft, setDraft] = useState('');
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span data-testid="source">{source}</span>
      <span data-testid="probe">{t('common.translatedProbe')}</span>
      <span data-testid="persistence">{persistence}</span>
      <span data-testid="reason">{persistenceReason ?? ''}</span>
      <input aria-label="draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <details data-testid="details" open>
        <summary>Details</summary>
        <p>Body</p>
      </details>
      <select aria-label="pick" defaultValue="a">
        <option value="a">A</option>
        <option value="b">B</option>
      </select>
      <button type="button" onClick={() => setLocale('zh-CN')}>
        to-zh
      </button>
      <button type="button" onClick={() => setLocale('en')}>
        to-en
      </button>
    </div>
  );
}

describe('initial hydration and immediate switching (W1 acceptance case 4)', () => {
  it('uses the saved locale for the first render and sets html lang', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('probe').textContent).toBe('已翻译探针');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('changes copy but preserves input/draft/selection/open state and component identity', async () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    const user = userEvent.setup();
    renderWithProviders(<Probe />);

    const inputBefore = screen.getByLabelText('draft') as HTMLInputElement;
    await user.type(inputBefore, 'unsent draft');
    expect(inputBefore.value).toBe('unsent draft');

    await user.click(screen.getByText('to-en'));

    expect(screen.getByTestId('probe').textContent).toBe('Translated probe');
    const inputAfter = screen.getByLabelText('draft') as HTMLInputElement;
    // Same DOM node => the locale switch did not remount app state.
    expect(inputAfter).toBe(inputBefore);
    expect(inputAfter.value).toBe('unsent draft');
    expect(screen.getByTestId('details')).toHaveAttribute('open');
    expect((screen.getByLabelText('pick') as HTMLSelectElement).value).toBe('a');
  });

  it('does not issue network requests during provider startup (lazy auth preserved)', async () => {
    const requests: string[] = [];
    const record = ({ request }: { request: Request }) => {
      requests.push(new URL(request.url).pathname);
    };
    server.events.on('request:start', record);
    try {
      renderWithProviders(<Probe />);
      await act(async () => {
        await Promise.resolve();
      });
      expect(requests).toEqual([]);
    } finally {
      server.events.removeListener('request:start', record);
    }
  });
});

describe('storage failures (W1 acceptance case 2)', () => {
  it('renders with English when getItem throws and stays switchable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('locale').textContent).toBe('en');
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
  });

  it('switches in memory when setItem fails and reports honest failure', async () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    await user.click(screen.getByText('to-en'));

    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(screen.getByTestId('persistence').textContent).toBe('failed');
    expect(screen.getByTestId('reason').textContent).toBe('error');
  });

  it('reports durable and persists a successful write', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('persistence').textContent).toBe('durable');
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('zh-CN');
  });
});

describe('same-origin tab synchronization (W1 acceptance case 3)', () => {
  it('applies an external valid locale without writing back', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    renderWithProviders(<Probe />);
    setItem.mockClear();
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: LOCALE_STORAGE_KEY, newValue: 'zh-CN' }),
      );
    });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(setItem).not.toHaveBeenCalled();
  });

  it('resolves an invalid external value to English (documented resolver)', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: LOCALE_STORAGE_KEY, newValue: 'fr' }));
    });
    expect(screen.getByTestId('locale').textContent).toBe('en');
  });

  it('treats key deletion as unset', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: LOCALE_STORAGE_KEY, newValue: null }),
      );
    });
    expect(screen.getByTestId('locale').textContent).toBe('en');
  });

  it('treats a storage clear as unset', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: null, newValue: null }));
    });
    expect(screen.getByTestId('locale').textContent).toBe('en');
  });

  it('ignores unrelated keys', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'happyranch.theme', newValue: 'dark' }),
      );
    });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
  });

  it('ignores an unrelated storage area', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: LOCALE_STORAGE_KEY,
          newValue: 'en',
          storageArea: sessionStorage,
        }),
      );
    });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
  });

  it('removes its storage listener on unmount', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = renderWithProviders(<Probe />);
    unmount();
    expect(removeSpy).toHaveBeenCalledWith('storage', expect.any(Function));
  });

  it('keeps add/remove listener symmetry under StrictMode', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = render(
      <StrictMode>
        <I18nProvider>
          <Probe />
        </I18nProvider>
      </StrictMode>,
    );
    unmount();
    const adds = addSpy.mock.calls.filter((call) => call[0] === 'storage').length;
    const removes = removeSpy.mock.calls.filter((call) => call[0] === 'storage').length;
    expect(adds).toBeGreaterThan(0);
    expect(removes).toBe(adds);
  });
});

describe('injectable adapter seam (W1 acceptance case 8)', () => {
  it('renders a synchronous native initial locale on the first pass (no English flash)', () => {
    const seen: Locale[] = [];
    function RecordingProbe(): JSX.Element {
      const { locale, t } = useI18n();
      seen.push(locale);
      return <span data-testid="native-probe">{t('common.translatedProbe')}</span>;
    }
    const adapter = makeAdapter({ saved: null, native: 'zh-CN' });
    renderWithProviders(<RecordingProbe />, { i18n: { adapter } });
    expect(seen[0]).toBe('zh-CN');
    expect(screen.getByTestId('native-probe').textContent).toBe('已翻译探针');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('does not signal durable success before a delayed acknowledgement resolves', async () => {
    let resolveWrite!: (outcome: LocaleWriteOutcome) => void;
    const adapter = makeAdapter(
      { saved: null },
      () => new Promise<LocaleWriteOutcome>((resolve) => (resolveWrite = resolve)),
    );
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });

    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('persistence').textContent).toBe('pending');

    await act(async () => {
      resolveWrite({ status: 'durable' });
    });
    expect(screen.getByTestId('persistence').textContent).toBe('durable');
  });

  it('reports failure for a rejected acknowledgement', async () => {
    const adapter = makeAdapter({ saved: null }, () => Promise.reject(new Error('native offline')));
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('persistence').textContent).toBe('failed');
    expect(screen.getByTestId('reason').textContent).toBe('rejected');
  });

  it('expresses a native failure status without pretending durability', async () => {
    const adapter = makeAdapter({ saved: null }, () => ({
      status: 'failed',
      reason: 'native-unavailable',
    }));
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('persistence').textContent).toBe('failed');
    expect(screen.getByTestId('reason').textContent).toBe('native-unavailable');
  });

  it('defaults safely when the adapter read itself throws', () => {
    const adapter: LocalePreferenceAdapter = {
      id: 'broken-native',
      readSnapshot() {
        throw new Error('bridge unavailable');
      },
      write() {
        return { status: 'failed', reason: 'native-unavailable' };
      },
    };
    renderWithProviders(<Probe />, { i18n: { adapter } });
    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(document.documentElement.getAttribute('lang')).toBe('en');
  });
});

describe('unavailable storage getter during storage events (W1 cases 1-3)', () => {
  it('does not throw on an area-tagged event and ignores it when the getter throws', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    const realArea = localStorage;
    const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    try {
      expect(() =>
        act(() => {
          window.dispatchEvent(
            new StorageEvent('storage', {
              key: LOCALE_STORAGE_KEY,
              newValue: 'en',
              storageArea: realArea,
            }),
          );
        }),
      ).not.toThrow();
      // The area cannot be verified as localStorage, so it is ignored (never
      // guessed) and the handler never writes back.
      expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
      expect(setItem).not.toHaveBeenCalled();
    } finally {
      getter.mockRestore();
      setItem.mockRestore();
    }
  });

  it('does not throw and keeps ignoring unrelated key/area with a hostile getter', () => {
    const realArea = localStorage;
    const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      renderWithProviders(<Probe />);
      expect(screen.getByTestId('locale').textContent).toBe('en');
      expect(() => {
        window.dispatchEvent(
          new StorageEvent('storage', {
            key: 'happyranch.theme',
            newValue: 'dark',
            storageArea: realArea,
          }),
        );
        window.dispatchEvent(
          new StorageEvent('storage', {
            key: LOCALE_STORAGE_KEY,
            newValue: 'zh-CN',
            storageArea: sessionStorage,
          }),
        );
      }).not.toThrow();
      expect(screen.getByTestId('locale').textContent).toBe('en');
    } finally {
      getter.mockRestore();
    }
  });

  it('still applies a null-area event for the locale key when the getter throws', () => {
    const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      renderWithProviders(<Probe />);
      dispatchStorageEvent({ key: LOCALE_STORAGE_KEY, newValue: 'zh-CN' });
      expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    } finally {
      getter.mockRestore();
    }
  });
});

describe('adapter ownership: native authority (W1 acceptance case 8)', () => {
  it('a native snapshot beats a conflicting browser saved value', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
    renderWithProviders(<Probe />, {
      i18n: { adapter: makeNativeAdapter({ saved: null, native: 'zh-CN' }) },
    });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('source').textContent).toBe('native');
    expect(screen.getByTestId('probe').textContent).toBe('已翻译探针');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('a conflicting browser storage event cannot override the native snapshot', () => {
    renderWithProviders(<Probe />, {
      i18n: { adapter: makeNativeAdapter({ saved: null, native: 'zh-CN' }) },
    });
    dispatchStorageEvent({ key: LOCALE_STORAGE_KEY, newValue: 'en' });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('does not register a browser storage listener for a native adapter', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    renderWithProviders(<Probe />, {
      i18n: { adapter: makeNativeAdapter({ saved: null, native: 'zh-CN' }) },
    });
    expect(addSpy.mock.calls.filter((call) => call[0] === 'storage')).toHaveLength(0);
  });

  it('still applies browser cross-tab updates for the browser adapter', () => {
    renderWithProviders(<Probe />);
    dispatchStorageEvent({ key: LOCALE_STORAGE_KEY, newValue: 'zh-CN' });
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('source').textContent).toBe('saved');
  });

  it('reports native source and honest failure for a native-adapter switch', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />, {
      i18n: {
        adapter: makeNativeAdapter({ saved: null, native: 'en' }, () => ({
          status: 'failed',
          reason: 'native-unavailable',
        })),
      },
    });
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('source').textContent).toBe('native');
    expect(screen.getByTestId('persistence').textContent).toBe('failed');
    expect(screen.getByTestId('reason').textContent).toBe('native-unavailable');
  });
});

describe('setLocale acknowledgement ordering and lifecycle (W1 cases 3/8)', () => {
  function makeSettlingAdapter(): {
    adapter: LocalePreferenceAdapter;
    settlements: Array<(outcome: LocaleWriteOutcome) => void>;
  } {
    const settlements: Array<(outcome: LocaleWriteOutcome) => void> = [];
    const adapter = makeAdapter(
      { saved: null },
      () => new Promise<LocaleWriteOutcome>((resolve) => settlements.push(resolve)),
    );
    return { adapter, settlements };
  }

  it('retains the latest choice when the latest fails and the older succeeds later', async () => {
    const { adapter, settlements } = makeSettlingAdapter();
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    await user.click(screen.getByText('to-en'));
    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(screen.getByTestId('persistence').textContent).toBe('pending');

    await act(async () => {
      settlements[1]({ status: 'failed', reason: 'error' });
    });
    expect(screen.getByTestId('persistence').textContent).toBe('failed');

    await act(async () => {
      settlements[0]({ status: 'durable' });
    });
    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(screen.getByTestId('persistence').textContent).toBe('failed');
  });

  it('keeps the latest durable choice when the older write fails later', async () => {
    const { adapter, settlements } = makeSettlingAdapter();
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    await user.click(screen.getByText('to-en'));

    await act(async () => {
      settlements[1]({ status: 'durable' });
    });
    expect(screen.getByTestId('persistence').textContent).toBe('durable');

    await act(async () => {
      settlements[0]({ status: 'failed', reason: 'error' });
    });
    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(screen.getByTestId('persistence').textContent).toBe('durable');
  });

  it('an external change invalidates a pending write so it cannot report durable', async () => {
    const { adapter, settlements } = makeSettlingAdapter();
    const user = userEvent.setup();
    renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('persistence').textContent).toBe('pending');

    dispatchStorageEvent({ key: LOCALE_STORAGE_KEY, newValue: 'en' });
    expect(screen.getByTestId('locale').textContent).toBe('en');
    expect(screen.getByTestId('persistence').textContent).toBe('idle');

    await act(async () => {
      settlements[0]({ status: 'durable' });
    });
    expect(screen.getByTestId('persistence').textContent).toBe('idle');
  });

  it('a late acknowledgement after unmount does not throw', async () => {
    const { adapter, settlements } = makeSettlingAdapter();
    const user = userEvent.setup();
    const errorSpy = vi.spyOn(console, 'error');
    const { unmount } = renderWithProviders(<Probe />, { i18n: { adapter } });
    await user.click(screen.getByText('to-zh'));
    unmount();
    await act(async () => {
      settlements[0]({ status: 'durable' });
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });
});

describe('single startup snapshot handoff (W1 acceptance cases 4/7)', () => {
  it('uses a handed-in resolution and never rereads the adapter', () => {
    let reads = 0;
    const adapter: LocalePreferenceAdapter = {
      id: 'counting',
      readSnapshot: () => {
        reads += 1;
        return { saved: reads === 1 ? 'zh-CN' : 'en' };
      },
      write: () => ({ status: 'durable' }),
    };
    render(
      <I18nProvider adapter={adapter} initialResolution={{ locale: 'zh-CN', source: 'saved' }}>
        <Probe />
      </I18nProvider>,
    );
    expect(reads).toBe(0);
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('probe').textContent).toBe('已翻译探针');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('bootstrap and provider agree when the adapter snapshot changes between reads', () => {
    let reads = 0;
    const adapter: LocalePreferenceAdapter = {
      id: 'flaky',
      readSnapshot: () => {
        reads += 1;
        return { saved: reads === 1 ? 'zh-CN' : 'en' };
      },
      write: () => ({ status: 'durable' }),
    };
    const resolution = bootstrapDocumentLocale({ adapter });
    expect(reads).toBe(1);
    render(
      <I18nProvider adapter={adapter} initialResolution={resolution}>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
    // The provider used the handed snapshot rather than a second read.
    expect(reads).toBe(1);
  });

  it('keeps the first text and html lang consistent under StrictMode', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    render(
      <StrictMode>
        <I18nProvider>
          <Probe />
        </I18nProvider>
      </StrictMode>,
    );
    expect(screen.getByTestId('locale').textContent).toBe('zh-CN');
    expect(screen.getByTestId('probe').textContent).toBe('已翻译探针');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });
});
