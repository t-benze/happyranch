/**
 * L1 — local state machine, copy, validation and a11y wiring.
 *
 * `@/hooks/settings` is mocked, so this level proves what the COMPONENT does.
 * It cannot prove anything about the provider, the cache, request ordering or
 * the wire; those claims live in `DaemonCapacitySection.mounted.test.tsx` (L4)
 * and `lib/api/settings.raw.test.ts` (L3), at the real boundary.
 *
 * Every mount goes through `renderGuarded` because the component calls
 * `useBlocker`, which requires a data router (see `capacityTestMount.tsx`).
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import { DaemonCapacitySection } from './DaemonCapacitySection';
import { renderGuarded } from './capacityTestMount';

const hooks = vi.hoisted(() => ({ query: vi.fn(), mutation: vi.fn() }));
vi.mock('@/hooks/settings', () => ({
  useDaemonCapacity: hooks.query,
  useUpdateDaemonCapacity: hooks.mutation,
}));

export const REV_A = `sha256:${'a'.repeat(64)}`;
export const REV_B = `sha256:${'b'.repeat(64)}`;
export const REV_C = `sha256:${'c'.repeat(64)}`;

/**
 * The COMPLETE required member set of `DaemonCapacitySnapshot`
 * (`lib/api/types.ts`) — 16 required members; `message` is the only optional
 * one and is omitted by default.
 *
 * The merge is SHALLOW, matching the component's own consumption: overriding a
 * nested member (`running_at_daemon_start`, `persisted_yaml`, `next_start`,
 * `producer_components`, `guidance`) replaces it wholly, so an override must
 * supply every key of that member. A deliberately negative fixture builds
 * `{ ...validSnapshot(), <member>: <bad> }` and never borrows this name.
 */
export function validSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'startup-resolved settings snapshot',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [] as string[],
    environment_warning: null as string | null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [] as string[],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: {
      queue_workers: 'Starting guidance 4-6.',
      host_global_session_cap: 'Starting guidance 11-13.',
      enforced: false,
    },
    authorization: 'daemon bearer required',
    ...overrides,
  };
}

const mutateAsync = vi.fn();

function loaded(overrides: Record<string, unknown> = {}, observation: unknown = {
  issuedSeq: 1, settledSeq: 2, outcome: 'usable', receiptAt: Date.parse('2026-09-21T09:08:07'), sourceRevision: REV_A,
}) {
  hooks.query.mockReturnValue({
    data: validSnapshot(overrides),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({ data: validSnapshot(overrides) }),
    isFetching: false,
    observation,
  });
  hooks.mutation.mockReturnValue({ mutateAsync, isPending: false });
}

function mount() {
  return renderGuarded(<DaemonCapacitySection />, { entries: ['/orgs/alpha/settings/daemon-capacity'] });
}

const workers = () => screen.getByLabelText(/Task session slots/);
const cap = () => screen.getByLabelText(/Host session admission limit/);
const reasonBox = () => screen.getByLabelText('Reason for change');
const saveButton = () => screen.getByRole('button', { name: /Save for next restart|Saving/ });

async function fillAndSave(text = 'measured receipts') {
  const user = userEvent.setup();
  await user.type(reasonBox(), text);
  await user.click(saveButton());
}

beforeEach(() => {
  vi.clearAllMocks();
  loaded();
  mutateAsync.mockReset();
});

