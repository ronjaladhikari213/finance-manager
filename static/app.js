/* Finance Manager frontend. No frameworks, no build step.
   User-supplied text is only ever inserted via textContent (never innerHTML) to prevent XSS. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { currency: "Rs", csrf: null, month: thisMonth(), tab: "home", type: "expense", authMode: "login", cats: null };

/* ---------------------------------------------------------- helpers */
function thisMonth() {
  const d = new Date();
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
}
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function shiftMonth(m, delta) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo - 1 + delta, 1);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
}
function monthName(m) {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}
const fmt = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (n) => `${state.currency} ${fmt(n)}`;
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  } catch {
    throw new Error("Can't reach the server. Check your connection.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON (csv etc.) */ }
  if (res.status === 401 && path !== "/api/login") { showAuth(); throw new Error("Please log in again."); }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

/* ---------------------------------------------------------- auth */
function showAuth() {
  $("app").classList.add("hidden");
  $("auth").classList.remove("hidden");
}
function applyCurrency(cur) {
  if (!cur) return;
  state.currency = cur;
  $("fMin").placeholder = `Min ${cur}`;
  $("fMax").placeholder = `Max ${cur}`;
}
function showApp(username) {
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("whoami").textContent = username;
  switchTab(state.tab);
}
function setAuthMode(mode) {
  state.authMode = mode;
  document.querySelectorAll("#authTabs button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("authBtn").textContent = mode === "login" ? "Log in" : "Create account";
  $("authPass").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("authErr").textContent = "";
}
document.querySelectorAll("#authTabs button").forEach((b) => b.addEventListener("click", () => setAuthMode(b.dataset.mode)));

$("authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("authErr").textContent = "";
  $("authBtn").disabled = true;
  try {
    const r = await api(state.authMode === "login" ? "/api/login" : "/api/register", {
      method: "POST",
      body: { username: $("authUser").value, password: $("authPass").value },
    });
    state.csrf = r.csrf;
    applyCurrency(r.currency);
    $("authPass").value = "";
    showApp(r.username);
  } catch (err) {
    $("authErr").textContent = err.message;
  } finally {
    $("authBtn").disabled = false;
  }
});

$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
  state.csrf = null;
  showAuth();
});

/* ---------------------------------------------------------- tabs / month */
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + tab));
  document.querySelectorAll(".bottom button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("fab").classList.toggle("hidden", tab === "more");
  refresh();
}
document.querySelectorAll(".bottom button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

function changeMonth(delta) {
  state.month = shiftMonth(state.month, delta);
  refresh();
}
$("prevMonth").addEventListener("click", () => changeMonth(-1));
$("nextMonth").addEventListener("click", () => changeMonth(1));

async function refresh() {
  $("monthLabel").textContent = monthName(state.month);
  try {
    if (state.tab === "home") await loadHome();
    else if (state.tab === "tx") await loadTransactions();
    else if (state.tab === "cat") await loadCategories();
  } catch (err) {
    toast(err.message);
  }
}

/* ---------------------------------------------------------- HOME (summary + budget) */
function catBar(name, total, percent, sub) {
  const row = el("div", "cat-row");
  const top = el("div", "top");
  top.append(el("b", null, name), el("span", null, sub || fmt(total)));
  const bar = el("div", "bar");
  const fill = document.createElement("i");
  fill.style.width = Math.min(100, percent) + "%";
  bar.append(fill);
  row.append(top, bar);
  return row;
}

async function loadHome() {
  const s = await api("/api/summary?month=" + state.month);
  $("sBalance").textContent = money(s.remaining);
  $("sIncome").textContent = money(s.income);
  $("sExpense").textContent = money(s.expenses);
  $("sSaving").textContent = s.saving_rate === null ? "Saving rate: add some income to see it" : `Saving rate: ${s.saving_rate}%`;

  const body = $("budgetBody");
  body.replaceChildren();
  if (s.budget > 0) {
    const pct = s.budget_used_percent;
    const wrap = el("div");
    const line = el("div", "budget-line");
    const left = el("div", "budget-left", s.over_budget ? `Over by ${money(-s.budget_remaining)}` : `${money(s.budget_remaining)} left`);
    left.style.color = s.over_budget ? "var(--exp)" : "var(--inc)";
    const spent = el("div", "muted small", `Spent ${money(s.expenses)} of ${money(s.budget)}`);
    line.append(left, spent);
    const bar = el("div", "bar" + (s.over_budget ? " over" : pct >= 80 ? " warn" : ""));
    const fill = document.createElement("i");
    fill.style.width = Math.min(100, pct) + "%";
    bar.append(fill);
    wrap.append(line, bar, el("div", "muted small", `${pct}% of budget used`));
    body.append(wrap);
  } else {
    body.textContent = "No budget set for this month.";
  }

  const cats = $("homeCats");
  cats.replaceChildren();
  if (!s.categories.length) { cats.append(el("span", "muted small", "No expenses yet.")); return; }
  s.categories.slice(0, 4).forEach((c) => cats.append(catBar(c.category, c.total, s.expenses ? (c.total / s.expenses) * 100 : 0, money(c.total))));
}

/* ---------------------------------------------------------- TRANSACTIONS (view / search / delete) */
let searchTimer;
["fText", "fMin", "fMax"].forEach((id) => $(id).addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadTransactions, 250); }));
$("fType").addEventListener("change", loadTransactions);
$("fAllTime").addEventListener("change", loadTransactions);

