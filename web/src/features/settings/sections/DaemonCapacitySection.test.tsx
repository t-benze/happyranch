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
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useSyncExternalStore } from 'react';
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
    running_provenance: 'Resolved when the HappyRanch service started',
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
  hooks.mutation.mockReturnValue({ mutateAsync, isPending: false, settlementOf: () => null });
}

function mount() {
  return renderGuarded(<DaemonCapacitySection />, { entries: ['/orgs/alpha/settings/daemon-capacity'] });
}

const workers = () => screen.getByLabelText(/Task session limit/);
const cap = () => screen.getByLabelText(/Overall supervised-session limit/);
const reasonBox = () => screen.getByLabelText('Reason for change');
const saveButton = () => screen.getByRole('button', { name: /Save for next start|Saving/ });

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
    loaded({ effective_admission_cap: null, effective_admission_reason: 'HappyRanch cannot currently verify the overall supervised-session limit.' });
    mount();
    expect(screen.getAllByText(/Unavailable/).length).toBeGreaterThan(0);
    expect(document.body).toHaveTextContent(/runtime effect .* cannot be verified/i);
    // Distinct from a zero: no standalone "0" is presented as the cap.
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    await userEvent.type(reasonBox(), 'staging anyway');
    expect(saveButton()).toBeEnabled();
  });

  test('13.5 fallback cap renders beside startup configured and is distinguishable from 13.4', () => {
    loaded({ effective_admission_cap: 4, effective_admission_reason: 'The active execution backend cannot enforce every host-safety check, so HappyRanch is using a lower session limit.' });
    mount();
    expect(document.body).toHaveTextContent(/The active execution backend cannot enforce every host-safety check/);
    expect(document.body).toHaveTextContent(/Configured at startup: 10\. Limit in effect now: 4/);
    expect(document.body).toHaveTextContent(/not counts of sessions in use or available/i);
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
    expect(await screen.findByText('Saved. These values already match the limits in effect.')).toBeInTheDocument();
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
    expect(await screen.findByText('Saved for the next start. Limits in effect now have not changed.')).toBeInTheDocument();
  });

  test('18.1 the four-column header is reproduced and no unlabelled pair appears', () => {
    mount();
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Setting', 'In effect since startup', 'Saved configuration', 'Expected after next start',
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
    expect(document.body).toHaveTextContent(/Values received at \d{2}:\d{2}:\d{2} \(your device time\)/);
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
    expect(body).toMatch(/cannot link that token to a verified person/);
    expect(body).toMatch(/does not include background Assistant or job processes/);
  });

  test('18.7 audit copy is qualified and never promises the entry is recorded', async () => {
    mount();
    await userEvent.click(screen.getByText('Capacity details'));
    expect(document.body).toHaveTextContent(/Your reason is sent with the save request/);
    expect(document.body).toHaveTextContent(/cannot guarantee that every failed or uncertain save produces a completed audit entry/i);
    expect(document.body).not.toHaveTextContent(/is recorded in the audit entry/i);
  });
});

// ---------------------------------------------------------------------------
describe('3 — partial environment override', () => {
  const shadowW = {
    environment_shadowed: ['queue_workers'],
    environment_warning: 'An environment setting takes priority over the saved configuration. Restarting HappyRanch will not make the saved value take effect.',
    next_start: { queue_workers: 3, host_global_session_cap: 12 },
  };

  test('3.1 W-only shadow resolves W=3 / H=14 and names only the task slot key', async () => {
    loaded(shadowW);
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    await userEvent.clear(cap());
    await userEvent.type(cap(), '14');
    expect(document.body).toHaveTextContent(/Task session limit 3, Overall supervised-session limit 14/);
    expect(document.body).toHaveTextContent(/Task session limit is set by the environment/);
    expect(document.body).not.toHaveTextContent(/resolves 3 \/ 12/);
    expect(document.body).not.toHaveTextContent(/saved file|YAML/i);
    // R6 / accepted 3.1: the CONSEQUENCE uses the resolved W = 3, so the
    // worker-pool total is 3 + 7 = 10 — never the drafted 5 + 7 = 12.
    expect(screen.getAllByText('Total worker slots')[0].nextElementSibling?.firstChild?.textContent)
      .toBe('10');
    expect(screen.getAllByText('Total worker slots')[0].parentElement?.textContent)
      .toContain('3 task slots + 7 other worker slots');
    expect(document.body).toHaveTextContent(/The overall session limit \(14\) is higher than the total worker slots \(10\)/);
    expect(document.body).not.toHaveTextContent(/total worker slots 12/);
  });

  test('3.2 mirrored H-only shadow resolves W=5 / H=10 and names only the host limit', async () => {
    loaded({
      environment_shadowed: ['host_global_session_cap'],
      environment_warning: 'An environment setting takes priority over the saved configuration. Restarting HappyRanch will not make the saved value take effect.',
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
    });
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    await userEvent.clear(cap());
    await userEvent.type(cap(), '14');
    expect(document.body).toHaveTextContent(/Task session limit 5, Overall supervised-session limit 10/);
    expect(document.body).toHaveTextContent(/Overall supervised-session limit is set by the environment/);
    // The direction compares the RESOLVED cap 10 against the pool 5 + 7 = 12.
    expect(screen.getAllByText('Total worker slots')[0].parentElement?.textContent)
      .toContain('5 task slots + 7 other worker slots');
    expect(document.body).toHaveTextContent(/The overall session limit \(10\) is lower than the total worker slots \(12\)/);
    expect(document.body).not.toHaveTextContent(/higher than the total worker slots/);
  });

  test('3.4 the preview is bounded and predicts no future effective cap', async () => {
    loaded(shadowW);
    mount();
    await userEvent.clear(cap());
    await userEvent.type(cap(), '14');
    expect(document.body).toHaveTextContent(/Assumes unchanged environment and\s+worker topology/i);
    // No predicted FUTURE effective admission cap anywhere, in any state.
    expect(document.body).not.toHaveTextContent(/effective admission (cap )?will|future effective|predicted/i);
    expect(document.body).not.toHaveTextContent(/effective next start/i);
    // The only effective-cap number on screen is the OBSERVED running one.
    expect(screen.getByText('Overall supervised-session limit in effect').parentElement?.textContent)
      .toMatch(/10/);
  });
});

