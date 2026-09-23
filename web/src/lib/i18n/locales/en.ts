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
  'common.close': 'Close',
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

  // --- W2b: onboarding (/onboarding) ----------------------------------------
  // First-run vs returning welcome.
  'onboarding.welcome.eyebrow.firstRun': 'Fresh start',
  'onboarding.welcome.eyebrow.returning': 'New workspace',
  'onboarding.welcome.title.firstRun': 'Welcome to HappyRanch.',
  'onboarding.welcome.title.firstRunLine2': 'Let’s create your first org.',
  'onboarding.welcome.title.returning': 'Create another org',
  'onboarding.welcome.body.prefix':
    'An {orgTerm} is a workspace where your agents, threads, and tasks live. {tail}',
  'onboarding.welcome.body.orgTerm': 'org',
  'onboarding.welcome.body.firstRun':
    "You don't have one yet — create one to get started. Everything else stays quiet until then.",
  'onboarding.welcome.body.returning':
    'Add another to run a separate one, or return to an existing org from the sidebar.',
  'onboarding.welcome.cta.firstRun': 'Create your first org',
  'onboarding.welcome.cta.returning': 'Create another org',
  'onboarding.welcome.timing': 'Takes a few seconds.',
  'onboarding.welcome.note':
    'Creating an org sets up the workspace only. It does {emphasis} install agentic CLIs ({clis}…) — you’ll wire those up separately from Settings once the org exists.',
  'onboarding.welcome.note.emphasis': 'not',
  'onboarding.loading': 'Loading',

  // Create step.
  'onboarding.create.eyebrow': 'New org',
  'onboarding.create.heading': 'Name your org',
  'onboarding.create.slugLabel': 'Org slug',
  'onboarding.create.slugHint':
    'This is the org’s permanent identifier. It can’t be changed later.',
  'onboarding.create.slugPlaceholder': 'e.g. hk-macau-tourism',
  'onboarding.create.slugRule': 'Lowercase letters, numbers and hyphens',
  'onboarding.create.submit': 'Create org',
  'onboarding.creating.aria': 'Creating org',
  'onboarding.creating.heading': 'Creating {slug}…',
  'onboarding.creating.body': 'Setting up the workspace.',

  // Success step.
  'onboarding.success.heading': 'Org {slug} is ready.',
  'onboarding.success.body':
    'Your workspace is live. Next: wire up an agentic CLI from Settings, then dispatch your first task.',
  'onboarding.success.enter': 'Enter {slug}',
  'onboarding.success.createAnother': 'Create another',

  // Broken-org list (raw slug + raw error stay verbatim).
  'onboarding.broken.heading': {
    one: '{count} org failed to load',
    other: '{count} orgs failed to load',
  },
  'onboarding.broken.body':
    'These workspaces are on disk but the daemon could not open them. The raw error is shown as reported — fix it on the host.',
  'onboarding.broken.footer':
    'Broken orgs don’t block you — you can still {link} while these stay parked.',
  'onboarding.broken.createNewOrg': 'create a new org',
  'onboarding.broken.copied': 'Copied',
  'onboarding.broken.copyError': 'Copy error',

  // Executor prereq readiness panel (raw tool/path/hint stay verbatim).
  'onboarding.prereqs.aria': 'Executor readiness',
  'onboarding.prereqs.checking': 'Checking host tools…',
  'onboarding.prereqs.summary': '{present} of {total} tools registered',
  'onboarding.prereqs.connected': 'connected',
  'onboarding.prereqs.notRegistered': 'Not registered. {hint}',
  'onboarding.prereqs.registered': 'registered',
  'onboarding.prereqs.notRegisteredPill': 'not registered',

  // Step 1 wrapper chrome (ConnectRuntimeStep).
  'onboarding.connect.eyebrow': 'Step 1 of 2 · Connect your agentic CLI',
  'onboarding.connect.heading': 'Connect your agentic CLI.',
  'onboarding.connect.skip': 'Skip — I’ll connect a CLI later',
  'onboarding.connect.skipWaiting': 'Skip for now',
  'onboarding.connect.continue': 'Continue',
  'onboarding.connect.connected.builtin':
    'Its binary path is registered on this machine — HappyRanch can launch it now. You can manage your CLIs anytime from Settings.',
  'onboarding.connect.connected.custom':
    'Your custom CLI is registered and available to every org. You can manage your CLIs anytime from Settings.',

  // Built-in connect body.
  'onboarding.connect.builtin.intro':
    'Pick the agentic CLI you run — Claude Code, Codex, opencode, or Pi. Paste the generated prompt into it: it proves it works and tells HappyRanch where its binary lives on this machine so it can launch it.',
  'onboarding.connect.builtin.label': 'Pick your agentic CLI',
  'onboarding.connect.builtin.placeholder': 'Choose an agentic CLI…',
  'onboarding.connect.generate': 'Generate connect prompt',
  'onboarding.connect.generating': 'Generating…',
  'onboarding.connect.useCustom': 'Connect a custom CLI instead',
  'onboarding.connect.mintError.status': 'Could not generate a prompt ({status}).',
  'onboarding.connect.mintError.unreachable':
    'Could not generate a prompt. Is the daemon reachable?',

  // Custom adapter connect body.
  'onboarding.connect.adapter.bannerTitle': 'Create a custom adapter wrapper',
  'onboarding.connect.adapter.bannerBody':
    'Your CLI creates a small v1 adapter wrapper that speaks HappyRanch’s standard AdapterInput/AdapterOutput contract. It reads the prompt from stdin, invokes your CLI, and returns a normalized result. One POST connects it — no approval wait.',
  'onboarding.connect.adapter.nameLabel': 'Name this CLI',
  'onboarding.connect.adapter.nameHint':
    'A short identifier — becomes its executor name. The adapter id will be {code}.',
  'onboarding.connect.adapter.namePlaceholder': 'e.g. my-cli',
  'onboarding.connect.adapter.builtinName':
    'Pick a name that isn’t a built-in (claude, codex, opencode, pi) — connect those from the dropdown instead.',
  'onboarding.connect.adapter.nameRule':
    'Lowercase letters, numbers and hyphens · starts with a letter',
  'onboarding.connect.useBuiltin': 'Connect a built-in CLI instead',

  // Committing.
  'onboarding.connect.committing.title': 'Finishing connection…',
  'onboarding.connect.committing.body':
    '{name} reported in. HappyRanch is verifying it now — this takes a moment.',

  // Built-in waiting.
  'onboarding.connect.waiting.header': 'connect prompt · paste into your agentic CLI',
  'onboarding.connect.copy': 'Copy',
  'onboarding.connect.copied': 'Copied',
  'onboarding.connect.copyPrompt': 'Copy prompt',
  'onboarding.connect.waiting.hint':
    'Then run it in your terminal — this screen updates live.',
  'onboarding.connect.expired.title': 'This link expired',
  'onboarding.connect.expired.body':
    'The prompt is valid for about 30 minutes and this one lapsed before a CLI connected. Nothing was lost — regenerate a fresh prompt.',
  'onboarding.connect.regenerate': 'Regenerate prompt',
  'onboarding.connect.regenerating': 'Regenerating…',
  'onboarding.connect.waiting.aria': 'Waiting for your CLI',
  'onboarding.connect.waiting.title': 'Waiting for {name} to connect…',
  'onboarding.connect.waiting.stepsLead':
    'As your CLI runs the prompt, it completes these checks, then registers:',
  'onboarding.connect.back': 'Back to the prompt',
  'onboarding.connect.step.workspace_access': 'Reads its workspace & skills',
  'onboarding.connect.step.loopback_reachable': 'Reaches HappyRanch at 127.0.0.1',
  'onboarding.connect.step.cli_callback': 'Reports in & registers',
  'onboarding.connect.step.emit_envelope': 'Produces a valid result-envelope',

  // Adapter waiting.
  'onboarding.connect.adapter.waiting.header': 'adapter connect prompt · paste into your CLI',
  'onboarding.connect.adapter.waiting.hint':
    'Create the wrapper, run the prompt — this screen updates live.',
  'onboarding.connect.adapter.expired.body':
    'The prompt is valid for about 30 minutes. Regenerate a fresh prompt.',
  'onboarding.connect.adapter.waiting.aria': 'Waiting for adapter submission',
  'onboarding.connect.adapter.waiting.title': 'Waiting for adapter submission…',
  'onboarding.connect.adapter.waiting.body':
    'Your CLI should create a v1 adapter wrapper, complete the conformance checks, and submit it. This screen updates when the adapter appears.',

  // Retryable conformance failure.
  'onboarding.connect.retryable.title':
    'Connection attempt failed — retry with changed artifacts',
  'onboarding.connect.retryable.body':
    '{name} reported in, but the conformance probe failed. Modify the wrapper or child artifacts, then rerun the existing prompt below before it expires. Unchanged or reordered artifacts are refused; only one genuinely changed candidate remains under this token.',
  'onboarding.connect.retryable.header':
    'existing connect prompt · rerun with changed artifacts',
  'onboarding.connect.retryable.rerun':
    "I've rerun the prompt — watch for the new attempt",
  'onboarding.connect.error.label': 'Error: {detail}',

  // Terminal connect failure + clear.
  'onboarding.connect.failed.title': 'Connection failed',
  'onboarding.connect.failed.body':
    '{name} reported in, but HappyRanch could not finish connecting it. {categoryMessage}',
  'onboarding.connect.failed.category.expired':
    'This connect link expired before the CLI finished. Start over with a fresh prompt.',
  'onboarding.connect.failed.category.exhausted':
    'This lifecycle has used its allowed retry. Start over with a fresh registration.',
  'onboarding.connect.failed.category.nonretryable':
    'This failure is not retryable with the same artifacts.',
  'onboarding.connect.failed.operation': 'Operation: {operationId}',
  'onboarding.connect.validate': 'Validate immutable snapshot',
  'onboarding.connect.backShort': 'Back',
  'onboarding.connect.clearing': 'Clearing…',
  'onboarding.connect.clear.confirm': 'Confirm clear failed connection',
  'onboarding.connect.clear.action': 'Clear failed connection',
  'onboarding.connect.clear.note':
    'Validate immutable snapshot re-checks the persisted wrapper/child snapshot without changing artifacts. To retry with changed artifacts, clear this record and start over.',

  // Cleared failure record.
  'onboarding.connect.cleared.title': 'Failed connection cleared',
  'onboarding.connect.cleared.body': '{name}’s failed connection record was cleared.',
  'onboarding.connect.cleared.alreadyAbsent': 'The failed wrapper was already absent.',
  'onboarding.connect.cleared.preservedChanged':
    'The wrapper was preserved because it changed after the failed connection.',
  'onboarding.connect.cleared.preservedUnsafe':
    'The wrapper was preserved because HappyRanch could not safely prove it matched.',
  'onboarding.connect.cleared.reconnect': 'Reconnect this CLI',

  // Connected card.
  'onboarding.connect.connected.title': '{name} connected.',
  'onboarding.connect.connected.adapterSubtitle':
    'Your custom CLI is connected directly. It is available to every org.',
  'onboarding.connect.connected.name': 'Name',
  'onboarding.connect.connected.registeredAt': 'Registered at',
  'onboarding.connect.connected.registrationRequired': 'registration required',
  'onboarding.connect.connectAnother': 'Connect another',

  // How-this-works honesty note.
  'onboarding.connect.how.title': 'How this works',
  'onboarding.connect.how.body':
    'The prompt carries a short-lived, scoped token valid for about {duration}. Copying doesn’t run anything — nothing executes on your machine until you paste and run it yourself. Connecting only makes the CLI available to choose; assigning an agent to run on it is a separate, later step.',
  'onboarding.connect.how.duration': '30 minutes',
} as const satisfies Catalog;

export default en;
