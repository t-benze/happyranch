export function AppBar({ title }: { title: string }): JSX.Element {
  return (
    <div className="border-border-default bg-surface-canvas/80 flex h-14 shrink-0 items-center gap-3.5 border-b px-6.5">
      <span className="text-text-primary font-display text-lg font-medium tracking-[-0.2px]">{title}</span>
    </div>
  );
}
