"""FastAPI backend + pages.   Run:  python -m uvicorn app.main:app --port 8000

Public:  /  (onboarding), /api/auth/*, /api/health
Signed-in only: /app (console), /profile, every other /api route, and the audit screenshots under /files.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import assistant, auth, connect, dossier, llm, sessions
from app.capture.store import AuditStore
from app.complaint import draft_complaint
from app.config import ROOT, settings
from app.personas import default_matrix, quick_matrix
from app.pipeline import DEMO_SITES, AuditInconclusive, audit_label, run_audit
from app.providers import get_providers
from app.providers.opensource import ensure_allowed, host_of as ensure_host

app = FastAPI(title="Pardarshi: active consumer-protection auditor", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
store = AuditStore()
WEB = ROOT / "web"
JOBS: dict[str, dict] = {}  # audit_id -> {events, done, error, owner}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    """CSRF defence for the JSON API + security headers on every response."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "pardarshi" or (origin and urlparse(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "Blocked: cross-site or malformed request."}, status_code=403)
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                         "Content-Security-Policy": CSP, "Permissions-Policy": "camera=(), microphone=(self), geolocation=()"})
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/app", "/profile"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------------------- pages
def _page(name: str) -> FileResponse:
    return FileResponse(WEB / name)


@app.get("/", include_in_schema=False)
def landing(request: Request):
    u = auth.optional_user(request)
    upgrading = bool(u and u["is_guest"] and request.query_params.get("upgrade"))  # a demo visitor creating an account
    return RedirectResponse("/app") if u and u["onboarded"] and not upgrading else _page("index.html")


@app.get("/app", include_in_schema=False)
def console(request: Request):
    u = auth.optional_user(request)
    return _page("app.html") if u and u["onboarded"] else RedirectResponse("/")


@app.get("/report", include_in_schema=False)
def report_page(request: Request):
    u = auth.optional_user(request)
    return _page("report.html") if u and u["onboarded"] else RedirectResponse("/")


@app.get("/profile", include_in_schema=False)
def profile_page(request: Request):
    u = auth.optional_user(request)
    return _page("profile.html") if u and u["onboarded"] else RedirectResponse("/")


# ------------------------------------------------------------------------------- screenshots (access-checked)
@app.get("/files/{path:path}")
def files(path: str, user=Depends(auth.require_user)):
    audit_id = path.split("/", 1)[0]
    target = (store.root / path).resolve()
    if not str(target).startswith(str(store.root.resolve())) or not target.is_file():
        raise HTTPException(404, "Not found")
    if not auth.can_view(audit_id, user["id"]):
        raise HTTPException(404, "Not found")  # 404, not 403: do not reveal that it exists
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------------------------- API
class AuditRequest(BaseModel):
    site: str = Field(default="dirty", max_length=200)
    provider: str | None = None  # "opensource" | "tinyfish"; default from PROVIDER
    repeats: int = Field(default=3, ge=1, le=5)
    products: int = Field(default=1, ge=1, le=5)  # products audited side by side, one parallel lane each
    quick: bool = False  # 2 personas instead of 6: cheaper for live demos / credits
    with_policy: bool = True
    use_session: bool = False  # audit behind the login with the user's own saved sign-in (scripts/connect_account)


class ComplaintRequest(BaseModel):
    name: str = Field(default="[Your name]", max_length=80)


class AgentRequest(BaseModel):
    message: str = Field(max_length=2000)
    session_id: str | None = Field(default=None, max_length=40)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/health/details")
async def health_details(user=Depends(auth.require_user)):
    shop = False
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            shop = (await c.get(f"{settings.demo_shop_url}/__truth/clean")).status_code == 200
    except Exception:
        pass
    return {"provider": settings.provider, "tinyfish_key": bool(settings.tinyfish_api_key), "gemini_key": llm.available(),
            "classifier_trained": (settings.models_dir / "pattern_clf.joblib").exists(), "demo_shop_up": shop}


@app.get("/api/sites")
def sites(user=Depends(auth.require_user)):
    demo = [{"id": s, "label": audit_label(s), "kind": "demo"} for s in sorted(DEMO_SITES, reverse=True)]
    # Allowlisted real sites, one entry per site (www. and bare host count once); never the local machine.
    seen, real = set(), []
    for h in settings.allowed_hosts:
        bare = h[4:] if h.startswith("www.") else h
        if bare in seen or h in ("localhost", "127.0.0.1") or h == ensure_host(settings.demo_shop_url):
            continue
        seen.add(bare)
        host = h if h.startswith("www.") or f"www.{bare}" not in settings.allowed_hosts else f"www.{bare}"
        real.append({"id": host, "label": bare, "kind": "allowed"})
    return demo + sorted(real, key=lambda x: x["label"])


