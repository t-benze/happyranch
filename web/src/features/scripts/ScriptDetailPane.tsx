import { useNavigate } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { useScript, useScriptsRoutes } from '@/hooks/scripts';
import { cn } from '@/lib/utils';
import type { ScriptRequestStatus } from '@/lib/api/types';

// Mirrors STATUS_CLASS from ScriptsPage — keep in sync until Task 27 extracts
// this to a shared pattern.
const STATUS_CLASS: Record<ScriptRequestStatus, string> = {
  pending: 'bg-tier-yellow-tint text-status-archiving',
  running: 'bg-tier-green-tint text-status-open',
  completed: 'border border-border-subtle bg-transparent text-status-archived',
  failed: 'bg-tier-red-tint text-status-abandoned',
  rejected: 'border border-border-subtle bg-transparent text-fg-muted',
};

function ScriptStatusBadge({ status }: { status: ScriptRequestStatus }): JSX.Element {
  return (
    <span
      className={cn(
        'text-mono-sm inline-flex items-center rounded-sm px-2 py-px font-mono text-xs font-semibold',
        STATUS_CLASS[status],
      )}
    >
      {status}
    </span>
  );
}

interface ScriptDetailPaneProps {
  srId: string;
}

export function ScriptDetailPane({ srId }: ScriptDetailPaneProps): JSX.Element {
  const navigate = useNavigate();
  const routes = useScriptsRoutes();
  const query = useScript(srId);

  const onClose = () => navigate(routes.inbox());

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent className="flex flex-col">
        <header className="border-border-subtle shrink-0 border-b p-4">
          <DrawerTitle className="text-fg flex items-center gap-2 text-lg">
            <span className="text-id-task font-mono text-sm">{srId}</span>
            {query.data && <ScriptStatusBadge status={query.data.status} />}
          </DrawerTitle>
          {query.data && (
            <p className="text-fg-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span>{query.data.agent_name}</span>
              <span>· {query.data.task_id}</span>
              <span>· {query.data.interpreter}</span>
            </p>
          )}
        </header>
        <section className="min-h-0 flex-1 overflow-y-auto p-4">
          {query.isLoading && (
            <p className="text-fg-muted text-sm">Loading…</p>
          )}
          {query.isError && (
            <p className="text-fg-muted text-sm">Error loading {srId}.</p>
          )}
          {query.data && (
            <>
              <h2 className="text-fg mb-4 text-base font-semibold">{query.data.title}</h2>
              <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                Rationale
              </h3>
              <p className="text-fg mb-4 whitespace-pre-wrap text-sm">{query.data.rationale}</p>
              <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                Script ({query.data.interpreter})
              </h3>
              <pre className="bg-surface-canvas text-fg overflow-x-auto rounded p-3 text-xs whitespace-pre">
                {query.data.script_text}
              </pre>
            </>
          )}
        </section>
      </DrawerContent>
    </Drawer>
  );
}
