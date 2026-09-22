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

MANILA_OFFSET = datetime.timedelta(hours=8)  # Asia/Manila is UTC+8, no daylight saving


def to_manila(value):
    """Converts a naive UTC datetime (how everything is stored) to Philippine time for
    display. Storage always stays UTC - this is purely a display-time conversion."""
    if not value:
        return value
    return value + MANILA_OFFSET


def manila_datetime(value, fmt="%m/%d/%Y %I:%M %p"):
    """Convenience filter: convert to Manila time AND format in one step."""
    v = to_manila(value)
    return v.strftime(fmt) if v else ""


templates.env.filters["to_manila"] = to_manila
templates.env.filters["manila_datetime"] = manila_datetime
