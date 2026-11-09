"""SFA download engine for Hydra TvA — the same approach as Project Chimera.

    one browser -> log in ONCE -> one tab per report (in parallel, same session) ->
    per tab: open URL -> fill filters by their on-screen labels -> Search -> Export ->
             check the file against the filters -> load it into the database
    -> then (optionally) build the daily report pack

* SalesPro is an Angular / PrimeNG app: every filter is found by the label shown above it
  (exactly what is typed in the report profile) and driven by kind: PrimeNG dropdown /
  multiselect, ng-select, native select, date input, text box or toggle.
* "Detect filters" opens the page and lists its filter labels, types and dropdown options.
* The Export button is found by its text, or by an Excel / export icon.
* Every download is checked (report kind + banner values: dates, Report Type, Invoice Status…)
  before it is loaded; a wrong file is rejected, not imported.
* Each tab succeeds or fails on its own with retries; progress is published to LIVE for the UI.

Hydra TvA additions: the password is encrypted (Windows DPAPI); the admin's report profiles
ship as defaults (sfa_defaults.json); users can edit them or reset them to the default.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import secret
from .db import get_setting, set_setting
from .util import iso, month_first, month_last, now_iso

# ------------------------------------------------------------------ constants
REPORTS = [
    ("sales", "Outlet Wise SKU Sales"),
    ("callage", "Callage Summary"),
    ("pending", "Pending Orders (Sales Order)"),
    ("rep_perf", "Rep Performance (month end)"),
]
REPORT_NAMES = dict(REPORTS)
DAILY_KEYS = ["sales", "callage", "pending"]
FILTER_TYPES = ("dropdown", "multiselect", "date", "text", "toggle")
DATE_MODES = ("report_date", "today", "yesterday", "month_start", "month_end", "fixed")
STEPS = ["login", "open", "filters", "search", "export", "validate", "import"]
STEP_LABELS = {"login": "Log in", "open": "Open", "filters": "Filters", "search": "Search",
               "export": "Export", "validate": "Check", "import": "Load"}
SUPPORTED_BROWSERS = {"edge": "msedge", "chrome": "chrome", "firefox": None}
NO_DATA_PATTERNS = re.compile(r"no (records?|data|results?)( found| available)?|nothing to (show|display)", re.I)

PANEL_SEL = ".p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay, .cdk-overlay-pane"
OPTION_SEL = "[role=option], li.p-dropdown-item, li.p-multiselect-item, .ng-option"


def _d(label, mode):
    return {"label": label, "type": "date", "value": "", "date_mode": mode, "required": True, "options": []}


def _dd(label, value):
    return {"label": label, "type": "dropdown", "value": value, "date_mode": "", "required": True, "options": []}


SEED = {
    "sales": {"url": "https://lakmee.salespro.lk/it/reports/outlet_wise_sku_sales",
              "filters": [_dd("Report Type", "All Invoices"), _d("From Date", "report_date"),
                          _d("To Date", "report_date")]},
    "callage": {"url": "", "filters": [_d("From Date", "report_date"), _d("To Date", "report_date")]},
    "pending": {"url": "", "filters": [_dd("Invoice Status", "Pending"), _d("Date From", "report_date"),
                                       _d("Date To", "report_date")]},
    "rep_perf": {"url": "", "filters": [_d("From Date", "month_start"), _d("To Date", "month_end")]},
}
PROFILE_DEFAULTS = {"search_text": "SEARCH", "export_text": "Export", "wait_seconds": 60, "allow_empty": True,
                    "enabled": True}

SETTINGS_DEFAULTS = {"sfa_login_url": "https://lakmee.salespro.lk/", "sfa_username": "", "sfa_password": "",
                     "sfa_browser": "edge", "sfa_parallel": "3", "sfa_retries": "1", "sfa_timeout": "60",
                     "sfa_show_browser": "0"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sfa_reports (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL DEFAULT '',
    filters      TEXT NOT NULL DEFAULT '[]',
    search_text  TEXT NOT NULL DEFAULT 'SEARCH',
    export_text  TEXT NOT NULL DEFAULT 'Export',
    wait_seconds INTEGER NOT NULL DEFAULT 60,
    allow_empty  INTEGER NOT NULL DEFAULT 1,
    enabled      INTEGER NOT NULL DEFAULT 1,
    edited       INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT
);
"""


class StepError(Exception):
    def __init__(self, step, message, retry=True):
        super().__init__(message)
        self.step, self.message, self.retry = step, message, retry


class Cancelled(Exception):
    pass


# ================================================================== settings & credentials
def init(con, default_paths):
    con.executescript(SCHEMA)
    for k, v in SETTINGS_DEFAULTS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    # v1.1 -> v1.2: browser names and the login URL of the old step-recorder profiles
    b = get_setting(con, "sfa_browser", "edge")
    if b == "msedge":
        set_setting(con, "sfa_browser", "edge")
    try:
        old = con.execute("SELECT url FROM sfa_profiles WHERE key='login'").fetchone()
        if old and old["url"] and get_setting(con, "sfa_login_url") in ("", SETTINGS_DEFAULTS["sfa_login_url"]):
            set_setting(con, "sfa_login_url", old["url"])
    except Exception:  # noqa: BLE001 (no old table)
        pass
    defaults = load_defaults(default_paths)
    for k, name in REPORTS:
        dv = defaults[k]
        row = con.execute("SELECT edited FROM sfa_reports WHERE key=?", (k,)).fetchone()
        vals = (name, dv["url"], json.dumps(dv["filters"]), dv["search_text"], dv["export_text"],
                int(dv["wait_seconds"]), int(bool(dv["allow_empty"])), int(bool(dv["enabled"])), now_iso())
        if row is None:
            con.execute("INSERT INTO sfa_reports (name, url, filters, search_text, export_text, wait_seconds, "
                        "allow_empty, enabled, updated_at, key, edited) VALUES (?,?,?,?,?,?,?,?,?,?,0)", (*vals, k))
        elif not row["edited"]:
            con.execute("UPDATE sfa_reports SET name=?, url=?, filters=?, search_text=?, export_text=?, "
                        "wait_seconds=?, allow_empty=?, enabled=?, updated_at=? WHERE key=?", (*vals, k))
    con.commit()
    return defaults


def load_defaults(paths) -> dict:
    out = {k: {**PROFILE_DEFAULTS, **json.loads(json.dumps(SEED[k]))} for k, _ in REPORTS}
    out["_source"] = "built-in"
    for p in paths or []:
        if p and Path(p).is_file():
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for k, v in (data.get("reports") or {}).items():
                if k in out:
                    out[k].update({x: v[x] for x in ("url", "filters", "search_text", "export_text",
                                                      "wait_seconds", "allow_empty", "enabled") if x in v})
            if data.get("login_url"):
                out["_login_url"] = data["login_url"]
            out["_source"] = str(p)
            break
    return out


def get_config(con) -> dict:
    g = lambda k: get_setting(con, k, SETTINGS_DEFAULTS[k])  # noqa: E731
    return {"login_url": g("sfa_login_url"), "username": g("sfa_username"),
            "password": secret.unprotect(g("sfa_password")), "browser": g("sfa_browser"),
            "parallel": max(1, min(6, int(float(g("sfa_parallel") or 3)))),
            "retries": max(0, min(3, int(float(g("sfa_retries") or 1)))),
            "timeout_ms": max(15, int(float(g("sfa_timeout") or 60))) * 1000,
            "show_browser": g("sfa_show_browser") == "1"}


