"""Private exact canonical planner/replacer for the approved consultant move.

No service admission, materialization, reset or live invocation.
Staging evidence commits only through the existing operation audit owner.
All three complete candidate images validate before any replacement.
"""
from __future__ import annotations

import base64
import os
import re
import stat
import uuid
from pathlib import Path

import yaml

from runtime.infrastructure.team_move_store import FileImage, PathIdentity, StagingFact, TeamMoveError, _digest
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


def _stage_path(root, operation_id, fact):
    target = check_parents(root, fact.path)
    # Exact operation-bound name was durably reserved BEFORE O_EXCL allocation.
    return target.with_name(".team-move-" + _digest(operation_id.encode()) + "-" + _digest(fact.stage_id.encode()))


def _sync_parent(target, expected):
    fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if _identity(os.fstat(fd)) != expected or _identity(target.parent.lstat()) != expected:
            raise TeamMoveError("team_move_staging_parent_changed")
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_observation(root, operation_id, fact):
    path = _stage_path(root, operation_id, fact)
    if _identity(path.parent.lstat()) != fact.parent:
        raise TeamMoveError("team_move_staging_parent_changed")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path, None
    if (fact.identity is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or _identity(info) != fact.identity):
        # Allocation-to-record interruption, collision or substituted inode:
        # intent is a cleanup obligation, never proof of an acquired file.
        raise TeamMoveError("team_move_staging_identity_unknown_" + fact.stage_id)
    return path, info


def reconcile_staging(root, row, store):
    """Only exact acquired artifacts; owner-finished never establishes cleanup."""
    images = _recorded_images(row)
    for fact in store.staging(row):
        path, info = _stage_observation(root, row["operation_id"], fact)
        if fact.state == "disposed":
            if info is not None:
                raise TeamMoveError("team_move_disposed_staging_reappeared_" + fact.stage_id)
            continue
        target = check_parents(root, fact.path)
        disposition = "absent"
        if info is not None:
            os.unlink(path)
            disposition = "unlinked"
        elif fact.identity is not None:
            raw, actual = read_regular(target)
            image = next(i for i in images if i.path == fact.path)
            if actual == fact.identity and _digest(raw) == image.after_digest:
                with target.open("rb") as stream:
                    os.fsync(stream.fileno())
                disposition = "replaced"
        _sync_parent(target, fact.parent)
        store.record_staging(row, fact.model_copy(update={"state": "disposed", "disposition": disposition}))


def _recorded_images(row):
    from runtime.infrastructure.team_move_store import MoveRequest, _decode
    return _decode(MoveRequest, row["request_bytes"]).files


def verify_staging(root, row, store, images):
    """Read-only terminal disposition and canonical inode coverage at release."""
    facts = store.staging(row)
    for fact in facts:
        _path, info = _stage_observation(root, row["operation_id"], fact)
        if fact.state != "disposed" or info is not None:
            raise TeamMoveError("team_move_staging_cleanup_unproven_" + fact.stage_id)
    for image in images:
        raw, identity = read_regular(check_parents(root, image.path))
        if (_digest(raw) != image.after_digest
                or not any(f.path == image.path and f.disposition == "replaced" and f.identity == identity for f in facts)):
            # Historical records remain readable, but unrecorded random staging
            # from an older writer cannot be called clean by this new owner.
            raise TeamMoveError("team_move_legacy_staging_ownership_unknown")
    return facts


def replace_owned(root: Path, images: tuple[FileImage, ...], shared: TeamsRegistry, *, candidate,
                  row, store, observed_after) -> tuple[str, ...]:
    """Short exact-image mutation; complete candidate discovery is pre-captured.

    Caller revalidates the native roster/profile revision before entering its
    mutation leases. No all-agent discovery or snapshot capture occurs here.
    """
    from runtime.infrastructure.team_move_store import ForwardDecision, Progress, _decode
    with canonical_roster(root):
        reconcile_staging(root, row, store)
        states = []
        for image in images:
            target = check_parents(root, image.path)
            current, identity = read_regular(target)
            if identity.mode != image.identity.mode or identity.device != image.identity.device:
                raise TeamMoveError("team_move_canonical_identity_unknown")
            if current == base64.b64decode(image.after_base64):
                facts = store.staging(row)
                if not any(f.path == image.path and f.disposition == "replaced" and f.identity == identity for f in facts):
                    raise TeamMoveError("team_move_legacy_staging_ownership_unknown")
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
                progress = _decode(Progress, row["progress_bytes"])
                owner = progress.recovery[-1].owner_id if progress.recovery else _decode(ForwardDecision, row["decision_bytes"]).writer_invocation_id
                fact = StagingFact(version=1, request_digest=row["request_digest"], stage_id=uuid.uuid4().hex,
                    owner_id=owner, path=image.path, parent=_identity(target.parent.lstat()), identity=None, state="intent", disposition=None)
                store.record_staging(row, fact)
                staged = _stage_path(root, row["operation_id"], fact)
                fd = None
                primary = None
                try:
                    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    os.fchmod(fd, image.identity.mode)
                    fact = fact.model_copy(update={"state": "acquired", "identity": _identity(os.fstat(fd))})
                    store.record_staging(row, fact)  # Must commit before writing or replacing.
                    _sync_parent(target, fact.parent)
                    stream = os.fdopen(fd, "wb")
                    fd = None
                    with stream:
                        stream.write(base64.b64decode(image.after_base64))
                        stream.flush()
                        os.fsync(stream.fileno())
                    _stage_observation(root, row["operation_id"], fact)
                    os.replace(staged, target)
                except BaseException as exc:
                    primary = exc
                    raise
                finally:
                    try:
                        if fd is not None:
                            os.close(fd)
                        reconcile_staging(root, row, store)
                    except BaseException as cleanup:
                        if primary is None:
                            raise
                        primary.add_note("canonical staging unresolved; retain operation hold: " + str(cleanup))
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            _sync_parent(target, _identity(target.parent.lstat()))
            observed, observed_identity = read_regular(check_parents(root, image.path))
            if _digest(observed) != image.after_digest or observed_identity.mode != image.identity.mode:
                raise TeamMoveError("team_move_canonical_readback")
            row = observed_after(image.path)
        verify_staging(root, row, store, images)
        shared.adopt_canonical(candidate)
        return PATHS
