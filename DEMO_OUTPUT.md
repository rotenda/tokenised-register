# Demonstration output

Output of `python3 demo.py`. Reproduced here so the walkthrough can be read without running anything.

```

TOKENISED NOTE REGISTER — REFERENCE IMPLEMENTATION
Instrument: SME receivables note, R100,000 denomination
Structure : private placement, R1,000,000 minimum per holder

──────────────────────────────────────────────────────────────────────────
1. ONBOARDING
──────────────────────────────────────────────────────────────────────────
  admitted INV001  Meridian Credit Partners (Pty) Ltd  [qualifying_investor]
  admitted INV002  Kestrel Family Office (Pty) Ltd  [qualifying_investor]
  admitted INV003  Highveld Securities (Pty) Ltd  [securities_dealer]
  admitted RETAIL01  J. Nkosi  [unclassified]  <- cannot hold

──────────────────────────────────────────────────────────────────────────
2. PRIMARY ISSUANCE
──────────────────────────────────────────────────────────────────────────
  allotted  200 notes to INV001     R20,000,000.00
  allotted  150 notes to INV002     R15,000,000.00
  allotted  150 notes to INV003     R15,000,000.00

  total in issue: 500 notes, R50,000,000.00
  register sequence at close of issuance: 7

──────────────────────────────────────────────────────────────────────────
3. A TRANSFER THAT IS REFUSED
──────────────────────────────────────────────────────────────────────────
  attempt: INV001 -> RETAIL01, 20 notes
  result : REFUSED
     - transferee RETAIL01 is classified 'unclassified' and may not hold this instrument

  nothing was appended to the register. the attempt is on file.

  attempt: INV002 -> INV003, 145 notes (leaves a R500,000 stub)
  result : REFUSED
     - transferor would retain 5 units, below the minimum of 10; transfer the full holding or retain at least the minimum

──────────────────────────────────────────────────────────────────────────
4. A TRANSFER THAT IS PERMITTED
──────────────────────────────────────────────────────────────────────────
  attempt: INV002 -> INV003, 50 notes
  result : EXECUTED
  positions now: {'INV001': 200, 'INV002': 100, 'INV003': 200}

──────────────────────────────────────────────────────────────────────────
5. COUPON AT A PAST RECORD POINT
──────────────────────────────────────────────────────────────────────────
  rate 14.5% p.a., 91 days, record point = sequence 7
  total distribution: R1,807,534.25

     INV001   200 notes      R723,013.70
     INV002   150 notes      R542,260.28
     INV003   150 notes      R542,260.27

  allocated: R1,807,534.25   sums exactly: True
  note the record point predates the transfer in step 4 —
  INV002 is paid on 150 notes, not 100. reproducible from the log alone.

──────────────────────────────────────────────────────────────────────────
6. THE CHAIN MIRROR
──────────────────────────────────────────────────────────────────────────
  publish: INV001: +200 units (tx1)
  publish: INV002: +100 units (tx2)
  publish: INV003: +200 units (tx3)

  In sync at seq 9: 500 units on both sides.

──────────────────────────────────────────────────────────────────────────
7. DIVERGENCE — AND WHO WINS
──────────────────────────────────────────────────────────────────────────
  simulating two failures:
   (a) a rogue balance appears on an address with no register position
   (b) a publish is dropped silently

  DIVERGENCE at seq 10: register 500 units, chain 555 units, 2 discrepancies (2 critical).

     [critical] excess_on_chain: chain over by 30 units
     [critical] unknown_on_chain: address GROGUE9999 holds 25 units but has no corresponding register position

  repairing...
     debited INV001 30 excess units
     FROZE GROGUE9999 pending investigation — units held by an address with no register position

  DIVERGENCE at seq 10: register 500 units, chain 525 units, 1 discrepancies (1 critical).
  the dropped publish is repaired. the rogue balance is NOT —
  it is frozen and still reported, deliberately. a script does not
  get to burn units and close a compliance incident quietly.
  frozen addresses: ['GROGUE9999']

  register positions unchanged by repair: True
  register sequence unchanged by repair:   True

  the chain was corrected to match the register.
  the register was never adjusted to match the chain.
  that is the whole architecture.

──────────────────────────────────────────────────────────────────────────
8. INTEGRITY
──────────────────────────────────────────────────────────────────────────
  entries in log      : 10
  refused transfers   : 2
  integrity problems  : none

  full audit trail for INV002:
     seq   2  Admitted INV002 (qualifying_investor)                [registrar]
     seq   6  Issued 150 to INV002                                 [registrar]
     seq   8  Transferred 50 from INV002 to INV003                 [registrar]
     seq  10  Transferred 30 from INV001 to INV002                 [registrar]

```
