import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Plus, Shield, Sparkles, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { isCustomSkillForbidden, useCustomSkillsCatalog } from '@/hooks/custom-skills';
import { CustomSkillCard } from './CustomSkillCard';

export function CustomSkillsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [removed, setRemoved] = useState(false);
  const query = useCustomSkillsCatalog(true, removed ? 'removed' : 'current');
  const skills = query.data?.skills ?? [];
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-fg-subtle text-overline mb-1 tracking-wider uppercase">{removed ? 'Removed' : 'Founder workspace'} · {skills.length}</div><h1 className="text-h2 text-fg">Custom Skills</h1><p className="text-fg-muted text-body-sm mt-1">Guidance visibility only. Eligibility controls who is shown a skill; it never grants permissions or tools.</p></div>{!removed && <Button asChild><Link to={`/orgs/${slug ?? ''}/skills/custom/new`}><Plus />Add custom skill</Link></Button>}</header>
    <div className="mb-4 flex gap-2" role="group" aria-label="Custom skill view"><Button variant={!removed ? 'secondary' : 'ghost'} aria-pressed={!removed} onClick={() => setRemoved(false)}>Current</Button><Button variant={removed ? 'secondary' : 'ghost'} aria-pressed={removed} onClick={() => setRemoved(true)}>Removed</Button></div>
    {query.isLoading ? <div className="border-border-subtle bg-surface-subtle h-28 animate-pulse rounded-md border" aria-label={`Loading ${removed ? 'removed' : 'current'} custom skills`} />
      : isCustomSkillForbidden(query.error) ? <EmptyState icon={<Shield size={28} />} title="Founder access required" body="Custom skill management is restricted to the founder. Agent sessions cannot view or change custom skills." />
      : query.isError ? <EmptyState icon={<TriangleAlert size={28} />} title={`Could not load ${removed ? 'removed' : 'custom'} skills`} body="The custom skills catalog is unavailable right now. Try again shortly." />
      : skills.length === 0 ? <EmptyState icon={<Sparkles size={28} />} title={removed ? 'No permanently removed skills' : 'No custom skills yet'} body={removed ? 'Permanent tombstones will appear here with their retained reservation receipts.' : 'Create a custom skill to begin managing founder-authored guidance.'} />
      : <ul className="flex flex-col gap-3">{skills.map((skill) => <li key={skill.id ?? skill.skill_id}><CustomSkillCard skill={skill} slug={slug ?? ''} /></li>)}</ul>}
  </div></div>;
}

export function CustomSkillsState({ icon, title, body }: { icon: JSX.Element; title: string; body: string }): JSX.Element { return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6"><EmptyState icon={icon} title={title} body={body} /></div></div>; }
