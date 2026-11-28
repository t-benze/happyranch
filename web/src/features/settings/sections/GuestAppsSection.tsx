/**
 * Settings ▸ Organization ▸ Desktop app guests (THR-362 spec §4.1 / §13.2).
 *
 * Read-only text per app: the `guests setup` command, the folder it writes
 * and the config stanza for a founder who would rather add the server to a
 * project of their own. It calls no route: setup writes files on this
 * computer, so the founder runs the CLI command (the setup route is excluded
 * from the web contract). The stanza mirrors `runtime/daemon/guest_setup.py`
 * `config_text`; it holds no secret.
 *
 * The web has no runtime-root read, so the folder keeps the documented
 * `<runtime>` placeholder. The port is the page's own loopback port when the
 * daemon serves this page, else the daemon default (8765).
 */
import { useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';

const DEFAULT_DAEMON_PORT = '8765';

function daemonPort(): string {
  if (import.meta.env.DEV || typeof window === 'undefined') return DEFAULT_DAEMON_PORT;
  const { protocol, hostname, port } = window.location;
  const loopback = hostname === '127.0.0.1' || hostname === 'localhost';
  return protocol === 'http:' && loopback && port ? port : DEFAULT_DAEMON_PORT;
}

export function codexGuestConfig(slug: string, port: string): string {
  return (
    '[mcp_servers.happyranch]\n' +
    `url = "http://127.0.0.1:${port}/api/v1/orgs/${slug}/guests/mcp"\n` +
    'default_tools_approval_mode = "approve"\n' +
    'tool_timeout_sec = 60\n'
  );
}

export function GuestAppsSection({ slug }: { slug: string }): JSX.Element {
  const { t } = useTranslation();
  const command = `happyranch guests setup codex_desktop --org ${slug}`;
  const folder = `<runtime>/orgs/${slug}/guests/codex_desktop/`;
  const stanza = codexGuestConfig(slug, daemonPort());
  return (
    <section className="mt-8" aria-labelledby="settings-guest-apps">
      <h3 id="settings-guest-apps" className="font-display mb-1 text-base font-semibold">
        {t('settings.guests.title')}
      </h3>
      <p className="text-text-secondary mb-4 text-sm">{t('settings.guests.description')}</p>
      <div className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4">
        <h4 className="text-text-primary mb-3 text-sm font-semibold">{t('settings.guests.app.codex')}</h4>
        <div className="flex flex-col gap-4">
          <CopyBlock label={t('settings.guests.command')} hint={t('settings.guests.commandHint')} value={command} />
          <CopyBlock label={t('settings.guests.folder')} hint={t('settings.guests.folderHint')} value={folder} />
          <CopyBlock label={t('settings.guests.stanza')} hint={t('settings.guests.stanzaHint')} value={stanza} multiline />
        </div>
      </div>
      <p className="text-text-secondary mt-3 text-xs">{t('settings.guests.claudeNote')}</p>
    </section>
  );
}

function CopyBlock({
  label,
  hint,
  value,
  multiline = false,
}: {
  label: string;
  hint: string;
  value: string;
  multiline?: boolean;
}): JSX.Element {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(value);
    setCopied(true);
  };
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-text-secondary text-xs font-medium">{label}</span>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={copy}
          aria-label={t('settings.guests.copyAria', { item: label })}
        >
          {copied ? t('settings.guests.copied') : t('settings.guests.copy')}
        </Button>
      </div>
      <pre
        className={`bg-surface-sunken border-border-default text-text-primary overflow-x-auto rounded-sm border px-2.5 py-2 font-mono text-xs ${multiline ? 'whitespace-pre' : 'whitespace-pre-wrap break-all'}`}
      >
        {value}
      </pre>
      <p className="text-text-secondary mt-1 text-xs">{hint}</p>
    </div>
  );
}
