# Verity — AI Reconciliation Agent
### *"Match. Flag. Explain."*
### Razorpay Buildathon 2026 — Track 4: AI Finance Controller

Built by Deeksha · MITE Mangalore · targeting the AI Builder Intern role at Razorpay

---

## 1. Which track, and what it actually is

**Track: AI Finance Controller.**

Every time a customer pays through Razorpay, two independent records get created:
1. Razorpay's own internal payment log ("we processed this payment")
2. The bank / payment partner's settlement file ("we sent you this money")

These two records are supposed to match — same amount, same transaction, roughly the
same time. In practice they often don't line up exactly: fees get deducted, settlement
is delayed by a few days, reference numbers are formatted differently across systems,
sometimes an entry is simply missing. Someone has to go through and check: **do these
two records actually represent the same transaction?**

This checking process is called **reconciliation**. Today it is done largely by humans
in spreadsheets. It is slow, repetitive, and error-prone — and it is one of the biggest
hidden costs in any payments company.

## 2. The problem I'm solving

**Problem statement:** Build an AI agent that automatically reconciles transactions
between two independent, differently-formatted data sources, flags anything it can't
confidently match, explains why in plain English, and lets a human ask it questions
about its decisions in natural language.

**Why this needs solving (not just "nice to have"):**
- Reconciliation is manual, repetitive, and doesn't scale — as transaction volume grows,
  the human effort grows linearly with it.
- Every existing reconciliation tool needs to be manually configured for each new data
  source's column format. This is a genuine, unsolved pain point — funded startups in
  this exact space (End Close, Zomma) explicitly sell "no integration project needed"
  as their core value proposition.
- Mistakes are expensive. Force-matching two records that aren't actually the same
  transaction hides real discrepancies — sometimes fraud, sometimes lost revenue.
- Razorpay's own framing of this track states plainly: *"verification capacity, not
  generation speed, is the bottleneck"* — this is the exact problem this project
  targets.

## 3. How I'm solving it (approach)

A five-layer pipeline:

1. **Schema-agnostic ingestion (the differentiator).** Instead of hardcoding which
   column means "amount" or "date," the agent looks at any unfamiliar CSV's column
   names and sample values and figures out the mapping itself — via an LLM call, with
   a keyword-based heuristic fallback so it still works with zero API key. Provable
   live: upload any two CSVs with any column names in the dashboard and watch it work.
2. **Rule-based + confidence-scored matching.** A continuous 0–100 confidence score
   (not fixed buckets) blending amount/date fit with reference-code similarity —
   so two unrelated transactions with coincidentally near-identical amounts don't
   get swapped just because one scored marginally higher on amount/date alone.
   The reference-similarity signal is only used when the data actually carries a
   genuine cross-reference field; otherwise it safely falls back to amount/date only.
3. **Duplicate-settlement detection.** If two or more unclaimed settlement records
   are near-identical copies of each other for the same payment, that's flagged as
   an exception too — force-picking one would hide a real duplicate-payment risk.
4. **Honest exception handling.** Anything below the confidence threshold is NOT
   force-matched. It's logged with a plain-English reason, written by an LLM reading
   the two records side by side (falls back to a template if no API key is set).
5. **Natural-language query interface.** Ask *"why didn't TXN045 match?"* and the
   agent answers directly from its own audit log.

Every decision — matched or flagged — is logged with its reasoning. On top of that,
because the demo dataset is synthetic with known-correct labels, the dashboard also
reports **real accuracy/precision/recall** validated against ground truth — not just
a self-reported match rate (see the Accuracy tab, and `src/evaluate.py`).

## 4. Tech stack

