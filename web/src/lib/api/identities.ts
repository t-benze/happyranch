/** Source-backed naming v1. Every actor, URL and saved selection remains an ID. */
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
  constructor(public readonly resolution: ResolveResponse['resolutions'][number]) {
    super(resolution.status);
    this.name = 'IdentityAddressError';
  }
}

/** Matches the server parser, including quoted and email-interior @tokens. */
export function bodyAddresses(body: string): string[] {
  return [...new Set(Array.from(body.matchAll(/@([A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)/g), (m) => m[1]))];
}

export interface AddressPreflight {
  slug: string;
  context: ResolveBody['context'];
  thread_id?: string;
  recipients?: string[];
  body?: string;
  /** Only already-selected/known permanent IDs may fall back when naming is unavailable. */
  canonicalAgentIds: string[];
  allowFounder?: boolean;
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

/** Read-only preview, not a reservation across uploads or the final action. */
export async function preflightAddresses(args: AddressPreflight): Promise<{ recipients: string[] }> {
  const addresses = (args.recipients ?? []).map((a) => a.startsWith('@') ? a.slice(1) : a);
  const mentions = bodyAddresses(args.body ?? '');
  const knownIds = new Set(args.canonicalAgentIds);
  // Lookup-only read filters retain their established historical permanent-ID access.
  const knownCanonical = (a: string) => knownIds.has(a) || (args.context === 'lookup' && /^[a-z0-9_]{1,64}$/.test(a));
  let explicit: ResolveResponse['resolutions'];
  try {
    // Classify body before contextual eligibility. Unknown body @text is literal baseline.
    const bodyResult = await resolveAll(args.slug, { addresses: mentions, context: 'lookup' });
    for (const r of bodyResult) if (r.status === 'former_name') throw new IdentityAddressError(r);
    explicit = await resolveAll(args.slug, {
      addresses, context: args.context, ...(args.thread_id ? { thread_id: args.thread_id } : {}),
    });
  } catch (error) {
    // A failed read never licenses a label. Baseline canonical operations remain reachable.
    if (error instanceof IdentityAddressError) throw error;
    if (error instanceof ApiError && error.status < 500) throw error;
    if (addresses.every((a) => knownCanonical(a) || (args.allowFounder && a === 'founder'))) {
      return { recipients: [...new Set(addresses.map((a) => a === 'founder' ? '@founder' : a))] };
    }
    throw new IdentityAddressError({ address: addresses.find((a) => !knownCanonical(a)) ?? '', status: 'naming_unavailable', identity: null, eligible: false });
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
  return { recipients: [...new Set(recipients)] };
}
