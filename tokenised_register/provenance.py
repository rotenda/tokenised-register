"""
Provenance: the same architecture, applied to physical custody.

Written to test a claim — that the register design is not really about
securities. Strip out the securities vocabulary and what remains is: an
authoritative event-sourced record, restrictions evaluated before commit, and
reconciliation against an external view. Those apply to any asset where who
holds what, and how it got there, has legal consequences.

Here the asset is a mineral lot moving from mine to export, under the kind of
due diligence expectations found in the OECD Guidance for minerals from
conflict-affected and high-risk areas, and increasingly demanded by EU buyers.

The difference from the securities case is the interesting part, and it is not
a technical one:

    A securities register is self-contained. The units exist because the
    register says they do. Nothing outside the register can contradict it.

    A provenance register makes claims about the physical world. The lot
    either is or is not 400 tonnes of the stated grade, and the register has
    no way to know.

That gap is the oracle problem, and no ledger design closes it. What a register
can do is make the gap explicit: record who attested, when, to what, and refuse
to let a claim travel further than its attestation supports. That is what the
restrictions below do. It is a smaller promise than "tamper-proof provenance",
and it is the honest one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, Sequence


class Role(str, Enum):
    MINE = "mine"
    PROCESSOR = "processor"
    SMELTER = "smelter"
    TRANSPORTER = "transporter"
    TRADER = "trader"
    EXPORTER = "exporter"


class DueDiligenceStatus(str, Enum):
    """
    Whether a participant has a current third-party due diligence assessment.

    LAPSED is deliberately distinct from NONE. A counterparty who was assessed
    and has let it expire is a different risk from one never assessed, and
    collapsing the two loses information a compliance team needs.
    """

    CURRENT = "current"
    LAPSED = "lapsed"
    NONE = "none"
    SUSPENDED = "suspended"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- events ----------


@dataclass(frozen=True)
class ProvenanceEvent:
    actor: str
    reason: str
    seq: int = field(default=0, compare=False)
    timestamp: datetime = field(default_factory=_now, compare=False)

    def describe(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class ParticipantRegistered(ProvenanceEvent):
    participant_id: str = ""
    legal_name: str = ""
    role: Role = Role.TRADER
    country: str = "ZA"

    def describe(self) -> str:
        return f"Registered {self.participant_id} ({self.role.value}, {self.country})"


@dataclass(frozen=True)
class DueDiligenceAssessed(ProvenanceEvent):
    """
    Records a third-party assessment and when it expires.

    Expiry is the point. An attestation without a validity period is a claim
    about the past being used as a claim about the present.
    """

    participant_id: str = ""
    status: DueDiligenceStatus = DueDiligenceStatus.NONE
    assessor: str = ""
    valid_until: datetime | None = None

    def describe(self) -> str:
        until = self.valid_until.date().isoformat() if self.valid_until else "n/a"
        return f"DD {self.participant_id}: {self.status.value} by {self.assessor} until {until}"


@dataclass(frozen=True)
class LotDeclared(ProvenanceEvent):
    """Origin declaration. The root of the custody chain, and its weakest link."""

    lot_id: str = ""
    participant_id: str = ""
    mineral: str = ""
    mass_kg: int = 0
    mine_site: str = ""
    country_of_origin: str = "ZA"

    def describe(self) -> str:
        return f"Declared {self.lot_id}: {self.mass_kg}kg {self.mineral} from {self.mine_site}"


@dataclass(frozen=True)
class AssayRecorded(ProvenanceEvent):
    """An independent measurement of grade, with the laboratory named."""

    lot_id: str = ""
    laboratory: str = ""
    grade_ppm: int = 0
    mass_kg: int = 0

    def describe(self) -> str:
        return f"Assay {self.lot_id}: {self.grade_ppm}ppm, {self.mass_kg}kg by {self.laboratory}"


@dataclass(frozen=True)
class CustodyTransferred(ProvenanceEvent):
    lot_id: str = ""
    from_participant: str = ""
    to_participant: str = ""
    mass_kg: int = 0

    def describe(self) -> str:
        return (
            f"Custody {self.lot_id}: {self.from_participant} -> "
            f"{self.to_participant} ({self.mass_kg}kg)"
        )


@dataclass(frozen=True)
class MassReconciled(ProvenanceEvent):
    """
    Records a discrepancy between declared and received mass.

    Losses are normal — moisture, handling, processing yield. Recording them
    rather than silently adjusting is what makes an implausible loss visible.
    """

    lot_id: str = ""
    expected_kg: int = 0
    actual_kg: int = 0
    explanation: str = ""

    @property
    def variance_kg(self) -> int:
        return self.actual_kg - self.expected_kg

    def describe(self) -> str:
        return f"Reconciled {self.lot_id}: {self.variance_kg:+d}kg — {self.explanation}"


@dataclass(frozen=True)
class LotExported(ProvenanceEvent):
    lot_id: str = ""
    participant_id: str = ""
    destination_country: str = ""

    def describe(self) -> str:
        return f"Exported {self.lot_id} to {self.destination_country}"


# ---------- state ----------


@dataclass
class Participant:
    participant_id: str
    legal_name: str
    role: Role
    country: str
    dd_status: DueDiligenceStatus = DueDiligenceStatus.NONE
    dd_valid_until: datetime | None = None
    dd_assessor: str = ""

    def dd_current_at(self, when: datetime) -> bool:
        if self.dd_status is not DueDiligenceStatus.CURRENT:
            return False
        return self.dd_valid_until is not None and when < self.dd_valid_until


@dataclass
class Lot:
    lot_id: str
    mineral: str
    mass_kg: int
    mine_site: str
    country_of_origin: str
    holder: str
    custody_depth: int = 0
    last_assay: datetime | None = None
    assay_grade_ppm: int | None = None
    laboratory: str = ""
    exported: bool = False


@dataclass(frozen=True)
class ProposedTransfer:
    lot_id: str
    from_participant: str
    to_participant: str
    mass_kg: int
    at: datetime


@dataclass(frozen=True)
class ProvenanceView:
    participants: dict[str, Participant]
    lots: dict[str, Lot]


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


# ---------- restrictions ----------


class Rule(Protocol):
    name: str

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision: ...


@dataclass(frozen=True)
class BothPartiesRegistered:
    name: str = "both_parties_registered"

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        problems = []
        if t.from_participant not in view.participants:
            problems.append(f"transferor {t.from_participant} is not registered")
        if t.to_participant not in view.participants:
            problems.append(f"transferee {t.to_participant} is not registered")
        if t.lot_id not in view.lots:
            problems.append(f"lot {t.lot_id} has not been declared")
        return Decision.ok() if not problems else Decision.refuse(*problems)


@dataclass(frozen=True)
class CounterpartyDueDiligenceCurrent:
    """
    Both parties must hold a current assessment at the time of transfer.

    This is the rule that carries the compliance weight. It is also the one
    that will refuse the most transfers in practice, because assessments lapse
    quietly and nobody notices until something is blocked.
    """

    name: str = "counterparty_due_diligence_current"

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        problems = []
        for role, pid in (("transferor", t.from_participant), ("transferee", t.to_participant)):
            p = view.participants.get(pid)
            if p is None:
                continue
            if p.dd_status is DueDiligenceStatus.SUSPENDED:
                problems.append(f"{role} {pid} is suspended")
            elif not p.dd_current_at(t.at):
                if p.dd_status is DueDiligenceStatus.CURRENT:
                    problems.append(
                        f"{role} {pid} has a lapsed assessment "
                        f"(expired {p.dd_valid_until.date().isoformat()})"
                    )
                else:
                    problems.append(
                        f"{role} {pid} has no current due diligence assessment "
                        f"(status: {p.dd_status.value})"
                    )
        return Decision.ok() if not problems else Decision.refuse(*problems)


@dataclass(frozen=True)
class AssayNotStale:
    """
    An assay older than the permitted window no longer supports a claim.

    The register cannot verify the lot's grade. What it can do is refuse to let
    an old measurement travel indefinitely as though it were current.
    """

    max_age_days: int = 180
    name: str = "assay_not_stale"

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        lot = view.lots.get(t.lot_id)
        if lot is None:
            return Decision.ok()
        if lot.last_assay is None:
            return Decision.refuse(f"lot {t.lot_id} has no recorded assay")
        age = (t.at - lot.last_assay).days
        if age > self.max_age_days:
            return Decision.refuse(
                f"assay for lot {t.lot_id} is {age} days old, exceeding the "
                f"{self.max_age_days}-day limit"
            )
        return Decision.ok()


@dataclass(frozen=True)
class MassConservation:
    """
    Mass transferred cannot exceed mass held, and losses beyond a tolerance
    must be explained rather than absorbed.
    """

    tolerance_pct: float = 2.0
    name: str = "mass_conservation"

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        lot = view.lots.get(t.lot_id)
        if lot is None:
            return Decision.ok()
        if t.mass_kg > lot.mass_kg:
            return Decision.refuse(
                f"cannot transfer {t.mass_kg}kg from a lot holding {lot.mass_kg}kg"
            )
        shortfall = lot.mass_kg - t.mass_kg
        if shortfall > 0:
            pct = 100.0 * shortfall / lot.mass_kg
            if pct > self.tolerance_pct:
                return Decision.refuse(
                    f"unexplained mass loss of {shortfall}kg ({pct:.1f}%) exceeds "
                    f"the {self.tolerance_pct}% tolerance; record a reconciliation "
                    "with an explanation first"
                )
        return Decision.ok()


@dataclass(frozen=True)
class CustodyHeldByTransferor:
    name: str = "custody_held_by_transferor"

    def check(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        lot = view.lots.get(t.lot_id)
        if lot is None:
            return Decision.ok()
        if lot.exported:
            return Decision.refuse(f"lot {t.lot_id} has already been exported")
        if lot.holder != t.from_participant:
            return Decision.refuse(
                f"lot {t.lot_id} is held by {lot.holder}, not {t.from_participant}"
            )
        return Decision.ok()


class ProvenanceRestrictions:
    def __init__(self, rules: Sequence[Rule]):
        self.rules = tuple(rules)

    def evaluate(self, view: ProvenanceView, t: ProposedTransfer) -> Decision:
        reasons: list[str] = []
        for rule in self.rules:
            d = rule.check(view, t)
            if not d.allowed:
                reasons.extend(d.reasons)
        return Decision.ok() if not reasons else Decision.refuse(*reasons)

    @classmethod
    def oecd_default(cls, assay_max_age_days: int = 180) -> "ProvenanceRestrictions":
        return cls(
            [
                BothPartiesRegistered(),
                CustodyHeldByTransferor(),
                CounterpartyDueDiligenceCurrent(),
                AssayNotStale(max_age_days=assay_max_age_days),
                MassConservation(),
            ]
        )


# ---------- the register ----------


class ProvenanceError(Exception):
    pass


@dataclass(frozen=True)
class RejectedTransfer:
    transfer: ProposedTransfer
    reasons: tuple[str, ...]
    at: datetime


class ProvenanceRegister:
    """
    Same shape as the securities register: append-only log, derived state,
    restrictions before commit, point-in-time reconstruction.

    The vocabulary changed. The architecture did not.
    """

    def __init__(self, restrictions: ProvenanceRestrictions | None = None):
        self.restrictions = restrictions or ProvenanceRestrictions.oecd_default()
        self._log: list[ProvenanceEvent] = []
        self._rejections: list[RejectedTransfer] = []

    @property
    def log(self) -> tuple[ProvenanceEvent, ...]:
        return tuple(self._log)

    @property
    def rejections(self) -> tuple[RejectedTransfer, ...]:
        return tuple(self._rejections)

    @property
    def head_seq(self) -> int:
        return len(self._log)

    def _append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        stamped = replace(event, seq=len(self._log) + 1)
        self._log.append(stamped)
        return stamped

    def _fold(self, up_to: int | None = None) -> ProvenanceView:
        participants: dict[str, Participant] = {}
        lots: dict[str, Lot] = {}

        for e in self._log:
            if up_to is not None and e.seq > up_to:
                break
            if isinstance(e, ParticipantRegistered):
                participants[e.participant_id] = Participant(
                    participant_id=e.participant_id,
                    legal_name=e.legal_name,
                    role=e.role,
                    country=e.country,
                )
            elif isinstance(e, DueDiligenceAssessed):
                p = participants[e.participant_id]
                p.dd_status = e.status
                p.dd_valid_until = e.valid_until
                p.dd_assessor = e.assessor
            elif isinstance(e, LotDeclared):
                lots[e.lot_id] = Lot(
                    lot_id=e.lot_id,
                    mineral=e.mineral,
                    mass_kg=e.mass_kg,
                    mine_site=e.mine_site,
                    country_of_origin=e.country_of_origin,
                    holder=e.participant_id,
                )
            elif isinstance(e, AssayRecorded):
                lot = lots[e.lot_id]
                lot.last_assay = e.timestamp
                lot.assay_grade_ppm = e.grade_ppm
                lot.laboratory = e.laboratory
                lot.mass_kg = e.mass_kg
            elif isinstance(e, CustodyTransferred):
                lot = lots[e.lot_id]
                lot.holder = e.to_participant
                lot.mass_kg = e.mass_kg
                lot.custody_depth += 1
            elif isinstance(e, MassReconciled):
                lots[e.lot_id].mass_kg = e.actual_kg
            elif isinstance(e, LotExported):
                lots[e.lot_id].exported = True

        return ProvenanceView(participants=participants, lots=lots)

    def view(self, at_seq: int | None = None) -> ProvenanceView:
        return self._fold(at_seq)

    # ---------- operations ----------

    def register_participant(
        self, participant_id: str, legal_name: str, role: Role, actor: str,
        reason: str, country: str = "ZA",
    ) -> ProvenanceEvent:
        return self._append(
            ParticipantRegistered(
                actor=actor, reason=reason, participant_id=participant_id,
                legal_name=legal_name, role=role, country=country,
            )
        )

    def assess_due_diligence(
        self, participant_id: str, status: DueDiligenceStatus, assessor: str,
        valid_until: datetime | None, actor: str, reason: str,
    ) -> ProvenanceEvent:
        if participant_id not in self._fold().participants:
            raise ProvenanceError(f"{participant_id} is not registered")
        return self._append(
            DueDiligenceAssessed(
                actor=actor, reason=reason, participant_id=participant_id,
                status=status, assessor=assessor, valid_until=valid_until,
            )
        )

    def declare_lot(
        self, lot_id: str, participant_id: str, mineral: str, mass_kg: int,
        mine_site: str, actor: str, reason: str, country_of_origin: str = "ZA",
    ) -> ProvenanceEvent:
        view = self._fold()
        if participant_id not in view.participants:
            raise ProvenanceError(f"{participant_id} is not registered")
        if view.participants[participant_id].role is not Role.MINE:
            raise ProvenanceError(
                f"{participant_id} is not a mine and cannot declare origin"
            )
        return self._append(
            LotDeclared(
                actor=actor, reason=reason, lot_id=lot_id,
                participant_id=participant_id, mineral=mineral, mass_kg=mass_kg,
                mine_site=mine_site, country_of_origin=country_of_origin,
            )
        )

    def record_assay(
        self, lot_id: str, laboratory: str, grade_ppm: int, mass_kg: int,
        actor: str, reason: str, at: datetime | None = None,
    ) -> ProvenanceEvent:
        if lot_id not in self._fold().lots:
            raise ProvenanceError(f"lot {lot_id} has not been declared")
        event = AssayRecorded(
            actor=actor, reason=reason, lot_id=lot_id, laboratory=laboratory,
            grade_ppm=grade_ppm, mass_kg=mass_kg,
        )
        if at is not None:
            object.__setattr__(event, "timestamp", at)
        return self._append(event)

    def transfer_custody(
        self, lot_id: str, from_participant: str, to_participant: str,
        mass_kg: int, actor: str, reason: str, at: datetime | None = None,
    ) -> tuple[ProvenanceEvent | None, Decision]:
        proposed = ProposedTransfer(
            lot_id=lot_id, from_participant=from_participant,
            to_participant=to_participant, mass_kg=mass_kg, at=at or _now(),
        )
        decision = self.restrictions.evaluate(self._fold(), proposed)
        if not decision.allowed:
            self._rejections.append(
                RejectedTransfer(proposed, decision.reasons, proposed.at)
            )
            return None, decision

        return (
            self._append(
                CustodyTransferred(
                    actor=actor, reason=reason, lot_id=lot_id,
                    from_participant=from_participant,
                    to_participant=to_participant, mass_kg=mass_kg,
                )
            ),
            decision,
        )

    def reconcile_mass(
        self, lot_id: str, actual_kg: int, explanation: str, actor: str
    ) -> ProvenanceEvent:
        lot = self._fold().lots.get(lot_id)
        if lot is None:
            raise ProvenanceError(f"lot {lot_id} has not been declared")
        return self._append(
            MassReconciled(
                actor=actor, reason="mass reconciliation", lot_id=lot_id,
                expected_kg=lot.mass_kg, actual_kg=actual_kg,
                explanation=explanation,
            )
        )

    def export_lot(
        self, lot_id: str, destination_country: str, actor: str, reason: str
    ) -> ProvenanceEvent:
        lot = self._fold().lots.get(lot_id)
        if lot is None:
            raise ProvenanceError(f"lot {lot_id} has not been declared")
        return self._append(
            LotExported(
                actor=actor, reason=reason, lot_id=lot_id,
                participant_id=lot.holder, destination_country=destination_country,
            )
        )

    # ---------- reporting ----------

    def custody_chain(self, lot_id: str) -> list[ProvenanceEvent]:
        return [e for e in self._log if getattr(e, "lot_id", None) == lot_id]

    def attestation_gaps(self, lot_id: str) -> list[str]:
        """
        What this record cannot prove.

        The most useful output in the module, and the one most provenance
        systems omit. A due diligence file that lists its own weaknesses is
        more credible than one that claims completeness, and an auditor will
        find these anyway.
        """
        view = self._fold()
        lot = view.lots.get(lot_id)
        if lot is None:
            return [f"lot {lot_id} has not been declared"]

        gaps: list[str] = []
        gaps.append(
            f"origin at {lot.mine_site} is a declaration by {lot.lot_id.split('-')[0]}, "
            "not an independently verified fact"
        )
        if lot.last_assay is None:
            gaps.append("no independent assay has been recorded")
        else:
            age = (_now() - lot.last_assay).days
            gaps.append(
                f"grade rests on a single assay by {lot.laboratory}, {age} days old"
            )

        reconciliations = [
            e for e in self._log
            if isinstance(e, MassReconciled) and e.lot_id == lot_id
        ]
        for r in reconciliations:
            gaps.append(
                f"mass variance of {r.variance_kg:+d}kg at sequence {r.seq} rests on "
                f"an explanation ({r.explanation}), not a measurement"
            )

        gaps.append(
            "the register records custody as reported by participants; it cannot "
            "confirm the physical lot was not substituted between transfers"
        )
        return gaps