def save_config(con, v: dict):
    if v.get("login_url") is not None:
        u = str(v["login_url"]).strip()
        if u and not re.match(r"^https?://", u):
            raise ValueError("The login URL must start with https://")
        set_setting(con, "sfa_login_url", u)
    if v.get("username") is not None:
        set_setting(con, "sfa_username", str(v["username"]).strip())
    if v.get("password"):
        set_setting(con, "sfa_password", secret.protect(v["password"]))
    if v.get("browser") in SUPPORTED_BROWSERS:
        set_setting(con, "sfa_browser", v["browser"])
    for k in ("parallel", "retries", "timeout"):
        if v.get(k) not in (None, ""):
            set_setting(con, f"sfa_{k}", str(int(float(v[k]))))
    if v.get("show_browser") is not None:
        set_setting(con, "sfa_show_browser", "1" if v["show_browser"] else "0")


def login_ready(cfg) -> bool:
    return bool(cfg["login_url"] and cfg["username"] and cfg["password"])


# ================================================================== profiles
def _row(r) -> dict:
    p = dict(r)
    p["filters"] = json.loads(p["filters"] or "[]")
    for k in ("allow_empty", "enabled", "edited"):
        p[k] = bool(p[k])
    p["problems"] = problems(p)
    return p


def list_profiles(con) -> list[dict]:
    order = {k: i for i, (k, _) in enumerate(REPORTS)}
    return sorted([_row(r) for r in con.execute("SELECT * FROM sfa_reports")], key=lambda p: order.get(p["key"], 9))


def get_profile(con, key) -> dict:
    return _row(con.execute("SELECT * FROM sfa_reports WHERE key=?", (key,)).fetchone())


def problems(p) -> list[str]:
    out = []
    if not p.get("url"):
        out.append("Navigation URL is not set.")
    elif not re.match(r"^https?://", p["url"]):
        out.append("Navigation URL must start with http:// or https://")
    for f in p.get("filters", []):
        if f.get("required") and f.get("type") != "date" and f.get("value") in ("", None, []):
            out.append(f"Filter '{f.get('label')}' is required but has no value.")
        if f.get("type") == "date" and f.get("date_mode") == "fixed" and \
                not re.match(r"^\d{4}-\d{2}-\d{2}$", str(f.get("value") or "")):
            out.append(f"Filter '{f.get('label')}' uses a fixed date but none is set.")
    return out


def clean_profile(data: dict) -> dict:
    url = str(data.get("url", "")).strip()
    if url and not re.match(r"^https?://", url):
        raise ValueError("Navigation URL must start with http:// or https://")
    filters, seen = [], set()
    for i, f in enumerate(data.get("filters") or [], start=1):
        label = str(f.get("label", "")).strip()
        if not label:
            raise ValueError(f"Filter row {i} has no label.")
        if label.lower() in seen:
            raise ValueError(f"Filter '{label}' is listed twice.")
        seen.add(label.lower())
        ftype = f.get("type", "dropdown")
        if ftype not in FILTER_TYPES:
            raise ValueError(f"Filter '{label}' has an unknown type.")
        value = f.get("value", "")
        if ftype == "multiselect":
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            value = [str(v).strip() for v in value if str(v).strip()]
        elif ftype == "toggle":
            value = "on" if str(value).lower() in ("on", "true", "1", "yes") else "off"
        else:
            value = str(value or "").strip()
        mode = f.get("date_mode") or ("report_date" if ftype == "date" else "")
        if ftype == "date" and mode not in DATE_MODES:
            raise ValueError(f"Filter '{label}' has an unknown date option.")
        if ftype == "date" and mode == "fixed" and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            raise ValueError(f"Filter '{label}': pick the fixed date.")
        options = [str(o)[:120] for o in (f.get("options") or []) if str(o).strip()][:200]
        filters.append({"label": label, "type": ftype, "value": value, "date_mode": mode if ftype == "date" else "",
                        "required": bool(f.get("required")), "options": options})
    try:
        wait = int(data.get("wait_seconds", 60))
    except (TypeError, ValueError):
        raise ValueError("Wait time must be a whole number of seconds.")
    if not 5 <= wait <= 600:
        raise ValueError("Wait time must be between 5 and 600 seconds.")
    return {"url": url, "filters": filters, "search_text": str(data.get("search_text") or "SEARCH").strip() or "SEARCH",
            "export_text": str(data.get("export_text") or "Export").strip() or "Export", "wait_seconds": wait,
            "allow_empty": bool(data.get("allow_empty", True)), "enabled": bool(data.get("enabled", True))}


def save_profile(con, key, data):
    c = clean_profile(data)
    con.execute("UPDATE sfa_reports SET url=?, filters=?, search_text=?, export_text=?, wait_seconds=?, "
                "allow_empty=?, enabled=?, edited=1, updated_at=? WHERE key=?",
                (c["url"], json.dumps(c["filters"]), c["search_text"], c["export_text"], c["wait_seconds"],
                 int(c["allow_empty"]), int(c["enabled"]), now_iso(), key))
    con.commit()
    return get_profile(con, key)


def set_enabled(con, key, enabled):
    con.execute("UPDATE sfa_reports SET enabled=?, edited=1 WHERE key=?", (int(bool(enabled)), key))
    con.commit()


def reset_profile(con, key, default_paths):
    con.execute("UPDATE sfa_reports SET edited=0 WHERE key=?", (key,))
    init(con, default_paths)


def export_defaults(con, path) -> str:
    """Admin: current report profiles (+ login URL, never username/password) -> defaults file."""
    data = {"exported_at": now_iso(), "login_url": get_setting(con, "sfa_login_url", ""), "reports": {
        p["key"]: {k: p[k] for k in ("url", "filters", "search_text", "export_text", "wait_seconds",
                                     "allow_empty", "enabled")} for p in list_profiles(con)}}
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


def resolve_date(f, report_date: str) -> str:
    mode = f.get("date_mode") or "report_date"
    rd = date.fromisoformat(report_date)
    if mode == "today":
        return date.today().isoformat()
    if mode == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if mode == "month_start":
        return month_first(rd).isoformat()
    if mode == "month_end":
        return month_last(rd).isoformat()
    if mode == "fixed":
        return f.get("value") or report_date
    return rd.isoformat()


def expected_banner_checks(profile, report_date):
    checks = []
    for f in profile["filters"]:
        if f["type"] in ("dropdown", "text") and f["value"]:
            checks.append({"key": f["label"], "expect": f["value"]})
        if f["type"] == "date":
            checks.append({"key": f["label"], "expect": resolve_date(f, report_date), "is_date": True})
    return checks


