/**
 * OnboardingPage — first-run / add-org shell (THR-061 Slice 11).
 *
 * A presentational onboarding shell composed of three steps — Welcome →
 * Create org → Success — plus a read-only broken-org list. This surface is
 * GLOBAL (not org-scoped): it drives the two EXISTING non-org routes only:
 *   - GET  /api/v1/orgs   (listOrgs → { orgs, broken })
 *   - POST /api/v1/orgs   (createOrg → { slug })
 * No new backend route is added; the create flow reuses the same slug
 * contract + inline error mapping as the Sidebar's AddOrgDialog.
 *
 * Honesty fence (THR-061 §D): no invented metric/badge/role/$; Pasture tokens
 * only, zero raw hex; no Baloo 2. Gated/deferred surfaces are OMITTED, never
 * fabricated:
 *   - template picker (createOrg `from_example`) — G12 #312, founder-gated.
 *   - broken-org Retry action — gated; broken orgs render read-only.
 *   - executor-prereq readiness messaging — backend #314, shipped later as a
 *     separate PR; a clean seam is noted below, nothing is invented here.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Check, Info, Sparkles } from 'lucide-react';
import { health as healthApi, orgs as orgsApi } from '@/lib/api';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';

/** Same slug contract the daemon enforces (mirror of AddOrgDialog). */
const SLUG_RE = /^[a-z0-9-]{1,40}$/;

type Step = 'welcome' | 'create' | 'success';

/* ------------------------------------------------------------------ */
/*  Shell — three steps + broken-org list                              */
/* ------------------------------------------------------------------ */

