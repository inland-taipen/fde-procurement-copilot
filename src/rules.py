"""Deterministic policy engine and decision validator.

Everything that has exactly one right answer lives here: financial
thresholds, the 365-day vendor-assessment window, sensitivity triggers,
registry-vs-service conflict detection, and the final merge that makes the
LLM output safe. The model can add caution (extra flags, approvals, missing
items) but can never remove a control the policy requires.
"""
from __future__ import annotations

import re
from datetime import date

from src.contracts import EvidenceItem, ProcurementDecision

# Policy: "Data snapshot / evaluation reference date: 2026-09-30".
# Never use the machine clock for staleness checks.
REFERENCE_DATE = date(2026, 9, 30)
ASSESSMENT_VALID_DAYS = 365
NEW_VENDOR_LEGAL_THRESHOLD_USD = 10_000

SECURITY_DATA_LEVELS = {
    "source_code",
    "production_telemetry",
    "confidential_documents",
    "employee_pii",
    "customer_pii",
    "credentials",
    "secrets",
}
PRIVACY_DATA_LEVELS = {"employee_pii", "customer_pii"}
SECURITY_INTEGRATION_PAT = re.compile(
    r"production|cloud|git|repository|source|codebase|credential|secret", re.IGNORECASE
)

# Canonical ordering for required_approvals output.
APPROVAL_ORDER = [
    "Manager",
    "Department Head",
    "Procurement",
    "Finance",
    "CFO",
    "Security",
    "Privacy",
    "Legal",
]


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _stale(review_date: date | None) -> bool:
    return review_date is None or (REFERENCE_DATE - review_date).days > ASSESSMENT_VALID_DAYS


def derive_rulings(tool_outputs: dict[str, dict]) -> dict:
    """Turn collected tool outputs into risk flags, approvals and missing info.

    Only uses tools that actually ran - in the single-agent architecture an
    agent that skips a tool produces a thinner ruling, which is an honest,
    measurable difference between the architectures.
    """
    flags: set[str] = set()
    approvals: set[str] = set()
    missing: list[str] = []

    fields = tool_outputs.get("check_required_fields", {}).get("data", {})
    if fields.get("missing"):
        flags.add("missing_information")
        missing.extend(fields["missing"])

    budget = tool_outputs.get("check_budget", {}).get("data", {})
    if budget.get("within_budget") is False:
        flags.add("budget_insufficient")
        approvals.add("Finance")  # budget exception review, policy section 2

    overlap = tool_outputs.get("find_catalog_overlap", {}).get("data", {})
    if overlap.get("matches"):
        flags.add("existing_tool_overlap")

    thresholds = tool_outputs.get("compute_approval_requirements", {}).get("data", {})
    approvals.update(thresholds.get("approvals", []))

    registry = tool_outputs.get("lookup_vendor_registry", {}).get("data", {})
    risk = tool_outputs.get("get_vendor_risk", {}).get("data", {})
    context = tool_outputs.get("get_request_context", {}).get("data", {})
    injection = tool_outputs.get("scan_request_for_injection", {}).get("data", {})
    access = (context.get("request", {}).get("data_access_level") or "").lower()

    # --- vendor posture: registry + external service, with conflict handling
    is_new_vendor = not registry or registry.get("procurement_status") == "New"
    reg_date = _iso_date(registry.get("security_review_date"))
    reg_status = str(registry.get("security_status", "")).lower()

    risk_status = risk.get("status")
    if risk_status == "unavailable":
        flags.update({"vendor_risk_unavailable", "security_review_required"})
        approvals.add("Security")
    elif risk_status == "not_found":
        flags.add("security_review_required")  # no external assessment exists
        approvals.add("Security")
    elif risk_status == "ok":
        ext_status = str(risk.get("security_review_status", "")).lower()
        ext_date = _iso_date(risk.get("last_review_date"))
        assessment_bad = ext_status in {"expired", "not_completed"} or _stale(ext_date)
        if ext_status == "expired" or (ext_status == "approved" and _stale(ext_date)):
            flags.add("vendor_review_expired")
        if assessment_bad:
            flags.add("security_review_required")
            approvals.add("Security")
        # registry claims approved but the service disagrees (or vice versa)
        ext_ok = ext_status == "approved" and not _stale(ext_date)
        if registry and (reg_status == "approved") != ext_ok:
            flags.add("conflicting_vendor_evidence")
            approvals.add("Security")
        # vendor processes personal data / stores it outside the region only
        # matters if this request actually sends data to the tool
        if access != "none" and (
            risk.get("processes_personal_data") or risk.get("stores_data_outside_region")
        ):
            flags.add("privacy_review_required")
            approvals.add("Privacy")
    elif registry and _stale(reg_date):
        flags.update({"vendor_review_expired", "security_review_required"})
        approvals.add("Security")

    # --- data sensitivity from the request itself
    if access in SECURITY_DATA_LEVELS or access in {"unknown", ""}:
        flags.add("security_review_required")
        approvals.add("Security")
    if access in PRIVACY_DATA_LEVELS:
        flags.add("privacy_review_required")
        approvals.add("Privacy")
    integrations = context.get("request", {}).get("requested_integrations") or []
    if any(SECURITY_INTEGRATION_PAT.search(i) for i in integrations):
        flags.add("security_review_required")
        approvals.add("Security")

    # --- legal review triggers
    cost = context.get("request", {}).get("annual_cost_usd")
    legal_terms = str(registry.get("legal_terms_status", "")).lower()
    if (is_new_vendor and (cost or 0) >= NEW_VENDOR_LEGAL_THRESHOLD_USD) or (
        registry and legal_terms != "approved"
    ):
        flags.add("legal_review_required")
        approvals.add("Legal")

    if injection.get("suspected"):
        flags.add("prompt_injection_detected")

    # an incomplete request still needs an intake owner to chase the gaps
    if "missing_information" in flags:
        approvals.add("Procurement")

    ordered = [a for a in APPROVAL_ORDER if a in approvals]
    ordered += sorted(approvals - set(ordered))
    return {
        "risk_flags": sorted(flags),
        "required_approvals": ordered,
        "missing_information": missing,
    }


def merge_decision(
    request_id: str,
    ruling: dict,
    draft: dict,
    tool_outputs: dict[str, dict],
) -> ProcurementDecision:
    """Merge an LLM draft with the deterministic ruling.

    Derived approvals/flags/missing items are the floor: the model may add
    more caution, never less. Evidence is the union of tool-grounded items
    and any extra items the model added (which must carry a source).
    """
    flags = set(ruling["risk_flags"]) | set(draft.get("risk_flags", []))
    approvals = set(ruling["required_approvals"]) | set(draft.get("required_approvals", []))
    missing = list(dict.fromkeys(ruling["missing_information"] + draft.get("missing_information", [])))

    evidence: list[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for out in tool_outputs.values():
        for item in out.get("evidence", []):
            key = (item.source, item.finding)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    for item in draft.get("evidence", []):
        item = EvidenceItem.model_validate(item)
        if item.source and item.finding and (item.source, item.finding) not in seen:
            seen.add((item.source, item.finding))
            evidence.append(item)

    return ProcurementDecision(
        request_id=request_id,
        recommendation=draft.get("recommendation") or "Route for human review",
        evidence=evidence[:15],
        required_approvals=[a for a in APPROVAL_ORDER if a in approvals]
        + sorted(approvals - set(APPROVAL_ORDER)),
        missing_information=missing,
        risk_flags=sorted(flags),
        next_step=draft.get("next_step") or "Send the evidence package to the listed approvers.",
        human_review_required=True,  # policy section 11: recommendations only
    )
