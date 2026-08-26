# Linux Canonical Skill Store Support

> Status: current
> Current Source: `protocol/05b-agent-runtime.md` and `docs/agent-guides/agent-executors-and-permissions.md`
> Issue: #690

## Decision

HappyRanch supports the canonical skill store and executor launch boundary on
macOS (`darwin`) and Linux (`linux`). Both use the same same-owner POSIX model:
the daemon and executor run as the same OS identity, workspace entries are
validated relative symlinks into the hash-addressed canonical store, and the
executor is launched directly with `subprocess.Popen`.

This is detection-only integrity with fail-closed refusal. Linux support does
not add an OS security boundary and does not make canonical bytes, artifact
sources, or links immutable or protected from a same-UID process.

Windows and unknown platforms remain unsupported and raise the named
`PlatformIsolationError("unsupported_platform", ...)` before materialization or
launch. There is no copy fallback.

## Supported Linux Environment

The supported contract is a native Linux host with:

- Python reporting `sys.platform == "linux"`;
- a filesystem that implements POSIX relative symbolic links and atomic
  same-directory replacement via `os.replace`;
- ordinary owner/group/mode operations used for cosmetic readonly hardening;
- direct same-identity subprocess launch through `subprocess.Popen`;
- the daemon home and each workspace on filesystems where their respective
  same-directory atomic replacement operations are supported.

Containers and network filesystems are supported only when they preserve those
semantics. The implementation does not infer safety from a distribution name,
kernel version, mount type, namespace, ACL, LSM, or container boundary. A host
that lacks the required filesystem/process primitives fails through the
existing named materialization or launch error path.

## Platform Adapter Shape

The macOS and Linux adapters share one private POSIX same-owner implementation
for:

1. rejecting absolute or excessive-parent relative link targets;
2. refusing to recursively remove an ordinary directory at a managed link;
3. creating relative symbolic links;
4. resolving and validating links against the expected canonical target/root;
5. launching the executor directly under the daemon identity.

Thin platform-specific subclasses keep detection explicit and make the
supported-platform audit mechanically visible. Platform detection never falls
back from an unknown platform to the shared implementation.

## Launch and Retry Boundary

No launch call site changes. `executors._run_command` continues to run the
caller-provided integrity validator before every `Popen`, including throttle
retries. It then obtains the explicit platform adapter and launches. Task,
subtask, thread, wake, dream, schedule, bootstrap, and executor-switch paths
continue to converge on the existing materialization and verification seams.

## Failure Modes

- Unsupported platform: named `unsupported_platform`, no materialization or
  process launch.
- Missing symlink or wrong target: repaired atomically when the managed entry is
  safely replaceable.
- Ordinary directory at a link path: refused without recursive deletion.
- External, broken, stale, or substituted link: rejected or atomically repaired
  by the existing reconciliation rules.
- Canonical member or manifest mismatch: durable integrity event followed by
  refusal before launch/retry.
- Missing POSIX primitive or subprocess launch error: named failure, no silent
  fallback.

## Residual Risks

The executor shares the daemon UID on Linux exactly as it does on macOS. It can
mutate anything that UID can reach, including canonical packages, workspace
links, release sources, and ArtifactStore bytes. It may race validation and can
affect active or overlapping sessions. Pre-launch hashing detects state at the
check; it cannot prevent a same-UID time-of-check/time-of-use race after it.

Readonly modes are cosmetic. Linux namespaces, ACLs, capabilities, immutable
flags, seccomp, Landlock, SELinux, and AppArmor are neither required nor claimed
as enforcement by this design.

Recovery remains manual and operator-invoked after authoritative external
re-sync/redeploy. The implementation never heals corrupted canonical bytes from
a same-UID local source.

## Validation

Linux CI must run the canonical-store, production-bound, cutover, freshness,
workspace-adapter, pre-launch integrity, and system-contract suites using the
real Linux adapter. Focused tests cover platform detection, unsupported-platform
refusal, relative-link creation and validation, external/wrong/broken links,
ordinary-directory preservation, member mutation, manifest mismatch, atomic
repair, and direct same-owner executor launch.

The existing macOS canonical validation job remains authoritative for macOS and
must continue to pass unchanged.

## Non-goals

- Windows support.
- Cross-UID executor isolation.
- Automatic repair or a legacy copy fallback.
- Changes to auth, credentials, allow rules, executor argv, schema, or API
  contracts.
