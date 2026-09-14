import os
import datetime
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.globals["zip"] = zip


def pretty_date(value):
    """Formats an ISO date string ('2026-09-13') or date object as 'September 13, 2026'."""
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.date.fromisoformat(value)
    return value.strftime("%B %-d, %Y") if os.name != "nt" else value.strftime("%B %#d, %Y")


templates.env.filters["pretty_date"] = pretty_date
