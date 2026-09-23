/**
 * ExecutorBinariesSection — machine-local executor binary-path registry UI
 * (THR-085 SLICE A, registration-only).
 *
 * For each of the four built-in executor kinds (claude, codex, pi, opencode)
 * this shows the currently-registered absolute path + validity from
 * GET /executor-binaries, and lets the operator REGISTER a path via MANUAL
 * absolute-path entry (validated via POST /executor-binaries/validate before,
 * and again server-side at POST /executor-binaries/register).
 *
 * Founder ruling THR-085 msg45: discovery is REGISTRATION-ONLY. There is NO
 * daemon auto-detect/scan — so this UI renders NO auto-detected-candidate
 * list. When a fresh env has nothing registered, we render an actionable
 * "register your CLI" blocked banner that names the remediation (the
 * manual-entry field below) — never an opaque failure.
 *
 * Authority: KB ADR `executor-path-registry-resolution`.
 */
import { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronRight, Terminal, XCircle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { ApiError } from '@/lib/api';
import {
  EXECUTOR_BINARY_KINDS,
  useExecutorBinaries,
  useRegisterExecutorBinary,
  useValidateExecutorBinary,
  type BinaryRegistryEntry,
  type ExecutorBinaryKind,
} from '@/hooks/executor-binaries';
import { useTranslation } from '@/hooks/i18n';

/** Extract a human-readable message from an ApiError (422 detail is a string)
 *  or any thrown value. */
function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail && typeof err.detail === 'object' && 'msg' in err.detail) {
      return String((err.detail as { msg: unknown }).msg);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

type Validity = 'valid' | 'invalid' | 'unregistered';

const VALIDITY_STYLE: Record<Validity, string> = {
  valid: 'text-status-open bg-tier-green-tint',
  invalid: 'text-status-abandoned bg-tier-red-tint',
  unregistered: 'text-status-archived border border-border-default bg-transparent',
};

const VALIDITY_LABEL_KEY = {
  valid: 'settings.executors.binaries.validity.valid',
  invalid: 'settings.executors.binaries.validity.invalid',
  unregistered: 'settings.executors.binaries.validity.unregistered',
} as const satisfies Record<Validity, string>;

function ValidityPill({ validity }: { validity: Validity }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-semibold tabular-nums ${VALIDITY_STYLE[validity]}`}
      data-testid="binary-validity"
      data-validity={validity}
    >
      {validity === 'valid' && (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />
      )}
      {t(VALIDITY_LABEL_KEY[validity])}
    </span>
  );
}

interface KindRowProps {
  kind: ExecutorBinaryKind;
  entry: BinaryRegistryEntry | undefined;
}

/** One kind's row: current path + validity, plus the manual-entry remediation
 *  (input → Validate → Register). */
function KindRow({ kind, entry }: KindRowProps): JSX.Element {
  const { t, render } = useTranslation();
  const registered = entry?.path != null;
  const validity: Validity = !registered
    ? 'unregistered'
    : entry?.valid
      ? 'valid'
      : 'invalid';

  const [path, setPath] = useState('');
  const [check, setCheck] = useState<{ valid: boolean; error: string | null } | null>(null);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const validate = useValidateExecutorBinary();
  const register = useRegisterExecutorBinary();
  const inputId = `binary-path-${kind}`;

  const trimmed = path.trim();
  const busy = validate.isPending || register.isPending;

  const onValidate = async () => {
    setRegisterError(null);
    setCheck(null);
    try {
      const res = await validate.mutateAsync({ path: trimmed });
      setCheck({ valid: res.valid, error: res.error });
    } catch (err) {
      setCheck({ valid: false, error: errMessage(err, t('settings.executors.binaries.validationFailed')) });
    }
  };

  const onRegister = async () => {
    setRegisterError(null);
    setCheck(null);
    try {
      await register.mutateAsync({ kind, path: trimmed });
      setPath('');
    } catch (err) {
      // The register route validates server-side and returns 422 with a
      // human-readable reason (not absolute / missing / not executable).
      setRegisterError(errMessage(err, t('settings.executors.binaries.registerFailed')));
    }
  };

  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`binary-row-${kind}`}
      data-kind={kind}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Terminal size={16} aria-hidden className="text-text-secondary shrink-0" />
          <span className="text-text-primary font-mono text-sm font-medium">{kind}</span>
        </div>
        <ValidityPill validity={validity} />
      </div>

      <div className="mt-2">
        {registered ? (
          <p className="text-text-secondary text-sm">
            {render('settings.executors.binaries.registeredPath', {
              path: (
                <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
                  {entry?.path}
                </code>
              ),
            })}
          </p>
        ) : (
          <p className="text-text-muted text-sm">
            {render('settings.executors.binaries.noPath', {
              kind: <span className="font-mono">{kind}</span>,
            })}
          </p>
        )}
      </div>

      {/* Manual absolute-path entry — DEMOTED to a secondary "Advanced"
          disclosure (THR-107 S3). The primary add path is the shared
          "Connect a CLI" flow; manual entry is kept as a management convenience
          (fast re-point of a moved binary). Still the only discovery mechanism
          — no auto-detect. */}
      <details className="group mt-3" data-testid={`binary-manual-${kind}`}>
        <summary className="text-text-secondary hover:text-text-primary flex cursor-pointer list-none items-center gap-1.5 text-xs">
          <ChevronRight
            aria-hidden="true"
            size={14}
            className="transition-transform group-open:rotate-90"
          />
          {t('settings.executors.binaries.advanced')}
        </summary>
        <div className="mt-3 space-y-2">
        <label
          htmlFor={inputId}
          className="text-label text-text-muted font-medium tracking-wide"
        >
          {registered
            ? t('settings.executors.binaries.updatePath')
            : t('settings.executors.binaries.registerPath')}
        </label>
        <div className="flex items-start gap-2">
          <Input
            id={inputId}
            value={path}
            onChange={(e) => {
              setPath(e.target.value);
              setCheck(null);
              setRegisterError(null);
            }}
            placeholder={`/absolute/path/to/${kind}`}
            className="font-mono"
            spellCheck={false}
            autoComplete="off"
          />
          <Button
            type="button"
            variant="secondary"
            onClick={onValidate}
            disabled={!trimmed || busy}
          >
            {validate.isPending
              ? t('settings.executors.binaries.validating')
              : t('settings.executors.binaries.validate')}
          </Button>
          <Button
            type="button"
            onClick={onRegister}
            disabled={!trimmed || busy}
          >
            {register.isPending
              ? t('settings.executors.binaries.registering')
              : t('settings.executors.binaries.register')}
          </Button>
        </div>

        {/* Inline validate result */}
        {check && (
          <p
            className={`flex items-center gap-1.5 text-sm ${check.valid ? 'text-status-open' : 'text-feedback-danger'}`}
            role="status"
            data-testid={`binary-check-${kind}`}
          >
            {check.valid ? (
              <CheckCircle2 size={14} aria-hidden />
            ) : (
              <XCircle size={14} aria-hidden />
            )}
            {check.valid
              ? t('settings.executors.binaries.checkValid')
              : (check.error ?? t('settings.executors.binaries.checkInvalid'))}
          </p>
        )}

        {/* Register failure (server-side validation) */}
        {registerError && (
          <p
            className="text-feedback-danger flex items-center gap-1.5 text-sm"
            role="alert"
            data-testid={`binary-register-error-${kind}`}
          >
            <XCircle size={14} aria-hidden />
            {registerError}
          </p>
        )}
        </div>
      </details>
    </div>
  );
}

export function ExecutorBinariesSection(): JSX.Element {
  const { t, render } = useTranslation();
  const query = useExecutorBinaries();

  const byKind = useMemo(() => {
    const map = new Map<string, BinaryRegistryEntry>();
    for (const e of query.data?.entries ?? []) map.set(e.kind, e);
    return map;
  }, [query.data]);

  // Fresh-env blocked state: nothing registered across ANY known kind.
  const anyRegistered = EXECUTOR_BINARY_KINDS.some((k) => byKind.get(k)?.path != null);

  return (
    <section className="space-y-4" data-testid="executor-binaries-section">
      <p className="text-text-secondary text-sm">
        {render('settings.executors.binaries.intro', {
          connect: <span className="font-medium">{t('settings.executors.connectCli')}</span>,
          advanced: (
            <span className="font-medium">{t('settings.executors.binaries.advanced')}</span>
          ),
        })}
      </p>

      {query.isLoading && (
        <p className="text-text-secondary text-sm">
          {t('settings.executors.binaries.loading')}
        </p>
      )}

      {query.isError && (
        <p className="text-feedback-danger text-sm" role="alert">
          {query.error?.message
            ? t('settings.executors.binaries.loadErrorDetail', {
                detail: query.error.message,
              })
            : t('settings.executors.binaries.loadError')}
        </p>
      )}

      {query.data && (
        <>
          {/* Actionable fresh-env "register your CLI" blocked banner. Never an
              opaque failure — it names the remediation (Connect a CLI below, or
              the per-row Advanced manual-entry disclosure). */}
          {!anyRegistered && (
            <div
              className="border-tier-red-tint bg-tier-red-tint/40 flex items-start gap-3 rounded-lg border p-4"
              role="alert"
              data-testid="fresh-env-blocked"
            >
              <AlertTriangle size={18} aria-hidden className="text-status-abandoned mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h3 className="text-text-primary text-sm font-semibold">
                  {t('settings.executors.binaries.fresh.title')}
                </h3>
                <p className="text-text-secondary text-sm">
                  {render('settings.executors.binaries.fresh.body', {
                    connect: (
                      <span className="font-medium">{t('settings.executors.connectCli')}</span>
                    ),
                    advanced: (
                      <span className="font-medium">
                        {t('settings.executors.binaries.advanced')}
                      </span>
                    ),
                    command: (
                      <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs">
                        which claude
                      </code>
                    ),
                  })}
                </p>
              </div>
            </div>
          )}

          <div className="space-y-3" data-testid="binary-rows">
            {EXECUTOR_BINARY_KINDS.map((kind) => (
              <KindRow key={kind} kind={kind} entry={byKind.get(kind)} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
