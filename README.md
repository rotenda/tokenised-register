# tokenised_register

[![tests](https://github.com/rotenda/tokenised-register/actions/workflows/tests.yml/badge.svg)](https://github.com/rotenda/tokenised-register/actions/workflows/tests.yml)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rotenda/tokenised-register)

A reference implementation of a securities register for tokenised private-market debt, built for the South African legal position.

Not production software. It exists to make an argument legible in code.

**[Securities walkthrough](DEMO_OUTPUT.md)** · **[Storage and provenance walkthrough](DEMO_PROVENANCE_OUTPUT.md)** — full output, no installation required.

---

## The claim

> The off-chain register is the authoritative record of title.
> The chain is a derived mirror.
> When they disagree, the chain is corrected.

South African law does not currently recognise an on-chain register as the authoritative record of title to a security. Switzerland does — article 973d of its Code of Obligations created *ledger-based securities*, where the register entry itself is the security. The Financial Markets Act review has not resolved the equivalent question here.

Until it does, an issuer in South Africa has one defensible architecture, and this is it. Everything else in the codebase follows from that single constraint.

## Running it

```bash
python3 demo.py              # securities register walkthrough
python3 demo_provenance.py   # tamper-evident storage + mineral custody
python3 -m pytest tests/     # 49 tests
```

No dependencies beyond the standard library. `pytest` for the tests.

## What the modules do

| Module | Responsibility |
|---|---|
| `events.py` | The append-only record. Immutable, sequence-ordered, every entry carrying actor and reason. |
| `register.py` | The authoritative register. State is a projection of the event log, never stored as source of truth. |
| `restrictions.py` | Transfer restrictions. Evaluated *before* anything is appended. |
| `actions.py` | Coupons and redemption, computed against a past record point. |
| `mirror.py` | The chain side: publishing, reconciliation, and repair. |
| `storage.py` | Hash-chained, tamper-evident persistence on SQLite. |
| `provenance.py` | The same architecture applied to physical mineral custody. |

## Six design decisions worth defending

**1. Event-sourced, because a statutory register must be reconstructible.**

You must be able to answer "who held what on 15 March" years later, and show how you know. A mutable balances table cannot answer that. An append-only log answers it by construction, and makes silent corruption of a position structurally impossible — to change a balance you must append a visible event.

**2. Restrictions are evaluated before the event is appended.**

A refused transfer never enters the register. This is the inverse of the common on-chain pattern where a transaction settles and compliance is assessed afterwards. The refusal is retained separately, because refusals are exactly what a supervisor asks about — but a refusal is not a register entry, because it did not happen.

**3. The minimum-holding rule binds both sides of a trade.**

Where an instrument relies on a minimum-subscription exemption, the threshold has to survive secondary trading. A holder who acquires at the threshold and then sells down to a stub defeats the exemption as effectively as issuing below it. So the transferee must end at or above the minimum, and the transferor must either stay above it or exit entirely.

**4. Every refusal returns every reason.**

Not the first failing rule — all of them. Fixing one and resubmitting only to hit the next is a poor experience and a worse audit trail.

**5. Distributions allocate to whole cents and always sum to the total.**

Money does not divide evenly. Largest-remainder allocation with deterministic tie-breaking means the parts sum exactly to the whole and identical inputs always produce identical output. An implementation that quietly loses or creates a cent is an audit finding.

**6. Reconciliation is a feature, not error handling.**

The register and the chain *will* diverge — dropped writes, reorgs, compromised keys, peer-to-peer transfers outside the platform. A design that assumes they stay in sync is not a design. Divergences are classified by severity, and the serious one is a balance on an address with no register position, because it means an unqualified party may be holding.

Repair credits or debits the chain to match the register. It never adjusts the register. An unknown balance is *frozen*, not burned — that is a compliance incident requiring a human, not something a script closes quietly.

**7. Tamper evidence does not require a chain.**

A register in an ordinary database is only as trustworthy as whoever holds write access. A hash chain — `SHA256(seq || prev_hash || payload_hash)` — fixes that with one column and no distributed system. Alter any historical payload and every subsequent hash breaks.

Publishing the head hash externally at intervals (*anchoring*) bounds how far back an attacker with full database access could rewrite. That is one value per interval rather than a transaction per transfer, and it is the cheap 90% of what a chain is usually invoked to provide. Worth being precise about, because integrity is often the only property actually being sought.

**8. The architecture is not really about securities.**

`provenance.py` applies the same design to a mineral lot moving from mine to export: append-only log, restrictions evaluated before commit, point-in-time reconstruction, reconciliation. The vocabulary changes; the structure does not.

The instructive difference is not technical. A securities register is self-contained — units exist because the register says so. A provenance register makes claims about the physical world, and cannot verify them. No ledger design closes that gap. What a register *can* do is make it explicit: record who attested, when, to what, and refuse to let a claim travel further than its attestation supports.

Hence `attestation_gaps()`, which reports what the record cannot prove. Most provenance systems omit this. It is the most useful output in the module.

## What this deliberately does not do

No chain, no wallet, no custody, no exchange, no stablecoin. Each is a licensing surface and none is the interesting problem. The `ChainAdapter` interface is four methods precisely so the ledger is replaceable — chain choice is a ten-year bet made with two years of information.

There is also no attempt here to say what the law *should* be. The implementation takes the current position as a constraint and shows what follows from it.

The provenance module does not claim to prove provenance. It claims to make the limits of a provenance claim legible, which is a smaller and more defensible promise.

## Status

Illustrative. Written to accompany published analysis on tokenisation in South African markets. Not audited, not deployed, not offered as a product, and not a solicitation of any kind.

Written in a personal capacity. Views are the author's own and do not represent those of any employer or institution. Not legal or financial advice.
