/**
 * SkillStatusBadge — per-skill validation-state pill in product language
 * (Validated / In catalog / Needs attention). Reused across the Skills
 * slices (catalog, detail, runtime validation). Pure prop-driven; tone →
 * Pasture token mapping mirrors the StatusBadge pattern.
 *
 * NOT permission language: "Needs attention" flags a failed *technical*
 * validation, never an approval/admission gate.
 */
import { BadgeCheck, CircleDashed, TriangleAlert } from 'lucide-react';
import type { CatalogSkillItem } from '@/hooks/skills';
import { validationLabel, type ValidationTone } from './skills-catalog';

const TONE_STYLE: Record<ValidationTone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  attention: 'text-attention-text bg-attention-soft',
  neutral: 'text-fg-muted border border-border-default bg-transparent',
};

const TONE_ICON: Record<ValidationTone, typeof BadgeCheck> = {
  positive: BadgeCheck,
  attention: TriangleAlert,
  neutral: CircleDashed,
};

export function SkillStatusBadge({
  state,
}: {
  state: CatalogSkillItem['validation_state'];
}): JSX.Element {
  const { text, tone } = validationLabel(state);
  const Icon = TONE_ICON[tone];
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${TONE_STYLE[tone]}`}
    >
      <Icon size={11} aria-hidden="true" className="shrink-0" />
      {text}
    </span>
  );
}
