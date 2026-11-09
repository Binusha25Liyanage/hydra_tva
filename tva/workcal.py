"""Working-day and competition calendar (from the NSM's monthly competition calendar)."""
from __future__ import annotations

import calendar as _cal
from datetime import date

from .util import iso, now_iso


def month_days(con, year: int, month: int) -> list[dict]:
    """Every date of the month with is_working / is_competition / round.

    Months set up in the Calendar screen use the stored rows. Otherwise Monday-Saturday are
    working days and there is no competition.
    """
    n = _cal.monthrange(year, month)[1]
    stored = {r["date"]: r for r in con.execute(
        "SELECT * FROM calendar_days WHERE date BETWEEN ? AND ?",
        (iso(date(year, month, 1)), iso(date(year, month, n))))}
    out = []
    for d in range(1, n + 1):
        dt = date(year, month, d)
        r = stored.get(iso(dt))
        if r:
            out.append({"date": dt, "is_working": bool(r["is_working"]), "is_competition": bool(r["is_competition"]),
                        "round": r["round"], "configured": True})
        else:
            out.append({"date": dt, "is_working": dt.weekday() != 6, "is_competition": False, "round": None,
                        "configured": False})
    return out


def is_configured(con, year, month) -> bool:
    return any(d["configured"] for d in month_days(con, year, month))


def working_dates(con, year, month) -> list[date]:
    return [d["date"] for d in month_days(con, year, month) if d["is_working"]]


def nowd(con, year, month) -> int:
    """Number of working days. A configured calendar wins; otherwise Famulus' stored NOWD; else Mon-Sat."""
    if is_configured(con, year, month):
        return len(working_dates(con, year, month))
    r = con.execute("SELECT nowd FROM month_settings WHERE year=? AND month=?", (year, month)).fetchone()
    if r:
        return int(r["nowd"])
    return len(working_dates(con, year, month))


def is_working(con, d: date) -> bool:
    return next(x["is_working"] for x in month_days(con, d.year, d.month) if x["date"] == d)


def competition_info(con, d: date) -> dict:
    days = month_days(con, d.year, d.month)
    me = next(x for x in days if x["date"] == d)
    rnd = me["round"]
    rdays = [x["date"] for x in days if x["is_competition"] and x["round"] == rnd] if me["is_competition"] else []
    return {"is_competition": me["is_competition"], "round": rnd,
            "round_day": (rdays.index(d) + 1) if d in rdays else None, "round_days": len(rdays)}


def save_month(con, year: int, month: int, days: list[dict]) -> None:
    """days: [{date:'YYYY-MM-DD', is_working:bool, is_competition:bool, round:int|None}] for the whole month."""
    n = _cal.monthrange(year, month)[1]
    con.execute("DELETE FROM calendar_days WHERE date BETWEEN ? AND ?",
                (iso(date(year, month, 1)), iso(date(year, month, n))))
    for d in days:
        comp = bool(d.get("is_competition")) and bool(d.get("is_working"))
        con.execute("INSERT INTO calendar_days(date, is_working, is_competition, round) VALUES (?,?,?,?)",
                    (d["date"], int(bool(d.get("is_working"))), int(comp), (d.get("round") or 1) if comp else None))
    con.execute("INSERT INTO month_settings(year, month, nowd, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(year, month) DO UPDATE SET nowd=excluded.nowd, updated_at=excluded.updated_at",
                (year, month, sum(1 for d in days if d.get("is_working")), now_iso()))
    con.commit()


def seed_known_calendars(con) -> None:
    """November 2026 competition calendar from the NSM (24 working days, two rounds)."""
    if is_configured(con, 2026, 11):
        return
    holidays = {24}
    r1 = {3, 4, 5, 6, 7, 9, 10, 11}
    r2 = {13, 14, 16, 17, 18, 19, 20}
    days = []
    for d in range(1, 31):
        dt = date(2026, 11, d)
        working = dt.weekday() != 6 and d not in holidays
        rnd = 1 if d in r1 else 2 if d in r2 else None
        days.append({"date": iso(dt), "is_working": working, "is_competition": rnd is not None, "round": rnd})
    save_month(con, 2026, 11, days)
