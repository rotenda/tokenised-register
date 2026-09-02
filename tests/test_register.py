"""
Tests.

Each test corresponds to a claim the design makes. The point is not coverage
for its own sake — it is that the legally interesting properties are the ones
under test: exemption integrity, point-in-time reconstruction, distributions
that sum exactly, and the register winning every disagreement with the chain.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokenised_register import (  # noqa: E402
    Classification,
    DivergenceType,
    InMemoryChain,
    Mirror,
    Register,
    RegisterError,
    RestrictionEngine,
    allocate_pro_rata,
    compute_coupon,
    compute_redemption,
)

DENOM = 100_000_00  # R100,000 per note, in cents
MIN_HOLDING = 1_000_000_00  # R1,000,000 minimum, in cents


def build_register(lock_up_days: int = 0, holder_cap: int = 50) -> Register:
    return Register(
        instrument_id="TESTNOTE-2029",
        denomination_cents=DENOM,
        restrictions=RestrictionEngine.private_placement_default(
            minimum_cents=MIN_HOLDING,
            lock_up_days=lock_up_days,
            holder_cap=holder_cap,
        ),
    )


def seed(register: Register, holders: dict[str, int]) -> None:
    for hid, units in holders.items():
        register.admit_holder(
            hid, f"{hid} (Pty) Ltd", Classification.QUALIFYING_INVESTOR,
            actor="registrar", reason="onboarding complete",
        )
        if units:
            register.issue(hid, units, actor="registrar", reason="primary allotment")


# ---------- core register ----------

def test_positions_derive_from_the_log():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    assert r.positions() == {"INV001": 20, "INV002": 30}
    assert r.total_in_issue() == 50


def test_register_reconstructs_to_any_past_point():
    """The property that makes an event-sourced register worth the trouble."""
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    seq_after_issue = r.head_seq

    r.transfer("INV001", "INV002", 10, actor="registrar", reason="secondary trade")

    assert r.positions() == {"INV001": 10, "INV002": 40}
    assert r.positions(at_seq=seq_after_issue) == {"INV001": 20, "INV002": 30}


def test_nothing_is_ever_mutated_only_appended():
    r = build_register()
    seed(r, {"INV001": 20})
    before = r.log
    r.apply_correction(corrects_seq=2, note="wrong reason code", actor="registrar")
    assert r.log[: len(before)] == before
    assert r.log[-1].corrects_seq == 2


def test_integrity_check_passes_on_a_consistent_register():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    r.transfer("INV001", "INV002", 10, actor="registrar", reason="trade")
    r.redeem("INV002", 5, actor="registrar", reason="partial redemption")
    assert r.verify_integrity() == []


def test_holder_statement_shows_every_touching_entry():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    r.transfer("INV001", "INV002", 10, actor="registrar", reason="trade")
    entries = list(r.statement("INV001"))
    assert len(entries) == 3  # admitted, issued, transferred


# ---------- restrictions: the exemption integrity tests ----------

def test_unclassified_party_cannot_receive_units():
    """The failure that turns a private placement into a public offer."""
    r = build_register()
    seed(r, {"INV001": 20})
    r.admit_holder(
        "RETAIL01", "A Person", Classification.UNCLASSIFIED,
        actor="registrar", reason="enquiry",
    )
    event, decision = r.transfer(
        "INV001", "RETAIL01", 10, actor="registrar", reason="attempted trade"
    )
    assert event is None
    assert not decision.allowed
    assert any("may not hold this instrument" in x for x in decision.reasons)
    assert r.positions() == {"INV001": 20}


def test_transfer_leaving_transferee_below_minimum_is_refused():
    r = build_register()
    seed(r, {"INV001": 20})
    r.admit_holder(
        "INV002", "Two (Pty) Ltd", Classification.QUALIFYING_INVESTOR,
        actor="registrar", reason="onboarded",
    )
    # 9 notes = R900,000, below the R1m minimum
    event, decision = r.transfer("INV001", "INV002", 9, actor="registrar", reason="trade")
    assert event is None
    assert any("below the minimum" in x for x in decision.reasons)


def test_transfer_leaving_transferor_with_a_stub_is_refused():
    """
    The subtle one. A holder cannot sell down to below the minimum and keep a
    remnant — that defeats the exemption as surely as issuing below it.
    """
    r = build_register()
    seed(r, {"INV001": 15, "INV002": 10})
    event, decision = r.transfer("INV001", "INV002", 10, actor="registrar", reason="trade")
    assert event is None
    assert any("would retain 5 units" in x for x in decision.reasons)


def test_transferor_may_exit_completely():
    r = build_register()
    seed(r, {"INV001": 15, "INV002": 10})
    event, decision = r.transfer("INV001", "INV002", 15, actor="registrar", reason="full exit")
    assert decision.allowed
    assert r.positions() == {"INV002": 25}


def test_suspended_holder_cannot_transact():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 20})
    r.suspend_holder("INV001", actor="compliance", reason="due diligence lapsed")
    event, decision = r.transfer("INV001", "INV002", 10, actor="registrar", reason="trade")
    assert event is None
    assert any("suspended" in x for x in decision.reasons)

    r.reinstate_holder("INV001", actor="compliance", reason="due diligence refreshed")
    event, decision = r.transfer("INV001", "INV002", 10, actor="registrar", reason="trade")
    assert decision.allowed


def test_lock_up_blocks_early_transfer_then_releases():
    r = build_register(lock_up_days=90)
    seed(r, {"INV001": 20, "INV002": 20})

    early = datetime.now(timezone.utc) + timedelta(days=10)
    event, decision = r.transfer(
        "INV001", "INV002", 10, actor="registrar", reason="trade", at=early
    )
    assert event is None
    assert any("lock-up" in x for x in decision.reasons)

    later = datetime.now(timezone.utc) + timedelta(days=100)
    event, decision = r.transfer(
        "INV001", "INV002", 10, actor="registrar", reason="trade", at=later
    )
    assert decision.allowed


def test_holder_cap_blocks_a_new_position_but_allows_a_swap():
    r = build_register(holder_cap=2)
    seed(r, {"INV001": 20, "INV002": 20})
    r.admit_holder(
        "INV003", "Three (Pty) Ltd", Classification.QUALIFYING_INVESTOR,
        actor="registrar", reason="onboarded",
    )

    event, decision = r.transfer("INV001", "INV003", 10, actor="registrar", reason="trade")
    assert event is None
    assert any("above the cap" in x for x in decision.reasons)

    # A full exit keeps the count constant, so it passes.
    event, decision = r.transfer("INV001", "INV003", 20, actor="registrar", reason="full exit")
    assert decision.allowed


def test_all_refusal_reasons_are_returned_not_just_the_first():
    r = build_register()
    seed(r, {"INV001": 5})
    r.admit_holder(
        "RETAIL01", "A Person", Classification.UNCLASSIFIED,
        actor="registrar", reason="enquiry",
    )
    event, decision = r.transfer("INV001", "RETAIL01", 99, actor="registrar", reason="trade")
    assert event is None
    assert len(decision.reasons) >= 2


def test_refused_transfers_are_retained_but_not_in_the_register():
    r = build_register()
    seed(r, {"INV001": 20})
    r.admit_holder(
        "RETAIL01", "A Person", Classification.UNCLASSIFIED,
        actor="registrar", reason="enquiry",
    )
    seq_before = r.head_seq
    r.transfer("INV001", "RETAIL01", 10, actor="registrar", reason="trade")
    assert r.head_seq == seq_before          # register untouched
    assert len(r.rejections) == 1            # but the attempt is on file


def test_cannot_allot_to_an_unclassified_holder():
    r = build_register()
    r.admit_holder(
        "RETAIL01", "A Person", Classification.UNCLASSIFIED,
        actor="registrar", reason="enquiry",
    )
    with pytest.raises(RegisterError, match="unclassified"):
        r.issue("RETAIL01", 10, actor="registrar", reason="allotment")


# ---------- corporate actions ----------

def test_allocation_always_sums_to_the_total():
    """Money does not divide evenly. The parts must still sum to the whole."""
    positions = {"A": 7, "B": 11, "C": 13}
    total = 100_000_01  # deliberately awkward
    allocations = allocate_pro_rata(positions, total)
    assert sum(a.amount_cents for a in allocations) == total


def test_allocation_is_deterministic():
    positions = {"A": 7, "B": 11, "C": 13}
    first = allocate_pro_rata(positions, 100_000_01)
    second = allocate_pro_rata({"C": 13, "A": 7, "B": 11}, 100_000_01)
    assert first == second


def test_coupon_computed_at_a_past_record_point():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    record_seq = r.head_seq

    r.transfer("INV001", "INV002", 10, actor="registrar", reason="post-record trade")

    dist = compute_coupon(
        r, "CPN-2026-01", annual_rate=Decimal("0.145"), days=91, record_seq=record_seq
    )
    by_holder = {a.holder_id: a.units for a in dist.allocations}
    assert by_holder == {"INV001": 20, "INV002": 30}  # pre-transfer holdings
    assert dist.check_sums()


def test_redemption_returns_full_nominal():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    dist = compute_redemption(r, "RED-2029")
    assert dist.total_amount_cents == 50 * DENOM
    assert dist.check_sums()


# ---------- the mirror: the central claim ----------

def test_publish_then_reconcile_shows_in_sync():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")
    mirror.bind("INV002", "GADDR002")

    mirror.publish(r)
    report = mirror.reconcile(r)
    assert report.in_sync
    assert report.chain_total == report.register_total == 50


def test_dropped_write_is_detected_and_repaired():
    """A publish fails silently. Reconciliation must catch it."""
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")
    mirror.bind("INV002", "GADDR002")

    chain.drop_next_write = True
    mirror.publish(r)

    report = mirror.reconcile(r)
    assert not report.in_sync
    assert any(d.kind is DivergenceType.MISSING_ON_CHAIN for d in report.divergences)

    mirror.repair(r, report)
    assert mirror.reconcile(r).in_sync


def test_unknown_on_chain_holder_is_critical_and_frozen():
    """
    The compliance incident that matters: units on an address with no register
    position. Someone unqualified may be holding.
    """
    r = build_register()
    seed(r, {"INV001": 20})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")
    mirror.publish(r)

    chain.inject_unknown_holder("GROGUE99", 5)

    report = mirror.reconcile(r)
    assert not report.in_sync
    assert any(d.kind is DivergenceType.UNKNOWN_ON_CHAIN for d in report.divergences)
    assert len(report.critical) == 1

    mirror.repair(r, report)
    assert "GROGUE99" in chain.frozen


def test_register_is_never_adjusted_to_match_the_chain():
    """The whole architecture in one assertion."""
    r = build_register()
    seed(r, {"INV001": 20})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")
    mirror.publish(r)

    chain.inject_unknown_holder("GROGUE99", 5)
    positions_before = r.positions()
    seq_before = r.head_seq

    report = mirror.reconcile(r)
    mirror.repair(r, report)

    assert r.positions() == positions_before
    assert r.head_seq == seq_before


def test_holder_without_a_bound_address_is_flagged_not_silently_skipped():
    r = build_register()
    seed(r, {"INV001": 20, "INV002": 30})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")  # INV002 deliberately unbound

    mirror.publish(r)
    report = mirror.reconcile(r)
    assert any(d.kind is DivergenceType.NOT_MIRRORED for d in report.divergences)


def test_publish_is_idempotent():
    r = build_register()
    seed(r, {"INV001": 20})
    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    mirror.bind("INV001", "GADDR001")

    mirror.publish(r)
    mirror.publish(r)
    assert mirror.reconcile(r).in_sync
    assert chain.balances()["GADDR001"] == 20
