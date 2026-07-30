"""Validated JobHub-to-Xero payload mappings."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _money(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def build_contact_payload(contact: dict[str, Any]) -> dict[str, Any]:
    name = str(contact.get("name") or contact.get("company_name") or "").strip()
    if not name:
        raise ValueError("Contact name is required.")
    payload: dict[str, Any] = {"Name": name}
    mappings = {
        "email": "EmailAddress",
        "abn": "TaxNumber",
        "first_name": "FirstName",
        "last_name": "LastName",
    }
    for source, target in mappings.items():
        value = str(contact.get(source, "")).strip()
        if value:
            payload[target] = value
    return payload


def _line_item(
    description: str,
    quantity: Any,
    unit_amount: Any,
    account_code: str,
    tax_type: str,
) -> dict[str, Any]:
    if not description.strip():
        raise ValueError("Invoice line description is required.")
    if not account_code.strip():
        raise ValueError("Xero account code is required.")
    return {
        "Description": description.strip(),
        "Quantity": _money(quantity),
        "UnitAmount": _money(unit_amount),
        "AccountCode": account_code.strip(),
        "TaxType": tax_type.strip(),
    }


def build_sales_invoice_payload(
    *,
    contact_id: str,
    reference: str,
    date: str,
    due_date: str,
    lines: list[dict[str, Any]],
    account_code: str,
    tax_type: str = "OUTPUT",
) -> dict[str, Any]:
    if not contact_id:
        raise ValueError("Xero contact ID is required.")
    return {
        "Type": "ACCREC",
        "Contact": {"ContactID": contact_id},
        "Date": date,
        "DueDate": due_date,
        "Reference": reference,
        "Status": "DRAFT",
        "LineAmountTypes": "Exclusive",
        "LineItems": [
            _line_item(
                str(line.get("description", "")),
                line.get("quantity", 1),
                line.get("unit_amount", 0),
                account_code,
                tax_type,
            )
            for line in lines
        ],
    }


def build_purchase_bill_payload(
    *,
    contact_id: str,
    reference: str,
    date: str,
    due_date: str,
    lines: list[dict[str, Any]],
    account_code: str,
    tax_type: str = "INPUT",
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
    payload["Type"] = "ACCPAY"
    return payload
