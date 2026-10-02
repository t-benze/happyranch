import { useState } from 'react';
import { Link, useBlocker, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/design-system/primitives/Dialog';
import { useAgentsList, useAgentsRoutes } from '@/hooks/agents';
import { isEligiblePolicyManager } from '@/hooks/authorityPolicy';
import { useTeamsList } from '@/hooks/teams';
import { TeamEscalationPolicyCard } from './TeamEscalationPolicyCard';
import { useTranslation } from '@/hooks/i18n';

export function TeamEscalationPolicyPage(): JSX.Element {
  const { agent_name: agentName } = useParams<{ agent_name: string }>();
  const agents = useAgentsList();
  const routes = useAgentsRoutes();
  const { t, render } = useTranslation();
  const teams = useTeamsList();
  const [dirty, setDirty] = useState(false);
  const blocker = useBlocker(dirty);

  if (agents.isLoading || teams.isLoading) return <div className="text-text-muted p-6">{t('agents.policy.loadingAgent')}</div>;
  const agent = agents.data?.agents.find((item) => item.name === agentName);
  const eligibleAgent = agent?.team && agent.role
    ? { name: agent.name, team: agent.team, role: agent.role }
    : undefined;
  if (agents.isError || teams.isError || !isEligiblePolicyManager(eligibleAgent, teams.data?.teams)) {
    return <div className="text-text-muted p-6">{render('agents.policy.notFound', { link: <Link className="text-accent-text underline" to={routes.inbox()}>{t('agents.policy.backToAgents')}</Link> })}</div>;
  }
  const policyAgent = eligibleAgent!;

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      <section className="mx-auto w-full max-w-4xl p-4 sm:p-6" aria-labelledby="team-policy-page-heading">
        <Button asChild variant="ghost" size="sm"><Link to={routes.detail(policyAgent.name)}>{t('agents.policy.backTo', { name: displayName(policyAgent.name) })}</Link></Button>
        <header className="mt-4 mb-5">
          <h1 id="team-policy-page-heading" className="font-display text-text-primary text-2xl font-medium">{t('agents.policy.title')}</h1>
          <p className="text-text-muted mt-1 text-sm">{displayName(policyAgent.team)} · {displayName(policyAgent.name)}</p>
        </header>
        <TeamEscalationPolicyCard agent={policyAgent} onDirtyChange={setDirty} />
      </section>
      <Dialog open={blocker.state === 'blocked'} onOpenChange={(open) => { if (!open && blocker.state === 'blocked') blocker.reset(); }}>
        <DialogContent aria-label={t('agents.policy.discardAria')} closeLabel={t('common.close')}>
          <DialogHeader>
            <DialogTitle>{t('agents.policy.discardTitle')}</DialogTitle>
            <DialogDescription>{t('agents.policy.discardBody')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={() => blocker.state === 'blocked' && blocker.reset()}>{t('agents.policy.stay')}</Button>
            <Button size="sm" onClick={() => blocker.state === 'blocked' && blocker.proceed()}>{t('agents.policy.discard')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function displayName(value: string): string {
  return value.split(/[_-]+/).filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}
