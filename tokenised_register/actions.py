"""
Corporate actions.

Two things matter here and both are about reproducibility.

First, a distribution is computed against the register as it stood at a record
point, not as it stands now. Because the register is event-sourced, the record
point is just a sequence number, and the computation can be re-run identically
years later from the log alone. That is the audit property that makes an
event-sourced register worth the trouble.

Second, money does not divide evenly. A coupon split across holders in
proportion to units will almost always leave a remainder of a few cents. Any
implementation that ignores this either loses money or creates it. Both are
findings in an audit. The allocation below distributes to whole cents and then
assigns the remainder deterministically, so the parts always sum exactly to the
whole and the same inputs always produce the same output.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .register import Register


@dataclass(frozen=True)
class Allocation:
    holder_id: str
    units: int
    amount_cents: int

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_cents) / 100


@dataclass(frozen=True)
class Distribution:
    action_id: str
    action_type: str
    record_seq: int
    total_amount_cents: int
    allocations: tuple[Allocation, ...]

    @property
    def total(self) -> Decimal:
        return Decimal(self.total_amount_cents) / 100

    def check_sums(self) -> bool:
        return sum(a.amount_cents for a in self.allocations) == self.total_amount_cents


def allocate_pro_rata(
    positions: dict[str, int], total_amount_cents: int
) -> tuple[Allocation, ...]:
    """
    Split an amount across holders in proportion to units held.

    Uses the largest-remainder method: floor every allocation, then hand the
    leftover cents one at a time to the holders with the largest fractional
    parts. Ties break on holder_id so the result is deterministic regardless of
    dict ordering.

    The parts always sum exactly to the total. That is the invariant.
    """
    total_units = sum(positions.values())
    if total_units == 0:
        return ()
    if total_amount_cents < 0:
        raise ValueError("distribution amount cannot be negative")

    exact = {
        hid: Decimal(total_amount_cents) * Decimal(units) / Decimal(total_units)
        for hid, units in positions.items()
    }
    floored = {hid: int(v) for hid, v in exact.items()}
    remainder = total_amount_cents - sum(floored.values())

    ranked = sorted(
        exact.items(),
        key=lambda kv: (-(kv[1] - int(kv[1])), kv[0]),
    )
    for i in range(remainder):
        hid = ranked[i % len(ranked)][0]
        floored[hid] += 1

    return tuple(
        Allocation(holder_id=hid, units=positions[hid], amount_cents=floored[hid])
        for hid in sorted(positions)
    )


def compute_coupon(
    register: Register,
    action_id: str,
    annual_rate: Decimal,
    days: int,
    day_count: int = 365,
    record_seq: int | None = None,
) -> Distribution:
    """
    Compute a periodic coupon on the outstanding nominal.

    Simple actual/365 accrual, which is the common convention for floating-rate
    ZAR instruments. Rounds the total to whole cents once, then allocates.
    Rounding once at the top and distributing the remainder is what keeps the
    parts summing to the whole.
    """
    seq = record_seq if record_seq is not None else register.head_seq
    positions = register.positions(at_seq=seq)
    units = sum(positions.values())
    nominal_cents = units * register.denomination_cents

    total = (
        Decimal(nominal_cents) * annual_rate * Decimal(days) / Decimal(day_count)
    )
    total_cents = int(total.quantize(Decimal("1")))

    return Distribution(
        action_id=action_id,
        action_type="coupon",
        record_seq=seq,
        total_amount_cents=total_cents,
        allocations=allocate_pro_rata(positions, total_cents),
    )


def compute_redemption(
    register: Register, action_id: str, record_seq: int | None = None
) -> Distribution:
    """Full redemption of nominal at maturity."""
    seq = record_seq if record_seq is not None else register.head_seq
    positions = register.positions(at_seq=seq)
    denom = register.denomination_cents
    allocations = tuple(
        Allocation(holder_id=hid, units=units, amount_cents=units * denom)
        for hid, units in sorted(positions.items())
    )
    return Distribution(
        action_id=action_id,
        action_type="redemption",
        record_seq=seq,
        total_amount_cents=sum(a.amount_cents for a in allocations),
        allocations=allocations,
    )
