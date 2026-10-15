/** Source-backed naming v1. Every actor, URL and saved selection remains an ID. */
import { proseAddresses } from '../threadMentions';
import { request, ApiError } from './client';
import type { IdentityList, IdentityView, RenameBody, ResolveBody, ResolveResponse } from './types';

const orgPath = (slug: string) => `/orgs/${encodeURIComponent(slug)}`;
export const listIdentities = (slug: string, signal?: AbortSignal): Promise<IdentityList> =>
  request(`${orgPath(slug)}/identities`, { signal });
export const resolveIdentities = (slug: string, body: ResolveBody): Promise<ResolveResponse> =>
  request(`${orgPath(slug)}/identities/resolve`, { method: 'POST', body });
export const renameAgent = (slug: string, canonicalId: string, body: RenameBody): Promise<IdentityView> =>
  request(`${orgPath(slug)}/agents/${encodeURIComponent(canonicalId)}/addressable-name`, { method: 'PUT', body });
export const renameFounder = (slug: string, body: RenameBody): Promise<IdentityView> =>
  request(`${orgPath(slug)}/founder/addressable-name`, { method: 'PUT', body });

/** Exact editable grammar; never trim/normalize a user's rename input. */
export const isEditableAddressableName = (value: string): boolean =>
  value.length >= 1 && value.length <= 64 && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value);

export class IdentityAddressError extends Error {
  constructor(public readonly resolution: ResolveResponse['resolutions'][number], public readonly bodyIntent = false, public readonly suggestions: string[] = []) {
    super(resolution.status);
    this.name = 'IdentityAddressError';
  }
}

/** Same explicit-prose grammar as the live server; preserve raw body bytes. */
export const bodyAddresses = proseAddresses;

export interface AddressPreflight {
  slug: string;
  context: ResolveBody['context'];
  thread_id?: string;
  recipients?: string[];
  body?: string;
  /** Only already-selected/known permanent IDs may fall back when naming is unavailable. */
  canonicalAgentIds: string[];
  allowFounder?: boolean;
  /** Current existing-thread agent roster; new threads use resolved To instead. */
  bodyAgentIds?: string[];
}

async function resolveAll(slug: string, body: ResolveBody): Promise<ResolveResponse['resolutions']> {
  const result: ResolveResponse['resolutions'] = [];
  for (let i = 0; i < body.addresses.length; i += 128) {
    const chunk = body.addresses.slice(i, i + 128);
    const response = await resolveIdentities(slug, { ...body, addresses: chunk });
    // Do not lose an already-observed former token to a later chunk/read failure.
    for (const r of response.resolutions) if (r.status === 'former_name' && chunk.includes(r.address)) throw new IdentityAddressError(r);
    // A partial/unattributable response cannot silently authorize a recipient.
    if (response.resolutions.length !== chunk.length || response.resolutions.some((r, j) => r.address !== chunk[j])) {
      throw new Error('Incomplete identity resolution');
    }
    result.push(...response.resolutions);
  }
  return result;
}

/** Bounded spelling guidance over eligible current options only. */
function spellingDistance(address: string, candidate: string): number {
  const a = address.slice(0, 64).toLowerCase(), b = candidate.toLowerCase();
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    for (let j = 1; j <= b.length; j++) current[j] = Math.min(
      current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    previous = current;
  }
  return previous[b.length] + Math.max(0, address.length - 64);
}

