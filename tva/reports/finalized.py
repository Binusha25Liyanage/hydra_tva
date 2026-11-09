"""Month-end Finalized Target vs Achievement (GT + Total PC + AVG PC + previous month comparison).

Percentages, variances, averages and subtotals are Excel formulas so the file can be audited.
Extra sheets: 'Days Worked' (rep breakdown) and 'Reconciliation' (database vs SFA Rep Performance).
"""
from __future__ import annotations

from datetime import date

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from ..engine import Month, prev_period
from ..util import iso, long_date
from .common import BORDER, DEC1, INT, MONEY, PCT, hier_kind, new_sheet, save, title_text, write_table

FIRST_DATA_ROW = 4


def finalized(m: Month, d1: date, d2: date, folder: str) -> dict:
    perf = m.rep_perf()
    notes = []
    if perf is None:
        notes.append(f"No Rep Performance Report loaded for {m.year}-{m.month:02d}: Total PC and AVG PC are 0.")
        perf = {}
    p_day, p_first = prev_period(d2)
    cur = m.sales_sum(d1, d2)
    prv = m.sales_sum(p_first, p_day)
    days, detail = m.days_worked(d1, d2)
    smmt_pc_up = m.setting("include_smmt_pc_in_subtotals", "0") == "1"
    with_nat = m.setting("include_national_in_finalized", "1") == "1"
    prev_loaded = m.loaded_dates("achievement_uploads", p_first, p_day)
    missing_prev = (p_day - p_first).days + 1 - len(prev_loaded)
    if missing_prev > 4:
        notes.append(f"Previous month has sales loaded for only {len(prev_loaded)} days "
                     f"({iso(p_first)} to {iso(p_day)}), so column H may be incomplete.")

    order = [n for n in m.tree.postorder() if with_nat or n.level != 0]
    rownum = {n.id: FIRST_DATA_ROW + i for i, n in enumerate(order)}

    def child_sum(n, col, smmt_ok=True):
        refs = []
        for c in n.children:
            if c.id not in rownum:
                continue
            if not smmt_ok and c.is_territory and c.is_smmt:
                continue
            refs.append(f"{col}{rownum[c.id]}")
        return "=" + "+".join(refs) if refs else 0

    rows = []
    for n in order:
        if n.is_territory:
            gt = not n.is_smmt
            pc = perf.get(n.key, [0, 0])[0]
            b, c, h = (n.gt_target, cur.get(n.key, 0.0), prv.get(n.key, 0.0)) if gt else (0, 0, 0)
            f, j = pc, days.get(n.key, 0)
        else:
            b, c, h = child_sum(n, "B"), child_sum(n, "C"), child_sum(n, "H")
            f, j = child_sum(n, "F", smmt_pc_up), child_sum(n, "J", smmt_pc_up)
        rows.append({"kind": hier_kind(n), "cells": [
            n.row_label(), b, c, "=IF(B{r}=0,0,C{r}/B{r})", "=C{r}-B{r}", f, "=IF(J{r}=0,0,F{r}/J{r})", h,
            "=C{r}-H{r}", j]})

    cols = [("Territory", 40, None, "left"), ("Monthly Target", 16, MONEY, "right"),
            (f"Achievement GT til {long_date(d2)}", 16, MONEY, "right"), ("GT Tgt Vs Achive %", 10, PCT, "right"),
            ("GT Variance", 16, MONEY, "right"), ("Total PC Count", 10, INT, "right"),
            ("AVG PC Count", 10, DEC1, "right"), (f"Achievement GT til {long_date(p_day)}", 16, MONEY, "right"),
            (f"compare with {d2.strftime('%b')} Month", 16, MONEY, "right"), ("Days Worked (helper)", 10, INT, "right")]
    title = title_text(m.con, "Finalized Target vs Achievement", f"{d2.strftime('%B %Y')}  ({iso(d1)} to {iso(d2)})")
    wb, ws = new_sheet(title, "Target vs Achievement")
    write_table(ws, title, "", cols, rows, header_row=3, header_height=45)
    ws["B2"] = date(d2.year, d2.month, 1)
    ws.merge_cells("B2:F2")
    ws["H2"] = p_first
    for a in ("B2", "H2"):
        ws[a].number_format = "d-mmm-yy"
        ws[a].font = Font(bold=True)
        ws[a].alignment = Alignment(horizontal="center")

    # Days Worked helper sheet
    dw = wb.create_sheet("Days Worked")
    dw.append(["Territory", "Rep code", "Sales rep", "Days worked (PC > 0)"])
    for t, rid, rep, n in detail:
        dw.append([t, rid, rep, n])
    _plain_style(dw, [22, 12, 38, 12])

    # Reconciliation sheet
    tol = float(m.setting("recon_tolerance", "1000") or 1000)
    rc = wb.create_sheet("Reconciliation")
    rc.append(["Territory", "Achievement (daily SKU sales)", "SFA ACHIEVED SALES (Rep Performance)", "Difference",
               f"Over tolerance ({tol:,.0f})"])
    flagged = 0
    for t in m.tree.territories():
        a = cur.get(t.key, 0.0)
        s = perf.get(t.key, [0, 0])[1]
        if not a and not s:
            continue
        over = abs(a - s) > tol
        flagged += over
        rc.append([t.display, a, s, a - s, "YES" if over else ""])
    _plain_style(rc, [22, 18, 18, 16, 12], money_cols=(2, 3, 4))
    if notes:
        n_ws = wb.create_sheet("Notes")
        for x in notes:
            n_ws.append([x])
        n_ws.column_dimensions["A"].width = 110

    path = save(wb, folder, f"Finalized_Target_vs_Achievement_{iso(d1)}_to_{iso(d2)}.xlsx")
    return {"report": "Finalized Target vs Achievement", "path": path,
            "summary": f"{flagged} territories differ from SFA by more than {tol:,.0f}", "notes": notes}


def _plain_style(ws, widths, money_cols=()):
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
        h = ws.cell(1, j)
        h.font = Font(bold=True, color="FFFFFF")
        from .common import HEADER_FILL
        h.fill = HEADER_FILL
        h.alignment = Alignment(wrap_text=True, vertical="center")
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(widths)):
        for c in row:
            c.border = BORDER
            if c.row > 1 and c.column in money_cols:
                c.number_format = MONEY
    ws.freeze_panes = "A2"
