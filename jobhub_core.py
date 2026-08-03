"""Security and financial helpers for Premier Brushworks JobHub.

This module deliberately has no Streamlit or database imports so its business
rules can be tested without starting the application.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
from datetime import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse


PASSWORD_SCHEME = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 6
MONEY_PLACES = Decimal("0.01")

_KNOWN_DEFAULT_PASSWORDS = ("admin123", "manager123", "changeme123")
KNOWN_DEFAULT_PASSWORD_HASHES = frozenset(
    hashlib.sha256(password.encode("utf-8")).hexdigest()
    for password in _KNOWN_DEFAULT_PASSWORDS
)


def hash_password(
    password: str,
    *,
    iterations: int = PBKDF2_ITERATIONS,
    salt_hex: str | None = None,
) -> str:
    """Return a salted PBKDF2-SHA256 password hash."""
    password = str(password or "")
    if not password:
        raise ValueError("Password cannot be blank.")
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        int(iterations),
    ).hex()
    return f"{PASSWORD_SCHEME}${int(iterations)}${salt_hex}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify PBKDF2 hashes and legacy SHA-256 hashes during migration."""
    try:
        stored_hash = str(stored_hash or "")
        if stored_hash.startswith(f"{PASSWORD_SCHEME}$"):
            _, iterations_text, salt_hex, expected = stored_hash.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                str(password or "").encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations_text),
            ).hex()
            return hmac.compare_digest(actual, expected)

        legacy = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored_hash)
    except (ValueError, TypeError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """Return True for legacy or lower-work-factor hashes."""
    try:
        scheme, iterations_text, _salt, _digest = str(stored_hash or "").split("$", 3)
        return scheme != PASSWORD_SCHEME or int(iterations_text) < PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


def is_known_default_password_hash(stored_hash: str) -> bool:
    return str(stored_hash or "") in KNOWN_DEFAULT_PASSWORD_HASHES


def password_strength_errors(password: str, username: str = "") -> list[str]:
    """Return user-facing password policy failures."""
    password = str(password or "")
    username = str(username or "").strip().casefold()
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if username and username in password.casefold():
        errors.append("Do not include the username.")
    if password.casefold() in {item.casefold() for item in _KNOWN_DEFAULT_PASSWORDS}:
        errors.append("This default password is disabled.")
    return errors


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def calculate_estimate_pricing(
    *,
    line_total: object,
    labour_hours: object,
    labour_rate: object,
    material_allowance: object,
    access_equipment_allowance: object,
    subcontractor_allowance: object,
    sundries_allowance: object,
    pricing_percent: object,
    contingency_percent: object,
    gst_percent: object,
    pricing_method: str = "Target Gross Margin",
) -> dict[str, float]:
    """Calculate an estimate using an explicit markup or gross-margin method."""
    labour_total = _decimal(labour_hours) * _decimal(labour_rate)
    direct_total = (
        _decimal(line_total)
        + labour_total
        + _decimal(material_allowance)
        + _decimal(access_equipment_allowance)
        + _decimal(subcontractor_allowance)
        + _decimal(sundries_allowance)
    )
    contingency_amount = direct_total * (_decimal(contingency_percent) / Decimal("100"))
    subtotal = direct_total + contingency_amount
    percent = _decimal(pricing_percent) / Decimal("100")

    method = str(pricing_method or "Target Gross Margin").strip()
    if method == "Target Gross Margin":
        if percent < 0 or percent >= 1:
            raise ValueError("Target gross margin must be at least 0% and less than 100%.")
        total_ex_gst = subtotal / (Decimal("1") - percent) if subtotal else Decimal("0")
        pricing_amount = total_ex_gst - subtotal
    elif method == "Markup":
        if percent < 0:
            raise ValueError("Markup cannot be negative.")
        pricing_amount = subtotal * percent
        total_ex_gst = subtotal + pricing_amount
    else:
        raise ValueError("Pricing method must be 'Target Gross Margin' or 'Markup'.")

    gst_amount = total_ex_gst * (_decimal(gst_percent) / Decimal("100"))
    total_inc_gst = total_ex_gst + gst_amount
    achieved_margin_percent = (
        (pricing_amount / total_ex_gst) * Decimal("100")
        if total_ex_gst
        else Decimal("0")
    )

    return {
        "line_total": float(_money(_decimal(line_total))),
        "labour_total": float(_money(labour_total)),
        "direct_total": float(_money(direct_total)),
        "contingency_amount": float(_money(contingency_amount)),
        "subtotal_before_pricing": float(_money(subtotal)),
        "margin_amount": float(_money(pricing_amount)),
        "total_ex_gst": float(_money(total_ex_gst)),
        "gst_amount": float(_money(gst_amount)),
        "total_inc_gst": float(_money(total_inc_gst)),
        "achieved_margin_percent": float(
            achieved_margin_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
    }


def is_public_ip_address(value: str) -> bool:
    """Only globally routable addresses are safe for server-side URL fetching."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global)


def validate_public_http_url(url: str) -> tuple[bool, str]:
    """Validate URL syntax and resolve its host away from private networks."""
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https URLs are allowed."
    if not parsed.hostname:
        return False, "The URL must include a valid hostname."
    if parsed.username or parsed.password:
        return False, "URLs containing embedded credentials are not allowed."
    if parsed.port and parsed.port not in {80, 443}:
        return False, "Only standard HTTP and HTTPS ports are allowed."

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False, "Local and private-network hosts are not allowed."

    try:
        resolved = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except OSError:
        return False, "The hostname could not be resolved."
    if not resolved or any(not is_public_ip_address(address) for address in resolved):
        return False, "The URL resolves to a private, local, reserved or otherwise unsafe address."
    return True, ""


def next_scoped_number(existing_values: list[object], prefix: str) -> str:
    """Generate the next stable scoped number without reusing a deleted count."""
    prefix = str(prefix or "").upper().strip("-")
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", flags=re.IGNORECASE)
    for value in existing_values:
        match = pattern.match(str(value or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def calculate_shift_hours(
    start_time: object,
    finish_time: object,
    break_minutes: object = 0,
) -> float:
    """Calculate net shift hours, including overnight shifts."""

    def minutes(value: object) -> int:
        if isinstance(value, time):
            return int(value.hour) * 60 + int(value.minute)
        match = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
        if not match:
            raise ValueError("Time must be in HH:MM format.")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("Time is outside the valid 24-hour range.")
        return hour * 60 + minute

    start_minutes = minutes(start_time)
    finish_minutes = minutes(finish_time)
    if finish_minutes < start_minutes:
        finish_minutes += 24 * 60
    gross_minutes = finish_minutes - start_minutes
    try:
        break_value = float(break_minutes or 0)
    except (TypeError, ValueError):
        raise ValueError("Break minutes must be a number.") from None
    if break_value < 0:
        raise ValueError("Break minutes cannot be negative.")
    net_minutes = gross_minutes - break_value
    if net_minutes < 0:
        raise ValueError("Break minutes cannot exceed the shift duration.")
    return round(net_minutes / 60, 2)
