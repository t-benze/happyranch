/**
 * ExecutorsSection — registration token generator + prompt/snippet renderer.
 *
 * THR-052 PR-3: The founder fills in the candidate CLI's command, argv_template,
 * and adapter. On "Generate", the SPA calls POST /auth/registration-token to mint
 * a scoped hrreg_ token, then renders two copy-paste blocks:
 *
 *   (1) A registration/conformance PROMPT the founder pastes into the candidate
 *       CLI — embeds the hrreg_ token and the exact conformance-challenge steps.
 *   (2) The proposed profile entry the daemon persists to the machine-global
 *       runtime store (executor_profiles.yaml under the daemon home) on
 *       successful registration (THR-107: the per-org org/config.yaml
 *       executor_profiles surface is removed).
 */
import { useState, type FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { mintRegistrationToken } from '@/hooks/settings';

/** Conformance steps the daemon expects — mirrors
 * RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS. */
const CONFORMANCE_STEPS = [
  {
    id: 'workspace_access',
    description:
      'Read the Agent prompt, workspace layout, and skills your CLI loads on startup.',
  },
  {
    id: 'loopback_reachable',
    description:
      'Confirm the candidate CLI can reach http://127.0.0.1 (the daemon loopback).',
  },
  {
    id: 'cli_callback',
    description:
      'Run `happyranch executors register` with the hrreg_ token provided below.',
  },
] as const;

const ADAPTERS = ['claude', 'codex', 'opencode', 'pi'] as const;

interface MintResult {
  token: string;
  expires_at: number;
}

export function ExecutorsSection(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [name, setName] = useState('');
  const [command, setCommand] = useState('');
  const [argvTemplate, setArgvTemplate] = useState('');
  const [adapter, setAdapter] = useState<string>('pi');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MintResult | null>(null);

  const handleGenerate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);

    if (!slug || !name.trim() || !command.trim() || !argvTemplate.trim()) {
      setError('Name, command, and argv template are required.');
      return;
    }

    setGenerating(true);
    try {
      const resp = await mintRegistrationToken({ org: slug, name: name.trim() });
      setResult({ token: resp.token, expires_at: resp.expires_at });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : 'Failed to mint registration token';
      setError(msg);
    } finally {
      setGenerating(false);
    }
  };

  const expiresLabel =
    result && result.expires_at
      ? new Date(result.expires_at * 1000).toLocaleTimeString()
      : 'unknown';

  // Build argv_template as a YAML list for the snippet
  const argvParts = argvTemplate
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const argvYaml =
    argvParts.length > 0
      ? '[' + argvParts.map((p) => `"${p}"`).join(', ') + ']'
      : '[]';

  const conformancePrompt = result
    ? [
        'Copy the block below and paste it into the candidate CLI terminal.',
        'The candidate must complete ALL three conformance steps:',
        '',
        '--- BEGIN CONFORMANCE PROMPT ---',
        '',
        `Registration token (expires at ${expiresLabel}):`,
        `  ${result.token}`,
        '',
        'Conformance steps:',
        ...CONFORMANCE_STEPS.map(
          (s) => `  [ ] ${s.id} — ${s.description}`,
        ),
        '',
        `Then register the profile:`,
        `  happyranch executors register \\`,
        `    --org ${slug} \\`,
        `    --token ${result.token} \\`,
        `    --exec-command ${command.trim()} \\`,
        `    --argv-template-json '${JSON.stringify(argvParts)}' \\`,
        `    --adapter ${adapter}`,
        '',
        '--- END CONFORMANCE PROMPT ---',
      ].join('\n')
    : '';

  const configSnippet = result
    ? [
        '# profile entry written to the machine-global runtime store',
        '# (~/.happyranch/executor_profiles.yaml) on successful registration',
        '',
        `${name.trim()}:`,
        `  command: "${command.trim()}"`,
        `  argv_template: ${argvYaml}`,
        `  adapter: ${adapter}`,
      ].join('\n')
    : '';

  return (
    <section className="space-y-6">
      {/* Generator form */}
      <form
        onSubmit={handleGenerate}
        className="bg-surface border-border-default shadow-pasture-sm space-y-4 rounded-lg border p-4"
        data-testid="executor-registration-form"
      >
        <h3 className="text-text-primary text-sm font-medium">
          Register a new executor
        </h3>
        <p className="text-text-secondary text-sm">
          Fill in the candidate CLI's details, then generate a registration
          token and prompt. The candidate completes the conformance challenge
          and the daemon writes the profile.
        </p>

        {/* Name */}
        <div className="space-y-1">
          <label
            htmlFor="exec-name"
            className="text-text-secondary text-xs font-medium"
          >
            Profile name
          </label>
          <input
            id="exec-name"
            type="text"
            className="bg-surface-sunken border-border-default text-text-primary placeholder:text-text-tertiary w-full rounded border px-3 py-2 text-sm"
            placeholder="e.g. my-custom-cli"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        {/* Command */}
        <div className="space-y-1">
          <label
            htmlFor="exec-command"
            className="text-text-secondary text-xs font-medium"
          >
            Command (executable name)
          </label>
          <input
            id="exec-command"
            type="text"
            className="bg-surface-sunken border-border-default text-text-primary placeholder:text-text-tertiary w-full rounded border px-3 py-2 text-sm"
            placeholder="e.g. my-cli"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
          />
        </div>

        {/* argv_template */}
        <div className="space-y-1">
          <label
            htmlFor="exec-argv"
            className="text-text-secondary text-xs font-medium"
          >
            argv_template (space-separated)
          </label>
          <input
            id="exec-argv"
            type="text"
            className="bg-surface-sunken border-border-default text-text-primary placeholder:text-text-tertiary w-full rounded border px-3 py-2 font-mono text-sm"
            placeholder="{prompt} --timeout {timeout_seconds}"
            value={argvTemplate}
            onChange={(e) => setArgvTemplate(e.target.value)}
          />
          <p className="text-text-tertiary text-xs">
            Supported placeholders: {'{prompt}'}, {'{timeout_seconds}'},{' '}
            {'{workspace}'}
          </p>
        </div>

        {/* Adapter */}
        <div className="space-y-1">
          <label
            htmlFor="exec-adapter"
            className="text-text-secondary text-xs font-medium"
          >
            Adapter
          </label>
          <select
            id="exec-adapter"
            className="bg-surface-sunken border-border-default text-text-primary w-full rounded border px-3 py-2 text-sm"
            value={adapter}
            onChange={(e) => setAdapter(e.target.value)}
          >
            {ADAPTERS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={generating}
          className="bg-accent text-accent-foreground hover:bg-accent-hover rounded px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          {generating ? 'Generating…' : 'Generate registration token'}
        </button>

        {/* Error */}
        {error && (
          <div
            className="text-feedback-danger text-sm"
            role="alert"
            data-testid="registration-error"
          >
            {error}
          </div>
        )}
      </form>

      {/* Result: conformance prompt */}
      {result && (
        <div className="space-y-4">
          {/* Block 1: Conformance prompt */}
          <div className="bg-surface border-border-default shadow-pasture-sm space-y-3 rounded-lg border p-4">
            <h3 className="text-text-primary text-sm font-medium">
              Step 1 — Paste this prompt into the candidate CLI
            </h3>
            <p className="text-text-secondary text-xs">
              The candidate completes the conformance challenge, then the
              daemon registers the profile automatically.
            </p>
            <pre
              className="bg-surface-sunken text-text-primary overflow-x-auto rounded p-3 font-mono text-xs whitespace-pre"
              data-testid="conformance-prompt"
            >
              {conformancePrompt}
            </pre>
          </div>

          {/* Block 2: Config snippet */}
          <div className="bg-surface border-border-default shadow-pasture-sm space-y-3 rounded-lg border p-4">
            <h3 className="text-text-primary text-sm font-medium">
              Step 2 — Resulting config entry
            </h3>
            <p className="text-text-secondary text-xs">
              On successful registration, the daemon writes this entry to the
              machine-global runtime store{' '}
              <code className="text-text-secondary bg-surface-sunken rounded px-1 font-mono text-xs">
                ~/.happyranch/executor_profiles.yaml
              </code>
              :
            </p>
            <pre
              className="bg-surface-sunken text-text-primary overflow-x-auto rounded p-3 font-mono text-xs whitespace-pre"
              data-testid="config-snippet"
            >
              {configSnippet}
            </pre>
          </div>
        </div>
      )}

      {/* Read-only notice — always visible at the bottom */}
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <h3 className="text-text-primary text-sm font-medium">
          Per-agent executor assignment
        </h3>
        <p className="text-text-secondary mt-1 text-sm">
          Assign executors to individual agents from the{' '}
          <a href="../agents" className="text-accent hover:underline">
            Agents page
          </a>.
          Each agent's executor (claude, codex, opencode, pi) is set during
          enrollment and cannot be changed from Settings.
        </p>
      </div>
    </section>
  );
}
