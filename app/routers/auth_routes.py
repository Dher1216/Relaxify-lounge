from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os
from ..template_env import templates

from ..database import get_db
from ..models import User, AuditLog
from ..auth import verify_password, get_current_user

router = APIRouter()


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        db.add(AuditLog(username=username or "(unknown)", action="Failed login attempt", module="AUTH"))
        db.commit()
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid username or password."}
        )

    request.session["user_id"] = user.id
    db.add(AuditLog(username=user.username, action="Logged in", module="AUTH"))
    db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        db.add(AuditLog(username=user.username, action="Logged out (Quit)", module="AUTH"))
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
