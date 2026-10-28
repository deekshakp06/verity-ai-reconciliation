"""Build payments.csv + settlements.csv from real Olist payment amounts.
Amounts, order IDs and dates are real (Olist public dataset).
Settlements are SIMULATED: fee deducted, delays, and injected errors.
Run: python make_real_test.py
"""
import random
import pandas as pd

random.seed(7)
N = 500
FEE = 0.02  # 2% gateway fee, same rate for all rows

pay = pd.read_csv("olist_order_payments_dataset.csv")
orders = pd.read_csv("olist_orders_dataset.csv", parse_dates=["order_purchase_timestamp"])

amt = pay.groupby("order_id", as_index=False)["payment_value"].sum()
df = amt.merge(orders[["order_id", "order_purchase_timestamp"]], on="order_id")
df = df[df.payment_value > 0].sample(N, random_state=7).reset_index(drop=True)

payments, settlements, truth = [], [], []
for i, r in df.iterrows():
    txn = f"TXN{i + 1:05d}"
    gross = round(float(r.payment_value), 2)
    pdate = r.order_purchase_timestamp.normalize()
    ref = "REF-" + r.order_id[:8].upper()
    payments.append((txn, gross, pdate.date(), "Olist", ref))

    net = round(gross * (1 - FEE), 2)
    delay = random.randint(1, 3)
    kind = random.choices(
        ["clean", "amount_mismatch", "ref_typo", "duplicate", "missing", "late"],
        weights=[78, 5, 5, 4, 5, 3])[0]

    s_ref, s_amt, s_date = ref, net, pdate + pd.Timedelta(days=delay)
    if kind == "missing":
        truth.append((txn, kind, False)); continue
    if kind == "amount_mismatch":
        s_amt = round(net * random.uniform(0.90, 0.97), 2)
    if kind == "ref_typo":
        k = random.randrange(4, len(s_ref))
        s_ref = s_ref[:k] + random.choice("XQZ7") + s_ref[k + 1:]
    if kind == "late":
        s_date = pdate + pd.Timedelta(days=random.randint(12, 25))
    settlements.append((s_ref, s_amt, s_date.date(), f"BANK_{random.randint(1, 5)}"))
    if kind == "duplicate":
        settlements.append((s_ref, s_amt, s_date.date(), f"BANK_{random.randint(1, 5)}"))
    truth.append((txn, kind, kind in ("clean", "late")))

pd.DataFrame(payments, columns=["txn_id", "amount_inr", "payment_date", "merchant", "internal_ref"]).to_csv("payments.csv", index=False)
random.shuffle(settlements)
pd.DataFrame(settlements, columns=["reference_no", "settled_amount", "settlement_date", "bank_code"]).to_csv("settlements.csv", index=False)
pd.DataFrame(truth, columns=["txn_id", "scenario", "should_match"]).to_csv("real_test_truth.csv", index=False)
print(f"payments.csv: {len(payments)} rows | settlements.csv: {len(settlements)} rows")