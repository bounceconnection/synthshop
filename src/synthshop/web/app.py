"""Single-owner loopback UI. GET, upload, analysis and research never publish."""

import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from synthshop.core.application import Application
from synthshop.core.config import Settings
from synthshop.core.photos import MAX_BYTES
from synthshop.core.pricing import recommendation
from synthshop.core.product_store import DraftConflictError
from synthshop.core.publishing import Publisher
from synthshop.integrations.reverb import ReverbClient

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION = "synthshop_session"


def matches(supplied: str, expected: str) -> bool:
    """Constant-time comparison that also tolerates non-ASCII attacker input."""
    return bool(expected) and secrets.compare_digest(supplied.encode(), expected.encode())


def render(request: Request, template: str, **context):
    """Expose presence/target only; never serialize backend settings to the browser."""
    settings = request.app.state.service.settings
    return TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context={
            "csrf": request.app.state.csrf,
            "ready": settings.readiness(),
            "environment": settings.reverb_base_url,
            "target": settings.expected_shop_slug,
            **context,
        },
    )


def request_rejection(request: Request) -> HTMLResponse | None:
    """Check host, origin and bounded request size before parsing any uploaded content."""
    origins = request.app.state.origins
    host = "http://" + request.headers.get("host", "")
    if host not in origins or request.headers.get("sec-fetch-site") == "cross-site":
        return HTMLResponse("Untrusted Host or cross-site request", status_code=403)
    if request.method != "GET" and request.headers.get("origin") not in origins:
        return HTMLResponse("Local same-origin requests only", status_code=403)
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return HTMLResponse("Invalid request size", status_code=400)
    if size > 80 * 1024 * 1024:
        return HTMLResponse("Upload total exceeds 80 MiB; add smaller batches.", status_code=413)
    if request.method == "POST" and size <= 0:
        return HTMLResponse("A bounded Content-Length is required", status_code=411)
    return None


async def local_boundary(request: Request, call_next):
    """Launch-link session plus defense-in-depth content/embedding policies."""
    rejected = request_rejection(request)
    if rejected is not None:
        return rejected
    if request.url.path != "/unlock" and not matches(
        request.cookies.get(SESSION, ""), request.app.state.session
    ):
        return HTMLResponse(
            "Locked. Open the one-time link printed by synthshop serve.", status_code=403
        )
    response = await call_next(request)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; "
            "script-src 'none'; frame-ancestors 'none'; form-action 'self'; "
            "base-uri 'none'; connect-src 'self'",
        }
    )
    return response


async def unlock(request: Request, key: str = ""):
    """One-time redemption; async without awaits, so check-and-clear is atomic."""
    state = request.app.state
    if matches(request.cookies.get(SESSION, ""), state.session):
        return RedirectResponse("/", status_code=303)
    if not matches(key, state.unlock):
        return HTMLResponse(
            "This unlock link is invalid or already used. Restart synthshop serve for a new link.",
            status_code=403,
        )
    state.unlock = ""
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION, state.session, httponly=True, samesite="strict", path="/")
    return response


async def form_data(request: Request):
    """Every POST must carry the current session's unpredictable form token."""
    form = await request.form(max_files=25, max_fields=150, max_part_size=250_000)
    if not matches(str(form.get("csrf", "")), request.app.state.csrf):
        raise PermissionError("Session/CSRF check failed. Reload the local page.")
    return form


async def action_error(request: Request, exc: Exception):
    """Keep provider bodies, file paths and validation input out of rendered errors."""
    if isinstance(exc, PermissionError):
        status, message = 403, "Session/CSRF check failed. Reload the local page."
    elif isinstance(exc, KeyError):
        status, message = 404, "Draft or record not found."
    elif isinstance(exc, (httpx.HTTPError, OSError)):
        status, message = 502, "Provider or local photo unavailable. Saved draft retained."
    else:
        status = 409 if isinstance(exc, DraftConflictError) else 400
        message = (
            str(exc)
            if type(exc) in (ValueError, DraftConflictError)
            else "Invalid input; check fields."
        )
    response = render(request, "error.html", message=message)
    response.status_code = status
    return response


