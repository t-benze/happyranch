/**
 * Model + reasoning-effort inputs shared by New task and Revisit (THR-356).
 *
 * Options and disabling come from the target agent's executor through
 * `useTaskSelectionOptions`; this component adds no catalog of its own. Each
 * field states in words what will be sent, so "carry", "change" and "clear"
 * are never implicit.
 */
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import { useTranslation } from '@/hooks/i18n';
import {
  effortBlockFor,
  type TaskSelectionFieldBlock,
  type TaskSelectionOptions,
} from '@/hooks/task-selection';
import type { InvalidTaskSelectionDetail } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';
import { fieldIntent, type CarriedSelection, type SelectionDraft } from './taskSelection';

/** Radix Select items cannot carry an empty value. */
const DEFAULT_VALUE = '__agent_default__';

const BLOCK_KEYS: Record<Exclude<TaskSelectionFieldBlock, null | 'undeclared'>, MessageKey> = {
  'no-agent': 'tasks.selection.block.noAgent',
  loading: 'tasks.selection.block.loading',
  error: 'tasks.selection.block.error',
  'unknown-agent': 'tasks.selection.block.unknownAgent',
  'executor-unavailable': 'tasks.selection.block.executorUnavailable',
  'needs-pinned-model': 'tasks.selection.block.needsPinnedModel',
};

interface Props {
  idPrefix: string;
  options: TaskSelectionOptions;
  draft: SelectionDraft;
  onChange: (next: SelectionDraft) => void;
  /** Present on revisit: the predecessor's override, carried unless edited. */
  carried?: CarriedSelection;
  disabled?: boolean;
}

export function TaskSelectionFields({
  idPrefix, options, draft, onChange, carried, disabled = false,
}: Props): JSX.Element {
  const { t } = useTranslation();
  const executor = options.executor ?? '';
  const cliDefault = t('tasks.selection.cliDefault');
  // The model that would apply decides whether effort is allowed (OpenCode).
  const effectiveModel = draft.model !== '' ? draft.model : options.agentModel;
  const modelBlock = options.modelBlock;
  const effortBlock = effortBlockFor(options, effectiveModel);

  const blockText = (block: TaskSelectionFieldBlock, field: 'model' | 'effort'): string | null => {
    if (block === null) return null;
    if (block === 'undeclared') {
      return t(field === 'model' ? 'tasks.selection.block.modelUndeclared'
        : 'tasks.selection.block.effortUndeclared', { executor });
    }
    return t(BLOCK_KEYS[block], { executor });
  };

  const intentText = (value: string, previous: string | null | undefined, agentDefault: string | null): string => {
    const fallback = agentDefault ?? cliDefault;
    switch (fieldIntent(value, previous)) {
      case 'carry': return t('tasks.selection.intent.carry', { value });
      case 'change': return t('tasks.selection.intent.change', { from: previous ?? '', to: value });
      case 'clear': return t('tasks.selection.intent.clear', { value: previous ?? '', fallback });
      case 'set': return t('tasks.selection.intent.set', { value });
      default: return t('tasks.selection.intent.default', { fallback });
    }
  };

  const effortChoices = [...options.effortValues];
  for (const extra of [carried?.effort ?? '', draft.effort]) {
    if (extra !== '' && !effortChoices.includes(extra)) effortChoices.push(extra);
  }

  const field = (
    name: 'model' | 'effort',
    block: TaskSelectionFieldBlock,
    value: string,
    previous: string | null | undefined,
    agentDefault: string | null,
    control: JSX.Element,
  ): JSX.Element => {
    const blocked = blockText(block, name);
    // A blocked field can still hold a value (a carried override, or one set
    // before the block applied). It is never dropped silently: say so and
    // keep an explicit clear action available.
    const canClear = !disabled && value !== '' && (block !== null || previous !== undefined);
    return (
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-${name}`} className="text-text-secondary mb-1 block text-xs font-medium">
          {t(name === 'model' ? 'tasks.selection.model' : 'tasks.selection.effort')}
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1 basis-48">{control}</div>
          {canClear && (
            <Button
              type="button" variant="outline" size="sm" className="h-auto max-w-full whitespace-normal break-words"
              onClick={() => onChange({ ...draft, [name]: '' })}
            >
              {t(name === 'model' ? 'tasks.selection.clearModel' : 'tasks.selection.clearEffort')}
            </Button>
          )}
        </div>
        <p id={`${idPrefix}-${name}-help`} className="text-text-muted mt-1 break-words text-xs">
          {blocked !== null && <span>{blocked} </span>}
          {(blocked === null || value !== '' || previous != null) && (
            <span>{intentText(value, previous, agentDefault)}</span>
          )}
          {blocked !== null && value !== '' && (
            <span className="text-feedback-warning"> {t('tasks.selection.blockedValue')}</span>
          )}
        </p>
      </div>
    );
  };

  return (
    <div className="grid gap-3" data-selection-state={options.state}>
      {field('model', modelBlock, draft.model, carried ? carried.model : undefined, options.agentModel, (
        <Input
          id={`${idPrefix}-model`}
          value={draft.model}
          disabled={disabled || modelBlock !== null}
          aria-describedby={`${idPrefix}-model-help`}
          placeholder={t('tasks.selection.defaultOption')}
          onChange={(event) => onChange({ ...draft, model: event.target.value })}
        />
      ))}
      {field('effort', effortBlock, draft.effort, carried ? carried.effort : undefined, options.agentEffort, (
        <Select
          value={draft.effort === '' ? DEFAULT_VALUE : draft.effort}
          disabled={disabled || effortBlock !== null}
          onValueChange={(next) => onChange({ ...draft, effort: next === DEFAULT_VALUE ? '' : next })}
        >
          <SelectTrigger id={`${idPrefix}-effort`} className="h-9 w-full" aria-describedby={`${idPrefix}-effort-help`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={DEFAULT_VALUE}>{t('tasks.selection.defaultOption')}</SelectItem>
            {effortChoices.map((choice) => (
              <SelectItem key={choice} value={choice}>{choice}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      ))}
    </div>
  );
}

/** The daemon's refusal, verbatim reason plus the options it named. */
export function TaskSelectionRefusal({ refusal }: { refusal: InvalidTaskSelectionDetail }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div role="alert" className="border-feedback-danger text-feedback-danger rounded-sm border p-3 text-sm">
      <p className="font-medium">{t('tasks.selection.refusal.title')}</p>
      <p className="mt-1 break-words">{refusal.reason}</p>
      <p className="mt-1 break-words text-xs">
        {t('tasks.selection.refusal.options', {
          executor: refusal.executor,
          options: refusal.valid_options.length > 0
            ? refusal.valid_options.join(', ') : t('tasks.selection.refusal.noOptions'),
        })}
      </p>
    </div>
  );
}
