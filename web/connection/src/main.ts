import 'connection-tokens.css';
import './styles.css';
import { Connection } from './bridge';
import { catalogs } from './strings';
import type { Copy, Locale } from './strings';
import type { HostSnapshot, ClientSnapshot, SnapshotEvent, Runtime } from './types';

const root = document.querySelector<HTMLElement>('#connection');
if (!root) throw new Error('Missing connection root');
const container = root;
let locale: Locale = navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
let role: 'host' | 'client';
let runtime: Runtime | 'stale' | 'unavailable' = 'unknown';
let reading = true;
let connection: Connection | null = null;
let stopRole: (() => void) | undefined;
let generation = 0;
let ended = false;
const localized: { node: HTMLElement; key: keyof Copy }[] = [];
const copy = (): Copy => catalogs[locale];
function element<K extends keyof HTMLElementTagNameMap>(tag: K, key?: keyof Copy): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (key) { localized.push({ node, key }); node.textContent = copy()[key]; }
  return node;
}
function translate(): void {
  document.documentElement.lang = locale;
  document.title = copy().title;
  localized.forEach(({ node, key }) => { node.textContent = copy()[key]; });
  language.setAttribute('aria-label', copy().language);
  updateStatus();
}
const language = document.createElement('select');
language.id = 'language';
for (const [value, text] of [['en', 'English'], ['zh-CN', '简体中文']]) {
  const option = document.createElement('option'); option.value = value; option.textContent = text; language.append(option);
}
language.value = locale;
language.addEventListener('change', () => { locale = language.value as Locale; translate(); });
const status = element('p');
status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
function updateStatus(): void { status.textContent = copy()[reading ? 'loading' : runtime]; }
function observe(snapshot: HostSnapshot | ClientSnapshot): void {
  const limits = connection?.hello.observationLimits[role];
  if (!limits || limits.includes('runtime') || limits.includes('observation') || snapshot.observation === 'unavailable') {
    runtime = 'unavailable';
  } else if (snapshot.observation === 'stale') {
    runtime = 'stale';
  } else {
    runtime = snapshot.observation === 'known' ? snapshot.runtime : 'unknown';
  }
  reading = false; updateStatus();
  if (role === 'client') renderSaved(snapshot as ClientSnapshot);
}
function hide(): void {
  generation++; ended = true; stopRole?.(); connection?.dispose(); container.replaceChildren(); container.hidden = true;
}
async function loadRole(next: 'host' | 'client'): Promise<void> {
  stopRole?.(); role = next; reading = true; runtime = 'unknown'; updateStatus();
  const selectedGeneration = ++generation;
  let observedEvent = false;
  roleButtons.forEach((button, value) => { button.setAttribute('aria-pressed', String(value === role)); });
  roleDescription.textContent = copy()[role === 'host' ? 'hostDescription' : 'clientDescription'];
  descriptionEntry.key = role === 'host' ? 'hostDescription' : 'clientDescription';
  if (!connection?.has(`${role}.status`)) { showUnsupported(); return; }
  try {
    stopRole = connection.on(`${role}.stateChanged`, (raw) => {
      if (generation !== selectedGeneration) return;
      observedEvent = true;
      observe((JSON.parse(raw) as SnapshotEvent<HostSnapshot | ClientSnapshot>).snapshot);
    });
    const raw = await connection.call(`${role}.status`, '{}');
    if (generation === selectedGeneration && !observedEvent) observe(JSON.parse(raw) as HostSnapshot | ClientSnapshot);
    if (next === 'client') await refreshSaved(selectedGeneration);
    savedPanel.hidden = next !== 'client' || !connection.has('client.savedComputers');
  } catch { if (generation === selectedGeneration && !ended) showUnsupported(); }
}
const savedPanel = element('section');
savedPanel.className = 'panel'; savedPanel.hidden = true;
const savedWarning = element('p', 'savedNotice');
const savedUnknown = element('p', 'savedUnknown');
const savedEmpty = element('p', 'savedEmpty');
const savedError = element('p', 'savedError'); savedError.hidden = true;
savedError.setAttribute('role', 'status');
const savedList = element('ul');
let savedKey = '';
let clientState: ClientSnapshot | null = null;
const clientButtons: { node: HTMLButtonElement; command: string }[] = [];
async function clientAction(command: string, args = '{}'): Promise<void> {
  if (!connection) return;
  const current = generation;
  savedError.hidden = true;
  try { await connection.call(command, args); }
  catch { if (current === generation) savedError.hidden = false; }
  if (current === generation && !ended) await refreshSaved(current);
}
function actionButton(command: string, key: keyof Copy, args = '{}'): HTMLButtonElement {
  const button = element('button', key); button.type = 'button';
  button.addEventListener('click', () => { void clientAction(command, args); });
  clientButtons.push({ node: button, command });
  return button;
}
savedPanel.append(element('h2', 'savedTitle'), savedWarning, savedUnknown, savedEmpty, savedError,
  actionButton('client.acceptInvite', 'acceptInvite'),
  actionButton('client.savedComputers', 'refreshSaved'),
  actionButton('client.retrySave', 'retrySave'),
  actionButton('client.cancelAttempt', 'cancelAttempt'),
  actionButton('client.disconnect', 'disconnect'), savedList);
