/* Hydra TvA — UI logic. Talks to Python through window.pywebview.api */
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let API, APP = {};

async function call(name, ...args) {
  const r = await API[name](...args);
  if (r && typeof r === "object" && !Array.isArray(r) && r.error) { toast(r.error, true); throw new Error(r.error); }
  return r;
}
async function busy(text, fn) {
  $("#busyText").textContent = text; $("#busy").classList.add("on");
  try { return await fn(); } finally { $("#busy").classList.remove("on"); }
}
let toastT;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = err ? "err" : ""; t.style.display = "block";
  clearTimeout(toastT); toastT = setTimeout(() => (t.style.display = "none"), err ? 7000 : 3500);
}
const ym = (s) => [parseInt(s.slice(0, 4)), parseInt(s.slice(5, 7))];
const monthOf = (d) => d.slice(0, 7);
function lastDay(y, m) { return new Date(y, m, 0).getDate(); }
function pad(n) { return String(n).padStart(2, "0"); }

/* ------------------------------------------------------------------ navigation */
const loaders = {};
document.querySelectorAll(".sidebar a").forEach((a) => a.addEventListener("click", () => show(a.dataset.view)));
function show(v) {
  document.querySelectorAll(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("active", s.id === "view-" + v));
  loaders[v] && loaders[v]();
}

/* ------------------------------------------------------------------ previews (shared) */
function renderPreviews(el, list, onSave) {
  if (!list.length) { el.innerHTML = ""; return; }
  let h = `<table class="mini preview"><tr><th>File</th><th>Detected</th><th>Details</th></tr>`;
  for (const p of list) {
    if (p.error) { h += `<tr><td class="file">${esc(p.file)}</td><td colspan="2" class="err">${esc(p.error)}</td></tr>`; continue; }
    const conf = p.conflicts.length ? `<div class="warn">Already loaded: ${esc(p.conflicts.join(", "))}</div>` : "";
    const w = p.warnings.map((x) => `<div class="warn">⚠ ${esc(x)}</div>`).join("");
    h += `<tr><td class="file">${esc(p.file)}</td><td>${esc(p.kind_label)}</td><td>${p.info.map(esc).join("<br>")}${conf}${w}</td></tr>`;
  }
  h += `</table>`;
  const ok = list.filter((p) => p.id);
  if (ok.length) h += `<div class="row"><button class="btn primary" id="${el.id}Save">Save ${ok.length} file(s) to database</button><button class="btn ghost" id="${el.id}Clear">Clear</button></div>`;
  el.innerHTML = h;
  if (ok.length) {
    $(`#${el.id}Save`).onclick = () => onSave(ok.map((p) => p.id));
    $(`#${el.id}Clear`).onclick = () => (el.innerHTML = "");
  }
}
function renderResults(el, res) {
  let h = "";
  for (const r of res.results) {
    h += `<div class="result"><div><div class="name">${esc(r.report)}</div><div class="sum">${esc(r.summary || "")}</div></div>`;
    h += r.path ? `<div class="row"><button class="btn tiny" data-open="${esc(r.path)}">Open</button><button class="btn tiny ghost" data-folder="${esc(r.path)}">Show</button></div>` : `<span class="muted small">skipped</span>`;
    h += `</div>`;
  }
  if (res.warnings && res.warnings.length) h += `<div class="notes">${res.warnings.map((w) => `<div>⚠ ${esc(w)}</div>`).join("")}</div>`;
  el.innerHTML = h;
  el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => call("open_path", b.dataset.open)));
  el.querySelectorAll("[data-folder]").forEach((b) => (b.onclick = () => call("open_folder", b.dataset.folder)));
}

/* ------------------------------------------------------------------ DAILY */
async function loadDaily() {
  const d = $("#dDate").value; if (!d) return;
  const s = await call("status", d);
  const chip = (ok, txt) => `<span class="chip ${ok ? "ok" : "no"}">${ok ? "✓" : "✗"} ${txt}</span>`;
  let h = chip(s.targets, "Targets") + chip(s.sales, "Sales") + chip(s.callage, "Callage") + chip(s.pending, "Pending orders")
    + chip(s.prev_sales, "Last month sales (" + s.prev_date + ")")
    + `<span class="chip info">NOWD ${s.nowd}${s.calendar_configured ? "" : " (calendar not set)"}</span>`;
  if (!s.is_working) h += `<span class="chip no">Non-working day</span>`;
  else if (s.calendar.is_competition) h += `<span class="chip comp">Competition Round ${s.calendar.round} · day ${s.calendar.round_day} of ${s.calendar.round_days}</span>`;
  else h += `<span class="chip info">Not a competition day</span>`;
  $("#dChips").innerHTML = h;
  const [y, m] = ym(d);
  const mo = await call("month_overview", y, m);
  let g = DOW.map((x) => `<div class="dh">${x}</div>`).join("");
  const first = mo.days[0].dow;
  for (let i = 0; i < first; i++) g += `<div></div>`;
  for (const x of mo.days) {
    const cls = ["md", !x.working ? "off" : x.competition ? "comp" + x.round : "", x.date === d ? "sel" : ""].join(" ");
    const dot = (on, c) => `<span class="dot ${on ? c : "miss"}"></span>`;
    g += `<div class="${cls}" data-d="${x.date}"><div class="n">${x.day}</div><div class="dots">${dot(x.sales, "s")}${dot(x.callage, "c")}${dot(x.pending, "p")}</div></div>`;
  }
  $("#dMonth").innerHTML = g;
  $("#dMonth").querySelectorAll("[data-d]").forEach((e) => (e.onclick = () => { $("#dDate").value = e.dataset.d; loadDaily(); }));
}
loaders.daily = loadDaily;
$("#dDate").addEventListener("change", loadDaily);
$("#dAdd").onclick = async () => {
  const files = await call("pick_files"); if (!files.length) return;
  const list = await busy("Reading files…", () => call("preview_files", files, {}));
  renderPreviews($("#dPreview"), list, async (ids) => {
    const r = await busy("Saving…", () => call("commit_previews", ids, $("#dReplace").checked));
    $("#dPreview").innerHTML = `<div class="okt">${r.messages.map(esc).join("<br>")}</div>`;
    const days = [...new Set(list.filter((p) => p.id).flatMap((p) => p.dates))].sort();
    if (days.length) $("#dDate").value = days[days.length - 1];
    loadDaily();
  });
};
$("#dGen").onclick = async () => {
  const which = [...document.querySelectorAll("#dWhich input:checked")].map((i) => i.value);
  if (!which.length) return toast("Select at least one report.", true);
  const res = await busy("Generating reports…", () => call("generate_daily", $("#dDate").value, which, $("#dForce").checked));
  renderResults($("#dResults"), res);
  toast("Reports generated.");
};
$("#dOpenOut").onclick = () => call("open_folder", null);