@app.post("/api/audits")
async def start_audit(req: AuditRequest, user=Depends(auth.require_user)):
    if req.site not in DEMO_SITES:
        try:
            ensure_allowed(req.site if "//" in req.site else f"https://{req.site}")
        except PermissionError as e:
            raise HTTPException(400, str(e))
    if req.provider == "tinyfish" and not settings.tinyfish_api_key:
        raise HTTPException(400, "No TinyFish API key is configured on the server.")
    session_file = None
    if req.use_session:
        if req.site in DEMO_SITES:
            raise HTTPException(400, "The demo shops have no accounts to sign in to.")
        if user["is_guest"]:
            raise HTTPException(400, "Create an account first: demo sessions cannot connect shop accounts.")
        if (req.provider or settings.provider) != "opensource":
            raise HTTPException(400, "Your signed-in session stays on this machine, so it runs on the Open source provider only.")
        try:
            found = sessions.find(user["id"], req.site)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not found:
            raise HTTPException(400, f"No saved sign-in for {req.site}. Click “Connect account” in the Another site "
                                     "dialog, sign in in the window that opens, then click Save sign-in.")
        session_file = str(found)
    auth._throttle(f"audit:{user['id']}", 4 if user["is_guest"] else 30, 3600, "audits (credit protection)")
    audit_id = f"{req.site.replace('/', '_').replace(':', '')[:20]}-{uuid.uuid4().hex[:6]}"
    auth.set_owner(audit_id, user["id"])
    job = JOBS[audit_id] = {"events": [], "done": False, "error": None, "owner": user["id"]}

    async def task():
        providers = None
        try:
            providers = get_providers(req.provider, store)
            await run_audit(req.site, providers=providers, store=store, audit_id=audit_id, repeats=req.repeats,
                            personas=quick_matrix() if req.quick else default_matrix(), on_event=job["events"].append,
                            with_policy=req.with_policy, session_file=session_file, products=req.products)
        except AuditInconclusive as e:
            job["error"] = str(e)[:400]
            job["events"].append({"type": "error", "message": job["error"]})
        except Exception as e:
            job["error"] = f"{type(e).__name__}: {e}"[:300]
            job["events"].append({"type": "error", "message": job["error"]})
        finally:
            job["done"] = True
            if providers:
                await providers.aclose()

    asyncio.create_task(task())
    return {"audit_id": audit_id}


@app.get("/api/audits")
def list_audits(user=Depends(auth.require_user)):
    mine = auth.owned_ids(user["id"])
    out = []
    for r in store.list_reports():
        if r.audit_id in mine or auth.owner_of(r.audit_id) is None:
            out.append({"audit_id": r.audit_id, "site": r.site, "provider": r.provider, "trust_score": r.trust_score,
                        "findings": len(r.findings), "created_at": r.created_at, "runs_ok": r.runs_ok,
                        "runs_total": r.runs_total, "mine": r.audit_id in mine})
    return out


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str, user=Depends(auth.require_user)):
    report = store.load_report(audit_id) if auth.can_view(audit_id, user["id"]) else None
    if not report:
        raise HTTPException(404, "Report not ready")
    return report


@app.get("/api/sessions")
def list_sessions(user=Depends(auth.require_user)):
    """The user's connected shop accounts (host names only; the cookies never leave the server)."""
    return [] if user["is_guest"] else sessions.list_for(user["id"])


