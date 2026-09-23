import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import {
  AUTHORITY_POLICY_V2_STARTER,
  type V2PairedControlRequest,
} from '@/hooks/authorityPolicy';
import { TeamEscalationPolicyCard } from './TeamEscalationPolicyCard';

const legacyTemplate = {
  title: 'Canonical legacy policy',
  normative_text: 'Normative text',
  clauses: [{ id: 'esc-one', category: 'protected', condition: 'Stop.', action: 'escalate_to_founder' as const }],
  continuation_phrase: 'routine same-root follow-through of the already-completed slice',
};
const EMPTY_SELECTOR_ID = `APS-${'a'.repeat(64)}`;
const ACTIVE_SELECTOR_ID = `APS-${'b'.repeat(64)}`;
const NEXT_SELECTOR_ID = `APS-${'c'.repeat(64)}`;
const RELEASE_DIGEST = 'd'.repeat(64);
const RELEASE_ID = `APV2-${RELEASE_DIGEST}`;
const ACTIVATION_DIGEST = 'e'.repeat(64);
const ACTIVATION_ID = `APV2A-${ACTIVATION_DIGEST}`;

const empty = {
  team: 'engineering' as const, target_manager: 'engineering_manager' as const,
  can_mutate: true as const, bootstrap_required: true as const,
  family: 'empty' as const, selector_id: EMPTY_SELECTOR_ID, selector_epoch: 0 as const,
  bootstrap_template: legacyTemplate,
};
const legacyActive = {
  team: 'engineering' as const, target_manager: 'engineering_manager' as const,
  can_mutate: true as const,
  family: 'legacy_v1' as const, contract_version: 'v1' as const,
  selector_id: ACTIVE_SELECTOR_ID, selector_epoch: 3,
  bootstrap_template: legacyTemplate,
  active: {
    family: 'legacy_v1' as const,
    activation_id: 'APA-active', epoch: 7, action: 'activate' as const,
    created_at: '2026-09-02T00:00:00Z', actor_attribution: 'shared local operator credential' as const,
    release: {
      id: 'APR-active', policy_id: 'engineering/pre-escalation-authority', version: 3,
      ...legacyTemplate, digest: '1'.repeat(64), created_at: '2026-09-02T00:00:00Z',
      actor_attribution: 'shared local operator credential' as const,
    },
  },
};

function activeV2(overrides: Partial<{ selectorId: string; selectorEpoch: number; title: string;
  whatTo: string; whatNot: string; version: number }> = {}) {
  const selectorId = overrides.selectorId ?? NEXT_SELECTOR_ID;
  const selectorEpoch = overrides.selectorEpoch ?? 4;
  return {
    team: 'engineering' as const, target_manager: 'engineering_manager' as const,
    can_mutate: true as const,
    family: 'v2' as const, contract_version: 'v2' as const,
    selector_id: selectorId, selector_epoch: selectorEpoch,
    bootstrap_template: legacyTemplate,
    active: {
      family: 'v2' as const,
      activation_id: ACTIVATION_ID, selector_epoch: selectorEpoch, action: 'activate' as const,
      created_at: '2026-09-03T00:00:00Z', actor_attribution: 'shared local operator credential' as const,
      release: {
        id: RELEASE_ID, policy_id: AUTHORITY_POLICY_V2_STARTER.policy_id,
        version: overrides.version ?? 2, title: overrides.title ?? AUTHORITY_POLICY_V2_STARTER.title,
        what_to_escalate: overrides.whatTo ?? 'Escalate scope changes.',
        what_not_to_escalate: overrides.whatNot ?? 'Continue ordinary work.',
        digest: RELEASE_DIGEST, actor_attribution: 'shared local operator credential' as const,
      },
    },
  };
}

