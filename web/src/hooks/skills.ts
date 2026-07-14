/**
 * Public, provider-aware skills hook. Mirrors `useData().skills` so
 * compositions never reach into `design-system/providers/` directly (same
 * seam as `@/hooks/audit`).
 *
 * Also the single sanctioned re-export point for the skills row/detail types:
 * `features/*` may not deep-import `@/lib/api/skills` (eslint
 * no-restricted-imports), so the Skills compositions take the types from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export type {
  CatalogSkillItem,
  CreateSkillRequest,
  CreateSkillResponse,
  SkillDetail,
  ValidateSkillResponse,
} from '@/lib/api/skills';

export const useSkillsCatalog: ReturnType<
  typeof useData
>['skills']['useSkillsCatalog'] = (params) =>
  useData().skills.useSkillsCatalog(params);

export const useSkillDetail: ReturnType<
  typeof useData
>['skills']['useSkillDetail'] = (skillId) =>
  useData().skills.useSkillDetail(skillId);

export const useCreateSkill: ReturnType<
  typeof useData
>['skills']['useCreateSkill'] = () => useData().skills.useCreateSkill();

export const useValidateSkill: ReturnType<
  typeof useData
>['skills']['useValidateSkill'] = () => useData().skills.useValidateSkill();
