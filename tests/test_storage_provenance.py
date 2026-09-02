"""
Tests for the persistence and provenance extensions.

The storage tests are mostly about tamper detection, because that is the
property being claimed. The provenance tests are mostly about refusals, for
the same reason as in the securities case: what a register declines to record
is the compliance control.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokenised_register import Classification  # noqa: E402
from tokenised_register.events import (  # noqa: E402
    HolderAdmitted,
    NotesIssued,
    TransferExecuted,
)
from tokenised_register.provenance import (  # noqa: E402
    DueDiligenceStatus,
    ProvenanceError,
    ProvenanceRegister,
    ProvenanceRestrictions,
    Role,
)
from tokenised_register.storage import (  # noqa: E402
    EventStore,
    TamperDetected,
    anchor_value,
    canonical_payload,
    payload_digest,
)

UTC = timezone.utc


# ============================================================
# STORAGE
# ============================================================


def sample_events():
    return [
        HolderAdmitted(
            actor="registrar", reason="onboarded", holder_id="INV001",
            legal_name="One (Pty) Ltd",
            classification=Classification.QUALIFYING_INVESTOR,
        ),
        NotesIssued(
            actor="registrar", reason="allotment", holder_id="INV001", units=20
        ),
        HolderAdmitted(
            actor="registrar", reason="onboarded", holder_id="INV002",
            legal_name="Two (Pty) Ltd",
            classification=Classification.QUALIFYING_INVESTOR,
        ),
        TransferExecuted(
            actor="registrar", reason="trade", from_holder="INV001",
            to_holder="INV002", units=10,
        ),
    ]


def test_serialisation_is_deterministic():
    """The hash is meaningless unless the bytes are stable."""
    event = sample_events()[0]
    assert canonical_payload(event) == canonical_payload(event)
    assert payload_digest(event) == payload_digest(event)


def test_chain_verifies_on_a_clean_store():
    store = EventStore()
    store.append_all(sample_events())
    store.verify()
    assert store.verified()
    assert len(store) == 4


def test_head_advances_and_is_stable():
    store = EventStore()
    store.append_all(sample_events())
    seq, head = store.head
    assert seq == 4
    assert len(head) == 64
    assert anchor_value(store) == f"4:{head}"


def test_altering_a_payload_is_detected():
    """The central claim: silent edits become loud."""
    store = EventStore()
    store.append_all(sample_events())
    assert store.verified()

    store._conn.execute(
        "UPDATE event_log SET payload = REPLACE(payload, '\"units\":20', '\"units\":200') "
        "WHERE seq = 2"
    )
    store._conn.commit()

    with pytest.raises(TamperDetected, match="payload altered at sequence 2"):
        store.verify()


def test_deleting_a_record_is_detected():
    store = EventStore()
    store.append_all(sample_events())

    store._conn.execute("DELETE FROM event_log WHERE seq = 2")
    store._conn.commit()

    with pytest.raises(TamperDetected, match="sequence gap"):
        store.verify()


def test_recomputing_hashes_after_tampering_still_breaks_the_chain():
    """
    The sophisticated attack: edit the payload AND recompute its digest.

    That defeats the payload check but not the link, because the record hash
    still commits to the original digest and every subsequent record commits
    to that hash.
    """
    import hashlib

    store = EventStore()
    store.append_all(sample_events())

    row = store._conn.execute(
        "SELECT payload FROM event_log WHERE seq = 2"
    ).fetchone()
    forged = row[0].replace('"units":20', '"units":200')
    forged_digest = hashlib.sha256(forged.encode()).hexdigest()

    store._conn.execute(
        "UPDATE event_log SET payload = ?, payload_hash = ? WHERE seq = 2",
        (forged, forged_digest),
    )
    store._conn.commit()

    with pytest.raises(TamperDetected, match="record hash invalid at sequence 2"):
        store.verify()


def test_replay_reconstructs_the_events():
    store = EventStore()
    original = sample_events()
    store.append_all(original)

    rebuilt = store.replay()
    assert len(rebuilt) == 4
    assert [type(e).__name__ for e in rebuilt] == [
        type(e).__name__ for e in original
    ]
    assert rebuilt[1].units == 20
    assert rebuilt[0].classification is Classification.QUALIFYING_INVESTOR
    assert [e.seq for e in rebuilt] == [1, 2, 3, 4]


def test_replay_refuses_a_tampered_store():
    store = EventStore()
    store.append_all(sample_events())
    store._conn.execute("DELETE FROM event_log WHERE seq = 3")
    store._conn.commit()

    with pytest.raises(TamperDetected):
        store.replay()


def test_survives_a_restart(tmp_path):
    db = tmp_path / "register.db"
    store = EventStore(db)
    store.append_all(sample_events())
    _, head_before = store.head
    store.close()

    reopened = EventStore(db)
    reopened.verify()
    assert reopened.head[1] == head_before
    assert len(reopened.replay()) == 4
    reopened.close()


def test_proof_contains_what_a_verifier_needs():
    store = EventStore()
    store.append_all(sample_events())
    proof = store.proof(2)
    assert set(proof) == {
        "seq", "payload", "payload_hash", "prev_hash", "record_hash"
    }


def test_store_exposes_no_update_or_delete():
    """Not discipline. The methods do not exist."""
    assert not hasattr(EventStore, "update")
    assert not hasattr(EventStore, "delete")
    assert not hasattr(EventStore, "edit")


# ============================================================
# PROVENANCE
# ============================================================


def build_chain(assay_age_days: int = 10, dd_valid_days: int = 365):
    r = ProvenanceRegister()
    now = datetime.now(UTC)

    parties = [
        ("MINE01", "Limpopo Chrome Mining (Pty) Ltd", Role.MINE),
        ("PROC01", "Rustenburg Processing (Pty) Ltd", Role.PROCESSOR),
        ("TRAD01", "Meridian Metals (Pty) Ltd", Role.TRADER),
        ("EXP01", "Durban Export Services (Pty) Ltd", Role.EXPORTER),
    ]
    for pid, name, role in parties:
        r.register_participant(pid, name, role, actor="admin", reason="onboarded")
        r.assess_due_diligence(
            pid, DueDiligenceStatus.CURRENT, assessor="SGS",
            valid_until=now + timedelta(days=dd_valid_days),
            actor="compliance", reason="annual assessment",
        )

    r.declare_lot(
        "MINE01-L0001", "MINE01", "chrome ore", 400_000, "Mogalakwena Section 4",
        actor="mine-ops", reason="production declaration",
    )
    r.record_assay(
        "MINE01-L0001", laboratory="Intertek", grade_ppm=420_000, mass_kg=400_000,
        actor="lab-intake", reason="pre-shipment assay",
        at=now - timedelta(days=assay_age_days),
    )
    return r, now


def test_custody_moves_along_a_clean_chain():
    r, now = build_chain()
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery to processor", at=now,
    )
    assert decision.allowed
    assert r.view().lots["MINE01-L0001"].holder == "PROC01"


def test_transfer_to_a_party_without_due_diligence_is_refused():
    r, now = build_chain()
    r.register_participant(
        "TRAD99", "Unassessed Trading Co", Role.TRADER,
        actor="admin", reason="onboarded",
    )
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "TRAD99", 400_000,
        actor="logistics", reason="attempted sale", at=now,
    )
    assert event is None
    assert any("no current due diligence" in x for x in decision.reasons)


def test_lapsed_assessment_is_distinguished_from_none():
    r, now = build_chain(dd_valid_days=5)
    later = now + timedelta(days=30)
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=later,
    )
    assert event is None
    assert any("lapsed assessment" in x for x in decision.reasons)


def test_suspended_party_is_refused():
    r, now = build_chain()
    r.assess_due_diligence(
        "PROC01", DueDiligenceStatus.SUSPENDED, assessor="SGS",
        valid_until=None, actor="compliance", reason="adverse finding",
    )
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )
    assert event is None
    assert any("suspended" in x for x in decision.reasons)


def test_stale_assay_blocks_transfer():
    r, now = build_chain(assay_age_days=400)
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )
    assert event is None
    assert any("days old" in x for x in decision.reasons)


def test_unexplained_mass_loss_is_refused_but_explained_loss_passes():
    """The silo-receipt failure mode, encoded as a rule."""
    r, now = build_chain()
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 300_000,
        actor="logistics", reason="delivery", at=now,
    )
    assert event is None
    assert any("unexplained mass loss" in x for x in decision.reasons)

    r.reconcile_mass(
        "MINE01-L0001", actual_kg=300_000,
        explanation="moisture loss and screening rejects, weighbridge ticket 4471",
        actor="site-supervisor",
    )
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 300_000,
        actor="logistics", reason="delivery", at=now,
    )
    assert decision.allowed


def test_cannot_transfer_a_lot_you_do_not_hold():
    r, now = build_chain()
    r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )
    event, decision = r.transfer_custody(
        "MINE01-L0001", "MINE01", "TRAD01", 400_000,
        actor="logistics", reason="double sale", at=now,
    )
    assert event is None
    assert any("is held by PROC01" in x for x in decision.reasons)


def test_exported_lot_cannot_move_again():
    r, now = build_chain()
    r.transfer_custody(
        "MINE01-L0001", "MINE01", "EXP01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )
    r.export_lot("MINE01-L0001", "NL", actor="exporter", reason="shipment")
    event, decision = r.transfer_custody(
        "MINE01-L0001", "EXP01", "TRAD01", 400_000,
        actor="logistics", reason="post-export sale", at=now,
    )
    assert event is None
    assert any("already been exported" in x for x in decision.reasons)


def test_only_a_mine_may_declare_origin():
    r, _ = build_chain()
    with pytest.raises(ProvenanceError, match="not a mine"):
        r.declare_lot(
            "TRAD01-L0002", "TRAD01", "chrome ore", 100_000, "Unknown",
            actor="trader", reason="declaration",
        )


def test_point_in_time_reconstruction():
    r, now = build_chain()
    before = r.head_seq
    r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )
    assert r.view().lots["MINE01-L0001"].holder == "PROC01"
    assert r.view(at_seq=before).lots["MINE01-L0001"].holder == "MINE01"


def test_attestation_gaps_are_reported_honestly():
    """
    The module's most important output: what the record cannot prove.
    """
    r, now = build_chain()
    r.reconcile_mass(
        "MINE01-L0001", actual_kg=390_000, explanation="moisture loss",
        actor="site-supervisor",
    )
    gaps = r.attestation_gaps("MINE01-L0001")

    assert any("not an independently verified fact" in g for g in gaps)
    assert any("single assay" in g for g in gaps)
    assert any("mass variance" in g for g in gaps)
    assert any("substituted between transfers" in g for g in gaps)


def test_provenance_events_persist_in_the_hash_chain():
    """The storage layer is domain-agnostic; provenance events chain too."""
    r, now = build_chain()
    r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )

    store = EventStore()
    for event in r.log:
        store.append(event)

    store.verify()
    assert len(store) == r.head_seq


def test_provenance_events_replay_from_the_store():
    """Type resolution must work across both event modules, not just one."""
    r, now = build_chain()
    r.transfer_custody(
        "MINE01-L0001", "MINE01", "PROC01", 400_000,
        actor="logistics", reason="delivery", at=now,
    )

    store = EventStore()
    for event in r.log:
        store.append(event)

    rebuilt = store.replay()
    assert len(rebuilt) == r.head_seq
    assert [type(e).__name__ for e in rebuilt] == [
        type(e).__name__ for e in r.log
    ]
    lot_declared = [e for e in rebuilt if type(e).__name__ == "LotDeclared"][0]
    assert lot_declared.mass_kg == 400_000
    dd = [e for e in rebuilt if type(e).__name__ == "DueDiligenceAssessed"][0]
    assert dd.status is DueDiligenceStatus.CURRENT
    assert isinstance(dd.valid_until, datetime)
