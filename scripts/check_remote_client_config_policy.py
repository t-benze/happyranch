"""Read-only shape and daemon-allowlist drift check for the app config policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

POLICY_PATH = "protocol/remote-client-config-policy.v1.json"
DAEMON_POLICY_PATH = "runtime/remote_access/route_policy.json"


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def check(root: Path) -> int:
    policy = json.loads(
        (root / POLICY_PATH).read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
    if not isinstance(policy, dict) or set(policy) != {"version", "entries"}:
        raise ValueError("policy keys must be exactly version, entries")
    if type(policy["version"]) is not int or policy["version"] != 1:
        raise ValueError("version must be integer 1")
    if not isinstance(policy["entries"], list):
        raise ValueError("entries must be an array")

    daemon = json.loads((root / DAEMON_POLICY_PATH).read_text(encoding="utf-8"))
    allowed = {(entry["method"], entry["path_template"]) for entry in daemon["allow"]}
    seen = set()
    for index, entry in enumerate(policy["entries"]):
        label = f"entries[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"method", "path_template", "class"}:
            raise ValueError(f"{label}: keys must be exactly method, path_template, class")
        if entry["class"] != "config":
            raise ValueError(f"{label}: class must be config")
        if not isinstance(entry["method"], str) or entry["method"] not in {
            "POST", "PUT", "PATCH", "DELETE",
        }:
            raise ValueError(f"{label}: method must be an uppercase write method")
        if not isinstance(entry["path_template"], str):
            raise ValueError(f"{label}: path_template must be a string")
        route = (entry["method"], entry["path_template"])
        if route in seen:
            raise ValueError(f"{label}: duplicate route {route}")
        seen.add(route)
        if route not in allowed:
            raise ValueError(f"{label}: absent from daemon allow: {route}")
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count = check(args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: cannot check remote-client config policy: {exc}", file=sys.stderr)
        return 1
    print(f"Remote-client config policy OK: closed v1 shape; {count} config entries in daemon allow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