| Layer | Tool | Why |
|---|---|---|
| Data handling | Python, Pandas | Cleaning and joining two messy tabular sources |
| Data simulation | Python, with ground-truth labels kept alongside | Realistic fake data with intentional mismatches, and known-correct answers for accuracy validation |
| Schema mapping (differentiator) | Groq (Llama, OpenAI-compatible) or OpenAI + heuristic fallback | Reads column headers/samples, proposes canonical field mapping |
| Matching engine | Rule-based logic + `rapidfuzz` for fuzzy string matching | Deterministic, explainable, fast — no black box for the core decision |
| Exception explanations | LLM API call | Turns a raw mismatch into a plain-English reason a human can act on |
| Accuracy validation | `src/evaluate.py` against synthetic ground truth | Answers "were the matches actually correct," not just "what % matched" |
| Audit trail | JSON Lines log file | Simple, inspectable, versionable |
| Dashboard | Streamlit, with file upload + tabs + charts | Live differentiator demo, organized results, visual proof |
| NL query box | LLM API call over the audit log | Lets the panel interact with the system live |

**Note on the LLM calls:** every LLM-dependent piece has a heuristic/template
fallback, so the whole pipeline runs and demos correctly even without an API key —
you just get better, more natural explanations when one is connected. **Groq is
recommended** (free, no card required, very fast — good for live demos); OpenAI is
supported as an alternative. See `.env.example`.

## 5. Project structure

```
reconciliation-agent/
├── README.md                    <- this file
├── requirements.txt
├── Procfile                      <- for Render/Railway deployment of the Flask app
├── .env.example                  <- API key + config template (copy to .env)
├── .gitignore                    <- keeps .env and junk out of git
├── .streamlit/config.toml        <- theme config (only used by the Streamlit version)
├── data/                         <- generated CSVs + ground truth land here
├── logs/                         <- app.log (operational) + audit_trail.jsonl (decisions)
├── src/
│   ├── config.py                  <- all tunable thresholds, documented, in one place
│   ├── logger.py                  <- shared logging setup (console + file)
│   ├── llm_client.py               <- picks Groq or OpenAI automatically from env vars
│   ├── generate_data.py            <- simulates data + writes ground truth labels
│   ├── schema_mapper.py            <- Layer 1: schema-agnostic column detection
│   ├── matcher.py                  <- Layers 2+3: matching, scoring, duplicate detection
│   ├── explainer.py                <- LLM plain-English exception explanations
│   ├── evaluate.py                 <- ground-truth accuracy/precision/recall validation
│   ├── auto_reconciler.py          <- unified strategy picker + multi-source batch verification
│   ├── three_way_reconciler.py     <- exact-verification engine for a 3-sheet Excel workbook
│   ├── data_loaders.py             <- CSV/Excel/SQL loaders, all feeding the same logic
│   └── audit_log.py                <- audit trail read/write
├── tests/
│   ├── test_reconciliation.py      <- 18 tests: tolerance-matching engine
│   ├── test_auto_reconciler.py     <- 18 tests: strategy selection + multi-source batches
│                                       + ground-truth validation for exact verification
│   ├── test_explainer_exact.py     <- 4 tests: exact-engine explanations are genuinely
│                                       distinct from raw reasons, not a silent duplicate
│   ├── test_three_way_reconciler.py <- 9 tests: exact-verification engine (3-sheet Excel)
│   └── test_data_loaders.py        <- 3 tests: CSV/Excel/SQL all feed the same engine
├── web/                          <- custom HTML/CSS/JS frontend (primary — pixel-accurate design)
│   ├── index.html
│   ├── styles.css
│   └── script.js
├── server.py                     <- Flask backend serving web/ + the reconciliation API (primary)
└── app.py                        <- Streamlit dashboard (secondary — faster to deploy, some
                                      design limitations vs. the Flask/HTML version)
```

**Two frontends, same backend logic (`src/`), pick one:**
- **`server.py` + `web/`** — the primary version, pixel-accurate to the approved design,
  fully custom HTML/CSS/JS. Run: `python server.py`, open `http://localhost:5000`.
- **`app.py`** — the Streamlit version, faster to deploy (Streamlit Community Cloud is a
  few clicks), but Streamlit's own component styling puts a ceiling on visual customization.
  Run: `streamlit run app.py`.

## 5a. Production-readiness layer (why this isn't "just a demo script")

A panel evaluating for a real internship isn't only checking "does it work once" —
they're checking "would I trust this code in a real PR." This project has that layer
built in deliberately:

