/**
 * Presentational pieces of the Create team / Create agent surface (THR-402
 * design v1.1). Props in, JSX out: every string arrives localized from
 * `AddAgentDialog`, which owns state, validation and submission.
 */
import { CircleAlert, CircleCheck, CircleHelp, Users, Waypoints } from 'lucide-react';
import type { ReactNode } from 'react';

export type NoteKind = 'info' | 'warn' | 'bad' | 'plain';

const NOTE_CLASS: Record<NoteKind, string> = {
  info: 'bg-info-soft border-transparent',
  warn: 'bg-attention-soft border-transparent',
  bad: 'bg-danger-soft border-transparent',
  plain: 'bg-surface-raised border-border-subtle',
};
const NOTE_TITLE: Record<NoteKind, string> = {
  info: 'text-text-primary',
  warn: 'text-attention-text',
  bad: 'text-danger',
  plain: 'text-text-primary',
};

/** A banner / inline notice: title, optional body and actions. */
export function Note({
  kind,
  title,
  children,
  actions,
  role,
  testId,
}: {
  kind: NoteKind;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: 'alert' | 'status';
  testId?: string;
}): JSX.Element {
  return (
    <div role={role} data-testid={testId} className={`rounded-md border px-3.5 py-3 text-sm leading-relaxed break-words ${NOTE_CLASS[kind]}`}>
      <p className={`font-semibold ${NOTE_TITLE[kind]}`}>{title}</p>
      {children && <div className="text-text-secondary">{children}</div>}
      {actions && <div className="relative mt-2.5 flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** Numbered group heading ("1 TEAM AND ROLE"). */
export function GroupLegend({ n, children, id }: { n: number; children: ReactNode; id?: string }): JSX.Element {
  return (
    <legend id={id} className="text-text-secondary flex items-center gap-2.5 p-0 text-xs font-bold tracking-wider uppercase">
      <span aria-hidden="true" className="bg-text-primary text-surface-canvas inline-grid h-5.5 w-5.5 place-items-center rounded-full text-xs tracking-normal">
        {n}
      </span>
      {children}
    </legend>
  );
}

/** Field label with a visible "Required" word (text, not colour). */
export function FieldLabel({ htmlFor, id, required, children, as = 'label' }: {
  htmlFor?: string;
  id?: string;
  required: string;
  children: ReactNode;
  as?: 'label' | 'legend';
}): JSX.Element {
  const body = (
    <>
      {children} <span className="text-text-muted text-xs font-semibold">{required}</span>
    </>
  );
  const cls = 'text-text-primary mb-1.5 flex items-baseline gap-2 p-0 text-sm font-semibold';
  return as === 'legend' ? (
    <legend id={id} className={cls}>{body}</legend>
  ) : (
    <label id={id} htmlFor={htmlFor} className={cls}>{body}</label>
  );
}

export function Help({ id, children }: { id?: string; children: ReactNode }): JSX.Element {
  return <p id={id} className="text-text-muted mt-1.5 text-xs leading-snug">{children}</p>;
}

/** Inline field error (icon + text); renders an empty live slot when clean. */
export function FieldError({ id, message }: { id: string; message: string | null }): JSX.Element {
  return (
    <div id={id} aria-live="polite">
      {message && (
        <p className="text-danger mt-1.5 flex items-start gap-1.5 text-xs font-semibold leading-snug">
          <CircleAlert aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 break-words">{message}</span>
        </p>
      )}
    </div>
  );
}

/** A radio card (outcome, team or executor option). */
export function OptionCard({
  id,
  name,
  value,
  checked,
  disabled,
  unavailable,
  title,
  detail,
  onChange,
}: {
  id: string;
  name: string;
  value: string;
  checked: boolean;
  disabled?: boolean;
  unavailable?: boolean;
  title: ReactNode;
  detail?: ReactNode;
  onChange: () => void;
}): JSX.Element {
  return (
    <label
      htmlFor={id}
      className={`border-border-strong bg-surface-canvas flex min-h-12 cursor-pointer items-start gap-3 rounded-md border px-3.5 py-3 has-checked:border-accent-default has-checked:bg-accent-soft has-checked:ring-1 has-checked:ring-inset has-checked:ring-accent-default has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent-default has-disabled:cursor-default ${unavailable ? 'border-danger border-dashed' : ''}`}
    >
      <input
        type="radio"
        id={id}
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className="accent-accent-default mt-0.5 h-4 w-4 shrink-0"
      />
      <span className="min-w-0">
        <span className="text-text-primary block text-sm font-semibold break-words">{title}</span>
        {detail && <span className="text-text-secondary mt-0.5 block text-xs break-words">{detail}</span>}
      </span>
    </label>
  );
}

/** "A new team starts with one new manager." (Create team only). */
export function FixedRelationship({ title, body }: { title: string; body: string }): JSX.Element {
  return (
    <div data-testid="create-role-fixed" className="bg-info-soft mt-3.5 flex items-start gap-3 rounded-md px-3.5 py-3">
      <Users aria-hidden="true" className="text-text-secondary mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-text-primary text-sm font-semibold">{title}</p>
        <p className="text-text-secondary text-xs">{body}</p>
      </div>
    </div>
  );
}

/** The live relationship line under the team choice. */
export function RelationshipLine({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p data-testid="create-relationship" className="text-text-secondary mt-2.5 flex items-center gap-2 text-xs break-words [&_b]:text-text-primary">
      <Waypoints aria-hidden="true" className="text-info h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/** Result panel icon. */
export function ResultIcon({ kind }: { kind: 'ok' | 'unknown' }): JSX.Element {
  return kind === 'ok' ? (
    <span aria-hidden="true" className="bg-tier-green-tint text-tier-green inline-grid h-11 w-11 place-items-center rounded-full">
      <CircleCheck className="h-5 w-5" />
    </span>
  ) : (
    <span aria-hidden="true" className="bg-attention-soft text-attention-text inline-grid h-11 w-11 place-items-center rounded-full">
      <CircleHelp className="h-5 w-5" />
    </span>
  );
}

/** One `<dt>/<dd>` row of the review rail or submitted details. */
export function ReviewRow({
  label,
  value,
  missing,
  error,
  edit,
}: {
  label: string;
  value: ReactNode;
  missing?: boolean;
  error?: boolean;
  edit?: ReactNode;
}): JSX.Element {
  return (
    <div className={`border-border-subtle relative border-t py-2.5 first:border-t-0 ${edit ? 'pe-14' : ''}`}>
      <dt className="text-text-secondary text-xs">{label}</dt>
      <dd className={`min-w-0 text-sm break-words ${error ? 'text-danger font-semibold' : missing ? 'text-text-muted italic' : 'text-text-primary'}`}>
        {value}
      </dd>
      {edit && <dd className="absolute end-0 top-1/2 -translate-y-1/2">{edit}</dd>}
    </div>
  );
}
