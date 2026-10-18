/** Public provider-aware naming hooks; all network work belongs to the provider. */
import { useContext, useMemo } from 'react';
import { DataContext, useData } from '@/design-system/providers/DataContext';
import type { AddressOption } from '@/design-system/patterns/MentionAutocomplete';
import type { IdentityView } from '@/lib/api/types';
import { ApiError } from '@/lib/api';
import { useTranslation } from '@/hooks/i18n';
import { IdentityAddressError } from '@/lib/api/identities';
import type { ThreadErrorDetail } from '@/lib/threadErrors';

export const useHasIdentityProvider = () => useContext(DataContext)?.identities !== undefined;
export const useIdentities = () => useData().identities.useIdentities();
export const useReadIdentities = () => useData().identities.useReadIdentities();
export const useRenameIdentity = () => useData().identities.useRenameIdentity();
export const usePreflightAddresses = () => useData().identities.usePreflightAddresses();
export { isEditableAddressableName } from '@/lib/api/identities';

export function currentName(identity: IdentityView | undefined, canonicalId: string): string {
  return identity?.naming_status === 'ready' && identity.lifecycle !== 'absent' && identity.addressable_name
    ? identity.addressable_name : canonicalId;
}
export function identityText(identity: IdentityView | undefined, canonicalId: string): string {
  const name = currentName(identity, canonicalId);
  return name === canonicalId ? canonicalId : `${name} · ${canonicalId}`;
}
export function useIdentityPresentation() {
  const query = useIdentities();
  const records = useMemo(() => new Map((query.isError ? [] : query.data?.identities ?? []).map((i) => [`${i.kind}:${i.canonical_id}`, i])), [query.data, query.isError]);
  const identity = (id: string, kind: 'agent' | 'founder' = id === 'founder' ? 'founder' : 'agent') => records.get(`${kind}:${id}`);
  return { query, identity, name: (id: string) => currentName(identity(id), id), label: (id: string) => identityText(identity(id), id) };
}

/** Avatar initials for a display name: first letters of its first two separator-delimited segments, else its first two characters. */
export function identityInitials(name: string): string {
  const parts = name.split(/[_\s.-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}
/** Bounded Founder presentation: the ready current name alone, else the canonical `founder` ID (never a stale or foreign org's name). */
export function useFounderName(): string {
  return useIdentityPresentation().name('founder');
}

/** Descriptor stays locale-neutral; rendering never triggers another request. */
export function namingAddressError(error: unknown): ThreadErrorDetail | null {
  if (error instanceof ApiError) {
    if (error.code === 'former_name' && error.detail && typeof error.detail === 'object') {
      const d = error.detail as Record<string, unknown>;
      return { kind: 'mapped', key: 'identity.formerServer', params: { address: typeof d.address === 'string' ? d.address : '', current: typeof d.current_name === 'string' ? d.current_name : typeof d.canonical_id === 'string' ? d.canonical_id : '' } };
    }
    if (error.code === 'invalid_body_mention' && error.detail && typeof error.detail === 'object') {
      const d = error.detail as Record<string, unknown>;
      return { kind: 'mapped', key: 'identity.invalidBodyMention', params: { address: String(d.address ?? ''), suggestions: Array.isArray(d.suggestions) ? d.suggestions.join(', ') : '' } };
    }
    if (error.code === 'naming_classification_changed') return { kind: 'mapped', key: 'identity.classificationChanged' };
    if (error.code === 'naming_unavailable') return { kind: 'mapped', key: 'identity.unavailable' };
  }
  if (!(error instanceof IdentityAddressError)) return null;
  const r = error.resolution;
  if (error.bodyIntent) return { kind: 'mapped', key: 'identity.invalidBodyMention', params: { address: r.address, suggestions: error.suggestions.join(', ') } };
  if (r.status === 'former_name') return { kind: 'mapped', key: 'identity.former', params: { address: r.address, current: r.identity?.addressable_name ?? r.identity?.canonical_id ?? '' } };
  return { kind: 'mapped', key: r.status === 'naming_unavailable' ? 'identity.unavailable' : 'identity.invalidAddress', params: { address: r.address } };
}

/** Eligibility remains the supplied canonical roster. Founder is a separate human option. */
export function useIdentityOptions(agents: readonly { name: string; team: string | null }[], includeFounder = false): AddressOption[] {
  const { identity } = useIdentityPresentation();
  const { t } = useTranslation();
  const options: AddressOption[] = agents.map((a) => ({ name: a.name, team: a.team, kind: 'agent' as const,
    addressable_name: currentName(identity(a.name, 'agent'), a.name), label: identityText(identity(a.name, 'agent'), a.name),
  }));
  if (includeFounder) options.push({ name: 'founder', team: null, kind: 'founder', kindLabel: t('identity.human'),
    addressable_name: currentName(identity('founder', 'founder'), 'founder'), label: identityText(identity('founder', 'founder'), 'founder'),
  });
  return options;
}

/** Owner-projected IDs for body fallback; independent of the eligible hint roster. */
export function useKnownAgentIds(agents: readonly { name: string }[]): string[] {
  const query = useIdentities();
  return useMemo(() => [...new Set([
    ...agents.map(a => a.name),
    ...(query.isError ? [] : query.data?.identities ?? [])
      .filter(i => i.kind === 'agent' && i.lifecycle !== 'absent').map(i => i.canonical_id),
  ])], [agents, query.data, query.isError]);
}
