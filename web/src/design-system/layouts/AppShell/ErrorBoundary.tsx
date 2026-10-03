import { Component, type ErrorInfo, type ReactNode } from 'react';

/** App-owned fallback copy. Diagnostics (`error.message`/`error.stack`) never translate. */
export interface ErrorBoundaryCopy {
  title: string;
  body: string;
  retry: string;
}

const DEFAULT_COPY: ErrorBoundaryCopy = {
  title: 'Something went wrong on this page.',
  body: 'The rest of the app is still usable — navigate elsewhere via the top bar, or reload to retry.',
  retry: 'Try again',
};

interface Props {
  children: ReactNode;
  resetKey?: string;
  /**
   * Localized fallback copy supplied by the hook consumer
   * (`AppShellErrorBoundary`). Changing this prop re-renders the fallback but
   * never remounts the boundary or clears `state.error`, so a language switch
   * cannot drop the captured error.
   */
  copy?: ErrorBoundaryCopy;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    const copy = this.props.copy ?? DEFAULT_COPY;
    return (
      <div className="text-fg-muted flex h-full flex-col gap-3 p-6">
        <div className="text-fg text-base font-semibold">{copy.title}</div>
        <div className="text-xs">{copy.body}</div>
        <pre className="border-border-subtle text-fg-muted mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border p-3 text-xs">
          {error.message}
          {error.stack ? `\n\n${error.stack}` : null}
        </pre>
        <div>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="border-border-subtle text-fg hover:bg-bg-muted rounded border px-3 py-1 text-xs"
          >
            {copy.retry}
          </button>
        </div>
      </div>
    );
  }
}
