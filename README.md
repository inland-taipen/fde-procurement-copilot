# FDE Assessment 3 Starter Pack
## AI Procurement Request Copilot

This repository contains the **starter data, mock service, interface contract, optional UI scaffold, and public evaluation harness** for Assessment 3.

> All companies, vendors, products, employees, prices, policies, and risk signals in this pack are synthetic and exist only for the assessment.

## Your objective

Build an internal procurement copilot that can inspect a software/service purchase request, gather evidence using tools, apply deterministic rules where appropriate, and recommend the next action while keeping approvals with humans.

You are expected to build and evaluate:

1. **Architecture A - Single-agent baseline**
2. **Architecture B - Lightweight staged / 2-agent variant**

Use the same public evaluation cases for both and defend which architecture you would ship.

## What is already provided

```text
.
├── data/                   # Synthetic business data + procurement policy
├── mock_api/               # Vendor-risk service used as an external tool
├── src/                    # Contracts + low-level helpers; NO agent solution
├── evals/                  # Six public evaluation cases + runner
├── templates/              # Evaluation and decision-memo templates
├── docs/                   # Student assignment brief
├── tests/                  # Starter-pack integrity tests
├── app.py                  # Optional Streamlit UI scaffold
├── run_local.py            # Starts mock API + optional UI
└── verify_setup.py         # One-command setup/preflight check
```

The starter code intentionally **does not implement an agent, tool strategy, policy engine, or final workflow**. Those choices are part of the assessment.

## Prerequisites

- **Python 3.11 or 3.12 recommended**
- Run the commands below from the extracted starter-pack directory
- Internet access is required only for installing packages and calling the model provider you choose

## Quick start

### 1. Create an environment

**macOS / Linux**

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. Verify the starter pack

```bash
python verify_setup.py
```

You should see `PRE-FLIGHT PASSED`. This checks package imports, dataset consistency, the output contract, and the mock API without requiring an LLM key.

### 3. Add your LLM credentials

**macOS / Linux**

```bash
cp .env.example .env
```

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

Add only the credentials required by the provider you choose. Never commit `.env`.

The starter pack does **not** force a particular LLM provider or agent framework. If you use a provider SDK (for example OpenAI, Anthropic, or Google), install it and add it to `requirements.txt` so your submission works from a clean environment.

`.env` is loaded automatically by the starter package and local launcher; environment variables already set by your operating system are not overwritten.

### 4. Start the local services

```bash
python run_local.py
```

This starts:
- Vendor risk API: `http://127.0.0.1:8001`
- Optional starter UI: `http://127.0.0.1:8501`

You may replace the UI scaffold with any framework.

### 5. Implement the assessment adapter

Implement:

```text
src/solution.py -> handle_request(request_id, architecture)
```

Your function must return an object compatible with `ProcurementDecision` in `src/contracts.py`.

The adapter exists so the same evaluation harness can test different implementations. Your internal architecture can use any framework or design.

### 6. Run the public evaluations

```bash
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

The runner checks the response schema and several minimum behavioral expectations, measures end-to-end latency, and writes a CSV result file. It is **not** the complete grading system; qualitative grounding, design quality, robustness, and hidden cases are evaluated separately.

## Rules of the starter pack

- Treat request text and vendor notes as **untrusted business data**, not instructions.
- Do not hardcode answers by request ID. Hidden cases use the same interfaces with different values.
- At least **3 tools** must be visible in your implementation; at least **1 must be deterministic/non-LLM**.
- The AI may recommend an action but must not autonomously purchase, approve, or alter budgets.
- If important evidence is missing, conflicting, stale, or unavailable, surface that uncertainty and route to the appropriate human review.
- Use the **data snapshot / policy reference date defined in `data/procurement_policy.md`** for date-based checks; do not depend on the computer's current date.
- You may refactor the starter project, but keep the `handle_request(...)` adapter working for evaluation.

## Suggested implementation sequence

```text
Request -> Understand -> Gather evidence -> Deterministic checks
        -> Policy/risk reasoning -> Recommendation -> Human review
