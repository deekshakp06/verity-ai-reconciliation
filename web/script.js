const PAGE_SIZE = 100;
const state = {
  payments: null, settlements: null, bank: null,
  dataset: "hard", lastResult: null,
  filter: "all", search: "", lateOnly: false,
  excLimit: PAGE_SIZE, matchLimit: PAGE_SIZE,
  datasetKey: null, reviews: {}, statusFilter: "all", openId: null, sort: "gap",
};
const REVIEW_STATUS = { open: ["Open", "warn"], escalated: ["Escalated", "bad"], resolved: ["Resolved", "ok"] };
const reviewOf = (e) => state.reviews[e.payment_id] || { status: "open", note: "", reviewer: "" };
const openCount = () => state.lastResult ? state.lastResult.exceptions.filter(e => reviewOf(e).status === "open").length : 0;

// ---------------------------------------------------------------- helpers
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v))) ? null : Number(v);
const inr = (v) => num(v) === null ? "—" : "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const trunc = (t, n) => { t = String(t || ""); return t.length > n ? t.slice(0, n).replace(/\s+\S*$/, "") + "…" : t; };
const round1 = (n) => Math.round(n * 10) / 10;

const PAGE_META = {
  overview: ["Overview", "How your payments line up with settlements"],
  exceptions: ["Exceptions", "Flagged for review, each with a reason"],
  matched: ["Matched", "Auto-closed with high confidence"],
  accuracy: ["Accuracy", "Measured against known-correct labels"],
  data: ["Data", "Upload your own files or preview the demo data"],
  ask: ["Ask Verity", "Questions answered from the audit log"],
};
function switchPage(name) {
  const m = PAGE_META[name];
  if (m) { $("page-title").textContent = m[0]; $("page-sub").textContent = m[1]; }
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.dataset.page === name));
  document.querySelectorAll(".page").forEach(p => p.hidden = p.dataset.page !== name);
  const ban = $("status-banner");
  ban.hidden = name !== "overview" && !ban.querySelector(".error");
  window.scrollTo({ top: 0 });
}
$("navtabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (t) switchPage(t.dataset.page);
});
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-goto]");
  if (g) switchPage(g.dataset.goto);
});

function showMethod(label, reason) {
  $("status-banner").innerHTML = `<div class="method"><span class="dot on"></span>${esc(label)}${reason ? tip(reason) : ""}</div>`;
}
function showBanner(msg, type) { $("status-banner").innerHTML = `<div class="banner ${type}">${esc(msg)}</div>`; }
function clearBanner() { $("status-banner").innerHTML = ""; }

async function checkStatus() {
  try {
    const d = await (await fetch("/api/status")).json();
    setAiStatus(d.ai_connected, d.model);
    const pg = d.db === "postgresql";
    $("db-dot").className = "dot " + (d.db_error ? "off" : "on");
    $("db-status-text").textContent = d.db_error ? "Database: SQLite (fallback)" : `Database: ${pg ? "PostgreSQL" : "SQLite"}`;
    $("db-status-text").title = d.db_error || "";
  } catch { setAiStatus(false, null); }
}
function setAiStatus(on, model) {
  $("ai-dot").className = "dot " + (on ? "on" : "off");
  $("ai-status-text").textContent = on ? `AI explanations on (${model})` : "Template explanations (no API key)";
}

// ---------------------------------------------------------------- classification
const KIND = {
  amount:    { label: "Amount mismatch",    cls: "bad",  tip: "The settlement exists but the amount differs by more than a normal fee." },
  reference: { label: "Reference mismatch", cls: "bad",  tip: "The reference code does not match exactly; Verity found a close candidate but is not sure." },
  duplicate: { label: "Duplicate",          cls: "bad",  tip: "The same settlement appears more than once." },
  fee:       { label: "Fee deviation",      cls: "bad",  tip: "The fee taken is different from the usual fee rate in this file." },
  refund:    { label: "Refund / chargeback", cls: "warn", tip: "A refund or chargeback-style entry. Needs manual handling." },
  delayed:   { label: "Delayed",            cls: "warn", tip: "A likely match, but it settled too long after the payment to auto-close." },
  unmatched: { label: "Unmatched",          cls: "bad",  tip: "No plausible settlement was found for this payment." },
  prior:     { label: "Prior-period",       cls: "warn", tip: "Looks like an adjustment from an earlier period." },
  pending:   { label: "Pending",            cls: "warn", tip: "Not settled yet." },
  other:     { label: "Other",              cls: "warn", tip: "Flagged for a reason that does not fit another type. See the explanation." },
};

function kindOf(e) {
  if (e.exact) {
    return { math_error: "amount", unmatched_to_reference: "unmatched",
             prior_period_deduction: "prior", pending_settlement: "pending" }[e.status] || "other";
  }
  const r = (e.reason || "").toLowerCase();
  if ((e.flags || []).includes("fee_deviation")) return "fee";
  if ((e.flags || []).includes("refund")) return "refund";
  if (r.includes("no plausible") || r.includes("no matching")) return "unmatched";
  if (r.includes("duplicate")) return "duplicate";
  if (r.includes("reference code") || r.includes("closest settlement")) return "reference";
  if (r.includes("days after") || r.includes("late")) return "delayed";
  if (r.includes("mismatch") || r.includes("amount")) return "amount";
  return "other";
}
function gapOf(e) {
  if (e.gap !== undefined && e.gap !== null) return Math.abs(Number(e.gap));
  const a = num(e.amount_a), b = num(e.amount_b);
  return a !== null && b !== null ? Math.abs(a - b) : null;
}
const TIPS = {
  match: "Share of payments Verity matched to a settlement on its own, with high confidence.",
  exceptions: "Payments Verity would not guess on. Each has a reason and needs a person to decide.",
  atRisk: "Total value of the payments that are in the exceptions list.",
  runtime: "How long the matching itself took on this file.",
  gap: "Difference between the payment amount and the closest settlement amount.",
  amountA: "Amount on your payment record.",
  amountB: "Amount on the settlement (or bank) side.",
  late: "Matched, but the money arrived later than usual. Worth a quick look.",
};
const tip = (t) => `<span class="tip" tabindex="0" data-tip="${esc(t)}">i</span>`;
const chip = (text, cls) => `<span class="chip ${cls}">${esc(text)}</span>`;

