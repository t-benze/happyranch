/** Source-contract assertions; mocked HTTP is not DB proof. Execution receipts live in the maker handoff. */
import { beforeEach, describe, expect, test } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/server';
import { bodyAddresses, isEditableAddressableName, listIdentities, preflightAddresses, renameAgent, renameFounder, resolveIdentities } from './identities';
import corpus from '../../../../protocol/thread-mention-corpus.json';
import { scanMentions, openMention } from '../threadMentions';
import type { IdentityView } from './types';

const agent: IdentityView = { canonical_id: 'worker', kind: 'agent', lifecycle: 'active', addressable_name: 'Current', name_revision: 2, canonical_definition_revision: null, naming_status: 'ready' };
const founder: IdentityView = { ...agent, canonical_id: 'founder', kind: 'founder', lifecycle: 'founder' };
describe('naming adapters — mocked source contract', () => {
  beforeEach(() => sessionStorage.setItem('happyranch.token', 'mock-token'));
  test('all four real routes keep canonical IDs, positive CAS and exact body without actor/session proof', async () => {
    const calls: { path: string; body: unknown }[] = [];
    server.use(
      http.get('/api/v1/orgs/one/identities', () => HttpResponse.json({ identities: [agent, founder] })),
      http.post('/api/v1/orgs/one/identities/resolve', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json({ resolutions: [{ address: 'Current', identity: agent, status: 'resolved', eligible: true }] }); }),
      http.put('/api/v1/orgs/one/agents/worker/addressable-name', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json(agent); }),
      http.put('/api/v1/orgs/one/founder/addressable-name', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json(founder); }),
    );
    expect((await listIdentities('one')).identities[0].canonical_id).toBe('worker');
    await resolveIdentities('one', { addresses: ['Current'], context: 'thread_recipient' });
    const body = { addressable_name: 'Exact-Name', expected_name_revision: 2 };
    await renameAgent('one', 'worker', body); await renameFounder('one', body);
    expect(calls).toEqual([
      { path: '/api/v1/orgs/one/identities/resolve', body: { addresses: ['Current'], context: 'thread_recipient' } },
      { path: '/api/v1/orgs/one/agents/worker/addressable-name', body },
      { path: '/api/v1/orgs/one/founder/addressable-name', body },
    ]);
  });
  test('editable grammar is exact; historical underscore IDs never widen rename grammar', () => {
    for (const name of ['A', '0_name-Test', 'a'.repeat(64)]) expect(isEditableAddressableName(name)).toBe(true);
    for (const name of ['', '_worker', ' A', 'A ', '张三', 'A.B', 'a'.repeat(65)]) expect(isEditableAddressableName(name)).toBe(false);
    expect(bodyAddresses('> "mail@Former and @Current" @unknown @A.B @_worker')).toEqual([]);
  });
  test('genuine Former precedes recipient errors; unknown body refuses; quote remains literal', async () => {
    const calls: unknown[] = [];
    server.use(http.post('/api/v1/orgs/one/identities/resolve', async ({ request }) => {
      const body = await request.json() as { addresses: string[]; context: string }; calls.push(body);
      return HttpResponse.json({ resolutions: body.addresses.map((address) => ({ address, status: address === 'Former' ? 'former_name' : 'unknown_identity', identity: address === 'Former' ? agent : null, eligible: false })) });
    }));
    await expect(preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['worker'], body: '@Former', canonicalAgentIds: ['worker'] })).rejects.toMatchObject({ resolution: { status: 'former_name' } });
    expect(calls).toEqual([{ addresses: ['worker', 'Former'], context: 'lookup' }]);
    await expect(preflightAddresses({ slug: 'one', context: 'thread_recipient', body: '@unknown', canonicalAgentIds: [] })).rejects.toMatchObject({ bodyIntent: true, resolution: {status: 'unknown_identity'} });
  });
  test('unavailable naming retains known permanent IDs but never licenses an alias or retries', async () => {
    let requests = 0;
    server.use(http.post('/api/v1/orgs/one/identities/resolve', () => { requests += 1; return HttpResponse.json({ detail: { code: 'naming_unavailable' } }, { status: 503 }); }));
    expect(await preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['_worker'], canonicalAgentIds: ['_worker'] })).toEqual({ recipients: ['_worker'] });
    await expect(preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['Current'], canonicalAgentIds: ['_worker'] })).rejects.toMatchObject({ resolution: { status: 'naming_unavailable' } });
    expect(requests).toBe(2);
  });
});

