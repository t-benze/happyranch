/** Deterministic prototype model only. These observations are not daemon/DB proof. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { MOCK_AGENTS, MOCK_ENROLLMENTS } from '@/mocks';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { ApiError, type IdentityList, type IdentityView } from '@/lib/api';
import { bodyAddresses, IdentityAddressError, isEditableAddressableName, type AddressPreflight } from '@/lib/api/identities';
import type { IdentitiesApi } from './DataContext';

type Model = { identities: IdentityView[]; claims: Record<string, { kind: IdentityView['kind']; id: string }> };
function seed(): Model {
  const identities: IdentityView[] = [
    ...MOCK_AGENTS.map((a) => ({ canonical_id: a.name, kind: 'agent' as const, lifecycle: 'active' as const, addressable_name: a.name, name_revision: 1, canonical_definition_revision: null, naming_status: 'ready' as const })),
    ...MOCK_ENROLLMENTS.filter((a) => a.status === 'pending').map((a) => ({ canonical_id: a.name, kind: 'agent' as const, lifecycle: 'pending' as const, addressable_name: a.name, name_revision: 1, canonical_definition_revision: null, naming_status: 'ready' as const })),
    { canonical_id: 'founder', kind: 'founder', lifecycle: 'founder', addressable_name: 'founder', name_revision: 1, canonical_definition_revision: null, naming_status: 'ready' },
  ];
  return { identities, claims: Object.fromEntries(identities.map((i) => [i.canonical_id.toLowerCase(), { kind: i.kind, id: i.canonical_id }])) };
}
function model(qc: QueryClient, slug: string): Model {
  const key = ['mock-identity-model', slug];
  const cached = qc.getQueryData<Model>(key);
  if (cached) return cached;
  const initial = seed(); qc.setQueryData(key, initial); return initial;
}
const snapshot = (m: Model): IdentityList => ({ identities: m.identities.map((i) => ({ ...i })) });

export const mockIdentitiesApi: IdentitiesApi = {
  useIdentities: () => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    return useQuery({ queryKey: ['mock-identities', slug], queryFn: async () => snapshot(model(qc, slug)), enabled: !!slug, staleTime: Infinity });
  },
  useReadIdentities: () => {
    const qc = useQueryClient();
    return useMutation({ mutationFn: async ({ slug }: { slug: string }) => {
      const fresh = snapshot(model(qc, slug)); qc.setQueryData(['mock-identities', slug], fresh); return fresh;
    }, retry: false });
  },
  useRenameIdentity: () => {
    const qc = useQueryClient();
    return useMutation({ mutationFn: async ({ slug, kind, canonicalId, body }: Parameters<ReturnType<IdentitiesApi['useRenameIdentity']>['mutateAsync']>[0]) => {
      const m = model(qc, slug);
      const owner = m.identities.find((i) => i.kind === kind && i.canonical_id === canonicalId);
      if (!owner || owner.lifecycle === 'absent') throw new ApiError(404, 'identity_owner_absent', {});
      if (!isEditableAddressableName(body.addressable_name) || !Number.isInteger(body.expected_name_revision) || body.expected_name_revision <= 0) throw new ApiError(422, 'invalid_identity_request', {});
      if (body.expected_name_revision !== owner.name_revision) throw new ApiError(409, 'stale_identity_revision', {});
      const normalized = body.addressable_name.toLowerCase();
      const claim = m.claims[normalized];
      if (claim && (claim.kind !== kind || claim.id !== canonicalId)) throw new ApiError(409, 'identity_name_unavailable', {});
      const result = { ...owner, addressable_name: body.addressable_name, name_revision: (owner.name_revision ?? 0) + 1 };
      const next: Model = { identities: m.identities.map((i) => i === owner ? result : i), claims: { ...m.claims, [normalized]: { kind, id: canonicalId } } };
      qc.setQueryData(['mock-identity-model', slug], next);
      qc.setQueryData(['mock-identities', slug], snapshot(next));
      return result;
    }, retry: false });
  },
  usePreflightAddresses: () => {
    const qc = useQueryClient();
    return useMutation({ mutationFn: async (args: AddressPreflight) => {
      const m = model(qc, args.slug);
      function resolve(address: string, body: boolean): string | null {
        const normalized = (address === '@founder' ? 'founder' : address).toLowerCase();
        const claim = m.claims[normalized];
        const owner = claim && m.identities.find((i) => i.kind === claim.kind && i.canonical_id === claim.id);
        const former = owner && normalized !== owner.canonical_id.toLowerCase() && normalized !== owner.addressable_name?.toLowerCase();
        if (former) throw new IdentityAddressError({ address, status: 'former_name', identity: owner, eligible: false });
        if (body) return null;
        if (args.context === 'lookup' && !owner && (args.canonicalAgentIds.includes(address) || /^[a-z0-9_]{1,64}$/.test(address))) return address;
        if (!owner || (owner.kind === 'founder' ? !args.allowFounder : owner.lifecycle !== 'active')) throw new IdentityAddressError({ address, status: 'ineligible_identity', identity: owner ?? null, eligible: false });
        return owner.kind === 'founder' ? '@founder' : owner.canonical_id;
      }
      bodyAddresses(args.body ?? '').forEach((a) => resolve(a, true));
      return { recipients: [...new Set((args.recipients ?? []).map((a) => resolve(a, false) as string))] };
    }, retry: false });
  },
};
