"""Runtime-managed skill policy v1.

This module provides the foundational skill policy system for HappyRanch:
- SkillRegistry: loads skill packages from runtime/skills/<slug>/
- EligibilityResolver: additive inheritance with explicit deny
- Two-gated exposure: catalog gate + eligibility gate

Skills are permission-INERT — they do not grant tools, credentials, or
capabilities. System/contract skills are outside this toggleable surface.
"""
