# Locked design OD-1: contract-driven command-mode admission

Status: **LOCKED** 2026-11-13 by operator decision (OD-1, PROGRAM_STATE.md). Implementation unit M3.6.
This changes behaviour-frozen code (capability negotiation, runtime admission). AGENTS.md
requires a locked design and parity tests; this file is the design, section 6 is the parity bar.

## 1. Decision

Haplous refuses a **task** on a machine that cannot verifiably satisfy the task's required
command semantics. It does not refuse the **machine**: the same machine stays admissible for
tasks whose requirements it meets. Hardware independence means tasks state requirements
independently of the machine, and the runtime decides whether a machine meets them. It does
not mean all machines are interchangeable.

Invariant:

    Admit(T, M)  ⇔  for every required mode r of T:
                      r ∈ M.command_modes
                      or ( r ∉ T.non_substitutable
                           and ∃ a ∈ M.command_modes with (r → a) ∈ T.authorized_substitutes
                                                     and (r → a) ∈ M.verified_adapters )

A substitute needs BOTH sides: the task authorizes it and the machine has independent
evidence (a verified adapter) that it preserves the required behaviour. Neither side alone
suffices. A mode name match is necessary for exact admission; string aliases are never
equivalences.

## 2. Concepts and where they live (existing owners, no new module)

| Concept | Location | Change |
|---|---|---|
| Capability manifest | `haplous.capabilities.MachineCapabilities` | add `verified_adapters: frozenset[tuple[CommandMode, CommandMode]] = frozenset()` (required → native) |
| Execution contract | the behaviour that realizes the task (the code that emits `ServoCommand`s) | each registered behaviour declares `required_command_modes: frozenset[CommandMode]`, `authorized_substitutes: frozenset[tuple[CommandMode, CommandMode]] = frozenset()`, `non_substitutable_modes: frozenset[CommandMode] = frozenset()` |
| Admission control | `haplous.capabilities.negotiate` + `haplous.runtime` submit path | negotiate receives the selected behaviour's contract; refuses before any session opens |

`ServoCommand` carries no mode tag, so a behaviour's required modes are a declared fact about
the commands it emits. The implementer must justify each existing behaviour's declaration from
its command semantics (which phases regulate pose, which regulate axial force) with a
`file:line` citation, not by choosing whatever keeps tests green.

## 3. Admission rules

1. **Contract undeclared → REFUSED** `command_contract_undeclared`. Missing data never passes.
2. **Requirement unmet → REFUSED** `unsupported_command_mode`, one reason per unmet mode, with
   structured data: `required` (mode value), `available` (sorted machine mode values),
   `substitute_considered` (pair or null, with which side was missing),
   `execution_started: false`.
3. **Requirement met only through a verified, authorized adapter → SUPPORTED** with a recorded
   disclosure `command_mode_substituted` (required, selected native mode). Never DEGRADED:
   a verified adapter is full satisfaction, an unverified one is refusal.
4. The existing empty-mode-set refusal (`unsupported_control_mode`) stays as is.
5. Refusal at submit means no session is opened and no command is sent. Tests check this on
   the machine (`open_session` call count 0), not only the returned label.

`RefusalReason` gains an optional structured `data` mapping (default empty) so existing
callers and codes are unchanged.

## 4. Capability loss during execution

On each supervision tick the runtime re-evaluates rule 2 against `machine.capabilities()`.
If a required mode is no longer satisfied, it takes the existing abort path (machine `stop()`,
outcome ABORTED, reason `command_capability_lost`, evidence event with required/available).
It never continues under invalid assumptions. Machines whose capabilities are static (all
current machines) see no behavioural change.

## 5. Track B ownership (mandate: resolve the overlap)

`evaluation/track_b.py` `_resolve_mode` implements the same admission semantics for the v1.1
contract fixtures. Decision: `haplous/` owns runtime admission; Track B remains the fixture
truth prototype and serves as an oracle. A differential test drives both with the same random
mode configurations (exact, authorized-only, verified-only, both, non-substitutable) and asserts
identical admit/refuse decisions where the concepts overlap.

## 6. Parity bar (must hold before merge)

- Every insertion and slide HEB scenario × every system in `benchmarks.heb.runner.evaluate`
  gives identical reported disposition, reason, refusal codes, graded outcome and observations
  before and after, on the standard `MuJoCoMachine` (paired table, committed JSON report).
- Committed HES outputs unchanged: `hes qualify` byte-identical, `hes report --check` digest
  `dc913cf8e9886bfd` (run with `--out` to a temp dir).
- Both committed recordings still pass the M2 gate 11/11 (`--existing`, report to a temp dir).
- M3.3's strict xfail `test_negotiation_refuses_a_mode_the_behavior_cannot_use[descent_only, str_alias]`
  becomes passing tests: descent-only machine REFUSED at submit with `unsupported_command_mode`
  and zero sessions opened; a str alias is refused, not crashed.
- New tests: undeclared contract refused; verified+authorized adapter admitted with disclosure;
  authorized-only and verified-only refused; non-substitutable never substituted; mid-execution
  capability loss aborts via stop with the evidence event; the Track B differential test.
- Each new guard is shown red then green.

## 7. Out of scope

Real OEM adapters, ROS 2 controller modes, any change to physics, controllers or `ServoCommand`.
The incumbent (G1) gets its own admission semantics in M4.1, compared against this.
