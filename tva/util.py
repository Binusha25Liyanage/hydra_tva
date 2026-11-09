"""Small shared helpers: number parsing, territory keys, dates, ordinals."""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date, datetime, timedelta


def num(v) -> float:
    """Parse SFA numbers safely: '2,325.00' -> 2325.0, None/'' -> 0."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def key(name) -> str:
    """Territory/category match key: case-insensitive, ignores spaces and punctuation.

    'Nuwara Eliya' == 'Nuwaraeliya', 'Anuradhapura(SMMT)' == 'Anuradhapura (SMMT)'.
    """
    if name is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def parse_date(v) -> date | None:
    """Accepts date/datetime, '2026-11-03', '03.11.2026', 'November 3, 2026', '11/03/2026'."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    return None


def iso(d: date) -> str:
    return d.isoformat()


def month_first(d: date) -> date:
    return d.replace(day=1)


def month_last(d: date) -> date:
    return d.replace(day=_cal.monthrange(d.year, d.month)[1])


def prev_month_same_day(d: date) -> date:
    """Same calendar day last month, clamped (31 Dec -> 30 Nov)."""
    first_prev = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
    last = _cal.monthrange(first_prev.year, first_prev.month)[1]
    return first_prev.replace(day=min(d.day, last))


def days_between(d1: date, d2: date):
    d = d1
    while d <= d2:
        yield d
        d += timedelta(days=1)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def long_date(d: date) -> str:
    """'3rd November 2026' (Famulus column-header style)."""
    return f"{ordinal(d.day)} {d.strftime('%B %Y')}"


def plain_date(d: date) -> str:
    """'3 October 2026'."""
    return f"{d.day} {d.strftime('%B %Y')}"


def short_date(d: date) -> str:
    """'03 Nov 2026'."""
    return d.strftime("%d %b %Y")


def signed(v: float) -> str:
    r = round(v)
    return f"+{r:,.0f}" if r > 0 else f"{r:,.0f}"


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()