| Concern | How it's handled |
|---|---|
| **Config management** | All thresholds live in `src/config.py`, documented, overridable via environment variables — never hardcoded inside logic files. |
| **Logging** | `src/logger.py` gives every module proper leveled logging (INFO/WARNING/ERROR) written to both console and `logs/app.log` — no `print()` debugging left in. |
| **Input validation** | `matcher.py` and `schema_mapper.py` fail fast with a clear `ValueError` message on empty, malformed, or missing-column input. |
| **Graceful degradation** | Every LLM call has a heuristic/template fallback — a missing or failing API key never breaks the pipeline. |
| **Tests** | 52 tests across five files — `test_reconciliation.py` (18, tolerance-based engine), `test_auto_reconciler.py` (18, strategy selection + multi-source batch verification + ground-truth validation for exact verification), `test_three_way_reconciler.py` (9, exact-verification engine, full end-to-end run against the real sample workbook), `test_data_loaders.py` (3, proving CSV/Excel/SQL all feed the same logic), `test_explainer_exact.py` (4, exact-engine explanations are genuinely distinct from raw reasons). Several exist specifically because they caught real bugs during development. Run with `pytest tests/ -v`. |
| **Secrets handling** | `.env.example` documents required/optional variables; `.gitignore` keeps `.env` out of version control; no key is ever hardcoded. |
| **Accuracy validation** | Not just a self-reported match rate — `evaluate.py` checks decisions against known-correct synthetic labels, reporting real precision/recall. |

Mention this layer explicitly in your pitch — it's what signals "I understand
production concerns," not just "I can make a demo work."

## 6. Step-by-step build & run instructions

1. `pip install -r requirements.txt`
2. (Optional but recommended) copy `.env.example` to a new file named `.env`
   and paste your Groq API key into it (get one free at console.groq.com —
   see `.env.example` for details). Both frontends auto-load `.env` on startup,
   so this only needs to be done once. `.env` is already in `.gitignore` so
   it's never accidentally committed.
3. Generate the simulated data:
   - `python src/generate_data.py` → creates `data/payment_log.csv`,
     `data/settlement_file.csv` (different column names on purpose, to prove
     the schema-agnostic mapper works), and `data/ground_truth.csv`
     (known-correct labels, for accuracy validation).
   - `python src/generate_multi_source_data.py` → creates the **default,
     zero-upload demo**: `data/sample_internal_orders.csv`,
     `data/sample_razorpay_real_schema_settlement.csv`, and
     `data/sample_razorpay_bank_statement.csv` — real Razorpay field names,
     with 3 deliberately planted issues (1 math error, 1 unmatched
     settlement, 1 batch-vs-bank mismatch) so clicking "Run Reconciliation"
     with zero uploads immediately proves multi-source verification works.
4. (Optional but recommended before your demo) run the tests to confirm everything
   works: `pytest tests/ -v` — should show 52 passed.
5. **Run the dashboard — pick one:**
   - Primary (custom design): `python server.py` → open `http://localhost:5000`
   - Secondary (Streamlit, faster to deploy): `streamlit run app.py`
6. Click **"Run Reconciliation"** to see it work on the demo dataset, or upload two
   of your own CSVs first (any column names) to prove the schema mapping live —
   `data/sample_unfamiliar_payments.csv` and `data/sample_unfamiliar_settlements.csv`
   are provided specifically for this: completely different columns, never
   hardcoded anywhere in the app.
7. Check the **Accuracy panel** — real precision/recall against known-correct labels,
   not just a self-reported match rate (demo dataset only).
8. Try **Ask Verity**: *"why didn't TXN00042 match?"* or *"how many were
   amount mismatches?"*

## 6a. The second engine: exact verification (three_way_reconciler.py)

`matcher.py` answers *"do these two records probably describe the same
transaction?"* using tolerance-based scoring — the right tool when you only
have two files with no fee breakdown. But it's still an approximation, not
exact math.

`three_way_reconciler.py` is a genuinely different, stricter engine for when
a fee-breakdown source is available (a real payment gateway report). It
verifies **Gross − MDR Fee − Tax = Net, to the rupee** — real arithmetic, not
a percentage guess — against a three-sheet model: Internal Sales, Gateway
Report, Bank Statement. It correctly classifies, in priority order:

