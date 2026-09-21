/**
 * English catalog — the source-of-truth key set for the W1 i18n contract.
 *
 * W1 ships the foundation only: this is a deliberately small, first-party
 * catalog (no third-party i18n dependency). Route/page translation is W2-W4;
 * `coverage.ts` tracks every mounted surface that is still English-only so
 * fallback is never mistaken for coverage.
 */
import type { Catalog } from '../catalog';

export const en = {
  'app.name': 'HappyRanch',
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.save': 'Save',
  'common.dismiss': 'Dismiss',
  'common.language': 'Language',
  'common.languageDescription': 'Choose the interface language.',
  'common.englishOnlyNotice': 'This surface is not yet available in Chinese.',
  'common.translatedProbe': 'Translated probe',
  'common.greeting': 'Hello, {name}',
  'common.movableSlots': '{subject} before {object}',
  'common.itemCount': {
    one: '{count} item',
    other: '{count} items',
  },
  'common.filesSelected': {
    one: '{count} file selected by {name}',
    other: '{count} files selected by {name}',
  },
  'coverage.title': 'Translation coverage',
  'coverage.englishOnly': 'English only — not yet migrated',
  'coverage.notApplicable': 'No user-facing copy',
  'coverage.summary': '{translated} of {total} namespaces translated',

  // --- W2a: mounted shell (AppBar page titles + Sidebar navigation) ---------
  'shell.title.home': 'Home',
  'shell.title.threads': 'Threads',
  'shell.title.tasks': 'Tasks',
  'shell.title.agents': 'Agents',
  'shell.title.skills': 'Skills',
  'shell.title.knowledge': 'Knowledge',
  'shell.title.artifacts': 'Artifacts',
  'shell.title.usage': 'Usage',
  'shell.title.dreams': 'Dreams',
  'shell.title.workHours': 'Work Hours',
  'shell.title.audit': 'Audit',
  'shell.title.settings': 'Settings',
  'shell.title.jobs': 'Jobs',
  'shell.title.runtimeHealth': 'Runtime Health',
  'shell.title.assistant': 'Assistant',
  'shell.title.getStarted': 'Get started',

  'shell.nav.home': 'Home',
  'shell.nav.threads': 'Threads',
  'shell.nav.tasks': 'Tasks',
  'shell.nav.jobs': 'Jobs',
  'shell.nav.todos': 'Todos',
  'shell.nav.agents': 'Agents',
  'shell.nav.workHours': 'Work Hours',
  'shell.nav.skills': 'Skills',
  'shell.nav.knowledge': 'Knowledge',
  'shell.nav.artifacts': 'Artifacts',
  'shell.nav.audit': 'Audit',
  'shell.nav.dreams': 'Dreams',
  'shell.nav.usage': 'Usage',
  'shell.nav.health': 'Health',
  'shell.nav.settings': 'Settings',

  'shell.nav.primary': 'Primary navigation',
  'shell.nav.primaryItems': 'Primary navigation items',
  'shell.orgSwitcher': 'Organization switcher',
  'shell.activeOrg': 'Active org',
  'shell.addOrgOption': '+ Add org…',
  'shell.noOrg': 'No org',
  'shell.dayCount': 'Day {count}',
  'shell.account.label': 'Account: {name}, {role}',
  'shell.account.you': 'You',
  'shell.account.founder': 'Founder',

  'shell.openAssistant': 'Open assistant',
  'shell.switchToLight': 'Switch to light theme',
  'shell.switchToDark': 'Switch to dark theme',

  'shell.loading': 'Loading…',
  'shell.notFound.body': 'Not found. {link}.',
  'shell.notFound.goHome': 'Go home',

  'shell.error.title': 'Something went wrong on this page.',
  'shell.error.body':
    'The rest of the app is still usable — navigate elsewhere via the top bar, or reload to retry.',
  'shell.error.retry': 'Try again',

  // --- W2a: Add-org dialog --------------------------------------------------
  'org.add.title': 'New org',
  'org.add.slugLabel': 'Slug',
  'org.add.slugPlaceholder': 'e.g. hk-macau-tourism',
  'org.add.slugHint': 'Lowercase letters, digits, and hyphens. 1–40 characters.',
  'org.add.create': 'Create',
  'org.add.creating': 'Creating…',
  'org.add.error.noActiveRuntime':
    'No runtime is active yet — the daemon is still starting up. Try again in a moment.',
  'org.add.error.dirHasData':
    'A directory for "{slug}" already exists and contains data. It may be listed under broken orgs. Manual cleanup is required.',
  'org.add.error.exists': 'An org with slug "{slug}" already exists.',
  'org.add.error.invalidSlug': 'Slug must match ^[a-z0-9-]{1,40}$.',
  'org.add.error.generic': 'Could not create org.',

  // --- W2a: help drawer + command palette (shared presentation) -------------
  'help.title': 'Keyboard shortcuts',
  'help.description': 'List of keyboard shortcuts available on this screen.',
  'help.footnote':
    'Shortcuts are suppressed when focus is inside an input, textarea, or contenteditable element.',
  'help.footnote.focus': 'Shortcuts are disabled while focus is inside an input or textarea.',
  'help.empty': 'No shortcuts defined.',
  'help.tab.global': 'Global',
  'help.tab.dashboard': 'Dashboard',
  'help.tab.threads': 'Threads',
  'help.tab.tasks': 'Tasks',
  'help.tab.kb': 'KB',
  'help.tab.agents': 'Agents',
  'help.tab.audit': 'Audit',
  'help.shortcut.openAssistant': 'Open assistant dock',
  'help.shortcut.showHelp': 'Show this help',
  'help.shortcut.closeAny': 'Close any dialog, drawer, or palette',
  'help.shortcut.jumpDashboard': 'Jump to Dashboard',
  'help.shortcut.jumpThreads': 'Jump to Threads',
  'help.shortcut.jumpTasks': 'Jump to Tasks',
  'help.shortcut.jumpKnowledge': 'Jump to Knowledge Base',
  'help.shortcut.jumpAudit': 'Jump to Audit',
  'help.shortcut.jumpAgents': 'Jump to Agents',
  'help.shortcut.jumpHere': 'Jump here from anywhere',
  'help.shortcut.saveAgent': 'Save agent changes (when dirty)',
  'help.shortcut.closeTaskView': 'Close the open task drawer or dialog',
  'help.shortcut.newThread': 'New thread',
  'help.shortcut.inviteParticipant': 'Invite participant',
  'help.shortcut.archiveThread': 'Archive thread',
  'help.shortcut.forwardThread': 'Forward thread (compose new with quoted excerpt)',
  'help.shortcut.focusComposer': 'Focus composer',
  'help.shortcut.send': 'Send (in composer)',
  'help.shortcut.closeDialog': 'Close dialog',

  'palette.title': 'Command palette',
  'palette.description':
    'Type to filter. Use up and down arrows to move. Press Enter to open. Press Escape to close.',
  'palette.searchPlaceholder': 'Search threads, tasks, agents, orgs, KB…',
  'palette.searchLabel': 'Command palette search',
  'palette.resultsLabel': 'Results',
  'palette.noMatches': 'No matches.',
  'palette.nothingLoaded': 'Nothing loaded yet — visit a page first.',
  'palette.footer.navigate': 'navigate',
  'palette.footer.open': 'open',
  'palette.footer.close': 'close',
  'palette.section.orgs': 'Orgs',
  'palette.section.threads': 'Threads',
  'palette.section.tasks': 'Tasks',
  'palette.section.agents': 'Agents',
  'palette.section.kb': 'KB',
} as const satisfies Catalog;

export default en;
