import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { ExecutorBinariesSection } from './ExecutorBinariesSection';

interface Entry {
  kind: string;
  path: string | null;
  valid: boolean;
}

function stubRegistry(entries: Entry[]) {
  server.use(
    http.get('/api/v1/executor-binaries', () => HttpResponse.json({ entries })),
  );
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<ExecutorBinariesSection />);
}

describe('ExecutorBinariesSection (Settings → Executors → CLI binary paths)', () => {
  test('registered: shows the stored path + valid pill, no fresh-env banner', async () => {
    stubRegistry([{ kind: 'claude', path: '/usr/local/bin/claude', valid: true }]);
    render();

    const row = await screen.findByTestId('binary-row-claude');
    expect(within(row).getByText('/usr/local/bin/claude')).toBeInTheDocument();
    expect(within(row).getByTestId('binary-validity')).toHaveAttribute(
      'data-validity',
      'valid',
    );
    // At least one kind is registered → no blocked banner.
    expect(screen.queryByTestId('fresh-env-blocked')).not.toBeInTheDocument();
  });

  test('fresh env: nothing registered renders the actionable blocked banner + all four kinds', async () => {
    stubRegistry([]);
    render();

    expect(await screen.findByTestId('fresh-env-blocked')).toBeInTheDocument();
    for (const kind of ['claude', 'codex', 'pi', 'opencode']) {
      const row = screen.getByTestId(`binary-row-${kind}`);
      expect(within(row).getByTestId('binary-validity')).toHaveAttribute(
        'data-validity',
        'unregistered',
      );
      // The manual-entry remediation is present on every row.
      expect(within(row).getByLabelText(/Register binary path/i)).toBeInTheDocument();
    }
  });

  test('validate: an invalid path surfaces the daemon reason inline (no opaque failure)', async () => {
    stubRegistry([]);
    server.use(
      http.post('/api/v1/executor-binaries/validate', () =>
        HttpResponse.json({
          path: '/nope',
          valid: false,
          error: 'Path does not exist: /nope',
        }),
      ),
    );
    render();

    const row = await screen.findByTestId('binary-row-claude');
    await userEvent.type(within(row).getByLabelText(/Register binary path/i), '/nope');
    await userEvent.click(within(row).getByRole('button', { name: /^Validate$/i }));

    expect(await within(row).findByTestId('binary-check-claude')).toHaveTextContent(
      /Path does not exist/i,
    );
  });
});
