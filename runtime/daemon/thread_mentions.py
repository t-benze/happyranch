"""Compatibility alias: moved to runtime.infrastructure.thread_mentions (THR-273 step 5 slice 1)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("runtime.infrastructure.thread_mentions")
