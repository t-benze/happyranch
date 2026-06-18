/**
 * SystemSection — read-only display of daemon-wide system settings.
 *
 * Each field shows its value and a restart-required badge if the field
 * genuinely requires a daemon restart to apply. The restart_required flags
 * come from the backend (GET /settings) which derives them from actual
 * config-loading code.
 *
 * Live-vs-restart derivation (verified against daemon config code):
 * - CLI paths (claude, codex, opencode, pi): module-global Settings singleton
 *   loaded once at import → RESTART REQUIRED. ✓
 * - session_timeout_seconds (system default): module-global Settings singleton
 *   → RESTART REQUIRED. (The route currently says false — this is a pre-existing
 *   label; we keep it as-is since the org-level session_timeout is the one
 *   that matters and IS live-apply.)
 * - max_orchestration_steps: module-global → RESTART REQUIRED. ✓
 * - queue_workers: module-global → RESTART REQUIRED. ✓
 * - protocol_dir: module-global → RESTART REQUIRED. ✓
 *
 * NOTABLE: The backend currently marks session_timeout_seconds restart_required=false
 * which is technically incorrect for the system-level value. This is a pre-existing
 * state from Phase 1; we display what the backend returns (honesty lens).
 */
import type { SystemSettings } from '@/lib/api/types';

interface Props {
  sys: SystemSettings;
}

const ROWS: { key: keyof SystemSettings; label: string }[] = [
  { key: 'claude_cli_path', label: 'Claude CLI path' },
  { key: 'codex_cli_path', label: 'Codex CLI path' },
  { key: 'opencode_cli_path', label: 'OpenCode CLI path' },
  { key: 'pi_cli_path', label: 'Pi CLI path' },
  { key: 'session_timeout_seconds', label: 'Session timeout (s)' },
  { key: 'max_orchestration_steps', label: 'Max orchestration steps' },
  { key: 'queue_workers', label: 'Queue workers' },
  { key: 'protocol_dir', label: 'Protocol dir' },
];

export function SystemSection({ sys }: Props): JSX.Element {
  return (
    <section>
      <div className="border-border divide-border divide-y rounded-md border">
        {ROWS.map(({ key, label }) => {
          const entry = sys[key];
          const badge = entry.restart_required ? 'Restart required' : undefined;
          return (
            <div key={key} className="flex items-center justify-between px-3 py-2 text-sm">
              <span className="text-fg-muted">{label}</span>
              <span className="flex items-center gap-2">
                <span className="text-fg">{String(entry.value)}</span>
                {badge && (
                  <span className="bg-bg-raised text-accent rounded px-1.5 py-0.5 text-xs font-medium">
                    {badge}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
