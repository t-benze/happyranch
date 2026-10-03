import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Plus, Shield, Sparkles, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { isCustomSkillForbidden, useCustomSkillsCatalog } from '@/hooks/custom-skills';
import { useTranslation } from '@/hooks/i18n';
import { CustomSkillCard } from './CustomSkillCard';
import { classifySkillError, renderSkillError } from './strings';

export function CustomSkillsPage(): JSX.Element {
  const { t } = useTranslation();
  const { slug } = useParams<{ slug: string }>();
  const [removed, setRemoved] = useState(false);
  const query = useCustomSkillsCatalog(true, removed ? 'removed' : 'current');
  const skills = query.data?.skills ?? [];
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-fg-subtle text-overline mb-1 tracking-wider uppercase">{t('skills.customList.overline', { label: t(removed ? 'skills.view.removed' : 'skills.customList.founderWorkspace'), count: skills.length })}</div><h1 className="text-h2 text-fg">{t('skills.customList.title')}</h1><p className="text-fg-muted text-body-sm mt-1">{t('skills.customList.description')}</p></div>{!removed && <Button asChild><Link to={`/orgs/${slug ?? ''}/skills/custom/new`}><Plus />{t('skills.addCustom')}</Link></Button>}</header>
    <div className="mb-4 flex gap-2" role="group" aria-label={t('skills.view.label')}><Button variant={!removed ? 'secondary' : 'ghost'} aria-pressed={!removed} onClick={() => setRemoved(false)}>{t('skills.view.current')}</Button><Button variant={removed ? 'secondary' : 'ghost'} aria-pressed={removed} onClick={() => setRemoved(true)}>{t('skills.view.removed')}</Button></div>
    {query.isLoading ? <div className="border-border-subtle bg-surface-subtle h-28 animate-pulse rounded-md border" aria-label={t(removed ? 'skills.customList.loadingRemoved' : 'skills.customList.loadingCurrent')} />
      : isCustomSkillForbidden(query.error) ? <EmptyState icon={<Shield size={28} />} title={t('skills.founderRequired.title')} body={t('skills.founderRequired.body')} />
      : query.isError ? <EmptyState icon={<TriangleAlert size={28} />} title={t(removed ? 'skills.customList.errorTitleRemoved' : 'skills.catalog.customErrorTitle')} body={renderSkillError(classifySkillError(query.error, 'skills.catalog.customErrorBody'), t)} />
      : skills.length === 0 ? <EmptyState icon={<Sparkles size={28} />} title={t(removed ? 'skills.customList.emptyRemovedTitle' : 'skills.customList.emptyTitle')} body={t(removed ? 'skills.customList.emptyRemovedBody' : 'skills.customList.emptyBody')} />
      : <ul className="flex flex-col gap-3">{skills.map((skill) => <li key={skill.id ?? skill.skill_id}><CustomSkillCard skill={skill} slug={slug ?? ''} /></li>)}</ul>}
  </div></div>;
}

export function CustomSkillsState({ icon, title, body }: { icon: JSX.Element; title: string; body: string }): JSX.Element { return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6"><EmptyState icon={icon} title={title} body={body} /></div></div>; }
