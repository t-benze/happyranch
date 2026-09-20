import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Shield, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { isCustomSkillForbidden, isCustomSkillSlugPermanentlyReserved, useCreateCustomSkill } from '@/hooks/custom-skills';

export function CustomSkillCreatePage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>(); const navigate = useNavigate(); const create = useCreateCustomSkill();
  const [name, setName] = useState(''); const [skillSlug, setSkillSlug] = useState(''); const [description, setDescription] = useState(''); const [skillMd, setSkillMd] = useState(''); const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null);
    try {
      // THR-262: a trimmed-blank optional description is omitted from the
      // request entirely, so the server derives the catalog description from
      // the SKILL.md frontmatter. A nonblank value is sent as supplied and the
      // server rejects it when it differs from the validated frontmatter.
      const body = { name, slug: skillSlug, skill_md: skillMd, ...(description.trim() ? { description } : {}) };
      const result = await create.mutateAsync(body);
      navigate(`/orgs/${slug ?? ''}/skills/custom/${encodeURIComponent(result.skill_id)}`);
    } catch (err) { setError(err); }
  };
  const back = <Link to={`/orgs/${slug ?? ''}/skills/custom`} className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"><ArrowLeft size={15} />Back to custom skills</Link>;
  if (isCustomSkillForbidden(error)) return <div className="p-6">{back}<EmptyState icon={<Shield size={28} />} title="Founder access required" body="Custom skill management is restricted to the founder. Agent sessions cannot view or change custom skills." /></div>;
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-3xl px-4 py-5 md:px-7 md:py-6">{back}<h1 className="text-h2 text-fg mb-1">Create custom skill</h1><p className="text-fg-muted text-body-sm mb-5">Create founder-authored guidance. It remains hidden until eligibility is configured.</p><form onSubmit={submit} className="border-border-default bg-surface-raised space-y-4 rounded-md border p-5"><label className="block text-sm font-medium">Name<Input value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="block text-sm font-medium">Slug<Input value={skillSlug} onChange={(e) => setSkillSlug(e.target.value)} required /></label><label className="block text-sm font-medium">Description<Input value={description} onChange={(e) => setDescription(e.target.value)} /><span className="text-fg-muted block text-xs font-normal">Leave blank to use the description from the skill guide frontmatter. A nonblank entry must match it.</span></label><label className="block text-sm font-medium">SKILL.md<Textarea value={skillMd} onChange={(e) => setSkillMd(e.target.value)} required /></label>{Boolean(error) && <p role="alert" className="text-attention-text flex items-center gap-1.5 text-sm"><TriangleAlert size={15} />{isCustomSkillSlugPermanentlyReserved(error) ? 'This slug is permanently reserved by a removed custom skill. Open the Removed view to inspect its receipt.' : 'Could not create this custom skill. Check the details and try again.'}</p>}<Button type="submit" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create custom skill'}</Button></form></div></div>;
}