# ================================================================== live state (polled by the UI)
class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self._s = {"active": False, "lanes": [], "log": [], "phase": "idle"}
        self._cancel = threading.Event()

    def reset(self, **kw):
        with self._lock:
            self._cancel.clear()
            self._s = {"active": True, "lanes": [], "log": [], "phase": "starting", "result": None,
                       "started_at": datetime.now().isoformat(timespec="seconds"), **kw}

    def set(self, **kw):
        with self._lock:
            self._s.update(kw)

    def lane(self, idx, **kw):
        with self._lock:
            if 0 <= idx < len(self._s["lanes"]):
                self._s["lanes"][idx].update(kw)

    def step(self, idx, step, state):
        with self._lock:
            if 0 <= idx < len(self._s["lanes"]):
                lane = self._s["lanes"][idx]
                lane["steps"][step] = state
                if state == "active":
                    lane["step"] = step

    def log(self, msg, lane=None, level="info"):
        with self._lock:
            self._s["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "lane": lane, "level": level, "msg": msg})
            self._s["log"] = self._s["log"][-400:]

    def snapshot(self):
        with self._lock:
            s = json.loads(json.dumps(self._s, default=str))
        for lane in s.get("lanes", []):
            if lane.get("started") and lane.get("status") in ("running", "retrying"):
                lane["elapsed"] = round(time.time() - lane["started"])
        return s

    def cancel(self):
        self._cancel.set()
        self.log("Stop requested.", level="warn")

    def cancelled(self):
        return self._cancel.is_set()


LIVE = LiveState()
_ACTIVE = {"loop": None, "browser": None}
_RUN_LOCK = threading.Lock()


def force_stop(timeout=10):
    was = bool(LIVE.snapshot().get("active")) or _ACTIVE["browser"] is not None
    if was:
        LIVE.cancel()
    loop, browser = _ACTIVE["loop"], _ACTIVE["browser"]
    if loop and browser:
        try:
            asyncio.run_coroutine_threadsafe(browser.close(), loop).result(timeout)
        except Exception:  # noqa: BLE001
            pass
    return was


def _new_lane(p):
    return {"key": p["key"], "name": p["name"], "status": "queued", "step": None,
            "steps": {s: "pending" for s in STEPS}, "attempt": 0, "max_attempts": 1, "message": "", "rows": None,
            "file": None, "screenshot": None, "shots": [], "started": None, "elapsed": 0, "checks": []}


# ================================================================== browser helpers (from Chimera)
async def _launch(p, browser_key, headless):
    if os.environ.get("HYDRA_TVA_TEST_HEADLESS"):
        headless = True
    if browser_key == "firefox":
        return await p.firefox.launch(headless=headless)
    channel = SUPPORTED_BROWSERS.get(browser_key, "msedge")
    try:
        return await p.chromium.launch(headless=headless, channel=channel)
    except Exception as first:  # noqa: BLE001
        try:
            return await p.chromium.launch(headless=headless)
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"Could not start {browser_key}: {str(first).splitlines()[0]}. Choose another browser "
                               "in SFA Connection.")


async def _settle(page, timeout_ms=8000):
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(400)


async def _login(page, cfg, timeout_ms):
    await page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=timeout_ms)
    pw = page.locator("input[type='password']").first
    try:
        await pw.wait_for(state="visible", timeout=15000)
    except Exception:  # noqa: BLE001
        await _settle(page)
        if await page.locator("input[type='password']").count() == 0:
            return True, "Already signed in."
        raise StepError("login", "The login form never appeared.")
    user = page.locator("input[name='username'], input[formcontrolname='username'], input[type='email'], "
                        "input[type='text']").first
    await user.fill(cfg["username"])
    await pw.fill(cfg["password"])
    await pw.press("Enter")
    try:
        await page.locator("input[type='password']").first.wait_for(state="hidden", timeout=25000)
    except Exception:  # noqa: BLE001
        raise StepError("login", "Still on the login page after submitting — check the SFA username and "
                                 "password in SFA Connection.", retry=False)
    await _settle(page)
    return True, "Logged in."


async def _ensure_logged_in(page, cfg, timeout_ms):
    if await page.locator("input[type='password']").count() and \
            await page.locator("input[type='password']").first.is_visible():
        await _login(page, cfg, timeout_ms)
        return True
    return False


async def _welcome(page):
    try:
        m = re.search(r"Welcome,?\s+([^\n]{2,60})", await page.inner_text("body"))
        return m.group(1).strip() if m else ""
    except Exception:  # noqa: BLE001
        return ""


_FIND_FIELD_JS = r"""
([label, token]) => {
  const norm = s => (s || '').replace(/[*:]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const target = norm(label);
  const ctrlSel = '.p-dropdown, .p-multiselect, .ng-select, select, textarea, .p-inputswitch, ' +
                  'input:not([type=hidden]):not([type=button]):not([type=submit])';
  const insidePanel = el => !!el.closest('.p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay');
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const topLevel = ctrls => ctrls.filter(c => !ctrls.some(o => o !== c && o.contains(c)));
  const kindOf = c => {
    if (c.matches('.p-dropdown')) return 'dropdown';
    if (c.matches('.p-multiselect')) return 'multiselect';
    if (c.matches('.ng-select')) return c.matches('.ng-select-multiple') ? 'multiselect' : 'ngselect';
    if (c.matches('select')) return c.multiple ? 'nativemulti' : 'select';
    if (c.matches('.p-inputswitch') || (c.matches('input') && ['checkbox', 'radio'].includes(c.type))) return 'toggle';
    if (c.matches('input[type=date]')) return 'date';
    if (c.closest('.p-calendar')) return 'calendar';
    return 'text';
  };
  const labels = [...document.querySelectorAll('label, span, div, p, h5, h6, b, strong, td, th, mat-label, legend')]
    .filter(el => norm(el.textContent) === target && visible(el) && !insidePanel(el)
                  && !el.closest('.p-dropdown, .p-multiselect, .ng-select, select, nav, header')
                  && ![...el.children].some(ch => norm(ch.textContent) === target));
  for (const el of labels) {
    if (el.tagName === 'LABEL' && el.htmlFor) {
      const c = document.getElementById(el.htmlFor);
      if (c) { const top = c.closest('.p-dropdown, .p-multiselect, .ng-select, .p-inputswitch') || c;
               top.setAttribute('data-chimera', token); return {found: true, kind: kindOf(top)}; }
    }
    let a = el;
    for (let i = 0; i < 5 && a; i++) {
      a = a.parentElement;
      if (!a) break;
      const ctrls = topLevel([...a.querySelectorAll(ctrlSel)].filter(c => !insidePanel(c) && visible(c)));
      const after = ctrls.filter(c => el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING);
      const pick = after[0] || null;
      if (pick) { pick.setAttribute('data-chimera', token); return {found: true, kind: kindOf(pick)}; }
    }
  }
  return {found: false};
}
"""

_PICK_OPTION_JS = r"""
([value, token, exactOnly]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const panels = [...document.querySelectorAll('%PANEL%')].filter(visible);
  const opts = panels.flatMap(p => [...p.querySelectorAll('%OPTION%')]).filter(visible);
  const v = norm(value);
  let hit = opts.find(o => norm(o.getAttribute('aria-label')) === v || norm(o.textContent) === v);
  if (!hit && !exactOnly) {
    const partial = opts.filter(o => norm(o.textContent).includes(v));
    if (partial.length === 1) hit = partial[0];
  }
  if (!hit) return {found: false, options: opts.slice(0, 40).map(o => o.textContent.replace(/\s+/g, ' ').trim())};
  hit.scrollIntoView({block: 'center'});
  hit.setAttribute('data-chimera-opt', token);
  const selected = hit.getAttribute('aria-selected') === 'true' || hit.classList.contains('p-highlight')
                   || hit.classList.contains('ng-option-selected');
  return {found: true, selected};
}
""".replace("%PANEL%", PANEL_SEL).replace("%OPTION%", OPTION_SEL)