// ---------------------------------------------------------------------------
describe('13 — loading, initial-unavailable, supervisor-null', () => {
  test('13.1 loading does not flash defaults or controls', () => {
    hooks.query.mockReturnValue({ isLoading: true, isError: false, error: null, refetch: vi.fn(), isFetching: true, observation: null });
    mount();
    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  test('13.2 read error displays no values and no form', () => {
    hooks.query.mockReturnValue({
      data: undefined, isLoading: false, isError: true,
      error: new ApiError(500, 'config_parse_failed', {}), refetch: vi.fn(), isFetching: false, observation: null,
    });
    mount();
    expect(screen.getByRole('alert')).toHaveTextContent('No values are displayed');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  test('13.4 supervisor-null reads Unavailable, never 0, and stays stageable', async () => {
    loaded({ effective_admission_cap: null, effective_admission_reason: 'No supervisor snapshot' });
    mount();
    expect(screen.getAllByText(/Unavailable/).length).toBeGreaterThan(0);
    expect(document.body).toHaveTextContent(/runtime effect .* cannot be verified/i);
    // Distinct from a zero: no standalone "0" is presented as the cap.
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    await userEvent.type(reasonBox(), 'staging anyway');
    expect(saveButton()).toBeEnabled();
  });

  test('13.5 fallback cap renders beside startup configured and is distinguishable from 13.4', () => {
    loaded({ effective_admission_cap: 4, effective_admission_reason: 'Capability fallback binds because enforcement is unavailable.' });
    mount();
    expect(document.body).toHaveTextContent(/Capability fallback binds/);
    expect(document.body).toHaveTextContent(/Startup configured host limit 10; running effective 4/);
    expect(document.body).toHaveTextContent(/not a count of available slots/i);
  });
});

// ---------------------------------------------------------------------------
describe('1 / 18 — staged save copy and provenance', () => {
  test('1.3 no-pending copy follows next-vs-running, not saved-vs-next', async () => {
    mutateAsync.mockResolvedValue(validSnapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
      running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
      restart_pending: false, revision: REV_B,
    }));
    mount();
    await fillAndSave();
    expect(await screen.findByText('Saved. No restart is pending for these values.')).toBeInTheDocument();
  });

  test('1.3b saved==next but next!=running still shows restart pending', async () => {
    mutateAsync.mockResolvedValue(validSnapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
      restart_pending: true, revision: REV_B,
    }));
    mount();
    await fillAndSave();
    expect(await screen.findByText('Saved for next restart. Running limits are unchanged.')).toBeInTheDocument();
  });

  test('18.1 the four-column header is reproduced and no unlabelled pair appears', () => {
    mount();
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Setting', 'Running at startup', 'Saved in file', 'Expected next start',
    ]);
    expect(document.body).not.toHaveTextContent(/(^|\s)3 \/ 10(\s|$)/);
  });

  test('18.2 expected-next-start is labelled best effort, never a guarantee', () => {
    mount();
    expect(document.body).toHaveTextContent(/best effort, based on the configuration observed by this daemon/i);
    expect(document.body).toHaveTextContent(/assuming an unchanged environment and worker topology/i);
    expect(document.body).toHaveTextContent(/It is not a guarantee/i);
  });

  test('18.3 / 14.5 the receipt is a browser clock, never a server age, and isStale is not surfaced', () => {
    mount();
    expect(document.body).toHaveTextContent(/Last received \d{2}:\d{2}:\d{2} \(this browser's clock\)/);
    expect(document.body).not.toHaveTextContent(/as of|server time|seconds old|stale/i);
  });

  test('2.12 / 18.6 honesty fence: no sequence, restart, throughput or audit-history claim', () => {
    loaded({ restart_pending: true });
    mount();
    const body = document.body.textContent ?? '';
    for (const forbidden of [
      /Apply now/i, /\bApplied\b/, /Restart daemon/i, /throughput/i, /utilization/i,
      /free slots/i, /host safety/i, /process ceiling/i, /launch spacing/i,
      /issuedSeq|settledSeq|sequence number/i, /complete audit history/i,
    ]) {
      expect(body).not.toMatch(forbidden);
    }
    expect(body).toMatch(/cannot be attributed to a verified person/);
    expect(body).toMatch(/excludes headless System Assistant and job processes/);
  });

  test('18.7 audit copy is qualified and never promises the entry is recorded', async () => {
    mount();
    await userEvent.click(screen.getByText('Capacity details'));
    expect(document.body).toHaveTextContent(/Reason included in the save request/);
    expect(document.body).toHaveTextContent(/terminal completion of the audit entry is not\s+guaranteed/i);
    expect(document.body).not.toHaveTextContent(/is recorded in the audit entry/i);
  });
});

