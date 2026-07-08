import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const TASK = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  block_kind: null,
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-05-18T10:00:00Z',
  updated_at: '2026-05-18T10:06:12Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
};

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({ tasks: [TASK] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
      HttpResponse.json({
        task: TASK,
        results: [],
        audit_log: [],
        revisit_chain: [],
        direct_revisits: [],
        predecessor_prior_status: null,
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
      HttpResponse.json({
        task_id: TASK.task_id,
        assigned_agent: 'content_writer',
        brief: TASK.brief,
        status: TASK.status,
        output_summary: null,
        children: [],
      }),
    ),
    // SSE — return an empty stream so subscribeSSE opens, gets no events, and stays quiet
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

const ESCALATED_TASK = { ...TASK, status: 'escalated', block_kind: null };

// Mirrors stubBaseHandlers but serves the task in the `escalated` status so the
// TaskDetailPage renders the escalation action set (Continue + Cancel).
function stubEscalatedHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({ tasks: [ESCALATED_TASK] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
      HttpResponse.json({
        task: ESCALATED_TASK,
        results: [],
        audit_log: [],
        revisit_chain: [],
        direct_revisits: [],
        predecessor_prior_status: null,
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
      HttpResponse.json({
        task_id: TASK.task_id,
        assigned_agent: 'content_writer',
        brief: TASK.brief,
        status: 'escalated',
        output_summary: null,
        children: [],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

describe('Tasks write path', () => {
  test('cancels a task end-to-end', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let cancelCalled = false;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/cancel`, () => {
        cancelCalled = true;
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Wait for the "Cancel" button to appear in the detail pane header
    await screen.findByRole('button', { name: /^Cancel$/ });

    // Click the "Cancel" ghost button in the Drawer header (opens the dialog)
    await user.click(screen.getByRole('button', { name: /^Cancel$/ }));

    // Fill in the cancellation reason
    await user.type(
      screen.getByPlaceholderText(/Reason for cancellation/),
      'No longer needed.',
    );

    // Submit the destructive button — distinct name from the header's "Cancel"
    await user.click(screen.getByRole('button', { name: /^Cancel task$/ }));

    // Dialog should close after successful mutation
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^Cancel task$/ })).toBeNull();
    });

    expect(cancelCalled).toBe(true);
  });

  test('cancels a task with blank reason', async () => {
    // THR-046: cancel with blank reason is allowed; payload includes rationale field.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let cancelBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/cancel`, async ({ request }) => {
        cancelBody = await request.json();
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    await screen.findByRole('button', { name: /^Cancel$/ });
    await user.click(screen.getByRole('button', { name: /^Cancel$/ }));

    // Submit without typing anything — button is no longer disabled for blank text
    await user.click(screen.getByRole('button', { name: /^Cancel task$/ }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^Cancel task$/ })).toBeNull();
    });

    expect(cancelBody).toEqual({ rationale: '' });
  });

  test('escalated task detail shows only Continue + Cancel (no Resolve…/Revisit)', async () => {
    // THR-069 msg74: the escalated action set is exactly Continue + Cancel.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubEscalatedHandlers();

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Continue + Cancel are present…
    await screen.findByRole('button', { name: /^Continue$/ });
    expect(screen.getByRole('button', { name: /^Cancel$/ })).toBeInTheDocument();

    // …and Resolve… / Revisit are gone for an escalated task.
    expect(screen.queryByRole('button', { name: /Resolve…/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Revisit$/ })).toBeNull();
  });

  test('non-escalated task detail keeps Revisit + Cancel (no Continue)', async () => {
    // Guardrail: the change is scoped to isEscalated only — a normal
    // in_progress task still offers Revisit + the generic Cancel.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    await screen.findByRole('button', { name: /^Revisit$/ });
    expect(screen.getByRole('button', { name: /^Cancel$/ })).toBeInTheDocument();

    // No escalation-only actions on a non-escalated task.
    expect(screen.queryByRole('button', { name: /^Continue$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Resolve…/ })).toBeNull();
  });

  test('escalated Cancel routes to resolve-escalation cancel, NOT generic /cancel', async () => {
    // LOAD-BEARING (THR-075 ruling): escalated Cancel MUST hit
    // POST /resolve-escalation {decision:'cancel'} — the generic /cancel would
    // leave the Feishu escalation notification dangling and write the wrong
    // audit row.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubEscalatedHandlers();

    let resolveBody: unknown = null;
    let genericCancelCalled = false;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/resolve-escalation`,
        async ({ request }) => {
          resolveBody = await request.json();
          return HttpResponse.json({
            ok: true,
            task_id: TASK.task_id,
            new_status: 'cancelled',
          });
        },
      ),
      http.post(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/cancel`, () => {
        genericCancelCalled = true;
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Open the escalation Cancel dialog from the action row.
    await screen.findByRole('button', { name: /^Cancel$/ });
    await user.click(screen.getByRole('button', { name: /^Cancel$/ }));

    // Rationale is optional for cancel — submit straight away.
    await user.click(screen.getByRole('button', { name: /^Cancel task$/ }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^Cancel task$/ })).toBeNull();
    });

    expect(resolveBody).toEqual({ decision: 'cancel', rationale: '' });
    expect(genericCancelCalled).toBe(false);
  });

  test('escalated Continue routes to resolve-escalation continue with required rationale', async () => {
    // THR-075: continue requires non-empty rationale and resumes → pending.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubEscalatedHandlers();

    let resolveBody: unknown = null;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/resolve-escalation`,
        async ({ request }) => {
          resolveBody = await request.json();
          return HttpResponse.json({
            ok: true,
            task_id: TASK.task_id,
            new_status: 'pending',
          });
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Open the Continue dialog from the action row.
    await screen.findByRole('button', { name: /^Continue$/ });
    await user.click(screen.getByRole('button', { name: /^Continue$/ }));

    // Dialog primary "Continue task" is disabled while rationale is empty.
    const confirmBtn = screen.getByRole('button', { name: /^Continue task$/ });
    expect(confirmBtn).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/Rationale/), 'go ahead');
    expect(confirmBtn).not.toBeDisabled();

    await user.click(confirmBtn);

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /^Continue task$/ }),
      ).toBeNull();
    });

    expect(resolveBody).toEqual({ decision: 'continue', rationale: 'go ahead' });
  });
});
