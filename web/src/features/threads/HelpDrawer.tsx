import { Modal } from '@/components/Modal';

const SHORTCUTS: Array<{ key: string; description: string }> = [
  { key: 'N', description: 'New thread' },
  { key: 'I', description: 'Invite participant' },
  { key: 'A', description: 'Archive thread' },
  { key: 'X', description: 'Abandon thread' },
  { key: 'F', description: 'Forward thread (compose new with quoted excerpt)' },
  { key: 'R', description: 'Focus composer' },
  { key: 'Ctrl+Enter', description: 'Send (in composer)' },
  { key: 'Esc', description: 'Close dialog' },
  { key: '?', description: 'Show this help' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export function HelpDrawer({ open, onClose }: Props): JSX.Element {
  return (
    <Modal title="Keyboard shortcuts" open={open} onClose={onClose}>
      <ul className="flex flex-col gap-1.5">
        {SHORTCUTS.map((s) => (
          <li key={s.key} className="flex items-center gap-3 text-sm">
            <kbd className="min-w-[5rem] rounded border border-border bg-bg-raised px-2 py-0.5 text-center font-mono text-xs text-fg">
              {s.key}
            </kbd>
            <span className="text-fg-muted">{s.description}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-fg-subtle">
        Shortcuts are disabled while focus is inside an input or textarea.
      </p>
    </Modal>
  );
}
