/**
 * Desktop Settings dialog body (THR-387 S6-W1; handoff v2.4 §2, C4).
 *
 * Navigation follows v2.4 §2: This app → Language; Runtime settings →
 * Capacity, Executors, Organization, Coding; plus Open Connection. The panes
 * are the existing `features/settings` sections (`SettingsPane`), so there is
 * one Settings editor and no duplicate mutation path. "HappyRanch on this
 * computer" is S7-L2; its group shows only the C5 scope note, and only when a
 * remote Host name is known (none is known until the S5-W1 status store
 * lands).
 *
 * The desktop dialog host (`features/desktop-dialogs`) renders this body;
 * `routes.tsx` composes the two, so neither feature imports the other.
 *
 * Layout (C4): below md (e.g. 760x560 at 200%) nav and pane stack in one
 * vertically scrolling column and nav items wrap; md and wider use a left
 * rail. There is never a sideways-only strip.
 */
import {
  GitPullRequest,
  Gauge,
  Home as HomeIcon,
  Languages,
  Plug,
  Terminal,
  type LucideIcon,
} from 'lucide-react';
import { useRef, type RefObject } from 'react';
import { Route, Routes, useMatch } from 'react-router-dom';
import { DialogTitle } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { OrgProvider } from '@/lib/orgSlug';
import { NativeRecoveryFence } from '@/shared/connection/NativeRecoveryPanel';
import { useNativeRecovery } from '@/hooks/nativeRecovery';
import { SettingsPane, type SettingsSection } from './SettingsPage';

interface NavItem {
  section: SettingsSection;
  labelKey: MessageKey;
  icon: LucideIcon;
}

const APP_ITEMS: readonly NavItem[] = [
  { section: 'preferences', labelKey: 'desktopDialogs.settings.nav.language', icon: Languages },
];

const RUNTIME_ITEMS: readonly NavItem[] = [
  { section: 'daemon-capacity', labelKey: 'settings.nav.daemonCapacity', icon: Gauge },
  { section: 'executors', labelKey: 'settings.nav.executors', icon: Terminal },
  { section: 'organization', labelKey: 'settings.nav.organization', icon: HomeIcon },
  { section: 'coding', labelKey: 'settings.nav.coding', icon: GitPullRequest },
];

export interface SettingsDialogBodyProps {
  headingRef: RefObject<HTMLHeadingElement>;
  section: SettingsSection;
  onSelectSection: (section: SettingsSection) => void;
  onOpenConnection: () => void;
  /** Selected remote Host name for the C5 note; null while unknown. */
  remoteHostName?: string | null;
}

export function SettingsDialogBody({
  headingRef,
  section,
  onSelectSection,
  onOpenConnection,
  remoteHostName = null,
}: SettingsDialogBodyProps): JSX.Element {
  const { t } = useTranslation();
  const orgMatch = useMatch('/orgs/:slug/*');
  const slug = orgMatch?.params.slug ?? null;
  const paneRef = useRef<HTMLDivElement>(null);
  // Compact layout stacks nav above the page: bring the chosen page into view.
  const select = (next: SettingsSection): void => {
    onSelectSection(next);
    if (window.matchMedia('(min-width: 48rem)').matches) return;
    requestAnimationFrame(() => paneRef.current?.scrollIntoView({ block: 'start' }));
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-border-default shrink-0 border-b py-3 pr-14 pl-4 md:pl-6">
        <DialogTitle
          ref={headingRef}
          tabIndex={-1}
          className="font-display focus-visible:ring-accent-default w-fit max-w-full rounded-sm text-lg break-words focus:outline-none focus-visible:ring-2"
        >
          {t('settings.page.title')}
        </DialogTitle>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto md:flex md:overflow-hidden">
        <nav
          aria-label={t('desktopDialogs.settings.nav.label')}
          className="border-border-default bg-surface-sunken min-w-0 border-b p-3 md:w-60 md:shrink-0 md:overflow-y-auto md:border-r md:border-b-0"
        >
          <NavGroup
            heading={t('desktopDialogs.settings.nav.thisApp')}
            items={APP_ITEMS}
            active={section}
            onSelect={select}
          />
          {remoteHostName && (
            <section className="mt-3 min-w-0">
              <h3 className="text-overline text-text-secondary mb-1 px-1 tracking-wider break-words uppercase">
                {t('desktopDialogs.settings.nav.thisComputer')}
              </h3>
              <p className="text-text-secondary px-1 text-xs break-words" data-testid="local-scope-note">
                {t('desktopDialogs.settings.localScope', { host: remoteHostName })}
              </p>
            </section>
          )}
          <NavGroup
            className="mt-3"
            heading={t('desktopDialogs.settings.nav.runtime')}
            context={slug ? t('desktopDialogs.settings.nav.org', { org: slug }) : undefined}
            items={RUNTIME_ITEMS}
            active={section}
            onSelect={select}
          />
          <div className="border-border-default mt-3 border-t pt-3">
            <button
              type="button"
              onClick={onOpenConnection}
              className="text-text-secondary hover:bg-surface-hover hover:text-text-primary focus-visible:ring-accent-default flex w-full min-w-0 items-center gap-2 rounded-full px-3 py-1 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none"
            >
              <Plug size={16} aria-hidden="true" className="shrink-0" />
              <span className="min-w-0 break-words">{t('desktopDialogs.settings.openConnection')}</span>
            </button>
          </div>
        </nav>
        <div ref={paneRef} className="min-w-0 md:flex-1 md:overflow-y-auto" data-testid="desktop-settings-pane">
          {section === 'preferences' ? (
            <SettingsPane section="preferences" />
          ) : (
            <Routes>
              <Route
                path="/orgs/:slug/*"
                element={
                  <OrgProvider>
                    <SettingsPane section={section} />
                  </OrgProvider>
                }
              />
              <Route path="*" element={<NoOrgPane />} />
            </Routes>
          )}
        </div>
      </div>
    </div>
  );
}

function NavGroup({
  heading,
  context,
  items,
  active,
  onSelect,
  className = '',
}: {
  heading: string;
  context?: string;
  items: readonly NavItem[];
  active: SettingsSection;
  onSelect: (section: SettingsSection) => void;
  className?: string;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <section className={`min-w-0 ${className}`}>
      <h3 className="text-overline text-text-secondary mb-1 px-1 tracking-wider break-words uppercase">
        {heading}
      </h3>
      {context && <p className="text-text-muted mb-1 px-1 text-xs break-all">{context}</p>}
      <ul className="flex flex-wrap gap-0.5 md:block md:space-y-0.5">
        {items.map((item) => {
          const isActive = item.section === active;
          return (
            <li key={item.section} className="min-w-0 max-w-full">
              <button
                type="button"
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelect(item.section)}
                className={`focus-visible:ring-accent-default flex w-full min-w-0 items-center gap-2 rounded-full px-3 py-1 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none ${
                  isActive
                    ? 'bg-accent-soft text-accent-text'
                    : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'
                }`}
              >
                <item.icon size={16} aria-hidden="true" className="shrink-0" />
                <span className="min-w-0 break-words">{t(item.labelKey)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Runtime page with no organization in Main (e.g. onboarding, recovery). */
function NoOrgPane(): JSX.Element {
  const { t } = useTranslation();
  const recovery = useNativeRecovery();
  if (recovery) return <NativeRecoveryFence>{null}</NativeRecoveryFence>;
  return <p className="text-text-secondary p-8 text-sm">{t('desktopDialogs.settings.noOrg')}</p>;
}
