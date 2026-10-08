"""Offline check of the deterministic core.

Runs every tool + derive_rulings with the vendor-risk HTTP call stubbed by
data/vendor_risk.json, so no LLM key or running mock server is needed.
Fails if the policy engine regresses on the representative cases.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
RISK_DATA = json.loads((ROOT / "data" / "vendor_risk.json").read_text())


def fake_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    record = RISK_DATA.get(vendor_name)
    if record is None:
        raise requests.HTTPError("404", response=type("R", (), {"status_code": 404})())
    if record.get("force_error"):
        raise requests.HTTPError("503", response=type("R", (), {"status_code": 503})())
    return {"vendor_name": vendor_name, **record}


class RulingTests(unittest.TestCase):
    def ruling(self, request_id: str) -> dict:
        from src.rules import derive_rulings
        from src.tools import TOOLS

        with patch("src.tools._risk_call", side_effect=fake_risk):
            outputs = {name: fn(request_id) for name, fn in TOOLS.items()}
        return derive_rulings(outputs)

    def test_low_value_approved_vendor(self):
        r = self.ruling("REQ-1001")
        self.assertIn("Manager", r["required_approvals"])
        self.assertNotIn("budget_insufficient", r["risk_flags"])
        self.assertEqual(r["missing_information"], [])

    def test_budget_and_new_vendor(self):
        r = self.ruling("REQ-1005")
        for flag in (
            "budget_insufficient",
            "security_review_required",
            "privacy_review_required",
            "legal_review_required",
        ):
            self.assertIn(flag, r["risk_flags"])
        for approver in ("Finance", "Security", "Privacy", "Legal"):
            self.assertIn(approver, r["required_approvals"])

    def test_incomplete_and_injected_request(self):
        r = self.ruling("REQ-1006")
        self.assertIn("missing_information", r["risk_flags"])
        self.assertIn("prompt_injection_detected", r["risk_flags"])
        self.assertGreaterEqual(len(r["missing_information"]), 3)

    def test_stale_conflicting_vendor(self):
        r = self.ruling("REQ-1007")
        self.assertIn("vendor_review_expired", r["risk_flags"])
        self.assertIn("conflicting_vendor_evidence", r["risk_flags"])

    def test_vendor_risk_outage(self):
        r = self.ruling("REQ-1009")
        self.assertIn("vendor_risk_unavailable", r["risk_flags"])
        self.assertIn("security_review_required", r["risk_flags"])
        self.assertIn("Legal", r["required_approvals"])

    def test_no_data_training_pack_stays_manager_only(self):
        r = self.ruling("REQ-1010")
        self.assertEqual(r["required_approvals"], ["Manager"])


if __name__ == "__main__":
    unittest.main()
