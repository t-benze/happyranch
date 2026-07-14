/**
 * SkillEditPage — edit + re-validate a user-authored custom skill
 * (THR-092 Slice 4 of 6). Route `/orgs/:slug/skills/:skillId/edit`, reached
 * from the Slice-2 detail page's Edit entry point — which appears ONLY for
 * custom (user_authored) skills. Bundled / system-contract skills are
 * read-only; if one is reached here directly, the form is not rendered.
 *
 * Flow (spec v3 §9.5):
 *   1. PREFILL — name / summary / version are seeded from the Slice-2 detail
 *      fetch. The detail fetch does NOT expose the raw SKILL.md body or the
 *      reference/asset maps, and the daemon PATCH preserves the stored SKILL.md
 *      when it is omitted — so a blank SKILL.md field KEEPS the current guidance
 *      and reference/asset editors are replacement maps (see `skills-edit`).
 *      There is NO policy_class control — an edit can never mint or alter a
 *      system contract (spec v3 §3.4).
 *   2. RE-VALIDATION RESULT — a PASS confirms + offers "View skill" (the Slice-2
 *      detail route). A FAILURE is NOT a dead end (spec v3 §9.1a): the edited
 *      draft is still persisted, so we show the mapped issues, the plain-language
 *      explanation of every check the validator runs, and "View skill" /
 *      "Re-validate".
 *   3. EDITED-EFFECTIVE — after a PASS that BUMPS the version, any agent for whom
 *      this skill was already effective is shown as assigned-but-not-yet-
 *      effective: the new version takes effect at that agent's next session
 *      (spec v3 §7.1). The provenance vocabulary is REUSED from `skills-detail`.
 *
 * Copy discipline (hard): guidance-visibility language throughout — NO
 * permission / approve / admit / grant / materialize / pending, and never a
 * user-facing "active". All copy + mapping lives in the unit-tested
 * `skills-edit` / `skills-create` / `skills-detail` modules.
 */
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BadgeCheck,
  CircleDashed,
  Info,
  Loader2,
  Lock,
  Pencil,
  TriangleAlert,
} from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import {
  useEditSkill,
  useSkillDetail,
  useValidateSkill,
  type EditSkillRequest,
  type EditSkillResponse,
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
  failureHeadline,
  isValidationPassed,
  plainValidationErrors,
} from './skills-create';
import {
  agentProvenanceList,
  isEditableSkill,
  readOnlyReason,
  type AgentEffectiveStatus,
} from './skills-detail';
import type { ValidationTone } from './skills-catalog';
import {
  buildEditSkillRequest,
  editFormErrors,
  editSuccessHeadline,
  effectiveAfterEdit,
  isVersionBumped,
  prefillEditForm,
  type EditSkillFormValues,
} from './skills-edit';

type SkillResult = EditSkillResponse | ValidateSkillResponse;

const STATUS_TONE: Record<AgentEffectiveStatus, ValidationTone> = {
  effective: 'positive',
  not_yet_effective: 'attention',
  not_assigned: 'neutral',
};

const TONE_CHIP: Record<ValidationTone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  attention: 'text-attention-text bg-attention-soft',
  neutral: 'text-fg-muted border border-border-default bg-transparent',
};

const TONE_ICON: Record<ValidationTone, typeof BadgeCheck> = {
  positive: BadgeCheck,
  attention: CircleDashed,
  neutral: CircleDashed,
};

