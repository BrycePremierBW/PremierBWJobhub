#!/usr/bin/env python3
"""
Import a Xero-style Contacts.csv file into JobHub's builders_clients table.

Default behaviour:
- Adds new contacts.
- For an existing name (case-insensitive), fills only blank fields.
- Does not delete anything.
- Can be run repeatedly without duplicating contacts.

Usage:
    python import_contacts_to_jobhub.py Contacts.csv

Optional:
    python import_contacts_to_jobhub.py Contacts.csv --overwrite-existing
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import psycopg2


BUILDER_TERMS = (
    "builder", "building", "built", "construction", "constructions",
    "homes", "property group", "works", "sunstruct"
)


def clean(value: object) -> str:
    return str(value or "").strip()


def normalize_phone(value: str) -> str:
    original = clean(value)
    if not original:
        return ""
    if re.search(r"[A-Za-z]", original):
        return original
    digits = re.sub(r"\D", "", original)
    if len(digits) == 9 and digits[0] in {"2", "3", "4", "7", "8"}:
        digits = "0" + digits
    if len(digits) == 10:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return original


def combine_address(row: dict[str, str], prefix: str) -> str:
    fields = [
        f"{prefix}AddressLine1", f"{prefix}AddressLine2",
        f"{prefix}AddressLine3", f"{prefix}AddressLine4",
        f"{prefix}City", f"{prefix}Region",
        f"{prefix}PostalCode", f"{prefix}Country",
    ]
    return ", ".join(clean(row.get(k)) for k in fields if clean(row.get(k)))


def convert_row(row: dict[str, str]) -> dict[str, str] | None:
    name = clean(row.get("*ContactName"))
    if not name:
        return None

    contact = " ".join(
        x for x in (clean(row.get("FirstName")), clean(row.get("LastName"))) if x
    )
    if not contact:
        contact = " ".join(
            x for x in (
                clean(row.get("Person1FirstName")),
                clean(row.get("Person1LastName")),
            ) if x
        )

    email = clean(row.get("EmailAddress")) or clean(row.get("Person1Email"))
    phone = normalize_phone(clean(row.get("PhoneNumber")) or clean(row.get("MobileNumber")))
    address = combine_address(row, "PO") or combine_address(row, "SA")
    abn = clean(row.get("TaxNumber"))
    legal_name = clean(row.get("LegalName"))
    website = clean(row.get("Website"))
    contact_type = "Builder" if any(term in name.lower() for term in BUILDER_TERMS) else "Client"

    notes = ["Imported from Contacts.csv"]
    if legal_name and legal_name.lower() != name.lower():
        notes.append(f"Legal name: {legal_name}")
    if website:
        notes.append(f"Website: {website}")

    return {
        "type": contact_type,
        "name": name,
        "contact_name": contact,
        "phone": phone,
        "email": email,
        "address": address,
        "qbcc": "",
        "abn": abn,
        "terms": "",
        "notes": " | ".join(notes),
    }


def merge_value(existing: object, incoming: str, overwrite: bool) -> str:
    current = clean(existing)
    if overwrite and incoming:
        return incoming
    return current or incoming


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", nargs="?", default="Contacts.csv")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Replace nonblank fields on existing matching names.",
    )
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        return 2

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in (convert_row(row) for row in csv.DictReader(f)) if r]

    if not rows:
        print("No named contacts were found in the CSV.")
        return 1

    conn = psycopg2.connect(db_url, sslmode="require")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS builders_clients (
                        id SERIAL PRIMARY KEY,
                        type TEXT,
                        name TEXT UNIQUE,
                        contact_name TEXT,
                        phone TEXT,
                        email TEXT,
                        address TEXT,
                        qbcc TEXT,
                        abn TEXT,
                        terms TEXT,
                        notes TEXT
                    )
                """)
                cur.execute("""
                    SELECT id, type, name, contact_name, phone, email,
                           address, qbcc, abn, terms, notes
                    FROM builders_clients
                """)
                existing_rows = cur.fetchall()
                existing = {
                    clean(row[2]).casefold(): {
                        "id": row[0],
                        "type": row[1],
                        "name": row[2],
                        "contact_name": row[3],
                        "phone": row[4],
                        "email": row[5],
                        "address": row[6],
                        "qbcc": row[7],
                        "abn": row[8],
                        "terms": row[9],
                        "notes": row[10],
                    }
                    for row in existing_rows
                }

                inserted = 0
                updated = 0
                unchanged = 0

                for item in rows:
                    key = item["name"].casefold()
                    old = existing.get(key)

                    if old is None:
                        cur.execute("""
                            INSERT INTO builders_clients
                            (type, name, contact_name, phone, email, address,
                             qbcc, abn, terms, notes)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            item["type"], item["name"], item["contact_name"],
                            item["phone"], item["email"], item["address"],
                            item["qbcc"], item["abn"], item["terms"], item["notes"],
                        ))
                        inserted += 1
                        continue

                    merged = {
                        field: merge_value(old.get(field), item[field], args.overwrite_existing)
                        for field in (
                            "type", "contact_name", "phone", "email", "address",
                            "qbcc", "abn", "terms", "notes"
                        )
                    }
                    changed = any(clean(old.get(k)) != clean(v) for k, v in merged.items())
                    if not changed:
                        unchanged += 1
                        continue

                    cur.execute("""
                        UPDATE builders_clients
                        SET type=%s, contact_name=%s, phone=%s, email=%s,
                            address=%s, qbcc=%s, abn=%s, terms=%s, notes=%s
                        WHERE id=%s
                    """, (
                        merged["type"], merged["contact_name"], merged["phone"],
                        merged["email"], merged["address"], merged["qbcc"],
                        merged["abn"], merged["terms"], merged["notes"], old["id"],
                    ))
                    updated += 1

        print(f"Contacts read: {len(rows)}")
        print(f"Inserted: {inserted}")
        print(f"Updated/fill blanks: {updated}")
        print(f"Already unchanged: {unchanged}")
        print("IMPORT COMPLETE")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
