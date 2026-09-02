"""
Demonstration of the persistence and provenance extensions.

Run: python3 demo_provenance.py

Two parts. First, tamper-evident storage — what a hash chain gives you for
register integrity, without a chain. Second, the same register architecture
applied to physical mineral custody, including the part most provenance
systems leave out: an honest statement of what the record cannot prove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tokenised_register import Classification
from tokenised_register.events import HolderAdmitted, NotesIssued
from tokenised_register.provenance import (
    DueDiligenceStatus,
    ProvenanceRegister,
    Role,
)
from tokenised_register.storage import EventStore, TamperDetected, anchor_value

UTC = timezone.utc


def rule(title: str) -> None:
    print(f"\n{'─' * 74}\n{title}\n{'─' * 74}")


def part_one() -> None:
    print("\nPART ONE — TAMPER-EVIDENT STORAGE")
    print("What a hash chain gives you for register integrity, without a chain.")

    rule("1. APPEND AND VERIFY")

    store = EventStore()
    store.append(
        HolderAdmitted(
            actor="registrar", reason="onboarded", holder_id="INV001",
            legal_name="Meridian Credit Partners (Pty) Ltd",
            classification=Classification.QUALIFYING_INVESTOR,
        )
    )
    store.append(
        NotesIssued(actor="registrar", reason="allotment", holder_id="INV001", units=20)
    )
    store.append(
        HolderAdmitted(
            actor="registrar", reason="onboarded", holder_id="INV002",
            legal_name="Kestrel Family Office (Pty) Ltd",
            classification=Classification.QUALIFYING_INVESTOR,
        )
    )

    for row in store.records():
        print(f"  seq {row['seq']}  {row['event_type']:<16} {row['record_hash'][:24]}…")

    print(f"\n  chain verifies: {store.verified()}")
    print(f"  anchor value  : {anchor_value(store)}")
    print("\n  that single value is what you publish externally. one per interval,")
    print("  not one per transaction.")

    rule("2. A DBA EDITS A ROW")

    print("  UPDATE event_log SET payload = ... units 20 -> 200 WHERE seq = 2")
    store._conn.execute(
        "UPDATE event_log SET payload = REPLACE(payload, '\"units\":20', '\"units\":200') "
        "WHERE seq = 2"
    )
    store._conn.commit()

    try:
        store.verify()
        print("  chain verifies (this would be bad)")
    except TamperDetected as e:
        print(f"  DETECTED: {e}")

    rule("3. THE SOPHISTICATED VERSION")

    print("  the attacker also recomputes the payload digest to match.")
    import hashlib

    store2 = EventStore()
    store2.append(
        HolderAdmitted(
            actor="registrar", reason="onboarded", holder_id="INV001",
            legal_name="Meridian Credit Partners (Pty) Ltd",
            classification=Classification.QUALIFYING_INVESTOR,
        )
    )
    store2.append(
        NotesIssued(actor="registrar", reason="allotment", holder_id="INV001", units=20)
    )
    store2.append(
        NotesIssued(actor="registrar", reason="allotment", holder_id="INV001", units=5)
    )

    row = store2._conn.execute("SELECT payload FROM event_log WHERE seq = 2").fetchone()
    forged = row[0].replace('"units":20', '"units":200')
    store2._conn.execute(
        "UPDATE event_log SET payload = ?, payload_hash = ? WHERE seq = 2",
        (forged, hashlib.sha256(forged.encode()).hexdigest()),
    )
    store2._conn.commit()

    try:
        store2.verify()
        print("  chain verifies (this would be bad)")
    except TamperDetected as e:
        print(f"  DETECTED: {e}")

    print("\n  the record hash still commits to the original digest, and every")
    print("  later record commits to that hash. rewriting one row means")
    print("  rewriting all of them — and the anchor still would not match.")


def part_two() -> None:
    print("\n\nPART TWO — THE SAME ARCHITECTURE, PHYSICAL CUSTODY")
    print("Mineral lot from mine to export, under OECD-style due diligence.")

    r = ProvenanceRegister()
    now = datetime.now(UTC)

    rule("1. PARTICIPANTS AND DUE DILIGENCE")

    parties = [
        ("MINE01", "Limpopo Chrome Mining (Pty) Ltd", Role.MINE, 365),
        ("PROC01", "Rustenburg Processing (Pty) Ltd", Role.PROCESSOR, 365),
        ("TRAD01", "Meridian Metals (Pty) Ltd", Role.TRADER, 5),
        ("EXP01", "Durban Export Services (Pty) Ltd", Role.EXPORTER, 365),
    ]
    for pid, name, role, valid_days in parties:
        r.register_participant(pid, name, role, actor="admin", reason="onboarded")
        r.assess_due_diligence(
            pid, DueDiligenceStatus.CURRENT, assessor="SGS",
            valid_until=now + timedelta(days=valid_days),
            actor="compliance", reason="assessment",
        )
        print(f"  {pid:<8} {name:<38} DD valid {valid_days}d")

    rule("2. LOT DECLARED AND ASSAYED")

    r.declare_lot(
        "MINE01-L0001", "MINE01", "chrome ore", 400_000, "Mogalakwena Section 4",
        actor="mine-ops", reason="production declaration",
    )
    r.record_assay(
        "MINE01-L0001", laboratory="Intertek", grade_ppm=420_000, mass_kg=400_000,
        actor="lab-intake", reason="pre-shipment assay",
        at=now - timedelta(days=10),
    )
    lot = r.view().lots["MINE01-L0001"]
    print(f"  {lot.lot_id}: {lot.mass_kg:,}kg {lot.mineral}")
    print(f"  origin: {lot.mine_site}, {lot.country_of_origin}")
    print(f"  assay : {lot.assay_grade_ppm:,}ppm by {lot.laboratory}")

    rule("3. A DELIVERY WITH UNEXPLAINED SHORTFALL")

    print("  attempt: MINE01 -> PROC01, 300,000kg (declared 400,000kg)")
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 300_000,
        actor="logistics", reason="delivery", at=now,
    )
    print(f"  result : {'EXECUTED' if event else 'REFUSED'}")
    for reason in decision.reasons:
        print(f"     - {reason}")

    print("\n  this is the silo-receipt failure mode. the register cannot weigh")
    print("  the truck, but it can refuse to let 400t become 300t silently.")

    rule("4. RECONCILED, THEN PERMITTED")

    r.reconcile_mass(
        "MINE01-L0001", actual_kg=392_000,
        explanation="moisture loss and screening rejects, weighbridge ticket 4471",
        actor="site-supervisor",
    )
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 392_000,
        actor="logistics", reason="delivery", at=now,
    )
    print(f"  transfer of 392,000kg after reconciliation: "
          f"{'EXECUTED' if event else 'REFUSED'}")

    rule("5. A COUNTERPARTY WHOSE ASSESSMENT HAS LAPSED")

    later = now + timedelta(days=30)
    print("  attempt: PROC01 -> TRAD01, 30 days from now (TRAD01 DD expires in 5)")
    event, decision = r.transfer_custody(
        "MINE01-L0001", "PROC01", "TRAD01", 392_000,
        actor="logistics", reason="sale", at=later,
    )
    print(f"  result : {'EXECUTED' if event else 'REFUSED'}")
    for reason in decision.reasons:
        print(f"     - {reason}")

    rule("6. ROUTED TO A COMPLIANT EXPORTER INSTEAD")

    event, decision = r.transfer_custody(
        "MINE01-L0001", "PROC01", "EXP01", 392_000,
        actor="logistics", reason="delivery to exporter", at=later,
    )
    print(f"  PROC01 -> EXP01: {'EXECUTED' if event else 'REFUSED'}")
    r.export_lot("MINE01-L0001", "NL", actor="exporter", reason="shipment to Rotterdam")
    print("  exported to NL")

    rule("7. THE CUSTODY CHAIN")

    for e in r.custody_chain("MINE01-L0001"):
        print(f"  seq {e.seq:>3}  {e.describe()}")

    rule("8. WHAT THIS RECORD CANNOT PROVE")

    for gap in r.attestation_gaps("MINE01-L0001"):
        print(f"  - {gap}")

    print("\n  most provenance systems omit this section. it is the most useful")
    print("  output in the module: a due diligence file that states its own")
    print("  limits is more credible than one claiming completeness, and an")
    print("  auditor will find these anyway.")

    rule("9. PERSISTED AND CHAINED")

    store = EventStore()
    for event in r.log:
        store.append(event)
    print(f"  {len(store)} events persisted")
    print(f"  chain verifies: {store.verified()}")
    print(f"  anchor        : {anchor_value(store)}")
    print(f"  replayed      : {len(store.replay())} events reconstructed")
    print("\n  the storage layer is domain-agnostic. securities events and")
    print("  provenance events chain identically.")
    print()


if __name__ == "__main__":
    part_one()
    part_two()
