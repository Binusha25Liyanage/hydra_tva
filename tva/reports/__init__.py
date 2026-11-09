"""Report generation entry points."""
from __future__ import annotations

from datetime import date

from ..engine import Month
from ..util import iso, now_iso
from .daily import (daily_winners, day_report, pending_order_value, sales_comparison,
                    target_vs_achievement)
from .finalized import finalized

DAILY = {
    "tva": "Target vs Achievement",
    "day": "Day Report",
    "comparison": "Sales Comparison",
    "winners": "Daily Winners",
    "pending": "Pending Order Value",
}


def readiness(con, d: date) -> dict:
    """What is loaded for a report date (shown before generating)."""
    def has(t, x):
        return bool(con.execute(f"SELECT 1 FROM {t} WHERE date=?", (iso(x),)).fetchone())
    from ..util import prev_month_same_day
    p = prev_month_same_day(d)
    return {
        "targets": bool(con.execute("SELECT 1 FROM target_uploads WHERE year=? AND month=?",
                                    (d.year, d.month)).fetchone()),
        "sales": has("achievement_uploads", d), "callage": has("callage_uploads", d),
        "pending": has("pending_order_uploads", d), "prev_sales": has("achievement_uploads", p),
        "prev_date": iso(p),
    }


def generate_daily(con, d: date, folder: str, which: list[str] | None = None, force_winners=False) -> dict:
    which = which or list(DAILY)
    m = Month(con, d.year, d.month)
    out = []
    first = date(d.year, d.month, 1)
    funcs = {
        "tva": lambda: target_vs_achievement(m, first, d, folder),
        "day": lambda: day_report(m, d, folder),
        "comparison": lambda: sales_comparison(m, d, folder),
        "winners": lambda: daily_winners(m, d, folder, force_winners),
        "pending": lambda: pending_order_value(m, d, folder),
    }
    for k in which:
        r = funcs[k]()
        out.append(r)
        if r.get("path"):
            con.execute("INSERT INTO generation_log(created_at, report, period, path) VALUES (?,?,?,?)",
                        (now_iso(), r["report"], iso(d), r["path"]))
    con.commit()
    return {"results": out, "warnings": missing_days(con, m, d) + _warnings(m)}


def missing_days(con, m: Month, d: date) -> list[str]:
    """Working days this month (up to the report date) and last month (same period) with no sales loaded."""
    from ..util import prev_month_same_day
    from .. import workcal
    w = []
    have = m.loaded_dates("achievement_uploads", m.first, d)
    miss = [x for x in workcal.working_dates(con, d.year, d.month) if x <= d and x not in have]
    if miss:
        w.append("Sales not loaded for working day(s) " + ", ".join(iso(x) for x in miss)
                 + ": day targets and cumulative figures are understated until they are loaded.")
    if not m.loaded_dates("callage_uploads", d, d):
        w.append(f"No Callage report loaded for {iso(d)}: calls, productive calls and Daily Winners bill counts are 0.")
    p = prev_month_same_day(d)
    pf = date(p.year, p.month, 1)
    phave = m.loaded_dates("achievement_uploads", pf, p)
    pmiss = [x for x in workcal.working_dates(con, p.year, p.month) if x <= p and x not in phave]
    if pmiss:
        w.append(f"Last month's sales are missing for {len(pmiss)} working day(s) up to {iso(p)} "
                 f"(e.g. {', '.join(iso(x) for x in pmiss[:4])}): last-month comparisons are understated.")
    return w


def generate_finalized(con, d1: date, d2: date, folder: str) -> dict:
    m = Month(con, d2.year, d2.month)
    r = finalized(m, d1, d2, folder)
    con.execute("INSERT INTO generation_log(created_at, report, period, path) VALUES (?,?,?,?)",
                (now_iso(), r["report"], f"{iso(d1)} to {iso(d2)}", r["path"]))
    con.commit()
    return {"results": [r], "warnings": _warnings(m) + r.get("notes", [])}


def _warnings(m: Month) -> list[str]:
    w = sorted(set(m.notes))
    if m.unmatched:
        w.append("Not in the target hierarchy (excluded from totals; add an alias in Settings if it is a "
                 "spelling difference): " + ", ".join(sorted(m.unmatched)))
    return w
