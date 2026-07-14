/**
 * Public, provider-aware skills hook. Mirrors `useData().skills` so
 * compositions never reach into `design-system/providers/` directly (same
 * seam as `@/hooks/audit`).
 *
 * Also the single sanctioned re-export point for the `CatalogSkillItem` row
 * type: `features/*` may not deep-import `@/lib/api/skills` (eslint
 * no-restricted-imports), so the Skills catalog composition takes the type
 * from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export type { CatalogSkillItem } from '@/lib/api/skills';

export const useSkillsCatalog: ReturnType<
  typeof useData
>['skills']['useSkillsCatalog'] = (params) =>
  useData().skills.useSkillsCatalog(params);
