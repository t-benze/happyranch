/**
 * PlaceholderPage — lightweight "coming in the design overhaul" page for
 * Phase 1b Operate surfaces that aren't built yet (Spend, Dreams, Schedule).
 *
 * Each P2 surface will swap this placeholder for the real page + nav wiring.
 * This keeps Phase 1b render-only with no dead links.
 */
import { useParams } from 'react-router-dom';

const LABELS: Record<string, string> = {
  spend: 'Spend',
  dreams: 'Dreams',
  schedule: 'Schedule',
};

export function PlaceholderPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  // Infer the surface name from the URL path
  const surface = typeof window !== 'undefined'
    ? window.location.pathname.split('/').pop() ?? ''
    : '';
  const label = LABELS[surface] ?? surface;

  return (
    <div className="bg-surface-canvas flex h-full items-center justify-center">
      <div className="text-center">
        <div className="text-text-muted mb-2 text-5xl">🏗️</div>
        <h1 className="text-h2 text-text-primary mb-1">{label}</h1>
        <p className="text-body text-text-muted">
          Coming in the design overhaul.
        </p>
        {slug && (
          <p className="text-caption text-text-muted mt-2 font-mono">
            /orgs/{slug}/{surface}
          </p>
        )}
      </div>
    </div>
  );
}