_CLICKABLES_JS = r"""
([want, token, exportMode]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        && getComputedStyle(el).visibility !== 'hidden';
  const sel = 'button, a, [role=button], .p-button, .btn, input[type=button], input[type=submit], ' +
              '[ptooltip], [mattooltip], [data-pr-tooltip], [title]';
  const els = [...document.querySelectorAll(sel)].filter(el => visible(el) && !el.closest('nav, header, .navbar, .p-menubar'));
  // keep the outermost clickable (a <button> rather than the <i> icon inside it)
  const top = els.filter(el => !els.some(o => o !== el && o.contains(el) && o.matches('button, a, [role=button]')));
  const info = top.map(el => {
    const icons = [...el.querySelectorAll('i, span, svg, img')].map(i => (i.getAttribute('class') || '') + ' ' + (i.getAttribute('src') || '') + ' ' + (i.getAttribute('data-icon') || '')).join(' ');
    const words = [el.innerText || el.value || '', el.getAttribute('title'), el.getAttribute('aria-label'),
                   el.getAttribute('ptooltip'), el.getAttribute('mattooltip'), el.getAttribute('data-pr-tooltip'),
                   el.getAttribute('data-original-title')].filter(Boolean).join(' | ');
    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('p-disabled');
    return {el, words: norm(words), icons: norm(icons + ' ' + (el.getAttribute('class') || '')), disabled};
  }).filter(x => !x.disabled);
  const w = norm(want);
  if (/^css:/i.test(want || '')) {          // exact element chosen by the user, e.g. css:img[src*="excel"]
    let el = null; try { el = document.querySelector(want.slice(4).trim()); } catch (e) {}
    if (!el) return {found: false, seen: ['nothing matches ' + want.slice(4).trim()]};
    el.setAttribute('data-chimera-btn', token);
    return {found: true, label: want, seen: []};
  }
  let hit = info.find(x => x.words === w) || info.find(x => w && x.words.includes(w));
  if (!hit && exportMode) {
    hit = info.find(x => /excel|xlsx?\b/.test(x.icons + ' ' + x.words))
       || info.find(x => /export|download/.test(x.icons + ' ' + x.words));
  }
  // Export controls that aren't buttons at all: an Excel picture / icon /
  // background image the page made clickable (e.g. SalesPro's green "X"
  // sheet above the results table). Images may be blocked by the data saver
  // and have no size, so these don't have to be visible — they're clicked
  // with a JavaScript click if the normal click can't reach them.
  let iconHits = [];
  if (exportMode) {
    const describe = el => {
      const bg = getComputedStyle(el).backgroundImage || '';
      return norm([el.getAttribute('src'), el.getAttribute('srcset'), el.getAttribute('alt'), el.getAttribute('title'),
                   el.getAttribute('aria-label'), el.getAttribute('class'), el.getAttribute('id'), el.getAttribute('data-icon'),
                   el.getAttribute('href'), el.getAttribute('ptooltip'), el.getAttribute('mattooltip'),
                   el.getAttribute('data-pr-tooltip'), bg !== 'none' ? bg : '',
                   el.tagName === 'svg' ? (el.querySelector('title') || {}).textContent : ''].filter(Boolean).join(' '));
    };
    const cands = [...document.querySelectorAll('img, svg, i, span, div, a, input[type=image], mat-icon, em')]
      .filter(el => !el.closest('nav, header, .navbar, .p-menubar, .p-dropdown-panel, .p-multiselect-panel'))
      .map(el => ({el, d: describe(el)}))
      .filter(x => /excel|\bxlsx?\b|xls[-_.]|file-excel|spreadsheet/.test(x.d)
                || (/export|download/.test(x.d) && !/pdf|print|csv/.test(x.d)));
    // the innermost match, then its nearest clickable ancestor (a button, link or anything with a click cursor)
    const inner = cands.filter(x => !cands.some(o => o !== x && x.el.contains(o.el)));
    iconHits = inner.map(x => {
      let target = x.el;
      for (let p = x.el; p && p !== document.body; p = p.parentElement) {
        if (p.matches('button, a, [role=button], .p-button, .btn') || getComputedStyle(p).cursor === 'pointer') { target = p; break; }
      }
      return {el: target, label: x.d.slice(0, 80), visible: visible(target),
              excel: /excel|\bxlsx?\b|xls[-_.]|file-excel|spreadsheet/.test(x.d)};
    }).filter(x => !(x.el.disabled || x.el.getAttribute('aria-disabled') === 'true'));
    if (!hit) {
      const order = [...iconHits.filter(x => x.excel && x.visible), ...iconHits.filter(x => x.excel),
                     ...iconHits.filter(x => x.visible), ...iconHits];
      if (order.length) hit = {el: order[0].el, words: '', icons: '[excel icon] ' + order[0].label};
    }
  }
  const seen = info.map(x => (x.words || ('[icon: ' + (x.icons.match(/(pi|fa|mdi|material-icons)[-\w ]*/) || [x.icons.slice(0, 40)])[0] + ']')).slice(0, 50))
                   .filter(Boolean).slice(0, 25)
                   .concat(iconHits.slice(0, 5).map(x => '[icon: ' + x.label.slice(0, 50) + ']'));
  if (!hit && exportMode) {     // help the user name it: clickable pictures on the page
    [...document.querySelectorAll('img, input[type=image]')]
      .filter(i => !i.closest('nav, header, .navbar, .p-menubar') && (getComputedStyle(i).cursor === 'pointer'
               || (i.parentElement && getComputedStyle(i.parentElement).cursor === 'pointer')))
      .slice(0, 6).forEach(i => seen.push('[clickable picture: ' + (i.getAttribute('src') || '').split('/').pop().slice(0, 40)
                                         + ' → use css:img[src*="' + (i.getAttribute('src') || '').split('/').pop().slice(0, 30) + '"]]'));
  }
  if (!hit) return {found: false, seen};
  hit.el.setAttribute('data-chimera-btn', token);
  return {found: true, label: hit.words || hit.icons.slice(0, 60), seen};
}
"""

