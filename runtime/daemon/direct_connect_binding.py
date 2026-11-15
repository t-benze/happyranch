"""Shared, compensating adapter replacement for direct connect and retry."""
from __future__ import annotations

from typing import Literal

from runtime.orchestrator import custom_adapter_registry
from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    acquire_store_lock,
    get_adapter,
    release_store_lock,
    remove_adapter,
    save_adapter,
)


def bind_direct_connect_adapter(
    entry: AdapterEntry,
    *,
    operation_id: str,
    via: Literal["direct_connect", "direct_connect_retry"],
) -> dict:
    """Bind under the caller's profile lease, restoring the entry on failure."""
    acquire_store_lock()
    try:
        previous = get_adapter(entry.id)
        replaced = previous is not None and (
            previous.executable_hash != entry.executable_hash
            or previous.dependency_manifest_version != entry.dependency_manifest_version
            or {(d["executable"], d["sha256"]) for d in previous.dependencies}
            != {(d["executable"], d["sha256"]) for d in entry.dependencies}
        )
        if previous is None or replaced:
            save_adapter(entry)
        update_payload = None
        if replaced:
            update_payload = {
                "adapter_id": entry.id,
                "operation_id": operation_id,
                "before_wrapper_sha256": previous.executable_hash,
                "after_wrapper_sha256": entry.executable_hash,
                "before_dependencies": [
                    {"executable": d["executable"], "sha256": d["sha256"]}
                    for d in previous.dependencies
                ],
                "after_dependencies": [
                    {"executable": d["executable"], "sha256": d["sha256"]}
                    for d in entry.dependencies
                ],
                "via": via,
            }
        try:
            return custom_adapter_registry._perform_adapter_profile_binding(
                adapter_id=entry.id,
                profile_name=entry.intended_profile_name,
                workspace_adapter=entry.workspace_adapter,
                adapter_update=update_payload,
            )
        except Exception:
            if previous is None:
                remove_adapter(entry.id)
            elif replaced:
                save_adapter(previous)
            raise
    finally:
        release_store_lock()
