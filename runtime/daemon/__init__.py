"""HappyRanch orchestrator daemon: FastAPI app, runner, event bus."""

from __future__ import annotations

import time

# The package is entered before __main__ (also under python -m), so these
# observations precede heavy imports and survive __main__'s canonical reimport.
try:
    _PROCESS_START_EPOCH = time.time()
    _PROCESS_START_MONOTONIC = time.monotonic()
except BaseException:
    _PROCESS_START_EPOCH = None
    _PROCESS_START_MONOTONIC = None
