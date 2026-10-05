import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { TierEditorDialog } from './TierEditorDialog';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { translate } from '@/lib/i18n';
import type { WorkingHoursSettings } from '@/lib/api/types';

const SLUG = 'alpha';

// Agent override already in continuous mode so the interval input renders
// directly (no need to drive the Radix mode Select).
function continuousWh(): WorkingHoursSettings {
  return {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: [] },
    default: {
      mode: 'continuous',
      window: { start: null, end: null, timezone: 'UTC' },
      interval: '2h',
      days: null,
      catch_up_on_startup: false,
    },
    teams: {},
    overrides: {
      dev_agent: {
        mode: 'continuous',
        window: { start: null, end: null, timezone: 'UTC' },
        interval: '2h',
        days: null,
        catch_up_on_startup: false,
      },
    },
  };
}

function renderDialog(
  locale: 'en' | 'zh-CN' = 'en',
  onSaved = () => {},
  onOpenChange = (_open: boolean) => {},
) {
  return renderWithProviders(
    <Routes>
      <Route
        path="/orgs/:slug/*"
        element={
          <TierEditorDialog
            open
            onOpenChange={onOpenChange}
            tier={{ kind: 'agent', agent: 'dev_agent' }}
            wh={continuousWh()}
            agentTeam={{ dev_agent: null }}
            allAgents={['dev_agent']}
            onSaved={onSaved}
          />
        }
      />
    </Routes>,
    {
      route: `/orgs/${SLUG}/work-hours/dev_agent`,
      i18n: { adapter: savedLocaleAdapter(locale) },
    },
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

describe('TierEditorDialog — continuous interval is server-authoritative', () => {
  test('accepts a non-divisor interval (5h) with NO client-side block and surfaces the server 422', async () => {
    let sentInterval: unknown = undefined;
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        const body = (await request.json()) as {
          working_hours?: { overrides?: Record<string, { interval?: unknown }> };
        };
        sentInterval = body.working_hours?.overrides?.dev_agent?.interval;
        return HttpResponse.json(
          { detail: { errors: ['interval 5h must evenly divide 24h'] } },
          { status: 422 },
        );
      }),
    );

    const user = userEvent.setup();
    renderDialog();

    // The continuous-mode interval is a free-form text input (placeholder "2h"),
    // NOT a divisor Select. Typing a non-divisor must be accepted client-side.
    const intervalInput = await screen.findByPlaceholderText('2h');
    await user.clear(intervalInput);
    await user.type(intervalInput, '5h');
    expect(intervalInput).toHaveValue('5h');

    // Agent tier saves directly (no impact-confirm step).
    await user.click(screen.getByRole('button', { name: translate('en', 'common.save') }));

    // The client sent the non-divisor value straight to the server — it did
    // not gate on divides-24h.
    await waitFor(() => expect(sentInterval).toBe('5h'));

    // The server's 422 is surfaced as a field error (the only validation
    // authority).
    expect(
      await screen.findByText('interval 5h must evenly divide 24h'),
    ).toBeInTheDocument();
    // ...under the localized blocking heading, with the daemon text verbatim.
    expect(screen.getByRole('alert')).toHaveTextContent(
      translate('en', 'workHours.dialog.saveRejected'),
    );
  });
});

describe('TierEditorDialog — zh-CN close control and ErrorPanel (THR-118 W4b)', () => {
  test('the dialog close control and the blocking ErrorPanel are localized', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: { errors: ['interval 5h must evenly divide 24h'] } }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderDialog('zh-CN');

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Close' })).toBeNull();

    await user.click(within(dialog).getByRole('button', { name: translate('zh-CN', 'common.save') }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('保存被拒绝 — 配置未写入。');
    // The daemon error string stays verbatim.
    expect(alert).toHaveTextContent('interval 5h must evenly divide 24h');
  });
});


describe('TierEditorDialog — reset through the saved tier patch', () => {
  test.each(['en', 'zh-CN'] as const)('%s interval reset sends null and retains other leaves, then notifies and closes', async (locale) => {
    const sent: unknown[] = [];
    server.use(http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
      sent.push(await request.json());
      return HttpResponse.json({});
    }));
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderDialog(locale, onSaved, onOpenChange);
    const dialog = await screen.findByRole('dialog');
    const interval = within(dialog).getByPlaceholderText('2h');
    expect(interval).toHaveValue('2h');
    // The reset is an ordinary sibling control of this field, independent of
    // its responsive position. Observe its value/provenance and actual HTTP.
    await user.click(within(interval.parentElement!).getByRole('button', {
      name: translate(locale, 'workHours.tier.reset'),
    }));
    expect(interval).toHaveValue('');
    expect(within(dialog).getByText(translate(locale, 'workHours.tier.inheritedGhost', {
      value: '2h', source: translate(locale, 'workHours.provenance.org'),
    }))).toBeInTheDocument();
    expect(sent).toEqual([]);
    await user.click(within(dialog).getByRole('button', { name: translate(locale, 'common.save') }));
    await waitFor(() => expect(sent).toEqual([{ working_hours: { overrides: { dev_agent: {
      mode: 'continuous', interval: null, catch_up_on_startup: false,
      window: { start: null, end: null, timezone: 'UTC' }, days: null,
    } } } }]));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onOpenChange).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
