"""A process's API docs by relative paths, so that they work under any path the process is
reached at.

FastAPI's docs page, at `/docs`, loads the API's description from `/openapi.json`, a path from the
root of the server, and its "Try it out" sends each request to the root of the server too. Through
the Launcher of use case 2, which passes each process's pages through under `/p/<id>/`, both miss
the process. Every other page of a run asks for what it needs by a relative path, and so do the
docs here: the page loads the description next to itself, and the description names its server
`.`, the folder it was loaded from. At the process's own address that is its root, as before.

Nothing needs to know the path in front: not the process, which is started before the Launcher
serves it, and not the Launcher, which rewrites nothing that passes through.
"""

from __future__ import annotations

import posixpath

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse


def _relative(path: str, page: str) -> str:
    """`path` as the page served at `page` names it: relative to the page's folder."""
    return posixpath.relpath(path, posixpath.dirname(page))


def relative_api_docs(app: FastAPI) -> None:
    """Serve `app`'s API docs by relative paths, in place of FastAPI's own docs page.

    The page stays where it was, with the same title and settings, and lists the same routes; only
    the paths it names change. An app without docs is left as it is.
    """
    docs_url, openapi_url = app.docs_url, app.openapi_url
    if docs_url is None or openapi_url is None:
        return
    for route in list(app.routes):
        if getattr(route, "path", None) == docs_url:
            app.routes.remove(route)
    oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url

    @app.get(docs_url, include_in_schema=False)
    async def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=_relative(openapi_url, docs_url),
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=(
                _relative(oauth2_redirect_url, docs_url)
                if oauth2_redirect_url
                else None
            ),
            init_oauth=app.swagger_ui_init_oauth,
            swagger_ui_parameters=app.swagger_ui_parameters,
        )

    # Swagger UI resolves a relative server against the address it loaded the description from.
    app.servers = [{"url": "."}]
    # A description built before has no server: it is built again on the next request.
    app.openapi_schema = None
