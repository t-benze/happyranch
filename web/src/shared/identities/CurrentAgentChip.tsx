import { AgentChip } from '@/design-system/patterns/AgentChip';
import { IdentityName } from './IdentityName';

/** Provider-aware composition; AgentChip itself stays pure and ID-valued. */
export function CurrentAgentChip(props: React.ComponentProps<typeof AgentChip>): JSX.Element {
  return <AgentChip {...props} label={<IdentityName canonicalId={props.name} className={props.wrap ? 'min-w-0 break-all' : undefined} />} />;
}
