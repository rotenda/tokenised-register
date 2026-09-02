"""
The authoritative register.

This is the legal record. Not the chain.

South African law does not currently recognise an on-chain register as the
authoritative record of title to a security. Switzerland does, under article
973d of its Code of Obligations. South Africa has no equivalent, and the
Financial Markets Act review has not yet resolved the question.

Until it does, an issuer here has one defensible architecture: keep the
statutory register off-chain, treat it as authoritative, and use the chain as a
derived mirror for transparency and transferability. This module is that
register. mirror.py is the chain side.

The practical consequence — and the point of the whole exercise — is that when
the two disagree, the register wins and the chain gets corrected. Never the
reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Iterator

from .events import (
    Classification,
    CorporateActionRecorded,
    CorrectionApplied,
    Event,
    HolderAdmitted,
    HolderReclassified,
    HolderReinstated,
    HolderSuspended,
    NotesIssued,
    NotesRedeemed,
    TransferExecuted,
)
from .restrictions import (
    Decision,
    HolderView,
    ProposedTransfer,
    RegisterView,
    RestrictionEngine,
)


class RegisterError(Exception):
    """Raised where an operation would corrupt the register's integrity."""


@dataclass
class HolderState:
    holder_id: str
    legal_name: str
    classification: Classification
    jurisdiction: str
    suspended: bool = False
    units_held: int = 0
    first_acquired: datetime | None = None

    def view(self) -> HolderView:
        return HolderView(
            holder_id=self.holder_id,
            classification=self.classification,
            suspended=self.suspended,
            units_held=self.units_held,
            first_acquired=self.first_acquired,
        )


@dataclass(frozen=True)
class RejectedTransfer:
    """
    A refused transfer.

    Not a register entry — the register records what happened, and a refused
    transfer did not happen. But refusals are exactly what a supervisor asks
    about, so they are retained separately and are queryable.
    """

    transfer: ProposedTransfer
    reasons: tuple[str, ...]
    at: datetime