/* ------------------------------------------------------------------ MONTH END */
async function loadMonthEnd() {
  const [y, m] = ym($("#mMonth").value);
  if (!$("#mFrom").value || monthOf($("#mFrom").value) !== $("#mMonth").value) {
    $("#mFrom").value = `${y}-${pad(m)}-01`; $("#mTo").value = `${y}-${pad(m)}-${pad(lastDay(y, m))}`;
  }
  const mo = await call("month_overview", y, m);
  const sales = mo.days.filter((x) => x.sales).length, work = mo.days.filter((x) => x.working).length;
  $("#mRepStatus").innerHTML = (mo.rep_perf ? `<span class="okt">✓ Rep Performance loaded: ${esc(mo.rep_perf.source_filename)}</span>` : `<span class="err">✗ No Rep Performance Report for this month yet.</span>`)
    + `<br>Sales loaded for ${sales} days (working days: ${work}). ` + (mo.targets ? "" : `<span class="err">No targets for this month.</span>`);
}
loaders.monthend = loadMonthEnd;
$("#mMonth").addEventListener("change", () => { $("#mFrom").value = ""; loadMonthEnd(); });
$("#mAdd").onclick = async () => {
  const files = await call("pick_files"); if (!files.length) return;
  const list = await busy("Reading…", () => call("preview_files", files, { kind: "rep_perf", month: $("#mMonth").value }));
  renderPreviews($("#mPreview"), list, async (ids) => {
    const r = await call("commit_previews", ids, $("#mReplace").checked);
    $("#mPreview").innerHTML = `<div class="okt">${r.messages.map(esc).join("<br>")}</div>`; loadMonthEnd();
  });
};
$("#mGen").onclick = async () => {
  const res = await busy("Building the Finalized file…", () => call("generate_finalized", $("#mFrom").value, $("#mTo").value));
  renderResults($("#mResults"), res); toast("Finalized file generated.");
};

/* ------------------------------------------------------------------ CALENDAR */
let CAL = null;
async function loadCalendar() {
  const [y, m] = ym($("#cMonth").value);
  CAL = await call("calendar_get", y, m); drawCalendar();
}
function cdState(x) { return !x.working ? "holiday" : x.competition ? "r" + (x.round || 1) : "working"; }
function drawCalendar() {
  let g = DOW.map((x) => `<div class="dh">${x}</div>`).join("");
  for (let i = 0; i < CAL.days[0].dow; i++) g += `<div></div>`;
  CAL.days.forEach((x, i) => {
    const st = cdState(x);
    const cls = st === "holiday" ? "off" : st === "r1" ? "comp1" : st === "r2" ? "comp2" : "";
    const lab = { holiday: x.dow === 6 ? "Sunday" : "Holiday", working: "Working", r1: "Competition · R1", r2: "Competition · R2" }[st];
    g += `<div class="cd ${cls}" data-i="${i}"><div class="n">${x.day}</div><div class="k">${lab}</div></div>`;
  });
  $("#cGrid").innerHTML = g;
  $("#cGrid").querySelectorAll("[data-i]").forEach((e) => (e.onclick = () => {
    const x = CAL.days[+e.dataset.i];
    const next = { working: "r1", r1: "r2", r2: "holiday", holiday: "working" }[cdState(x)];
    x.working = next !== "holiday"; x.competition = next === "r1" || next === "r2"; x.round = next === "r1" ? 1 : next === "r2" ? 2 : null;
    drawCalendar();
  }));
  const w = CAL.days.filter((x) => x.working).length, r1 = CAL.days.filter((x) => x.competition && x.round === 1).length,
    r2 = CAL.days.filter((x) => x.competition && x.round === 2).length;
  $("#cSummary").innerHTML = `Working days (NOWD): <b>${w}</b> &nbsp;·&nbsp; Round 1: <b>${r1}</b> days &nbsp;·&nbsp; Round 2: <b>${r2}</b> days` + (CAL.configured ? "" : ` &nbsp;<span class="warn">(not saved yet: showing Mon–Sat default)</span>`);
}
loaders.calendar = loadCalendar;
$("#cMonth").addEventListener("change", loadCalendar);
$("#cReset").onclick = () => { CAL.days.forEach((x) => { x.working = x.dow !== 6; x.competition = false; x.round = null; }); drawCalendar(); };
$("#cSave").onclick = async () => {
  const [y, m] = ym($("#cMonth").value);
  const r = await call("calendar_save", y, m, CAL.days); CAL.configured = true; drawCalendar(); toast(`Calendar saved. NOWD = ${r.nowd}.`);
};

