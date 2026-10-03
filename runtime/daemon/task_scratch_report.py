"""Compatibility alias: moved to runtime.orchestrator.task_scratch_report (THR-273 step 5 slice 1)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("runtime.orchestrator.task_scratch_report")
