const state = { payments: null, settlements: null, bank: null, lastResult: null, filter: "all" };

// ---------------------------------------------------------------------------
// Tab navigation — real pages, not a single long scroll
// ---------------------------------------------------------------------------
function switchPage(pageName) {
  document.querySelectorAll(".navtab").forEach(t => t.classList.toggle("on", t.dataset.page === pageName));
  document.querySelectorAll(".page").forEach(p => p.hidden = p.dataset.page !== pageName);
  window.scrollTo({ top: 0, behavior: "instant" });
}

document.getElementById("navtabs").addEventListener("click", (ev) => {
  const tab = ev.target.closest(".navtab");
  if (!tab) return;
  switchPage(tab.dataset.page);
});

// ---------------------------------------------------------------------------
// Status + banners
// ---------------------------------------------------------------------------
async function checkStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    setAiStatus(data.ai_connected, data.model);
  } catch (e) {
    setAiStatus(false, null);
  }
}

function setAiStatus(connected, model) {
  const dot = document.getElementById("ai-dot");
  const text = document.getElementById("ai-status-text");
  dot.className = "dot " + (connected ? "on" : "off");
  text.textContent = connected ? `AI Connected (${model})` : "Fallback mode (no API key)";
}

function showBanner(message, type) {
  document.getElementById("status-banner").innerHTML = `<div class="banner ${type}">${message}</div>`;
}
function clearBanner() {
  document.getElementById("status-banner").innerHTML = "";
}

// ---------------------------------------------------------------------------
// Data preview (Upload Data page)
// ---------------------------------------------------------------------------
function renderTable(rows, columns) {
  if (!rows || !rows.length) return "<p style='color:#8b93a3;font-size:12px;'>No rows</p>";
  let html = "<table><thead><tr>";
  columns.forEach(c => html += `<th>${c}</th>`);
  html += "</tr></thead><tbody>";
  rows.forEach(r => {
    html += "<tr>";
    columns.forEach(c => html += `<td>${r[c] ?? ""}</td>`);
    html += "</tr>";
  });
  html += "</tbody></table>";
  return html;
}

async function loadDemoPreview() {
  try {
    const res = await fetch("/api/demo-data");
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById("data-preview").innerHTML = `
      <div class="col">
        <div class="col-title">📄 Payment log — raw (${data.payment_count} rows)</div>
        ${renderTable(data.payment_preview, data.payment_columns)}
      </div>
      <div class="col">
        <div class="col-title">🏦 Settlement file — raw (${data.settlement_count} rows)</div>
        ${renderTable(data.settlement_preview, data.settlement_columns)}
      </div>`;
  } catch (e) { /* silent — demo data may not exist yet */ }
}

function setupUpload(inputId, dropId, labelId, stateKey) {
  const input = document.getElementById(inputId);
  const drop = document.getElementById(dropId);
  const label = document.getElementById(labelId);
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files.length) {
      state[stateKey] = input.files[0];
      label.textContent = input.files[0].name;
      drop.classList.add("filled");
    }
  });
}

