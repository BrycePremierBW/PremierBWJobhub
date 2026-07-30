from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from jobhub_v3.commercial import (
    calculate_eot_due_date_extension,
    calculate_progress_claim,
    validate_transition,
)
from jobhub_v3.schema import ensure_xero_schema
from jobhub_v3.sync_service import XeroSyncEventStore, XeroSyncService
from jobhub_v3.xero_client import XeroToken


class SharedDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")

    def connect(self):
        connection = self.connection

        class Wrapper:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        return Wrapper()


class FakeTokenStore:
    def __init__(self, token):
        self.token = token
        self.saved = []

    def load(self, tenant_id):
        return self.token

    def save(self, **values):
        self.saved.append(values)
        self.token = values["token"]


class FakeXeroClient:
    def __init__(self):
        self.requests = []
        self.refreshes = []

    def refresh(self, refresh_token):
        self.refreshes.append(refresh_token)
        return XeroToken(
            "rotated-access",
            "rotated-refresh",
            datetime.now(timezone.utc) + timedelta(minutes=30),
        )

    def accounting_request(
        self,
        method,
        endpoint,
        token,
        tenant_id,
        *,
        payload=None,
        params=None,
        idempotency_key="",
    ):
        self.requests.append(
            {
                "method": method,
                "endpoint": endpoint,
                "token": token,
                "tenant_id": tenant_id,
                "payload": payload,
                "idempotency_key": idempotency_key,
            }
        )
        if endpoint == "Contacts":
            return {"Contacts": [{"ContactID": "contact-123", **payload["Contacts"][0]}]}
        if endpoint == "Invoices":
            return {"Invoices": [{"InvoiceID": "invoice-123", **payload["Invoices"][0]}]}
        if endpoint.startswith("Invoices/"):
            return {
                "Invoices": [
                    {
                        "InvoiceID": endpoint.split("/", 1)[1],
                        "Status": "PAID",
                        "AmountDue": 0,
                        "AmountPaid": 1250.5,
                        "FullyPaidOnDate": "2026-08-01",
                    }
                ]
            }
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


def connected_service(*, expired=False):
    database = SharedDatabase()
    ensure_xero_schema(database.connect)
    expires = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
        if expired
        else datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    token_store = FakeTokenStore(XeroToken("access", "refresh", expires))
    client = FakeXeroClient()
    service = XeroSyncService(
        tenant_id="tenant-1",
        tenant_name="Premier Brushworks Demo",
        actor="admin",
        client=client,
        token_store=token_store,
        event_store=XeroSyncEventStore(database.connect),
    )
    return service, client, token_store, database


class CommercialTests(unittest.TestCase):
    def test_progress_claim_applies_retention_cap(self):
        result = calculate_progress_claim(
            contract_value=100_000,
            work_complete_percent=50,
            previous_claims_ex_gst=40_000,
            approved_variations_ex_gst=2_000,
            retention_rate_percent=10,
            retention_held_to_date=4_500,
            retention_cap_percent=5,
        )
        self.assertEqual(result["gross_claim_ex_gst"], 12_000.0)
        self.assertEqual(result["retention_this_claim_ex_gst"], 500.0)
        self.assertEqual(result["net_claim_ex_gst"], 11_500.0)

    def test_commercial_transitions_reject_skipping_approval(self):
        validate_transition("progress_claim", "submitted", "approved")
        with self.assertRaisesRegex(ValueError, "cannot move"):
            validate_transition("progress_claim", "draft", "invoiced")

    def test_eot_extension_deducts_concurrent_delay(self):
        self.assertEqual(
            calculate_eot_due_date_extension(
                approved_days=12,
                concurrent_delay_days=3,
            ),
            9,
        )


class XeroSyncServiceTests(unittest.TestCase):
    def test_contact_sync_is_idempotent(self):
        service, client, _store, _database = connected_service()
        values = {
            "entity_type": "builder_client",
            "entity_id": 41,
            "contact": {"name": "Example Builder", "email": "a@example.com"},
        }
        first = service.sync_contact(**values)
        second = service.sync_contact(**values)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["xero_id"], "contact-123")
        self.assertEqual(len(client.requests), 1)

    def test_sales_claim_is_sent_to_xero_as_draft(self):
        service, client, _store, _database = connected_service()
        result = service.push_sales_claim(
            claim_id=7,
            contact_id="contact-123",
            reference="PB101 Claim 2",
            date="2026-07-27",
            due_date="2026-08-26",
            lines=[{"description": "Progress claim", "quantity": 1, "unit_amount": 1000}],
            account_code="200",
        )
        self.assertEqual(result["xero_id"], "invoice-123")
        payload = client.requests[0]["payload"]["Invoices"][0]
        self.assertEqual(payload["Type"], "ACCREC")
        self.assertEqual(payload["Status"], "DRAFT")
        self.assertEqual(len(client.requests[0]["idempotency_key"]), 64)

    def test_supplier_bill_is_sent_to_xero_as_draft(self):
        service, client, _store, _database = connected_service()
        service.push_supplier_bill(
            bill_id=8,
            contact_id="supplier-123",
            reference="SUP-55",
            date="2026-07-27",
            due_date="2026-08-26",
            lines=[{"description": "Paint", "quantity": 2, "unit_amount": 250}],
            account_code="310",
        )
        payload = client.requests[0]["payload"]["Invoices"][0]
        self.assertEqual(payload["Type"], "ACCPAY")
        self.assertEqual(payload["Status"], "DRAFT")

    def test_expired_token_is_refreshed_and_rotated_before_request(self):
        service, client, token_store, _database = connected_service(expired=True)
        service.sync_contact(
            entity_type="supplier",
            entity_id=9,
            contact={"name": "Paint Supplier"},
        )
        self.assertEqual(client.refreshes, ["refresh"])
        self.assertEqual(token_store.saved[0]["token"].refresh_token, "rotated-refresh")
        self.assertEqual(client.requests[0]["token"].access_token, "rotated-access")

    def test_payment_status_is_normalised(self):
        service, _client, _store, _database = connected_service()
        result = service.pull_invoice_status("invoice-123")
        self.assertEqual(result["status"], "PAID")
        self.assertEqual(result["amount_paid"], 1250.5)
        self.assertEqual(result["amount_due"], 0.0)


if __name__ == "__main__":
    unittest.main()
