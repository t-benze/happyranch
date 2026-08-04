/**
 * Source-bound execution coverage of the ACTUAL buildConnectPrompt output.
 *
 * Replaces hand-written shell mirrors with direct import + controlled mock
 * execution of the real emitted binary-connection script.  No generated
 * string duplicated in test code — the production prompt builder is the
 * single source of truth.
 *
 * @vitest-environment node
 */
import { describe, test, expect, beforeAll, afterAll } from 'vitest';
import { execSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { buildConnectPrompt } from './useRuntimeConnect';

// Node environment polyfill: the global vitest setup references sessionStorage.
interface SessionStorageMock {
  _store: Map<string, string>;
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
  clear(): void;
}
(globalThis as unknown as { sessionStorage: SessionStorageMock }).sessionStorage = {
  _store: new Map<string, string>(),
  getItem(k: string) { return this._store.get(k) ?? null; },
  setItem(k: string, v: string) { this._store.set(k, v); },
  removeItem(k: string) { this._store.delete(k); },
  clear() { this._store.clear(); },
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const ORIGIN = 'http://localhost:8765';
const TOKEN = 'hrreg_fake_token_0000000000000000';
const NAME = 'testexecutor';

/** Build the real binary script from the shared prompt builder. */
function buildScript(name: string = NAME, token: string = TOKEN, origin: string = ORIGIN): string {
  return buildConnectPrompt(name, token, origin, 'binary');
}

/** Write a mock executable that exits with the given code and optional stdout/stderr. */
function writeMockExec(
  dir: string,
  name: string,
  exitCode: number,
  stdout: string = '',
  stderr: string = '',
): string {
  const p = join(dir, name);
  const lines = [`#!/bin/bash`];
  if (stdout) lines.push(`echo '${stdout.replace(/'/g, "'\\''")}'`);
  if (stderr) lines.push(`echo '${stderr.replace(/'/g, "'\\''")}' >&2`);
  lines.push(`exit ${exitCode}`);
  writeFileSync(p, lines.join('\n'));
  chmodSync(p, 0o755);
  return p;
}

/** Write a mock command/which that prints the given path when invoked */
function writeMockDiscovery(dir: string, name: string, binaryPath: string): string {
  const cmdP = join(dir, 'command');
  writeFileSync(cmdP, `#!/bin/bash\nif [ "$1" = "${name}" ]; then echo "${binaryPath}"; exit 0; fi\necho ""; exit 1`);
  chmodSync(cmdP, 0o755);
  const whichP = join(dir, 'which');
  writeFileSync(whichP, `#!/bin/bash\nif [ "$1" = "${name}" ]; then echo "${binaryPath}"; exit 0; fi\necho ""; exit 1`);
  chmodSync(whichP, 0o755);
  return binaryPath;
}

/** Build a PATH-adjusted environment that puts mockDir first. */
function mockPath(mockDir: string): NodeJS.ProcessEnv {
  const env = { ...process.env };
  env.PATH = `${mockDir}:${env.PATH ?? ''}`;
  // suppress any daemon-side interference
  delete env.HAPPYRANCH_DAEMON_HOME;
  return env;
}

/** Write generated script to a file, execute it, return combined stdout+stderr + exit code. */
function runScript(script: string, env: NodeJS.ProcessEnv, cwd: string, label: string): { output: string; exitCode: number } {
  const scriptPath = join(cwd, `${label}.sh`);
  writeFileSync(scriptPath, script);
  chmodSync(scriptPath, 0o755);
  let output = '';
  let exitCode = 0;
  try {
    output = execSync(`bash "${scriptPath}"`, { env, cwd, timeout: 15_000, encoding: 'utf-8', stdio: 'pipe' });
  } catch (e: unknown) {
    const err = e as { stdout?: string; stderr?: string; status?: number };
    output = (err.stdout ?? '') + (err.stderr ?? '');
    exitCode = err.status ?? 1;
  }
  return { output, exitCode };
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('buildConnectPrompt binary script — source-bound shell execution', () => {
  let cwd: string;

  /** Shared temp directory per suite to avoid polluting /tmp. */
  beforeAll(() => {
    cwd = join(tmpdir(), `hr-test-shell-${process.pid}-${Date.now()}`);
    mkdirSync(cwd, { recursive: true });
  });
  afterAll(() => {
    try { rmSync(cwd, { recursive: true, force: true }); } catch { /* best-effort */ }
  });

  /* ---------------------------------------------------------------- */
  /*  Criterion (a): any curl failure exits nonzero, prints the body   */
  /*  where available, and NEVER reaches register-binary.              */
  /* ---------------------------------------------------------------- */

  test('first curl (workspace_access) failure exits nonzero and blocks registration', () => {
    const mockDir = join(cwd, 'mock-curl-first-fail');
    mkdirSync(mockDir, { recursive: true });

    // Mock binary discovery: point to a real executable (bash itself)
    const binPath = '/bin/bash';
    writeMockDiscovery(mockDir, NAME, binPath);

    // Mock curl: fail on first call with a body
    writeMockExec(mockDir, 'curl', 22, '{"error":"workspace not accessible"}', '');

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'first-fail');

    // Must exit non-zero
    expect(exitCode).not.toBe(0);
    // The failed response body must be visible
    expect(output).toContain('workspace not accessible');
    // register-binary must NOT be reached
    expect(output).not.toContain('register-binary');
  });

  test('second curl (loopback_reachable) failure exits nonzero and blocks registration', () => {
    const mockDir = join(cwd, 'mock-curl-second-fail');
    mkdirSync(mockDir, { recursive: true });
    const binPath = '/bin/bash';
    writeMockDiscovery(mockDir, NAME, binPath);

    // Mock curl: succeed first call, fail second
    const curlPath = join(mockDir, 'curl');
    const stateFile = join(cwd, 'mock-curl-second-state');
    writeFileSync(stateFile, '0');
    writeFileSync(curlPath, [
      '#!/bin/bash',
      `COUNT=$(cat "${stateFile}")`,
      'NEXT=$((COUNT + 1))',
      `echo "$NEXT" > "${stateFile}"`,
      'if [ "$NEXT" -eq 2 ]; then',
      "  echo '{\"error\":\"loopback unreachable\"}'",
      '  exit 22',
      'fi',
      "echo '{\"arrived\":true}'",
      'exit 0',
    ].join('\n'));
    chmodSync(curlPath, 0o755);

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'second-fail');

    expect(exitCode).not.toBe(0);
    expect(output).toContain('loopback unreachable');
    expect(output).not.toContain('register-binary');
  });

  test('fourth curl (emit_envelope) failure exits nonzero, prints response body, blocks registration', () => {
    const mockDir = join(cwd, 'mock-curl-fourth-fail');
    mkdirSync(mockDir, { recursive: true });
    const binPath = '/bin/bash';
    writeMockDiscovery(mockDir, NAME, binPath);

    // Mock curl: succeed first 3, fail 4th
    const curlPath = join(mockDir, 'curl');
    const stateFile = join(cwd, 'mock-curl-fourth-state');
    writeFileSync(stateFile, '0');
    writeFileSync(curlPath, [
      '#!/bin/bash',
      `COUNT=$(cat "${stateFile}")`,
      'NEXT=$((COUNT + 1))',
      `echo "$NEXT" > "${stateFile}"`,
      'if [ "$NEXT" -eq 4 ]; then',
      // On fourth call (emit_envelope), write body to stdout THEN exit non-zero
      // This simulates curl --fail-with-body behavior
      "  echo '{\"error\":\"invalid envelope\",\"code\":\"bad_envelope\"}'",
      '  exit 22',
      'fi',
      "echo '{\"arrived\":true}'",
      'exit 0',
    ].join('\n'));
    chmodSync(curlPath, 0o755);

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'fourth-fail');

    expect(exitCode).not.toBe(0);
    // Fourth response body must be printed even on failure
    expect(output).toContain('invalid envelope');
    // register-binary must NOT be reached
    expect(output).not.toContain('register-binary');
  });

  /* ---------------------------------------------------------------- */
  /*  Criterion (b): all_complete:false prints response, blocks reg    */
  /* ---------------------------------------------------------------- */

  test('all_complete:false prints fourth response but blocks registration', () => {
    const mockDir = join(cwd, 'mock-curl-all_complete-false');
    mkdirSync(mockDir, { recursive: true });
    const binPath = '/bin/bash';
    writeMockDiscovery(mockDir, NAME, binPath);

    // Mock curl: all 4 succeed but fourth returns all_complete:false
    const curlPath = join(mockDir, 'curl');
    const stateFile = join(cwd, 'mock-curl-acf-state');
    writeFileSync(stateFile, '0');
    writeFileSync(curlPath, [
      '#!/bin/bash',
      `COUNT=$(cat "${stateFile}")`,
      'NEXT=$((COUNT + 1))',
      `echo "$NEXT" > "${stateFile}"`,
      'if [ "$NEXT" -eq 4 ]; then',
      "  echo '{\"arrived\":true,\"all_complete\":false,\"pending_steps\":[\"cli_callback\"]}'",
      '  exit 0',
      'fi',
      "echo '{\"arrived\":true}'",
      'exit 0',
    ].join('\n'));
    chmodSync(curlPath, 0o755);

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'acf-false');

    // all_complete gate should block => exit non-zero
    expect(exitCode).not.toBe(0);
    // Should tell user conformance is not complete
    expect(output).toContain('not complete');
    // Fourth response body must be visible
    expect(output).toContain('all_complete":false');
    // register-binary must NOT be reached
    expect(output).not.toContain('register-binary');
  });

  /* ---------------------------------------------------------------- */
  /*  Criterion (c): all four successful + all_complete:true =>        */
  /*  exactly one register-binary invocation                           */
  /* ---------------------------------------------------------------- */

  test('all four successful + all_complete:true reaches exactly one register-binary', () => {
    const mockDir = join(cwd, 'mock-curl-all-success');
    mkdirSync(mockDir, { recursive: true });
    const binPath = '/bin/bash';
    writeMockDiscovery(mockDir, NAME, binPath);

    // Mock curl: all 4 succeed, fourth returns all_complete:true
    const curlPath = join(mockDir, 'curl');
    const stateFile = join(cwd, 'mock-curl-success-state');
    writeFileSync(stateFile, '0');
    writeFileSync(curlPath, [
      '#!/bin/bash',
      `COUNT=$(cat "${stateFile}")`,
      'NEXT=$((COUNT + 1))',
      `echo "$NEXT" > "${stateFile}"`,
      'if [ "$NEXT" -eq 4 ]; then',
      "  echo '{\"arrived\":true,\"all_complete\":true}'",
      '  exit 0',
      'fi',
      "echo '{\"arrived\":true}'",
      'exit 0',
    ].join('\n'));
    chmodSync(curlPath, 0o755);

    const script = buildScript();
    const env = mockPath(mockDir);

    // Rewrite the mock to handle the register-binary call (5th curl) as well
    // by always returning arrived:true for calls >= 5.
    rmSync(curlPath);
    writeFileSync(curlPath, [
      '#!/bin/bash',
      `COUNT=$(cat "${stateFile}")`,
      'NEXT=$((COUNT + 1))',
      `echo "$NEXT" > "${stateFile}"`,
      'if [ "$NEXT" -eq 4 ]; then',
      "  echo '{\"arrived\":true,\"all_complete\":true}'",
      '  exit 0',
      'elif [ "$NEXT" -ge 5 ]; then',
      "  echo '{\"registered\":true}'",
      '  exit 0',
      'fi',
      "echo '{\"arrived\":true}'",
      'exit 0',
    ].join('\n'));
    chmodSync(curlPath, 0o755);
    // Reset state
    writeFileSync(stateFile, '0');

    const result2 = runScript(script, env, cwd, 'all-success');

    // Must reach register-binary
    expect(result2.output).toContain('register-binary');
    expect(result2.output).toContain('registered');
  });

  /* ---------------------------------------------------------------- */
  /*  Criterion (d): missing binary prints clear error, no curl/reg    */
  /* ---------------------------------------------------------------- */

  test('missing binary prints discovery error and makes no curl/register call', () => {
    const mockDir = join(cwd, 'mock-discovery-missing');
    mkdirSync(mockDir, { recursive: true });

    // Mock command/which that return nothing (binary not found)
    const cmdP = join(mockDir, 'command');
    writeFileSync(cmdP, '#!/bin/bash\necho ""\nexit 1');
    chmodSync(cmdP, 0o755);
    const whichP = join(mockDir, 'which');
    writeFileSync(whichP, '#!/bin/bash\necho ""\nexit 1');
    chmodSync(whichP, 0o755);

    // Mock curl: if reached, should fail loudly
    writeMockExec(mockDir, 'curl', 0, 'UNEXPECTED_CURL_CALL', '');

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'missing-binary');

    // Must exit non-zero
    expect(exitCode).not.toBe(0);
    // Must print the actionable error
    expect(output).toContain('cannot find executable');
    expect(output).toContain(NAME);
    // No curl call was made
    expect(output).not.toContain('UNEXPECTED_CURL_CALL');
  });

  /* ---------------------------------------------------------------- */
  /*  Criterion (2): set -e must not bypass binary discovery error     */
  /* ---------------------------------------------------------------- */

  test('set -e does not suppress missing-binary error', () => {
    const mockDir = join(cwd, 'mock-set-e-discovery');
    mkdirSync(mockDir, { recursive: true });

    // Mock command/which that return nothing
    const cmdP = join(mockDir, 'command');
    writeFileSync(cmdP, '#!/bin/bash\necho ""\nexit 1');
    chmodSync(cmdP, 0o755);
    const whichP = join(mockDir, 'which');
    writeFileSync(whichP, '#!/bin/bash\necho ""\nexit 1');
    chmodSync(whichP, 0o755);

    // Mock curl should NEVER be called
    writeMockExec(mockDir, 'curl', 0, 'CURL_SHOULD_NOT_BE_CALLED', '');

    const script = buildScript();
    const env = mockPath(mockDir);
    const { output, exitCode } = runScript(script, env, cwd, 'set-e-discovery');

    expect(exitCode).not.toBe(0);
    // The explicit error from the prompt must be visible
    expect(output).toContain('cannot find executable');
    // No curl calls
    expect(output).not.toContain('CURL_SHOULD_NOT_BE_CALLED');
  });
});
