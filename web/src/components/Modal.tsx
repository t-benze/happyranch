import { useEffect } from 'react';

interface ModalProps {
  title: string;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
}

export function Modal({ title, open, onClose, children }: ModalProps): JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-lg border border-border bg-bg-subtle p-5 shadow-xl">
        <header className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-fg-muted hover:text-fg"
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div>{children}</div>
      </div>
    </div>
  );
}
