"""
End-to-end walkthrough of a tokenised note programme.

Run: python3 demo.py

Follows a R50m private placement from onboarding through issuance, a refused
transfer, a permitted one, a coupon computed at a past record point, and finally
a chain divergence that gets detected and repaired.

The last section is the important one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tokenised_register import (
    Classification,
    InMemoryChain,
    Mirror,
    Register,
    RestrictionEngine,
    compute_coupon,
)

DENOM = 100_000_00       # R100,000 per note
MIN_HOLDING = 1_000_000_00  # R1,000,000 minimum ticket


def rule(title: str) -> None:
    print(f"\n{'─' * 74}\n{title}\n{'─' * 74}")


def money(cents: int) -> str:
    return f"R{Decimal(cents) / 100:,.2f}"


def main() -> None:
    print("\nTOKENISED NOTE REGISTER — REFERENCE IMPLEMENTATION")
    print("Instrument: SME receivables note, R100,000 denomination")
    print("Structure : private placement, R1,000,000 minimum per holder")

    register = Register(
        instrument_id="SMEN-2029",
        denomination_cents=DENOM,
        restrictions=RestrictionEngine.private_placement_default(
            minimum_cents=MIN_HOLDING, lock_up_days=0, holder_cap=50
        ),
    )

    # ---------------------------------------------------------------
    rule("1. ONBOARDING")

    investors = [
        ("INV001", "Meridian Credit Partners (Pty) Ltd", Classification.QUALIFYING_INVESTOR),
        ("INV002", "Kestrel Family Office (Pty) Ltd", Classification.QUALIFYING_INVESTOR),
        ("INV003", "Highveld Securities (Pty) Ltd", Classification.SECURITIES_DEALER),
    ]
    for hid, name, cls in investors:
        register.admit_holder(
            hid, name, cls, actor="registrar", reason="KYC and classification approved"
        )
        print(f"  admitted {hid}  {name}  [{cls.value}]")

    register.admit_holder(
        "RETAIL01", "J. Nkosi", Classification.UNCLASSIFIED,
        actor="registrar", reason="enquiry received, not classified",
    )
    print("  admitted RETAIL01  J. Nkosi  [unclassified]  <- cannot hold")

    # ---------------------------------------------------------------
    rule("2. PRIMARY ISSUANCE")

    allotments = {"INV001": 200, "INV002": 150, "INV003": 150}
    for hid, units in allotments.items():
        register.issue(hid, units, actor="registrar", reason="primary allotment")
        print(f"  allotted {units:>4} notes to {hid}   {money(units * DENOM):>16}")

    total = register.total_in_issue()
    print(f"\n  total in issue: {total} notes, {money(total * DENOM)}")

    record_seq = register.head_seq
    print(f"  register sequence at close of issuance: {record_seq}")

    # ---------------------------------------------------------------
    rule("3. A TRANSFER THAT IS REFUSED")

    print("  attempt: INV001 -> RETAIL01, 20 notes")
    event, decision = register.transfer(
        "INV001", "RETAIL01", 20, actor="registrar", reason="instructed trade"
    )
    print(f"  result : {'EXECUTED' if event else 'REFUSED'}")
    for reason in decision.reasons:
        print(f"     - {reason}")
    print("\n  nothing was appended to the register. the attempt is on file.")

    print("\n  attempt: INV002 -> INV003, 145 notes (leaves a R500,000 stub)")
    event, decision = register.transfer(
        "INV002", "INV003", 145, actor="registrar", reason="instructed trade"
    )
    print(f"  result : {'EXECUTED' if event else 'REFUSED'}")
    for reason in decision.reasons:
        print(f"     - {reason}")

    # ---------------------------------------------------------------
    rule("4. A TRANSFER THAT IS PERMITTED")

    print("  attempt: INV002 -> INV003, 50 notes")
    event, decision = register.transfer(
        "INV002", "INV003", 50, actor="registrar", reason="instructed trade"
    )
    print(f"  result : {'EXECUTED' if event else 'REFUSED'}")
    print(f"  positions now: {register.positions()}")

    # ---------------------------------------------------------------
    rule("5. COUPON AT A PAST RECORD POINT")

    coupon = compute_coupon(
        register,
        action_id="CPN-2026-Q3",
        annual_rate=Decimal("0.145"),
        days=91,
        record_seq=record_seq,
    )
    print(f"  rate 14.5% p.a., 91 days, record point = sequence {record_seq}")
    print(f"  total distribution: {money(coupon.total_amount_cents)}\n")
    for a in coupon.allocations:
        print(f"     {a.holder_id}  {a.units:>4} notes   {money(a.amount_cents):>14}")

    allocated = sum(a.amount_cents for a in coupon.allocations)
    print(f"\n  allocated: {money(allocated)}   sums exactly: {coupon.check_sums()}")
    print("  note the record point predates the transfer in step 4 —")
    print("  INV002 is paid on 150 notes, not 100. reproducible from the log alone.")

    register.record_corporate_action(
        "CPN-2026-Q3", "coupon", record_seq, coupon.total_amount_cents,
        actor="registrar", reason="quarterly coupon",
    )

    # ---------------------------------------------------------------
    rule("6. THE CHAIN MIRROR")

    chain = InMemoryChain()
    mirror = Mirror(chain=chain)
    for hid in ("INV001", "INV002", "INV003"):
        mirror.bind(hid, f"G{hid}XXXXXXXX")

    for line in mirror.publish(register):
        print(f"  publish: {line}")

    report = mirror.reconcile(register)
    print(f"\n  {report.summary()}")

    # ---------------------------------------------------------------
    rule("7. DIVERGENCE — AND WHO WINS")

    print("  simulating two failures:")
    print("   (a) a rogue balance appears on an address with no register position")
    chain.inject_unknown_holder("GROGUE9999", 25)
    print("   (b) a publish is dropped silently")
    chain.drop_next_write = True
    register.transfer("INV001", "INV002", 30, actor="registrar", reason="trade")
    mirror.publish(register)

    report = mirror.reconcile(register)
    print(f"\n  {report.summary()}\n")
    for d in report.divergences:
        print(f"     [{d.severity:>8}] {d.kind.value}: {d.detail}")

    positions_before = register.positions()
    seq_before = register.head_seq

    print("\n  repairing...")
    for action in mirror.repair(register, report):
        print(f"     {action}")

    final = mirror.reconcile(register)
    print(f"\n  {final.summary()}")
    print("  the dropped publish is repaired. the rogue balance is NOT —")
    print("  it is frozen and still reported, deliberately. a script does not")
    print("  get to burn units and close a compliance incident quietly.")
    print(f"  frozen addresses: {sorted(chain.frozen)}")

    print("\n  register positions unchanged by repair:",
          register.positions() == positions_before)
    print("  register sequence unchanged by repair:  ",
          register.head_seq == seq_before)
    print("\n  the chain was corrected to match the register.")
    print("  the register was never adjusted to match the chain.")
    print("  that is the whole architecture.")

    # ---------------------------------------------------------------
    rule("8. INTEGRITY")

    problems = register.verify_integrity()
    print(f"  entries in log      : {register.head_seq}")
    print(f"  refused transfers   : {len(register.rejections)}")
    print(f"  integrity problems  : {problems if problems else 'none'}")

    print("\n  full audit trail for INV002:")
    for e in register.statement("INV002"):
        print(f"     seq {e.seq:>3}  {e.describe():<52} [{e.actor}]")

    print()


if __name__ == "__main__":
    main()