// ---------------------------------------------------------------- overview
function kpi(label, value, foot, footCls = "", tipText = "") {
  return `<div class="kpi"><div class="k-label">${esc(label)}${tipText ? tip(tipText) : ""}</div><div class="k-value">${value}</div><div class="k-foot ${footCls}">${foot}</div></div>`;
}

function renderOverview(r) {
  const total = r.matched.length + r.exceptions.length;
  const late = r.matched.filter(m => (m.flags || []).includes("late_settlement")).length;
  const atRisk = r.exceptions.reduce((s, e) => s + (num(e.amount_a) || 0), 0);


  const ICON = {
    rate: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    exc: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/>',
    money: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5c0-1 1-1.7 2.5-1.7s2.5.7 2.5 1.7-1 1.5-2.5 1.8-2.5.7-2.5 1.8 1 1.7 2.5 1.7 2.5-.7 2.5-1.7"/>',
    fee: '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
    ok: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    time: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  };
  const stat = (label, value, delta, cls = "", t = "", icon = "rate", tone = "blue") =>
    `<div class="stat"><span class="s-ico ${tone}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[icon]}</svg></span>` +
    `<div class="s-label">${esc(label)}${t ? tip(t) : ""}</div><div class="s-value">${value}</div><div class="s-delta ${cls}">${delta}</div></div>`;
  const nOpen = openCount();
  const feesM = Math.max(0, r.matched.reduce((x, m) => x + ((num(m.amount_a) || 0) - (num(m.amount_b) || 0)), 0));
  $("kpis").innerHTML =
    stat("Match rate", `${r.match_rate}%`, `${r.matched.length} of ${total} matched`, "ok", TIPS.match, "rate", "blue") +
    stat("Exceptions", r.exceptions.length, `${nOpen} still open`, nOpen ? "bad" : "ok", TIPS.exceptions, "exc", "red") +
    stat("In exceptions", inr(atRisk), "value awaiting review", "warn", TIPS.atRisk, "money", "amber") +
    stat("Fees on matched", inr(feesM), "gateway fee and tax", "", "", "fee", "violet") +
    (r.gt_metrics ? stat("Accuracy", `${r.gt_metrics.accuracy}%`, "vs the answer key", "ok", "", "ok", "green")
                  : stat("Run time", `${r.elapsed_seconds}s`, `${Number(r.throughput_per_sec).toLocaleString("en-IN")} records/sec`, "", TIPS.runtime, "time", "green"));

  renderSummary(r); renderTotals(r); renderCloseCheck(r); renderSpeed(r); renderConfidence(r); renderRecent(r);

  renderInsights();
  const gt = r.gt_metrics;
  $("gt-line").textContent = gt
    ? `This dataset has ${gt.true_negative + gt.false_positive} deliberately planted problem cases. Verity flagged ${gt.true_negative} of them and auto-matched ${gt.true_positive} clean payments, with ${gt.false_negative} clean payments sent to review.`
    : "";

  // breakdown
  const counts = {};
  r.exceptions.forEach(e => { const k = kindOf(e); counts[k] = (counts[k] || 0) + 1; });
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = rows.length ? rows[0][1] : 1;
  $("exc-breakdown").innerHTML = rows.length
    ? rows.map(([k, n]) => `<div class="bar-row"><span>${KIND[k].label}</span><div class="bar"><i style="width:${(100 * n / max).toFixed(0)}%"></i></div><span class="n">${n}</span></div>`).join("")
    : `<p class="empty">No exceptions.</p>`;

  // top exceptions
  const top = [...r.exceptions].sort((a, b) => (gapOf(b) ?? num(b.amount_a) ?? 0) - (gapOf(a) ?? num(a.amount_a) ?? 0)).slice(0, 5);
  $("top-exceptions").innerHTML = top.length
    ? `<table class="tbl"><thead><tr><th>ID</th><th>Type</th><th class="num">Gap</th></tr></thead><tbody>` +
      top.map(e => `<tr><td class="id">${esc(e.payment_id)}</td><td>${chip(KIND[kindOf(e)].label, KIND[kindOf(e)].cls)}</td><td class="num">${inr(gapOf(e))}</td></tr>`).join("") +
      `</tbody></table>`
    : `<p class="empty">No exceptions.</p>`;

  $("overview-empty").hidden = true;
  $("overview-results").hidden = false;
}

// ---------------------------------------------------------------- plain-language summary, totals, close check
function renderSummary(r) {
  const total = r.matched.length + r.exceptions.length;
  const late = r.matched.filter(m => (m.flags || []).includes("late_settlement")).length;
  const counts = {};
  r.exceptions.forEach(e => { const k = kindOf(e); counts[k] = (counts[k] || 0) + 1; });
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k, n]) => `${n} ${KIND[k].label.toLowerCase()}`);
  const nOpen = openCount(), n = r.exceptions.length, done = n - nOpen;
  $("summary-sentence").innerHTML = `<h3>Needs your review</h3><div style="margin-top:6px">${r.matched.length} of ${total} payments matched on their own.` +
    (top.length ? ` Mostly ${esc(top.join(" and "))}.` : "") + (late ? ` ${late} matched payments settled late.` : "") + `</div>`;
  $("open-big").innerHTML = `${nOpen}<small>open exceptions</small>`;
  $("open-prog").style.width = (n ? Math.round(100 * done / n) : 100) + "%";
  $("open-foot").textContent = `${done} of ${n} reviewed`;
  $("start-review-btn").hidden = !n;
}