/* ------------------------------------------------------------------ TARGETS */
async function loadTargets() {
  const [y, m] = ym($("#tMonth").value);
  const t = await call("targets_get", y, m);
  if (!t.loaded) {
    $("#tStatus").innerHTML = `<span class="err">No targets for ${$("#tMonth").value}.</span> Load a file or copy last month.`;
    $("#tTable").innerHTML = ""; return;
  }
  const u = t.upload;
  $("#tStatus").innerHTML = `GT target <b>${fmt(u.gt_total)}</b> · SMMT target <b>${fmt(u.smmt_total)}</b> · ${t.rows.filter((r) => r.territory).length} territories<br><span class="muted small">Source: ${esc(u.source_filename)} · ${esc(u.uploaded_at)}</span>`;
  let h = `<tr><th>Hierarchy</th><th>Level</th><th style="text-align:right">GT Target</th><th style="text-align:right">SMMT Target</th></tr>`;
  const lv = ["National", "Region", "RSM", "ASM", "DTM", "ASE", "Territory"];
  for (const r of t.rows) {
    const cls = r.level === 0 ? "parent national" : r.territory ? "" : "parent";
    const name = `<span style="padding-left:${r.depth * 16}px">${esc(r.display)}</span>` + (r.display !== r.label ? ` <span class="muted small">(${esc(r.label)})</span>` : "") + (r.smmt ? `<span class="badge">SMMT</span>` : "");
    if (r.territory) {
      h += `<tr><td>${name}</td><td>${lv[r.level]}</td><td class="num"><input type="number" step="1000" value="${r.gt}" data-l="${esc(r.label)}" data-f="gt"></td><td class="num"><input type="number" step="1000" value="${r.sm}" data-l="${esc(r.label)}" data-f="sm"></td></tr>`;
    } else h += `<tr class="${cls}"><td>${name}</td><td>${lv[r.level]}</td><td class="num">${fmt(r.gt)}</td><td class="num">${fmt(r.sm)}</td></tr>`;
  }
  $("#tTable").innerHTML = h;
  $("#tTable").querySelectorAll("input").forEach((i) => (i.onchange = async () => {
    const row = i.closest("tr"), gt = row.querySelector('[data-f="gt"]').value, sm = row.querySelector('[data-f="sm"]').value;
    await call("targets_set", y, m, i.dataset.l, gt, sm); toast(`Saved ${i.dataset.l}`); loadTargets();
  }));
}
loaders.targets = loadTargets;
$("#tMonth").addEventListener("change", loadTargets);
$("#tLoad").onclick = async () => {
  const files = await call("pick_files"); if (!files.length) return;
  const list = await busy("Reading…", () => call("preview_files", files.slice(0, 1), { kind: "target", month: $("#tMonth").value }));
  renderPreviews($("#tPreview"), list, async (ids) => {
    const r = await call("commit_previews", ids, true);
    $("#tPreview").innerHTML = `<div class="okt">${r.messages.map(esc).join("<br>")}</div>`; loadTargets();
  });
};
$("#tCopy").onclick = async () => {
  const [y, m] = ym($("#tMonth").value);
  if (!confirm("Copy last month's hierarchy and targets into " + $("#tMonth").value + "? Existing targets for this month are replaced.")) return;
  await call("targets_copy", y, m); toast("Copied."); loadTargets();
};
$("#tTemplate").onclick = async () => {
  const [y, m] = ym($("#tMonth").value);
  const r = await call("targets_template", y, m);
  toast("Template opened. Edit, save, then use 'Load target file…' to load it back.");
};

/* ------------------------------------------------------------------ SETTINGS */
async function loadSettings() {
  const s = await call("settings_get");
  $("#sOut").textContent = s.settings.output_folder; $("#sPrefix").value = s.settings.title_prefix || "";
  $("#sNat").checked = s.settings.include_national_in_finalized === "1";
  $("#sSmmtPc").checked = s.settings.include_smmt_pc_in_subtotals === "1";
  $("#sTol").value = s.settings.recon_tolerance; $("#sDb").textContent = s.db;
  $("#lVanS").value = s.limits.van_sale_limit; $("#lVanB").value = s.limits.van_bill_limit;
  $("#lPreS").value = s.limits.pre_sale_limit; $("#lPreB").value = s.limits.pre_bill_limit;
  $("#sModes").innerHTML = `<tr><th>Territory</th><th>Selling mode</th></tr>` + s.territories.map((t) =>
    `<tr><td>${esc(t.label)}</td><td><select data-t="${esc(t.label)}"><option value="">— not set —</option><option value="van" ${t.mode === "van" ? "selected" : ""}>Van Selling</option><option value="pre" ${t.mode === "pre" ? "selected" : ""}>Pre Selling</option></select></td></tr>`).join("");
  $("#sModes").querySelectorAll("select").forEach((e) => (e.onchange = async () => { if (e.value) { await call("mode_set", e.dataset.t, e.value); toast("Saved."); } }));
  $("#sCats").innerHTML = `<tr><th>In competition</th><th>Outlet category</th><th>Pricing</th></tr>` + s.categories.map((c) =>
    `<tr><td><input type="checkbox" data-k="${esc(c.category_key)}" ${c.include_in_competition ? "checked" : ""}></td><td>${esc(c.category_label)}</td><td>${esc(c.pricing_scheme || "")}</td></tr>`).join("");
  $("#sCats").querySelectorAll("input").forEach((e) => (e.onchange = async () => { await call("category_set", e.dataset.k, e.checked); toast("Saved."); }));
  $("#aTerr").innerHTML = s.territories.map((t) => `<option>${esc(t.label)}</option>`).join("");
  $("#sAliases").innerHTML = s.aliases.length ? `<tr><th>Name in SFA</th><th>Territory</th><th></th></tr>` + s.aliases.map((a) =>
    `<tr><td>${esc(a.alias_label)}</td><td>${esc(a.territory_label)}</td><td><button class="btn tiny ghost" data-k="${esc(a.alias_key)}">Remove</button></td></tr>`).join("") : `<tr><td class="muted small">No aliases.</td></tr>`;
  $("#sAliases").querySelectorAll("[data-k]").forEach((b) => (b.onclick = async () => { await call("alias_delete", b.dataset.k); loadSettings(); }));
  $("#sOverrides").innerHTML = s.overrides.length ? `<tr><th>Label in targets</th><th>Printed as</th><th></th></tr>` + s.overrides.map((o) =>
    `<tr><td>${esc(o.original_label)}</td><td>${esc(o.display_label)}</td><td><button class="btn tiny ghost" data-k="${esc(o.original_key)}">Remove</button></td></tr>`).join("") : `<tr><td class="muted small">No overrides.</td></tr>`;
  $("#sOverrides").querySelectorAll("[data-k]").forEach((b) => (b.onclick = async () => { await call("override_delete", b.dataset.k); loadSettings(); }));
}
loaders.settings = loadSettings;
$("#sSave").onclick = async () => {
  await call("settings_save", { title_prefix: $("#sPrefix").value, include_national_in_finalized: $("#sNat").checked ? "1" : "0",
    include_smmt_pc_in_subtotals: $("#sSmmtPc").checked ? "1" : "0", recon_tolerance: $("#sTol").value || "1000" });
  toast("Settings saved.");
};
$("#sOutChange").onclick = async () => { const r = await call("choose_output_folder"); if (r.path) loadSettings(); };
$("#sOutOpen").onclick = () => call("open_folder", null);
$("#lSave").onclick = async () => {
  await call("limits_save", { van_sale_limit: $("#lVanS").value, van_bill_limit: $("#lVanB").value, pre_sale_limit: $("#lPreS").value, pre_bill_limit: $("#lPreB").value });
  toast("Limits saved.");
};
$("#aAdd").onclick = async () => { if (!$("#aAlias").value.trim()) return; await call("alias_add", $("#aAlias").value, $("#aTerr").value); $("#aAlias").value = ""; loadSettings(); };
$("#oAdd").onclick = async () => { if (!$("#oOrig").value.trim() || !$("#oDisp").value.trim()) return; await call("override_add", $("#oOrig").value, $("#oDisp").value); $("#oOrig").value = $("#oDisp").value = ""; loadSettings(); };
$("#sFamulus").onclick = async () => {
  const r = await busy("Importing Famulus data…", () => call("import_famulus"));
  if (r.cancelled) return;
  $("#sFamRes").textContent = `Imported ${r.sales_days} sales days, ${r.callage_days} callage days, ${r.pending_days} pending days, ${r.target_months} target months. Skipped ${r.skipped_days} days already loaded.`;
  loadSettings();
};

