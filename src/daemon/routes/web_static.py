"""Static SPA mount + SPA fallback.

In production the daemon serves the React build from ``web/dist/``. The mount
location is resolvable in two ways, in order:

1. ``OPC_WEB_DIST`` environment variable (used by tests).
2. ``<repo>/web/dist`` relative to this source file.

If neither resolves, the daemon renders a small placeholder telling the founder
to run ``scripts/build_web.sh``.

Routing model:

- ``/assets/*`` → ``StaticFiles`` (real asset bytes).
- ``GET /`` → ``index.html``.
- Any other ``GET`` that lands at the 404 exception handler with a non-``/api``
  path → falls back to ``index.html`` so React Router can own the route.

We deliberately do NOT mount ``StaticFiles`` at ``/``. Mounting at ``/`` would
intercept ``POST /api/v1/.../`` (the trailing-slash variant) and return 405
instead of letting FastAPI's slash-redirect emit a 307 to ``/api/v1/...``.
The exception-handler approach only fires when the request actually 404s,
preserving normal FastAPI routing semantics for every method on every path.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


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


def _resolve_dist_dir() -> Path | None:
    override = os.environ.get("OPC_WEB_DIST")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    # ``src/daemon/routes/web_static.py`` → parents[3] is repo root.
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "web" / "dist"
    return candidate if candidate.is_dir() else None


def _is_spa_route(path: str) -> bool:
    """True if a 404 on this path should fall back to index.html."""
    if path.startswith("/api/"):
        return False
    if path.startswith("/assets/"):
        return False
    return True


def register(app: FastAPI) -> None:
    """Attach SPA static mount or placeholder to the FastAPI app.

    Call this AFTER all API routers are registered.
    """
    dist = _resolve_dist_dir()

    if dist is not None:
        assets_dir = dist / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="assets",
            )
        index_path = dist / "index.html"

        @app.get("/", include_in_schema=False)
        def _spa_root() -> FileResponse:
            return FileResponse(str(index_path))

        @app.exception_handler(StarletteHTTPException)
        async def _spa_or_404(request: Request, exc: StarletteHTTPException):
            if (
                request.method == "GET"
                and exc.status_code == 404
                and _is_spa_route(request.url.path)
            ):
                return FileResponse(str(index_path))
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
    else:

        @app.get("/", include_in_schema=False)
        def _placeholder_root() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER_HTML)

        @app.exception_handler(StarletteHTTPException)
        async def _placeholder_or_404(request: Request, exc: StarletteHTTPException):
            if (
                request.method == "GET"
                and exc.status_code == 404
                and _is_spa_route(request.url.path)
            ):
                return HTMLResponse(_PLACEHOLDER_HTML)
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
