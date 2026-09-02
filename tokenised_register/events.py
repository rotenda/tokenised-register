"""
Register events.

The register is event-sourced: the event log is the record, and positions are
derived from it. This is a deliberate legal choice, not a technical preference.

A statutory securities register must be reconstructible to any point in time —
you must be able to answer "who held what on 15 March" long after the fact, and
show how you know. A mutable table of current balances cannot answer that
question. An append-only log can answer it by construction.

Events are immutable. Nothing is ever deleted or edited. A mistake is corrected
by appending a compensating event that references the original, so the error and
its correction both remain visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional


class Classification(str, Enum):
    """
    Investor classification.

    Drives transfer restrictions. Under the Companies Act 71 of 2008 an offer is
    not an offer to the public where it falls within the section 96 exemptions —
    for example where the addressee's ordinary business involves dealing in
    securities, or where the acquisition cost per single addressee acting as
    principal meets the prescribed threshold.

    The register does not decide legal status. It records the classification a
    human has determined and approved, and then enforces the consequences
    consistently. The determination is a legal act; the enforcement is code.
    """

    QUALIFYING_INVESTOR = "qualifying_investor"
    SECURITIES_DEALER = "securities_dealer"
    ISSUER_TREASURY = "issuer_treasury"
    UNCLASSIFIED = "unclassified"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    """
    Base event.

    seq is assigned by the register on append and is the authoritative ordering.
    Wall-clock time is recorded but never relied on for ordering — clocks drift,
    sequence numbers do not.

    actor and reason exist because a register entry that cannot say who made it
    and why is not an audit trail. Every mutation carries both.
    """

    actor: str
    reason: str
    seq: int = field(default=0, compare=False)
    timestamp: datetime = field(default_factory=_now, compare=False)

    def describe(self) -> str:
        return self.__class__.__name__


@dataclass(frozen=True)
class HolderAdmitted(Event):
    holder_id: str = ""
    legal_name: str = ""
    classification: Classification = Classification.UNCLASSIFIED
    jurisdiction: str = "ZA"

    def describe(self) -> str:
        return f"Admitted {self.holder_id} ({self.classification.value})"


@dataclass(frozen=True)
class HolderSuspended(Event):
    """
    Suspension freezes a holder's ability to transact without erasing their
    position. Used where, for example, periodic due diligence has lapsed.
    """

    holder_id: str = ""

    def describe(self) -> str:
        return f"Suspended {self.holder_id}"


@dataclass(frozen=True)
class HolderReinstated(Event):
    holder_id: str = ""

    def describe(self) -> str:
        return f"Reinstated {self.holder_id}"


@dataclass(frozen=True)
class HolderReclassified(Event):
    holder_id: str = ""
    classification: Classification = Classification.UNCLASSIFIED

    def describe(self) -> str:
        return f"Reclassified {self.holder_id} -> {self.classification.value}"


@dataclass(frozen=True)
class NotesIssued(Event):
    """Primary issuance. Units are created and allotted to a holder."""

    holder_id: str = ""
    units: int = 0

    def describe(self) -> str:
        return f"Issued {self.units} to {self.holder_id}"


@dataclass(frozen=True)
class TransferExecuted(Event):
    """
    Secondary transfer between holders.

    Only ever appended after the restriction engine has approved it. The register
    does not hold rejected transfers — a rejection is not a register entry, it is
    a refusal to make one. Rejections are logged separately by the caller.
    """

    from_holder: str = ""
    to_holder: str = ""
    units: int = 0

    def describe(self) -> str:
        return f"Transferred {self.units} from {self.from_holder} to {self.to_holder}"


@dataclass(frozen=True)
class NotesRedeemed(Event):
    """Redemption at maturity or on early call. Units are extinguished."""

    holder_id: str = ""
    units: int = 0

    def describe(self) -> str:
        return f"Redeemed {self.units} from {self.holder_id}"


@dataclass(frozen=True)
class CorporateActionRecorded(Event):
    """
    Records that a corporate action was computed against a specific register
    state. Stores the sequence number used as the record point so the
    computation can be reproduced exactly, years later, from the log alone.
    """

    action_id: str = ""
    action_type: str = ""
    record_seq: int = 0
    total_amount_cents: int = 0

    def describe(self) -> str:
        amount = Decimal(self.total_amount_cents) / 100
        return f"{self.action_type} {self.action_id}: R{amount:,.2f} at seq {self.record_seq}"


@dataclass(frozen=True)
class CorrectionApplied(Event):
    """
    A compensating entry.

    Nothing in the log is ever mutated. Where an entry was made in error, this
    event records the correction and points at the sequence number of the entry
    being corrected. Both remain permanently visible.
    """

    corrects_seq: int = 0
    note: str = ""

    def describe(self) -> str:
        return f"Correction to seq {self.corrects_seq}: {self.note}"


Optional  # re-exported for callers that annotate against it
