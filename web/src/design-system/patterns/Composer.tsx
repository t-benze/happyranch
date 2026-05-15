/**
 * Composer — sticky-bottom textarea + helper line + Send button. Per
 * DESIGN.md `components.textarea` + `components.button.primary`. Used at
 * the foot of the threads detail pane.
 *
 * Pure prop-driven: the composition owns the mutation; the pattern just
 * gathers the markdown body and calls `onSend(markdown)`. Ctrl+Enter sends.
 */
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';

interface ComposerProps {
  disabled?: boolean;
  pending?: boolean;
  /** Optional error message (typically from a failed send). */
  errorMessage?: string | null;
  /** Helper text shown below the textarea when no error is set. */
  helper?: string;
  /** Placeholder for the textarea. Defaults to a reasonable copy. */
  placeholder?: string;
  /**
   * Called with the markdown when the user presses Send or Ctrl+Enter.
   * May return a Promise; if it rejects (or a sync impl throws), the draft is
   * preserved so the user can retry without retyping.
   */
  onSend: (markdown: string) => unknown | Promise<unknown>;
  /** Lets a parent focus the textarea (e.g. the R keyboard shortcut). */
  registerFocus?: (focus: () => void) => void;
}

export function Composer({
  disabled,
  pending,
  errorMessage,
  helper,
  placeholder,
  onSend,
  registerFocus,
}: ComposerProps): JSX.Element {
  const [body, setBody] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  const submit = async () => {
    if (!body.trim() || disabled || pending) return;
    try {
      await onSend(body);
      setBody('');
    } catch {
      // Composition surfaces the failure via the errorMessage prop; preserve
      // the draft so the user can fix and retry without retyping.
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={
          placeholder ?? (disabled ? 'Thread is closed.' : 'Write a message… Ctrl+Enter to send.')
        }
        disabled={disabled || pending}
        rows={4}
        aria-label="Compose follow-up"
        className="w-full resize-none rounded-md border border-border-default bg-surface-raised px-3 py-2 text-body-lg text-text-primary placeholder:text-text-muted focus:border-accent-default focus:outline-none disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-2">
        {errorMessage ? (
          <span className="text-caption text-feedback-danger">{errorMessage}</span>
        ) : (
          <span className="text-caption text-text-muted">{helper ?? ''}</span>
        )}
        <Button onClick={submit} disabled={disabled || !body.trim() || pending}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
