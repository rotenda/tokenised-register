# Demonstration output — storage and provenance

Output of `python3 demo_provenance.py`.

```

PART ONE — TAMPER-EVIDENT STORAGE
What a hash chain gives you for register integrity, without a chain.

──────────────────────────────────────────────────────────────────────────
1. APPEND AND VERIFY
──────────────────────────────────────────────────────────────────────────
  seq 1  HolderAdmitted   2df5e9b1f2b5807ec01c7f1d…
  seq 2  NotesIssued      57f0a1d558362ab168e5e2e9…
  seq 3  HolderAdmitted   2fc9bcc7f8e7bc09cbef68ec…

  chain verifies: True
  anchor value  : 3:2fc9bcc7f8e7bc09cbef68ec2f8d8e326f1c9ad0a30f27cfbcd94ee57e04874c

  that single value is what you publish externally. one per interval,
  not one per transaction.

──────────────────────────────────────────────────────────────────────────
2. A DBA EDITS A ROW
──────────────────────────────────────────────────────────────────────────
  UPDATE event_log SET payload = ... units 20 -> 200 WHERE seq = 2
  DETECTED: payload altered at sequence 2: content does not match its stored digest

──────────────────────────────────────────────────────────────────────────
3. THE SOPHISTICATED VERSION
──────────────────────────────────────────────────────────────────────────
  the attacker also recomputes the payload digest to match.
  DETECTED: record hash invalid at sequence 2

  the record hash still commits to the original digest, and every
  later record commits to that hash. rewriting one row means
  rewriting all of them — and the anchor still would not match.


PART TWO — THE SAME ARCHITECTURE, PHYSICAL CUSTODY
Mineral lot from mine to export, under OECD-style due diligence.

──────────────────────────────────────────────────────────────────────────
1. PARTICIPANTS AND DUE DILIGENCE
──────────────────────────────────────────────────────────────────────────
  MINE01   Limpopo Chrome Mining (Pty) Ltd        DD valid 365d
  PROC01   Rustenburg Processing (Pty) Ltd        DD valid 365d
  TRAD01   Meridian Metals (Pty) Ltd              DD valid 5d
  EXP01    Durban Export Services (Pty) Ltd       DD valid 365d

──────────────────────────────────────────────────────────────────────────
2. LOT DECLARED AND ASSAYED
──────────────────────────────────────────────────────────────────────────
  MINE01-L0001: 400,000kg chrome ore
  origin: Mogalakwena Section 4, ZA
  assay : 420,000ppm by Intertek

──────────────────────────────────────────────────────────────────────────
3. A DELIVERY WITH UNEXPLAINED SHORTFALL
──────────────────────────────────────────────────────────────────────────
  attempt: MINE01 -> PROC01, 300,000kg (declared 400,000kg)
  result : REFUSED
     - unexplained mass loss of 100000kg (25.0%) exceeds the 2.0% tolerance; record a reconciliation with an explanation first

  this is the silo-receipt failure mode. the register cannot weigh
  the truck, but it can refuse to let 400t become 300t silently.

──────────────────────────────────────────────────────────────────────────
4. RECONCILED, THEN PERMITTED
──────────────────────────────────────────────────────────────────────────
  transfer of 392,000kg after reconciliation: EXECUTED

──────────────────────────────────────────────────────────────────────────
5. A COUNTERPARTY WHOSE ASSESSMENT HAS LAPSED
──────────────────────────────────────────────────────────────────────────
  attempt: PROC01 -> TRAD01, 30 days from now (TRAD01 DD expires in 5)
  result : REFUSED
     - transferee TRAD01 has a lapsed assessment (expired 2026-08-09)

──────────────────────────────────────────────────────────────────────────
6. ROUTED TO A COMPLIANT EXPORTER INSTEAD
──────────────────────────────────────────────────────────────────────────
  PROC01 -> EXP01: EXECUTED
  exported to NL

──────────────────────────────────────────────────────────────────────────
7. THE CUSTODY CHAIN
──────────────────────────────────────────────────────────────────────────
  seq   9  Declared MINE01-L0001: 400000kg chrome ore from Mogalakwena Section 4
  seq  10  Assay MINE01-L0001: 420000ppm, 400000kg by Intertek
  seq  11  Reconciled MINE01-L0001: -8000kg — moisture loss and screening rejects, weighbridge ticket 4471
  seq  12  Custody MINE01-L0001: MINE01 -> PROC01 (392000kg)
  seq  13  Custody MINE01-L0001: PROC01 -> EXP01 (392000kg)
  seq  14  Exported MINE01-L0001 to NL

──────────────────────────────────────────────────────────────────────────
8. WHAT THIS RECORD CANNOT PROVE
──────────────────────────────────────────────────────────────────────────
  - origin at Mogalakwena Section 4 is a declaration by MINE01, not an independently verified fact
  - grade rests on a single assay by Intertek, 10 days old
  - mass variance of -8000kg at sequence 11 rests on an explanation (moisture loss and screening rejects, weighbridge ticket 4471), not a measurement
  - the register records custody as reported by participants; it cannot confirm the physical lot was not substituted between transfers

  most provenance systems omit this section. it is the most useful
  output in the module: a due diligence file that states its own
  limits is more credible than one claiming completeness, and an
  auditor will find these anyway.

──────────────────────────────────────────────────────────────────────────
9. PERSISTED AND CHAINED
──────────────────────────────────────────────────────────────────────────
  14 events persisted
  chain verifies: True
  anchor        : 14:ae24c2f56c207ae57936a7830803201bb63df8be31feb72bd28a48d79726c58f
  replayed      : 14 events reconstructed

  the storage layer is domain-agnostic. securities events and
  provenance events chain identically.

```
