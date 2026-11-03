"""Content-Disposition formatting for attachment downloads."""
from __future__ import annotations

from urllib.parse import quote


def attachment_content_disposition(name: str) -> str:
    """Keep the original UTF-8 name with a safe quoted ASCII fallback."""
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_"
        for ch in name
    )
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name, safe="")}'
