/**
 * System Assistant page — full-viewport in-browser terminal.
 *
 * All configuration/setup for the assistant now lives in Settings → Assistant
 * (features/settings/sections/AssistantSection.tsx). This surface is purely the
 * terminal: when the assistant is configured it fills the whole content region;
 * otherwise the PTY can't attach, so it shows a short prompt pointing the
 * founder to Settings instead of a broken terminal.
 *
 * See docs/superpowers/specs/2026-06-12-system-assistant-web-ui-design.md.
 */
import { Link, useParams } from 'react-router-dom';
import { useAssistantStatus } from '@/hooks/assistant';
import { AssistantTerminal } from './AssistantTerminal';

export function SystemAssistantPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const statusQuery = useAssistantStatus();
  const status = statusQuery.data;

  if (statusQuery.isLoading) {
    return (
      <div className="bg-surface-canvas flex h-full items-center justify-center p-4">
        <p className="text-text-secondary">Loading…</p>
      </div>
    );
  }

  if (statusQuery.isError || !status) {
    return (
      <div className="bg-surface-canvas flex h-full items-center justify-center p-4">
        <p role="alert" className="text-feedback-danger text-sm">
          Could not load assistant status.
        </p>
      </div>
    );
  }

  // The terminal only attaches once an executor is registered; until then show
  // a prompt routing the founder to the one place that can configure it.
  if (status.state !== 'configured') {
    const settingsHref = slug ? `/orgs/${slug}/settings/assistant` : '#';
    return (
      <div className="bg-surface-canvas flex h-full items-center justify-center p-4">
        <div className="flex max-w-sm flex-col items-center gap-2 text-center">
          <p className="text-text-primary font-display text-base">Assistant not set up</p>
          <p className="text-text-secondary text-sm">
            Configure it in{' '}
            <Link to={settingsHref} className="text-accent hover:underline">
              Settings → Assistant
            </Link>
            .
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface-canvas h-full w-full">
      <AssistantTerminal />
    </div>
  );
}
