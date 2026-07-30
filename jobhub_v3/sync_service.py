"""Idempotent Xero contact, invoice, bill and payment synchronisation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from .mappings import (
    build_contact_payload,
    build_purchase_bill_payload,
    build_sales_invoice_payload,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _idempotency_key(
    tenant_id: str,
    entity_type: str,
    entity_id: Any,
    operation: str,
    payload: Any,
) -> str:
    value = {
        "tenant_id": tenant_id,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "operation": operation,
        "payload": payload,
    }
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _event_id(key: str) -> int:
    # Existing V3 tables use an INTEGER primary key. Deriving it from the
    # idempotency key keeps inserts portable across SQLite and PostgreSQL.
    return int(hashlib.sha256(key.encode("ascii")).hexdigest()[:15], 16)


class XeroSyncEventStore:
    def __init__(self, connection_factory) -> None:
        self.connection_factory = connection_factory

    def begin(
        self,
        *,
        key: str,
        entity_type: str,
        entity_id: int,
        direction: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        connection = self.connection_factory()
        cursor = connection.cursor()
        now = datetime.now(timezone.utc).isoformat()
        try:
            cursor.execute(
                """
                INSERT INTO xero_sync_events
                (id, entity_type, entity_id, direction, operation,
                 idempotency_key, status, request_json, attempt_count,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    _event_id(key),
                    entity_type,
                    int(entity_id),
                    direction,
                    operation,
                    key,
                    _canonical(payload),
                    now,
                    now,
                ),
            )
            inserted = int(cursor.rowcount or 0) > 0
            cursor.execute(
                """
                SELECT status, response_json, xero_id, attempt_count
                FROM xero_sync_events
                WHERE idempotency_key = ?
                """,
                (key,),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("Xero sync event could not be loaded.")
            if not inserted and str(row[0]) == "completed":
                connection.commit()
                return {
                    "duplicate": True,
                    "status": "completed",
                    "response": json.loads(row[1] or "{}"),
                    "xero_id": str(row[2] or ""),
                    "attempt_count": int(row[3] or 0),
                }
            cursor.execute(
                """
                UPDATE xero_sync_events
                SET status = 'processing', attempt_count = attempt_count + 1,
                    last_error = '', updated_at = ?
                WHERE idempotency_key = ?
                """,
                (now, key),
            )
            connection.commit()
            return {
                "duplicate": False,
                "status": "processing",
                "attempt_count": int(row[3] or 0) + 1,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete(self, key: str, response: dict[str, Any], xero_id: str = "") -> None:
        self._finish(key, "completed", response, xero_id=xero_id)

    def fail(self, key: str, error: Exception) -> None:
        self._finish(key, "failed", {}, error=str(error)[:2000])

    def _finish(
        self,
        key: str,
        status: str,
        response: dict[str, Any],
        *,
        xero_id: str = "",
        error: str = "",
    ) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE xero_sync_events
                SET status = ?, response_json = ?, xero_id = ?,
                    last_error = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    status,
                    _canonical(response),
                    xero_id,
                    error,
                    datetime.now(timezone.utc).isoformat(),
                    key,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


class XeroSyncService:
    """Perform draft-only Xero writes with token rotation and idempotency."""

    def __init__(
        self,
        *,
        tenant_id: str,
        tenant_name: str,
        actor: str,
        client,
        token_store,
        event_store: XeroSyncEventStore,
    ) -> None:
        if not tenant_id:
            raise ValueError("A Xero tenant ID is required.")
        self.tenant_id = tenant_id
        self.tenant_name = tenant_name
        self.actor = actor
        self.client = client
        self.token_store = token_store
        self.event_store = event_store

    def _token(self):
        token = self.token_store.load(self.tenant_id)
        if token is None:
            raise RuntimeError("The Xero organisation is not connected.")
        if token.needs_refresh():
            token = self.client.refresh(token.refresh_token)
            self.token_store.save(
                tenant_id=self.tenant_id,
                tenant_name=self.tenant_name,
                token=token,
                connected_by=self.actor,
                now=datetime.now(timezone.utc),
            )
        return token

    @staticmethod
    def _extract_first(response: dict[str, Any], plural_name: str) -> dict[str, Any]:
        values = response.get(plural_name) or []
        if not isinstance(values, list) or not values:
            raise RuntimeError(f"Xero returned no {plural_name}.")
        return dict(values[0])

    def _write(
        self,
        *,
        entity_type: str,
        entity_id: int,
        operation: str,
        endpoint: str,
        wrapper: str,
        payload: dict[str, Any],
        id_field: str,
    ) -> dict[str, Any]:
        key = _idempotency_key(
            self.tenant_id,
            entity_type,
            entity_id,
            operation,
            payload,
        )
        started = self.event_store.begin(
            key=key,
            entity_type=entity_type,
            entity_id=entity_id,
            direction="outbound",
            operation=operation,
            payload=payload,
        )
        if started["duplicate"]:
            return {
                "duplicate": True,
                "xero_id": started["xero_id"],
                "response": started["response"],
            }
        try:
            response = self.client.accounting_request(
                "PUT",
                endpoint,
                self._token(),
                self.tenant_id,
                payload={wrapper: [payload]},
                idempotency_key=key,
            )
            item = self._extract_first(response, wrapper)
            xero_id = str(item.get(id_field) or "")
            if not xero_id:
                raise RuntimeError(f"Xero did not return {id_field}.")
            self.event_store.complete(key, response, xero_id)
            return {"duplicate": False, "xero_id": xero_id, "response": response}
        except Exception as exc:
            self.event_store.fail(key, exc)
            raise

    def sync_contact(
        self,
        *,
        entity_type: str,
        entity_id: int,
        contact: dict[str, Any],
    ) -> dict[str, Any]:
        return self._write(
            entity_type=entity_type,
            entity_id=entity_id,
            operation="upsert_contact",
            endpoint="Contacts",
            wrapper="Contacts",
            payload=build_contact_payload(contact),
            id_field="ContactID",
        )

    def push_sales_claim(
        self,
        *,
        claim_id: int,
        contact_id: str,
        reference: str,
        date: str,
        due_date: str,
        lines: list[dict[str, Any]],
        account_code: str,
        tax_type: str = "OUTPUT",
    ) -> dict[str, Any]:
        payload = build_sales_invoice_payload(
            contact_id=contact_id,
            reference=reference,
            date=date,
            due_date=due_date,
            lines=lines,
            account_code=account_code,
            tax_type=tax_type,
        )
        return self._write(
            entity_type="progress_claim",
            entity_id=claim_id,
            operation="create_draft_sales_invoice",
            endpoint="Invoices",
            wrapper="Invoices",
            payload=payload,
            id_field="InvoiceID",
        )

    def push_supplier_bill(
        self,
        *,
        bill_id: int,
        contact_id: str,
        reference: str,
        date: str,
        due_date: str,
        lines: list[dict[str, Any]],
        account_code: str,
        tax_type: str = "INPUT",
    ) -> dict[str, Any]:
        payload = build_purchase_bill_payload(
            contact_id=contact_id,
            reference=reference,
            date=date,
            due_date=due_date,
            lines=lines,
            account_code=account_code,
            tax_type=tax_type,
        )
        return self._write(
            entity_type="supplier_bill",
            entity_id=bill_id,
            operation="create_draft_supplier_bill",
            endpoint="Invoices",
            wrapper="Invoices",
            payload=payload,
            id_field="InvoiceID",
        )

    def pull_invoice_status(self, xero_invoice_id: str) -> dict[str, Any]:
        response = self.client.accounting_request(
            "GET",
            f"Invoices/{xero_invoice_id}",
            self._token(),
            self.tenant_id,
        )
        invoice = self._extract_first(response, "Invoices")
        return {
            "invoice_id": str(invoice.get("InvoiceID") or xero_invoice_id),
            "status": str(invoice.get("Status") or ""),
            "amount_due": float(invoice.get("AmountDue") or 0),
            "amount_paid": float(invoice.get("AmountPaid") or 0),
            "fully_paid_on_date": str(invoice.get("FullyPaidOnDate") or ""),
        }
