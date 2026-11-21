"""Runtime-managed skill policy v1.

This module provides the foundational skill policy system for HappyRanch:
- SkillRegistry: loads runtime/skills/<slug>/skill.yaml catalog metadata;
  release instruction packages live in runtime/skills/bundled/<slug>/
- EligibilityResolver: additive inheritance with explicit deny
- Two-gated exposure: catalog gate + eligibility gate
- SystemContract: mandatory operating-contract definitions + context predicates
- SessionContext: session type enum (TASK, THREAD, WAKE, DREAM)

Skills are permission-INERT — they do not grant tools, credentials, or
capabilities. System/contract skills are outside this toggleable surface.
"""
