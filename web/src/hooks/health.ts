/**
 * Public, provider-aware health hook. Features should call this instead of
 * reaching into `@/lib/api/health` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useHealth = () => useData().health.useHealth();
