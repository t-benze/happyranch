/**
 * FormField — label + input/textarea slot + inline error. Per DESIGN.md
 * `components.input` + `typography.label`. Used by all dialogs.
 *
 * The label is wired to the slotted input via `htmlFor`; pass a matching
 * `id` on the input element.
 */
import type { ReactNode } from 'react';

interface FormFieldProps {
  label: string;
  htmlFor: string;
  error?: string;
  children: ReactNode;
}

export function FormField({ label, htmlFor, error, children }: FormFieldProps): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={htmlFor}
        className="text-label font-medium tracking-wide text-text-muted"
      >
        {label}
      </label>
      {children}
      {error && (
        <p className="text-caption text-feedback-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
