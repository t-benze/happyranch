/**
 * Desktop Settings dialog body (THR-387 S6-W1; handoff v2.4 §2, C4).
 *
 * Navigation follows v2.4 §2: This app → Language; This computer →
 * HappyRanch on this computer; Runtime settings → Capacity, Executors,
 * Organization, Coding; plus Open Connection. The panes
 * are the existing `features/settings` sections (`SettingsPane`), so there is
 * one Settings editor and no duplicate mutation path. This computer →
 * "HappyRanch on this computer" (S7-L2, `./local`) is listed where native
 * admits the `local.*` commands (Linux), including while a remote target is
 * selected; then the group also shows the C5 scope note naming that Host
 * (from native `app.status`).
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
  Monitor,
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
import { useNativeAppStatus } from '@/lib/native/hooks';
import { OwnerScope } from '@/shared/owners';
import { SettingsPane, type SettingsSection } from './SettingsPage';
import { LocalServicePage, useLocalCommandsAdmitted } from './local/LocalServicePage';

/** Dialog pages: the Settings page sections plus desktop-only `local`. */
export type SettingsDialogSection = SettingsSection | 'local';

interface NavItem {
  section: SettingsDialogSection;
  labelKey: MessageKey;
  icon: LucideIcon;
}

const APP_ITEMS: readonly NavItem[] = [
  { section: 'preferences', labelKey: 'desktopDialogs.settings.nav.language', icon: Languages },
];

const COMPUTER_ITEMS: readonly NavItem[] = [
  { section: 'local', labelKey: 'desktopDialogs.settings.nav.local', icon: Monitor },
];

const RUNTIME_ITEMS: readonly NavItem[] = [
  { section: 'daemon-capacity', labelKey: 'settings.nav.daemonCapacity', icon: Gauge },
  { section: 'executors', labelKey: 'settings.nav.executors', icon: Terminal },
  { section: 'organization', labelKey: 'settings.nav.organization', icon: HomeIcon },
  { section: 'coding', labelKey: 'settings.nav.coding', icon: GitPullRequest },
];

export interface SettingsDialogBodyProps {
  headingRef: RefObject<HTMLHeadingElement>;
  section: SettingsDialogSection;
  onSelectSection: (section: SettingsDialogSection) => void;
  onOpenConnection: () => void;
}

export function SettingsDialogBody({
  headingRef,
  section,
  onSelectSection,
  onOpenConnection,
}: SettingsDialogBodyProps): JSX.Element {
  const { t } = useTranslation();
  const app = useNativeAppStatus();
  // C5: the selected remote Host's display name, from native app.status.
  const remoteHostName = app?.target?.kind === 'remote' ? app.target.name : null;
  const localAdmitted = useLocalCommandsAdmitted();
  const orgMatch = useMatch('/orgs/:slug/*');
  const slug = orgMatch?.params.slug ?? null;
  const paneRef = useRef<HTMLDivElement>(null);
  // Compact layout stacks nav above the page: bring the chosen page into view.
  const select = (next: SettingsDialogSection): void => {
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
          {localAdmitted !== false && (
            <NavGroup
              className="mt-3"
              heading={t('desktopDialogs.settings.nav.thisComputer')}
              context={remoteHostName ? t('desktopDialogs.settings.localScope', { host: remoteHostName }) : undefined}
              contextTestId="local-scope-nav-note"
              items={COMPUTER_ITEMS}
              active={section}
              onSelect={select}
            />
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
          ) : section === 'local' ? (
            localAdmitted === false ? (
              <p className="text-text-secondary p-8 text-sm">{t('settings.local.notAvailable')}</p>
            ) : (
              <LocalServicePage />
            )
          ) : (
            <Routes>
              <Route
                path="/orgs/:slug/*"
                element={
                  <OrgProvider>
                    {/* S6-W2a: these editors are Settings-dialog owners of
                        this page; keyed by org so an old-org draft is never
                        shown under another org. */}
                    <OwnerScope scope="settings" section={section}>
                      <SettingsPane key={slug ?? ''} section={section} />
                    </OwnerScope>
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
  contextTestId,
  items,
  active,
  onSelect,
  className = '',
}: {
  heading: string;
  context?: string;
  contextTestId?: string;
  items: readonly NavItem[];
  active: SettingsDialogSection;
  onSelect: (section: SettingsDialogSection) => void;
  className?: string;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <section className={`min-w-0 ${className}`}>
      <h3 className="text-overline text-text-secondary mb-1 px-1 tracking-wider break-words uppercase">
        {heading}
      </h3>
      {context && (
        <p className={`text-text-muted mb-1 px-1 text-xs ${contextTestId ? 'break-words' : 'break-all'}`} data-testid={contextTestId}>
          {context}
        </p>
      )}
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
