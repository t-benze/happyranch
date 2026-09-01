"""Run the actual pinned e197b20 application Database reader in isolation."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys


def main() -> None:
    database_path = Path(sys.argv[1])
    source_path = Path(__file__).with_name("thread_breaker_database_e197b20.py.source")
    module_name = "runtime.infrastructure.thread_breaker_database_e197b20"
    spec = importlib.util.spec_from_loader(
        module_name, SourceFileLoader(module_name, str(source_path))
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load pinned Database source: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    database = module.Database(database_path)
    try:
        thread = database.get_thread("THR-HIST")
        messages = database.list_thread_messages("THR-HIST")
        print(json.dumps({
            "thread_type": type(thread).__name__,
            "thread": thread.model_dump(mode="json") if thread is not None else None,
            "message_types": [type(message).__name__ for message in messages],
            "messages": [message.model_dump(mode="json") for message in messages],
        }, sort_keys=True))
    finally:
        database.close()


if __name__ == "__main__":
    main()