/** Read-only preview, not a reservation across uploads or the final action. */
export async function preflightAddresses(args: AddressPreflight): Promise<{ recipients: string[] }> {
  const addresses = (args.recipients ?? []).map((a) => a.startsWith('@') ? a.slice(1) : a);
  const mentions = bodyAddresses(args.body ?? '');
  const knownIds = new Set(args.canonicalAgentIds);
  // Lookup-only read filters retain their established historical permanent-ID access.
  const knownCanonical = (a: string) => knownIds.has(a) || (args.context === 'lookup' && /^[a-z0-9_]{1,64}$/.test(a));
  const knownBody = (a: string) => knownIds.has(a.toLowerCase()) || a.toLowerCase() === 'founder';
  const unavailable = (address: string) => new IdentityAddressError({ address, status: 'naming_unavailable', identity: null, eligible: false });
  // Body failures must be decided independently of explicit recipients (even []).
  // Historical lookup's shape fallback applies only to its read target, never body.
  let bodyResult: ResolveResponse['resolutions'];
  try {
    // Classify the whole input before contextual/unknown/body diagnostics.
    const whole = mentions.length ? await resolveAll(args.slug, { addresses: [...addresses, ...mentions], context: 'lookup' }) : [];
    bodyResult = whole.slice(addresses.length);
  } catch (error) {
    if (error instanceof IdentityAddressError) throw error;
    if (error instanceof ApiError && error.status < 500) throw error;
    const alias = mentions.find(a => !knownBody(a));
    if (alias !== undefined) {
      if (error instanceof ApiError && (error.status === 503 || error.code === 'naming_unavailable')) throw unavailable(alias);
      // Generic server/network failures keep their infrastructure category.
      throw error;
    }
    bodyResult = [];
  }
  for (const r of bodyResult) {
    if (r.status === 'former_name') throw new IdentityAddressError(r);
    if (r.status === 'naming_unavailable' && !knownBody(r.address)) throw unavailable(r.address);
  }
  let explicit: ResolveResponse['resolutions'];
  try {
    explicit = await resolveAll(args.slug, {
      addresses, context: args.context, ...(args.thread_id ? { thread_id: args.thread_id } : {}),
    });
  } catch (error) {
    // Existing selected-ID/read-target compatibility remains separate.
    if (error instanceof IdentityAddressError) throw error;
    if (error instanceof ApiError && error.status < 500) throw error;
    if (addresses.every((a) => knownCanonical(a) || (args.allowFounder && a === 'founder'))) {
      explicit = addresses.map(address => ({ address, status: 'naming_unavailable' as const, identity: null, eligible: false }));
    } else {
      if (!(error instanceof ApiError) || (error.status !== 503 && error.code !== 'naming_unavailable')) throw error;
      throw unavailable(addresses.find((a) => !knownCanonical(a)) ?? '');
    }
  }
  const recipients = explicit.map((r) => {
    if (args.context === 'lookup' && r.status === 'unknown_identity' && knownCanonical(r.address)) return r.address;
    if (r.status === 'naming_unavailable' && (knownCanonical(r.address) || (args.allowFounder && r.address === 'founder'))) {
      return r.address === 'founder' ? '@founder' : r.address;
    }
    if (r.status !== 'resolved' || !r.eligible || !r.identity || (r.identity.kind === 'founder' && !args.allowFounder)) {
      throw new IdentityAddressError(r.identity?.kind === 'founder' && !args.allowFounder ? { ...r, status: 'ineligible_identity', eligible: false } : r);
    }
    return r.identity.kind === 'founder' ? '@founder' : r.identity.canonical_id;
  });
  const roster = new Set(args.bodyAgentIds ?? recipients.filter(id => id !== '@founder'));
  for (const address of mentions) {
    const row = bodyResult.find(r => r.address === address);
    const id = row?.identity?.canonical_id ?? address.toLowerCase();
    if (row?.identity?.kind === 'founder' || id === 'founder') continue;
    // Outage fallback uses known actual IDs only, with the same roster gate.
    const activeAgent = row?.status === 'resolved' && row.identity?.kind === 'agent'
      && row.identity.lifecycle === 'active';
    const canonicalFallback = knownBody(address) && (!row || row.status === 'naming_unavailable');
    if ((activeAgent || canonicalFallback) && roster.has(id)) continue;
    const suggestions = [...new Set([...explicit, ...bodyResult]
      .filter(r => r.identity?.kind === 'agent' && r.identity.lifecycle === 'active' && roster.has(r.identity.canonical_id))
      .map(r => r.identity!.addressable_name ?? r.identity!.canonical_id).concat([...roster]))]
      .sort((a,b) => spellingDistance(address,a) - spellingDistance(address,b) || a.localeCompare(b)).slice(0, 3);
    throw new IdentityAddressError({ address,
      status: row?.status === 'unknown_identity' ? 'unknown_identity' : 'ineligible_identity',
      identity: row?.identity ?? null, eligible: false }, true, suggestions);
  }
  return { recipients: [...new Set(recipients)] };
}