function dayDiff(a, b) {
  const x = Date.parse(a), y = Date.parse(b);
  return isNaN(x) || isNaN(y) ? null : Math.round((y - x) / 86400000);
}
function miniBars(rows, colorFn) {
  const max = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([k, n, c]) => `<div class="mb"><span class="mb-k">${esc(k)}</span><div class="bar"><i style="width:${(100 * n / max).toFixed(0)}%;background:${c || "var(--brand-2)"}"></i></div><span class="n">${n}</span></div>`).join("");
}
function renderSpeed(r) {
  const days = r.matched.map(m => dayDiff(m.date_a, m.date_b)).filter(d => d !== null && d >= 0);
  const late = r.matched.filter(m => (m.flags || []).includes("late_settlement")).length;
  if (!days.length) { $("speed-card").innerHTML = `<p class="muted">No dates to measure.</p>`; return; }
  const avg = days.reduce((a, b) => a + b, 0) / days.length;
  const bucket = (lo, hi) => days.filter(d => d >= lo && d <= hi).length;
  $("speed-card").innerHTML = `<div class="big-num">${round1(avg)} <small>days on average</small></div>
    <div class="muted" style="margin-bottom:10px">from payment to settlement, matched items only</div>` +
    miniBars([["0 to 1 days", bucket(0, 1)], ["2 to 3 days", bucket(2, 3)], ["4 to 7 days", bucket(4, 7)], ["8+ days", bucket(8, 9999), "#f59e0b"]]) +
    `<div class="muted" style="margin-top:8px">${late} flagged as late settlements</div>`;
}
function renderConfidence(r) {
  if (r.strategy === "exact") { $("conf-card").innerHTML = `<div class="big-num">Exact</div><p class="muted">Every match is arithmetic (gross minus fee minus tax equals net), so there is no confidence score.</p>`; return; }
  const c = r.matched.map(m => Number(m.confidence)).filter(x => !isNaN(x));
  if (!c.length) { $("conf-card").innerHTML = `<p class="muted">Nothing was matched.</p>`; return; }
  const avg = c.reduce((a, b) => a + b, 0) / c.length;
  const b = (lo, hi) => c.filter(x => x >= lo && x < hi).length;
  $("conf-card").innerHTML = `<div class="big-num">${round1(avg)}% <small>average confidence</small></div>
    <div class="muted" style="margin-bottom:10px">auto-matches only, higher is more certain</div>` +
    miniBars([["95% and above", b(95, 101)], ["90 to 95%", b(90, 95)], ["85 to 90%", b(85, 90)], ["Below 85%", b(0, 85), "#f59e0b"]]);
}
function renderRecent(r) {
  const ids = new Set(r.exceptions.map(e => e.payment_id));
  const items = Object.entries(state.reviews).filter(([id, v]) => ids.has(id) && v.updated_at && v.status !== "open")
    .sort((a, b) => String(b[1].updated_at).localeCompare(String(a[1].updated_at))).slice(0, 5);
  $("recent-card").innerHTML = items.length
    ? `<ul class="recent">` + items.map(([id, v]) => { const rs = REVIEW_STATUS[v.status];
        return `<li><div><b>${esc(id)}</b> ${chip(rs[0], rs[1])}</div><div class="muted">${v.reviewer ? "by " + esc(v.reviewer) + " · " : ""}${esc(String(v.updated_at).replace("T", " ").slice(0, 16))}${v.note ? `<br>${esc(trunc(v.note, 70))}` : ""}</div></li>`; }).join("") + `</ul>`
    : `<p class="muted">No reviews yet. Exceptions you resolve or escalate will show up here.</p>`;
}

function sumA(list) { return list.reduce((s, x) => s + (num(x.amount_a) || 0), 0); }
function renderTotals(r) {
  const all = sumA(r.matched) + sumA(r.exceptions);
  const matchedGross = sumA(r.matched), exc = sumA(r.exceptions);
  const fees = Math.max(0, r.matched.reduce((x, m) => x + ((num(m.amount_a) || 0) - (num(m.amount_b) || 0)), 0));
  const settled = Math.max(0, matchedGross - fees);
  const pct = (v) => all ? (100 * v / all) : 0;
  const parts = [["#2f6bff", "Settled and reconciled", settled, ""], ["#a9b7e8", "Fees and deductions", fees, "Payment amount minus settled amount on matched items. Normally the gateway fee and tax."], ["#f59e0b", "Awaiting review", exc, ""]];
  const R = 15.9155; let off = 0;
  const arcs = parts.map(([c, , v]) => { const l = pct(v); const a = `<circle r="${R}" cx="21" cy="21" fill="none" stroke="${c}" stroke-width="5" stroke-dasharray="${l.toFixed(2)} ${(100 - l).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}"/>`; off += l; return a; }).join("");
  $("value-sub").textContent = `${inr(all)} across ${r.matched.length + r.exceptions.length} payments`;
  $("totals").innerHTML = `<div class="donut-wrap">
    <div class="donut"><svg viewBox="0 0 42 42" width="170" height="170" role="img" aria-label="Payment value split into settled, fees and awaiting review"><circle r="${R}" cx="21" cy="21" fill="none" stroke="#eef0f5" stroke-width="5"/>${arcs}</svg>
      <div class="d-center"><b>${round1(pct(settled + fees))}%</b><span>reconciled</span></div></div>
    <div class="legend">` + parts.map(([c, k, v, t]) =>
      `<div class="lg" style="--c:${c}"><span class="lg-k">${k}${t ? tip(t) : ""}</span><span class="lg-v">${inr(v)}</span><span class="lg-f">${round1(pct(v))}% of total value</span></div>`).join("") + `</div></div>`;
}

