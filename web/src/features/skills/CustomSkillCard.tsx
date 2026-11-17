import { Link } from 'react-router-dom';
import { getCustomSkillId, type CustomSkill } from '@/hooks/custom-skills';
import { useTranslation } from '@/hooks/i18n';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { skillStatusBadge } from './skillStatus';

export function CustomSkillCard({ skill, slug }: { skill: CustomSkill; slug: string }): JSX.Element {
  const { t } = useTranslation();
  const skillId = getCustomSkillId(skill);
  const hidden = skill.hidden_reason === 'no_eligibility_policy';
  const removed = skill.state === 'permanently_removed';

  return (
    <Link
      to={`/orgs/${slug}/skills/custom/${encodeURIComponent(skillId)}`}
      className="focus-visible:ring-accent-default border-border-default bg-surface-raised hover:bg-surface-sunken block rounded-md border p-4 transition-colors focus:outline-none focus-visible:ring-2"
      aria-label={removed ? t('skills.catalog.viewRemovedSkill', { slug: skill.slug }) : t('skills.catalog.viewSkill', { name: skill.name })}
    >
      <article data-source="custom" data-skill-id={skillId}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="bg-accent-soft text-accent-text text-2xs rounded-full px-2 py-0.5 font-bold tracking-wide uppercase">
                {t('skills.source.custom')}
              </span>
              <StatusBadge {...skillStatusBadge(removed ? 'permanently_removed' : hidden ? 'no_eligibility_policy' : skill.validation_state, t)} />
              {!removed && skill.retired_at && <span className="text-mono-sm text-text-secondary">{t('skills.retired')}</span>}
            </div>
            <h3 className="text-text-primary font-mono text-sm font-semibold">{skill.name}</h3>
            <p className="text-text-secondary text-body-sm mt-1.5">{skill.description || t('skills.noDescription')}</p>
          </div>
          {removed ? <span className="text-mono-sm text-text-muted">{t('skills.reservationRetained')}</span> : <span className="text-mono-sm text-text-muted">v{skill.current_version_id}</span>}
        </div>
      </article>
    </Link>
  );
}