_DETECT_JS = r"""
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const insidePanel = el => !!el.closest('.p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay');
  const ctrlSel = '.p-dropdown, .p-multiselect, .ng-select, select, textarea, .p-inputswitch, ' +
                  'input:not([type=hidden]):not([type=button]):not([type=submit])';
  const all = [...document.querySelectorAll(ctrlSel)].filter(c => visible(c) && !insidePanel(c)
                && !c.closest('nav, header, .navbar, .p-menubar'));
  const top = all.filter(c => !all.some(o => o !== c && o.contains(c)));
  const kindOf = c => {
    if (c.matches('.p-dropdown')) return 'dropdown';
    if (c.matches('.p-multiselect')) return 'multiselect';
    if (c.matches('.ng-select')) return c.matches('.ng-select-multiple') ? 'multiselect' : 'dropdown';
    if (c.matches('select')) return c.multiple ? 'multiselect' : 'dropdown';
    if (c.matches('.p-inputswitch') || (c.matches('input') && ['checkbox', 'radio'].includes(c.type))) return 'toggle';
    if (c.matches('input[type=date]') || c.closest('.p-calendar')) return 'date';
    return 'text';
  };
  const textOf = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const out = [];
  top.forEach((c, i) => {
    let label = '';
    if (c.id) { const l = document.querySelector(`label[for="${c.id}"]`); if (l) label = textOf(l); }
    let a = c;
    for (let d = 0; d < 4 && !label && a; d++) {
      a = a.parentElement;
      if (!a) break;
      const walker = document.createTreeWalker(a, NodeFilter.SHOW_TEXT);
      let n, best = '';
      while ((n = walker.nextNode())) {
        const el = n.parentElement;
        if (!el || c.contains(el) || insidePanel(el)) continue;
        if (!(el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        const t = n.textContent.replace(/\s+/g, ' ').trim();
        if (t && t.length <= 40) best = t;   // the closest text before the control
      }
      if (best) label = best;
    }
    label = label.replace(/[*:]+$/, '').trim();
    let state = null;
    if (kindOf(c) === 'toggle') state = c.matches('input') ? c.checked :
        (c.classList.contains('p-inputswitch-checked') || !!c.querySelector('input:checked'));
    c.setAttribute('data-chimera-detect', String(i));
    out.push({idx: i, label, type: kindOf(c), toggled: state,
              native_options: c.matches('select') ? [...c.options].map(o => o.textContent.trim()).filter(Boolean) : null});
  });
  return out;
}
"""


async def _find_field(page, label):
    token = uuid.uuid4().hex[:10]
    res = await page.evaluate(_FIND_FIELD_JS, [label, token])
    if not res.get("found"):
        return None, None
    return page.locator(f"[data-chimera='{token}']").first, res["kind"]


async def _open_panel(page, ctrl, kind):
    target = ctrl.locator(".ng-select-container").first if kind in ("ngselect", "multiselect") and \
        await ctrl.locator(".ng-select-container").count() else ctrl
    await target.click()
    try:
        await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).first.wait_for(state="visible", timeout=8000)
    except Exception:  # noqa: BLE001
        await page.wait_for_timeout(500)


async def _type_in_panel_filter(page, ctrl, value):
    box = page.locator(".p-dropdown-filter:visible, .p-multiselect-filter:visible").first
    if await box.count():
        await box.fill(value)
        await page.wait_for_timeout(350)
        return True
    inner = ctrl.locator(".ng-select input").first if await ctrl.locator(".ng-select input").count() else \
        ctrl.locator("input[role=combobox], .ng-input input").first
    if await inner.count():
        await inner.fill(value)
        await page.wait_for_timeout(350)
        return True
    return False


async def _pick(page, value, exact_only=False):
    token = uuid.uuid4().hex[:10]
    res = await page.evaluate(_PICK_OPTION_JS, [value, token, exact_only])
    if not res.get("found"):
        return False, res.get("options", []), False
    if not res.get("selected"):
        await page.locator(f"[data-chimera-opt='{token}']").first.click()
    return True, [], res.get("selected", False)


async def _close_panel(page):
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)


async def _control_text(ctrl):
    try:
        return re.sub(r"\s+", " ", (await ctrl.inner_text()).strip())
    except Exception:  # noqa: BLE001
        return ""


async def fill_filter(page, f, report_date):
    """Fills one profile filter on the current page. Raises StepError with a clear reason."""
    label, ftype, value = f["label"], f["type"], f.get("value")
    if ftype != "date" and value in ("", None, []):
        return "left empty"
    ctrl, kind = await _find_field(page, label)
    if ctrl is None:
        raise StepError("filters", f"Couldn't find a filter labelled '{label}' on the page. Check the label "
                                   "spelling, or use 'Detect filters' to read the exact labels.")
    if ftype == "date":
        isod = resolve_date(f, report_date)
        if kind == "date":
            await ctrl.fill(isod)
        else:
            ph = (await ctrl.get_attribute("placeholder") or "").lower()
            y, m, d = isod.split("-")
            text = f"{m}/{d}/{y}" if ph.startswith("mm") else (f"{d}/{m}/{y}" if ph.startswith("dd") else isod)
            await ctrl.click()
            await ctrl.fill(text)
            await page.keyboard.press("Escape")
        try:
            await ctrl.blur()
        except Exception:  # noqa: BLE001
            pass
        got = await ctrl.input_value() if kind in ("date", "calendar", "text") else ""
        if kind == "date" and got != isod:
            raise StepError("filters", f"Date filter '{label}' didn't take the value {isod} (shows '{got}').")
        return isod

    if ftype == "toggle":
        want = value == "on"
        is_on = await ctrl.evaluate(
            "c => c.matches('input') ? c.checked : (c.classList.contains('p-inputswitch-checked') "
            "|| c.getAttribute('aria-checked') === 'true' || !!c.querySelector('input:checked'))")
        if is_on != want:
            await ctrl.click()
            await page.wait_for_timeout(400)
        return value

    if ftype == "text" or kind == "text":
        if kind not in ("text", "calendar"):
            raise StepError("filters", f"'{label}' is a {kind} on the page, not a text box — change its type.",
                            retry=False)
        await ctrl.fill(str(value))
        return value

    if kind in ("select", "nativemulti"):
        vals = value if isinstance(value, list) else [value]
        try:
            await ctrl.select_option(label=vals if kind == "nativemulti" else vals[0])
        except Exception:  # noqa: BLE001
            raise StepError("filters", f"'{', '.join(vals)}' is not an option of '{label}'.", retry=False)
        return value

    if kind not in ("dropdown", "multiselect", "ngselect"):
        raise StepError("filters", f"'{label}' is a {kind} on the page; set its type accordingly.", retry=False)

    values = value if isinstance(value, list) else [value]
    if kind in ("dropdown", "ngselect") and len(values) > 1:
        raise StepError("filters", f"'{label}' accepts one value only.", retry=False)

    await _open_panel(page, ctrl, kind)
    for v in values:
        await _type_in_panel_filter(page, ctrl, v)
        ok, options, _already = await _pick(page, v, exact_only=True)
        if not ok:
            await _close_panel(page)
            hint = f" Options seen: {', '.join(options[:12])}" if options else ""
            raise StepError("filters", f"'{v}' is not an option of '{label}'.{hint}", retry=False)
        if kind == "multiselect":
            box = page.locator(".p-multiselect-filter:visible").first
            if await box.count():
                await box.fill("")
    if kind == "multiselect" or await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).count():
        await _close_panel(page)
    shown = ""
    for _ in range(10):
        await page.wait_for_timeout(250)
        fresh, _k = await _find_field(page, label)
        shown = (await _control_text(fresh)).lower() if fresh is not None else ""
        if all(v.lower() in shown for v in values):
            return value
    raise StepError("filters", f"Picked '{', '.join(values)}' for '{label}' but the box shows '{shown or 'nothing'}'.")


async def _find_button(page, text, timeout_ms, export_mode=False):
    deadline = time.time() + timeout_ms / 1000
    seen = []
    while True:
        token = uuid.uuid4().hex[:10]
        res = await page.evaluate(_CLICKABLES_JS, [text, token, export_mode])
        seen = res.get("seen", [])
        if res.get("found"):
            return page.locator(f"[data-chimera-btn='{token}']").first, res.get("label", ""), seen
        if time.time() >= deadline:
            return None, "", seen
        await page.wait_for_timeout(500)