// ---------------------------------------------------------------------------
describe('4 — acknowledgment identity', () => {
  test('4.3 shadow clearing removes the control, resets ack and frees Save — with the component STILL MOUNTED', async () => {
    loaded({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'w',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    });
    mount();
    await userEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();
    await userEvent.type(reasonBox(), 'why');
    // Shadowed and un-acknowledged is a DISABLED Save; acknowledged is enabled.
    expect(saveButton()).toBeEnabled();
    expect(document.body).toHaveTextContent(/set by the environment/);

    // R9 4.3: re-render the SAME component with the new data. `rerender(<div />)`
    // unmounts it, so a missing checkbox afterwards proves nothing about the
    // transition.
    // R9 4.3: the component stays MOUNTED. The hook now returns an unshadowed
    // snapshot and an ordinary keystroke re-renders it — `rerender(<div />)`
    // would unmount it, and a missing checkbox afterwards would prove nothing.
    loaded({ environment_shadowed: [], environment_warning: null });
    await userEvent.type(reasonBox(), '!');

    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());
    // The panel and its preview are gone; Save is free without an ack.
    expect(document.body).not.toHaveTextContent(/set by the environment/);
    expect(document.body).not.toHaveTextContent(/An environment setting overrides a saved value/);
    expect(saveButton()).toBeEnabled();
    // The editor and reason survive the transition.
    expect(reasonBox()).toHaveValue('why!');
    expect(workers()).toHaveValue('3');
    expect(mutateAsync).not.toHaveBeenCalled();
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
    ['5', /lower than the total worker slots \(10\)/i],
    ['10', /These limits match/i],
    ['20', /higher than the total worker slots \(10\)/i],
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
    expect(document.body).toHaveTextContent(/HappyRanch returned conflicting capacity values/);
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
    expect(document.body).toHaveTextContent(/Total worker slots are outside the range this editor can represent exactly/i);
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
    expect(document.body).toHaveTextContent(/5 task slots \+ 4 other worker slots/);
    expect(document.body).not.toHaveTextContent(/\+ 7 other worker slots/);
  });

  test('5.5 excess cap copy never claims added capability, before AND after a successful save', async () => {
    mutateAsync.mockResolvedValue(validSnapshot({
      revision: REV_B,
      persisted_yaml: { queue_workers: 3, host_global_session_cap: 30 },
      next_start: { queue_workers: 3, host_global_session_cap: 30 },
      restart_pending: true,
    }));
    mount();
    await userEvent.clear(cap());
    await userEvent.type(cap(), '30');
    expect(document.body).toHaveTextContent(/Raising this limit alone does not add worker slots/);
    expect(document.body).not.toHaveTextContent(/more capacity|higher throughput/i);

    // R9 5.5: the required POST-SUCCESS behaviour — the honest copy survives
    // into the result panel and no capability claim appears there either.
    await fillAndSave('raise the cap');
    expect(await screen.findByText(/Saved for the next start/)).toBeVisible();
    expect(document.body).not.toHaveTextContent(/more capacity|higher throughput|additional capability|faster/i);
    expect(document.body).not.toHaveTextContent(/Applied|Apply now|Restart daemon/);
    // The pair settled at the saved values and the form is clean.
    expect(cap()).toHaveValue('30');
    expect(reasonBox()).toHaveValue('');
    expect(document.body).not.toHaveTextContent(/Unsaved changes/);
  });
});

