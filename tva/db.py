"""SQLite storage. Schema is a superset of the Famulus database so its history can be imported."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .util import key, now_iso

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS target_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    source_filename TEXT,
    source_kind TEXT,
    uploaded_at TEXT NOT NULL,
    gt_total REAL,
    smmt_total REAL,
    UNIQUE(year, month)
);
CREATE TABLE IF NOT EXISTS target_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES target_uploads(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES target_nodes(id) ON DELETE CASCADE,
    level INTEGER NOT NULL,          -- 0 National,1 Region,2 RSM,3 ASM,4 DTM,5 ASE,6 Territory
    sort_order INTEGER NOT NULL,
    label TEXT NOT NULL,
    gt_target REAL DEFAULT 0,        -- territory rows only; parents are rolled up
    smmt_target REAL DEFAULT 0,
    distributor TEXT,
    csr TEXT,
    is_operating INTEGER
);
CREATE INDEX IF NOT EXISTS ix_tn_upload ON target_nodes(upload_id);
CREATE TABLE IF NOT EXISTS target_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES target_uploads(id) ON DELETE CASCADE,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS achievement_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    source_filename TEXT,
    uploaded_at TEXT NOT NULL,
    row_count INTEGER,
    total_sales_value REAL
);
CREATE TABLE IF NOT EXISTS achievement_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES achievement_uploads(id) ON DELETE CASCADE,
    rsm TEXT, asm TEXT, ase TEXT, territory TEXT, territory_key TEXT, distributor TEXT,
    sales_rep_code TEXT, sales_rep_type TEXT, sales_rep_model TEXT, sales_rep TEXT,
    outlet_code TEXT, outlet_classification TEXT, outlet TEXT, invoice_no TEXT,
    category TEXT, brand TEXT, product_code TEXT, product TEXT,
    wholesale_price REAL, mrp REAL, total_units REAL, sales_value REAL, sales_volume REAL
);
CREATE INDEX IF NOT EXISTS ix_al_upload ON achievement_lines(upload_id);
CREATE INDEX IF NOT EXISTS ix_al_terr ON achievement_lines(territory_key);

CREATE TABLE IF NOT EXISTS callage_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    source_filename TEXT,
    uploaded_at TEXT NOT NULL,
    row_count INTEGER
);
CREATE TABLE IF NOT EXISTS callage_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES callage_uploads(id) ON DELETE CASCADE,
    rsm TEXT, asm TEXT, ase TEXT, territory TEXT, territory_key TEXT,
    sales_rep_code TEXT, sales_rep TEXT,
    itinerary_planned_days REAL, nod REAL, schedule_calls REAL,
    calls_made REAL, productive_calls REAL, unproductive_calls REAL,
    missed_calls REAL, productivity_pct REAL, unplanned_visits REAL
);
CREATE INDEX IF NOT EXISTS ix_cl_upload ON callage_lines(upload_id);
CREATE INDEX IF NOT EXISTS ix_cl_terr ON callage_lines(territory_key);

CREATE TABLE IF NOT EXISTS pending_order_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    source_filename TEXT,
    uploaded_at TEXT NOT NULL,
    row_count INTEGER
);
CREATE TABLE IF NOT EXISTS pending_order_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES pending_order_uploads(id) ON DELETE CASCADE,
    so_number TEXT, territory TEXT, territory_key TEXT, distributor TEXT, sales_rep TEXT, route TEXT,
    customer TEXT, pricing_scheme TEXT,
    gross_amount REAL, discount REAL, net_amount REAL, invoice_status TEXT
);
CREATE INDEX IF NOT EXISTS ix_pl_upload ON pending_order_lines(upload_id);

CREATE TABLE IF NOT EXISTS rep_perf_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    source_filename TEXT,
    uploaded_at TEXT NOT NULL,
    row_count INTEGER,
    UNIQUE(year, month)
);
CREATE TABLE IF NOT EXISTS rep_perf_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES rep_perf_uploads(id) ON DELETE CASCADE,
    territory TEXT, territory_key TEXT, sales_rep TEXT,
    productive_calls REAL, achieved_sales REAL, total_bills REAL, bill_days REAL
);

CREATE TABLE IF NOT EXISTS month_settings (
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    nowd INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(year, month)
);
-- One row per calendar date for months that have been set up from the NSM calendar.
CREATE TABLE IF NOT EXISTS calendar_days (
    date TEXT PRIMARY KEY,
    is_working INTEGER NOT NULL,
    is_competition INTEGER NOT NULL DEFAULT 0,
    round INTEGER
);

CREATE TABLE IF NOT EXISTS territory_selling_mode (
    territory_key TEXT PRIMARY KEY,
    territory_label TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('van', 'pre')),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outlet_category_config (
    category_key TEXT PRIMARY KEY,
    category_label TEXT NOT NULL,
    pricing_scheme TEXT,
    include_in_competition INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competition_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    van_sale_limit REAL NOT NULL,
    van_bill_limit REAL NOT NULL,
    pre_sale_limit REAL NOT NULL,
    pre_bill_limit REAL NOT NULL,
    updated_at TEXT NOT NULL
);
-- Name in an SFA file -> territory label used in the target hierarchy.
CREATE TABLE IF NOT EXISTS territory_alias (
    alias_key TEXT PRIMARY KEY,
    alias_label TEXT NOT NULL,
    territory_label TEXT NOT NULL
);
-- Hierarchy label -> label printed in reports (e.g. 'ASM Vacant - Uva & Sab' -> 'ASM Vidura - Uva & Sab').
CREATE TABLE IF NOT EXISTS label_override (
    original_key TEXT PRIMARY KEY,
    original_label TEXT NOT NULL,
    display_label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    report TEXT NOT NULL,
    period TEXT,
    path TEXT
);
"""

