/**
 * SkillCreatePage — Add / Import a custom skill + the validation-result
 * surface (THR-092 Slice 3 of 6). Reached from the "Add custom skill" action
 * on the Catalog page; route `/orgs/:slug/skills/new`.
 *
 * Flow (spec v3 §9.1):
 *   1. AUTHOR/IMPORT — a form that POSTs a CreateSkillRequest via
 *      `createSkill`: slug, name, optional version/summary, the SKILL.md body,
 *      and optional reference/asset files (name→content). A custom skill is
 *      user_authored ONLY — there is NO control that sets policy_class, so the
 *      UI can never ask the backend to mint a system_contract (spec v3 §3.4).
 *   2. VALIDATION RESULT — render the response. A technical-validation PASS
 *      confirms + offers "View skill" (the Slice-2 detail route). A FAILURE is
 *      NOT a dead end (spec v3 §9.1a): the draft is still persisted
 *      (`skill_id`), so we show the mapped issues, a plain-language explanation
 *      of every check the validator runs, and "View skill" / "Re-validate".
 *
 * Copy discipline (hard): guidance-visibility language throughout — NO
 * permission / approve / admit / grant / materialize / pending, and never a
 * user-facing "active". All copy + mapping lives in the unit-tested
 * `skills-create` module; assignment (Slice 5) and the edit page (Slice 4) are
 * out of scope — this surface only LINKS to the detail stub.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, BadgeCheck, Info, Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import {
  useCreateSkill,
  useValidateSkill,
  type CreateSkillRequest,
  type CreateSkillResponse,
  type ValidateSkillResponse,
} from '@/hooks/skills';
import { SkillStatusBadge } from './SkillStatusBadge';
import {
  Eyebrow,
  FieldLabel,
  FileMapEditor,
  ValidationChecklist,
} from './skills-form-parts';
import {
  buildCreateSkillRequest,
  createFormErrors,
  emptyCreateSkillForm,
  failureHeadline,
  isValidationPassed,
  plainValidationErrors,
  successHeadline,
  type CreateSkillFormValues,
} from './skills-create';

type SkillResult = CreateSkillResponse | ValidateSkillResponse;

export function SkillCreatePage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const create = useCreateSkill();
  const revalidate = useValidateSkill();

  const [values, setValues] = useState<CreateSkillFormValues>(
    emptyCreateSkillForm(),
  );
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<SkillResult | null>(null);

  const set = <K extends keyof CreateSkillFormValues>(
    key: K,
    v: CreateSkillFormValues[K],
  ) => setValues((prev) => ({ ...prev, [key]: v }));

  const pending = create.isPending || revalidate.isPending;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    const errs = createFormErrors(values);
    setFormErrors(errs);
    if (errs.length > 0) return;
    try {
      const body = buildCreateSkillRequest(values) as CreateSkillRequest;
      const res = await create.mutateAsync(body);
      setResult(res);
    } catch {
      // A content-validation failure still resolves (draft persisted); only a
      // malformed request (422) or transport error lands here.
      setSubmitError(
        'Could not save the skill — the request could not be completed. Check the required details and try again.',
      );
    }
  };

  const onRevalidate = async () => {
    if (!result) return;
    setSubmitError(null);
    try {
      const res = await revalidate.mutateAsync({ skillId: result.skill_id });
      setResult(res);
    } catch {
      setSubmitError('Could not re-run validation right now. Try again shortly.');
    }
  };

  const backLink = (
    <Link
      to={`/orgs/${slug ?? ''}/skills`}
      className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"
    >
      <ArrowLeft size={15} aria-hidden="true" />
      Back to skills
    </Link>
  );

  const passed = result ? isValidationPassed(result) : false;
  const issues = result ? plainValidationErrors(result.validation?.errors) : [];
  const detailPath = result
    ? `/orgs/${slug ?? ''}/skills/${encodeURIComponent(result.skill_id)}`
    : '';

  return (
    // `break-words` (inherited overflow-wrap) keeps long mono slugs/paths from
    // forcing horizontal overflow of the content region on narrow viewports
    // (the AppShell rail does not collapse at 390px — MEM-081).
    <div className="mx-auto w-full max-w-3xl px-4 py-5 break-words md:px-7 md:py-6">
      {backLink}

      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6">
        <div className="flex items-start gap-3">
          {/* Decorative — hidden on the narrowest widths so the prose title
              keeps the full (already shell-squeezed) content width and wraps
              per word rather than per character (MEM-081). */}
          <span className="bg-accent-soft text-accent-text mt-0.5 hidden h-9 w-9 shrink-0 items-center justify-center rounded-md sm:flex">
            <Sparkles size={18} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="text-h2 text-fg">Add a custom skill</h1>
            <p className="text-fg-muted text-body-sm mt-1 max-w-xl leading-relaxed">
              Author guidance for your own agents, or import it as files. Custom
              skills are checked for technical correctness before agents are
              shown them.
            </p>
          </div>
        </div>
      </header>

      {/* ── Form ───────────────────────────────────────────────── */}
      <form
        onSubmit={onSubmit}
        noValidate
        className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor="skill-slug" required hint="lowercase, hyphenated">
              Slug / id
            </FieldLabel>
            <Input
              id="skill-slug"
              className="font-mono"
              placeholder="incident-postmortem"
              value={values.slug}
              onChange={(e) => set('slug', e.target.value)}
            />
          </div>
          <div>
            <FieldLabel htmlFor="skill-version" hint="optional">
              Version
            </FieldLabel>
            <Input
              id="skill-version"
              className="font-mono"
              placeholder="v1"
              value={values.version}
              onChange={(e) => set('version', e.target.value)}
            />
          </div>
        </div>

        <div className="mt-4">
          <FieldLabel htmlFor="skill-name" required hint="human-readable">
            Name
          </FieldLabel>
          <Input
            id="skill-name"
            placeholder="Incident postmortem"
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
          />
        </div>

        <div className="mt-4">
          <FieldLabel htmlFor="skill-summary" hint="optional — one line">
            Summary
          </FieldLabel>
          <Input
            id="skill-summary"
            placeholder="What this guidance is for, in a sentence."
            value={values.summary}
            onChange={(e) => set('summary', e.target.value)}
          />
        </div>

        <div className="mt-4">
          <FieldLabel htmlFor="skill-md" required hint="the guidance itself">
            SKILL.md
          </FieldLabel>
          <Textarea
            id="skill-md"
            rows={10}
            className="font-mono text-sm leading-relaxed"
            placeholder={
              '# Incident postmortem\n\n## When to use\nAfter any production incident.\n\n## Steps\n- Reconstruct the timeline from the audit trail.'
            }
            value={values.skillMd}
            onChange={(e) => set('skillMd', e.target.value)}
          />
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <FileMapEditor
            idBase="skill-ref"
            legend="Reference"
            hint="Optional — extra files the guidance points to."
            namePlaceholder="notes.md"
            entries={values.references}
            onChange={(next) => set('references', next)}
          />
          <FileMapEditor
            idBase="skill-asset"
            legend="Asset"
            hint="Optional — images or data the skill bundles."
            namePlaceholder="diagram.svg"
            entries={values.assets}
            onChange={(next) => set('assets', next)}
          />
        </div>

        {/* Inline required-field guard */}
        {formErrors.length > 0 && (
          <div
            className="border-danger/40 bg-danger-soft text-danger text-body-sm mt-5 rounded-md border p-3"
            role="alert"
          >
            <ul className="list-disc space-y-0.5 pl-5">
              {formErrors.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        )}

        {submitError && (
          <div
            className="border-danger/40 bg-danger-soft text-danger text-body-sm mt-5 rounded-md border p-3"
            role="alert"
          >
            {submitError}
          </div>
        )}

        <div className="border-border-subtle mt-6 flex flex-wrap items-center gap-3 border-t pt-4">
          {/* Full-width + wrappable on mobile so the nowrap primitive label
              never forces horizontal overflow inside the un-collapsed 390px
              shell (MEM-081); auto width from `sm` up. */}
          <Button
            type="submit"
            disabled={pending}
            className="h-auto min-h-9 w-full py-1.5 text-center whitespace-normal sm:w-auto"
          >
            {create.isPending ? (
              <Loader2 size={15} aria-hidden="true" className="animate-spin" />
            ) : (
              <BadgeCheck size={15} aria-hidden="true" />
            )}
            {create.isPending ? 'Validating…' : 'Validate & save'}
          </Button>
          <span className="text-fg-subtle text-body-sm">
            A failed check keeps an editable draft in the catalog — nothing is
            lost.
          </span>
        </div>
      </form>

      {/* ── Validation result ─────────────────────────────────── */}
      {result && (
        <section
          className={`mt-4 rounded-md border p-5 md:p-6 ${
            passed
              ? 'border-status-open/40 bg-tier-green-tint'
              : 'border-attention/40 bg-attention-soft'
          }`}
          aria-label="Validation result"
          data-result={passed ? 'validated' : 'failed_validation'}
        >
          <div className="flex flex-wrap items-center gap-2">
            <SkillStatusBadge
              state={
                (passed ? 'validated' : 'failed_validation') as
                  | 'in_catalog'
                  | 'validated'
                  | 'failed_validation'
              }
            />
            <span className="text-fg-subtle text-mono-sm break-all">
              {result.skill_id}
            </span>
          </div>

          <p className="text-fg mt-3 text-sm font-semibold">
            {passed ? successHeadline() : failureHeadline(issues.length)}
          </p>

          {!passed && issues.length > 0 && (
            <div className="mt-3">
              <Eyebrow>What to fix</Eyebrow>
              <ul className="text-fg-muted text-body-sm list-disc space-y-1 pl-5">
                {issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Guidance: every technical check, in plain language (failure only). */}
          {!passed && <ValidationChecklist />}

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Link
              to={detailPath}
              className="border-border-default bg-surface-subtle text-fg hover:bg-bg-subtle text-body-sm inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
            >
              View skill
            </Link>
            {!passed && (
              <button
                type="button"
                onClick={onRevalidate}
                disabled={pending}
                className="border-border-default text-fg-muted hover:bg-bg-subtle text-body-sm inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold disabled:opacity-50"
              >
                {revalidate.isPending ? (
                  <Loader2 size={14} aria-hidden="true" className="animate-spin" />
                ) : (
                  <BadgeCheck size={14} aria-hidden="true" />
                )}
                {revalidate.isPending ? 'Re-validating…' : 'Re-validate'}
              </button>
            )}
          </div>
        </section>
      )}

      {/* ── Guidance-only footer ──────────────────────────────── */}
      <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mt-4 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
        <Info size={15} aria-hidden="true" className="text-fg-subtle shrink-0" />
        <span>
          <b className="text-fg font-semibold">Guidance visibility only.</b>{' '}
          Skills shape what an agent is told — they do not change available
          tools or commands.
        </span>
      </div>
    </div>
  );
}
