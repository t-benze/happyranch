/**
 * ProposalDetailPage — stub for THR-055 Slice 3B (proposal detail / review).
 * NOT part of Slice 3A scope. This stub provides a route target for the queue
 * deep-links so rows are navigable, displaying a "coming in a future slice"
 * placeholder that respects the no-mutation constraint.
 */
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Construction } from 'lucide-react';

export function ProposalDetailPage(): JSX.Element {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-6xl flex-col overflow-hidden px-4 py-5 md:px-7 md:py-6">
      <div className="mb-4">
        <Link
          to={`/orgs/${slug ?? ''}/skills/proposals`}
          className="text-fg-muted hover:text-fg text-body-sm inline-flex items-center gap-1.5 font-semibold"
        >
          <ArrowLeft size={15} aria-hidden="true" />
          Back to Proposal Queue
        </Link>
      </div>

      <div className="border-border-default bg-surface-raised mx-auto mt-12 flex max-w-md flex-col items-center gap-4 rounded-lg border p-8 text-center">
        <Construction size={32} aria-hidden="true" className="text-fg-subtle" />
        <div>
          <h2 className="text-h3 text-fg mb-1">Proposal Detail</h2>
          <p className="text-fg-muted text-body-sm">
            Proposal detail and lifecycle actions (claim, validate, review,
            publish, assign) are coming in a future slice. This placeholder
            confirms the queue deep-link routing works correctly.
          </p>
          <p className="text-fg-subtle text-mono-xs mt-3">
            Version ID: {versionId ?? '—'}
          </p>
        </div>
        <Link
          to={`/orgs/${slug ?? ''}/skills/proposals`}
          className="text-accent-text text-body-sm font-semibold hover:underline"
        >
          Return to queue
        </Link>
      </div>
    </div>
  );
}
