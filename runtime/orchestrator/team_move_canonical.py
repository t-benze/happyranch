"""Private exact canonical planner/replacer for the approved consultant move.

No service admission, DB mutation, materialization, reset or live invocation.
All three complete candidate images validate before any replacement.
"""
from __future__ import annotations

import base64
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

from runtime.infrastructure.team_move_store import FileImage, PathIdentity, TeamMoveError, _digest
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import parse_agent_text
from runtime.orchestrator.canonical_roster import canonical_roster
from runtime.orchestrator.org_validation import validate_team_definitions
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.teams import TeamsRegistry

AGENTS = ("consultant_codex", "consultant_head")
PATHS = tuple(f"org/agents/{agent}.md" for agent in AGENTS) + ("org/teams.yaml",)
OLD_CHARTER = "You're heading the consultant team to advise the HappyRanch team on building its product and services, and also on business operation."
NEW_CHARTER = "Advise the founder and HappyRanch teams on product, services and business operations. You are an individual consultant reporting directly to the founder."


def _mapping(node):
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        raise TeamMoveError("team_move_literal_mapping_required")
    pairs = {key.value: (key, value) for key, value in node.value}
    if len(pairs) != len(node.value):
        raise TeamMoveError("team_move_duplicate_mapping_key")
    return pairs


def _literal(text):
    if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for t in yaml.scan(text)):
        raise TeamMoveError("team_move_yaml_alias_unsupported")
    return _mapping(yaml.compose(text))


def _edits(text, changes):
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def plan_agent(contents: bytes, agent_id: str) -> bytes:
    """Native parser validation, preserving all unspecified frontmatter bytes."""
    text = contents.decode("utf-8")
    definition = parse_agent_text(text.replace("\r\n", "\n"), expected_name=agent_id)
    if (definition.team != "consultant" or definition.role != ("manager" if agent_id == "consultant_head" else "worker")
            or definition.allow_rules):
        raise TeamMoveError("team_move_source_or_explicit_grants_unsupported")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if match is None:
        raise TeamMoveError("team_move_literal_frontmatter_required")
    fm = match[1]
    nodes = _literal(fm)
    changes = []
    fields = (("team", "default"),) + ((("role", "worker"),) if agent_id == "consultant_head" else ())
    for key, replacement in fields:
        node = nodes[key][1]
        if not isinstance(node, yaml.ScalarNode) or node.start_mark.line != node.end_mark.line:
            raise TeamMoveError("team_move_literal_scalar_required")
        changes.append((match.start(1) + node.start_mark.index, match.start(1) + node.end_mark.index, replacement))
    result = _edits(text, changes)
    if agent_id == "consultant_head":
        if definition.system_prompt.strip() != OLD_CHARTER or result.count(OLD_CHARTER) != 1:
            raise TeamMoveError("team_move_exact_head_charter_required")
        result = result.replace(OLD_CHARTER, NEW_CHARTER, 1)
    after = parse_agent_text(result.replace("\r\n", "\n"), expected_name=agent_id)
    from dataclasses import replace
    expected = replace(definition, team="default", role="worker",
        system_prompt=definition.system_prompt.replace(OLD_CHARTER, NEW_CHARTER, 1) if agent_id == "consultant_head" else definition.system_prompt)
    if after != expected:
        raise TeamMoveError("team_move_agent_delta_not_exact")
    return result.encode("utf-8")


