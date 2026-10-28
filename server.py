"""
server.py
---------
Flask backend for Verity. Serves the custom HTML/CSS/JS frontend (web/) and
exposes the SAME reconciliation logic from src/ as a JSON API — no backend
logic was rewritten, only re-exposed. This exists because Streamlit couldn't
achieve pixel-accurate control over the approved design; plain HTML/CSS/JS
has no such ceiling.

Run with: python server.py
Then open: http://localhost:5000
"""

import os
import sys
import io
import json
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from logger import get_logger
from schema_mapper import map_schema, apply_mapping
from matcher import reconcile
from explainer import explain_all_exceptions, explain_all_exact_exceptions
from audit_log import write_audit_log, write_audit_log_exact, load_audit_log
from evaluate import evaluate_against_ground_truth, has_ground_truth, evaluate_exact_against_ground_truth
from llm_client import get_client_and_model
from auto_reconciler import run_auto_reconciliation
from excel_export import build_workbook
from exception_store import ExceptionStore, dataset_key as make_dataset_key

logger = get_logger(__name__)

DATA_DIR = "data"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")

_store = None


_db_error = None


def get_store() -> ExceptionStore:
    """Created on first use so importing the server never touches the database.
    If the configured database (VERITY_DB_URL) can't be reached, fall back to the
    local SQLite file and report it via /api/status instead of failing every run."""
    global _store, _db_error
    if _store is None:
        try:
            _store = ExceptionStore()
        except Exception as e:  # driver missing, bad URL, network, wrong password
            _db_error = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
            logger.error("Could not use the configured database (%s); falling back to local SQLite", _db_error)
            os.environ.pop("VERITY_DB_URL", None)
            _store = ExceptionStore()
    return _store


@app.errorhandler(Exception)
def _json_errors(e):
    """API routes always answer in JSON, so the UI shows a real message, not an HTML page."""
    from werkzeug.exceptions import HTTPException
    if request.path.startswith("/api/"):
        if isinstance(e, HTTPException):
            return jsonify({"error": e.description}), e.code
        logger.exception("Unhandled error on %s", request.path)
        return jsonify({"error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"}), 500
    if isinstance(e, HTTPException):
        return e
    raise e


@app.route("/api/export", methods=["POST"])
def export_excel():
    """Excel report built from the results the page already holds, plus saved reviews."""
    from flask import send_file
    body = request.get_json(force=True) or {}
    matched, exceptions = body.get("matched") or [], body.get("exceptions") or []
    kinds = body.get("kinds") or {}
    reviews = get_store().get_reviews(body["dataset_key"]) if body.get("dataset_key") else {}
    data = build_workbook(matched, exceptions, reviews, kind_of=lambda e: kinds.get(str(e.get("payment_id")), ""),
                          meta={"method": body.get("method", "")})
    return send_file(io.BytesIO(data), as_attachment=True, download_name="verity_report.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/review", methods=["POST"])
