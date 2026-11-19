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
    StopResponse,
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
    Ack,
    AckStatus,
    ActionEnvelope,
    CartesianImpedanceTarget,
    ClockDomain,
    CommandOutcome,
    CommandPayload,
    ControlParameters,
    JointPositionTarget,
    LeasedSession,
    Machine,
    MachineObservation,
    MachineSession,
    RealtimeTickSource,
    Stamp,
    TickSource,
)
from haplous.runtime import HaplousRuntime
from haplous.state import (
    ExecutionState,
    ExecutionStateTracker,
    IllegalTransitionError,
)

__version__ = "0.5.0"

__all__ = [
    "Ack",
    "AckStatus",
    "ActionEnvelope",
    "CartesianImpedanceTarget",
    "ClockDomain",
    "CommandMode",
    "CommandOutcome",
    "CommandPayload",
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
    "JointPositionTarget",
    "LeasedSession",
    "Machine",
    "MachineCapabilities",
    "MachineObservation",
    "MachineSession",
    "RealtimeTickSource",
    "RecoveryPolicy",
    "RefusalReason",
    "Requirement",
    "RequirementKind",
    "SensingChannel",
    "Stamp",
    "StopResponse",
    "SuccessCondition",
    "TickSource",
    "__version__",
]
