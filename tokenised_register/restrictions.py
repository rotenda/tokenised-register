"""
Transfer restrictions.

This module is the reason the whole thing exists.

A tokenised note that anyone can hold is a different legal instrument from one
only qualifying investors can hold. The restrictions are not a product feature —
they are what keeps the instrument inside the exemption it was issued under. If
they fail, the issuer may have made an offer to the public without a registered
prospectus, and no amount of good intent afterwards repairs that.

Design decisions worth noting:

1. Restrictions are evaluated BEFORE the event is appended. A rejected transfer
   never enters the register. This is the opposite of the common on-chain
   pattern where a transaction settles and compliance is assessed afterwards.

2. Every rule returns a reason on rejection. "Transfer failed" is useless to a
   holder and useless to a supervisor. The reason is part of the output.

3. Rules are evaluated in order and ALL are evaluated, not short-circuited, so
   a caller can see every reason a transfer was refused rather than only the
   first. Fixing one and resubmitting only to hit the next is a bad experience
   and a worse audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

from .events import Classification


@dataclass(frozen=True)
class ProposedTransfer:
    from_holder: str
    to_holder: str
    units: int
    at: datetime


@dataclass(frozen=True)
class HolderView:
    """Read-only snapshot of a holder as the restriction engine sees them."""

    holder_id: str
    classification: Classification
    suspended: bool
    units_held: int
    first_acquired: datetime | None


@dataclass(frozen=True)
class RegisterView:
    """Read-only snapshot of register state for restriction evaluation."""

    holders: dict[str, HolderView]
    total_units_in_issue: int
    denomination_cents: int

    def holder(self, holder_id: str) -> HolderView | None:
        return self.holders.get(holder_id)

    def active_holder_count(self) -> int:
        return sum(1 for h in self.holders.values() if h.units_held > 0)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def ok(cls) -> "Decision":
        return cls(True, ())

    @classmethod
    def refuse(cls, *reasons: str) -> "Decision":
        return cls(False, tuple(reasons))


class Rule(Protocol):
    name: str

    def check(self, view: RegisterView, transfer: ProposedTransfer) -> Decision: ...


@dataclass(frozen=True)
class BothPartiesKnown:
    """Transfers to or from unknown parties are refused outright."""

    name: str = "both_parties_known"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        problems = []
        if view.holder(t.from_holder) is None:
            problems.append(f"transferor {t.from_holder} is not on the register")
        if view.holder(t.to_holder) is None:
            problems.append(
                f"transferee {t.to_holder} is not on the register; "
                "a holder must be admitted before receiving units"
            )
        return Decision.ok() if not problems else Decision.refuse(*problems)


@dataclass(frozen=True)
class NeitherPartySuspended:
    name: str = "neither_party_suspended"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        problems = []
        for role, hid in (("transferor", t.from_holder), ("transferee", t.to_holder)):
            h = view.holder(hid)
            if h is not None and h.suspended:
                problems.append(f"{role} {hid} is suspended")
        return Decision.ok() if not problems else Decision.refuse(*problems)


@dataclass(frozen=True)
class SufficientUnits:
    name: str = "sufficient_units"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        if t.units <= 0:
            return Decision.refuse("transfer size must be a positive number of units")
        h = view.holder(t.from_holder)
        if h is None:
            return Decision.ok()  # BothPartiesKnown reports this
        if h.units_held < t.units:
            return Decision.refuse(
                f"transferor holds {h.units_held} units, cannot transfer {t.units}"
            )
        return Decision.ok()


@dataclass(frozen=True)
class EligibleTransfereeOnly:
    """
    Only classified investors may receive units.

    This is the rule that keeps the instrument inside its exemption. An
    unclassified party receiving units is the failure mode that turns a private
    placement into a public offer.
    """

    permitted: frozenset[Classification] = frozenset(
        {
            Classification.QUALIFYING_INVESTOR,
            Classification.SECURITIES_DEALER,
            Classification.ISSUER_TREASURY,
        }
    )
    name: str = "eligible_transferee_only"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        h = view.holder(t.to_holder)
        if h is None:
            return Decision.ok()
        if h.classification not in self.permitted:
            return Decision.refuse(
                f"transferee {t.to_holder} is classified "
                f"'{h.classification.value}' and may not hold this instrument"
            )
        return Decision.ok()


@dataclass(frozen=True)
class MinimumHolding:
    """
    Enforces a minimum holding value per holder.

    Where an instrument relies on a minimum-subscription exemption, the
    threshold has to survive secondary trading. A holder who acquires at the
    threshold and then sells down to a fraction of it defeats the exemption
    just as effectively as issuing below it in the first place.

    So the rule is applied to BOTH sides: the transferee must end at or above
    the minimum, and the transferor must either end at or above it, or exit
    entirely. Partial exits leaving a stub below the threshold are refused.
    """

    minimum_cents: int = 1_000_000_00
    name: str = "minimum_holding"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        problems = []
        denom = view.denomination_cents
        min_units = -(-self.minimum_cents // denom)  # ceiling division

        to_h = view.holder(t.to_holder)
        if to_h is not None:
            resulting = to_h.units_held + t.units
            if resulting < min_units:
                problems.append(
                    f"transferee would hold {resulting} units, below the "
                    f"minimum of {min_units}"
                )

        from_h = view.holder(t.from_holder)
        if from_h is not None:
            remaining = from_h.units_held - t.units
            if 0 < remaining < min_units:
                problems.append(
                    f"transferor would retain {remaining} units, below the "
                    f"minimum of {min_units}; transfer the full holding or "
                    "retain at least the minimum"
                )

        return Decision.ok() if not problems else Decision.refuse(*problems)


@dataclass(frozen=True)
class LockUp:
    """
    No transfer within a set period of first acquisition.

    Included because it is common in private placements and because it
    demonstrates a restriction that depends on register history rather than
    current state — the kind of rule that is trivial with an event log and
    painful without one.
    """

    days: int = 90
    name: str = "lock_up"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        h = view.holder(t.from_holder)
        if h is None or h.first_acquired is None:
            return Decision.ok()
        unlock = h.first_acquired + timedelta(days=self.days)
        if t.at < unlock:
            return Decision.refuse(
                f"transferor is within the {self.days}-day lock-up; "
                f"units become transferable on {unlock.date().isoformat()}"
            )
        return Decision.ok()


@dataclass(frozen=True)
class MaximumHolders:
    """
    Caps the number of holders on the register.

    Where an exemption depends on an offer not being made to the public, an
    uncontrolled expansion of the holder base is a risk in itself. The cap is
    checked only where the transfer would admit a genuinely new position.
    """

    cap: int = 50
    name: str = "maximum_holders"

    def check(self, view: RegisterView, t: ProposedTransfer) -> Decision:
        to_h = view.holder(t.to_holder)
        from_h = view.holder(t.from_holder)
        if to_h is None or to_h.units_held > 0:
            return Decision.ok()  # not a new position

        prospective = view.active_holder_count() + 1
        if from_h is not None and from_h.units_held == t.units:
            prospective -= 1  # transferor exits entirely

        if prospective > self.cap:
            return Decision.refuse(
                f"transfer would take the register to {prospective} holders, "
                f"above the cap of {self.cap}"
            )
        return Decision.ok()


class RestrictionEngine:
    """
    Evaluates all rules and aggregates the reasons.

    Deliberately evaluates every rule rather than stopping at the first refusal,
    so a rejected transfer comes back with the complete picture.
    """

    def __init__(self, rules: Sequence[Rule]):
        self.rules = tuple(rules)

    def evaluate(self, view: RegisterView, transfer: ProposedTransfer) -> Decision:
        reasons: list[str] = []
        for rule in self.rules:
            decision = rule.check(view, transfer)
            if not decision.allowed:
                reasons.extend(decision.reasons)
        return Decision.ok() if not reasons else Decision.refuse(*reasons)

    @classmethod
    def private_placement_default(
        cls,
        minimum_cents: int = 1_000_000_00,
        lock_up_days: int = 90,
        holder_cap: int = 50,
    ) -> "RestrictionEngine":
        return cls(
            [
                BothPartiesKnown(),
                NeitherPartySuspended(),
                SufficientUnits(),
                EligibleTransfereeOnly(),
                MinimumHolding(minimum_cents=minimum_cents),
                LockUp(days=lock_up_days),
                MaximumHolders(cap=holder_cap),
            ]
        )


timezone  # imported for callers constructing aware datetimes