DEFAULT_SETTINGS = {
    "output_folder": "",
    "title_prefix": "",                # e.g. 'Lakmee' -> 'Lakmee — Day Report — ...'
    "include_smmt_pc_in_subtotals": "0",
    "recon_tolerance": "1000",
    "include_national_in_finalized": "1",
}


def connect(path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    con.execute(
        "INSERT OR IGNORE INTO competition_config VALUES (1, 100000, 20, 125000, 30, ?)", (now_iso(),)
    )
    con.commit()
    return con


def get_setting(con, k: str, default: str = "") -> str:
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def set_setting(con, k: str, v) -> None:
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    con.commit()


# ---------------------------------------------------------------- Famulus import
def import_famulus(con: sqlite3.Connection, famulus_path: str, replace: bool = False) -> dict:
    """Copy history and settings from a Famulus target_report.db.

    Dates/months already present are skipped unless replace=True.
    """
    src = sqlite3.connect(famulus_path)
    src.row_factory = sqlite3.Row
    stats = {"sales_days": 0, "callage_days": 0, "pending_days": 0, "target_months": 0,
             "skipped_days": 0, "settings": 0}

    def tables(c):
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    have = tables(src)

    def copy_daily(kind, line_cols, key_col="territory"):
        up_t, ln_t = f"{kind}_uploads", f"{kind}_lines"
        if up_t not in have:
            return 0
        n = 0
        for up in src.execute(f"SELECT * FROM {up_t} ORDER BY date"):
            ex = con.execute(f"SELECT id FROM {up_t} WHERE date=?", (up["date"],)).fetchone()
            if ex:
                if not replace:
                    stats["skipped_days"] += 1
                    continue
                con.execute(f"DELETE FROM {up_t} WHERE id=?", (ex["id"],))
            ucols = [c for c in up.keys() if c not in ("id", "total_units", "total_volume")]
            cur = con.execute(
                f"INSERT INTO {up_t} ({','.join(ucols)}) VALUES ({','.join('?' * len(ucols))})",
                [up[c] for c in ucols],
            )
            new_id = cur.lastrowid
            rows = src.execute(f"SELECT {','.join(line_cols)} FROM {ln_t} WHERE upload_id=?", (up["id"],)).fetchall()
            con.executemany(
                f"INSERT INTO {ln_t} (upload_id, territory_key, {','.join(line_cols)}) VALUES (?, ?, {','.join('?' * len(line_cols))})",
                [(new_id, key(r[key_col]), *r) for r in rows],
            )
            n += 1
        return n

    stats["sales_days"] = copy_daily("achievement", [
        "rsm", "asm", "ase", "territory", "distributor", "sales_rep_code", "sales_rep_type", "sales_rep_model",
        "sales_rep", "outlet_code", "outlet_classification", "outlet", "category", "brand", "product_code",
        "product", "wholesale_price", "mrp", "total_units", "sales_value", "sales_volume"])
    stats["callage_days"] = copy_daily("callage", [
        "rsm", "asm", "ase", "territory", "sales_rep_code", "sales_rep", "itinerary_planned_days", "nod",
        "schedule_calls", "calls_made", "productive_calls", "unproductive_calls", "missed_calls",
        "productivity_pct", "unplanned_visits"])
    stats["pending_days"] = copy_daily("pending_order", [
        "so_number", "territory", "distributor", "sales_rep", "route", "customer", "pricing_scheme",
        "gross_amount", "discount", "net_amount", "invoice_status"])

    # Targets: Famulus stores one declared total per node; SMMT territories are those named SMMT.
    if "target_uploads" in have:
        for up in src.execute("SELECT * FROM target_uploads ORDER BY year, month"):
            ex = con.execute("SELECT id FROM target_uploads WHERE year=? AND month=?", (up["year"], up["month"])).fetchone()
            if ex:
                if not replace:
                    continue
                con.execute("DELETE FROM target_uploads WHERE id=?", (ex["id"],))
            cur = con.execute(
                "INSERT INTO target_uploads(year, month, source_filename, source_kind, uploaded_at) VALUES (?,?,?,?,?)",
                (up["year"], up["month"], up["source_filename"], "famulus_db", now_iso()))
            new_up = cur.lastrowid
            idmap = {}
            nodes = src.execute(
                "SELECT * FROM target_nodes WHERE upload_id=? ORDER BY level, sort_order, id", (up["id"],)).fetchall()
            for nd in nodes:
                tgt = nd["declared_total"] or 0
                is_terr = nd["level"] == 6
                smmt = is_terr and "smmt" in key(nd["label"])
                cur = con.execute(
                    "INSERT INTO target_nodes(upload_id, parent_id, level, sort_order, label, gt_target, smmt_target,"
                    " distributor, csr, is_operating) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (new_up, idmap.get(nd["parent_id"]), nd["level"], nd["sort_order"], nd["label"],
                     (0 if smmt else tgt) if is_terr else 0, (tgt if smmt else 0) if is_terr else 0,
                     nd["distributor"], nd["csr"], nd["is_operating"]))
                idmap[nd["id"]] = cur.lastrowid
            _update_target_totals(con, new_up)
            stats["target_months"] += 1

    if "month_settings" in have:
        for r in src.execute("SELECT year, month, nowd FROM month_settings"):
            con.execute("INSERT OR IGNORE INTO month_settings VALUES (?,?,?,?)", (r["year"], r["month"], r["nowd"], now_iso()))
            stats["settings"] += 1
    if "territory_selling_mode" in have:
        for r in src.execute("SELECT territory_key, territory_label, mode FROM territory_selling_mode"):
            con.execute("INSERT OR IGNORE INTO territory_selling_mode VALUES (?,?,?,?)",
                        (key(r["territory_label"]), r["territory_label"], r["mode"], now_iso()))
            stats["settings"] += 1
    if "outlet_category_config" in have:
        for r in src.execute("SELECT * FROM outlet_category_config"):
            con.execute("INSERT OR IGNORE INTO outlet_category_config VALUES (?,?,?,?,?)",
                        (key(r["category_label"]), r["category_label"], r["pricing_scheme"],
                         r["include_in_competition"], now_iso()))
            stats["settings"] += 1
    if "competition_config" in have:
        r = src.execute("SELECT * FROM competition_config WHERE id=1").fetchone()
        if r:
            con.execute("UPDATE competition_config SET van_sale_limit=?, van_bill_limit=?, pre_sale_limit=?,"
                        " pre_bill_limit=?, updated_at=? WHERE id=1",
                        (r["van_sale_limit"], r["van_bill_limit"], r["pre_sale_limit"], r["pre_bill_limit"], now_iso()))
    con.commit()
    src.close()
    return stats


def _update_target_totals(con, upload_id):
    r = con.execute("SELECT SUM(gt_target) g, SUM(smmt_target) s FROM target_nodes WHERE upload_id=? AND level=6",
                    (upload_id,)).fetchone()
    con.execute("UPDATE target_uploads SET gt_total=?, smmt_total=? WHERE id=?", (r["g"] or 0, r["s"] or 0, upload_id))
