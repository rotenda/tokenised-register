"""
tokenised_register — a reference implementation of a securities register for
tokenised private-market debt, built for the South African legal position.

The central claim of this implementation:

    The off-chain register is the authoritative record of title.
    The chain is a derived mirror.
    When they disagree, the chain is corrected.

South African law does not currently recognise an on-chain register as
authoritative. Switzerland does, under article 973d of its Code of Obligations.
The Financial Markets Act review has not resolved the question here. Until it
does, this is the architecture the law permits.

Not production software. A demonstration of design decisions, published to make
an argument legible.
"""

from .actions import Allocation, Distribution, allocate_pro_rata, compute_coupon, compute_redemption
from .events import Classification
from .mirror import (
    ChainAdapter,
    Divergence,
    DivergenceType,
    InMemoryChain,
    Mirror,
    ReconciliationReport,
)
from .register import Register, RegisterError, RejectedTransfer
from .restrictions import (
    Decision,
    EligibleTransfereeOnly,
    LockUp,
    MaximumHolders,
    MinimumHolding,
    ProposedTransfer,
    RestrictionEngine,
)

__all__ = [
    "Register",
    "RegisterError",
    "RejectedTransfer",
    "Classification",
    "RestrictionEngine",
    "ProposedTransfer",
    "Decision",
    "MinimumHolding",
    "LockUp",
    "MaximumHolders",
    "EligibleTransfereeOnly",
    "compute_coupon",
    "compute_redemption",
    "allocate_pro_rata",
    "Distribution",
    "Allocation",
    "Mirror",
    "ChainAdapter",
    "InMemoryChain",
    "ReconciliationReport",
    "Divergence",
    "DivergenceType",
]

__version__ = "0.1.0"