export function OnboardingPage(): JSX.Element {
  const [step, setStep] = useState<Step>('welcome');
  const [createdSlug, setCreatedSlug] = useState<string | null>(null);

  // Dedupes with the app-wide ['orgs'] query key — same read the Sidebar and
  // RootRedirect consume, so this issues no extra fetch.
  const orgsQuery = useQuery({ queryKey: ['orgs'], queryFn: orgsApi.listOrgs });
  const broken = orgsQuery.data?.broken ?? [];
  const existingCount = orgsQuery.data?.orgs.length ?? 0;

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl p-6 sm:p-8">
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
            silently swallowed. Read-only: the Retry action is gated. */}
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
  return (
    <section>
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-8">
        <span
          aria-hidden="true"
          className="bg-accent-soft text-accent-text inline-flex h-11 w-11 items-center justify-center rounded-full"
        >
          <Sparkles size={20} />
        </span>
        <h1 className="font-display text-h1 text-text-primary mt-4 font-medium">
          Welcome to HappyRanch
        </h1>
        <p className="text-text-secondary mt-2 text-sm leading-relaxed">
          HappyRanch runs your AI agent organization — teams of agents that pick
          up tasks, collaborate in threads, and report back. An{' '}
          <span className="text-text-primary font-medium">org</span> is a single
          workspace with its own agents, tasks, and knowledge.
        </p>
        <p className="text-text-muted mt-3 text-sm">
          {existingCount > 0
            ? 'Add another org to run a separate workspace, or return to one from the sidebar.'
            : 'Create your first org to get started.'}
        </p>
        <div className="mt-6">
          <Button onClick={onStart}>
            {existingCount > 0 ? 'Create another org' : 'Create your first org'}
            <ArrowRight aria-hidden="true" />
          </Button>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Step 2 — Create org (slug-only; reuses createOrg contract)         */
/* ------------------------------------------------------------------ */

function CreateStep({
  onBack,
  onCreated,
}: {
  onBack: () => void;
  onCreated: (slug: string) => void;
}): JSX.Element {
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: (body: { slug: string }) => orgsApi.createOrg(body),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['orgs'] });
      onCreated(resp.slug);
    },
    onError: (err: unknown) => {
      const e = err as { code?: string; status?: number; message?: string };
      if (e.code === 'org_exists' || e.status === 409) {
        setServerError(`An org with slug "${slug}" already exists.`);
      } else if (e.code === 'invalid_slug') {
        setServerError('Slug must match ^[a-z0-9-]{1,40}$.');
      } else {
        setServerError(e.message ?? 'Could not create org.');
      }
    },
  });

  const valid = SLUG_RE.test(slug);

  const submit = (): void => {
    if (valid && !create.isPending) create.mutate({ slug });
  };

  return (
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-8">
      <PageHeader
        title="Create your organization"
        meta="Choose a slug — this becomes the workspace's stable id."
      />

      <form
        className="mt-6 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Label htmlFor="onboarding-slug">Slug</Label>
        <Input
          id="onboarding-slug"
          value={slug}
          onChange={(e) => {
            setSlug(e.target.value);
            setServerError(null);
          }}
          placeholder="e.g. hk-macau-tourism"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={serverError ? true : undefined}
        />
        <p className="text-text-muted text-xs">
          Lowercase letters, digits, and hyphens. 1–40 characters.
        </p>
        {serverError && (
          <p className="text-feedback-danger text-sm" role="alert">
            {serverError}
          </p>
        )}

        {/* Template picker (createOrg `from_example`) is founder-gated (G12
            #312) — omitted here, not stubbed. */}

        <ExecutorPrereqPanel />

        <div className="flex items-center gap-2 pt-4">
          <Button type="submit" disabled={!valid || create.isPending}>
            {create.isPending ? 'Creating…' : 'Create org'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onBack}
            disabled={create.isPending}
          >
            Back
          </Button>
        </div>
      </form>
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
        Organization created
      </h1>
      <p className="text-text-secondary mt-2 text-sm">
        <span className="text-text-primary font-medium">{slug}</span> is ready.
        Head to its dashboard to enrol agents and dispatch the first task.
      </p>
      <div className="mt-6 flex items-center gap-2">
        <Button onClick={() => navigate(`/orgs/${slug}/dashboard`)}>
          Enter {slug}
          <ArrowRight aria-hidden="true" />
        </Button>
        <Button variant="ghost" onClick={onCreateAnother}>
          Create another
        </Button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Broken-org list — read-only diagnostics (Retry is gated)           */
/* ------------------------------------------------------------------ */

function BrokenOrgList({
  broken,
}: {
  broken: { slug: string; error: string }[];
}): JSX.Element {
  return (
    <section className="border-feedback-warning/30 bg-feedback-warning/5 rounded-lg border p-5">
      <div className="flex items-center gap-2">
        <AlertTriangle
          aria-hidden="true"
          size={16}
          className="text-feedback-warning shrink-0"
        />
        <h2 className="text-text-primary text-sm font-semibold">
          {broken.length} org{broken.length === 1 ? '' : 's'} failed to load
        </h2>
      </div>
      <p className="text-text-muted mt-1 text-xs">
        These workspaces are on disk but the daemon could not load them. Resolve
        the error below from the CLI.
      </p>
      <ul className="mt-3 space-y-2">
        {broken.map((b) => (
          <li
            key={b.slug}
            className="bg-surface border-border-default rounded-md border p-3"
          >
            <p className="text-text-primary font-mono text-sm">{b.slug}</p>
            <p className="text-text-secondary mt-1 font-mono text-xs break-words">
              {b.error}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Executor prereq readiness (THR-061 backend #314)                    */
/* ------------------------------------------------------------------ */

function ExecutorPrereqPanel(): JSX.Element | null {
  const prereqsQuery = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    staleTime: 120_000, // 2 min — CLI presence doesn't change mid-session
    retry: 1,
  });

  // While loading or on error, show nothing — this is informational only.
  if (prereqsQuery.isPending || prereqsQuery.isError) return null;

  const prereqs = prereqsQuery.data?.prereqs ?? [];
  if (prereqs.length === 0) return null;

  const absent = prereqs.filter((p) => !p.present);
  // If every executor is present, show a compact success line.
  if (absent.length === 0) {
    return (
      <div
        aria-label="Executor readiness"
        className="border-border-default bg-surface mt-4 rounded-md border px-3 py-2"
      >
        <p className="text-text-muted flex items-center gap-1.5 text-xs">
          <Check aria-hidden="true" size={14} className="text-feedback-success shrink-0" />
          All executor CLIs found on PATH.
        </p>
      </div>
    );
  }

  // At least one executor is absent — show a diagnostically useful panel.
  return (
    <section
      aria-label="Executor readiness"
      className="border-feedback-warning/25 bg-feedback-warning/5 mt-4 rounded-md border p-3"
    >
      <div className="flex items-center gap-1.5">
        <Info aria-hidden="true" size={14} className="text-feedback-warning shrink-0" />
        <p className="text-text-primary text-xs font-medium">
          Executor readiness — {absent.length} missing
        </p>
      </div>
      <ul className="mt-2 space-y-1.5">
        {prereqs.map((p) => (
          <li key={p.tool} className="flex items-start gap-1.5">
            {p.present ? (
              <Check
                aria-hidden="true"
                size={13}
                className="text-feedback-success mt-px shrink-0"
              />
            ) : (
              <AlertTriangle
                aria-hidden="true"
                size={13}
                className="text-feedback-warning mt-px shrink-0"
              />
            )}
            <span className="text-text-secondary text-xs">
              <span
                className={
                  p.present ? 'text-text-primary font-medium' : 'text-text-primary'
                }
              >
                {p.tool}
              </span>
              {p.present ? ' — ready' : ` — not found. ${p.hint}`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