const query = {
  data: undefined as unknown,
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};
const legacyCreate = { mutateAsync: vi.fn(), isPending: false };
const legacyActivate = { mutateAsync: vi.fn(), isPending: false };
const v2Create = { mutateAsync: vi.fn(), isPending: false };
const legacyHistory = { data: { pages: [{ items: [] as Array<Record<string, unknown>>, next_cursor: null as string | null }] }, isLoading: false, isError: false, error: null, fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false };
const v2History = { data: { pages: [{ items: [] as Array<Record<string, unknown>>, next_cursor: null as string | null }] }, isLoading: false, isError: false, error: null, fetchNextPage: vi.fn(), refetch: vi.fn(), hasNextPage: false, isFetchingNextPage: false };
const outcomes = { data: { pages: [{ items: [] as Array<Record<string, unknown>>, next_cursor: null as string | null }] }, isLoading: false, isError: false, error: null, fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false };

vi.mock('@/hooks/authorityPolicy', async () => ({
  ...(await vi.importActual<typeof import('@/hooks/authorityPolicy')>('@/hooks/authorityPolicy')),
  authorityPolicyActiveEpoch: (active: { family: string; epoch?: number; selector_epoch?: number }) =>
    (active.family === 'legacy_v1' ? active.epoch : active.selector_epoch),
  useTeamEscalationPolicy: () => query,
  useCreateTeamEscalationPolicyRelease: () => legacyCreate,
  useActivateTeamEscalationPolicyRelease: () => legacyActivate,
  useCreateTeamEscalationPolicyV2Release: () => v2Create,
  useTeamEscalationPolicyHistory: () => legacyHistory,
  useTeamEscalationPolicyV2History: () => v2History,
  useTeamEscalationPolicyOutcomes: () => outcomes,
}));

const agent = { name: 'engineering_manager', team: 'engineering', role: 'manager' };

function controlResponse(request: V2PairedControlRequest) {
  return {
    control: 'v2_create_activate' as const,
    family: 'v2' as const,
    contract_version: 'v2' as const,
    selector_id: NEXT_SELECTOR_ID,
    selector_epoch: 1,
    previous_selector_id: request.expected_selector_id ?? EMPTY_SELECTOR_ID,
    receipt: {
      team: 'engineering' as const,
      kind: 'v2_create_activate' as const,
      create_request_id: request.create_request_id,
      create_request_digest: '2'.repeat(64),
      activation_request_id: request.activation_request_id,
      activation_request_digest: '3'.repeat(64),
      release_id: RELEASE_ID,
      policy_digest: RELEASE_DIGEST,
      release_version: 1,
      activation_id: ACTIVATION_ID,
      activation_digest: ACTIVATION_DIGEST,
      selector_id: NEXT_SELECTOR_ID,
      selector_epoch: 1,
      action: request.action,
      previous_selector_id: request.expected_selector_id ?? EMPTY_SELECTOR_ID,
      created_at: '2026-09-03T00:00:00Z',
    },
  };
}

async function editPairAndOpenConfirmation(whatTo = 'Escalate edited scope.', whatNot = 'Continue edited work.') {
  fireEvent.change(await screen.findByLabelText('What to escalate'), { target: { value: whatTo } });
  fireEvent.change(screen.getByLabelText('What not to escalate'), { target: { value: whatNot } });
  fireEvent.click(screen.getByRole('button', { name: 'Save & activate' }));
  return screen.findByRole('dialog', { name: 'Save and activate both policy texts?' });
}