// ---------------------------------------------------------------------------
describe('6 — absent keys, no-op semantics, rationale-only dirty', () => {
  test('6.1 absent keys read "Not explicitly saved" and fields seed from next_start', () => {
    loaded({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } });
    mount();
    const table = screen.getByRole('table');
    expect(within(table).getAllByText('Not explicitly saved')).toHaveLength(2);
    expect(workers()).toHaveValue('3');
    expect(cap()).toHaveValue('10');
    expect(document.body).not.toHaveTextContent(/default/i);
  });

  test('6.3 rationale-only dirty arms protection without a value-comparison panel', async () => {
    mount();
    // The consequence panel IS rendered for a pristine form, so its absence
    // after typing a reason is a real transition, not a never-present element.
    expect(document.body).toHaveTextContent(/Your entries match the saved values/);
    await userEvent.type(reasonBox(), 'just the reason');
    expect(document.body).toHaveTextContent(/values are unchanged from the saved file; only the reason differs/);
    // Accepted 6.3 (S3): NO value-comparison / consequence panel in this state.
    expect(document.body).not.toHaveTextContent(/Your entries match the saved values/);
    expect(document.body).not.toHaveTextContent(/Your edits differ from the saved values/);
    expect(screen.queryByText(/task slots \+/)).not.toBeInTheDocument();
    // No prediction about whether the revision will move.
    expect(document.body).not.toHaveTextContent(/revision will|new revision/i);
    expect(document.body).toHaveTextContent(/Unsaved changes/);
    expect(saveButton()).toBeEnabled();
  });

  test('6.4 the comparison is on (presence, value), never value alone — asserted on a MOUNTED transition', async () => {
    loaded({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } });
    const view = mount();
    // Absent keys seed the same digits a present 3/10 would, so identical
    // digits must NOT be read as identical state.
    expect(workers()).toHaveValue('3');
    expect(cap()).toHaveValue('10');
    expect(within(screen.getByRole('table')).getAllByText('Not explicitly saved')).toHaveLength(2);
    // Absent keys are still stageable with a reason (6.2 contrast).
    await userEvent.type(reasonBox(), 'stage both keys explicitly');
    expect(saveButton()).toBeEnabled();
    expect(document.body).not.toHaveTextContent(/no-op|nothing to save/i);
    // Staging absent keys to PRESENT at the same digits is a real change, so the
    // comparison panel appears and the form is genuinely dirty.
    expect(document.body).toHaveTextContent(/Your edits differ from the saved values/);
    expect(document.body).toHaveTextContent(/Unsaved changes/);

    // R9 6.4: the SAME component now sees keys PRESENT at identical digits.
    // R9 6.4: the SAME MOUNTED component now sees keys PRESENT at identical
    // digits, re-rendered by an ordinary keystroke rather than an unmount.
    loaded({ persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 } });
    await userEvent.type(reasonBox(), '!');

    // The digits are unchanged, but the presence reading is different: the
    // "Not explicitly saved" cells are gone and the values are shown instead.
    await waitFor(() => expect(
      within(screen.getByRole('table')).queryAllByText('Not explicitly saved'),
    ).toHaveLength(0));
    expect(workers()).toHaveValue('3');
    expect(cap()).toHaveValue('10');
    const savedCells = within(screen.getByRole('table')).getAllByRole('row')
      .slice(1)
      .map((row) => (row as HTMLTableRowElement).cells[2].textContent);
    expect(savedCells).toEqual(['3', '10']);
    expect(reasonBox()).toHaveValue('stage both keys explicitly!');
    // In THIS mount the accepted base was seeded while the keys were ABSENT, so
    // at identical digits the transition is still a genuine configuration
    // change (presence moves absent -> present) and the comparison panel
    // correctly reflects that.
    expect(document.body).toHaveTextContent(/Your edits differ from the saved values/);
    expect(document.body).toHaveTextContent(/Unsaved changes/);

    // CONTRAST — a base whose keys were ALREADY present at the same digits is
    // rationale-only. That is a distinct SERVER state (the file carries the
    // keys), so mount it explicitly rather than misrepresenting the same-mount
    // presence change as a no-op.
    view.unmount();
    loaded({ persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 } });
    mount();
    await userEvent.type(reasonBox(), 'only the reason');
    expect(document.body).toHaveTextContent(
      /values are unchanged from the saved file; only the reason differs/i,
    );
    expect(document.body).not.toHaveTextContent(/Your edits differ from the saved values/);
    expect(document.body).not.toHaveTextContent(/Your entries match the saved values/);
    expect(screen.queryByText(/task slots \+/)).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent(/Unsaved changes/);
    expect(saveButton()).toBeEnabled();
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
    expect(document.body).toHaveTextContent(/the update choices are unavailable/);
    expect(screen.queryByRole('button', { name: /use latest saved version/i })).not.toBeInTheDocument();
  });

  test('9.2 a 409 whose latest has a quoted numeric is unusable, and "9" never renders as a number', async () => {
    mutateAsync.mockRejectedValue(new ApiError(409, 'stale_revision', {
      latest: validSnapshot({ persisted_yaml: { queue_workers: '9', host_global_session_cap: 9 } }),
    }));
    mount();
    await fillAndSave();
    expect(await screen.findByText(/Latest saved values could not be read/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /use latest saved version/i })).not.toBeInTheDocument();
  });

  test.each([
    ['12.1 401', new ApiError(401, null, {}), /valid daemon access token to save these settings. No values were changed by this request/i],
    ['12.1 403', new ApiError(403, null, {}), /valid daemon access token to save these settings/i],
    ['12.2 422', new ApiError(422, null, {}), /whole number greater than zero/i],
    ['12.3 428', new ApiError(428, 'if_match_required', {}), /Refresh the saved settings before saving again/i],
    ['12.3 400', new ApiError(400, 'if_match_invalid', {}), /Refresh the saved settings before saving again/i],
    ['12.4 audit_failed', new ApiError(503, 'audit_failed', {}), /This request did not change the configuration/i],
    ['12.5 write absent', new ApiError(503, 'config_write_failed', {}), /This request did not save new values/i],
    ['12.5 write present', new ApiError(503, 'config_write_failed', { artifact_state: 'present' }), /temporary save file remains/i],
    ['12.5 write unknown', new ApiError(503, 'config_write_failed', { artifact_state: 'unknown' }), /could not determine whether a temporary save file remains/i],
    ['12.6 env confirm', new ApiError(409, 'environment_confirmation_required', {}), /Confirm the environment override/i],
    ['12.8 unknown code', new ApiError(503, 'something_new', {}), /HappyRanch could not confirm whether the save finished/i],
    ['11.1 network', new Error('socket hang up'), /HappyRanch could not confirm whether the save finished/i],
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
    await screen.findByText(/This request did not save new values/);
    expect(document.body).not.toHaveTextContent(/previous authoritative file remains in use/i);
  });

  test('11.1 a generic failure is never called "failed safely"', async () => {
    mutateAsync.mockRejectedValue(new ApiError(500, null, {}));
    mount();
    await fillAndSave();
    await screen.findByText(/HappyRanch could not confirm whether the save finished/);
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
    await screen.findByText(/This request did not save new values/);
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
    expect(await screen.findByText(/This value is too large for this page to handle exactly/i)).toBeInTheDocument();
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
    expect(await screen.findByText(/This value is too large for this page to handle exactly/i)).toBeInTheDocument();
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

  test('15.7 the EMPTY field is refused too — the ninth grammar input, which cannot be typed', async () => {
    mount();
    // `''` is the one 15.7 input `user.type` cannot produce; it is the CLEARED
    // field. Asserted separately so the grammar table is complete at 9 of 9.
    await userEvent.clear(workers());
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    // The blank field gets its OWN specific message — `capacityModel.ts:107`
    // separates `blank` from the malformed-grammar reason — so it is refused
    // with field-associated copy and is never coerced to 0 or NaN.
    expect(await screen.findByText(/Task session limit is required/i)).toBeInTheDocument();
    expect(workers()).toHaveValue('');
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  test('15.9 the refusal is presented as an editor limit, never a server maximum', async () => {
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '9007199254740993');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(saveButton());
    await screen.findByText(/This value is too large for this page to handle exactly/i);
    expect(document.body).toHaveTextContent(/limit of the page, not a HappyRanch limit/i);
    expect(document.body).not.toHaveTextContent(/maximum (allowed|value|supported)/i);
  });

  // 15.6 — every REQUIRED RAW TYPE rejection, on every consumed numeric slot.
  const NON_NUMERIC: [string, unknown][] = [
    ['a quoted numeric', '5'],
    ['a boolean', true],
    ['an array', []],
    ['an object', {}],
    ['a null', null],
  ];
  const CONSUMED_SLOT_PATCHES: [string, (bad: unknown) => Record<string, unknown>][] = [
    ['running_at_daemon_start.queue_workers', (b) => ({ running_at_daemon_start: { queue_workers: b, host_global_session_cap: 10 } })],
    ['running_at_daemon_start.host_global_session_cap', (b) => ({ running_at_daemon_start: { queue_workers: 3, host_global_session_cap: b } })],
    ['next_start.queue_workers', (b) => ({ next_start: { queue_workers: b, host_global_session_cap: 10 } })],
    ['next_start.host_global_session_cap', (b) => ({ next_start: { queue_workers: 3, host_global_session_cap: b } })],
    ['producer_envelope', (b) => ({ producer_envelope: b })],
    ['producer_components.task_workers', (b) => ({ producer_components: { task_workers: b, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1 } })],
    ['producer_components.thread_workers', (b) => ({ producer_components: { task_workers: 3, thread_workers: b, dream_workers: 1, wake_workers: 1, schedule_workers: 1 } })],
    ['producer_components.dream_workers', (b) => ({ producer_components: { task_workers: 3, thread_workers: 4, dream_workers: b, wake_workers: 1, schedule_workers: 1 } })],
    ['producer_components.wake_workers', (b) => ({ producer_components: { task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: b, schedule_workers: 1 } })],
    ['producer_components.schedule_workers', (b) => ({ producer_components: { task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: b } })],
  ];

  test('15.6 / 15.10 EVERY consumed non-nullable numeric slot rejects EVERY non-numeric raw type, and never renders a zero', () => {
    for (const [slotName, patch] of CONSUMED_SLOT_PATCHES) {
      for (const [typeName, bad] of NON_NUMERIC) {
        loaded(patch(bad));
        const view = mount();
        expect(
          document.body.textContent,
          `${slotName} = ${typeName}`,
        ).toMatch(/HappyRanch could not read the saved capacity settings/);
        expect(document.body.textContent, `${slotName} = ${typeName}`).not.toMatch(/\b0\b/);
        view.unmount();
      }
    }
  });

  test('15.10 the NULLABLE positions accept null, and 5.5 fractional values never render rounded', () => {
    // Only these three may be null.
    for (const patch of [
      { persisted_yaml: { queue_workers: null, host_global_session_cap: 10 } },
      { persisted_yaml: { queue_workers: 3, host_global_session_cap: null } },
      { effective_admission_cap: null, effective_admission_reason: 'HappyRanch cannot currently verify the overall supervised-session limit.' },
    ]) {
      loaded(patch);
      const view = mount();
      expect(screen.queryByText(/HappyRanch could not read the saved capacity settings/)).not.toBeInTheDocument();
      view.unmount();
    }
    // A fraction is not a safe integer: withheld, never shown as `5`.
    loaded({ next_start: { queue_workers: 5.5, host_global_session_cap: 10 } });
    const fractional = mount();
    expect(document.body.textContent).toMatch(/outside the range this editor can represent exactly/);
    expect(document.body).not.toHaveTextContent(/\b5\b/);
    fractional.unmount();
  });

  test('15.10 the DOMAIN and RELATIONAL invariants are enforced, not rendered', () => {
    // W and H strictly positive, in EVERY consumed position.
    for (const patch of [
      { next_start: { queue_workers: 0, host_global_session_cap: 10 } },
      { next_start: { queue_workers: -1, host_global_session_cap: 10 } },
      { next_start: { queue_workers: 3, host_global_session_cap: 0 } },
      { running_at_daemon_start: { queue_workers: -1, host_global_session_cap: 10 } },
      { running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 0 } },
      { running_at_daemon_start: { queue_workers: 3, host_global_session_cap: -1 } },
      { persisted_yaml: { queue_workers: 0, host_global_session_cap: 10 } },
      // Every producer component is nonnegative — each has its own case.
      { producer_components: { task_workers: -1, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1 } },
      { producer_components: { task_workers: 3, thread_workers: -1, dream_workers: 1, wake_workers: 1, schedule_workers: 1 } },
      { producer_components: { task_workers: 3, thread_workers: 4, dream_workers: -1, wake_workers: 1, schedule_workers: 1 } },
      { producer_components: { task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: -1, schedule_workers: 1 } },
      { producer_components: { task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: -1 } },
      { producer_envelope: -1 },
    ]) {
      loaded(patch);
      const view = mount();
      expect(document.body.textContent, JSON.stringify(patch))
        .toMatch(/HappyRanch could not read the saved capacity settings/);
      view.unmount();
    }
    // Relational: task_workers <= producer_envelope. A violation is
    // INCONSISTENT (case 5.2), a distinct outcome from unusable.
    loaded({
      producer_envelope: 10,
      producer_components: { task_workers: 12, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0 },
    });
    mount();
    expect(document.body.textContent).toMatch(/HappyRanch returned conflicting capacity values/);
    expect(document.body).not.toHaveTextContent(/-2/);
  });

  test('15.10 a null in a non-nullable position is unusable, never a zero', () => {
    loaded({ producer_envelope: null });
    mount();
    expect(screen.getByRole('alert')).toHaveTextContent(/HappyRanch could not read the saved capacity settings/);
    expect(document.body).not.toHaveTextContent(/envelope 0/);
  });

  test('15.6 / 15.10 the NULLABLE positions reject every remaining non-numeric type and invalid domain', () => {
    const NULLABLE: [string, (bad: unknown) => Record<string, unknown>][] = [
      ['persisted_yaml.queue_workers', (b) => ({ persisted_yaml: { queue_workers: b, host_global_session_cap: 10 } })],
      ['persisted_yaml.host_global_session_cap', (b) => ({ persisted_yaml: { queue_workers: 3, host_global_session_cap: b } })],
      ['effective_admission_cap', (b) => ({ effective_admission_cap: b, effective_admission_reason: 'x' })],
    ];
    for (const [slotName, patch] of NULLABLE) {
      for (const [typeName, bad] of [
        ['a quoted numeric', '5'],
        ['a boolean', true],
        ['an array', []],
        ['an object', {}],
      ] as [string, unknown][]) {
        loaded(patch(bad));
        const view = mount();
        expect(document.body.textContent, `${slotName} = ${typeName}`)
          .toMatch(/HappyRanch could not read the saved capacity settings/);
        expect(document.body.textContent, `${slotName} = ${typeName}`).not.toMatch(/\b0\b/);
        view.unmount();
      }
    }
    // Domain: a persisted W/H of zero or negative is unusable (positive domain).
    for (const patch of [
      { persisted_yaml: { queue_workers: 0, host_global_session_cap: 10 } },
      { persisted_yaml: { queue_workers: 3, host_global_session_cap: -1 } },
    ]) {
      loaded(patch);
      const view = mount();
      expect(document.body.textContent, JSON.stringify(patch))
        .toMatch(/HappyRanch could not read the saved capacity settings/);
      view.unmount();
    }
    // A FRACTION is not a safe integer: withheld, and 5.5 never renders as 5 —
    // in a nullable slot as well as a non-nullable one.
    for (const patch of [
      { persisted_yaml: { queue_workers: 5.5, host_global_session_cap: 10 } },
      { persisted_yaml: { queue_workers: 3, host_global_session_cap: 5.5 } },
      { effective_admission_cap: 5.5, effective_admission_reason: 'x' },
    ]) {
      loaded(patch);
      const view = mount();
      expect(document.body.textContent, JSON.stringify(patch))
        .toMatch(/outside the range this editor can represent exactly/);
      expect(document.body, JSON.stringify(patch)).not.toHaveTextContent(/\b5\b/);
      view.unmount();
    }
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
    const status = await screen.findByText(/Saved for the next start/);
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

  test('16.6 the accepted TAB ORDER is walked, and every action is a real keyboard-operable control', async () => {
    loaded({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'An environment setting takes priority over the saved configuration. Restarting HappyRanch will not make the saved value take effect.',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    });
    mount();
    for (const name of [/Save for next start/, /Discard draft/, /Refresh capacity values/]) {
      expect(screen.getByRole('button', { name })).toBeInstanceOf(HTMLButtonElement);
    }
    expect(screen.getByText('Capacity details').tagName).toBe('SUMMARY');

    // Accepted 16.6 order: W -> H -> reason -> acknowledgment -> Save ->
    // Discard -> Refresh -> Capacity details.
    //
    // The acknowledgment is given first: a DISABLED Save is not focusable, so
    // walking the order with it disabled would silently skip a step.
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox'));
    expect(saveButton()).toBeEnabled();
    workers().focus();
    const expected: HTMLElement[] = [
      cap(),
      reasonBox(),
      screen.getByRole('checkbox'),
      saveButton(),
      screen.getByRole('button', { name: /Discard draft/ }),
      screen.getByRole('button', { name: /Refresh capacity values/ }),
    ];
    for (const next of expected) {
      await user.tab();
      expect(document.activeElement).toBe(next);
    }
    // The disclosure is a NATIVE <summary>, which is keyboard-focusable and
    // Enter/Space-operable in a real browser. jsdom does not implement
    // <details>/<summary> focus at all, so the LAST step of the walk and the
    // activation gesture are proven in the browser harness (16.11), not here.
    // This is a stated venue boundary, not a skipped requirement.
    expect(screen.getByText('Capacity details').tagName).toBe('SUMMARY');
  });

  test('16.6b the reconciliation controls are real keyboard-operable buttons too', async () => {
    mutateAsync.mockRejectedValue(new ApiError(409, 'stale_revision', {
      code: 'stale_revision',
      latest: validSnapshot({ revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 } }),
    }));
    mount();
    await userEvent.clear(workers());
    await userEvent.type(workers(), '5');
    await fillAndSave('why');
    await screen.findByText('The saved settings changed elsewhere. Your edits are still here.');

    for (const name of ['Keep my edits and use latest saved version', 'Discard my edits and use latest saved version']) {
      expect(screen.getByRole('button', { name })).toBeInstanceOf(HTMLButtonElement);
    }
    // Operate one by keyboard only.
    screen.getByRole('button', { name: 'Keep my edits and use latest saved version' }).focus();
    await userEvent.keyboard('{Enter}');
    await waitFor(() => expect(
      screen.queryByRole('button', { name: 'Keep my edits and use latest saved version' }),
    ).not.toBeInTheDocument());
  });

  test('16.8 important warnings are VISIBLE with the details disclosure collapsed', () => {
    loaded({
      restart_pending: true,
      warnings: ['The overall session limit (3) is lower than the total worker slots (4). Under high demand, some sessions may wait. You can still save this setting.'],
      environment_shadowed: ['queue_workers'],
      environment_warning: 'An environment setting takes priority over the saved configuration. Restarting HappyRanch will not make the saved value take effect.',
    });
    mount();
    expect(screen.getByText('The overall session limit (3) is lower than the total worker slots (4). Under high demand, some sessions may wait. You can still save this setting.')).toBeVisible();
    expect(screen.getByText(/An environment setting takes priority/)).toBeVisible();
    expect(screen.getByText(/A saved value differs from the value in effect now/)).toBeVisible();
    // The disclosure is collapsed, so its contents are not what carries them.
    expect(document.querySelector('details')?.open).toBeFalsy();
  });

  test('16.9 warnings carry text, never colour alone', () => {
    loaded({ warnings: ['The overall session limit (3) is lower than the total worker slots (4). Under high demand, some sessions may wait. You can still save this setting.'] });
    mount();
    const warning = screen.getByText('The overall session limit (3) is lower than the total worker slots (4). Under high demand, some sessions may wait. You can still save this setting.');
    expect((warning.textContent ?? '').trim().length).toBeGreaterThan(10);
  });

  test('16.9b known and UNKNOWN/FUTURE server warnings are rendered verbatim (no prose parsing)', () => {
    const known = 'The overall session limit (3) is lower than the total worker slots (4). Under high demand, some sessions may wait. You can still save this setting.';
    const future = 'Future capacity notice: reservoir pressure is nominal.';
    loaded({ warnings: [known, future] });
    mount();
    // A known structured relation is shown exactly as the daemon supplied it.
    expect(screen.getByText(known)).toBeVisible();
    // An unrecognized/future warning is neither silently hidden nor rewritten.
    expect(screen.getByText(future)).toBeVisible();
    expect(document.body).toHaveTextContent(future);
  });

  test('16.9c known capability variants and an unknown/future reason are shown verbatim', () => {
    loaded({
      effective_admission_cap: null,
      effective_admission_reason: 'HappyRanch cannot currently verify the overall supervised-session limit.',
    });
    const nullCap = mount();
    expect(document.body).toHaveTextContent(
      'HappyRanch cannot currently verify the overall supervised-session limit.',
    );
    nullCap.unmount();

    loaded({
      effective_admission_cap: 4,
      effective_admission_reason: 'The active execution backend cannot enforce every host-safety check, so HappyRanch is using a lower session limit.',
    });
    const fallback = mount();
    expect(document.body).toHaveTextContent(
      'The active execution backend cannot enforce every host-safety check, so HappyRanch is using a lower session limit.',
    );
    fallback.unmount();

    loaded({ effective_admission_cap: 4, effective_admission_reason: 'Some future capability reason.' });
    mount();
    expect(document.body).toHaveTextContent('Some future capability reason.');
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
    expect(screen.getByText(/If you leave or reload, these edits will be lost/i)).toBeInTheDocument();
  });

  test('17.5 a clean form installs no warning', () => {
    mount();
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(screen.queryByText(/If you leave or reload, these edits will be lost/i)).not.toBeInTheDocument();
  });

  test('17.7 the guarded component mounts and renders normally with no navigation involved', () => {
    mount();
    expect(screen.getByRole('heading', { name: 'Capacity' })).toBeInTheDocument();
    expect(saveButton()).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// C3-N — own-settlement ordering branches whose timing the mounted venue
// cannot pin: a real browser can deliver a render between the provider settling
// this editor's write and the submit continuation, in either order. The hook
// is driven through a store so the SAME mounted component re-renders with an
// exact observation, and `settlementOf` reports the provider's record.
// ---------------------------------------------------------------------------
describe('C3-N — own settlement vs. other observations (ordering branches)', () => {
  const REV_X = `sha256:${'9'.repeat(64)}`;
  const pairSnapshot = (w: number, h: number, revision: string) => validSnapshot({
    revision,
    persisted_yaml: { queue_workers: w, host_global_session_cap: h },
    next_start: { queue_workers: w, host_global_session_cap: h },
    restart_pending: true,
  });

  function drivenVenue() {
    let value: Record<string, unknown> = {
      data: validSnapshot(), isLoading: false, isError: false, error: null,
      refetch: vi.fn(), isFetching: false,
      observation: { issuedSeq: 1, settledSeq: 2, origin: 'read', outcome: 'usable', receiptAt: 1, sourceRevision: REV_A },
    };
    const listeners = new Set<() => void>();
    hooks.query.mockImplementation(() => useSyncExternalStore(
      (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
      () => value,
    ));
    let settlement: { settledSeq: number; outcome: 'usable' | 'unusable' } | null = null;
    let gate!: (value: unknown) => void;
    mutateAsync.mockImplementation(() => new Promise((resolve) => { gate = resolve; }));
    hooks.mutation.mockReturnValue({ mutateAsync, isPending: false, settlementOf: () => settlement });
    return {
      /** Publish one provider observation with its snapshot. */
      observe(snapshot: Record<string, unknown>, observation: Record<string, unknown>) {
        act(() => {
          value = { ...value, data: snapshot, observation };
          listeners.forEach((listener) => listener());
        });
      },
      settleOwn(settledSeq: number) { settlement = { settledSeq, outcome: 'usable' }; },
      async respond(result: unknown) { await act(async () => { gate(result); }); },
    };
  }

  test('an observation that settled AFTER the own write, recorded while it was pending, is ADOPTED when the write is accepted — not fenced as obsolete', async () => {
    const venue = drivenVenue();
    mount();
    const user = userEvent.setup();
    await user.clear(workers());
    await user.type(workers(), '5');
    await user.clear(cap());
    await user.type(cap(), '12');
    await fillAndSave('own');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));

    // The provider settles THIS request at seq 8; before the continuation runs,
    // a render delivers another write that settled LATER (seq 10) at 9/9.
    venue.settleOwn(8);
    venue.observe(pairSnapshot(9, 9, REV_X),
      { issuedSeq: 10, settledSeq: 10, origin: 'write', outcome: 'usable', receiptAt: 2, sourceRevision: REV_X });
    await screen.findByText('Configuration changed elsewhere.');

    await venue.respond(pairSnapshot(5, 12, REV_B));
    await screen.findByText(/^Saved for the next start/);
    await waitFor(() => expect(workers()).toHaveValue('9'));
    expect(cap()).toHaveValue('9');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();

    // Its next deliberate save is built on the newer, adopted revision.
    await user.clear(workers());
    await user.type(workers(), '4');
    await fillAndSave('after newer');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync.mock.calls[1][0]).toMatchObject({ revision: REV_X, queue_workers: 4, host_global_session_cap: 9 });
  });

  test('a late render of an observation that settled BEFORE the accepted own write never re-arms the clean saved state', async () => {
    const venue = drivenVenue();
    mount();
    const user = userEvent.setup();
    await user.clear(workers());
    await user.type(workers(), '5');
    await user.clear(cap());
    await user.type(cap(), '12');
    await fillAndSave('own');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));

    // The own write settled at seq 8; a stale render still carries seq 6.
    venue.settleOwn(8);
    venue.observe(pairSnapshot(9, 9, REV_X),
      { issuedSeq: 6, settledSeq: 6, origin: 'write', outcome: 'usable', receiptAt: 2, sourceRevision: REV_X });
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();

    await venue.respond(pairSnapshot(5, 12, REV_B));
    await screen.findByText(/^Saved for the next start/);
    // Then the own settlement itself renders: still no phantom.
    venue.observe(pairSnapshot(5, 12, REV_B),
      { issuedSeq: 8, settledSeq: 8, origin: 'write', outcome: 'usable', receiptAt: 3, sourceRevision: REV_B });
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    await fillAndSave('next');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync.mock.calls[1][0]).toMatchObject({ revision: REV_B });
  });

  test('the own settlement rendered BEFORE the continuation is not an external change, while a different settlement of the SAME revision is', async () => {
    const venue = drivenVenue();
    mount();
    const user = userEvent.setup();
    await user.clear(workers());
    await user.type(workers(), '5');
    await user.clear(cap());
    await user.type(cap(), '12');
    await fillAndSave('own');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));

    venue.settleOwn(8);
    venue.observe(pairSnapshot(5, 12, REV_B),
      { issuedSeq: 8, settledSeq: 8, origin: 'write', outcome: 'usable', receiptAt: 2, sourceRevision: REV_B });
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    await venue.respond(pairSnapshot(5, 12, REV_B));
    await screen.findByText(/^Saved for the next start/);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();

    // Another editor moves it to 7/14, then restores 5/12 — the same revision
    // bytes at a DIFFERENT settlement. This clean editor adopts both.
    venue.observe(pairSnapshot(7, 14, REV_C),
      { issuedSeq: 11, settledSeq: 11, origin: 'write', outcome: 'usable', receiptAt: 3, sourceRevision: REV_C });
    await waitFor(() => expect(workers()).toHaveValue('7'));
    venue.observe(pairSnapshot(5, 12, REV_B),
      { issuedSeq: 13, settledSeq: 13, origin: 'write', outcome: 'usable', receiptAt: 4, sourceRevision: REV_B });
    await waitFor(() => expect(workers()).toHaveValue('5'));
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    await fillAndSave('after restore');
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync.mock.calls[1][0]).toMatchObject({ revision: REV_B, queue_workers: 5 });
  });
});
