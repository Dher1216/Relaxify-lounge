import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from sqlalchemy import inspect, text

from .database import Base, engine, SessionLocal
from .models import User
from .routers import auth_routes, dashboard, transactions, reports, accounts, users, audit, rates, staff_sale

app = FastAPI(title="Relaxify Lounge - Accounting System")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-change-me-in-render-env-vars")

STAFF_ALLOWED_PREFIXES = ("/staff", "/static", "/login", "/logout")


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


@app.get("/service-worker.js")
def service_worker():
    # Served from the site ROOT (not /static/) on purpose: a service worker can only
    # control pages under the same folder it's served from by default, and it needs
    # to control /staff/sale specifically for offline mode to actually work there.
    return FileResponse(
        os.path.join(BASE_DIR, "static", "service-worker.js"),
        media_type="application/javascript",
    )

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
