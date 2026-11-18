import 'connection-tokens.css';
import './styles.css';
import { Connection } from './bridge';
import { ClientView } from './client';
import { HostView } from './host';
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
const localized: { node: HTMLElement; key: keyof Copy; ordinal?: number }[] = [];
const copy = (): Copy => catalogs[locale];
function text(key: keyof Copy, ordinal?: number): string {
  return copy()[key] + (ordinal === undefined ? '' : ` ${new Intl.NumberFormat(locale).format(ordinal)}`);
}
function element<K extends keyof HTMLElementTagNameMap>(tag: K, key?: keyof Copy, ordinal?: number): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (key) { localized.push({ node, key, ordinal }); node.textContent = text(key, ordinal); }
  return node;
}
function translate(): void {
  document.documentElement.lang = locale;
  document.title = copy().title;
  localized.forEach(({ node, key, ordinal }) => { node.textContent = text(key, ordinal); });
  language.setAttribute('aria-label', copy().language);
  updateStatus();
  if (role === 'client') clientView.translate(); else if (role === 'host') hostView.translate();
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
  if (role === 'client') clientView.update(snapshot as ClientSnapshot);
  else hostView.update(snapshot as HostSnapshot);
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
  // Each role view owns its card/live region; the shell runtime line stays for host.
  status.hidden = next === 'client';
  const view = next === 'client' ? clientView : hostView;
  for (const each of [clientView, hostView]) { each.root.hidden = true; each.region.textContent = ''; }
  // Completions from an earlier selection (or overtaken by newer native state) are ignored.
  clientView.reset(); hostView.reset();
  const selected = (): boolean => generation === selectedGeneration && !ended;
  if (!connection?.has(`${role}.status`)) { showUnsupported(); return; }
  try {
    stopRole = connection.on(`${role}.stateChanged`, (raw) => {
      if (generation !== selectedGeneration) return;
      observedEvent = true;
      observe((JSON.parse(raw) as SnapshotEvent<HostSnapshot | ClientSnapshot>).snapshot);
    });
    const first = view.fence();
    const raw = await connection.call(`${role}.status`, '{}');
    if (!selected()) return;
    if (!observedEvent && view.latest(first)) observe(JSON.parse(raw) as HostSnapshot | ClientSnapshot);
    view.root.hidden = false;
    // Saved discovery failure is a client-screen state, not an unsupported bridge;
    // status is reread independently of discovery.
    if (next === 'client' && connection.has('client.savedComputers')) {
      try { await connection.call('client.savedComputers', '{}'); } catch { /* computers/savedAccess stay as native reports them. */ }
      if (!selected()) return;
      const token = clientView.fence();
      try {
        const fresh = await connection.call('client.status', '{}');
        if (selected() && clientView.latest(token)) observe(JSON.parse(fresh) as ClientSnapshot);
      } catch { /* The last native snapshot stays; Check again remains available after actions. */ }
    }
  } catch { if (selected()) showUnsupported(); }
}
const clientView = new ClientView({
  connection: () => connection, locale: () => locale,
  current: () => !ended && role === 'client' && connection !== null,
});
clientView.root.hidden = true;
const hostView = new HostView({
  connection: () => connection, locale: () => locale,
  current: () => !ended && role === 'host' && connection !== null,
  observe: (snapshot) => { observe(snapshot); },
});
hostView.root.hidden = true;
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
  container.append(header, element('h1', 'title'), element('p', 'intro'), roles, roleDescription, status,
    clientView.region, clientView.root, hostView.region, hostView.root);
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
