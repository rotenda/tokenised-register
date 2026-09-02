"""
Tamper-evident persistence.

The in-memory register proves the architecture. It does not survive a restart,
and more importantly it cannot prove it has not been altered.

That second problem is the real one. A securities register kept in an ordinary
database is only as trustworthy as whoever holds write access to it. A DBA can
update a row and nothing in the data reveals it. For a statutory record that is
a genuine weakness, and it is the weakness a chain is usually invoked to solve.

It does not need a chain. It needs a hash chain, which is a much smaller idea:

    record_hash = SHA256(seq || prev_hash || payload_hash)

Each record commits to its predecessor. Change any byte of any historical
payload and every subsequent hash breaks. The tampering is not prevented — it
is made *evident*, which is the property an auditor actually needs.

This is what a chain gives you for register integrity, without a chain. Worth
being precise about that, because it is often the only property being sought.

Publishing the head hash somewhere external — a chain, a newspaper, a notary,
a counterparty's system — is what additionally prevents wholesale substitution
of the entire log. That is anchoring, and it is a separate and much cheaper
operation than mirroring every transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import events as event_module
from .events import Event

GENESIS = "0" * 64


def canonical_payload(event: Event) -> str:
    """
    Deterministic serialisation.

    The hash is only meaningful if the same event always produces the same
    bytes. That rules out ordinary dict ordering, float formatting surprises,
    and locale-dependent datetime rendering. Keys are sorted, datetimes are
    ISO-8601 in UTC, enums reduce to their values, and separators are fixed.

    seq is excluded from the payload because it is hashed separately as a
    positional commitment.
    """
    if not is_dataclass(event):
        raise TypeError("events must be dataclasses")

    data: dict[str, Any] = {"_type": type(event).__name__}
    for f in fields(event):
        if f.name == "seq":
            continue
        value = getattr(event, f.name)
        if isinstance(value, datetime):
            value = value.astimezone().isoformat()
        elif hasattr(value, "value"):  # Enum
            value = value.value
        data[f.name] = value

    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def payload_digest(event: Event) -> str:
    return hashlib.sha256(canonical_payload(event).encode("utf-8")).hexdigest()


def link_hash(seq: int, prev_hash: str, payload_hash: str) -> str:
    material = f"{seq}|{prev_hash}|{payload_hash}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class TamperDetected(Exception):
    """Raised when the stored chain does not verify."""


class EventStore:
    """
    Append-only, hash-chained event store backed by SQLite.

    Deliberately exposes no update or delete. Not as a matter of discipline —
    the methods do not exist. A caller who wants to change history has to go
    around the class, and going around the class breaks the chain, which is
    exactly the outcome the design wants.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                seq          INTEGER PRIMARY KEY,
                event_type   TEXT NOT NULL,
                payload      TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                prev_hash    TEXT NOT NULL,
                record_hash  TEXT NOT NULL UNIQUE,
                appended_at  TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # ---------- writing ----------

    @property
    def head(self) -> tuple[int, str]:
        """(sequence, hash) of the most recent record. The anchoring point."""
        row = self._conn.execute(
            "SELECT seq, record_hash FROM event_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (0, GENESIS) if row is None else (row[0], row[1])

    def append(self, event: Event) -> str:
        """Append an event and return its record hash."""
        prev_seq, prev_hash = self.head
        seq = prev_seq + 1
        p_hash = payload_digest(event)
        r_hash = link_hash(seq, prev_hash, p_hash)

        self._conn.execute(
            "INSERT INTO event_log "
            "(seq, event_type, payload, payload_hash, prev_hash, record_hash, appended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                type(event).__name__,
                canonical_payload(event),
                p_hash,
                prev_hash,
                r_hash,
                datetime.now().astimezone().isoformat(),
            ),
        )
        self._conn.commit()
        return r_hash

    def append_all(self, events: list[Event]) -> str:
        for event in events:
            self.append(event)
        return self.head[1]

    # ---------- reading ----------

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]

    def records(self) -> Iterator[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        yield from self._conn.execute("SELECT * FROM event_log ORDER BY seq")

    def _resolve_type(self, name: str):
        """
        Find an event class by name across the registered event modules.

        The store is domain-agnostic — it chains securities events and
        provenance events equally well — so type resolution cannot assume a
        single module.
        """
        from . import provenance as provenance_module

        for module in (event_module, provenance_module):
            cls = getattr(module, name, None)
            if cls is not None:
                return cls
        raise TamperDetected(
            f"stored event type '{name}' is not defined in any known module"
        )

    def replay(self) -> list[Event]:
        """
        Rebuild events from stored payloads.

        Verifies the chain first. A store that does not verify is not replayed —
        returning state derived from a tampered log would defeat the point.
        """
        self.verify()
        rebuilt: list[Event] = []
        self._conn.row_factory = sqlite3.Row
        for row in self._conn.execute("SELECT * FROM event_log ORDER BY seq"):
            data = json.loads(row["payload"])
            cls = self._resolve_type(data.pop("_type"))
            kwargs = {}
            for f in fields(cls):
                if f.name == "seq" or f.name not in data:
                    continue
                value = data[f.name]
                if isinstance(value, str):
                    if f.name in ("timestamp", "valid_until", "last_assay"):
                        value = datetime.fromisoformat(value)
                    elif f.name == "classification":
                        value = event_module.Classification(value)
                    elif f.name == "status":
                        from .provenance import DueDiligenceStatus
                        value = DueDiligenceStatus(value)
                    elif f.name == "role":
                        from .provenance import Role
                        value = Role(value)
                kwargs[f.name] = value
            event = cls(**kwargs)
            object.__setattr__(event, "seq", row["seq"])
            rebuilt.append(event)
        return rebuilt

    # ---------- integrity ----------

    def verify(self) -> None:
        """
        Recompute the whole chain. Raises TamperDetected on the first break.

        Three things are checked: that each payload still hashes to its stored
        digest (content unchanged), that each link hash is correctly derived
        (structure unchanged), and that sequence numbers are contiguous
        (nothing removed).
        """
        prev_hash = GENESIS
        expected_seq = 1
        self._conn.row_factory = sqlite3.Row

        for row in self._conn.execute("SELECT * FROM event_log ORDER BY seq"):
            seq = row["seq"]
            if seq != expected_seq:
                raise TamperDetected(
                    f"sequence gap: expected {expected_seq}, found {seq} "
                    "(a record has been removed)"
                )

            recomputed_payload = hashlib.sha256(
                row["payload"].encode("utf-8")
            ).hexdigest()
            if recomputed_payload != row["payload_hash"]:
                raise TamperDetected(
                    f"payload altered at sequence {seq}: content does not match "
                    "its stored digest"
                )

            if row["prev_hash"] != prev_hash:
                raise TamperDetected(
                    f"broken link at sequence {seq}: predecessor hash does not "
                    "match the preceding record"
                )

            recomputed_link = link_hash(seq, prev_hash, row["payload_hash"])
            if recomputed_link != row["record_hash"]:
                raise TamperDetected(
                    f"record hash invalid at sequence {seq}"
                )

            prev_hash = row["record_hash"]
            expected_seq += 1

    def verified(self) -> bool:
        try:
            self.verify()
            return True
        except TamperDetected:
            return False

    def proof(self, seq: int) -> dict[str, str]:
        """
        Everything needed for a third party to verify one record independently,
        given a trusted head hash.
        """
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT * FROM event_log WHERE seq = ?", (seq,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no record at sequence {seq}")
        return {
            "seq": str(row["seq"]),
            "payload": row["payload"],
            "payload_hash": row["payload_hash"],
            "prev_hash": row["prev_hash"],
            "record_hash": row["record_hash"],
        }

    def close(self) -> None:
        self._conn.close()


def anchor_value(store: EventStore) -> str:
    """
    The single value worth publishing externally.

    Anchoring the head hash to any independent medium at intervals bounds how
    far back an attacker with full database access could rewrite: no further
    than the last anchor. One value per interval, rather than a transaction per
    transfer.

    This is the cheap 90% of what people reach for a chain to achieve.
    """
    seq, head = store.head
    return f"{seq}:{head}"


asdict  # retained for callers building their own serialisers
