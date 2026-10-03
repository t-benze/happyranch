/**
 * OnboardingPage — first-run / add-org shell (THR-061 Slice 11; THR-088
 * F-Step1 + F-Step2 + F-Prereqs).
 *
 * A presentational two-step onboarding shell:
 *   Step 1 — Connect your agentic CLI (ConnectRuntimeStep, THR-088 Slice A).
 *   Step 2 — Welcome → Create org → Creating → Success — plus a read-only
 *            broken-org list and an executor-prereq readiness panel.
 * This surface is GLOBAL (not org-scoped). It drives only EXISTING routes:
 *   - GET  /api/v1/orgs                         (listOrgs → { orgs, broken })
 *   - POST /api/v1/orgs                         (createOrg → { slug })
 *   - GET  /api/v1/health/prereqs               (getPrereqs → prereqs[])
 *   - POST /api/v1/auth/registration-token/runtime (Step-1 token mint)
 * No new backend route is added; the create flow reuses the same slug
 * contract + inline error mapping as the Sidebar's AddOrgDialog. Step 1 leads
 * first-run onboarding; a returning user adding another org starts at Step 2.
 *
 * THR-118 W2b translates all product-owned presentation reachable from this
 * route into en/zh-CN. User-entered slugs, registered tool names/paths, the
 * slug regex, broken-org raw errors and the generated copy-paste CLI payloads
 * (rendered by the shared ConnectFlow) stay byte-for-byte verbatim; mapped
 * daemon error categories keep their identity and re-translate on a locale
 * switch without resubmission.
 *
 * Honesty fence (THR-061 §D; THR-088): no invented metric/badge/role/$/version;
 * Pasture tokens only, zero raw hex; no Baloo 2. Gated/deferred surfaces are
 * OMITTED, never fabricated:
 *   - template picker (createOrg `from_example`) — G12 #312, founder-gated.
 *   - broken-org Retry action — gated (needs a reload route that does not
 *     exist; GET /orgs is cached, never re-scans). Broken orgs render
 *     read-only + Copy-error only.
 *   - executor `version` — the backend ExecutorPrereq model returns only
 *     {tool, present, path, hint}; no version field exists, so none is shown.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Check, Info } from 'lucide-react';
import { health as healthApi, orgs as orgsApi } from '@/lib/api';
import type { ExecutorPrereq } from '@/lib/api/types';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { useTranslation } from '@/hooks/i18n';
import { classifyAddOrgError, renderAddOrgError, type AddOrgError } from '@/lib/addOrgError';
import { ConnectRuntimeStep } from './ConnectRuntimeStep';

/** Same slug contract the daemon enforces (mirror of AddOrgDialog). */
const SLUG_RE = /^[a-z0-9-]{1,40}$/;

/** Step 1 = connect runtime, Step 2 = create org (welcome/create/success). */
type Phase = 'connect' | 'org';
type Step = 'welcome' | 'create' | 'success';

/* ------------------------------------------------------------------ */
/*  Shell — steps + broken-org list                                    */
/* ------------------------------------------------------------------ */

