"""Readers for the SFA exports and target files.

Every reader finds its header row by column names (the SFA banner rows above it may change),
skips the unlabelled totals row, and returns plain dicts plus a list of warnings.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import openpyxl

from .util import clean, key, num, parse_date


class ParseError(Exception):
    pass


def _rows(path):
    p = Path(path)
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ParseError(f"{p.name}: only .xlsx files are supported (open it in Excel and Save As .xlsx).")
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb["Export"] if "Export" in wb.sheetnames else wb.worksheets[0]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().upper()


def _find_header(rows, required, search=40):
    req = [_norm(r) for r in required]
    for i, r in enumerate(rows[:search]):
        cells = [_norm(c) for c in r]
        if all(any(c == q or c.startswith(q) for c in cells) for q in req):
            return i, cells
    raise ParseError("Could not find the header row (looked for: " + ", ".join(required) + "). Is this the right report?")


def _col(cells, *names, prefix=False):
    for n in names:
        n = _norm(n)
        for i, c in enumerate(cells):
            if c == n or (prefix and c.startswith(n)):
                return i
    return None


def _meta(rows, upto):
    """Read 'From Date' / 'To Date' style label-value pairs from the banner rows."""
    meta = {}
    for r in rows[:upto]:
        for i in range(len(r) - 1):
            lab = _norm(r[i])
            if lab in ("FROM DATE", "DATE FROM", "TO DATE", "DATE TO", "INVOICE STATUS", "REPORT TYPE"):
                meta[lab] = r[i + 1]
    d1 = parse_date(meta.get("FROM DATE") or meta.get("DATE FROM"))
    d2 = parse_date(meta.get("TO DATE") or meta.get("DATE TO"))
    return d1, d2, meta


def _get(r, i):
    return r[i] if i is not None and i < len(r) else None


# ------------------------------------------------------------------ Outlet Wise SKU Sales
def parse_sku_sales(path) -> dict:
    rows = _rows(path)
    hi, cells = _find_header(rows, ["TERRITORY", "SALES VALUE"])
    d1, d2, _ = _meta(rows, hi)
    c = {
        "rsm": _col(cells, "RSM"), "asm": _col(cells, "ASM"), "ase": _col(cells, "ASE"),
        "territory": _col(cells, "TERRITORY"), "distributor": _col(cells, "DISTRIBUTOR"),
        "sales_rep_code": _col(cells, "SALES REP CODE"), "sales_rep_type": _col(cells, "SALES REP TYPE"),
        "sales_rep_model": _col(cells, "SALES REP MODEL"), "sales_rep": _col(cells, "SALES REP"),
        "outlet_code": _col(cells, "OUTLET CODE"), "outlet_classification": _col(cells, "OUTLET CLASSIFICATION"),
        "outlet": _col(cells, "OUTLET"), "invoice_no": _col(cells, "INVOICE / SO NUMBER", "INVOICE NUMBER", prefix=True),
        "category": _col(cells, "CATEGORY"), "brand": _col(cells, "BRAND"),
        "product_code": _col(cells, "PRODUCT CODE"), "product": _col(cells, "PRODUCT"),
        "wholesale_price": _col(cells, "WHOLESALE PRICE"), "mrp": _col(cells, "MRP"),
        "total_units": _col(cells, "TOTAL UNITS"), "sales_value": _col(cells, "SALES VALUE", prefix=True),
        "sales_volume": _col(cells, "SALES VOLUME", prefix=True),
    }
    c_date = _col(cells, "INVOICE / TRANSACTION DATE", "INVOICE DATE", "TRANSACTION DATE", prefix=True)
    warnings, by_date = [], defaultdict(list)
    file_total = None
    for rn, r in enumerate(rows[hi + 1:], start=hi + 2):
        terr = clean(_get(r, c["territory"]))
        if not terr:
            sv = _get(r, c["sales_value"])
            if sv not in (None, ""):
                file_total = num(sv)  # unlabelled totals row
            continue
        d = parse_date(_get(r, c_date)) if c_date is not None else None
        if d is None:
            d = d1 if (d1 and d1 == d2) else None
        if d is None:
            raise ParseError(f"Row {rn}: no invoice date, and the report covers more than one day.")
        line = {k: (_get(r, i) if i is not None else None) for k, i in c.items()}
        for k in ("wholesale_price", "mrp", "total_units", "sales_value", "sales_volume"):
            line[k] = num(line[k])
        for k in ("rsm", "asm", "ase", "distributor", "sales_rep_code", "sales_rep_type", "sales_rep_model",
                  "sales_rep", "outlet_code", "outlet_classification", "outlet", "invoice_no", "category",
                  "brand", "product_code", "product"):
            line[k] = clean(line[k]) if line[k] is not None else None
        line["territory"] = terr
        by_date[d].append(line)
    total = sum(l["sales_value"] for ls in by_date.values() for l in ls)
    if file_total is not None and abs(file_total - total) > 1:
        warnings.append(f"Line total {total:,.2f} differs from the file's totals row {file_total:,.2f}.")
    if not by_date:
        raise ParseError("No sales lines found.")
    return {"kind": "sales", "by_date": dict(by_date), "from": d1, "to": d2, "total": total, "warnings": warnings}


# ------------------------------------------------------------------ Callage Summary
def parse_callage(path, report_date: date | None = None) -> dict:
    rows = _rows(path)
    hi, cells = _find_header(rows, ["TERRITORY", "PRODUCTIVE CALLS"])
    d1, d2, _ = _meta(rows, hi)
    d = report_date or (d1 if d1 and d1 == d2 else None)
    if d is None:
        raise ParseError("The Callage report must cover a single day (From Date = To Date). "
                         f"This file covers {d1} to {d2}.")
    cols = {
        "rsm": _col(cells, "RSM"), "asm": _col(cells, "ASM"), "ase": _col(cells, "ASE"),
        "territory": _col(cells, "TERRITORY"), "sales_rep_code": _col(cells, "SALES REP CODE"),
        "sales_rep": _col(cells, "SALES REP"), "itinerary_planned_days": _col(cells, "ITINERARY PLANNED DAYS"),
        "nod": _col(cells, "NOD"), "schedule_calls": _col(cells, "SCHEDULE CALLS"),
        "calls_made": _col(cells, "CALLS MADE"), "productive_calls": _col(cells, "PRODUCTIVE CALLS"),
        "unproductive_calls": _col(cells, "UNPRODUCTIVE CALLS"), "missed_calls": _col(cells, "MISSED CALLS"),
        "productivity_pct": _col(cells, "PRODUCTIVITY %"), "unplanned_visits": _col(cells, "UNPLANNED VISIT COUNT"),
    }
    lines = []
    for r in rows[hi + 1:]:
        terr = clean(_get(r, cols["territory"]))
        if not terr:
            continue
        ln = {k: _get(r, i) for k, i in cols.items()}
        for k in ("itinerary_planned_days", "nod", "schedule_calls", "calls_made", "productive_calls",
                  "unproductive_calls", "missed_calls", "productivity_pct", "unplanned_visits"):
            ln[k] = num(ln[k])
        for k in ("rsm", "asm", "ase", "sales_rep_code", "sales_rep"):
            ln[k] = clean(ln[k]) if ln[k] is not None else None
        ln["territory"] = terr
        lines.append(ln)
    if not lines:
        raise ParseError("No callage rows found.")
    return {"kind": "callage", "date": d, "lines": lines, "warnings": []}


# ------------------------------------------------------------------ Pending orders (View Pre-Selling / Sales Order)
def parse_pending(path, report_date: date | None = None) -> dict:
    rows = _rows(path)
    hi, cells = _find_header(rows, ["SO NUMBER", "TERRITORY", "NET AMOUNT"])
    d1, d2, meta = _meta(rows, hi)
    d = report_date or (d1 if d1 and d1 == d2 else None)
    if d is None:
        raise ParseError("The pending order report must cover a single day (Date From = Date To).")
    cols = {
        "so_number": _col(cells, "SO NUMBER"), "territory": _col(cells, "TERRITORY"),
        "distributor": _col(cells, "DISTRIBUTOR"), "sales_rep": _col(cells, "SALES REP"),
        "route": _col(cells, "ROUTE"), "customer": _col(cells, "CUSTOMER"),
        "pricing_scheme": _col(cells, "PRICING SCHEME"), "gross_amount": _col(cells, "GROSS AMOUNT"),
        "discount": _col(cells, "DISCOUNT"), "net_amount": _col(cells, "NET AMOUNT"),
        "invoice_status": _col(cells, "INVOICE STATUS"),
    }
    lines, warnings = [], []
    for r in rows[hi + 1:]:
        so = clean(_get(r, cols["so_number"]))
        terr = clean(_get(r, cols["territory"]))
        if not so or not terr:
            continue
        ln = {k: _get(r, i) for k, i in cols.items()}
        for k in ("gross_amount", "discount", "net_amount"):
            ln[k] = num(ln[k])
        for k in ("distributor", "sales_rep", "route", "customer", "pricing_scheme", "invoice_status"):
            ln[k] = clean(ln[k]) if ln[k] is not None else None
        ln["so_number"], ln["territory"] = so, terr
        lines.append(ln)
    st = _norm(meta.get("INVOICE STATUS"))
    if st and st != "PENDING":
        warnings.append(f"Report filter 'Invoice Status' is '{meta.get('INVOICE STATUS')}', not 'Pending'. "
                        "Only rows with status Pending are counted.")
    return {"kind": "pending", "date": d, "lines": lines, "warnings": warnings}


# ------------------------------------------------------------------ Rep Performance (month end)
def parse_rep_performance(path) -> dict:
    rows = _rows(path)
    hi, cells = _find_header(rows, ["TERRITORY", "SALES REP"])
    pc = _col(cells, "PRODUTIVE CALLS", "PRODUCTIVE CALLS")
    if pc is None:
        raise ParseError("Column 'PRODUTIVE CALLS' not found. Is this the Rep Performance Report?")
    d1, d2, _ = _meta(rows, hi)
    cols = {"territory": _col(cells, "TERRITORY"), "sales_rep": _col(cells, "SALES REP"),
            "productive_calls": pc, "achieved_sales": _col(cells, "ACHIEVED SALES"),
            "total_bills": _col(cells, "TOTAL BILLS"), "bill_days": _col(cells, "BILL DAYS")}
    lines = []
    for r in rows[hi + 1:]:
        terr = clean(_get(r, cols["territory"]))
        if not terr:
            continue  # unlabelled totals row
        ln = {k: _get(r, i) for k, i in cols.items()}
        for k in ("productive_calls", "achieved_sales", "total_bills", "bill_days"):
            ln[k] = num(ln[k])
        ln["territory"], ln["sales_rep"] = terr, clean(ln["sales_rep"])
        lines.append(ln)
    if not lines:
        raise ParseError("No rows found in the Rep Performance Report.")
    return {"kind": "rep_perf", "from": d1, "to": d2, "lines": lines, "warnings": []}


# ------------------------------------------------------------------ Targets
LEVEL_SUFFIX = {"ase total": 5, "dtm total": 4, "asm total": 3, "rsm total": 2, "region total": 1}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}


def parse_target_tva_layout(path) -> dict:
    """Read the hierarchy + targets from a Famulus 'Target vs Achievement' (AI file) layout.

    Rows are post-order: territories, then '<name>  (ASE Total)' etc., ending with NATIONAL TOTAL.
    Levels can be skipped (an ASE directly under an RSM), so each total row adopts the most recent
    unparented rows whose GT+SMMT targets add up to its declared totals.
    """
    rows = _rows(path)
    hi, cells = _find_header(rows, ["HIERARCHY", "MONTHLY TARGET"])
    c_lab, c_gt, c_smmt = 0, _col(cells, "MONTHLY TARGET"), _col(cells, "SMMT TARGET")
    year = month = None
    for r in rows[:hi]:
        for v in r:
            m = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
                          str(v or ""), re.I)
            if m:
                month, year = MONTHS[m.group(1).lower()], int(m.group(2))
                break
        if year:
            break

    warnings = []
    stack = []  # pending nodes awaiting a parent
    seq = 0
    for rn, r in enumerate(rows[hi + 1:], start=hi + 2):
        lab = clean(_get(r, c_lab))
        if not lab:
            continue
        gt, sm = num(_get(r, c_gt)), num(_get(r, c_smmt)) if c_smmt is not None else 0.0
        m = re.match(r"^(.*?)\s*\((ASE|DTM|ASM|RSM|Region) Total\)\s*$", lab, re.I)
        if lab.upper() == "NATIONAL TOTAL":
            level, name = 0, "National"
        elif m:
            level, name = LEVEL_SUFFIX[m.group(2).lower() + " total"], clean(m.group(1))
        else:
            seq += 1
            stack.append({"label": lab, "level": 6, "gt": gt, "smmt": sm, "children": [], "seq": seq, "row": rn})
            continue
        node = {"label": name, "level": level, "gt": gt, "smmt": sm, "children": [], "seq": seq, "row": rn}
        sg = ss = 0.0
        kids = []
        while stack and stack[-1]["level"] > level:
            if level == 0 or not kids or abs(sg - gt) > 0.5 or abs(ss - sm) > 0.5:
                k = stack.pop()
                kids.append(k)
                sg += k["gt"]
                ss += k["smmt"]
            else:
                break
        if abs(sg - gt) > 0.5 or abs(ss - sm) > 0.5:
            warnings.append(f"Row {rn} '{lab}': children add up to GT {sg:,.0f} / SMMT {ss:,.0f}, "
                            f"declared GT {gt:,.0f} / SMMT {sm:,.0f}.")
        node["children"] = list(reversed(kids))
        stack.append(node)
    roots = stack
    if len(roots) != 1 or roots[0]["level"] != 0:
        raise ParseError("Could not rebuild the hierarchy: the file must end with a NATIONAL TOTAL row.")
    return {"kind": "target", "year": year, "month": month, "root": roots[0], "warnings": warnings,
            "source": "tva_layout"}


def parse_target_template(path) -> dict:
    """Read the app's own editable template: columns Level | Label | GT Target | SMMT Target
    (+ optional Distributor, CSR). Rows are in top-down order (parent before children)."""
    rows = _rows(path)
    hi, cells = _find_header(rows, ["LEVEL", "LABEL", "GT TARGET"])
    c_lv, c_lab, c_gt = _col(cells, "LEVEL"), _col(cells, "LABEL"), _col(cells, "GT TARGET")
    c_sm, c_dist, c_csr = _col(cells, "SMMT TARGET"), _col(cells, "DISTRIBUTOR"), _col(cells, "CSR")
    c_par = _col(cells, "PARENT")
    names = {"NATIONAL": 0, "REGION": 1, "RSM": 2, "ASM": 3, "DTM": 4, "ASE": 5, "TERRITORY": 6}
    year = month = None
    for r in rows[:hi]:
        for i in range(len(r) - 1):
            if _norm(r[i]) == "YEAR":
                year = int(num(r[i + 1]))
            if _norm(r[i]) == "MONTH":
                month = int(num(r[i + 1]))
    root, path_stack, warnings = None, [], []
    groups: dict[str, dict] = {}   # label key -> most recent group node (for the Parent column)
    for rn, r in enumerate(rows[hi + 1:], start=hi + 2):
        lab = clean(_get(r, c_lab))
        lv_raw = _norm(_get(r, c_lv))
        if not lab or not lv_raw:
            continue
        lv = names.get(lv_raw, int(num(lv_raw)) if lv_raw.isdigit() else None)
        if lv is None:
            warnings.append(f"Row {rn}: unknown level '{lv_raw}', row skipped.")
            continue
        node = {"label": lab, "level": lv, "gt": num(_get(r, c_gt)) if lv == 6 else 0.0,
                "smmt": num(_get(r, c_sm)) if (lv == 6 and c_sm is not None) else 0.0, "children": [],
                "distributor": (clean(_get(r, c_dist)) or None) if c_dist is not None else None,
                "csr": (clean(_get(r, c_csr)) or None) if c_csr is not None else None}
        if lv == 0:
            root, path_stack = node, [node]
            groups[key(lab)] = node
            continue
        if root is None:
            raise ParseError("The first row of the template must be the National row.")
        par_lab = clean(_get(r, c_par)) if c_par is not None else ""
        parent = groups.get(key(par_lab)) if par_lab else None
        if par_lab and parent is None:
            warnings.append(f"Row {rn} '{lab}': parent '{par_lab}' not found above it; placed by level instead.")
        if parent is not None and parent["level"] >= lv:
            warnings.append(f"Row {rn} '{lab}': parent '{par_lab}' is not a higher level; placed by level instead.")
            parent = None
        if parent is None:
            while path_stack and path_stack[-1]["level"] >= lv:
                path_stack.pop()
            parent = path_stack[-1]
        parent["children"].append(node)
        if lv < 6:
            # keep the level stack consistent with the chosen parent
            while path_stack and path_stack[-1] is not parent:
                path_stack.pop()
            path_stack.append(node)
            groups[key(lab)] = node
    if root is None:
        raise ParseError("No National row found in the template.")
    return {"kind": "target", "year": year, "month": month, "root": root, "warnings": warnings, "source": "template"}


def parse_target(path) -> dict:
    """Detect which target layout the file uses."""
    rows = _rows(path)
    for r in rows[:40]:
        cells = [_norm(c) for c in r]
        if "HIERARCHY" in cells and any(c.startswith("MONTHLY TARGET") for c in cells):
            return parse_target_tva_layout(path)
        if "LEVEL" in cells and "LABEL" in cells:
            return parse_target_template(path)
    raise ParseError("Unrecognised target file. Use a Famulus Target vs Achievement report or the app's "
                     "Targets template (Targets screen -> Download template).")
