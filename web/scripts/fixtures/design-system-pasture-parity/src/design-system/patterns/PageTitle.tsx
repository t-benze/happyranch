export function PageTitle({ title }: { title: string }): JSX.Element {
  return (
    <div>
      <h1
        className={`font-display text-page-title text-text-primary${''}`}
      >
        {title}
      </h1>
    </div>
  );
}