document.getElementById("clear-upload-btn").addEventListener("click", () => {
  state.payments = null;
  state.settlements = null;
  state.bank = null;
  ["payment-file", "settlement-file", "bank-file"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("payment-file-label").textContent = "Payment / source file (CSV)";
  document.getElementById("settlement-file-label").textContent = "Settlement / comparison file (CSV)";
  document.getElementById("bank-file-label").textContent = "Bank statement (optional) — enables multi-source batch verification";
  document.getElementById("payment-drop").classList.remove("filled");
  document.getElementById("settlement-drop").classList.remove("filled");
  document.getElementById("bank-drop").classList.remove("filled");
  loadDemoPreview();
  showBanner("Cleared — will use the built-in demo dataset on next run.", "info");
});

// ---------------------------------------------------------------------------
// Matched records (Matched page)
// ---------------------------------------------------------------------------
function renderMatched() {
  const list = document.getElementById("matched-list");
  if (!state.lastResult) {
    list.innerHTML = `<p class="empty-state">Run reconciliation from the Dashboard tab to see matched records here.</p>`;
    return;
  }
  const matched = state.lastResult.matched;
  if (!matched.length) {
    list.innerHTML = `<p class="empty-state">No records were auto-matched.</p>`;
    return;
  }
  list.innerHTML = matched.map(d => {
    const chip = d.exact
      ? `<div class="chip" style="background:rgba(34,197,94,0.15);color:#22c55e;">✓ VERIFIED (exact)</div>`
      : `<div class="chip" style="background:rgba(34,197,94,0.15);color:#22c55e;">${Math.round(d.confidence)}% confidence</div>`;
    return `
    <details class="exc" data-sev="normal" style="border-left-color:#22c55e;">
      <summary>
        <div><div class="exc-id">${d.payment_id} → ${d.settlement_ref}</div><div class="exc-reason">${d.reason}</div></div>
        ${chip}
      </summary>
      <div class="exc-body">
        Amount A: ${d.amount_a ?? "—"} · Amount B: ${d.amount_b ?? "—"}<br>
        Date A: ${d.date_a ?? "—"} · Date B: ${d.date_b ?? "—"}
      </div>
    </details>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// CSV export — matched + exceptions, client-side, no backend round-trip
// ---------------------------------------------------------------------------
function toCsvValue(v) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadReport() {
  if (!state.lastResult) return;
  const rows = [["payment_id", "settlement_ref", "status", "confidence", "reason", "amount_a", "amount_b", "date_a", "date_b", "explanation"]];
  state.lastResult.matched.forEach(d => rows.push([
    d.payment_id, d.settlement_ref, "matched", d.confidence, d.reason, d.amount_a, d.amount_b, d.date_a, d.date_b, ""
  ]));
  state.lastResult.exceptions.forEach(d => rows.push([
    d.payment_id, d.settlement_ref, "exception", d.confidence, d.reason, d.amount_a, d.amount_b, d.date_a, d.date_b, d.explanation || ""
  ]));
  const csv = rows.map(r => r.map(toCsvValue).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `verity_reconciliation_report_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

document.getElementById("download-btn").addEventListener("click", downloadReport);

// ---------------------------------------------------------------------------
// Exceptions (Exceptions page)
// ---------------------------------------------------------------------------
function reasonCategory(e) {
  // Exact-verification exceptions carry a real, structured status — use
  // that directly instead of guessing from English text (which is what
  // caused the original bug: the exact engine says "shortfall"/"excess",
  // never the word "mismatch" the tolerance engine's language uses).
  if (e.exact) {
    if (e.status === "math_error") return "amount";
    if (e.status === "pending_settlement") return "delayed";
    return "other"; // unmatched_to_reference, prior_period_deduction
  }
  const r = e.reason.toLowerCase();
  if (r.includes("duplicate")) return "duplicate";
  if (r.includes("days after") || r.includes("late")) return "delayed";
  if (r.includes("mismatch")) return "amount";
  return "other";
}

function renderExceptions() {
  const list = document.getElementById("exceptions-list");
  if (!state.lastResult) {
    list.innerHTML = `<p class="empty-state">Run reconciliation from the Dashboard tab to see exceptions here.</p>`;
    return;
  }
  const exceptions = state.lastResult.exceptions.filter(
    e => state.filter === "all" || reasonCategory(e) === state.filter
  );
  if (!exceptions.length) {
    list.innerHTML = `<p class="empty-state">No exceptions in this category. 🎉</p>`;
    return;
  }
  const EXACT_STATUS_LABELS = {
    math_error: ["🧮 Math error", "chip-red"],
    unmatched_to_reference: ["❓ Unmatched", "chip-red"],
    prior_period_deduction: ["↩️ Prior-period", "chip-amber"],
    pending_settlement: ["⏳ Pending", "chip-amber"],
  };

  list.innerHTML = exceptions.map(e => {
    let chipClass, chipText, sev;
    if (e.exact) {
      const [label, cls] = EXACT_STATUS_LABELS[e.status] || ["Flagged", "chip-amber"];
      chipClass = cls;
      chipText = label;
      sev = cls === "chip-red" ? "high" : "normal";
    } else {
      sev = reasonCategory(e) === "duplicate" ? "high" : "normal";
      chipClass = sev === "high" ? "chip-red" : "chip-amber";
      chipText = sev === "high" ? "Needs review" : `${Math.round(e.confidence)}% confidence`;
    }
    return `<details class="exc" data-sev="${sev}">
      <summary>
        <div><div class="exc-id">${e.payment_id}</div><div class="exc-reason">${e.reason.slice(0, 90)}</div></div>
        <div class="chip ${chipClass}">${chipText}</div>
      </summary>
      <div class="exc-body">
        <b>Reason:</b> ${e.reason}<br><br>
        <b>Explanation:</b> ${e.explanation || "—"}<br><br>
        Amount A: ${e.amount_a ?? "—"} · Amount B: ${e.amount_b ?? "—"}<br>
        Date A: ${e.date_a ?? "—"} · Date B: ${e.date_b ?? "—"}
      </div>
    </details>`;
  }).join("");
}

document.getElementById("filter-tabs").addEventListener("click", (ev) => {
  const target = ev.target.closest("[data-filter]");
  if (!target) return;
  document.querySelectorAll("#filter-tabs div").forEach(d => d.classList.remove("on"));
  target.classList.add("on");
  state.filter = target.dataset.filter;
  renderExceptions();
});

// ---------------------------------------------------------------------------
// Accuracy page
// ---------------------------------------------------------------------------
function renderAccuracyPage(result) {
  const el = document.getElementById("accuracy-content");

  if (result.strategy === "exact") {
    const gt = result.gt_metrics_exact;
    let html = `
      <p style="font-size:12.5px;color:#c7cede;line-height:1.7;margin-bottom:14px;">
        This run used <b>exact arithmetic verification</b>, not probabilistic matching —
        every classification (verified / math error / unmatched / prior-period deduction)
        is a <b>computed mathematical fact</b> (Gross − Fee − Tax = Net, checked to the rupee),
        not a guess.
      </p>`;

    if (gt) {
      const statusRows = Object.entries(gt.per_status).map(([name, v]) =>
        `<tr><td style="text-transform:capitalize;">${name.replace(/_/g, " ")}</td><td>${v.correct}/${v.total}</td><td>${v.pct}%</td></tr>`
      ).join("");
      html += `
        <p style="font-size:12.5px;color:#c7cede;line-height:1.7;margin-bottom:14px;">
          Since this demo data has deliberately planted issues with known-correct answers,
          the engine's classifications can still be <b>measured</b> against ground truth —
          not just claimed as trustworthy because it's arithmetic:
        </p>
        <div class="metrics" style="margin-bottom:20px;">
          <div class="metric-card"><div class="label">ACCURACY vs GROUND TRUTH</div><div class="value">${gt.accuracy}%</div></div>
          <div class="metric-card"><div class="label">CORRECT</div><div class="value">${gt.correct} / ${gt.total_labeled}</div></div>
        </div>
        <h3 style="font-size:13px;margin-bottom:10px;">By true status</h3>
        <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:20px;">
          <thead><tr style="color:#8b93a3;text-align:left;">
            <th style="padding:8px;border-bottom:1px solid #1f2430;">True status</th>
            <th style="padding:8px;border-bottom:1px solid #1f2430;">Correct</th>
            <th style="padding:8px;border-bottom:1px solid #1f2430;">%</th>
          </tr></thead>
          <tbody>${statusRows}</tbody>
        </table>`;
    } else {
      html += `<p class="empty-state">No ground-truth labels available for this data — the math itself is still
        the check (there's no separate probabilistic guess to validate), but a measured accuracy
        number isn't available for this specific dataset.</p>`;
    }

    html += `
      <p class="empty-state" style="margin-top:14px;">
        Want to see probabilistic accuracy validation instead? Upload
        <code>data/payment_log.csv</code> and <code>data/settlement_file.csv</code>
        on the Upload Data page — that pair has no fee breakdown, so the
        tolerance-matching engine takes over, and its match decisions get
        validated against known-correct synthetic labels (precision/recall) here.
      </p>`;
    el.innerHTML = html;
    return;
  }

  if (result.using_uploaded_data && !result.gt_metrics) {
    el.innerHTML = `<p class="empty-state">Accuracy validation only works when the uploaded data's payment IDs
      correspond to the built-in synthetic ground truth — your uploaded data doesn't, so there's no
      known-correct answer to check against here.</p>`;
    return;
  }
  if (!result.gt_metrics) {
    el.innerHTML = `<p class="empty-state">No ground truth file found for the demo dataset.</p>`;
    return;
  }
  const gt = result.gt_metrics;
  const scenarioRows = Object.entries(gt.per_scenario).map(([name, v]) =>
    `<tr><td style="text-transform:capitalize;">${name}</td><td>${v.correct}/${v.total}</td><td>${v.pct}%</td></tr>`
  ).join("");

  el.innerHTML = `
    <div class="metrics" style="margin-bottom:20px;">
      <div class="metric-card"><div class="label">ACCURACY</div><div class="value">${gt.accuracy}%</div></div>
      <div class="metric-card"><div class="label">PRECISION</div><div class="value">${gt.precision}%</div></div>
      <div class="metric-card"><div class="label">RECALL</div><div class="value">${gt.recall}%</div></div>
    </div>
    <h3 style="font-size:13px;margin-bottom:10px;">By scenario type</h3>
    <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:20px;">
      <thead><tr style="color:#8b93a3;text-align:left;">
        <th style="padding:8px;border-bottom:1px solid #1f2430;">Scenario</th>
        <th style="padding:8px;border-bottom:1px solid #1f2430;">Correct</th>
        <th style="padding:8px;border-bottom:1px solid #1f2430;">%</th>
      </tr></thead>
      <tbody>${scenarioRows}</tbody>
    </table>
    <details class="exc" data-sev="normal">
      <summary><div class="exc-id">What do these numbers mean?</div></summary>
      <div class="exc-body">
        <b>True positives:</b> ${gt.true_positive} — genuinely clean transactions correctly auto-matched<br>
        <b>False positives:</b> ${gt.false_positive} — auto-matched something that should have been flagged<br>
        <b>True negatives:</b> ${gt.true_negative} — genuinely problematic transactions correctly flagged<br>
        <b>False negatives:</b> ${gt.false_negative} — flagged something that could have safely auto-matched<br><br>
        Small fee deductions under the tolerance threshold, and settlements arriving within the normal
        lag window, are intentionally auto-matched even though they're technically not "clean" scenarios —
        that's a deliberate tolerance design choice, not an error.
      </div>
    </details>`;
}

// ---------------------------------------------------------------------------
// Pipeline status
// ---------------------------------------------------------------------------
function renderPipeline(result) {
  const stages = [
    ["📄 Schema Mapper", true],
    ["🔗 Matching Engine", true],
    ["🕵️ Duplicate Detector", true],
    [`💬 Explainer (${result.model || "fallback"})`, result.ai_connected],
    ["🗂️ Audit Logger", true],
  ];
  document.getElementById("pipeline-stages").innerHTML = stages.map(([name, online]) =>
    `<div class="stage"><div class="stage-name">${name}</div>
     <div class="${online ? 'badge-on' : 'badge-fallback'}">${online ? 'ONLINE' : 'FALLBACK'}</div></div>`
  ).join("");
}

// ---------------------------------------------------------------------------
// Main results render (Dashboard metrics + all dependent pages)
// ---------------------------------------------------------------------------
function renderResults(result) {
  state.lastResult = result;

  document.getElementById("m-match-rate").textContent = result.match_rate + "%";
  document.getElementById("m-match-foot").textContent =
    `📈 ${result.matched.length} of ${result.matched.length + result.exceptions.length} auto-matched`;

  if (result.gt_metrics) {
    document.getElementById("m-accuracy").textContent = result.gt_metrics.accuracy + "%";
    document.getElementById("m-accuracy-foot").textContent = "🔍 Validated vs ground truth";
  } else if (result.strategy === "exact") {
    document.getElementById("m-accuracy").textContent = "N/A";
    document.getElementById("m-accuracy-foot").textContent = "🧮 Exact math — see Accuracy tab";
  } else {
    document.getElementById("m-accuracy").textContent = "—";
    document.getElementById("m-accuracy-foot").textContent = "No ground truth for this data";
  }

  document.getElementById("m-exceptions").textContent = result.exceptions.length;
  document.getElementById("nav-exc-count").textContent = `(${result.exceptions.length})`;
  document.getElementById("nav-matched-count").textContent = `(${result.matched.length})`;
  document.getElementById("m-throughput").innerHTML = result.throughput_per_sec + "<span style='font-size:13px;color:#8b93a3;'>/sec</span>";
  document.getElementById("m-throughput-foot").textContent =
    `⏱️ ${result.matched.length + result.exceptions.length} records in ${result.elapsed_seconds}s`;

  renderExceptions();
  renderMatched();
  renderPipeline(result);
  renderAccuracyPage(result);
  setAiStatus(result.ai_connected, result.model);
  document.getElementById("dl-row").style.display = "block";
}

// ---------------------------------------------------------------------------
// Run reconciliation — triggered from either Dashboard or Upload Data page
// ---------------------------------------------------------------------------
function formatMapping(mapping) {
  return Object.entries(mapping).map(([field, col]) => `${field} → "${col}"`).join(", ");
}

function adaptExactResultForExistingUI(data) {
  /* Reuses the existing Matched/Exceptions UI (built for the tolerance
     engine) to display exact-verification results too. IMPORTANT: exact
     verification is deterministic, not probabilistic — there is no real
     "confidence score" to show. Each row carries its real status instead
     of an invented percentage, and the renderer displays that status as
     a label (VERIFIED / MATH ERROR / UNMATCHED), never a fake number. */
  const verified = data.orders.filter(o => o.status === "verified");
  const flagged = data.orders.filter(o => o.status !== "verified");
  const toRow = (o) => ({
    payment_id: o.order_id,
    settlement_ref: o.status === "verified" ? "(exact match)" : "—",
    exact: true,
    status: o.status,
    reason: o.reason,
    explanation: o.explanation ?? o.reason,
    amount_a: o.gross, amount_b: o.net, date_a: o.date_a ?? "—", date_b: o.date_b ?? "—",
  });
  return {
    match_rate: data.total_orders ? round1(100 * verified.length / data.total_orders) : 0,
    throughput_per_sec: data.throughput_per_sec,
    elapsed_seconds: data.elapsed_seconds,
    matched: verified.map(toRow),
    exceptions: flagged.map(toRow),
    gt_metrics: null,
    ai_connected: data.ai_connected,
    model: data.model,
    using_uploaded_data: data.using_uploaded_data,
    strategy: "exact",
    gt_metrics_exact: data.gt_metrics_exact ?? null,
  };
}

function round1(n) { return Math.round(n * 10) / 10; }

async function runReconciliation() {
  const buttons = [document.getElementById("run-btn"), document.getElementById("run-btn-2")];
  buttons.forEach(b => { b.disabled = true; b.innerHTML = '<span class="spinner"></span> Running...'; });
  clearBanner();

  const formData = new FormData();
  if (state.payments && state.settlements) {
    formData.append("payment_file", state.payments);
    formData.append("settlement_file", state.settlements);
  }
  if (state.bank) {
    formData.append("bank_file", state.bank);
  }

  try {
    const res = await fetch("/api/auto-reconcile", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      showBanner(`⚠️ ${data.error}`, "error");
      return;
    }

    if (data.strategy === "exact") {
      let banner = `🧮 Exact verification used — ${data.decision_reason}`;
      if (data.batch_note) banner += ` ℹ️ ${data.batch_note}`;
      showBanner(banner, "success");
      renderResults(adaptExactResultForExistingUI(data));
      renderBatchPanel(data);
    } else {
      showBanner(`📐 Tolerance matching used — ${data.decision_reason}`, "success");
      renderResults(data);
      document.getElementById("batch-panel").style.display = "none";
    }
    switchPage("dashboard");
  } catch (e) {
    showBanner(`⚠️ Request failed: ${e.message}`, "error");
  } finally {
    buttons.forEach(b => { b.disabled = false; b.innerHTML = "▶ Run Reconciliation"; });
  }
}

function renderBatchPanel(data) {
  const panel = document.getElementById("batch-panel");
  if (!data.batches || !data.batches.length) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "block";

  const listEl = document.getElementById("batch-list");
  listEl.innerHTML = data.batches.map(b => {
    const color = b.status === "verified" ? "#22c55e" : b.status === "mismatch" ? "#ef4444" : "#f59e0b";
    return `<div class="stage">
      <div class="stage-name">${b.batch_id}<br><span style="font-weight:400;color:#8b93a3;font-size:10.5px;">${b.reason}</span></div>
      <div class="badge-on" style="color:${color};background:${color}22;">${b.status.replace('_',' ').toUpperCase()}</div>
    </div>`;
  }).join("");

  const unrelatedEl = document.getElementById("batch-unrelated");
  const unrelated = data.unrelated_bank_lines || [];
  if (!unrelated.length) {
    unrelatedEl.innerHTML = "";
  } else {
    unrelatedEl.innerHTML = `<div style="font-size:11.5px;color:#8b93a3;margin-bottom:8px;">🧾 Unrelated bank activity (excluded, not flagged):</div>` +
      unrelated.map(u => `<div class="stage"><div class="stage-name">${u.description}</div><div style="color:#5f6a80;font-size:11px;">₹${u.amount ?? 0}</div></div>`).join("");
  }
}

document.getElementById("run-btn").addEventListener("click", runReconciliation);
document.getElementById("run-btn-2").addEventListener("click", runReconciliation);

// ---------------------------------------------------------------------------
// Ask Verity
// ---------------------------------------------------------------------------
async function askVerity() {
  const input = document.getElementById("ask-input");
  const query = input.value.trim();
  if (!query) return;
  const log = document.getElementById("chat-log");
  log.innerHTML += `<div class="chat-bubble user">${query}</div>`;
  input.value = "";
  log.scrollTop = log.scrollHeight;

  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    const answer = data.answer || (data.raw_entries ? "No API key set — check the raw audit log in the browser console." : "Something went wrong.");
    if (data.raw_entries) console.log("Raw audit entries:", data.raw_entries);
    log.innerHTML += `<div class="chat-bubble ai">${answer}</div>`;
    log.scrollTop = log.scrollHeight;
  } catch (e) {
    log.innerHTML += `<div class="chat-bubble ai">Request failed: ${e.message}</div>`;
  }
}

document.getElementById("ask-send").addEventListener("click", askVerity);
document.getElementById("ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askVerity(); });

setupUpload("payment-file", "payment-drop", "payment-file-label", "payments");
setupUpload("settlement-file", "settlement-drop", "settlement-file-label", "settlements");
setupUpload("bank-file", "bank-drop", "bank-file-label", "bank");

checkStatus();
loadDemoPreview();
