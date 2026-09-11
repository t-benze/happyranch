#!/usr/bin/env python3
"""Submit, reattach to, collect from, or cancel an existing Jenkins job safely.

This is deliberately standalone: Python 3.12+ standard library only, with no
HappyRanch import or runtime integration.  See docs/jenkins-jobs.md.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = "1"
DEFAULT_TIMEOUT = 15.0
DEFAULT_DEADLINE = 900.0
DEFAULT_POLL = 2.0
DEFAULT_API_BYTES = 1_000_000
DEFAULT_LOG_BYTES = 1_000_000
DEFAULT_ARTIFACT_BYTES = 10_000_000
DEFAULT_ARTIFACT_TOTAL = 50_000_000
DEFAULT_ARTIFACT_COUNT = 32
SUPPORTED_PARAMETERS = {
    "hudson.model.StringParameterDefinition", "hudson.model.TextParameterDefinition",
    "hudson.model.BooleanParameterDefinition", "hudson.model.ChoiceParameterDefinition",
    "hudson.model.PasswordParameterDefinition", "StringParameterDefinition",
    "TextParameterDefinition", "BooleanParameterDefinition", "ChoiceParameterDefinition",
}


class JenkinsError(Exception): pass
class ValidationError(JenkinsError): pass
class ReceiptError(JenkinsError): pass
class TransportError(JenkinsError): pass


def _bad_component(value: str) -> bool:
    lower = value.lower()
    return not value or value in {".", ".."} or "%2f" in lower or "%5c" in lower or "\\" in value or "/" in value


def job_path(name: str) -> str:
    parts = name.split("/")
    if not parts or any(_bad_component(part) for part in parts):
        raise ValidationError("job name must be nonempty folder/name components without traversal or encoded separators")
    return "".join("/job/" + quote(part, safe="") for part in parts)


def validate_controller(url: str, allow_http: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}) or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError("controller must be a credential-free HTTPS origin (HTTP requires explicit --allow-http)")
    if any(piece in {".", ".."} or "%2f" in piece.lower() or "%5c" in piece.lower() for piece in parsed.path.split("/")):
        raise ValidationError("controller context must not contain traversal or encoded separators")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def controller_url(controller: str, name: str) -> str:
    return validate_controller(controller, allow_http=True) + job_path(name)


@dataclass(frozen=True)
class Controller:
    base: str
    def __post_init__(self) -> None: object.__setattr__(self, "base", validate_controller(self.base, allow_http=True))
    @property
    def parsed(self) -> Any: return urlsplit(self.base)
    def checked(self, url: str, expected_path: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != self.parsed.scheme or parsed.netloc != self.parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValidationError("Jenkins returned an off-origin or credential-bearing URL")
        root = self.parsed.path.rstrip("/")
        path = parsed.path
        if not path.startswith(root + "/") or not path[len(root):].startswith(expected_path):
            raise ValidationError("Jenkins returned a URL outside expected controller context or identity")
        if any(x in {".", ".."} or "%2f" in x.lower() or "%5c" in x.lower() for x in path.split("/")):
            raise ValidationError("Jenkins returned unsafe path")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None: return None


class Client:
    def __init__(self, controller: Controller, token: str | None, timeout: float, max_bytes: int) -> None:
        self.controller, self.token, self.timeout, self.max_bytes = controller, token, timeout, max_bytes
        self.opener = build_opener(NoRedirect)
    def request(self, method: str, url: str, data: bytes | None = None, limit: int | None = None) -> tuple[int, dict[str, str], bytes]:
        headers = {"Accept": "application/json"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                return exc.code, dict(exc.headers.items()), b""
            return exc.code, dict(exc.headers.items()), exc.read(min(limit or self.max_bytes, self.max_bytes))
        except (URLError, OSError, TimeoutError) as exc:
            raise TransportError("transport failed without a safe retry guarantee") from exc
        cap = min(limit or self.max_bytes, self.max_bytes)
        chunks: list[bytes] = []; size = 0
        with response:
            while True:
                part = response.read(min(65536, cap - size + 1))
                if not part: break
                chunks.append(part); size += len(part)
                if size > cap: raise TransportError("response exceeded configured bound")
        return response.status, dict(response.headers.items()), b"".join(chunks)
    def api(self, url: str) -> dict[str, Any]:
        status, _, body = self.request("GET", url)
        if status != 200: raise TransportError(f"Jenkins API returned HTTP {status}")
        try: value = json.loads(body)
        except json.JSONDecodeError as exc: raise TransportError("Jenkins API returned invalid JSON") from exc
        if not isinstance(value, dict): raise TransportError("Jenkins API response was not an object")
        return value


def prepare_parameters(metadata: dict[str, Any], supplied: dict[str, str]) -> dict[str, str]:
    definitions: list[dict[str, Any]] = []
    for prop in metadata.get("property", []) or []:
        definitions.extend(prop.get("parameterDefinitions", []) or [])
    allowed: dict[str, str] = {}
    for definition in definitions:
        name, kind = definition.get("name"), definition.get("type")
        if not isinstance(name, str) or not isinstance(kind, str): raise ValidationError("malformed Jenkins parameter definition")
        if kind not in SUPPORTED_PARAMETERS: raise ValidationError(f"unsupported declared Jenkins parameter type: {kind}")
        allowed[name] = kind
    unknown = set(supplied) - set(allowed)
    if unknown: raise ValidationError("parameters are not declared by this job: " + ", ".join(sorted(unknown)))
    return dict(supplied)


def _safe_receipt_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink(): raise ReceiptError("receipt path may not be a symlink")
    if path.parent.is_symlink(): raise ReceiptError("receipt directory may not be a symlink")


def _write_json(path: Path, data: dict[str, Any], create: bool = False) -> None:
    _safe_receipt_path(path)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if create else os.O_TRUNC)
    try: fd = os.open(path, flags, 0o600)
    except FileExistsError as exc: raise ReceiptError("receipt already exists; reattach rather than submit") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    except BaseException:
        try: os.close(fd)
        except OSError: pass
        raise


def open_receipt(path: Path, controller: str, name: str) -> dict[str, Any]:
    data = {"version": VERSION, "request_id": secrets.token_hex(16), "controller": validate_controller(controller, allow_http=True), "job": name, "phase": "intent", "submitted_at": time.time(), "queue_url": None, "build_url": None, "jenkins_result": None, "collection": {"status": "not_started", "artifacts": []}}
    _write_json(path, data, create=True); return data


def load_receipt(path: Path) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ReceiptError("receipt is unreadable") from exc
    if not isinstance(value, dict) or value.get("version") != VERSION: raise ReceiptError("unsupported receipt")
    return value


def save_receipt(path: Path, receipt: dict[str, Any]) -> None: _write_json(path, receipt)


def outcome(build: dict[str, Any]) -> str:
    if build.get("building") is True: return "BUILDING"
    result = build.get("result")
    if result in {"SUCCESS", "FAILURE", "UNSTABLE", "ABORTED"}: return str(result)
    return "UNSUPPORTED_RESULT:" + (str(result) if result is not None else "missing")


def submit(client: Client, receipt_path: Path, job: str, params: dict[str, str]) -> dict[str, Any]:
    receipt = open_receipt(receipt_path, client.controller.base, job)
    job_url = controller_url(client.controller.base, job)
    metadata = client.api(job_url + "/api/json")
    declared = prepare_parameters(metadata, params)
    endpoint = "/buildWithParameters" if any((p.get("parameterDefinitions") or []) for p in metadata.get("property", []) or []) else "/build"
    body = urlencode(declared).encode() if endpoint.endswith("Parameters") else None
    status, headers, _ = client.request("POST", job_url + endpoint, body)
    location = headers.get("Location") or headers.get("location")
    if status not in {200, 201, 202} or not location:
        receipt["phase"] = "submission_uncertain"; receipt["submission_status"] = status; save_receipt(receipt_path, receipt)
        raise ReceiptError("submission outcome is uncertain; do not submit again; reconcile this receipt")
    receipt["queue_url"] = client.controller.checked(urljoin(job_url, location), "/queue/item/")
    receipt["phase"] = "queued"; save_receipt(receipt_path, receipt); return receipt


def _deadline(deadline: float, start: float) -> None:
    if time.monotonic() - start >= deadline: raise TimeoutError


def wait_for_terminal(client: Client, receipt_path: Path, deadline: float, poll: float) -> dict[str, Any]:
    receipt = load_receipt(receipt_path); start = time.monotonic()
    try:
        while True:
            _deadline(deadline, start)
            if receipt.get("build_url"):
                build_url = client.controller.checked(str(receipt["build_url"]), job_path(str(receipt["job"])) + "/")
                build = client.api(build_url.rstrip("/") + "/api/json")
                state = outcome(build)
                if state != "BUILDING":
                    receipt["jenkins_result"] = state; receipt["phase"] = "terminal"; save_receipt(receipt_path, receipt); return receipt
            elif receipt.get("queue_url"):
                queue_url = client.controller.checked(str(receipt["queue_url"]), "/queue/item/")
                try: queue = client.api(queue_url.rstrip("/") + "/api/json")
                except TransportError: raise
                if queue.get("cancelled"):
                    receipt["jenkins_result"] = "QUEUE_CANCELLED"; receipt["phase"] = "terminal"; save_receipt(receipt_path, receipt); return receipt
                executable = queue.get("executable")
                if isinstance(executable, dict) and isinstance(executable.get("url"), str):
                    receipt["build_url"] = client.controller.checked(executable["url"], job_path(str(receipt["job"])) + "/"); receipt["phase"] = "building"; save_receipt(receipt_path, receipt)
            time.sleep(poll)
    except TimeoutError:
        receipt["collection"]["status"] = "timeout"; save_receipt(receipt_path, receipt); raise ReceiptError("operation deadline reached; identity retained; no remote cancellation was sent")
    except (TransportError, ValidationError) as exc:
        receipt["collection"]["status"] = "transport_unknown"; save_receipt(receipt_path, receipt); raise ReceiptError("transport/identity failure; identity retained") from exc


def cancel(client: Client, receipt_path: Path) -> dict[str, Any]:
    receipt = load_receipt(receipt_path)
    target = receipt.get("build_url") or receipt.get("queue_url")
    if not target: raise ReceiptError("no recorded Jenkins identity to cancel")
    expected = job_path(str(receipt["job"])) + "/" if receipt.get("build_url") else "/queue/item/"
    target = client.controller.checked(str(target), expected)
    endpoint = target.rstrip("/") + ("/stop" if receipt.get("build_url") else "/cancelQueue")
    status, _, _ = client.request("POST", endpoint)
    receipt["cancel_requested_status"] = status; receipt["phase"] = "cancel_requested"; save_receipt(receipt_path, receipt)
    if status not in {200, 201, 202, 302}: raise ReceiptError("Jenkins did not accept cancellation; receipt retained")
    return receipt


def _output_file(root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValidationError("artifact path is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink(): raise ValidationError("output directory may not be a symlink")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink() or target.is_symlink(): raise ValidationError("artifact output may not traverse symlinks")
    return target


def collect(client: Client, receipt_path: Path, output: Path, log_bytes: int, per_file: int, total_bytes: int, count: int) -> dict[str, Any]:
    receipt = load_receipt(receipt_path)
    if not receipt.get("build_url") or not receipt.get("jenkins_result"):
        raise ReceiptError("collect requires a recorded terminal exact build")
    build_url = client.controller.checked(str(receipt["build_url"]), job_path(str(receipt["job"])) + "/")
    build = client.api(build_url.rstrip("/") + "/api/json")
    manifest: list[dict[str, Any]] = []
    log_status, _, log = client.request("GET", build_url.rstrip("/") + "/consoleText", limit=log_bytes)
    log_target = _output_file(output, "console.log")
    if log_status == 200: log_target.write_bytes(log); manifest.append({"path": "console.log", "status": "downloaded", "bytes": len(log)})
    else: manifest.append({"path": "console.log", "status": "error", "http_status": log_status})
    used = 0
    artifacts = build.get("artifacts", [])
    if not isinstance(artifacts, list): raise TransportError("malformed artifacts list")
    for artifact in artifacts[:count]:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("relativePath"), str):
            raise TransportError("malformed artifact metadata")
        relative = artifact["relativePath"]
        if used >= total_bytes: manifest.append({"path": relative, "status": "skipped", "reason": "aggregate_limit"}); continue
        url = client.controller.checked(build_url.rstrip("/") + "/artifact/" + "/".join(quote(p, safe="") for p in relative.split("/")), job_path(str(receipt["job"])) + "/")
        try:
            status, _, data = client.request("GET", url, limit=min(per_file, total_bytes - used))
            if status != 200: manifest.append({"path": relative, "status": "error", "http_status": status}); continue
            target = _output_file(output, relative); target.write_bytes(data); used += len(data); manifest.append({"path": relative, "status": "downloaded", "bytes": len(data)})
        except (JenkinsError, OSError): manifest.append({"path": relative, "status": "error"})
    for artifact in artifacts[count:]:
        if isinstance(artifact, dict): manifest.append({"path": artifact.get("relativePath", "unknown"), "status": "skipped", "reason": "count_limit"})
    receipt["collection"] = {"status": "complete", "artifacts": manifest}; save_receipt(receipt_path, receipt); return receipt


def parse_parameters(values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in values:
        if "=" not in item: raise ValidationError("--parameter needs NAME=VALUE")
        key, value = item.split("=", 1)
        if not key or key in output: raise ValidationError("parameter names must be unique and nonempty")
        output[key] = value
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True); parser.add_argument("--job", required=True); parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--token-env", default="JENKINS_API_TOKEN"); parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT); parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE); parser.add_argument("--poll", type=float, default=DEFAULT_POLL); parser.add_argument("--api-bytes", type=int, default=DEFAULT_API_BYTES)
    sub = parser.add_subparsers(dest="command", required=True)
    submit_p = sub.add_parser("submit"); submit_p.add_argument("--parameter", action="append", default=[]); sub.add_parser("wait"); sub.add_parser("cancel"); sub.add_parser("show")
    collect_p = sub.add_parser("collect"); collect_p.add_argument("--output", type=Path, required=True); collect_p.add_argument("--log-bytes", type=int, default=DEFAULT_LOG_BYTES); collect_p.add_argument("--artifact-bytes", type=int, default=DEFAULT_ARTIFACT_BYTES); collect_p.add_argument("--artifact-total-bytes", type=int, default=DEFAULT_ARTIFACT_TOTAL); collect_p.add_argument("--artifact-count", type=int, default=DEFAULT_ARTIFACT_COUNT)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.deadline <= 0 or args.poll <= 0 or args.api_bytes <= 0: parser.error("all bounds must be positive")
    try:
        controller = Controller(validate_controller(args.controller, args.allow_http)); client = Client(controller, os.environ.get(args.token_env), args.timeout, args.api_bytes)
        if args.command == "submit": receipt = submit(client, args.receipt, args.job, parse_parameters(args.parameter))
        elif args.command == "wait": receipt = wait_for_terminal(client, args.receipt, args.deadline, args.poll)
        elif args.command == "cancel": receipt = cancel(client, args.receipt)
        elif args.command == "collect":
            if min(args.log_bytes, args.artifact_bytes, args.artifact_total_bytes, args.artifact_count) <= 0: parser.error("collection bounds must be positive")
            receipt = collect(client, args.receipt, args.output, args.log_bytes, args.artifact_bytes, args.artifact_total_bytes, args.artifact_count)
        else: receipt = load_receipt(args.receipt)
        print(json.dumps({"request_id": receipt["request_id"], "phase": receipt["phase"], "jenkins_result": receipt.get("jenkins_result"), "receipt": str(args.receipt)}))
        return 0 if receipt.get("jenkins_result") == "SUCCESS" or args.command in {"submit", "show", "cancel"} else 2
    except (JenkinsError, ValidationError) as exc:
        print(f"jenkins-jobs: {exc}", file=sys.stderr); return 3


if __name__ == "__main__":
    raise SystemExit(main())