describe('TeamEscalationPolicyCard v2 editor', () => {
  beforeEach(() => {
    query.data = empty; query.isLoading = false; query.isError = false; query.error = null;
    query.refetch.mockReset(); query.refetch.mockImplementation(async () => ({ data: query.data }));
    legacyCreate.mutateAsync.mockReset(); legacyActivate.mutateAsync.mockReset();
    v2Create.mutateAsync.mockReset();
    for (const stream of [legacyHistory, v2History, outcomes]) {
      stream.data.pages = [{ items: [], next_cursor: null }];
      stream.isLoading = false; stream.isError = false; stream.hasNextPage = false;
      stream.isFetchingNextPage = false; stream.fetchNextPage.mockReset();
    }
    v2History.refetch.mockReset();
  });

  it('uses the exact approved starter pair only for an empty selector and exposes exactly two editable textareas', async () => {
    render(<TeamEscalationPolicyCard agent={agent} />);
    const textareas = await screen.findAllByRole('textbox');
    expect(textareas).toHaveLength(2);
    expect(screen.getByLabelText('What to escalate')).toHaveValue(AUTHORITY_POLICY_V2_STARTER.what_to_escalate);
    expect(screen.getByLabelText('What not to escalate')).toHaveValue(AUTHORITY_POLICY_V2_STARTER.what_not_to_escalate);
    expect(screen.getByText(AUTHORITY_POLICY_V2_STARTER.title)).toBeInTheDocument();
    expect(screen.queryByText('Normative policy')).not.toBeInTheDocument();
    expect(screen.queryByText('Canonical continuation phrase')).not.toBeInTheDocument();
    expect(screen.queryByText('esc-one')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save & activate' })).toBeEnabled();
  });

  it('initializes active v2 bytes and truthfully shows release, activation, selector and digest identity', async () => {
    query.data = activeV2();
    render(<TeamEscalationPolicyCard agent={agent} />);
    expect(await screen.findByLabelText('What to escalate')).toHaveValue('Escalate scope changes.');
    expect(screen.getByLabelText('What not to escalate')).toHaveValue('Continue ordinary work.');
    const identity = screen.getByText(/Active release v2/);
    expect(identity).toHaveTextContent(RELEASE_ID);
    expect(identity).toHaveTextContent(ACTIVATION_ID);
    expect(identity).toHaveTextContent(NEXT_SELECTOR_ID);
    expect(identity).toHaveTextContent(RELEASE_DIGEST);
  });

  it('uses one paired request for active-v1 transition and never invokes either legacy mutation', async () => {
    query.data = legacyActive;
    v2Create.mutateAsync.mockImplementation(async ({ body }) => controlResponse(body));
    const readback = activeV2({ selectorEpoch: 1, whatTo: '  Escalate exact bytes.  ', whatNot: '  Continue exact bytes.  ', version: 1 });
    query.refetch.mockImplementation(async () => { query.data = readback; return { data: readback }; });
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation('  Escalate exact bytes.  ', '  Continue exact bytes.  ');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    await waitFor(() => expect(v2Create.mutateAsync).toHaveBeenCalledOnce());
    const request = v2Create.mutateAsync.mock.calls[0][0].body as V2PairedControlRequest;
    expect(request).toMatchObject({
      team: 'engineering', policy_id: AUTHORITY_POLICY_V2_STARTER.policy_id,
      title: AUTHORITY_POLICY_V2_STARTER.title,
      based_on_selector_id: ACTIVE_SELECTOR_ID,
      expected_selector_id: ACTIVE_SELECTOR_ID,
      action: 'activate',
      what_to_escalate: '  Escalate exact bytes.  ',
      what_not_to_escalate: '  Continue exact bytes.  ',
      acknowledge_shared_credential_attribution: true,
    });
    expect(request.create_request_id).not.toBe(request.activation_request_id);
    expect(legacyCreate.mutateAsync).not.toHaveBeenCalled();
    expect(legacyActivate.mutateAsync).not.toHaveBeenCalled();
  });

  it('uses literal null selector bases and bootstrap only for genuinely empty projection', async () => {
    v2Create.mutateAsync.mockImplementation(async ({ body }) => controlResponse(body));
    const readback = activeV2({ selectorEpoch: 1, whatTo: 'Escalate edited scope.', whatNot: 'Continue edited work.', version: 1 });
    query.refetch.mockImplementation(async () => { query.data = readback; return { data: readback }; });
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    await waitFor(() => expect(v2Create.mutateAsync).toHaveBeenCalledOnce());
    expect(v2Create.mutateAsync.mock.calls[0][0].body).toMatchObject({
      based_on_selector_id: null, expected_selector_id: null, action: 'bootstrap',
    });
  });

  it('requires both raw text values and applies scalar bounds without trimming accepted bytes', async () => {
    render(<TeamEscalationPolicyCard agent={agent} />);
    const whatTo = await screen.findByLabelText('What to escalate');
    fireEvent.change(whatTo, { target: { value: '   ' } });
    expect(screen.getByRole('status')).toHaveTextContent('What to escalate is required.');
    expect(screen.getByRole('button', { name: 'Save & activate' })).toBeDisabled();
    fireEvent.change(whatTo, { target: { value: 'x'.repeat(20_001) } });
    expect(screen.getByRole('status')).toHaveTextContent('What to escalate must be at most 20000 characters.');
    expect(v2Create.mutateAsync).not.toHaveBeenCalled();
  });

  it('preserves an unsaved paired draft across ordinary query rerenders and warns for navigation/reload', async () => {
    const onDirtyChange = vi.fn();
    const view = render(<TeamEscalationPolicyCard agent={agent} onDirtyChange={onDirtyChange} />);
    fireEvent.change(await screen.findByLabelText('What to escalate'), { target: { value: 'Unsaved escalation bytes' } });
    fireEvent.change(screen.getByLabelText('What not to escalate'), { target: { value: 'Unsaved continuation bytes' } });
    query.data = { ...empty };
    view.rerender(<TeamEscalationPolicyCard agent={agent} onDirtyChange={onDirtyChange} />);
    expect(screen.getByLabelText('What to escalate')).toHaveValue('Unsaved escalation bytes');
    expect(screen.getByLabelText('What not to escalate')).toHaveValue('Unsaved continuation bytes');
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('requires confirmation and local pending state prevents a duplicate submit', async () => {
    let finish!: (value: ReturnType<typeof controlResponse>) => void;
    v2Create.mutateAsync.mockImplementation(({ body }) => new Promise((resolve) => {
      finish = () => resolve(controlResponse(body));
    }));
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation();
    const confirm = screen.getByRole('button', { name: 'Confirm save & activate' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(v2Create.mutateAsync).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Saving & activating…' })).toBeDisabled();
    await act(async () => {
      finish(controlResponse(v2Create.mutateAsync.mock.calls[0][0].body));
    });
  });

  it('reports success only after the paired receipt and refetched active readback agree', async () => {
    v2Create.mutateAsync.mockImplementation(async ({ body }) => controlResponse(body));
    const readback = activeV2({ selectorEpoch: 1, whatTo: 'Escalate edited scope.', whatNot: 'Continue edited work.', version: 1 });
    query.refetch.mockImplementation(async () => { query.data = readback; return { data: readback }; });
    const onDirtyChange = vi.fn();
    render(<TeamEscalationPolicyCard agent={agent} onDirtyChange={onDirtyChange} />);
    await editPairAndOpenConfirmation();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Saved and activated immutable v2 release');
    expect(status).toHaveTextContent(RELEASE_ID);
    expect(status).toHaveTextContent(NEXT_SELECTOR_ID);
    expect(query.refetch).toHaveBeenCalledOnce();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it('preserves the draft on stale selector and refetches/rebases only after deliberate user choice', async () => {
    query.data = legacyActive;
    v2Create.mutateAsync.mockRejectedValueOnce(new ApiError(409, 'selector_conflict', {}));
    const current = activeV2({ selectorId: NEXT_SELECTOR_ID, selectorEpoch: 8 });
    query.refetch.mockImplementation(async () => ({ data: current }));
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation('Unsaved escalation bytes', 'Unsaved continuation bytes');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    expect(await screen.findByRole('status')).toHaveTextContent('active selector changed');
    expect(query.refetch).not.toHaveBeenCalled();
    expect(screen.getByLabelText('What to escalate')).toHaveValue('Unsaved escalation bytes');
    fireEvent.click(screen.getByRole('button', { name: 'Review current selector' }));
    await waitFor(() => expect(query.refetch).toHaveBeenCalledOnce());
    expect(screen.getByLabelText('What to escalate')).toHaveValue('Unsaved escalation bytes');
    expect(screen.getByRole('status')).toHaveTextContent('Your draft is preserved');
  });

  it.each([
    [new ApiError(422, 'invalid_policy_request', { secret: 'must-not-render' }), 'The server rejected the paired policy contract.'],
    [new ApiError(500, 'policy_store_unavailable', { secret: 'must-not-render' }), 'The save result is unknown.'],
    [new Error('network leaked detail'), 'The save result is unknown.'],
  ])('uses bounded sanitized failure copy and no local success fiction', async (error, copy) => {
    v2Create.mutateAsync.mockRejectedValueOnce(error);
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    expect(await screen.findByRole('status')).toHaveTextContent(copy);
    expect(screen.getByRole('status')).not.toHaveTextContent(/must-not-render|network leaked detail/);
    expect(screen.queryByText(/Saved and activated immutable/)).not.toBeInTheDocument();
  });

  it('retries an ambiguous network outcome with the exact paired request and accepts the same receipt', async () => {
    v2Create.mutateAsync.mockRejectedValueOnce(new Error('network')).mockImplementationOnce(async ({ body }) => controlResponse(body));
    const readback = activeV2({ selectorEpoch: 1, whatTo: 'Escalate edited scope.', whatNot: 'Continue edited work.', version: 1 });
    query.refetch.mockImplementation(async () => ({ data: readback }));
    render(<TeamEscalationPolicyCard agent={agent} />);
    await editPairAndOpenConfirmation();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm save & activate' }));
    const retry = await screen.findByRole('button', { name: 'Retry exact save & activate' });
    fireEvent.click(retry);
    await waitFor(() => expect(v2Create.mutateAsync).toHaveBeenCalledTimes(2));
    expect(v2Create.mutateAsync.mock.calls[1][0]).toEqual(v2Create.mutateAsync.mock.calls[0][0]);
    expect(await screen.findByRole('status')).toHaveTextContent('Saved and activated immutable v2 release');
  });

  it('renders immutable full v2 history and retries its cursor independently without editing or loss', async () => {
    query.data = activeV2();
    v2History.data.pages = [{ items: [{
      family: 'v2', contract_version: 'v2', release_id: RELEASE_ID,
      policy_id: 'engineering-dual-text', version: 2, title: 'Dual text',
      what_to_escalate: 'Escalate history.', what_not_to_escalate: 'Continue history.',
      contract_digest: '4'.repeat(64), policy_digest: RELEASE_DIGEST,
      release_created_at: '2026-09-03T00:00:00Z', actor_attribution: 'shared local operator credential',
      activation: { id: ACTIVATION_ID, selector_epoch: 4, action: 'activate',
        digest: ACTIVATION_DIGEST, created_at: '2026-09-03T00:01:00Z' },
    }], next_cursor: 'v2-next' }];
    v2History.hasNextPage = true; v2History.isError = true;
    legacyHistory.data.pages[0].items = [{ release_id: 'APR-old', policy_id: 'legacy', version: 1,
      policy_digest: '1'.repeat(64), release_created_at: '2026-09-01',
      actor_attribution: 'shared local operator credential', activation: null }];
    render(<TeamEscalationPolicyCard agent={agent} />);
    expect(await screen.findByText('Escalate history.')).toBeInTheDocument();
    expect(screen.getByText('Continue history.')).toBeInTheDocument();
    expect(screen.getByText(/contract 444444444444/)).toBeInTheDocument();
    expect(screen.getAllByText(/selector epoch 4/)).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Reactivate/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/APR-old/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading dual-text history' }));
    expect(v2History.fetchNextPage).toHaveBeenCalledOnce();
    expect(legacyHistory.fetchNextPage).not.toHaveBeenCalled();
    expect(screen.getByText('Escalate history.')).toBeInTheDocument();
  });

  it('retries an initial v2 history error independently from the current projection', async () => {
    v2History.data.pages = [{ items: [], next_cursor: null }];
    v2History.isError = true;
    render(<TeamEscalationPolicyCard agent={agent} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry dual-text history' }));
    expect(v2History.refetch).toHaveBeenCalledOnce();
    expect(v2History.fetchNextPage).not.toHaveBeenCalled();
    expect(query.refetch).not.toHaveBeenCalled();
  });

  it('retries a projection load error on the same mount without exposing an editor', async () => {
    query.data = undefined; query.isError = true;
    const view = render(<TeamEscalationPolicyCard agent={agent} />);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    query.refetch.mockImplementationOnce(async () => {
      query.isError = false; query.isLoading = true;
      view.rerender(<TeamEscalationPolicyCard agent={agent} />);
      return { data: undefined };
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(query.refetch).toHaveBeenCalledOnce();
    expect(screen.getByText('Loading team policy…')).toBeInTheDocument();
  });
});