export function OnboardingPage(): JSX.Element {
  const [phase, setPhase] = useState<Phase | null>(null);
  const [step, setStep] = useState<Step>('welcome');
  const [createdSlug, setCreatedSlug] = useState<string | null>(null);

  // Dedupes with the app-wide ['orgs'] query key — same read the Sidebar and
  // RootRedirect consume, so this issues no extra fetch.
  const orgsQuery = useQuery({ queryKey: ['orgs'], queryFn: orgsApi.listOrgs });
  const broken = orgsQuery.data?.broken ?? [];
  const existingCount = orgsQuery.data?.orgs.length ?? 0;

  // Step 1 (connect runtime) leads first-run onboarding; a returning user
  // adding another org already has a runtime, so they land on Step 2. Once
  // the user acts (Continue/Skip), `phase` is set and sticks. Hold the render
  // until orgs load so the first-run vs returning choice doesn't flash.
  if (orgsQuery.isPending) {
    return <div className="bg-surface-canvas h-full" />;
  }
  const effectivePhase: Phase = phase ?? (existingCount === 0 ? 'connect' : 'org');

  if (effectivePhase === 'connect') {
    return (
      <div className="bg-surface-canvas h-full overflow-y-auto">
        <div className="max-w-content-form mx-auto p-6 sm:p-8">
          <ConnectRuntimeStep
            onContinue={() => setPhase('org')}
            onSkip={() => setPhase('org')}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      <div className="max-w-content-form mx-auto p-6 sm:p-8">
        {step === 'welcome' && (
          <WelcomeStep
            existingCount={existingCount}
            onStart={() => setStep('create')}
          />
        )}
        {step === 'create' && (
          <CreateStep
            onBack={() => setStep('welcome')}
            onCreated={(slug) => {
              setCreatedSlug(slug);
              setStep('success');
            }}
          />
        )}
        {step === 'success' && createdSlug && (
          <SuccessStep
            slug={createdSlug}
            onCreateAnother={() => {
              setCreatedSlug(null);
              setStep('create');
            }}
          />
        )}

        {/* Broken orgs are surfaced on the entry screens so they are never
            silently swallowed. Read-only: Copy-error only, Retry is gated. */}
        {step !== 'success' && broken.length > 0 && (
          <div className="mt-8">
            <BrokenOrgList broken={broken} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Step 1 — Welcome                                                    */
/* ------------------------------------------------------------------ */

function WelcomeStep({
  existingCount,
  onStart,
}: {
  existingCount: number;
  onStart: () => void;
}): JSX.Element {
  const { t, render } = useTranslation();
  const firstRun = existingCount === 0;
  return (
    <section className="pt-6 sm:pt-10">
      <RanchLogo className="text-brand-foreground h-14 w-14" />
      <p className="text-accent-text mt-5 text-xs font-semibold tracking-wider uppercase">
        {firstRun
          ? t('onboarding.welcome.eyebrow.firstRun')
          : t('onboarding.welcome.eyebrow.returning')}
      </p>
      <h1 className="font-display text-display text-text-primary mt-3 font-medium">
        {firstRun ? (
          <>
            {t('onboarding.welcome.title.firstRun')}
            <br />
            {t('onboarding.welcome.title.firstRunLine2')}
          </>
        ) : (
          t('onboarding.welcome.title.returning')
        )}
      </h1>
      <p className="text-text-secondary mt-3 max-w-lg text-base leading-relaxed">
        {render('onboarding.welcome.body.prefix', {
          orgTerm: (
            <span className="text-text-primary font-medium">
              {t('onboarding.welcome.body.orgTerm')}
            </span>
          ),
          tail: firstRun
            ? t('onboarding.welcome.body.firstRun')
            : t('onboarding.welcome.body.returning'),
        })}
      </p>
      <div className="mt-7 flex flex-wrap items-center gap-4">
        <Button onClick={onStart}>
          <Plus />
          {firstRun
            ? t('onboarding.welcome.cta.firstRun')
            : t('onboarding.welcome.cta.returning')}
        </Button>
        <span className="text-text-muted text-xs">{t('onboarding.welcome.timing')}</span>
      </div>
      <div className="border-border-default bg-surface-sunken mt-8 flex max-w-lg items-start gap-3 rounded-lg border p-4">
        <Info
          aria-hidden="true"
          size={17}
          className="text-text-muted mt-0.5 shrink-0"
        />
        <p className="text-text-secondary text-xs leading-relaxed">
          {render('onboarding.welcome.note', {
            emphasis: (
              <span className="text-text-primary font-semibold">
                {t('onboarding.welcome.note.emphasis')}
              </span>
            ),
            clis: (
              <>
                <span className="font-mono">claude</span>,{' '}
                <span className="font-mono">codex</span>,{' '}
                <span className="font-mono">node</span>
              </>
            ),
          })}
        </p>
      </div>
    </section>
  );
}

/** HappyRanch brand mark — mirrors the Sidebar Brandmark path at hero scale. */
function RanchLogo({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      viewBox="0 0 100 100"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      className={className}
    >
      <g transform="rotate(-7 50 44)">
        <path
          d="M50 26 C68 26 78 34 78 44 C78 54 66 60 50 60 C34 60 22 54 22 44 C22 34 32 26 50 26 Z"
          strokeWidth="6.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      <ellipse cx="41" cy="59" rx="6.2" ry="5" strokeWidth="5" />
      <path
        d="M44 63 C50 78 70 82 80 71 C85 65 83 59 77 60"
        strokeWidth="6.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Small inline glyphs (hand-rolled; keep lucide surface minimal)     */
/* ------------------------------------------------------------------ */

/** Plus glyph for the primary CTA (kept local to avoid icon churn). */
function Plus(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      className="h-4 w-4"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/** Copy glyph for the broken-org Copy-error affordance. */
function CopyGlyph(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
    >
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

/** Small red ✕ used for a missing executor tool. */
function XGlyph(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

/** Ring spinner (creating + prereq-checking affordances). */
function Spinner({ className, label }: { className?: string; label: string }): JSX.Element {
  return (
    <span
      role="status"
      aria-label={label}
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className ?? ''}`}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Step 2 — Create org (slug-only; reuses createOrg contract)         */
/*  Includes the distinct `creating` progress state (THR-088 F-Step2). */
/* ------------------------------------------------------------------ */

function CreateStep({
  onBack,
  onCreated,
}: {
  onBack: () => void;
  onCreated: (slug: string) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<AddOrgError | null>(null);
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: (body: { slug: string }) => orgsApi.createOrg(body),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['orgs'] });
      onCreated(resp.slug);
    },
    onError: (err: unknown, variables) => {
      // Store the error IDENTITY (not a rendered string) so a locale switch
      // re-translates without resubmitting, and the raw unknown detail stays
      // byte-for-byte (W2a review R1).
      setServerError(classifyAddOrgError(err, variables.slug));
    },
  });

  const valid = SLUG_RE.test(slug);
  const errorText = serverError ? renderAddOrgError(serverError, t) : null;

  const submit = (): void => {
    if (valid && !create.isPending) create.mutate({ slug });
  };

  // Distinct `creating` progress state — the workspace is being provisioned.
  if (create.isPending) {
    return <CreatingState slug={slug} />;
  }

  return (
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-8">
      <p className="text-accent-text text-xs font-semibold tracking-wider uppercase">
        {t('onboarding.create.eyebrow')}
      </p>
      <h1 className="font-display text-h1 text-text-primary mt-1.5 font-medium">
        {t('onboarding.create.heading')}
      </h1>

      <form
        className="mt-6 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Label htmlFor="onboarding-slug">{t('onboarding.create.slugLabel')}</Label>
        <p className="text-text-muted -mt-1 text-xs">
          {t('onboarding.create.slugHint')}
        </p>
        <Input
          id="onboarding-slug"
          value={slug}
          onChange={(e) => {
            setSlug(e.target.value);
            setServerError(null);
          }}
          placeholder={t('onboarding.create.slugPlaceholder')}
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={serverError ? true : undefined}
        />
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
          {valid ? (
            <span className="text-feedback-success inline-flex items-center gap-1 font-medium">
              <Check aria-hidden="true" size={13} />
              {t('onboarding.create.slugRule')}
            </span>
          ) : (
            <span className="text-text-muted">
              {t('onboarding.create.slugRule')}
            </span>
          )}
          <span className="text-text-muted font-mono">
            · ^[a-z0-9-]&#123;1,40&#125;$
          </span>
        </p>
        {errorText && (
          <p className="text-feedback-danger text-sm" role="alert">
            {errorText}
          </p>
        )}

        {/* Template picker (createOrg `from_example`) is founder-gated (G12
            #312) — omitted here, not stubbed. */}

        <ExecutorPrereqPanel />

        <div className="flex items-center gap-2 pt-4">
          <Button type="submit" disabled={!valid || create.isPending}>
            {t('onboarding.create.submit')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onBack}
            disabled={create.isPending}
          >
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </section>
  );
}

/** Centered progress card shown while POST /orgs is in flight. */
function CreatingState({ slug }: { slug: string }): JSX.Element {
  const { t, render } = useTranslation();
  return (
    <section
      aria-label={t('onboarding.creating.aria')}
      className="bg-surface border-border-default shadow-pasture-sm flex flex-col items-center rounded-lg border px-8 py-16 text-center"
    >
      <Spinner label={t('onboarding.loading')} className="text-accent h-8 w-8" />
      <h1 className="font-display text-h2 text-text-primary mt-5 font-medium">
        {render('onboarding.creating.heading', {
          slug: <span className="text-accent-text font-mono">{slug}</span>,
        })}
      </h1>
      <p className="text-text-secondary mt-2 text-sm">
        {t('onboarding.creating.body')}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Step 3 — Success                                                    */
/* ------------------------------------------------------------------ */

function SuccessStep({
  slug,
  onCreateAnother,
}: {
  slug: string;
  onCreateAnother: () => void;
}): JSX.Element {
  const { t, render } = useTranslation();
  const navigate = useNavigate();
  return (
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-8">
      <span
        aria-hidden="true"
        className="bg-feedback-success/15 text-feedback-success inline-flex h-11 w-11 items-center justify-center rounded-full"
      >
        <Check size={22} />
      </span>
      <h1 className="font-display text-h2 text-text-primary mt-4 font-medium">
        {render('onboarding.success.heading', {
          slug: <span className="text-accent-text font-mono">{slug}</span>,
        })}
      </h1>
      <p className="text-text-secondary mt-2 text-sm leading-relaxed">
        {t('onboarding.success.body')}
      </p>
      <div className="mt-6 flex items-center gap-2">
        <Button onClick={() => navigate(`/orgs/${slug}/dashboard`)}>
          {t('onboarding.success.enter', { slug })}
          <ArrowRight aria-hidden="true" />
        </Button>
        <Button variant="ghost" onClick={onCreateAnother}>
          {t('onboarding.success.createAnother')}
        </Button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Broken-org list — read-only diagnostics + Copy-error (Retry gated) */
/* ------------------------------------------------------------------ */

function BrokenOrgList({
  broken,
}: {
  broken: { slug: string; error: string }[];
}): JSX.Element {
  const { t, render } = useTranslation();
  return (
    <section className="border-feedback-warning/30 bg-feedback-warning/5 rounded-lg border p-5">
      <div className="flex items-center gap-2">
        <AlertTriangle
          aria-hidden="true"
          size={16}
          className="text-feedback-warning shrink-0"
        />
        <h2 className="text-text-primary text-sm font-semibold">
          {t('onboarding.broken.heading', { count: broken.length })}
        </h2>
      </div>
      <p className="text-text-muted mt-1 text-xs">
        {t('onboarding.broken.body')}
      </p>
      <ul className="mt-3 space-y-2">
        {broken.map((b) => (
          <BrokenOrgCard key={b.slug} slug={b.slug} error={b.error} />
        ))}
      </ul>
      <p className="text-text-muted mt-3 text-xs">
        {render('onboarding.broken.footer', {
          link: (
            <span className="text-text-primary font-medium">
              {t('onboarding.broken.createNewOrg')}
            </span>
          ),
        })}
      </p>
    </section>
  );
}

/** One broken-org card: slug + raw error + Copy-error (no Retry — gated). */
function BrokenOrgCard({
  slug,
  error,
}: {
  slug: string;
  error: string;
}): JSX.Element {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const copy = (): void => {
    void navigator.clipboard?.writeText(error);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <li className="bg-surface border-border-default rounded-md border p-3">
      <p className="text-text-primary font-mono text-sm">{slug}</p>
      <p className="text-feedback-danger mt-1 font-mono text-xs break-words">
        {error}
      </p>
      <div className="mt-2">
        <button
          type="button"
          onClick={copy}
          className="text-text-secondary hover:text-text-primary hover:border-border-strong border-border-default inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors"
        >
          {copied ? (
            <>
              <Check
                aria-hidden="true"
                size={13}
                className="text-feedback-success"
              />
              {t('onboarding.broken.copied')}
            </>
          ) : (
            <>
              <CopyGlyph />
              {t('onboarding.broken.copyError')}
            </>
          )}
        </button>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  Executor prereq readiness (THR-088 F-Prereqs)                       */
/*  GET /health/prereqs → {tool, present, path, hint}[] (no version).   */
/* ------------------------------------------------------------------ */

function ExecutorPrereqPanel(): JSX.Element | null {
  const { t } = useTranslation();
  const prereqsQuery = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    staleTime: 120_000, // 2 min — CLI presence doesn't change mid-session
    retry: 1,
  });

  // Checking affordance while the query is in flight.
  if (prereqsQuery.isPending) {
    return (
      <section
        aria-label={t('onboarding.prereqs.aria')}
        className="border-border-default bg-surface mt-4 rounded-md border px-3 py-2.5"
      >
        <p className="text-text-muted flex items-center gap-2 text-xs">
          <Spinner label={t('onboarding.loading')} className="text-text-muted h-3.5 w-3.5" />
          {t('onboarding.prereqs.checking')}
        </p>
      </section>
    );
  }

  // On error, degrade silently — this panel is informational only.
  if (prereqsQuery.isError) return null;

  const prereqs = prereqsQuery.data?.prereqs ?? [];
  if (prereqs.length === 0) return null;

  const presentCount = prereqs.filter((p) => p.present).length;
  const total = prereqs.length;
  // Fresh runtime with zero registered is expected — not a blocking state.
  const hasAnyRegistered = presentCount > 0;

  return (
    <section
      aria-label={t('onboarding.prereqs.aria')}
      className="border-border-default bg-surface mt-4 rounded-md border p-3"
    >
      {/* FE-computed 'X of Y tools registered' summary — real data, no fabrication. */}
      <div
        className={`flex items-center gap-1.5 rounded-md px-2.5 py-2 text-xs ${
          hasAnyRegistered
            ? 'text-feedback-success bg-feedback-success/10'
            : 'text-text-secondary bg-surface-sunken'
        }`}
      >
        {hasAnyRegistered ? (
          <Check
            aria-hidden="true"
            size={14}
            className="text-feedback-success shrink-0"
          />
        ) : (
          <Info aria-hidden="true" size={14} className="text-text-muted shrink-0" />
        )}
        <span>
          {t('onboarding.prereqs.summary', { present: presentCount, total })}
        </span>
      </div>

      <ul className="mt-2 space-y-1.5">
        {prereqs.map((p) => (
          <PrereqRow key={p.tool} prereq={p} />
        ))}
      </ul>
    </section>
  );
}

/** One executor row: icon + name + path/hint + registered/not-registered pill. */
function PrereqRow({ prereq }: { prereq: ExecutorPrereq }): JSX.Element {
  const { t } = useTranslation();
  const { tool, present, path, hint } = prereq;
  return (
    <li className="border-border-default bg-surface-sunken/40 flex items-center gap-2.5 rounded-md border px-2.5 py-2">
      <span
        aria-hidden="true"
        className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${
          present
            ? 'bg-feedback-success/15 text-feedback-success'
            : 'bg-feedback-danger/12 text-feedback-danger'
        }`}
      >
        {present ? <Check size={14} /> : <XGlyph />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-text-primary font-mono text-xs font-medium">{tool}</p>
        {/* Render the registered path when connected; the hint when not.
            No `version` — the backend model does not return one. */}
        {present ? (
          <p className="text-text-muted text-caption truncate font-mono">
            {path ?? t('onboarding.prereqs.connected')}
          </p>
        ) : (
          <p className="text-text-secondary text-caption leading-snug">
            {t('onboarding.prereqs.notRegistered', { hint })}
          </p>
        )}
      </div>
      <span
        className={`text-caption shrink-0 rounded-full px-2 py-0.5 font-semibold ${
          present
            ? 'text-status-open bg-tier-green-tint'
            : 'text-feedback-danger bg-tier-red-tint'
        }`}
      >
        {present ? t('onboarding.prereqs.registered') : t('onboarding.prereqs.notRegisteredPill')}
      </span>
    </li>
  );
}
