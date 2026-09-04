"""
app.py
------
Verity — AI Reconciliation Agent. Full dashboard, styled to match the
approved design: dark theme, amber accent, card-based layout, architecture
section. Same backend as before — this file only changes presentation.

Run with: streamlit run app.py
"""

import os
import sys
import json
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from schema_mapper import map_schema, apply_mapping
from matcher import reconcile
from explainer import explain_all_exceptions
from audit_log import write_audit_log, load_audit_log
from evaluate import evaluate_against_ground_truth, has_ground_truth
from llm_client import get_client_and_model

DATA_DIR = "data"

st.set_page_config(
    page_title="Verity — AI Reconciliation Agent",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container { padding-top: 1.2rem; max-width: 1400px; }

    .v-topbar {
        display:flex; align-items:center; justify-content:space-between;
        padding: 14px 22px; background:#10141d; border:1px solid #1f2430;
        border-radius:14px; margin-bottom:18px;
    }
    .v-brand { display:flex; align-items:center; gap:12px; }
    .v-logo {
        width:38px; height:38px; border-radius:10px;
        background: linear-gradient(135deg,#d97706,#f59e0b);
        display:flex; align-items:center; justify-content:center; font-size:19px;
    }
    .v-name { font-size:17px; font-weight:800; display:flex; align-items:center; gap:10px; }
    .v-sub { font-size:11px; color:#8b93a3; margin-top:1px; }
    .v-pill {
        font-size:10px; font-weight:700; padding:3px 9px; border-radius:20px;
        background: rgba(217,119,6,0.15); color:#f59e0b; border:1px solid rgba(217,119,6,0.4);
    }
    .v-status { display:flex; align-items:center; gap:7px; font-size:12px; color:#8b93a3; }
    .v-dot { width:7px; height:7px; border-radius:50%; }
    .v-dot.on { background:#22c55e; box-shadow:0 0 6px #22c55e; }
    .v-dot.off { background:#f59e0b; box-shadow:0 0 6px #f59e0b; }

    .v-hero {
        padding: 24px 28px; border-radius:16px; margin-bottom:18px;
        background: linear-gradient(135deg, #14181f, #0f1319); border:1px solid #1f2430;
    }
    .v-hero h1 { font-size:26px; font-weight:800; margin:0 0 6px 0; color:#e6e8ec; }
    .v-hero h1 span { color:#f59e0b; }
    .v-hero p { color:#8b93a3; font-size:13px; margin:0; max-width:640px; }

    .v-metric {
        background:#12161f; border:1px solid #1f2430; border-radius:14px;
        padding:16px 18px; height:100%;
    }
    .v-metric .label { font-size:10.5px; letter-spacing:0.5px; color:#8b93a3; text-transform:uppercase; }
    .v-metric .value { font-size:26px; font-weight:800; margin-top:8px; color:#e6e8ec; }
    .v-metric .foot { font-size:11.5px; margin-top:5px; }
    .foot-green { color:#22c55e; } .foot-amber { color:#f59e0b; } .foot-muted { color:#8b93a3; }

    .v-panel {
        background:#12161f; border:1px solid #1f2430; border-radius:14px;
        padding:18px 20px; margin-bottom: 14px;
    }
    .v-panel h3 { font-size:14.5px; font-weight:800; margin:0 0 2px 0; color:#e6e8ec; }
    .v-panel .sub { font-size:11.5px; color:#8b93a3; margin-bottom:14px; }

    details.v-exc {
        background:#181c26; border-radius:10px; padding:10px 14px; margin-bottom:8px;
        border-left:3px solid #f59e0b;
    }
    details.v-exc[data-sev="high"] { border-left-color:#ef4444; }
    details.v-exc summary { list-style:none; cursor:pointer; display:flex; justify-content:space-between; align-items:center; }
    details.v-exc summary::-webkit-details-marker { display:none; }
    .v-exc-id { font-size:12.5px; font-weight:800; color:#e6e8ec; }
    .v-exc-reason { font-size:11px; color:#8b93a3; margin-top:2px; }
    .v-chip { font-size:10.5px; font-weight:700; padding:4px 10px; border-radius:20px; white-space:nowrap; }
    .chip-amber { background:rgba(245,158,11,0.15); color:#f59e0b; }
    .chip-red { background:rgba(239,68,68,0.15); color:#ef4444; }
    .v-exc-body { font-size:11.5px; color:#c7cede; margin-top:10px; line-height:1.6; }

    .v-stage { display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid #1f2430; }
    .v-stage:last-child { border-bottom:none; }
    .v-stage-name { font-size:12px; font-weight:700; flex:1; color:#e6e8ec; }
    .v-badge-on { font-size:9px; font-weight:800; color:#22c55e; background:rgba(34,197,94,0.12); padding:3px 8px; border-radius:20px; }

    .v-arch-flow { display:flex; gap:8px; }
    .v-arch-box { flex:1; background:#12161f; border:1px solid #1f2430; border-radius:12px; padding:14px 10px; text-align:center; }
    .v-arch-box .emoji { font-size:22px; margin-bottom:6px; }
    .v-arch-box .title { font-size:11px; font-weight:800; color:#e6e8ec; margin-bottom:3px; }
    .v-arch-box .desc { font-size:9.5px; color:#8b93a3; line-height:1.35; }
    .v-arch-arrow { display:flex; align-items:center; justify-content:center; width:22px; color:#f59e0b; font-size:16px; font-weight:800; }
    .v-tag-row { display:flex; gap:8px; margin-top:14px; flex-wrap:wrap; }
    .v-tag { font-size:10px; background:#181c26; color:#8b93a3; padding:5px 11px; border-radius:8px; border:1px solid #1f2430; }

    div[data-testid="stButton"] button[kind="primary"] {
        background: linear-gradient(90deg,#d97706,#f59e0b); border:none; color:#0b0e14;
        font-weight:700; box-shadow:0 0 18px rgba(245,158,11,0.3);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

client, model = get_client_and_model()
ai_status_dot = "on" if client is not None else "off"
ai_status_text = f"AI Connected ({model.split('/')[-1] if model else ''})" if client else "Fallback mode (no API key)"

st.markdown(
    f"""
    <div class="v-topbar">
        <div class="v-brand">
            <div class="v-logo">🧾</div>
            <div>
                <div class="v-name">Verity <span class="v-pill">TRACK 4</span></div>
                <div class="v-sub">AI Reconciliation Agent</div>
            </div>
        </div>
        <div class="v-status"><div class="v-dot {ai_status_dot}"></div>{ai_status_text}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="v-hero">
        <h1>Verity <span>Reconciliation Engine</span></h1>
        <p>Autonomous schema-agnostic matching between payment and settlement records —
        with honest, explainable exceptions instead of blind guesses.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def load_default_data():
    payments = pd.read_csv(os.path.join(DATA_DIR, "payment_log.csv"))
    settlements = pd.read_csv(os.path.join(DATA_DIR, "settlement_file.csv"))
    return payments, settlements


st.markdown("#### 📤 Try it on your own data")
st.caption("Upload any two transaction CSVs — column names don't need to match anything below. Leave both empty to use the built-in demo dataset.")

up_col1, up_col2 = st.columns(2)
with up_col1:
    uploaded_payments = st.file_uploader("Payment / source file (CSV)", type="csv", key="uploaded_payments")
with up_col2:
    uploaded_settlements = st.file_uploader("Settlement / comparison file (CSV)", type="csv", key="uploaded_settlements")

using_uploaded_data = uploaded_payments is not None and uploaded_settlements is not None

if using_uploaded_data:
    try:
        payments_raw = pd.read_csv(uploaded_payments)
        settlements_raw = pd.read_csv(uploaded_settlements)
        st.success(f"Using your uploaded files — {len(payments_raw)} and {len(settlements_raw)} rows. Mapping below is live, not pre-configured.")
    except Exception as e:
        st.error(f"Could not read one of the uploaded files: {e}")
        st.stop()
elif uploaded_payments is not None or uploaded_settlements is not None:
    st.info("Upload BOTH files to use your own data, or clear both to use the demo dataset.")
    if not os.path.exists(os.path.join(DATA_DIR, "payment_log.csv")):
        st.warning("No demo data found either. Run `python src/generate_data.py` first.")
        st.stop()
    payments_raw, settlements_raw = load_default_data()
else:
    if not os.path.exists(os.path.join(DATA_DIR, "payment_log.csv")):
        st.warning("No data found. Run `python src/generate_data.py` first, then reload this page.")
        st.stop()
    payments_raw, settlements_raw = load_default_data()
    st.caption(f"Currently showing the built-in demo dataset ({len(payments_raw)} simulated records).")

col1, col2 = st.columns(2)
with col1:
    st.markdown("**📄 Payment log (raw)**")
    st.dataframe(payments_raw.head(5), use_container_width=True, hide_index=True)
with col2:
    st.markdown("**🏦 Settlement file (raw)**")
    st.dataframe(settlements_raw.head(5), use_container_width=True, hide_index=True)

data_fingerprint = (len(payments_raw), len(settlements_raw), tuple(payments_raw.columns), tuple(settlements_raw.columns))
if st.session_state.get("data_fingerprint") != data_fingerprint:
    st.session_state.pop("result", None)
    st.session_state.pop("explanations", None)
    st.session_state.pop("gt_metrics", None)
    st.session_state["data_fingerprint"] = data_fingerprint

st.write("")
run_clicked = st.button("▶️  Run Reconciliation", type="primary")

if run_clicked:
    try:
        with st.spinner("Mapping unfamiliar schemas..."):
            payment_mapping = map_schema(payments_raw, "payment_log")
            settlement_mapping = map_schema(settlements_raw, "settlement_file")
            payments = apply_mapping(payments_raw, payment_mapping)
            settlements = apply_mapping(settlements_raw, settlement_mapping)
    except ValueError as e:
        st.error(f"Couldn't confidently map this file's columns: {e}\n\nThis is the agent correctly refusing to guess on a schema it can't resolve.")
        st.stop()

    st.success(f"Schema mapping resolved — payment: {payment_mapping}, settlement: {settlement_mapping}")

    with st.spinner("Matching records..."):
        result = reconcile(payments, settlements)

    with st.spinner("Generating plain-English explanations..."):
        explanations = explain_all_exceptions(result["exceptions"])
        write_audit_log(result["decisions"], explanations)

    gt_metrics = None
    if not using_uploaded_data and has_ground_truth():
        gt_metrics = evaluate_against_ground_truth(result["decisions"])

    st.session_state["result"] = result
    st.session_state["explanations"] = explanations
    st.session_state["gt_metrics"] = gt_metrics
    st.session_state["using_uploaded_data"] = using_uploaded_data

if "result" in st.session_state:
    result = st.session_state["result"]
    explanations = st.session_state["explanations"]
    gt_metrics = st.session_state.get("gt_metrics")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f"""<div class="v-metric"><div class="label">✅ MATCH RATE</div>
        <div class="value">{result['match_rate']}%</div>
        <div class="foot foot-green">📈 {len(result['matched'])} of {len(result['decisions'])} auto-matched</div></div>""", unsafe_allow_html=True)
    with m2:
        acc_display = f"{gt_metrics['accuracy']}%" if gt_metrics else "—"
        acc_foot = "🔍 Validated vs ground truth" if gt_metrics else "Upload has no ground truth"
        st.markdown(f"""<div class="v-metric"><div class="label">🎯 ACCURACY</div>
        <div class="value">{acc_display}</div>
        <div class="foot foot-muted">{acc_foot}</div></div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""<div class="v-metric"><div class="label">⚠️ EXCEPTIONS</div>
        <div class="value">{len(result['exceptions'])}</div>
        <div class="foot foot-amber">🚩 Flagged, not guessed</div></div>""", unsafe_allow_html=True)
    with m4:
        st.markdown(f"""<div class="v-metric"><div class="label">⚡ THROUGHPUT</div>
        <div class="value">{result['throughput_per_sec']}<span style="font-size:13px;color:#8b93a3;">/sec</span></div>
        <div class="foot foot-muted">⏱️ {len(result['decisions'])} records in {result['elapsed_seconds']}s</div></div>""", unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([1.5, 1])

    with left:
        exc_html = """<div class="v-panel"><h3>🔎 Exceptions — not auto-matched on purpose</h3>
        <div class="sub">Every flag has a specific, auditable reason</div>"""
        for d in result["exceptions"]:
            sev = "high" if "duplicate" in d.reason.lower() else "normal"
            chip_class = "chip-red" if sev == "high" else "chip-amber"
            chip_text = "Needs review" if sev == "high" else f"{d.confidence:.0f}% confidence"
            explanation = explanations.get(d.payment_id, "").replace('"', "&quot;")
            exc_html += f"""
            <details class="v-exc" data-sev="{sev}">
                <summary>
                    <div><div class="v-exc-id">{d.payment_id}</div><div class="v-exc-reason">{d.reason[:80]}</div></div>
                    <div class="v-chip {chip_class}">{chip_text}</div>
                </summary>
                <div class="v-exc-body">
                    <b>Reason:</b> {d.reason}<br><br>
                    <b>Explanation:</b> {explanation}<br><br>
                    Amount A: {d.amount_a} · Amount B: {d.amount_b}<br>
                    Date A: {d.date_a} · Date B: {d.date_b}
                </div>
            </details>"""
        exc_html += "</div>"
        st.markdown(exc_html, unsafe_allow_html=True)

    with right:
        stages = [
            ("📄 Schema Mapper", True),
            ("🔗 Matching Engine", True),
            ("🕵️ Duplicate Detector", True),
            (f"💬 Explainer ({model.split('/')[-1] if model else 'fallback'})", client is not None),
            ("🗂️ Audit Logger", True),
        ]
        stage_html = """<div class="v-panel"><h3>🧭 Pipeline Status</h3><div class="sub">Live agent stages</div>"""
        for name, online in stages:
            badge = '<div class="v-badge-on">ONLINE</div>' if online else '<div class="v-badge-on" style="color:#f59e0b;background:rgba(245,158,11,0.12);">FALLBACK</div>'
            stage_html += f'<div class="v-stage"><div class="v-stage-name">{name}</div>{badge}</div>'
        stage_html += "</div>"
        st.markdown(stage_html, unsafe_allow_html=True)

        if gt_metrics:
            st.markdown(f"""<div class="v-panel"><h3>📊 Precision / Recall</h3>
            <div class="sub">Against known-correct labels</div>
            <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:8px;">
                <span style="color:#8b93a3;">Precision</span><b style="color:#e6e8ec;">{gt_metrics['precision']}%</b>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:12px;">
                <span style="color:#8b93a3;">Recall</span><b style="color:#e6e8ec;">{gt_metrics['recall']}%</b>
            </div></div>""", unsafe_allow_html=True)

    st.markdown("""<div class="v-panel"><h3>💬 Ask Verity</h3><div class="sub">Ask about the reconciliation run in plain English</div></div>""", unsafe_allow_html=True)
    query = st.text_input("e.g. \"why didn't TXN00042 match?\" or \"how many were amount mismatches?\"", key="nl_query", label_visibility="collapsed")
    if query:
        log = load_audit_log()
        query_lower = query.lower()
        relevant = [r for r in log if r["payment_id"].lower() in query_lower] or [r for r in log if r["status"] == "exception"]
        context = json.dumps(relevant[:15], indent=2)

        if client is not None:
            prompt = f"""You are answering a question about a reconciliation audit log.
Audit log entries (JSON): {context}

Question: {query}

Answer directly and concisely, citing specific payment IDs where relevant."""
            try:
                resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0)
                st.info(resp.choices[0].message.content.strip())
            except Exception as e:
                st.error(f"LLM query failed ({e}) — showing raw entries instead:")
                st.json(relevant[:5])
        else:
            st.info("No API key set — showing raw matching audit log entries instead:")
            st.json(relevant[:5])
else:
    st.info("👆 Click **Run Reconciliation** above to see results.")

st.write("")
st.markdown("#### 🏗️ How Verity works — architecture")
st.caption("Five-stage pipeline · rule-based core with LLM assistance, never a black box")

arch_stages = [
    ("📄", "Schema Mapper", "Reads unfamiliar column names + samples, maps to id/amount/date on its own"),
    ("🔗", "Matching Engine", "Scores every candidate pair on amount, date + reference similarity"),
    ("🕵️", "Duplicate Detector", "Flags near-identical settlement copies instead of guessing which is real"),
    ("💬", "Explainer (Groq)", "Turns raw confidence scores into plain-English exception reasons"),
    ("🗂️", "Audit Logger", "Every decision — matched or flagged — recorded with full reasoning"),
]
arch_html = '<div class="v-arch-flow">'
for i, (emoji, title, desc) in enumerate(arch_stages):
    if i > 0:
        arch_html += '<div class="v-arch-arrow">→</div>'
    arch_html += f"""<div class="v-arch-box"><div class="emoji">{emoji}</div>
    <div class="title">{title}</div><div class="desc">{desc}</div></div>"""
arch_html += "</div>"
arch_html += """<div class="v-tag-row">
<div class="v-tag">🐍 Python + Pandas</div>
<div class="v-tag">🧠 Groq LLaMA (free tier)</div>
<div class="v-tag">🔍 RapidFuzz matching</div>
<div class="v-tag">📊 Streamlit dashboard</div>
<div class="v-tag">✅ 12 automated tests</div>
</div>"""
st.markdown(arch_html, unsafe_allow_html=True)
