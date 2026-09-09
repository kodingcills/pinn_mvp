"""Haplous v0.5-sim: physical intent -> requirements -> machine capability
-> feasibility -> live supervised execution (observe/validate/decide/
command/step) -> recover/degrade/refuse/abort/complete -> execution
evidence.

The core package is machine-agnostic and simulator-free (AST-enforced by
tests/test_haplous_structure.py). Simulators sit behind the Machine
protocol in haplous.adapters; application knowledge lives in
haplous.behaviors.

Public API: everything importable from this module is the supported
surface. Anything else is internal and may change without notice."""

from haplous.capabilities import (
    CommandMode,
    FeasibilityResult,
    MachineCapabilities,
    RefusalReason,
    SensingChannel,
)
from haplous.constraints import Constraint, ConstraintStatus
from haplous.evidence import EvidenceEvent, ExecutionEvidence
from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import (
    CommandOutcome,
    ControlParameters,
    Machine,
    MachineObservation,
    MachineSession,
    ServoCommand,
    Stamp,
)
from haplous.runtime import HaplousRuntime
from haplous.state import (
    ExecutionState,
    ExecutionStateTracker,
    IllegalTransitionError,
)

__version__ = "0.5.0"

__all__ = [
    "CommandMode",
    "CommandOutcome",
    "Constraint",
    "ControlParameters",
    "EvidenceEvent",
    "ExecutionEvidence",
    "ExecutionIntent",
    "ExecutionState",
    "ExecutionStateTracker",
    "FeasibilityResult",
    "HaplousRuntime",
    "IllegalTransitionError",
    "Machine",
    "MachineCapabilities",
    "MachineObservation",
    "MachineSession",
    "RecoveryPolicy",
    "RefusalReason",
    "Requirement",
    "RequirementKind",
    "SensingChannel",
    "ServoCommand",
    "Stamp",
    "SuccessCondition",
    "__version__",
]