function renderCloseCheck(r) {
  const total = r.exceptions.length;
  const c = { open: 0, escalated: 0, resolved: 0 };
  r.exceptions.forEach(e => c[reviewOf(e).status]++);
  const reviewed = total - c.open;
  const pct = total ? Math.round(100 * reviewed / total) : 100;
  const ready = c.open === 0 && c.escalated === 0;
  const items = [
    [c.open === 0, c.open === 0 ? "Every exception has been reviewed" : `${c.open} exception${c.open === 1 ? "" : "s"} still open`],
    [c.escalated === 0, c.escalated === 0 ? "Nothing waiting on escalation" : `${c.escalated} escalated, waiting for sign-off`],
    [true, `${c.resolved} resolved with a note`],
  ];
  $("close-card").innerHTML = `<div class="close-head"><div><h3>Ready to close this period? ${ready ? chip("Ready", "ok") : chip("Not yet", "warn")}</h3>
    <div class="muted">Close once every exception is resolved or signed off.</div></div>
    ${c.open ? `<button class="btn" id="close-next">Review next open exception →</button>` : ""}</div>
    <ul class="close-list">${items.map(([d, t]) => `<li class="${d ? "done" : ""}">${esc(t)}</li>`).join("")}</ul>
    <div class="progress"><i style="width:${pct}%"></i></div><div class="muted" style="margin-top:4px">${pct}% of exceptions reviewed</div>`;
}

// ---------------------------------------------------------------- review insights
function renderInsights() {
  const el = $("insights");
  if (!state.lastResult) { el.innerHTML = ""; return; }
  const by = {};
  state.lastResult.exceptions.forEach(e => {
    const rv = reviewOf(e);
    if (rv.status === "open") return;
    const k = kindOf(e);
    (by[k] = by[k] || []).push({ resolved: rv.status === "resolved", gap: gapOf(e) });
  });
  const lines = Object.entries(by).filter(([, v]) => v.length >= 5).map(([k, v]) => {
    const res = v.filter(x => x.resolved), share = Math.round(100 * res.length / v.length);
    const maxGap = Math.max(0, ...res.map(x => x.gap || 0));
    return `<li><b>${KIND[k].label}:</b> you resolved ${res.length} of ${v.length} reviewed (${share}%)` +
      (share >= 80 && maxGap ? `, the largest resolved gap was ${inr(maxGap)}. If these are always fine, consider widening the tolerance for this type.` : ".") + `</li>`;
  });
  el.innerHTML = lines.length ? `<div class="card"><h3 style="margin-bottom:6px">What your reviews suggest</h3><ul style="padding-left:18px;font-size:13px">${lines.join("")}</ul>
    <div class="muted" style="margin-top:6px">Based on at least 5 reviewed exceptions of a type. Suggestions only; nothing changes automatically.</div></div>` : "";
}

// ---------------------------------------------------------------- exceptions
function filteredExceptions() {
  if (!state.lastResult) return [];
  const q = state.search.toLowerCase();
  return state.lastResult.exceptions.filter(e =>
    (state.filter === "all" || kindOf(e) === state.filter) &&
    (state.statusFilter === "all" || reviewOf(e).status === state.statusFilter) &&
    (!q || String(e.payment_id).toLowerCase().includes(q) || String(e.reason || "").toLowerCase().includes(q)));
}

function renderFilterChips() {
  const all = state.lastResult ? state.lastResult.exceptions : [];
  const counts = {};
  all.forEach(e => { const k = kindOf(e); counts[k] = (counts[k] || 0) + 1; });
  const items = [["all", `All ${all.length}`], ...Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(k => [k, `${KIND[k].label} ${counts[k]}`])];
  $("filter-tabs").innerHTML = items.map(([k, t]) => `<button data-filter="${k}" class="${state.filter === k ? "on" : ""}">${esc(t)}</button>`).join("");
}

