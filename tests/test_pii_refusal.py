"""R6 hardening: PII is refused in code, not just asked for in a prompt.

Covers the shared denylist (`_shared/pii.py`) and its wiring into the spine
skills (`_shared/spine.validate_dimension` / `validate_filters`). All pure and
in-process — no warehouse access.

Two directions matter equally, and the pair is the actual guarantee:
  - real PII is flagged (the wall works), and
  - the allow-listed opaque keys / aggregate geography are NOT flagged (the wall
    is not so blunt that governed analysis stops working).

`pii.py` documents that adding to `SAFE_ALLOWLIST` is the one way to weaken R6,
so every entry is asserted here by construction rather than by a hand-kept list:
a new allow-list entry cannot be added without this test covering it.

Deferred to a later phase: the wiring test for the query-metric helper and the
author-time "no bundled metric allow-lists a PII column" invariant. Both need
skills/content this template does not have yet (a metric catalog); the denylist
they both lean on is fully covered here.
"""

import pytest

import _shared.spine as spine
from _shared import pii


# Direct PII — must be refused. Every category in PII_PATTERNS is represented.
PII_COLUMNS = [
    "name", "first_name", "last_name", "middle_name", "full_name",
    "customer_name", "buyer_name", "cardholder_name", "shipping_name",
    "billing_name", "recipient_name", "contact_name",
    "email", "email_address", "phone", "phone_number", "mobile_number",
    "ssn", "social_security_number", "tax_id", "tin", "drivers_license",
    "passport_number", "national_id",
    # Prefixed forms. These are the shapes a real warehouse actually uses, and the
    # bare-word patterns above did not catch them: `customer_name` was covered by
    # the name category's explicit prefix list, but `customer_email` was not covered
    # by anything until the contact / government-id / date-of-birth categories
    # gained a generic snake_case prefix. A column called `customer_email` is about
    # as common as a column gets, so this was a real hole rather than a hypothetical.
    "customer_email", "billing_email", "contact_email", "buyer_e_mail",
    "customer_phone", "mobile_phone", "shipping_phone_number",
    "customer_ssn", "hashed_ssn", "ssn_last4", "applicant_tax_id",
    "customer_dob", "insured_date_of_birth", "user_birthday",
    "customer_address", "recipient_address", "purchaser_name",
    "account_number", "bank_account_number", "routing_number", "iban",
    "card_number", "credit_card_number", "cc_number", "cvv",
    "diagnosis", "icd10_code", "medical_record_number", "mrn", "prescription",
    "lab_results",
    "dob", "date_of_birth", "birth_date", "birthdate", "birthday",
    "street", "street_1", "street_address", "address", "address_line_1",
    "mailing_address", "shipping_address", "billing_address", "delivery_address",
]

# Ordinary governed dimensions of the example domain — must pass. These are the
# columns a checkout funnel actually groups by; if the denylist caught one of
# these, the plugin would be unusable.
NEUTRAL_DIMENSIONS = [
    "channel", "payment_method", "market", "category", "device_type",
    "cohort_week", "order_total", "promo_code", "sku", "fulfillment_center",
    "terminal_branch", "final_outcome", "final_outcome_detail",
    "address_type", "destination_market", "is_gift",
    # `*_name` columns that name a THING, not a person. These pin the deliberate
    # asymmetry in `pii.py`: the contact / government-id / date-of-birth categories
    # take a generic snake_case prefix because nothing innocent is called
    # `<x>_email`, while `name` and `address` enumerate person-ish prefixes because
    # plenty of innocent things are called `<x>_name` or `<x>_address`. Widen the
    # name category generically and these fail — which is the point.
    "product_name", "category_name", "carrier_name", "channel_name",
    "warehouse_address", "store_address",
]


@pytest.mark.parametrize("col", PII_COLUMNS)
def test_pii_columns_flagged(col):
    assert pii.is_pii_column(col), f"{col!r} should be flagged as PII"
    assert pii.pii_reason(col) is not None


@pytest.mark.parametrize("col", sorted(pii.SAFE_ALLOWLIST))
def test_every_allowlist_entry_is_unflagged(col):
    """The allow-list is only meaningful if each entry actually survives the
    denylist. Parametrized over the live set, so adding an entry extends this
    test automatically — and a new entry that collides with a PII pattern fails
    here instead of silently shadowing the wall."""
    assert not pii.is_pii_column(col), f"{col!r} is allow-listed but still flagged"


@pytest.mark.parametrize("col", NEUTRAL_DIMENSIONS)
def test_neutral_dimensions_not_flagged(col):
    assert not pii.is_pii_column(col), f"{col!r} should NOT be flagged as PII"


def test_reason_names_the_category():
    """The refusal message names *why*, so a user sees which rule fired."""
    assert pii.pii_reason("cardholder_name") == "name"
    assert pii.pii_reason("email_address") == "contact"
    assert pii.pii_reason("tin") == "government_id"
    assert pii.pii_reason("cvv") == "financial"
    assert pii.pii_reason("icd10") == "health"
    assert pii.pii_reason("dob") == "date_of_birth"
    assert pii.pii_reason("billing_address") == "address"


def test_matching_is_whole_name_and_case_insensitive():
    """Anchored on the whole lowercased name: a short pattern must not fire on a
    longer innocent column, and casing/padding must not be a bypass."""
    assert pii.is_pii_column("  First_Name ")
    assert pii.is_pii_column("EMAIL")
    assert not pii.is_pii_column("destination_market")  # contains "tin"
    assert not pii.is_pii_column("address_type")        # starts with "address"


def test_empty_and_none_are_not_pii():
    for value in (None, "", "   "):
        assert pii.pii_reason(value) is None
        assert not pii.is_pii_column(value)


# --- spine governance: R6 outranks the allow-list ---------------------------

def test_spine_validate_dimension_refuses_pii():
    """Even when a spec explicitly allow-lists it, a PII dimension is refused —
    R6 is checked before the allow-list, so a spec cannot opt in."""
    with pytest.raises(SystemExit):
        spine.validate_dimension({"allowed_dimensions": ["email"]}, "email")


def test_spine_validate_dimension_allows_safe():
    spine.validate_dimension({"allowed_dimensions": ["market"]}, "market")  # must not raise


def test_spine_validate_dimension_none_is_noop():
    spine.validate_dimension({"allowed_dimensions": []}, None)  # must not raise


def test_spine_validate_dimension_refuses_off_allowlist():
    """A non-PII dimension that simply isn't allow-listed is refused too (R2)."""
    with pytest.raises(SystemExit):
        spine.validate_dimension({"allowed_dimensions": ["channel"]}, "market")


def test_spine_validate_filters_refuses_pii():
    with pytest.raises(SystemExit):
        spine.validate_filters({"allowed_filters": ["date_of_birth"]}, {"date_of_birth": "x"})


def test_spine_validate_filters_allows_safe():
    spine.validate_filters({"allowed_filters": ["channel"]}, {"channel": "email"})  # must not raise