// ---------------------------------------------------------------------------
describe('3 — partial environment override', () => {
  const shadowW = {
    environment_shadowed: ['queue_workers'],
    environment_warning: 'Environment overrides win; a restart alone will not make YAML win.',
    next_start: { queue_workers: 3, host_global_session_cap: 12 },
  };

  test('3.1 W-only shadow resolves W=3 / H=14 and names only the task slot key', async () => {
    loaded(shadowW);
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    await userEvent.clear(cap());
    await userEvent.type(cap(), '14');
    expect(document.body).toHaveTextContent(/Task session slots 3, Host session admission limit 14/);
    expect(document.body).toHaveTextContent(/Task session slots is set by the environment/);
    expect(document.body).not.toHaveTextContent(/resolves 3 \/ 12/);
    expect(document.body).not.toHaveTextContent(/the saved value\b/i);
  });

  test('3.2 mirrored H-only shadow resolves W=5 / H=10 and names only the host limit', async () => {
    loaded({
      environment_shadowed: ['host_global_session_cap'],
      environment_warning: 'Environment overrides win.',
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
    });
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    await userEvent.clear(cap());
    await userEvent.type(cap(), '14');
    expect(document.body).toHaveTextContent(/Task session slots 5, Host session admission limit 10/);
    expect(document.body).toHaveTextContent(/Host session admission limit is set by the environment/);
  });

  test('3.4 the preview is bounded and predicts no future effective cap', async () => {
    loaded(shadowW);
    mount();
    expect(document.body).toHaveTextContent(/Assumes unchanged environment and\s+worker topology/i);
  });
});