function renderStatusChips() {
  const all = state.lastResult ? state.lastResult.exceptions : [];
  const c = { open: 0, escalated: 0, resolved: 0 };
  all.forEach(e => c[reviewOf(e).status]++);
  const items = [["all", `Any status`], ["open", `Open ${c.open}`], ["escalated", `Escalated ${c.escalated}`], ["resolved", `Resolved ${c.resolved}`]];
  $("status-select").innerHTML = items.map(([k, t]) => `<option value="${k}" ${state.statusFilter === k ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("nav-exc-count").textContent = all.length ? `(${c.open})` : "";
}

function renderExceptions() {
  const el = $("exceptions-list");
  if (!state.lastResult) { el.innerHTML = `<div class="empty-state"><b>No exceptions yet</b><p>Run a reconciliation from Overview. Payments Verity can't confidently match will be listed here with a reason.</p><button class="btn primary" data-goto="overview">Go to Overview</button></div>`; return; }
  renderFilterChips(); renderStatusChips(); renderSortChips();
  const list = sortedExceptions(filteredExceptions());
  if (!list.length) { el.innerHTML = `<p class="empty">Nothing matches this filter.</p>`; return; }
  const shown = list.slice(0, state.excLimit);
  el.innerHTML = `<table class="tbl"><thead><tr>
      <th>ID</th><th>Type</th><th>Status</th><th class="num">Amount A${tip(TIPS.amountA)}</th><th class="num">Amount B${tip(TIPS.amountB)}</th><th class="num">Gap${tip(TIPS.gap)}</th><th>What happened</th></tr></thead><tbody>` +
    shown.map((e, i) => {
      const k = KIND[kindOf(e)];
      const rv = reviewOf(e), rs = REVIEW_STATUS[rv.status];
      const isOpen = state.openId === e.payment_id;
      return `<tr class="row" data-i="${i}" data-id="${esc(e.payment_id)}">
        <td class="id">${esc(e.payment_id)}</td>
        <td><span title="${esc(k.tip)}">${chip(k.label, k.cls)}</span></td><td>${chip(rs[0], rs[1])}</td>
        <td class="num">${inr(e.amount_a)}</td><td class="num">${inr(e.amount_b)}</td><td class="num">${inr(gapOf(e))}</td>
        <td class="sub">${esc(trunc(e.reason, 110))}</td></tr>
        <tr class="detail" ${isOpen ? "" : "hidden"}><td colspan="7"><div class="detail">
          <p><span class="lbl">Why it was flagged</span>${esc(e.reason)}</p>
          <p><span class="lbl">Explanation</span>${esc(e.explanation && e.explanation !== e.reason ? e.explanation : "—")}</p>
          <div class="detail-grid"><div><span class="lbl">Closest settlement</span>${esc(e.settlement_ref || "—")}</div>
          <div><span class="lbl">Dates</span>${esc(e.date_a ?? "—")} → ${esc(e.date_b ?? "—")}</div></div>
          <div class="review" data-id="${esc(e.payment_id)}">
            <span class="lbl">Review</span>
            <div class="review-row">
              <select class="rv-status">${Object.entries(REVIEW_STATUS).map(([v, [t]]) => `<option value="${v}" ${v === rv.status ? "selected" : ""}>${t}</option>`).join("")}</select>
              <input class="rv-reviewer" placeholder="Your name" value="${esc(rv.reviewer || savedReviewer())}">
              <input class="rv-note" placeholder="Reason (required to resolve or escalate)" value="${esc(rv.note)}">
              <button class="btn primary rv-save">Save</button>
            </div>
            <div class="rv-error" hidden></div>
            <div class="review-actions"><button class="btn rv-next" type="button">Save and go to next open →</button></div>
            <div class="rv-history" data-id="${esc(e.payment_id)}"></div>
            ${rv.updated_at ? `<div class="muted" style="margin-top:6px">Last updated ${esc(rv.updated_at.replace("T", " ").replace("+00:00", " UTC"))}${rv.reviewer ? " by " + esc(rv.reviewer) : ""}</div>` : ""}
          </div>
        </div></td></tr>`;
    }).join("") + `</tbody></table>` +
    (list.length > shown.length ? `<div class="more"><button class="btn" id="exc-more">Show more (${list.length - shown.length} left)</button></div>` : "");
  if (state.openId) loadHistory(state.openId);
}

function sortedExceptions(list) {
  const l = [...list];
  if (state.sort === "gap") l.sort((a, b) => (gapOf(b) ?? num(b.amount_a) ?? 0) - (gapOf(a) ?? num(a.amount_a) ?? 0));
  else if (state.sort === "oldest") l.sort((a, b) => String(a.date_a || "9999").localeCompare(String(b.date_a || "9999")));
  else if (state.sort === "newest") l.sort((a, b) => String(b.date_a || "").localeCompare(String(a.date_a || "")));
  return l;
}
function renderSortChips() { $("sort-select").value = state.sort; }
$("sort-select").addEventListener("change", (e) => { state.sort = e.target.value; state.excLimit = PAGE_SIZE; renderExceptions(); });

async function loadHistory(pid) {
  const box = document.querySelector(`.rv-history[data-id="${CSS.escape(pid)}"]`);
  if (!box || !state.datasetKey) return;
  try {
    const rows = await (await fetch(`/api/review/${encodeURIComponent(state.datasetKey)}/${encodeURIComponent(pid)}/history`)).json();
    if (!Array.isArray(rows) || !rows.length) { box.innerHTML = `<div class="muted" style="margin-top:8px">No review history yet.</div>`; return; }
    box.innerHTML = `<span class="lbl" style="margin-top:10px">History</span><ul class="timeline">` + rows.map(h =>
      `<li><b>${esc(h.from_status || "open")} → ${esc(h.to_status)}</b> ${h.reviewer ? "by " + esc(h.reviewer) : ""} · ${esc(String(h.at || "").replace("T", " ").replace("+00:00", " UTC").slice(0, 19))}${h.note ? `<br>${esc(h.note)}` : ""}</li>`).join("") + `</ul>`;
  } catch { /* history is optional */ }
}

function openNextException(fromId) {
  if (!state.lastResult) return;
  const list = sortedExceptions(filteredExceptions());
  const open = list.filter(e => reviewOf(e).status === "open" && e.payment_id !== fromId);
  const idx = fromId ? list.findIndex(e => e.payment_id === fromId) : -1;
  const next = open.find(e => list.indexOf(e) > idx) || open[0];
  if (!next) { showBanner("No open exceptions in this view.", "info"); return; }
  const pos = list.indexOf(next);
  if (pos >= state.excLimit) state.excLimit = pos + 1;
  state.openId = next.payment_id;
  switchPage("exceptions");
  renderExceptions();
  const row = document.querySelector(`tr.row[data-id="${CSS.escape(next.payment_id)}"]`);
  if (row) { row.classList.add("flash"); row.scrollIntoView({ block: "center", behavior: "smooth" }); }
}
$("next-open-btn").addEventListener("click", () => openNextException(null));
document.addEventListener("click", (e) => { if (e.target.id === "close-next") { state.statusFilter = "all"; state.filter = "all"; openNextException(null); } });

$("exceptions-list").addEventListener("click", (e) => {
  if (e.target.id === "exc-more") { state.excLimit += PAGE_SIZE; renderExceptions(); return; }
  const save = e.target.closest(".rv-save");
  if (save) { saveReview(save.closest(".review")); return; }
  const nxt = e.target.closest(".rv-next");
  if (nxt) { const box = nxt.closest(".review"); saveReview(box, true); return; }
  if (e.target.closest(".review")) return;
  const row = e.target.closest("tr.row");
  if (row) {
    const d = row.nextElementSibling; d.hidden = !d.hidden;
    state.openId = d.hidden ? null : row.querySelector(".id").textContent;
    if (state.openId) loadHistory(state.openId);
  }
});

const savedReviewer = () => { try { return localStorage.getItem("verity_reviewer") || ""; } catch { return ""; } };

async function saveReview(box, goNext = false) {
  const btn = box.querySelector(".rv-save");
  const err = box.querySelector(".rv-error");
  const st = box.querySelector(".rv-status").value, nt = box.querySelector(".rv-note").value.trim();
  if ((st === "resolved" || st === "escalated") && nt.length < 5) {
    err.textContent = `Add a short reason (at least 5 characters) before marking this ${st}.`; err.hidden = false;
    box.querySelector(".rv-note").focus(); return;
  }
  err.hidden = true;
  const body = {
    dataset_key: state.datasetKey, payment_id: box.dataset.id,
    status: box.querySelector(".rv-status").value,
    reviewer: box.querySelector(".rv-reviewer").value,
    note: box.querySelector(".rv-note").value,
  };
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const res = await fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not save");
    state.reviews[body.payment_id] = data;
    try { localStorage.setItem("verity_reviewer", body.reviewer); } catch { /* optional */ }
    state.openId = body.payment_id;
    renderExceptions();
    if (state.lastResult) renderOverview(state.lastResult);
    if (goNext) openNextException(body.payment_id);
  } catch (err) {
    showBanner(err.message, "error");
    btn.disabled = false; btn.textContent = "Save";
  }
}

