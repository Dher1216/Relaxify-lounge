import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import hashlib
from fastapi.responses import RedirectResponse, FileResponse, Response, JSONResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from sqlalchemy import inspect, text

from .database import Base, engine, SessionLocal
from .models import User
from .routers import auth_routes, dashboard, transactions, reports, accounts, users, audit, rates, staff_sale, socks

app = FastAPI(title="Relaxify Lounge - Accounting System")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-change-me-in-render-env-vars")

STAFF_ALLOWED_PREFIXES = ("/staff", "/static", "/login", "/logout", "/launch", "/session-ping",
                          "/sw.js", "/service-worker.js", "/manifest.webmanifest")


class StaffConfinementMiddleware(BaseHTTPMiddleware):
    """STAFF-role users only ever see the simplified Record a Sale screen - this
    enforces that at the routing level (not just by hiding nav links), so it can't
    be bypassed by typing a URL directly."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(STAFF_ALLOWED_PREFIXES):
            user_id = request.session.get("user_id")
            if user_id:
                db = SessionLocal()
                try:
                    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
                    if user and user.role == "STAFF":
                        return RedirectResponse("/staff/sale", status_code=303)
                finally:
                    db.close()
        return await call_next(request)


# Middleware order matters: Starlette runs the LAST-added middleware first, so
# SessionMiddleware must be added AFTER StaffConfinementMiddleware - otherwise
# request.session isn't set up yet when the staff check tries to read it.
app.add_middleware(StaffConfinementMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _build_fingerprint():
    """Hash of every file the service worker saves on the device. When any of them
    changes (i.e. on any deploy that touches the shell), the cache name changes, so
    phones drop the old saved screens automatically - no manual version bump."""
    h = hashlib.sha1()
    files = [os.path.join(BASE_DIR, "sw.js"), os.path.join(BASE_DIR, "templates", "launch.html")]
    for sub in ("css", "js", "img"):
        d = os.path.join(BASE_DIR, "static", sub)
        files += [os.path.join(d, f) for f in sorted(os.listdir(d))]
    for f in files:
        try:
            with open(f, "rb") as fh:
                h.update(os.path.basename(f).encode()); h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


BUILD_ID = _build_fingerprint()
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Service-Worker-Allowed": "/"}


def _sw_response():
    with open(os.path.join(BASE_DIR, "sw.js"), encoding="utf-8") as fh:
        body = fh.read().replace("__BUILD__", BUILD_ID)
    return Response(body, media_type="application/javascript", headers=_NO_CACHE)


# Served from the site ROOT (a service worker only controls pages at or below its own
# path). /service-worker.js is the OLD address; phones that registered it pick up the
# new code from there and then move over to /sw.js.
@app.get("/sw.js")
def service_worker():
    return _sw_response()


@app.get("/service-worker.js")
def service_worker_legacy():
    return _sw_response()


@app.get("/manifest.webmanifest")
def web_manifest():
    manifest = {
        "name": "Relaxify Lounge", "short_name": "Relaxify",
        "start_url": "/launch", "scope": "/", "display": "standalone",
        "background_color": "#0b0b0c", "theme_color": "#0b0b0c",
        "icons": [
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


@app.get("/launch")
def launch_page():
    # Public, data-free page that the service worker saves on the device.
    with open(os.path.join(BASE_DIR, "templates", "launch.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/session-ping")
def session_ping(request: Request):
    """Tiny data-free request used by the launcher to wake the server and learn where
    to send the person. Returns no figures. Never cached."""
    home, logged_in = "/login", False
    user_id = request.session.get("user_id")
    if user_id:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
            if user:
                logged_in = True
                home = "/staff/sale" if user.role == "STAFF" else "/"
        finally:
            db.close()
    return JSONResponse({"awake": True, "logged_in": logged_in, "home": home},
                        headers={"Cache-Control": "no-store"})

# Create tables on boot if they don't exist yet, then seed the Chart of Accounts and
# the initial users if they aren't already there. Safe to run on every startup: seed.run()
# only inserts rows that don't already exist, so this never duplicates or overwrites data.
# (This replaces needing Render Shell access, which isn't available on the free plan.)
Base.metadata.create_all(bind=engine)


def _auto_add_missing_columns():
    """create_all() only creates tables that don't exist yet - it never alters an
    existing table to add a new column. So whenever a Column is added to a model
    for an existing table (e.g. Account.parent_id), the live database needs to be
    told about it separately, or every query against that table breaks with
    'UndefinedColumn'. This inspects the live schema on every boot and ALTERs in
    any column that's on the model but missing from the table - safe to run
    every startup since it only ever adds columns that aren't already there, and
    it never touches or drops existing data. Works on both SQLite (dev) and
    Postgres (prod). This is a lightweight stand-in for a real migration tool
    (e.g. Alembic) - fine for solo/small projects, but a growing team should
    move to proper migrations."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand-new table - create_all() already handled it
            existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                nullable = "" if column.nullable else " NOT NULL"
                default = ""
                # Only simple, safe defaults are auto-applied here. A NOT NULL column
                # with no default and no fallback below would fail on a non-empty
                # table - if that ever happens, add a default or make it nullable.
                if column.default is not None and getattr(column.default, "arg", None) is not None \
                        and not callable(column.default.arg):
                    default = f" DEFAULT {column.default.arg!r}"
                elif not column.nullable:
                    # Can't safely add a NOT NULL column with no default to a table
                    # that may already have rows - add it as nullable instead so the
                    # app keeps working; tighten it manually later if needed.
                    nullable = ""
                stmt = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}{nullable}{default}'
                conn.execute(text(stmt))
                print(f"[auto-migrate] Added missing column: {table.name}.{column.name}")


_auto_add_missing_columns()

from . import seed as _seed
_seed.run()

app.include_router(auth_routes.router)
app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(reports.router)
app.include_router(accounts.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(rates.router)
app.include_router(staff_sale.router)
app.include_router(socks.router)
