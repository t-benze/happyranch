"""Static SPA mount + SPA fallback.

In production the daemon serves the React build from ``web/dist/``. The mount
location is resolvable in two ways, in order:

1. ``OPC_WEB_DIST`` environment variable (used by tests).
2. ``<repo>/web/dist`` relative to this source file.

If neither resolves, the daemon renders a small placeholder telling the founder
to run ``scripts/build_web.sh``.

Routing model: API routes are registered first (via ``include_router``); this
module registers its handlers last so they only run for paths the API didn't
match. ``SPAStaticFiles`` falls back to ``index.html`` on 404 so React Router
can own all non-API paths.

We avoid bare ``@app.get("/{full_path:path}")`` catch-alls because they would
suppress FastAPI's slash-redirect logic: ``POST /foo/`` against a registered
``POST /foo`` would return 405 instead of 307 once a catch-all GET matches the
path.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles


_PLACEHOLDER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>OPC — web UI not built</title>
<style>body{font-family:system-ui;background:#0b0d10;color:#e6e6e6;padding:3rem;max-width:42rem;margin:0 auto;line-height:1.5}
code{background:#1a1d22;padding:.15rem .4rem;border-radius:.25rem}</style></head>
<body><h1>OPC web UI is not built</h1>
<p>Run the following from the repo root, then refresh:</p>
<pre><code>scripts/build_web.sh</code></pre>
<p>Or, for development, run <code>npm run dev</code> in the <code>web/</code> directory.</p>
</body></html>
"""


class _SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to ``index.html`` on 404, except for API paths.

    Unknown ``/api/*`` paths must surface as real 404s so the TS client can
    detect mistyped endpoints. Everything else falls back to ``index.html`` so
    React Router can own the path.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


def _resolve_dist_dir() -> Path | None:
    override = os.environ.get("OPC_WEB_DIST")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    # ``src/daemon/routes/web_static.py`` → parents[3] is repo root.
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "web" / "dist"
    return candidate if candidate.is_dir() else None


def register(app: FastAPI) -> None:
    """Attach SPA static mount or placeholder to the FastAPI app.

    Call this AFTER all API routers are registered.
    """
    dist = _resolve_dist_dir()
    if dist is not None:
        app.mount("/", _SPAStaticFiles(directory=str(dist), html=True), name="spa")
    else:
        # No build present — placeholder at / only. Hard-refresh on a SPA route
        # would 404; that's OK because in this mode the founder hasn't built
        # the UI yet and we want them to see the build instructions.
        @app.get("/", include_in_schema=False)
        def _placeholder_root() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER_HTML)
