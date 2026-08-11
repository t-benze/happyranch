import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Shield, TriangleAlert } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useCreateCustomSkill } from '@/hooks/custom-skills';

export function CustomSkillCreatePage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>(); const navigate = useNavigate(); const create = useCreateCustomSkill();
  const [name, setName] = useState(''); const [skillSlug, setSkillSlug] = useState(''); const [description, setDescription] = useState(''); const [skillMd, setSkillMd] = useState(''); const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => { e.preventDefault(); setError(null); try { const result = await create.mutateAsync({ name, slug: skillSlug, description, skill_md: skillMd }); navigate(`/orgs/${slug ?? ''}/skills/custom/${encodeURIComponent(result.skill_id)}`); } catch (err) { setError(err); } };
  const back = <Link to={`/orgs/${slug ?? ''}/skills/custom`} className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"><ArrowLeft size={15} />Back to custom skills</Link>;
  if (error instanceof ApiError && error.status === 403) return <div className="p-6">{back}<EmptyState icon={<Shield size={28} />} title="Founder access required" body="Custom skill management is restricted to the founder. Agent sessions cannot view or change custom skills." /></div>;
  return <div className="h-full overflow-y-auto"><div className="mx-auto w-full max-w-3xl px-4 py-5 md:px-7 md:py-6">{back}<h1 className="text-h2 text-fg mb-1">Create custom skill</h1><p className="text-fg-muted text-body-sm mb-5">Create founder-authored guidance. It remains hidden until eligibility is configured.</p><form onSubmit={submit} className="border-border-default bg-surface-raised space-y-4 rounded-md border p-5"><label className="block text-sm font-medium">Name<Input value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="block text-sm font-medium">Slug<Input value={skillSlug} onChange={(e) => setSkillSlug(e.target.value)} required /></label><label className="block text-sm font-medium">Description<Input value={description} onChange={(e) => setDescription(e.target.value)} /></label><label className="block text-sm font-medium">SKILL.md<Textarea value={skillMd} onChange={(e) => setSkillMd(e.target.value)} required /></label>{Boolean(error) && <p className="text-attention-text flex items-center gap-1.5 text-sm"><TriangleAlert size={15} />Could not create this custom skill. Check the details and try again.</p>}<Button type="submit" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create custom skill'}</Button></form></div></div>;
}