1. **Pending settlement** — money hasn't moved yet; never treated as a failure
2. **Prior-period deduction** — a refund/chargeback with no current-period
   sales record; explained, not flagged as "missing"
3. **Math error** — the fee math genuinely doesn't add up; a real exception
4. **Unmatched to sales** — settled an order our own system never recorded
5. **Verified** — math checks out AND the fee rate matches what's expected
   for that payment method (e.g. a premium card network's higher MDR is
   *recognized as normal*, not falsely flagged just for being higher)

It also verifies settlement **batches** against actual bank deposits, and
correctly excludes unrelated bank activity (rent, cloud bills) instead of
treating it as a missing settlement.

This engine's logic (order-level exact math + batch-level bank verification)
is fully tested (`tests/test_three_way_reconciler.py`) and works standalone
against a real 3-sheet Excel workbook (`data/three_way_sample.xlsx`) — but
it is **not** exposed as its own separate UI page. That capability now lives
inside the single unified flow instead — see §6b below, "True multi-source
reconciliation" — so the user never has to pick between engines or navigate
to a different tab for a 3-source scenario.

## 6b. The unified auto-detection layer (auto_reconciler.py)

Having two engines is only useful if the system picks the right one itself —
a real tool doesn't ask the user "do you want exact or fuzzy matching?" It
looks at the data and decides:

> *Do these two sources share a real, overlapping ID column, AND does one of
> them carry a genuine fee breakdown (gross/fee/net)? If yes — verify
> exactly. If no — match by tolerance.*

This is the single entry point (`/api/auto-reconcile`) the dashboard's
"Run Reconciliation" button actually calls now — the two engines still exist
underneath, but the user never has to choose between them manually.

**Validated against two real, independently-sourced company schemas** (not
just the demo data this project generated), to prove the detection logic
genuinely generalizes rather than being tuned to one shape:

| | Razorpay's settlement recon report | Stripe's balance transaction report |
|---|---|---|
| Source | Razorpay's own SDK docs (`razorpay-node`) | Stripe's own reporting docs |
| ID columns | Two — `entity_id` AND `order_id` (only one is the correct join key) | One — `id` |
| Tax reported separately? | Yes (`tax`) | No — `fee` is inclusive |
| Fee column names | `fee`, `tax`, `amount`, `credit` | `fee`, `net`, `amount` |

The detector correctly identified the **exact** strategy for both — including
correctly picking `order_id` over `entity_id` as the real join key for
Razorpay's data, by testing which column *actually* overlaps with the
reference source instead of trusting a single naming-based guess. Sample
files: `data/sample_razorpay_real_schema_settlement.csv` and
`data/sample_stripe_real_schema_balance.csv`, with matching internal-records
files. Tests: `tests/test_auto_reconciler.py` (18 tests, including 5 for
multi-source batch verification).

**Two real bugs were found and fixed while building this against authentic
schemas** — good material to mention if asked how you validated correctness:
1. A column named `billed_cents` wasn't recognized as an amount field —
   the keyword heuristic only knew common names like "amount"/"paid". Fixed
   by broadening the keyword list.
2. Real gateway timestamps (`created_at: 1754006400`) are Unix epoch
   **seconds** — pandas' default parser reads a raw integer as epoch
   **nanoseconds**, silently producing a date in 1970 and a false
   ~20,000-day gap. Fixed with explicit epoch-range detection in
   `schema_mapper.apply_mapping()`.

### True multi-source reconciliation (the third file: a real bank statement)

This directly answers the track's example direction "Multi-source
reconciliation" — with real chained logic, not just a bigger single algorithm.

**The production-realistic insight:** multi-source reconciliation isn't one
bigger three-way comparison — it's **two pairwise checks chained together**,
at different levels of granularity, which is how real finance-ops actually
works:

1. **Order-level** (source A vs. source B): does each individual
   transaction's math check out? *(the exact-verification engine above)*
