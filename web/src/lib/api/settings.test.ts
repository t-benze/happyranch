import { beforeEach, describe, expect, expectTypeOf, it, test, vi } from 'vitest';
import { putDaemonCapacity, type RuntimeRegistrationTokenMintRequest } from './settings';
import type { OrgSettings } from './types';

vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';

describe('RuntimeRegistrationTokenMintRequest', () => {
  it('accepts the optional Slice-1A direct-authority workspace adapter', () => {
    const value: RuntimeRegistrationTokenMintRequest = {
      name: 'custom-cli', purpose: 'adapter', intended_profile_name: 'custom-profile',
      workspace_adapter_id: 'codex',
    };
    expectTypeOf(value.workspace_adapter_id).toEqualTypeOf<
      'claude' | 'codex' | 'opencode' | 'pi' | undefined
    >();
  });
});

describe('OrgSettings response contract', () => {
  it('accepts the server-returned reviewer_agents field without creating UI logic', () => {
    expectTypeOf<OrgSettings['reviewer_agents']>().toEqualTypeOf<string[]>();
  });
});

describe('daemon capacity conditional client', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  test('sends one strong If-Match header and omits revision from the exact body', async () => {
    vi.mocked(request).mockResolvedValue({});
    await putDaemonCapacity('alpha', {
      revision: 'sha256:abc', queue_workers: 6, host_global_session_cap: 13,
      rationale: 'receipts', confirm_environment_shadow: false,
    });
    expect(request).toHaveBeenCalledWith('/orgs/alpha/settings/daemon-capacity', {
      method: 'PUT',
      headers: { 'If-Match': '"sha256:abc"' },
      body: { queue_workers: 6, host_global_session_cap: 13,
        rationale: 'receipts', confirm_environment_shadow: false },
    });
  });
});

// R2 client risk: observe REAL client serialization/retry behavior over HTTP.
// These fixtures make no settings persistence, authority or native claim.
import { http, HttpResponse } from 'msw';
import { server } from '@/test/server';
import { getCodingSettings, putCodingSettings } from './settings';
import type { CodingSettingsCommand } from './types';

const codingCommand: CodingSettingsCommand = {
  operation_key: 'save:one', org_slug: 'alpha', format: 'workflow-coding-settings-command@1',
  mapping: {
    roles: { qa_engineer: { team: 'verification', principal: 'qa_engineer' },
      implementer: { team: 'engineering', principal: 'dev_agent' },
      agent_manager: { team: 'delivery', principal: 'engineering_manager' },
      code_reviewer: { team: 'review', principal: 'code_reviewer' } },
    repository_references: [{ repo_key: 'repo', repository_id: 'workflow-repository:' + 'b'.repeat(64),
      origin_digest: 'c'.repeat(64), object_format: 'sha1' }],
    eligible_replacements: { qa_engineer: [], implementer: [], agent_manager: [], code_reviewer: [] },
    release: { version: 1, identity: 'release/happyranch/coding',
      definition_digest: '6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8' },
    authority: { snapshot_digest: 'a'.repeat(64), namespace: 'org/alpha', generation: 1 },
  },
  expected_mapping_generation: 7, expected_generation: 0, admission: 'disabled',
};

// Literal independent wire oracle, not a serialization of the input fixture.
const codingWire = '{"admission":"disabled","expected_generation":0,"expected_mapping_generation":7,"format":"workflow-coding-settings-command@1","mapping":{"authority":{"generation":1,"namespace":"org/alpha","snapshot_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"eligible_replacements":{"agent_manager":[],"code_reviewer":[],"implementer":[],"qa_engineer":[]},"release":{"definition_digest":"6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8","identity":"release/happyranch/coding","version":1},"repository_references":[{"object_format":"sha1","origin_digest":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","repo_key":"repo","repository_id":"workflow-repository:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"roles":{"agent_manager":{"principal":"engineering_manager","team":"delivery"},"code_reviewer":{"principal":"code_reviewer","team":"review"},"implementer":{"principal":"dev_agent","team":"engineering"},"qa_engineer":{"principal":"qa_engineer","team":"verification"}}},"operation_key":"save:one","org_slug":"alpha"}';

const absentCoding = { format: 'workflow-coding-settings-view@1', org_slug: 'alpha',
  current: { generation: 0, admission: 'disabled', release: null, mapping: null,
    snapshot_digest: null, mapping_generation: 7 }, configuration: null, operation: null };

describe('canonical coding settings HTTP mirror', () => {
  beforeEach(async () => {
    const actual = await vi.importActual<typeof import('./client')>('./client');
    vi.mocked(request).mockImplementation(actual.request);
    sessionStorage.setItem('happyranch.token', 'tok');
  });

  test('sends the exact canonical UTF-8 command through the real shared client', async () => {
    let received = '';
    let authorization = '';
    server.use(http.put('/api/v1/orgs/alpha/settings/coding', async ({ request: incoming }) => {
      received = await incoming.text();
      authorization = incoming.headers.get('authorization') ?? '';
      return HttpResponse.json({});
    }));
    await putCodingSettings('alpha', codingCommand);
    expect(received).toBe(codingWire);
    expect(authorization).toBe('Bearer tok');
  });

  test.each([401, 503])('never reapplies a PUT on HTTP %i; original-key GET is explicit', async status => {
    let puts = 0;
    let key: string | null = null;
    server.use(http.put('/api/v1/orgs/alpha/settings/coding', () => {
      puts += 1;
      return HttpResponse.json({ detail: { code: 'coding_settings_publication_uncertain', operation_key: 'save:one' } }, { status });
    }), http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'tok-2' })),
    http.get('/api/v1/orgs/alpha/settings/coding', ({ request: incoming }) => {
      key = new URL(incoming.url).searchParams.get('operation_key');
      return HttpResponse.json(absentCoding);
    }));
    await expect(putCodingSettings('alpha', codingCommand)).rejects.toMatchObject({ status });
    expect(puts).toBe(1);
    expect(await getCodingSettings('alpha', 'save:one')).toEqual(absentCoding);
    expect(key).toBe('save:one');
    expect(codingCommand.operation_key).toBe('save:one');
  });

  test('refuses an unsafe integer before transport instead of rounding a CAS', async () => {
    let puts = 0;
    server.use(http.put('/api/v1/orgs/alpha/settings/coding', () => {
      puts += 1;
      return HttpResponse.json({});
    }));
    await expect(putCodingSettings('alpha', { ...codingCommand,
      expected_generation: 9007199254740992 })).rejects.toBeInstanceOf(TypeError);
    expect(puts).toBe(0);
  });
});