$("status-select").addEventListener("change", (e) => { state.statusFilter = e.target.value; state.excLimit = PAGE_SIZE; renderExceptions(); });
$("filter-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-filter]");
  if (!b) return;
  state.filter = b.dataset.filter; state.excLimit = PAGE_SIZE; renderExceptions();
});
$("exc-search").addEventListener("input", (e) => { state.search = e.target.value; state.excLimit = PAGE_SIZE; renderExceptions(); });

// ---------------------------------------------------------------- matched
function renderMatched() {
  const el = $("matched-list");
  if (!state.lastResult) { el.innerHTML = `<p class="empty">Run a reconciliation from Overview to see matches here.</p>`; return; }
  let list = state.lastResult.matched;
  if (state.lateOnly) list = list.filter(m => (m.flags || []).includes("late_settlement"));
  if (!list.length) { el.innerHTML = `<p class="empty">${state.lateOnly ? "No late settlements in this run." : "Nothing was auto-matched."}</p>`; return; }
  const shown = list.slice(0, state.matchLimit);
  el.innerHTML = `<table class="tbl"><thead><tr>
      <th>Payment</th><th>Settlement</th><th class="num">Amount A</th><th class="num">Amount B</th><th>Dates</th><th>Result</th></tr></thead><tbody>` +
    shown.map(d => {
      const late = (d.flags || []).includes("late_settlement");
      const result = d.exact ? chip("Exact", "ok") : chip(`${Math.round(d.confidence)}%`, "ok");
      return `<tr><td class="id">${esc(d.payment_id)}</td><td>${esc(d.settlement_ref)}</td>
        <td class="num">${inr(d.amount_a)}</td><td class="num">${inr(d.amount_b)}</td>
        <td class="sub">${esc(d.date_a ?? "—")} → ${esc(d.date_b ?? "—")}</td>
        <td>${result} ${late ? `<span title="${esc(TIPS.late)}">${chip("Late settlement", "warn")}</span>` : ""}</td></tr>`;
    }).join("") + `</tbody></table>` +
    (list.length > shown.length ? `<div class="more"><button class="btn" id="match-more">Show more (${list.length - shown.length} left)</button></div>` : "");
}
$("matched-list").addEventListener("click", (e) => { if (e.target.id === "match-more") { state.matchLimit += PAGE_SIZE; renderMatched(); } });
$("late-only").addEventListener("change", (e) => { state.lateOnly = e.target.checked; state.matchLimit = PAGE_SIZE; renderMatched(); });

// ---------------------------------------------------------------- accuracy
function barRows(entries) {
  return `<table class="tbl"><thead><tr><th>Scenario</th><th>Correct</th><th style="width:40%"></th><th class="num">%</th></tr></thead><tbody>` +
    entries.map(([name, v]) => `<tr><td>${esc(name.replace(/_/g, " "))}</td><td>${v.correct}/${v.total}</td>
      <td><div class="bar"><i style="width:${v.pct}%;${v.pct < 100 ? "background:#d97706" : ""}"></i></div></td><td class="num">${v.pct}%</td></tr>`).join("") +
    `</tbody></table>`;
}

function renderAccuracy(r) {
  const el = $("accuracy-content");
  if (r.strategy === "exact") {
    const gt = r.gt_metrics_exact;
    el.innerHTML = `<div class="card"><h3>Exact verification</h3>
      <p class="muted" style="margin:4px 0 12px">Every call is arithmetic (Gross − Fee − Tax = Net), not a probability.</p>` +
      (gt ? `<div class="kpis" style="grid-template-columns:repeat(2,1fr)">${kpi("Accuracy vs planted issues", gt.accuracy + "%", "")}${kpi("Correct", `${gt.correct} / ${gt.total_labeled}`, "")}</div>
        ${barRows(Object.entries(gt.per_status))}
        <div class="note">These labels come from the demo generator, which planted three issues. For a harder test with decoys, collisions and late settlements, run the hard test set from Overview.</div>`
        : `<p class="empty">No labels exist for this data, so no measured accuracy is available.</p>`) + `</div>`;
    return;
  }
  const gt = r.gt_metrics;
  if (!gt) {
    el.innerHTML = `<div class="card"><p class="empty">There are no ground-truth labels for this data, so Verity can't measure accuracy on it.<br>Run the hard test set from Overview to see measured numbers.</p></div>`;
    return;
  }
  const sc = gt.per_scenario || {};
  const weak = sc.small_fee_anomaly && sc.small_fee_anomaly.pct < 100
    ? `<div class="note"><b>Known weakness:</b> small fee anomalies (${sc.small_fee_anomaly.correct}/${sc.small_fee_anomaly.total} correct). A deduction smaller than the amount tolerance looks like a normal gateway fee, so some are auto-matched when they should be flagged.</div>` : "";
  el.innerHTML = `
    <div class="kpis" style="grid-template-columns:repeat(3,1fr)">
      ${kpi("Accuracy", gt.accuracy + "%", `${gt.total_labeled} labelled payments`)}
      ${kpi("Precision", gt.precision + "%", "of auto-matches were correct")}
      ${kpi("Recall", gt.recall + "%", "of clean payments were matched")}
    </div>
    <div class="card"><h3 style="margin-bottom:8px">By scenario</h3>${barRows(Object.entries(sc))}
      <h3 style="margin:18px 0 4px">Confusion</h3>
      <div class="confusion">
        <div><b>${gt.true_positive}</b>correctly auto-matched</div>
        <div><b>${gt.false_positive}</b>auto-matched but should be flagged</div>
        <div><b>${gt.true_negative}</b>correctly flagged</div>
        <div><b>${gt.false_negative}</b>flagged but could be matched</div>
      </div>${weak}
      <div class="note" style="background:var(--soft);border-color:var(--line);color:var(--muted)">Labels are synthetic, so this measures behaviour on planted cases, not production accuracy.</div>
    </div>`;
}

