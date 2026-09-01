import { Link } from 'react-router-dom';
import { getCustomSkillId, type CustomSkill } from '@/hooks/custom-skills';
import { SkillStatusBadge } from './SkillStatusBadge';

export function CustomSkillCard({ skill, slug }: { skill: CustomSkill; slug: string }): JSX.Element {
  const skillId = getCustomSkillId(skill);
  const hidden = skill.hidden_reason === 'no_eligibility_policy';
  const removed = skill.state === 'permanently_removed';

  return (
    <Link
      to={`/orgs/${slug}/skills/custom/${encodeURIComponent(skillId)}`}
      className="focus-visible:ring-accent border-border-default bg-surface-raised hover:bg-bg-subtle block rounded-md border p-4 transition-colors focus:outline-none focus-visible:ring-2"
      aria-label={removed ? `View permanently removed ${skill.slug}` : `View ${skill.name}`}
    >
      <article data-source="custom" data-skill-id={skillId}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="bg-accent-soft text-accent-text text-2xs rounded-full px-2 py-0.5 font-bold tracking-wide uppercase">
                custom
              </span>
              <SkillStatusBadge state={removed ? 'permanently_removed' : hidden ? 'no_eligibility_policy' : skill.validation_state} />
              {!removed && skill.retired_at && <span className="text-mono-sm text-fg-muted">Retired</span>}
            </div>
            <h3 className="text-fg font-mono text-sm font-semibold">{skill.name}</h3>
            <p className="text-fg-muted text-body-sm mt-1.5">{skill.description || 'No description provided.'}</p>
          </div>
          {removed ? <span className="text-mono-sm text-fg-subtle">Reservation retained</span> : <span className="text-mono-sm text-fg-subtle">v{skill.current_version_id}</span>}
        </div>
      </article>
    </Link>
  );
}