async function loadTransactions() {
  const p = new URLSearchParams();
  if (!$("fAllTime").checked) p.set("month", state.month);
  if ($("fType").value) p.set("type", $("fType").value);
  if ($("fText").value.trim()) p.set("text", $("fText").value.trim());
  if ($("fMin").value) p.set("min", $("fMin").value);
  if ($("fMax").value) p.set("max", $("fMax").value);

  let rows;
  try { rows = await api("/api/transactions?" + p.toString()); } catch (err) { toast(err.message); return; }
  const list = $("txList");
  list.replaceChildren();
  if (!rows.length) { list.append(el("div", "empty", "No transactions found.")); return; }

  let lastDate = null;
  rows.forEach((t) => {
    if (t.date !== lastDate) {
      lastDate = t.date;
      const d = new Date(t.date + "T00:00:00");
      list.append(el("div", "day-head", d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" })));
    }
    const row = el("div", "tx");
    row.append(el("div", "ico " + t.type, t.type === "income" ? "↓" : "↑"));
    const mid = el("div", "mid");
    mid.append(el("b", null, t.category));
    if (t.note) mid.append(el("span", null, t.note));
    const amt = el("div", "amt " + t.type, (t.type === "income" ? "+" : "−") + money(t.amount));
    const del = el("button", "del", "✕");
    del.setAttribute("aria-label", "Delete transaction");
    del.addEventListener("click", () => deleteTx(t));
    row.append(mid, amt, del);
    list.append(row);
  });
}

async function deleteTx(t) {
  if (!confirm(`Delete this ${t.type}?\n${t.category}: ${money(t.amount)} on ${t.date}`)) return;
  try {
    await api("/api/transactions/" + t.id, { method: "DELETE" });
    toast("Deleted");
    refresh();
  } catch (err) { toast(err.message); }
}

/* ---------------------------------------------------------- CATEGORIES (feature 5) */
async function loadCategories() {
  const r = await api("/api/categories/summary?month=" + state.month);
  $("catTotal").textContent = `Total expenses: ${money(r.total_expenses)}`;
  const list = $("catList");
  list.replaceChildren();
  if (!r.categories.length) { list.append(el("div", "empty", "No expenses this month.")); return; }
  r.categories.forEach((c) => list.append(catBar(c.category, c.total, c.percent, `${money(c.total)} · ${c.percent}%`)));
}

/* ---------------------------------------------------------- ADD TRANSACTION SHEET (features 1 & 2) */
function openSheet(id) { $(id).classList.remove("hidden"); $("sheetBackdrop").classList.remove("hidden"); }
function closeSheets() {
  ["sheet", "budgetSheet"].forEach((s) => $(s).classList.add("hidden"));
  $("sheetBackdrop").classList.add("hidden");
}
$("sheetBackdrop").addEventListener("click", closeSheets);
$("cancelTx").addEventListener("click", closeSheets);
$("cancelBudget").addEventListener("click", closeSheets);

function setType(type) {
  state.type = type;
  document.querySelectorAll("#typeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.type === type));
  renderCatSuggestions();
}
document.querySelectorAll("#typeSeg button").forEach((b) => b.addEventListener("click", () => setType(b.dataset.type)));

function renderCatSuggestions() {
  const chips = $("catChips");
  const dl = $("catOptions");
  chips.replaceChildren();
  dl.replaceChildren();
  const list = (state.cats && state.cats[state.type]) || [];
  list.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c;
    dl.append(opt);
    const chip = el("button", "chip", c);
    chip.type = "button";
    chip.addEventListener("click", () => { $("txCat").value = c; });
    chips.append(chip);
  });
}

$("fab").addEventListener("click", async () => {
  $("txForm").reset();
  $("txDate").value = today();
  $("txErr").textContent = "";
  setType(state.type);
  openSheet("sheet");
  try { state.cats = await api("/api/categories"); renderCatSuggestions(); } catch { /* suggestions are optional */ }
  setTimeout(() => $("txAmount").focus(), 60);
});

$("txForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("txErr").textContent = "";
  try {
    await api("/api/transactions", {
      method: "POST",
      body: { type: state.type, amount: $("txAmount").value, category: $("txCat").value, note: $("txNote").value, date: $("txDate").value },
    });
    closeSheets();
    toast(state.type === "income" ? "Income added" : "Expense added");
    // jump to the month of the new entry so the user sees it
    const m = $("txDate").value.slice(0, 7);
    if (m) state.month = m;
    refresh();
  } catch (err) { $("txErr").textContent = err.message; }
});

/* ---------------------------------------------------------- BUDGET (feature 4) */
$("editBudget").addEventListener("click", async () => {
  $("budgetMonthLabel").textContent = monthName(state.month);
  $("budgetErr").textContent = "";
  try {
    const b = await api("/api/budget?month=" + state.month);
    $("budgetInput").value = b.amount > 0 ? b.amount : "";
  } catch { $("budgetInput").value = ""; }
  openSheet("budgetSheet");
  setTimeout(() => $("budgetInput").focus(), 60);
});
$("budgetForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("budgetErr").textContent = "";
  try {
    await api("/api/budget", { method: "PUT", body: { month: state.month, amount: $("budgetInput").value } });
    closeSheets();
    toast("Budget saved");
    refresh();
  } catch (err) { $("budgetErr").textContent = err.message; }
});

/* ---------------------------------------------------------- PWA install + boot */
let deferredInstall;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstall = e;
  $("installBtn").style.display = "block";
});
$("installBtn").addEventListener("click", async () => {
  if (!deferredInstall) return;
  deferredInstall.prompt();
  await deferredInstall.userChoice;
  deferredInstall = null;
  $("installBtn").style.display = "none";
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

(async function boot() {
  try {
    const s = await api("/api/session");
    applyCurrency(s.currency);
    if (s.authenticated) { state.csrf = s.csrf; showApp(s.username); }
    else showAuth();
  } catch {
    showAuth();
  }
})();