def plan_teams(contents: bytes) -> bytes:
    """Only source removal and target workers; routing/unknown metadata stay exact."""
    import copy
    text = contents.decode("utf-8")
    top = _literal(text)
    if "teams" not in top:
        raise TeamMoveError("team_move_topology_unsupported")
    teams = _mapping(top["teams"][1])
    if not {"consultant", "default"} <= teams.keys():
        raise TeamMoveError("team_move_topology_unsupported")
    raw = yaml.safe_load(text)
    registry = TeamsRegistry.from_bytes(contents)
    source, target = (registry.manager_for_team(team) for team in ("consultant", "default"))
    if (source.kind != "agent" or source.name != "consultant_head" or source.workers != ("consultant_codex",)
            or target.kind != "human" or target.principal != "founder"
            or set(target.workers).intersection(AGENTS) or registry.default_team != "default"
            or registry.task_default_team != "engineering" or "task_default_team" in raw):
        raise TeamMoveError("team_move_topology_unsupported")
    # Entry metadata has no runtime representation. Refuse rather than silently
    # discard metadata belonging to the retiring team.
    if set(raw["teams"]["consultant"]) != {"manager", "workers"}:
        raise TeamMoveError("team_move_source_metadata_unsupported")
    expected = copy.deepcopy(raw)
    del expected["teams"]["consultant"]
    expected["teams"]["default"]["workers"] = list(target.workers) + list(AGENTS)
    key, node = teams["consultant"]
    start = key.start_mark.index - key.start_mark.column
    end = node.end_mark.index - node.end_mark.column
    if end <= start:
        raise TeamMoveError("team_move_unbounded_source_block")
    retained = []
    for line in text[start:end].splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            retained.append(line)
        elif "#" in line:
            # Inline quoted # characters cannot be safely treated as comments.
            raise TeamMoveError("team_move_source_inline_comment_unsupported")
    changes = [(start, end, "".join(retained))]
    workers = _mapping(teams["default"][1])["workers"][1]
    if not isinstance(workers, yaml.SequenceNode):
        raise TeamMoveError("team_move_workers_sequence_required")
    if workers.flow_style and workers.start_mark.line == workers.end_mark.line:
        rendered = "[" + ", ".join(yaml.safe_dump(x, default_style='"').strip() for x in expected["teams"]["default"]["workers"]) + "]"
        changes.append((workers.start_mark.index, workers.end_mark.index, rendered))
    elif not workers.flow_style and workers.value:
        last = workers.value[-1]
        line_end = text.find("\n", last.end_mark.index)
        if line_end < 0:
            raise TeamMoveError("team_move_workers_final_line_unsupported")
        indent = workers.start_mark.column
        newline = "\r\n" if "\r\n" in text else "\n"
        changes.append((line_end + 1, line_end + 1, "".join(" " * indent + "- " + a + newline for a in AGENTS)))
    else:
        raise TeamMoveError("team_move_workers_layout_unsupported")
    result = _edits(text, changes).encode("utf-8")
    if yaml.safe_load(result) != expected:
        raise TeamMoveError("team_move_roster_delta_not_exact")
    return result


def _identity(info) -> PathIdentity:
    if (info.st_uid != os.getuid() or info.st_gid != os.getgid()
            or stat.S_IMODE(info.st_mode) > 0o777):
        raise TeamMoveError("team_move_file_owner_or_mode")
    return PathIdentity(device=info.st_dev, inode=info.st_ino, mode=stat.S_IMODE(info.st_mode))


