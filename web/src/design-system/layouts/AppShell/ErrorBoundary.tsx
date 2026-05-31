import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  resetKey?: string;
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
    return (
      <div className="text-fg-muted flex h-full flex-col gap-3 p-6">
        <div className="text-fg text-base font-semibold">Something went wrong on this page.</div>
        <div className="text-xs">
          The rest of the app is still usable — navigate elsewhere via the top bar, or reload to retry.
        </div>
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
            Try again
          </button>
        </div>
      </div>
    );
  }
}

export const meta = {
  name: "ErrorBoundary",
  layer: "layout",
  import: "@/design-system/layouts/AppShell/ErrorBoundary",
  variants: {},
  consumes: [],
  example: "<ErrorBoundary><RouteContent /></ErrorBoundary>",
} as const;
