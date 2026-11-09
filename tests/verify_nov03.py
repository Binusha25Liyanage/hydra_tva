"""Regression check: rebuild the 3 Nov 2026 daily pack and compare with Famulus' outputs cell by cell.

Usage:  python tests/verify_nov03.py <inputs_folder> <famulus_outputs_folder> <famulus_db>

2 Nov raw exports were not available, so 2 Nov per-territory sales and productive calls are
reconstructed from Famulus' Day Report (cumulative minus day) — only for this test.
Pre Selling territories' callage/pending differ because Famulus downloaded them at another time.
"""
import glob
import sys
import tempfile
from datetime import date
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tva import db, ingest, workcal  # noqa: E402
from tva.reports import generate_daily  # noqa: E402
from tva.util import key, now_iso  # noqa: E402

PRE = {"chilaw", "puttlam", "kuliyapitiya", "wennappuwa", "hingurakgoda", "pannala"}


def main(inp, famout, famdb):
    tmp = Path(tempfile.mkdtemp())
    con = db.connect(tmp / "t.db")
    print("Famulus import:", db.import_famulus(con, famdb))
    workcal.seed_known_calendars(con)
    for f in sorted(glob.glob(f"{inp}/**/*.xlsx", recursive=True)):
        pv = ingest.preview(con, f)
        print(f"  {pv['kind_label']:28} {pv['info']} warn={pv['warnings']}")
        print("   ", ingest.commit(con, pv, replace=True))

    # synthetic 2 Nov from Famulus Day Report
    dr = openpyxl.load_workbook(glob.glob(f"{famout}/*Day_Report*.xlsx")[0]).active
    cur = con.execute("INSERT INTO achievement_uploads(date, source_filename, uploaded_at) VALUES "
                      "('2026-11-02','synthetic',?)", (now_iso(),))
    cu2 = con.execute("INSERT INTO callage_uploads(date, source_filename, uploaded_at) VALUES "
                      "('2026-11-02','synthetic',?)", (now_iso(),))
    for r in dr.iter_rows(min_row=5, values_only=True):
        t = r[0]
        if not t or "Total" in t or "TOTAL" in t:
            continue
        v, pc = (r[15] or 0) - (r[3] or 0), (r[20] or 0) - (r[9] or 0)
        if v:
            con.execute("INSERT INTO achievement_lines(upload_id, territory, territory_key, outlet_classification,"
                        " sales_value) VALUES (?,?,?,?,?)", (cur.lastrowid, t, key(t), "Groceries - Small(GR - S)", v))
        if pc:
            con.execute("INSERT INTO callage_lines(upload_id, territory, territory_key, productive_calls, calls_made)"
                        " VALUES (?,?,?,?,?)", (cu2.lastrowid, t, key(t), pc, pc))
    con.commit()

    out = tmp / "out"
    res = generate_daily(con, date(2026, 11, 3), str(out))
    for r in res["results"]:
        print("GEN", r["report"], "->", Path(r["path"]).name if r.get("path") else None, "|", r["summary"])
    print("WARN", res["warnings"])

    total_bad = 0
    for pat in ["Target_vs_Achievement", "Day_Report", "Sales_Comparison", "Daily_Winners", "Pending_Order_Value"]:
        a = openpyxl.load_workbook(glob.glob(f"{out}/{pat}*.xlsx")[0]).active
        b = openpyxl.load_workbook(glob.glob(f"{famout}/*{pat}*.xlsx")[0]).active
        bad = pre_only = 0
        if a.max_row != b.max_row:
            print(f"  {pat}: row count {a.max_row} vs Famulus {b.max_row}")
        for r in range(4, max(a.max_row, b.max_row) + 1):
            for c in range(1, max(a.max_column, b.max_column) + 1):
                x, y = a.cell(r, c).value, b.cell(r, c).value
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    same = abs(x - y) < 0.01 + 1e-9 * abs(y)
                else:
                    same = (x or None) == (y or None)
                if not same:
                    lab = a.cell(r, 1).value if pat != "Daily_Winners" else a.cell(r, 6).value
                    ylab = b.cell(r, 1).value if pat != "Daily_Winners" else b.cell(r, 6).value
                    is_pre = key(lab) in PRE or key(ylab) in PRE or "Total" in str(lab) or "TOTAL" in str(lab)
                    if is_pre:
                        pre_only += 1
                    else:
                        bad += 1
                        if bad <= 8:
                            print(f"  {pat} R{r}C{c} [{lab}] ours={x!r} famulus={y!r}")
        hdr = [(a.cell(4, c).value, b.cell(4, c).value) for c in range(1, a.max_column + 1)
               if a.cell(4, c).value != b.cell(4, c).value]
        print(f"{pat}: mismatches {bad} (+{pre_only} in Pre/subtotal rows from download timing)"
              + (f" header diffs {hdr}" if hdr else ""))
        print(f"   title ours  : {a['A1'].value}\n   title famul : {b['A1'].value}")
        print(f"   sub ours    : {a['A2'].value}\n   sub famul   : {b['A2'].value}")
        total_bad += bad
    print("TOTAL non-timing mismatches:", total_bad, "| output folder:", out)
    return total_bad


if __name__ == "__main__":
    sys.exit(1 if main(*sys.argv[1:4]) else 0)
