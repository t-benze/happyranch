import { useId, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { Button } from '@/design-system/primitives/Button';
import { FormField } from '@/design-system/patterns/FormField';
import { ApiError, type IdentityView } from '@/lib/api';
import { useIdentities, useReadIdentities, useRenameIdentity, isEditableAddressableName, identityText } from '@/hooks/identities';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';

type Notice = { key: MessageKey; current?: IdentityView };
type Draft = { value: string; expected: number | null; notice?: Notice };

/** Independent inline editor: never shares agent-definition revision or settings save. */
export function AddressableNameEditor({ canonicalId, kind = 'agent' }: { canonicalId: string; kind?: 'agent' | 'founder' }): JSX.Element {
  const { t } = useTranslation();
  const scopedSlug = useOrgSlugOptional();
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const slug = scopedSlug ?? routeSlug ?? '';
  const query = useIdentities();
  const rename = useRenameIdentity();
  const read = useReadIdentities();
  const id = useId();
  const target = `${slug}:${kind}:${canonicalId}`;
  const currentTarget = useRef(target); currentTarget.current = target;
  // Keep independent drafts across target switches. Locale/refetch never resets them.
  const [drafts, setDrafts] = useState<Record<string, Draft | undefined>>({});
  const [pendingTarget, setPendingTarget] = useState<string | null>(null);
  const latch = useRef<string | null>(null);
  const identity = query.data?.identities.find((i) => i.kind === kind && i.canonical_id === canonicalId);
  const draft = drafts[target];
  const value = draft?.value ?? identity?.addressable_name ?? '';
  const revision = draft?.expected ?? identity?.name_revision ?? null;
  const ready = !query.isError && identity?.naming_status === 'ready' && identity.lifecycle !== 'absent' && Number.isInteger(identity.name_revision) && (identity.name_revision ?? 0) > 0;
  const pending = pendingTarget === target;
  const dirty = draft !== undefined && value !== identity?.addressable_name;

  function update(change: Partial<Draft>) {
    setDrafts((all) => ({ ...all, [target]: { value, expected: revision, ...all[target], ...change } }));
  }
  function notice(captured: string, next: Notice, expected?: number | null) {
    setDrafts((all) => all[captured] ? ({ ...all, [captured]: { ...all[captured]!, notice: next, ...(expected !== undefined ? { expected } : {}) } }) : all);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); event.stopPropagation();
    if (latch.current || !ready || !dirty || !revision) return;
    if (!isEditableAddressableName(value)) { update({ notice: { key: 'identity.grammar' } }); return; }
    const captured = target;
    const request = { addressable_name: value, expected_name_revision: revision };
    latch.current = captured; setPendingTarget(captured);
    update({ notice: undefined });
    try {
      const result = await rename.mutateAsync({ slug, kind, canonicalId, body: request });
      if (result.naming_status !== 'ready' || !Number.isInteger(result.name_revision) || result.addressable_name !== request.addressable_name || (result.name_revision ?? 0) <= request.expected_name_revision) throw new Error('Unconfirmed rename receipt');
      notice(captured, { key: 'identity.saved', current: result }, result.name_revision);
    } catch (error) {
      const stale = error instanceof ApiError && error.code === 'stale_identity_revision';
      const definitive = error instanceof ApiError && error.status < 500 && !stale;
      if (definitive) {
        const key: MessageKey = error.status === 403 ? 'identity.denied' : error.code === 'identity_name_unavailable' ? 'identity.collision' : error.status === 404 ? 'identity.absent' : 'identity.grammar';
        notice(captured, { key });
      } else {
        try {
          // Explicit network observation; no cached "success", blind retry or rollback claim.
          const fresh = await read.mutateAsync({ slug });
          const observed = fresh.identities.find((i) => i.kind === kind && i.canonical_id === canonicalId);
          if (!observed || observed.naming_status !== 'ready' || !observed.name_revision) throw new Error('Naming unavailable');
          notice(captured, { key: stale ? 'identity.stale' : 'identity.observed', current: observed }, observed.name_revision);
        } catch {
          notice(captured, { key: 'identity.uncertain' });
        }
      }
    } finally {
      if (latch.current === captured) latch.current = null;
      if (currentTarget.current === captured) setPendingTarget(null);
      else setPendingTarget((old) => old === captured ? null : old);
    }
  }
  const statusKey: MessageKey | null = query.isLoading ? 'identity.loading' : query.isError ? 'identity.loadError' : !identity || identity.lifecycle === 'absent' ? 'identity.absent' : !ready ? 'identity.unavailable' : null;
  return (
    <section className="border-border-default bg-surface min-w-0 space-y-3 rounded-lg border p-4" aria-label={t(kind === 'founder' ? 'identity.founderHeading' : 'identity.agentHeading')}>
      <h3 className="text-text-primary text-sm font-medium">{t(kind === 'founder' ? 'identity.founderHeading' : 'identity.agentHeading')}</h3>
      <p className="text-text-secondary break-all text-sm">{t('identity.immutableId')}: <code>{canonicalId}</code></p>
      {kind === 'founder' && <p className="text-text-muted text-xs">{t('identity.founderHelp')}</p>}
      <form onSubmit={submit} className="space-y-3" noValidate>
        <FormField label={t('identity.name')} htmlFor={`${id}-input`}>
          <input id={`${id}-input`} className="input w-full min-w-0" value={value} disabled={!ready || pending}
            aria-describedby={`${id}-help ${id}-status`} aria-invalid={!!draft?.notice && ['identity.grammar', 'identity.collision', 'identity.stale'].includes(draft.notice.key)}
            onChange={(event) => update({ value: event.target.value, notice: undefined })} autoComplete="off" spellCheck={false} />
        </FormField>
        <p id={`${id}-help`} className="text-text-muted text-xs">{t('identity.grammar')}</p>
        <p id={`${id}-status`} role={statusKey || draft?.notice ? 'status' : undefined} aria-live="polite" className="text-text-secondary break-words text-sm">
          {statusKey ? t(statusKey) : draft?.notice ? t(draft.notice.key, { current: identityText(draft.notice.current, canonicalId), revision: draft.notice.current?.name_revision ?? '' }) : t('identity.revision', { revision: identity?.name_revision ?? '' })}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={!ready || pending || !dirty || draft?.notice?.key === 'identity.uncertain'}>{t(pending ? 'identity.saving' : draft?.notice?.key === 'identity.stale' || draft?.notice?.key === 'identity.observed' ? 'identity.resubmit' : 'identity.save')}</Button>
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setDrafts((all) => ({ ...all, [target]: undefined }))}>{t('common.cancel')}</Button>
          {(query.isError || draft?.notice?.key === 'identity.uncertain') && <Button type="button" variant="outline" size="sm" disabled={pending || read.isPending} onClick={async () => {
            const captured = target;
            try {
              const fresh = await read.mutateAsync({ slug });
              const observed = fresh.identities.find((i) => i.kind === kind && i.canonical_id === canonicalId);
              if (draft && observed?.naming_status === 'ready' && observed.name_revision) notice(captured, { key: 'identity.observed', current: observed }, observed.name_revision);
            } catch { if (draft) notice(captured, { key: 'identity.uncertain' }); }
          }}>{t('identity.refresh')}</Button>}
        </div>
      </form>
    </section>
  );
}