2. **Batch-level** (source B vs. source C): real gateways group many
   transactions into one lump-sum payout — `settlement_id` (Razorpay),
   `automatic_payout_id` (Stripe). A bank statement only ever shows that
   lump sum, never the individual transaction amounts. So the second check
   is: *did the promised batch total actually arrive in the bank?*

**This is now the default, zero-upload experience** (`python src/generate_multi_source_data.py` builds it) — clicking "Run Reconciliation" with nothing uploaded immediately demonstrates multi-source verification, not a 2-file fallback. Column names don't need to match anything (a free-text narration column + a credited-amount column are auto-detected, the same way the other schema-agnostic detection works). Uploading your own third file works the same way, replacing the default.

**What it correctly does, verified with real tests (`tests/test_auto_reconciler.py`):**
- Groups the fee-breakdown source by its real settlement/payout ID
- Sums each batch and checks it against the actual bank deposit for that batch
- Flags a genuine ₹-level mismatch as an exception, not a guess
- **Correctly excludes unrelated bank activity** (rent, a cloud bill) instead
  of treating every unmatched bank line as a missing settlement — a bank
  statement always has non-settlement noise mixed in, and a naive checker
  would falsely flag all of it
- Gracefully does nothing (not crash) if the third file isn't a usable bank
  statement, or if tolerance matching (no fee structure) was chosen instead —
  a bank statement only makes sense to reconcile against a real settlement
  concept, and the system says so honestly rather than pretending

## 6c. Making "Match. Flag. Explain." genuinely true on both engines

Three real gaps were found by actually using the app and questioning the
output, not just trusting it — worth documenting since finding these is as
important as building the feature in the first place:

**1. The exception filter tabs (All/Amount/Duplicate/Delayed) silently
broke for exact-verification exceptions.** The filter matched on the literal
word "mismatch" in the reason text — language the *tolerance* engine uses
("Amount mismatch of 8%"), but the *exact* engine never says (it says
"shortfall of ₹42.00" instead). Clicking "Amount" showed zero results even
though math errors are obviously amount-related. Fixed by categorizing on
the actual `status` field (`math_error`, `unmatched_to_reference`, etc.)
instead of parsing English text, which is inherently fragile.

**2. "Explanation" was silently just a copy of "Reason" for every
exact-verification exception.** The pipeline status showed "Explainer:
ONLINE" as if AI was contributing, but the exact-verification path never
actually called it — there was nothing wired up to generate a distinct
explanation. Fixed by adding `explain_all_exact_exceptions()` to
`explainer.py`: a genuine second explainer, with its own prompt and its own
template fallback, so "Explain" is now actually true on both engines, not
just the tolerance one.

**3. The Accuracy page only explained *why* there's no ground truth for
exact verification — it never showed a number.** That's a defensible
position (the math is deterministic, not a guess), but it left the "one
cherry-picked match proves nothing" concern only half-answered. Since the
multi-source demo data has deliberately planted issues with known-correct
answers, the true status of every order is knowable in advance — so
`generate_multi_source_data.py` now also writes `data/ground_truth_multi_source.csv`,
and `evaluate_exact_against_ground_truth()` measures the engine's actual
classifications against it. Result on the current demo data: **100% accuracy,
57/57 correct** — a real, checked number, not just an argument for why one
isn't needed.

## 6d. What data sources this actually supports

- **CSV** — native, via `matcher.py` + `schema_mapper.py` (the original engine)
- **Excel (.xlsx)** — native, both for the 2-file flow and the 3-way engine
- **SQL** — `src/data_loaders.py` has a working `load_sql_query()` that
  connects via SQLAlchemy to any supported database (Postgres, MySQL,
  SQLite, SQL Server) and hands the result straight to the same
  reconciliation logic — proven with a real test against an actual SQLite
  database (`tests/test_data_loaders.py`). **Not wired into the web UI** —
  a connection-string input needs real credential-handling care, which
  deserved more time than the deadline allowed — but the reconciliation
  logic itself needs zero changes to accept SQL-sourced data.

## 7. What "winning" looks like for this build

- **Match rate reported honestly** (don't inflate it — a credible 80–90% beats a
  suspicious 100%), backed by real accuracy/precision/recall against ground truth
