import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .database import Base, engine
from .routers import auth_routes, dashboard, transactions, reports, accounts, users, audit

app = FastAPI(title="Relaxify Lounge - Accounting System")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-change-me-in-render-env-vars")
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