async function refreshSaved(current: number): Promise<void> {
  if (!connection?.has('client.savedComputers')) return;
  try {
    await connection.call('client.savedComputers', '{}');
    const raw = await connection.call('client.status', '{}');
    if (current === generation && !ended) observe(JSON.parse(raw) as ClientSnapshot);
  } catch { if (current === generation) savedError.hidden = false; }
}
function renderSaved(snapshot: ClientSnapshot): void {
  clientState = snapshot;
  savedWarning.hidden = snapshot.savedAccess === 'saved' || snapshot.stage !== 'connected';
  savedUnknown.hidden = snapshot.computers !== null && snapshot.savedAccess !== 'unknown';
  savedEmpty.hidden = snapshot.computers?.length !== 0;
  const key = JSON.stringify(snapshot.computers);
  if (savedKey !== key) {
    savedKey = key;
    for (let i = localized.length - 1; i >= 0; i--) {
      if (savedList.contains(localized[i].node)) localized.splice(i, 1);
    }
    for (let i = clientButtons.length - 1; i >= 0; i--) {
      if (savedList.contains(clientButtons[i].node)) clientButtons.splice(i, 1);
    }
    savedList.replaceChildren();
    for (const computer of snapshot.computers ?? []) {
      const row = element('li');
      const label = element('span'); label.textContent = computer.label;
      row.append(label, actionButton('client.connectSaved', 'connectSaved', JSON.stringify({ computerRef: computer.computerRef })),
        actionButton('client.removeSaved', 'removeSaved', JSON.stringify({ computerRef: computer.computerRef })));
      savedList.append(row);
    }
  }
  for (const { node, command } of clientButtons) {
    node.hidden = !connection?.has(command);
    const idle = ['idle', 'failed'].includes(clientState.stage);
    node.disabled = command === 'client.retrySave' ? clientState.stage !== 'connected' || !['notSaved', 'needsPermission', 'unknown'].includes(clientState.savedAccess)
      : command === 'client.cancelAttempt' ? !['validating', 'joining', 'pairing', 'waitingApproval', 'saving', 'opening'].includes(clientState.stage)
      : command === 'client.disconnect' ? clientState.stage !== 'connected'
      : command === 'client.savedComputers' ? false : !idle;
  }
}
const roleButtons = new Map<'host' | 'client', HTMLButtonElement>();
const roleDescription = element('p');
const descriptionEntry = { node: roleDescription, key: 'hostDescription' as keyof Copy };
localized.push(descriptionEntry);
function showUnsupported(): void {
  stopRole?.(); connection?.dispose();
  localized.length = 0; container.replaceChildren();
  const heading = element('h1', 'unsupportedTitle');
  const label = element('label', 'language'); label.htmlFor = 'language';
  const code = element('code'); code.textContent = 'happyranch connection';
  container.append(label, language, heading, element('p', 'unsupported'), element('p', 'futureCli'), code);
  translate(); container.hidden = false;
}
function mount(): void {
  const header = element('header');
  const brand = element('span'); brand.textContent = 'HappyRanch'; brand.className = 'brand';
  const label = element('label', 'language'); label.htmlFor = 'language';
  const localeControl = element('div'); localeControl.className = 'locale'; localeControl.append(label, language);
  header.append(brand, localeControl);
  const roles = element('div'); roles.className = 'roles';
  for (const availableRole of connection?.hello.roles.filter((value) => connection?.has(`${value}.status`)) ?? []) {
    const button = element('button', availableRole); button.type = 'button';
    button.addEventListener('click', () => { void loadRole(availableRole); });
    roleButtons.set(availableRole, button); roles.append(button);
  }
  const panel = element('section'); panel.className = 'panel';
  panel.append(element('h2', 'shellTitle'), element('p', 'shellDescription'), element('p', 'native'));
  container.append(header, element('h1', 'title'), element('p', 'intro'), roles, roleDescription, status, savedPanel, panel);
  translate(); container.hidden = false;
}
window.addEventListener('pagehide', hide, { once: true });
window.addEventListener('beforeunload', hide, { once: true });
void Connection.detect(window.happyranchConnection, hide).then((detected) => {
  if (!detected || ended) { detected?.dispose(); return; }
  connection = detected;
  if (!detected.hello.roles.some((value) => detected.has(`${value}.status`))) { showUnsupported(); return; }
  mount();
  const firstRole = detected.hello.roles.find((value) => detected.has(`${value}.status`));
  if (firstRole) void loadRole(firstRole);
});
