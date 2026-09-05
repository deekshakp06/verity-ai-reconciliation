"""
audit_log.py
------------
Writes every single decision the agent makes (matched or exception) to a
JSON Lines file — one JSON object per line, easy to inspect, append-only,
and easy to feed into the natural-language query box later.

This is the "audit trail" deliverable the buildathon explicitly asks for:
every action should be loggable and explainable.
"""

import json
import os
from datetime import datetime, timezone

LOG_PATH = "logs/audit_trail.jsonl"


def write_audit_log(decisions, explanations=None):
    """
    decisions: list of matcher.Decision objects
    explanations: optional dict {payment_id: llm_generated_explanation}
    """
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    explanations = explanations or {}

    with open(LOG_PATH, "w") as f:
        for d in decisions:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payment_id": d.payment_id,
                "settlement_ref": d.settlement_ref,
                "status": d.status,
                "confidence": d.confidence,
                "reason": d.reason,
                "amount_a": d.amount_a,
                "amount_b": d.amount_b,
                "date_a": d.date_a,
                "date_b": d.date_b,
                "detailed_explanation": explanations.get(d.payment_id, ""),
            }
            f.write(json.dumps(record) + "\n")

    print(f"[audit_log] Wrote {len(decisions)} decisions -> {LOG_PATH}")


def load_audit_log():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def write_audit_log_exact(orders):
    """
    Same idea as write_audit_log() above, but for the exact-verification
    engine's plain-dict order records (order_id, status, reason, gross, fee,
    tax, net, etc.) instead of matcher.Decision objects — the two engines
    have genuinely different record shapes, so this needs its own writer.

    Without this, uploading 3 files (triggering exact verification) never
    wrote anything to the audit log at all — meaning "Ask Verity" always
    read an empty or stale log for that path, answering confidently wrong
    ("no audit-log entries") even though the Exceptions tab correctly
    showed real, freshly-computed results just moments earlier.

    Writes under the same "payment_id" key the tolerance engine uses (set
    here to the order's own order_id) so /api/ask's existing filtering
    logic works identically for both engines without special-casing.
    """
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    with open(LOG_PATH, "w") as f:
        for o in orders:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payment_id": o["order_id"],
                "settlement_ref": o.get("order_id"),
                "status": o["status"],
                "confidence": 100 if o["status"] == "verified" else None,
                "reason": o["reason"],
                "amount_a": o.get("gross"),
                "amount_b": o.get("net"),
                "date_a": o.get("date_a"),
                "date_b": o.get("date_b"),
                "detailed_explanation": o.get("explanation", ""),
            }
            f.write(json.dumps(record) + "\n")

    print(f"[audit_log] Wrote {len(orders)} exact-verification orders -> {LOG_PATH}")