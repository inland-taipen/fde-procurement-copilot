"""Tool layer: evidence-gathering functions shared by both architectures.

Each tool takes a request_id, reads the business data (or calls the mock
vendor-risk API) and returns a dict:

    {"tool": <name>, "data": {...}, "evidence": [EvidenceItem, ...]}

Evidence items are built from real records so the evidence panel is always
grounded in a tool output. At least 3 tools must exist (>=1 deterministic);
all of these except get_vendor_risk are fully deterministic, and even that
one is a pure HTTP lookup.
"""
from __future__ import annotations

import re

import pandas as pd
import requests

from src.contracts import EvidenceItem
from src.data_access import (
    get_request,
    load_budgets,
    load_employees,
    load_software_catalog,
    load_vendors,
)
from src.vendor_client import get_vendor_risk as _risk_call


def _ev(source: str, finding: str, reference: str | None = None) -> EvidenceItem:
    return EvidenceItem(source=source, finding=finding, reference=reference)


def _req(request_id: str) -> dict:
    return get_request(request_id)


def get_request_context(request_id: str) -> dict:
    """Load the request plus requester/department/budget context."""
    req = _req(request_id)
    employee = load_employees().query("employee_id == @req['requester_id']").iloc[0]
    budget_rows = load_budgets().query("department == @employee['department']")
    budget = budget_rows.iloc[0].to_dict() if len(budget_rows) else {}
    return {
        "tool": "get_request_context",
        "data": {
            "request": req,
            "requester": {
                "employee_id": employee["employee_id"],
                "name": employee["name"],
                "department": employee["department"],
            },
            "budget": {
                "annual_usd": int(budget["annual_software_budget_usd"]),
                "committed_usd": int(budget["committed_usd"]),
                "available_usd": int(budget["available_usd"]),
            },
        },
        "evidence": [
            _ev(
                "request_record",
                f"{req['request_id']}: {req['product_name']} from {req['vendor_name']} "
                f"for {employee['name']} ({employee['department']})",
                req["request_id"],
            ),
            _ev(
                "department_budgets.csv",
                f"{employee['department']} has ${budget['available_usd']:,} available "
                f"of ${budget['annual_software_budget_usd']:,} annual software budget",
                employee["department"],
            ),
        ],
    }


def check_required_fields(request_id: str) -> dict:
    """Deterministic policy section 1 check: which required fields are missing."""
    req = _req(request_id)

    def blank(value) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    missing = []
    if blank(req.get("requester_id")):
        missing.append("requester and department")
    if blank(req.get("product_name")) or blank(req.get("vendor_name")):
        missing.append("product/vendor")
    if req.get("annual_cost_usd") is None:
        missing.append("annual cost or a reasonable annual estimate")
    if req.get("user_count") is None:
        missing.append("number of users/licenses")
    if blank(req.get("business_justification")):
        missing.append("business purpose")
    if blank(req.get("data_access_level")) or req.get("data_access_level") == "unknown":
        missing.append("intended data-access level")

    return {
        "tool": "check_required_fields",
        "data": {"missing": missing},
        "evidence": [
            _ev(
                "policy_check",
                f"Required fields missing: {', '.join(missing)}"
                if missing
                else "All required request fields are present",
                "procurement_policy.md section 1",
            )
        ],
    }


def check_budget(request_id: str) -> dict:
    """Deterministic budget check: annual cost vs department's available budget."""
    ctx = get_request_context(request_id)["data"]
    cost = ctx["request"].get("annual_cost_usd")
    available = ctx["budget"]["available_usd"]
    dept = ctx["requester"]["department"]
    if cost is None:
        return {
            "tool": "check_budget",
            "data": {"within_budget": None, "cost": None, "available_usd": available},
            "evidence": [
                _ev(
                    "policy_check",
                    f"Budget check not possible: no annual cost provided "
                    f"({dept} has ${available:,} available)",
                    "procurement_policy.md section 2",
                )
            ],
        }
    within = cost <= available
    return {
        "tool": "check_budget",
        "data": {"within_budget": within, "cost": cost, "available_usd": available},
        "evidence": [
            _ev(
                "policy_check",
                f"Annual cost ${cost:,} is "
                f"{'within' if within else 'ABOVE'} the {dept} available budget "
                f"of ${available:,}",
                "procurement_policy.md section 2",
            )
        ],
    }