// ---------------------------------------------------------------- batches
function renderBatchPanel(data) {
  const panel = $("batch-panel");
  if (!data.batches || !data.batches.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("batch-sub").textContent = `${data.batches.length} batches`;
  const cls = { verified: "ok", mismatch: "bad" };
  $("batch-list").innerHTML = `<table class="tbl"><thead><tr><th>Batch</th><th>Detail</th><th>Status</th></tr></thead><tbody>` +
    data.batches.map(b => `<tr><td class="id">${esc(b.batch_id)}</td><td class="sub">${esc(b.reason)}</td><td>${chip(String(b.status).replace(/_/g, " "), cls[b.status] || "warn")}</td></tr>`).join("") + `</tbody></table>`;
  const un = data.unrelated_bank_lines || [];
  $("batch-unrelated").innerHTML = un.length
    ? `<p class="muted" style="margin:14px 0 4px">Bank lines that aren't settlements (excluded, not flagged): ${un.map(u => esc(u.description)).join(" · ")}</p>` : "";
}

// ---------------------------------------------------------------- results
function renderResults(r) {
  state.lastResult = r;
  state.datasetKey = r.dataset_key; state.reviews = r.reviews || {};
  state.statusFilter = "all"; state.openId = null;
  state.filter = "all"; state.search = ""; $("exc-search").value = "";
  state.excLimit = PAGE_SIZE; state.matchLimit = PAGE_SIZE;
  $("nav-matched-count").textContent = `(${r.matched.length})`;
  renderOverview(r); renderExceptions(); renderMatched(); renderAccuracy(r);
  setAiStatus(r.ai_connected, r.model);
}

function adaptExact(data) {
  const toRow = (o) => ({
    payment_id: o.order_id, settlement_ref: o.status === "verified" ? "(exact)" : "—",
    exact: true, status: o.status, reason: o.reason, explanation: o.explanation ?? o.reason,
    amount_a: o.gross, amount_b: o.net, gap: o.status === "math_error" ? o.diff : null,
    date_a: o.date_a, date_b: o.date_b, flags: [],
  });
  const ok = data.orders.filter(o => o.status === "verified");
  return {
    strategy: "exact",
    match_rate: data.total_orders ? round1(100 * ok.length / data.total_orders) : 0,
    throughput_per_sec: data.throughput_per_sec, elapsed_seconds: data.elapsed_seconds,
    matched: ok.map(toRow), exceptions: data.orders.filter(o => o.status !== "verified").map(toRow),
    gt_metrics: null, gt_metrics_exact: data.gt_metrics_exact ?? null,
    dataset_key: data.dataset_key, reviews: data.reviews || {},
    ai_connected: data.ai_connected, model: data.model,
  };
}

// ---------------------------------------------------------------- run
function showDsDesc() { /* descriptions now show as hover tooltips on the dataset tabs */ }
$("dataset-seg").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ds]");
  if (!b) return;
  state.dataset = b.dataset.ds; showDsDesc();
  document.querySelectorAll("#dataset-seg button").forEach(x => x.classList.toggle("on", x === b));
  if (state.dataset === "upload" && !(state.payments && state.settlements)) switchPage("data");
});

async function buildForm(dataset) {
  const fd = new FormData();
  if (dataset === "upload") {
    if (!(state.payments && state.settlements)) throw new Error("Choose a payments file and a settlements file on the Data tab first.");
    fd.append("payment_file", state.payments); fd.append("settlement_file", state.settlements);
    if (state.bank) fd.append("bank_file", state.bank);
  } else if (dataset === "hard") {
    const [p, s] = await Promise.all(["hard_payment_log.csv", "hard_settlement_file.csv"].map(async n => {
      const res = await fetch("/api/sample/" + n);
      if (!res.ok) throw new Error("Hard test set not found on the server. Run python src/generate_hard_data.py.");
      return res.blob();
    }));
    fd.append("payment_file", p, "hard_payment_log.csv"); fd.append("settlement_file", s, "hard_settlement_file.csv");
  }
  return fd;
}