@app.delete("/api/sessions/{host}")
def forget_session(host: str, user=Depends(auth.require_user)):
    try:
        connect.cancel(user["id"], host)
        shutil.rmtree(connect.profile_dir(user["id"], host), ignore_errors=True)
        return {"ok": sessions.forget(user["id"], host)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _connect_host(host: str, user) -> str:
    if user["is_guest"]:
        raise HTTPException(400, "Create an account first: demo sessions cannot connect shop accounts.")
    try:
        h = sessions.clean_host(host)
        ensure_allowed(f"https://{h}")
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    return h


@app.post("/api/sessions/{host}/connect")
def connect_session(host: str, user=Depends(auth.require_user)):
    """Open the shop in a normal Chrome window on this machine; the account holder signs in there themselves."""
    h = _connect_host(host, user)
    auth._throttle(f"connect:{user['id']}", 20, 3600, "sign-in windows")
    try:
        return connect.start(user["id"], h)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/sessions/{host}/save")
async def save_session(host: str, user=Depends(auth.require_user)):
    h = _connect_host(host, user)
    try:
        return await connect.save(user["id"], h)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Could not read the sign-in window: {type(e).__name__}")


@app.post("/api/sessions/{host}/cancel")
def cancel_session(host: str, user=Depends(auth.require_user)):
    connect.cancel(user["id"], _connect_host(host, user))
    return {"ok": True}


@app.get("/api/audits/{audit_id}/dossier")
def get_dossier(audit_id: str, user=Depends(auth.require_user)):
    """Reference number, method and SHA-256 fingerprints for the printable findings report."""
    report = store.load_report(audit_id) if auth.can_view(audit_id, user["id"]) else None
    if not report:
        raise HTTPException(404, "Report not ready")
    return dossier.build(report, store)


@app.delete("/api/audits/{audit_id}")
def delete_audit(audit_id: str, user=Depends(auth.require_user)):
    if auth.owner_of(audit_id) != user["id"]:
        raise HTTPException(404, "Not found")
    shutil.rmtree(store.root / audit_id, ignore_errors=True)
    auth.q("DELETE FROM audit_owner WHERE audit_id=?", (audit_id,))
    JOBS.pop(audit_id, None)
    return {"ok": True}


@app.get("/api/audits/{audit_id}/events")
async def audit_events(audit_id: str, user=Depends(auth.require_user)):
    if not auth.can_view(audit_id, user["id"]):
        raise HTTPException(404, "Unknown audit")
    job = JOBS.get(audit_id)
    if job is None:  # cached audit from an earlier session: replay a single 'complete' event
        report = store.load_report(audit_id)
        if not report:
            raise HTTPException(404, "Unknown audit")

        async def replay():
            yield f"data: {json.dumps({'type': 'complete', 'audit_id': audit_id, 'trust_score': report.trust_score})}\n\n"

        return StreamingResponse(replay(), media_type="text/event-stream")

    async def stream():
        i = 0
        while True:
            while i < len(job["events"]):
                yield f"data: {json.dumps(job['events'][i], default=str)}\n\n"
                i += 1
            if job["done"] and i >= len(job["events"]):
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/audits/{audit_id}/complaint")
async def complaint(audit_id: str, req: ComplaintRequest, user=Depends(auth.require_user)):
    report = store.load_report(audit_id) if auth.can_view(audit_id, user["id"]) else None
    if not report:
        raise HTTPException(404, "Report not ready")
    return await draft_complaint(report, req.name)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict] = Field(default_factory=list, max_length=12)


@app.post("/api/audits/{audit_id}/ask")
async def ask_audit(audit_id: str, req: AskRequest, user=Depends(auth.require_user)):
    """Questions about ONE audit, answered from its own data (exact local engine, or Gemini for open-ended ones)."""
    report = store.load_report(audit_id) if auth.can_view(audit_id, user["id"]) else None
    if not report:
        raise HTTPException(404, "Report not ready")
    auth._throttle(f"ask:{user['id']}", 60 if user["is_guest"] else 240, 3600, "questions")
    return await assistant.ask(report.model_dump(mode="json"), req.question, req.history)


@app.post("/api/agent")
async def agent_chat(req: AgentRequest, user=Depends(auth.require_user)):
    """Talk to the Google ADK orchestrator (it can start audits, read policies, draft complaints)."""
    if not llm.available():
        raise HTTPException(400, "GEMINI_API_KEY is not set")
    auth._throttle(f"chat:{user['id']}", 10 if user["is_guest"] else 60, 3600, "chat messages (quota protection)")
    from app.agents.orchestrator import chat  # lazy: importing ADK is slow

    auth.CURRENT_USER.set(user["id"])  # audits started by the agent belong to this user
    try:
        return await chat(req.message, req.session_id, user_id=f"u{user['id']}")
    except Exception as e:
        raise HTTPException(502, f"Agent error: {type(e).__name__}: {str(e)[:200]}")


@app.get("/api/classifier")
def classifier_metrics(user=Depends(auth.require_user)):
    p = settings.models_dir / "metrics.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@app.get("/api/eval")
def eval_results(user=Depends(auth.require_user)):
    p = settings.data_dir / "eval_results.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@app.get("/api/profile/stats")
def profile_stats(user=Depends(auth.require_user)):
    mine = auth.owned_ids(user["id"])
    reports = [r for r in store.list_reports() if r.audit_id in mine]
    if not reports:
        return {"audits": 0}
    scores = [r.trust_score for r in reports]
    worst, best = min(reports, key=lambda r: r.trust_score), max(reports, key=lambda r: r.trust_score)
    return {"audits": len(reports), "avg_score": round(statistics.mean(scores), 1), "findings": sum(len(r.findings) for r in reports),
            "worst": {"site": worst.site, "score": worst.trust_score, "audit_id": worst.audit_id},
            "best": {"site": best.site, "score": best.trust_score, "audit_id": best.audit_id},
            "last": {"site": reports[0].site, "audit_id": reports[0].audit_id, "created_at": reports[0].created_at}}


store.root.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
