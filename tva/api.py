"""Methods exposed to the UI (pywebview js_api). Every method returns JSON-safe data.

Errors are returned as {'error': message} so the UI can show them instead of crashing.
"""
from __future__ import annotations

import calendar as _cal
import functools
import os
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__, db, hierarchy, ingest, sfa, workcal
from .reports import DAILY, generate_daily, generate_finalized, readiness
from .templates import write_template
from .util import iso, key, now_iso


def safe_nolock(fn):
    """For long browser calls: no database lock is held while they run."""
    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return {"error": str(e) or e.__class__.__name__}
    return wrap


def safe(fn):
    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        try:
            with self._lock:
                return fn(self, *a, **kw)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return {"error": str(e) or e.__class__.__name__}
    return wrap


class Api:
    def __init__(self, db_path: Path, default_out: Path, app_dir: Path | None = None, admin: bool = False):
        self._lock = threading.RLock()
        self._con = db.connect(db_path, check_same_thread=False)
        self._db_path = db_path
        self._data_dir = Path(db_path).parent
        self._app_dir = Path(app_dir or self._data_dir.parent)
        self._admin = admin
        # defaults: a sfa_defaults.json next to the exe/main.py wins over the one bundled in tva/
        self._default_paths = [self._app_dir / "sfa_defaults.json", Path(__file__).with_name("sfa_defaults.json")]
        if not db.get_setting(self._con, "output_folder"):
            db.set_setting(self._con, "output_folder", str(default_out))
        workcal.seed_known_calendars(self._con)
        self._defaults = sfa.init(self._con, self._default_paths)
        if self._defaults.get("_login_url") and not db.get_setting(self._con, "sfa_username"):
            db.set_setting(self._con, "sfa_login_url", self._defaults["_login_url"])
        self._window = None
        self._previews: dict[str, dict] = {}

    # ------------------------------------------------------------------ helpers
    def _out(self) -> str:
        return db.get_setting(self._con, "output_folder")

    def _dialog(self, kind, **kw):
        import webview
        fd = getattr(webview, "FileDialog", None)
        const = {"open": fd.OPEN if fd else webview.OPEN_DIALOG,
                 "folder": fd.FOLDER if fd else webview.FOLDER_DIALOG,
                 "save": fd.SAVE if fd else webview.SAVE_DIALOG}[kind]
        return self._window.create_file_dialog(const, **kw)

    # ------------------------------------------------------------------ app
    @safe
    def init(self):
        y = date.today() - timedelta(days=1)
        return {"version": __version__, "db": str(self._db_path), "output_folder": self._out(),
                "default_date": iso(y), "reports": DAILY, "admin": self._admin}

    @safe
    def status(self, d: str):
        dt = date.fromisoformat(d)
        r = readiness(self._con, dt)
        r["calendar"] = workcal.competition_info(self._con, dt)
        r["is_working"] = workcal.is_working(self._con, dt)
        r["nowd"] = workcal.nowd(self._con, dt.year, dt.month)
        r["calendar_configured"] = workcal.is_configured(self._con, dt.year, dt.month)
        return r

    @safe
    def month_overview(self, y: int, m: int):
        n = _cal.monthrange(y, m)[1]
        a, b = iso(date(y, m, 1)), iso(date(y, m, n))

        def ds(t):
            return {r["date"] for r in self._con.execute(f"SELECT date FROM {t} WHERE date BETWEEN ? AND ?", (a, b))}
        s, c, p = ds("achievement_uploads"), ds("callage_uploads"), ds("pending_order_uploads")
        days = workcal.month_days(self._con, y, m)
        out = []
        for x in days:
            k = iso(x["date"])
            out.append({"date": k, "day": x["date"].day, "dow": x["date"].weekday(), "working": x["is_working"],
                        "competition": x["is_competition"], "round": x["round"],
                        "sales": k in s, "callage": k in c, "pending": k in p})
        rp = self._con.execute("SELECT source_filename, uploaded_at FROM rep_perf_uploads WHERE year=? AND month=?",
                              (y, m)).fetchone()
        tg = self._con.execute("SELECT * FROM target_uploads WHERE year=? AND month=?", (y, m)).fetchone()
        return {"days": out, "rep_perf": dict(rp) if rp else None, "targets": dict(tg) if tg else None,
                "nowd": workcal.nowd(self._con, y, m)}

    # ------------------------------------------------------------------ uploads
    @safe
    def pick_files(self):
        r = self._dialog("open", allow_multiple=True, file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        return list(r or [])

    @safe
    def preview_files(self, paths: list, opts: dict | None = None):
        opts = opts or {}
        out = []
        for p in paths:
            try:
                ym = opts.get("month")
                y, m = (int(ym[:4]), int(ym[5:7])) if ym else (None, None)
                pv = ingest.preview(self._con, p, opts.get("kind") or None,
                                    date.fromisoformat(opts["date"]) if opts.get("date") else None, y, m)
                pid = uuid.uuid4().hex
                self._previews[pid] = pv
                out.append({"id": pid, **{k: v for k, v in pv.items() if k != "payload"}})
            except Exception as e:  # noqa: BLE001
                out.append({"id": None, "file": Path(p).name, "error": str(e)})
        return out

    @safe
    def commit_previews(self, ids: list, replace: bool):
        msgs = []
        for i in ids:
            pv = self._previews.pop(i, None)
            if pv:
                msgs += ingest.commit(self._con, pv, replace)
        return {"messages": msgs}

    @safe
    def delete_upload(self, kind: str, d: str):
        t = {"sales": "achievement_uploads", "callage": "callage_uploads", "pending": "pending_order_uploads"}[kind]
        self._con.execute(f"DELETE FROM {t} WHERE date=?", (d,))
        self._con.commit()
        return {"ok": True}

    @safe
    def import_famulus(self):
        r = self._dialog("open", file_types=("Famulus database (*.db)", "All files (*.*)"))
        if not r:
            return {"cancelled": True}
        return db.import_famulus(self._con, r[0] if isinstance(r, (list, tuple)) else r)

    # ------------------------------------------------------------------ generate
    @safe
    def generate_daily(self, d: str, which: list, force_winners: bool = False):
        res = generate_daily(self._con, date.fromisoformat(d), self._out(), which, force_winners)
        return res

    @safe
    def generate_finalized(self, d1: str, d2: str):
        return generate_finalized(self._con, date.fromisoformat(d1), date.fromisoformat(d2), self._out())

    @safe
    def history(self):
        return [dict(r) for r in self._con.execute("SELECT * FROM generation_log ORDER BY id DESC LIMIT 200")]

    @safe
    def open_path(self, p: str):
        if not p or not os.path.exists(p):
            return {"error": "File not found (it may have been moved or deleted)."}
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
        return {"ok": True}

    @safe
    def open_folder(self, p: str | None = None):
        p = p or self._out()
        if os.path.isfile(p):
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
                return {"ok": True}
            p = os.path.dirname(p)
        Path(p).mkdir(parents=True, exist_ok=True)
        return self.open_path(p)

    # ------------------------------------------------------------------ calendar
    @safe
    def calendar_get(self, y: int, m: int):
        days = workcal.month_days(self._con, y, m)
        return {"configured": workcal.is_configured(self._con, y, m), "nowd": workcal.nowd(self._con, y, m),
                "days": [{"date": iso(x["date"]), "day": x["date"].day, "dow": x["date"].weekday(),
                          "working": x["is_working"], "competition": x["is_competition"], "round": x["round"]}
                         for x in days]}

    @safe
    def calendar_save(self, y: int, m: int, days: list):
        workcal.save_month(self._con, y, m, [{"date": d["date"], "is_working": d["working"],
                                             "is_competition": d["competition"], "round": d.get("round")}
                                            for d in days])
        return {"ok": True, "nowd": workcal.nowd(self._con, y, m)}

    # ------------------------------------------------------------------ targets
    @safe
    def targets_get(self, y: int, m: int):
        t = hierarchy.load_tree(self._con, y, m)
        up = self._con.execute("SELECT * FROM target_uploads WHERE year=? AND month=?", (y, m)).fetchone()
        if t is None:
            return {"loaded": False}
        rows = []

        def walk(n, depth):
            rows.append({"label": n.label, "display": n.display, "level": n.level, "depth": depth,
                         "territory": n.is_territory, "smmt": n.is_smmt, "gt": n.gt_target, "sm": n.smmt_target})
            for c in n.children:
                walk(c, depth + 1)
        walk(t, 0)
        return {"loaded": True, "rows": rows, "upload": dict(up)}

    @safe
    def targets_set(self, y: int, m: int, label: str, gt: float, sm: float):
        hierarchy.set_territory_target(self._con, y, m, label, float(gt or 0), float(sm or 0))
        return {"ok": True}

    @safe
    def targets_copy(self, y: int, m: int):
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        hierarchy.copy_month(self._con, (py, pm), (y, m))
        return {"ok": True}

    @safe
    def targets_template(self, y: int, m: int):
        p = Path(self._out()) / f"Targets_Template_{y}-{m:02d}.xlsx"
        write_template(self._con, y, m, p)
        self.open_path(str(p))
        return {"path": str(p)}

    # ------------------------------------------------------------------ settings
    @safe
    def settings_get(self):
        s = {r["key"]: r["value"] for r in self._con.execute("SELECT * FROM settings")}
        lim = dict(self._con.execute("SELECT * FROM competition_config WHERE id=1").fetchone())
        # territories: union of latest hierarchy + selling mode table
        terr = {}
        up = self._con.execute("SELECT id FROM target_uploads ORDER BY year DESC, month DESC LIMIT 1").fetchone()
        if up:
            for r in self._con.execute("SELECT label FROM target_nodes WHERE upload_id=? AND level=6 ORDER BY id",
                                      (up["id"],)):
                terr[key(r["label"])] = {"label": r["label"], "mode": None}
        for r in self._con.execute("SELECT * FROM territory_selling_mode ORDER BY territory_label"):
            terr.setdefault(r["territory_key"], {"label": r["territory_label"], "mode": None})["mode"] = r["mode"]
        cats = [dict(r) for r in self._con.execute("SELECT * FROM outlet_category_config ORDER BY category_label")]
        aliases = [dict(r) for r in self._con.execute("SELECT * FROM territory_alias ORDER BY alias_label")]
        overrides = [dict(r) for r in self._con.execute("SELECT * FROM label_override ORDER BY original_label")]
        return {"settings": s, "limits": lim, "territories": list(terr.values()), "categories": cats,
                "aliases": aliases, "overrides": overrides, "db": str(self._db_path)}

    @safe
    def settings_save(self, s: dict):
        for k, v in s.items():
            db.set_setting(self._con, k, v)
        return {"ok": True}

    @safe
    def choose_output_folder(self):
        r = self._dialog("folder")
        if not r:
            return {"cancelled": True}
        p = r[0] if isinstance(r, (list, tuple)) else r
        db.set_setting(self._con, "output_folder", p)
        return {"path": p}

    @safe
    def limits_save(self, v: dict):
        self._con.execute("UPDATE competition_config SET van_sale_limit=?, van_bill_limit=?, pre_sale_limit=?, "
                         "pre_bill_limit=?, updated_at=? WHERE id=1",
                         (float(v["van_sale_limit"]), float(v["van_bill_limit"]), float(v["pre_sale_limit"]),
                          float(v["pre_bill_limit"]), now_iso()))
        self._con.commit()
        return {"ok": True}

    @safe
    def mode_set(self, label: str, mode: str):
        self._con.execute("INSERT INTO territory_selling_mode VALUES (?,?,?,?) ON CONFLICT(territory_key) DO UPDATE "
                         "SET mode=excluded.mode, territory_label=excluded.territory_label, updated_at=excluded.updated_at",
                         (key(label), label, mode, now_iso()))
        self._con.commit()
        return {"ok": True}

    @safe
    def category_set(self, category_key: str, include: bool):
        self._con.execute("UPDATE outlet_category_config SET include_in_competition=?, updated_at=? WHERE category_key=?",
                         (int(bool(include)), now_iso(), category_key))
        self._con.commit()
        return {"ok": True}

    @safe
    def alias_add(self, alias: str, territory: str):
        self._con.execute("INSERT OR REPLACE INTO territory_alias VALUES (?,?,?)", (key(alias), alias.strip(), territory.strip()))
        self._con.commit()
        return {"ok": True}

    @safe
    def alias_delete(self, alias_key: str):
        self._con.execute("DELETE FROM territory_alias WHERE alias_key=?", (alias_key,))
        self._con.commit()
        return {"ok": True}

    @safe
    def override_add(self, original: str, display: str):
        self._con.execute("INSERT OR REPLACE INTO label_override VALUES (?,?,?)",
                         (key(original), original.strip(), display.strip()))
        self._con.commit()
        return {"ok": True}

    @safe
    def override_delete(self, original_key: str):
        self._con.execute("DELETE FROM label_override WHERE original_key=?", (original_key,))
        self._con.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ SFA connection (Chimera approach)
    @safe
    def sfa_overview(self):
        cfg = sfa.get_config(self._con)
        return {"config": {k: v for k, v in cfg.items() if k != "password"}, "has_password": bool(cfg["password"]),
                "login_ready": sfa.login_ready(cfg), "profiles": sfa.list_profiles(self._con),
                "filter_types": list(sfa.FILTER_TYPES), "date_modes": list(sfa.DATE_MODES),
                "browsers": list(sfa.SUPPORTED_BROWSERS), "admin": self._admin,
                "defaults_source": self._defaults.get("_source", "built-in"),
                "live_active": bool(sfa.LIVE.snapshot().get("active"))}

    @safe
    def sfa_save_config(self, v: dict):
        sfa.save_config(self._con, v)
        return {"ok": True}

    @safe_nolock
    def sfa_test_connection(self, show_browser: bool = False):
        return sfa.test_connection(self._con, self._lock, headless=not show_browser)

    @safe_nolock
    def sfa_detect(self, url: str, show_browser: bool = False):
        return sfa.detect_filters(self._con, self._lock, url, headless=not show_browser)

    @safe
    def sfa_save_profile(self, key: str, data: dict):
        try:
            return {"ok": True, "profile": sfa.save_profile(self._con, key, data)}
        except ValueError as e:
            return {"ok": False, "message": str(e)}

    @safe
    def sfa_set_enabled(self, key: str, enabled: bool):
        sfa.set_enabled(self._con, key, enabled)
        return {"ok": True}

    @safe
    def sfa_reset_profile(self, key: str):
        sfa.reset_profile(self._con, key, self._default_paths)
        return {"ok": True}

    @safe
    def sfa_export_defaults(self):
        if not self._admin:
            return {"error": "Only the admin copy can save defaults."}
        target = self._app_dir / "sfa_defaults.json"
        if not getattr(sys, "frozen", False):
            target = Path(__file__).with_name("sfa_defaults.json")
        return {"path": sfa.export_defaults(self._con, target)}

    # ---- loading downloaded files into the database (called by the run, under the lock)
    def _store_download(self, key, path, report_date):
        d = date.fromisoformat(report_date)
        kw = {}
        if key in ("callage", "pending"):
            kw["report_date"] = d
        if key == "rep_perf":
            kw["year"], kw["month"] = d.year, d.month
        pv = ingest.preview(self._con, str(path), key, **kw)
        msgs = ingest.commit(self._con, pv, replace=True)
        rows = sum(len(x) for x in pv["payload"].get("by_date", {}).values()) if key == "sales" else \
            len(pv["payload"].get("lines", []))
        return {"rows": rows, "message": "; ".join(msgs), "warnings": pv["warnings"]}

    def _store_empty(self, key, report_date):
        if key == "pending":   # no pending orders now: replace the day's snapshot with an empty one
            self._con.execute("DELETE FROM pending_order_uploads WHERE date=?", (report_date,))
            self._con.execute("INSERT INTO pending_order_uploads(date, source_filename, uploaded_at, row_count) "
                             "VALUES (?, 'SFA: no records', ?, 0)", (report_date, now_iso()))
            self._con.commit()
            return "No pending orders in SFA — saved as 0 for this date."
        return "SFA has no records for this date — nothing loaded."

    @safe
    def sfa_start_run(self, keys: list, report_date: str, test: bool = False, show_browser: bool | None = None,
                      generate: bool = False, which: list | None = None):
        def after(res, lanes):
            if not generate:
                return None
            if not any(l["key"] in sfa.DAILY_KEYS and l["status"] in ("done", "empty") for l in lanes):
                return None
            sfa.LIVE.log("Building the report pack…")
            with self._lock:
                g = generate_daily(self._con, date.fromisoformat(report_date), self._out(), which)
            for x in g["results"]:
                sfa.LIVE.log(f"{x['report']}: {x['summary']}")
            for w in g["warnings"]:
                sfa.LIVE.log("⚠ " + w, level="warn")
            return {"generated": g}
        return sfa.start_background(con=self._con, lock=self._lock, data_dir=self._data_dir, keys=keys,
                                    report_date=report_date, test=test,
                                    headless=None if show_browser is None else not show_browser,
                                    store=self._store_download, store_empty=self._store_empty, after=after)

    def sfa_live(self):
        return sfa.LIVE.snapshot()

    def sfa_stop(self):
        return {"stopped": sfa.force_stop()}

    def sfa_log_text(self):
        return sfa.log_text(sfa.LIVE.snapshot())

    @safe
    def sfa_open_downloads(self):
        p = self._data_dir / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return self.open_path(str(p))
