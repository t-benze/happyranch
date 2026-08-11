/** TanStack Query seam for the founder Custom Skills surface. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import * as api from '@/lib/api/customSkills';

const key = (slug: string, skillId?: string) => ['custom-skills', slug, skillId] as const;
function useSlug(): string { return useParams<{ slug: string }>().slug ?? ''; }
function invalidate(qc: ReturnType<typeof useQueryClient>, slug: string, skillId?: string) { return qc.invalidateQueries({ queryKey: key(slug, skillId) }); }

export function useCustomSkillsCatalog() { const slug = useSlug(); return useQuery({ queryKey: [...key(slug), 'catalog'], queryFn: () => api.listCustomSkills(slug), enabled: !!slug }); }
export function useCustomSkill(skillId?: string) { const slug = useSlug(); return useQuery({ queryKey: [...key(slug, skillId), 'detail'], queryFn: () => api.getCustomSkill(slug, skillId as string), enabled: !!slug && !!skillId }); }
export function useCustomSkillVersions(skillId?: string) { const slug = useSlug(); return useQuery({ queryKey: [...key(slug, skillId), 'versions'], queryFn: () => api.listCustomSkillVersions(slug, skillId as string), enabled: !!slug && !!skillId }); }
export function useCustomSkillEligibility(skillId?: string) { const slug = useSlug(); return useQuery({ queryKey: [...key(slug, skillId), 'eligibility'], queryFn: () => api.getCustomSkillEligibility(slug, skillId as string), enabled: !!slug && !!skillId }); }
export function useCustomSkillDiff(skillId: string | undefined, a: number | undefined, b: number | undefined) { const slug = useSlug(); return useQuery({ queryKey: [...key(slug, skillId), 'diff', a, b], queryFn: () => api.getCustomSkillVersionDiff(slug, skillId as string, a as number, b as number), enabled: !!slug && !!skillId && a !== undefined && b !== undefined }); }
export function useCreateCustomSkill() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: (body: api.CustomSkillInput) => api.createCustomSkill(slug, body), onSuccess: () => void invalidate(qc, slug) }); }
export function usePatchCustomSkill() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: ({ skillId, body }: { skillId: string; body: Pick<api.CustomSkillInput, 'name' | 'description'> }) => api.patchCustomSkill(slug, skillId, body), onSuccess: (_, v) => void invalidate(qc, slug, v.skillId) }); }
export function useAddCustomSkillVersion() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: ({ skillId, skill_md }: { skillId: string; skill_md: string }) => api.addCustomSkillVersion(slug, skillId, { skill_md }), onSuccess: (_, v) => { void invalidate(qc, slug, v.skillId); void invalidate(qc, slug); } }); }
export function useRetireCustomSkill() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: ({ skillId, reason }: { skillId: string; reason?: string }) => api.retireCustomSkill(slug, skillId, { reason }), onSuccess: (_, v) => { void invalidate(qc, slug, v.skillId); void invalidate(qc, slug); } }); }
export function useRestoreCustomSkill() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: (skillId: string) => api.restoreCustomSkill(slug, skillId), onSuccess: (_, skillId) => { void invalidate(qc, slug, skillId); void invalidate(qc, slug); } }); }
export function usePreviewCustomSkillEligibility() { const slug = useSlug(); return useMutation({ mutationFn: ({ skillId, rules }: { skillId: string; rules: api.EligibilityRule[] }) => api.previewCustomSkillEligibility(slug, skillId, rules) }); }
export function useSaveCustomSkillEligibility() { const slug = useSlug(); const qc = useQueryClient(); return useMutation({ mutationFn: ({ skillId, rules, revision }: { skillId: string; rules: api.EligibilityRule[]; revision: number }) => api.saveCustomSkillEligibility(slug, skillId, rules, revision), onSuccess: (_, v) => void invalidate(qc, slug, v.skillId) }); }
