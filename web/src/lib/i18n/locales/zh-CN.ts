/**
 * Simplified Chinese catalog — explicit locale-specific plural forms.
 *
 * Chinese has a single CLDR plural category (`other`); English requires
 * `one` + `other`. `validateCatalogParity` rejects a missing form, an
 * unexpected form, or any placeholder-set drift between locales.
 */
import type { MessageKey, MessageValue } from '../catalog';

export const zhCN: Record<MessageKey, MessageValue> = {
  'app.name': 'HappyRanch',
  'common.retry': '重试',
  'common.cancel': '取消',
  'common.save': '保存',
  'common.dismiss': '关闭',
  'common.language': '语言',
  'common.languageDescription': '选择界面语言。',
  'common.englishOnlyNotice': '此界面尚未提供中文版本。',
  'common.translatedProbe': '已翻译探针',
  'common.greeting': '你好，{name}',
  'common.movableSlots': '将 {subject} 放在 {object} 之前',
  'common.itemCount': {
    other: '{count} 个项目',
  },
  'common.filesSelected': {
    other: '{name} 选择了 {count} 个文件',
  },
  'coverage.title': '翻译覆盖',
  'coverage.englishOnly': '仅英文 — 尚未迁移',
  'coverage.notApplicable': '无面向用户的文案',
  'coverage.summary': '已翻译 {translated}/{total} 个命名空间',
};

export default zhCN;
