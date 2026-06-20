/**
 * JobsPage — placeholder for the index route only.
 *
 * The standalone Jobs list/tab is RETIRED per PRD §4.13 / Q6. Jobs are
 * reachable contextually from Audit, task detail, and artifact cards.
 *
 * This placeholder ensures the index route (/orgs/:slug/jobs) doesn't 404
 * but directs the founder to the surfaces where jobs actually live.
 */
import { useParams, Link } from 'react-router-dom';
import { EmptyState } from '@/design-system/patterns/EmptyState';

export function JobsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();

  return (
    <EmptyState
      title="Jobs"
      body={
        <span>
          Jobs are reachable contextually — find them in the{' '}
          <Link to={`/orgs/${slug}/audit`} className="text-accent-default hover:underline">
            Audit
          </Link>{' '}
          timeline or on the{' '}
          <Link to={`/orgs/${slug}/dashboard`} className="text-accent-default hover:underline">
            Dashboard
          </Link>{' '}
          awaiting-your-approval rollup.
        </span>
      }
    />
  );
}
