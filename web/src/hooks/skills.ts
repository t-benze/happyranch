/**
 * Public, provider-aware skills hooks. Mirrors `useData().skills` so
 * compositions never reach into `design-system/providers/` directly (same
 * seam as `@/hooks/audit`).
 *
 * Also the single sanctioned re-export point for the skills row/detail types:
 * `features/*` may not deep-import `@/lib/api/skills` (eslint
 * no-restricted-imports), so the Skills compositions take the types from here.
 *
 * THR-136: Proposal Detail hooks and types are retired — agent-authored
 * proposals are now synchronously validated and published. The proposal
 * queue/detail pages redirect to the Skills catalog.
 */
import { useData } from '@/design-system/providers/DataContext';

export type {
  AssignSkillRequest,
  AssignSkillResponse,
  CatalogSkillItem,
  CreateSkillRequest,
  CreateSkillResponse,
  EditSkillRequest,
  EditSkillResponse,
  SkillDetail,
  SkillStatusAssignment,
  SkillStatusResponse,
  ValidateSkillResponse,
  ValidationEvent,
} from '@/lib/api/skills';

export const useSkillsCatalog: ReturnType<
  typeof useData
>['skills']['useSkillsCatalog'] = (params) =>
  useData().skills.useSkillsCatalog(params);

export const useSkillDetail: ReturnType<
  typeof useData
>['skills']['useSkillDetail'] = (skillId) =>
  useData().skills.useSkillDetail(skillId);

export const useSkillStatus: ReturnType<
  typeof useData
>['skills']['useSkillStatus'] = (skillId) =>
  useData().skills.useSkillStatus(skillId);

export const useAssignSkill: ReturnType<
  typeof useData
>['skills']['useAssignSkill'] = () => useData().skills.useAssignSkill();

export const useSkillValidation: ReturnType<
  typeof useData
>['skills']['useSkillValidation'] = (params) =>
  useData().skills.useSkillValidation(params);

export { ApiError } from '@/lib/api/client';
