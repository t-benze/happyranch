export function SidebarNavItem({ to, enabled, children }: { to: string; enabled: boolean; children: string }): JSX.Element {
  if (!enabled) {
    return (
      <span
        className="text-text-muted text-nav flex cursor-not-allowed items-center gap-2.75 rounded-sm px-2 py-2 font-medium md:px-2.5"
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
        `flex items-center gap-2.75 rounded-sm px-2 py-2 md:px-2.5 text-nav focus-visible:ring-2 focus-visible:ring-accent-default ${
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
