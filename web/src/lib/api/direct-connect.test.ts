import { describe, expect, test } from 'vitest';
import type { DirectConnectRequest } from './direct-connect';

describe('DirectConnectRequest', () => {
  test('exposes metadata and declared children but no wrapper authority selector', () => {
    const request: DirectConnectRequest = {
      version: '1.0.0',
      dependency_manifest_version: 2,
      dependencies: [{ slot: 'cli', executable: '/opt/cli', version_probe_argv: ['/opt/cli', '--version'] }],
    };

    expect(Object.keys(request)).toEqual(['version', 'dependency_manifest_version', 'dependencies']);
    expect(JSON.stringify(request)).not.toMatch(/wrapper|destination|workspace_adapter|profile|adapter_id/i);
  });
});
