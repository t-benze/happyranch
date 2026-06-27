import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { TierEditorDialog } from './TierEditorDialog';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
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

function renderDialog() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/orgs/:slug/*"
        element={
          <TierEditorDialog
            open
            onOpenChange={() => {}}
            tier={{ kind: 'agent', agent: 'dev_agent' }}
            wh={continuousWh()}
            agentTeam={{ dev_agent: null }}
            allAgents={['dev_agent']}
            onSaved={() => {}}
          />
        }
      />
    </Routes>,
    { route: `/orgs/${SLUG}/work-hours/dev_agent` },
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
    await user.click(screen.getByRole('button', { name: 'Save' }));

    // The client sent the non-divisor value straight to the server — it did
    // not gate on divides-24h.
    await waitFor(() => expect(sentInterval).toBe('5h'));

    // The server's 422 is surfaced as a field error (the only validation
    // authority).
    expect(
      await screen.findByText('interval 5h must evenly divide 24h'),
    ).toBeInTheDocument();
  });
});