/* ------------------------------------------------------------------ HISTORY */
async function loadHistory() {
  const h = await call("history");
  $("#hTable").innerHTML = `<tr><th>Generated</th><th>Report</th><th>Period</th><th></th></tr>` + h.map((r) =>
    `<tr><td>${esc(r.created_at.replace("T", " "))}</td><td>${esc(r.report)}</td><td>${esc(r.period)}</td><td><button class="btn tiny" data-open="${esc(r.path)}">Open</button> <button class="btn tiny ghost" data-folder="${esc(r.path)}">Show</button></td></tr>`).join("");
  $("#hTable").querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => call("open_path", b.dataset.open)));
  $("#hTable").querySelectorAll("[data-folder]").forEach((b) => (b.onclick = () => call("open_folder", b.dataset.folder)));
}
loaders.history = loadHistory;

/* ------------------------------------------------------------------ SFA (Chimera approach) */
const SF = { ov: null, draft: null, poll: null, monitorOpen: false, onDone: null, busyModal: false };
const STEP_ORDER = ["login", "open", "filters", "search", "export", "validate", "import"];
const STEP_LABEL = { login: "Log in", open: "Open", filters: "Filters", search: "Search", export: "Export", validate: "Check", import: "Load" };
const CHIPS = { done: ["ok", "Done"], empty: ["ok", "No data"], failed: ["bad", "Failed"], retrying: ["warn", "Retrying"], running: ["warn", "Running"],
  queued: ["grey", "Queued"], cancelled: ["grey", "Cancelled"], success: ["ok", "Success"], partial: ["warn", "Partial"] };
const chipx = (s) => { const c = CHIPS[s] || ["grey", s || "—"]; return `<span class="chipx ${c[0]}">${esc(c[1])}</span>`; };
const DATE_MODE_LABEL = { report_date: "Report date", today: "Today", yesterday: "Yesterday", month_start: "1st of month", month_end: "Month end", fixed: "Fixed" };

// ---- pop-ups
function openModal({ title, body, footer = "", size = "", onClose = null }) {
  $("#modal").className = "modal " + size; $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body; $("#modal-footer").innerHTML = footer;
  $("#modal-backdrop").style.display = "flex"; SF.onClose = onClose;
}
function closeModal(force = false) {
  if (SF.busyModal && !force) return;
  $("#modal-backdrop").style.display = "none"; const cb = SF.onClose; SF.onClose = null; if (cb) cb();
}
$("#modal-x").onclick = () => closeModal();
$("#modal-backdrop").addEventListener("mousedown", (e) => { if (e.target.id === "modal-backdrop") closeModal(); });

