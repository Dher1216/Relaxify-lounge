import os
import datetime
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user, can
from ..models import ChairRate, Setting, AuditLog

router = APIRouter(prefix="/rates")


@router.get("")
def view_rates(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "accounts"):
        return RedirectResponse("/", status_code=303)

    today = datetime.date.today()
    from .. import accounting as acc_lib
    current = acc_lib.current_chair_rates(db, today)
    all_rates = db.query(ChairRate).filter(ChairRate.is_active == True).order_by(ChairRate.chair_type, ChairRate.duration_minutes, ChairRate.effective_from).all()  # noqa: E712
    settings = {s.key: s.value for s in db.query(Setting).all()}
    return templates.TemplateResponse("rates.html", {
        "request": request, "user": user, "current": current, "all_rates": all_rates,
        "settings": settings, "error": None, "today": today.isoformat(),
    })


@router.post("/add")
def add_rate(
    request: Request, db: Session = Depends(get_db),
    chair_type: str = Form(...), duration_minutes: int = Form(...), price: str = Form(...),
    effective_from: str = Form(...),
):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/rates", status_code=303)

    try:
        price_val = float(price)
        eff_date = datetime.date.fromisoformat(effective_from)
    except ValueError:
        return RedirectResponse("/rates", status_code=303)

    # We deliberately do NOT deactivate the previous rate here. Multiple rate rows can
    # coexist for the same chair+duration with different effective_from dates - the
    # correct one is always resolved by date at the time of sale (see current_chair_rates
    # / _get_rate), so a future-dated price change never affects today's sales early.
    db.add(ChairRate(chair_type=chair_type, duration_minutes=duration_minutes, price=price_val, effective_from=eff_date, is_active=True))
    db.add(AuditLog(
        username=user.username,
        action=f"Set {chair_type} {duration_minutes}min rate to ₱{price_val:,.2f} (effective {eff_date})",
        module="RATES",
    ))
    db.commit()
    return RedirectResponse("/rates", status_code=303)


@router.post("/settings/update")
def update_settings(
    request: Request, db: Session = Depends(get_db),
    eye_massager_short_price: str = Form(...),
    eye_massager_short_threshold: str = Form(...),
    eye_massager_free_threshold: str = Form(...),
):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/rates", status_code=303)

    updates = {
        "eye_massager_short_price": eye_massager_short_price,
        "eye_massager_short_threshold": eye_massager_short_threshold,
        "eye_massager_free_threshold": eye_massager_free_threshold,
    }
    for key, value in updates.items():
        setting = db.query(Setting).filter(Setting.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.add(AuditLog(username=user.username, action="Updated Eye Massager add-on settings", module="RATES"))
    db.commit()
    return RedirectResponse("/rates", status_code=303)
