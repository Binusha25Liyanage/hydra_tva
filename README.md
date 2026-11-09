# Hydra TvA — interim Target vs Achievement app

A small desktop app (Python + pywebview, same stack as Chimera/Hydra) that does the Famulus
Target vs Achievement process until Hydra launches. The `tva/` engine has no UI code, so it can be
copied into Hydra as a module later.

## What it does

Since v1.2 it **downloads the SFA files itself** the same way Chimera does (SFA Connection screen): each user enters
their own SFA username/password; the report page URLs and navigation steps come as defaults from the
admin and can be edited per user.

| When | Input (SFA / NSM) | Output |
|---|---|---|
| Start of month | Targets (Famulus TvA report or the app's Excel template), competition calendar | — |
| Daily | Outlet Wise SKU Sales, Callage Summary, Sales Order (pending) | Target vs Achievement, Day Report, Sales Comparison, Daily Winners, Pending Order Value |
| Month end | Rep Performance Report | Finalized Target vs Achievement (+ Days Worked and Reconciliation sheets) |

Checked against Famulus' 3 Nov 2026 outputs: all five daily reports match cell by cell. The only
differences are in Pre Selling territories, because Famulus downloaded their callage and pending
orders at a different time than the exports in the inputs folder (`tests/verify_nov03.py`).

## Setup (Windows)

1. Put this folder at `D:\projects\Hydra TvA\` (any path works).
2. Double-click `setup.bat` (creates `.venv` with Python 3.12, installs pywebview + openpyxl).
3. Double-click `run.bat`.
4. First run: **Settings → Import Famulus database…** and pick `target_report.db`, which brings in
   Sep–Oct history, NOWD, selling modes, outlet categories and limits.
5. **Targets** → month November 2026 → **Load target file…** → pick the Famulus
   `Target_vs_Achievement_2026-11-01_to_2026-11-03.xlsx`.

Optional: `build_exe.bat` makes `dist\Hydra TvA\Hydra TvA.exe`.

The database is `data\hydra_tva.db` next to `main.py` (or next to the exe). Reports go to
`Documents\Hydra TvA Reports` (change it in Settings).

## SFA automatic download (same approach as Chimera)

Hydra logs in **once**, then opens one tab per report (in parallel): it opens the report page,
sets each filter **by the label shown on the SalesPro screen** (PrimeNG dropdowns, multi-selects,
dates, text boxes, toggles), clicks **SEARCH**, then **Export** (or the green Excel icon),
checks the downloaded file against the filters (report kind, Report Type, Invoice Status, dates)
and loads it. A wrong file is rejected, not loaded. Each report succeeds or fails on its own,
with retries, a live progress view, screenshots on failure and a copyable log.

**SFA Connection screen**
* *SFA login*: Login URL, Username, Password, Browser (Edge / Chrome / Firefox) → **Save** →
  **Test connection** (shows the SFA “Welcome, …” text and a screenshot).
* *Report profiles* (Outlet Wise SKU Sales, Callage Summary, Pending Orders, Rep Performance):
  **Edit** → paste the report page URL → **✦ Detect filters from page** (reads the real labels and
  dropdown options) → tick the filters → choose values → **Save profile** → **Test** (runs it with
  screenshots of each step, nothing loaded).
* Date filters follow the run date: *Report date*, Today, Yesterday, *1st of month*, *Month end*
  (Rep Performance uses 1st of month → Month end), or Fixed.
* Export text: `Export`; if Export opens a menu use `Export > Excel`; one exact element:
  `css:img[src*="excel"]`.

Seeded profiles (check them with Detect filters):
| Report | Filters |
|---|---|
| Outlet Wise SKU Sales | Report Type = All Invoices, From Date, To Date |
| Callage Summary | From Date, To Date |
| Pending Orders (Sales Order) | Invoice Status = Pending, Date From, Date To |
| Rep Performance | From Date = 1st of month, To Date = month end |

**Daily**: Daily Reports → **⇩ Download from SFA** (with "then generate the reports").
**Month end**: Month End → **⇩ Download from SFA** (Rep Performance for the To date's month).

**Defaults for the exe (admin)**: SFA Connection → *Admin* → **Save current profiles as defaults**
→ `tva\sfa_defaults.json` (login URL + profiles, never usernames/passwords) → `build_exe.bat`.
Users' profiles follow the defaults until they edit one; **Reset to default** restores it.
Admin copy = running from source, or an `admin.flag` file next to the exe. A `sfa_defaults.json`
next to the exe overrides the bundled one.

Downloads: `data\downloads\<date>\<time>\`; failure screenshots: `data\screenshots\…`.
The password is encrypted with Windows DPAPI (only that Windows user on that PC can read it).

## Daily use

1. Daily Reports → pick the report date → **⇩ Download from SFA** (done), or:
2. **Add files…** → select the three SFA exports together. The type and date are read from each
   file. A sales export covering several days is split into days, which is handy for back-filling.
3. **Save to database** → **Generate reports**.
4. The month strip shows which days have Sales / Callage / Pending loaded. Any missing working day
   is listed as a warning when generating.

## Calculation rules (confirmed against Famulus)

* **Achievement** = SUM(SALES VALUE) of the Outlet Wise SKU Sales report, all outlet categories.
* **GT vs SMMT**: a territory with an SMMT target and no GT target reports in the SMMT columns.
* **Day target** = (monthly target − achievement before today) ÷ (NOWD − earlier working days on
  which the territory had a sale). The cumulative day target is the sum of the day targets of each
  working day so far. Once a territory has reached its target, its day target is 0.
* **NOWD and working days** come from the Calendar screen (November 2026 is preloaded from the NSM
  calendar: 24 working days, Round 1 = 8 days, Round 2 = 7 days, 24 Nov holiday).
* **Last month** = the same calendar date last month. "Cumulative" = 1st of last month to that date.
  Sales Comparison includes SMMT; the Day Report's last-month columns are GT only.
* **Daily Winners** (only on competition days): Sale Value = sales in outlet categories that count
  toward the competition. Bill Count = productive calls in that day's Callage. Winner = Sale ≥ limit
  AND Bills ≥ limit (Van 100,000 / 20, Pre 125,000 / 30). SMMT territories are not listed.
* **Pending Order Value** = Pre Selling territories, status Pending, NET AMOUNT, count of orders.
* **Finalized**: Total PC = Rep Performance `PRODUTIVE CALLS` (territory rows added together).
  AVG PC = Total PC ÷ days worked (callage days with PC > 0, counted per rep and added up).
  Subtotals use summed PC ÷ summed days.
  Percentages, variances, averages and subtotals are Excel formulas.

## Open points (settings exist; confirm with management)

* SMMT PC in subtotals: off by default (Settings → Finalized file).
* NATIONAL TOTAL row in the Finalized file: on by default.
* The Famulus database ends on 22 Oct. Load the 23–31 Oct daily exports so October comparisons and
  the October Finalized file are complete.
* The real "Target Edit file" layout hasn't been seen yet. For now targets load from the Famulus TvA
  report or the app's template (Targets → Edit in Excel).

## Project layout

```
main.py              app entry (pywebview window)
tva/                 engine: db, parsers, ingest, hierarchy, workcal, engine, reports/, api
tva/sfa.py           SFA download (Chimera engine): login, label-based filters, export, checks, live lanes
tva/secret.py        password encryption (Windows DPAPI)
tva/sfa_defaults.json  admin's default login URL + report profiles (Save current profiles as defaults)
ui/                  index.html, app.css, app.js
tests/verify_nov03.py  regression vs Famulus 3 Nov outputs
```
