# Architecture Decision Memo

## Decision

Ship the **staged / 2-agent** variant (`architecture="staged"`).

## Evidence

Both architectures share one deterministic core: `src/tools.py` (8 tools,
7 fully deterministic + the vendor-risk HTTP call) and `src/rules.py`
(policy engine + a merge validator that unions model output with the code
ruling and forces `human_review_required`). Only the LLM's role differs.

Results from `evals/run_public_evals.py` on the six public cases:

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| Cases passing minimum checks | 6/6 | 6/6 |
| Avg latency (ms)* | 74,148 | 41,000 |
| Median latency (ms) | 20,421 | 17,904 |
| Avg LLM calls | 3.2 | 2.0 |
| Avg tool calls | 8.0 | 8.0 |
| Policy failures found manually | 0 | 0 |

*Latency includes free-tier 429/503 retry waits; medians are the honest
comparison. Manual review of the four non-public requests (REQ-1004/1007/
1008/1010) found correct behaviour from both, including the
registry-vs-service conflict on SignalWatch and graceful degradation on the
NimbusAI 503.

## Trade-offs

Single agent's only real advantage is dynamic tool choice, which is
worthless here: every request needs the same evidence, and it consistently
batched all 8 tool calls in one turn anyway. Its cost is ~60% more LLM
calls, higher per-case variance, and one extra failure mode observed in
development - the model inventing soft "missing information" on a complete
request, which the contract wording had to be tightened against. The staged
pipeline is fixed-shape: every tool always runs, so evidence coverage is
guaranteed rather than model-dependent.

## Risks / limitations

- Six public + four manual cases is a thin evidence base; hidden cases may
  stress ambiguous categories and paraphrased injections.
- The injection screen is a keyword heuristic backed by an LLM judgment;
  sophisticated paraphrase could evade both - but injected text can never
  grant an approval, since approvals are computed in code.
- Catalog matching is vendor/product/category equality; semantic matching
  is a known upgrade path.
- Free-tier quotas (~20 calls/day/model) made eval runs slow; model
  fallback chain mitigates.

## Why this is the right MVP

Procurement is an audit problem disguised as an AI problem. Staged puts
LLM calls at two fixed, inspectable points with a typed contract between
them, so every flag and approval is reproducible from logged evidence. That
is what the workflow needs, at the lowest latency, cost, and failure
surface of the two options - and the eval numbers confirm it sacrifices
nothing.