def find_catalog_overlap(request_id: str) -> dict:
    """Check the approved software catalog for same product/vendor/category."""
    req = _req(request_id)
    catalog = load_software_catalog()
    matches = []
    for row in catalog.itertuples():
        reasons = []
        if row.vendor_name.lower() == req["vendor_name"].lower():
            reasons.append("same vendor")
        if row.product_name.lower() == req["product_name"].lower():
            reasons.append("same product")
        if row.category.lower() == req["category"].lower():
            reasons.append("same category")
        if reasons:
            matches.append(
                {
                    "product_name": row.product_name,
                    "vendor_name": row.vendor_name,
                    "category": row.category,
                    "status": row.status,
                    "licensed_seats": int(row.licensed_seats),
                    "scope": row.scope,
                    "reasons": reasons,
                }
            )
    return {
        "tool": "find_catalog_overlap",
        "data": {"matches": matches},
        "evidence": [
            _ev(
                "software_catalog.csv",
                f"Existing catalog match ({' + '.join(m['reasons'])}): "
                f"{m['product_name']} ({m['vendor_name']}), status '{m['status']}', "
                f"{m['licensed_seats']} seats, scope {m['scope']}",
                m["product_name"],
            )
            for m in matches
        ]
        or [
            _ev(
                "software_catalog.csv",
                "No existing approved product matches this request's vendor or category",
                "software_catalog.csv",
            )
        ],
    }


def lookup_vendor_registry(request_id: str) -> dict:
    """Look the vendor up in the internal procurement registry (vendors.csv)."""
    vendor = _req(request_id)["vendor_name"]
    rows = load_vendors().query("vendor_name == @vendor")
    if rows.empty:
        return {
            "tool": "lookup_vendor_registry",
            "data": {"in_registry": False, "vendor_name": vendor},
            "evidence": [
                _ev(
                    "vendors.csv",
                    f"{vendor} is not in the internal vendor registry",
                    "vendors.csv",
                )
            ],
        }
    row = rows.iloc[0]
    # empty CSV cells arrive as NaN (truthy); normalise to None so nothing
    # downstream mistakes them for data or emits invalid JSON
    data = {
        key: (None if pd.isna(row[key]) else row[key])
        for key in (
            "procurement_status",
            "security_status",
            "security_review_date",
            "legal_terms_status",
            "notes",
        )
    }
    return {
        "tool": "lookup_vendor_registry",
        "data": {"in_registry": True, "vendor_name": vendor, **data},
        "evidence": [
            _ev(
                "vendors.csv",
                f"Registry: {vendor} procurement={data['procurement_status']}, "
                f"security={data['security_status']} "
                f"(review date {data['security_review_date'] or 'none'}), "
                f"legal terms={data['legal_terms_status']}",
                row["vendor_id"],
            )
        ],
    }


def get_vendor_risk(request_id: str) -> dict:
    """Call the external vendor-risk service. Never raises: failures become
    structured 'unavailable'/'not_found' data the rules engine can flag."""
    vendor = _req(request_id)["vendor_name"]
    try:
        record = _risk_call(vendor)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        state = "not_found" if status == 404 else "unavailable"
        detail = f"HTTP {status}" if status else str(exc)
        return {
            "tool": "get_vendor_risk",
            "data": {"status": state, "vendor_name": vendor, "detail": detail},
            "evidence": [
                _ev(
                    "vendor_risk_api",
                    f"Vendor-risk service returned {detail} for {vendor}; "
                    "vendor security posture could not be verified externally",
                    f"/vendor-risk/{vendor}",
                )
            ],
        }
    except requests.RequestException as exc:
        return {
            "tool": "get_vendor_risk",
            "data": {"status": "unavailable", "vendor_name": vendor, "detail": str(exc)},
            "evidence": [
                _ev(
                    "vendor_risk_api",
                    f"Vendor-risk service unreachable for {vendor}: {exc}",
                    f"/vendor-risk/{vendor}",
                )
            ],
        }
    return {
        "tool": "get_vendor_risk",
        "data": {"status": "ok", "vendor_name": vendor, **record},
        "evidence": [
            _ev(
                "vendor_risk_api",
                f"External risk service: {vendor} risk={record.get('risk_level')}, "
                f"security review={record.get('security_review_status')} "
                f"(last review {record.get('last_review_date') or 'none'})",
                f"/vendor-risk/{vendor}",
            )
        ],
    }


