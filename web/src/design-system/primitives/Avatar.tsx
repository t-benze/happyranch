/**
 * Avatar — the one initials avatar (THR-376 Phase 3d). Replaces the agents
 * roster/detail `AgentAvatar`, the Jobs requested-by chip, the Todos owner
 * chips, the Threads transcript turn avatar, the retired-agent chips and the
 * Sidebar founder avatar.
 *
 * Pure and decorative: it renders the `initials` it is given (each caller keeps
 * its existing initials rule, so no displayed text changes) and is always
 * `aria-hidden`, because every caller already names the identity next to it.
 *
 * Pasture `web/design/pasture-ds.css` defines `.avatar` (26px, 50% radius,
 * `linear-gradient(135deg, var(--accent), var(--info))`, 11px / 700, white)
 * and `.avatar-sm` (22px, 50% radius, 9.5px / 700, white). It defines no other
 * avatar palette or gradient. Composed values (existing tokens only):
 *   sizes  — sm 22px (`size-5.5`, 9.5px label, ds.css `.avatar-sm`),
 *            md 30px (`size-7.5`, 11px `text-overline`, ds.css `.avatar`
 *            label), lg 34px (`size-8.5`, 12px `text-caption`). 22/30/34 are
 *            the designer review's three steps.
 *   radius — 50% (`rounded-full`), ds.css Direction A for both rules.
 *   tones  — founder: the ds.css `.avatar` gradient, accent → info.
 *            manager: `--color-agent-manager` (alias of `--color-info`).
 *            worker:  `--color-agent-worker` (alias of `--color-accent`).
 *            soft:    `--accent-soft` fill + `--accent-text` (role unknown).
 *            neutral: `--surface-2` fill + `--ink-2` (retired agents).
 *   label  — `--color-text-inverse` on the solid/gradient tones (ds.css uses
 *            #fff; the token is white in light and dark ink in dark mode,
 *            which is what these chips already used).
 *
 *   <Avatar initials="EM" tone="manager" size="lg" />
 */
import { cn } from '@/lib/utils';

export type AvatarTone = 'founder' | 'manager' | 'worker' | 'soft' | 'neutral';
export type AvatarSize = 'sm' | 'md' | 'lg';

interface AvatarProps {
  /** Already-derived initials; rendered verbatim. */
  initials: string;
  tone?: AvatarTone;
  /** sm = 22px, md = 30px, lg = 34px. */
  size?: AvatarSize;
  className?: string;
}

// Label size is kept out of `cn()`: tailwind-merge reads `text-overline` /
// `text-caption` as text colours and would drop them against `text-*` tones.
const SIZE_CLASS: Record<AvatarSize, string> = {
  sm: 'size-5.5 text-[9.5px]',
  md: 'size-7.5 text-overline',
  lg: 'size-8.5 text-caption',
};

const TONE_CLASS: Record<AvatarTone, string> = {
  // ds.css .avatar gradient verbatim (sRGB interpolation, as in ds.css).
  founder: 'bg-[linear-gradient(135deg,var(--color-accent),var(--color-info))] text-text-inverse',
  manager: 'bg-agent-manager text-text-inverse',
  worker: 'bg-agent-worker text-text-inverse',
  soft: 'bg-accent-soft text-accent-text',
  neutral: 'bg-surface-sunken text-text-secondary',
};

export function Avatar({ initials, tone = 'worker', size = 'md', className }: AvatarProps): JSX.Element {
  return (
    <span
      aria-hidden="true"
      data-avatar-tone={tone}
      className={`${SIZE_CLASS[size]} ${cn(
        'inline-grid shrink-0 place-items-center rounded-full leading-none font-bold',
        TONE_CLASS[tone],
        className,
      )}`}
    >
      {initials}
    </span>
  );
}
