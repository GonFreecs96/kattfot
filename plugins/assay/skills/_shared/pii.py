"""Shared PII denylist — the code-level half of refusal rule R6.

R6 ("PII column reach") was historically enforced only at the agent layer
(the skill prompt). This module makes it a *checked* refusal: a denylist of
column-name patterns that no governed skill may group by or filter on, so a
PII column can never surface even if it slipped into a metric's allow-list or
into a spine request. Per the 2026-06-10 governance decision, direct PII is a
**hard refuse with no override**; non-PII data-quality guardrails (units,
cutovers, cardinality) are a separate, warn-level concern handled elsewhere.

What is PII (refused): personal names, contact (email / phone), government IDs
(raw SSN, tax ID, driver's licence, passport), financial account/card numbers,
health records (diagnosis / ICD / medical record / prescription), date of
birth, and street-level address.

What is NOT PII (allowed): tokenized / opaque join keys (`customer_token`,
`session_id`, `order_id`, `payment_token`, …) and aggregate geography
(`market`, `region`, `city`, `postal_code`, `country`). These are explicitly
allow-listed so identity-linkage and geo analysis keep working.

Pure, dependency-free, and unit-tested — imported by the governed query helper
and by `_shared/spine.py` (the spine-backed skills).
"""

from __future__ import annotations

import re

# Opaque identifiers / aggregate geography that must never be flagged as PII,
# even if a substring looks sensitive (e.g. a token column naming what it
# tokenizes). Nothing here is a direct identifier of a person: these are
# surrogate keys and geography coarse enough to aggregate on.
#
# Adding to this list is the one way to weaken R6, so it is reviewed as
# governance, not as config. The test suite asserts each entry stays unflagged
# AND that the denylist below still fires on real PII.
SAFE_ALLOWLIST = {
    # Opaque / surrogate join keys.
    "customer_token",
    "payment_token",
    "session_id",
    "cart_id",
    "checkout_session_id",
    "order_id",
    "order_number",  # business identifier on the order, not a person
    "shipment_id",
    "external_customer_id",
    # Aggregate geography — coarse enough to group by.
    "market",
    "region",
    "state",
    "city",
    "postal_code",
    "zip",
    "zip_code",
    "country",
}

# Each category maps to regexes matched against the *whole* lowercased column
# name (re.fullmatch). Anchoring on fullmatch is what stops a short pattern from
# firing on an innocent column that merely contains it: `tin` (taxpayer ID) must
# not flag `destination_market`, and `address` must not flag `address_type`.
PII_PATTERNS: dict[str, list[re.Pattern]] = {
    "name": [
        re.compile(r"name"),
        re.compile(r"(first|last|middle|full|legal|maiden|preferred|customer|"
                   r"buyer|shopper|recipient|cardholder|account_holder|contact|"
                   r"shipping|billing|payer|owner)_name"),
        re.compile(r"name_(first|last|middle|full)"),
    ],
    "contact": [
        re.compile(r"e_?mail(_address)?"),
        re.compile(r"phone(_number)?"),
        re.compile(r"mobile(_number)?"),
        re.compile(r"fax(_number)?"),
    ],
    "government_id": [
        re.compile(r"ssn"),
        re.compile(r"social_security(_number)?"),
        re.compile(r"tax_id"),
        re.compile(r"tin"),
        re.compile(r"drivers?_licen[sc]e(_number)?"),
        re.compile(r"dl_number"),
        re.compile(r"passport(_number)?"),
        re.compile(r"national_id"),
    ],
    "financial": [
        re.compile(r"(bank_)?account_number"),
        re.compile(r"account_no"),
        re.compile(r"routing_number"),
        re.compile(r"aba(_number)?"),
        re.compile(r"iban"),
        re.compile(r"swift(_code)?"),
        re.compile(r"(credit_)?card_number"),
        re.compile(r"cc_number"),
        re.compile(r"cvv"),
        re.compile(r"bank_account(_number)?"),
    ],
    "health": [
        re.compile(r"diagnos[ie]s"),
        re.compile(r"icd(10|9)?(_code)?"),
        re.compile(r"medical_record(_number)?"),
        re.compile(r"mrn"),
        re.compile(r"prescription"),
        re.compile(r"rx_number"),
        re.compile(r"medications?"),
        re.compile(r"lab_results?"),
        re.compile(r"health_condition"),
    ],
    "date_of_birth": [
        re.compile(r"dob"),
        re.compile(r"date_of_birth"),
        re.compile(r"birth_?date"),
        re.compile(r"birthday"),
        re.compile(r"birth_dt"),
    ],
    "address": [
        re.compile(r"street(_address)?"),
        re.compile(r"street_\d+"),
        re.compile(r"address(_\d+)?"),
        re.compile(r"addr(_\d+)?"),
        re.compile(r"address_line(_\d+)?"),
        re.compile(r"(mailing|home|residential|full|shipping|billing|delivery)_address"),
    ],
}


def pii_reason(column: str) -> str | None:
    """Return the PII category a column matches, or None if it's not PII.

    Pure: lowercases and whole-name-matches against PII_PATTERNS, after honoring
    SAFE_ALLOWLIST. Used both to decide refusal and to name the category in the
    refusal message.
    """
    if column is None:
        return None
    c = column.strip().lower()
    if not c or c in SAFE_ALLOWLIST:
        return None
    for category, patterns in PII_PATTERNS.items():
        if any(rx.fullmatch(c) for rx in patterns):
            return category
    return None


def is_pii_column(column: str) -> bool:
    """True if the column directly surfaces personal PII (refused under R6)."""
    return pii_reason(column) is not None
