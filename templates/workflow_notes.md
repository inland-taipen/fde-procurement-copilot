# Workflow / Architecture Notes

## Design principle

**Deterministic-first, LLM-last.** Every check that has exactly one right
answer (budget arithmetic, approval thresholds, the 365-day assessment
window, registry-vs-service conflicts, required fields) is computed by code.
The LLM only interprets (gap credibility, data sensitivity, injection intent)
and writes prose. It can add caution but can never remove a required control:
`merge_decision` unions model output with the deterministic ruling and forces
`human_review_required=True` (policy section 11).

## Shared flow

```text
Purchase Request (untrusted business data)
      |
      v
TOOLS (src/tools.py)                      <- 8 deterministic + 1 HTTP call
  get_request_context / check_required_fields / check_budget /
  find_catalog_overlap / find_prior_purchases / lookup_vendor_registry /
  get_vendor_risk / compute_approval_requirements / scan_request_for_injection
      |
      v
RULES (src/rules.py -> derive_rulings)    <- pure code, policy sections 1-10
      |   reference date 2026-09-30, approval floor, risk flags, missing info
      v
LLM layer (architecture-specific)
      |
      v
merge_decision (src/rules.py)             <- deterministic validator
      |   union of approvals/flags/missing, evidence dedup, human gate
      v
ProcurementDecision -> human reviewers
```

## Architecture A - single agent (`architecture="single"`)

One Gemini call in a tool loop (`run_single_agent`): the model receives all
9 tools as function declarations, chooses which to call (in practice it
batches all of them in one turn), then a second structured call produces the
decision draft. The ruling is derived only from tools the agent actually
called - skipping evidence is a real, measurable failure mode.

## Architecture B - staged / 2-agent (`architecture="staged"`)

Prompt-chain workflow (`run_staged`): code runs all 9 tools unconditionally
-> agent 1 ("analyst") judges what rules cannot (credible gap vs catalog,
sensitivity interpretation, injection suspicion) -> agent 2 ("writer")
composes the draft. Fixed shape, exactly 2 LLM calls.

## Deterministic vs model-driven

| Deterministic (code)                        | Model-driven                          |
|---------------------------------------------|---------------------------------------|
| Required-field completeness (policy s1)     | Credibility of stated gap vs catalog  |
| Budget vs available (s2)                    | Interpreting fuzzy data-access levels |
| Overlap matching (s3)                       | Injection intent (semantic layer)     |
| Approval thresholds (s4)                    | Recommendation + next_step prose      |
| Assessment staleness vs 2026-09-30 (s5)     |                                       |
| Conflict/expiry/unavailable detection (s5)  |                                       |
| Sensitivity triggers, PII (s5/s6)           |                                       |
| New-vendor + >=$10k legal trigger (s7)      |                                       |
| Injection keyword screen (s9)               |                                       |

## Stop / escalation conditions

- `missing_information` -> request clarification via Procurement intake
- `budget_insufficient` -> Finance exception review
- `vendor_risk_unavailable` / `conflicting_vendor_evidence` /
  `vendor_review_expired` -> Security manual review; never infer a
  favourable status
- `prompt_injection_detected` -> embedded instruction ignored, flagged
- Everything else -> named approver chain; the copilot never approves

## Handoff format

Every run returns the shared `ProcurementDecision` contract:
recommendation, evidence (each item cites the tool/record/policy section it
came from), required_approvals, missing_information, risk_flags, next_step,
human_review_required, telemetry.

## Intentionally not built

- No autonomous purchase/approval/budget writes (policy s11)
- No framework (no LangChain/ADK): raw `google-genai`, prompts visible
- No RAG over the policy: it is short and is embedded verbatim in the
  system prompt
- No semantic vendor matching beyond vendor/product/category equality;
  fuzzy matching would help hidden cases but adds a matching model for
  marginal gain
- No caching: every run is a fresh, auditable decision
