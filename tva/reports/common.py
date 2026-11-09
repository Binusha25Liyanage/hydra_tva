"""Excel styling shared by all reports (Famulus look: blue header, thin borders, bold subtotals)."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="305496")
SUB_FILL = PatternFill("solid", fgColor="DDEBF7")
NAT_FILL = PatternFill("solid", fgColor="BDD7EE")
YES_FILL = PatternFill("solid", fgColor="C6EFCE")
HELP_FILL = PatternFill("solid", fgColor="808080")
THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MONEY = "#,##0.00"
INT = "#,##0"
PCT = "0.0%"
DEC1 = "0.0"


def title_text(con, name: str, rest: str) -> str:
    from ..db import get_setting
    prefix = get_setting(con, "title_prefix", "").strip()
    return f"{prefix} — {name} — {rest}" if prefix else f"{name} — {rest}"


def new_sheet(title: str, sheet_name: str):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    return wb, ws


def write_table(ws, title: str, subtitle: str, columns: list[tuple], rows: list[dict],
                header_row: int = 4, header_height: float = 30, subtitle_row: int = 2):
    """columns: [(header, width, number_format, align)]; rows: [{'kind': terr|sub|nat|total|plain, 'cells': [...],
    'fills': {col_index: fill}}]. Values starting with '=' are written as formulas."""
    ncol = len(columns)
    last = get_column_letter(ncol)
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(f"A1:{last}1")
    if subtitle:
        c = ws.cell(subtitle_row, 1, subtitle)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.merge_cells(start_row=subtitle_row, start_column=1, end_row=subtitle_row, end_column=ncol)
        if len(subtitle) > 160:
            ws.row_dimensions[subtitle_row].height = 15 * (1 + len(subtitle) // 220)
    for j, (h, w, fmt, al) in enumerate(columns, start=1):
        c = ws.cell(header_row, j, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL if not h.endswith("(helper)") else HELP_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal=al or "left", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.row_dimensions[header_row].height = header_height
    r = header_row
    for row in rows:
        r += 1
        kind = row.get("kind", "terr")
        bold = kind in ("sub", "nat", "total")
        fill = SUB_FILL if kind == "sub" else NAT_FILL if kind in ("nat", "total") else None
        for j, v in enumerate(row["cells"], start=1):
            if isinstance(v, str) and "{r}" in v:
                v = v.replace("{r}", str(r))
            c = ws.cell(r, j, v)
            h, w, fmt, al = columns[j - 1]
            c.border = BORDER
            c.number_format = fmt or "General"
            c.alignment = Alignment(horizontal=al or ("left" if j == 1 else "right"))
            if bold:
                c.font = Font(bold=True)
            f = row.get("fills", {}).get(j, fill)
            if f is not None:
                c.fill = f
    ws.freeze_panes = ws.cell(header_row + 1, 2 if ncol > 3 else 1)
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row}:{header_row}"
    return r


def hier_kind(node) -> str:
    return "nat" if node.level == 0 else "terr" if node.is_territory else "sub"


def div(a, b):
    return a / b if b else 0.0


def save(wb, folder: str | Path, filename: str) -> str:
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    out = p / filename
    try:
        wb.save(out)
    except PermissionError:
        raise PermissionError(f"{out.name} is open in Excel. Close it and generate again.")
    return str(out)
