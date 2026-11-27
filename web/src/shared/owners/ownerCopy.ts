/**
 * Localized owner context (page · computer · org) for owner-gate and save copy.
 * The page id and org slug identify the owner; the strings are presentation.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';
import type { OwnerRecord } from '@/lib/owners/ownerRegistry';

type Translate = (key: MessageKey, params?: MessageParams) => string;

const PAGE_KEYS: Record<OwnerRecord['page'], MessageKey> = {
  'settings.organization': 'owners.page.settings.organization',
  'settings.workHours': 'owners.page.settings.workHours',
  'settings.capacity': 'owners.page.settings.capacity',
  'settings.executors': 'owners.page.settings.executors',
  'agents.agent': 'owners.page.agents.agent',
  'agents.systemPrompt': 'owners.page.agents.systemPrompt',
  'agents.escalationPolicy': 'owners.page.agents.escalationPolicy',
  'agents.create': 'owners.page.agents.create',
  'skills.assignment': 'owners.page.skills.assignment',
  'skills.customCreate': 'owners.page.skills.customCreate',
  'skills.customDetail': 'owners.page.skills.customDetail',
  'workHours.tier': 'owners.page.workHours.tier',
  'settings.taskAttributes': 'owners.page.settings.taskAttributes',
  'tasks.attributes': 'owners.page.tasks.attributes',
};

/** `{ page, computer, org }` params for an owner, localized. */
export function ownerContextParams(
  owner: Pick<OwnerRecord, 'page' | 'subject' | 'org' | 'computer'>,
  t: Translate,
): MessageParams {
  return {
    page: t(PAGE_KEYS[owner.page], { subject: owner.subject ?? '' }),
    computer: owner.computer ?? t('owners.computer.thisComputer'),
    org: owner.org ?? t('owners.org.none'),
  };
}