def read_regular(path: Path) -> tuple[bytes, PathIdentity]:
    """No-follow descriptor read with path/descriptor identity revalidation."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
            raise TeamMoveError("team_move_regular_file_required")
        identity = _identity(before)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        after = os.fstat(fd)
        if (len(raw) > 1024 * 1024 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or (path.lstat().st_dev, path.lstat().st_ino) != (after.st_dev, after.st_ino)):
            raise TeamMoveError("team_move_file_changed")
        return raw, identity
    finally:
        os.close(fd)


def check_parents(root: Path, relative: str) -> Path:
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise TeamMoveError("team_move_root_redirected")
    path = root
    for part in Path(relative).parts[:-1]:
        path /= part
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise TeamMoveError("team_move_parent_redirected")
    return root / relative


def validate_candidate(root: Path, images: tuple[FileImage, ...]) -> TeamsRegistry:
    if tuple(image.path for image in images) != PATHS:
        raise TeamMoveError("team_move_canonical_closure")
    paths = OrgPaths(root)
    definitions = {a.name: a for a in prompt_loader.list_agents(paths)}
    for image in images[:2]:
        name = Path(image.path).stem
        before = base64.b64decode(image.before_base64, validate=True)
        after = base64.b64decode(image.after_base64, validate=True)
        if plan_agent(before, name) != after:
            raise TeamMoveError("team_move_candidate_delta")
        definitions[name] = parse_agent_text(after.decode().replace("\r\n", "\n"), expected_name=name)
    roster = images[-1]
    raw = base64.b64decode(roster.after_base64, validate=True)
    if plan_teams(base64.b64decode(roster.before_base64, validate=True)) != raw:
        raise TeamMoveError("team_move_candidate_delta")
    candidate = TeamsRegistry.from_bytes(raw, root=root)
    validate_team_definitions(candidate, definitions.values(), prompt_loader.list_pending(paths))
    return candidate


def capture_images(root: Path) -> tuple[FileImage, ...]:
    with canonical_roster(root):
        images = []
        for relative in PATHS:
            raw, identity = read_regular(check_parents(root, relative))
            after = plan_teams(raw) if relative == PATHS[-1] else plan_agent(raw, Path(relative).stem)
            images.append(FileImage(path=relative, identity=identity,
                before_base64=base64.b64encode(raw).decode(), before_digest=_digest(raw),
                after_base64=base64.b64encode(after).decode(), after_digest=_digest(after)))
        result = tuple(images)
        validate_candidate(root, result)
        return result


def replace_owned(root: Path, images: tuple[FileImage, ...], shared: TeamsRegistry, *, observed_after=None) -> tuple[str, ...]:
    """Exact forward-only prefix repair; never restore a before-image.

    No gate/DB transaction/await spans this file window. Each target is either
    its original inode+bytes or owned after bytes with the captured mode/owner.
    Unknown paths remain untouched. Parent directories never follow symlinks.
    """
    with canonical_roster(root):
        candidate = validate_candidate(root, images)
        # Classify ALL images before the first effect.
        states = []
        for image in images:
            target = check_parents(root, image.path)
            current, identity = read_regular(target)
            if identity.mode != image.identity.mode or identity.device != image.identity.device:
                raise TeamMoveError("team_move_canonical_identity_unknown")
            if current == base64.b64decode(image.after_base64):
                state = "after"
            elif current == base64.b64decode(image.before_base64) and identity == image.identity:
                state = "before"
            else:
                raise TeamMoveError("team_move_canonical_bytes_unknown")
            states.append((image, target, state))
        for image, target, state in states:
            if state == "before":
                current, identity = read_regular(target)
                if identity != image.identity or _digest(current) != image.before_digest:
                    raise TeamMoveError("team_move_canonical_changed")
                fd, staged = tempfile.mkstemp(prefix=".team-move-", dir=target.parent)
                primary = None
                try:
                    stream = os.fdopen(fd, "wb")
                    fd = None  # stream owns the descriptor, including fchmod failure.
                    with stream:
                        os.fchmod(stream.fileno(), image.identity.mode)
                        stream.write(base64.b64decode(image.after_base64))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(staged, target)
                except BaseException as exc:
                    primary = exc
                    raise
                finally:
                    try:
                        if fd is not None:
                            os.close(fd)
                        if os.path.lexists(staged):
                            os.unlink(staged)  # Only this invocation's acquired file.
                    except BaseException as cleanup:
                        if primary is None:
                            raise
                        primary.add_note("owned canonical staging cleanup failed: " + type(cleanup).__name__)
            # Repeat flush after an interrupted rename, including exact after.
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            observed, observed_identity = read_regular(check_parents(root, image.path))
            if _digest(observed) != image.after_digest or observed_identity.mode != image.identity.mode:
                raise TeamMoveError("team_move_canonical_readback")
            if observed_after is not None:
                observed_after(image.path)  # D's native per-file progress CAS.
        for image in images:
            raw, identity = read_regular(check_parents(root, image.path))
            if _digest(raw) != image.after_digest or identity.mode != image.identity.mode:
                raise TeamMoveError("team_move_canonical_readback")
        shared.adopt_canonical(candidate)
        return PATHS
