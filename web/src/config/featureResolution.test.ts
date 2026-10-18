// @vitest-environment node
/**
 * THR-305 build-time feature flags — the exact selector/resolver functions the
 * real `vite.config.ts` factory calls, over real JSON files in a disposable
 * web root (mkdtemp is test-side isolation only).
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import {
  FEATURE_PROFILE_MARKER,
  resolveFeatureFlags,
  selectFeatureProfile,
  type FeatureProfile,
} from '../../vite.config';

const WEB = path.resolve(__dirname, '../..');
const REAL_CONFIG = path.join(WEB, 'config');
const COMPAT = 'VITE_ENABLE_KB_COMPOSE';
const LOCAL_BUILD = { command: 'build' as const, marker: 'local', env: { npm_lifecycle_event: 'build:local' } };

let webRoot: string;
let savedCompat: string | undefined;

function write(rel: string, content: string): void {
  const file = path.join(webRoot, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function resolve(profile: FeatureProfile, mode = profile === 'test' ? 'test' : 'development') {
  return resolveFeatureFlags({ profile, webRoot, mode });
}

beforeEach(() => {
  webRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ff-'));
  fs.cpSync(REAL_CONFIG, path.join(webRoot, 'config'), { recursive: true });
  fs.rmSync(path.join(webRoot, 'config', 'features.local.json'), { force: true });
  savedCompat = process.env[COMPAT];
  delete process.env[COMPAT];
});

afterEach(() => {
  if (savedCompat === undefined) delete process.env[COMPAT];
  else process.env[COMPAT] = savedCompat;
  fs.rmSync(webRoot, { recursive: true, force: true });
});

describe('selectFeatureProfile (C1a, C4-u2)', () => {
  test('serve without Vitest is local; build:local marker + lifecycle is local', () => {
    expect(selectFeatureProfile({ command: 'serve', env: {}, marker: undefined })).toBe('local');
    expect(selectFeatureProfile(LOCAL_BUILD)).toBe('local');
  });

  test('every build without the marker is production; Vitest serve is test; preview resolves nothing', () => {
    expect(selectFeatureProfile({ command: 'build', env: {}, marker: undefined })).toBe('production');
    expect(selectFeatureProfile({ command: 'build', env: { NODE_ENV: 'development', VITEST: 'true', [COMPAT]: 'true' }, marker: undefined })).toBe('production');
    expect(selectFeatureProfile({ command: 'serve', env: { VITEST: 'true' }, marker: undefined })).toBe('test');
    expect(selectFeatureProfile({ command: 'serve', isPreview: true, env: {}, marker: undefined })).toBe('preview');
  });

  test.each([
    ['marker "staging" with build:local lifecycle', { command: 'build' as const, marker: 'staging', env: { npm_lifecycle_event: 'build:local' } }],
    ['marker "production"', { command: 'build' as const, marker: 'production', env: { npm_lifecycle_event: 'build:local' } }],
    ['empty marker', { command: 'build' as const, marker: '', env: { npm_lifecycle_event: 'build:local' } }],
    ['boolean marker', { command: 'build' as const, marker: true, env: { npm_lifecycle_event: 'build:local' } }],
    ['marker under serve', { command: 'serve' as const, marker: 'local', env: { npm_lifecycle_event: 'build:local' } }],
    ['marker without lifecycle', { command: 'build' as const, marker: 'local', env: {} }],
    ['lifecycle build:local without marker', { command: 'build' as const, marker: undefined, env: { npm_lifecycle_event: 'build:local' } }],
  ])('rejects %s before any resolution', (_label, input) => {
    expect(() => selectFeatureProfile(input)).toThrow(/^\[features\] unsupported selector/);
  });

  test('the default marker argument reads the global build-local symbol', () => {
    const g = globalThis as Record<symbol, unknown>;
    g[FEATURE_PROFILE_MARKER] = 'staging';
    try {
      expect(() => selectFeatureProfile({ command: 'build', env: { npm_lifecycle_event: 'build:local' } })).toThrow(/unsupported selector/);
    } finally {
      delete g[FEATURE_PROFILE_MARKER];
    }
  });
});

describe('local and test resolution (C1a, C2a, C2c, C10a)', () => {
  test('local defaults: development only, alias to a content-addressed snapshot', () => {
    const r = resolve('local');
    expect(r.flags).toEqual({ work_hours_ui: true, kb_compose_ui: false });
    expect(r.sources).toEqual({ work_hours_ui: 'development', kb_compose_ui: 'development' });
    expect(r.logLine).toBe('[features] profile=local sources=development work_hours_ui=true(development) kb_compose_ui=false(development)');
    expect(path.dirname(r.aliasTarget)).toBe(path.join(webRoot, '.feature-flags'));
    expect(path.basename(r.aliasTarget)).toMatch(/^resolved-local-[0-9a-f]{8}\.json$/);
    expect(JSON.parse(fs.readFileSync(r.aliasTarget, 'utf-8'))).toEqual(r.flags);
    // Write-once: the same selection reuses the identical file.
    expect(resolve('local').aliasTarget).toBe(r.aliasTarget);
  });

  test('partial local override flips only its key', () => {
    write('config/features.local.json', '{"work_hours_ui": false}');
    const r = resolve('local');
    expect(r.flags).toEqual({ work_hours_ui: false, kb_compose_ui: false });
    expect(r.sources).toEqual({ work_hours_ui: 'features.local.json', kb_compose_ui: 'development' });
    expect(r.logLine).toContain('work_hours_ui=false(features.local.json)');
  });

  test('KB is independent of Work Hours', () => {
    write('config/features.local.json', '{"kb_compose_ui": true}');
    expect(resolve('local').flags).toEqual({ work_hours_ui: true, kb_compose_ui: true });
  });

  test('KB compose is off by default in local, test and production', () => {
    expect(resolve('local').flags.kb_compose_ui).toBe(false);
    expect(resolve('test').flags.kb_compose_ui).toBe(false);
    expect(resolve('production', 'production').flags.kb_compose_ui).toBe(false);
  });

  test('test ignores the local file and names it', () => {
    write('config/features.local.json', '{"work_hours_ui": false, "kb_compose_ui": true}');
    const r = resolve('test');
    expect(r.flags).toEqual({ work_hours_ui: true, kb_compose_ui: false });
    expect(r.ignored).toEqual(['features.local.json']);
    expect(r.logLine).toMatch(/ ignored: features\.local\.json$/);
  });
});

describe('malformed selected local file fails (C3)', () => {
  test.each([
    ['bad JSON', '{', /features\.local\.json is not valid JSON/],
    ['array root', '[]', /features\.local\.json root must be an object/],
    ['null root', 'null', /features\.local\.json root must be an object/],
    ['string root', '"x"', /features\.local\.json root must be an object/],
    ['number root', '1', /features\.local\.json root must be an object/],
    ['unknown key', '{"bogus":true}', /features\.local\.json has unknown flag "bogus"/],
    ['string value', '{"work_hours_ui":"true"}', /flag "work_hours_ui" must be true or false \(got string\)/],
    ['null value', '{"work_hours_ui":null}', /flag "work_hours_ui" must be true or false \(got null\)/],
  ])('%s', (_label, content, message) => {
    write('config/features.local.json', content);
    for (const selection of [selectFeatureProfile({ command: 'serve', env: {}, marker: undefined }), selectFeatureProfile(LOCAL_BUILD)]) {
      expect(selection).toBe('local');
      expect(() => resolve(selection as FeatureProfile)).toThrow(message);
    }
    // Not selected by production: the invalid file is ignored.
    expect(resolve('production', 'production').flags).toEqual({ work_hours_ui: false, kb_compose_ui: false });
  });
});

describe('invalid profile or registry fails for its selection (C4, C4-u1)', () => {
  test.each([
    ['missing key', '{"work_hours_ui": false}', /production profile .* is missing flag "kb_compose_ui"/],
    ['unknown key', '{"work_hours_ui": false, "kb_compose_ui": false, "x": false}', /has unknown flag "x"/],
    ['string "false"', '{"work_hours_ui": "false", "kb_compose_ui": false}', /must be true or false \(got string\)/],
    ['bad JSON', '{', /is not valid JSON/],
    ['array root', '[]', /root must be an object/],
    ['null root', 'null', /root must be an object/],
    ['string root', '"x"', /root must be an object/],
    ['boolean root', 'true', /root must be an object/],
  ])('production profile: %s fails production only', (_label, content, message) => {
    write('config/features.production.json', content);
    expect(() => resolve('production', 'production')).toThrow(message);
    expect(resolve('local').flags.work_hours_ui).toBe(true);
    expect(resolve('test').flags.work_hours_ui).toBe(true);
  });

  test('production profile absent fails production', () => {
    fs.rmSync(path.join(webRoot, 'config', 'features.production.json'));
    expect(() => resolve('production', 'production')).toThrow(/production profile .* is missing or unreadable/);
  });

  test.each([
    ['array root', '[]', /development profile .* root must be an object/],
    ['missing key', '{"work_hours_ui": true}', /development profile .* is missing flag "kb_compose_ui"/],
  ])('development profile: %s fails local and test, not production', (_label, content, message) => {
    write('config/features.development.json', content);
    expect(() => resolve('local')).toThrow(message);
    expect(() => resolve('test')).toThrow(message);
    expect(resolve('production', 'production').flags).toEqual({ work_hours_ui: false, kb_compose_ui: false });
  });

  test('registry entry lacking owner fails every selection', () => {
    const registry = JSON.parse(fs.readFileSync(path.join(REAL_CONFIG, 'features.registry.json'), 'utf-8'));
    delete registry.work_hours_ui.owner;
    write('config/features.registry.json', JSON.stringify(registry));
    for (const profile of ['local', 'test', 'production'] as const) {
      expect(() => resolve(profile)).toThrow(/registry .* entry "work_hours_ui" needs a nonempty owner/);
    }
  });

  test('a registry key absent from a profile fails that profile', () => {
    const registry = JSON.parse(fs.readFileSync(path.join(REAL_CONFIG, 'features.registry.json'), 'utf-8'));
    registry.extra_ui = { purpose: 'p', owner: 'o', removal_condition: 'r' };
    write('config/features.registry.json', JSON.stringify(registry));
    expect(() => resolve('local')).toThrow(/development profile .* is missing flag "extra_ui"/);
    expect(() => resolve('production', 'production')).toThrow(/production profile .* is missing flag "extra_ui"/);
  });
});

describe('production isolation at the resolver (C5a resolver half, C10d)', () => {
  test('production reads only the checked-in production file and lists ignored inputs', () => {
    write('config/features.local.json', '{"work_hours_ui": true, "kb_compose_ui": true}');
    write('.env.production.local', `${COMPAT}=true\n`);
    process.env[COMPAT] = 'yes';
    const r = resolve('production', 'production');
    expect(r.flags).toEqual({ work_hours_ui: false, kb_compose_ui: false });
    expect(r.aliasTarget).toBe(path.join(webRoot, 'config', 'features.production.json'));
    expect(r.ignored).toEqual(['features.local.json', COMPAT]);
    expect(r.logLine).toBe(`[features] profile=production sources=production work_hours_ui=false(production) kb_compose_ui=false(production) ignored: features.local.json,${COMPAT}`);
    expect(fs.existsSync(path.join(webRoot, '.feature-flags'))).toBe(false);
  });
});

describe('KB compatibility input precedence (C10b)', () => {
  const local = (rows: Array<[string, string]>, profile: FeatureProfile = 'local', mode?: string) => {
    for (const [rel, content] of rows) write(rel, content);
    return resolve(profile, mode);
  };

  test('local true + env false -> false; local false + env true -> true', () => {
    write('config/features.local.json', '{"kb_compose_ui": true}');
    process.env[COMPAT] = 'false';
    expect(resolve('local').flags.kb_compose_ui).toBe(false);
    write('config/features.local.json', '{"kb_compose_ui": false}');
    process.env[COMPAT] = 'true';
    const r = resolve('local');
    expect(r.flags.kb_compose_ui).toBe(true);
    expect(r.sources.kb_compose_ui).toBe(COMPAT);
  });

  test.each(['TRUE', '1', 'yes'])('env %s fails clearly', (value) => {
    process.env[COMPAT] = value;
    expect(() => resolve('local')).toThrow(new RegExp(`^\\[features\\] ${COMPAT} must be the literal true or false`));
    expect(() => resolve('test')).toThrow(/must be the literal true or false/);
  });

  test('empty env is no opinion: the local value stands', () => {
    write('config/features.local.json', '{"kb_compose_ui": true}');
    process.env[COMPAT] = '';
    const r = resolve('local');
    expect(r.flags.kb_compose_ui).toBe(true);
    expect(r.sources.kb_compose_ui).toBe('features.local.json');
  });

  test('process env wins over .env.local', () => {
    process.env[COMPAT] = 'false';
    expect(local([['.env.local', `${COMPAT}=true\n`]]).flags.kb_compose_ui).toBe(false);
  });

  test('.env.development.local wins over .env.development', () => {
    expect(local([['.env.development', `${COMPAT}=true\n`], ['.env.development.local', `${COMPAT}=false\n`]]).flags.kb_compose_ui).toBe(false);
  });

  test('.env.development (mode file) wins over .env.local', () => {
    expect(local([['.env.local', `${COMPAT}=true\n`], ['.env.development', `${COMPAT}=false\n`]]).flags.kb_compose_ui).toBe(false);
  });

  test('test selection: env true wins and the local file is ignored', () => {
    write('config/features.local.json', '{"kb_compose_ui": false}');
    process.env[COMPAT] = 'true';
    const r = resolve('test');
    expect(r.flags.kb_compose_ui).toBe(true);
    expect(r.ignored).toEqual(['features.local.json']);
  });

  test('test selection: .env.test.local wins over .env.test', () => {
    expect(local([['.env.test', `${COMPAT}=true\n`], ['.env.test.local', `${COMPAT}=false\n`]], 'test').flags.kb_compose_ui).toBe(false);
  });

  test('test selection: records whether the locked Vite loadEnv reads .env.local in mode test (G4)', () => {
    // Observation, not an assumption: Vite 5 loads `.env.local` for every mode.
    expect(local([['.env.local', `${COMPAT}=true\n`]], 'test').flags.kb_compose_ui).toBe(true);
  });

  test('the compatibility input never changes work_hours_ui', () => {
    process.env[COMPAT] = 'true';
    expect(resolve('local').flags.work_hours_ui).toBe(true);
    write('config/features.local.json', '{"work_hours_ui": false}');
    expect(resolve('local').flags.work_hours_ui).toBe(false);
  });
});

describe('contract coherence (C12)', () => {
  const read = (name: string) => JSON.parse(fs.readFileSync(path.join(REAL_CONFIG, name), 'utf-8')) as Record<string, unknown>;

  test('registry, profiles and example agree', () => {
    const registry = read('features.registry.json');
    const names = Object.keys(registry).sort();
    expect(names).toEqual(['kb_compose_ui', 'work_hours_ui']);
    for (const entry of Object.values(registry)) {
      expect(Object.keys(entry as object).sort()).toEqual(['owner', 'purpose', 'removal_condition']);
    }
    expect(read('features.development.json')).toEqual({ work_hours_ui: true, kb_compose_ui: false });
    expect(read('features.production.json')).toEqual({ work_hours_ui: false, kb_compose_ui: false });
    for (const key of Object.keys(read('features.local.example.json'))) expect(names).toContain(key);
  });

  test('only the consumer module imports the flag specifier; no scattered env readers', () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(p);
        else if (/\.(ts|tsx)$/.test(entry.name)) files.push(p);
      }
    };
    walk(path.join(WEB, 'src'));
    const rel = (p: string) => path.relative(WEB, p).split(path.sep).join('/');
    const self = rel(__filename);
    const importers = files.filter((f) => rel(f) !== self && fs.readFileSync(f, 'utf-8').includes("from '@happyranch/feature-flags'")).map(rel);
    expect(importers).toEqual(['src/config/features.ts']);
    // A reader is an env access or stub; prose mentions in comments are not.
    const reader = /(import\.meta\.env|process\.env)\s*(\.|\[\s*['"])(VITE_ENABLE_KB_COMPOSE|VITE_[A-Z_]*WORK_HOURS)|stubEnv\(\s*['"](VITE_ENABLE_KB_COMPOSE|VITE_[A-Z_]*WORK_HOURS)/;
    const envReaders = files.filter((f) => rel(f) !== self && reader.test(fs.readFileSync(f, 'utf-8'))).map(rel);
    expect(envReaders).toEqual([]);
  });
});