export function SkillEditPage(): JSX.Element {
  const { slug, skillId } = useParams<{ slug: string; skillId: string }>();
  // Prefill source is the Slice-2 detail query (brief: prefill via
  // getSkillCatalogDetail) — reused, not re-fetched through a new endpoint.
  const query = useSkillDetail(skillId);
  const edit = useEditSkill();
  const revalidate = useValidateSkill();

  // Seed the form once the detail loads (and re-seed if the skillId changes).
  const [values, setValues] = useState<EditSkillFormValues>(prefillEditForm({}));
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<SkillResult | null>(null);

  const detail = query.data;
  useEffect(() => {
    if (detail && seededFor !== detail.skill_id) {
      setValues(
        prefillEditForm({
          name: detail.name,
          summary: detail.summary,
          version: detail.version,
        }),
      );
      setSeededFor(detail.skill_id);
    }
  }, [detail, seededFor]);

  const set = <K extends keyof EditSkillFormValues>(
    key: K,
    v: EditSkillFormValues[K],
  ) => setValues((prev) => ({ ...prev, [key]: v }));

  const pending = edit.isPending || revalidate.isPending;

  const backLink = (
    <Link
      to={`/orgs/${slug ?? ''}/skills/${encodeURIComponent(skillId ?? '')}`}
      className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"
    >
      <ArrowLeft size={15} aria-hidden="true" />
      Back to skill
    </Link>
  );

  // ── Load / error / read-only guards ─────────────────────────────────
  if (query.isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-5 md:px-7 md:py-6">
        {backLink}
        <div
          className="border-border-subtle bg-surface-subtle h-40 animate-pulse rounded-md border"
          aria-hidden="true"
        />
      </div>
    );
  }

  if (query.isError || !detail) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-5 md:px-7 md:py-6">
        {backLink}
        <EmptyState
          icon={<TriangleAlert size={28} />}
          title="Could not load this skill"
          body="This skill is unavailable right now, or the link is out of date."
        />
      </div>
    );
  }

  // A custom (user_authored) skill is the ONLY editable kind. Bundled /
  // system-contract skills are read-only — never regress the Slice-2 gating,
  // even when this route is reached directly.
  if (!isEditableSkill(detail)) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-5 break-words md:px-7 md:py-6">
        {backLink}
        <section
          className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6"
          aria-label="Read-only skill"
        >
          <div className="text-fg flex items-center gap-2 text-sm font-semibold">
            <Lock size={15} aria-hidden="true" className="text-fg-subtle" />
            This skill is read-only
          </div>
          <p className="text-fg-muted text-body-sm mt-2">
            {readOnlyReason(detail) ??
              'Only your own custom skills can be edited here.'}
          </p>
          <Link
            to={`/orgs/${slug ?? ''}/skills/${encodeURIComponent(detail.skill_id)}`}
            className="border-border-default bg-surface-subtle text-fg hover:bg-bg-subtle text-body-sm mt-4 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
          >
            View skill
          </Link>
        </section>
      </div>
    );
  }

  const baselineVersion = detail.version;
  const assignments = detail.assignments ?? [];

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    const errs = editFormErrors(values);
    setFormErrors(errs);
    if (errs.length > 0) return;
    try {
      const body = buildEditSkillRequest(values) as EditSkillRequest;
      const res = await edit.mutateAsync({ skillId: detail.skill_id, body });
      setResult(res);
    } catch {
      // A content-validation failure still resolves (draft persisted); only a
      // malformed request (422) or transport error lands here.
      setSubmitError(
        'Could not save the changes — the request could not be completed. Check the required details and try again.',
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

  const passed = result ? isValidationPassed(result) : false;
  const issues = result ? plainValidationErrors(result.validation?.errors) : [];
  const resultVersion =
    result && 'version' in result ? (result.version as string) : baselineVersion;
  const versionBumped = passed && isVersionBumped(baselineVersion, resultVersion);
  const provenance = agentProvenanceList(
    effectiveAfterEdit(assignments, versionBumped),
  );
  const detailPath = `/orgs/${slug ?? ''}/skills/${encodeURIComponent(detail.skill_id)}`;

  return (
    // `break-words` (inherited overflow-wrap) keeps long mono slugs/paths from
    // forcing horizontal overflow of the content region on narrow viewports
    // (the AppShell rail does not collapse at 390px — MEM-081/084).
    <div className="mx-auto w-full max-w-3xl px-4 py-5 break-words md:px-7 md:py-6">
      {backLink}

      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6">
        <div className="flex items-start gap-3">
          {/* Decorative — hidden on the narrowest widths so the prose title
              keeps the full (already shell-squeezed) content width and wraps
              per word rather than per character (MEM-084). */}
          <span className="bg-accent-soft text-accent-text mt-0.5 hidden h-9 w-9 shrink-0 items-center justify-center rounded-md sm:flex">
            <Pencil size={18} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="text-h2 text-fg">Edit a custom skill</h1>
            <p className="text-fg-muted text-body-sm mt-1 max-w-xl leading-relaxed">
              Update this skill’s guidance. Changes are checked for technical
              correctness and saved as an editable draft — a failed check never
              loses your work.
            </p>
            <p className="text-fg-subtle text-mono-sm mt-2 break-all">
              {detail.skill_id} · v{baselineVersion}
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
          <div>
            <FieldLabel htmlFor="skill-version" hint="bump to take effect next session">
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
          <FieldLabel htmlFor="skill-md" hint="leave blank to keep the current guidance">
            SKILL.md
          </FieldLabel>
          <Textarea
            id="skill-md"
            rows={10}
            className="font-mono text-sm leading-relaxed"
            placeholder={
              'Leave blank to keep the current guidance, or paste the full updated SKILL.md to replace it.'
            }
            value={values.skillMd}
            onChange={(e) => set('skillMd', e.target.value)}
          />
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <FileMapEditor
            idBase="skill-ref"
            legend="Reference"
            hint="Optional — replaces the skill’s reference files with what you list here."
            namePlaceholder="notes.md"
            entries={values.references}
            onChange={(next) => set('references', next)}
          />
          <FileMapEditor
            idBase="skill-asset"
            legend="Asset"
            hint="Optional — replaces the skill’s asset files with what you list here."
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
              shell (MEM-084); auto width from `sm` up. */}
          <Button
            type="submit"
            disabled={pending}
            className="h-auto min-h-9 w-full py-1.5 text-center whitespace-normal sm:w-auto"
          >
            {edit.isPending ? (
              <Loader2 size={15} aria-hidden="true" className="animate-spin" />
            ) : (
              <BadgeCheck size={15} aria-hidden="true" />
            )}
            {edit.isPending ? 'Saving…' : 'Save & re-validate'}
          </Button>
          <span className="text-fg-subtle text-body-sm">
            A failed check keeps your edited draft in the catalog — nothing is
            lost.
          </span>
        </div>
      </form>

      {/* ── Re-validation result ──────────────────────────────── */}
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
              {result.skill_id} · v{resultVersion}
            </span>
          </div>

          <p className="text-fg mt-3 text-sm font-semibold">
            {passed
              ? editSuccessHeadline(versionBumped)
              : failureHeadline(issues.length)}
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

          {/* Edited-effective: after a version-bumping pass, already-effective
              agents move to "takes effect next session" (spec v3 §7.1). Reuses
              the Slice-2 provenance vocabulary, never a parallel copy. */}
          {passed && provenance.length > 0 && (
            <div className="border-border-subtle mt-4 border-t pt-4">
              <Eyebrow>Per-agent effect</Eyebrow>
              <p className="text-fg-muted text-body-sm mb-3">
                Where this skill is shown as guidance now that it is saved.
                Editing changes what agents are shown — it does not change their
                tools or commands.
              </p>
              <ul className="flex flex-col gap-2.5">
                {provenance.map((p) => {
                  const tone = STATUS_TONE[p.status];
                  const Icon = TONE_ICON[tone];
                  return (
                    <li
                      key={p.agent}
                      data-agent={p.agent}
                      data-status={p.status}
                      className="border-border-subtle bg-surface-raised flex flex-wrap items-start justify-between gap-x-4 gap-y-2 rounded-md border p-3"
                    >
                      <div className="min-w-0">
                        <div className="text-fg font-mono text-sm font-semibold break-all">
                          {p.agent}
                        </div>
                        <p className="text-fg-muted text-body-sm mt-1">
                          {p.reason}
                        </p>
                      </div>
                      <span
                        className={`text-mono-sm inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${TONE_CHIP[tone]}`}
                      >
                        <Icon size={11} aria-hidden="true" className="shrink-0" />
                        {p.statusLabel}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

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