async function runReconciliation(forceDataset) {
  const btns = [$("run-btn"), $("run-btn-2")];
  const labels = btns.map(b => b.textContent);
  btns.forEach(b => { b.disabled = true; b.innerHTML = '<span class="spinner"></span>Running'; });
  clearBanner();
  try {
    const fd = await buildForm(forceDataset || state.dataset);
    const res = await fetch("/api/auto-reconcile", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) { showBanner(data.error || "Request failed", "error"); return; }
    if (data.strategy === "exact") {
      showMethod("Exact verification used", `${data.decision_reason}${data.batch_note ? " " + data.batch_note : ""}`);
      renderResults(adaptExact(data)); renderBatchPanel(data);
    } else {
      showMethod("Tolerance matching used", data.decision_reason);
      renderResults({ ...data, strategy: "tolerance" }); $("batch-panel").hidden = true;
    }
    switchPage("overview");
  } catch (e) {
    showBanner(e.message, "error");
  } finally {
    btns.forEach((b, i) => { b.disabled = false; b.textContent = labels[i]; });
  }
}
$("run-btn").addEventListener("click", () => runReconciliation());
$("run-btn-2").addEventListener("click", () => runReconciliation("upload"));

// ---------------------------------------------------------------- download
function csvCell(v) { if (v === null || v === undefined) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }
$("download-btn").addEventListener("click", () => {
  const r = state.lastResult; if (!r) return;
  const rows = [["payment_id", "settlement_ref", "status", "type", "flags", "confidence", "amount_a", "amount_b", "gap", "reason", "explanation", "review_status", "review_note", "reviewer"]];
  r.matched.forEach(d => rows.push([d.payment_id, d.settlement_ref, "matched", "", (d.flags || []).join(";"), d.confidence, d.amount_a, d.amount_b, "", d.reason, "", "", "", ""]));
  r.exceptions.forEach(d => rows.push([d.payment_id, d.settlement_ref, "exception", KIND[kindOf(d)].label, "", d.confidence, d.amount_a, d.amount_b, gapOf(d) ?? "", d.reason, d.explanation || "", reviewOf(d).status, reviewOf(d).note, reviewOf(d).reviewer]));
  const url = URL.createObjectURL(new Blob([rows.map(x => x.map(csvCell).join(",")).join("\n")], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `verity_report_${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
});

$("excel-btn").addEventListener("click", async () => {
  const r = state.lastResult; if (!r) return;
  const btn = $("excel-btn"), label = btn.textContent;
  btn.disabled = true; btn.textContent = "Preparing…";
  try {
    const kinds = {}; r.exceptions.forEach(e => { kinds[e.payment_id] = KIND[kindOf(e)].label; });
    const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ matched: r.matched, exceptions: r.exceptions, kinds, dataset_key: state.datasetKey,
                             method: r.strategy === "exact" ? "Exact verification" : "Tolerance matching" }) });
    if (!res.ok) throw new Error((await res.json()).error || "Export failed");
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `verity_report_${new Date().toISOString().slice(0, 10)}.xlsx` });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  } catch (e) { showBanner(e.message, "error"); }
  finally { btn.disabled = false; btn.textContent = label; }
});

// ---------------------------------------------------------------- data page
function tableHtml(rows, cols) {
  if (!rows || !rows.length) return `<p class="muted">No rows</p>`;
  return `<table class="tbl"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("") + `</tbody></table>`;
}
async function loadDemoPreview() {
  try {
    const res = await fetch("/api/demo-data"); if (!res.ok) return;
    const d = await res.json();
    $("data-preview").innerHTML = [
      ["Internal orders", d.payment_count, d.payment_preview, d.payment_columns],
      ["Razorpay settlement report", d.settlement_count, d.settlement_preview, d.settlement_columns],
      ["Bank statement", d.bank_count, d.bank_preview, d.bank_columns],
    ].map(([t, n, rows, cols]) => `<div class="col"><div class="col-title">${t} · ${n} rows</div>${tableHtml(rows, cols)}</div>`).join("");
  } catch { /* demo data optional */ }
}

function refreshUploadLabel() {
  const n = [state.payments, state.settlements].filter(Boolean).length;
  $("upload-small").textContent = n === 2 ? "2 files ready" : n ? "1 of 2 files" : "none yet";
}
function setupSlot(inputId, dropId, labelId, key) {
  const input = $(inputId), drop = $(dropId), label = $(labelId);
  input.addEventListener("change", () => {
    if (!input.files.length) return;
    state[key] = input.files[0];
    label.textContent = input.files[0].name; drop.classList.add("filled");
    refreshUploadLabel();
  });
}
$("clear-upload-btn").addEventListener("click", () => {
  state.payments = state.settlements = state.bank = null;
  [["payment-file", "payment-drop", "payment-file-label"], ["settlement-file", "settlement-drop", "settlement-file-label"], ["bank-file", "bank-drop", "bank-file-label"]]
    .forEach(([i, d, l]) => { $(i).value = ""; $(d).classList.remove("filled"); $(l).textContent = "Choose CSV"; });
  refreshUploadLabel();
});

// ---------------------------------------------------------------- ask
async function askVerity() {
  const input = $("ask-input"), q = input.value.trim(); if (!q) return;
  const log = $("chat-log");
  if (log.querySelector(".empty")) log.innerHTML = "";
  log.insertAdjacentHTML("beforeend", `<div class="bubble user">${esc(q)}</div>`);
  input.value = "";
  let text;
  try {
    const d = await (await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) })).json();
    text = d.answer || (d.raw_entries ? "No API key is set, so I can't phrase an answer. The matching audit entries are in the browser console." : (d.error || "Something went wrong."));
    if (d.raw_entries) console.log("Audit entries:", d.raw_entries);
  } catch (e) { text = "Request failed: " + e.message; }
  log.insertAdjacentHTML("beforeend", `<div class="bubble ai">${esc(text)}</div>`);
  log.scrollTop = log.scrollHeight;
}
$("ask-send").addEventListener("click", askVerity);
$("ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askVerity(); });

setupSlot("payment-file", "payment-drop", "payment-file-label", "payments");
setupSlot("settlement-file", "settlement-drop", "settlement-file-label", "settlements");
setupSlot("bank-file", "bank-drop", "bank-file-label", "bank");
showDsDesc();
checkStatus();
loadDemoPreview();