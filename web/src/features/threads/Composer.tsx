import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { useSendFollowUp } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  disabled?: boolean;
  /** External ref-handle so KeyboardShortcuts can focus the composer via R. */
  registerFocus?: (focus: () => void) => void;
}

export function Composer({ threadId, disabled, registerFocus }: Props): JSX.Element {
  const slug = useOrgSlug();
  const [body, setBody] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const send = useSendFollowUp(slug, threadId);

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  const submit = async () => {
    if (!body.trim() || disabled) return;
    setErrorMsg(null);
    try {
      await send.mutateAsync({ body_markdown: body, addressed_to: ['@all'] });
      setBody('');
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMsg(describeError(err.code, `HTTP ${err.status}`));
      } else {
        setErrorMsg(String(err));
      }
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
        placeholder={disabled ? 'Thread is closed.' : 'Write a message… Ctrl+Enter to send.'}
        disabled={disabled || send.isPending}
        rows={4}
        aria-label="Compose follow-up"
        className="w-full resize-none rounded border border-border bg-bg-raised px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none disabled:opacity-50"
      />
      <div className="flex items-center justify-between">
        {errorMsg ? (
          <span className="text-xs text-tier-red">{errorMsg}</span>
        ) : (
          <span className="text-xs text-fg-subtle">
            Sends as <strong className="text-fg-muted">founder</strong>; @all by default.
          </span>
        )}
        <Button onClick={submit} disabled={disabled || !body.trim() || send.isPending}>
          {send.isPending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