async def _click_and_download(page, el, timeout_ms):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def on_download(d):
        if not fut.done():
            fut.set_result(d)

    def on_page(p):
        p.on("download", on_download)

    page.on("download", on_download)
    page.context.on("page", on_page)
    try:
        try:
            await el.click(timeout=5000)
        except Exception:  # noqa: BLE001
            await el.evaluate("e => e.click()")
        return await asyncio.wait_for(fut, timeout_ms / 1000)
    except asyncio.TimeoutError:
        return None
    finally:
        page.remove_listener("download", on_download)
        page.context.remove_listener("page", on_page)


async def _shot(page, idx, name, folder, keep_b64=False):
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{idx + 1}_{name}.jpg")
        data = await page.screenshot(type="jpeg", quality=60, full_page=False)
        with open(path, "wb") as fh:
            fh.write(data)
        LIVE.lane(idx, screenshot=path)
        if keep_b64:
            with LIVE._lock:
                LIVE._s["lanes"][idx]["shots"].append({"step": name, "img": base64.b64encode(data).decode("ascii")})
        return path
    except Exception:  # noqa: BLE001
        return None


# ================================================================== checking a downloaded file
_BANNER_ALIASES = {"date from": ["date from", "from date"], "date to": ["date to", "to date"],
                   "from date": ["from date", "date from"], "to date": ["to date", "date to"]}


def read_banner(path) -> dict:
    """Key/value pairs from the SFA banner rows above the column headers."""
    from .parsers import _rows
    banner = {}
    for r in _rows(path)[:30]:
        cells = [str(c).strip() for c in r if c is not None and str(c).strip() != ""]
        if len(cells) >= 5 and all(c == c.upper() for c in cells) and "NOT SELECTED" not in cells:
            break  # the column-header row
        if len(cells) >= 2:
            for k, v in zip(cells[0::2], cells[1::2]):
                banner[k] = v
    return banner


def validate_download(path, profile, report_date) -> dict:
    from . import ingest
    from .util import parse_date
    checks, ok = [], True
    try:
        kind = ingest.detect(path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e), "checks": [], "kind": None}
    good = kind == profile["key"]
    checks.append({"what": "Report kind", "expect": REPORT_NAMES[profile["key"]],
                   "got": ingest.KIND_LABELS.get(kind, kind), "ok": good})
    ok &= good
    banner = {k.lower(): v for k, v in read_banner(path).items()}
    for c in expected_banner_checks(profile, report_date):
        keys = _BANNER_ALIASES.get(c["key"].lower(), [c["key"].lower()])
        got = next((banner[k] for k in keys if k in banner), None)
        if got is None:
            continue  # this report's banner doesn't print that filter
        if c.get("is_date"):
            d = parse_date(got)
            good = bool(d) and d.isoformat() == c["expect"]
        else:
            good = got.strip().lower() == str(c["expect"]).strip().lower()
        checks.append({"what": c["key"], "expect": c["expect"], "got": got, "ok": good})
        ok &= good
    msg = "File matches the profile." if ok else "Downloaded file doesn't match the profile: " + "; ".join(
        f"{c['what']} is '{c['got']}', expected '{c['expect']}'" for c in checks if not c["ok"])
    return {"ok": ok, "message": msg, "checks": checks, "kind": kind}


