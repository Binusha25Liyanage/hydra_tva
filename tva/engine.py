"""Data access + the business rules confirmed against Famulus' 3 Nov 2026 outputs.

Rules (see README 'Calculation rules'):
* Achievement = SUM(sales_value) from the Outlet Wise SKU Sales report (all outlet categories).
* SMMT territories (SMMT target, no GT target) report in the SMMT columns, others in GT.
* Day target = (monthly target - achievement before today) / (NOWD - earlier working days the
  territory had a sale). Cumulative day target = sum of the day targets of every working day so far.
* Bill count (Daily Winners) = productive calls in the Callage report for that day.
* Winner = competition sale >= limit AND bills >= bill limit (Van 100,000/20, Pre 125,000/30).
  Competition sale excludes outlet categories marked 'not in competition' (SMMT/MRP).
* Last month = same calendar date last month (clamped to month end); cumulative = 1st..that date.
* AVG PC = Total PC (Rep Performance Report) / days worked (callage dates with PC > 0, per rep, summed).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import workcal
from .db import get_setting
from .hierarchy import Node, load_tree
from .util import iso, key, month_first, prev_month_same_day


class NoTargets(Exception):
    pass


class Month:
    """Everything a report needs for one target month."""

    def __init__(self, con, year: int, month: int):
        self.con, self.year, self.month = con, year, month
        self.tree: Node | None = load_tree(con, year, month)
        if self.tree is None:
            raise NoTargets(f"No targets loaded for {year}-{month:02d}. Load them on the Targets screen first.")
        self.alias = {r["alias_key"]: key(r["territory_label"]) for r in con.execute("SELECT * FROM territory_alias")}
        self.terr = {t.key: t for t in self.tree.territories()}
        self.modes = {r["territory_key"]: r["mode"] for r in con.execute("SELECT * FROM territory_selling_mode")}
        self.cat_in = {r["category_key"]: bool(r["include_in_competition"])
                       for r in con.execute("SELECT * FROM outlet_category_config")}
        lim = con.execute("SELECT * FROM competition_config WHERE id=1").fetchone()
        self.limits = {"van": (lim["van_sale_limit"], lim["van_bill_limit"]),
                       "pre": (lim["pre_sale_limit"], lim["pre_bill_limit"])}
        self.unmatched: set[str] = set()   # territory labels in the data that are not in the hierarchy
        self.notes: list[str] = []

    # -------------------------------------------------------------- helpers
    def resolve(self, tkey: str) -> str:
        return self.alias.get(tkey, tkey)

    def mode(self, t: Node) -> str:
        m = self.modes.get(t.key)
        if m is None:
            self.notes.append(f"No selling mode set for {t.label}; treated as Van. Set it in Settings.")
            return "van"
        return m

    def _note_unmatched(self, label, value):
        if label and value and key(label) and self.resolve(key(label)) not in self.terr:
            self.unmatched.add(label)

    # -------------------------------------------------------------- sales
    def daily_sales(self, d1: date, d2: date) -> dict:
        """{date: {tkey: [total, competition_sale]}} for territories in the hierarchy."""
        out: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        q = ("SELECT u.date d, l.territory t, l.territory_key k, l.outlet_classification c, SUM(l.sales_value) v "
             "FROM achievement_lines l JOIN achievement_uploads u ON u.id=l.upload_id "
             "WHERE u.date BETWEEN ? AND ? GROUP BY 1,2,3,4")
        for r in self.con.execute(q, (iso(d1), iso(d2))):
            k = self.resolve(r["k"])
            if k not in self.terr:
                self._note_unmatched(r["t"], r["v"] or 0)
                continue
            cell = out[date.fromisoformat(r["d"])][k]
            v = r["v"] or 0.0
            cell[0] += v
            if self.cat_in.get(key(r["c"]), True):
                cell[1] += v
        return out

    def sales_sum(self, d1: date, d2: date, comp=False) -> dict:
        tot: dict = defaultdict(float)
        for per in self.daily_sales(d1, d2).values():
            for k, v in per.items():
                tot[k] += v[1] if comp else v[0]
        return tot

    def loaded_dates(self, table: str, d1: date, d2: date) -> set:
        return {date.fromisoformat(r["date"]) for r in self.con.execute(
            f"SELECT date FROM {table} WHERE date BETWEEN ? AND ?", (iso(d1), iso(d2)))}

    # -------------------------------------------------------------- callage
    def callage_sum(self, d1: date, d2: date) -> dict:
        """{tkey: [calls_made, productive_calls]}"""
        out: dict = defaultdict(lambda: [0.0, 0.0])
        q = ("SELECT l.territory t, l.territory_key k, SUM(calls_made) c, SUM(productive_calls) p "
             "FROM callage_lines l JOIN callage_uploads u ON u.id=l.upload_id "
             "WHERE u.date BETWEEN ? AND ? GROUP BY 1,2")
        for r in self.con.execute(q, (iso(d1), iso(d2))):
            k = self.resolve(r["k"])
            out[k][0] += r["c"] or 0
            out[k][1] += r["p"] or 0
        return out

    def days_worked(self, d1: date, d2: date):
        """({tkey: days}, [(territory, rep_code, rep, days)]) — a worked day = PC > 0 that day."""
        q = ("SELECT l.territory t, l.territory_key k, COALESCE(NULLIF(l.sales_rep_code,''), l.sales_rep) rid, "
             "MAX(l.sales_rep) rep, COUNT(DISTINCT u.date) n "
             "FROM callage_lines l JOIN callage_uploads u ON u.id=l.upload_id "
             "WHERE u.date BETWEEN ? AND ? AND l.productive_calls > 0 GROUP BY 1,2,3 ORDER BY 1,3")
        per_t: dict = defaultdict(int)
        detail = []
        for r in self.con.execute(q, (iso(d1), iso(d2))):
            k = self.resolve(r["k"])
            per_t[k] += r["n"]
            detail.append((r["t"], r["rid"], r["rep"], r["n"]))
        return per_t, detail

    # -------------------------------------------------------------- pending + rep performance
    def pending(self, d: date) -> dict:
        """{tkey: [net_value, order_count]} for status Pending."""
        out: dict = defaultdict(lambda: [0.0, 0])
        q = ("SELECT l.territory t, l.territory_key k, SUM(net_amount) v, COUNT(DISTINCT so_number) n "
             "FROM pending_order_lines l JOIN pending_order_uploads u ON u.id=l.upload_id "
             "WHERE u.date=? AND LOWER(COALESCE(invoice_status,'pending'))='pending' GROUP BY 1,2")
        for r in self.con.execute(q, (iso(d),)):
            k = self.resolve(r["k"])
            if k not in self.terr:
                self._note_unmatched(r["t"], r["v"] or 0)
                continue
            out[k][0] += r["v"] or 0
            out[k][1] += r["n"] or 0
        return out

    def rep_perf(self) -> dict | None:
        up = self.con.execute("SELECT id FROM rep_perf_uploads WHERE year=? AND month=?",
                              (self.year, self.month)).fetchone()
        if not up:
            return None
        out: dict = defaultdict(lambda: [0.0, 0.0])
        for r in self.con.execute("SELECT territory, territory_key, productive_calls, achieved_sales "
                                  "FROM rep_perf_lines WHERE upload_id=?", (up["id"],)):
            k = self.resolve(r["territory_key"])
            if k not in self.terr:
                if (r["productive_calls"] or 0) or (r["achieved_sales"] or 0):
                    self._note_unmatched(r["territory"], r["achieved_sales"] or 0)
                continue
            out[k][0] += r["productive_calls"] or 0
            out[k][1] += r["achieved_sales"] or 0
        return out

    # -------------------------------------------------------------- calendar
    @property
    def first(self) -> date:
        return date(self.year, self.month, 1)

    def nowd(self) -> int:
        return workcal.nowd(self.con, self.year, self.month)

    def working(self) -> set:
        return set(workcal.working_dates(self.con, self.year, self.month))

    def setting(self, k, default=""):
        return get_setting(self.con, k, default)


def day_targets(m: Month, report_date: date, sales: dict) -> dict:
    """{tkey: (day_target, cumulative_day_target)} using the dynamic rule."""
    nowd = m.nowd()
    work = m.working()
    out = {}
    for k, t in m.terr.items():
        tgt = t.smmt_target if t.is_smmt else t.gt_target
        prior_ach, prior_days, cum, today = 0.0, 0, 0.0, 0.0
        d = m.first
        while d <= report_date:
            if d in work:
                remaining = max(nowd - prior_days, 1)
                dt = max(tgt - prior_ach, 0.0) / remaining if tgt else 0.0
                cum += dt
                if d == report_date:
                    today = dt
            s = sales.get(d, {}).get(k, [0.0, 0.0])[0]
            if d < report_date and s > 0:
                prior_ach += s
                if d in work:
                    prior_days += 1
            d += timedelta(days=1)
        out[k] = (today, cum)
    return out


def prev_period(report_date: date):
    """(same day last month, first day of last month)."""
    p = prev_month_same_day(report_date)
    return p, month_first(p)
