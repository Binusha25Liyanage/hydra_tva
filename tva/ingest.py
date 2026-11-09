"""Detect an uploaded file's type and store it in the database."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import parsers
from .hierarchy import save_tree
from .util import iso, key, now_iso

KIND_LABELS = {
    "sales": "Outlet Wise SKU Sales",
    "callage": "Callage Summary",
    "pending": "Pending Orders (Sales Order)",
    "rep_perf": "Rep Performance Report",
    "target": "Targets",
}


def detect(path) -> str:
    rows = parsers._rows(path)
    for r in rows[:40]:
        cells = {parsers._norm(c) for c in r if c is not None}
        if "HIERARCHY" in cells and any(c.startswith("MONTHLY TARGET") for c in cells):
            return "target"
        if {"LEVEL", "LABEL"} <= cells:
            return "target"
        if "SO NUMBER" in cells and "NET AMOUNT" in cells:
            return "pending"
        if "PRODUTIVE CALLS" in cells or ("ACHIEVED SALES" in cells and "PRODUCTIVE CALLS" in cells):
            return "rep_perf"
        if "PRODUCTIVE CALLS" in cells and "CALLS MADE" in cells:
            return "callage"
        if "TERRITORY" in cells and any(c.startswith("SALES VALUE") for c in cells):
            return "sales"
    raise parsers.ParseError("Unrecognised file. Expected an SFA Outlet Wise SKU Sales, Callage Summary, "
                             "Sales Order (pending), Rep Performance report, or a target file.")


def _exists(con, table, d: date):
    return con.execute(f"SELECT id FROM {table} WHERE date=?", (iso(d),)).fetchone()


def preview(con, path, forced_kind: str | None = None, report_date: date | None = None,
            year: int | None = None, month: int | None = None) -> dict:
    """Parse without saving. Returns a summary plus the parsed payload (kept by the caller)."""
    kind = forced_kind or detect(path)
    name = Path(path).name
    if kind == "sales":
        p = parsers.parse_sku_sales(path)
        dates = sorted(p["by_date"])
        info = [f"{iso(d)}: {len(p['by_date'][d]):,} lines, sales {sum(l['sales_value'] for l in p['by_date'][d]):,.2f}"
                for d in dates]
        conflicts = [iso(d) for d in dates if _exists(con, "achievement_uploads", d)]
    elif kind == "callage":
        p = parsers.parse_callage(path, report_date)
        dates = [p["date"]]
        info = [f"{iso(p['date'])}: {len(p['lines'])} reps, {sum(l['calls_made'] for l in p['lines']):,.0f} calls, "
                f"{sum(l['productive_calls'] for l in p['lines']):,.0f} productive"]
        conflicts = [iso(p["date"])] if _exists(con, "callage_uploads", p["date"]) else []
    elif kind == "pending":
        p = parsers.parse_pending(path, report_date)
        dates = [p["date"]]
        pend = [l for l in p["lines"] if (l["invoice_status"] or "").lower() == "pending"]
        info = [f"{iso(p['date'])}: {len(pend)} pending orders, net {sum(l['net_amount'] for l in pend):,.2f}"]
        conflicts = [iso(p["date"])] if _exists(con, "pending_order_uploads", p["date"]) else []
    elif kind == "rep_perf":
        p = parsers.parse_rep_performance(path)
        d = p["from"] or report_date
        y, m = (year, month) if year else ((d.year, d.month) if d else (None, None))
        if not y:
            raise parsers.ParseError("Could not tell which month this Rep Performance Report is for. Choose the month.")
        p["year"], p["month"] = y, m
        dates = []
        info = [f"{y}-{m:02d}: {len(p['lines'])} rows, PC {sum(l['productive_calls'] for l in p['lines']):,.0f}"]
        conflicts = [f"{y}-{m:02d}"] if con.execute("SELECT 1 FROM rep_perf_uploads WHERE year=? AND month=?",
                                                    (y, m)).fetchone() else []
    elif kind == "target":
        p = parsers.parse_target(path)
        if year:
            p["year"], p["month"] = year, month
        if not p["year"]:
            raise parsers.ParseError("Could not tell which month these targets are for. Choose the month.")
        terrs = []

        def walk(n):
            if n["level"] == 6:
                terrs.append(n)
            for c in n["children"]:
                walk(c)
        walk(p["root"])
        info = [f"{p['year']}-{p['month']:02d}: {len(terrs)} territories, GT {sum(t['gt'] for t in terrs):,.0f}, "
                f"SMMT {sum(t['smmt'] for t in terrs):,.0f}"]
        dates = []
        conflicts = [f"{p['year']}-{p['month']:02d}"] if con.execute(
            "SELECT 1 FROM target_uploads WHERE year=? AND month=?", (p["year"], p["month"])).fetchone() else []
        mn = parsers.MONTHS
        fname_months = [v for k, v in mn.items() if k in name.lower() or k[:3] in name.lower().replace("-", " ").split()]
        if fname_months and p["month"] not in fname_months:
            p["warnings"].append(f"The file name suggests a different month than {p['year']}-{p['month']:02d}.")
    else:
        raise parsers.ParseError(f"Unknown kind {kind}")
    return {"kind": kind, "kind_label": KIND_LABELS[kind], "file": name, "info": info,
            "warnings": p.get("warnings", []), "conflicts": conflicts, "dates": [iso(d) for d in dates],
            "payload": p}


def commit(con, pv: dict, replace: bool = False) -> list[str]:
    """Save a preview. Existing dates are skipped unless replace=True."""
    kind, p, name = pv["kind"], pv["payload"], pv["file"]
    done = []
    if kind == "sales":
        for d, lines in sorted(p["by_date"].items()):
            ex = _exists(con, "achievement_uploads", d)
            if ex and not replace:
                done.append(f"Skipped sales {iso(d)} (already loaded)")
                continue
            if ex:
                con.execute("DELETE FROM achievement_uploads WHERE id=?", (ex["id"],))
            cur = con.execute("INSERT INTO achievement_uploads(date, source_filename, uploaded_at, row_count, "
                              "total_sales_value) VALUES (?,?,?,?,?)",
                              (iso(d), name, now_iso(), len(lines), sum(l["sales_value"] for l in lines)))
            uid = cur.lastrowid
            cols = ["rsm", "asm", "ase", "territory", "distributor", "sales_rep_code", "sales_rep_type",
                    "sales_rep_model", "sales_rep", "outlet_code", "outlet_classification", "outlet", "invoice_no",
                    "category", "brand", "product_code", "product", "wholesale_price", "mrp", "total_units",
                    "sales_value", "sales_volume"]
            con.executemany(
                f"INSERT INTO achievement_lines(upload_id, territory_key, {','.join(cols)}) "
                f"VALUES (?, ?, {','.join('?' * len(cols))})",
                [(uid, key(l["territory"]), *[l[c] for c in cols]) for l in lines])
            _register_categories(con, lines)
            done.append(f"Saved sales {iso(d)} ({len(lines):,} lines)")
    elif kind in ("callage", "pending"):
        up_t, ln_t = ("callage_uploads", "callage_lines") if kind == "callage" else \
            ("pending_order_uploads", "pending_order_lines")
        d = p["date"]
        ex = _exists(con, up_t, d)
        if ex and not replace:
            return [f"Skipped {KIND_LABELS[kind]} {iso(d)} (already loaded)"]
        if ex:
            con.execute(f"DELETE FROM {up_t} WHERE id=?", (ex["id"],))
        cur = con.execute(f"INSERT INTO {up_t}(date, source_filename, uploaded_at, row_count) VALUES (?,?,?,?)",
                          (iso(d), name, now_iso(), len(p["lines"])))
        uid = cur.lastrowid
        cols = list(p["lines"][0].keys()) if p["lines"] else []
        if cols:
            con.executemany(
                f"INSERT INTO {ln_t}(upload_id, territory_key, {','.join(cols)}) VALUES (?, ?, {','.join('?' * len(cols))})",
                [(uid, key(l["territory"]), *[l[c] for c in cols]) for l in p["lines"]])
        done.append(f"Saved {KIND_LABELS[kind]} {iso(d)} ({len(p['lines'])} rows)")
    elif kind == "rep_perf":
        ex = con.execute("SELECT id FROM rep_perf_uploads WHERE year=? AND month=?", (p["year"], p["month"])).fetchone()
        if ex and not replace:
            return [f"Skipped Rep Performance {p['year']}-{p['month']:02d} (already loaded)"]
        if ex:
            con.execute("DELETE FROM rep_perf_uploads WHERE id=?", (ex["id"],))
        cur = con.execute("INSERT INTO rep_perf_uploads(year, month, source_filename, uploaded_at, row_count) "
                          "VALUES (?,?,?,?,?)", (p["year"], p["month"], name, now_iso(), len(p["lines"])))
        uid = cur.lastrowid
        con.executemany("INSERT INTO rep_perf_lines(upload_id, territory, territory_key, sales_rep, productive_calls, "
                        "achieved_sales, total_bills, bill_days) VALUES (?,?,?,?,?,?,?,?)",
                        [(uid, l["territory"], key(l["territory"]), l["sales_rep"], l["productive_calls"],
                          l["achieved_sales"], l["total_bills"], l["bill_days"]) for l in p["lines"]])
        done.append(f"Saved Rep Performance {p['year']}-{p['month']:02d}")
    elif kind == "target":
        ex = con.execute("SELECT 1 FROM target_uploads WHERE year=? AND month=?", (p["year"], p["month"])).fetchone()
        if ex and not replace:
            return [f"Skipped targets {p['year']}-{p['month']:02d} (already loaded)"]
        save_tree(con, p["year"], p["month"], p["root"], name, p["source"])
        done.append(f"Saved targets {p['year']}-{p['month']:02d}")
    con.commit()
    return done


def _register_categories(con, lines):
    """New outlet categories are added as 'included in competition' so they can be reviewed in Settings."""
    seen = {l["outlet_classification"] for l in lines if l["outlet_classification"]}
    for c in seen:
        con.execute("INSERT OR IGNORE INTO outlet_category_config VALUES (?,?,?,?,?)",
                    (key(c), c, None, 1, now_iso()))
