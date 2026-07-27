"""THR-055: Agent-assisted custom-skill lifecycle pilot.

Lifecycle state machine: proposed -> draft -> validation -> review ->
approved -> published -> assigned -> effective -> rolled_back/retired.

This module provides:
- models.py: Pydantic v2 data models for the lifecycle ledger
- service.py: SkillLifecycleService — the single writer for all lifecycle transitions
- stores.py: SQLite-backed package/version/event/assignment stores
"""