// TASK11431 finite groups1/2/3/6: shipping extraction, not renderer equivalence.
const lexicalCases: [string, string[]][] = [
  ['the contact email is `contact@danze.ltd`', []],
  ['contact@Former word@Current @@Current a.b+tag@Current /@Current .@Current', []],
  ['(@Current), "@Other" >@Current @Current. @current @_worker @A.B', ['Current', 'Other', 'current', 'A.B']],
  ['\\@Former \\\\@Current word\\\\@Other', ['Current']],
  ['\\` @Current', ['Current']],
  ['`@Former` @Current', ['Current']],
  ['``one ` @Former`` @Current', ['Current']],
  ['`@Former\\` @Current', ['Current']],
  ['`\n\n@Current\n\n`', ['Current']],
  ['`\r\n\r\n@Current\r\n\r\n`', ['Current']],
  ['`\r\r@Current\r\r`', ['Current']],
  ['`\n@Current', ['Current']],
  ['> `\n@Current\n`', ['Current']],
  ['`\n> @Current\n`', []],
  ['`\n@Current\n`', []],
  ['> `\n> @Former\n> `\n@Current', ['Current']],
  [' > ```\n> @Former\n@Current', ['Current']],
  [' > ```\r\n> @Former\r\n@Current', ['Current']],
  [' > ```\n> @Former\n>\n> @Other\n@Current', ['Current']],
  [' > ```\n> @Former\n\n@Current', ['Current']],
  [' > ```\n> @Former\n>> @Current', []],
  ['```\n@Former\n> @Current', []],
  ['```\n@Former\n```\n@Current', ['Current']],
  ['~~~~ info `\n@Former\n~~~\n@Other\n~~~~~ \t\n@Current', ['Current']],
  ['```info` @Current', ['Current']],
  ['email@Former `@Other`\n@Current', ['Current']],
  [' > ```\n> @Current\n@Former', ['Former']],
];
describe('new-message lexical contract', () => {
  beforeEach(() => sessionStorage.setItem('happyranch.token', 'mock-token'));
  test.each(lexicalCases)('extracts prose tokens from %j', (body, expected) => {
    expect(bodyAddresses(body)).toEqual(expected);
    for (const match of body.matchAll(/@([A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)/g)) {
      const hint = openMention(body, match.index! + match[0].length);
      if (hint) expect(expected).toContain(match[1]);
    }
  });
  test('caret uses complete code/paragraph/container regions and terminating punctuation', () => {
    for (const body of ['contact@Current', '`@Current`', '```\n@Current', '\\@Current']) {
      const caret = body.indexOf('@Current') + 8;
      expect(openMention(body, caret)).toBeNull();
    }
    for (const body of ['(@Current),', '`\n\n@Current\n\n`', ' > ```\n> @Former\n@Current', '`\n@Current']) {
      const at = body.indexOf('@Current');
      expect(openMention(body, at + 8)).toEqual({tokenStart:at,query:'Current'});
    }
    expect(openMention('hi @',4)).toEqual({tokenStart:3,query:''});
    expect(openMention('@Current.',9)).toBeNull();
    expect(openMention('@Current suffix',10)).toBeNull();
  });
  test('uses the finite shared whitespace set, excluding FEFF and Python-only controls', () => {
    const spaces = [9,10,11,12,13,32,133,160,5760,...Array.from({length:11},(_,i)=>8192+i),8232,8233,8239,8287,12288];
    for (const cp of spaces) expect(bodyAddresses(`${String.fromCodePoint(cp)}@Current`)).toEqual(['Current']);
    for (const cp of [0xfeff,28,29,30,31]) expect(bodyAddresses(`${String.fromCodePoint(cp)}@Current`)).toEqual([]);
  });
  test('literal-only input performs zero body resolution reads', async () => {
    let reads = 0;
    server.use(http.post('/api/v1/orgs/one/identities/resolve', () => { reads++; return HttpResponse.json({}, {status:503}); }));
    expect(await preflightAddresses({slug:'one',context:'thread_recipient',body:'email@Former `@Current`',canonicalAgentIds:[]})).toEqual({recipients:[]});
    expect(reads).toBe(0);
  });
  test.each(['503','per-token'] as const)('body alias with empty recipients refuses %s unavailable', async (failure) => {
    const reads: unknown[] = [];
    server.use(http.post('/api/v1/orgs/one/identities/resolve', async ({request}) => {
      const body = await request.json() as {addresses:string[]}; reads.push(body);
      return failure === '503' ? HttpResponse.json({detail:{code:'naming_unavailable'}},{status:503})
        : HttpResponse.json({resolutions:body.addresses.map(address=>({address,status:'naming_unavailable',identity:null,eligible:false}))});
    }));
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',body:'@current',canonicalAgentIds:['worker']})).rejects.toMatchObject({resolution:{status:'naming_unavailable'}});
    expect(reads).toEqual([{addresses:['current'],context:'lookup'}]);
  });
  test('known body IDs and manual Founder survive unavailable naming, ID-shaped aliases do not', async () => {
    server.use(http.post('/api/v1/orgs/one/identities/resolve', () => HttpResponse.json({detail:{code:'naming_unavailable'}},{status:503})));
    expect(await preflightAddresses({slug:'one',context:'thread_recipient',body:'@WORKER @founder',bodyAgentIds:['worker'],canonicalAgentIds:['worker'],allowFounder:true})).toEqual({recipients:[]});
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',recipients:['worker'],body:'@unknown',canonicalAgentIds:['worker']})).rejects.toMatchObject({resolution:{status:'naming_unavailable',address:'unknown'}});
    // Lookup compatibility applies only to explicit read targets, never body aliases.
    await expect(preflightAddresses({slug:'one',context:'lookup',body:'@unknown',canonicalAgentIds:[]})).rejects.toMatchObject({resolution:{status:'naming_unavailable'}});
  });
  test.each([500,422])('unresolved body HTTP %i keeps infrastructure/validation category', async (status) => {
    server.use(http.post('/api/v1/orgs/one/identities/resolve',()=>HttpResponse.json({detail:{code:'server_or_validation_error'}},{status})));
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',body:'@Current',canonicalAgentIds:[]})).rejects.toMatchObject({status});
  });
  test('network failure remains a transport error for unresolved body', async () => {
    server.use(http.post('/api/v1/orgs/one/identities/resolve',()=>HttpResponse.error()));
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',body:'@Current',canonicalAgentIds:[]})).rejects.toBeInstanceOf(TypeError);
  });
  test('observed Former in first chunk refuses before a later failing chunk or recipients', async () => {
    let reads=0;
    server.use(http.post('/api/v1/orgs/one/identities/resolve',async ({request})=>{
      reads++; const body=await request.json() as {addresses:string[]};
      return reads===1 ? HttpResponse.json({resolutions:body.addresses.map(address=>({address,status:address==='Former'?'former_name':'unknown_identity',identity:address==='Former'?agent:null,eligible:false}))}) : HttpResponse.json({},{status:503});
    }));
    const body = ['@Former',...Array.from({length:128},(_,i)=>`@unknown${i}`)].join(' ');
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',recipients:['worker'],body,canonicalAgentIds:['worker']})).rejects.toMatchObject({resolution:{status:'former_name'}});
    expect(reads).toBe(1);
  });
});

// C10/C19/C25: accepted shared source data drives the shipping scanner/caret.
// Four answers: raw scanner seam; exact tokens/occurrences/caret; catches drift
// and duplicate suppression; lexical support only, public writes/UI still needed.
describe('THR310 shared corpus', () => {
  beforeEach(() => sessionStorage.setItem('happyranch.token', 'mock-token'));
  test.each(corpus.cases)('$id $requirement', row => {
    expect(bodyAddresses(row.input)).toEqual(row.expected_tokens);
    expect(scanMentions(row.input).filter(m => m.query).map(m => ({ token: m.query,
      start_codepoint: Array.from(row.input.slice(0,m.tokenStart)).length,
      end_codepoint: Array.from(row.input.slice(0,m.end)).length,
      start_utf16: m.tokenStart, end_utf16: m.end }))).toEqual(row.expected_ranges);
    if (row.caret_utf16 !== null) expect(openMention(row.input,row.caret_utf16)).toEqual(row.expected_open_mention);
    for (const sample of 'parameter_samples' in row ? row.parameter_samples! : []) {
      expect(bodyAddresses(sample.input)).toEqual(sample.expected_tokens);
      expect(scanMentions(sample.input).filter(m => m.query).map(m => ({token:m.query,start_codepoint:m.tokenStart,end_codepoint:m.end,start_utf16:m.tokenStart,end_utf16:m.end}))).toEqual(sample.expected_ranges);
    }
  });
  test('To Former beats unknown body; unknown To beats invalid body', async () => {
    server.use(http.post('/api/v1/orgs/one/identities/resolve', async ({request}) => {
      const body=await request.json() as {addresses:string[]};
      return HttpResponse.json({resolutions:body.addresses.map(address=>({address,status:address==='Former'?'former_name':'unknown_identity',identity:address==='Former'?agent:null,eligible:false}))});
    }));
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',recipients:['Former'],body:'@Typo',canonicalAgentIds:[]})).rejects.toMatchObject({resolution:{status:'former_name'}});
    await expect(preflightAddresses({slug:'one',context:'thread_recipient',recipients:['missing'],body:'@Typo',canonicalAgentIds:[]})).rejects.toMatchObject({bodyIntent:false,resolution:{address:'missing'}});
  });
});
