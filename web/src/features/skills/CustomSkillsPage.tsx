import { Link, useParams } from 'react-router-dom';
import { Plus, Shield, Sparkles, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { getCustomSkillId, isCustomSkillForbidden, useCustomSkillsCatalog } from '@/hooks/custom-skills';
import { SkillStatusBadge } from './SkillStatusBadge';

export function CustomSkillsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const query = useCustomSkillsCatalog();
  if (query.isLoading) return <div className="mx-auto w-full max-w-5xl p-6"><div className="border-border-subtle bg-surface-subtle h-28 animate-pulse rounded-md border" /></div>;
  if (isCustomSkillForbidden(query.error)) return <CustomSkillsState icon={<Shield size={28} />} title="Founder access required" body="Custom skill management is restricted to the founder. Agent sessions cannot view or change custom skills." />;
  if (query.isError) return <CustomSkillsState icon={<TriangleAlert size={28} />} title="Could not load custom skills" body="The custom skills catalog is unavailable right now. Try again shortly." />;
  const skills = query.data?.skills ?? [];
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-fg-subtle text-overline mb-1 tracking-wider uppercase">Founder workspace · {skills.length}</div><h1 className="text-h2 text-fg">Custom Skills</h1><p className="text-fg-muted text-body-sm mt-1">Guidance visibility only. Eligibility controls who is shown a skill; it never grants permissions or tools.</p></div><Button asChild><Link to={`/orgs/${slug ?? ''}/skills/custom/new`}><Plus />Add custom skill</Link></Button></header>
    {skills.length === 0 ? <EmptyState icon={<Sparkles size={28} />} title="No custom skills yet" body="Create a custom skill to begin managing founder-authored guidance." /> : <ul className="flex flex-col gap-3">{skills.map((skill) => { const id = getCustomSkillId(skill); const hidden = skill.hidden_reason === 'no_eligibility_policy'; return <li key={id}><Link to={`/orgs/${slug ?? ''}/skills/custom/${encodeURIComponent(id)}`} className="border-border-default bg-surface-raised hover:bg-bg-subtle block rounded-md border p-4 transition-colors"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="mb-2 flex flex-wrap items-center gap-2"><span className="bg-accent-soft text-accent-text text-2xs rounded-full px-2 py-0.5 font-bold tracking-wide uppercase">custom</span><SkillStatusBadge state={hidden ? 'no_eligibility_policy' : skill.validation_state} />{skill.retired_at && <span className="text-mono-sm text-fg-muted">Retired</span>}</div><h2 className="text-fg font-mono text-sm font-semibold">{skill.name}</h2><p className="text-fg-muted text-body-sm mt-1.5">{skill.description || 'No description provided.'}</p></div><span className="text-mono-sm text-fg-subtle">v{skill.current_version_id}</span></div></Link></li>; })}</ul>}
  </div></div>;
}

export function CustomSkillsState({ icon, title, body }: { icon: JSX.Element; title: string; body: string }): JSX.Element { return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6"><EmptyState icon={icon} title={title} body={body} /></div></div>; }