def save_review():
    body = request.get_json(force=True) or {}
    try:
        rec = get_store().set_review(
            body.get("dataset_key"), body.get("payment_id"), body.get("status"),
            body.get("note", ""), body.get("reviewer", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(rec)


@app.route("/api/review/<dataset_key>/<payment_id>/history")
def review_history(dataset_key, payment_id):
    return jsonify(get_store().history(dataset_key, payment_id))


def _decision_to_dict(d):
    return {
        "payment_id": d.payment_id,
        "settlement_ref": d.settlement_ref,
        "status": d.status,
        "confidence": d.confidence,
        "reason": d.reason,
        "amount_a": d.amount_a,
        "amount_b": d.amount_b,
        "date_a": d.date_a,
        "date_b": d.date_b,
        "flags": list(getattr(d, "flags", [])),
    }


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


SAMPLE_FILES = {
    "hard_payment_log.csv", "hard_settlement_file.csv",
}


@app.route("/api/sample/<name>")
def sample_file(name):
    """Serves the bundled hard test set so the UI can load it in one click."""
    if name not in SAMPLE_FILES:
        return jsonify({"error": "Unknown sample file"}), 404
    return send_from_directory(DATA_DIR, name, mimetype="text/csv")


def _best_ground_truth(decisions):
    """Score against whichever labelled set the payment IDs belong to
    (the standard demo set or the hard set); None for unlabelled data."""
    best = None
    for path in (None, os.path.join(DATA_DIR, "ground_truth_hard.csv")):
        m = evaluate_against_ground_truth(decisions, truth_path=path)
        if m and (best is None or m["total_labeled"] > best["total_labeled"]):
            best = m
    return best


@app.route("/api/status")
def status():
    client, model = get_client_and_model()
    store = get_store()
    return jsonify({
        "ai_connected": client is not None,
        "model": model.split("/")[-1] if model else None,
        "db": store.engine.dialect.name,
        "db_error": _db_error,
    })


@app.route("/api/demo-data")
def demo_data():
    """
    Returns a preview of the ACTUAL default dataset that "Run Reconciliation"
    processes with zero uploads — the multi-source scenario (real Razorpay
    schema + bank statement), not the older 2-file tolerance-matching demo.
    Previously this endpoint was hardcoded to payment_log.csv/settlement_file.csv,
    which meant the preview shown here never matched what actually ran —
    genuinely confusing, since the numbers a user saw here (fake TXN IDs)
    had nothing to do with the results they'd see after clicking Run.
    """
    required = [
        "sample_internal_orders.csv",
        "sample_razorpay_real_schema_settlement.csv",
        "sample_razorpay_bank_statement.csv",
    ]
    if not all(os.path.exists(os.path.join(DATA_DIR, f)) for f in required):
        return jsonify({"error": "Default multi-source demo data not found. Run `python src/generate_multi_source_data.py` first."}), 404

    payments = pd.read_csv(os.path.join(DATA_DIR, "sample_internal_orders.csv"))
    settlements = pd.read_csv(os.path.join(DATA_DIR, "sample_razorpay_real_schema_settlement.csv"))
    bank = pd.read_csv(os.path.join(DATA_DIR, "sample_razorpay_bank_statement.csv"))
    return jsonify({
        "payment_preview": payments.head(5).to_dict(orient="records"),
        "payment_columns": list(payments.columns),
        "payment_count": len(payments),
        "settlement_preview": settlements.head(5).to_dict(orient="records"),
        "settlement_columns": list(settlements.columns),
        "settlement_count": len(settlements),
        "bank_preview": bank.head(5).to_dict(orient="records"),
        "bank_columns": list(bank.columns),
        "bank_count": len(bank),
    })


@app.route("/api/reconcile", methods=["POST"])
def run_reconcile():
    """
    Accepts either:
      - multipart form with 'payment_file' and 'settlement_file' uploads, OR
      - no files at all -> uses the built-in demo dataset
    Runs the full pipeline and returns everything the frontend needs to render.
    """
    using_uploaded = "payment_file" in request.files and "settlement_file" in request.files
    using_uploaded = bool(using_uploaded and request.files["payment_file"].filename and request.files["settlement_file"].filename)

    try:
        if using_uploaded:
            payments_raw = pd.read_csv(request.files["payment_file"])
            settlements_raw = pd.read_csv(request.files["settlement_file"])
        else:
            if not os.path.exists(os.path.join(DATA_DIR, "payment_log.csv")):
                return jsonify({"error": "Demo data not found. Run `python src/generate_data.py` first."}), 404
            payments_raw = pd.read_csv(os.path.join(DATA_DIR, "payment_log.csv"))
            settlements_raw = pd.read_csv(os.path.join(DATA_DIR, "settlement_file.csv"))
    except Exception as e:
        return jsonify({"error": f"Could not read one of the files: {e}"}), 400

    try:
        payment_mapping = map_schema(payments_raw, "payment_log")
        settlement_mapping = map_schema(settlements_raw, "settlement_file")
        payments = apply_mapping(payments_raw, payment_mapping)
        settlements = apply_mapping(settlements_raw, settlement_mapping)
    except ValueError as e:
        return jsonify({"error": f"Could not confidently map columns: {e}", "type": "schema_error"}), 422

    result = reconcile(payments, settlements)
    explanations = explain_all_exceptions(result["exceptions"])
    write_audit_log(result["decisions"], explanations)

    gt_metrics = None
    if not using_uploaded and has_ground_truth():
        gt_metrics = evaluate_against_ground_truth(result["decisions"])

    client, model = get_client_and_model()

    return jsonify({
        "using_uploaded_data": using_uploaded,
        "payment_mapping": payment_mapping,
        "settlement_mapping": settlement_mapping,
        "match_rate": result["match_rate"],
        "throughput_per_sec": result["throughput_per_sec"],
        "elapsed_seconds": result["elapsed_seconds"],
        "matched": [_decision_to_dict(d) for d in result["matched"]],
        "exceptions": [
            {**_decision_to_dict(d), "explanation": explanations.get(d.payment_id, "")}
            for d in result["exceptions"]
        ],
        "gt_metrics": gt_metrics,
        "ai_connected": client is not None,
        "model": model.split("/")[-1] if model else None,
    })


@app.route("/api/ask", methods=["POST"])
def ask():
    body = request.get_json(force=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    log = load_audit_log()
    query_lower = query.lower()
    relevant = [r for r in log if r["payment_id"].lower() in query_lower] or [
        r for r in log if r["status"] == "exception"
    ]
    context = json.dumps(relevant[:15], indent=2)

    client, model = get_client_and_model()
    if client is None:
        return jsonify({"answer": None, "raw_entries": relevant[:5], "ai_connected": False})

    prompt = f"""You are answering a question about a reconciliation audit log.
Audit log entries (JSON): {context}

Question: {query}

Answer directly and concisely, citing specific payment IDs where relevant."""
    try:
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], temperature=0
        )
        return jsonify({"answer": resp.choices[0].message.content.strip(), "ai_connected": True})
    except Exception as e:
        return jsonify({"error": str(e), "raw_entries": relevant[:5]}), 502


@app.route("/api/auto-reconcile", methods=["POST"])
def auto_reconcile_endpoint():
    """
    THE UNIFIED ENTRY POINT. Accepts two REQUIRED files (any shape — CSV,
    any company's export format) and one OPTIONAL third file (a real bank
    statement). Automatically decides, by inspecting the actual columns,
    whether to run exact fee-based verification or tolerance-based
    matching — and if a bank statement is provided alongside exact
    verification, chains a second, genuine multi-source check: does the
    settlement batch total actually match what hit the bank. See
    src/auto_reconciler.py — nothing here is hardcoded to any company's schema.
    """
    using_uploaded = "payment_file" in request.files and "settlement_file" in request.files
    using_uploaded = bool(using_uploaded and request.files["payment_file"].filename and request.files["settlement_file"].filename)

    bank_provided = "bank_file" in request.files and request.files["bank_file"].filename

    try:
        if using_uploaded:
            payments_raw = pd.read_csv(request.files["payment_file"])
            settlements_raw = pd.read_csv(request.files["settlement_file"])
            bank_raw = pd.read_csv(request.files["bank_file"]) if bank_provided else None
        else:
            # DEFAULT, ZERO-UPLOAD EXPERIENCE: the multi-source scenario.
            # "Multi-source reconciliation" is one of only four example
            # directions named explicitly on the buildathon page — this is
            # the headline story every visitor sees first, not something
            # they'd only discover by knowing to upload a third file.
            # Real Razorpay field names (see README §6b), with 3 deliberately
            # planted issues so the demo proves something rather than showing
            # a suspiciously perfect run. Regenerate anytime with:
            #   python src/generate_multi_source_data.py
            required = [
                "sample_internal_orders.csv",
                "sample_razorpay_real_schema_settlement.csv",
                "sample_razorpay_bank_statement.csv",
            ]
            if not all(os.path.exists(os.path.join(DATA_DIR, f)) for f in required):
                return jsonify({
                    "error": "Default multi-source demo data not found. "
                             "Run `python src/generate_multi_source_data.py` first."
                }), 404
            payments_raw = pd.read_csv(os.path.join(DATA_DIR, "sample_internal_orders.csv"))
            settlements_raw = pd.read_csv(os.path.join(DATA_DIR, "sample_razorpay_real_schema_settlement.csv"))
            bank_raw = pd.read_csv(os.path.join(DATA_DIR, "sample_razorpay_bank_statement.csv"))
    except Exception as e:
        return jsonify({"error": f"Could not read one of the files: {e}"}), 400

    try:
        outcome = run_auto_reconciliation(payments_raw, settlements_raw, bank_df=bank_raw)
    except ValueError as e:
        return jsonify({"error": str(e), "type": "schema_error"}), 422
    except Exception as e:
        return jsonify({"error": f"Reconciliation failed: {e}"}), 400

    decision = outcome["decision"]
    client, model = get_client_and_model()

    if decision["strategy"] == "exact":
        result = outcome["exact_result"]
        batch_result = outcome.get("batch_result")

        # Generate real, distinct AI explanations for exact-verification
        # exceptions too — without this, "explanation" was just a silent
        # copy of the raw computed "reason", which isn't genuinely
        # "explaining" anything the math didn't already say.
        explanations = explain_all_exact_exceptions(result["orders"])
        orders_with_explanations = [
            {**o, "explanation": explanations.get(o["order_id"], o["reason"])}
            for o in result["orders"]
        ]
        write_audit_log_exact(orders_with_explanations)

        dkey = make_dataset_key(payments_raw)
        response = {
            "dataset_key": dkey,
            "reviews": get_store().get_reviews(dkey),
            "strategy": "exact",
            "decision_reason": decision["reason"],
            "using_uploaded_data": using_uploaded,
            "total_orders": result["total"],
            "status_counts": result["status_counts"],
            "orders": orders_with_explanations,
            "elapsed_seconds": result["elapsed_seconds"],
            "throughput_per_sec": result["throughput_per_sec"],
            "ai_connected": client is not None,
            "model": model.split("/")[-1] if model else None,
            "gt_metrics_exact": evaluate_exact_against_ground_truth(result["orders"]),
        }
        if bank_provided and batch_result is None:
            response["batch_note"] = (
                "A bank statement was provided, but no settlement/batch ID column or "
                "usable bank columns (a narration/description + a credited amount) "
                "could be identified — batch-level verification was skipped."
            )
        elif batch_result is not None:
            response["batches"] = batch_result["batches"]
            response["unrelated_bank_lines"] = batch_result["unrelated_bank_lines"]
        return jsonify(response)

    # tolerance strategy — reshape to match the existing /api/reconcile
    # contract so the frontend's existing renderResults() keeps working
    result = outcome["tolerance_result"]
    explanations = explain_all_exceptions(result["exceptions"])
    write_audit_log(result["decisions"], explanations)

    # Always attempt ground-truth evaluation (not gated on using_uploaded) —
    # evaluate_against_ground_truth() safely returns None if the payment IDs
    # genuinely don't correspond to the synthetic ground truth data. This
    # matters because the multi-source scenario is now the default, so the
    # ONLY way to see the tolerance engine's accuracy validation is by
    # uploading payment_log.csv + settlement_file.csv — that upload must not
    # be penalized just for being an upload.
    gt_metrics = _best_ground_truth(result["decisions"])

    dkey = make_dataset_key(payments_raw)
    return jsonify({
        "dataset_key": dkey,
        "reviews": get_store().get_reviews(dkey),
        "strategy": "tolerance",
        "decision_reason": decision["reason"],
        "using_uploaded_data": using_uploaded,
        "match_rate": result["match_rate"],
        "throughput_per_sec": result["throughput_per_sec"],
        "elapsed_seconds": result["elapsed_seconds"],
        "matched": [_decision_to_dict(d) for d in result["matched"]],
        "exceptions": [
            {**_decision_to_dict(d), "explanation": explanations.get(d.payment_id, "")}
            for d in result["exceptions"]
        ],
        "gt_metrics": gt_metrics,
        "ai_connected": client is not None,
        "model": model.split("/")[-1] if model else None,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG", "false").lower() == "true")