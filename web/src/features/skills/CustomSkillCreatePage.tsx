import { useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Shield, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { isCustomSkillForbidden, useCreateCustomSkill, useCustomSkillsCatalog } from '@/hooks/custom-skills';
import { OwnerSaveNotice, useConfigOwner } from '@/shared/owners';
import { useTranslation } from '@/hooks/i18n';
import { classifySkillError, renderSkillErrorInContext } from './strings';
import { skillMdHash } from './skillMdHash';
import { REMOTE_OPS, isRemoteReadOnlyError, useRemoteReadOnly } from '@/lib/remotePolicy';
import { RemoteReadOnlyNote } from '@/shared/remotePolicy/RemoteReadOnlyNote';

// Create-error copy goes through the shared Skills diagnostic boundary: the
// seq43 Option A `invalid_slug` refusal and the `slug_permanently_reserved`
// receipt map to catalog copy; any other daemon code/detail is appended
// verbatim after the localized generic failure line.

// THR-387 S6-W2a: routes.tsx keys this page by org, so an org change (after
// Discard consent) always starts a new empty draft for the new org.
export function CustomSkillCreatePage(): JSX.Element {
  const { t } = useTranslation();
  const { slug } = useParams<{ slug: string }>(); const navigate = useNavigate(); const create = useCreateCustomSkill();
  const [name, setName] = useState(''); const [skillSlug, setSkillSlug] = useState(''); const [description, setDescription] = useState(''); const [skillMd, setSkillMd] = useState(''); const [error, setError] = useState<unknown>(null);
  // THR-387 S6-W2a: the unsent new-skill form is a Skills owner. An unknown
  // create result is checked against the catalog: no row with the sent slug
  // means it did not apply; a row only proves it when its name (and the sent
  // description) and its current content hash match exactly what this create
  // sent. A same-slug row with anything else keeps the result unknown (an
  // existing slug proves nothing about this create).
  const catalog = useCustomSkillsCatalog(false);
  const sent = useRef<{ body: { name: string; slug: string; skill_md: string; description?: string }; hash: Promise<string | null> } | null>(null);
  const reset = (): void => { setName(''); setSkillSlug(''); setDescription(''); setSkillMd(''); setError(null); };
  const owner = useConfigOwner({
    page: 'skills.customCreate',
    org: slug ?? null,
    dirty: [name, skillSlug, description, skillMd].some((value) => value !== ''),
    draft: { name, skillSlug, description, skillMd },
    onDiscard: reset,
    checkResult: async () => {
      const submitted = sent.current;
      if (submitted === null) return 'unknown';
      const read = await catalog.refetch();
      if (read.isError || !read.data) return 'unknown';
      const row = read.data.skills.find((skill) => skill.slug === submitted.body.slug);
      if (!row) return 'not_saved';
      const hash = await submitted.hash;
      const matches = hash !== null
        && row.content_hash === hash
        && row.name === submitted.body.name
        && (submitted.body.description === undefined || row.description === submitted.body.description);
      return matches ? 'saved' : 'unknown';
    },
  });
  // S8-2b: creating a custom skill is configuration. On a remote Host this
  // page (incl. deep links) stays visible, read-only: "Change this on {Host}".
  const readOnlyHost = useRemoteReadOnly(REMOTE_OPS.createCustomSkill);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); if (readOnlyHost !== null) return; setError(null);
    // THR-262: a trimmed-blank optional description is omitted from the
    // request entirely, so the server derives the catalog description from
    // the SKILL.md frontmatter. A nonblank value is sent as supplied and the
    // server rejects it when it differs from the validated frontmatter.
    const body = { name, slug: skillSlug, skill_md: skillMd, ...(description.trim() ? { description } : {}) };
    // No second create while this one's result is unknown (Check result
    // first) or while one is in flight (receipt admission or request): begin()
    // refuses then, before the sent payload is replaced.
    const ticket = owner.begin();
    if (ticket === null) return;
    sent.current = { body, hash: skillMdHash(skillMd) };
    const result = await owner.track(() => create.mutateAsync(body), undefined, ticket);
    if (result.ok) navigate(`/orgs/${slug ?? ''}/skills/custom/${encodeURIComponent(result.value.skill_id)}`);
    else if (!isRemoteReadOnlyError(result.error)) setError(result.error);
  };
  // Receipt admission counts as pending, like the request itself.
  const pending = create.isPending || owner.busy;
  const back = <Link to={`/orgs/${slug ?? ''}/skills/custom`} className="text-text-secondary hover:text-text-primary text-body-sm mb-4 inline-flex items-center gap-1.5"><ArrowLeft size={15} />{t('skills.backToCustom')}</Link>;
  if (isCustomSkillForbidden(error)) return <div className="p-8">{back}<EmptyState icon={<Shield size={28} />} title={t('skills.founderRequired.title')} body={t('skills.founderRequired.body')} /></div>;
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-3xl px-4 py-6 md:px-12 md:py-8">{back}<h1 className="text-h2 text-text-primary mb-1">{t('skills.create.title')}</h1><p className="text-text-secondary text-body-sm mb-6">{t('skills.create.description')}</p>{readOnlyHost && <RemoteReadOnlyNote host={readOnlyHost} />}<form onSubmit={submit} className="border-border-default bg-surface-raised rounded-md border p-6"><fieldset disabled={readOnlyHost !== null} className="m-0 min-w-0 space-y-4 border-0 p-0"><label className="block text-sm font-medium">{t('skills.create.name')}<Input value={name} onChange={(e) => setName(e.target.value)} disabled={pending} required /></label><label className="block text-sm font-medium">{t('skills.create.slug')}<Input value={skillSlug} onChange={(e) => setSkillSlug(e.target.value)} disabled={pending} required /></label><label className="block text-sm font-medium">{t('skills.create.descriptionLabel')}<Input value={description} onChange={(e) => setDescription(e.target.value)} disabled={pending} /><span className="text-text-secondary block text-xs font-normal">{t('skills.create.descriptionHint')}</span></label><label className="block text-sm font-medium">SKILL.md<Textarea value={skillMd} onChange={(e) => setSkillMd(e.target.value)} disabled={pending} required /></label><OwnerSaveNotice owner={owner} className="" />{Boolean(error) && owner.record?.status !== 'unknown' && <p role="alert" className="text-attention-text flex items-center gap-1.5 text-sm"><TriangleAlert size={15} />{renderSkillErrorInContext(classifySkillError(error, 'skills.create.error.fallback'), 'skills.create.error.fallback', t)}</p>}<Button type="submit" disabled={pending || owner.saveRefused}>{t(pending ? 'skills.create.submitting' : 'skills.create.submit')}</Button></fieldset></form></div></div>;
}