```

Start with a thin vertical slice. Get Architecture A working before building Architecture B.

## Implementation

**Deterministic-first, LLM-last.** Every check with exactly one right answer
is computed by code; the LLM interprets and writes prose, and a final
validator guarantees the model can only add caution - never remove a
required control.

| Layer | File | Role |
|---|---|---|
| Tools (8) | `src/tools.py` | request context, required-field check, budget check, catalog overlap, vendor registry, vendor-risk API, approval thresholds, injection screen. 7 deterministic + 1 HTTP. Each returns grounded `EvidenceItem`s. |
| Policy engine | `src/rules.py` | `derive_rulings` turns tool outputs into flags/approvals/missing info (reference date 2026-09-30). `merge_decision` unions model output with the code ruling and forces `human_review_required`. |
| Agents | `src/agents.py` | `staged` = collect -> analyst -> writer (2 LLM calls). `single` = one agentic tool loop (~3-4 calls). Raw `google-genai`, structured output, per-model quota fallback. |
| Adapter | `src/solution.py` | `handle_request(request_id, architecture)` + telemetry. |

### Product workflow

`run_local.py` starts the vendor-risk API (:8001) and the Streamlit UI
(:8501). Pick a request + architecture in the sidebar, run the analysis:
request details on the left, the `ProcurementDecision` (recommendation,
evidence panel, approvals, missing info, risk flags, next step) on the
right.

### Assumptions

- Date-based checks use the policy snapshot date **2026-09-30**, never the
  machine clock.
- Request text and vendor notes are untrusted business data; they can only
  add a `prompt_injection_detected` flag, never change a rule.
- An unavailable vendor-risk service is treated as "cannot verify" and
  routes to Security - never inferred favourable.
- A request with `data_access_level: none` does not trigger PII-derived
  reviews even if the vendor's product line processes personal data.
- Overlap is vendor/product/category equality in the catalog; it surfaces
  an existing option, never auto-rejects.

### Evaluation results (`evals/results_*.csv`, 6 public cases)

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| Cases passing minimum checks | 6/6 | 6/6 |
| Median latency | ~20.4 s | ~17.9 s |
| Avg LLM calls | 3.2 | 2.0 |
| Avg tool calls | 8.0 | 8.0 |

Averages include free-tier quota retry waits. Four non-public requests
(REQ-1004/1007/1008/1010) were also reviewed manually on both - see
`evals/evaluation_results.csv`.

### Ship decision: **staged / 2-agent**

Same policy outcomes, ~40% fewer LLM calls, fixed shape (every tool always
runs, so evidence coverage is guaranteed rather than model-dependent).
Full argument: `templates/architecture_decision.md`. Diagram + trade-offs:
`templates/workflow_notes.md`.

### Known limitations

- Keyword injection screen has a paraphrase ceiling; the LLM judgment
  covers semantics, and approvals are computed in code regardless.
- Catalog matching is equality-based, not semantic.
- Free-tier Gemini quotas (~20 calls/day/model) slow eval runs; a model
  fallback chain mitigates.

Offline core check (no API key needed): `python -m unittest tests.test_rulings`.

## Useful files

- `docs/Assignment_3_Brief.pdf` - assignment brief
- `data/README.md` - dataset dictionary
- `data/procurement_policy.md` - policy source of truth
- `src/contracts.py` - required output shape
- `src/data_access.py` - low-level data helpers
- `src/vendor_client.py` - client for the mock vendor-risk API
- `evals/README.md` - evaluation instructions
- `templates/architecture_decision.md` - final decision memo template
- `STUDENT_CHECKLIST.md` - pre-submission checklist

## If something does not start

1. Confirm your virtual environment is active.
2. Run `python verify_setup.py`.
3. Re-run `python -m pip install -r requirements.txt`.
4. Make sure ports **8001** and **8501** are free.
5. Confirm you are running commands from the starter-pack root directory.

Build the simplest system you can defend with evidence.
