from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from jobhub_v3.mappings import (
    build_contact_payload,
    build_purchase_bill_payload,
    build_sales_invoice_payload,
)
from jobhub_v3.xero_client import XeroClient, XeroOAuthConfig, XeroToken


class XeroMappingTests(unittest.TestCase):
    def test_contact_mapping(self):
        payload = build_contact_payload(
            {"company_name": "Example Builder", "email": "accounts@example.com"}
        )
        self.assertEqual(payload["Name"], "Example Builder")
        self.assertEqual(payload["EmailAddress"], "accounts@example.com")

    def test_sales_claim_is_created_as_draft_receivable(self):
        payload = build_sales_invoice_payload(
            contact_id="contact-1",
            reference="JOB-101 Claim 2",
            date="2026-07-27",
            due_date="2026-08-26",
            lines=[{"description": "Progress claim", "quantity": 1, "unit_amount": 1200}],
            account_code="200",
        )
        self.assertEqual(payload["Type"], "ACCREC")
        self.assertEqual(payload["Status"], "DRAFT")
        self.assertEqual(payload["LineItems"][0]["UnitAmount"], 1200.0)

    def test_supplier_invoice_is_created_as_draft_payable(self):
        payload = build_purchase_bill_payload(
            contact_id="supplier-1",
            reference="INV-55",
            date="2026-07-27",
            due_date="2026-08-26",
            lines=[{"description": "Paint", "quantity": 2, "unit_amount": 210.455}],
            account_code="310",
        )
        self.assertEqual(payload["Type"], "ACCPAY")
        self.assertEqual(payload["LineItems"][0]["UnitAmount"], 210.46)


class XeroOAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = XeroOAuthConfig(
            client_id="client",
            client_secret="secret",
            redirect_uri="https://staging.example.com/xero/callback",
        )

    def test_authorisation_url_has_state_and_required_scopes(self):
        client = XeroClient(self.config, session=Mock())
        url = client.authorisation_url("csrf-token")
        self.assertIn("state=csrf-token", url)
        self.assertIn("offline_access", url)
        self.assertIn("accounting.transactions", url)
        self.assertNotIn("secret", url)

    def test_expiring_token_requires_refresh(self):
        now = datetime.now(timezone.utc)
        token = XeroToken("access", "refresh", now + timedelta(seconds=60))
        self.assertTrue(token.needs_refresh(now=now))

    def test_refresh_token_rotation_is_returned_to_caller(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 1800,
        }
        session = Mock()
        session.post.return_value = response
        client = XeroClient(self.config, session=session)
        token = client.refresh("old-refresh")
        self.assertEqual(token.refresh_token, "new-refresh")
        request_data = session.post.call_args.kwargs["data"]
        self.assertEqual(request_data["refresh_token"], "old-refresh")


if __name__ == "__main__":
    unittest.main()
