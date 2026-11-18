const en = {
  title: 'Connection', language: 'Language', loading: 'Reading connection availability…',
  intro: 'Manage this computer’s connection from the native app, even when its runtime is unavailable.',
  host: 'Host', client: 'Client', hostDescription: 'This computer runs the runtime that other computers can reach.',
  clientDescription: 'This computer connects to a runtime on another computer.',
  shellTitle: 'Connection controls are being prepared',
  shellDescription: 'Setup, invitations and saved access management are not available in this view yet.',
  native: 'Invitations and permissions stay in native dialogs.',
  offline: 'The runtime is offline. This connection view remains available.',
  restarting: 'The runtime is restarting. This connection view remains available.',
  unknown: 'Runtime availability has not been confirmed.',
  available: 'The runtime is available.',
  unsupportedTitle: 'Connection controls are unavailable',
  unsupported: 'Use a version of the native app that supports connection controls. A future release will provide the local command below when these controls cannot be opened.',
  futureCli: 'Planned CLI · not available in this build',
};
export type Copy = typeof en;
export type Locale = 'en' | 'zh-CN';
const zh: Copy = {
  title: '连接', language: '语言', loading: '正在读取连接功能的可用状态…',
  intro: '即使运行时不可用，也能从原生应用管理这台电脑的连接。',
  host: '主机', client: '客户端', hostDescription: '这台电脑运行可供其他电脑访问的运行时。',
  clientDescription: '这台电脑连接另一台电脑上的运行时。',
  shellTitle: '连接功能正在准备中',
  shellDescription: '此界面尚未提供设置、邀请和已保存访问权限的管理功能。',
  native: '邀请和权限操作始终在原生对话框中进行。',
  offline: '运行时已离线。此连接界面仍然可用。',
  restarting: '运行时正在重启。此连接界面仍然可用。',
  unknown: '尚未确认运行时是否可用。', available: '运行时可用。',
  unsupportedTitle: '连接功能不可用',
  unsupported: '请使用支持连接功能的原生应用版本。后续版本将提供以下本地命令，用于无法打开此界面的情况。',
  futureCli: '计划中的命令行功能 · 此版本尚未提供',
};
export const catalogs: Record<Locale, Copy> = { en, 'zh-CN': zh };
