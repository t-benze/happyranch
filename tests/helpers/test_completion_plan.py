"""Plan payloads use the real JSON callback shape with a controlled CLI double."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.helpers.completion_plan import completion_prelude


@pytest.mark.parametrize("summary,decision", [
    ('{"action":"done","summary":"ok"}', {"action": "done", "summary": "ok"}),
    ('{"action":"delegate","agent":"dev_agent","prompt":"build"}',
     {"action": "delegate", "agent": "dev_agent", "prompt": "build"}),
    ("worker finished", None),
])
def test_plan_writes_bound_payload_and_single_absolute_file_callback(
    tmp_path: Path, summary: str, decision: dict | None,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    callback = bin_dir / "happyranch"
    callback.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    callback.chmod(0o700)
    script = ("set -e\ntask_id=TASK-001\nsession_id=sess-test\norg_slug=alpha\n"
              + completion_prelude()
              + "report_completion engineering_head " + shlex.quote(summary) + "\n")
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path,
        env={"PATH": str(bin_dir), "PYTHONNOUSERSITE": "1"},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    arguments = json.loads((tmp_path / "argv.json").read_text())
    assert arguments[:4] == ["report-completion", "--org", "alpha", "--from-file"]
    assert len(arguments) == 5
    payload_path = Path(arguments[4])
    assert payload_path.is_absolute() and payload_path.parent == tmp_path
    payload = json.loads(payload_path.read_text())
    assert payload["task_id"] == "TASK-001"
    assert payload["session_id"] == "sess-test"
    assert payload["agent"] == "engineering_head"
    assert payload["status"] == "completed"
    assert payload["summary"] == summary
    if decision is None:
        assert "decision" not in payload
    else:
        assert payload["decision"] == decision
