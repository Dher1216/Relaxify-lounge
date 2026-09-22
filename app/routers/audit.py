import os
from ..template_env import templates
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user, can
from ..models import AuditLog

router = APIRouter(prefix="/audit")


@router.get("")
def view_audit(request: Request, db: Session = Depends(get_db), q: str = None, username: str = None, page: int = 1):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "audit"):
        return RedirectResponse("/", status_code=303)

    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if q:
        query = query.filter(AuditLog.action.ilike(f"%{q}%"))
    if username:
        query = query.filter(AuditLog.username == username)

    page_size = 50
    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    logs = query.offset((page - 1) * page_size).limit(page_size).all()

    return templates.TemplateResponse("audit.html", {
        "request": request, "user": user, "logs": logs, "q": q or "", "username": username or "",
        "page": page, "total_pages": total_pages, "total": total,
    })
