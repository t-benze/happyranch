"""Deterministic multi-process trust-state race worker (TASK-6045).

Runs a REAL separate OS process (subprocess) that performs ONE Supported-DIY
ceremony operation against a REAL file-backed trust-state store shared with
a sibling worker, with a synchronization seam that parks the worker between
load and publication so the test can force the exact interleaving:

- on the pre-fix code path the seam wraps ``AtomicFileTrustStateStore.save``
  (the ceremony's load->check->save sequence);
- on the post-fix code path the ceremony runs through the store's
  ``transaction()`` and the seam wraps ``AtomicFileTrustStateStore._write_pair``
  (the snapshot+anchor publication), so the worker parks INSIDE the
  inter-process transaction lock — its sibling is provably blocked on the
  flock while this worker holds it.

The worker signals readiness by writing ``<ready_path>`` and then waits for
``<release_path>`` before publishing. The result of the ceremony operation
is written as JSON to ``<result_path>``; the worker exits 0 on success and
nonzero on harness failure (the test asserts the OPERATION's result, so an
exception inside the operation is reported in the result JSON, not as a
harness crash).

This is a TEST HARNESS script (mirrors ``diy_client.py``): it renders no
credential material beyond the operation's own return value, and that value
is captured only in the test's result file.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The checkout root (tests/remote_access/<this file> -> parents[2]); the
# editable install in the venv also resolves it, but an explicit entry keeps
# the harness robust when run outside ``uv``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.remote_access.authorization import TrustState  # noqa: E402
from runtime.remote_access.identity import ConnectorIdentity  # noqa: E402
from runtime.remote_access.pairing import PairingManager  # noqa: E402
from runtime.remote_access.state_store import AtomicFileTrustStateStore  # noqa: E402

DEFAULT_IDENTITY = ConnectorIdentity(
    tenant_id="diy", home_id="home-a", connector_id="connector-a"
)


def _signal(path: str) -> None:
    Path(path).write_text("ready", encoding="utf-8")


def _wait_for(path: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for release marker {path}")


def _install_seam(store, ready_path: str, release_path: str) -> None:
    """Park between load and publication. On the post-fix code path the
    ceremony publishes through ``_write_pair`` inside the inter-process
    transaction lock; on the pre-fix path it saves through ``save``."""
    write_pair = getattr(store, "_write_pair", None)
    if write_pair is not None:
        orig = store._write_pair

        def parked_write(state, generation):  # noqa: ANN001
            _signal(ready_path)
            _wait_for(release_path)
            return orig(state, generation)

        store._write_pair = parked_write  # type: ignore[method-assign]
        return
    orig_save = store.save

    def parked_save(state):  # noqa: ANN001
        _signal(ready_path)
        _wait_for(release_path)
        return orig_save(state)

    store.save = parked_save  # type: ignore[method-assign]


def _default_state() -> TrustState:
    return TrustState(connector_identity=DEFAULT_IDENTITY, pairing_epoch=0, revocation_epoch=0)


def main(spec_json: str) -> int:
    spec = json.loads(spec_json)
    store = AtomicFileTrustStateStore(
        Path(spec["state_path"]),
        _default_state(),
    )
    manager = PairingManager(
        state_store=store,
        identity=DEFAULT_IDENTITY,
        now_fn=lambda: datetime.now(timezone.utc),
    )
    _install_seam(store, spec["ready_path"], spec["release_path"])
    try:
        operation = spec["operation"]
        if operation == "redeem":
            credential = manager.redeem_pairing(spec["code"])
            result = {"credential": credential}
        elif operation == "issue":
            issued = manager.issue_pairing_code(spec["device_name"])
            result = {"code": issued.code}
        elif operation == "revoke_device":
            outcome = manager.revoke(device_id=spec["device_name"])
            result = {"epoch": outcome.epoch}
        elif operation == "remove_device":
            manager.remove_device(spec["device_name"])
            result = {"removed": spec["device_name"]}
        else:  # pragma: no cover - harness contract
            result = {"error": f"unknown operation {operation}"}
    except Exception as exc:  # noqa: BLE001 - harness reports the outcome
        result = {"error": f"{type(exc).__name__}: {exc}"}
    Path(spec["result_path"]).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
