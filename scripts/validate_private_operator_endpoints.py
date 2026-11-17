"""Bounded synthetic validation of the private-endpoint lint CLI, without pytest."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts/check_private_operator_endpoints.py"
SYNTHETIC = (
    "operator-fixture-east", "203.0.113.42", "203-0-113-42",
    "203-0-113-42.sslip.io",
)
FAILURES: list[str] = []


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=30)


def expect(label: str, command: list[str], root: Path, code: int, diagnostics: str = "") -> None:
    result = run(command, root)
    if result.returncode != code or result.stderr != diagnostics:
        # Deliberately omit child output: a broken checker could echo a token.
        FAILURES.append(f"{label}: expected exit {code} and redacted diagnostics; "
                        f"got exit {result.returncode}")
    elif code == 0 and result.stdout != "Private operator endpoint lint PASS\n":
        FAILURES.append(f"{label}: missing success diagnostic")
    elif code != 0 and result.stdout:
        FAILURES.append(f"{label}: unexpected stdout")
    else:
        print(f"{label}: exit {code} (expected)")


def configure(checker: Path, digests: set[str]) -> None:
    # Test-side isolation changes only the copied digest constant. No production
    # flag, import or configurable bypass is added for synthetic validation.
    source = CHECKER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "FORBIDDEN_SHA256"
                              for target in node.targets))
    lines = source.splitlines(keepends=True)
    lines[assignment.lineno - 1:assignment.end_lineno] = [
        f"FORBIDDEN_SHA256 = frozenset({sorted(digests)!r})\n"
    ]
    checker.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="private-endpoint-validation-") as temp:
        root = Path(temp)
        result = run(["git", "init", "--quiet"], root)
        if result.returncode:
            raise AssertionError("synthetic repository initialization failed")
        checker = root / "checker.py"
        command = [sys.executable, str(checker), "--root", str(root)]
        probe = root / "probe.md"
        # Punctuation, mixed case, URLs/ports and an IPv4 embedded in a hostname
        # exercise tokenization; expected file/line diagnostics are independent.
        probe.write_text("\n".join((
            SYNTHETIC[0], f"'{SYNTHETIC[1]}',", f"({SYNTHETIC[2]})",
            f"https://{SYNTHETIC[3].upper()}:8443/", SYNTHETIC[0].upper(),
            f"node.{SYNTHETIC[1]}.example.invalid",
        )) + "\n", encoding="utf-8")
        result = run(["git", "add", "probe.md"], root)
        if result.returncode:
            raise AssertionError("synthetic repository staging failed")
        configure(checker, set())
        expect("disabled guard admits synthetic leak (negative control)", command, root, 0)
        configure(checker, {hashlib.sha256(token.encode()).hexdigest() for token in SYNTHETIC})
        diagnostics = "".join(f"probe.md:{number}: private operator endpoint\n"
                              for number in range(1, 7))
        expect("tracked token forms and redacted diagnostics", command, root, 1, diagnostics)

        named = root / f"{SYNTHETIC[0]}.md"
        named.write_text(SYNTHETIC[1] + "\n", encoding="utf-8")
        result = run(["git", "add", named.name], root)
        if result.returncode:
            raise AssertionError("synthetic named-file staging failed")
        expect("endpoint-bearing diagnostic path is redacted", command, root, 1,
               "<redacted-path>:1: private operator endpoint\n" + diagnostics)
        result = run(["git", "rm", "--cached", "--quiet", named.name], root)
        if result.returncode:
            raise AssertionError("synthetic named-file cleanup failed")
        named.unlink()

        # The index still contains the leak; lint must inspect current text.
        probe.write_text("the operator test server\nhttps://<TEST_CONTROL_HOST>:8443\n"
                         "203.0.113.43\noperator-fixture\n", encoding="utf-8")
        (root / "untracked.md").write_text(SYNTHETIC[0], encoding="utf-8")
        (root / "binary.dat").write_bytes(b"\0" + SYNTHETIC[1].encode())
        (root / "link.md").symlink_to("untracked.md")
        result = run(["git", "add", "binary.dat", "link.md"], root)
        if result.returncode:
            raise AssertionError("synthetic binary/link staging failed")
        expect("working text, unrelated tokens, untracked/binary/link exclusions", command, root, 0)
        probe.write_text("clean\n" + SYNTHETIC[3] + "\n", encoding="utf-8")
        expect("tracked working-tree edits are checked", command, root, 1,
               "probe.md:2: private operator endpoint\n")
        probe.unlink()
        expect("missing tracked content refuses incomplete scan", command, root, 2,
               "Private operator endpoint lint: scan incomplete\n")
        probe.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
        expect("oversized tracked content refuses incomplete scan", command, root, 2,
               "Private operator endpoint lint: scan incomplete\n")
        probe.write_text("restored clean synthetic baseline\n", encoding="utf-8")
        expect("restored clean synthetic baseline", command, root, 0)
    if FAILURES:
        raise AssertionError("\n".join(FAILURES))
    print("Private operator endpoint CLI synthetic validation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