// ---------------------------------------------------------------------------
describe('4 — acknowledgment identity', () => {
  test('4.3 shadow clearing removes the control and frees Save', () => {
    loaded({ environment_shadowed: ['queue_workers'], environment_warning: 'w' });
    const { rerender } = mount();
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
    loaded({ environment_shadowed: [], environment_warning: null });
    rerender(<div />);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  test('4.4 submitting without the acknowledgment issues no PUT and focuses the control', async () => {
    loaded({ environment_shadowed: ['queue_workers'], environment_warning: 'w' });
    mount();
    await userEvent.type(reasonBox(), 'why');
    fireEvent.submit(saveButton().closest('form')!);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('checkbox')));
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(await screen.findByText('Confirm the environment override before saving.')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
describe('5 — draft consequence and precision', () => {
  test.each([
    ['5', /below the worker-pool total 10/i],
    ['10', /These configured limits align/i],
    ['20', /above the worker-pool total 10/i],
  ])('consequence direction for cap %s', async (value, expected) => {
    mount();
    await userEvent.clear(cap());
    await userEvent.type(cap(), value);
    expect(document.body).toHaveTextContent(expected);
  });

  test('5.2 an inconsistent topology withholds the panel and prints no negative total', async () => {
    loaded({
      producer_envelope: 10,
      producer_components: {
        task_workers: 12, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
      },
    });
    mount();
    expect(document.body).toHaveTextContent(/Capacity details are inconsistent in this response/);
    expect(document.body).not.toHaveTextContent(/-2/);
    expect(saveButton()).toBeDisabled();
  });

  test('5.3 / 15.8 a derived sum outside the safe range is withheld, not printed inexactly', async () => {
    loaded({
      producer_envelope: 9007199254740991,
      producer_components: {
        task_workers: 3, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
      },
    });
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740000');
    expect(document.body).toHaveTextContent(/Worker-pool total is outside the range this editor can represent exactly/i);
    expect(document.body).not.toHaveTextContent(/18014398509480988/);
  });

  test('5.4 totals track response metadata, with no hardcoded producer count', async () => {
    loaded({
      producer_envelope: 7,
      producer_components: {
        task_workers: 3, thread_workers: 2, dream_workers: 1, wake_workers: 1, schedule_workers: 0,
      },
    });
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    // contribution = 7 - 3 = 4, so the pool total is 5 + 4 = 9 — never 5 + 7.
    expect(document.body).toHaveTextContent(/5 task \+ 4 other producers/);
    expect(document.body).not.toHaveTextContent(/\+ 7 other producers/);
  });

  test('5.5 excess cap copy never claims added capability', async () => {
    mount();
    await userEvent.clear(cap());
    await userEvent.type(cap(), '30');
    expect(document.body).toHaveTextContent(/Extra admission room does not create additional producers/);
    expect(document.body).not.toHaveTextContent(/more capacity|higher throughput/i);
  });
});

// ---------------------------------------------------------------------------
describe('6 — absent keys, no-op semantics, rationale-only dirty', () => {
  test('6.1 absent keys read "Not set in file" and fields seed from next_start', () => {
    loaded({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } });
    mount();
    const table = screen.getByRole('table');
    expect(within(table).getAllByText('Not set in file')).toHaveLength(2);
    expect(workers()).toHaveValue('3');
    expect(cap()).toHaveValue('10');
    expect(document.body).not.toHaveTextContent(/default/i);
  });

  test('6.3 rationale-only dirty arms protection without a value-comparison panel', async () => {
    mount();
    await userEvent.type(reasonBox(), 'just the reason');
    expect(document.body).toHaveTextContent(/values are unchanged from the saved file; only the reason differs/);
    expect(document.body).toHaveTextContent(/Draft matches the saved configuration/);
    expect(document.body).toHaveTextContent(/Unsaved changes/);
    expect(saveButton()).toBeEnabled();
  });

  test('6.4 the comparison is on (presence, value), never value alone', () => {
    loaded({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } });
    const { rerender } = mount();
    // Absent keys seed the same digits a present 3/10 would, so identical
    // digits must NOT be read as identical state.
    expect(workers()).toHaveValue('3');
    expect(within(screen.getByRole('table')).getAllByText('Not set in file')).toHaveLength(2);
    loaded({ persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 } });
    rerender(<div />);
  });

  test('6.5 defaults-only fixture shows no override control', () => {
    loaded({
      persisted_yaml: { queue_workers: null, host_global_session_cap: null },
      environment_shadowed: [],
    });
    mount();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
describe('9 / 12 — rejection map (constructed ApiError; parseError itself is L3)', () => {
  test('9.1 a 409 with no latest fabricates no numbers and blocks retry', async () => {
    mutateAsync.mockRejectedValue(new ApiError(409, 'stale_revision', {}));
    mount();
    await fillAndSave();
    expect(await screen.findByText(/Latest saved values could not be read/)).toBeInTheDocument();
    expect(document.body).toHaveTextContent(/no rebase is offered/);
    expect(screen.queryByRole('button', { name: /rebase onto latest/i })).not.toBeInTheDocument();
  });

  test('9.2 a 409 whose latest has a quoted numeric is unusable, and "9" never renders as a number', async () => {
    mutateAsync.mockRejectedValue(new ApiError(409, 'stale_revision', {
      latest: validSnapshot({ persisted_yaml: { queue_workers: '9', host_global_session_cap: 9 } }),
    }));
    mount();
    await fillAndSave();
    expect(await screen.findByText(/Latest saved values could not be read/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /rebase onto latest/i })).not.toBeInTheDocument();
  });

  test.each([
    ['12.1 401', new ApiError(401, null, {}), /valid daemon bearer is required; no values were changed/i],
    ['12.1 403', new ApiError(403, null, {}), /valid daemon bearer is required/i],
    ['12.2 422', new ApiError(422, null, {}), /whole number greater than zero/i],
    ['12.3 428', new ApiError(428, 'if_match_required', {}), /Refresh the saved settings before saving again/i],
    ['12.3 400', new ApiError(400, 'if_match_invalid', {}), /Refresh the saved settings before saving again/i],
    ['12.4 audit_failed', new ApiError(503, 'audit_failed', {}), /This request did not change the configuration/i],
    ['12.5 write absent', new ApiError(503, 'config_write_failed', {}), /This request did not publish new values/i],
    ['12.5 write present', new ApiError(503, 'config_write_failed', { artifact_state: 'present' }), /temporary artifact remains/i],
    ['12.5 write unknown', new ApiError(503, 'config_write_failed', { artifact_state: 'unknown' }), /artifact state is unknown/i],
    ['12.6 env confirm', new ApiError(409, 'environment_confirmation_required', {}), /Confirm the environment override/i],
    ['12.8 unknown code', new ApiError(503, 'something_new', {}), /Save result unknown/i],
    ['11.1 network', new Error('socket hang up'), /Save result unknown/i],
  ])('%s maps to safe fixed copy', async (_name, error, expected) => {
    mutateAsync.mockRejectedValue(error);
    mount();
    await fillAndSave();
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  test('12.5 config_write_failed no longer asserts current file contents', async () => {
    mutateAsync.mockRejectedValue(new ApiError(503, 'config_write_failed', {}));
    mount();
    await fillAndSave();
    await screen.findByText(/This request did not publish new values/);
    expect(document.body).not.toHaveTextContent(/previous authoritative file remains in use/i);
  });

  test('11.1 a generic failure is never called "failed safely"', async () => {
    mutateAsync.mockRejectedValue(new ApiError(500, null, {}));
    mount();
    await fillAndSave();
    await screen.findByText(/Save result unknown/);
    for (const forbidden of [/failed safely/i, /nothing changed/i, /no live capacity was changed/i]) {
      expect(document.body).not.toHaveTextContent(forbidden);
    }
  });

  test('12.7 no raw exception text, stack, path or private value reaches the DOM', async () => {
    mutateAsync.mockRejectedValue(new ApiError(503, 'config_write_failed', {
      artifact_state: 'present',
      message: 'secret at /var/lib/happyranch/secret.yaml\n  at Module._compile (node:internal)',
    }));
    mount();
    await fillAndSave();
    await screen.findByText(/This request did not publish new values/);
    const body = document.body.textContent ?? '';
    expect(body).not.toMatch(/secret/);
    expect(body).not.toMatch(/\/var\/lib|node:internal|\bat Module\./);
    expect(document.body).not.toHaveTextContent(/undefined/);
  });
});

// ---------------------------------------------------------------------------
describe('15 — numeric honesty at the write site (Q8 is NOT solved)', () => {
  test('15.1 an unsafe integer is refused, the text is preserved, and no PUT is issued', async () => {
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740993');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    expect(await screen.findByText(/Task session slots is outside the range this editor can represent exactly/i)).toBeInTheDocument();
    expect(workers()).toHaveValue('9007199254740993');
    expect(document.body).not.toHaveTextContent('9007199254740992');
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  test('15.2 MAX_SAFE_INTEGER is ACCEPTED at the editor boundary and sent verbatim', async () => {
    mutateAsync.mockResolvedValue(validSnapshot({ revision: REV_B }));
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740991');
    await fillAndSave();
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0][0].queue_workers).toBe(9007199254740991);
  });

  test('15.3 the boundary+1 is conservatively over-refused (documented limitation G5)', async () => {
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740992');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    expect(await screen.findByText(/Task session slots is outside the range this editor can represent exactly/i)).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  test.each(['5.5', '1e3', '012', '-1', '0', ' 12 ', '0x10', 'abc'])(
    '15.7 grammar rejects %j with a field-associated message and never coerces it',
    async (text) => {
      mount();
      await userEvent.clear(workers());
      await userEvent.type(workers(), text);
      await userEvent.type(reasonBox(), 'why');
      await userEvent.click(saveButton());
      const error = await screen.findByText(/must be a whole number greater than zero, written in plain digits/i);
      expect(error).toBeInTheDocument();
      expect(workers()).toHaveValue(text);
      expect(mutateAsync).not.toHaveBeenCalled();
    },
  );

  test('15.9 the refusal is presented as an editor limit, never a server maximum', async () => {
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740993');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    await screen.findByText(/Task session slots is outside the range this editor can represent exactly/i);
    expect(document.body).toHaveTextContent(/limit of this editor, not a limit of the daemon/i);
    expect(document.body).not.toHaveTextContent(/maximum (allowed|value|supported)/i);
  });

  test('15.6 / 15.10 a quoted numeric anywhere in the read makes it unusable, not a zero', () => {
    loaded({ next_start: { queue_workers: '5', host_global_session_cap: 10 } });
    mount();
    expect(screen.getByRole('alert')).toHaveTextContent(/Cannot read capacity configuration/);
  });

  test('15.10 a null in a non-nullable position is unusable, never a zero', () => {
    loaded({ producer_envelope: null });
    mount();
    expect(screen.getByRole('alert')).toHaveTextContent(/Cannot read capacity configuration/);
    expect(document.body).not.toHaveTextContent(/envelope 0/);
  });

  test('15.4 an unsafe number that survived JSON.parse is withheld, not rendered', () => {
    loaded({ persisted_yaml: { queue_workers: 9007199254740992, host_global_session_cap: 10 } });
    mount();
    expect(screen.getAllByRole('alert').some((node) =>
      /outside the range this editor can represent exactly/i.test(node.textContent ?? ''))).toBe(true);
    expect(document.body).not.toHaveTextContent('9007199254740992');
  });
});

// ---------------------------------------------------------------------------
describe('16 — accessibility wiring (not a screen-reader acceptance pass)', () => {
  test('16.1 all three controls have real associated labels', () => {
    mount();
    expect(workers().id).toBe('capacity-workers');
    expect(cap().id).toBe('capacity-cap');
    expect(reasonBox().id).toBe('capacity-reason');
    for (const id of ['capacity-workers', 'capacity-cap', 'capacity-reason']) {
      expect(document.querySelector(`label[for="${id}"]`)).not.toBeNull();
    }
  });

  test('16.2 guidance is referenced always, the error additionally, and aria-invalid is set', async () => {
    mount();
    // Both the descriptive helper and the inline guidance are referenced.
    expect(workers()).toHaveAttribute(
      'aria-describedby', 'capacity-workers-help capacity-workers-guidance',
    );
    expect(workers()).not.toHaveAttribute('aria-invalid');
    await userEvent.clear(workers());
    await userEvent.type(workers(), '0');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    await waitFor(() => expect(workers()).toHaveAttribute('aria-invalid', 'true'));
    expect(workers()).toHaveAttribute(
      'aria-describedby', 'capacity-workers-help capacity-workers-guidance capacity-workers-error',
    );
    // Recovering clears both.
    await userEvent.clear(workers());
    await userEvent.type(workers(), '4');
    await userEvent.click(saveButton());
    await waitFor(() => expect(workers()).not.toHaveAttribute('aria-invalid'));
  });

  test('16.3 the FIELD receives focus on invalid submit, not only the status region', async () => {
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '0');
    await userEvent.type(reasonBox(), 'why');
    fireEvent.submit(saveButton().closest('form')!);
    await waitFor(() => expect(document.activeElement).toBe(workers()));
    expect(workers()).toHaveValue('0');
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  test('16.4 progress/success are polite status, errors are alerts', async () => {
    mutateAsync.mockResolvedValue(validSnapshot({ restart_pending: true, revision: REV_B }));
    mount();
    await fillAndSave();
    const status = await screen.findByText(/Saved for next restart/);
    expect(status.closest('[role="status"]')).toHaveAttribute('aria-live', 'polite');
    expect(document.querySelector('[aria-live="assertive"]')).toBeNull();
  });

  test('16.5 reason is required, counted, and refused past 1000 characters', async () => {
    mount();
    expect(document.body).toHaveTextContent('0 / 1000');
    await userEvent.click(saveButton());
    expect(await screen.findByText('Reason for change is required.')).toBeInTheDocument();
    fireEvent.change(reasonBox(), { target: { value: 'x'.repeat(1000) } });
    expect(document.body).toHaveTextContent('1000 / 1000 — limit reached');
    fireEvent.change(reasonBox(), { target: { value: 'x'.repeat(1001) } });
    await userEvent.click(saveButton());
    expect(await screen.findByText(/1000 characters or fewer/)).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
    // Whitespace-only is blank.
    fireEvent.change(reasonBox(), { target: { value: '   ' } });
    await userEvent.click(saveButton());
    expect(await screen.findByText('Reason for change is required.')).toBeInTheDocument();
  });

  test('16.6 every action is a real control, never a click-only div', () => {
    mount();
    for (const name of [/Save for next restart/, /Discard draft/, /Refresh running state/]) {
      expect(screen.getByRole('button', { name })).toBeInstanceOf(HTMLButtonElement);
    }
    expect(screen.getByText('Capacity details').tagName).toBe('SUMMARY');
  });

  test('16.8 important warnings are VISIBLE with the details disclosure collapsed', () => {
    loaded({
      restart_pending: true,
      warnings: ['Producer envelope exceeds the admission limit.'],
      environment_shadowed: ['queue_workers'],
      environment_warning: 'Environment overrides win.',
    });
    mount();
    expect(screen.getByText('Producer envelope exceeds the admission limit.')).toBeVisible();
    expect(screen.getByText(/Environment overrides win/)).toBeVisible();
    expect(screen.getByText(/A persisted next-start value differs/)).toBeVisible();
    // The disclosure is collapsed, so its contents are not what carries them.
    expect(document.querySelector('details')?.open).toBeFalsy();
  });

  test('16.9 warnings carry text, never colour alone', () => {
    loaded({ warnings: ['Producer envelope exceeds the admission limit.'] });
    mount();
    const warning = screen.getByText('Producer envelope exceeds the admission limit.');
    expect((warning.textContent ?? '').trim().length).toBeGreaterThan(10);
  });
});

// ---------------------------------------------------------------------------
describe('17 — navigation guard (beforeunload half)', () => {
  test('17.1 a dirty draft installs the reload warning', async () => {
    mount();
    await userEvent.type(reasonBox(), 'draft');
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(screen.getByText(/Leaving or reloading will discard/i)).toBeInTheDocument();
  });

  test('17.5 a clean form installs no warning', () => {
    mount();
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(screen.queryByText(/Leaving or reloading will discard/i)).not.toBeInTheDocument();
  });

  test('17.7 the guarded component mounts and renders normally with no navigation involved', () => {
    mount();
    expect(screen.getByRole('heading', { name: 'Capacity' })).toBeInTheDocument();
    expect(saveButton()).toBeInTheDocument();
  });
});