// ---- SFA screen
async function loadSfa() {
  SF.ov = await call("sfa_overview");
  const c = SF.ov.config;
  $("#sfaUrl").value = c.login_url || ""; $("#sfaUser").value = c.username || ""; $("#sfaPass").value = "";
  $("#sfaPass").placeholder = SF.ov.has_password ? "•••••••• (saved — type to change)" : "not set";
  $("#sfaBrowser").value = c.browser; $("#sfaPar").value = c.parallel; $("#sfaRet").value = c.retries;
  $("#sfaTmo").value = Math.round(c.timeout_ms / 1000); $("#sfaShow").checked = c.show_browser;
  $("#sfaLoginWarn").innerHTML = SF.ov.login_ready ? "" : `<div class="problem-box">SFA login isn't set up yet — enter the login URL, username and password, then Save and Test connection.</div>`;
  $("#sfaDefSrc").textContent = SF.ov.defaults_source;
  $("#sfaAdmin").style.display = SF.ov.admin ? "block" : "none";
  renderSfaRows();
}
loaders.sfa = loadSfa;
function filterSummary(p) {
  const parts = p.filters.filter(f => f.type === "date" || (f.value && f.value.length)).map(f => {
    const v = f.type === "date" ? (f.date_mode === "fixed" ? f.value : DATE_MODE_LABEL[f.date_mode || "report_date"].toLowerCase())
      : Array.isArray(f.value) ? f.value.join(", ") : f.value;
    return `${esc(f.label)} = <b>${esc(v)}</b>`; });
  return parts.length ? parts.join("<br>") : '<span class="muted small">no filters</span>';
}
function renderSfaRows() {
  $("#sfaRows").innerHTML = SF.ov.profiles.map(p => `
    <tr data-k="${p.key}">
      <td><label class="switch2"><input type="checkbox" data-act="enable" ${p.enabled ? "checked" : ""}><span></span></label></td>
      <td><div class="p-name">${esc(p.name)}<span class="tagb ${p.edited ? "ed" : "def"}">${p.edited ? "edited" : "default"}</span></div>
          ${p.problems.length ? `<div class="p-desc err">${esc(p.problems[0])}</div>` : `<div class="p-desc">Search “${esc(p.search_text)}” → Export “${esc(p.export_text)}”</div>`}</td>
      <td class="url-cell">${p.url ? `<span class="mono">${esc(p.url)}</span>` : '<span class="chipx bad">URL needed</span>'}</td>
      <td style="font-size:12px">${filterSummary(p)}</td>
      <td><div class="row-actions"><button class="btn tiny" data-act="edit">Edit</button>
        <button class="btn tiny" data-act="test" ${p.problems.length ? "disabled title='Fix the profile first'" : ""}>Test</button>
        <button class="btn tiny ghost" data-act="reset" ${p.edited ? "" : "disabled"}>Reset to default</button></div></td>
    </tr>`).join("");
}
$("#sfaRows").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const k = b.closest("tr").dataset.k, p = SF.ov.profiles.find(x => x.key === k);
  if (b.dataset.act === "edit") openProfileEditor(p);
  if (b.dataset.act === "test") openTestRun(p);
  if (b.dataset.act === "reset" && confirm(`Reset ${p.name} to the default profile?`)) { await call("sfa_reset_profile", k); toast("Reset to default."); loadSfa(); }
});
$("#sfaRows").addEventListener("change", async (e) => {
  if (e.target.dataset.act !== "enable") return;
  await call("sfa_set_enabled", e.target.closest("tr").dataset.k, e.target.checked); loadSfa();
});
async function saveSfaConfig() {
  await call("sfa_save_config", { login_url: $("#sfaUrl").value, username: $("#sfaUser").value, password: $("#sfaPass").value,
    browser: $("#sfaBrowser").value, parallel: $("#sfaPar").value, retries: $("#sfaRet").value, timeout: $("#sfaTmo").value, show_browser: $("#sfaShow").checked });
}
$("#sfaSave").onclick = $("#sfaSave2").onclick = async () => { await saveSfaConfig(); toast("SFA settings saved."); loadSfa(); };
$("#sfaTest").onclick = async () => {
  await saveSfaConfig();
  $("#sfaTestRes").innerHTML = `<div class="ok-box" style="background:#f6f6f7;color:#444;border-color:#ddd"><span class="spin"></span> Logging in to SFA… (about 10–30 seconds)</div>`;
  $("#sfaTest").disabled = true;
  let r; try { r = await API.sfa_test_connection($("#sfaTestShow").checked); } finally { $("#sfaTest").disabled = false; }
  $("#sfaTestRes").innerHTML = `<div class="${r.ok ? "ok-box" : "problem-box"}">${esc(r.message || r.error)}</div>` +
    (r.screenshot ? `<details><summary class="small muted">What SFA showed</summary><img class="shotimg" src="data:image/jpeg;base64,${r.screenshot}"></details>` : "");
  loadSfa();
};
$("#sfaOpenDl").onclick = () => call("sfa_open_downloads");
$("#sfaOpenMonitor").onclick = () => openMonitor();
$("#sfaExport").onclick = async () => { const r = await call("sfa_export_defaults"); $("#sfaExportRes").textContent = "Saved: " + r.path; loadSfa(); };