def create_app(settings: Settings | None = None, *, port: int = 8765) -> FastAPI:
    """No externally bound server factory or browser-stored credential configuration."""
    service = Application(settings or Settings())
    publisher = Publisher(service)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.unlock = secrets.token_urlsafe(32)
    app.state.session = secrets.token_urlsafe(32)
    app.state.csrf = secrets.token_urlsafe(32)
    app.state.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    app.middleware("http")(local_boundary)
    for error_type in (PermissionError, ValueError, KeyError, httpx.HTTPError, OSError):
        app.add_exception_handler(error_type, action_error)

    app.get("/unlock")(unlock)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(
            request,
            "index.html",
            drafts=service.store.list_all(),
            import_errors=service.import_errors,
        )

    @app.get("/style.css")
    def stylesheet():
        return FileResponse(Path(__file__).parent / "style.css", media_type="text/css")

    @app.get("/photos/{photo_id}")
    def photo(photo_id: str):
        path = service.photos.path(photo_id)
        if not path.is_file():
            raise KeyError("Photo missing")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/drafts")
    async def intake(request: Request):
        form = await form_data(request)
        uploads = [item for item in form.getlist("photos") if isinstance(item, UploadFile)]
        content = [await item.read(MAX_BYTES + 1) for item in uploads]
        draft = await run_in_threadpool(service.upload, content)
        return RedirectResponse(f"/drafts/{draft.id}", status_code=303)

    @app.get("/drafts/{draft_id}", response_class=HTMLResponse)
    def editor(request: Request, draft_id: str):
        draft = service.store.load(draft_id)
        return render(
            request,
            "draft.html",
            draft=draft,
            pricing=recommendation(draft),
            attempt=service.store.attempt(draft_id),
            references=None,
        )

    @app.post("/drafts/{draft_id}/{action}")
    async def mutate(request: Request, draft_id: str, action: str):
        form = await form_data(request)
        revision = int(str(form.get("revision", "0")))
        simple_actions = {"analyze": service.analyze, "research": service.research}
        if action in simple_actions:
            await run_in_threadpool(simple_actions[action], draft_id, revision)
        elif action == "save":
            await run_in_threadpool(service.edit, draft_id, revision, dict(form))
        elif action == "import":
            await run_in_threadpool(
                service.import_evidence, draft_id, revision, str(form["evidence"])
            )
        elif action == "match":
            await run_in_threadpool(
                service.review_evidence,
                draft_id,
                revision,
                str(form["comp_id"]),
                form.get("include") == "yes",
                str(form["rationale"]),
            )
        elif action == "order":
            await run_in_threadpool(
                service.reorder, draft_id, revision, str(form["order"]).split(",")
            )
        elif action == "photos":
            uploads = [item for item in form.getlist("photos") if isinstance(item, UploadFile)]
            content = [await item.read(MAX_BYTES + 1) for item in uploads]
            await run_in_threadpool(service.upload, content, draft_id, revision)
        elif action == "references":
            draft = service.current(draft_id, revision)

            def load_references():
                with ReverbClient(
                    service.settings, authenticated=bool(service.settings.reverb_api_token)
                ) as client:
                    return client.references()

            references = await run_in_threadpool(load_references)
            return render(
                request,
                "draft.html",
                draft=draft,
                pricing=recommendation(draft),
                attempt=service.store.attempt(draft_id),
                references=references,
            )
        elif action == "review":
            context = await run_in_threadpool(publisher.review, draft_id, revision)
            return render(request, "review.html", **context)
        elif action == "publish":
            if form.get("approval") != "publish-exact-revision":
                raise ValueError("Explicit approval of this exact revision is required.")
            await run_in_threadpool(
                publisher.publish, draft_id, revision, str(form.get("token", ""))
            )
        else:
            raise KeyError("Unknown action")
        return RedirectResponse(f"/drafts/{draft_id}", status_code=303)

    return app