class Register:
    """
    Event-sourced securities register.

    State is derived by folding the event log. Positions are never stored as
    the source of truth — they are a projection, rebuilt on demand. This makes
    point-in-time reconstruction free and makes silent corruption of a balance
    structurally impossible: to change a position you must append an event, and
    the event is visible.
    """

    def __init__(
        self,
        instrument_id: str,
        denomination_cents: int,
        restrictions: RestrictionEngine | None = None,
    ):
        if denomination_cents <= 0:
            raise ValueError("denomination must be positive")
        self.instrument_id = instrument_id
        self.denomination_cents = denomination_cents
        self.restrictions = restrictions or RestrictionEngine.private_placement_default()
        self._log: list[Event] = []
        self._rejections: list[RejectedTransfer] = []

    # ---------- log ----------

    @property
    def log(self) -> tuple[Event, ...]:
        return tuple(self._log)

    @property
    def rejections(self) -> tuple[RejectedTransfer, ...]:
        return tuple(self._rejections)

    @property
    def head_seq(self) -> int:
        return len(self._log)

    def _append(self, event: Event) -> Event:
        stamped = replace(event, seq=len(self._log) + 1)
        self._log.append(stamped)
        return stamped

    # ---------- projection ----------

    def _fold(self, up_to_seq: int | None = None) -> dict[str, HolderState]:
        holders: dict[str, HolderState] = {}
        for event in self._log:
            if up_to_seq is not None and event.seq > up_to_seq:
                break
            self._apply_to(holders, event)
        return holders

    @staticmethod
    def _apply_to(holders: dict[str, HolderState], event: Event) -> None:
        if isinstance(event, HolderAdmitted):
            holders[event.holder_id] = HolderState(
                holder_id=event.holder_id,
                legal_name=event.legal_name,
                classification=event.classification,
                jurisdiction=event.jurisdiction,
            )
        elif isinstance(event, HolderSuspended):
            holders[event.holder_id].suspended = True
        elif isinstance(event, HolderReinstated):
            holders[event.holder_id].suspended = False
        elif isinstance(event, HolderReclassified):
            holders[event.holder_id].classification = event.classification
        elif isinstance(event, NotesIssued):
            h = holders[event.holder_id]
            h.units_held += event.units
            if h.first_acquired is None:
                h.first_acquired = event.timestamp
        elif isinstance(event, TransferExecuted):
            src = holders[event.from_holder]
            dst = holders[event.to_holder]
            src.units_held -= event.units
            dst.units_held += event.units
            if dst.first_acquired is None:
                dst.first_acquired = event.timestamp
        elif isinstance(event, NotesRedeemed):
            holders[event.holder_id].units_held -= event.units
        # CorporateActionRecorded and CorrectionApplied do not move positions.

    def positions(self, at_seq: int | None = None) -> dict[str, int]:
        """Units held per holder, optionally as at a past sequence number."""
        return {
            hid: h.units_held
            for hid, h in self._fold(at_seq).items()
            if h.units_held > 0
        }

    def total_in_issue(self, at_seq: int | None = None) -> int:
        return sum(h.units_held for h in self._fold(at_seq).values())

    def holder(self, holder_id: str, at_seq: int | None = None) -> HolderState | None:
        return self._fold(at_seq).get(holder_id)

    def view(self, at_seq: int | None = None) -> RegisterView:
        holders = self._fold(at_seq)
        return RegisterView(
            holders={hid: h.view() for hid, h in holders.items()},
            total_units_in_issue=sum(h.units_held for h in holders.values()),
            denomination_cents=self.denomination_cents,
        )

    # ---------- operations ----------

    def admit_holder(
        self,
        holder_id: str,
        legal_name: str,
        classification: Classification,
        actor: str,
        reason: str,
        jurisdiction: str = "ZA",
    ) -> Event:
        if holder_id in self._fold():
            raise RegisterError(f"holder {holder_id} is already on the register")
        return self._append(
            HolderAdmitted(
                actor=actor,
                reason=reason,
                holder_id=holder_id,
                legal_name=legal_name,
                classification=classification,
                jurisdiction=jurisdiction,
            )
        )

    def suspend_holder(self, holder_id: str, actor: str, reason: str) -> Event:
        self._require_holder(holder_id)
        return self._append(
            HolderSuspended(actor=actor, reason=reason, holder_id=holder_id)
        )

    def reinstate_holder(self, holder_id: str, actor: str, reason: str) -> Event:
        self._require_holder(holder_id)
        return self._append(
            HolderReinstated(actor=actor, reason=reason, holder_id=holder_id)
        )

    def reclassify_holder(
        self, holder_id: str, classification: Classification, actor: str, reason: str
    ) -> Event:
        self._require_holder(holder_id)
        return self._append(
            HolderReclassified(
                actor=actor,
                reason=reason,
                holder_id=holder_id,
                classification=classification,
            )
        )

    def issue(self, holder_id: str, units: int, actor: str, reason: str) -> Event:
        """
        Primary issuance.

        Deliberately does NOT run the transfer restriction engine. Issuance is
        the allotment decision itself — the issuer has already decided who may
        subscribe, on advice, through a subscription process. Running secondary
        transfer rules here would conflate two different legal acts. What is
        enforced is the minimum: the allottee must be eligible to hold.
        """
        self._require_holder(holder_id)
        if units <= 0:
            raise RegisterError("issuance must be a positive number of units")
        holder = self._fold()[holder_id]
        if holder.classification is Classification.UNCLASSIFIED:
            raise RegisterError(
                f"cannot allot to {holder_id}: holder is unclassified"
            )
        if holder.suspended:
            raise RegisterError(f"cannot allot to {holder_id}: holder is suspended")
        return self._append(
            NotesIssued(actor=actor, reason=reason, holder_id=holder_id, units=units)
        )

    def transfer(
        self,
        from_holder: str,
        to_holder: str,
        units: int,
        actor: str,
        reason: str,
        at: datetime | None = None,
    ) -> tuple[Event | None, Decision]:
        """
        Secondary transfer.

        Returns (event, decision). On refusal the event is None, nothing is
        appended to the register, and the attempt is recorded in rejections.
        """
        proposed = ProposedTransfer(
            from_holder=from_holder,
            to_holder=to_holder,
            units=units,
            at=at or datetime.now().astimezone(),
        )
        decision = self.restrictions.evaluate(self.view(), proposed)
        if not decision.allowed:
            self._rejections.append(
                RejectedTransfer(
                    transfer=proposed, reasons=decision.reasons, at=proposed.at
                )
            )
            return None, decision

        event = self._append(
            TransferExecuted(
                actor=actor,
                reason=reason,
                from_holder=from_holder,
                to_holder=to_holder,
                units=units,
            )
        )
        return event, decision

    def redeem(self, holder_id: str, units: int, actor: str, reason: str) -> Event:
        self._require_holder(holder_id)
        held = self._fold()[holder_id].units_held
        if units <= 0 or units > held:
            raise RegisterError(
                f"cannot redeem {units} units from {holder_id} holding {held}"
            )
        return self._append(
            NotesRedeemed(actor=actor, reason=reason, holder_id=holder_id, units=units)
        )

    def record_corporate_action(
        self,
        action_id: str,
        action_type: str,
        record_seq: int,
        total_amount_cents: int,
        actor: str,
        reason: str,
    ) -> Event:
        return self._append(
            CorporateActionRecorded(
                actor=actor,
                reason=reason,
                action_id=action_id,
                action_type=action_type,
                record_seq=record_seq,
                total_amount_cents=total_amount_cents,
            )
        )

    def apply_correction(self, corrects_seq: int, note: str, actor: str) -> Event:
        if not 1 <= corrects_seq <= len(self._log):
            raise RegisterError(f"no entry at sequence {corrects_seq}")
        return self._append(
            CorrectionApplied(
                actor=actor,
                reason="correction",
                corrects_seq=corrects_seq,
                note=note,
            )
        )

    # ---------- integrity ----------

    def _require_holder(self, holder_id: str) -> None:
        if holder_id not in self._fold():
            raise RegisterError(f"holder {holder_id} is not on the register")

    def verify_integrity(self) -> list[str]:
        """
        Self-check.

        Recomputes state from the log and asserts the invariants that must hold
        of any securities register: sequence numbers are contiguous, no position
        is negative, and the sum of positions equals units in issue.
        """
        problems: list[str] = []

        for i, event in enumerate(self._log, start=1):
            if event.seq != i:
                problems.append(f"sequence break at position {i}: found seq {event.seq}")

        holders = self._fold()
        for hid, h in holders.items():
            if h.units_held < 0:
                problems.append(f"holder {hid} has a negative position: {h.units_held}")

        issued = sum(e.units for e in self._log if isinstance(e, NotesIssued))
        redeemed = sum(e.units for e in self._log if isinstance(e, NotesRedeemed))
        expected = issued - redeemed
        actual = sum(h.units_held for h in holders.values())
        if expected != actual:
            problems.append(
                f"units in issue do not reconcile: expected {expected}, holders sum to {actual}"
            )

        return problems

    def statement(self, holder_id: str) -> Iterator[Event]:
        """Every log entry touching a given holder, in order."""
        for event in self._log:
            fields = (
                getattr(event, "holder_id", None),
                getattr(event, "from_holder", None),
                getattr(event, "to_holder", None),
            )
            if holder_id in fields:
                yield event


Iterable  # re-exported for typing convenience
