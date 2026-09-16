import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

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

# Create tables on boot if they don't exist yet, then seed the Chart of Accounts and
# the initial users if they aren't already there. Safe to run on every startup: seed.run()
# only inserts rows that don't already exist, so this never duplicates or overwrites data.
# (This replaces needing Render Shell access, which isn't available on the free plan.)
Base.metadata.create_all(bind=engine)
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
