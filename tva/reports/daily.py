"""The daily report pack: Target vs Achievement, Day Report, Sales Comparison, Daily Winners, Pending Order Value."""
from __future__ import annotations

from datetime import date

from .. import workcal
from ..engine import Month, day_targets, prev_period
from ..util import iso, long_date, plain_date, short_date, signed
from .common import (INT, MONEY, PCT, YES_FILL, div, hier_kind, new_sheet, save, title_text, write_table)


def _range_text(d1: date, d2: date) -> str:
    if d1.month == d2.month:
        return f"{d1.day}-{d2.day} {d2.strftime('%B %Y')}" if d1 != d2 else plain_date(d2)
    return f"{plain_date(d1)} to {plain_date(d2)}"


# ------------------------------------------------------------------ 1. Target vs Achievement (AI file)
def target_vs_achievement(m: Month, d1: date, d2: date, folder: str) -> dict:
    sales = m.sales_sum(d1, d2)
    for t in m.tree.territories():
        a = sales.get(t.key, 0.0)
        t.m = {"gt_t": 0.0 if t.is_smmt else t.gt_target, "gt_a": 0.0 if t.is_smmt else a,
               "sm_t": t.smmt_target if t.is_smmt else 0.0, "sm_a": a if t.is_smmt else 0.0}
    m.tree.rollup(["gt_t", "gt_a", "sm_t", "sm_a"])
    rows = []
    for n in m.tree.postorder():
        x = n.m
        rows.append({"kind": hier_kind(n), "cells": [
            n.row_label(), x["gt_t"], x["gt_a"], div(x["gt_a"], x["gt_t"]), x["gt_a"] - x["gt_t"],
            x["sm_t"], x["sm_a"], div(x["sm_a"], x["sm_t"]), (x["gt_a"] + x["sm_a"]) - (x["gt_t"] + x["sm_t"])]})
    nat = m.tree.m
    till = long_date(d2)
    cols = [("Hierarchy", 40, None, "left"), ("Monthly Target", 16, MONEY, "right"),
            (f"Achievement GT til {till}", 16, MONEY, "right"), ("GT Tgt Vs Achive %", 9, PCT, "right"),
            ("GT Variance", 17, MONEY, "right"), ("SMMT Target", 15, MONEY, "right"),
            (f"Achievement SMMT till {till}", 15, MONEY, "right"), ("SMMT Tgt Vs Achive %", 9, PCT, "right"),
            ("Balance to be achieved", 17, MONEY, "right")]
    title = title_text(m.con, "Target vs Achievement Report",
                       f"{d2.strftime('%B %Y')}  ({iso(d1)} to {iso(d2)})")
    sub = (f"GT Target: {nat['gt_t']:,.0f}   |   GT Achievement: {nat['gt_a']:,.0f}   |   "
           f"SMMT Target: {nat['sm_t']:,.0f}   |   SMMT Achievement: {nat['sm_a']:,.0f}")
    wb, ws = new_sheet(title, "Comparison")
    write_table(ws, title, sub, cols, rows, header_height=42)
    path = save(wb, folder, f"Target_vs_Achievement_{iso(d1)}_to_{iso(d2)}.xlsx")
    return {"report": "Target vs Achievement", "path": path, "summary": sub}