# ================================================================== one tab
async def _run_lane(context, cfg, profile, idx, report_date, opts, sem):
    retries, timeout_ms = opts["retries"], cfg["timeout_ms"]
    LIVE.lane(idx, max_attempts=retries + 1)
    async with sem:
        for attempt in range(1, retries + 2):
            if LIVE.cancelled():
                LIVE.lane(idx, status="cancelled", message="Cancelled before starting.")
                return
            LIVE.lane(idx, status="running", attempt=attempt, started=time.time(), message="")
            for s in STEPS[1:]:
                LIVE.step(idx, s, "pending")
            page = await context.new_page()
            current = "open"
            try:
                LIVE.step(idx, "open", "active")
                LIVE.log(f"Opening {profile['url']}", lane=profile["name"])
                await page.goto(profile["url"], wait_until="domcontentloaded", timeout=timeout_ms)
                await _settle(page)
                if await _ensure_logged_in(page, cfg, timeout_ms):
                    LIVE.log("Session had expired in this tab — logged in again.", lane=profile["name"])
                    await page.goto(profile["url"], wait_until="domcontentloaded", timeout=timeout_ms)
                    await _settle(page)
                first = next((f["label"] for f in profile["filters"]), None)
                if first:
                    deadline = time.time() + timeout_ms / 1000
                    while (await _find_field(page, first))[0] is None:
                        if time.time() > deadline:
                            raise StepError("open", f"The page loaded but the '{first}' filter never appeared. "
                                                    "Check the URL and the label spelling (Detect filters shows "
                                                    "the exact labels).")
                        if LIVE.cancelled():
                            raise Cancelled()
                        await page.wait_for_timeout(500)
                LIVE.step(idx, "open", "done")
                if opts["test"]:
                    await _shot(page, idx, "open", opts["shot_dir"], keep_b64=True)

                current = "filters"
                LIVE.step(idx, "filters", "active")
                for f in profile["filters"]:
                    if LIVE.cancelled():
                        raise Cancelled()
                    v = await fill_filter(page, f, report_date)
                    LIVE.log(f"{f['label']} = {v if not isinstance(v, list) else ', '.join(v)}", lane=profile["name"])
                LIVE.step(idx, "filters", "done")
                if opts["test"]:
                    await _shot(page, idx, "filters", opts["shot_dir"], keep_b64=True)

                current = "search"
                LIVE.step(idx, "search", "active")
                btn, _lbl, seen = await _find_button(page, profile["search_text"], 10000)
                if btn is None:
                    raise StepError("search", f"No '{profile['search_text']}' button found. Buttons on the page: "
                                              + ("; ".join(seen[:15]) or "none"), retry=False)
                await btn.click()
                await _settle(page, profile["wait_seconds"] * 1000)
                LIVE.step(idx, "search", "done")
                if opts["test"]:
                    await _shot(page, idx, "search", opts["shot_dir"], keep_b64=True)

                current = "export"
                LIVE.step(idx, "export", "active")
                parts = [p.strip() for p in profile["export_text"].split(">") if p.strip()]
                wait_ms = profile["wait_seconds"] * 1000
                for p_text in parts[:-1]:
                    el, _lbl, seen = await _find_button(page, p_text, wait_ms, export_mode=True)
                    if el is None:
                        raise StepError("export", f"No '{p_text}' button found. Buttons on the page: "
                                                  + "; ".join(seen[:15]))
                    await el.click()
                    await page.wait_for_timeout(500)
                el, matched, seen = await _find_button(page, parts[-1], wait_ms, export_mode=True)
                if el is not None and matched and parts[-1].lower() not in matched:
                    LIVE.log(f"No button says '{parts[-1]}' — using the one that looks like an export: "
                             f"'{matched[:50]}'.", lane=profile["name"], level="warn")
                if el is None:
                    body = await page.inner_text("body")
                    if NO_DATA_PATTERNS.search(body) and profile["allow_empty"]:
                        for s in ("export", "validate"):
                            LIVE.step(idx, s, "skipped")
                        LIVE.step(idx, "import", "active")
                        msg = "SFA has no records for this date."
                        if opts["import"]:
                            msg = opts["store_empty"](profile["key"], report_date) or msg
                        LIVE.step(idx, "import", "done")
                        LIVE.lane(idx, status="empty", rows=0, message=msg)
                        LIVE.log("No records in SFA for this date.", lane=profile["name"])
                        return
                    raise StepError("export", f"No '{parts[-1]}' button appeared within {profile['wait_seconds']}s "
                                              "after searching. Buttons Hydra can see: " + ("; ".join(seen[:25]) or
                                              "none") + ". Set the Export button text to one of these (or raise "
                                              "the wait time if SFA is still loading).")
                os.makedirs(opts["download_dir"], exist_ok=True)
                dl = await _click_and_download(page, el, wait_ms)
                if dl is None:
                    raise StepError("export", f"Clicked '{(matched or parts[-1])[:50]}' but no file downloaded within "
                                              f"{profile['wait_seconds']}s. If it opens a menu, set the export text "
                                              "to e.g. 'Export > Excel'; for big reports raise the wait time.")
                fail = await dl.failure()
                if fail:
                    raise StepError("export", f"Download failed: {fail}")
                ext = os.path.splitext(dl.suggested_filename or "")[1] or ".xlsx"
                path = os.path.join(opts["download_dir"], f"{profile['key']}{ext}")
                await dl.save_as(path)
                LIVE.lane(idx, file=path)
                LIVE.step(idx, "export", "done")
                LIVE.log(f"Downloaded {dl.suggested_filename or os.path.basename(path)}", lane=profile["name"])

                current = "validate"
                LIVE.step(idx, "validate", "active")
                v = validate_download(path, profile, report_date)
                LIVE.lane(idx, checks=v["checks"])
                if not v["ok"]:
                    raise StepError("validate", v["message"], retry=False)
                LIVE.step(idx, "validate", "done")

                current = "import"
                LIVE.step(idx, "import", "active")
                if not opts["import"]:
                    LIVE.step(idx, "import", "skipped")
                    LIVE.lane(idx, status="done", message="Downloaded and checked (test — not loaded).")
                    return
                res = opts["store"](profile["key"], path, report_date)
                LIVE.step(idx, "import", "done")
                LIVE.lane(idx, status="done", rows=res.get("rows"), message=res.get("message", ""))
                LIVE.log(res.get("message", "Loaded."), lane=profile["name"])
                for w in res.get("warnings", []):
                    LIVE.log("⚠ " + w, lane=profile["name"], level="warn")
                return

            except Cancelled:
                LIVE.step(idx, current, "failed")
                LIVE.lane(idx, status="cancelled", message="Cancelled.")
                return
            except StepError as e:
                if LIVE.cancelled():
                    LIVE.step(idx, e.step, "failed")
                    LIVE.lane(idx, status="cancelled", message="Cancelled.")
                    return
                LIVE.step(idx, e.step, "failed")
                shot = await _shot(page, idx, f"fail_{e.step}_try{attempt}", opts["shot_dir"], keep_b64=opts["test"])
                last = attempt >= retries + 1 or not e.retry
                LIVE.lane(idx, status="failed" if last else "retrying", message=e.message, screenshot=shot)
                LIVE.log(f"{STEP_LABELS[e.step]} failed: {e.message}" + ("" if last else " — retrying"),
                         lane=profile["name"], level="error" if last else "warn")
                if last:
                    return
            except Exception as e:  # noqa: BLE001
                LIVE.step(idx, current, "failed")
                if LIVE.cancelled():
                    LIVE.lane(idx, status="cancelled", message="Cancelled.")
                    return
                shot = await _shot(page, idx, f"fail_{current}_try{attempt}", opts["shot_dir"], keep_b64=opts["test"])
                msg = str(e).split("\n")[0][:300]
                if "Timeout" in msg and current == "open":
                    msg = "SFA took too long to open the report page."
                last = attempt >= retries + 1
                LIVE.lane(idx, status="failed" if last else "retrying", message=msg, screenshot=shot)
                LIVE.log(f"{STEP_LABELS.get(current, current)} error: {msg}" + ("" if last else " — retrying"),
                         lane=profile["name"], level="error" if last else "warn")
                if last:
                    return
            finally:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(2)


# ================================================================== whole run
async def _run_async(cfg, items, report_date, opts):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        LIVE.set(phase="logging_in")
        LIVE.log(f"Starting {cfg['browser']} ({'hidden' if opts['headless'] else 'visible'}).")
        browser = await _launch(p, cfg["browser"], opts["headless"])
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            if LIVE.cancelled():
                raise Cancelled()
            context = await browser.new_context(accept_downloads=True, viewport={"width": 1500, "height": 950})
            page = await context.new_page()
            for i, _p in items:
                LIVE.step(i, "login", "active")
            try:
                _ok, msg = await _login(page, cfg, cfg["timeout_ms"])
            except Exception as e:  # noqa: BLE001
                await _shot(page, items[0][0], "login_failed", opts["shot_dir"], keep_b64=True)
                m = e.message if isinstance(e, StepError) else f"Couldn't reach SFA: {str(e).splitlines()[0]}"
                for i, _p in items:
                    LIVE.step(i, "login", "failed")
                    LIVE.lane(i, status="failed", message=m)
                LIVE.log(m, level="error")
                return False
            w = await _welcome(page)
            LIVE.log(msg + (f" SFA shows “Welcome, {w}”." if w else ""))
            for i, _p in items:
                LIVE.step(i, "login", "done")
            await page.close()
            LIVE.set(phase="downloading")
            sem = asyncio.Semaphore(opts["parallel"])
            await asyncio.gather(*[_run_lane(context, cfg, prof, i, report_date, opts, sem) for i, prof in items])
            return True
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass


