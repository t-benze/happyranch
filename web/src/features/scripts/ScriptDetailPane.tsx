import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { useScript, useScriptsRoutes } from '@/hooks/scripts';
import { cn } from '@/lib/utils';
import type { ScriptRequestStatus } from '@/lib/api/types';
import { RejectScriptDialog } from './RejectScriptDialog';
import { RunScriptDialog } from './RunScriptDialog';
import { OutputPanel } from './OutputPanel';

// Mirrors STATUS_CLASS from ScriptsPage — keep in sync until a shared
// ScriptStatusBadge pattern is extracted to design-system.
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

type OpenDialog = 'reject' | 'run' | null;

interface ScriptDetailPaneProps {
  srId: string;
}

export function ScriptDetailPane({ srId }: ScriptDetailPaneProps): JSX.Element {
  const navigate = useNavigate();
  const routes = useScriptsRoutes();
  const query = useScript(srId);
  const [openDialog, setOpenDialog] = useState<OpenDialog>(null);
  const { slug } = useParams<{ slug: string }>();

  const onClose = () => navigate(routes.inbox());

  const sr = query.data;

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()}>
        <DrawerContent className="flex flex-col">
          {/* ── Header ── */}
          <header className="border-border-subtle shrink-0 border-b p-4">
            <DrawerTitle className="text-fg flex items-center gap-2 text-lg">
              <span className="text-id-task font-mono text-sm">{srId}</span>
              {sr && <ScriptStatusBadge status={sr.status} />}
            </DrawerTitle>
            {sr && (
              <p className="text-fg-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span>{sr.agent_name}</span>
                <span>·</span>
                <span className="text-id-task font-mono">{sr.task_id}</span>
                <span>·</span>
                <span>{sr.interpreter}</span>
                {sr.created_at && (
                  <>
                    <span>·</span>
                    <span>{new Date(sr.created_at).toLocaleString()}</span>
                  </>
                )}
              </p>
            )}
          </header>

          {/* ── Body ── */}
          <section className="min-h-0 flex-1 overflow-y-auto p-4 space-y-5">
            {query.isLoading && (
              <p className="text-fg-muted text-sm">Loading…</p>
            )}
            {query.isError && (
              <p className="text-fg-muted text-sm">Error loading {srId}.</p>
            )}
            {sr && (
              <>
                {/* 1. Title */}
                <h2 className="text-fg text-base font-semibold">{sr.title}</h2>

                {/* 2. Rationale */}
                <div>
                  <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Rationale
                  </h3>
                  <p className="text-fg text-sm whitespace-pre-wrap">{sr.rationale}</p>
                </div>

                {/* 3. Script preview */}
                <div>
                  <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Script
                    <span className="ml-1 normal-case">({sr.interpreter}
                      {sr.cwd_hint ? ` · cwd: ${sr.cwd_hint}` : ''}
                    )</span>
                  </h3>
                  <pre className="bg-surface-canvas text-fg overflow-x-auto rounded p-3 text-xs whitespace-pre">
                    {sr.script_text}
                  </pre>
                </div>

                {/* 4. Action bar — pending only */}
                {sr.status === 'pending' && (
                  <div className="flex gap-3">
                    <Button
                      variant="default"
                      onClick={() => setOpenDialog('run')}
                    >
                      Run
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => setOpenDialog('reject')}
                    >
                      Reject
                    </Button>
                  </div>
                )}

                {/* 5. Reject reason — rejected only */}
                {sr.status === 'rejected' && sr.reject_reason && (
                  <div>
                    <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                      Reject reason
                    </h3>
                    <p className="text-sm whitespace-pre-wrap">{sr.reject_reason}</p>
                  </div>
                )}

                {/* 6. Output panel — running / completed / failed */}
                <OutputPanel sr={sr} slug={slug ?? ''} />
              </>
            )}
          </section>
        </DrawerContent>
      </Drawer>

      {/* Reject dialog — mounted outside the Drawer so z-index stacks correctly */}
      {openDialog === 'reject' && (
        <RejectScriptDialog
          srId={srId}
          open
          onClose={() => setOpenDialog(null)}
        />
      )}

      {/* Run dialog — mounted outside the Drawer so z-index stacks correctly */}
      {openDialog === 'run' && sr && (
        <RunScriptDialog
          sr={sr}
          open
          onClose={() => setOpenDialog(null)}
        />
      )}
    </>
  );
}
