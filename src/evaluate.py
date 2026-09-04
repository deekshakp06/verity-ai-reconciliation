"""
evaluate.py
-----------
Measures REAL accuracy — not just match rate — by comparing the agent's
decisions against ground truth labels that only exist because the demo
dataset is synthetic (generate_data.py knows exactly which scenario it
created for each record).

This directly answers the buildathon's own bar for Track 4: "measured
accuracy" and "one cherry-picked match proves nothing." Match rate alone
only says what % the agent auto-matched — it says nothing about whether
those matches were actually correct. This module closes that gap.

IMPORTANT: this evaluation is only possible because the demo data is
synthetic with known-correct labels. On a real/uploaded dataset there is no
ground truth to check against — accuracy validation is a demo-data-only
feature, and the UI is honest about that distinction.
"""

import os
import pandas as pd
from logger import get_logger

logger = get_logger(__name__)

GROUND_TRUTH_PATH = os.path.join("data", "ground_truth.csv")
GROUND_TRUTH_MULTI_SOURCE_PATH = os.path.join("data", "ground_truth_multi_source.csv")


def has_ground_truth() -> bool:
    return os.path.exists(GROUND_TRUTH_PATH)


def has_multi_source_ground_truth() -> bool:
    return os.path.exists(GROUND_TRUTH_MULTI_SOURCE_PATH)


def evaluate_against_ground_truth(decisions) -> dict | None:
    """
    decisions: list of matcher.Decision objects from reconcile()

    Returns a dict of precision/recall/accuracy metrics, or None if no
    ground truth file is available (e.g. the user uploaded their own data).
    """
    if not has_ground_truth():
        return None

    truth = pd.read_csv(GROUND_TRUTH_PATH).set_index("txn_id")

    true_positive = 0   # correctly auto-matched a genuinely clean transaction
    false_positive = 0  # auto-matched something that should have been flagged
    true_negative = 0   # correctly flagged a genuinely problematic transaction
    false_negative = 0  # flagged something that should have auto-matched
    unlabeled = 0        # decision for a payment_id not in ground truth (e.g. uploaded data)

    per_scenario = {}

    for d in decisions:
        if d.payment_id not in truth.index:
            unlabeled += 1
            continue

        row = truth.loc[d.payment_id]
        should_match = bool(row["should_match"])
        scenario = row["scenario"]
        did_match = d.status == "matched"

        per_scenario.setdefault(scenario, {"correct": 0, "total": 0})
        per_scenario[scenario]["total"] += 1

        if should_match and did_match:
            true_positive += 1
            per_scenario[scenario]["correct"] += 1
        elif should_match and not did_match:
            false_negative += 1
        elif not should_match and did_match:
            false_positive += 1
        else:
            true_negative += 1
            per_scenario[scenario]["correct"] += 1

    total_labeled = true_positive + false_positive + true_negative + false_negative
    if total_labeled == 0:
        return None

    accuracy = (true_positive + true_negative) / total_labeled * 100
    precision = (true_positive / (true_positive + false_positive) * 100) if (true_positive + false_positive) else 0.0
    recall = (true_positive / (true_positive + false_negative) * 100) if (true_positive + false_negative) else 0.0

    result = {
        "accuracy": round(accuracy, 2),
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "total_labeled": total_labeled,
        "unlabeled": unlabeled,
        "per_scenario": {
            k: {"correct": v["correct"], "total": v["total"],
                "pct": round(100 * v["correct"] / v["total"], 1) if v["total"] else 0.0}
            for k, v in per_scenario.items()
        },
    }
    logger.info("Ground-truth evaluation: accuracy=%.2f%%, precision=%.2f%%, recall=%.2f%%",
                accuracy, precision, recall)
    return result


def evaluate_exact_against_ground_truth(orders: list[dict]) -> dict | None:
    """
    Validates the exact-verification engine's classifications (verified /
    math_error / unmatched_to_reference / prior_period_deduction) against
    known-correct labels — possible only because generate_multi_source_data.py
    knows exactly which issues it planted. This is the exact-engine
    equivalent of evaluate_against_ground_truth() above: not a claim that
    "the math is trustworthy" in the abstract, but an actual measured
    comparison against known-correct answers, per the buildathon's own
    standard ("one cherry-picked match proves nothing").

    orders: list of order dicts from auto_reconciler.verify_exact()'s result
    Returns None if no ground truth file is available (e.g. real/uploaded data).
    """
    if not has_multi_source_ground_truth():
        return None

    truth = pd.read_csv(GROUND_TRUTH_MULTI_SOURCE_PATH).set_index("order_id")

    correct = 0
    total_labeled = 0
    unlabeled = 0
    per_status = {}
    mistakes = []

    for o in orders:
        order_id = o["order_id"]
        if order_id not in truth.index:
            unlabeled += 1
            continue

        true_status = truth.loc[order_id, "true_status"]
        predicted_status = o["status"]
        total_labeled += 1

        per_status.setdefault(true_status, {"correct": 0, "total": 0})
        per_status[true_status]["total"] += 1

        if predicted_status == true_status:
            correct += 1
            per_status[true_status]["correct"] += 1
        else:
            mistakes.append({"order_id": order_id, "true_status": true_status, "predicted_status": predicted_status})

    if total_labeled == 0:
        return None

    accuracy = round(100 * correct / total_labeled, 2)
    result = {
        "accuracy": accuracy,
        "correct": correct,
        "total_labeled": total_labeled,
        "unlabeled": unlabeled,
        "mistakes": mistakes,
        "per_status": {
            k: {"correct": v["correct"], "total": v["total"],
                "pct": round(100 * v["correct"] / v["total"], 1) if v["total"] else 0.0}
            for k, v in per_status.items()
        },
    }
    logger.info("Exact-engine ground-truth evaluation: accuracy=%.2f%% (%d/%d correct)",
                accuracy, correct, total_labeled)
    return result
