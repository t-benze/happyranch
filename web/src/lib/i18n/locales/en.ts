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

  // --- W2c: settings/page (anchor; keys go below) ---
  'settings.page.title': 'Settings',
  'settings.page.meta': 'Daemon and org configuration.',
  'settings.page.loading': 'Loading settings…',
  'settings.page.loadError': 'Could not load settings.',
  'settings.nav.heading': 'Configuration',
  'settings.nav.daemonCapacity': 'Capacity',
  'settings.nav.assistant': 'Assistant',
  'settings.nav.organization': 'Organization',
  'settings.nav.executors': 'Executors',
  'settings.nav.preferences': 'Preferences',
  'settings.panel.assistant.title': 'System Assistant',
  'settings.panel.organization.title': 'Organization',
  'settings.panel.organization.description':
    'Org-level settings. Changes apply live — the daemon hot-reloads them automatically.',
  'settings.panel.executors.title': 'Executors',
  'settings.panel.executors.description':
    'The agentic CLIs registered on this machine. The daemon launches agents on these — connect a new CLI or manage the ones already registered.',
  'settings.panel.preferences.title': 'Preferences',
  'settings.panel.preferences.description':
    'Personal settings for this browser. They are not saved to the organization.',
  // @w2c-end:page

  // --- W2c: settings/preferences (anchor; keys go below) ---
  'settings.preferences.languageHelp':
    'Choose the interface language. The change applies immediately.',
  'settings.preferences.status.pending': 'Saving your language choice…',
  'settings.preferences.status.durable': 'Saved in this browser.',
  'settings.preferences.status.failed':
    'Could not save in this browser. The language applies to this session only.',
  // @w2c-end:preferences

  // --- W2c: settings/assistant (anchor; keys go below) ---
  'settings.assistant.state.uninitialized': 'Uninitialized',
  'settings.assistant.state.configured': 'Configured',
  'settings.assistant.state.staleOrBroken': 'Stale or broken',
  'settings.assistant.loading': 'Loading…',
  'settings.assistant.loadError': 'Could not load assistant status.',
  'settings.assistant.status.aria': 'Assistant status',
  'settings.assistant.status.state': 'State',
  'settings.assistant.executor': 'Executor',
  'settings.assistant.status.workspace': 'Workspace',
  'settings.assistant.setup.aria': 'Setup actions',
  'settings.assistant.setup.title': 'Setup',
  'settings.assistant.setup.uninitializedBody':
    'Prepare the registration workspace, then either register an executor below or launch your CLI in the workspace and let it self-register.',
  'settings.assistant.setup.initializing': 'Initializing…',
  'settings.assistant.setup.initialize': 'Initialize workspace',
  'settings.assistant.setup.selfRegistration.title': 'Self-registration',
  'settings.assistant.setup.selfRegistration.step1':
    'Open your agentic CLI (claude, codex, opencode, pi, …) in the workspace shown above.',
  'settings.assistant.setup.selfRegistration.step2': 'Ask it to register itself; it runs {command}.',
  'settings.assistant.setup.staleBody':
    'The workspace drifted from the saved config. Repair rebuilds it from the recorded executor without clearing your registration.',
  'settings.assistant.setup.repairing': 'Repairing…',
  'settings.assistant.setup.repair': 'Repair',
  'settings.assistant.setup.configuredBody':
    'Reconfiguring closes any open sessions and clears the saved config so you can register a different executor from scratch.',
  'settings.assistant.setup.reconfigure': 'Reconfigure…',
  'settings.assistant.reconfigure.title': 'Reconfigure the assistant?',
  'settings.assistant.reconfigure.body':
    'This closes all open assistant sessions and clears the saved configuration. You will need to register an executor again.',
  'settings.assistant.reconfigure.confirming': 'Reconfiguring…',
  'settings.assistant.reconfigure.confirm': 'Reconfigure',
  'settings.assistant.register.title': 'Register executor',
  'settings.assistant.register.switchTitle': 'Switch executor',
  'settings.assistant.register.preserveNote':
    'Re-registering preserves the workspace — the server derives it from the runtime root, not from any input here — and only one executor is active at a time, so registering replaces the current one.',
  'settings.assistant.register.noRestart':
    'Registration applies immediately; no daemon restart is required.',
  'settings.assistant.register.other': 'Other…',
  'settings.assistant.register.executorName': 'Executor name',
  'settings.assistant.register.command': 'Command',
  'settings.assistant.register.argv': 'Argv (optional — defaults to the command)',
  'settings.assistant.register.argvHint':
    'Space-separated. Leave blank to launch the command with no extra args.',
  'settings.assistant.register.errorNoExecutor': 'Choose or name an executor.',
  'settings.assistant.register.errorNoCommand': 'Enter the command to launch.',
  'settings.assistant.register.errorHttp': 'Registration failed (HTTP {status}).',
  'settings.assistant.register.registering': 'Registering…',
  'settings.assistant.register.submit': 'Register',
  // @w2c-end:assistant

  // --- W2c: settings/organization (anchor; keys go below) ---
  'settings.organization.saveFailed': 'Save failed: {detail}',
  'settings.organization.saved': 'Saved. Changes will take effect within ~1 minute.',
  'settings.organization.workHoursSaved':
    'Saved ✓ — takes effect at the next scheduler pass (≈ within ~60s).',
  'settings.organization.badge.live': 'Applies live',
  'settings.organization.session.heading': 'Session',
  'settings.organization.session.timeout': 'Session timeout (s)',
  'settings.organization.session.timeoutPlaceholder': 'use system default',
  'settings.organization.dreaming.heading': 'Dreaming',
  'settings.organization.enabled': 'Enabled',
  'settings.organization.dreaming.scheduleTime': 'Schedule time',
  'settings.organization.dreaming.scheduleTimezone': 'Schedule timezone',
  'settings.organization.dreaming.catchUp': 'Catch up on startup',
  'settings.organization.dreaming.agentMode': 'Agent mode',
  'settings.organization.dreaming.included': 'Included agents',
  'settings.organization.dreaming.excluded': 'Excluded agents',
  'settings.organization.dreaming.addAgentsPlaceholder': 'add agents…',
  'settings.organization.threads.heading': 'Threads',
  'settings.organization.threads.invocationTimeout': 'Invocation timeout (s)',
  'settings.organization.threads.timeoutPlaceholder': 'none',
  'settings.organization.operating.heading': 'Operating controls',
  'settings.organization.operating.description':
    'Organization-wide work hours enablement and agent eligibility. The scheduler and tier-level cadence is configured under {link}.',
  'settings.organization.operating.workHoursLink': 'Work Hours',
  'settings.organization.operating.workHours': 'Work Hours',
  'settings.organization.operating.on': 'ON',
  'settings.organization.operating.off': 'OFF',
  'settings.organization.operating.eligibility': 'Agent eligibility',
  'settings.organization.operating.whitelist': 'Whitelist ({count} included)',
  'settings.organization.operating.allAgents': 'All agents',
  'settings.organization.operating.excluded': ', {count} excluded',
  'settings.organization.operating.editEligibility': 'Edit eligibility',
  'settings.organization.saveBar.saving': 'Saving…',
  'settings.organization.saveBar.save': 'Save changes',
  'settings.organization.saveBar.discard': 'Discard',
  'settings.organization.saveBar.shortcut': '⌘S to save',
  'settings.organization.disableDialog.title': 'Disable work hours?',
  'settings.organization.disableDialog.description':
    'Turning the feature off halts all scheduled wakes for every agent. Eligibility and tier config are preserved; nothing runs until you turn it back on.',
  'settings.organization.disableDialog.cancel': 'Cancel',
  'settings.organization.disableDialog.confirm': 'Disable',
  // @w2c-end:organization

  // --- W2c: settings/executors (anchor; keys go below) ---
  'settings.executors.connected.custom':
    'Your custom CLI is registered and available to every org.',
  'settings.executors.connected.builtin':
    'This CLI is registered — the daemon can now launch agents on it.',
  'settings.executors.back': 'Back to executors',
  'settings.executors.connectCli': 'Connect a CLI',
  'settings.executors.done': 'Done',
  'settings.executors.assignment.title': 'Per-agent executor assignment',
  'settings.executors.assignment.body':
    "Assign executors to individual agents from the {link}. Each agent's executor (claude, codex, opencode, pi) is set during enrollment and cannot be changed from Settings.",
  'settings.executors.assignment.agentsPage': 'Agents page',
  'settings.executors.profiles.title': 'Custom CLIs',
  'settings.executors.profiles.description':
    'Custom executor profiles you connected. Removing one deletes it from the machine-global runtime store.',
  'settings.executors.profiles.loading': 'Loading custom CLIs…',
  'settings.executors.profiles.loadError': 'Could not load custom executor profiles.',
  'settings.executors.profiles.loadErrorDetail':
    'Could not load custom executor profiles. {detail}',
  'settings.executors.profiles.empty':
    'No custom CLIs registered — connect one with {action} below.',
  'settings.executors.profiles.onMachine': 'on this machine',
  'settings.executors.profiles.notOnMachine': 'not on this machine',
  'settings.executors.profiles.executable': 'Executable: {value}',
  'settings.executors.profiles.noExecutable': 'No executable recorded for this profile.',
  'settings.executors.profiles.path': 'Path: {value}',
  'settings.executors.profiles.removing': 'Removing…',
  'settings.executors.profiles.confirmRemove': 'Confirm remove',
  'settings.executors.profiles.remove': 'Remove',
  'settings.executors.profiles.removeFailed': 'Could not remove this profile.',
  'settings.executors.binaries.intro':
    'Where each built-in executor CLI binary lives on this machine. Paths are stored in machine-local runtime config and take effect on the next agent spawn. Register one with {connect} below, or expand {advanced} on a row to type the absolute path yourself — there is no automatic scan.',
  'settings.executors.binaries.loading': 'Loading registry…',
  'settings.executors.binaries.loadError': 'Could not load the executor binary registry.',
  'settings.executors.binaries.loadErrorDetail':
    'Could not load the executor binary registry. {detail}',
  'settings.executors.binaries.fresh.title': 'No executor CLI is registered on this machine',
  'settings.executors.binaries.fresh.body':
    "The daemon can't spawn agents until at least one executor CLI binary is registered. Use {connect} below, or expand {advanced} on a row to enter an absolute path (for example, the output of {command}).",
  'settings.executors.binaries.validity.valid': 'valid',
  'settings.executors.binaries.validity.invalid': 'invalid path',
  'settings.executors.binaries.validity.unregistered': 'not registered',
  'settings.executors.binaries.registeredPath': 'Registered path: {path}',
  'settings.executors.binaries.noPath':
    'No path registered — the daemon cannot spawn {kind} agents until you connect it.',
  'settings.executors.binaries.advanced': 'Advanced: enter path manually',
  'settings.executors.binaries.updatePath': 'Update binary path',
  'settings.executors.binaries.registerPath': 'Register binary path',
  'settings.executors.binaries.validating': 'Validating…',
  'settings.executors.binaries.validate': 'Validate',
  'settings.executors.binaries.registering': 'Registering…',
  'settings.executors.binaries.register': 'Register',
  'settings.executors.binaries.checkValid':
    'Looks good — this path is absolute, exists, and is executable.',
  'settings.executors.binaries.checkInvalid': 'This path is not valid.',
  'settings.executors.binaries.validationFailed': 'Validation failed.',
  'settings.executors.binaries.registerFailed': 'Could not register this path.',
  // @w2c-end:executors

  // --- W2c: settings/capacity (anchor; keys go below) ---
  'settings.capacity.field.queue_workers': 'Task session limit',
  'settings.capacity.field.host_global_session_cap': 'Overall supervised-session limit',
  'settings.capacity.fieldJoiner': ' and ',
  'settings.capacity.consequence.below':
    'The overall session limit ({cap}) is lower than the total worker slots ({total}). When demand is high, some sessions may wait. You can still save this setting.',
  'settings.capacity.consequence.above':
    'The overall session limit ({cap}) is higher than the total worker slots ({total}). Raising this limit alone does not add worker slots.',
  'settings.capacity.consequence.aligned':
    'These limits match. Other HappyRanch or service-provider limits may still reduce concurrent work.',
  'settings.capacity.notSetInFile': 'Not explicitly saved',
  'settings.capacity.receipt': 'Values received at {time} (your device time)',
  'settings.capacity.numeric.blank': '{field} is required.',
  'settings.capacity.numeric.grammar': '{field} must be a whole number greater than zero, written in plain digits.',
  'settings.capacity.numeric.representation':
    'This value is too large for this page to handle exactly. Your entry has not been changed. This is a limit of the page, not a HappyRanch limit.',
  'settings.capacity.readUnusable': 'HappyRanch could not read the saved capacity settings. Editing is disabled until a successful refresh.',
  'settings.capacity.inconsistentResponse': 'HappyRanch returned conflicting capacity values.',
  'settings.capacity.refreshFailed': 'Could not refresh, so the current values are unverified.',
  'settings.capacity.readBlockedSave':
    'HappyRanch could not confirm the current saved version, so nothing was sent. Refresh the values before saving again.',
  'settings.capacity.unknownOutcome':
    'HappyRanch could not confirm whether the save finished. Your edits are still here. Reconnect, then check the saved values before trying again.',
  'settings.capacity.artifact.present': ' A temporary save file remains. Ask an administrator to inspect it before cleanup.',
  'settings.capacity.artifact.unknown': ' HappyRanch could not determine whether a temporary save file remains. Ask an administrator to inspect the configuration storage before cleanup.',
  'settings.capacity.error.unauthorized': 'You need a valid daemon access token to save these settings. No values were changed by this request.',
  'settings.capacity.error.confirmOverride': 'Confirm the environment override before saving.',
  'settings.capacity.error.rejectedValues':
    'The daemon rejected these values. {workersLabel} and {capLabel} must each be a whole number greater than zero, and the reason must not be blank.',
  'settings.capacity.error.ifMatch':
    'Refresh the saved settings before saving again. This request did not publish new values.',
  'settings.capacity.error.auditFailed':
    'HappyRanch could not create the required change record, so this request did not change the configuration.',
  'settings.capacity.error.configWriteFailed': 'HappyRanch could not write the configuration. This request did not save new values.',
  'settings.capacity.error.publicationUncertain':
    'HappyRanch replaced the configuration file, but could not finish verifying or cleaning up the save. Do not assume the new values will be used at the next start. Check the saved values before trying again.',
  'settings.capacity.ackReset': 'The environment override changed; confirm it again before saving.',
  'settings.capacity.writeLocked':
    'Review the latest saved values before saving again. Keep your edits using the latest version, or discard your edits and use the latest saved values.',
  'settings.capacity.reasonRequired': 'Reason for change is required.',
  'settings.capacity.reasonTooLong': 'Reason for change must be {max} characters or fewer.',
  'settings.capacity.checkFields': 'Check the highlighted fields before saving. Nothing was sent.',
  'settings.capacity.conflict.representation':
    'Latest saved values are outside the range this editor can represent exactly.',
  'settings.capacity.conflict.unreadable': 'Latest saved values could not be read.',
  'settings.capacity.conflict.changed': 'The saved settings changed elsewhere. Your edits are still here.',
  'settings.capacity.title': 'Capacity',
  'settings.capacity.description':
    'Set the limits HappyRanch uses to start and allow work sessions. Saving does not change running work; the new values are considered the next time the HappyRanch service starts.',
  'settings.capacity.pill.allOrgs': 'All organizations',
  'settings.capacity.pill.restartRequired': 'Not applied to running sessions',
  'settings.capacity.bearer':
    'A daemon access token is required. HappyRanch cannot link that token to a verified person. These settings affect all organizations.',
  'settings.capacity.loading': 'Loading daemon capacity…',
  'settings.capacity.loadError': 'Could not load daemon capacity. No values are displayed. {detail}',
  'settings.capacity.initialRepresentation':
    'Capacity values are outside the range this editor can represent exactly. No values are displayed and editing is unavailable.',
  'settings.capacity.latestRepresentation':
    'Latest capacity values are outside the range this editor can represent exactly. Editing is unavailable against this read.',
  'settings.capacity.lastKnownPointer': 'Previously received values are shown below under “Last known”.',
  'settings.capacity.editingUnavailableRead': 'Editing is unavailable against this read.',
  'settings.capacity.refreshFailedKept':
    'Your draft, reason and acknowledgment are kept. Saving is blocked until a successful read confirms the saved revision.',
  'settings.capacity.running.noteLastKnown':
    '— previously received values; the latest refresh did not confirm them',
  'settings.capacity.running.noteObserved': '— observed from the running service; saving does not change them',
  'settings.capacity.running.lastKnown': 'Last known',
  'settings.capacity.running.now': 'Limits in effect',
  'settings.capacity.card.workersLabel': 'Task-session capacity at startup',
  'settings.capacity.card.workersDescription': 'Task-session capacity set when HappyRanch started. Shared across all organizations; this is not a count of sessions currently running.',
  'settings.capacity.card.capLabel': 'Overall supervised-session limit in effect',
  'settings.capacity.card.unavailable': 'Unavailable',
  'settings.capacity.card.capUnavailableDescription':
    'Unavailable — {reason}. The runtime effect of a saved value cannot be verified from this page.',
  'settings.capacity.card.capDescription':
    'Maximum supervised sessions HappyRanch will allow at once. This is a limit, not a count of sessions in use or available.',
  'settings.capacity.effectiveDiffers':
    'Configured at startup: {startup}. Limit in effect now: {effective}. {reason} These are limits, not counts of sessions in use or available.',
  'settings.capacity.table.title': 'Current and saved limits',
  'settings.capacity.table.setting': 'Setting',
  'settings.capacity.table.running': 'In effect since startup',
  'settings.capacity.table.saved': 'Saved configuration',
  'settings.capacity.table.next': 'Expected after next start',
  'settings.capacity.table.note':
    'Expected next start is best effort, based on the configuration observed by this daemon and assuming an unchanged environment and worker topology. It is not a guarantee.',
  'settings.capacity.restartPending': 'Restart required for saved changes',
  'settings.capacity.noRestartPending': 'Saved and running values match',
  'settings.capacity.restartPendingNote':
    'A saved value differs from the value in effect now. Saving does not apply it to running sessions, and this page cannot restart HappyRanch.',
  'settings.capacity.runningMatches':
    'The saved and running startup values match. This confirms only that the numbers are equal; it does not show when or why they became equal.',
  'settings.capacity.change.title': 'Change saved settings',
  'settings.capacity.workersHelp':
    'Maximum task sessions HappyRanch can run at once across all organizations. Other HappyRanch or service-provider limits may reduce the number that actually run.',
  'settings.capacity.capHelp':
    'Maximum combined sessions from task, thread, dream, wake, and schedule work. It does not limit every process on the machine and does not include background Assistant or job processes.',
  'settings.capacity.draft.changes': 'Your edits differ from the saved values',
  'settings.capacity.draft.matches': 'Your entries match the saved values',
  'settings.capacity.draft.capLabel': 'Overall session limit',
  'settings.capacity.draft.poolTotal': 'Total worker slots',
  'settings.capacity.draft.poolBreakdown': '{task} task slots + {other} other worker slots',
  'settings.capacity.draft.poolRepresentation':
    'Total worker slots are outside the range this editor can represent exactly.',
  'settings.capacity.rationaleOnly': 'The values are unchanged from the saved file; only the reason differs.',
  'settings.capacity.reason.label': 'Reason for change',
  'settings.capacity.reason.limitReached': ' — limit reached',
  'settings.capacity.reason.help': 'Briefly explain why you are changing these limits. Your reason is sent with the save request.',
  'settings.capacity.reason.placeholder': 'e.g. Queue delay grew after adding the second team; raising task slots.',
  'settings.capacity.override.heading': 'An environment setting overrides a saved value',
  'settings.capacity.override.shadowed': {
    one: '{fields} is set by the environment.',
    other: '{fields} are set by the environment.',
  },
  'settings.capacity.override.preview':
    'Expected next start with this draft: {workersLabel} {workers}, {capLabel} {cap}. Assumes unchanged environment and worker topology.',
  'settings.capacity.override.noPreview':
    'The expected next start cannot be previewed against the current read. Your acknowledgment is kept as entered.',
  'settings.capacity.override.ack':
    'I understand that the environment setting will still override the saved value after a restart.',
  'settings.capacity.save': 'Save for next start',
  'settings.capacity.saving': 'Saving…',
  'settings.capacity.discard': 'Discard draft',
  'settings.capacity.refresh': 'Refresh capacity values',
  'settings.capacity.unsaved': 'Unsaved changes. If you leave or reload, these edits will be lost.',
  'settings.capacity.outcome.saving': 'Saving for next start…',
  'settings.capacity.outcome.savedPending': 'Saved for the next start. Limits in effect now have not changed.',
  'settings.capacity.outcome.savedNoPending': 'Saved. These values already match the limits in effect.',
  'settings.capacity.outcome.overridden': {
    one: 'Environment setting takes priority: {fields} will continue to use the environment value. Expected after next start: {workersLabel} {workers}, {capLabel} {cap}.',
    other: 'Environment settings take priority: {fields} will continue to use the environment values. Expected after next start: {workersLabel} {workers}, {capLabel} {cap}.',
  },
  'settings.capacity.submitted': 'Your last save attempt sent {workersLabel} {workers} and {capLabel} {cap} using configuration version {revision}.',
  'settings.capacity.draftHeld':
    'You have since changed the fields to {workersLabel} {workers}, {capLabel} {cap}. Those edits were not part of the last save attempt.',
  'settings.capacity.checkSaved': 'Check saved values',
  'settings.capacity.conflictUnusableSuffix':
    'The latest saved values could not be read, so the update choices are unavailable. Refresh successfully before saving again.',
  'settings.capacity.changedElsewhere': 'Configuration changed elsewhere.',
  'settings.capacity.compare.acceptedBase': 'Version you started from',
  'settings.capacity.compare.yourDraft': 'Your edits',
  'settings.capacity.compare.currentlySaved': 'Currently saved',
  'settings.capacity.compare.pair': '{workersLabel} {workers}, {capLabel} {cap}',
  'settings.capacity.compare.couldNotBeRead': 'Could not be read',
  'settings.capacity.rebase': 'Keep my edits and use latest saved version',
  'settings.capacity.acceptLatest': 'Discard my edits and use latest saved version',
  'settings.capacity.details.summary': 'Capacity details',
  'settings.capacity.details.configurationKeys': 'Configuration keys',
  'settings.capacity.details.producerEnvelope': 'Total worker slots',
  'settings.capacity.details.producerComponents': 'Worker-slot breakdown',
  'settings.capacity.details.componentsValue':
    'Task {task} · Thread {thread} · Dream {dream} · Wake {wake} · Schedule {schedule}',
  'settings.capacity.details.runningProvenance': 'Source of running values',
  'settings.capacity.details.revision': 'Configuration version',
  'settings.capacity.details.audit': 'Change record',
  'settings.capacity.details.auditBody':
    'Your reason is sent with the save request. The audit log records the organization and daemon access token, not a verified person. This page cannot guarantee that every failed or uncertain save produces a completed audit entry.',
  'settings.capacity.dialog.title': 'Discard unsaved capacity changes?',
  'settings.capacity.dialog.description':
    'Your draft has not been saved. Stay to keep editing, or discard it and continue navigating.',
  'settings.capacity.dialog.stay': 'Stay on page',
  'settings.capacity.dialog.discardContinue': 'Discard and continue',
  'settings.capacity.headline.notSet':
    'These values are not explicitly saved, so they do not match any explicit values shown below.',
  'settings.capacity.headline.matchesSubmitted':
    'The currently saved values match your last save attempt ({workers} / {cap}). This does not prove that the attempt caused the change.',
  'settings.capacity.headline.matchesDraft':
    'The currently saved values match your current edits ({draftWorkers} / {draftCap}), but not your last save attempt ({workers} / {cap}). This comparison does not confirm that either was saved by your request.',
  'settings.capacity.headline.unchanged':
    'The saved values still match the version you started from. HappyRanch still cannot confirm the result of your save attempt.',
  'settings.capacity.headline.differs': 'The saved values still differ from what you submitted.',
  // @w2c-end:capacity
} as const satisfies Catalog;

export default en;