def run_downloads(con, lock, data_dir, keys, report_date: str, *, test=False, headless=None, store=None,
                  store_empty=None, after=None, trigger="manual"):
    """Synchronous entry point (run it in a background thread). Live progress is in LIVE.snapshot().
    store(key, path, report_date) -> {rows, message, warnings}; store_empty(key, report_date) -> message;
    after(result) -> runs when the downloads are finished (e.g. build the reports)."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    try:
        with lock:
            cfg = get_config(con)
            all_p = list_profiles(con)
        profiles = [p for p in all_p if p["key"] in keys and (p["enabled"] or test or len(keys) == 1)]
        LIVE.reset(trigger=trigger, report_date=report_date, test=test)
        LIVE.set(lanes=[_new_lane(p) for p in profiles])
        problem = None
        if not profiles:
            problem = "No enabled report profiles to download."
        elif not login_ready(cfg):
            problem = "SFA login isn't set up — enter the login URL, username and password in SFA Connection."
        if problem:
            for i in range(len(profiles)):
                LIVE.lane(i, status="failed", message=problem)
            LIVE.log(problem, level="error")
            res = {"ok": False, "status": "failed", "message": problem}
            LIVE.set(active=False, phase="done", result=res)
            return res
        runnable = []
        for i, p in enumerate(profiles):
            if p["problems"]:
                LIVE.lane(i, status="failed", message=" ".join(p["problems"]))
                for s in STEPS:
                    LIVE.step(i, s, "skipped")
                LIVE.log("Skipped — " + " ".join(p["problems"]), lane=p["name"], level="error")
            else:
                runnable.append(i)
        stamp = datetime.now().strftime("%H%M%S")
        locked_store = (lambda k, pth, rd: _locked(lock, store, k, pth, rd)) if store else None
        locked_empty = (lambda k, rd: _locked(lock, store_empty, k, rd)) if store_empty else (lambda k, rd: None)
        opts = {"retries": 0 if test else cfg["retries"], "parallel": cfg["parallel"],
                "headless": (not cfg["show_browser"]) if headless is None else bool(headless),
                "download_dir": os.path.join(str(data_dir), "downloads", report_date, stamp),
                "shot_dir": os.path.join(str(data_dir), "screenshots", report_date, stamp),
                "import": bool(store) and not test, "test": test, "store": locked_store, "store_empty": locked_empty}
        if runnable:
            asyncio.run(_run_async(cfg, [(i, profiles[i]) for i in runnable], report_date, opts))
        lanes = LIVE.snapshot()["lanes"]
        ok_lanes = [l for l in lanes if l["status"] in ("done", "empty")]
        cancelled = LIVE.cancelled()
        status = "cancelled" if cancelled else "success" if lanes and len(ok_lanes) == len(lanes) else \
            "partial" if ok_lanes else "failed"
        msg = f"{len(ok_lanes)} of {len(lanes)} report(s) downloaded."
        res = {"ok": status in ("success", "partial"), "status": status, "message": msg}
        if after and not test and ok_lanes and not cancelled:
            LIVE.set(phase="generating")
            try:
                extra = after(res, lanes)
                if extra:
                    res.update(extra)
            except Exception as e:  # noqa: BLE001
                LIVE.log(f"Building the reports failed: {e}", level="error")
        LIVE.log(f"Run finished: {status.upper()} — {msg}", level="info" if status == "success" else "warn")
        LIVE.set(active=False, phase="done", result=res)
        return res
    except Exception as e:  # noqa: BLE001
        msg = f"Unexpected error: {e}"
        LIVE.log(msg + "\n" + traceback.format_exc(limit=3), level="error")
        res = {"ok": False, "status": "failed", "message": msg}
        LIVE.set(active=False, phase="done", result=res)
        return res
    finally:
        _RUN_LOCK.release()


def _locked(lock, fn, *a):
    with lock:
        return fn(*a)


def start_background(**kw):
    if LIVE.snapshot().get("active"):
        return {"ok": False, "message": "An SFA run is already in progress."}
    LIVE.reset(trigger=kw.get("trigger", "manual"), report_date=kw.get("report_date"))
    threading.Thread(target=run_downloads, kwargs=kw, daemon=True, name="sfa-run").start()
    return {"ok": True}


# ================================================================== test connection & detect filters
async def _test_conn_async(cfg, headless):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await _launch(p, cfg["browser"], headless)
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            page = await (await browser.new_context(viewport={"width": 1400, "height": 900})).new_page()
            err = None
            try:
                await _login(page, cfg, cfg["timeout_ms"])
            except StepError as e:
                err = e.message
            except Exception as e:  # noqa: BLE001
                err = f"Couldn't reach SFA: {str(e).splitlines()[0]}"
            w = "" if err else await _welcome(page)
            shot = base64.b64encode(await page.screenshot(type="jpeg", quality=60)).decode("ascii")
            return err, w, shot
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass


def test_connection(con, lock, headless=None) -> dict:
    with lock:
        cfg = get_config(con)
    if not login_ready(cfg):
        return {"ok": False, "message": "Enter the login URL, username and password first."}
    try:
        err, w, shot = asyncio.run(_test_conn_async(cfg, (not cfg["show_browser"]) if headless is None else headless))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Connection attempt failed: {str(e).splitlines()[0]}"}
    if err:
        return {"ok": False, "message": err, "screenshot": shot}
    return {"ok": True, "message": "Logged in to SFA successfully" + (f" — SFA shows “Welcome, {w}”." if w else "."),
            "screenshot": shot}


async def _detect_async(cfg, url, headless, read_options):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await _launch(p, cfg["browser"], headless)
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            context = await browser.new_context(viewport={"width": 1500, "height": 950})
            page = await context.new_page()
            await _login(page, cfg, 60000)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await _settle(page, 15000)
            if await _ensure_logged_in(page, cfg, 60000):
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await _settle(page, 15000)
            fields = []
            for _ in range(20):
                fields = await page.evaluate(_DETECT_JS)
                if fields:
                    break
                await page.wait_for_timeout(750)
            for f in fields:
                f["options"] = f.pop("native_options") or []
                if read_options and f["type"] in ("dropdown", "multiselect") and not f["options"]:
                    ctrl = page.locator(f"[data-chimera-detect='{f['idx']}']").first
                    try:
                        await ctrl.click()
                        await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).first.wait_for(
                            state="visible", timeout=4000)
                        f["options"] = await page.evaluate(
                            "([ps, os]) => [...document.querySelectorAll(ps)].filter(p => p.offsetParent !== null)"
                            ".flatMap(p => [...p.querySelectorAll(os)]).map(o => o.textContent.replace(/\\s+/g,' ')"
                            ".trim()).filter(Boolean).slice(0, 200)", [PANEL_SEL, OPTION_SEL])
                    except Exception:  # noqa: BLE001
                        f["options"] = []
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(200)
            shot = await page.screenshot(type="jpeg", quality=55)
            return fields, base64.b64encode(shot).decode("ascii"), await page.title()
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass


def detect_filters(con, lock, url, headless=True, read_options=True) -> dict:
    """Opens the report page and lists its filters (label, type, options). Never changes anything in SFA."""
    with lock:
        cfg = get_config(con)
    if not login_ready(cfg):
        return {"ok": False, "message": "SFA login isn't set up — fill it in first."}
    if not re.match(r"^https?://", url or ""):
        return {"ok": False, "message": "Enter the report's URL first (starting with https://)."}
    try:
        fields, shot, title = asyncio.run(_detect_async(cfg, url, headless, read_options))
    except StepError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Couldn't read the page: {str(e).splitlines()[0]}"}
    fields = [f for f in fields if f["label"]]
    if not fields:
        return {"ok": False, "message": "No filters found on that page — is the URL right, and are the filters "
                                        "shown?", "screenshot": shot}
    return {"ok": True, "fields": fields, "screenshot": shot, "title": title,
            "message": f"Found {len(fields)} filter(s)."}


def log_text(s) -> str:
    out = [f"Hydra TvA SFA run · report date {s.get('report_date') or '-'} · {s.get('trigger') or ''}"]
    res = s.get("result") or {}
    if res:
        out.append(f"Result: {str(res.get('status', '')).upper()} — {res.get('message', '')}")
    out.append("")
    for l in s.get("lanes") or []:
        steps = " ".join(f"{k}:{v}" for k, v in (l.get("steps") or {}).items())
        out.append(f"[{l.get('status')}] {l.get('name')} — {l.get('message') or ''} {steps}")
    out.append("")
    for x in s.get("log") or []:
        out.append(f"{x['t']} {x['level'].upper():5} {('[' + x['lane'] + '] ') if x.get('lane') else ''}{x['msg']}")
    return "\n".join(out)
