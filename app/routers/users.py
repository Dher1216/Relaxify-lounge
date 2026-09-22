import os
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user, can, hash_password
from ..models import User, AuditLog

router = APIRouter(prefix="/users")
ROLES = ["ADMIN", "ACCOUNTANT", "STAFF", "VIEWER"]


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "users"):
        return RedirectResponse("/", status_code=303)
    users = db.query(User).order_by(User.username).all()
    return templates.TemplateResponse("users.html", {
        "request": request, "user": user, "users": users, "roles": ROLES, "error": None,
    })


@router.post("/add")
def add_user(
    request: Request, db: Session = Depends(get_db),
    username: str = Form(...), password: str = Form(...), role: str = Form(...),
):
    user = get_current_user(request, db)
    if not user or not can(user, "users"):
        return RedirectResponse("/users", status_code=303)

    username = username.strip()
    if db.query(User).filter(User.username == username).first():
        users = db.query(User).order_by(User.username).all()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users, "roles": ROLES,
            "error": f"Username '{username}' already exists.",
        })
    if len(password) < 8:
        users = db.query(User).order_by(User.username).all()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users, "roles": ROLES,
            "error": "Password must be at least 8 characters.",
        })

    db.add(User(username=username, password_hash=hash_password(password), role=role, is_active=True))
    db.add(AuditLog(username=user.username, action=f"Added user {username} with role {role}", module="USERS"))
    db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/toggle")
def toggle_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not can(user, "users"):
        return RedirectResponse("/users", status_code=303)
    target = db.query(User).filter(User.id == user_id).first()
    if target and target.id != user.id:
        target.is_active = not target.is_active
        db.add(AuditLog(
            username=user.username,
            action=f"{'Activated' if target.is_active else 'Deactivated'} user {target.username}",
            module="USERS",
        ))
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, db: Session = Depends(get_db), new_password: str = Form(...)):
    user = get_current_user(request, db)
    if not user or not can(user, "users"):
        return RedirectResponse("/users", status_code=303)
    target = db.query(User).filter(User.id == user_id).first()
    if target and len(new_password) >= 8:
        target.password_hash = hash_password(new_password)
        db.add(AuditLog(username=user.username, action=f"Reset password for user {target.username}", module="USERS"))
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/change-password")
def change_own_password(request: Request, db: Session = Depends(get_db), current_password: str = Form(...), new_password: str = Form(...)):
    from ..auth import verify_password
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not verify_password(current_password, user.password_hash):
        users = db.query(User).order_by(User.username).all()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users, "roles": ROLES,
            "error": "Current password is incorrect.",
        })
    if len(new_password) < 8:
        users = db.query(User).order_by(User.username).all()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users, "roles": ROLES,
            "error": "New password must be at least 8 characters.",
        })
    user.password_hash = hash_password(new_password)
    db.add(AuditLog(username=user.username, action="Changed own password", module="USERS"))
    db.commit()
    return RedirectResponse("/users", status_code=303)