// ---- profile editor (Chimera)
function openProfileEditor(p) {
  SF.draft = JSON.parse(JSON.stringify(p)); SF.draft.filters.forEach(f => { f.options = f.options || []; });
  openModal({ title: `Edit report — ${p.name}`, size: "lg", body: `
    <div id="pe-problems"></div>
    <div class="section-title">Page</div>
    <div class="grid-2"><label class="field full">Navigation URL (the report page in SalesPro) <input id="pe-url" class="mono" placeholder="https://lakmee.salespro.lk/it/reports/..."></label></div>
    <div class="section-title">Filters <span><button class="btn tiny" id="pe-detect">✦ Detect filters from page</button> <button class="btn tiny" id="pe-add">+ Add filter</button></span></div>
    <p class="muted small" style="margin-top:-4px">Labels must match the SFA screen exactly. Leave a value empty to keep SFA's “Not Selected”. Dates follow the date you run for.</p>
    <div id="pe-detect-area"></div>
    <table class="filter-table"><thead><tr><th style="width:24%">Filter label</th><th style="width:14%">Type</th><th>Value</th><th style="width:66px">Required</th><th style="width:96px"></th></tr></thead><tbody id="pe-filters"></tbody></table>
    <div class="section-title">After the filters</div>
    <div class="grid-2">
      <label class="field">Search button text <input id="pe-search"></label>
      <label class="field">Export button text <input id="pe-export"><span>Hydra also finds the green Excel icon by itself. If Export opens a menu write <code>Export &gt; Excel</code>; for one exact element <code>css:img[src*="excel"]</code>.</span></label>
      <label class="field">Wait for results (seconds) <input type="number" min="5" max="600" id="pe-wait"></label>
      <label class="field" style="justify-content:flex-end"><span><input type="checkbox" id="pe-empty"> An empty report (no records) is OK</span></label>
    </div>
    <div class="section-title">Checks Hydra makes on the downloaded file</div><ul class="check-list" id="pe-checks"></ul>`,
    footer: `<label class="check left"><input type="checkbox" id="pe-enabled"> Enabled</label><button class="btn ghost" id="pe-cancel">Cancel</button><button class="btn primary" id="pe-save">Save profile</button>` });
  const d = SF.draft;
  $("#pe-url").value = d.url || ""; $("#pe-search").value = d.search_text; $("#pe-export").value = d.export_text;
  $("#pe-wait").value = d.wait_seconds; $("#pe-empty").checked = !!d.allow_empty; $("#pe-enabled").checked = !!d.enabled;
  renderFilterRows();
  $("#pe-url").addEventListener("input", renderChecks);
  $("#pe-add").onclick = () => { d.filters.push({ label: "", type: "dropdown", value: "", date_mode: "", required: false, options: [] }); renderFilterRows(); };
  $("#pe-detect").onclick = runDetect; $("#pe-cancel").onclick = () => closeModal(); $("#pe-save").onclick = saveDraft;
}
function readBasics() {
  const d = SF.draft; d.url = $("#pe-url").value.trim(); d.search_text = $("#pe-search").value; d.export_text = $("#pe-export").value;
  d.wait_seconds = Number($("#pe-wait").value); d.allow_empty = $("#pe-empty").checked; d.enabled = $("#pe-enabled").checked;
}
function valueEditor(f, i) {
  if (f.type === "date") {
    return `<div class="seg">${SF.ov.date_modes.map(k => `<button type="button" data-i="${i}" data-mode="${k}" class="${(f.date_mode || "report_date") === k ? "on" : ""}">${DATE_MODE_LABEL[k]}</button>`).join("")}</div>
      ${f.date_mode === "fixed" ? `<input type="date" data-i="${i}" data-k="value" value="${esc(f.value)}" style="margin-top:6px">` : ""}`;
  }
  if (f.type === "toggle") return `<select data-i="${i}" data-k="value"><option value="on" ${f.value !== "off" ? "selected" : ""}>On</option><option value="off" ${f.value === "off" ? "selected" : ""}>Off</option></select>`;
  if (f.type === "multiselect") {
    const vals = Array.isArray(f.value) ? f.value : (f.value ? [f.value] : []);
    const opts = (f.options || []).filter(o => !vals.includes(o));
    const adder = opts.length ? `<select data-i="${i}" data-k="ms-add"><option value="">+ add a value…</option>${opts.map(o => `<option>${esc(o)}</option>`).join("")}</select>`
      : `<input data-i="${i}" data-k="ms-add-text" placeholder="type a value and press Enter">`;
    return `${adder}<div class="ms-chips">${vals.map((v, j) => `<span class="ms-chip">${esc(v)}<button type="button" data-i="${i}" data-rm="${j}">×</button></span>`).join("") || '<span class="muted small">left empty (Not Selected)</span>'}</div>`;
  }
  if (f.type === "dropdown" && f.options && f.options.length) {
    const opts = f.options.includes(f.value) || !f.value ? f.options : [f.value].concat(f.options);
    return `<select data-i="${i}" data-k="value"><option value="">— leave empty (Not Selected) —</option>${opts.map(o => `<option ${o === f.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  }
  return `<input data-i="${i}" data-k="value" value="${esc(Array.isArray(f.value) ? f.value.join(", ") : f.value)}" placeholder="leave empty = Not Selected">`;
}
function renderFilterRows() {
  const d = SF.draft, tb = $("#pe-filters");
  const types = SF.ov.filter_types.map(t => `<option value="${t}">${{ dropdown: "Dropdown", multiselect: "Multi-select", date: "Date", text: "Text", toggle: "Toggle" }[t]}</option>`).join("");
  tb.innerHTML = d.filters.map((f, i) => `<tr>
      <td><input data-i="${i}" data-k="label" value="${esc(f.label)}" placeholder="e.g. Report Type"></td>
      <td><select data-i="${i}" data-k="type">${types}</select></td>
      <td>${valueEditor(f, i)}</td>
      <td style="text-align:center"><input type="checkbox" data-i="${i}" data-k="required" ${f.required ? "checked" : ""}></td>
      <td><div class="row-actions"><button type="button" class="btn tiny ghost" data-mv="${i}:-1" ${i === 0 ? "disabled" : ""}>▲</button><button type="button" class="btn tiny ghost" data-mv="${i}:1" ${i === d.filters.length - 1 ? "disabled" : ""}>▼</button><button type="button" class="btn tiny ghost" data-del="${i}">✕</button></div></td>
    </tr>`).join("") || `<tr><td colspan="5" class="muted small">No filters — the report is exported with SFA's defaults.</td></tr>`;
  tb.querySelectorAll('select[data-k="type"]').forEach(s => { s.value = d.filters[Number(s.dataset.i)].type; });
  renderChecks();
}
document.addEventListener("input", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters") || el.dataset.k === undefined) return;
  const f = SF.draft.filters[Number(el.dataset.i)];
  if (el.dataset.k === "label") { f.label = el.value; renderChecks(); }
  if (el.dataset.k === "value" && el.tagName === "INPUT" && el.type !== "date") { f.value = el.value; renderChecks(); }
});
document.addEventListener("change", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters")) return;
  const f = SF.draft.filters[Number(el.dataset.i)], k = el.dataset.k;
  if (k === "type") { f.type = el.value; f.value = f.type === "multiselect" ? [] : (f.type === "toggle" ? "on" : ""); f.date_mode = f.type === "date" ? "report_date" : ""; renderFilterRows(); }
  else if (k === "value") { f.value = el.value; renderChecks(); }
  else if (k === "required") f.required = el.checked;
  else if (k === "ms-add" && el.value) { f.value = (Array.isArray(f.value) ? f.value : []).concat([el.value]); renderFilterRows(); }
});
document.addEventListener("keydown", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters") || el.dataset.k !== "ms-add-text" || e.key !== "Enter") return;
  e.preventDefault(); const f = SF.draft.filters[Number(el.dataset.i)], v = el.value.trim();
  if (v) { f.value = (Array.isArray(f.value) ? f.value : []).concat([v]); renderFilterRows(); }
});
document.addEventListener("click", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters")) return;
  const d = SF.draft;
  if (el.dataset.mode) { const f = d.filters[Number(el.dataset.i)]; f.date_mode = el.dataset.mode; if (f.date_mode !== "fixed") f.value = ""; renderFilterRows(); }
  if (el.dataset.rm !== undefined) { d.filters[Number(el.dataset.i)].value.splice(Number(el.dataset.rm), 1); renderFilterRows(); }
  if (el.dataset.del !== undefined) { d.filters.splice(Number(el.dataset.del), 1); renderFilterRows(); }
  if (el.dataset.mv) { const [i, dir] = el.dataset.mv.split(":").map(Number), j = i + dir; [d.filters[i], d.filters[j]] = [d.filters[j], d.filters[i]]; renderFilterRows(); }
});
function renderChecks() {
  const ul = $("#pe-checks"); if (!ul) return;
  const items = [`The file is an <b>${esc(SF.draft.name)}</b> export`];
  SF.draft.filters.forEach(f => {
    if (!f.label) return;
    if (f.type === "date") items.push(`<b>${esc(f.label)}</b> in the file banner = ${f.date_mode === "fixed" ? esc(f.value || "(pick a date)") : DATE_MODE_LABEL[f.date_mode || "report_date"].toLowerCase()} (when the report prints it)`);
    else if ((f.type === "dropdown" || f.type === "text") && f.value) items.push(`<b>${esc(f.label)}</b> in the file banner = “${esc(f.value)}”`);
  });
  items.push("Banner rows and the totals row are skipped; numbers stored as text are converted");
  ul.innerHTML = items.map(t => `<li><span class="tick">✓</span><span>${t}</span></li>`).join("");
  const probs = [], url = $("#pe-url").value.trim();
  if (!url) probs.push("Paste the report's navigation URL."); else if (!/^https?:\/\//.test(url)) probs.push("The URL must start with https://");
  SF.draft.filters.forEach(f => { if (!f.label.trim()) probs.push("A filter row has no label."); });
  $("#pe-problems").innerHTML = probs.length ? `<div class="problem-box">${probs.map(esc).join("<br>")}</div>` : "";
}
async function saveDraft() {
  readBasics();
  const r = await call("sfa_save_profile", SF.draft.key, SF.draft);
  if (!r.ok) { $("#pe-problems").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; $("#modal-body").scrollTop = 0; return; }
  closeModal(true); toast(SF.draft.name + " saved."); loadSfa();
}
async function runDetect() {
  readBasics();
  const area = $("#pe-detect-area");
  if (!/^https?:\/\//.test(SF.draft.url)) { area.innerHTML = `<div class="problem-box">Paste the report's URL first.</div>`; return; }
  area.innerHTML = `<div class="ok-box" style="background:#f6f6f7;color:#444;border-color:#ddd"><span class="spin"></span> Logging in to SFA and reading the filters on that page… about 20–40 seconds. Nothing in SFA is changed.</div>`;
  SF.busyModal = true; $("#pe-detect").disabled = true;
  let r; try { r = await API.sfa_detect(SF.draft.url, false); } catch (err) { r = { ok: false, message: String(err) }; }
  SF.busyModal = false; $("#pe-detect").disabled = false;
  if (!r.ok) { area.innerHTML = `<div class="problem-box">${esc(r.message || r.error)}</div>` + (r.screenshot ? `<img class="shotimg" src="data:image/jpeg;base64,${r.screenshot}">` : ""); return; }
  const have = new Set(SF.draft.filters.map(f => f.label.toLowerCase()));
  area.innerHTML = `<div class="ok-box">${esc(r.message)} Tick the ones this report should set, then “Apply”. Options seen on the page become pick-lists.</div>
    <div class="detect-list">${r.fields.map((f, i) => `<label><input type="checkbox" data-di="${i}" ${have.has(f.label.toLowerCase()) || f.type === "date" ? "checked" : ""}>
      <b>${esc(f.label)}</b> <span class="chipx grey">${esc(f.type)}</span>
      <span class="muted">${f.options.length ? esc(f.options.slice(0, 4).join(", ")) + (f.options.length > 4 ? ` … (${f.options.length})` : "") : (f.type === "toggle" ? (f.toggled ? "currently on" : "currently off") : "")}</span></label>`).join("")}</div>
    <div class="row"><button class="btn primary tiny" id="pe-apply">Apply</button><button class="btn ghost tiny" id="pe-dismiss">Dismiss</button>${r.screenshot ? `<button class="btn ghost tiny" id="pe-shot">View page screenshot</button>` : ""}</div>`;
  $("#pe-dismiss").onclick = () => (area.innerHTML = "");
  if (r.screenshot) $("#pe-shot").onclick = () => area.insertAdjacentHTML("beforeend", `<img class="shotimg" src="data:image/jpeg;base64,${r.screenshot}">`);
  $("#pe-apply").onclick = () => {
    area.querySelectorAll("input[data-di]:checked").forEach(cb => {
      const f = r.fields[Number(cb.dataset.di)];
      const ex = SF.draft.filters.find(x => x.label.toLowerCase() === f.label.toLowerCase());
      if (ex) { ex.type = f.type; ex.options = f.options || []; if (f.type === "date" && !ex.date_mode) ex.date_mode = "report_date"; }
      else SF.draft.filters.push({ label: f.label, type: f.type, value: f.type === "multiselect" ? [] : (f.type === "toggle" ? (f.toggled ? "on" : "off") : ""),
        date_mode: f.type === "date" ? "report_date" : "", required: f.type === "date", options: f.options || [] });
    });
    const order = r.fields.map(f => f.label.toLowerCase());
    SF.draft.filters.sort((a, b) => (order.indexOf(a.label.toLowerCase()) + 1 || 999) - (order.indexOf(b.label.toLowerCase()) + 1 || 999));
    area.innerHTML = `<div class="ok-box">Filters updated — choose the values below, then save.</div>`;
    renderFilterRows();
  };
}

// ---- test one profile (download + check, nothing loaded)
function openTestRun(p) {
  const d = p.key === "rep_perf" ? ($("#mTo").value || $("#dDate").value) : $("#dDate").value;
  openModal({ title: `Test — ${p.name}`, body: `
      <p>Logs in, opens the page, sets the filters, searches and exports — then checks the file. <b>Nothing is loaded</b> into the database. Screenshots of each step are shown.</p>
      <div class="grid-2"><label class="field">Date to test with <input type="date" id="tr-date" value="${d}"></label>
      <label class="field" style="justify-content:flex-end"><span><input type="checkbox" id="tr-show"> Show the browser</span></label></div>`,
    footer: `<button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" id="tr-go">▶ Run test</button>` });
  $("#tr-go").onclick = async () => {
    const r = await call("sfa_start_run", [p.key], $("#tr-date").value, true, $("#tr-show").checked, false, null);
    if (!r.ok) return toast(r.message, true);
    closeModal(true); openMonitor(true);
  };
}

// ---- run from Daily / Month End
async function startSfaRun(keys, date, generate, onDone) {
  const which = [...document.querySelectorAll("#dWhich input:checked")].map(i => i.value);
  const r = await call("sfa_start_run", keys, date, false, null, generate, which);
  if (!r.ok) return toast(r.message, true);
  SF.onDone = onDone; openMonitor();
}
$("#dSfa").onclick = () => startSfaRun(["sales", "callage", "pending"], $("#dDate").value, $("#dSfaGen").checked, (s) => {
  const g = s.result && s.result.generated; if (g) renderResults($("#dResults"), g); loadDaily();
});
$("#mSfa").onclick = () => startSfaRun(["rep_perf"], $("#mTo").value, false, () => loadMonthEnd());

// ---- live monitor (Chimera)
function stepper(l) {
  return `<div class="stepper">${STEP_ORDER.map(s => { const st = l.steps[s]; const ic = st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
    return `<div class="st ${st}"><span class="dot">${ic}</span>${STEP_LABEL[s]}</div>`; }).join("")}</div>`;
}
function laneHtml(l, test) {
  const shots = test && l.shots && l.shots.length ? `<div class="shots" style="grid-column:1/-1">${l.shots.map(x => `<figure><img src="data:image/jpeg;base64,${x.img}"><figcaption>${esc(x.step.replace(/_/g, " "))}</figcaption></figure>`).join("")}</div>` : "";
  const checks = (l.checks || []).length ? `<div style="grid-column:1/-1;font-size:12px">${l.checks.map(c => `${c.ok ? "✓" : "✗"} ${esc(c.what)}: ${esc(c.got)}${c.ok ? "" : ` (expected ${esc(c.expect)})`}`).join(" &nbsp;·&nbsp; ")}</div>` : "";
  return `<div class="lane ${l.status}"><div><div class="lane-name">${esc(l.name)}</div><div class="lane-msg">${esc(l.message || (l.status === "queued" ? "Waiting for a free tab…" : ""))}</div></div>
    ${stepper(l)}<div class="lane-meta">${chipx(l.status)}${l.max_attempts > 1 && l.attempt > 1 ? `<div>try ${l.attempt}/${l.max_attempts}</div>` : ""}<div>${l.rows != null ? l.rows + " rows" : ""} ${l.elapsed ? l.elapsed + "s" : ""}</div>
    ${l.file ? `<div><button class="btn tiny ghost" data-folder="${esc(l.file)}">file</button></div>` : ""}${l.screenshot && l.status === "failed" ? `<div><button class="btn tiny ghost" data-open="${esc(l.screenshot)}">screenshot</button></div>` : ""}</div>${checks}${shots}</div>`;
}
function openMonitor(test = false) {
  SF.monitorOpen = true; SF.testView = test;
  openModal({ title: test ? "Test run" : "SFA download", size: "xl", body: `<div id="mon-body"><span class="spin"></span> Starting…</div>`,
    footer: `<button class="btn primary left" id="mon-stop">■ Stop now</button><button class="btn ghost" id="mon-copy">Copy log</button><button class="btn ghost" id="mon-close">Minimise</button>`,
    onClose: () => { SF.monitorOpen = false; } });
  $("#mon-close").onclick = () => closeModal();
  $("#mon-stop").onclick = async () => { await API.sfa_stop(); toast("Stopping…"); };
  $("#mon-copy").onclick = async () => { const t = await API.sfa_log_text(); try { await navigator.clipboard.writeText(t); toast("Log copied."); } catch (e) { toast("Couldn't copy.", true); } };
  startPolling();
}
function renderMonitor(s) {
  const el = $("#mon-body"); if (!el) return;
  const lanes = s.lanes || [], total = lanes.length * STEP_ORDER.length || 1;
  const done = lanes.reduce((a, l) => a + STEP_ORDER.filter(x => ["done", "skipped"].includes(l.steps[x]) || ["failed", "cancelled", "empty"].includes(l.status)).length, 0);
  const pct = s.active ? Math.min(99, Math.round(100 * done / total)) : 100;
  const phase = { starting: "Starting…", logging_in: "Logging in to SFA…", downloading: "Downloading reports…", generating: "Building the report pack…", done: "Finished" }[s.phase] || s.phase;
  let gen = "";
  const g = s.result && s.result.generated;
  if (g) gen = `<div class="section-title">Reports built</div>` + g.results.map(r => `<div class="result"><div><div class="name">${esc(r.report)}</div><div class="sum">${esc(r.summary || "")}</div></div>${r.path ? `<button class="btn tiny" data-open="${esc(r.path)}">Open</button>` : '<span class="muted small">skipped</span>'}</div>`).join("");
  el.innerHTML = `<div class="row between"><div><b>${esc(phase)}</b> <span class="muted small">· date ${esc(s.report_date || "")}${s.test ? " · test (nothing loaded)" : ""}</span></div><div>${s.result ? chipx(s.result.status) : ""}</div></div>
    <div class="progress"><div style="width:${pct}%"></div></div>
    ${s.result && !s.result.ok && s.result.message ? `<div class="problem-box">${esc(s.result.message)}</div>` : ""}
    <div class="lanes">${lanes.map(l => laneHtml(l, SF.testView || s.test)).join("")}</div>${gen}
    <div class="section-title">Log</div><div class="log-box" id="mon-log">${(s.log || []).slice(-150).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("")}</div>`;
  const lg = $("#mon-log"); lg.scrollTop = lg.scrollHeight;
  el.querySelectorAll("[data-open]").forEach(b => (b.onclick = () => call("open_path", b.dataset.open)));
  el.querySelectorAll("[data-folder]").forEach(b => (b.onclick = () => call("open_folder", b.dataset.folder)));
  if ($("#mon-stop")) $("#mon-stop").style.display = s.active ? "" : "none";
  if ($("#mon-close")) $("#mon-close").textContent = s.active ? "Minimise" : "Close";
}
function startPolling() {
  if (SF.poll) return;
  const tick = async () => {
    let s; try { s = await API.sfa_live(); } catch (e) { return; }
    if (SF.monitorOpen) renderMonitor(s);
    if (!s.active && s.phase === "done") {
      clearInterval(SF.poll); SF.poll = null;
      if (SF.onDone) { const cb = SF.onDone; SF.onDone = null; cb(s); }
      if ($("#view-sfa").classList.contains("active")) loadSfa();
    }
  };
  tick(); SF.poll = setInterval(tick, 1000);
}

/* ------------------------------------------------------------------ start */
window.addEventListener("pywebviewready", async () => {
  API = window.pywebview.api;
  APP = await call("init");
  $("#version").textContent = "v" + APP.version;
  $("#sideFoot").innerHTML = "Interim app until Hydra launches";
  $("#dDate").value = APP.default_date;
  const mo = monthOf(APP.default_date);
  $("#mMonth").value = mo; $("#cMonth").value = mo; $("#tMonth").value = mo;
  $("#dWhich").innerHTML = Object.entries(APP.reports).map(([k, v]) => `<label class="check"><input type="checkbox" value="${k}" checked> ${esc(v)}</label>`).join("");
  loadDaily();
});
