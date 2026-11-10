"""Static consistency checks for the single shipped remote-access policy."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

POLICY_PATH = "runtime/remote_access/route_policy.json"
R1_ROUTES = frozenset({
    ("POST", "/api/v1/executor-binaries/register"),
    ("POST", "/api/v1/runtime/custom-cli/{operation_id}/commit"),
    ("POST", "/api/v1/runtime/use"),
    ("DELETE", "/api/v1/runtime/adapters/{adapter_id}"),
    ("DELETE", "/api/v1/orgs/{slug}"),
})


def locked_class_ids(path: Path) -> set[str]:
    """Read the literal lock without importing or executing runtime code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [
        node.value for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "LOCKED_FORBIDDEN_CLASS_IDS"
                for t in node.targets)
    ]
    if len(values) != 1:
        raise ValueError("expected one LOCKED_FORBIDDEN_CLASS_IDS assignment")
    value = values[0]
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset" and len(value.args) == 1
            and not value.keywords):
        raise ValueError("LOCKED_FORBIDDEN_CLASS_IDS must be a literal frozenset")
    ids = ast.literal_eval(value.args[0])
    if not isinstance(ids, set) or not all(isinstance(i, str) for i in ids):
        raise ValueError("LOCKED_FORBIDDEN_CLASS_IDS must contain string IDs")
    return ids


def check(root: Path) -> list[str]:
    raw = (root / POLICY_PATH).read_bytes()
    policy = json.loads(raw)
    errors = []
    ids = [item["id"] for item in policy["forbidden_classes"]]
    locked = locked_class_ids(root / "runtime/remote_access/policy.py")
    if set(ids) != locked or len(ids) != len(set(ids)):
        errors.append(
            f"forbidden-class IDs drift: shipped={len(ids)}, locked={len(locked)}; "
            f"unlocked={sorted(set(ids) - locked)}, missing={sorted(locked - set(ids))}"
        )
    surfaces = {
        "allow": {(e["method"], e["path_template"]) for e in policy["allow"]},
    }
    for surface in ("sse", "websocket"):
        surfaces[surface] = {
            tuple(template.split(maxsplit=1))
            for template in policy["upgrade_semantics"][surface]["allowed_templates"]
        }
    for surface, routes in surfaces.items():
        for method, template in sorted(R1_ROUTES & routes):
            errors.append(f"R1 remote exclusion present in {surface}: {method} {template}")
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root,
    ).decode("utf-8").split("\0")
    copies = sorted({name for name in tracked
                     if Path(name).name in {"route-policy.json", "route_policy.json"}})
    if copies != [POLICY_PATH]:
        errors.append(f"expected only tracked {POLICY_PATH}; found {copies}")
    manifest = json.loads((root / "labs/tenant_isolation/manifest.json").read_text(encoding="utf-8"))
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if manifest["fixtures"].get(POLICY_PATH) != digest:
        errors.append(f"labs manifest digest mismatch for {POLICY_PATH}; expected {digest}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        errors = check(args.root)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError) as exc:
        errors = [f"cannot check remote-access policy: {exc}"]
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Remote-access policy consistency OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
