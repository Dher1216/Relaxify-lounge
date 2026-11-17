import bcrypt
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


ROLE_PERMISSIONS = {
    "ADMIN": {"transactions", "reports", "accounts", "users", "audit", "edit_posted_transactions"},
    "ACCOUNTANT": {"transactions", "reports", "accounts"},
    "STAFF": {"transactions", "reports_view"},
    "VIEWER": {"reports_view"},
}


def can(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, set())
