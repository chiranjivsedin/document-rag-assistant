"""Serve the built web app (frontend/dist) from the API's own address, so a server needs no Node.js.

Turned on by FRONTEND_DIST; without it the API serves only its endpoints, as before. In development the
web app still runs on Vite's dev server, which forwards the API paths here.

How a request is answered once it is on:
- Every API route (and /docs, /openapi.json) behaves exactly as without the web app: they are matched first,
  and the web app only sees requests that no route claimed, not even with another method (those stay 405).
- /assets/... : Vite's build files, whose names carry a content hash, so browsers may keep them for a year.
  A missing one is a 404, never the page.
- Any other GET or HEAD: a file at the top of the build folder if there is one (for example a favicon),
  otherwise index.html, so the app opens whatever path it is loaded from. Unknown paths under the API's own
  prefixes (/health, /chat, /documents, /docs, ..., in any letter case) and missing files (a last segment with a
  dot) stay 404s.

Starlette's StaticFiles resolves every path, so a request can't reach a file outside the build folder. A name
the file system can't hold (e.g. "<" or "*" on Windows) is a plain miss, like any other missing file.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

INDEX = "index.html"
LONG_CACHE = "public, max-age=31536000, immutable"  # hashed names: a new build gets new names
NO_CACHE = "no-cache"  # revalidate every time, so a new build is picked up straight away

# Fixed content types for what Vite builds. Python takes them from the Windows registry when there is one, and
# other software can change it there; a script sent as text/plain, say, is never run (with nosniff): a blank page.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


class _BuildFiles(StaticFiles):
    """StaticFiles that adds the cache rule for its kind of file, fixes the build's content types, stops browsers
    guessing them, and treats a name the file system can't hold as missing."""

    def __init__(self, *, directory: Path, cache_control: str):
        super().__init__(directory=directory)
        self.cache_control = cache_control

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        try:
            return super().lookup_path(path)
        except PermissionError:
            raise  # StaticFiles answers it itself; a build folder the server can't read is not a missing file
        except OSError:  # e.g. "<", "*" or "?" in a name on Windows: no such file can exist
            return "", None

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        content_type = CONTENT_TYPES.get(Path(full_path).suffix.lower())
        if content_type and isinstance(response, FileResponse):  # not on a 304, which has no body or type
            response.media_type = content_type
            response.headers["Content-Type"] = content_type
        response.headers["Cache-Control"] = self.cache_control
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def serve_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the build in `dist` at /. Call after every API route is added: their paths are reserved here."""
    if not (dist / INDEX).is_file():
        raise RuntimeError(
            f"FRONTEND_DIST is set to '{dist}', which is '{os.path.abspath(dist)}' from the folder the API started "
            f"in, but there is no {INDEX} there. Build the web app (cd frontend; npm run build), point "
            "FRONTEND_DIST at its dist folder (preferably by its full path), or leave FRONTEND_DIST empty."
        )
    if (dist / "assets").is_dir():
        app.mount("/assets", _BuildFiles(directory=dist / "assets", cache_control=LONG_CACHE), name="web-assets")

    reserved = _api_prefixes(app)
    top_level = _BuildFiles(directory=dist, cache_control=NO_CACHE)
    not_found = app.router.not_found

    async def web_app(scope: Scope, receive: Receive, send: Send) -> None:
        """Runs only when no route matched (Starlette's router `default`)."""
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD") or _first_segment(scope["path"]) in reserved:
            await not_found(scope, receive, send)
            return
        try:
            response = await top_level.get_response(top_level.get_path(scope), scope)
        except HTTPException as error:
            if error.status_code != 404 or _looks_like_file(scope["path"]):
                raise
            response = await top_level.get_response(INDEX, scope)
        await response(scope, receive, send)

    app.router.default = web_app


def _api_prefixes(app: FastAPI) -> set[str]:
    """First path segments the API owns (e.g. "health", "documents", "docs"): never answered with the web app,
    in any letter case ("/HEALTH/x" is a JSON 404 like "/health/x", not the page).

    Taken from the OpenAPI schema, which lists every API route (also those of included routers), plus the docs
    pages and any other top-level route. A route hidden from the schema (include_in_schema=False) inside an
    included router would be missed: add its prefix here if one is ever added.
    """
    paths = set(app.openapi().get("paths", {}))
    paths |= {url for url in (app.docs_url, app.redoc_url, app.openapi_url) if url}
    paths |= {route.path for route in app.routes if isinstance(getattr(route, "path", None), str)}
    return {_first_segment(path) for path in paths} - {"", "assets"}


def _first_segment(path: str) -> str:
    """The first segment of a path, in lower case, so the API's prefixes are compared whatever their case."""
    return path.lstrip("/").split("/", 1)[0].lower()


def _looks_like_file(path: str) -> bool:
    return "." in path.rstrip("/").rsplit("/", 1)[-1]