- **At least one exception the agent correctly refused to auto-match** — show this
  deliberately in your pitch video, and explain *why* refusing was the right call
- **Schema mapping demoed on a genuinely unfamiliar column format** live, via the
  upload feature — not just pre-baked demo data
- **Clean audit trail** — every decision traceable
- **README limitations section** — state plainly what this doesn't yet handle (see
  below)

## 7a. Known limitations (say these out loud — it reads as maturity, not weakness)

- **No multi-currency / forex support** — international transactions would need
  currency + exchange-rate normalization before comparing amounts.
- **No refund/chargeback handling in the tolerance-matching engine** — a
  payment refunded before settlement won't appear in the settlement file
  the way that engine expects; it would be flagged generically rather than
  correctly explained as "refunded, not missing." (The exact-verification
  engine *does* handle this correctly — see prior-period deduction above —
  but only when a fee-breakdown source is available.)
- **Multi-source batch verification requires a genuine settlement/batch ID
  and fee-breakdown structure to already be present** — it can't invent a
  batch concept where the data provides none; in that case the system
  correctly falls back to 2-source tolerance matching and says so honestly.
- **Not scale-tested** — the matching loop is O(n×m) candidate comparisons; fine
  for hundreds of records, would need indexing/batching for millions.
- **Thresholds are hand-picked, not tuned on real historical data.**

## 8. 10-day build plan

| Day | Focus |
|---|---|
| 1–2 | Data simulation + finalize scope |
| 3–4 | Core rule-based matching engine |
| 5–6 | Schema-agnostic mapping (LLM layer) — the differentiator, give it real time |
| 7 | Audit trail + Streamlit dashboard + NL query box |
| 8 | **Production hardening**: pull thresholds into `config.py`, add logging, add input validation, write the pytest suite (7+ tests), add `.env.example` |
| 9 | Full pipeline test, compute honest metrics, find your "good failure" demo case, polish repo + limitations section |
| 10 | Record a tight, metric-first pitch video |

> Day 8 is the difference between "a script that worked in my demo" and "code a real
> team would trust in a PR" — don't skip it even under time pressure.

## 9. Pitch video structure (5 minutes)

1. **Open with the number** (10s): "My agent reconciled X records with a Y% honest
   match rate, and correctly flagged Z exceptions instead of guessing."
2. **The problem** (30s): why reconciliation is a real, expensive, human bottleneck.
3. **Live demo** (2.5 min): run it on data live, show the schema mapping working on an
   unfamiliar format, show the NL query box answering a real question.
4. **The one deliberate failure** (45s): show a case it refused to auto-match, and why
   that refusal is the correct, trustworthy behavior.
5. **Limitations + what's next** (45s): be honest about what it doesn't do yet.

## 10. Deploying

### Option A: the custom design (Flask) — Render.com, free tier

1. Push this whole folder to a **public GitHub repo** (required for buildathon
   submission anyway).
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python server.py`
5. Add an environment variable: `GROQ_API_KEY` = your key (Render's dashboard has
   an "Environment" tab for this — same idea as Streamlit Cloud's Secrets).
6. Deploy. Render assigns a port via the `PORT` env var automatically —
   `server.py` already reads `os.environ.get("PORT", 5000")`, so no code change needed.
7. Free-tier Render services sleep after inactivity — hit the URL once yourself
   before your demo to wake it up.

### Option B: Streamlit Community Cloud — free, fastest to set up

1. Push to GitHub (same repo as above).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. Click "New app," pick your repo, branch `main`, and set the main file to `app.py`.
4. Under "Advanced settings" → "Secrets," add your API key:
   ```
   GROQ_API_KEY = "your_key_here"
   ```
5. Deploy. First build takes a minute or two.
6. Same sleep behavior as Render on the free tier — wake it up before demoing.

**Note either way:** the demo dataset and ground truth are generated locally via
`python src/generate_data.py` and committed as CSVs in `data/` — make sure those
files are actually pushed to GitHub (check `.gitignore` isn't excluding `data/`),
or the deployed app will show the "no data found" warning until someone uploads
their own files.

