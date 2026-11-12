/**
 * CodingSection — Settings → Coding after U8b (THR-336).
 *
 * The editable Coding surface moved to Workflows → Setup (repo-free defaults).
 * This pane only points there and keeps the HISTORICAL repo-bearing
 * `workflow-coding-settings-command@1` saves recoverable through their existing
 * read-only original-key reader (GET /settings/coding?operation_key=KEY):
 * the historical send fence was provider-memory only (never persisted) and
 * nothing in this bundle can create one, so an earlier pending/uncertain save
 * is recovered by its ORIGINAL operation key, which the old page displayed.
 * Nothing here can send a PUT: the historical command is never converted,
 * re-keyed or resent, and an absent/error lookup proves nothing.
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import {
  useCodingSettingsActions,
  type CodingSettingsCurrent,
  type CodingSettingsReceipt,
} from '@/hooks/settings';
import { ApiError } from '@/lib/api';

type Lookup =
  | { kind: 'none' }
  | { kind: 'reading' }
  | { kind: 'committed'; receipt: CodingSettingsReceipt; current: CodingSettingsCurrent }
  | { kind: 'absent' }
  | { kind: 'error'; detail: string };

function detailOf(error: unknown): string {
  if (error instanceof ApiError) return error.code ?? `HTTP ${error.status}`;
  return error instanceof Error ? error.message : String(error);
}

export function CodingSection(): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  return <CodingMoved key={slug} orgSlug={slug} />;
}

function CodingMoved({ orgSlug }: { orgSlug: string }): JSX.Element {
  const { t } = useTranslation();
  const actions = useCodingSettingsActions();
  const active = useRef(true);
  useEffect(() => () => { active.current = false; }, []);
  const [manualKey, setManualKey] = useState('');
  const [manual, setManual] = useState<{ key: string; state: Lookup }>({ key: '', state: { kind: 'none' } });

  const read = async (key: string): Promise<Lookup> => {
    try {
      const view = await actions.readOperation(key);
      return view.operation && view.operation.operation_key === key
        ? { kind: 'committed', receipt: view.operation, current: view.current }
        : { kind: 'error', detail: 'operation_missing' };
    } catch (error) {
      return error instanceof ApiError && error.status === 404 && error.code === 'coding_settings_operation_not_found'
        ? { kind: 'absent' } : { kind: 'error', detail: detailOf(error) };
    }
  };
  const checkManual = async () => {
    const key = manualKey.trim();
    if (!key || manual.state.kind === 'reading') return;
    setManual({ key, state: { kind: 'reading' } });
    const next = await read(key);
    if (active.current) setManual({ key, state: next });
  };

  return (
    <section className="flex flex-col gap-4" aria-labelledby="coding-moved-title">
      <div className="border-border-default bg-bg-raised rounded-lg border p-4 sm:p-6">
        <h3 id="coding-moved-title" className="font-display text-text-primary text-lg font-semibold">{t('settings.coding.moved.title')}</h3>
        <p className="text-text-secondary mt-2 text-sm">{t('settings.coding.moved.body')}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button asChild size="sm">
            <Link to={`/orgs/${orgSlug}/workflows/setup`}>{t('settings.coding.moved.open')}</Link>
          </Button>
          <span className="text-text-muted text-xs">{t('settings.coding.moved.noTransfer')}</span>
        </div>
      </div>

      <div className="border-border-default rounded-lg border p-4 text-sm">
        <h3 className="text-text-primary font-medium">{t('settings.coding.moved.historyTitle')}</h3>
        <p className="text-text-muted mt-1">{t('settings.coding.moved.historyBody')}</p>
        <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void checkManual(); }}>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs" htmlFor="coding-original-key">
            <span className="text-text-muted">{t('settings.coding.moved.keyLabel')}</span>
            <input id="coding-original-key" value={manualKey} onChange={(e) => setManualKey(e.target.value)} spellCheck={false} autoComplete="off"
              className="bg-surface-sunken border-border-default text-text-primary min-w-0 rounded-lg border px-2 py-1 font-mono text-sm" />
          </label>
          <Button type="submit" size="sm" variant="outline" aria-disabled={!manualKey.trim() || manual.state.kind === 'reading'} className="aria-disabled:opacity-50">
            {t(manual.state.kind === 'reading' ? 'settings.coding.reconcile.reading' : 'settings.coding.reconcile.action')}
          </Button>
        </form>
        {manual.key && <p className="text-text-muted mt-2 font-mono text-xs break-all">{manual.key}</p>}
        <LookupResult state={manual.state} />
      </div>
    </section>
  );
}

function LookupResult({ state }: { state: Lookup }): JSX.Element | null {
  const { t } = useTranslation();
  if (state.kind === 'committed') {
    return <div className="mt-2">
      <p>{t('settings.coding.reconcile.committed', { receipt: state.receipt.snapshot.generation, current: state.current.generation })}</p>
      <p className="text-text-muted mt-1 text-xs">{t('settings.coding.moved.receiptFacts', {
        admission: state.receipt.snapshot.admission, published: state.receipt.snapshot.published_at,
      })}</p>
    </div>;
  }
  if (state.kind === 'absent') return <p className="mt-2">{t('settings.coding.moved.absent')}</p>;
  if (state.kind === 'error') return <p className="mt-2">{t('settings.coding.reconcile.error', { detail: state.detail })}</p>;
  return null;
}
