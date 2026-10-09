/** Usage presentation only: no data, selection, query or cache ownership. */
import { useI18n } from '@/hooks/i18n';
import * as format from './usageFormat';

export function useUsagePresentation() {
  const { locale, t } = useI18n();
  return {
    t,
    formatWindow: (window: Parameters<typeof format.formatWindow>[0]) => format.formatWindow(window, locale),
    formatInstant: (iso: string, zone: string) => format.formatInstant(iso, zone, locale),
    formatCount: (n: number) => format.formatCount(n, locale),
    formatTokenValue: (n: number) => format.formatTokenValue(n, locale),
    formatExactTokens: (n: number) => format.formatExactTokens(n, locale),
    formatDuration: (n: number) => format.formatDuration(n, locale),
    formatRate: (n: number) => format.formatRate(n, locale),
    formatPercentDelta: (n: number) => format.formatPercentDelta(n, locale),
    withheldExplanation: (reason: string | null) => format.withheldExplanation(reason, locale),
  };
}
export type UsagePresentation = ReturnType<typeof useUsagePresentation>;
