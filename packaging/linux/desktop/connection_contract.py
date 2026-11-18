"""Packaged connection assets and closed, dependency-free v1 validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from urllib.parse import urlsplit

ORIGIN = "happyranch-shell://connection"
ENTRY = ORIGIN + "/index.html"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "font-src 'self'; connect-src 'none'; frame-src 'none'; object-src 'none'; "
       "base-uri 'none'; form-action 'none'")
MAX_ASSET = 16 * 1024 * 1024


def parse_json(raw: str, limit: int) -> Any:
    if type(raw) is not str or len(raw.encode("utf-8")) > limit:
        raise ValueError("invalid string size")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise ValueError("invalid constant")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)

    def walk(item: Any, depth: int) -> None:
        if depth > 16:
            raise ValueError("JSON depth")
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, float):
            raise ValueError("noninteger number")

    walk(value, 0)
    return value


def literal_path(value: str) -> list[str]:
    if (type(value) is not str or len(value) > 256
            or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("nonliteral resource path")
    return value.split("/")


class Assets:
    """No-follow descriptor walks; hash again at delivery, not just startup."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased asset root")
        manifest = parse_json(self.read("resource-manifest.json", 1024 * 1024).decode("utf-8"),
                              1024 * 1024)
        if (type(manifest) is not dict or set(manifest) != {"version", "entry", "resources"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["entry"] != "index.html" or type(manifest["resources"]) is not list
                or not 1 <= len(manifest["resources"]) <= 256):
            raise ValueError("invalid resource manifest")
        self.resources = {}
        total = 0
        for item in manifest["resources"]:
            if type(item) is not dict or set(item) != {"path", "bytes", "sha256"}:
                raise ValueError("invalid resource record")
            name = item["path"]
            literal_path(name)
            if (name == "resource-manifest.json" or name in self.resources
                    or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= MAX_ASSET
                    or type(item["sha256"]) is not str
                    or re.fullmatch("[0-9a-f]{64}", item["sha256"]) is None):
                raise ValueError("invalid resource record")
            total += item["bytes"]
            self.resources[name] = item
        if "index.html" not in self.resources or total > 32 * 1024 * 1024:
            raise ValueError("invalid resource budget")
        actual = set()
        for parent, directories, files in os.walk(self.root, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                mode = path.lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError("nonregular resource")
                if stat.S_ISREG(mode):
                    actual.add(path.relative_to(self.root).as_posix())
        if actual != {*self.resources, "resource-manifest.json"}:
            raise ValueError("resource inventory mismatch")
        for name in self.resources:
            self.asset(name)

    def read(self, name: str, limit: int) -> bytes:
        parts = literal_path(name)
        # Re-admit the literal root too; no ambient web-dist or cwd discovery.
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased asset root")
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise ValueError("invalid resource file")
                data = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if (len(data) != before.st_size or len(data) > limit
                        or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError("resource changed")
                return data
        finally:
            os.close(directory)

    def asset(self, name: str) -> bytes:
        record = self.resources.get(name)
        if record is None:
            raise ValueError("unknown resource")
        data = self.read(name, record["bytes"])
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("resource digest mismatch")
        return data

    def resolve(self, uri: str) -> bytes:
        # Comparing the literal URL also rejects escapes, credentials, ports,
        # query strings, fragments, doubled slashes and URL normalization tricks.
        path = urlsplit(uri).path.removeprefix("/")
        literal_path(path)
        if uri != ORIGIN + "/" + path:
            raise ValueError("unknown origin")
        return self.asset(path)


class Protocol:
    def __init__(self, path: Path) -> None:
        self.schema = parse_json(path.read_text(encoding="utf-8"), 128 * 1024)
        self.commands = self.schema["commands"]
        self.events = self.schema["events"]

    def validate(self, value: Any, schema: dict) -> None:
        if "$ref" in schema:
            self.validate(value, self.schema["$defs"][schema["$ref"].split("/")[-1]])
            return
        if "anyOf" in schema:
            for candidate in schema["anyOf"]:
                try:
                    self.validate(value, candidate)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("invalid alternative")
        if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
            raise ValueError("invalid constant")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("invalid enum")
        kind = schema.get("type")
        if kind == "object":
            if type(value) is not dict or not set(schema.get("required", ())) <= set(value):
                raise ValueError("invalid object")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False and not set(value) <= set(properties):
                raise ValueError("extra properties")
            for key, child in value.items():
                if key in properties:
                    self.validate(child, properties[key])
        elif kind == "array":
            if type(value) is not list or len(value) > schema.get("maxItems", 256):
                raise ValueError("invalid array")
            if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
                raise ValueError("duplicate array item")
            for child in value:
                self.validate(child, schema["items"])
        elif kind == "string":
            if (type(value) is not str or len(value) < schema.get("minLength", 0)
                    or len(value.encode("utf-8")) > schema.get("maxUtf8Bytes", 32768)):
                raise ValueError("invalid string")
            pattern = schema.get("pattern")
            if pattern and re.search(pattern, value) is None:
                raise ValueError("invalid string pattern")
        elif kind == "integer":
            if (type(value) is not int or value < schema.get("minimum", -9007199254740991)
                    or value > schema.get("maximum", 9007199254740991)):
                raise ValueError("invalid integer")
        elif kind == "null" and value is not None:
            raise ValueError("invalid null")
        for child in schema.get("allOf", ()):
            self.validate(value, child)

    def request(self, raw: str, *, origin: str, main_frame: bool) -> dict:
        if origin != ORIGIN or main_frame is not True:
            raise ValueError("untrusted document")
        value = parse_json(raw, 4096)
        self.validate(value, self.schema["$defs"]["request"])
        self.validate(value["args"], self.commands[value["command"]]["args"])
        return value

    def result(self, command: str, value: dict) -> dict:
        self.validate(value, self.commands[command]["result"])
        parse_json(json.dumps(value, ensure_ascii=False), 32768)
        return value

    def hello(self) -> dict:
        return self.result("bridge.hello", {
            "version": 1, "platform": "linux", "roles": ["host", "client"],
            "capabilities": [{"command": name, "availability": (
                "available" if name == "host.status" else "unavailable")}
                for name in self.commands if name != "bridge.hello"],
            "observationLimits": {"host": ["inviteState", "expiresAt"],
                                  "client": list(self.schema["$defs"]["client"]["properties"])}})

    def host(self, raw: str) -> dict:
        value = parse_json(raw, 32768)
        if (type(value) is not dict or set(value) != {"phase", "ready", "devices", "pending", "selected"}
                or value["phase"] not in ("off", "pending", "ready", "paused", "failed")
                or type(value["ready"]) is not bool or type(value["devices"]) is not list
                or len(value["devices"]) > 32):
            raise ValueError("invalid helper snapshot")

        def device(item: Any) -> dict | None:
            if item is None:
                return None
            if (type(item) is not dict or set(item) != {"label", "fingerprint", "comparison_phrase"}
                    or any(type(v) is not str or len(v.encode("utf-8")) > 4096 for v in item.values())
                    or re.fullmatch("[0-9a-f]{24}", item["fingerprint"]) is None):
                raise ValueError("invalid helper device")
            # Helper text is HTML-escaped. Decode into bounded plain text only.
            import html
            return {"label": html.unescape(item["label"]),
                    "comparisonPhrase": html.unescape(item["comparison_phrase"]) or None}

        devices = [device(item) for item in value["devices"]]
        if None in devices or value["selected"] is not None and value["selected"] not in value["devices"]:
            raise ValueError("invalid helper selection")
        return self.result("host.status", {
            "setup": value["phase"], "runtime": "available" if value["ready"] else "offline",
            "observation": "known", "devices": devices, "pending": device(value["pending"]),
            "selected": device(value["selected"]), "inviteState": "unknown", "expiresAt": None})
