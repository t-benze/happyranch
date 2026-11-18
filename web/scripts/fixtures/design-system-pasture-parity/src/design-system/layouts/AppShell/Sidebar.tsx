export function SidebarNavItem({ to, enabled, children }: { to: string; enabled: boolean; children: string }): JSX.Element {
  if (!enabled) {
    return (
      <span
        className="text-text-muted text-nav flex cursor-not-allowed items-center gap-2.5 rounded-sm px-2 py-1.5 font-medium md:gap-2.75 md:px-2.5 md:py-2"
        aria-disabled="true"
      >
        {children}
      </span>
    );
  }
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        // A `quoted` comment with backticks must not confuse the extractor.
        `flex items-center gap-2.5 rounded-sm px-2 py-1.5 md:gap-2.75 md:px-2.5 md:py-2 text-nav focus-visible:ring-2 focus-visible:ring-accent-default ${
          isActive
            ? 'bg-accent-soft text-accent-text font-semibold'
            : 'text-text-secondary font-medium hover:bg-surface-raised'
        }`
      }
    >
      {children}
    </NavLink>
  );
}
