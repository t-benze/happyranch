/**
 * Skill status → shared `StatusBadge` props (THR-376 PR9, replaces the
 * Skills-only SkillStatusBadge). Product-language words are unchanged
 * (Validated / In catalog / Needs attention, plus the hidden and permanently
 * removed custom-skill states) and render through the shared badge with the
 * semanticTone tone of the same name and the existing decorative icon.
 *
 * NOT permission language: "Needs attention" flags a failed *technical*
 * validation, never an approval/admission gate.
 */
import type { ReactNode } from 'react';
import { BadgeCheck, CircleDashed, Trash2, TriangleAlert } from 'lucide-react';
import type { Tone } from '@/design-system/patterns/semanticTone';
import type { MessageKey } from '@/lib/i18n';
import { validationLabel, type ValidationTone } from './skills-catalog';

const TONE_ICON: Record<ValidationTone, typeof BadgeCheck> = {
  positive: BadgeCheck,
  attention: TriangleAlert,
  neutral: CircleDashed,
};

function badge(tone: Tone, label: string, Icon: typeof BadgeCheck): { tone: Tone; label: string; icon: ReactNode } {
  return { tone, label, icon: <Icon size={11} aria-hidden="true" className="shrink-0" /> };
}

export function skillStatusBadge(
  state: string,
  t: (key: MessageKey) => string,
): { tone: Tone; label: string; icon: ReactNode } {
  if (state === 'permanently_removed') return badge('neutral', t('skills.status.permanentlyRemoved'), Trash2);
  if (state === 'no_eligibility_policy') return badge('attention', t('skills.status.hiddenNoEligibility'), TriangleAlert);
  const knownState = state === 'valid' ? 'validated' : state === 'invalid' ? 'failed_validation' : state;
  const { key, tone } = validationLabel(knownState as 'in_catalog' | 'validated' | 'failed_validation');
  return badge(tone, t(key), TONE_ICON[tone]);
}
