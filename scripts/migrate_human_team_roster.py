"""Explicit offline THR296 roster check/apply/complete-or-compensate.

No daemon attachment, supervisor mutation, live migration or traffic release.
A design plan is not an executable checked manifest. See the migration runbook.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Iterator

import yaml

from runtime.orchestrator.agent_def import parse_agent_text
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError

AGENTS = ("consultant_head", "consultant_codex")
CANONICAL = ("org/agents/consultant_head.md", "org/agents/consultant_codex.md", "org/teams.yaml")
ADVICE = "Advise the founder and HappyRanch teams on product, services and business operations. You are an individual consultant reporting directly to the founder."
SOURCE = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def image(path: Path) -> dict:
    """Closed exact bytes/type/mode/owner; links retain their raw text."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory", "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid, "gid": info.st_gid}
    if info.st_nlink != 1 or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ValueError(f"unsupported file identity: {path}")
    data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return {"kind": "link" if path.is_symlink() else "file", "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid, "bytes": base64.b64encode(data).decode(), "sha256": digest(data)}



def validate_image(value: dict) -> None:
    if not isinstance(value, dict) or value.get("kind") not in ("absent", "directory", "file", "link"):
        raise ValueError("invalid_closed_image")
    if value["kind"] == "absent":
        if set(value) != {"kind"}:
            raise ValueError("invalid_absent_image")
        return
    if value["kind"] == "directory":
        if (set(value) != {"kind", "mode", "uid", "gid"}
                or type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o777
                or value["uid"] != os.getuid() or value["gid"] != os.getgid()):
            raise ValueError("unrestorable_directory_metadata")
        return
    if (set(value) != {"kind", "mode", "uid", "gid", "bytes", "sha256"}
            or type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o777
            or value["uid"] != os.getuid() or value["gid"] != os.getgid()
            or digest(base64.b64decode(value["bytes"], validate=True)) != value["sha256"]
            or (value["kind"] == "link" and value["mode"] != 0o777)):
        raise ValueError("unrestorable_closed_image_metadata")


def preservation_inventory(root: Path) -> dict:
    """Uncapped read-only bytes/type/metadata closure without following links."""
    result = {}
    def visit(path: Path) -> None:
        info = path.lstat()
        if info.st_uid != os.getuid() or info.st_gid != os.getgid():
            raise ValueError("preservation_owner_not_restorable")
        rel = str(path.relative_to(root))
        entry = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        if stat.S_ISLNK(info.st_mode):
            entry.update(kind="link", target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            entry.update(kind="directory")
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            sha = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    sha.update(chunk)
            entry.update(kind="file", sha256=sha.hexdigest())
        else:
            raise ValueError("unsupported_preservation_identity")
        result[rel] = entry
        if entry["kind"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)
    visit(root)
    return result


def native_workspace_residue(root: Path, manifest: dict) -> dict:
    """Recognize only declared outputs' genuine native interrupted prefixes.

    Names alone confer no ownership. A new native journal must bind this OP,
    and each sibling must have the original/target bytes, owner and native
    creation mode. Retained pre-check siblings are never adopted or removed.
    This is cooperative offline recovery, not a hostile same-user guarantee.
    """
    baseline = manifest["preservation_inventory"]
    found = {}
    with read_db(root / "happyranch.db") as conn:
        verify_owned_publication(conn, manifest)
        original = set(manifest["control_row_hashes"]["workflow_publication_journals"])
        started = any(digest(repr(tuple(row)).encode()) not in original
                      for row in conn.execute("SELECT * FROM workflow_publication_journals"))
    if not started:
        return found
    for rel, before in manifest["before"].items():
        target = root / rel
        if not target.parent.is_dir():
            continue
        instruction = (rel in {f"workspaces/{agent}/{name}" for agent in AGENTS
                               for name in ("AGENTS.md", "CLAUDE.md")})
        for sibling in target.parent.iterdir():
            key = str(sibling.relative_to(root))
            if key in baseline or key in found:
                continue
            candidates = []
            backup = False
            if (len(Path(rel).parts) == 5 and Path(rel).parts[3] == "skills"
                    and sibling.name == ".tmp." + target.name):
                candidates = [manifest["after"][rel]]
            elif re.fullmatch(r"\." + re.escape(target.name) + r"\.roster-[A-Za-z0-9_]{8}", sibling.name):
                # mkstemp creates a regular 0600 file before write/fchmod;
                # link staging unlinks it then creates the final raw link.
                candidates = [before, manifest["after"][rel]]
            elif instruction:
                match = re.fullmatch(re.escape(target.name) +
                    r"\.happyranch-\d{8}T\d{12}Z\.(bak|tmp|restore\.tmp|lnk|restore\.lnk)(?:-[1-9]\d*)?", sibling.name)
                if match is None:
                    continue
                operation = match[1]
                backup = operation == "bak"
                candidates = ([before] if operation in ("bak", "restore.tmp", "restore.lnk")
                              else [manifest["after"][rel]])
            else:
                continue
            value = image(sibling)
            matches = []
            for candidate in candidates:
                if candidate["kind"] not in ("file", "link"):
                    continue
                if value.get("uid") != candidate["uid"] or value.get("gid") != candidate["gid"]:
                    continue
                if value["kind"] == "link" and value == candidate:
                    matches.append(candidate)
                elif (candidate['kind'] == 'link' and value['kind'] == 'file'
                      and re.fullmatch(r'\.' + re.escape(target.name) + r'\.roster-[A-Za-z0-9_]{8}', sibling.name)
                      and value['mode'] == 0o600 and base64.b64decode(value['bytes']) == b''):
                    # The utility's link replacement first reserves an empty
                    # mkstemp file, then unlinks it and creates the raw link.
                    matches.append(candidate)
                elif value["kind"] == candidate["kind"] == "file":
                    raw = base64.b64decode(value["bytes"])
                    if (value["mode"] in (0o600, candidate["mode"])
                            and base64.b64decode(candidate["bytes"]).startswith(raw)):
                        matches.append(candidate)
            if not matches or backup and before["kind"] != "file":
                raise ValueError(f"unknown_native_workspace_prefix:{key}")
            found[key] = {"observed": value, "backup": backup,
                          "complete": before if backup else matches[0]}
    return found


def close_native_workspace_residue(root: Path, manifest: dict, *, compensate: bool) -> None:
    """Close validated owned siblings, never sweep by filename prefix."""
    for rel, entry in native_workspace_residue(root, manifest).items():
        path = root / rel
        if image(path) != entry["observed"]:
            raise ValueError("native_workspace_prefix_CAS_lost")
        if entry["backup"] and not compensate:
            # Preserve the original bytes even at a kill inside its write.
            # Live outputs are still produced by the unchanged materializer.
            if image(path) != entry["complete"]:
                durable_replace(path, entry["complete"])
        else:
            durable_replace(path, {"kind": "absent"})


def known_output_prefix(root: Path, manifest: dict, rel: str) -> bool:
    current = image(root / rel)
    before, after = manifest["before"][rel], manifest["after"][rel]
    if current in (before, after):
        return True
    # Only these unchanged adapter writes truncate the live file. Canonical
    # roster and instruction files are replaced atomically and never admit a
    # partial live image. A checked old/target metadata match is mandatory.
    if not any(rel == f"workspaces/{agent}/.claude/settings.json"
               or rel == f"workspaces/{agent}/opencode.json" for agent in AGENTS):
        return False
    if current.get("kind") != "file" or after["kind"] != "file":
        return False
    if any(current[key] != after[key] for key in ("mode", "uid", "gid")):
        return False
    with read_db(root / "happyranch.db") as conn:
        verify_owned_publication(conn, manifest)
        baseline = set(manifest["control_row_hashes"]["workflow_publication_journals"])
        if not any(digest(repr(tuple(row)).encode()) not in baseline
                   for row in conn.execute("SELECT * FROM workflow_publication_journals")):
            return False
    return base64.b64decode(after["bytes"]).startswith(base64.b64decode(current["bytes"]))


def native_authority_residue(root: Path, manifest: dict) -> dict:
    """Observe a genuine own journal's exact native authority write prefix."""
    found = {}
    with read_db(root / 'happyranch.db') as conn:
        verify_owned_publication(conn, manifest)
        original = set(manifest['control_row_hashes']['workflow_publication_journals'])
        for row in conn.execute('SELECT * FROM workflow_publication_journals'):
            if digest(repr(tuple(row)).encode()) in original:
                continue
            rel = f"org/.workflow-authority.json.{row['id']}.staging"
            value = image(root / rel)
            if value['kind'] == 'absent':
                continue
            snapshot = bytes(row['snapshot_bytes'])
            if (row['state'] not in ('file_phase_reserved', 'canonical_published', 'forward_recovery_required')
                    or value['kind'] != 'file' or value['mode'] != 0o666 & ~manifest['native_creation_mask']
                    or value['uid'] != os.getuid() or value['gid'] != os.getgid()
                    or not snapshot.startswith(base64.b64decode(value['bytes']))):
                raise ValueError('unknown_authority_staging_prefix')
            found[rel] = {'observed': value, 'complete': base64.b64decode(value['bytes']) == snapshot,
                          'state': row['state']}
    return found


def close_native_authority_residue(root: Path, manifest: dict) -> None:
    """Discard only a checked own temp; its unchanged native owner decides.

    A complete reserved stage remains for native recovery to publish. A partial
    reserved stage is discarded so native recovery records aborted_unpublished
    unless the canonical snapshot already landed. A forward recovery rewrites
    its missing canonical snapshot. This utility never settles the journal.
    """
    for rel, entry in native_authority_residue(root, manifest).items():
        if entry['complete'] and entry['state'] == 'file_phase_reserved':
            continue
        if image(root / rel) != entry['observed']:
            raise ValueError('authority_staging_prefix_CAS_lost')
        durable_replace(root / rel, {'kind': 'absent'})


def owned_replacement_residue(root: Path, manifest: dict) -> dict:
    """Exact mkstemp prefixes require this OP's committed native fence."""
    found = {}
    for rel in manifest['before']:
        path = root / rel
        if not path.parent.is_dir():
            continue
        for sibling in path.parent.iterdir():
            if re.fullmatch(r'\.' + re.escape(path.name) + r'\.roster-[a-z0-9_]{8}', sibling.name) is None:
                continue
            with read_db(root / 'happyranch.db') as conn:
                verify_owned_publication(conn, manifest)
                baseline = set(manifest['control_row_hashes']['workflow_publication_journals'])
                if not any(digest(repr(tuple(row)).encode()) not in baseline
                           for row in conn.execute('SELECT * FROM workflow_publication_journals')):
                    raise ValueError('replacement_prefix_without_owned_fence')
            value = image(sibling)
            targets = (manifest['before'][rel], manifest['after'][rel])
            if (value.get('uid') != os.getuid() or value.get('gid') != os.getgid()
                    or not any(target['kind'] in ('file', 'link') and (
                        value['kind'] == 'file' and value['mode'] in (0o600, target['mode'])
                        and (target['kind'] == 'link' and base64.b64decode(value['bytes']) == b''
                             or target['kind'] == 'file' and base64.b64decode(target['bytes']).startswith(base64.b64decode(value['bytes'])))
                        or value['kind'] == 'link' and value == target) for target in targets)):
                raise ValueError('unknown_owned_replacement_prefix')
            found[str(sibling.relative_to(root))] = value
    return found


def close_owned_replacement_residue(root: Path, manifest: dict) -> None:
    for rel, expected in owned_replacement_residue(root, manifest).items():
        if image(root / rel) != expected:
            raise ValueError('replacement_prefix_CAS_lost')
        durable_replace(root / rel, {'kind': 'absent'})


def verify_preserved_paths(root: Path, manifest: dict) -> None:
    expected = manifest["preservation_inventory"]
    actual = preservation_inventory(root)
    mutable = set(manifest["before"]) | set(native_workspace_residue(root, manifest)) | {"happyranch.db", "happyranch.db-wal", "happyranch.db-shm", "org/.workflow-authority.json"}
    mutable.update(native_authority_residue(root, manifest))
    mutable.update(owned_replacement_residue(root, manifest))
    for rel in set(expected) | set(actual):
        if rel in mutable:
            continue
        if expected.get(rel) != actual.get(rel):
            raise ValueError(f"retained_path_changed:{rel}")


def durable_replace(path: Path, target: dict) -> None:
    """One exact path replacement, with file and containing-directory flush."""
    validate_image(target)
    if target["kind"] == "absent":
        if path.is_dir() and not path.is_symlink():
            path.rmdir()  # exact known empty directory only, never recursive
        else:
            path.unlink(missing_ok=True)
    elif target["kind"] == "directory":
        path.mkdir(mode=target["mode"], exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("directory_identity_changed")
        os.chmod(path, target["mode"])
    else:
        data = base64.b64decode(target["bytes"], validate=True)
        if digest(data) != target["sha256"] or target["uid"] != os.getuid() or target["gid"] != os.getgid():
            raise ValueError("metadata_or_image_not_owned")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(prefix=f".{path.name}.roster-", dir=path.parent)
        stage = Path(raw)
        try:
            with os.fdopen(fd, "wb") as out:
                if target["kind"] == "file":
                    out.write(data)
                    out.flush()
                    os.fchmod(out.fileno(), target["mode"])
                    os.fsync(out.fileno())
            if target["kind"] == "link":
                stage.unlink()
                stage.symlink_to(data.decode())
            os.replace(stage, path)
        finally:
            stage.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if image(path) != target:
        raise ValueError("replacement_readback_mismatch")


def verify_global_assets(manifest: dict, *, initial: bool = False) -> None:
    """Exact closed shared-store closure; no credential or permission rewrite."""
    from runtime.config import Settings
    from runtime.skills.canonical_store import _get_canonical_store_root
    store = _get_canonical_store_root(Settings(project_root=SOURCE)).resolve(strict=True)
    if str(store) != manifest["canonical_store_root"]:
        raise ValueError("effective_canonical_store_path_changed")
    if preservation_inventory(Path(manifest["closed_canonical_store_restore"])) != manifest["canonical_store_inventory"]:
        raise ValueError("canonical_store_closed_restore_changed")
    current = preservation_inventory(store)
    baseline = manifest["canonical_store_inventory"]
    mutable = manifest["global_before"]
    staging = manifest["global_staging_images"]
    if set(mutable) != set(manifest["global_after"]):
        raise ValueError("global_manifest_output_closure_mismatch")
    if not initial and current != baseline:
        root = Path(manifest['runtime_root']) / 'orgs' / manifest['org']
        with read_db(root / 'happyranch.db') as conn:
            verify_owned_publication(conn, manifest)
            original = set(manifest['control_row_hashes']['workflow_publication_journals'])
            if not any(digest(repr(tuple(row)).encode()) not in original
                       for row in conn.execute('SELECT * FROM workflow_publication_journals')):
                raise ValueError('global_prefix_without_owned_publication')
    for rel in set(current) | set(baseline):
        if rel not in mutable and rel not in staging and current.get(rel) != baseline.get(rel):
            raise ValueError(f"retained_global_path_changed:{rel}")
    for rel, before in mutable.items():
        relative = Path(rel)
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_global_manifest_path")
        validate_image(before)
        validate_image(manifest["global_after"][rel])
        path = store / rel
        if not path.parent.resolve().is_relative_to(store):
            raise ValueError("global_path_redirected")
        observed = image(path)
        allowed = (manifest["global_after"][rel], manifest["global_transitional_images"][rel])
        if observed != before and (initial or observed not in allowed):
            raise ValueError(f"global_before_image_CAS_or_unknown_state:{rel}")
    for rel, expected in staging.items():
        path = store / rel
        if not path.parent.resolve().is_relative_to(store):
            raise ValueError("global_staging_path_redirected")
        observed = image(path)
        partial = (observed.get("kind") == expected.get("kind") == "file"
                   and all(observed.get(field) == expected[field] for field in ("mode", "uid", "gid"))
                   and base64.b64decode(expected["bytes"]).startswith(base64.b64decode(observed["bytes"])))
        if observed != {"kind": "absent"} and (initial or observed != expected and not partial):
            raise ValueError(f"unknown_native_global_staging_state:{rel}")


def restore_global_assets(manifest: dict) -> None:
    """Compensate only declared known package paths, without deleting trees."""
    verify_global_assets(manifest)
    store = Path(manifest["canonical_store_root"])
    before = {**manifest["global_before"], **{rel: {"kind": "absent"} for rel in manifest["global_staging_images"]}}
    # Remove known newly built package files before their now-empty parents.
    for rel in sorted(before, key=lambda key: (-len(Path(key).parts), key)):
        if before[rel]["kind"] == "absent" and image(store / rel) != before[rel]:
            durable_replace(store / rel, before[rel])
    for rel in sorted(before, key=lambda key: (len(Path(key).parts), key)):
        if before[rel]["kind"] != "absent" and image(store / rel) != before[rel]:
            durable_replace(store / rel, before[rel])


def native_creation_mask() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("Umask:"):
            return int(line.split()[1], 8)
    raise ValueError("native_creation_mask_observation_unavailable")


def resume_native_package_hardening(manifest: dict) -> None:
    """Close only a proven own rename-before-hardening prefix with its owner."""
    from runtime.skills.canonical_store import _apply_readonly_hardening
    verify_global_assets(manifest)
    store = Path(manifest["canonical_store_root"])
    roots = {str(Path(*Path(rel).parts[:3])) for rel in manifest["global_after"] if len(Path(rel).parts) >= 3}
    for rel in sorted(roots):
        if not (store / rel).exists():
            continue
        members = {key: value for key, value in manifest["global_after"].items() if key == rel or key.startswith(rel + "/")}
        if all(image(store / key) == value for key, value in members.items()):
            continue
        if any(image(store / key) not in (value, manifest["global_transitional_images"][key]) for key, value in members.items()):
            raise ValueError("installed_native_package_is_not_complete_owned_prefix")
        _apply_readonly_hardening(store / rel)
        if any(image(store / key) != value for key, value in members.items()):
            raise ValueError("native_hardening_closed_output_mismatch")


def paths(args: argparse.Namespace) -> tuple[Path, Path]:
    if not args.runtime_root.is_absolute() or re.fullmatch(r"[a-z0-9-]{1,40}", args.org) is None:
        raise ValueError("invalid_runtime_or_org")
    runtime = args.runtime_root.resolve(strict=True)
    marker = yaml.safe_load((runtime / "happyranch.yaml").read_text())
    if marker.get("schema_version") != 2 or marker.get("type") != "multi-org-runtime":
        raise ValueError("explicit_multi_org_runtime_required")
    root = runtime / "orgs" / args.org
    if root.is_symlink() or (runtime / "orgs").is_symlink() or root.resolve(strict=True) != root:
        raise ValueError("org_path_redirected")
    for rel in CANONICAL:
        if (root / rel).is_symlink() or (root / rel).resolve().is_relative_to(root) is False:
            raise ValueError("canonical_path_redirected")
    return runtime, root


def source_identity() -> str:
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=SOURCE).strip():
        raise ValueError("clean_committed_candidate_required")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()


def reader_binding() -> dict:
    """Bind the effective offline reader, rather than an installed-path label."""
    executable = Path(sys.executable).resolve(strict=True)
    if sys.version_info[:2] != (3, 14):
        raise ValueError("effective_python314_required")
    return {"source_root": str(SOURCE), "source_sha": source_identity(),
            "python": str(executable), "python_sha256": digest(executable.read_bytes()),
            "python_version": sys.version}


def registered_runtime(runtime: Path) -> None:
    from runtime.runtime import daemon_home, RuntimeDir
    RuntimeDir.load(runtime)
    registry = daemon_home() / "runtimes.yaml"
    if registry.is_symlink():
        raise ValueError("effective_runtime_registration_redirected")
    state = yaml.safe_load(registry.read_bytes())
    registered = state.get("registered") if isinstance(state, dict) else None
    if (not isinstance(registered, list) or state.get("active") is not None
            or any(not isinstance(value, str) or not Path(value).is_absolute()
                   or Path(value).is_symlink() or Path(value).resolve(strict=True) != Path(value)
                   for value in registered)
            or len(registered) != len(set(registered)) or registered.count(str(runtime)) != 1):
        raise ValueError("effective_runtime_registration_mismatch")


def restore_verified(manifest: dict) -> None:
    """Recheck both independently closed backups before any mutation/replay."""
    if preservation_inventory(Path(manifest["closed_restore_root"])) != manifest["preservation_inventory"]:
        raise ValueError("closed_restore_changed")
    if preservation_inventory(Path(manifest["closed_canonical_store_restore"])) != manifest["canonical_store_inventory"]:
        raise ValueError("closed_canonical_store_restore_changed")
    backup = Path(manifest["closed_database_backup"])
    if backup != Path(manifest["closed_restore_root"]) / "happyranch.db" or image(backup) != manifest["closed_backup_image"]:
        raise ValueError("closed_backup_changed")
    with read_db(backup) as restored:
        if domain_signature(restored) != manifest["domain_signature"]:
            raise ValueError("backup_restore_history_mismatch")
    closed_backup_flush(backup)


def closed_backup_flush(backup: Path) -> None:
    """Flush the verified external closed copy; never checkpoint the source."""
    with backup.open('rb') as copied:
        os.fsync(copied.fileno())
    fd = os.open(backup.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def roster_delta(text: str, expected: dict) -> bytes:
    """Replace only approved YAML nodes, preserving unrelated literal text.

    Flow/alias/duplicate layouts cannot safely identify a three-file text delta
    and refuse instead of silently normalizing the entire canonical document.
    """
    document = yaml.compose(text)
    if not isinstance(document, yaml.MappingNode) or document.flow_style:
        raise ValueError("literal_block_roster_required")
    def mapping(node):
        if not isinstance(node, yaml.MappingNode) or node.flow_style:
            raise ValueError("literal_block_roster_required")
        result = {key.value: (key, value) for key, value in node.value}
        if len(result) != len(node.value):
            raise ValueError("duplicate_roster_key")
        return result
    top = mapping(document)
    teams = mapping(top["teams"][1])
    edits = []
    def replace_value(node, replacement):
        if node.start_mark.line != node.end_mark.line:
            raise ValueError("literal_single_line_roster_value_required")
        edits.append((node.start_mark.index, node.end_mark.index, replacement))
    consultant_key, consultant_value = teams["consultant"]
    if "default" not in teams:
        # Preserve the original block and every unrelated comment/blank byte.
        edits.append((consultant_key.start_mark.index, consultant_key.end_mark.index, "default"))
        children = mapping(consultant_value)
        replace_value(children["manager"][1], "{kind: human, principal: founder}")
        replace_value(children["workers"][1], "[consultant_head, consultant_codex]")
    else:
        # Remove only this closed obsolete block. Comments have no ownership
        # meaning and remain literal text, including comments before next key.
        start = consultant_key.start_mark.index - consultant_key.start_mark.column
        end = consultant_value.end_mark.index - consultant_value.end_mark.column
        if end <= start:
            raise ValueError("unbounded_roster_text_delta")
        comments = []
        for line in text[start:end].splitlines(keepends=True):
            if "#" in line:
                prefix, comment = line.split("#", 1)
                comments.append(line if not prefix.strip() else " " * consultant_key.start_mark.column + "#" + comment)
            elif not line.strip():
                comments.append(line)
        edits.append((start, end, "".join(comments)))
        children = mapping(teams["default"][1])
        replace_value(children["workers"][1], "[consultant_head, consultant_codex]")
    for name, value in (("default_team", "default"), ("task_default_team", "engineering")):
        if name in top:
            node = top[name][1]
            if not isinstance(node, yaml.ScalarNode):
                raise ValueError("literal_roster_pointer_required")
            edits.append((node.start_mark.index, node.end_mark.index, value))
        else:
            edits.append((len(text), len(text), ("" if text.endswith("\n") else "\n") + name + ": " + value + "\n"))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    if yaml.safe_load(text) != expected:
        raise ValueError("literal_roster_delta_not_exact")
    return text.encode()


def containment(plan: dict, runtime: Path) -> dict:
    """Observe actual persistent masks, registry detachment and no daemon.

    Unsupported/custom/alternate supervisors must be resolved by the operator,
    never accepted from a boolean or an unexecuted design-plan assertion.
    """
    declaration = plan.get("containment")
    if not isinstance(declaration, dict) or not declaration.get("systemd_user_units") or not declaration.get("registry_paths"):
        raise ValueError("actual_persistent_restart_inhibition_required")
    if (len(declaration["registry_paths"]) != len(set(declaration["registry_paths"]))):
        raise ValueError("duplicate_registry_inventory")
    from runtime.runtime import daemon_home
    home = daemon_home().resolve(strict=True)
    if declaration.get("daemon_home") != str(home) or str(home / "runtimes.yaml") not in declaration["registry_paths"]:
        raise ValueError("effective_daemon_registry_inventory_required")
    # This bounded adapter supports persistently masked systemd services.
    # Desktop/login startup wrappers have no inhibition owner here; do not
    # silently certify a VM which also has those launch channels installed.
    for startup in (Path.home() / ".config/autostart", Path("/etc/xdg/autostart")):
        if startup.is_symlink() or startup.exists() and any(startup.iterdir()):
            raise ValueError("unsupported_desktop_autostart_containment")
    rc_local = Path("/etc/rc.local")
    if rc_local.exists() and os.access(rc_local, os.X_OK):
        raise ValueError("unsupported_rc_local_containment")
    observed = []
    for scope, flags, mask_root in (("user", ["--user"], Path.home() / ".config/systemd/user"),
                                    ("system", [], Path("/etc/systemd/system"))):
        units = declaration.get(f"systemd_{scope}_units", [])
        if (not isinstance(units, list) or len(units) != len(set(units))
                or any(not isinstance(unit, str) or re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit) is None for unit in units)):
            raise ValueError("invalid_supervisor_inventory")
        # Include transient/loaded services as well as installed unit files.
        actual_units = set()
        for selection in (["list-unit-files"], ["list-units", "--all"]):
            inventory = subprocess.run(["systemctl", *flags, *selection, "--type=service", "--plain", "--no-legend", "--no-pager"], capture_output=True, text=True, timeout=10, check=True)
            actual_units.update(line.split()[0] for line in inventory.stdout.splitlines() if line.strip())
        if not set(units).issubset(actual_units):
            raise ValueError("declared_supervisor_missing_from_actual_inventory")
        excluded = []
        for unit in sorted(actual_units - set(units)):
            # Pre/post/reload commands can start a wrapper just as ExecStart
            # can. A shell/interpreter/remote/container trampoline does not
            # prove exclusion from this runtime by omitting its path in argv.
            launch = subprocess.run(["systemctl", *flags, "show", unit,
                "--property=ExecStart,ExecStartPre,ExecStartPost,ExecReload", "--value"],
                capture_output=True, text=True, timeout=10, check=True).stdout
            if "happyranch" in launch or "runtime.daemon" in launch or str(runtime) in launch:
                raise ValueError("undeclared_alternate_supervisor")
            commands = re.findall(r"\{ path=([^ ;}]+) ; argv\[\]=(.*?) ; ignore_errors=(yes|no) ;", launch)
            if launch.strip() and not commands:
                raise ValueError(f"uninspectable_supervisor_launch:{scope}:{unit}")
            if commands:
                # Neither an ELF signature nor an exact executable hash
                # proves that its internal/configured launches exclude this
                # runtime. This adapter has no independently validated
                # exclusion capability for unmasked command-bearing units.
                # Do not turn a plan's assertion into that capability.
                raise ValueError(f"unproven_supervisor_launch_closure:{scope}:{unit}")
            excluded.append({"unit": unit, "commands": []})
        for unit in units:
            mask = mask_root / unit
            if not mask.is_symlink() or os.readlink(mask) != "/dev/null":
                raise ValueError("persistent_supervisor_mask_required")
            run = subprocess.run(["systemctl", *flags, "show", unit, "--property=LoadState,ActiveState,SubState"], capture_output=True, text=True, timeout=10, check=True)
            properties = dict(line.split("=", 1) for line in run.stdout.splitlines())
            if properties != {"LoadState": "masked", "ActiveState": "inactive", "SubState": "dead"}:
                raise ValueError("supervisor_not_inhibited_and_stopped")
            observed.append({"scope": scope, "unit": unit, "mask": str(mask), "properties": properties})
        observed.append({"scope": scope, "excluded_launches": excluded})
    for value in declaration["registry_paths"]:
        registry = Path(value)
        if not registry.is_absolute() or registry.is_symlink():
            raise ValueError("registry_inventory_invalid")
        state = yaml.safe_load(registry.read_text())
        if not isinstance(state, dict) or state.get("active"):
            raise ValueError("alternate_registered_launch_not_inhibited")
    # Exhaustive same-user observation, fail closed on inaccessible candidates.
    proc = Path("/proc")
    if not proc.is_dir():
        raise ValueError("linux_process_census_unavailable")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            command = (entry / "cmdline").read_bytes()
            cwd = (entry / "cwd").resolve(strict=True) if command else None
        except FileNotFoundError:
            continue  # exited during census
        # A utility/observer argv mentioning R is not an executor writer.
        # Inspect actual module/command and working-directory scope instead.
        arguments = command.split(b"\0")
        names = {os.path.basename(os.fsdecode(argument)) for argument in arguments[:2]}
        if (b"runtime.daemon" in command or b"runtime/daemon/" in command
                or names.intersection({"daemon.sh", "claude", "codex", "opencode", "pi"})
                or b"happyranch\x00daemon" in command
                or b"happyranch daemon" in command
                or cwd is not None and cwd.is_relative_to(runtime)):
            raise ValueError("runtime_or_daemon_process_present")
    return {"supervisors": observed, "registry_paths": declaration["registry_paths"]}


@contextmanager
def read_db(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("database_identity_invalid")
    # A proven closed/checkpointed image needs no SQLite shared-memory writer.
    # Read committed WAL through SQLite during owned crash recovery; never
    # ignore it using immutable=1. Containment is checked before these readers.
    has_journal = any(Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size
                      for suffix in ("-wal", "-journal"))
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if has_journal else "?mode=ro&immutable=1"), uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
        conn.close()
        raise ValueError("database_integrity_refusal")
    try:
        yield conn
    finally:
        conn.close()


def domain_signature(conn: sqlite3.Connection) -> str:
    """Full retained domain history; reset/publication owners are separate."""
    excluded = {"audit_log", "workflow_authority_pointers", "workflow_publication_journals", "workflow_publication_leases", "workflow_profile_dependencies", "workflow_profile_operations", "workflow_profile_leases", "skill_validation_events"}
    rows = []
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = row[0]
        if name in excluded or name == "sqlite_sequence":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        records = [dict(record) for record in conn.execute(f"SELECT * FROM {quoted}")]
        if name == "thread_participants":
            for record in records:
                if record["agent_name"] in AGENTS:
                    record["agent_session_id"], record["last_resumed_seq"] = None, 0
        records = [tuple(record.items()) for record in records]
        rows.append((name, sorted(repr(record) for record in records)))
    return digest(canonical(rows))


def control_signature(conn: sqlite3.Connection) -> str:
    """Exact pre-edit CAS of the rows excluded from retained-domain checks."""
    tables = ("audit_log", "workflow_authority_pointers", "workflow_publication_journals",
              "workflow_publication_leases", "workflow_profile_dependencies",
              "workflow_profile_operations", "workflow_profile_leases")
    rows = [(table, sorted(repr(tuple(row)) for row in conn.execute(f'SELECT * FROM "{table}"')))
            for table in tables]
    return digest(canonical(rows))


def audit_prefix_signature(conn: sqlite3.Connection, baseline: int) -> str:
    return digest(canonical([repr(tuple(row)) for row in conn.execute(
        "SELECT * FROM audit_log WHERE id<=? ORDER BY id", (baseline,))]))


def row_hashes(conn: sqlite3.Connection, table: str) -> list[str]:
    return sorted(digest(repr(tuple(row)).encode()) for row in conn.execute(f'SELECT * FROM "{table}"'))


def verify_owned_publication(conn: sqlite3.Connection, manifest: dict) -> None:
    """Authenticate crash residue before letting the native owner recover it.

    This reader never clears a lease or edits a journal. A dead PID alone is
    insufficient: its complete token must belong to this manifest's journal.
    The native coordinator remains the only lease/journal mutation owner.
    """
    namespace = f"org/{manifest['org']}"
    prefix = f"THR296:{manifest['operation_id']}"
    original = set(manifest["control_row_hashes"]["workflow_publication_journals"])
    owned = []
    for row in conn.execute("SELECT * FROM workflow_publication_journals"):
        if digest(repr(tuple(row)).encode()) in original:
            continue
        publisher = row["publisher"]
        invocation = row["publisher_invocation"]
        if (row["namespace"] != namespace
                or publisher != prefix and not publisher.startswith(prefix + ":")
                or not invocation.startswith(f"workflow-writer:{prefix}:")
                or re.fullmatch(r"[a-f0-9]{32}", invocation.rsplit(":", 1)[-1]) is None
                or row["generation"] != row["expected_generation"] + 1
                or digest(bytes(row["snapshot_bytes"])) != row["snapshot_digest"]
                or row["recovery_owner"] != "workflow-recovery"):
            raise ValueError("unowned_publication_residue")
        owned.append(row)
    active = [row for row in owned if row["state"] not in ("cache_installed", "aborted")]
    if len(active) > 1:
        raise ValueError("ambiguous_operation_publication_residue")
    for lease in conn.execute("SELECT * FROM workflow_publication_leases"):
        token = lease["owner_token"]
        if (lease["namespace"] != namespace or len(active) != 1
                or not (token in (active[0]["publisher_invocation"], active[0]["file_phase_owner"])
                        or token.startswith(prefix + ":") and re.fullmatch(r"[a-f0-9]{32}", token.rsplit(":", 1)[-1])
                        or re.fullmatch(r"workflow-recovery:[a-f0-9]{32}", token))):
            raise ValueError("foreign_publication_lease")
        try:
            os.kill(lease["owner_pid"], 0)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise ValueError("publication_lease_liveness_unknown") from exc
        else:
            raise ValueError("live_publication_lease_owner")
    # Both accepted executors are builtin; this move creates no profile
    # operation/diagnostic lease. Never adopt a dead foreign profile owner.
    if conn.execute("SELECT 1 FROM workflow_profile_leases LIMIT 1").fetchone():
        raise ValueError("foreign_profile_lease")
    pointer = conn.execute("SELECT * FROM workflow_authority_pointers WHERE namespace=?", (namespace,)).fetchone()
    if pointer is None:
        raise ValueError("checked_authority_pointer_missing")
    if pointer["journal_id"] is not None:
        journal = conn.execute("SELECT * FROM workflow_publication_journals WHERE id=?", (pointer["journal_id"],)).fetchone()
        if (journal is None or journal["namespace"] != namespace
                or journal["generation"] != pointer["current_generation"]
                or journal["snapshot_digest"] != pointer["snapshot_digest"]):
            raise ValueError("operation_authority_pointer_conflict")


def verify_control_history(conn: sqlite3.Connection, manifest: dict) -> None:
    """Retained publication/profile/audit rows cannot be borrowed or rewritten."""
    original = manifest["control_row_hashes"]
    current_journals = conn.execute("SELECT * FROM workflow_publication_journals").fetchall()
    observed = {digest(repr(tuple(row)).encode()) for row in current_journals}
    if not set(original["workflow_publication_journals"]).issubset(observed):
        raise ValueError("retained_publication_journal_changed")
    owner = f"THR296:{manifest['operation_id']}"
    for row in current_journals:
        if digest(repr(tuple(row)).encode()) in original["workflow_publication_journals"]:
            continue
        if (row["namespace"] != f"org/{manifest['org']}"
                or row["publisher"] != owner and not row["publisher"].startswith(owner + ":")):
            raise ValueError("foreign_publication_journal_after_check")
    if row_hashes(conn, "workflow_profile_operations") != original["workflow_profile_operations"]:
        raise ValueError("foreign_profile_operation_after_check")
    # Team/role demotion retains executors and their existing bindings. This
    # operation cannot repair a stale dependency mirror or rebind any peer.
    if row_hashes(conn, "workflow_profile_dependencies") != original["workflow_profile_dependencies"]:
        raise ValueError("retained_profile_dependencies_changed")
    other = sorted(digest(repr(tuple(row)).encode()) for row in conn.execute(
        "SELECT * FROM workflow_authority_pointers WHERE namespace!=?", (f"org/{manifest['org']}",)))
    if other != manifest["other_pointer_hashes"]:
        raise ValueError("foreign_authority_pointer_changed")
    verify_owned_publication(conn, manifest)
    for row in conn.execute("SELECT * FROM audit_log WHERE id>? ORDER BY id", (manifest["baseline_audit"],)):
        agent = next((agent for agent in AGENTS if row["task_id"] == f"config:THR296:{manifest['operation_id']}:{agent}"), None)
        if (agent is None or row["agent"] != "founder" or row["action"] != "thread_session_invalidated"
                or json.loads(row["payload"]) != {"reason": f"THR296 roster operation {manifest['operation_id']}",
                    "rows": len(manifest["reset_before"][agent]), "name": agent}):
            raise ValueError("foreign_audit_after_check")


def verify_materialization_events(conn: sqlite3.Connection, manifest: dict) -> None:
    baseline = manifest["materialization_baseline"]
    prior = digest(canonical([repr(tuple(row)) for row in conn.execute(
        "SELECT * FROM skill_validation_events WHERE id<=? ORDER BY id", (baseline,))]))
    if prior != manifest["materialization_prefix_signature"]:
        raise ValueError("retained_materialization_history_changed")
    shapes = manifest["materialization_event_shapes"]
    for row in conn.execute("SELECT * FROM skill_validation_events WHERE id>? ORDER BY id", (baseline,)):
        value = dict(row)
        value.pop("id")
        value.pop("created_at")
        if value not in shapes:
            raise ValueError("unowned_materialization_event_after_check")


def require_quiescence(conn: sqlite3.Connection, root: Path, org: str) -> str:
    """Inspect complete native work owners, without settling any of them."""
    from runtime.infrastructure.workflow_schema import (
        validate_workflow_schema, _validate_submission_source_ownership,
    )
    layout = validate_workflow_schema(conn, expected_org_slug=org)
    _validate_submission_source_ownership(conn, layout)
    predicates = {
        "tasks": "status NOT IN ('completed','failed','cancelled','superseded') OR active_chain IS NOT NULL OR active_fanout IS NOT NULL",
        "jobs": "status NOT IN ('completed','failed','rejected')",
        "thread_invocations": "status NOT IN ('consumed','declined','timeout','failed')",
        "dreams": "status NOT IN ('completed','failed','timeout','skipped')",
        "work_hours": "status NOT IN ('completed','failed','timeout','skipped')",
        "schedules": "active=1 OR session_id IS NOT NULL OR status NOT IN ('fired','paused','cancelled','expired','failed','timeout')",
        "task_completion_recoveries": "state IN ('claimed','callback_accepted')",
        "thread_reply_delivery_state": "queued_invocation_token IS NOT NULL OR running_invocation_token IS NOT NULL OR required_through_seq>acknowledged_through_seq",
        "thread_reply_breaker_episodes": "state='probe' OR probe_lease_id IS NOT NULL",
        "thread_reply_exchange": "state='open'",
        "thread_exchange_deferrals": "state='held' OR catchup_pending=1",
        "workflow_instances": "status NOT IN ('complete','cancelled')",
        "workflow_dispatch_outbox": "state NOT IN ('completed','cancelled')",
        "workflow_request_task_bridges": "state NOT IN ('completed','cancelled')",
        "remote_job_attempts": "state!='terminal'",
        "remote_runner_workspaces": "active_attempt_id IS NOT NULL OR state IN ('leased','recreate_pending','uncertain')",
    }
    if layout in ('E', 'G'):
        predicates['workflow_draft_dispatch_intents'] = "state NOT IN ('completed','cancelled','failed')"
    for table, predicate in predicates.items():
        if conn.execute(f'SELECT 1 FROM "{table}" WHERE {predicate} LIMIT 1').fetchone():
            raise ValueError(f"nonquiescent_{table}: reconcile the existing owner before a fresh check")
    pending = root / "org/agents/_pending"
    if pending.exists() and (pending.is_symlink() or any(pending.iterdir())):
        raise ValueError("pending_or_uninspectable_enrollment")
    return layout


def final_cas(runtime: Path, root: Path, manifest: dict) -> None:
    """Last read-only pre-reset guard; no discovery runs under mutation leases."""
    registered_runtime(runtime)
    restore_verified(manifest)
    if (image(runtime / "happyranch.yaml") != manifest["runtime_marker"]
            or any(image(Path(path)) != expected for path, expected in manifest["registry_images"].items())):
        raise ValueError("runtime_registration_before_image_CAS_lost")
    if any(image(root / rel) != expected for rel, expected in manifest["before"].items()):
        raise ValueError("apply_before_image_CAS: use explicit owned recovery for a partial prefix")
    verify_preserved_paths(root, manifest)
    verify_global_assets(manifest, initial=True)
    with read_db(root / "happyranch.db") as conn:
        require_quiescence(conn, root, manifest["org"])
        if control_signature(conn) != manifest["control_signature"]:
            raise ValueError("publication_profile_or_audit_before_image_CAS_lost")
        if domain_signature(conn) != manifest["domain_signature"]:
            raise ValueError("traffic_or_history_changed_forward_repair_required")
        if image(root / "happyranch.db") != manifest["closed_backup_image"]:
            raise ValueError("database_closed_before_image_CAS_lost")


def check(args: argparse.Namespace) -> dict:
    plan = json.loads(args.plan.read_bytes())
    runtime, root = paths(args)
    observation = containment(plan, runtime)
    source = source_identity()
    if sys.version_info[:2] != (3, 14):
        raise ValueError("effective_python314_required")
    reader = reader_binding()
    if plan.get("reader_binding") != reader:
        raise ValueError("effective_reader_binding_mismatch")
    registered_runtime(runtime)
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.org_validation import validate_team_membership
    validate_team_membership(OrgPaths(root=root), TeamsRegistry.load(root))
    for agent in AGENTS:
        workspace = root / "workspaces" / agent
        if (not workspace.is_dir() or workspace.is_symlink()
                or not (workspace / "task_history.md").is_file()
                or not (workspace / "memory/_index.md").is_file()
                or (workspace / "memory").is_symlink()
                or (workspace / "learnings").exists()):
            raise ValueError("original_canonical_workspace_history_memory_required")
    operation = plan.get("operation_id")
    if not isinstance(operation, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", operation) is None:
        raise ValueError("operation_id_required")
    operation_dir = Path(plan["operation_dir"])
    if (not operation_dir.is_absolute() or not operation_dir.is_dir()
            or operation_dir.is_symlink() or operation_dir.resolve() != operation_dir
            or operation_dir.is_relative_to(runtime) or operation_dir.is_relative_to(Path("/tmp"))
            or operation_dir.stat().st_uid != os.getuid() or stat.S_IMODE(operation_dir.stat().st_mode) != 0o700):
        raise ValueError("durable_private_external_operation_directory_required")
    restore = Path(plan["closed_restore_root"])
    if (not restore.is_absolute() or restore.is_symlink() or restore.resolve(strict=True) != restore
            or restore.is_relative_to(runtime) or restore.is_relative_to(Path("/tmp"))):
        raise ValueError("independent_closed_restore_required")
    preserved = preservation_inventory(root)
    restored_inventory = preservation_inventory(restore)
    if preserved != restored_inventory:
        raise ValueError("closed_restore_bytes_type_metadata_mismatch")
    # Native refresh can publish shared canonical packages. An org-only
    # backup cannot cover that write closure, even when workspace links look
    # unchanged. Observe the effective original store and its closed restore.
    from runtime.config import Settings
    from runtime.skills.canonical_store import _get_canonical_store_root
    store = _get_canonical_store_root(Settings(project_root=SOURCE)).resolve(strict=True)
    global_restore = Path(plan["closed_canonical_store_restore"])
    if (not global_restore.is_absolute() or global_restore.is_symlink()
            or global_restore.resolve(strict=True) != global_restore
            or global_restore.is_relative_to(runtime) or global_restore.is_relative_to(store)
            or global_restore.is_relative_to(Path("/tmp"))):
        raise ValueError("independent_closed_canonical_store_restore_required")
    global_inventory = preservation_inventory(store)
    if preservation_inventory(global_restore) != global_inventory:
        raise ValueError("canonical_store_restore_bytes_type_metadata_mismatch")
    global_after = plan.get("canonical_store_after_images")
    if not isinstance(global_after, dict):
        raise ValueError("exact_global_materializer_output_images_required")
    global_before = {}
    global_transitional = {}
    global_staging = {}
    creation_mask = native_creation_mask()
    for rel, expected in global_after.items():
        relative = Path(rel)
        parts = relative.parts
        if (not parts or relative.is_absolute() or ".." in parts
                or not (store / rel).parent.resolve().is_relative_to(store)
                or len(parts) < 3 and expected.get("kind") != "directory"
                or len(parts) >= 3 and re.fullmatch(r"[a-f0-9]{16}", parts[2]) is None
                or expected.get("kind") == "link"):
            raise ValueError("global_generated_path_outside_native_package_closure")
        validate_image(expected)
        transitional = dict(expected)
        if len(parts) >= 3 and expected["kind"] in ("file", "directory"):
            transitional["mode"] = (0o666 if expected["kind"] == "file" else 0o777) & ~creation_mask
            stage_rel = str(Path(*parts[:2], f".tmp.{parts[2][:8]}", *parts[3:]))
            if stage_rel in global_staging and global_staging[stage_rel] != transitional:
                raise ValueError("native_global_staging_identity_collision")
            if image(store / stage_rel) != {"kind": "absent"}:
                raise ValueError("preexisting_global_staging_requires_its_original_owner")
            global_staging[stage_rel] = transitional
        global_transitional[rel] = transitional
        global_before[rel] = image(store / rel)
        # Existing immutable-addressed package content is preserved; native
        # refresh may add a verified new address, never repair an old one.
        if global_before[rel]["kind"] != "absent" and global_before[rel] != expected:
            raise ValueError("existing_global_package_change_not_authorized")
        for parent in relative.parents:
            if str(parent) != "." and not (store / parent).exists() and str(parent) not in global_after:
                raise ValueError("global_generated_directory_inventory_incomplete")
    space = os.statvfs(operation_dir)
    required_bytes = sum((root / rel).stat().st_size for rel, entry in preserved.items() if entry["kind"] == "file")
    if space.f_bavail * space.f_frsize < required_bytes or space.f_favail < len(preserved):
        raise ValueError("insufficient_operation_storage_bytes_or_inodes")
    backup = Path(plan["closed_database_backup"])
    if backup != restore / "happyranch.db":
        raise ValueError("backup_must_belong_to_verified_closed_restore")
    db_path = root / "happyranch.db"
    if any((root / ("happyranch.db" + suffix)).exists() and (root / ("happyranch.db" + suffix)).stat().st_size for suffix in ("-wal", "-journal")):
        raise ValueError("database_not_closed_checkpointed")
    if backup.is_relative_to(runtime) or backup.is_symlink() or backup.read_bytes() != db_path.read_bytes():
        raise ValueError("exact_closed_backup_required")
    closed_backup_flush(backup)
    with read_db(db_path) as current, read_db(backup) as restored:
        if domain_signature(current) != domain_signature(restored):
            raise ValueError("backup_restore_history_mismatch")
        layout = require_quiescence(current, root, args.org)
        if current.execute("SELECT 1 FROM workflow_publication_journals WHERE state NOT IN ('cache_installed','aborted') LIMIT 1").fetchone():
            raise ValueError("unfinished_publication_requires_existing_owner_reconciliation")
        if current.execute("SELECT 1 FROM workflow_profile_operations WHERE state NOT IN ('published','aborted') LIMIT 1").fetchone():
            raise ValueError("unfinished_profile_operation_requires_existing_owner_reconciliation")
        if (current.execute("SELECT 1 FROM workflow_publication_leases LIMIT 1").fetchone()
                or current.execute("SELECT 1 FROM workflow_profile_leases LIMIT 1").fetchone()):
            raise ValueError("preexisting_durable_lease_requires_existing_owner_reconciliation")
        pointer = current.execute("SELECT * FROM workflow_authority_pointers WHERE namespace=?", (f"org/{args.org}",)).fetchone()
        journal = None if pointer is None else current.execute("SELECT * FROM workflow_publication_journals WHERE id=?", (pointer["journal_id"],)).fetchone()
        if (pointer is None or pointer["state"] != "ready" or journal is None
                or journal["namespace"] != f"org/{args.org}" or journal["state"] != "cache_installed"
                or journal["generation"] != pointer["current_generation"]
                or journal["snapshot_digest"] != pointer["snapshot_digest"]
                or digest(bytes(journal["snapshot_bytes"])) != pointer["snapshot_digest"]
                or (root / "org/.workflow-authority.json").read_bytes() != bytes(journal["snapshot_bytes"])):
            raise ValueError("preceding_authority_not_coherent_and_ready")
        from runtime.workflows.authority import validate_authority_snapshot
        validate_authority_snapshot(json.loads(bytes(journal["snapshot_bytes"])))
        if current.execute("""SELECT 1 FROM workflow_profile_dependencies d
                LEFT JOIN workflow_profile_store s ON s.profile_name=d.profile_name
                LEFT JOIN workflow_profile_registry r ON r.profile_name=d.profile_name
                WHERE d.state='unbound' OR d.state='active' AND
                  (s.state IS NULL OR s.state!='active' OR s.generation!=d.bound_generation
                   OR r.published_generation IS NULL OR r.published_generation!=d.bound_generation)
                LIMIT 1""").fetchone():
            raise ValueError("preexisting_profile_closure_not_ready")
        before_domain = domain_signature(current)
        before_control = control_signature(current)
        control_rows = {table: row_hashes(current, table) for table in (
            "workflow_publication_journals", "workflow_profile_operations", "workflow_profile_dependencies")}
        other_pointers = sorted(digest(repr(tuple(row)).encode()) for row in current.execute(
            "SELECT * FROM workflow_authority_pointers WHERE namespace!=?", (f"org/{args.org}",)))
        baseline_audit = current.execute("SELECT COALESCE(MAX(id),0) FROM audit_log").fetchone()[0]
        audit_prefix = audit_prefix_signature(current, baseline_audit)
        materialization_baseline = current.execute("SELECT COALESCE(MAX(id),0) FROM skill_validation_events").fetchone()[0]
        materialization_prefix = digest(canonical([repr(tuple(row)) for row in current.execute(
            "SELECT * FROM skill_validation_events ORDER BY id")]))
        event_shapes = plan.get("materialization_event_shapes")
        if not isinstance(event_shapes, list):
            raise ValueError("closed_native_materialization_event_shapes_required")
        event_fields = {row[1] for row in current.execute("PRAGMA table_info(skill_validation_events)")} - {"id", "created_at"}
        for shape in event_shapes:
            if (not isinstance(shape, dict) or set(shape) != event_fields
                    or shape["agent"] not in AGENTS or shape["source"] != "materialization"
                    or shape["severity"] != "info" or shape["ok"] != 1
                    or shape["findings"] != "[]" or shape["reason_codes"] != "[]"):
                raise ValueError("materialization_event_shape_outside_native_refresh")
        resets = {agent: [dict(row) for row in current.execute("SELECT * FROM thread_participants WHERE agent_name=? ORDER BY thread_id", (agent,))] for agent in AGENTS}
    before = {rel: image(root / rel) for rel in CANONICAL}
    after = dict(before)
    for agent in AGENTS:
        rel = f"org/agents/{agent}.md"
        text = base64.b64decode(before[rel]["bytes"]).decode()
        definition = parse_agent_text(text, expected_name=agent)
        if (definition.team != "consultant" or definition.role != ("manager" if agent == AGENTS[0] else "worker")
                or definition.executor != ("claude" if agent == AGENTS[0] else "codex")):
            raise ValueError("canonical_consultant_before_image_required")
        text, count = re.subn(r"(?m)^team: consultant$", "team: default", text, count=1)
        if count != 1:
            raise ValueError("exact_team_field_required")
        if agent == AGENTS[0]:
            text, count = re.subn(r"(?m)^role: manager$", "role: worker", text, count=1)
            sentence = plan.get("head_manager_sentence")
            if count != 1 or not isinstance(sentence, str) or text.count(sentence) != 1 or sentence not in definition.system_prompt:
                raise ValueError("exact_sole_manager_sentence_required")
            text = text.replace(sentence, ADVICE, 1)
        data = text.encode()
        after[rel] = {**before[rel], "bytes": base64.b64encode(data).decode(), "sha256": digest(data)}
    roster = yaml.safe_load(base64.b64decode(before[CANONICAL[2]]["bytes"]))
    team = roster["teams"].get("consultant")
    if team != {"manager": AGENTS[0], "workers": [AGENTS[1]]}:
        raise ValueError("exact_consultant_membership_required")
    existing = roster["teams"].get("default")
    if existing is not None and existing != {"manager": {"kind": "human", "principal": "founder"}, "workers": []}:
        raise ValueError("conflicting_Default")
    del roster["teams"]["consultant"]
    roster["teams"]["default"] = {"manager": {"kind": "human", "principal": "founder"}, "workers": list(AGENTS)}
    roster.update(default_team="default", task_default_team="engineering")
    TeamsRegistry._from_layout(roster["teams"], metadata={k: v for k, v in roster.items() if k != "teams"})
    data = roster_delta(base64.b64decode(before[CANONICAL[2]]["bytes"]).decode(), roster)
    after[CANONICAL[2]] = {**before[CANONICAL[2]], "bytes": base64.b64encode(data).decode(), "sha256": digest(data)}
    generated = plan.get("generated_after_images")
    if not isinstance(generated, dict) or not generated:
        raise ValueError("independently_inspectable_materializer_output_closure_required")
    from runtime.daemon.routes.agents import _BOOTSTRAP_OWNED_FILES
    for rel, expected in generated.items():
        validate_image(expected)
        relative = Path(rel)
        if (relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 3
                or relative.parts[:2] not in [("workspaces", agent) for agent in AGENTS]
                or not (root / relative).parent.resolve().is_relative_to(root)):
            raise ValueError("generated_path_outside_exact_consultant_closure")
        owned = "/".join(relative.parts[2:])
        directories = {".claude", ".agents", ".claude/skills", ".agents/skills"}
        skill_link = (len(relative.parts) == 5 and relative.parts[2] in (".claude", ".agents")
                      and relative.parts[3] == "skills")
        if owned in directories:
            if expected.get("kind") != "directory":
                raise ValueError("native_provider_directory_image_required")
        elif skill_link:
            if expected.get("kind") != "link":
                raise ValueError("native_skill_link_image_required")
            target = base64.b64decode(expected["bytes"], validate=True).decode()
            if Path(target).is_absolute():
                raise ValueError("native_relative_skill_link_required")
            resolved = ((root / rel).parent / target).resolve()
            if not resolved.is_relative_to(store) or len(resolved.relative_to(store).parts) != 3:
                raise ValueError("skill_link_outside_original_canonical_package")
            package = str(resolved.relative_to(store))
            if (resolved.relative_to(store).parts[0] != relative.parts[-1]
                    or re.fullmatch(r"[a-f0-9]{16}", resolved.name) is None
                    or image(resolved).get("kind") != "directory"
                       and global_after.get(package, {}).get("kind") != "directory"):
                raise ValueError("skill_link_package_not_in_closed_native_inventory")
        elif owned not in _BOOTSTRAP_OWNED_FILES:
            raise ValueError("generated_path_not_owned_by_existing_materializers")
        if owned in ("task_history.md", "recent_tasks.md", "memory/_index.md") and expected != image(root / rel):
            raise ValueError("existing_history_and_memory_must_be_preserved")
        before[rel] = image(root / rel)
        after[rel] = expected
    for rel in generated:
        for parent in Path(rel).parents:
            if (str(parent) != "." and not (root / parent).exists()
                    and after.get(str(parent), {}).get("kind") != "directory"):
                raise ValueError("generated_directory_inventory_incomplete")
    for value in (*before.values(), *after.values()):
        validate_image(value)
    for agent in AGENTS:
        regular = f"workspaces/{agent}/AGENTS.md"
        link = f"workspaces/{agent}/CLAUDE.md"
        if (after.get(regular, {}).get("kind") != "file"
                or after.get(link, {}).get("kind") != "link"
                or base64.b64decode(after[link]["bytes"]) != b"AGENTS.md"):
            raise ValueError("both_complete_native_instruction_pairs_required")
        settings_rel = f"workspaces/{agent}/.claude/settings.json"
        if settings_rel in after and before[settings_rel]["kind"] == after[settings_rel]["kind"] == "file":
            prior = json.loads(base64.b64decode(before[settings_rel]["bytes"]))
            target = json.loads(base64.b64decode(after[settings_rel]["bytes"]))
            if not set(target.get("permissions", {}).get("allow", [])).issubset(prior.get("permissions", {}).get("allow", [])):
                raise ValueError("unexpected_generated_permission_grant")
        for provider in (".claude", ".agents"):
            prefix = f"workspaces/{agent}/{provider}/skills/"
            if any(rel.startswith(prefix) and expected["kind"] == "link" and before[rel]["kind"] != "link"
                   for rel, expected in after.items()):
                raise ValueError("unexpected_generated_skill_grant")
    return dict(kind="THR296-checked-manifest-v1", operation_id=operation,
                operation_dir=str(operation_dir), runtime_root=str(runtime), org=args.org,
                source_sha=source, reader_binding=reader, plan_sha=digest(args.plan.read_bytes()), containment=plan["containment"],
                runtime_marker=image(runtime / "happyranch.yaml"),
                registry_images={value: image(Path(value)) for value in plan["containment"]["registry_paths"]},
                containment_observation=observation, before=before, after=after,
                domain_signature=before_domain, reset_before=resets, baseline_audit=baseline_audit,
                control_signature=before_control, audit_prefix_signature=audit_prefix, workflow_layout=layout,
                control_row_hashes=control_rows, other_pointer_hashes=other_pointers,
                materialization_baseline=materialization_baseline, materialization_prefix_signature=materialization_prefix,
                materialization_event_shapes=event_shapes,
                canonical_store_root=str(store), canonical_store_inventory=global_inventory,
                closed_canonical_store_restore=str(global_restore), global_before=global_before, global_after=global_after,
                global_transitional_images=global_transitional, global_staging_images=global_staging, native_creation_mask=creation_mask,
                closed_database_backup=str(backup), closed_backup_image=image(backup),
                closed_restore_root=str(restore), preservation_inventory=preserved)


def finished_state(root: Path, manifest: dict, *, direction: str) -> dict | None:
    """Read actual owned journal/rows to reconstruct a missing external receipt.

    No Database constructor, reset helper, materializer or publication is used.
    This is not host/process/reboot proof; those require separate observations.
    """
    residue = native_workspace_residue(root, manifest)
    if owned_replacement_residue(root, manifest):
        return None
    if any(not entry["backup"] or entry["observed"] != entry["complete"]
           for entry in residue.values()) or direction == "compensate" and residue:
        return None
    verify_preserved_paths(root, manifest)
    verify_global_assets(manifest)
    desired = manifest["before"] if direction == "compensate" else manifest["after"]
    if any(image(root / rel) != value for rel, value in desired.items()):
        return None
    global_desired = manifest["global_before"] if direction == "compensate" else manifest["global_after"]
    if any(image(Path(manifest["canonical_store_root"]) / rel) != value for rel, value in global_desired.items()):
        return None
    if any(image(Path(manifest["canonical_store_root"]) / rel) != {"kind": "absent"} for rel in manifest["global_staging_images"]):
        return None
    with read_db(root / "happyranch.db") as conn:
        if domain_signature(conn) != manifest["domain_signature"]:
            raise ValueError("traffic_or_history_changed_forward_repair_required")
        if audit_prefix_signature(conn, manifest["baseline_audit"]) != manifest["audit_prefix_signature"]:
            raise ValueError("retained_audit_history_changed")
        verify_materialization_events(conn, manifest)
        verify_control_history(conn, manifest)
        for agent in AGENTS:
            rows = [dict(row) for row in conn.execute("SELECT * FROM thread_participants WHERE agent_name=? ORDER BY thread_id", (agent,))]
            cleared = [{**row, "agent_session_id": None, "last_resumed_seq": 0} for row in manifest["reset_before"][agent]]
            if rows != cleared:
                return None
            audits = conn.execute("SELECT * FROM audit_log WHERE task_id=? ORDER BY id", (f"config:THR296:{manifest['operation_id']}:{agent}",)).fetchall()
            if cleared:
                expected = {"reason": f"THR296 roster operation {manifest['operation_id']}", "rows": len(cleared), "name": agent}
                if (len(audits) != 1 or audits[0]["action"] != "thread_session_invalidated"
                        or audits[0]["agent"] != "founder" or audits[0]["id"] <= manifest["baseline_audit"]
                        or json.loads(audits[0]["payload"]) != expected):
                    return None
            elif audits:
                raise ValueError("zero_row_reset_receipt_conflict")
        pointer = conn.execute("SELECT * FROM workflow_authority_pointers WHERE namespace=?", (f"org/{manifest['org']}",)).fetchone()
        if pointer is None or pointer["state"] != "ready":
            return None
        journal = conn.execute("SELECT * FROM workflow_publication_journals WHERE id=?", (pointer["journal_id"],)).fetchone()
        if (journal is None or journal["state"] != "cache_installed"
                or journal["publisher"] != f"THR296:{manifest['operation_id']}:ready:{direction}"
                or journal["generation"] != pointer["current_generation"]
                or journal["snapshot_digest"] != pointer["snapshot_digest"]):
            return None
        raw = bytes(journal["snapshot_bytes"])
        if digest(raw) != pointer["snapshot_digest"] or (root / "org/.workflow-authority.json").read_bytes() != raw:
            raise ValueError("owned_ready_snapshot_mismatch")
        from runtime.workflows.authority import validate_authority_snapshot
        validate_authority_snapshot(json.loads(raw))
        if (conn.execute("SELECT 1 FROM workflow_publication_leases LIMIT 1").fetchone()
                or conn.execute("SELECT 1 FROM workflow_profile_leases LIMIT 1").fetchone()):
            return None
        if conn.execute("SELECT 1 FROM workflow_profile_dependencies WHERE org_namespace=? AND state='unbound'", (f"org/{manifest['org']}",)).fetchone():
            raise ValueError("profile_closure_not_ready")
        return dict(operation_id=manifest["operation_id"], direction=direction,
                    generation=pointer["current_generation"], snapshot_digest=pointer["snapshot_digest"],
                    preservation_copies={rel: entry["observed"] for rel, entry in residue.items()},
                    traffic_released=False)


def write_receipt(path: Path, result: dict) -> None:
    data = canonical(result)
    durable_replace(path, {"kind": "file", "mode": 0o600, "uid": os.getuid(), "gid": os.getgid(),
                          "bytes": base64.b64encode(data).decode(), "sha256": digest(data)})


def apply(args: argparse.Namespace, manifest: dict) -> dict:
    runtime, root = paths(args)
    if manifest.get("kind") != "THR296-checked-manifest-v1" or manifest.get("source_sha") != source_identity():
        raise ValueError("real_checked_manifest_and_exact_candidate_required")
    if manifest["runtime_root"] != str(runtime) or manifest["org"] != args.org:
        raise ValueError("manifest_owner_mismatch")
    if manifest.get("reader_binding") != reader_binding():
        raise ValueError("effective_reader_binding_mismatch")
    registered_runtime(runtime)
    if containment(manifest, runtime) != manifest["containment_observation"]:
        raise ValueError("persistent_containment_observation_changed")
    if (image(runtime / "happyranch.yaml") != manifest["runtime_marker"]
            or any(image(Path(value)) != expected for value, expected in manifest["registry_images"].items())):
        raise ValueError("runtime_registration_before_image_CAS_lost")
    if native_creation_mask() != manifest["native_creation_mask"]:
        raise ValueError("native_materializer_creation_mask_changed")
    if set(manifest["before"]) != set(manifest["after"]):
        raise ValueError("manifest_output_closure_mismatch")
    for rel, value in (*manifest["before"].items(), *manifest["after"].items()):
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts or not (root / rel).parent.resolve().is_relative_to(root):
            raise ValueError("manifest_path_redirected")
        validate_image(value)
    verify_preserved_paths(root, manifest)
    verify_global_assets(manifest)
    restore_verified(manifest)
    operation_dir = Path(manifest["operation_dir"])
    if (not operation_dir.is_absolute() or operation_dir.is_symlink() or operation_dir.resolve() != operation_dir
            or operation_dir.is_relative_to(runtime) or operation_dir.is_relative_to(Path("/tmp"))
            or operation_dir.stat().st_uid != os.getuid() or stat.S_IMODE(operation_dir.stat().st_mode) != 0o700):
        raise ValueError("operation_directory_not_private")
    operation_id = manifest["operation_id"]
    if args.recover and args.operation_id != operation_id:
        raise ValueError("recovery_operation_mismatch")
    primary_receipt = operation_dir / "receipt.json"
    compensation_receipt = operation_dir / "compensation-receipt.json"
    receipt = compensation_receipt if compensation_receipt.exists() else primary_receipt
    manifest_digest = args.expected_digest
    if receipt.exists():
        result = json.loads(receipt.read_bytes())
        if (result.get("manifest_sha256") != manifest_digest
                or result.get("operation_id") != operation_id
                or result.get("direction") not in ("complete", "compensate")
                or receipt == compensation_receipt and result.get("direction") != "compensate"
                or result.get("traffic_released") is not False):
            raise ValueError("receipt_request_mismatch")
        if result["direction"] == "compensate" and not (args.recover and args.direction == "compensate"):
            raise ValueError("fresh_operation_required_after_compensation")
        if result["direction"] == "complete" and args.recover and args.direction == "compensate":
            # Preserve a successful external receipt. Its genuine original
            # journal authenticates completion even during a later owned
            # compensation prefix; the new compensation gets a separate file.
            with read_db(root / "happyranch.db") as conn:
                journal = conn.execute("SELECT * FROM workflow_publication_journals WHERE namespace=? AND publisher=? AND generation=? AND snapshot_digest=? AND state='cache_installed'",
                    (f"org/{args.org}", f"THR296:{operation_id}:ready:complete", result["generation"], result["snapshot_digest"])).fetchall()
                if len(journal) != 1 or digest(bytes(journal[0]["snapshot_bytes"])) != result["snapshot_digest"]:
                    raise ValueError("completed_receipt_history_drift")
            receipt = compensation_receipt
        else:
            desired = manifest["before"] if result["direction"] == "compensate" else manifest["after"]
            if any(image(root / rel) != target for rel, target in desired.items()):
                raise ValueError("completed_receipt_state_drift")
            actual = finished_state(root, manifest, direction=result["direction"])
            if actual is None or {**actual, "manifest_sha256": manifest_digest} != result:
                raise ValueError("completed_receipt_readiness_drift")
            return result  # no reset, materializer, publication or protected write
    desired = manifest["before"] if args.recover and args.direction == "compensate" else manifest["after"]
    compensated = finished_state(root, manifest, direction="compensate")
    if compensated is not None and not (args.recover and args.direction == "compensate"):
        raise ValueError("fresh_operation_required_after_compensation")
    for rel in manifest["before"]:
        if not known_output_prefix(root, manifest, rel):
            raise ValueError(f"unknown_third_state:{rel}")
    if image(Path(manifest["closed_database_backup"])) != manifest["closed_backup_image"]:
        raise ValueError("closed_backup_changed")
    with read_db(root / "happyranch.db") as conn:
        if domain_signature(conn) != manifest["domain_signature"]:
            raise ValueError("traffic_or_history_changed_forward_repair_required")
        if audit_prefix_signature(conn, manifest["baseline_audit"]) != manifest["audit_prefix_signature"]:
            raise ValueError("retained_audit_history_changed")
        verify_materialization_events(conn, manifest)
        verify_control_history(conn, manifest)
    direction = "compensate" if args.recover and args.direction == "compensate" else "complete"
    completed = finished_state(root, manifest, direction=direction)
    if completed is not None:
        result = {**completed, "manifest_sha256": manifest_digest}
        write_receipt(receipt, result)
        return result  # receipt-loss reconstruction performs only external write
    if not args.recover:
        # Authenticate an already completed operation before this first-apply
        # gate, so a lost external receipt does not cause another mutation.
        final_cas(runtime, root, manifest)
    else:
        # Only explicit owned recovery closes a staged prefix. Check/first
        # apply never adopt a sibling left by an unrelated earlier operation.
        close_native_workspace_residue(root, manifest, compensate=direction == "compensate")
        close_native_authority_residue(root, manifest)
        close_owned_replacement_residue(root, manifest)
    # Existing native coordinators, no startup attachment/publication shortcut.
    from runtime.config import Settings
    from runtime.infrastructure.database import Database
    from runtime.daemon.org_state import OrgState
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.context_builder import ContextBuilder
    from runtime.orchestrator.org_validation import validate_team_membership
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.orchestrator.workspace_adapters import materialize_workspace_skills_union, validate_workspace_skills_integrity
    from runtime.workflows.profile_coordinator import ProfileCoordinator, _ConsumerWriterInterval
    settings = Settings(project_root=SOURCE)
    db = Database(root / "happyranch.db")
    try:
        paths_obj = OrgPaths(root=root)
        # A partial prefix cannot attach. The coherent target registry is used
        # only after exact observed-file CAS; no startup roster reconstruction.
        roster_bytes = base64.b64decode(desired[CANONICAL[2]]["bytes"])
        layout = yaml.safe_load(roster_bytes)
        teams = TeamsRegistry._from_layout(layout["teams"], root=root,
            metadata={key: value for key, value in layout.items() if key != "teams"})
        orch = Orchestrator(db, settings, paths_obj, args.org, teams)
        org = OrgState(slug=args.org, root=root, db=db, teams=teams, settings=settings, orchestrator=orch)
        profiles = ProfileCoordinator(daemon_home=settings.daemon_home, orgs={args.org: org})
        org.workflow_authority._profile_coordinator = profiles
        if args.recover:
            # Reserved file/publication phases cannot be superseded by a new
            # writer. Reconcile the authenticated OP's native phase first.
            # Prepared canonical prefixes remain fenced and are completed by
            # the bounded writer below, after all partial-file CAS checks.
            with db._lock:
                active = db._conn.execute("SELECT state FROM workflow_publication_journals WHERE namespace=? AND state NOT IN ('cache_installed','aborted')", (f"org/{args.org}",)).fetchall()
            if active and active[0]["state"] != "prepared":
                org.workflow_authority.recover()
        # Resume reset + native audit is atomic per consultant; retain cleared
        # resumes on compensation. Actual reset state/audit owns recovery.
        for agent in AGENTS:
            scope = f"config:THR296:{operation_id}:{agent}"
            with db._lock:
                audits = db.get_audit_logs(scope)
                rows = [dict(row) for row in db._conn.execute("SELECT * FROM thread_participants WHERE agent_name=? ORDER BY thread_id", (agent,))]
                before_rows = manifest["reset_before"][agent]
                cleared = [{**row, "agent_session_id": None, "last_resumed_seq": 0} for row in before_rows]
                if audits:
                    if (len(audits) != 1 or audits[0]["action"] != "thread_session_invalidated" or rows != cleared
                            or audits[0]["agent"] != "founder" or audits[0]["id"] <= manifest["baseline_audit"]
                            or audits[0]["payload"] != {"reason": f"THR296 roster operation {operation_id}", "rows": len(cleared), "name": agent}):
                        raise ValueError("reset_ownership_or_third_state_refusal")
                    continue
                if rows != before_rows:
                    raise ValueError("reset_before_image_changed")
                if rows:
                    db.reset_thread_sessions_for_agent(agent, audit_scope_id=scope, audit_agent="founder", audit_reason=f"THR296 roster operation {operation_id}")
        async def replace_roster() -> _ConsumerWriterInterval:
            # Existing consumer_writer captures outside leases and releases
            # profile ownership before publication discovery. Both first-party
            # providers retain their existing dependency bindings.
            async with profiles.consumer_writer(org=org, publisher=f"THR296:{operation_id}",
                    consumer=AGENTS[0], preserve=True) as interval:
                async with org.teams_lock:
                    with interval.canonical_change():
                        for rel in CANONICAL:
                            current = image(root / rel)
                            if current not in (manifest["before"][rel], manifest["after"][rel]):
                                raise ValueError("final_canonical_CAS_lost")
                            if current != desired[rel]:
                                durable_replace(root / rel, desired[rel])
                        teams_now = TeamsRegistry.load(root)
                        org.teams = orch._teams = org.workflow_authority._teams = teams_now
            return interval
        interval = asyncio.run(replace_roster())
        teams = org.teams
        validate_team_membership(paths_obj, teams)
        compensating = args.recover and args.direction == "compensate"
        if compensating:
            for rel in desired:
                if rel not in CANONICAL and image(root / rel) != desired[rel]:
                    durable_replace(root / rel, desired[rel])
            restore_global_assets(manifest)
        else:
            resume_native_package_hardening(manifest)
        for agent in (() if compensating else AGENTS):
            definition = load_agent(paths_obj, agent)
            workspace = root / "workspaces" / agent
            if not workspace.is_dir() or workspace.is_symlink():
                raise ValueError("original_workspace_required")
            ContextBuilder(settings, paths_obj, slug=args.org).ensure_workspace_ready(workspace, agent, definition.system_prompt, provider=definition.executor)
            expected_specs = materialize_workspace_skills_union(workspace, settings, slug=args.org,
                contexts=["task", "thread", "wake", "dream", "schedule", "bootstrap"], provider=definition.executor,
                agent_name=agent, team=definition.team, skills_root=SOURCE / "runtime/skills", org_root=root, db=db)
            validate_workspace_skills_integrity(workspace, expected_specs=expected_specs,
                settings=settings, db=db, agent_name=agent)
        close_native_workspace_residue(root, manifest, compensate=compensating)
        for rel, expected in desired.items():
            if image(root / rel) != expected:
                raise ValueError(f"materializer_closed_output_mismatch:{rel}")
            # The unchanged native materializers use several write strategies.
            # Establish durability of their verified complete output before
            # returning readiness; no same-byte replacement is needed.
            target = root / rel
            if expected["kind"] == "file":
                with target.open("rb") as installed:
                    os.fsync(installed.fileno())
            fd = os.open(target if expected["kind"] == "directory" else target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        verify_global_assets(manifest)
        global_desired = manifest["global_before"] if compensating else manifest["global_after"]
        for rel, expected in global_desired.items():
            path = Path(manifest["canonical_store_root"]) / rel
            if image(path) != expected:
                raise ValueError(f"global_materializer_closed_output_mismatch:{rel}")
            if expected["kind"] == "file":
                with path.open("rb") as installed:
                    os.fsync(installed.fileno())
            if expected["kind"] != "absent":
                fd = os.open(path if expected["kind"] == "directory" else path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        fd = os.open(Path(manifest["canonical_store_root"]), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        profiles.reconcile_supported_roster_batch(org.workflow_authority, completed_writer_invocation=interval.publisher_invocation)
        # Publication helper false/log is never readiness.
        if not org.workflow_authority.publish_after_supported_change(publisher=f"THR296:{operation_id}:ready:{direction}"):
            raise ValueError("publication_not_ready")
        ready = org.workflow_authority.verify_admission_ready()
        final = finished_state(root, manifest, direction=direction)
        if (final is None or final["generation"] != ready.generation
                or final["snapshot_digest"] != ready.snapshot_digest):
            raise ValueError("final_owned_state_not_ready")
        result = {**final, "manifest_sha256": manifest_digest}
        write_receipt(receipt, result)
        return result
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--recover", action="store_true")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-digest")
    parser.add_argument("--operation-id")
    parser.add_argument("--direction", choices=("complete", "compensate"))
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.plan is None:
                raise ValueError("real_check_input_required")
            result = check(args)
        else:
            if args.manifest is None or args.expected_digest is None:
                raise ValueError("exact_manifest_digest_required")
            raw = args.manifest.read_bytes()
            if digest(raw) != args.expected_digest:
                raise ValueError("manifest_digest_mismatch")
            if args.recover and (args.direction is None or args.operation_id is None):
                raise ValueError("explicit_recovery_owner_and_direction_required")
            result = apply(args, json.loads(raw))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError, subprocess.SubprocessError,
            yaml.YAMLError, OrgConsistencyError, WorkflowAuthorityError, ProfileCoordinatorError) as exc:
        print(f"refused: {exc}; retain restart inhibition and resolve the named boundary", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