# ------------------------------------------------------------------ 2. Day Report
def day_report(m: Month, d: date, folder: str) -> dict:
    p_day, p_first = prev_period(d)
    month_sales = m.daily_sales(m.first, d)
    prev_sales = m.daily_sales(p_first, p_day)
    dts = day_targets(m, d, month_sales)
    call_d = m.callage_sum(d, d)
    call_cum = m.callage_sum(m.first, d)
    for t in m.tree.territories():
        k = t.key
        today = month_sales.get(d, {}).get(k, [0.0, 0.0])[0]
        cum = sum(per.get(k, [0.0, 0.0])[0] for per in month_sales.values())
        prev_day = prev_sales.get(p_day, {}).get(k, [0.0, 0.0])[0]
        prev_cum = sum(per.get(k, [0.0, 0.0])[0] for per in prev_sales.values())
        dt, cdt = dts[k]
        gt = not t.is_smmt
        t.m = {
            "tgt": t.gt_target if gt else 0.0, "dt": dt if gt else 0.0, "da": today if gt else 0.0,
            "lm": prev_day if gt else 0.0, "calls": call_d.get(k, [0, 0])[0], "pc": call_d.get(k, [0, 0])[1],
            "sdt": 0.0 if gt else dt, "sda": 0.0 if gt else today,
            "cdt": cdt if gt else 0.0, "ca": cum if gt else 0.0, "pca": prev_cum if gt else 0.0,
            "cpc": call_cum.get(k, [0, 0])[1]}
    add = ["tgt", "dt", "da", "lm", "calls", "pc", "sdt", "sda", "cdt", "ca", "pca", "cpc"]
    m.tree.rollup(add)
    rows = []
    for n in m.tree.postorder():
        x = n.m
        rows.append({"kind": hier_kind(n), "cells": [
            n.row_label(), x["tgt"], x["dt"], x["da"], div(x["da"], x["dt"]), x["da"] - x["dt"], x["lm"],
            x["da"] - x["lm"], x["calls"], x["pc"], x["sdt"], x["sda"], div(x["sda"], x["sdt"]), x["sda"] - x["sdt"],
            x["cdt"], x["ca"], x["pca"], x["ca"] - x["pca"], div(x["ca"], x["cdt"]), x["ca"] - x["cdt"], x["cpc"]]})
    nat = m.tree.m
    lm_txt = plain_date(p_day)
    cols = [("Territory", 40, None, "left"), ("Monthly Target", 16, MONEY, "right"),
            ("Day Target GT", 14, MONEY, "right"), ("Day Achievement GT", 14, MONEY, "right"),
            ("Achievement %", 13, PCT, "right"), ("Variance from Day Target", 15, MONEY, "right"),
            (f"Last Month Same Day Sale GT ({lm_txt})", 14, MONEY, "right"),
            ("Day Variance vs Last Month", 13, MONEY, "right"), ("Calls Made", 7, INT, "right"),
            ("Productive Calls", 12, INT, "right"), ("SMMT Day Target", 12, MONEY, "right"),
            ("SMMT Day Achievement", 13, MONEY, "right"), ("Achievement %", 13, PCT, "right"),
            ("SMMT Variance", 13, MONEY, "right"), ("Cumulative Day Target GT", 15, MONEY, "right"),
            ("Cumulative Achievement GT", 14, MONEY, "right"),
            ("Prev. Month Same-Date Achievement GT", 14, MONEY, "right"),
            ("Variance vs Prev. Month", 13, MONEY, "right"), ("Cumulative Achievement %", 13, PCT, "right"),
            ("Cumulative Variance", 15, MONEY, "right"), ("Cumulative Productive Calls", 12, INT, "right")]
    title = title_text(m.con, "Day-wise Target vs Achievement Report", f"{iso(d)} ({d.strftime('%B %Y')})")
    sub = (f"Working Days (NOWD): {m.nowd()}   |   Day {d.day} of the month   |   "
           f"GT Day Target: {nat['dt']:,.0f}   |   GT Day Achievement: {nat['da']:,.0f}   |   "
           f"SMMT Day Target: {nat['sdt']:,.0f}   |   SMMT Day Achievement: {nat['sda']:,.0f}   |   "
           f"Last Month Same Day (GT, {lm_txt}): {nat['lm']:,.0f}   |   Day Variance: {signed(nat['da'] - nat['lm'])}   |   "
           f"Prev. Month (GT, {_range_text(p_first, p_day)}): {nat['pca']:,.0f}   |   "
           f"Variance vs Prev. Month: {signed(nat['ca'] - nat['pca'])}")
    if not workcal.is_working(m.con, d):
        sub = "NON-WORKING DAY (day target 0)   |   " + sub
    wb, ws = new_sheet(title, "Day Report")
    write_table(ws, title, sub, cols, rows)
    path = save(wb, folder, f"Day_Report_{iso(d)}.xlsx")
    return {"report": "Day Report", "path": path,
            "summary": f"GT day {nat['da']:,.0f} vs target {nat['dt']:,.0f}; cumulative {nat['ca']:,.0f}"}


# ------------------------------------------------------------------ 3. Sales Comparison
def sales_comparison(m: Month, d: date, folder: str) -> dict:
    p_day, p_first = prev_period(d)
    cur = m.daily_sales(m.first, d)
    prv = m.daily_sales(p_first, p_day)
    for t in m.tree.territories():
        k = t.key
        t.m = {"d": cur.get(d, {}).get(k, [0, 0])[0], "pd": prv.get(p_day, {}).get(k, [0, 0])[0],
               "c": sum(x.get(k, [0, 0])[0] for x in cur.values()),
               "pc": sum(x.get(k, [0, 0])[0] for x in prv.values())}
    m.tree.rollup(["d", "pd", "c", "pc"])
    rows = [{"kind": hier_kind(n), "cells": [n.row_label(), n.m["d"], n.m["pd"], n.m["d"] - n.m["pd"], n.m["c"],
                                             n.m["pc"], n.m["c"] - n.m["pc"]]} for n in m.tree.postorder()]
    nat = m.tree.m
    cols = [("Territory", 40, None, "left"), (f"Sale on {short_date(d)}", 14, MONEY, "right"),
            (f"Sale on {short_date(p_day)}", 14, MONEY, "right"), ("Sales Variance", 13, MONEY, "right"),
            (f"Cumulative Sale ({_range_text(m.first, d).replace(' ' + d.strftime('%B %Y'), ' ' + d.strftime('%b %Y'))})", 14, MONEY, "right"),
            (f"Cumulative Sale ({_range_text(p_first, p_day).replace(' ' + p_day.strftime('%B %Y'), ' ' + p_day.strftime('%b %Y'))})", 14, MONEY, "right"),
            ("Cumulative Sales Variance", 15, MONEY, "right")]
    title = title_text(m.con, "Sales Comparison", f"{short_date(d)} vs {short_date(p_day)}")
    sub = (f"Day Sale: {nat['d']:,.0f} vs {nat['pd']:,.0f} (Variance {signed(nat['d'] - nat['pd'])})   |   "
           f"Cumulative: {nat['c']:,.0f} vs {nat['pc']:,.0f} (Variance {signed(nat['c'] - nat['pc'])})   |   "
           f"Hierarchy: {d.strftime('%B %Y')} target")
    wb, ws = new_sheet(title, "Sales Comparison")
    write_table(ws, title, sub, cols, rows)
    path = save(wb, folder, f"Sales_Comparison_{iso(d)}_vs_{iso(p_day)}.xlsx")
    return {"report": "Sales Comparison", "path": path, "summary": sub.split("   |   Hierarchy")[0]}