def compute_approval_requirements(request_id: str) -> dict:
    """Deterministic financial approval thresholds (policy section 4)."""
    cost = _req(request_id).get("annual_cost_usd")
    if cost is None:
        approvals = []
        finding = "Approval threshold cannot be determined: no annual cost provided"
    elif cost <= 1_000:
        approvals = ["Manager"]
        finding = "Cost <= $1,000 -> Manager approval"
    elif cost <= 10_000:
        approvals = ["Department Head", "Procurement"]
        finding = "Cost $1,000.01-$10,000 -> Department Head + Procurement"
    elif cost <= 25_000:
        approvals = ["Department Head", "Finance", "Procurement"]
        finding = "Cost $10,000.01-$25,000 -> Department Head + Finance + Procurement"
    else:
        approvals = ["Department Head", "Finance", "CFO", "Procurement"]
        finding = "Cost > $25,000 -> Department Head + Finance + CFO + Procurement"
    return {
        "tool": "compute_approval_requirements",
        "data": {"cost": cost, "approvals": approvals},
        "evidence": [_ev("policy_check", finding, "procurement_policy.md section 4")],
    }


_INJECTION_PATTERNS = re.compile(
    r"ignore\s+(all|any|previous|prior|the|these|procurement|policy)|"
    r"\b(?:pre[- ]?approved|approved\s+by|treat\s+(this|it)\s+as)|"
    r"cfo[- ]approved|bypass|override\s+(the\s+)?(rule|policy|procurement|control)|"
    r"skip\s+(the\s+)?(review|approval|procurement|policy|security)|"
    r"do\s+not\s+(follow|apply)|disregard|forget\s+(all|your)|"
    r"approve\s+(it|this)\s+immediately",
    re.IGNORECASE,
)


def scan_request_for_injection(request_id: str) -> dict:
    """Deterministic screen for instruction-like text inside business data.

    ponytail: keyword/pattern ceiling - catches imperative override attempts
    but not paraphrases; the LLM analyst judges semantics on top, and all
    sensitive outputs are computed in code so a miss can only lose the flag,
    never grant an approval.
    """
    req = _req(request_id)
    hits = []
    for field in ("business_justification", "product_name", "vendor_name", "urgency"):
        value = req.get(field)
        if isinstance(value, str):
            for match in _INJECTION_PATTERNS.finditer(value):
                hits.append({"field": field, "matched": match.group(0)})
    return {
        "tool": "scan_request_for_injection",
        "data": {"suspected": bool(hits), "matches": hits},
        "evidence": [
            _ev(
                "policy_check",
                f"Request text contains instruction-like pattern in {hits[0]['field']}: "
                f"'{hits[0]['matched']}'" if hits else
                "No instruction-like patterns detected in request text",
                "procurement_policy.md section 9",
            )
        ],
    }


# Registry shared by both architectures: staged calls these directly, the
# single agent gets them as function declarations.
TOOLS = {
    fn.__name__: fn
    for fn in (
        get_request_context,
        check_required_fields,
        check_budget,
        find_catalog_overlap,
        lookup_vendor_registry,
        get_vendor_risk,
        compute_approval_requirements,
        scan_request_for_injection,
    )
}
