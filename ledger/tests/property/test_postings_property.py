from __future__ import annotations

import uuid
from decimal import Decimal

import hypothesis.strategies as st
from hypothesis import given

from ledger.domain.double_entry import EntrySpec, validate_entries


@given(
    amt=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000"), places=2),
)
def test_balanced_two_postings(amt: Decimal):
    a = uuid.uuid4()
    b = uuid.uuid4()
    entries = [
        EntrySpec(account_id=a, amount=amt, ccy="USD"),
        EntrySpec(account_id=b, amount=-amt, ccy="USD"),
    ]
    validate_entries(entries)


@given(
    amounts=st.lists(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2), min_size=2, max_size=6),
)
def test_property_balanced_sum_accepts(amounts):
    # Construct a balanced set by making the last element the negative sum of previous
    accs = [uuid.uuid4() for _ in range(len(amounts) + 1)]
    total = sum(amounts, Decimal(0))
    entries = [EntrySpec(account_id=accs[i], amount=a, ccy="USD") for i, a in enumerate(amounts)]
    entries.append(EntrySpec(account_id=accs[-1], amount=-total, ccy="USD"))
    validate_entries(entries)


@given(
    amounts=st.lists(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2), min_size=2, max_size=6),
)
def test_property_unbalanced_sum_rejects(amounts):
    accs = [uuid.uuid4() for _ in range(len(amounts))]
    entries = [EntrySpec(account_id=accs[i], amount=a, ccy="USD") for i, a in enumerate(amounts)]
    # If by chance the set sums to zero (unlikely), tweak the last by +0.01
    total = sum((e.amount for e in entries), Decimal(0))
    if total == 0:
        entries[-1] = EntrySpec(account_id=entries[-1].account_id, amount=entries[-1].amount + Decimal("0.01"), ccy="USD")
    from pytest import raises
    with raises(ValueError):
        validate_entries(entries)