# ------------------------------------------------------------------ 4. Daily Winners
def daily_winners(m: Month, d: date, folder: str, force: bool = False) -> dict:
    comp = workcal.competition_info(m.con, d)
    if not comp["is_competition"] and not force:
        return {"report": "Daily Winners", "path": None, "skipped": True,
                "summary": f"{iso(d)} is not a competition day in the calendar, so no winners report."}
    s = m.daily_sales(d, d).get(d, {})
    calls = m.callage_sum(d, d)
    lines = []
    for t in m.tree.territories():
        tot, cs = s.get(t.key, [0.0, 0.0])
        if tot <= 0 or t.is_smmt:
            continue
        mode = m.mode(t)
        sale_lim, bill_lim = m.limits[mode]
        bills = calls.get(t.key, [0, 0])[1]
        win = cs >= sale_lim and bills >= bill_lim
        anc = {lv: t.ancestor(lv) for lv in (1, 2, 3, 4, 5)}
        lines.append((cs, [anc[1].display if anc[1] else None, anc[2].display if anc[2] else None,
                           anc[3].display if anc[3] else None, anc[4].display if anc[4] else None,
                           anc[5].display if anc[5] else None, t.display,
                           "Pre Selling" if mode == "pre" else "Van Selling", cs, tot - cs, bills, sale_lim, bill_lim,
                           "Yes" if win else "No"]))
    lines.sort(key=lambda x: -x[0])
    winners = sum(1 for _, c in lines if c[-1] == "Yes")
    rows = [{"kind": "plain", "cells": c, "fills": {13: YES_FILL} if c[-1] == "Yes" else {}} for _, c in lines]
    cols = [("Region", 20, None, "left"), ("RSM", 23, None, "left"), ("ASM", 25, None, "left"),
            ("DTM", 23, None, "left"), ("ASE", 22, None, "left"), ("Territory", 17, None, "left"),
            ("Selling Mode", 12, None, "left"), ("Sale Value", 11, INT, "right"),
            ("SMMT Outlet Sale", 9, INT, "right"), ("Bill Count", 7, INT, "right"),
            ("Sale Limit", 9, INT, "right"), ("Bill Limit", 7, INT, "right"), ("Winner", 8, None, "center")]
    title = title_text(m.con, "Daily Winners", iso(d))
    rnd = (f"   |   Competition Round {comp['round']}: day {comp['round_day']} of {comp['round_days']}"
           if comp["is_competition"] else "   |   Not a competition day")
    sub = f"Territories with a sale today: {len(lines)}   |   Winners: {winners}{rnd}"
    wb, ws = new_sheet(title, "Daily Winners")
    write_table(ws, title, sub, cols, rows, header_height=32)
    path = save(wb, folder, f"Daily_Winners_{iso(d)}.xlsx")
    return {"report": "Daily Winners", "path": path, "summary": f"{winners} winners of {len(lines)} territories"}


# ------------------------------------------------------------------ 5. Pending Order Value
def pending_order_value(m: Month, d: date, folder: str) -> dict:
    if not m.loaded_dates("pending_order_uploads", d, d):
        return {"report": "Pending Order Value", "path": None, "skipped": True,
                "summary": f"No pending order report loaded for {iso(d)}."}
    po = m.pending(d)
    lines = []
    for t in m.tree.territories():
        if t.key in po and m.mode(t) == "pre" and po[t.key][1] > 0:
            lines.append((t.display, po[t.key][0], po[t.key][1]))
    lines.sort(key=lambda x: -x[1])
    tv, tn = sum(x[1] for x in lines), sum(x[2] for x in lines)
    rows = [{"kind": "plain", "cells": list(x)} for x in lines]
    rows.append({"kind": "total", "cells": ["TOTAL", tv, tn]})
    cols = [("Territory", 16, None, "left"), ("Order Value", 13, INT, "right"), ("Order Count", 9, INT, "right")]
    title = title_text(m.con, "Preselling Pending Order Value", iso(d))
    sub = f"Pre Selling territories with pending orders: {len(lines)}   |   Total Pending Order Value: {tv:,.0f}"
    wb, ws = new_sheet(title, "Pending Order Value")
    write_table(ws, title, sub, cols, rows, header_height=32)
    ws.row_dimensions[2].height = 30
    path = save(wb, folder, f"Pending_Order_Value_{iso(d)}.xlsx")
    return {"report": "Pending Order Value", "path": path, "summary": sub}
