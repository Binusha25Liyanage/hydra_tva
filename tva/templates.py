"""Editable targets template (Level | Label | GT Target | SMMT Target | Distributor | CSR)."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .hierarchy import LEVEL_NAMES, Node, load_tree


def write_template(con, year: int, month: int, path: str | Path) -> str:
    root = load_tree(con, year, month)
    wb = Workbook()
    ws = wb.active
    ws.title = "Targets"
    ws.append(["Year", year, "Month", month])
    ws.append(["Edit GT/SMMT targets on Territory rows only; parent totals are calculated. "
               "To change the hierarchy, add/remove rows and set each row's Parent (parents above their children)."])
    ws.append([])
    ws.append(["Level", "Label", "GT Target", "SMMT Target", "Distributor", "CSR", "Parent"])
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")

    def walk(n: Node):
        ws.append([LEVEL_NAMES[n.level], n.label, n.gt_target if n.is_territory else None,
                   n.smmt_target if n.is_territory else None, n.distributor, n.csr,
                   n.parent.label if n.parent else None])
        r = ws.max_row
        ws.cell(r, 2).alignment = Alignment(indent=n.level)
        if not n.is_territory:
            for c in ws[r]:
                c.font = Font(bold=True)
        for c in (3, 4):
            ws.cell(r, c).number_format = "#,##0"
        for ch in n.children:
            walk(ch)

    if root is not None:
        walk(root)
    else:
        ws.append(["National", "National"])
    for col, w in zip("ABCDEFG", (11, 40, 14, 14, 30, 30, 34)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return str(path)
