from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from jobhub_v3.mappings import (
    build_contact_payload,
    build_purchase_bill_payload,
    build_sales_invoice_payload,
)
from jobhub_v3.oauth_state import OAuthNonceStore, OAuthStateSigner
from jobhub_v3.schema import ensure_xero_schema
from jobhub_v3.token_store import XeroTokenStore
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
        self.assertNotIn("app.connections", url)
        self.assertIn("accounting.contacts", url)
        self.assertIn("accounting.invoices", url)
        self.assertIn("accounting.payments", url)
        self.assertNotIn("accounting.transactions", url)
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

    def test_missing_rotated_refresh_token_is_rejected(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "access_token": "new-access",
            "expires_in": 1800,
        }
        session = Mock()
        session.post.return_value = response
        client = XeroClient(self.config, session=session)
        with self.assertRaisesRegex(RuntimeError, "refresh token"):
            client.refresh("old-refresh")

    def test_contacts_are_returned_from_accounting_response(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "Contacts": [{"ContactID": "contact-1", "Name": "Example Builder"}]
        }
        session = Mock()
        session.request.return_value = response
        client = XeroClient(self.config, session=session)
        token = XeroToken(
            "access",
            "refresh",
            datetime.now(timezone.utc) + timedelta(minutes=20),
        )
        contacts = client.contacts(token, "tenant-1")
        self.assertEqual(contacts[0]["ContactID"], "contact-1")
        self.assertEqual(session.request.call_args.args[:2], ("GET", "https://api.xero.com/api.xro/2.0/Contacts"))

    def test_only_draft_invoices_can_be_created(self):
        client = XeroClient(self.config, session=Mock())
        token = XeroToken(
            "access",
            "refresh",
            datetime.now(timezone.utc) + timedelta(minutes=20),
        )
        with self.assertRaisesRegex(ValueError, "Only DRAFT"):
            client.create_draft_invoice(
                token,
                "tenant-1",
                {"Status": "AUTHORISED"},
            )

    def test_draft_invoice_is_wrapped_and_returned(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "Invoices": [
                {
                    "InvoiceID": "invoice-1",
                    "InvoiceNumber": "INV-1",
                    "Status": "DRAFT",
                }
            ]
        }
        session = Mock()
        session.request.return_value = response
        client = XeroClient(self.config, session=session)
        token = XeroToken(
            "access",
            "refresh",
            datetime.now(timezone.utc) + timedelta(minutes=20),
        )
        created = client.create_draft_invoice(
            token,
            "tenant-1",
            {"Status": "DRAFT"},
        )
        self.assertEqual(created["InvoiceID"], "invoice-1")
        request_payload = session.request.call_args.kwargs["json"]
        self.assertEqual(request_payload, {"Invoices": [{"Status": "DRAFT"}]})
        idempotency_key = session.request.call_args.kwargs["headers"].get(
            "Idempotency-Key"
        )
        self.assertEqual(len(idempotency_key), 64)


class OAuthStateTests(unittest.TestCase):
    def test_signed_state_round_trip(self):
        signer = OAuthStateSigner("s" * 32)
        state = signer.issue("admin-1", "nonce-1", now=1000)
        payload = signer.verify(state, now=1050)
        self.assertEqual(payload["user_id"], "admin-1")
        self.assertEqual(payload["nonce"], "nonce-1")

    def test_tampered_state_is_rejected(self):
        signer = OAuthStateSigner("s" * 32)
        state = signer.issue("admin-1", "nonce-1", now=1000)
        with self.assertRaisesRegex(ValueError, "signature"):
            signer.verify(state + "x", now=1050)

    def test_expired_state_is_rejected(self):
        signer = OAuthStateSigner("s" * 32, max_age_seconds=60)
        state = signer.issue("admin-1", "nonce-1", now=1000)
        with self.assertRaisesRegex(ValueError, "expired"):
            signer.verify(state, now=1061)

    def test_nonce_can_be_consumed_only_once(self):
        import sqlite3

        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE xero_oauth_nonces (
                nonce_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        class SharedConnection:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        store = OAuthNonceStore(lambda: SharedConnection())
        store.register(
            "admin-1",
            "nonce-1",
            created_at="2026-07-27T10:00:00+00:00",
            expires_at="2026-07-27T10:10:00+00:00",
        )
        self.assertTrue(
            store.consume(
                "admin-1",
                "nonce-1",
                consumed_at="2026-07-27T10:05:00+00:00",
            )
        )
        self.assertFalse(
            store.consume(
                "admin-1",
                "nonce-1",
                consumed_at="2026-07-27T10:06:00+00:00",
            )
        )


class PrefixCipher:
    def encrypt(self, value):
        return "encrypted:" + value

    def decrypt(self, value):
        if not value.startswith("encrypted:"):
            raise ValueError("Token was not encrypted.")
        return value.removeprefix("encrypted:")


class TokenStoreTests(unittest.TestCase):
    def test_tokens_are_encrypted_at_rest_and_can_be_loaded(self):
        import sqlite3

        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE xero_connections (
                id INTEGER PRIMARY KEY,
                tenant_id TEXT NOT NULL UNIQUE,
                tenant_name TEXT,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT NOT NULL,
                token_expires_at TEXT NOT NULL,
                scopes TEXT,
                connected_by TEXT,
                connected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        class SharedConnection:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        store = XeroTokenStore(lambda: SharedConnection(), PrefixCipher())
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        store.save(
            tenant_id="tenant-1",
            tenant_name="Premier Brushworks",
            token=XeroToken("access", "refresh", expires, scope="accounting.invoices"),
            connected_by="admin",
            now=datetime.now(timezone.utc),
        )
        raw = connection.execute(
            "SELECT encrypted_access_token, encrypted_refresh_token FROM xero_connections"
        ).fetchone()
        self.assertEqual(raw[0], "encrypted:access")
        self.assertEqual(raw[1], "encrypted:refresh")
        loaded = store.load("tenant-1")
        self.assertEqual(loaded.access_token, "access")
        self.assertEqual(loaded.refresh_token, "refresh")


class XeroSchemaTests(unittest.TestCase):
    def test_schema_accepts_connection_factory_and_is_restart_safe(self):
        import sqlite3

        connection = sqlite3.connect(":memory:")

        class SharedConnection:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        ensure_xero_schema(lambda: SharedConnection())
        ensure_xero_schema(lambda: SharedConnection())
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("xero_connections", tables)
        self.assertIn("xero_oauth_nonces", tables)


if __name__ == "__main__":
    unittest.main()
