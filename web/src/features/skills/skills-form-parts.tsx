/**
 * Shared presentational parts for the custom-skill AUTHOR (Slice 3) and EDIT
 * (Slice 4) forms + validation-result surfaces (THR-092). Extracted verbatim
 * from the merged Slice-3 SkillCreatePage so both pages compose the SAME
 * structure instead of forking a parallel copy. Purely presentational — every
 * user-facing string is passed in by the caller or comes from the unit-tested
 * `skills-create` module, so the copy discipline is enforced where the text
 * lives, not here.
 */
import { Check, ListChecks, Plus, X } from 'lucide-react';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { VALIDATION_CHECKS, type NamedFileEntry } from './skills-create';

/** Uppercase section caption — matches the Slice-2 detail eyebrow styling. */
export function Eyebrow({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="text-fg-subtle text-overline mb-2 tracking-wider uppercase">
      {children}
    </div>
  );
}

/** Label row with a required marker + optional hint, mirroring the mockup. */
export function FieldLabel({
  htmlFor,
  children,
  required,
  hint,
}: {
  htmlFor: string;
  children: React.ReactNode;
  required?: boolean;
  hint?: string;
}): JSX.Element {
  return (
    <Label htmlFor={htmlFor} className="mb-1.5 block">
      {children}
      {required && <span className="text-danger ml-0.5">*</span>}
      {hint && <span className="text-fg-subtle ml-2 font-normal">{hint}</span>}
    </Label>
  );
}

/** Repeatable name→content editor for the optional references / assets maps. */
export function FileMapEditor({
  idBase,
  legend,
  hint,
  namePlaceholder,
  entries,
  onChange,
}: {
  idBase: string;
  legend: string;
  hint: string;
  namePlaceholder: string;
  entries: NamedFileEntry[];
  onChange: (next: NamedFileEntry[]) => void;
}): JSX.Element {
  const setRow = (i: number, patch: Partial<NamedFileEntry>) =>
    onChange(entries.map((e, idx) => (idx === i ? { ...e, ...patch } : e)));
  const addRow = () => onChange([...entries, { name: '', content: '' }]);
  const removeRow = (i: number) =>
    onChange(entries.filter((_, idx) => idx !== i));

  return (
    <div>
      <Eyebrow>{legend}</Eyebrow>
      <p className="text-fg-subtle text-body-sm mb-2">{hint}</p>
      {entries.length > 0 && (
        <ul className="mb-2 flex flex-col gap-3">
          {entries.map((e, i) => (
            <li
              key={i}
              className="border-border-subtle bg-surface-subtle rounded-md border p-3"
            >
              <div className="flex items-center gap-2">
                <Input
                  id={`${idBase}-name-${i}`}
                  className="font-mono"
                  aria-label={`${legend} file name ${i + 1}`}
                  placeholder={namePlaceholder}
                  value={e.name}
                  onChange={(ev) => setRow(i, { name: ev.target.value })}
                />
                <button
                  type="button"
                  onClick={() => removeRow(i)}
                  aria-label={`Remove ${legend} file ${i + 1}`}
                  className="text-fg-subtle hover:text-fg hover:bg-bg-subtle shrink-0 rounded-md p-1.5"
                >
                  <X size={15} aria-hidden="true" />
                </button>
              </div>
              <Textarea
                id={`${idBase}-content-${i}`}
                rows={3}
                className="mt-2 font-mono text-sm"
                aria-label={`${legend} file content ${i + 1}`}
                placeholder="File content…"
                value={e.content}
                onChange={(ev) => setRow(i, { content: ev.target.value })}
              />
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        onClick={addRow}
        className="border-border-default text-fg-muted hover:bg-bg-subtle text-body-sm inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 font-medium"
      >
        <Plus size={14} aria-hidden="true" />
        Add {legend.toLowerCase()} file
      </button>
    </div>
  );
}

/** The always-available explanation of what the technical validator checks —
 *  guidance, not a gate. Rendered inside the failure result so the operator
 *  understands every check (spec v3 §8.3). */
export function ValidationChecklist(): JSX.Element {
  return (
    <div className="border-border-subtle bg-surface-subtle mt-4 rounded-md border p-4">
      <div className="text-fg mb-1 flex items-center gap-2 text-sm font-semibold">
        <ListChecks size={15} aria-hidden="true" className="text-fg-subtle" />
        What validation checks
      </div>
      <p className="text-fg-muted text-body-sm mb-3">
        Validation is a technical correctness check — it confirms the package is
        well-formed, never a review of what the guidance says. Every check:
      </p>
      <ul className="flex flex-col gap-2.5">
        {VALIDATION_CHECKS.map((c) => (
          <li key={c.key} className="flex items-start gap-2.5">
            <Check
              size={15}
              aria-hidden="true"
              className="text-fg-subtle mt-0.5 shrink-0"
            />
            <div className="min-w-0">
              <div className="text-fg text-body-sm font-semibold">{c.title}</div>
              <p className="text-fg-muted text-body-sm">{c.description}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
