"""
The chain mirror.

This module encodes the single most important design decision in the system,
and the one South African law currently forces:

    The register is authoritative. The chain is derived.
    When they disagree, the chain is wrong.

That sentence is doing legal work, not technical work. Switzerland's DLT Act
made the ledger entry itself the security — article 973d of the Code of
Obligations. South Africa has no equivalent. Until the Financial Markets Act
review resolves the question, an on-chain balance here evidences a claim; it
does not constitute title. Building as though it did would be building on an
assumption the law has not made.

So the chain does real work — transparency, transferability, a
tamper-evident public trail — while the register remains the record.

The interesting engineering is not in publishing to the chain. It is in
handling the fact that the two WILL diverge: a publish fails, a transaction is
dropped, a reorg happens, a key is compromised, someone transfers a token
peer-to-peer outside the platform. A design that assumes they stay in sync is
not a design. Reconciliation is the feature.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class DivergenceType(str, Enum):
    MISSING_ON_CHAIN = "missing_on_chain"
    EXCESS_ON_CHAIN = "excess_on_chain"
    UNKNOWN_ON_CHAIN = "unknown_on_chain"
    NOT_MIRRORED = "not_mirrored"


@dataclass(frozen=True)
class Divergence:
    holder_id: str
    kind: DivergenceType
    register_units: int
    chain_units: int
    detail: str

    @property
    def severity(self) -> str:
        """
        UNKNOWN_ON_CHAIN is the serious one.

        A balance held by an address the register does not recognise means
        either the mint is compromised or units moved peer-to-peer outside the
        platform's control. Either way an unqualified party may be holding, and
        that is the failure the restrictions exist to prevent.
        """
        if self.kind is DivergenceType.UNKNOWN_ON_CHAIN:
            return "critical"
        if self.kind is DivergenceType.EXCESS_ON_CHAIN:
            return "critical"
        return "warning"


@dataclass(frozen=True)
class ReconciliationReport:
    at: datetime
    register_seq: int
    register_total: int
    chain_total: int
    divergences: tuple[Divergence, ...]

    @property
    def in_sync(self) -> bool:
        return not self.divergences

    @property
    def critical(self) -> tuple[Divergence, ...]:
        return tuple(d for d in self.divergences if d.severity == "critical")

    def summary(self) -> str:
        if self.in_sync:
            return (
                f"In sync at seq {self.register_seq}: "
                f"{self.register_total} units on both sides."
            )
        return (
            f"DIVERGENCE at seq {self.register_seq}: register {self.register_total} "
            f"units, chain {self.chain_total} units, "
            f"{len(self.divergences)} discrepancies "
            f"({len(self.critical)} critical)."
        )


class ChainAdapter(ABC):
    """
    Interface to whatever ledger is used as the mirror.

    Deliberately minimal. The register does not care whether the chain is
    Stellar, an EVM chain, or a permissioned ledger — it cares about four
    operations. Keeping this surface small is what makes the chain replaceable,
    which matters because chain choice is a ten-year bet made with two years of
    information.
    """

    @abstractmethod
    def balances(self) -> dict[str, int]: ...

    @abstractmethod
    def credit(self, address: str, units: int, memo: str) -> str: ...

    @abstractmethod
    def debit(self, address: str, units: int, memo: str) -> str: ...

    @abstractmethod
    def freeze(self, address: str) -> str: ...


class InMemoryChain(ChainAdapter):
    """
    Test double.

    Includes fault injection because the failure paths are the point. A mirror
    that is only ever tested on the happy path teaches nothing.
    """

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}
        self._frozen: set[str] = set()
        self._tx: list[tuple[str, str, int, str]] = []
        self.drop_next_write = False

    def balances(self) -> dict[str, int]:
        return {a: u for a, u in self._balances.items() if u > 0}

    def credit(self, address: str, units: int, memo: str) -> str:
        if self.drop_next_write:
            self.drop_next_write = False
            return "dropped"
        self._balances[address] = self._balances.get(address, 0) + units
        self._tx.append(("credit", address, units, memo))
        return f"tx{len(self._tx)}"

    def debit(self, address: str, units: int, memo: str) -> str:
        if self.drop_next_write:
            self.drop_next_write = False
            return "dropped"
        self._balances[address] = self._balances.get(address, 0) - units
        self._tx.append(("debit", address, units, memo))
        return f"tx{len(self._tx)}"

    def freeze(self, address: str) -> str:
        self._frozen.add(address)
        self._tx.append(("freeze", address, 0, "compliance freeze"))
        return f"tx{len(self._tx)}"

    # test helpers
    def inject_unknown_holder(self, address: str, units: int) -> None:
        self._balances[address] = self._balances.get(address, 0) + units

    @property
    def frozen(self) -> frozenset[str]:
        return frozenset(self._frozen)


@dataclass
class Mirror:
    """
    Keeps a chain in step with the register, and repairs it when it drifts.

    Addresses are mapped to holder ids explicitly. An address with no mapping is
    by definition an unknown holder, which is the condition worth alarming on.
    """

    chain: ChainAdapter
    address_of: dict[str, str] = field(default_factory=dict)
    _published_to_seq: int = 0

    def bind(self, holder_id: str, address: str) -> None:
        self.address_of[holder_id] = address

    def holder_of(self, address: str) -> str | None:
        for hid, addr in self.address_of.items():
            if addr == address:
                return hid
        return None

    def publish(self, register) -> list[str]:
        """
        Push register state onto the chain up to the current head.

        Idempotent by sequence number: publishing twice does not double-apply.
        Reports what it did, including what it could not do.
        """
        results: list[str] = []
        target = register.head_seq
        if target <= self._published_to_seq:
            return ["nothing to publish"]

        before = register.positions(at_seq=self._published_to_seq)
        after = register.positions(at_seq=target)
        holders = set(before) | set(after)

        for hid in sorted(holders):
            delta = after.get(hid, 0) - before.get(hid, 0)
            if delta == 0:
                continue
            addr = self.address_of.get(hid)
            if addr is None:
                results.append(f"{hid}: NO ADDRESS BOUND, not mirrored")
                continue
            memo = f"{register.instrument_id}@{target}"
            if delta > 0:
                tx = self.chain.credit(addr, delta, memo)
            else:
                tx = self.chain.debit(addr, -delta, memo)
            results.append(f"{hid}: {delta:+d} units ({tx})")

        self._published_to_seq = target
        return results

    def reconcile(self, register) -> ReconciliationReport:
        """
        Compare chain against register and classify every discrepancy.

        This is the function that should run on a schedule in production and
        page someone on a critical finding.
        """
        reg_positions = register.positions()
        chain_balances = self.chain.balances()
        divergences: list[Divergence] = []

        for hid, reg_units in sorted(reg_positions.items()):
            addr = self.address_of.get(hid)
            if addr is None:
                divergences.append(
                    Divergence(
                        holder_id=hid,
                        kind=DivergenceType.NOT_MIRRORED,
                        register_units=reg_units,
                        chain_units=0,
                        detail="holder has no bound chain address",
                    )
                )
                continue
            chain_units = chain_balances.get(addr, 0)
            if chain_units < reg_units:
                divergences.append(
                    Divergence(
                        holder_id=hid,
                        kind=DivergenceType.MISSING_ON_CHAIN,
                        register_units=reg_units,
                        chain_units=chain_units,
                        detail=f"chain short by {reg_units - chain_units} units",
                    )
                )
            elif chain_units > reg_units:
                divergences.append(
                    Divergence(
                        holder_id=hid,
                        kind=DivergenceType.EXCESS_ON_CHAIN,
                        register_units=reg_units,
                        chain_units=chain_units,
                        detail=f"chain over by {chain_units - reg_units} units",
                    )
                )

        known = {self.address_of[h] for h in reg_positions if h in self.address_of}
        for addr, units in sorted(chain_balances.items()):
            if addr in known:
                continue
            hid = self.holder_of(addr)
            if hid is not None and reg_positions.get(hid, 0) > 0:
                continue
            divergences.append(
                Divergence(
                    holder_id=hid or f"<unmapped:{addr}>",
                    kind=DivergenceType.UNKNOWN_ON_CHAIN,
                    register_units=0,
                    chain_units=units,
                    detail=(
                        f"address {addr} holds {units} units but has no "
                        "corresponding register position"
                    ),
                )
            )

        return ReconciliationReport(
            at=datetime.now(timezone.utc),
            register_seq=register.head_seq,
            register_total=sum(reg_positions.values()),
            chain_total=sum(chain_balances.values()),
            divergences=tuple(divergences),
        )

    def repair(self, register, report: ReconciliationReport) -> list[str]:
        """
        Bring the chain back into line with the register.

        The register is never adjusted to match the chain. Not as a matter of
        preference — as a matter of what the register legally is. An unknown
        on-chain balance is frozen rather than silently burned, because it is a
        compliance incident that needs a human, not a script.
        """
        actions: list[str] = []
        for d in report.divergences:
            if d.kind is DivergenceType.MISSING_ON_CHAIN:
                addr = self.address_of[d.holder_id]
                short = d.register_units - d.chain_units
                self.chain.credit(addr, short, f"repair@{register.head_seq}")
                actions.append(f"credited {d.holder_id} {short} units to match register")
            elif d.kind is DivergenceType.EXCESS_ON_CHAIN:
                addr = self.address_of[d.holder_id]
                over = d.chain_units - d.register_units
                self.chain.debit(addr, over, f"repair@{register.head_seq}")
                actions.append(f"debited {d.holder_id} {over} excess units")
            elif d.kind is DivergenceType.UNKNOWN_ON_CHAIN:
                addr = d.detail.split()[1]
                self.chain.freeze(addr)
                actions.append(
                    f"FROZE {addr} pending investigation — units held by an "
                    "address with no register position"
                )
            elif d.kind is DivergenceType.NOT_MIRRORED:
                actions.append(
                    f"{d.holder_id}: cannot repair, no chain address bound"
                )
        return actions
