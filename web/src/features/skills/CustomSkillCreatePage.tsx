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

// Create-error copy goes through the shared Skills diagnostic boundary: the
// seq43 Option A `invalid_slug` refusal and the `slug_permanently_reserved`
// receipt map to catalog copy; any other daemon code/detail is appended
// verbatim after the localized generic failure line.

export function CustomSkillCreatePage(): JSX.Element {
  const { t } = useTranslation();
  const { slug } = useParams<{ slug: string }>(); const navigate = useNavigate(); const create = useCreateCustomSkill();
  const [name, setName] = useState(''); const [skillSlug, setSkillSlug] = useState(''); const [description, setDescription] = useState(''); const [skillMd, setSkillMd] = useState(''); const [error, setError] = useState<unknown>(null);
  // THR-387 S6-W2a: the unsent new-skill form is a Skills owner. An unknown
  // create result is checked by looking the sent slug up in the catalog.
  const catalog = useCustomSkillsCatalog(false);
  const sentSlug = useRef<string | null>(null);
  const owner = useConfigOwner({
    page: 'skills.customCreate',
    org: slug ?? null,
    dirty: [name, skillSlug, description, skillMd].some((value) => value !== ''),
    draft: { name, skillSlug, description, skillMd },
    checkResult: async () => {
      const read = await catalog.refetch();
      if (read.isError || !read.data || sentSlug.current === null) return 'unknown';
      return read.data.skills.some((skill) => skill.slug === sentSlug.current) ? 'saved' : 'not_saved';
    },
  });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null);
    // THR-262: a trimmed-blank optional description is omitted from the
    // request entirely, so the server derives the catalog description from
    // the SKILL.md frontmatter. A nonblank value is sent as supplied and the
    // server rejects it when it differs from the validated frontmatter.
    const body = { name, slug: skillSlug, skill_md: skillMd, ...(description.trim() ? { description } : {}) };
    sentSlug.current = skillSlug;
    const result = await owner.track(() => create.mutateAsync(body));
    if (result.ok) navigate(`/orgs/${slug ?? ''}/skills/custom/${encodeURIComponent(result.value.skill_id)}`);
    else setError(result.error);
  };
  const back = <Link to={`/orgs/${slug ?? ''}/skills/custom`} className="text-text-secondary hover:text-text-primary text-body-sm mb-4 inline-flex items-center gap-1.5"><ArrowLeft size={15} />{t('skills.backToCustom')}</Link>;
  if (isCustomSkillForbidden(error)) return <div className="p-8">{back}<EmptyState icon={<Shield size={28} />} title={t('skills.founderRequired.title')} body={t('skills.founderRequired.body')} /></div>;
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-3xl px-4 py-6 md:px-12 md:py-8">{back}<h1 className="text-h2 text-text-primary mb-1">{t('skills.create.title')}</h1><p className="text-text-secondary text-body-sm mb-6">{t('skills.create.description')}</p><form onSubmit={submit} className="border-border-default bg-surface-raised space-y-4 rounded-md border p-6"><label className="block text-sm font-medium">{t('skills.create.name')}<Input value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="block text-sm font-medium">{t('skills.create.slug')}<Input value={skillSlug} onChange={(e) => setSkillSlug(e.target.value)} required /></label><label className="block text-sm font-medium">{t('skills.create.descriptionLabel')}<Input value={description} onChange={(e) => setDescription(e.target.value)} /><span className="text-text-secondary block text-xs font-normal">{t('skills.create.descriptionHint')}</span></label><label className="block text-sm font-medium">SKILL.md<Textarea value={skillMd} onChange={(e) => setSkillMd(e.target.value)} required /></label><OwnerSaveNotice owner={owner} className="" />{Boolean(error) && owner.record?.status !== 'unknown' && <p role="alert" className="text-attention-text flex items-center gap-1.5 text-sm"><TriangleAlert size={15} />{renderSkillErrorInContext(classifySkillError(error, 'skills.create.error.fallback'), 'skills.create.error.fallback', t)}</p>}<Button type="submit" disabled={create.isPending}>{t(create.isPending ? 'skills.create.submitting' : 'skills.create.submit')}</Button></form></div></div>;
}
