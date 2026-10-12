import { describe, expect, test } from 'vitest';
import { buildDirectConnectPrompt } from './useRuntimeConnect';

const prompt = () => buildDirectConnectPrompt('my-cli', 'hrreg_test', 'http://localhost:8765', '/daemon/direct/my-cli-adapter');

describe('actual direct-connect prompt capability and update boundaries', () => {
  test('discovers installed capabilities without promising direct input2 activation', () => {
    expect(prompt()).toContain('installed CLI help/documentation');
    expect(prompt()).toContain('supported, unsupported and unknown');
    expect(prompt()).toContain('input-v1 only');
    expect(prompt()).toContain('Do not add input_options');
  });
  test('describes exact optional wire objects and literal argument substitution', () => {
    expect(prompt()).toContain('"model_id":"selected-model"');
    expect(prompt()).toContain('"reasoning_effort":"selected-effort"');
    expect(prompt()).toContain('literal argv');
    expect(prompt()).toContain('Null adds no flags');
    expect(prompt()).toContain('input_contract_version:2');
    expect(prompt()).toContain('adapter_metadata.contract_version:1');
  });
  test('distinguishes both, model-only, effort-only and neither with probe-model refusal', () => {
    expect(prompt()).toContain('Model only');
    expect(prompt()).toContain('Effort only');
    expect(prompt()).toContain('probe-model');
    expect(prompt()).toContain('truthful failure');
    expect(prompt()).toContain('Neither/unknown');
  });
  test('hands off a same-profile ordinary update without using the direct credential', () => {
    expect(prompt()).toContain('Do not execute an update or approval in this run');
    expect(prompt()).toContain('workspace_adapter_id omitted');
    expect(prompt()).toContain('required_executable_path');
    expect(prompt()).toContain('PENDING');
    expect(prompt()).toContain('Do not reuse $TOKEN');
    expect(prompt()).toContain('Do not duplicate, delete or recreate');
    expect(prompt()).toContain('/runtime/adapters/{id}/approve');
  });
  test('Chinese guidance preserves wire keys, route placeholders and shell credential quoting', () => {
    const chinese = buildDirectConnectPrompt('my-cli', "hrreg_a'b", 'http://localhost:8765', '/daemon/direct/my-cli-adapter', 'zh-CN');
    expect(chinese).toContain('检查已安装 CLI 的帮助和文档');
    expect(chinese).toContain('此直接连接仅支持 input-v1');
    expect(chinese).toContain('本次不得执行更新或审批');
    expect(chinese).toContain('/runtime/adapters/{id}/approve');
    expect(chinese).toContain('"model_id":"selected-model"');
    expect(chinese).toContain('probe-model');
    expect(chinese).toContain("TOKEN='hrreg_a'\\''b'");
  });
});
